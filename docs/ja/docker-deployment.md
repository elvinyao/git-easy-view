# Docker 実行・自動デプロイ・運用ガイド

[ドキュメント一覧](../README.md) · [简体中文](../zh-CN/docker-deployment.md) · [English](../en/docker-deployment.md)

依存取得や Git 接続に HTTP/HTTPS プロキシと社内 CA が必要な場合は、独立した [deploy/ 社内デプロイガイド](../../deploy/README.ja.md)を使用してください。入口は `bash .agent/run.sh deploy-intranet` で、専用 Dockerfile、設定準備、CA 信頼、運用手順を含みます。本ページの `deploy` は個人 PC の直接接続でも引き続き使えます。

## 1. 必要環境と単一コンテナ構成

Docker Desktop または Docker Engine を起動し、リポジトリのルートから実行します。ホストではソースの参照・編集だけを行い、実行には `bash .agent/run.sh ...` を使います。

常駐するのは **web コンテナ 1 個**です。Web、定期実行、バックグラウンドジョブ処理を含みます。SQLite にプロジェクト、ユーザー、ブランチ、ジョブを保存するため PostgreSQL と Redis は不要です。ジョブは順次実行し、その間も既存スナップショットを閲覧できます。子プロセスが終了するとコンテナを再起動し、未完了ジョブを DB から復旧します。

永続ボリュームは SQLite の `app-data:/data/db` と Git の `git-cache:/data/git` の 2 個です。ボリュームはコンテナではありません。デプロイ時の一時ツール・マイグレーションコンテナは終了後に削除します。旧サービスは移行・互換テスト用の `legacy` profile のみです。

SQLite は WAL と 30 秒のロック待機を使用します。単一ホストで十数個のリポジトリを見る用途に適しています。DB をネットワークファイルシステムに置いたり、web のレプリカを増やしたりしないでください。

## 2. shell コマンド 1 つでデプロイ

初回とソース更新後は最初のコマンドを使います。2 番目は既存イメージを再利用し、3 番目は Python のベースイメージも更新します。

```bash
bash .agent/run.sh deploy
bash .agent/run.sh deploy --no-build
bash .agent/run.sh deploy --pull
bash .agent/run.sh deploy --help
```

既定 URL は [http://127.0.0.1:8766](http://127.0.0.1:8766) です。`runtime/local.env`（0600）を生成または保持し、ビルド、書き込みプロセス停止、マイグレーション、web 起動、正常性確認を行います。失敗時は非ゼロ終了で、設定とボリュームを保持します。アカウントとサンプルは自動作成しません。

既存 PostgreSQL 環境からの初回移行：

```bash
bash .agent/run.sh deploy --migrate-sqlite
```

旧書き込みプロセスを停止して各テーブルをコピーし、全フィールド、外部キー、整合性を検証します。成功後に DB URL を変更し、PostgreSQL、Redis、旧 worker を停止します。ユーザー、ID、暗号化 token、セッションキー、Git キャッシュは保持します。旧 env は `runtime/local.env.postgres-backup` に保存し、元の DB ボリュームは削除しません。SQLite が既に存在する場合は上書きを拒否します。本番は `--production` を追加してください。

以後は通常の `deploy` だけです。旧 DB URL だけを SQLite に書き換えると空の DB を参照するため、この移行を使ってください。失敗時はログと保持ファイルを確認し、元のボリュームを削除しないでください。移行後の旧 DB へのロールバックには SQLite 側の新しい変更は含まれません。

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

`.env.example` はフィールド参照です。再デプロイでキーは変更しません。

| フィールド | 既定値 / 用途 |
|---|---|
| `GEV_DATABASE_URL` | `sqlite:////data/db/gev.sqlite3`、永続 DB |
| `GEV_JOB_BACKEND` | `database`、Redis 不要 |
| `GEV_CACHE_ROOT` | `/data/git` |
| `GEV_SESSION_SECRET` | 32 文字以上の安定したランダム値 |
| `GEV_CREDENTIAL_KEY` | DB と一緒に保存する Fernet key |
| `GEV_PRODUCTION`, `GEV_COOKIE_SECURE` | 本番は両方 `true` |
| `GEV_PORT` | local 8766、production 8767、loopback のみ |
| `COMPOSE_PROJECT_NAME` | 別環境は別名でボリュームを分離 |
| `GEV_ENV_FILE` | `--env-file` と一致 |
| `GEV_APP_IMAGE` | 環境のアプリイメージタグ |
| `GEV_JOB_TIMEOUT_SECONDS` | 3600 秒 |
| `GEV_SCHEDULER_INTERVAL_SECONDS` | 15 秒、同期対象検出と中断ジョブ復旧 |
| `GEV_HEARTBEAT_INTERVAL_SECONDS` | 15 秒 |
| `GEV_SESSION_MAX_AGE` | 43200 秒 |

キューは毎秒確認します。リポジトリの同期間隔は既定 300 秒で管理画面から変更します。本番は絶対パスの SQLite を許可しますが、安定したキーと Secure cookie は必須です。PostgreSQL/RQ アダプターは互換用途として残しています。

## 7. 日常操作と診断

ローカル環境では `runtime/local.env` に変更します。

```bash
bash .agent/run.sh compose --env-file runtime/production.env ps
bash .agent/run.sh compose --env-file runtime/production.env logs --tail=100 web
bash .agent/run.sh compose --env-file runtime/production.env exec -T web alembic current
bash .agent/run.sh compose --env-file runtime/production.env stop web
bash .agent/run.sh compose --env-file runtime/production.env up -d --wait --wait-timeout 300 web
```

`ps` の実行中サービスは web 1 個です。`ps -a` に表示される移行前の停止コンテナは実行リソースを消費しません。

Docker に接続できない場合は Docker を起動し、ホスト Python へ切り替えないでください。同期が進まない場合は web ログ、接続の停止状態、token/CA を確認します。DB が busy の場合は追加の手動実行プロセスを確認してください。WAL、キャッシュ、ロックは削除しないでください。マイグレーション失敗時は停止状態で修正・復旧後に再デプロイします。ログインループは HTTPS/cookie/セッションキー、復号失敗は対応する Fernet key を確認します。

## 8. バックアップ、復元、ロールバック

SQLite backup API でオンラインバックアップを行うため、WAL 内のコミット済みデータも含みます。稼働中の DB ファイルを単純コピーしません。毎回新しいディレクトリを指定してください。既存ディレクトリは拒否します。env のキーも保存し、管理された暗号化ストレージへ退避してください。

```bash
bash .agent/run.sh compose --env-file runtime/production.env run --rm --no-deps -T tools python .agent/sqlite-backup.py backup runtime/backups/pre-deploy --env-file runtime/production.env
```

復元は現在の DB を上書きします。対象を確認して web を停止し、tools や開発プロセスも DB に接続しない状態にしてください。env はバックアップと一致する必要があります。異なる場合は対応する設定を確認・復元してください。

```bash
bash .agent/run.sh compose --env-file runtime/production.env stop web
bash .agent/run.sh compose --env-file runtime/production.env run --rm --no-deps -T tools python .agent/sqlite-backup.py restore runtime/backups/pre-deploy --env-file runtime/production.env
bash .agent/run.sh deploy --production --no-build
```

Git キャッシュは再取得できます。復旧時間短縮には停止中のボリュームも別途保存できます。旧コードへ戻す際は schema 互換性を確認し、必要なら対応する DB とキーも復元します。`down -v` はデータを削除するため使用しないでください。

## 9. 受け入れ確認と容量の限界

常駐サービスの正常状態、`/health`、匿名閲覧、管理者の HTTPS ログインを確認します。その後、社内の実 token / CA で接続し、実リポジトリを 1 件インポートして検証してください。

ソース、インストール済みパッケージ、実 PostgreSQL / Redis / RQ、Chromium の確認は[開発ガイド](development.md#7-テストと検証)を参照してください。既定上限はリポジトリあたり 10,000 ブランチ、Git 出力 4 MiB、通常 Git 30 秒、clone / fetch 120 秒、ジョブ最大 5 試行、provider 最大 1000 ページです。ワークスペースは履歴 100 件、ファイル 200 件、ブランチ独自コミット画面は最大 50 件です。

サンプルと規模試験は 3 コミットだけで、本番大規模リポジトリの容量保証ではありません。社内プラットフォーム、ネットワーク、token、CA、長期運転、プロキシは実環境で確認してください。SSO、webhook、SSH Git、無制限の履歴閲覧は未実装です。
