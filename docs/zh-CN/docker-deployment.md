# Docker 运行、自动部署与运维

[文档目录](../README.md) · [日本語](../ja/docker-deployment.md) · [English](../en/docker-deployment.md)

## 1. 前提与部署组成

适用于运行 Docker 的单机 Linux 服务器或 Docker Desktop 开发机。源码已放到目标机并包含 `.agent/`；从仓库根目录执行全部命令。宿主不需要安装 Python、Node.js、pip 或 Compose 插件。

统一入口 `bash .agent/run.sh ...`：普通命令进入 `python:3.13-bookworm`；Compose 与部署调度进入 `docker:28-cli`。Docker daemon 需运行，runner 需要访问 `/var/run/docker.sock` 和当前目录。此 socket 具有主机管理权限，仅让可信运维及可信 CI 使用。

| 服务 | 职责 | 持久化 |
|---|---|---|
| `postgres` | PostgreSQL 17，配置、快照、用户、审计、任务 / outbox | `postgres-data` |
| `redis` | Redis 7.4，RQ 投递与重试调度 | `redis-data`，启用 AOF |
| `migrate` | 一次性 `alembic upgrade head` | 使用同一数据库 |
| `web` | FastAPI / Uvicorn，容器端口 8000 | 共用 Git cache |
| `worker` | 两个 RQ worker，Git / API 同步与比较 | `git-cache` |
| `dispatcher` | 定时检查到期仓库，恢复 outbox 投递 | 数据库与 Redis |
| `tools` | 一次性管理 CLI，挂载源码 | 默认不常驻 |
| `integration`, `browser` | 可选验证镜像 | 默认不启动 |

仅 Web 发布到宿主 **127.0.0.1**；数据库与 Redis 没有宿主端口。`/health` 检查数据库；Redis 故障时缓存读取仍可用。worker / dispatcher 各自有健康检查，健康不表示每个远端仓库都已同步成功。

## 2. 一条 shell 命令部署

本机演示／开发：

```bash
bash .agent/run.sh deploy
```

首次生成 `runtime/local.env`，随机数据库密码、session secret、Fernet key，权限 0600。无需 `.venv`，不创建账号、不 seed 数据。部署成功后打开 [http://127.0.0.1:8766](http://127.0.0.1:8766)。

重复运行会保留 env 原文、密钥、账号及数据卷。首次下载和安装依赖需要网络与时间；后续构建复用 Docker 缓存，不承诺固定秒数。

```bash
# 源码变更后：构建当前检出的版本并部署
bash .agent/run.sh deploy

# 源码／依赖没变，已存在正确镜像：省去构建
bash .agent/run.sh deploy --no-build

# 显式更新 PostgreSQL / Redis 镜像和构建时的 Python 基础镜像
bash .agent/run.sh deploy --pull

bash .agent/run.sh deploy --help
```

`--no-build` 不会把新源码打进旧镜像。生产不能靠它更新未构建的修改。

| 参数 | 用途 |
|---|---|
| `--production` | 生产模式，默认 `runtime/production.env`、8767、Secure cookie |
| `--env-file runtime/name.env` | 指定 runtime 下的相对 env 文件；自定义新文件需同时给项目名 |
| `--project-name gev-staging` | 新配置的 Compose 项目名，隔离网络、容器、数据卷和生成的镜像标签 |
| `--port 8770` | 新配置的本机绑定端口 |
| `--no-build` | 复用已有应用镜像 |
| `--pull` | 拉取基础／基础设施镜像；不自动更新 Python 锁定依赖 |
| `--wait-timeout 300` | 每个健康等待阶段的上限秒数，默认 180；不限制构建总时间 |

已有配置的 port / 项目名不会被参数改写；需要明确编辑 env。支持普通 `KEY=value`、整行注释及简单引号；不支持 env 内变量插值。快速脚本使用 Compose 内置的 `gev` PostgreSQL 数据库及 `/data/git`，自定义外部数据库需自行调整 Compose 与运维流程。

部署顺序为：校验配置 → 可选拉取 → 构建 → 验证应用设置 → 等待 PostgreSQL / Redis → 停止 Web / worker / dispatcher → 用新镜像执行迁移 → 启动并等待全部应用服务 → 输出状态和端口。每次都会显式执行迁移，不依赖旧的成功 migration 容器。

这是有短暂中断的单机部署。脚本不拉 Git、不自动备份、不自动回滚，不删除数据卷，也不覆盖现有 env。构建失败时尚未停止应用；迁移失败时应用保持停止，返回非零退出码。修复迁移或恢复已验证备份后再部署。CI 应串行执行同一环境的部署，避免重叠迁移。

## 3. 创建管理员与可选示例

空实例先创建正式管理员，密码交互输入且不回显：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python -m app.cli create-user --username your-admin --admin
```

使用长随机密码，Web 账号管理表单要求 12–1024 字符。生产命令把 env 文件换成 `runtime/production.env`。管理员创建只做一次；已有同名账号会返回错误，不重设密码。后续部署无需重复建用户。

无人值守首次建用户，可让 secret 管理器写入受控的 `runtime/admin-bootstrap.env`（0600，内容为 `GEV_ADMIN_PASSWORD=...`），再使用一次性容器：

```bash
bash .agent/run.sh compose --env-file runtime/production.env run --rm --no-deps -T --env-from-file /workspace/runtime/admin-bootstrap.env tools python -m app.cli create-user --username your-admin --admin --password-env GEV_ADMIN_PASSWORD
```

密码不进入命令参数和常驻 Web 环境，但容器管理者能读取一次性容器环境；完成后按 secret 管理流程移除文件。不要在日志输出文件内容。[Compose run 官方参数](https://docs.docker.com/reference/cli/docker/compose/run/)。

若只想体验 UI，**仅在空的非生产 local 数据库**执行：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python .agent/seed-local.py
```

访问资料在 `runtime/demo-access.json`（0600；仅向授权人员展示）。生成 3 个仓库、各 55 分支、3 个真实空树提交及明确虚构的 PR。示例连接保持暂停；不要检查其虚构地址。已有账号／数据时 seed 拒绝覆盖；中断后应使用独立新空实例，不能 reset 正在使用的数据库。

## 4. 生产部署、HTTPS 与远程自动化

```bash
bash .agent/run.sh deploy --production --wait-timeout 300
```

首次生成独立随机 `runtime/production.env`：项目名 `git-easy-view-production`、镜像标签 `git-easy-view:git-easy-view-production`、端口 8767、`GEV_PRODUCTION=true`、`GEV_COOKIE_SECURE=true`。已有 local 文件不改变。不同项目名隔离持久卷，**只换 env 文件不够**。[Compose 项目名说明](https://docs.docker.com/compose/how-tos/project-name/)。

脚本部署后端，不签发证书或自动开放公网。生产必须通过 HTTPS 反向代理访问，HTTP 无法正常使用 Secure 登录 cookie。以下为**同一宿主机** Nginx 配置示例，域名和证书路径换成已准备好的值：

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

代理如果运行在另一容器中，`127.0.0.1` 指该容器，不能照抄上面的上游；将代理接入同一受控 Docker 网络后使用 `web:8000`。匿名项目访问覆盖所有关联仓库，内网访问边界可由公司的网络／反向代理配置限制。

新预发布实例示例：

```bash
bash .agent/run.sh deploy --production --env-file runtime/staging.env --project-name gev-staging --port 8771 --wait-timeout 300
```

远程服务器已具备 Docker、源码、持久 env、HTTPS 代理后，发布当前检出的版本：

```bash
bash .agent/run.sh ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -i runtime/deploy-key -o UserKnownHostsFile=runtime/deploy-known-hosts deploy@server.example 'cd /srv/git-easy-view && bash .agent/run.sh deploy --production --wait-timeout 300'
```

源码可由 CI 上传受信发布包，或在**干净的专用部署 checkout**执行下面的更新，再部署。`origin/main` 只是示例，请换成批准的发布 ref；Git 写操作仍在 Docker 中：

```bash
bash .agent/run.sh git -c safe.directory=/workspace fetch origin
bash .agent/run.sh git -c safe.directory=/workspace merge --ff-only origin/main
bash .agent/run.sh deploy --production --wait-timeout 300
```

上面的 SSH 客户端也在 runner 容器内运行。控制端由受控 secret 管理流程准备 `runtime/deploy-key`（0600）与预先验证的 `runtime/deploy-known-hosts`；runner 不自动挂载宿主 `.ssh` 或 SSH agent。替换账号、主机和路径即可用于 CI。

CI/CD 的 deploy 阶段运行同一命令，非零退出码即失败。不禁用 SSH 主机身份检查；使用有 Docker 权限的可信部署账号。首次 env 初始化之后，持久配置保留在服务器，不放进发布包或 CI 日志；仅可信分支可触发部署。同一环境只允许一个流水线部署。脚本不保证零停机，也不验证公司的真实平台 token 或代理证书。

## 5. 平台令牌与公司 CA

管理员登录后创建项目与连接，成功检查连接后导入仓库。应用只做远端读取，但 token 本身也应限制权限：

- GitHub fine-grained PAT：选定仓库，Metadata read、Contents read、Pull requests read。仓库发现使用 `/user/repos`，不支持 installation token；classic `repo` scope 更广，不等于只读。参见[仓库列表](https://docs.github.com/en/rest/repos/repos#list-repositories-for-the-authenticated-user)、[PR API](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests)、[PAT 管理](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)。
- GitLab：`read_api` 与 `read_repository`，token 类型和可见范围以实际实例策略为准。参见[官方 token scopes](https://docs.gitlab.com/security/tokens/access_token_scopes/)。
- GitHub Enterprise / 自建 GitLab：使用 HTTPS 实例根地址，可识别 `/api/v3` / `/api/v4`；不用 SSH，不关闭 TLS 验证。

公司 CA 放入 `runtime/ca/company-ca.pem`，连接填 `/etc/gev/ca/company-ca.pem`。Compose 只读挂载该目录。更新证书后重启相关服务并重新检查连接。

token 以 Fernet 加密存入数据库。保管并备份 `GEV_CREDENTIAL_KEY`；更换 key 需要先迁移旧密文，丢失 key 后旧 token 无法解密。修改 token 或 CA 后连接暂停，管理员重新检查才恢复。

## 6. 关键配置

`.env.example` 仅列字段和占位符，不可直接用于部署。首次通常让脚本生成文件；后续明确编辑，并保持所有进程使用相同值。

| 字段 | 含义 / 默认 |
|---|---|
| `COMPOSE_PROJECT_NAME` | 实例隔离标识；已部署后更改会指向另一组卷 |
| `GEV_ENV_FILE` | Compose 服务实际加载的 env 路径，须与 `--env-file` 一致 |
| `GEV_APP_IMAGE` | 应用镜像标签；旧 local 配置缺失时使用 `git-easy-view:local` |
| `POSTGRES_PASSWORD`, `GEV_DATABASE_URL` | 必须匹配；数据库卷首次初始化后，改文件不会改数据库密码 |
| `GEV_SESSION_SECRET` | 至少 32 字符的随机稳定值；更换会使现有会话失效 |
| `GEV_CREDENTIAL_KEY` | 32 字节随机内容的 URL-safe Base64 Fernet key |
| `GEV_REDIS_URL` | 默认 `redis://redis:6379/0` |
| `GEV_CACHE_ROOT` | Compose 中 `/data/git`，Web / worker 共享 |
| `GEV_PRODUCTION`, `GEV_COOKIE_SECURE` | 正式部署均为 `true` |
| `GEV_PORT` | local 8766，production 8767，仅绑定 loopback |
| `GEV_QUEUE_NAME` | RQ 队列名，默认 `git-sync` |
| `GEV_JOB_TIMEOUT_SECONDS` | 后台任务超时，默认 3600 秒 |
| `GEV_SCHEDULER_INTERVAL_SECONDS` | dispatcher 周期，默认 15 秒 |
| `GEV_HEARTBEAT_INTERVAL_SECONDS` | 任务心跳，默认 15 秒 |
| `GEV_SESSION_MAX_AGE` | 会话期限，默认 43200 秒 |

单仓库同步间隔默认 300 秒，在管理 UI 配置。生产设置会拒绝 SQLite、缺少密钥或明确关闭 Secure cookie。现有 PostgreSQL 卷轮换密码时需要先执行数据库密码变更，再协调 env 和服务重启。

## 7. 日常操作与诊断

下面使用 production；本地实例替换 env 文件名。

```bash
bash .agent/run.sh compose --env-file runtime/production.env ps -a
bash .agent/run.sh compose --env-file runtime/production.env logs --tail=100 web worker dispatcher
bash .agent/run.sh compose --env-file runtime/production.env exec -T web python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health").read().decode())'
bash .agent/run.sh compose --env-file runtime/production.env exec -T web alembic current

# 停止但保留容器、数据卷
bash .agent/run.sh compose --env-file runtime/production.env stop

# 配置与镜像没变化时恢复服务；更新版本用 deploy
bash .agent/run.sh compose --env-file runtime/production.env up -d --wait --wait-timeout 300
```

`--wait` 等待服务 running / healthy，失败返回错误；见 [Compose up 官方参数](https://docs.docker.com/reference/cli/docker/compose/up/)。查看日志前注意其中的仓库、路径和用户信息，避免分享敏感内容；不要运行会打印所有密钥的完整 `compose config`，使用 `config --quiet`。

| 现象 | 检查 |
|---|---|
| Docker socket 无法连接 | 启动 Docker，检查部署账号权限；不要改用宿主 Python |
| 端口占用 | 明确编辑 `GEV_PORT`，确认代理上游同步调整 |
| Postgres authentication failed | 确认旧卷密码与 env 一致；不能通过重新随机生成文件修复 |
| migrate 失败 | 检查迁移输出及 DB；应用保持停止，修复或恢复备份后重新 deploy |
| 健康等待超时 | `ps -a` 和相关日志；资源慢时提高 `--wait-timeout`，但先排除真实错误 |
| 能看页面但无新快照 | 检查 worker、dispatcher、Redis、连接暂停、token / CA 及同步状态 |
| 登录循环 | 用 HTTPS，检查 cookie、代理与所有服务共用的 session secret |
| token 解密失败 | 恢复数据库对应的 Fernet key；不要覆盖旧 key |

强杀 worker 可能留下仍存活的 Git 子进程及锁。先终止对应进程或重启 worker 容器，确认无活动后再处理；不要在 Git 正在运行时删除缓存或锁。

## 8. 备份、恢复与升级回退

发布前至少备份 PostgreSQL 和对应 env（特别是 Fernet key），并受控保存 CA。Git cache 可重新抓取；保存它可缩短恢复时间。Redis 是投递层，数据库 outbox 保留任务事实。

以下示例写入 `runtime/backups/pre-deploy/`；每次改为新的备份目录，避免覆盖唯一副本。所有写入和重定向都在容器中：

```bash
bash .agent/run.sh python -c 'import os,shutil; from pathlib import Path; os.umask(0o077); p=Path("runtime/backups/pre-deploy"); p.mkdir(parents=True,exist_ok=True); shutil.copyfile("runtime/production.env",p/"deployment.env"); os.chmod(p/"deployment.env",0o600)'
bash .agent/run.sh compose --env-file runtime/production.env exec -T postgres sh -c 'umask 077; pg_dump -U gev -d gev -Fc -f /tmp/gev-backup.dump'
bash .agent/run.sh compose --env-file runtime/production.env cp postgres:/tmp/gev-backup.dump /workspace/runtime/backups/pre-deploy/gev.dump
bash .agent/run.sh python -c 'import os; os.chmod("runtime/backups/pre-deploy/gev.dump",0o600)'
```

将备份与密钥放入公司加密备份系统，并在隔离实例定期演练恢复。恢复操作会覆盖目标数据库：确认目标实例、备份版本与配套 key 后，停止全部应用写入者：

```bash
bash .agent/run.sh compose --env-file runtime/production.env stop web worker dispatcher
bash .agent/run.sh compose --env-file runtime/production.env cp /workspace/runtime/backups/pre-deploy/gev.dump postgres:/tmp/gev-restore.dump
bash .agent/run.sh compose --env-file runtime/production.env exec -T postgres pg_restore -U gev -d gev --clean --if-exists --exit-on-error /tmp/gev-restore.dump
bash .agent/run.sh deploy --production --wait-timeout 300
```

最后一步会升级到当前检出版本。若要回退到备份对应版本，先用受控发布包恢复该源码，再运行 deploy；旧代码不一定兼容新 schema，单独换旧镜像不能保证回退成功。恢复数据库对应 key 后才启动。

普通停止不要使用 `down -v`，该参数删除持久卷。不要删除 `runtime/production.env` 后当作重新安装，它保存了恢复旧数据必须使用的密钥。

## 9. 验收与容量边界

部署完成后确认所有常驻服务健康、`/health` 返回成功、匿名项目读取成功、管理员 HTTPS 登录成功，再用公司真实 token / CA 检查连接并导入一个实际仓库。

源码、安装包、真实 PostgreSQL / Redis / RQ 和 Chromium 检查见[开发指南](development.md#7-测试与验证)。默认上限：单仓库 10,000 分支，Git 输出 4 MiB，普通 Git 命令 30 秒，clone / fetch 120 秒，任务最多 5 次尝试，provider 最多 1000 页。工作台历史 100 条、文件 200 项；分支独有提交页面最多 50 条。

当前示例和规模样本只有三个提交，不能作为真实大仓库容量承诺。公司平台版本、网络、token、CA、长期运行和反向代理需在实际环境验收；SSO、webhook、SSH Git 与无限历史浏览未实现。
