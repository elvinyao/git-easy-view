# Docker 実行・自動デプロイ・運用ガイド

[ドキュメント一覧](../README.md) · [简体中文](../zh-CN/docker-deployment.md) · [English](../en/docker-deployment.md)

## 1. 前提条件とサービス構成

Docker を使える単一 Linux サーバー、または Docker Desktop の開発機向けです。`.agent/` を含むソースを配置し、リポジトリ直下で実行してください。ホストの Python、Node.js、pip、Compose プラグインは不要です。

入口は `bash .agent/run.sh ...` です。通常コマンドは `python:3.13-bookworm`、Compose とデプロイ制御は `docker:28-cli` 内で実行します。Docker daemon を起動し、runner に `/var/run/docker.sock` と作業ディレクトリへのアクセス権を与えます。この socket はホストの管理権限を持つため、信頼できる運用担当者と CI だけで使用してください。

| サービス | 役割 | 永続化 |
|---|---|---|
| `postgres` | PostgreSQL 17、設定・スナップショット・ユーザー・監査・ジョブ / outbox | `postgres-data` |
| `redis` | Redis 7.4、RQ キューと再試行スケジュール | `redis-data`、AOF 有効 |
| `migrate` | 一度だけ `alembic upgrade head` を実行 | 共通 DB |
| `web` | FastAPI / Uvicorn、コンテナ内ポート 8000 | 共通 Git cache |
| `worker` | 2 つの RQ worker、Git / API 同期と比較 | `git-cache` |
| `dispatcher` | 定期同期対象の確認、outbox 再投入 | DB と Redis |
| `tools` | ソースをマウントする単発管理 CLI | 常駐しない |
| `integration`, `browser` | 任意の検証イメージ | 通常は起動しない |

ホストへ公開するのは Web の **127.0.0.1** ポートだけです。DB と Redis はホストへ公開しません。`/health` は DB を確認し、Redis 障害中もキャッシュ閲覧は可能です。worker / dispatcher にも個別のヘルスチェックがありますが、正常表示は全リポジトリの同期完了を意味しません。

## 2. shell 1 コマンドでデプロイ

ローカルのデモ／開発環境：

```bash
bash .agent/run.sh deploy
```

初回は `runtime/local.env` を作成し、DB パスワード、session secret、Fernet key をランダム生成します。権限は 0600 です。`.venv` は不要で、アカウントやサンプルデータは自動作成しません。成功後は [http://127.0.0.1:8766](http://127.0.0.1:8766) を開きます。

再実行は env の内容、キー、アカウント、データボリュームを保持します。初回のダウンロード・依存関係インストールにはネットワークと時間が必要です。以後は Docker のビルドキャッシュを利用します。固定の所要秒数は保証しません。

```bash
# ソース変更後：現在の checkout をビルドしてデプロイ
bash .agent/run.sh deploy

# ソース・依存関係が同じで正しいイメージがある場合：ビルド省略
bash .agent/run.sh deploy --no-build

# PostgreSQL / Redis と、ビルド時の Python ベースを明示的に更新
bash .agent/run.sh deploy --pull

bash .agent/run.sh deploy --help
```

`--no-build` では新しいソースは古いイメージへ入りません。未ビルドの変更を本番へ反映する用途には使わないでください。

| オプション | 用途 |
|---|---|
| `--production` | 本番モード。既定は `runtime/production.env`、8767、Secure cookie |
| `--env-file runtime/name.env` | runtime 内の相対 env パス。新しい独自ファイルにはプロジェクト名も必要 |
| `--project-name gev-staging` | 新規設定の Compose 名。ネットワーク、コンテナ、ボリューム、生成イメージタグを分離 |
| `--port 8770` | 新規設定の loopback ポート |
| `--no-build` | 既存アプリケーションイメージを使用 |
| `--pull` | ベース／インフラのイメージを取得。Python の依存バージョンは自動更新しない |
| `--wait-timeout 300` | 各ヘルス待機段階の秒数上限。既定 180。ビルド全体の制限ではない |

既存 env のポートやプロジェクト名はオプションで上書きしません。明示的に env を編集してください。通常の `KEY=value`、行全体のコメント、単純な引用符に対応し、変数展開は使えません。高速スクリプトは Compose 内の `gev` PostgreSQL DB と `/data/git` を使用します。外部 DB は Compose と運用手順を別途調整してください。

手順は設定検証 → 任意の pull → ビルド → アプリ設定検証 → PostgreSQL / Redis 待機 → Web / worker / dispatcher 停止 → 新イメージでマイグレーション → 起動・全アプリサービスのヘルス待機 → 状態・ポート表示です。毎回マイグレーションを明示実行し、過去に成功した migrate コンテナには依存しません。

短い停止時間のある単一ホストのデプロイです。Git 取得、自動バックアップ、自動ロールバック、ボリューム削除、既存 env の上書きは行いません。ビルド失敗ならアプリ停止前、マイグレーション失敗ならアプリを停止したまま非ゼロ終了します。修正または検証済みバックアップの復元後に再実行してください。同一環境の CI デプロイは直列化します。

## 3. 管理者と任意のサンプルデータ

空の環境には正式な管理者を一度だけ作成します。パスワードは対話入力で、表示されません。

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python -m app.cli create-user --username your-admin --admin
```

長いランダムパスワードを使ってください。Web のアカウント管理では 12–1024 文字を要求します。本番は env パスを `runtime/production.env` に変更します。同名ユーザーがあればエラーとなり、パスワードを変更しません。再デプロイでユーザーを作り直す必要はありません。

無人で初回作成する場合は、secret 管理システムで `runtime/admin-bootstrap.env`（0600、`GEV_ADMIN_PASSWORD=...`）を配置し、単発コンテナへ渡します。

```bash
bash .agent/run.sh compose --env-file runtime/production.env run --rm --no-deps -T --env-from-file /workspace/runtime/admin-bootstrap.env tools python -m app.cli create-user --username your-admin --admin --password-env GEV_ADMIN_PASSWORD
```

パスワードは引数や常駐 Web 環境には入りませんが、コンテナ管理者は単発コンテナの環境を読めます。完了後は secret 管理手順に従ってファイルを除去し、内容をログに出さないでください。[Compose run の公式オプション](https://docs.docker.com/reference/cli/docker/compose/run/)。

UI を試す場合、**空の非本番 local DB のみ**で実行します。

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python .agent/seed-local.py
```

アクセス情報は `runtime/demo-access.json`（0600）に保存します。3 リポジトリ、各 55 ブランチ、3 つの実際の空ツリーコミット、架空と明示した PR を作成します。サンプル接続は停止状態です。架空 URL の接続確認はしないでください。既存ユーザー／データがある DB は上書きしません。中断後は別の空環境を使用し、利用中の DB を reset しないでください。

## 4. 本番、HTTPS、リモート自動化

```bash
bash .agent/run.sh deploy --production --wait-timeout 300
```

初回に独立したランダム値で `runtime/production.env` を生成します。プロジェクト名は `git-easy-view-production`、イメージは `git-easy-view:git-easy-view-production`、ポートは 8767、`GEV_PRODUCTION=true`、`GEV_COOKIE_SECURE=true` です。local ファイルは保持します。別のプロジェクト名で永続ボリュームを分離します。**env を変えるだけでは分離できません。** [Compose プロジェクト名の説明](https://docs.docker.com/compose/how-tos/project-name/)。

スクリプトはバックエンドをデプロイします。証明書の発行やインターネットへの公開は行いません。本番のブラウザー利用には HTTPS リバースプロキシが必要です。HTTP では Secure ログイン cookie が正常に使えません。以下は **同じホスト上** の Nginx の例です。ドメインと証明書パスを準備済みの値へ変更してください。

```nginx
server {
    listen 443 ssl;
    server_name git-view.example.com;
    ssl_certificate /etc/nginx/tls/fullchain.pem;
    ssl_certificate_key /etc/nginx/tls/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8767;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

プロキシが別コンテナの場合、`127.0.0.1` はそのコンテナです。上記 upstream をそのまま使わず、同じ管理対象 Docker ネットワークで `web:8000` を使用してください。匿名閲覧は全関連リポジトリを含みます。社内公開の範囲はネットワーク／プロキシでも制御できます。

独立した staging の初回例：

```bash
bash .agent/run.sh deploy --production --env-file runtime/staging.env --project-name gev-staging --port 8771 --wait-timeout 300
```

サーバーに Docker、ソース、永続 env、HTTPS プロキシがある場合、現在の checkout をリモート公開できます。

```bash
bash .agent/run.sh ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -i runtime/deploy-key -o UserKnownHostsFile=runtime/deploy-known-hosts deploy@server.example 'cd /srv/git-easy-view && bash .agent/run.sh deploy --production --wait-timeout 300'
```

ソースは信頼できる CI のリリースパッケージで配置するか、**クリーンな専用 deployment checkout** で更新します。`origin/main` は例なので承認済み ref に変更してください。Git 書き込みも Docker 内です。

```bash
bash .agent/run.sh git -c safe.directory=/workspace fetch origin
bash .agent/run.sh git -c safe.directory=/workspace merge --ff-only origin/main
bash .agent/run.sh deploy --production --wait-timeout 300
```

SSH クライアントも runner コンテナで実行します。制御側の secret 管理手順で `runtime/deploy-key`（0600）と検証済みの `runtime/deploy-known-hosts` を用意してください。ホストの `.ssh` や SSH agent は自動マウントしません。アカウント、ホスト、パスを変更すれば CI で利用できます。

CI/CD の deploy 段階で同じコマンドを使用し、非ゼロ終了を失敗として扱います。SSH ホスト検証を無効にしないでください。Docker 権限のある信頼できるアカウントを使います。永続設定は初期作成後もサーバーに保管し、リリースパッケージや CI ログへ入れません。信頼済みブランチだけに実行を許可し、同じ環境は 1 パイプラインだけでデプロイします。無停止、実 token、プロキシ証明書の検証はスクリプトだけでは保証しません。

## 5. プラットフォーム token と社内 CA

管理者がプロジェクトと接続を作成し、接続確認後にリポジトリをインポートします。アプリはリモート読み取りだけですが、token 自体の権限も制限してください。

- GitHub fine-grained PAT：対象リポジトリを限定し、Metadata read、Contents read、Pull requests read を付与します。検出は `/user/repos` を使い、installation token は非対応です。classic `repo` scope は広い権限です。[リポジトリ一覧](https://docs.github.com/en/rest/repos/repos#list-repositories-for-the-authenticated-user)、[PR API](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests)、[PAT 管理](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)。
- GitLab：`read_api` と `read_repository`。token 種別と公開範囲は実際のインスタンスの方針に従います。[公式 scopes](https://docs.gitlab.com/security/tokens/access_token_scopes/)。
- GitHub Enterprise / 自前 GitLab：HTTPS インスタンスのルートを指定します。`/api/v3` / `/api/v4` を認識します。SSH Git は使わず、TLS 検証を無効にしません。

社内 CA は `runtime/ca/company-ca.pem`、接続のコンテナ内パスは `/etc/gev/ca/company-ca.pem` です。Compose は読み取り専用でマウントします。証明書更新後は関連サービスを再起動し、接続を確認します。

token は Fernet で暗号化して DB に保存します。`GEV_CREDENTIAL_KEY` を保管・バックアップしてください。キー更新には既存暗号文の移行が必要で、紛失すると以前の token を復号できません。token / CA の変更後は接続確認が成功するまで停止します。

## 6. 主要な設定

`.env.example` はフィールドとプレースホルダーの参照用です。そのまま実行用には使えません。初回は通常スクリプトで生成し、以後は明示的に編集します。各プロセスで同じ値を使ってください。

| フィールド | 意味 / 既定値 |
|---|---|
| `COMPOSE_PROJECT_NAME` | 環境分離名。変更すると別のボリュームを参照 |
| `GEV_ENV_FILE` | サービスが読む env。`--env-file` と一致させる |
| `GEV_APP_IMAGE` | アプリイメージタグ。古い local 設定で省略時は `git-easy-view:local` |
| `POSTGRES_PASSWORD`, `GEV_DATABASE_URL` | 一致が必要。既存 DB のパスワードはファイル変更だけでは変わらない |
| `GEV_SESSION_SECRET` | 32 文字以上の安定したランダム値。更新で既存セッション失効 |
| `GEV_CREDENTIAL_KEY` | ランダム 32 バイトを URL-safe Base64 化した Fernet key |
| `GEV_REDIS_URL` | `redis://redis:6379/0` |
| `GEV_CACHE_ROOT` | `/data/git`、Web / worker 共用 |
| `GEV_PRODUCTION`, `GEV_COOKIE_SECURE` | 本番はともに `true` |
| `GEV_PORT` | local 8766、production 8767、loopback のみ |
| `GEV_QUEUE_NAME` | RQ キュー名、`git-sync` |
| `GEV_JOB_TIMEOUT_SECONDS` | ジョブ時間上限、3600 秒 |
| `GEV_SCHEDULER_INTERVAL_SECONDS` | dispatcher 周期、15 秒 |
| `GEV_HEARTBEAT_INTERVAL_SECONDS` | ジョブ heartbeat、15 秒 |
| `GEV_SESSION_MAX_AGE` | セッション有効期間、43200 秒 |

リポジトリの同期間隔は既定 300 秒で、管理画面から変更します。本番設定は SQLite、キー不足、Secure cookie の明示的無効化を拒否します。既存 PostgreSQL のパスワード変更では、DB 内の変更と env・サービス再起動を調整してください。

## 7. 日常操作と障害確認

以下は production の例です。local は env ファイルを変更します。

```bash
bash .agent/run.sh compose --env-file runtime/production.env ps -a
bash .agent/run.sh compose --env-file runtime/production.env logs --tail=100 web worker dispatcher
bash .agent/run.sh compose --env-file runtime/production.env exec -T web python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health").read().decode())'
bash .agent/run.sh compose --env-file runtime/production.env exec -T web alembic current

# コンテナ・ボリュームを保持して停止
bash .agent/run.sh compose --env-file runtime/production.env stop

# 設定・イメージに変更がない場合の復帰。更新は deploy を使用
bash .agent/run.sh compose --env-file runtime/production.env up -d --wait --wait-timeout 300
```

`--wait` は running / healthy を待ち、失敗を通知します。[Compose up の公式オプション](https://docs.docker.com/reference/cli/docker/compose/up/)。ログにはリポジトリ、パス、ユーザー情報があり得ます。秘密を共有しないでください。全設定を表示する `compose config` ではなく `config --quiet` を使用します。

| 症状 | 確認 |
|---|---|
| Docker socket に接続できない | Docker 起動とアカウント権限。ホスト Python へ切り替えない |
| ポート使用中 | `GEV_PORT` を明示編集し、プロキシ upstream も変更 |
| PostgreSQL 認証失敗 | 既存ボリュームと env のパスワードを照合。env 再生成で直さない |
| migrate 失敗 | 移行出力と DB を確認。アプリを停止したまま修正／復元後に deploy |
| ヘルス待機タイムアウト | `ps -a` とログ。原因を除外してから待機時間を延長 |
| 閲覧できるが同期されない | worker、dispatcher、Redis、接続停止、token / CA、同期状態 |
| ログインが繰り返される | HTTPS、cookie、プロキシ、共通 session secret |
| token 復号失敗 | DB に対応する Fernet key を復元。古いキーを上書きしない |

worker 強制終了後に Git 子プロセスとロックが残る場合があります。対象プロセス終了または worker コンテナ再起動で活動がないことを確認してください。実行中の Git キャッシュやロックを削除しません。

## 8. バックアップ、復元、更新の取り消し

公開前に PostgreSQL と対応 env（特に Fernet key）をバックアップし、CA も管理して保存します。Git cache は再取得でき、保存すると復旧を短縮できます。Redis は配送層で、ジョブの事実は DB outbox にあります。

例は `runtime/backups/pre-deploy/` です。毎回別のバックアップ先に変え、唯一のコピーを上書きしないでください。書き込み・リダイレクトはコンテナ内です。

```bash
bash .agent/run.sh python -c 'import os,shutil; from pathlib import Path; os.umask(0o077); p=Path("runtime/backups/pre-deploy"); p.mkdir(parents=True,exist_ok=True); shutil.copyfile("runtime/production.env",p/"deployment.env"); os.chmod(p/"deployment.env",0o600)'
bash .agent/run.sh compose --env-file runtime/production.env exec -T postgres sh -c 'umask 077; pg_dump -U gev -d gev -Fc -f /tmp/gev-backup.dump'
bash .agent/run.sh compose --env-file runtime/production.env cp postgres:/tmp/gev-backup.dump /workspace/runtime/backups/pre-deploy/gev.dump
bash .agent/run.sh python -c 'import os; os.chmod("runtime/backups/pre-deploy/gev.dump",0o600)'
```

社内の暗号化バックアップシステムへ移し、隔離環境で定期的に復元を試します。復元は対象 DB を上書きします。環境、バックアップのバージョン、対応キーを確認後、すべてのアプリ書き込みを停止してください。

```bash
bash .agent/run.sh compose --env-file runtime/production.env stop web worker dispatcher
bash .agent/run.sh compose --env-file runtime/production.env cp /workspace/runtime/backups/pre-deploy/gev.dump postgres:/tmp/gev-restore.dump
bash .agent/run.sh compose --env-file runtime/production.env exec -T postgres pg_restore -U gev -d gev --clean --if-exists --exit-on-error /tmp/gev-restore.dump
bash .agent/run.sh deploy --production --wait-timeout 300
```

最後のコマンドは現在の checkout までアップグレードします。バックアップ時のバージョンへ戻すなら、そのソースを管理されたリリースから先に復元してください。旧コードと新 schema の互換性は保証されず、旧イメージへの切り替えだけでは安全に戻せません。対応キーを復元してから起動します。

通常の停止で `down -v` を使わないでください。永続ボリュームを削除します。`runtime/production.env` の削除も再インストール手順にはしません。既存データ復旧に必要なキーが保存されています。

## 9. 受け入れ確認と容量の限界

常駐サービスの正常状態、`/health`、匿名閲覧、管理者の HTTPS ログインを確認します。その後、社内の実 token / CA で接続し、実リポジトリを 1 件インポートして検証してください。

ソース、インストール済みパッケージ、実 PostgreSQL / Redis / RQ、Chromium の確認は[開発ガイド](development.md#7-テストと検証)を参照してください。既定上限はリポジトリあたり 10,000 ブランチ、Git 出力 4 MiB、通常 Git 30 秒、clone / fetch 120 秒、ジョブ最大 5 試行、provider 最大 1000 ページです。ワークスペースは履歴 100 件、ファイル 200 件、ブランチ独自コミット画面は最大 50 件です。

サンプルと規模試験は 3 コミットだけで、本番大規模リポジトリの容量保証ではありません。社内プラットフォーム、ネットワーク、token、CA、長期運転、プロキシは実環境で確認してください。SSO、webhook、SSH Git、無制限の履歴閲覧は未実装です。
