# Git Easy View

面向团队的 Git 仓库与分支看板。项目及其关联仓库开放匿名只读访问，无需账号或成员授权；管理员登录后配置 GitHub / GitHub Enterprise、GitLab / 自建 GitLab 连接、导入仓库和管理分析规则。Python 3.13、FastAPI/Jinja/jQuery、SQLite 数据库任务队列与原生 Git；默认仅一个应用容器，无需 PostgreSQL、Redis 或 Node 构建。

配置页面、令牌、用户管理、立即同步与本地分析请求均仅限管理员，写请求继续验证 CSRF。未关联任何项目的仓库只向管理员开放。项目名称和成员名单不控制公开查看权限；导入或关联到项目后，其缓存的分支、提交和 PR/MR 记录即可匿名查看。

## 文档 / ドキュメント / Documentation

| 语言 | 用户说明 | 开发说明 | Docker、自动部署与运维 |
|---|---|---|---|
| 简体中文 | [用户指南](docs/zh-CN/user-guide.md) | [开发指南](docs/zh-CN/development.md) | [部署指南](docs/zh-CN/docker-deployment.md) |
| 日本語 | [ユーザーガイド](docs/ja/user-guide.md) | [開発ガイド](docs/ja/development.md) | [デプロイ・運用](docs/ja/docker-deployment.md) |
| English | [User guide](docs/en/user-guide.md) | [Development guide](docs/en/development.md) | [Deployment and operations](docs/en/docker-deployment.md) |

完整三语文档入口：[docs/README.md](docs/README.md)。以下为中文功能与操作摘要。

需要 HTTP/HTTPS 出站代理和公司 CA 的内网，请使用独立的 [deploy/ 部署指南](deploy/README.md)（[日本語](deploy/README.ja.md) / [English](deploy/README.en.md)）：先运行 `bash .agent/run.sh deploy-intranet --init`，编辑网络配置并放入 CA，再运行 `bash .agent/run.sh deploy-intranet`。原个人电脑部署入口保持兼容。

## 一条命令快速部署

Docker 运行后，从仓库根目录执行，无需宿主 Python、Node 或 `.venv`：

```bash
bash .agent/run.sh deploy
```

首次生成稳定随机配置、构建应用、执行迁移并等待全部常驻服务健康；重复运行保留密钥、账号和持久卷。不自动创建账号或示例数据。源码不变且已有正确镜像时可用 `bash .agent/run.sh deploy --no-build`；参数见 `bash .agent/run.sh deploy --help`。

生产后端使用 `bash .agent/run.sh deploy --production --wait-timeout 300`，生成独立 env、项目名、镜像标签与数据卷，并启用 Secure cookie。浏览器需要另行配置 HTTPS 代理。SSH / CI、首次管理员、备份恢复与失败处理见上述部署指南；更新会短暂停止应用，迁移失败会保持停止并返回非零退出码。

## 界面语言 / 表示言語 / Interface languages

支持简体中文（`zh-CN`）、日本語（`ja`）和 English（`en`）。语言选择器仅显示在登录页，手机布局也可使用；选择保存在当前浏览器中，登录、退出和刷新后仍有效，登录后的全部页面沿用这一设置。首次访问按浏览器的 `Accept-Language` 选择，未匹配时使用简体中文。也可使用 `?lang=ja`、`?lang=en` 或 `?lang=zh-CN`；优先顺序为 URL、已保存选择、浏览器语言、默认语言。

页面、管理员表单、状态、错误和异步通知使用同一套翻译。项目名称、分支名、提交内容和平台原始记录保留原文；所有时间仍显示 UTC。切换语言时在登录页选择，随后可以匿名浏览或登录管理。

翻译集中在 `app/locales/messages.json`，中文原文作为消息键，日语／英语作为对应值；动态内容使用 `{page}` 等具名参数，通过模板的 `t(...)` 插入并转义。新增文案时同时补齐两种翻译，避免直接拼接句子。`tests/test_i18n.py` 检查翻译完整性、参数一致性、语言隔离和设置保存；浏览器测试覆盖三种语言及手机布局。

## 项目分支总览

`/projects/{id}` 默认展开每个仓库的所有当前分支，按主干、release、feature 和其他角色分组。每条分支直接显示提交时间、短 hash、首行 message，以及相对默认主干的包含关系和领先／落后数量；点击 hash 进入 Git 工作台，点击分支名查看关系证据。优先使用平台声明的默认分支，未知时依次选择 `main`、`master`。

每页 50 个仓库；项目页顶部提供仓库快速定位。分支按最近提交排序，release 优先显示标记为活跃的分支，所有角色均无额外条数截断。简略关系图汇总相同、已包含、有独有提交、无共同历史和未分析的数量。“紧凑视图”保留全部分支并使用独立滚动列表；显示方式保存在 URL，筛选和刷新后保留。默认主干始终保留作比较上下文，删除分支不进入总览。“详细关系表”按需加载计划目标等证据。

关系使用当前固定 OID 的缓存结果；提交摘要按仓库批量读取本地提交对象，不遍历历史或联系远端，且按不可变 OID 缓存。同步详情与提交时间分别展示，过期、暂停、读取繁忙或缺少分析时均明确提示，不把历史 PR/MR 的合并记录当成当前包含关系。

## Git 工作台

`/repositories/{id}/branches` 是完整页面，参考 IntelliJ IDEA Git Log 的三栏布局：左侧搜索与分组分支，中央显示真实提交拓扑图，右侧查看提交详情、变更文件及分支关系。选中分支后过滤历史，选中提交后更新详情；选择、筛选和分页保留在 URL，支持刷新和返回。

历史只读取项目关联仓库的本地快照，最多 100 个提交和 200 个变更文件；合并提交的文件变更与第一个父提交比较。同步状态可展开查看，缓存读取不会触发远端同步。工作台不执行 checkout、merge、push 或删除分支。示例历史的三个提交为空树，因此变更文件列表为空；实际仓库显示真实文件路径和增删改状态。

项目页的详细关系表按需加载，通过 `?fragment=1` 获取 HTML 片段；`/repositories/{id}/log` 供工作台异步更新履历与详情。两种请求均检查仓库当前仍关联项目；管理员可读取未关联仓库。

## 本地启动（仅 Docker 执行）

默认是 **SQLite + 数据库任务队列，一个常驻应用容器**。不需要 PostgreSQL 或 Redis。网页、定时同步和串行后台任务在同一容器运行；SQLite 与 Git 缓存使用持久卷保存。

```bash
bash .agent/run.sh deploy
```

访问 [http://127.0.0.1:8766](http://127.0.0.1:8766)。配置生成在 `runtime/local.env`，重部署保留账号、数据和密钥。已有 PostgreSQL 实例首次切换执行：

```bash
bash .agent/run.sh deploy --migrate-sqlite
```

复制并校验所有表后才切换配置；原 PostgreSQL 卷和 `.postgres-backup` 配置保留，旧服务停止。后续使用普通 `deploy`，镜像没有变化时可加 `--no-build`。所有项目执行通过 Docker runner，宿主仅用于读源码和编辑文件。

## 显式示例与账号

**安装和启动不会创建任何账号或示例数据。** 本地演示必须手动执行，数据库必须为空，且 `GEV_PRODUCTION=false`：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm tools python .agent/seed-local.py
```

此命令使用初始化时随机生成的两组密码，访问资料保存在 `runtime/demo-access.json`（`demo-admin` 与 `demo-member`）。示例项目均可匿名查看；名称中的“管理员专属”是旧示例名称，不再代表读取限制。仅管理员账号可以修改配置。源码里没有默认密码；不要把访问文件提交或对外分享。

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

```bash
bash .agent/run.sh deploy --production --wait-timeout 300
```

生成独立项目与 `runtime/production.env`，默认端口 8767；SQLite 数据卷与本地演示隔离。生产通过 HTTPS 反向代理使用 Secure cookie。保留稳定的 session secret 和 Fernet key，不将 env、数据库或 token 提交到 Git。

管理员添加平台连接、检查凭据后选择仓库导入。只读页面匿名访问，token、配置与发起同步需要管理员。平台 token 权限、公司 CA、远程 SSH/CI 自动部署和无交互创建管理员见[中文部署指南](docs/zh-CN/docker-deployment.md)、[日本語](docs/ja/docker-deployment.md)、[English](docs/en/docker-deployment.md)。

默认每个仓库 5 分钟同步，后台串行处理任务，网页读取现有快照不等待同步。SQLite 采用 WAL 和 30 秒写锁等待，适合单机看板；不要将数据库放到网络文件系统或增加 web 副本。

## 如何解读证据

“来源”是基于当前提交图的候选或管理员声明，不是 Git 保存的真实创建来源。“计划目标”来自开放 PR/MR 或人工配置，不表示已合入。“当前提交关系”使用固定 OID 与明确比较对象判断可达性。历史已合并 PR/MR 是平台历史记录，可能与当前 Git 可达性不同：分支继续前进、force push、squash/rebase merge 或目标历史变更都可能导致差异。找不到历史记录也不能证明从未合并；页面注明覆盖窗口。应用不声称识别 squash、cherry-pick、revert 的内容等价性，不执行远端写操作。

## 备份与恢复

使用 SQLite backup API 在线备份，包含 WAL 中已提交的数据，同时保存对应密钥配置。每次使用新的目录名：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps -T tools python .agent/sqlite-backup.py backup runtime/backups/pre-deploy
```

恢复会覆盖当前数据库，先停止应用和额外开发进程，确认备份与 env 配套：

```bash
bash .agent/run.sh compose --env-file runtime/local.env stop web
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps -T tools python .agent/sqlite-backup.py restore runtime/backups/pre-deploy
bash .agent/run.sh deploy --no-build
```

将备份与密钥保存到受控加密存储。Git 缓存可重新获取；`down -v` 会删除数据卷，不要将它用于普通停止。

## 测试与边界

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python .agent/test-unit.py -q
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python -I .agent/check-installed.py
bash .agent/run.sh python .agent/check-docs.py
bash .agent/run.sh compose --env-file runtime/local.env build integration browser
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_SCALE=1 integration python -m pytest tests/test_scale.py -q -s
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_E2E=1 browser python -m pytest tests/test_e2e.py -q
```

默认测试覆盖权限、SQLite、无 Redis 任务执行与中断恢复、迁移复制和部署失败处理。安装包验证检查资源是否随 wheel 发布。完整浏览器测试需要已明确初始化的示例；空环境可排除 `compose_public_demo_screenshots`。旧 PostgreSQL/RQ 兼容测试需另行提供专用测试数据库，不属于默认部署。

规模测试使用 SQLite 和 100 个小型仓库，每仓库 55 refs、三个提交，不能外推真实大历史性能。默认单仓库最多 10,000 分支，普通 Git 命令 30 秒、clone/fetch 120 秒、后台任务 3600 秒、最多 5 次尝试。工作台历史最多 100 条、变更文件最多 200 项。公司平台 token、CA、网络和长期运行仍需实际验证。
