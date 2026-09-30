# 部署验收记录 · 2026-09-29

> 此记录描述当时的版本，成员限制等访问规则已被后续功能替代。当前用户、开发与部署操作请使用 [三语文档目录](README.md)；项目关联仓库现支持匿名只读访问。

所有执行、安装、构建、服务和浏览器操作均经 `bash .agent/run.sh` 进入 Docker。运行环境：Python 3.13.15 / Linux aarch64，PostgreSQL 17，Redis 7.4，RQ 2.12.0；Playwright 1.63.0 / Chromium 153.0.8010.12。最终独立需求与代码质量审查均已通过，实现提交为 `1b53eee`。

## 已执行检查

| 检查 | 命令（前缀均为 `bash .agent/run.sh`） | 结果 |
|---|---|---|
| 全量源码测试 | `.venv/bin/pytest -q` | 144 passed，6 个 opt-in 测试 skipped，32.74s |
| 真实 PostgreSQL / Redis / RQ | `compose --env-file runtime/local.env run --rm -e GEV_INTEGRATION=1 integration python -m pytest tests/test_integration.py -q -s` | 2 passed，6.93s |
| 100 仓库样本 | `compose --env-file runtime/local.env run --rm -e GEV_SCALE=1 integration python -m pytest tests/test_scale.py -q -s` | 1 passed，14.91s |
| Chromium 浏览器 | `compose --env-file runtime/local.env run --rm -e GEV_E2E=1 browser python -m pytest tests/test_e2e.py -q` | 3 passed，7.62s |
| 安装包资源 | `compose --env-file runtime/local.env run --rm tools python -I .agent/check-installed.py` | PASS，隔离源码目录后，已安装 wheel 的登录模板、DB health、CSS、JS、jQuery 和许可证 HTTP 200 |
| 镜像/服务 | `compose --env-file runtime/local.env up -d --build`、`ps` | Web、PostgreSQL、Redis、dispatcher、2 workers 启动且健康，migrate 成功退出 |
| 部署约束 | 容器内 Settings 生产校验、已部署镜像文件检查 | 生产 secret / Secure cookie 校验通过；镜像无 `.env`、runtime、tests、`.git` |
| 交互终端 | 普通 runner、Compose tools 容器内 `sys.stdin.isatty()/stdout.isatty()` | 两条路径均为 True/True；非 TTY 自动化继续可用 |

所有 pytest 运行有一个上游 Starlette/httpx 弃用警告，不影响通过。未为此变更依赖范围。

最终需求复核独立运行真实服务、demo 与 health 检查，7 passed（6.34s）；最终质量审查独立运行身份、权限与就绪检查，26 passed（5.99s），未发现待修问题。父任务另行验证已安装 wheel 的页面资源与数据库就绪检查通过，并完成截图视觉检查。功能分支 `feat/team-dashboard` 和本地演示服务保留供使用。

## 真实服务覆盖

- PostgreSQL 在 UUID 命名的一次性数据库中完成 migrations up → down → up，`alembic check` 无新增操作。测试库清理后未影响演示数据库。
- 实际停止 Redis 7.4 进程后创建比较任务，数据库 outbox 保留 pending；此时通过真实 PostgreSQL 的 HTTP 登录、项目页和 `/health` 均成功。恢复 Redis 后 dispatcher 重新投递。
- 从真实 Redis 中只移除队列项、保留 job hash，dispatcher 检测到丢失投递并恢复。
- 真正的 RQ fork workhorse 在比较时收到 SIGKILL；数据库留下 running 状态，dispatcher 在锁释放后恢复，下一次真实 worker 执行成功，attempts=2。
- Redis 进程重启并丢失全部测试实例内存后，数据库中的任务重新入队；两个真实 RQ worker 的比较执行时间区间重叠，均完成。
- 这些测试使用独立 Redis 端口/临时目录，未 reset/flush 演示 Redis。

## 规模样本（不可外推真实大仓库容量）

最终源码运行记录在 `runtime/verification/scale.json`：

| 项目 | 样本结果 |
|---|---:|
| 实际 bare 仓库 | 100 |
| 每仓库 refs / 总 refs | 55 / 5500 |
| 每仓库不同提交 OID | 3 |
| 初始生成、Git 分析与 PostgreSQL 发布 | 11.787s |
| 重复抓取与分析 | 2.789s |
| 首次 / 重复 Git 比较次数 | 400 / 0 |
| 持久化比较行 | 500 |
| cache 文件总字节数 | 3,655,700 |

样本包含真实的一致、包含、分叉历史，但仅三个空树提交。时间同时受 Docker Desktop、本机存储和当时其他测试影响；不是基准 SLA，也不证明大仓库克隆速度或生产容量。先前一次同类运行 14.678s / 1.860s 也说明本地时间会浮动，最终表使用当前源码重跑数据。

## 浏览器路径与修复

覆盖登录、成员看不到隐藏项目、直接 URL 返回 404 / 管理页 403、仓库展开、分支分页、筛选、刷新与分享 URL 恢复、分支来源/计划/历史 PR 证据、管理员创建项目、fixture 平台连接检查与仓库导入、仓库配置保存。测试 fixture provider 只注入隔离 Web；部署镜像没有测试入口。

实际复现“第 2 页改筛选后保留旧分页，导致只有 1 条结果却显示空表”，先获得失败 Chromium 断言，再修复筛选提交重置分支页码；直接刷新/分享仍恢复 URL。延迟的旧 AJAX 回调在 Chromium 中不能覆盖新响应。

另外通过先失败后修复的回归检查确保：demo 从第一事务即暂停连接、不留下可调度任务（中断也不会访问虚构主机）；DB 就绪端点反映数据库故障并隐藏细节；Settings repr 不输出数据库/Redis URL 或密钥。安装包检查暴露模板/static 未随 wheel 打包，补齐资源配置并以 `python -I` 独立验证。仅演示实例的数据库密码已同步轮换，其他密钥保留。

截图均为忽略提交的本地产物，同时保存 full-page 与 `-viewport.png`（1440×1000）：

- `runtime/screenshots/01-member-projects-viewport.png`
- `runtime/screenshots/02-project-expanded-viewport.png`
- `runtime/screenshots/03-branch-evidence.png`
- `runtime/screenshots/04-member-denied-viewport.png`
- `runtime/screenshots/05-admin-viewport.png`
- `runtime/screenshots/06-fixture-branch.png`
- `runtime/screenshots/07-fixture-import-viewport.png`
- `runtime/screenshots/08-fixture-config-viewport.png`

父任务已查看项目、分支证据及管理页，确认中文字体、布局与证据可读。

## 当前访问与未完成的外部验证

演示保留运行：[http://127.0.0.1:8766](http://127.0.0.1:8766)。账号资料为本地权限 0600 的 `runtime/demo-access.json`，源码和文档不含密码。原有 8765 服务未改动。生产示例配置采用独立 `COMPOSE_PROJECT_NAME=git-easy-view-production` 与 8767 端口，避免复用演示数据卷。

仍需公司实际 GitHub Enterprise / 自建 GitLab token、CA、网络和版本验证。公共 API 烟雾检查与虚构 fixture 不能代替这一步。备份/恢复步骤已文档化，但没有在现有演示库执行破坏性恢复；Git 大历史容量、长期运行及公司 HTTPS 代理配置需按真实环境演练。worker 被强杀后遗留 Git 锁的运维边界见 README。
