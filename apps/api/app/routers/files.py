"""
ファイル管理エンドポイント

ファイルのアップロードと Blob Storage への保存、
Table Storage へのメタデータ登録、Queue への投入を行います。
Step 2-3 で学習する 3 つの Azure Storage サービスの連携を実装しています。
"""

# ============================================================
# インポート
# ============================================================

# 標準ライブラリ: UUID生成用モジュール
import uuid
# 標準ライブラリ: 日時操作用モジュール
from datetime import datetime, timezone
# 標準ライブラリ: 型ヒント用モジュール
from typing import Optional

# FastAPI: ルーター機能、HTTPエラー、ファイルアップロード
from fastapi import APIRouter, HTTPException, UploadFile, File
# Pydantic: リクエスト/レスポンスのデータ検証とシリアライズ
from pydantic import BaseModel

# アプリケーション内部のストレージサービスをインポート
# Blob: ファイル実体の保存
from libs.shared.blob_service import BlobService
# Queue: 非同期メッセージング
from libs.shared.queue_service import QueueService
# Table: メタデータ・ステータス管理
from libs.shared.table_service import TableService

# ============================================================
# ルーター設定
# ============================================================

# APIルーターを作成
# - prefix: すべてのエンドポイントに /files プレフィックスを付与
# - tags: Swagger UIでのグループ化に使用
router = APIRouter(prefix="/files", tags=["Files"])

# ============================================================
# 定数
# ============================================================

# Table Storage のパーティションキー
# すべてのファイルエンティティを同じパーティションに配置
# 注意: 大規模システムでは適切なパーティション戦略が必要
DEFAULT_PARTITION_KEY = "files"


# ============================================================
# リクエスト/レスポンスモデル
# ============================================================


class FileResponse(BaseModel):
    """
    ファイルレスポンスのデータモデル

    APIレスポンスとして返すファイル情報の構造を定義。
    """

    # ファイルID（システムが自動生成）
    file_id: str
    # アップロード時のファイル名
    filename: str
    # Content-Type（MIME タイプ）
    content_type: Optional[str]
    # ファイルサイズ（バイト）
    size: int
    # 処理ステータス（pending, processing, completed, failed）
    status: str
    # Blob Storage 内のファイルパス
    blob_path: str
    # 作成日時（ISO 8601形式）
    created_at: str


# ============================================================
# ファイルアップロードエンドポイント
# ============================================================


@router.post("/upload", response_model=FileResponse, status_code=201)
async def upload_file(file: UploadFile = File(...)) -> FileResponse:
    """
    ファイルをアップロード

    3 つの Azure Storage サービスを連携させてファイルを処理:
    1. Blob Storage: ファイル実体を保存
    2. Table Storage: メタデータを登録（status=pending）
    3. Queue Storage: 処理メッセージを投入

    Args:
        file: アップロードファイル（multipart/form-data）

    Returns:
        FileResponse: アップロードしたファイルの情報
    """
    # ファイルIDを生成（UUIDの先頭8文字を使用）
    # 例: "file-a1b2c3d4"
    file_id = f"file-{uuid.uuid4().hex[:8]}"
    # 作成日時をUTCのISO 8601形式で記録
    created_at = datetime.now(timezone.utc).isoformat()

    # アップロードされたファイルの内容を読み込む
    # 注意: 大きなファイルの場合はストリーミング処理を検討
    content = await file.read()

    # Blob Storage 内のファイルパスを構築
    # 形式: "uploads/{file_id}/{filename}"
    blob_path = f"uploads/{file_id}/{file.filename}"

    # ============================================================
    # 1. Blob Storage にファイル実体をアップロード
    # ============================================================
    blob_service = BlobService()
    try:
        await blob_service.upload(
            # Blob 名（ファイルパス）
            blob_name=blob_path,
            # ファイルのバイナリデータ
            data=content,
            # Content-Type（ダウンロード時に使用）
            content_type=file.content_type,
        )
    finally:
        # 必ずクライアントをクローズしてリソースを解放
        await blob_service.close()

    # レスポンス用のファイルデータを構築
    file_data = {
        "file_id": file_id,
        "filename": file.filename,
        "content_type": file.content_type or "",
        "size": len(content),
        # 初期ステータスは "pending"（処理待ち）
        "status": "pending",
        "blob_path": blob_path,
        "created_at": created_at,
    }

    # ============================================================
    # 2. Table Storage にメタデータを登録
    # ============================================================
    table_service = TableService()
    try:
        await table_service.create_entity(
            # パーティションキー（同じパーティション内のデータは高速にアクセス可能）
            partition_key=DEFAULT_PARTITION_KEY,
            # 行キー（パーティション内での一意識別子）
            row_key=file_id,
            # 追加のエンティティプロパティ
            data={
                "Filename": file.filename,
                "ContentType": file.content_type or "",
                "Size": len(content),
                "Status": "pending",
                "BlobPath": blob_path,
            },
        )
    finally:
        # 必ずクライアントをクローズ
        await table_service.close()

    # ============================================================
    # 3. Queue Storage にメッセージを投入
    # ============================================================
    queue_service = QueueService()
    try:
        # Worker が処理するメッセージを構築
        message = {
            "file_id": file_id,
            "filename": file.filename,
            "blob_path": blob_path,
            # processor_type で Worker がどのプロセッサーを使うか判断
            "processor_type": "file",
        }
        # "task-queue" キューにメッセージを投入
        await queue_service.enqueue("task-queue", message)
    finally:
        # 必ずクライアントをクローズ
        await queue_service.close()

    # ファイル情報をレスポンスとして返却
    return FileResponse(**file_data)


# ============================================================
# ファイル情報取得エンドポイント
# ============================================================


@router.get("/{file_id}", response_model=FileResponse)
async def get_file(file_id: str) -> FileResponse:
    """
    ファイル情報を取得（Table Storage から）

    Table Storage に保存されたメタデータを取得して返す。

    Args:
        file_id: ファイルID（パスパラメータ）

    Returns:
        FileResponse: ファイル情報

    Raises:
        HTTPException: ファイルが見つからない場合（404 Not Found）
    """
    table_service = TableService()
    try:
        # Table Storage からエンティティを取得
        entity = await table_service.get_entity(
            partition_key=DEFAULT_PARTITION_KEY,
            row_key=file_id,
        )
    finally:
        # 必ずクライアントをクローズ
        await table_service.close()

    # エンティティが見つからない場合は 404 エラー
    if entity is None:
        raise HTTPException(status_code=404, detail="File not found")

    # Table Storage のエンティティを FileResponse に変換して返却
    return FileResponse(
        # RowKey がファイルID
        file_id=entity["RowKey"],
        filename=entity.get("Filename", ""),
        content_type=entity.get("ContentType"),
        size=entity.get("Size", 0),
        status=entity.get("Status", "unknown"),
        blob_path=entity.get("BlobPath", ""),
        created_at=entity.get("CreatedAt", ""),
    )


# ============================================================
# ファイルステータス取得エンドポイント
# ============================================================


@router.get("/{file_id}/status")
async def get_file_status(file_id: str) -> dict:
    """
    ファイル処理ステータスを取得（Table Storage から）

    Worker による処理の進捗状況を確認するためのエンドポイント。
    ポーリングによるステータス確認に使用する。

    Args:
        file_id: ファイルID（パスパラメータ）

    Returns:
        dict: ステータス情報
            - file_id: ファイルID
            - status: 処理ステータス
            - updated_at: 最終更新日時

    Raises:
        HTTPException: ファイルが見つからない場合（404 Not Found）
    """
    table_service = TableService()
    try:
        # Table Storage からエンティティを取得
        entity = await table_service.get_entity(
            partition_key=DEFAULT_PARTITION_KEY,
            row_key=file_id,
        )
    finally:
        # 必ずクライアントをクローズ
        await table_service.close()

    # エンティティが見つからない場合は 404 エラー
    if entity is None:
        raise HTTPException(status_code=404, detail="File not found")

    # ステータス情報のみを返却
    return {
        "file_id": file_id,
        "status": entity.get("Status", "unknown"),
        "updated_at": entity.get("UpdatedAt", ""),
    }


# ============================================================
# ファイル一覧取得エンドポイント
# ============================================================


@router.get("")
async def list_files() -> list[FileResponse]:
    """
    ファイル一覧を取得（Table Storage から）

    Table Storage に保存されているすべてのファイルのメタデータを返す。

    Returns:
        list[FileResponse]: ファイル一覧
    """
    table_service = TableService()
    try:
        # Table Storage からエンティティを検索
        # OData フィルター構文で PartitionKey を指定
        entities = await table_service.query_entities(
            filter_query=f"PartitionKey eq '{DEFAULT_PARTITION_KEY}'"
        )
    finally:
        # 必ずクライアントをクローズ
        await table_service.close()

    # 各エンティティを FileResponse に変換してリストで返却
    return [
        FileResponse(
            file_id=e["RowKey"],
            filename=e.get("Filename", ""),
            content_type=e.get("ContentType"),
            size=e.get("Size", 0),
            status=e.get("Status", "unknown"),
            blob_path=e.get("BlobPath", ""),
            created_at=e.get("CreatedAt", ""),
        )
        for e in entities
    ]
