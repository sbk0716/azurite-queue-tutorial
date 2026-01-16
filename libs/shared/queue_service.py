"""
Azure Queue Storage サービス

Queue Storage の操作を提供します。
Azurite（ローカル開発）と Azure（本番環境）の両方に対応。

このモジュールは以下の機能を提供します:
- メッセージの投入（enqueue）
- メッセージの取得（dequeue）
- メッセージの削除（delete）
- キュープロパティの取得

設計上の決定:
- 非同期クライアント（aio）を使用して高スループットを実現
- JSON 形式でメッセージを送受信
- キューが存在しない場合は自動作成

Azure Queue Storage の概念:
- メッセージは Base64 エンコードされて保存される
- Visibility Timeout: 取得後、他のコンシューマーから見えなくなる時間
- Dequeue Count: メッセージが取得された回数（リトライ判定に使用）
"""

# ============================================================
# インポート
# ============================================================

# 標準ライブラリ: JSON 操作用モジュール
# メッセージのシリアライズ/デシリアライズに使用
import json
# 標準ライブラリ: ログ出力用モジュール
import logging
# 標準ライブラリ: 型ヒント用モジュール
from typing import Any, Optional

# Azure SDK: Queue Storage 非同期クライアント
# aio モジュールを使用することで async/await が利用可能
from azure.storage.queue.aio import QueueServiceClient

# アプリケーション内部: 設定管理モジュール
# 接続文字列などの設定を取得
from libs.shared.config import settings

# ============================================================
# ロガー設定
# ============================================================

# このモジュール用のロガーを取得
logger = logging.getLogger(__name__)


# ============================================================
# Queue Storage サービスクラス
# ============================================================


class QueueService:
    """
    Queue Storage 操作サービス

    メッセージの送受信、削除を提供します。

    使用例:
        service = QueueService()
        try:
            # メッセージ投入
            await service.enqueue("my-queue", {"task": "process"})

            # メッセージ取得
            messages = await service.dequeue("my-queue")

            # メッセージ削除
            for msg in messages:
                await service.delete_message("my-queue", msg)
        finally:
            await service.close()

    Note:
        使用後は必ず close() を呼び出してリソースを解放してください。
    """

    def __init__(self):
        """
        Queue Service の初期化

        設定から接続文字列を取得し、クライアントを遅延初期化用に準備。
        実際のクライアント生成は最初の操作時に行われる（遅延初期化）。
        """
        # 設定から接続文字列を取得
        # Azurite: "UseDevelopmentStorage=true"
        # Azure: 実際の接続文字列
        self.connection_string = settings.storage_connection_string
        # クライアントは遅延初期化（最初の使用時に生成）
        self._client: Optional[QueueServiceClient] = None

    async def _get_client(self) -> QueueServiceClient:
        """
        Queue Service Client を取得

        遅延初期化パターン:
        - 最初の呼び出し時にクライアントを生成
        - 以降は同じインスタンスを再利用
        - メモリ効率と接続の再利用を両立

        Returns:
            QueueServiceClient: Queue Storage クライアント
        """
        if self._client is None:
            # 接続文字列からクライアントを生成
            self._client = QueueServiceClient.from_connection_string(
                self.connection_string
            )
        return self._client

    async def enqueue(
        self,
        queue_name: str,
        message: dict[str, Any],
        ttl: Optional[int] = None,
    ) -> str:
        """
        メッセージをキューに投入

        メッセージを JSON 文字列に変換してキューに送信します。
        キューが存在しない場合は自動的に作成されます。

        Args:
            queue_name: キュー名
                       例: "task-queue", "task-queue-dlq"
            message: 投入するメッセージ（dict）
                    JSON シリアライズ可能な辞書
            ttl: Time To Live（秒）、None の場合は無制限（7日間）
                メッセージの有効期限

        Returns:
            str: メッセージID
                 投入されたメッセージの一意識別子

        Raises:
            Exception: キュー操作に失敗した場合
        """
        try:
            # クライアントを取得
            client = await self._get_client()
            # キュークライアントを取得
            queue_client = client.get_queue_client(queue_name)

            # ============================================================
            # キューの自動作成
            # ============================================================
            # キューが存在しない場合は作成
            # 既に存在する場合は例外が発生するが無視
            try:
                await queue_client.create_queue()
                logger.info(f"キュー作成: {queue_name}")
            except Exception:
                # 既に存在する場合は無視（ResourceExistsError など）
                pass

            # ============================================================
            # メッセージの送信
            # ============================================================
            # 辞書を JSON 文字列に変換
            # ensure_ascii=False: 日本語などの非 ASCII 文字をそのまま保持
            message_content = json.dumps(message, ensure_ascii=False)

            # TTL が指定されている場合は設定
            if ttl:
                result = await queue_client.send_message(
                    message_content, time_to_live=ttl
                )
            else:
                # TTL なし（デフォルト: 7日間）
                result = await queue_client.send_message(message_content)

            # 投入完了ログ
            logger.info(
                f"メッセージ投入完了: queue={queue_name}, "
                f"message_id={result.id}, size={len(message_content)} bytes"
            )

            # メッセージID を返却
            return result.id

        except Exception as e:
            logger.error(f"メッセージ投入エラー: {e}")
            raise

    async def dequeue(
        self,
        queue_name: str,
        max_messages: int = 1,
        visibility_timeout: int = 300,
    ) -> list[Any]:
        """
        メッセージをキューから取得

        キューからメッセージを取得します。
        取得されたメッセージは Visibility Timeout の間、他のコンシューマーから見えなくなります。

        Visibility Timeout について:
        - 取得後、指定秒数の間メッセージは「見えない」状態になる
        - この間に処理を完了し、delete_message を呼び出す
        - 処理が完了せずタイムアウトすると、メッセージは再度見えるようになる
        - これにより「at-least-once」配信が保証される

        Args:
            queue_name: キュー名
            max_messages: 最大取得件数（デフォルト: 1、最大: 32）
            visibility_timeout: 可視性タイムアウト秒数（デフォルト: 300秒=5分）
                              処理時間の見積もりに応じて設定

        Returns:
            list: 取得したメッセージのリスト
                  メッセージオブジェクトには以下のプロパティがある:
                  - id: メッセージID
                  - content: メッセージ内容（JSON 文字列）
                  - dequeue_count: 取得回数
                  - pop_receipt: 削除時に必要なレシート

        Raises:
            Exception: キュー操作に失敗した場合
        """
        try:
            client = await self._get_client()
            queue_client = client.get_queue_client(queue_name)

            # キューの自動作成
            try:
                await queue_client.create_queue()
            except Exception:
                pass

            # ============================================================
            # メッセージの取得
            # ============================================================
            # receive_messages は AsyncIterator を返すため、
            # async for で反復処理
            messages = []
            async for message in queue_client.receive_messages(
                max_messages=max_messages,
                visibility_timeout=visibility_timeout,
            ):
                messages.append(message)

            # メッセージが取得できた場合のみログ出力（DEBUG レベル）
            if messages:
                logger.debug(
                    f"メッセージ取得: queue={queue_name}, count={len(messages)}"
                )

            return messages

        except Exception as e:
            logger.error(f"メッセージ取得エラー: {e}")
            raise

    async def delete_message(self, queue_name: str, message: Any) -> None:
        """
        メッセージを削除

        処理が成功した場合に呼び出します。
        削除しない場合、Visibility Timeout 後に再キューされます。

        重要:
        - 処理成功時は必ずこのメソッドを呼び出してメッセージを削除
        - 削除しないとメッセージが再処理される（重複処理の原因）
        - 削除には pop_receipt が必要（dequeue で取得したメッセージに含まれる）

        Args:
            queue_name: キュー名
            message: 削除するメッセージ
                    dequeue で取得したメッセージオブジェクト

        Raises:
            Exception: 削除に失敗した場合
                      例: pop_receipt が無効（タイムアウト後）
        """
        try:
            client = await self._get_client()
            queue_client = client.get_queue_client(queue_name)

            # メッセージを削除
            # message オブジェクトには id と pop_receipt が含まれている
            await queue_client.delete_message(message)

            logger.debug(f"メッセージ削除完了: queue={queue_name}, id={message.id}")

        except Exception as e:
            logger.error(f"メッセージ削除エラー: {e}")
            raise

    async def get_queue_properties(self, queue_name: str) -> dict[str, Any]:
        """
        キューのプロパティを取得

        キューのメタデータや統計情報を取得します。
        監視やヘルスチェックに使用できます。

        Args:
            queue_name: キュー名

        Returns:
            dict: キューのプロパティ
                - name: キュー名
                - approximate_message_count: 概算メッセージ数
                                            （正確な値ではないことに注意）
                - metadata: カスタムメタデータ

        Raises:
            Exception: プロパティ取得に失敗した場合
        """
        try:
            client = await self._get_client()
            queue_client = client.get_queue_client(queue_name)

            # キューの自動作成
            try:
                await queue_client.create_queue()
            except Exception:
                pass

            # キュープロパティを取得
            properties = await queue_client.get_queue_properties()

            return {
                # キュー名
                "name": queue_name,
                # 概算メッセージ数（Azure によって遅延更新される可能性あり）
                "approximate_message_count": properties.approximate_message_count,
                # カスタムメタデータ
                "metadata": properties.metadata,
            }

        except Exception as e:
            logger.error(f"キュープロパティ取得エラー: {e}")
            raise

    async def close(self) -> None:
        """
        クライアントをクローズ

        使用後は必ず呼び出してリソースを解放してください。
        HTTP 接続やソケットなどのリソースが適切にクリーンアップされます。

        使用パターン:
            service = QueueService()
            try:
                # 操作...
            finally:
                await service.close()
        """
        if self._client:
            await self._client.close()
            self._client = None
