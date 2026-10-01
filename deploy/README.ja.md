# 社内ネットワークへのデプロイ：プロキシと社内 CA

[简体中文](README.md) · [English](README.en.md) · [ドキュメント一覧](../docs/README.md)

このディレクトリには独立した Dockerfile、Compose 設定、デプロイスクリプトがあります。HTTP/HTTPS の外向きプロキシと社内 CA が必要な環境にも、個人 PC の直接接続にも対応します。常駐するアプリケーションコンテナは一つで、SQLite と Git キャッシュは既存の永続ボリュームに保存されます。

## 1. Docker のイメージ取得を先に設定する

Docker Engine 20.10 以降（または対応する Docker Desktop）と BuildKit が必要です。リポジトリルートから独立した入口 `bash deploy/deploy.sh` を実行します。ホストでは Docker だけを呼び出し、設定生成、ビルド、マイグレーション、アプリのコマンドはコンテナ内で実行します。ホストの Python / Node や `.agent/run.sh` は不要です。

**新しい `docker compose` と旧 `docker-compose`（最低 1.29.2）に対応します。** 既定では `docker:28-cli` ツールコンテナを使うため、ホスト側の Compose の有無やバージョンはデプロイに影響しません。ツールコンテナの Compose プラグインを優先し、なければ `docker-compose` を使います。旧版では互換設定と正常性のポーリングを自動的に使用し、BuildKit secret、プロキシ値、CA 設定を保持します。常駐コンテナは増えません。

v1 で更新する場合、デプロイスクリプトは DB マイグレーション成功後に web コンテナを置き換え、SQLite と Git キャッシュの名前付きボリュームを保持します。更新には `bash deploy/deploy.sh` を使ってください。旧 `docker-compose up` を直接呼ぶと、新しい Docker Engine では廃止された `ContainerConfig` フィールドが原因で失敗することがあります。

社内ツールイメージを使う場合は `GEV_DOCKER_CLI_IMAGE` を指定できます。イメージには `docker`、`sh`、`awk`、`date` と、上記いずれかの Compose が必要です。例のアドレスを実際のイメージに置き換え、以後のデプロイ・運用でも同じ設定を使ってください。個人 PC では設定不要です。

```bash
GEV_DOCKER_CLI_IMAGE=registry.example.com/tools/docker-cli-compose:1.29.2 bash deploy/deploy.sh
GEV_DOCKER_CLI_IMAGE=registry.example.com/tools/docker-cli-compose:1.29.2 bash deploy/deploy.sh compose --env-file runtime/local.env ps
```

プロキシには二つの設定箇所があります。

| 対象 | 設定場所 | 用途 |
|---|---|---|
| Docker 自体 | Desktop の設定、または Engine の daemon 設定とホストの信頼ストア | Python、Docker CLI、ビルド用ベースイメージの取得 |
| ビルドとアプリケーション | `runtime/*.network.env` と CA ディレクトリ | pip の依存取得、実行中の Git/API 通信 |

**最初に Docker 自体を設定してください。** アプリの設定ファイルでは、まだ取得できていないツール用イメージの問題を解決できません。

Docker Desktop：Settings の Proxies で設定し、社内ルート証明書をホストの信頼ストアに追加して Desktop を再起動します。Desktop は `daemon.json` のプロキシ設定を使いません。[Desktop のプロキシ設定](https://docs.docker.com/desktop/settings-and-maintenance/settings/#proxies)、[Docker の CA 説明](https://docs.docker.com/engine/network/ca-certs/)を参照してください。

Linux Docker Engine：管理者が以下の項目を `/etc/docker/daemon.json` に追加し、システムの CA 信頼を設定して Docker を再起動します。既存の設定は残してください。[Engine のプロキシ設定](https://docs.docker.com/engine/daemon/proxy/)を参照してください。

```json
{
  "proxies": {
    "http-proxy": "http://proxy.corp.example:3128",
    "https-proxy": "http://proxy.corp.example:3128",
    "no-proxy": "localhost,127.0.0.1,.corp.example"
  }
}
```

## 2. 初回の設定準備

```bash
bash deploy/deploy.sh --init
```

このコマンドはファイルの準備だけを行います。ビルド、起動、ユーザー作成は行いません。既存のアプリ設定と秘密鍵は保持します。

| ファイル / ディレクトリ | 内容 |
|---|---|
| `runtime/local.env` | アプリの秘密値、SQLite URL、Compose プロジェクト名、ポート |
| `runtime/local.network.env` | 外向きプロキシ、任意の pip インデックス |
| `runtime/local.deploy.env` | ネットワークファイル、CA ディレクトリ、バインドアドレスなど |
| `runtime/local.deploy/` | 自動生成されるビルド secret と実行時ネットワーク設定。直接編集しません |
| `runtime/ca/` | 社内ルート / 中間 CA。PEM 形式の `.pem` / `.crt` ファイル |

エディターで `runtime/local.network.env` を編集します。

```dotenv
HTTP_PROXY=http://proxy.corp.example:3128
HTTPS_PROXY=http://proxy.corp.example:3128
NO_PROXY=localhost,127.0.0.1,::1,web,gitlab.corp.example,.corp.example
PIP_INDEX_URL=
PIP_EXTRA_INDEX_URL=
```

`HTTPS_PROXY` は HTTPS の接続先に使うプロキシです。通常の HTTP CONNECT プロキシならアドレスは `http://` で始まります。社内指定の値を使い、プロキシ不要の項目は空欄にします。

- ファイルは一行ごとの `KEY=value` です。shell スクリプトではないため、`export`、変数参照、コマンド置換は書きません。スクリプトは大文字と小文字のプロキシ変数を設定し、ローカルのヘルスチェックをプロキシから除外します。
- `NO_PROXY` には直接接続する社内 Git / パッケージサーバーを指定します。プロキシが必要な外部接続先は含めません。
- 個人 PC 上のプロキシには、コンテナから `host.docker.internal:ポート` で接続します。`127.0.0.1` はコンテナ自身です。Linux では `host-gateway` を設定しますが、プロキシ側も Docker ネットワークからの接続を許可する必要があります。
- 認証 URL のユーザー名やパスワードの特殊文字はパーセントエンコードします。資格情報を含むファイルは 0600 のまま保管し、Git、ログ、チケットに載せません。
- pip インデックスはビルド時だけ使い、アプリにはプロキシだけ渡します。社内統一の `PIP_INDEX_URL` を優先し、追加インデックスは必要な場合だけ設定します。

社内 CA を `runtime/ca/company-root.crt` などとして配置します。秘密鍵、PFX/P12、サーバー証明書だけのファイルは置きません。複数の CA に対応し、社内 CA が不要な個人環境では空ディレクトリで構いません。

## 3. デプロイ、更新、個人 PC との互換性

```bash
bash deploy/deploy.sh
bash deploy/deploy.sh --no-build
bash deploy/deploy.sh --pull --wait-timeout 300
bash deploy/deploy.sh --help
```

初回とソース更新は一つ目、既存の社内用イメージの再利用は二つ目、ベースイメージも更新する場合は三つ目を使います。設定確認、ビルド、web 停止、DB マイグレーション、起動、ヘルスチェックを順に実行します。失敗時は非ゼロで終了し、設定とデータを保持します。既定の URL は [http://127.0.0.1:8766](http://127.0.0.1:8766) です。

同じ `COMPOSE_PROJECT_NAME` なら既存の `app-data` と `git-cache` を使い、SQLite をコピーしたり空にしたりしません。方式を切り替える前にバックアップし、同じプロジェクトに二つのデプロイ処理を同時実行しないでください。

個人 PC でも同じ `bash deploy/deploy.sh` を使い、プロキシと CA ディレクトリは空のままで構いません。専用の `-intranet` イメージタグを使います。初回はビルドが必要で、以後 `--no-build` を使えます。この入口は SQLite の単一コンテナ構成に対応します。旧 PostgreSQL 環境は[データベース移行ガイド](../docs/ja/docker-deployment.md)の `--migrate-sqlite` で先に移行し、DB URL だけを変更しないでください。

## 4. 社内本番サービスと独立インスタンス

```bash
bash deploy/deploy.sh --production --init
bash deploy/deploy.sh --production --wait-timeout 300
```

二つのコマンドの間で `runtime/production.network.env` と CA を用意します。本番は独立した `runtime/production.env`、Compose プロジェクト、ボリュームを使い、既定のバックエンドポートは 8767 です。Secure ログイン cookie のため、ブラウザーからは HTTPS リバースプロキシ経由でアクセスします。外向きの `HTTPS_PROXY` はブラウザー用 HTTPS の代わりにはなりません。

既定では `127.0.0.1` のみで待ち受け、同一ホストのリバースプロキシを想定します。別ホストのリバースプロキシから接続する場合はサーバーの社内 IP、または以下の全インターフェースを明示し、ファイアウォールで接続元を限定します。

```bash
bash deploy/deploy.sh --production --bind-address 0.0.0.0 --no-build
```

プロジェクトページは匿名閲覧可能です。公開するアドレスに接続できる利用者はプロジェクトを閲覧できます。本番の管理者ログインには引き続き HTTPS を使います。

独立した検証環境では、設定、ネットワーク、CA、プロジェクト名、ポートを分けられます。

```bash
bash deploy/deploy.sh --env-file runtime/intranet-test.env --project-name gev-intranet-test --port 8772 --network-file runtime/intranet-test.network.env --ca-dir runtime/company-ca --init
bash deploy/deploy.sh --env-file runtime/intranet-test.env
```

カスタムのファイルとディレクトリは `runtime/` 内に置きます。ネットワークパス、CA パス、バインドアドレスは対応する `.deploy.env` に保存され、再指定は不要です。既存 env のポートとプロジェクト名をコマンドで黙って変更することはありません。新規インスタンスは新しい env とプロジェクト名で作成します。本番環境は以後も `--production` を付けます。

## 5. プロキシと CA の適用方法

ビルド時は BuildKit secret としてネットワーク設定と CA をマウントし、pip はシステム CA と社内 CA を合わせた信頼で検証します。プロキシ資格情報と社内 CA は Dockerfile の `ENV` / `ARG` としてイメージに埋め込みません。

実行時は CA ディレクトリを `/etc/gev/ca` に読み取り専用でマウントします。入口プログラムがシステム CA と社内 CA を含む `/tmp/gev-ca-bundle.pem` を作り、Python/HTTPX、pip、Git、HTTPS プロキシ用の証明書検証を設定します。CA の秘密鍵は不要で、TLS 検証は常に有効です。

管理画面の接続 CA パスは通常空欄にし、既定の信頼を使えます。接続専用の CA パスを指定すると、その接続の Git / API の信頼を上書きするため、必要な CA チェーンを含めます。統合された信頼を明示するには `/tmp/gev-ca-bundle.pem` を指定できます。

資格情報はイメージに入りませんが、Docker 管理権限がある利用者は実行中コンテナの環境を参照できます。社内の資格情報管理方針に従い、ホストと `runtime/` を保護します。プロキシや CA を変更したら再度 `bash deploy/deploy.sh` を実行します。実行時設定だけの更新で既存イメージがある場合は `--no-build` が使えます。

## 6. 管理、バックアップ、トラブルシューティング

運用も同じスクリプトの `compose` サブコマンドを使います。専用 Compose ファイルと保存済みのデプロイ設定を自動的に読み込みます。

```bash
bash deploy/deploy.sh compose --env-file runtime/local.env ps
bash deploy/deploy.sh compose --env-file runtime/local.env logs --tail=100 web
bash deploy/deploy.sh compose --env-file runtime/local.env run --rm --no-deps tools python -m app.cli create-user --username your-admin --admin
bash deploy/deploy.sh compose --env-file runtime/local.env run --rm --no-deps -T tools python .agent/sqlite-backup.py backup runtime/backups/intranet-before-update --env-file runtime/local.env
```

管理者パスワードは対話入力します。本番では env パスを `runtime/production.env` に変更します。バックアップには毎回新しいディレクトリを指定します。DB と対応するアプリの秘密値に加え、ネットワーク設定、デプロイ設定、CA も安全に保管してください。復元は[運用ガイド](../docs/ja/docker-deployment.md)を参照し、Compose コマンドの先頭を `bash deploy/deploy.sh compose`、再起動を `bash deploy/deploy.sh --no-build` に置き換えます。`down -v` は実行しないでください。

| 症状 | 確認箇所 |
|---|---|
| ツール用 / ベースイメージ取得の失敗 | Desktop / daemon のプロキシとシステム信頼。アプリの処理前に失敗しています |
| pip のタイムアウト、407、証明書エラー | ネットワーク設定の認証、プロキシ、pip インデックス、ビルド用 CA |
| ページは表示されるが Git 同期に失敗 | アプリログ、プロキシへの接続、`NO_PROXY`、token 権限、接続 CA パス |
| CA 更新後も失敗 | 再デプロイによる証明書バンドル更新と、接続専用 CA による上書きの有無 |
| 本番ログインが繰り返される | ブラウザーの HTTPS、リバースプロキシ、Secure cookie |

デプロイのヘルスチェックはアプリと DB の準備だけを確認します。本番運用前に実際の社内ネットワークで接続を検証し、実リポジトリを取り込んで同期成功まで確認してください。ローカル検証だけでは社内プロキシ認証、CA チェーン、プラットフォーム権限は保証できません。
