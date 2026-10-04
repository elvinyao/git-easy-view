# Docker, Automated Deployment, and Operations

[Documentation index](../README.md) · [简体中文](../zh-CN/docker-deployment.md) · [日本語](../ja/docker-deployment.md)

For standalone deployment, use the [deploy/ deployment guide](../../deploy/README.en.md). Run `bash deploy/deploy.sh`, add `--init` for configuration, and use `bash deploy/deploy.sh compose ...` for operations. It supports HTTP/HTTPS proxies, company CAs, and direct access on personal computers without `.agent/run.sh`. This page documents the development runner's deployment, database migration, and operations workflows.

## Release management upgrade

Use the existing deployment commands; deployment applies migration `0003` automatically. New tables store tags, release plans and production history without new containers or environment variables. Back up SQLite using the backup section before upgrading; do not downgrade the schema after recording releases. Tags appear after the next repository synchronization, which administrators can also trigger manually. Local MR bindings use existing platform connections and container proxy/CA settings. Code directories remain mounted read-only.

## 1. Prerequisites and single-container layout

Start Docker Engine 20.10+ (or a corresponding Docker Desktop release), enable BuildKit, and run commands from the repository root. Inspect or edit source on the host; execute project commands through `bash .agent/run.sh ...`.

Deployment supports `docker compose` and legacy `docker-compose` (minimum 1.29.2). By default, Compose runs inside the `docker:28-cli` tool container; no host Compose installation or upgrade is needed. The script prefers the plugin, falling back to `docker-compose`, and automatically uses compatible configuration and health polling for older versions. To use a company tool image, set `GEV_DOCKER_CLI_IMAGE` to an image containing `docker`, `sh`, `awk`, `date`, and either Compose implementation above. Keep the same setting for subsequent commands:

```bash
GEV_DOCKER_CLI_IMAGE=registry.example.com/tools/docker-cli-compose:1.29.2 bash .agent/run.sh deploy
```

The standalone entry point `bash deploy/deploy.sh` accepts the same setting; see the [intranet deployment guide](../../deploy/README.en.md). Personal computers can keep the default image.

Use `bash .agent/run.sh deploy` for v1 updates. After a successful database migration, the script replaces the web container and retains the named SQLite and Git cache volumes. Calling legacy `docker-compose up` directly on newer Docker Engines can fail because of the obsolete `ContainerConfig` field.

Only **one web container** stays running. It contains HTTP, scheduling, and one background job processor. SQLite stores projects, users, branches, and jobs; PostgreSQL and Redis are unnecessary. Jobs execute sequentially while cached pages remain readable. If a child process exits, the container restarts and unfinished jobs recover from the database.

Two persistent volumes store SQLite (`app-data:/data/db`) and Git (`git-cache:/data/git`). Volumes are not containers. Temporary deployment tools and migration containers are removed after use. Old services remain under the `legacy` profile for migration and compatibility tests only.

SQLite uses WAL and a 30-second lock wait. This layout suits a single host monitoring a dozen repositories. Keep the database on local storage and do not scale web replicas.

## 2. Deploy with one shell command

Use the first command for initial deployment and source updates. The second reuses the existing image; the third also refreshes the Python base image.

```bash
bash .agent/run.sh deploy
bash .agent/run.sh deploy --no-build
bash .agent/run.sh deploy --pull
bash .agent/run.sh deploy --help
```

Open [http://127.0.0.1:8766](http://127.0.0.1:8766). The helper creates or preserves `runtime/local.env` (0600), builds, stops writers, migrates, starts web, and waits for health. Failures return nonzero and retain configuration and volumes. No accounts or demo data are created automatically.

For the first upgrade from an existing PostgreSQL deployment:

```bash
bash .agent/run.sh deploy --migrate-sqlite
```

The script stops old writers, copies every table, compares all fields, and checks foreign keys and integrity. Only then does it change the database URL and stop PostgreSQL, Redis, and old workers. Users, IDs, encrypted tokens, session keys, and Git caches are preserved. The original env is saved as `runtime/local.env.postgres-backup`; the old database volume is retained. An existing SQLite destination is never overwritten. Add `--production` for production upgrades.

Use ordinary `deploy` afterwards. Do not simply change the old database URL to SQLite: that selects an empty database. On interrupted transfer, inspect logs and retained files without deleting the old volume. Rolling back to PostgreSQL after migration does not include subsequent SQLite changes.

## 3. Create an administrator or an optional demo

Create a real administrator once on an empty installation. The password prompt does not echo:

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python -m app.cli create-user --username your-admin --admin
```

Use a long random password. Web account forms require 12–1024 characters. Change the env path to `runtime/production.env` for production. An existing username causes an error, without resetting its password. Redeployment does not require recreating accounts.

For unattended first-time creation, have a secret manager place `runtime/admin-bootstrap.env` with mode 0600 and `GEV_ADMIN_PASSWORD=...`, then pass it to a one-off container. The following `--env-from-file` example uses the default modern CLI image; with a custom legacy Compose image, use the interactive prompt above:

```bash
bash .agent/run.sh compose --env-file runtime/production.env run --rm --no-deps -T --env-from-file /workspace/runtime/admin-bootstrap.env tools python -m app.cli create-user --username your-admin --admin --password-env GEV_ADMIN_PASSWORD
```

The password is absent from command arguments and persistent Web configuration, although container administrators can read the temporary container's environment. Remove the file using your secret-management procedure afterward; never print it in logs. [Official Compose run options](https://docs.docker.com/reference/cli/docker/compose/run/).

To try the UI, seed **only an empty, non-production local database**:

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python .agent/seed-local.py
```

Access information is stored in `runtime/demo-access.json` with mode 0600. The demo creates three repositories, 55 branches each, three actual empty-tree commits, and clearly fictional PRs. Connections remain paused; do not check their fictional URLs. Existing accounts or data prevent seeding. After interrupted seeding, use a separate empty instance instead of resetting an active database.

## 4. Production, HTTPS, and remote automation

```bash
bash .agent/run.sh deploy --production --wait-timeout 300
```

The first run creates independent secrets in `runtime/production.env`, project `git-easy-view-production`, image `git-easy-view:git-easy-view-production`, port 8767, `GEV_PRODUCTION=true`, and `GEV_COOKIE_SECURE=true`. Existing local configuration stays intact. A different project name separates persistent volumes; **changing only the env file does not**. [Compose project names](https://docs.docker.com/compose/how-tos/project-name/).

The script deploys the backend. It does not issue certificates or expose the service publicly. Production browser access requires an HTTPS reverse proxy; HTTP cannot use the Secure login cookie normally. This Nginx example assumes the proxy runs **on the same host**. Replace the domain and certificate paths with prepared values:

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

In a separate proxy container, `127.0.0.1` refers to that container. Instead, join the controlled application network and use `web:8000`. Anonymous project reading includes every linked repository; company network / proxy rules can restrict the audience.

Example of a separate staging instance:

```bash
bash .agent/run.sh deploy --production --env-file runtime/staging.env --project-name gev-staging --port 8771 --wait-timeout 300
```

Once a remote server has Docker, source, persistent env, and an HTTPS proxy, deploy its current checkout with:

```bash
bash .agent/run.sh ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -i runtime/deploy-key -o UserKnownHostsFile=runtime/deploy-known-hosts deploy@server.example 'cd /srv/git-easy-view && bash .agent/run.sh deploy --production --wait-timeout 300'
```

Deliver source using a trusted CI release archive or update a **clean, dedicated deployment checkout** before deploying. `origin/main` is an example; substitute the approved release ref. Git writes still run inside Docker:

```bash
bash .agent/run.sh git -c safe.directory=/workspace fetch origin
bash .agent/run.sh git -c safe.directory=/workspace merge --ff-only origin/main
bash .agent/run.sh deploy --production --wait-timeout 300
```

The SSH client also runs inside the runner container. Use your secret-management procedure on the controller to prepare `runtime/deploy-key` (0600) and verified `runtime/deploy-known-hosts`. The runner does not mount host `.ssh` or forward an SSH agent automatically. Substitute the account, host, and path for CI use.

Use the same command as your CI/CD deploy stage and treat nonzero exit as failure. Do not disable SSH host verification. Use a trusted deployment account with Docker access. Keep persistent configuration on the server after initial setup, outside release archives and CI logs. Only trusted branches should trigger deployment; allow one deployment pipeline per environment. The script does not guarantee zero downtime or validate real company tokens and proxy certificates.

## 5. Platform tokens and company CA

An administrator creates a project and connection, checks the connection, then imports repositories. The application performs remote reads only; limit the token's own permissions too:

- GitHub fine-grained PAT: select repositories and grant Metadata read, Contents read, and Pull requests read. Discovery uses `/user/repos`; installation tokens are unsupported. Classic `repo` scope is broader than read-only. See [repository listing](https://docs.github.com/en/rest/repos/repos#list-repositories-for-the-authenticated-user), [PR API](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests), and [PAT management](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).
- GitLab: `read_api` plus `read_repository`; token type and visibility depend on the instance's policies. See [official token scopes](https://docs.gitlab.com/security/tokens/access_token_scopes/).
- GitHub Enterprise / self-managed GitLab: supply the HTTPS instance root; `/api/v3` / `/api/v4` forms are recognized. SSH Git and disabling TLS verification are unsupported.

Place a company CA at `runtime/ca/company-ca.pem` and configure `/etc/gev/ca/company-ca.pem` in the connection. Compose mounts this directory read-only. Restart the relevant services and recheck the connection after certificate changes.

Tokens are stored in the database with Fernet encryption. Preserve and back up `GEV_CREDENTIAL_KEY`. Rotation requires migrating existing ciphertext; losing the key prevents decryption of old tokens. Token or CA changes pause the connection until an administrator checks it again.

## 6. Key configuration

`.env.example` lists available fields. Redeployment preserves stable secrets.

| Field | Default / purpose |
|---|---|
| `GEV_DATABASE_URL` | `sqlite:////data/db/gev.sqlite3`, persistent DB file |
| `GEV_JOB_BACKEND` | `database`; no Redis |
| `GEV_CACHE_ROOT` | `/data/git` |
| `GEV_SESSION_SECRET` | Stable random value of at least 32 characters |
| `GEV_CREDENTIAL_KEY` | Fernet key; back up with the database |
| `GEV_PRODUCTION`, `GEV_COOKIE_SECURE` | Both `true` in production |
| `GEV_PORT` | Local 8766, production 8767; loopback only |
| `COMPOSE_PROJECT_NAME` | Different names isolate instance volumes |
| `GEV_ENV_FILE` | Must match `--env-file` |
| `GEV_APP_IMAGE` | Application image tag for this instance |
| `GEV_JOB_TIMEOUT_SECONDS` | 3600 seconds |
| `GEV_SCHEDULER_INTERVAL_SECONDS` | 15 seconds; due repositories and interrupted jobs |
| `GEV_HEARTBEAT_INTERVAL_SECONDS` | 15 seconds |
| `GEV_SESSION_MAX_AGE` | 43200 seconds |

The worker checks queued jobs every second. Repository synchronization defaults to 300 seconds, configurable by administrators. Production accepts absolute SQLite file paths and still requires stable keys and Secure cookies. PostgreSQL/RQ adapters remain for compatibility, outside the default deployment.

### 6.1 Mount local repositories

Use an editor to add an existing host repository root to the relevant `runtime/local.env` (or `runtime/production.env`):

```dotenv
GEV_LOCAL_GIT_DIRECTORY=/Users/elvinyao/workspace
```

Apply it with `bash .agent/run.sh deploy`; add `--no-build` for mapping-only changes after updating the image. Without this setting, an empty `runtime/local-repositories/` directory is used. Both deployment methods mount the root read-only at `/data/local-git`, limiting the administrator's directory picker to that root. Docker Desktop must allow sharing the host directory; Linux permissions must allow directory traversal and reading Git files.

The source directory is never used for cache writes or remote fetch/push. Synchronization copies local Git objects into `git-cache`. Do not point an existing source at another set of same-named repositories; preserve the directory structure during migration or create a new source.

## 7. Daily operations and diagnosis

Substitute `runtime/local.env` for local operation:

```bash
bash .agent/run.sh compose --env-file runtime/production.env ps
bash .agent/run.sh compose --env-file runtime/production.env logs --tail=100 web
bash .agent/run.sh compose --env-file runtime/production.env exec -T web alembic current
bash .agent/run.sh compose --env-file runtime/production.env stop web
bash .agent/run.sh deploy --production --no-build --wait-timeout 300
```

`ps` should show one running web service. Stopped containers from the old deployment may appear in `ps -a`; they consume no running resources.

If Docker is unavailable, start it; do not fall back to host Python. For stalled synchronization, inspect web logs, paused connections, and token/CA validity. Database busy errors may indicate extra manually started writers. Never delete active WAL, cache, or lock files. Keep the application stopped after migration failure until repaired or restored. Login loops require checking HTTPS, cookies, and session keys; token decryption requires the matching Fernet key.

## 8. Backup, restore, and reverting an upgrade

The helper uses SQLite's backup API, including committed WAL data during online backups. Do not simply copy a live database file. Choose a new directory for each backup; existing directories are rejected. Matching env secrets are saved too. Transfer both to controlled encrypted storage.

```bash
bash .agent/run.sh compose --env-file runtime/production.env run --rm --no-deps -T tools python .agent/sqlite-backup.py backup runtime/backups/pre-deploy --env-file runtime/production.env
```

Restore overwrites the current database. Verify the target and backup, stop web, and ensure no tools or development processes access the database. The environment must match the backup; verify and restore matching configuration explicitly if it differs.

```bash
bash .agent/run.sh compose --env-file runtime/production.env stop web
bash .agent/run.sh compose --env-file runtime/production.env run --rm --no-deps -T tools python .agent/sqlite-backup.py restore runtime/backups/pre-deploy --env-file runtime/production.env
bash .agent/run.sh deploy --production --no-build
```

Git caches can be fetched again, or separately archived while stopped to speed recovery. Verify schema compatibility before reverting application code; restore the matching database and keys when needed. Avoid `down -v`: it deletes persistent data.

## 9. Acceptance checks and capacity limits

Verify healthy persistent services, `/health`, anonymous reading, and administrator login over HTTPS. Then check a connection using real company tokens / CA and import one actual repository.

See the [development guide](development.md#7-testing-and-validation) for source, installed package, real PostgreSQL / Redis / RQ, and Chromium checks. Default limits are 10,000 branches per repository, 4 MiB Git output, 30 seconds for ordinary Git commands, 120 seconds for clone / fetch, five job attempts, and 1000 provider pages. Workspace history is limited to 100 commits and 200 files; branch unique-commit pages show at most 50.

The demo and scale sample contain only three commits and do not establish capacity for real large repositories. Company platform versions, networking, tokens, CA, long-running operation, and proxies need validation in their actual environment. SSO, webhooks, SSH Git, and unlimited history browsing are not implemented.
