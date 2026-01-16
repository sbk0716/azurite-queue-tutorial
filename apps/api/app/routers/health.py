"""
ヘルスチェックエンドポイント

アプリケーションの稼働状態を確認するためのエンドポイント。
Kubernetesのlivenessプローブやロードバランサーのヘルスチェックに使用する。
"""

# ============================================================
# インポート
# ============================================================

# 標準ライブラリ: 日時操作用モジュール
from datetime import datetime, timezone

# FastAPI: ルーター機能
from fastapi import APIRouter

# アプリケーション設定をインポート
from libs.shared.config import settings

# ============================================================
# ルーター設定
# ============================================================

# APIルーターを作成
# - tags: Swagger UIでのグループ化に使用
router = APIRouter(tags=["Health"])


# ============================================================
# ヘルスチェックエンドポイント
# ============================================================


@router.get("/health")
async def health_check() -> dict:
    """
    ヘルスチェック

    アプリケーションの稼働状態を返す。
    外部システム（ロードバランサー、Kubernetes等）からの
    定期的なヘルスチェックに使用される。

    Returns:
        dict: ヘルス情報
            - status: "healthy"（正常稼働中）
            - service: サービス名
            - env: 実行環境（local, dev, prod等）
            - timestamp: チェック時刻（ISO 8601形式）
    """
    return {
        # サービスの稼働状態（正常）
        "status": "healthy",
        # サービス名
        "service": "azurite-queue-tutorial",
        # 実行環境（設定から取得）
        "env": settings.env,
        # 現在時刻をUTCのISO 8601形式で返す
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
