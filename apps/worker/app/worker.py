"""
Queue Worker メインスクリプト

Queue Storage をポーリングして、メッセージを処理します。

主要機能:
- 指数バックオフ + Jitter（空ポーリング時のコスト最適化）
- DLQ実装（ポイズンメッセージの自動退避）
- Semaphore並行数制御
"""

# ============================================================
# インポート
# ============================================================

# 標準ライブラリ: 非同期処理用モジュール
import asyncio
# 標準ライブラリ: JSON処理用モジュール
import json
# 標準ライブラリ: ログ出力用モジュール
import logging
# 標準ライブラリ: 乱数生成用モジュール（Jitter計算に使用）
import random
# 標準ライブラリ: システム操作用モジュール
import sys
# 標準ライブラリ: 日時操作用モジュール
from datetime import datetime, timezone
# 標準ライブラリ: 型ヒント用モジュール
from typing import Any, Optional

# aiohttp: ヘルスチェック用HTTPサーバー
from aiohttp import web

# アプリケーション内部のプロセッサー（メッセージ処理ロジック）
from apps.worker.app.processors.base import BaseProcessor
from apps.worker.app.processors.task import TaskProcessor
from apps.worker.app.processors.file import FileProcessor
# アプリケーション設定
from libs.shared.config import settings
# Queue操作サービス
from libs.shared.queue_service import QueueService

# ============================================================
# ロギング設定
# ============================================================

# ロギングの基本設定を行う
# - level: INFO以上のログを出力
# - format: タイムスタンプ、ロガー名、ログレベル、メッセージを含む形式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# このモジュール専用のロガーを取得
logger = logging.getLogger(__name__)

# ============================================================
# 並行数制御
# ============================================================

# 設定から最大並行処理数を取得
MAX_CONCURRENT = settings.worker.max_concurrent
# Semaphore: 同時に実行できるタスク数を制限
# これにより、システムリソースの過負荷を防ぐ
semaphore = asyncio.Semaphore(MAX_CONCURRENT)


# ============================================================
# プロセッサー取得
# ============================================================


def get_processor(processor_type: str) -> BaseProcessor:
    """
    プロセッサータイプに応じたプロセッサーを取得

    メッセージ内の processor_type フィールドに基づいて、
    適切なプロセッサーインスタンスを返す。

    Args:
        processor_type: プロセッサータイプ（"task" または "file"）

    Returns:
        BaseProcessor: 対応するプロセッサーインスタンス
    """
    # 利用可能なプロセッサーのマッピング
    processors = {
        # タスク処理用（Step 1）
        "task": TaskProcessor(),
        # ファイル処理用（Step 2-3）
        "file": FileProcessor(),
    }
    # 見つからない場合はデフォルトで TaskProcessor を返す
    return processors.get(processor_type, TaskProcessor())


# ============================================================
# DLQ（Dead Letter Queue）移動処理
# ============================================================


async def move_to_dlq(
    queue_service: QueueService,
    queue_name: str,
    message: Any,
    reason: str,
    error_context: Optional[dict] = None,
    ttl_days: int = 7,
) -> None:
    """
    ポイズンメッセージをDLQに移動

    何度処理しても失敗するメッセージ（ポイズンメッセージ）を
    専用のDLQキューに退避させる。これにより:
    - 正常なメッセージの処理が継続できる
    - 問題のあるメッセージを後で調査・再処理できる

    Args:
        queue_service: Queue Service インスタンス
        queue_name: 元のキュー名
        message: ポイズンメッセージオブジェクト
        reason: DLQ移動理由（例: "MAX_RETRIES_EXCEEDED", "JSON_PARSE_ERROR"）
        error_context: エラーに関する追加情報
        ttl_days: DLQメッセージの保持期間（日）
    """
    # DLQキュー名を生成（例: "task-queue" → "task-poison-queue"）
    dlq_name = f"{queue_name.replace('-queue', '')}-poison-queue"

    try:
        # DLQに送信するペイロードを構築
        now_utc = datetime.now(timezone.utc)
        dlq_payload = {
            # 元のキュー名（どのキューから来たか追跡用）
            "original_queue": queue_name,
            # 元のメッセージID
            "original_message_id": message.id,
            # 何回取得されたか（リトライ回数 + 1）
            "dequeue_count": message.dequeue_count,
            # DLQに移動した日時
            "moved_at": now_utc.isoformat(),
            # 移動理由
            "reason": reason,
            # エラーの詳細情報
            "error_context": error_context or {},
            # 元のメッセージ内容
            "original_content": json.loads(message.content),
        }

        # TTL（Time To Live）を秒単位に変換
        ttl_seconds = ttl_days * 24 * 60 * 60
        # DLQにメッセージを送信
        await queue_service.enqueue(dlq_name, dlq_payload, ttl=ttl_seconds)

        # 元のキューからメッセージを削除
        # これをしないとVisibility Timeout後に再度キューに現れる
        await queue_service.delete_message(queue_name, message)

        # 警告ログを出力（DLQ移動は注意が必要なイベント）
        logger.warning(
            f"DLQ移動: {queue_name} -> {dlq_name}, "
            f"reason={reason}, dequeue_count={message.dequeue_count}"
        )

    except Exception as e:
        # DLQ移動自体が失敗した場合はエラーログを出力
        logger.error(f"DLQ移動エラー: {e}")
        raise


# ============================================================
# メッセージ処理
# ============================================================


async def process_message(
    queue_service: QueueService,
    queue_name: str,
    message: Any,
    max_retries: int,
    dlq_ttl_days: int,
) -> None:
    """
    メッセージを処理

    メッセージを受け取り、適切なプロセッサーで処理を行う。
    処理フロー:
    1. リトライ回数チェック → 超過していればDLQへ
    2. JSONパース → 失敗すればDLQへ
    3. プロセッサーで処理 → 成功すればメッセージ削除
    4. 処理失敗 → メッセージを削除しない（自動リトライ）

    Args:
        queue_service: Queue Service インスタンス
        queue_name: キュー名
        message: 処理対象のメッセージ
        max_retries: 最大リトライ回数
        dlq_ttl_days: DLQ TTL（日）
    """
    try:
        # ============================================================
        # 1. リトライ回数チェック
        # ============================================================
        # dequeue_count が max_retries を超えたらDLQへ移動
        # 例: max_retries=5 の場合、6回目の取得でDLQへ
        if message.dequeue_count > max_retries:
            await move_to_dlq(
                queue_service,
                queue_name,
                message,
                # 理由: 最大リトライ回数超過
                "MAX_RETRIES_EXCEEDED",
                {"last_dequeue_count": message.dequeue_count},
                dlq_ttl_days,
            )
            return

        # ============================================================
        # 2. メッセージ内容のJSONパース
        # ============================================================
        try:
            # メッセージ内容をJSON文字列からPython辞書に変換
            data = json.loads(message.content)
        except json.JSONDecodeError as e:
            # JSONパースエラーの場合はDLQへ
            # これは修正不可能なエラーなので即座にDLQへ
            await move_to_dlq(
                queue_service,
                queue_name,
                message,
                # 理由: JSONパースエラー
                "JSON_PARSE_ERROR",
                {"error": str(e)},
                dlq_ttl_days,
            )
            return

        # ============================================================
        # 3. プロセッサーで処理
        # ============================================================
        # メッセージ内の processor_type に基づいてプロセッサーを取得
        processor_type = data.get("processor_type", "task")
        processor = get_processor(processor_type)

        # 処理開始ログ
        logger.info(
            f"メッセージ処理開始: processor={processor_type}, "
            f"message_id={message.id}, dequeue_count={message.dequeue_count}"
        )

        # プロセッサーの execute メソッドを呼び出して処理実行
        result = await processor.execute(data)

        # ============================================================
        # 4. 処理成功 → メッセージ削除
        # ============================================================
        # 処理が正常完了したらキューからメッセージを削除
        await queue_service.delete_message(queue_name, message)

        # 処理完了ログ
        logger.info(
            f"メッセージ処理完了: message_id={message.id}, "
            f"status={result.get('status', 'unknown')}"
        )

    except Exception as e:
        # ============================================================
        # 処理失敗 → メッセージを削除しない
        # ============================================================
        # 例外が発生した場合、メッセージを削除しない
        # → Visibility Timeout 経過後に再びキューに現れる（自動リトライ）
        logger.error(
            f"メッセージ処理エラー: message_id={message.id}, "
            f"dequeue_count={message.dequeue_count}, error={str(e)}"
        )
        # メッセージを削除しない → Visibility Timeout後に再キュー


# ============================================================
# キューポーリング
# ============================================================


async def queue_poller(queue_config, queue_service: QueueService) -> None:
    """
    キューごとの独立ポーリング

    無限ループでキューをポーリングし、メッセージを処理する。
    メッセージがない場合は指数バックオフで待機時間を延長し、
    Azure Storage へのリクエスト数（コスト）を抑える。

    Args:
        queue_config: キュー設定（名前、間隔、リトライ数など）
        queue_service: Queue Service インスタンス
    """
    # 現在のバックオフ間隔（初期値は設定のポーリング間隔）
    current_backoff = queue_config.polling_interval

    # ポーリング開始ログ
    logger.info(
        f"ポーリング開始: queue={queue_config.name}, "
        f"interval={queue_config.polling_interval}s, "
        f"max_retries={queue_config.max_retries}"
    )

    # 無限ループでポーリング
    while True:
        try:
            # ============================================================
            # メッセージ取得
            # ============================================================
            messages = await queue_service.dequeue(
                queue_config.name,
                # 一度に最大10件取得
                max_messages=10,
                # 可視性タイムアウト: 5分（処理中は他のWorkerから見えない）
                visibility_timeout=300,
            )

            if messages:
                # ============================================================
                # メッセージあり → 処理してバックオフリセット
                # ============================================================
                # メッセージがあった場合はバックオフをリセット
                current_backoff = queue_config.polling_interval

                # 各メッセージを処理
                for msg in messages:
                    # Semaphore で同時実行数を制御
                    async with semaphore:
                        await process_message(
                            queue_service,
                            queue_config.name,
                            msg,
                            queue_config.max_retries,
                            queue_config.dlq_ttl_days,
                        )
            else:
                # ============================================================
                # メッセージなし → 指数バックオフ + Jitter
                # ============================================================
                # Jitter: ランダムな揺らぎを追加（複数Worker間の同期を防ぐ）
                jitter = random.uniform(0, 0.5)
                # 指数バックオフ: 待機時間を1.5倍に増加（上限あり）
                current_backoff = min(
                    current_backoff * 1.5 + jitter,
                    queue_config.backoff_max,
                )
                # デバッグログ（本番では表示されない）
                logger.debug(
                    f"キュー空: {queue_config.name}, "
                    f"次回ポーリング: {current_backoff:.1f}s後"
                )

            # 次のポーリングまで待機
            await asyncio.sleep(current_backoff)

        except Exception as e:
            # ポーリング自体のエラー（接続エラーなど）
            logger.error(f"ポーリングエラー: {queue_config.name}, {str(e)}")
            # エラー時は5秒待機してリトライ
            await asyncio.sleep(5)


# ============================================================
# ヘルスチェックサーバー
# ============================================================


async def health_check_handler(request):
    """
    ヘルスチェックエンドポイントのハンドラー

    Kubernetes の liveness/readiness プローブや
    ロードバランサーのヘルスチェックに使用する。

    Args:
        request: aiohttp のリクエストオブジェクト

    Returns:
        JSON レスポンス
    """
    return web.json_response(
        {
            # サービスの稼働状態
            "status": "healthy",
            # サービス名
            "service": "tutorial-worker",
            # 最大並行処理数
            "max_concurrent": MAX_CONCURRENT,
            # 現在時刻（UTC）
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


async def start_health_server() -> None:
    """
    ヘルスチェック用HTTPサーバー起動

    Worker プロセス内で軽量なHTTPサーバーを起動し、
    ヘルスチェックエンドポイントを提供する。
    """
    # aiohttp アプリケーションを作成
    app = web.Application()
    # /health エンドポイントを追加
    app.router.add_get("/health", health_check_handler)

    # サーバーランナーをセットアップ
    runner = web.AppRunner(app)
    await runner.setup()
    # ポート8000でリッスン開始
    site = web.TCPSite(runner, "0.0.0.0", 8000)
    await site.start()

    # 起動ログ
    logger.info("ヘルスチェックサーバー起動: http://0.0.0.0:8000/health")


# ============================================================
# メインエントリーポイント
# ============================================================


async def main() -> None:
    """
    Workerメインループ

    1. ヘルスチェックサーバーを起動
    2. 各キューに対してポーリングタスクを起動
    3. すべてのタスクを並行実行
    """
    # 起動ログ
    logger.info(
        f"Queue Worker 起動: max_concurrent={MAX_CONCURRENT}, "
        f"queues={len(settings.worker.queues)}"
    )

    # ヘルスチェックサーバーをバックグラウンドタスクとして起動
    asyncio.create_task(start_health_server())

    # Queue Service を初期化
    queue_service = QueueService()

    try:
        # 各キューに対して独立したポーリングタスクを作成
        # 注意: DLQ（poison-queue）はポーリング対象から除外
        tasks = [
            asyncio.create_task(queue_poller(q_cfg, queue_service))
            for q_cfg in settings.worker.queues
            # poison-queue で終わるキューは除外
            if not q_cfg.name.endswith("-poison-queue")
        ]

        # ポーリング開始ログ
        logger.info(f"{len(tasks)} 個のキューポーリング開始")

        # すべてのポーリングタスクを並行実行
        await asyncio.gather(*tasks)

    except KeyboardInterrupt:
        # Ctrl+C による停止
        logger.info("Worker 停止（KeyboardInterrupt）")
    except Exception as e:
        # 予期しないエラー
        logger.error(f"Worker 致命的エラー: {str(e)}")
        raise
    finally:
        # リソースのクリーンアップ
        await queue_service.close()


# ============================================================
# スクリプト実行時のエントリーポイント
# ============================================================

if __name__ == "__main__":
    try:
        # asyncio イベントループでメイン関数を実行
        asyncio.run(main())
    except KeyboardInterrupt:
        # Ctrl+C による終了
        logger.info("Worker 終了")
    except Exception as e:
        # 起動時のエラー
        logger.error(f"Worker 起動エラー: {str(e)}")
        # 異常終了コード1で終了
        sys.exit(1)
