"""
ファイルプロセッサー

Blob Storage からファイルを取得し、処理を行い、
Table Storage のステータスを更新します。

Step 2-3（Part 3: 拡張編）で使用するプロセッサーで、
3 つの Azure Storage サービスを連携させた処理を実装しています:
- Queue Storage: メッセージ受信（Worker 経由）
- Blob Storage: ファイルのダウンロード
- Table Storage: 処理ステータスの更新

処理フローの詳細は 09-table-storage.md のシーケンス図を参照。
"""

# ============================================================
# インポート
# ============================================================

# 標準ライブラリ: 非同期I/O処理用モジュール
# asyncio.sleep: 処理時間のシミュレーションに使用
import asyncio
# 標準ライブラリ: ログ出力用モジュール
import logging
# 標準ライブラリ: 型ヒント用モジュール
from typing import Any

# アプリケーション内部: プロセッサー基底クラス
from apps.worker.app.processors.base import BaseProcessor
# アプリケーション内部: Blob Storage 操作サービス
# ファイルのダウンロードに使用
from libs.shared.blob_service import BlobService
# アプリケーション内部: Table Storage 操作サービス
# ステータス更新に使用
from libs.shared.table_service import TableService

# ============================================================
# ロガー設定
# ============================================================

# このモジュール用のロガーを取得
logger = logging.getLogger(__name__)

# ============================================================
# 定数
# ============================================================

# Table Storage のパーティションキー
# API 側（files.py）と同じ値を使用する必要がある
# 異なる値を使用するとエンティティが見つからなくなる
DEFAULT_PARTITION_KEY = "files"


# ============================================================
# ファイルプロセッサークラス
# ============================================================


class FileProcessor(BaseProcessor):
    """
    ファイルプロセッサー

    1. Blob Storage からファイルをダウンロード
    2. ファイルを処理（タイプ検出など）
    3. Table Storage のステータスを更新（completed）

    使用シナリオ:
    - POST /files/upload エンドポイントから投入されたファイルの処理
    - Blob/Table Storage との連携確認
    - ファイルタイプの自動検出

    ステータス遷移:
    pending → processing → completed/failed
    - pending: API でアップロード時に設定
    - processing: このプロセッサーで処理開始時に更新
    - completed: 処理成功時に更新
    - failed: エラー発生時に更新（Worker が担当）
    """

    @property
    def processor_type(self) -> str:
        """
        プロセッサータイプを返す

        Returns:
            str: "file" - このプロセッサーの識別子
                 メッセージの processor_type が "file" の場合に選択される
        """
        return "file"

    async def execute(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        ファイルを処理

        3 つの Storage サービスを連携させてファイル処理を行います:
        1. Table: ステータスを processing に更新
        2. Blob: ファイルをダウンロード
        3. 処理: ファイルタイプ検出（シミュレーション）
        4. Table: ステータスを completed に更新

        Args:
            data: メッセージデータ（キューから取得した JSON データ）
                - file_id: ファイルID（API が生成した一意識別子）
                - filename: ファイル名（アップロード時のオリジナル名）
                - blob_path: Blob パス（uploads/{file_id}/{filename}）

        Returns:
            dict: 処理結果
                - status: "completed"（処理成功）
                - file_id: 処理したファイルのID
                - filename: ファイル名
                - result: 処理結果サマリー（サイズ、タイプ）

        Raises:
            Exception: Blob ダウンロードや Table 更新に失敗した場合
                       Worker がキャッチして failed ステータスに更新
        """
        # ============================================================
        # メッセージからファイル情報を抽出
        # ============================================================
        # get() を使用してキーが存在しない場合のデフォルト値を設定
        file_id = data.get("file_id", "unknown")
        filename = data.get("filename", "unknown")
        blob_path = data.get("blob_path", "")

        # 処理開始ログ（ファイルIDとファイル名を含める）
        logger.info(f"ファイル処理開始: {file_id} - {filename}")

        # ============================================================
        # 1. Table Storage のステータスを processing に更新
        # ============================================================
        # クライアントがステータスをポーリングした際に
        # 「処理中」であることを確認できるようにする
        table_service = TableService()
        try:
            await table_service.update_entity(
                # パーティションキー: API 側と同じ値
                partition_key=DEFAULT_PARTITION_KEY,
                # 行キー: ファイルIDで特定
                row_key=file_id,
                # 更新データ: Status を processing に変更
                data={"Status": "processing"},
            )
        finally:
            # 必ずクライアントをクローズしてリソースを解放
            await table_service.close()

        # ============================================================
        # 2. Blob Storage からファイルをダウンロード
        # ============================================================
        # API がアップロードしたファイルを取得
        blob_service = BlobService()
        try:
            # blob_path は "uploads/{file_id}/{filename}" 形式
            file_content = await blob_service.download(blob_path)
            # ダウンロード成功ログ（パスとサイズを含める）
            logger.info(
                f"ファイルダウンロード完了: {blob_path}, size={len(file_content)} bytes"
            )
        finally:
            # 必ずクライアントをクローズしてリソースを解放
            await blob_service.close()

        # ============================================================
        # 3. ファイル処理をシミュレーション
        # ============================================================
        # 実際のアプリケーションでは、ここでファイルの解析や変換を行う
        # 例: 画像リサイズ、PDF テキスト抽出、ウイルススキャンなど
        # 2秒の待機で処理に時間がかかることをシミュレート
        await asyncio.sleep(2)

        # ============================================================
        # 4. 処理結果のサマリーを作成
        # ============================================================
        # ファイルサイズとタイプを記録
        result_summary = {
            # ファイルサイズ（バイト）
            "file_size": len(file_content),
            # ファイルタイプ（マジックナンバーで判定）
            "file_type": self._detect_file_type(file_content),
        }

        # ============================================================
        # 5. Table Storage のステータスを completed に更新
        # ============================================================
        # 処理結果も一緒に保存（クライアントが確認可能）
        table_service = TableService()
        try:
            await table_service.update_entity(
                partition_key=DEFAULT_PARTITION_KEY,
                row_key=file_id,
                data={
                    # 処理完了ステータス
                    "Status": "completed",
                    # ファイルサイズ（Table Storage に保存）
                    "FileSize": len(file_content),
                    # ファイルタイプ（Table Storage に保存）
                    "FileType": result_summary["file_type"],
                },
            )
        finally:
            # 必ずクライアントをクローズ
            await table_service.close()

        # 処理完了ログ（ファイルIDと結果を含める）
        logger.info(f"ファイル処理完了: {file_id}, result={result_summary}")

        # ============================================================
        # 6. 処理結果を返却
        # ============================================================
        return {
            # 処理ステータス
            "status": "completed",
            # ファイルID
            "file_id": file_id,
            # ファイル名
            "filename": filename,
            # 処理結果サマリー
            "result": result_summary,
        }

    def _detect_file_type(self, content: bytes) -> str:
        """
        ファイルタイプを検出（簡易実装）

        ファイルの先頭バイト（マジックナンバー）を確認して
        ファイルタイプを判定します。

        実際のアプリケーションでは、python-magic などの
        ライブラリを使用することを推奨します。

        Args:
            content: ファイルの内容（バイト列）

        Returns:
            str: ファイルタイプ名
                 - "PDF": PDF ファイル（%PDF で始まる）
                 - "PNG": PNG 画像（\x89PNG で始まる）
                 - "JPEG": JPEG 画像（\xff\xd8\xff で始まる）
                 - "ZIP/Office": ZIP または Office ファイル（PK で始まる）
                 - "Unknown": 上記以外

        Note:
            マジックナンバーはファイル形式を識別するための
            固定バイトシーケンスです。拡張子は偽装可能ですが、
            マジックナンバーは内容を確認するためより信頼性があります。
        """
        # ============================================================
        # マジックナンバーによるファイルタイプ判定
        # ============================================================

        # PDF: %PDF で始まる
        if content.startswith(b"%PDF"):
            return "PDF"
        # PNG: \x89PNG\r\n\x1a\n で始まる（先頭4バイトで判定）
        elif content.startswith(b"\x89PNG"):
            return "PNG"
        # JPEG: \xff\xd8\xff で始まる（SOI + APPn マーカー）
        elif content.startswith(b"\xff\xd8\xff"):
            return "JPEG"
        # ZIP/Office: PK で始まる
        # Office 2007以降のファイル（.docx, .xlsx, .pptx）は
        # 実際には ZIP 形式のコンテナ
        elif content.startswith(b"PK"):
            return "ZIP/Office"
        # 上記以外: 不明なファイルタイプ
        else:
            return "Unknown"
