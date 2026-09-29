# Git Easy View

面向团队的 Git 仓库与分支看板。项目成员只能读取所属项目；管理员配置 GitHub / GitHub Enterprise、GitLab / 自建 GitLab 连接、导入仓库、管理成员和分析规则。Python 3.13、FastAPI/Jinja/jQuery、PostgreSQL、Redis/RQ 与原生 Git；不需要 Node 构建。

## 本地启动（仅 Docker 执行）

宿主机只用于阅读和编辑源码。安装并启动 Docker Desktop 后，**所有项目命令均从仓库目录经 `bash .agent/run.sh ...` 执行**。普通命令运行在 `python:3.13-bookworm`；`compose` 子命令运行在 `docker:28-cli` 容器，挂载 Docker socket 和相同绝对路径，确保 Compose 的构建上下文与挂载正确。这个入口具有 Docker 管理权限，仅运行可信命令。不要直接在宿主机运行 Python、pip、pytest、Git 写操作或 Docker Compose。

```bash
bash .agent/run.sh python -m venv .venv
bash .agent/run.sh .venv/bin/pip install -r requirements.lock
bash .agent/run.sh .venv/bin/pip install -e . --no-deps
bash .agent/run.sh .venv/bin/python .agent/setup-local.py
bash .agent/run.sh compose --env-file runtime/local.env config --quiet
bash .agent/run.sh compose --env-file runtime/local.env up -d --build
bash .agent/run.sh compose --env-file runtime/local.env ps
```

首次生成 `runtime/local.env` 和 `runtime/demo-access.json`，权限 0600，不覆盖已有文件且已忽略提交。Web、workers、dispatcher 共用稳定的会话密钥、Fernet 密钥和缓存目录。不要依赖开发模式每个进程独立生成的临时密钥。默认访问 [http://127.0.0.1:8766](http://127.0.0.1:8766)，修改 env 文件中的 `GEV_PORT` 可避开端口占用。仅 Web 发布到 **127.0.0.1**；PostgreSQL 和 Redis 不发布宿主端口。

Compose 启动 PostgreSQL 17、Redis 7.4、两个 RQ worker（含 `--with-scheduler`）及一个 outbox dispatcher。数据库迁移是独立的 `migrate` 一次性服务；worker 不执行迁移。数据库、Redis、Git cache 使用三个独立持久卷。Web `/health` 检查数据库，不依赖 Redis，因此 Redis 故障期间仍可登录、读缓存；新任务持久保存在数据库，恢复后补排。查看日志时注意访问路径和用户信息，不要分享完整环境变量。

## 显式示例与账号

**安装和启动不会创建任何账号或示例数据。** 本地演示必须手动执行，数据库必须为空，且 `GEV_PRODUCTION=false`：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm tools python .agent/seed-local.py
```

此命令使用初始化时随机生成的两组密码，访问资料保存在 `runtime/demo-access.json`（`demo-admin` 与 `demo-member`）。成员只可访问“示例 · 产品研发”，不能读取“示例 · 管理员专属项目”。源码里没有默认密码；不要把访问文件提交或对外分享。

也可通过交互式 CLI 自选密码（两个提示各输入一行）：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm tools python -m app.cli demo
```

CLI 支持 `--password-env` 和 `--member-password-env` 指定一次性环境变量名。示例有三个仓库，每仓库 55 个分支，真实生成三个不同 Git 提交，包含一致、已包含、分叉的关系；PR 记录明确标为虚构。示例连接从首次保存即暂停，初始化任务不会交给外部 provider，即使命令意外退出也不会访问虚构平台。若初始化中断，保留部分结果并拒绝再次覆盖；请使用新的独立空数据库重试。管理员不要对虚构连接执行“检查连接”；本地比较不需要启用平台。

实际部署创建管理员，密码由提示读取，不保留在 Web 环境变量：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm tools python -m app.cli create-user --username your-admin --admin
```

runner 在交互终端保留 TTY，密码提示不回显。自动化场景可从受控 secret 管理器通过 `--password-env` 指定的一次性变量传入。不要把密码放进命令参数、源码或常驻服务环境。

## 生产配置、令牌与公司 CA

`.env.example` 是配置字段清单，不是可运行凭据。为生产创建受控的 `runtime/production.env`，使用独立随机数据库密码、至少 32 字符随机 `GEV_SESSION_SECRET` 和有效 Fernet `GEV_CREDENTIAL_KEY`；设置 `COMPOSE_PROJECT_NAME=git-easy-view-production`、`GEV_PORT=8767`、`GEV_ENV_FILE=./runtime/production.env`、`GEV_PRODUCTION=true`、`GEV_COOKIE_SECURE=true`、PostgreSQL URL。后续所有 Compose 命令改为 `--env-file runtime/production.env`。

**仅更换 env 文件不会隔离 Compose 数据卷。** 生产必须使用上述不同项目名及端口，以创建独立空数据库、Redis 与 Git cache 卷，再创建正式管理员；不能沿用示例账号。生产校验拒绝 SQLite、缺少密钥和非 Secure cookie。`POSTGRES_PASSWORD` 仅在首次初始化数据库卷时设置，已有卷改密须先在数据库中完成协调轮换。

放在公司 HTTPS 反向代理之后，代理连接本机绑定端口；公网 HTTPS 下才能使用 Secure cookie。开发演示的 HTTP 和 `production=false` 不适合作为生产配置。限制谁能访问 Docker socket、环境文件、数据库和 Redis 网络；RQ 使用可信内部 pickle 队列，不能向不可信客户端开放。

管理页先创建用户与项目，关联成员，然后添加平台连接：

- GitHub 优先使用限定仓库的 fine-grained PAT：Metadata read、Contents read（HTTPS Git）和 Pull requests read。当前仓库发现使用 `/user/repos`，支持 fine-grained PAT / user token，不支持 installation token。按组织政策审批令牌。Classic `repo` scope 权限更广，不能称为只读凭据，虽然本应用只执行读取。[仓库列表权限](https://docs.github.com/en/rest/repos/repos#list-repositories-for-the-authenticated-user)、[PR 权限](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests)、[PAT 管理](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)。
- GitLab 使用 `read_api` 获取 API 数据，加 `read_repository` 获取 HTTPS Git；可按范围选择 personal/group/project token，受平台版本和实例策略限制。[GitLab 官方 scope 说明](https://docs.gitlab.com/security/tokens/access_token_scopes/)。
- GitHub Enterprise 填 HTTPS 实例根地址（可识别 `/api/v3`），自建 GitLab 填实例根地址（可识别 `/api/v4`）。只使用 HTTPS + token；不关闭 TLS 校验，不支持 SSH。
- 公司 CA 放到 `runtime/ca/company-ca.pem`，连接表单填容器路径 `/etc/gev/ca/company-ca.pem`。Compose 将目录只读挂载到 Web、两个 worker、dispatcher、migration 与管理工具；证书更新后按公司流程重启相关服务。令牌加密保存，轮换 Fernet key 前必须迁移已有密文，丢失 key 无法解密旧令牌。

保存连接后“检查身份与平台能力”，成功后选择仓库、导入到项目（一次最多 50 个）。API 返回的仓库身份与地址会在导入提交时复核。首次导入异步获取 Git 与 API，页面显示进度；同一仓库可关联多个项目，缓存和分析复用。凭据修改会暂停连接，重新检查后恢复同步。

默认每 5 分钟同步，两个 worker。API 获取全部开放 PR/MR，加最近 90 天更新的关闭/合并记录；管理员可在仓库配置中填历史起始日期做补录。同步间隔、角色前缀、手动来源、计划目标和主比较对象可配置，活跃发布分支初始为空，需要管理员明确启用。页面“刷新缓存视图”只重新读取已有数据；管理员“立即同步”才发起远端同步。Git/API 新鲜度分别显示，过期阈值为同步间隔的两倍且不少于 60 秒。

## 如何解读证据

“来源”是基于当前提交图的候选或管理员声明，不是 Git 保存的真实创建来源。“计划目标”来自开放 PR/MR 或人工配置，不表示已合入。“当前提交关系”使用固定 OID 与明确比较对象判断可达性。历史已合并 PR/MR 是平台历史记录，可能与当前 Git 可达性不同：分支继续前进、force push、squash/rebase merge 或目标历史变更都可能导致差异。找不到历史记录也不能证明从未合并；页面注明覆盖窗口。应用不声称识别 squash、cherry-pick、revert 的内容等价性，不执行远端写操作。

## 备份与恢复

至少同时保存 **PostgreSQL 备份与对应的 Fernet key**；会话 secret、部署配置、CA 也应受控备份。Git cache 可重建，备份可缩短恢复时间；Redis 是可丢弃投递层，数据库 outbox 才是任务事实来源。以下命令不在宿主执行重定向：

```bash
bash .agent/run.sh python -c 'from pathlib import Path; Path("runtime/backups").mkdir(parents=True, exist_ok=True)'
bash .agent/run.sh compose --env-file runtime/local.env exec -T postgres pg_dump -U gev -d gev -Fc -f /tmp/gev.dump
bash .agent/run.sh compose --env-file runtime/local.env cp postgres:/tmp/gev.dump /workspace/runtime/backups/gev.dump
bash .agent/run.sh python -c 'import os,shutil; os.umask(0o077); shutil.copyfile("runtime/local.env", "runtime/backups/deployment.env"); os.chmod("runtime/backups/deployment.env", 0o600)'
```

使用公司加密备份系统将这两份资料移出本机，并定期演练恢复。恢复前停止所有写入者，确认目标为待恢复数据库；不要对正在使用的数据库尝试恢复：

```bash
bash .agent/run.sh compose --env-file runtime/local.env stop web worker dispatcher
bash .agent/run.sh compose --env-file runtime/local.env cp /workspace/runtime/backups/gev.dump postgres:/tmp/restore.dump
bash .agent/run.sh compose --env-file runtime/local.env exec -T postgres pg_restore -U gev -d gev --clean --if-exists --exit-on-error /tmp/restore.dump
bash .agent/run.sh compose --env-file runtime/local.env run --rm migrate
bash .agent/run.sh compose --env-file runtime/local.env up -d web worker dispatcher
```

先恢复对应密钥配置再启动服务；避免用新生成的 key 解密旧数据库。不要执行 `down -v`，它会删除持久卷。普通停止使用 `stop`，重启使用 `up -d`。

## 测试与边界

```bash
bash .agent/run.sh .venv/bin/pytest -q
bash .agent/run.sh compose --env-file runtime/local.env run --rm tools python -I .agent/check-installed.py
bash .agent/run.sh compose --env-file runtime/local.env build integration browser
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_INTEGRATION=1 integration python -m pytest tests/test_integration.py -q -s
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_SCALE=1 integration python -m pytest tests/test_scale.py -q -s
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_E2E=1 browser python -m pytest tests/test_e2e.py -q
```

`python -I` 单独检查安装后的 wheel，确保模板、静态资源和 jQuery 许可证随包发布；其余 `python -m pytest` 检查当前源码。集成测试创建 UUID 命名的一次性 PostgreSQL 数据库（因此只对测试实例使用有建库权限的连接），测试结束删除它；Redis 7.4 子进程使用独立临时端口和目录，不清空运行中的 Redis。浏览器测试需要上面的 Compose 示例账号，并另起隔离 fixture Web 做管理员导入测试；所有浏览器执行在 Docker Chromium 中。截图在 `runtime/screenshots`，规模结果在 `runtime/verification/scale.json`。

验收包含真实 PostgreSQL migrations up/down/up + Alembic check；真实 Redis 停止/重启、丢队列项重排、RQ workhorse SIGKILL 后恢复、两个 worker 同时执行；100 个真实小型 bare 仓库 × 55 refs 的首次分析与缓存复用。它们只有三个生成的提交，不能外推大型仓库的克隆时间、容量或生产并发，需在公司的真实仓库和凭据下重新测量。

默认每页 50 行；单仓库最多 10,000 分支；Git 输出限制 4 MiB，普通 Git 命令 30 秒、克隆/抓取 120 秒；后台任务默认 3600 秒、最多 5 次尝试；provider 每次最多 1000 页。提交明细页面最多 50 条（底层接口上限 500）。大历史和慢网络可能触发这些上限；先用真实样本评估再调整。Git cache 与锁目录必须由 Web、worker、dispatcher 共享。强杀 worker 后仍存活的 Git 子进程会继续持有锁；如其卡死，先由运维终止相关进程或重启对应 worker 容器，**不要在进程活着时删除缓存或锁文件**。数据库外的缓存/连接手工改动不受应用保护。

尚需公司 GitHub Enterprise / 自建 GitLab 的实际 token、版本、CA 和网络验证。已验证公共 API 和本地 fixture 不等于完成公司平台接入。SSO、webhook、SSH、目录扫描器和完整提交图浏览不在当前范围。
