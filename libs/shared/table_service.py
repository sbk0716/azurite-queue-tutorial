"""
Azure Table Storage サービス

Table Storage の操作を提供します。
Azurite（ローカル開発）と Azure（本番環境）の両方に対応。

このモジュールは以下の機能を提供します:
- エンティティの作成（create_entity）
- エンティティの更新（update_entity）
- エンティティの取得（get_entity）
- エンティティの削除（delete_entity）
- エンティティのクエリ（query_entities）

設計上の決定:
- 非同期クライアント（aio）を使用して高スループットを実現
- テーブルが存在しない場合は自動作成
- 更新はマージモード（指定したプロパティのみ更新）

Azure Table Storage の概念:
- Table: エンティティの集合（RDB のテーブルに相当）
- Entity: 1 行のデータ（RDB の行に相当）
- PartitionKey: パーティション分割のキー（必須）
- RowKey: パーティション内での一意キー（必須）
- PartitionKey + RowKey でエンティティを一意に識別

パーティション設計:
- 同じ PartitionKey を持つエンティティは同じサーバーに配置される
- 高速なポイントクエリ: PartitionKey + RowKey を指定
- パーティションスキャン: PartitionKey のみ指定
- テーブルスキャン: キーなし（避けるべき）
"""

# ============================================================
# インポート
# ============================================================

# 標準ライブラリ: ログ出力用モジュール
import logging
# 標準ライブラリ: 日時操作用モジュール
from datetime import datetime, timezone
# 標準ライブラリ: 型ヒント用モジュール
from typing import Any, Optional

# Azure SDK: Table Storage 非同期クライアント
from azure.data.tables.aio import TableServiceClient, TableClient
# Azure SDK: 例外クラス
# リソースが既に存在する場合にスローされる
from azure.core.exceptions import ResourceExistsError

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

# デフォルトテーブル名
# ファイルメタデータの保存に使用
DEFAULT_TABLE = "files"


# ============================================================
# Table Storage サービスクラス
# ============================================================


class TableService:
    """
    Table Storage 操作サービス

    エンティティの作成・更新・取得・削除を提供します。

    使用例:
        service = TableService()
        try:
            # 作成
            await service.create_entity("pk", "rk", {"Status": "pending"})

            # 更新
            await service.update_entity("pk", "rk", {"Status": "completed"})

            # 取得
            entity = await service.get_entity("pk", "rk")

            # クエリ
            entities = await service.query_entities("PartitionKey eq 'pk'")

            # 削除
            await service.delete_entity("pk", "rk")
        finally:
            await service.close()

    Note:
        使用後は必ず close() を呼び出してリソースを解放してください。
    """

    def __init__(self, table_name: str = DEFAULT_TABLE):
        """
        Table Service の初期化

        Args:
            table_name: テーブル名
                       デフォルト: "files"
        """
        # 設定から接続文字列を取得
        self.connection_string = settings.storage_connection_string
        # 使用するテーブル名
        self.table_name = table_name
        # クライアントは遅延初期化
        self._client: Optional[TableServiceClient] = None

    async def _get_client(self) -> TableServiceClient:
        """
        Table Service Client を取得

        遅延初期化パターン:
        - 最初の呼び出し時にクライアントを生成
        - 以降は同じインスタンスを再利用

        Returns:
            TableServiceClient: Table Storage クライアント
        """
        if self._client is None:
            # 接続文字列からクライアントを生成
            self._client = TableServiceClient.from_connection_string(
                self.connection_string
            )
        return self._client

    async def _get_table_client(self) -> TableClient:
        """
        Table Client を取得（自動作成）

        テーブルが存在しない場合は自動的に作成します。
        これにより、初回使用時でもエラーなく動作します。

        Returns:
            TableClient: テーブルクライアント
        """
        client = await self._get_client()
        table_client = client.get_table_client(self.table_name)

        # テーブルの自動作成
        try:
            await client.create_table(self.table_name)
            logger.info(f"テーブル作成: {self.table_name}")
        except ResourceExistsError:
            # 既に存在する場合は無視
            pass
        except Exception as e:
            # その他のエラーは無視（Azurite の互換性のため）
            logger.debug(f"テーブル作成スキップ: {e}")

        return table_client

    async def create_entity(
        self,
        partition_key: str,
        row_key: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        エンティティを作成

        新しいエンティティをテーブルに作成します。
        CreatedAt と UpdatedAt が自動的に設定されます。

        Args:
            partition_key: パーティションキー
                          同じキーのエンティティは同じパーティションに配置
                          例: "files"（全ファイルを同じパーティションに）
            row_key: 行キー
                    パーティション内での一意識別子
                    例: "file-a1b2c3d4"（ファイルID）
            data: エンティティデータ
                 任意のプロパティを含む辞書
                 例: {"Status": "pending", "Filename": "doc.pdf"}

        Returns:
            dict: 作成されたエンティティ
                 PartitionKey, RowKey, CreatedAt, UpdatedAt を含む

        Raises:
            Exception: 作成に失敗した場合
                      例: 同じ PartitionKey + RowKey が既に存在

        Note:
            PartitionKey + RowKey は一意である必要があります。
            既に存在する場合は ResourceExistsError がスローされます。
        """
        try:
            table_client = await self._get_table_client()

            # ============================================================
            # エンティティの構築
            # ============================================================
            # 現在時刻を UTC ISO 8601 形式で取得
            now = datetime.now(timezone.utc).isoformat()

            entity = {
                # 必須キー
                "PartitionKey": partition_key,
                "RowKey": row_key,
                # 自動生成フィールド
                "CreatedAt": now,
                "UpdatedAt": now,
                # ユーザー指定データをマージ
                **data,
            }

            # エンティティを作成
            await table_client.create_entity(entity)

            # 作成完了ログ
            logger.info(
                f"エンティティ作成: table={self.table_name}, "
                f"pk={partition_key}, rk={row_key}"
            )

            return entity

        except Exception as e:
            logger.error(f"エンティティ作成エラー: {e}")
            raise

    async def update_entity(
        self,
        partition_key: str,
        row_key: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        エンティティを更新（マージ）

        既存のエンティティを更新します。
        マージモードのため、指定したプロパティのみが更新されます。
        UpdatedAt は自動的に更新されます。

        Args:
            partition_key: パーティションキー
            row_key: 行キー
            data: 更新データ
                 更新したいプロパティのみを含む辞書
                 例: {"Status": "completed"}

        Returns:
            dict: 更新されたエンティティ（部分的）
                 PartitionKey, RowKey, UpdatedAt, 指定したデータを含む

        Raises:
            Exception: 更新に失敗した場合
                      例: エンティティが存在しない

        Note:
            マージモード（mode="merge"）を使用しているため、
            指定していないプロパティはそのまま保持されます。
        """
        try:
            table_client = await self._get_table_client()

            # ============================================================
            # 更新エンティティの構築
            # ============================================================
            entity = {
                # 必須キー（識別用）
                "PartitionKey": partition_key,
                "RowKey": row_key,
                # 更新日時を自動設定
                "UpdatedAt": datetime.now(timezone.utc).isoformat(),
                # ユーザー指定データをマージ
                **data,
            }

            # マージモードで更新
            # mode="merge": 指定したプロパティのみ更新
            # mode="replace": エンティティ全体を置換
            await table_client.update_entity(entity, mode="merge")

            # 更新完了ログ
            logger.info(
                f"エンティティ更新: table={self.table_name}, "
                f"pk={partition_key}, rk={row_key}"
            )

            return entity

        except Exception as e:
            logger.error(f"エンティティ更新エラー: {e}")
            raise

    async def get_entity(
        self,
        partition_key: str,
        row_key: str,
    ) -> Optional[dict[str, Any]]:
        """
        エンティティを取得

        PartitionKey と RowKey を指定してエンティティを取得します。
        これはポイントクエリで、最も高速なアクセス方法です。

        Args:
            partition_key: パーティションキー
            row_key: 行キー

        Returns:
            dict: エンティティ（存在しない場合は None）
                 すべてのプロパティを含む辞書

        Raises:
            Exception: 取得に失敗した場合（Not Found 以外）

        Note:
            エンティティが存在しない場合は None を返します。
            例外はスローしません。
        """
        try:
            table_client = await self._get_table_client()

            # ポイントクエリでエンティティを取得
            entity = await table_client.get_entity(partition_key, row_key)

            # 取得完了ログ（DEBUG レベル）
            logger.debug(
                f"エンティティ取得: table={self.table_name}, "
                f"pk={partition_key}, rk={row_key}"
            )

            # Azure SDK のエンティティを Python 辞書に変換
            return dict(entity)

        except Exception as e:
            # エンティティが存在しない場合は None を返す
            # Azure SDK は ResourceNotFoundError をスローする
            if "ResourceNotFound" in str(e) or "does not exist" in str(e):
                return None
            logger.error(f"エンティティ取得エラー: {e}")
            raise

    async def delete_entity(
        self,
        partition_key: str,
        row_key: str,
    ) -> None:
        """
        エンティティを削除

        PartitionKey と RowKey を指定してエンティティを削除します。

        Args:
            partition_key: パーティションキー
            row_key: 行キー

        Raises:
            Exception: 削除に失敗した場合
                      例: エンティティが存在しない
        """
        try:
            table_client = await self._get_table_client()

            # エンティティを削除
            await table_client.delete_entity(partition_key, row_key)

            # 削除完了ログ
            logger.info(
                f"エンティティ削除: table={self.table_name}, "
                f"pk={partition_key}, rk={row_key}"
            )

        except Exception as e:
            logger.error(f"エンティティ削除エラー: {e}")
            raise

    async def query_entities(
        self,
        filter_query: Optional[str] = None,
        select: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """
        エンティティをクエリ

        条件に一致するエンティティを検索します。
        OData 形式のフィルタークエリを使用できます。

        Args:
            filter_query: フィルタクエリ（OData 形式）
                         例: "PartitionKey eq 'files'"
                         例: "Status eq 'pending'"
                         例: "PartitionKey eq 'files' and Status eq 'pending'"
            select: 取得するカラム（省略時は全カラム）
                   例: ["RowKey", "Status", "Filename"]

        Returns:
            list[dict]: エンティティリスト

        Raises:
            Exception: クエリに失敗した場合

        Note:
            パフォーマンスを考慮したクエリ設計:
            1. PartitionKey を含むクエリが最も効率的
            2. PartitionKey + RowKey のポイントクエリが最速
            3. テーブルスキャン（フィルタなし）は避ける
        """
        try:
            table_client = await self._get_table_client()

            # ============================================================
            # クエリの実行
            # ============================================================
            entities = []
            # query_entities は AsyncIterator を返す
            async for entity in table_client.query_entities(
                query_filter=filter_query,
                select=select,
            ):
                # Azure SDK のエンティティを Python 辞書に変換
                entities.append(dict(entity))

            # クエリ完了ログ（DEBUG レベル）
            logger.debug(
                f"エンティティクエリ: table={self.table_name}, "
                f"filter={filter_query}, count={len(entities)}"
            )

            return entities

        except Exception as e:
            logger.error(f"エンティティクエリエラー: {e}")
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
