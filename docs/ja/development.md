# 開発ガイド

[ドキュメント一覧](../README.md) · [简体中文](../zh-CN/development.md) · [English](../en/development.md)

## 1. 技術スタックと構成

Python 3.13、FastAPI、SQLAlchemy / Alembic、Jinja2、CSS / JavaScript、同梱 jQuery、PostgreSQL 17、Redis 7.4、RQ を使用します。フロントエンドの Node ビルドは不要です。

| パス | 役割 |
|---|---|
| `app/main.py`, `config.py`, `db.py`, `models.py` | アプリケーション工場、設定、DB、モデル |
| `app/routes/` | ログイン、公開閲覧、管理者操作の HTTP ルート |
| `app/providers/` | GitHub / GitLab API、HTTPS・URL 検証 |
| `app/git_service.py`, `git_workspace.py` | ローカル Git 実行、履歴、ファイル詳細 |
| `app/sync.py`, `jobs.py`, `scheduler.py` | スナップショット公開、DB outbox、RQ、定期実行 |
| `app/relations.py`, `project_overview.py` | 関係の証拠、プロジェクト概要の一括取得 |
| `app/templates/`, `app/static/`, `app/locales/` | 画面、静的リソース、3 言語メッセージ |
| `alembic/versions/` | DB マイグレーション |
| `tests/` | 単体、統合、規模、Chromium テスト |
| `.agent/` | Docker runner、デプロイ、サンプル、パッケージ検証、テストイメージ |
| `runtime/` | Git 管理外の env、CA、アクセス情報、画像、検証結果 |

変更前にルートの [AGENTS.md](../../AGENTS.md) を読んでください。`docs/superpowers/` は過去の設計であり、権限の一部は現在の匿名閲覧ルールに置き換わっています。

## 2. 実行ルールと準備

ホストではソース閲覧、検索、編集、読み取り専用 Git 操作だけを行います。Python、pip、pytest、マイグレーション、サーバー、整形、Git 書き込みは `bash .agent/run.sh ...` を経由します。ホストでプロジェクトコードや Docker Compose を直接実行しません。

依存関係とサービスを最短で用意するには：

```bash
bash .agent/run.sh deploy
```

`.venv` は不要です。独立した開発環境は初回に別の設定、プロジェクト名、ポートを指定します。

```bash
bash .agent/run.sh deploy --env-file runtime/development.env --project-name gev-development --port 8770
```

以後の Compose コマンドは `--env-file runtime/development.env` に変更します。本番設定で開発ツールやサンプルを実行しないでください。

ソーステスト用のコンテナ専用仮想環境も作成できます。

```bash
bash .agent/run.sh python -m venv .venv
bash .agent/run.sh .venv/bin/pip install -r requirements.lock
bash .agent/run.sh .venv/bin/pip install -e . --no-deps
```

マウント先の `.venv` は Linux コンテナのプログラムを含みます。macOS ホストから直接起動しません。依存関係は `requirements.lock` に合わせ、Python を任意に更新しないでください。

## 3. 編集とホットリロード

通常の `web` はインストール済みイメージで実行します。ソースの変更を反映するには再度 `deploy` を実行します。画面調整にはソースをマウントする `tools` を別ポートで起動できます。

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps -p 127.0.0.1:8768:8000 tools uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

[http://127.0.0.1:8768](http://127.0.0.1:8768) を開き、終了は Ctrl+C です。local の DB とキャッシュを共用するので、必要なら development 設定に変更してください。Python ファイルの変更は `--reload`、テンプレート・静的ファイルはブラウザー更新で反映します。worker はビルド済みコードを使うため、同期処理の変更時は開発環境を再デプロイします。

シェルもコンテナ内で起動します。

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools bash
```

## 4. アーキテクチャと維持する挙動

dispatcher が同期対象を検出 → DB にジョブ / outbox を記録 → Redis キューへ投入 → RQ worker がリポジトリロックを取得 → Git / API 取得 → 固定 OID で解析 → スナップショット公開、という流れです。ジョブの事実は DB に保存します。キューへの投入漏れ、プロセス終了、Redis の再起動は outbox から復旧します。

Git と API の更新日時は独立です。比較キャッシュは不変 OID とルールのバージョンで識別し、動く ref を解析中に混在させません。派生元候補、手動宣言、予定ターゲット、現在の到達可能性、過去 PR/MR を別々に表示します。

公開 GET は `optional_user` を使用します。プロジェクトは公開、非管理者のリポジトリアクセスは現在のプロジェクト関連付けを検証します。管理設定と新しい同期／比較要求は `require_admin`、POST は CSRF 検証を維持します。HTML フラグメント、JSON、コミット URL にも同じ確認が必要です。

公開 GET で DB とローカル Git を読めますが、リモート取得やプラットフォームへの書き込みは開始しません。管理者の接続確認・リポジトリ検出は既存フローでプラットフォームへアクセスします。TLS、HTTPS URL 検証、出力・時間制限、キャッシュロック、ログの資格情報保護を維持してください。

フロントエンドは古いリクエストとキャンセル処理を扱い、遅い応答で新しい絞り込み結果を上書きさせません。ブランチ、コミット、絞り込み、ページ、表示密度を URL から復元します。

## 5. コードスタイル、i18n、依存関係

Python は 4 スペース、関数・モジュールは `snake_case`、クラスは `PascalCase`、サービスインターフェースには型注釈を使います。HTML / JavaScript は既存の 2 スペースに合わせ、provider 処理は provider 層に置きます。統一 formatter / linter は設定されていません。

`app/locales/messages.json` は中国語のメッセージを key とし、`ja` と `en` を定義します。動的値は `{name}` などの名前付きプレースホルダーを使います。テンプレートは `t(...)`、サービスエラーは `Message` を利用できます。HTML エスケープを維持し、リポジトリ名やコミットなどのユーザーデータは翻訳しません。文言変更後は i18n テストと 3 言語のレイアウトを確認します。

依存関係の変更は `pyproject.toml` と `requirements.lock` を更新し、Python 3.13 Docker で検証します。`Dockerfile` はロック済み依存関係と wheel をインストールします。テンプレート、静的リソース、メッセージを追加したらパッケージ収録設定も確認してください。

## 6. DB マイグレーション

永続化モデルの変更には Alembic migration を追加します。稼働済みの local 開発 DB で差分を生成・確認する例：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools alembic revision --autogenerate -m "describe schema change"
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps migrate
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools alembic check
```

自動生成結果、データ変換、制約、downgrade をレビューします。`init-db` は空の非本番開発 DB 用で、本番マイグレーションの代用にはなりません。ユーザーデータがある DB で downgrade を試さず、使い捨てのテスト DB を使用してください。

## 7. テストと検証

pytest のファイル／関数は `test_*.py` / `test_*`、共通 fixture は `tests/conftest.py` です。数値によるカバレッジ基準はありません。権限、スナップショット整合性、障害復旧、操作の変更には意味のある回帰テストを追加します。

デプロイ済みイメージの依存関係とマウントしたソースで実行できます。

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python .agent/test-unit.py -q
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python .agent/test-unit.py tests/test_deploy.py tests/test_i18n.py -q
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python -I .agent/check-installed.py
bash .agent/run.sh python .agent/check-docs.py
```

`test-unit.py` は引き継いだ `GEV_*` デプロイ設定を除外し、独立した単体テストへの影響を防ぎます。`.venv` があれば `bash .agent/run.sh .venv/bin/pytest -q` も利用できます。`-I` はインストール済み wheel のリソースを確認します。ソーステストだけでは代替できません。文書検証はローカルリンク、見出し、3 言語の shell コマンド一致、shell 構文を確認します。

任意の追加テストは **local** 設定で、既定アプリケーションイメージ（`deploy`）とテストイメージを用意して実行します。

```bash
bash .agent/run.sh compose --env-file runtime/local.env build integration browser
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_INTEGRATION=1 integration python -m pytest tests/test_integration.py -q -s
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_SCALE=1 integration python -m pytest tests/test_scale.py -q -s
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_E2E=1 browser python -m pytest tests/test_e2e.py -q
```

統合テストは UUID 名の一時 PostgreSQL DB を作成・削除するため、データベース作成権限が必要です。Redis は独立プロセス、ポート、ディレクトリを使用します。規模テストは 100 の小さな bare リポジトリ、各 55 refs・3 コミットであり、大規模本番履歴の性能は保証しません。

完全 E2E の `test_compose_public_demo_screenshots` は、明示的に初期化した local サンプル（3 リポジトリ × 55 ブランチ）と一致する `runtime/demo-access.json` の管理者情報が必要です。空の環境で隔離 fixture だけを検証する場合は `-k 'not compose_public_demo_screenshots'` を追加します。既存 DB を seed する必要はありません。画像は `runtime/screenshots/`、規模結果は `runtime/verification/` に保存します。

## 8. コミットと PR

履歴には命令形の要約を使用し、`feat:`、`fix:`、`docs:` がよく使われます。接頭辞のないコミットもあります。1 コミットの目的を絞ってください。

PR に問題、挙動変更、関連 issue、検証コマンドと結果を記載します。UI 変更には画像、設定・schema 変更にはマイグレーションと運用影響を添えます。機能やコマンド変更は中国語、日本語、英語の文書にも反映してください。`runtime/`、`.env`、DB、キャッシュ、資格情報、秘密を含む画像はコミットしません。

Git 書き込みも Docker 経由です。

```bash
bash .agent/run.sh git -c safe.directory=/workspace switch -c codex/describe-change
bash .agent/run.sh git -c safe.directory=/workspace add app tests docs
bash .agent/run.sh git -c safe.directory=/workspace commit -m "docs: describe deployment workflow"
```

読み取り専用の `git status`、`git diff`、`git log` はホストで実行できます。差分に秘密がないことを確認し、変更に必要な検証を実行してください。
