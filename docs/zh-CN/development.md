# 开发指南

[文档目录](../README.md) · [日本語](../ja/development.md) · [English](../en/development.md)

## 发布管理实现

`app/releases.py` 负责本番摘要和 MR 证据核验，`app/routes/releases.py` 提供公开 GET 与管理员/CSRF 保护的 POST。迁移 `0003` 新增 `git_tags`、`release_sources`、`release_plans`、`release_items`、`deployments`。上线历史只追加；当前本番取每个项目/仓库最新记录 ID。计划版本号防止重复/过期写入；项目写锁串行化本番记录。

Git 同步原子发布 branch/tag 快照，失败保留旧快照。可选本地 MR 来源独立获取平台记录，绑定或凭据变化使在途结果失效。仅新鲜、完整且与固定 SHA/具体 MR 匹配的证据确认合并，不根据分支名称猜测。完成状态由上线和合入两项计算；MR 核验不写本番历史。测试位于 `tests/test_releases.py`、`tests/test_release_tags.py`、`tests/test_releases_browser.py`。

## 1. 技术栈与目录

Python 3.13，FastAPI，SQLAlchemy / Alembic，Jinja2，CSS / JavaScript 与 vendored jQuery；默认 SQLite，无需 Redis。前端无需 Node 构建。

| 路径 | 用途 |
|---|---|
| `app/main.py`, `config.py`, `db.py`, `models.py` | 应用工厂、配置、数据库、模型 |
| `app/routes/` | 登录、公开读取、管理员操作等 HTTP 路由 |
| `app/providers/` | GitHub / GitLab API 适配、HTTPS 与地址校验 |
| `app/git_service.py`, `git_workspace.py` | 本地 Git 执行、提交履历及文件详情 |
| `app/sync.py`, `jobs.py`, `scheduler.py`, `worker.py`, `server.py` | 快照发布、数据库任务队列、定时调度、子进程与单容器启动 |
| `app/relations.py`, `project_overview.py` | 分支证据、项目批量总览 |
| `app/templates/`, `app/static/`, `app/locales/` | 页面、静态资源、三语消息目录 |
| `alembic/versions/` | 数据库迁移 |
| `tests/` | 单元、集成、规模和 Chromium 回归测试 |
| `.agent/` | Docker runner、部署、示例、安装包检查与测试镜像 |
| `runtime/` | 忽略提交的环境、CA、访问资料、截图和验证结果 |

开始修改前阅读根目录 [AGENTS.md](../../AGENTS.md)。

## 2. 执行规则与环境准备

宿主机只做源码阅读、搜索、编辑及只读 Git 检查。Python、pip、pytest、迁移、服务器、格式化及 Git 写操作均使用 `bash .agent/run.sh ...`。不要直接执行 Docker Compose 或项目代码。

最快获得完整依赖和服务：

```bash
bash .agent/run.sh deploy
```

此命令不需要 `.venv`。需要独立开发实例时，首次指定独立配置、项目名和端口：

```bash
bash .agent/run.sh deploy --env-file runtime/development.env --project-name gev-development --port 8770
```

之后所有 Compose 命令换成 `--env-file runtime/development.env`。不要针对生产配置运行开发工具或示例。

也可创建仅供容器使用的虚拟环境，便于快速跑源码测试：

```bash
bash .agent/run.sh python -m venv .venv
bash .agent/run.sh .venv/bin/pip install -r requirements.lock
bash .agent/run.sh .venv/bin/pip install -e . --no-deps
```

该 `.venv` 位于挂载目录，包含 Linux 容器程序；不要在 macOS 宿主直接执行。依赖版本以 `requirements.lock` 为准，不随意升级 Python。

## 3. 编辑与本地热重载

默认 `web` 从安装好的镜像运行；修改宿主源码后需要再次 `deploy` 才更新它。快速页面调试可启动挂载源码的 `tools` 容器，使用不同端口：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps -p 127.0.0.1:8768:8000 tools uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

访问 [http://127.0.0.1:8768](http://127.0.0.1:8768)，结束用 Ctrl+C。此实例复用 local 数据库与缓存；需要隔离时使用上一节的 development 配置。`--reload` 检测 Python 文件，浏览器刷新后读取修改过的模板和静态资源。后台 worker 仍使用已构建代码，改动同步逻辑后重新部署对应开发实例。

需要 shell 时也进入容器：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools bash
```

## 4. 架构与必须保持的行为

`app.server` 执行迁移后启动 Web 和 `app.worker`。worker 定期查找到期仓库，将任务写入 SQLite，串行启动独立任务子进程；子进程持有仓库锁，抓取 Git/API 并按固定 OID 发布快照。数据库是任务事实来源，重启后恢复中断任务；无需 Redis。SQLite 使用 WAL 和 30 秒锁等待，保持短事务。任务超时会终止子进程，最多尝试 5 次。不要把网络请求放进持有写锁的事务。

Git 与 API 更新时间独立。分支比较基于不可变 OID 和规则版本缓存，不能在分析过程中混用移动 ref。来源候选、人工声明、计划目标、当前可达性和历史 PR/MR 应分别呈现。

公开 GET 使用 `optional_user`。项目读取公开；非管理员仓库访问必须确认仍关联项目。管理员配置及新同步／比较请求必须 `require_admin`，POST 保持 CSRF 校验。增加 HTML 片段、JSON 或提交 URL 时同样检查访问范围。

公开 GET 可以读取数据库和本地 Git 对象，但不应触发远端抓取或平台写入。管理员的连接检查与仓库发现按既有流程访问平台。保留 TLS 验证、远端 HTTPS 地址校验、输出和时间限制、缓存锁，以及日志中的凭据脱敏。

前端异步更新需要处理旧请求和取消回调，防止慢响应覆盖新筛选结果。URL 应恢复分支、提交、筛选、分页与密度状态。

本地来源由 `app/providers/local.py` 实现，复用连接、导入和同步模型，无需新增数据库表。宿主 `GEV_LOCAL_GIT_DIRECTORY` 只用于部署映射，服务内由 `GEV_LOCAL_GIT_ROOT`（默认 `/data/local-git`）限制访问范围。发现和导入均校验真实路径及 Git 元数据；Git 缓存复制仅允许 file 协议，保持原目录只读。API 时间为空且状态“不适用”，不能据此把本地仓库判为待同步。回归覆盖见 `tests/test_local_repositories.py`；三语目录选择、重试及批量导入的浏览器覆盖见 `tests/test_admin_experience_browser.py`。

## 5. 风格、i18n 与依赖

Python 使用四空格缩进、`snake_case` 函数／模块、`PascalCase` 类及服务接口类型注解。HTML / JavaScript 匹配现有两空格风格；provider 逻辑放在 provider 层。没有统一 formatter / linter 配置。

三语消息位于 `app/locales/messages.json`：以中文消息为 key，补齐 `ja` 和 `en`；动态内容使用命名占位符，例如 `{name}`。模板通过 `t(...)`，服务错误可用 `Message`。保持 HTML 转义；仓库名、提交内容等用户数据不要翻译。新增文案后运行 i18n 测试，检查三语页面布局。

依赖调整需同步 `pyproject.toml`、`requirements.lock`，并在 Python 3.13 Docker 中验证。`Dockerfile` 安装锁定依赖和 wheel；新增模板、静态资源或消息文件时检查包资源声明，避免只在源码挂载时可用。

## 6. 数据库迁移

修改持久化模型时新增 Alembic migration。对已经部署的 local 开发库生成并检查差异：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools alembic revision --autogenerate -m "describe schema change"
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps migrate
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools alembic check
```

审查自动生成结果、数据迁移、约束及 downgrade。`init-db` 只供空的非生产开发库创建表，不能替代生产迁移。已有用户数据的数据库不要做 downgrade 演练；使用一次性测试数据库。

## 7. 测试与验证

pytest 文件和函数命名 `test_*.py` / `test_*`，复用 `tests/conftest.py`。没有数值覆盖率门槛；对权限、快照一致性、恢复行为或交互变化增加有意义的回归测试。

默认测试可直接使用部署镜像中的依赖和源码挂载：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python .agent/test-unit.py -q
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python .agent/test-unit.py tests/test_deploy.py tests/test_i18n.py -q
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python -I .agent/check-installed.py
bash .agent/run.sh python .agent/check-docs.py
```

`test-unit.py` 清除继承的 `GEV_*` 部署设置，避免它们影响独立单元测试。若已创建 `.venv`，也可运行 `bash .agent/run.sh .venv/bin/pytest -q`。安装包检查使用 `-I`，验证已安装 wheel 的资源；源码测试不能代替它。文档检查验证本地链接、标题、三语 shell 命令一致性及 shell 语法。

以下可选套件以 **local** 配置运行，先构建默认应用镜像（`deploy`）及测试镜像：

```bash
bash .agent/run.sh compose --env-file runtime/local.env build integration browser
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_SCALE=1 integration python -m pytest tests/test_scale.py -q -s
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_E2E=1 browser python -m pytest tests/test_e2e.py -q
```

默认测试覆盖 SQLite 的真实后台子进程和迁移复制。旧 PostgreSQL/RQ 兼容套件 `tests/test_integration.py` 仅在另行提供有建库权限的测试 PostgreSQL URL、`GEV_JOB_BACKEND=redis` 和 `GEV_INTEGRATION=1` 时运行；不需要为日常开发启动这些服务。规模测试现在使用独立 SQLite，生成 100 个小型 bare 仓库（各 55 refs、三个提交），不能外推大历史性能。

完整 E2E 包含 `test_compose_public_demo_screenshots`，依赖明确初始化的 local 示例（3 仓库 × 55 分支），且 `runtime/demo-access.json` 管理员资料须匹配。新空实例只跑隔离 fixture 用例时追加 `-k 'not compose_public_demo_screenshots'`，不会要求 seed 已有库。截图在 `runtime/screenshots/`，规模输出在 `runtime/verification/`。

## 8. 提交与 PR

Git 历史使用祈使句摘要，常见 `feat:`、`fix:`、`docs:`，也存在不加前缀的提交。保持单个提交主题集中。

PR 说明写明问题、行为变化、关联 issue、验证命令与结果；UI 变化提供截图，配置和 schema 变化说明迁移及运维影响。功能／命令变更同步维护中文、日语、英语文档。不要提交 `runtime/`、`.env`、数据库、缓存、账号资料和截图中的秘密。

Git 写操作也在 Docker 内，例如：

```bash
bash .agent/run.sh git -c safe.directory=/workspace switch -c codex/describe-change
bash .agent/run.sh git -c safe.directory=/workspace add app tests docs
bash .agent/run.sh git -c safe.directory=/workspace commit -m "docs: describe deployment workflow"
```

只读的 `git status`、`git diff`、`git log` 可在宿主使用。提交前检查差异中没有敏感资料，并针对本次改动运行必要验证。
