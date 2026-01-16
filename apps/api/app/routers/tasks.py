"""
タスク管理エンドポイント

タスクの作成と Queue への投入を行います。
Step 1 の学習用として、シンプルなメモリ内ストアを使用しています。
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

# FastAPI: ルーター機能とHTTPエラーレスポンス
from fastapi import APIRouter, HTTPException
# Pydantic: リクエスト/レスポンスのデータ検証とシリアライズ
from pydantic import BaseModel

# アプリケーション内部のQueue操作サービスをインポート
from libs.shared.queue_service import QueueService

# ============================================================
# ルーター設定
# ============================================================

# APIルーターを作成
# - prefix: すべてのエンドポイントに /tasks プレフィックスを付与
# - tags: Swagger UIでのグループ化に使用
router = APIRouter(prefix="/tasks", tags=["Tasks"])

# ============================================================
# メモリ内ストア（Step 1 用の簡易実装）
# ============================================================

# タスク情報をメモリ内の辞書で管理
# 注意: サーバー再起動でデータは消える（本番では Table Storage を使用）
# キー: task_id、値: タスク情報の辞書
task_store: dict[str, dict] = {}


# ============================================================
# リクエスト/レスポンスモデル
# ============================================================


class TaskCreate(BaseModel):
    """
    タスク作成リクエストのデータモデル

    クライアントから送信されるJSONリクエストの構造を定義。
    Pydanticによる自動バリデーションが行われる。
    """

    # タスク名（必須）
    name: str
    # タスクの説明（オプション）
    description: Optional[str] = None


class TaskResponse(BaseModel):
    """
    タスクレスポンスのデータモデル

    APIレスポンスとして返すタスク情報の構造を定義。
    """

    # タスクID（システムが自動生成）
    task_id: str
    # タスク名
    name: str
    # タスクの説明
    description: Optional[str]
    # タスクのステータス（pending, processing, completed, failed）
    status: str
    # 作成日時（ISO 8601形式）
    created_at: str


# ============================================================
# タスク作成エンドポイント
# ============================================================


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(task: TaskCreate) -> TaskResponse:
    """
    タスクを作成し、Queue に投入

    処理フロー:
    1. タスクIDを生成
    2. タスク情報をメモリストアに保存
    3. Queue にメッセージを投入（Worker が後で処理）
    4. タスク情報をレスポンスとして返却

    Args:
        task: タスク作成リクエスト（name, description）

    Returns:
        TaskResponse: 作成されたタスク情報
    """
    # タスクIDを生成（UUIDの先頭8文字を使用）
    # 例: "task-a1b2c3d4"
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    # 作成日時をUTCのISO 8601形式で記録
    created_at = datetime.now(timezone.utc).isoformat()

    # タスク情報を辞書として構築
    task_data = {
        "task_id": task_id,
        "name": task.name,
        "description": task.description,
        # 初期ステータスは "pending"（処理待ち）
        "status": "pending",
        "created_at": created_at,
    }
    # メモリストアに保存
    task_store[task_id] = task_data

    # Queue にメッセージを投入
    # Worker がこのメッセージを取得して処理を行う
    queue_service = QueueService()
    try:
        # Queue に投入するメッセージを構築
        message = {
            "task_id": task_id,
            "name": task.name,
            "description": task.description,
            # processor_type で Worker がどのプロセッサーを使うか判断
            "processor_type": "task",
        }
        # "task-queue" キューにメッセージを投入
        await queue_service.enqueue("task-queue", message)
    finally:
        # 必ずクライアントをクローズしてリソースを解放
        await queue_service.close()

    # 作成したタスク情報をレスポンスとして返却
    return TaskResponse(**task_data)


# ============================================================
# タスク取得エンドポイント
# ============================================================


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str) -> TaskResponse:
    """
    タスク情報を取得

    指定されたタスクIDに対応するタスク情報をメモリストアから取得。

    Args:
        task_id: タスクID（パスパラメータ）

    Returns:
        TaskResponse: タスク情報

    Raises:
        HTTPException: タスクが見つからない場合（404 Not Found）
    """
    # メモリストアにタスクが存在するか確認
    if task_id not in task_store:
        # 存在しない場合は 404 エラーを返す
        raise HTTPException(status_code=404, detail="Task not found")

    # タスク情報をレスポンスとして返却
    return TaskResponse(**task_store[task_id])


# ============================================================
# タスク一覧エンドポイント
# ============================================================


@router.get("")
async def list_tasks() -> list[TaskResponse]:
    """
    タスク一覧を取得

    メモリストアに保存されているすべてのタスクを返す。

    Returns:
        list[TaskResponse]: タスク一覧
    """
    # メモリストアの全タスクをリストに変換して返却
    return [TaskResponse(**task) for task in task_store.values()]


# ============================================================
# 内部関数（Worker からの呼び出し用）
# ============================================================


def update_task_status(task_id: str, status: str) -> None:
    """
    タスクのステータスを更新

    Worker から呼び出され、タスクの処理状態を更新する。
    注意: この関数はAPIエンドポイントではない内部関数。

    Args:
        task_id: タスクID
        status: 新しいステータス（processing, completed, failed）
    """
    # メモリストアにタスクが存在する場合のみ更新
    if task_id in task_store:
        task_store[task_id]["status"] = status
