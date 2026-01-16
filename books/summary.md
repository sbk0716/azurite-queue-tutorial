# Python + Azuriteで学ぶ 実践キューイングシステム入門

Azure Storage の 3 つのサービス（Queue, Blob, Table）を使った非同期処理システムを、Azurite（ローカルエミュレーター）を使って段階的に構築するチュートリアルです。

## 学べること

- Azure Queue Storage による非同期メッセージング
- Azure Blob Storage によるファイル管理
- Azure Table Storage によるメタデータ・ステータス管理
- FastAPI + Worker アーキテクチャの実装パターン
- Dead Letter Queue（DLQ）によるエラーハンドリング

## 対象読者

- Python の基礎知識がある方
- Docker / Docker Compose の基本操作ができる方
- 非同期処理やメッセージキューに興味がある方

## 前提知識

**必須:**
- Python の基本文法（async/await を含む）
- Docker / Docker Compose の基本操作
- REST API の基礎知識

**あると良い:**
- FastAPI の経験
- Azure の基礎知識

---

# 免責事項

本書は執筆時点（2026年1月）の情報に基づいています。ライブラリのバージョンアップにより動作が変わる可能性があるため、最新情報は各公式ドキュメントをご確認ください。

本書のコードは**学習目的**であり、本番環境での使用には認証強化・監視設定・セキュリティ対策等の追加が必要です。

本書の情報はご自身の責任でご利用ください。著者は内容の保証を行わず、利用に起因する損害について一切の責任を負いません。
