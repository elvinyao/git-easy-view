# Docker 运行、自动部署与运维

[文档目录](../README.md) · [日本語](../ja/docker-deployment.md) · [English](../en/docker-deployment.md)

## 1. 环境要求与单容器结构

安装并启动 Docker Desktop 或 Docker Engine；从仓库根目录执行命令。宿主只读源码或编辑文件，项目执行统一使用 `bash .agent/run.sh ...`。

默认只常驻 **一个 web 容器**：包含网页、定时调度和一个后台任务执行进程。SQLite 保存项目、账号、分支与任务；无需 PostgreSQL 或 Redis。任务串行执行，网页可同时读取已有快照。进程异常退出会触发整个容器重启，未完成任务从数据库恢复。

两个持久卷分别保存 SQLite（`app-data:/data/db`）和 Git 缓存（`git-cache:/data/git`）。卷不是容器。部署时短暂运行的工具与迁移容器使用完即删除。旧服务仅在 `legacy` profile 下保留，用于迁移和兼容测试。

SQLite 使用 WAL 和 30 秒锁等待；适合单机、十几个仓库的看板。不要将数据库放在网络文件系统，也不要横向扩展 web 副本。

## 2. 一条 shell 命令部署

首次启动或更新源码使用第一条命令。第二条复用已有镜像，第三条同时更新 Python 构建基础镜像。

```bash
bash .agent/run.sh deploy
bash .agent/run.sh deploy --no-build
bash .agent/run.sh deploy --pull
bash .agent/run.sh deploy --help
```

默认访问 [http://127.0.0.1:8766](http://127.0.0.1:8766)。脚本生成或保留 `runtime/local.env`（0600），构建镜像、停止写入进程、执行迁移、启动 web 并等待健康检查。失败返回非零；保留配置和持久卷。启动不创建账号或示例数据。

已有 PostgreSQL 实例首次升级：

```bash
bash .agent/run.sh deploy --migrate-sqlite
```

脚本停止旧写入者，逐表复制到 SQLite，比较所有字段并检查外键和完整性；成功后才改数据库地址，并停止 PostgreSQL、Redis 和旧 workers。账号、ID、token 密文、会话密钥和 Git 缓存保留。原 env 保存为 `runtime/local.env.postgres-backup`，原数据库卷不删除。目标 SQLite 已存在时拒绝覆盖。生产升级增加 `--production`。

后续直接 `deploy`。不要手动把旧数据库 URL 改为 SQLite，否则会连接空数据库。中途失败时检查日志和已保留文件，不要删除原卷；复制成功后再回退旧库会丢失 SQLite 中的新修改。

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

`.env.example` 是字段参考。脚本生成稳定的密钥，重新部署不会轮换密钥。

| 字段 | 默认 / 用途 |
|---|---|
| `GEV_DATABASE_URL` | `sqlite:////data/db/gev.sqlite3`，持久数据库文件 |
| `GEV_JOB_BACKEND` | `database`，无需 Redis |
| `GEV_CACHE_ROOT` | `/data/git` |
| `GEV_SESSION_SECRET` | 至少 32 字符，保持稳定 |
| `GEV_CREDENTIAL_KEY` | Fernet key，必须与数据库一起备份 |
| `GEV_PRODUCTION`, `GEV_COOKIE_SECURE` | 生产均为 `true` |
| `GEV_PORT` | local 8766，production 8767，仅 loopback |
| `COMPOSE_PROJECT_NAME` | 不同实例使用不同名字以隔离卷 |
| `GEV_ENV_FILE` | 与 `--env-file` 一致 |
| `GEV_APP_IMAGE` | 实例应用镜像标签 |
| `GEV_JOB_TIMEOUT_SECONDS` | 3600 秒 |
| `GEV_SCHEDULER_INTERVAL_SECONDS` | 15 秒，查找到期仓库及恢复中断任务 |
| `GEV_HEARTBEAT_INTERVAL_SECONDS` | 15 秒 |
| `GEV_SESSION_MAX_AGE` | 43200 秒 |

后台每秒检查排队任务，单仓库同步间隔默认 300 秒，由管理员修改。生产允许绝对路径的 SQLite 文件，仍强制稳定密钥与 Secure cookie。PostgreSQL/RQ 适配器保留兼容用途，不属于默认部署。

## 7. 日常操作与排错

本地实例将文件名换为 `runtime/local.env`：

```bash
bash .agent/run.sh compose --env-file runtime/production.env ps
bash .agent/run.sh compose --env-file runtime/production.env logs --tail=100 web
bash .agent/run.sh compose --env-file runtime/production.env exec -T web alembic current
bash .agent/run.sh compose --env-file runtime/production.env stop web
bash .agent/run.sh compose --env-file runtime/production.env up -d --wait --wait-timeout 300 web
```

`ps` 应只有一个运行中的 web。升级遗留的停止容器可在 `ps -a` 看到，不占运行资源。

无法连接 Docker 时先启动 Docker；不能改用宿主 Python。同步没有进展时查看 web 日志、连接是否暂停及 token/CA 是否有效。数据库繁忙时检查是否有手工启动的额外写入者；不要删除 WAL、缓存或锁文件。迁移失败时保持应用停止，修复或恢复备份后重新部署。登录循环检查 HTTPS、cookie 和会话密钥；token 解密失败需恢复配套 Fernet key。

## 8. 备份、恢复与升级回退

备份工具使用 SQLite backup API，因此在线备份包含已提交的 WAL 数据，不直接复制活动数据库文件。每次使用新的目录名；已有目录会被拒绝。同时保存 env 中的密钥，并把备份放入受控加密存储。

```bash
bash .agent/run.sh compose --env-file runtime/production.env run --rm --no-deps -T tools python .agent/sqlite-backup.py backup runtime/backups/pre-deploy --env-file runtime/production.env
```

恢复会覆盖当前数据库。先确认实例和备份，再停止 web；不要有其他 tools/开发进程访问数据库。env 必须与备份一致，不一致时先由运维核对并恢复对应配置。

```bash
bash .agent/run.sh compose --env-file runtime/production.env stop web
bash .agent/run.sh compose --env-file runtime/production.env run --rm --no-deps -T tools python .agent/sqlite-backup.py restore runtime/backups/pre-deploy --env-file runtime/production.env
bash .agent/run.sh deploy --production --no-build
```

Git 缓存可重建，也可在应用停止后单独保存卷以缩短恢复时间。回退程序版本前确认 schema 兼容，必要时同时恢复对应数据库和密钥。不要执行 `down -v`，它会删除数据卷。

## 9. 验收与容量边界

部署完成后确认所有常驻服务健康、`/health` 返回成功、匿名项目读取成功、管理员 HTTPS 登录成功，再用公司真实 token / CA 检查连接并导入一个实际仓库。

源码、安装包、真实 PostgreSQL / Redis / RQ 和 Chromium 检查见[开发指南](development.md#7-测试与验证)。默认上限：单仓库 10,000 分支，Git 输出 4 MiB，普通 Git 命令 30 秒，clone / fetch 120 秒，任务最多 5 次尝试，provider 最多 1000 页。工作台历史 100 条、文件 200 项；分支独有提交页面最多 50 条。

当前示例和规模样本只有三个提交，不能作为真实大仓库容量承诺。公司平台版本、网络、token、CA、长期运行和反向代理需在实际环境验收；SSO、webhook、SSH Git 与无限历史浏览未实现。
