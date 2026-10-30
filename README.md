# Git Easy View

面向团队的 Git 仓库、分支关系与发布追踪看板。项目关联仓库与发布计划支持匿名只读访问；管理员登录后配置 GitHub / GitLab / 本地 Git 连接、导入仓库、管理规则及记录发布与本番上线。技术栈为 Python 3.13、FastAPI / Jinja / jQuery、SQLite 数据库任务队列与原生 Git，单容器运行，无需 PostgreSQL、Redis 或 Node 构建。

## 文档 / ドキュメント / Documentation

| 语言 | 用户说明 | 开发说明 | Docker、自动部署与运维 |
|---|---|---|---|
| 简体中文 | [用户指南](docs/zh-CN/user-guide.md) | [开发指南](docs/zh-CN/development.md) | [部署指南](docs/zh-CN/docker-deployment.md) |
| 日本語 | [ユーザーガイド](docs/ja/user-guide.md) | [開発ガイド](docs/ja/development.md) | [デプロイ・運用](docs/ja/docker-deployment.md) |
| English | [User guide](docs/en/user-guide.md) | [Development guide](docs/en/development.md) | [Deployment and operations](docs/en/docker-deployment.md) |

- 三语文档总入口：[docs/README.md](docs/README.md)
- 独立部署入口（支持内网代理/CA 与个人电脑直连）：`bash deploy/deploy.sh --init` → `bash deploy/deploy.sh`（[中文](deploy/README.md) · [日本語](deploy/README.ja.md) · [English](deploy/README.en.md)）

## 快速启动与初始账号

```bash
bash .agent/run.sh deploy
```

访问 [http://127.0.0.1:8766](http://127.0.0.1:8766)。配置生成在 `runtime/local.env`，重部署保留账号、数据和密钥。**首次部署不自动创建任何账号或示例数据**（`users` 表为空，无硬编码默认密码）：

```bash
# 方式 A：导入示例数据与演示账号（demo-admin / demo-member，随机密码保存在 runtime/demo-access.json）
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps -T tools python .agent/seed-local.py

# 方式 B：仅创建自定义管理员账号（按提示输入密码，不导入示例数据）
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python -m app.cli create-user --username admin --admin
```

生产环境使用 `bash .agent/run.sh deploy --production --wait-timeout 300`（生成独立 `runtime/production.env` 与数据卷，默认端口 `8767`，需另行配置 HTTPS 反向代理）。旧版 PostgreSQL 实例首次迁移至 SQLite 可执行 `bash .agent/run.sh deploy --migrate-sqlite`。

## 核心功能摘要

- **项目分支总览（`/projects/{id}`）**：按主干、release、feature 等角色展示本地快照分支、最近提交及相对默认主干的包含关系与领先/落后数量，支持紧凑视图与按需加载详细关系证据。
- **Git 工作台（`/repositories/{id}/branches`）**：三栏查看分支列表、提交拓扑图、提交详情与变更文件，仅读取本地快照，不触发远端写操作。
- **发布与本番管理（`/projects/{id}/releases`、`/projects/{id}/production`）**：单页创建 RLS 发布计划与变更条目，核验固定 SHA / MR 合入证据，并以只追加方式记录各仓库本番上线版本与回滚历史。
- **三语界面**：支持简体中文（`zh-CN`）、日本語（`ja`）、English（`en`），译文集中在 `app/locales/messages.json`。

## 备份、恢复与测试

```bash
# 在线备份与恢复 SQLite
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps -T tools python .agent/sqlite-backup.py backup runtime/backups/pre-deploy
bash .agent/run.sh compose --env-file runtime/local.env stop web
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps -T tools python .agent/sqlite-backup.py restore runtime/backups/pre-deploy
bash .agent/run.sh deploy --no-build

# 单元测试、安装包检查与文档一致性校验
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python .agent/test-unit.py -q
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python -I .agent/check-installed.py
bash .agent/run.sh python .agent/check-docs.py

# 可选规模测试与 Chromium E2E 测试
bash .agent/run.sh compose --env-file runtime/local.env build integration browser
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_SCALE=1 integration python -m pytest tests/test_scale.py -q -s
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_E2E=1 browser python -m pytest tests/test_e2e.py -q
```
