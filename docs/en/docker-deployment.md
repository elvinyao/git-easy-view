# Docker, Automated Deployment, and Operations

[Documentation index](../README.md) · [简体中文](../zh-CN/docker-deployment.md) · [日本語](../ja/docker-deployment.md)

## 1. Prerequisites and services

Use a single Linux server with Docker or a development machine with Docker Desktop. Place the repository, including `.agent/`, on the target machine and run commands from its root. Host Python, Node.js, pip, and a Compose plugin are unnecessary.

The entry point is `bash .agent/run.sh ...`. Ordinary commands execute in `python:3.13-bookworm`; Compose and deployment orchestration execute in `docker:28-cli`. Docker must be running, and the runner needs `/var/run/docker.sock` and repository access. This socket grants host administration privileges; use trusted operators and trusted CI only.

| Service | Responsibility | Persistence |
|---|---|---|
| `postgres` | PostgreSQL 17; settings, snapshots, users, audit, jobs / outbox | `postgres-data` |
| `redis` | Redis 7.4; RQ delivery and retry scheduling | `redis-data`, AOF enabled |
| `migrate` | One-off `alembic upgrade head` | Shared database |
| `web` | FastAPI / Uvicorn, container port 8000 | Shared Git cache |
| `worker` | Two RQ workers for Git / API synchronization and comparison | `git-cache` |
| `dispatcher` | Due repository scheduling and outbox recovery | Database and Redis |
| `tools` | One-off administration CLI with source mount | Not normally running |
| `integration`, `browser` | Optional verification images | Not normally started |

Only Web publishes a host port, bound to **127.0.0.1**. PostgreSQL and Redis publish no host ports. `/health` checks the database; cached reading remains available during a Redis outage. Workers and the dispatcher have separate health checks. Healthy services do not prove every remote repository synchronized successfully.

## 2. Deploy with one shell command

For local development or demonstration:

```bash
bash .agent/run.sh deploy
```

The first run creates `runtime/local.env` with a random database password, session secret, and Fernet key, using mode 0600. No `.venv` is needed. It creates no accounts or sample database records. After success, open [http://127.0.0.1:8766](http://127.0.0.1:8766).

Repeat runs preserve the env file contents, keys, accounts, and volumes. Initial downloads and dependency installation need network access and time. Later builds reuse Docker's cache; there is no fixed deployment time guarantee.

```bash
# After source changes: build and deploy the current checkout
bash .agent/run.sh deploy

# Unchanged source/dependencies and an existing correct image: skip building
bash .agent/run.sh deploy --no-build

# Explicitly refresh PostgreSQL/Redis images and the Python build base
bash .agent/run.sh deploy --pull

bash .agent/run.sh deploy --help
```

`--no-build` does not put new source into an old image. Do not use it to publish changes that have not been built.

| Option | Purpose |
|---|---|
| `--production` | Production mode; defaults to `runtime/production.env`, port 8767, Secure cookies |
| `--env-file runtime/name.env` | Relative env path under runtime; a new custom file also needs a project name |
| `--project-name gev-staging` | Compose name for new configuration; separates networks, containers, volumes, and generated image tags |
| `--port 8770` | Loopback port for new configuration |
| `--no-build` | Reuse the existing application image |
| `--pull` | Pull base / infrastructure images; does not update locked Python dependencies |
| `--wait-timeout 300` | Maximum seconds per health-wait phase; default 180, not a build duration limit |

Options do not rewrite an existing port or project name; edit the env deliberately. The helper accepts literal `KEY=value`, whole-line comments, and simple quoting, without variable interpolation. Quick deployment uses the bundled `gev` PostgreSQL database and `/data/git`. An external database requires separate Compose and operational changes.

The sequence is configuration validation → optional image pull → build → application settings validation → PostgreSQL / Redis readiness → stop Web / workers / dispatcher → migrate using the new image → start and wait for all application services → show status and port. Migrations execute explicitly every time instead of relying on an old completed migrate container.

This is a single-host deployment with a brief interruption. It does not fetch Git, back up automatically, roll back automatically, delete volumes, or overwrite an existing env. Build failure occurs before application shutdown. Migration failure leaves application services stopped and returns a nonzero exit code; fix the migration or restore a verified backup before retrying. Serialize CI deployments for the same environment.

## 3. Create an administrator or an optional demo

Create a real administrator once on an empty installation. The password prompt does not echo:

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python -m app.cli create-user --username your-admin --admin
```

Use a long random password. Web account forms require 12–1024 characters. Change the env path to `runtime/production.env` for production. An existing username causes an error, without resetting its password. Redeployment does not require recreating accounts.

For unattended first-time creation, have a secret manager place `runtime/admin-bootstrap.env` with mode 0600 and `GEV_ADMIN_PASSWORD=...`, then pass it to a one-off container:

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

`.env.example` is a field reference containing placeholders, not usable credentials. Normally generate the file with the helper, then edit deliberately. Keep values consistent across processes.

| Field | Meaning / default |
|---|---|
| `COMPOSE_PROJECT_NAME` | Instance identity; changing it selects another set of volumes |
| `GEV_ENV_FILE` | Env loaded by services; must match `--env-file` |
| `GEV_APP_IMAGE` | Application tag; legacy local files fall back to `git-easy-view:local` |
| `POSTGRES_PASSWORD`, `GEV_DATABASE_URL` | Must match; editing the file does not change an initialized database password |
| `GEV_SESSION_SECRET` | Stable random value, at least 32 characters; rotation invalidates sessions |
| `GEV_CREDENTIAL_KEY` | URL-safe Base64 encoding of 32 random bytes for Fernet |
| `GEV_REDIS_URL` | `redis://redis:6379/0` |
| `GEV_CACHE_ROOT` | `/data/git` in Compose, shared by Web and workers |
| `GEV_PRODUCTION`, `GEV_COOKIE_SECURE` | Both `true` for production |
| `GEV_PORT` | Local 8766, production 8767; loopback binding |
| `GEV_QUEUE_NAME` | RQ queue, `git-sync` |
| `GEV_JOB_TIMEOUT_SECONDS` | Job timeout, 3600 seconds |
| `GEV_SCHEDULER_INTERVAL_SECONDS` | Dispatcher interval, 15 seconds |
| `GEV_HEARTBEAT_INTERVAL_SECONDS` | Job heartbeat, 15 seconds |
| `GEV_SESSION_MAX_AGE` | Session lifetime, 43200 seconds |

The per-repository synchronization interval defaults to 300 seconds and is configured in the admin UI. Production validation rejects SQLite, missing keys, and explicitly disabled Secure cookies. For an initialized PostgreSQL volume, coordinate database password rotation with env changes and service restarts.

## 7. Daily operations and diagnosis

These examples use production; substitute the local env file when appropriate.

```bash
bash .agent/run.sh compose --env-file runtime/production.env ps -a
bash .agent/run.sh compose --env-file runtime/production.env logs --tail=100 web worker dispatcher
bash .agent/run.sh compose --env-file runtime/production.env exec -T web python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health").read().decode())'
bash .agent/run.sh compose --env-file runtime/production.env exec -T web alembic current

# Stop while retaining containers and volumes
bash .agent/run.sh compose --env-file runtime/production.env stop

# Resume unchanged configuration/images; use deploy for an update
bash .agent/run.sh compose --env-file runtime/production.env up -d --wait --wait-timeout 300
```

`--wait` waits for running / healthy services and reports failure. See [official Compose up options](https://docs.docker.com/reference/cli/docker/compose/up/). Logs can contain repository paths and user information. Avoid sharing sensitive data. Use `config --quiet` instead of printing the complete configuration and its secrets.

| Symptom | Check |
|---|---|
| Docker socket unavailable | Start Docker and check account permissions; do not fall back to host Python |
| Port occupied | Edit `GEV_PORT` deliberately and update the proxy upstream |
| PostgreSQL authentication failure | Match env credentials with the existing volume; regenerating env does not repair them |
| Migration fails | Inspect migration output and DB; leave apps stopped until repaired or restored |
| Health timeout | Inspect `ps -a` and logs; increase the timeout only after checking actual faults |
| Pages work but snapshots do not update | Workers, dispatcher, Redis, paused connections, tokens / CA, sync status |
| Login loop | HTTPS, cookie, proxy, shared session secret |
| Token decryption failure | Restore the matching Fernet key; do not overwrite the old key |

Force-killing a worker can leave a live Git child holding a lock. Terminate the relevant process or restart that worker container first. Do not delete caches or locks while Git is active.

## 8. Backup, restore, and reverting an upgrade

Before deployment, back up PostgreSQL and its matching env, especially the Fernet key, and retain controlled CA copies. Git cache can be fetched again; retaining it speeds recovery. Redis is the delivery layer, while the database outbox retains job facts.

This example uses `runtime/backups/pre-deploy/`. Choose a fresh directory each time rather than overwriting the only backup. All file writing and redirection happen inside containers:

```bash
bash .agent/run.sh python -c 'import os,shutil; from pathlib import Path; os.umask(0o077); p=Path("runtime/backups/pre-deploy"); p.mkdir(parents=True,exist_ok=True); shutil.copyfile("runtime/production.env",p/"deployment.env"); os.chmod(p/"deployment.env",0o600)'
bash .agent/run.sh compose --env-file runtime/production.env exec -T postgres sh -c 'umask 077; pg_dump -U gev -d gev -Fc -f /tmp/gev-backup.dump'
bash .agent/run.sh compose --env-file runtime/production.env cp postgres:/tmp/gev-backup.dump /workspace/runtime/backups/pre-deploy/gev.dump
bash .agent/run.sh python -c 'import os; os.chmod("runtime/backups/pre-deploy/gev.dump",0o600)'
```

Move backups into your company's encrypted backup system and rehearse recovery in an isolated instance. Restore overwrites the target database. Confirm the instance, backup version, and matching key before stopping application writers:

```bash
bash .agent/run.sh compose --env-file runtime/production.env stop web worker dispatcher
bash .agent/run.sh compose --env-file runtime/production.env cp /workspace/runtime/backups/pre-deploy/gev.dump postgres:/tmp/gev-restore.dump
bash .agent/run.sh compose --env-file runtime/production.env exec -T postgres pg_restore -U gev -d gev --clean --if-exists --exit-on-error /tmp/gev-restore.dump
bash .agent/run.sh deploy --production --wait-timeout 300
```

The final command upgrades to the current checkout. To revert to the backup's version, first restore that source from a controlled release. Older code may be incompatible with a newer schema; merely changing to an old image does not guarantee a safe rollback. Restore the database's matching key before starting.

Do not use `down -v` for ordinary stops; it deletes persistent volumes. Do not remove `runtime/production.env` as a reinstallation step: it contains keys needed to recover existing data.

## 9. Acceptance checks and capacity limits

Verify healthy persistent services, `/health`, anonymous reading, and administrator login over HTTPS. Then check a connection using real company tokens / CA and import one actual repository.

See the [development guide](development.md#7-testing-and-validation) for source, installed package, real PostgreSQL / Redis / RQ, and Chromium checks. Default limits are 10,000 branches per repository, 4 MiB Git output, 30 seconds for ordinary Git commands, 120 seconds for clone / fetch, five job attempts, and 1000 provider pages. Workspace history is limited to 100 commits and 200 files; branch unique-commit pages show at most 50.

The demo and scale sample contain only three commits and do not establish capacity for real large repositories. Company platform versions, networking, tokens, CA, long-running operation, and proxies need validation in their actual environment. SSO, webhooks, SSH Git, and unlimited history browsing are not implemented.
