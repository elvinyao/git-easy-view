# Intranet deployment: proxies and company CAs

[简体中文](README.md) · [日本語](README.ja.md) · [Full documentation](../docs/README.md)

This directory contains a separate Dockerfile, Compose configuration, and deployment script for networks requiring outbound HTTP/HTTPS proxies and company CAs. It also works with direct internet access on a personal computer. Only one application container remains running; SQLite and Git caches use the existing persistent volumes.

## 1. Configure Docker image access first

Use Docker Engine 20.10+ (or a corresponding Docker Desktop release) and BuildKit. Run the standalone entry point `bash deploy/deploy.sh` from the repository root. On the host it only invokes Docker; configuration, builds, migrations, and application commands execute in containers. Host Python, Node, and `.agent/run.sh` are unnecessary.

**Both modern `docker compose` and legacy `docker-compose` (minimum 1.29.2) are supported.** The default `docker:28-cli` tool container makes deployment independent of whether Compose is installed on the host and which version it has. The script prefers the tool container's Compose plugin and falls back to `docker-compose`. Older versions automatically use compatible configuration and health polling while preserving BuildKit secrets, proxy values, and CA settings. No additional persistent container is needed.

For v1 updates, the deployment script replaces the web container after a successful database migration and retains the named SQLite and Git cache volumes. Use `bash deploy/deploy.sh` for updates: calling legacy `docker-compose up` directly on newer Docker Engines can fail because of the obsolete `ContainerConfig` field.

To use an internal tool image, set `GEV_DOCKER_CLI_IMAGE`. The image must contain `docker`, `sh`, `awk`, `date`, and either Compose implementation above. Replace the example address with your actual image and use the same setting for subsequent deployment and operations commands. Personal computers need no override:

```bash
GEV_DOCKER_CLI_IMAGE=registry.example.com/tools/docker-cli-compose:1.29.2 bash deploy/deploy.sh
GEV_DOCKER_CLI_IMAGE=registry.example.com/tools/docker-cli-compose:1.29.2 bash deploy/deploy.sh compose --env-file runtime/local.env ps
```

There are two separate network configuration layers:

| Layer | Configuration | Purpose |
|---|---|---|
| Docker itself | Desktop settings, or Engine daemon settings and host trust | Pull Python, Docker CLI, and build base images |
| Build and application | `runtime/*.network.env` and the CA directory | Download pip dependencies and make runtime Git/API requests |

**Configure the first layer before deployment.** Application configuration cannot repair a tool image pull that has not succeeded yet.

Docker Desktop: configure Proxies in Settings, add company roots to host trust, and restart Desktop. Desktop ignores proxy settings in `daemon.json`. See [Desktop proxy settings](https://docs.docker.com/desktop/settings-and-maintenance/settings/#proxies) and [Docker CA guidance](https://docs.docker.com/engine/network/ca-certs/).

Linux Docker Engine: have an administrator merge these fields into `/etc/docker/daemon.json`, configure system CA trust, and restart Docker. Preserve existing settings. See [Engine proxy configuration](https://docs.docker.com/engine/daemon/proxy/).

```json
{
  "proxies": {
    "http-proxy": "http://proxy.corp.example:3128",
    "https-proxy": "http://proxy.corp.example:3128",
    "no-proxy": "localhost,127.0.0.1,.corp.example"
  }
}
```

## 2. Prepare the initial configuration

```bash
bash deploy/deploy.sh --init
```

This prepares files only; it does not build, start the application, or create users. Existing application configuration and keys are preserved.

| File / directory | Purpose |
|---|---|
| `runtime/local.env` | Application secrets, SQLite URL, Compose project name, and port |
| `runtime/local.network.env` | Outbound proxies and optional pip indexes |
| `runtime/local.deploy.env` | Network file, CA directory, bind address, and other deployment options |
| `runtime/local.deploy/` | Generated build secrets and runtime network configuration; do not edit manually |
| `runtime/ca/` | Company root / intermediate CAs, as PEM-encoded `.pem` or `.crt` files |

Edit `runtime/local.network.env` with a text editor:

```dotenv
HTTP_PROXY=http://proxy.corp.example:3128
HTTPS_PROXY=http://proxy.corp.example:3128
NO_PROXY=localhost,127.0.0.1,::1,web,gitlab.corp.example,.corp.example
PIP_INDEX_URL=
PIP_EXTRA_INDEX_URL=
```

`HTTPS_PROXY` selects the proxy for HTTPS destinations. An ordinary HTTP CONNECT proxy typically still uses an `http://` URL. Use the address supplied by your company; leave proxy values empty when unnecessary.

- Use literal `KEY=value` lines. This is not a shell script: do not use `export`, variable references, or command substitution. The helper supplies both uppercase and lowercase proxy variables and retains bypass entries for local health checks.
- Add internal Git / package servers to `NO_PROXY` when they require direct access. Do not bypass the proxy for external destinations that need it.
- For a proxy running on your personal computer, use `host.docker.internal:port` from containers. `127.0.0.1` means the container itself. Deployment supplies a Linux `host-gateway` mapping; the proxy must also accept connections from the Docker network.
- Percent-encode special characters in proxy usernames and passwords. Network files can contain credentials: retain mode 0600 and keep them out of Git, logs, and tickets.
- pip indexes apply only during builds; the application receives proxy settings only. Prefer your company's consolidated `PIP_INDEX_URL`; configure an extra index only when necessary.

Place company CA files in `runtime/ca/`, for example `runtime/ca/company-root.crt`. Do not place private keys, PFX/P12 files, or standalone server certificates there. Multiple CAs are supported; personal installations without company CAs may leave the directory empty.

## 3. Deploy, update, and retain personal-computer compatibility

```bash
bash deploy/deploy.sh
bash deploy/deploy.sh --no-build
bash deploy/deploy.sh --pull --wait-timeout 300
bash deploy/deploy.sh --help
```

Use the first command for initial deployment and source updates, the second to reuse the existing intranet image, and the third to refresh the build base image too. The script validates configuration, builds, stops web, migrates the database, starts web, and waits for health. Failure returns nonzero and retains configuration and volumes. The default URL is [http://127.0.0.1:8766](http://127.0.0.1:8766).

The same `COMPOSE_PROJECT_NAME` reuses existing `app-data` and `git-cache` volumes without copying or emptying SQLite. Back up before switching deployment methods. Do not run both deployment workflows against one project concurrently.

Personal computers use the same `bash deploy/deploy.sh` command with empty proxy values and an empty CA directory. The script uses a separate `-intranet` image tag. Build it on first use; subsequent runs may use `--no-build`. This entry point supports the current single-container SQLite layout. Migrate old PostgreSQL installations using `--migrate-sqlite` in the [database migration guide](../docs/en/docker-deployment.md) first; do not just replace the database URL.

## 4. Production intranet service and separate instances

```bash
bash deploy/deploy.sh --production --init
bash deploy/deploy.sh --production --wait-timeout 300
```

Between these commands, edit `runtime/production.network.env` and install your CAs. Production uses a separate `runtime/production.env`, Compose project, and volumes; the default backend port is 8767. Browser access must use an HTTPS reverse proxy for Secure login cookies. Outbound `HTTPS_PROXY` does not provide HTTPS to users' browsers.

The default bind address is `127.0.0.1`, suitable for a reverse proxy on the same host. To allow a reverse proxy on another machine, explicitly choose the server's intranet IP or the all-interface address below, and restrict source access with a firewall:

```bash
bash deploy/deploy.sh --production --bind-address 0.0.0.0 --no-build
```

Project pages allow anonymous reading. Anyone who can reach the published port can browse projects. Production administrator login still requires HTTPS.

A separate test instance can use its own application configuration, network settings, CA directory, project name, and port:

```bash
bash deploy/deploy.sh --env-file runtime/intranet-test.env --project-name gev-intranet-test --port 8772 --network-file runtime/intranet-test.network.env --ca-dir runtime/company-ca --init
bash deploy/deploy.sh --env-file runtime/intranet-test.env
```

Custom files and directories belong under `runtime/`. Network paths, CA paths, and bind addresses are saved in the corresponding `.deploy.env`; subsequent runs need not repeat them. Existing env ports and project names are not silently rewritten by command options. Use a new env and project name for another instance. Continue passing `--production` for subsequent production deployments.

## 5. How proxies and CAs reach the container

The build mounts network configuration and CAs as BuildKit secrets. pip verifies TLS against combined system and company trust. Proxy credentials and company roots are not embedded using Dockerfile `ENV` / `ARG` instructions.

At runtime, the CA directory is mounted read-only at `/etc/gev/ca`. The entry point generates `/tmp/gev-ca-bundle.pem` containing system and company CAs, and configures trust for Python/HTTPX, pip, Git, and HTTPS proxy verification. CA private keys are unnecessary. TLS verification stays enabled.

Normally, leave a connection's CA path empty in the administration UI to use this default trust. A connection-specific CA path overrides trust for that connection's Git / API requests, so it must include the required CA chain. You may explicitly choose `/tmp/gev-ca-bundle.pem` to use the combined bundle.

Credentials are absent from image configuration, but Docker administrators can inspect running container environments. Protect the host and `runtime/` according to company credential policies. Redeploy after changing network settings or CAs; `--no-build` is sufficient for runtime-only changes when an image already exists.

## 6. Administration, backups, and troubleshooting

Use the same script's `compose` subcommand for daily operations. It selects the dedicated Compose file and loads the saved deployment settings for the selected environment:

```bash
bash deploy/deploy.sh compose --env-file runtime/local.env ps
bash deploy/deploy.sh compose --env-file runtime/local.env logs --tail=100 web
bash deploy/deploy.sh compose --env-file runtime/local.env run --rm --no-deps tools python -m app.cli create-user --username your-admin --admin
bash deploy/deploy.sh compose --env-file runtime/local.env run --rm --no-deps -T tools python .agent/sqlite-backup.py backup runtime/backups/intranet-before-update --env-file runtime/local.env
```

Administrator passwords are prompted interactively. Substitute `runtime/production.env` for production operations. Use a fresh backup directory each time. In addition to the database and matching application keys, securely retain network settings, deployment options, and CAs. Follow the [operations guide](../docs/en/docker-deployment.md) for database restoration, starting Compose commands with `bash deploy/deploy.sh compose` and restarting with `bash deploy/deploy.sh --no-build`. Do not run `down -v`.

| Symptom | Where to check |
|---|---|
| Tool / base image pull fails | Desktop / daemon proxy and system trust; application deployment has not started yet |
| pip timeout, 407, or certificate failure | Network proxy authentication, pip indexes, and company CAs used for building |
| Pages work but Git synchronization fails | Application logs, proxy reachability, `NO_PROXY`, token permissions, and connection CA paths |
| CA changes do not resolve errors | Redeploy to regenerate the bundle; check connection-specific CA overrides |
| Production login loops | Browser HTTPS, reverse proxy configuration, and Secure cookies |

Deployment health checks establish application and database readiness only. Before production use, check a real connection on the actual company network, import a repository, and wait for successful synchronization. Local verification cannot establish compatibility with company proxy authentication, CA chains, or platform permissions.
