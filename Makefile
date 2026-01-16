# ============================================================
# Azurite Queue Tutorial - Makefile
# ============================================================
#
# 概要:
#   開発タスクを簡単に実行するための Makefile。
#   よく使うコマンドを短縮して実行できます。
#
# 使用方法:
#   make help    - 利用可能なコマンド一覧を表示
#   make up      - コンテナを起動
#   make down    - コンテナを停止
#
# 設計上の決定:
#   - すべてのターゲットを .PHONY で宣言（ファイル名と競合しない）
#   - 各コマンドに ## コメントでヘルプを記載
#   - セクションごとにグループ化して可読性を向上
#
# ============================================================

# ============================================================
# ヘルプコマンド
# ============================================================
# make または make help で利用可能なコマンド一覧を表示
#
# 実装の仕組み:
#   grep で "ターゲット: ## 説明" の形式を抽出し、整形して表示
#   ANSI カラーコードでターゲット名を強調（\033[36m = シアン）
#
.PHONY: help
help: ## このヘルプメッセージを表示
	@echo "Azurite Queue Tutorial - 利用可能なコマンド:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ============================================================
# 開発環境管理
# ============================================================
# Docker Compose を使用したコンテナの起動・停止・再起動
#

# コンテナ起動（フォアグラウンド）
# ログがターミナルに出力される
# Ctrl+C で停止
.PHONY: up
up: ## Dockerコンテナ起動
	docker compose up

# コンテナ起動（バックグラウンド）
# -d: detached モード（バックグラウンド実行）
# ログは docker compose logs で確認
.PHONY: up-d
up-d: ## Dockerコンテナ起動（バックグラウンド）
	docker compose up -d

# コンテナビルド＆起動
# --build: イメージを再ビルドしてから起動
# Dockerfile や requirements.txt を変更した場合に使用
.PHONY: up-build
up-build: ## Dockerコンテナビルド＆起動
	docker compose up --build

# コンテナ停止
# コンテナとネットワークを削除
# ボリュームは保持される
.PHONY: down
down: ## Dockerコンテナ停止
	docker compose down

# コンテナ再起動
# コードを変更した後、ホットリロードが効かない場合に使用
.PHONY: restart
restart: ## Dockerコンテナ再起動
	docker compose restart

# ============================================================
# ログ確認
# ============================================================
# コンテナのログをリアルタイムで表示
#

# API サーバーのログを表示
# -f: フォロー（リアルタイム更新）
# リクエスト/レスポンスの確認に使用
.PHONY: logs
logs: ## APIログ表示
	docker compose logs -f api

# Worker のログを表示
# -f: フォロー（リアルタイム更新）
# メッセージ処理の確認に使用
.PHONY: worker-logs
worker-logs: ## Workerログ表示
	docker compose logs -f worker

# 全コンテナのログを表示
# Azurite、API、Worker すべてのログを統合表示
# システム全体の動作確認に使用
.PHONY: logs-all
logs-all: ## 全コンテナのログ表示
	docker compose logs -f

# ============================================================
# シェルアクセス
# ============================================================
# コンテナ内でシェルを起動してデバッグ
#

# API コンテナ内でシェル起動
# Python の対話モードやファイル確認に使用
# exit でコンテナから離脱
.PHONY: shell
shell: ## APIコンテナ内でシェル起動
	docker exec -it tutorial-api /bin/bash

# Worker コンテナ内でシェル起動
.PHONY: worker-shell
worker-shell: ## Workerコンテナ内でシェル起動
	docker exec -it tutorial-worker /bin/bash

# ============================================================
# テスト
# ============================================================
# API の動作確認用コマンド
#

# ヘルスチェック
# API サーバーの稼働状態を確認
# 正常: {"status": "healthy", ...}
.PHONY: test-health
test-health: ## ヘルスチェック
	@curl -s http://localhost:8000/health | python -m json.tool

# タスク作成テスト
# POST /tasks エンドポイントをテスト
# 正常: {"task_id": "task-xxx", ...}
.PHONY: test-task
test-task: ## タスク作成テスト
	@curl -s -X POST http://localhost:8000/tasks \
		-H "Content-Type: application/json" \
		-d '{"name": "テストタスク", "description": "説明文"}' | python -m json.tool

# ============================================================
# 開発サポート
# ============================================================
# クリーンアップやステータス確認
#

# 一時ファイル削除
# Python のキャッシュファイル（__pycache__, *.pyc）を削除
# Git にコミットしない不要ファイルの整理
.PHONY: clean
clean: ## 一時ファイル削除
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

# コンテナ状態確認
# 各コンテナの起動状態、ポート、ヘルス状態を表示
.PHONY: status
status: ## Dockerコンテナ状態確認
	docker compose ps

# ============================================================
# クイックスタート
# ============================================================
# 初回セットアップや完全停止用のショートカット
#

# 初回起動（ビルド + 起動）
# 依存ターゲット: up-build
# リポジトリをクローンした直後に実行
.PHONY: start
start: up-build ## 初回起動（ビルド + 起動）

# 完全停止（コンテナ停止 + クリーンアップ）
# 依存ターゲット: down, clean
# 作業終了時や問題発生時に使用
.PHONY: stop
stop: down clean ## 完全停止（コンテナ停止 + クリーンアップ）

# ============================================================
# デフォルトターゲット
# ============================================================
# make コマンドを引数なしで実行した場合に help を表示
#
.DEFAULT_GOAL := help
