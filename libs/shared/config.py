"""
設定管理モジュール

環境変数と YAML 設定ファイルからアプリケーション設定を読み込みます。

このモジュールは以下の役割を持ちます:
- 環境変数から接続文字列等を取得
- config/queues.yaml から Worker 設定を読み込み
- 設定をデータクラスとして構造化

設計上の決定:
- dataclass を使用して設定を型安全に管理
- シングルトンパターンでアプリケーション全体で設定を共有
- YAML ファイルが存在しない場合はデフォルト値を使用
"""

# ============================================================
# インポート
# ============================================================

# 標準ライブラリ: OS環境変数・ファイルパス操作用モジュール
import os
# 標準ライブラリ: データクラス定義用デコレータ
# dataclass: クラスをデータ保持用に最適化
from dataclasses import dataclass
# 標準ライブラリ: 型ヒント用モジュール
from typing import Optional

# サードパーティ: YAML パーサー
# 設定ファイル（queues.yaml）の読み込みに使用
import yaml


# ============================================================
# キュー設定データクラス
# ============================================================


@dataclass
class QueueConfig:
    """
    キュー設定

    各キューの個別設定を保持します。
    config/queues.yaml の queues セクションに対応。

    Attributes:
        name: キュー名（必須）
              例: "task-queue"
        polling_interval: ポーリング間隔（秒）
                         キューが空の時の基本待機時間
        backoff_max: 最大バックオフ（秒）
                    Exponential Backoff の上限値
        max_retries: 最大リトライ回数
                    この回数を超えると DLQ に移動
        dlq_ttl_days: DLQ メッセージの TTL（日）
                     DLQ 内メッセージの保持期間
    """

    # キュー名（必須パラメータ）
    name: str
    # ポーリング間隔（秒）- キューが空の時の基本待機時間
    polling_interval: float = 1.0
    # 最大バックオフ（秒）- Exponential Backoff の上限
    backoff_max: float = 30.0
    # 最大リトライ回数 - この回数を超えると DLQ 移動
    max_retries: int = 5
    # DLQ 内メッセージの保持期間（日）
    dlq_ttl_days: int = 7


# ============================================================
# Worker 設定データクラス
# ============================================================


@dataclass
class WorkerConfig:
    """
    Worker 設定

    Worker プロセス全体の設定を保持します。
    config/queues.yaml の worker セクションに対応。

    Attributes:
        max_concurrent: 最大同時実行数
                       Worker が同時に処理できるメッセージ数
        queues: キュー設定のリスト
               監視対象のキューとその設定
    """

    # 最大同時実行数（セマフォで制御）
    max_concurrent: int = 5
    # 監視対象キューの設定リスト
    queues: list[QueueConfig] = None

    def __post_init__(self):
        """
        データクラス初期化後の処理

        queues が None の場合に空リストで初期化。
        dataclass のデフォルト値でミュータブルオブジェクト（list）を
        使用できないため、__post_init__ で処理する。
        """
        if self.queues is None:
            self.queues = []


# ============================================================
# アプリケーション設定データクラス
# ============================================================


@dataclass
class Settings:
    """
    アプリケーション設定

    アプリケーション全体の設定を保持します。
    環境変数と YAML ファイルから読み込まれます。

    Attributes:
        env: 実行環境名（local, development, production など）
        storage_connection_string: Azure Storage 接続文字列
                                  Azurite: "UseDevelopmentStorage=true"
                                  Azure: 実際の接続文字列
        worker: Worker 設定
    """

    # 実行環境名
    env: str
    # Azure Storage 接続文字列
    storage_connection_string: str
    # Worker 設定
    worker: WorkerConfig

    @classmethod
    def from_env(cls) -> "Settings":
        """
        環境変数から設定を読み込む

        クラスメソッドとして定義し、Settings インスタンスを生成。
        環境変数が設定されていない場合はデフォルト値を使用。

        環境変数:
            ENV: 実行環境名（デフォルト: "local"）
            STORAGE_CONNECTION_STRING: Storage 接続文字列
                                      （デフォルト: "UseDevelopmentStorage=true"）

        Returns:
            Settings: 設定インスタンス
        """
        # 実行環境名を取得（デフォルト: local）
        env = os.getenv("ENV", "local")
        # Storage 接続文字列を取得
        # "UseDevelopmentStorage=true" は Azurite への接続を示す特殊な値
        storage_connection_string = os.getenv(
            "STORAGE_CONNECTION_STRING",
            "UseDevelopmentStorage=true",
        )

        # YAML ファイルから Worker 設定を読み込み
        worker_config = cls._load_worker_config()

        # Settings インスタンスを生成して返却
        return cls(
            env=env,
            storage_connection_string=storage_connection_string,
            worker=worker_config,
        )

    @classmethod
    def _load_worker_config(cls) -> WorkerConfig:
        """
        Worker 設定を YAML ファイルから読み込む

        config/queues.yaml が存在する場合はその内容を読み込み、
        存在しない場合はデフォルト設定を返す。

        Returns:
            WorkerConfig: Worker 設定

        Note:
            YAML ファイルの構造:
            ```yaml
            worker:
              max_concurrent: 5
            queues:
              - name: task-queue
                polling_interval: 1.0
                ...
            ```
        """
        # 設定ファイルのパス（プロジェクトルートからの相対パス）
        config_path = "config/queues.yaml"

        # ============================================================
        # 設定ファイルが存在しない場合のデフォルト設定
        # ============================================================
        if not os.path.exists(config_path):
            # デフォルト設定を返す
            # task-queue という名前のキューを1つだけ監視
            return WorkerConfig(
                max_concurrent=5,
                queues=[
                    QueueConfig(name="task-queue"),
                ],
            )

        # ============================================================
        # YAML ファイルから設定を読み込み
        # ============================================================
        with open(config_path, "r") as f:
            # yaml.safe_load: 安全な YAML パース（任意コード実行を防止）
            data = yaml.safe_load(f)

        # worker セクションを取得（存在しない場合は空辞書）
        worker_data = data.get("worker", {})
        # queues セクションを取得（存在しない場合は空リスト）
        queues_data = data.get("queues", [])

        # ============================================================
        # キュー設定リストを生成
        # ============================================================
        # 各キュー設定を QueueConfig オブジェクトに変換
        queues = [
            QueueConfig(
                # キュー名（必須、デフォルト: "task-queue"）
                name=q.get("name", "task-queue"),
                # ポーリング間隔（秒）
                polling_interval=q.get("polling_interval", 1.0),
                # 最大バックオフ（秒）
                backoff_max=q.get("backoff_max", 30.0),
                # 最大リトライ回数
                max_retries=q.get("max_retries", 5),
                # DLQ TTL（日）
                dlq_ttl_days=q.get("dlq_ttl_days", 7),
            )
            for q in queues_data
        ]

        # ============================================================
        # WorkerConfig を生成して返却
        # ============================================================
        return WorkerConfig(
            # 最大同時実行数
            max_concurrent=worker_data.get("max_concurrent", 5),
            # キュー設定リスト
            queues=queues,
        )


# ============================================================
# シングルトンインスタンス
# ============================================================

# アプリケーション起動時に設定を読み込み、グローバルに共有
# 他のモジュールから `from libs.shared.config import settings` でアクセス
settings = Settings.from_env()
