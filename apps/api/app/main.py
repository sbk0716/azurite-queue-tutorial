"""
FastAPI アプリケーション

Azurite Queue Tutorial のメインエントリーポイント
"""

# ============================================================
# インポート
# ============================================================

# 標準ライブラリ: ログ出力用モジュール
import logging

# FastAPI: 高性能なPython Webフレームワーク
from fastapi import FastAPI
# CORSMiddleware: クロスオリジンリクエストを許可するためのミドルウェア
from fastapi.middleware.cors import CORSMiddleware

# アプリケーション内部のルーター（エンドポイント定義）をインポート
from apps.api.app.routers import health, tasks, files

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
# FastAPI アプリケーションの初期化
# ============================================================

# FastAPIアプリケーションインスタンスを作成
# - title: APIドキュメントに表示されるタイトル
# - description: APIの説明文
# - version: APIのバージョン番号
app = FastAPI(
    title="Azurite Queue Tutorial",
    description="Python + Azurite で学ぶ実践キューイングシステム入門",
    version="1.0.0",
)

# ============================================================
# CORS（Cross-Origin Resource Sharing）設定
# ============================================================

# CORSミドルウェアを追加（開発環境用の緩い設定）
# 注意: 本番環境では allow_origins を制限すること
app.add_middleware(
    CORSMiddleware,
    # すべてのオリジンからのリクエストを許可（開発環境用）
    allow_origins=["*"],
    # 認証情報（Cookie等）を含むリクエストを許可
    allow_credentials=True,
    # すべてのHTTPメソッドを許可
    allow_methods=["*"],
    # すべてのHTTPヘッダーを許可
    allow_headers=["*"],
)

# ============================================================
# ルーター登録
# ============================================================

# 各機能のルーターをアプリケーションに登録
# - health: ヘルスチェックエンドポイント（/health）
# - tasks: タスク管理エンドポイント（/tasks）
# - files: ファイル管理エンドポイント（/files）
app.include_router(health.router)
app.include_router(tasks.router)
app.include_router(files.router)


# ============================================================
# ルートエンドポイント
# ============================================================


@app.get("/")
async def root():
    """
    ルートエンドポイント

    APIの基本情報を返す。
    /docs でSwagger UIによるAPIドキュメントを確認できる。
    """
    return {
        # サービス名
        "service": "Azurite Queue Tutorial",
        # バージョン
        "version": "1.0.0",
        # APIドキュメントへのパス
        "docs": "/docs",
    }


# ============================================================
# ライフサイクルイベント
# ============================================================


@app.on_event("startup")
async def startup_event():
    """
    アプリケーション起動時の処理

    FastAPIサーバー起動時に一度だけ実行される。
    データベース接続の初期化などに使用する。
    """
    # 起動ログを出力
    logger.info("Azurite Queue Tutorial API 起動")


@app.on_event("shutdown")
async def shutdown_event():
    """
    アプリケーション終了時の処理

    FastAPIサーバー終了時に一度だけ実行される。
    リソースのクリーンアップなどに使用する。
    """
    # 終了ログを出力
    logger.info("Azurite Queue Tutorial API 終了")
