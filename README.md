# Azurite Queue Tutorial

Python + Azurite で学ぶ実践キューイングシステム入門のサンプルコード。

## 概要

Azure Storage の 3 つのサービス（Queue, Blob, Table）を使った非同期処理システムを段階的に構築します。

## クイックスタート

```bash
# コンテナ起動
make start

# ヘルスチェック
make test-health

# タスク作成テスト
make test-task

# ログ確認
make logs-all
```

## 構成

```
.
├── apps/
│   ├── api/          # FastAPI サーバー
│   └── worker/       # Queue ワーカー
├── libs/shared/      # 共通ライブラリ
├── config/           # 設定ファイル
└── books/            # Zenn Books マークダウン
```

## 段階的学習

1. **Step 1 (Chapter 1-7)**: Queue だけで動くシンプルなタスクキュー
2. **Step 2 (Chapter 8)**: Blob Storage でファイル管理を追加
3. **Step 3 (Chapter 9)**: Table Storage でステータス管理を追加

## 企業プロキシ環境での注意

SSL 証明書エラーが発生する場合は、以下の対応が必要です：

1. `cert/` ディレクトリにカスタム証明書を配置
2. `Dockerfile` と `compose.yaml` の証明書関連コメントを解除

詳細は `Dockerfile` 内のコメントを参照してください。
