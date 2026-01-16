"""
Azure Blob Storage サービス

Blob Storage の操作を提供します。
Azurite（ローカル開発）と Azure（本番環境）の両方に対応。

このモジュールは以下の機能を提供します:
- ファイルのアップロード（upload）
- ファイルのダウンロード（download）
- ファイルの削除（delete）
- ファイルの存在確認（exists）
- ファイル一覧の取得（list_blobs）

設計上の決定:
- 非同期クライアント（aio）を使用して高スループットを実現
- コンテナが存在しない場合は自動作成
- ContentSettings で MIME タイプを適切に設定

Azure Blob Storage の概念:
- Storage Account: ストレージの最上位単位
- Container: Blob をグループ化する論理単位（ディレクトリに相当）
- Blob: 実際のファイル（Binary Large Object）
"""

# ============================================================
# インポート
# ============================================================

# 標準ライブラリ: ログ出力用モジュール
import logging
# 標準ライブラリ: 型ヒント用モジュール
from typing import Optional

# Azure SDK: Blob Storage 設定クラス
# Content-Type などの HTTP ヘッダーを設定
from azure.storage.blob import ContentSettings
# Azure SDK: Blob Storage 非同期クライアント
# aio モジュールを使用することで async/await が利用可能
from azure.storage.blob.aio import BlobServiceClient, ContainerClient

# アプリケーション内部: 設定管理モジュール
from libs.shared.config import settings

# ============================================================
# ロガー設定
# ============================================================

# このモジュール用のロガーを取得
logger = logging.getLogger(__name__)

# ============================================================
# 定数
# ============================================================

# デフォルトコンテナ名
# ファイルアップロード API で使用
DEFAULT_CONTAINER = "files"


# ============================================================
# Blob Storage サービスクラス
# ============================================================


class BlobService:
    """
    Blob Storage 操作サービス

    ファイルのアップロード・ダウンロードを提供します。

    使用例:
        service = BlobService()
        try:
            # アップロード
            await service.upload("path/to/file.txt", content, "text/plain")

            # ダウンロード
            data = await service.download("path/to/file.txt")

            # 削除
            await service.delete("path/to/file.txt")
        finally:
            await service.close()

    Note:
        使用後は必ず close() を呼び出してリソースを解放してください。
    """

    def __init__(self, container_name: str = DEFAULT_CONTAINER):
        """
        Blob Service の初期化

        Args:
            container_name: コンテナ名
                           デフォルト: "files"
                           用途に応じて変更可能（例: "images", "documents"）
        """
        # 設定から接続文字列を取得
        self.connection_string = settings.storage_connection_string
        # 使用するコンテナ名
        self.container_name = container_name
        # クライアントは遅延初期化
        self._client: Optional[BlobServiceClient] = None

    async def _get_client(self) -> BlobServiceClient:
        """
        Blob Service Client を取得

        遅延初期化パターン:
        - 最初の呼び出し時にクライアントを生成
        - 以降は同じインスタンスを再利用

        Returns:
            BlobServiceClient: Blob Storage クライアント
        """
        if self._client is None:
            # 接続文字列からクライアントを生成
            self._client = BlobServiceClient.from_connection_string(
                self.connection_string
            )
        return self._client

    async def _get_container_client(self) -> ContainerClient:
        """
        Container Client を取得（自動作成）

        コンテナが存在しない場合は自動的に作成します。
        これにより、初回使用時でもエラーなく動作します。

        Returns:
            ContainerClient: コンテナクライアント
        """
        client = await self._get_client()
        container_client = client.get_container_client(self.container_name)

        # コンテナの自動作成
        try:
            await container_client.create_container()
            logger.info(f"コンテナ作成: {self.container_name}")
        except Exception:
            # 既に存在する場合は無視（ResourceExistsError など）
            pass

        return container_client

    async def upload(
        self,
        blob_name: str,
        data: bytes,
        content_type: Optional[str] = None,
    ) -> str:
        """
        ファイルをアップロード

        バイナリデータを Blob Storage にアップロードします。
        同名の Blob が存在する場合は上書きされます。

        Args:
            blob_name: Blob 名（ファイルパス）
                      例: "uploads/file-123/document.pdf"
                      スラッシュで階層を表現（仮想ディレクトリ）
            data: ファイルデータ（バイト列）
            content_type: Content-Type（MIME タイプ）
                         例: "text/plain", "image/png", "application/pdf"
                         ダウンロード時に使用される

        Returns:
            str: Blob URL
                 アップロードされた Blob へのアクセス URL

        Raises:
            Exception: アップロードに失敗した場合

        Note:
            ContentSettings を使用して MIME タイプを設定することで、
            ダウンロード時に適切な Content-Type ヘッダーが返されます。
        """
        try:
            container_client = await self._get_container_client()
            blob_client = container_client.get_blob_client(blob_name)

            # ============================================================
            # ファイルのアップロード
            # ============================================================
            await blob_client.upload_blob(
                data,
                # 既存の Blob を上書き
                overwrite=True,
                # Content-Type が指定されている場合は ContentSettings を設定
                # ContentSettings は辞書ではなくクラスを使用（重要）
                content_settings=ContentSettings(content_type=content_type)
                if content_type
                else None,
            )

            # アップロード完了ログ
            logger.info(
                f"ファイルアップロード完了: container={self.container_name}, "
                f"blob={blob_name}, size={len(data)} bytes"
            )

            # Blob URL を返却
            return blob_client.url

        except Exception as e:
            logger.error(f"ファイルアップロードエラー: {e}")
            raise

    async def download(self, blob_name: str) -> bytes:
        """
        ファイルをダウンロード

        Blob Storage からファイルを取得します。

        Args:
            blob_name: Blob 名（ファイルパス）
                      upload 時に指定したパスと同じ

        Returns:
            bytes: ファイルデータ（バイト列）

        Raises:
            Exception: ダウンロードに失敗した場合
                      例: Blob が存在しない
        """
        try:
            container_client = await self._get_container_client()
            blob_client = container_client.get_blob_client(blob_name)

            # ============================================================
            # ファイルのダウンロード
            # ============================================================
            # download_blob でストリームを取得
            download_stream = await blob_client.download_blob()
            # readall でバイト列として読み込み
            data = await download_stream.readall()

            # ダウンロード完了ログ
            logger.info(
                f"ファイルダウンロード完了: container={self.container_name}, "
                f"blob={blob_name}, size={len(data)} bytes"
            )

            return data

        except Exception as e:
            logger.error(f"ファイルダウンロードエラー: {e}")
            raise

    async def delete(self, blob_name: str) -> None:
        """
        ファイルを削除

        Blob Storage からファイルを削除します。

        Args:
            blob_name: Blob 名（ファイルパス）

        Raises:
            Exception: 削除に失敗した場合
                      例: Blob が存在しない
        """
        try:
            container_client = await self._get_container_client()
            blob_client = container_client.get_blob_client(blob_name)

            # Blob を削除
            await blob_client.delete_blob()

            # 削除完了ログ
            logger.info(
                f"ファイル削除完了: container={self.container_name}, blob={blob_name}"
            )

        except Exception as e:
            logger.error(f"ファイル削除エラー: {e}")
            raise

    async def exists(self, blob_name: str) -> bool:
        """
        ファイルの存在確認

        指定した Blob が存在するかどうかを確認します。

        Args:
            blob_name: Blob 名（ファイルパス）

        Returns:
            bool: 存在する場合 True、しない場合 False

        Raises:
            Exception: 確認に失敗した場合
        """
        try:
            container_client = await self._get_container_client()
            blob_client = container_client.get_blob_client(blob_name)

            # exists メソッドで存在確認
            return await blob_client.exists()

        except Exception as e:
            logger.error(f"ファイル存在確認エラー: {e}")
            raise

    async def list_blobs(self, prefix: Optional[str] = None) -> list[str]:
        """
        Blob 一覧を取得

        コンテナ内の Blob 一覧を取得します。
        プレフィックスを指定して特定のディレクトリ配下のみ取得することも可能です。

        Args:
            prefix: プレフィックス（フィルター）
                   例: "uploads/" で uploads/ 配下のみ取得

        Returns:
            list[str]: Blob 名のリスト

        Raises:
            Exception: 一覧取得に失敗した場合

        Note:
            Blob Storage にはディレクトリという概念はなく、
            Blob 名にスラッシュを含めることで仮想的な階層を表現します。
            list_blobs は仮想ディレクトリを考慮せずフラットにリストします。
        """
        try:
            container_client = await self._get_container_client()

            # ============================================================
            # Blob 一覧の取得
            # ============================================================
            blobs = []
            # list_blobs は AsyncIterator を返す
            async for blob in container_client.list_blobs(name_starts_with=prefix):
                blobs.append(blob.name)

            # 取得完了ログ（DEBUG レベル）
            logger.debug(
                f"Blob一覧取得: container={self.container_name}, "
                f"prefix={prefix}, count={len(blobs)}"
            )

            return blobs

        except Exception as e:
            logger.error(f"Blob一覧取得エラー: {e}")
            raise

    async def close(self) -> None:
        """
        クライアントをクローズ

        使用後は必ず呼び出してリソースを解放してください。
        HTTP 接続やソケットなどのリソースが適切にクリーンアップされます。
        """
        if self._client:
            await self._client.close()
            self._client = None
