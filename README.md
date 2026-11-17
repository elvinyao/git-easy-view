# Git Easy View

面向团队的 Git 仓库、分支关系与发布追踪看板。把多个 GitHub / GitLab / 本地 Git 仓库放进一个项目看板，直接回答三个问题：**哪些分支需要处理、主干比线上多了什么、这次发布进行到哪一步**。

技术栈为 Python 3.13、FastAPI / Jinja / jQuery、SQLite 与原生 Git；单容器运行，只需要 Docker，无需数据库服务或 Node 构建。应用对代码平台只读，不执行合并、推送或部署。

## 30 秒开始

```bash
./gev up
```

打开 [http://127.0.0.1:8766](http://127.0.0.1:8766)。第一次访问会让你在浏览器里创建管理员，然后按「三步完成配置」连接仓库来源、创建项目、导入仓库。

只想先看看效果，可以在全新的本地实例上导入示例数据（账号密码在 `runtime/demo-access.json`）：

```bash
./gev seed
```

## 常用命令

所有操作都通过仓库根目录的 `./gev`，完整列表见 `./gev help`。

```bash
./gev up --no-build
./gev stop
./gev start
./gev status
./gev logs
./gev backup
./gev test -q
```

| 命令 | 作用 |
|---|---|
| `./gev up` | 生成配置、构建镜像、迁移数据库并启动；更新代码后重复执行，密钥、数据和账号都会保留 |
| `./gev up --no-build` | 源码未改动时快速重新部署 |
| `./gev stop` / `start` / `restart` | 停止、启动、重启（保留容器与数据卷） |
| `./gev status` / `logs` | 查看健康状态与实时日志 |
| `./gev down` | 移除容器、保留数据卷（拒绝 `-v`，避免误删数据库） |
| `./gev backup [目录]` / `restore 目录` | 在线备份 SQLite 与配套密钥；恢复前先 `./gev stop` |
| `./gev create-admin 用户名` | 不经浏览器，在终端创建管理员 |
| `./gev test` | 运行源码测试 |

生产实例在命令前加 `-p`，使用独立的配置、数据卷和端口 8767，并需另配 HTTPS 反向代理；首次设置需要 `./gev -p setup-code` 输出的设置码：

```bash
./gev -p up --wait-timeout 300
./gev -p setup-code
```

需要 HTTP/HTTPS 出站代理或公司 CA 时，先执行 `./gev up --init` 生成配置文件再部署，见[代理与公司 CA 指南](deploy/README.md)（[日本語](deploy/README.ja.md) · [English](deploy/README.en.md)）。

## 核心功能

- **项目总览（`/projects/{id}`）**：每个仓库的默认主干、发布分支、功能分支及其相对主干的领先/落后数量，附同步状态。
- **需要处理（`/projects/{id}/attention`）**：同步失败的仓库、超期的发布计划、未合回主干的发布分支、可以清理的已合入分支、长期没有进展的分支。
- **未上线变更（`/projects/{id}/unreleased`）**：对比已登记的线上版本与默认主干，列出尚未上线的提交和已合并的 PR/MR。
- **发布与线上版本（`/projects/{id}/releases`）**：创建发布计划、固定版本、核验 MR 合入证据，并以只追加方式记录各仓库的上线与回滚历史。
- **Git 工作台（`/repositories/{id}/branches`）**：三栏查看分支、提交拓扑和变更文件，只读本地快照。
- **访问控制**：默认匿名只读；管理员可在管理工作台开启「登录后才能查看」。配置、同步和发布记录始终需要管理员。
- **三语界面**：简体中文、日本語、English。

## 文档 / ドキュメント / Documentation

| 语言 | 用户说明 | 开发说明 | 部署与运维 |
|---|---|---|---|
| 简体中文 | [用户指南](docs/zh-CN/user-guide.md) | [开发指南](docs/zh-CN/development.md) | [部署指南](docs/zh-CN/docker-deployment.md) |
| 日本語 | [ユーザーガイド](docs/ja/user-guide.md) | [開発ガイド](docs/ja/development.md) | [デプロイ・運用](docs/ja/docker-deployment.md) |
| English | [User guide](docs/en/user-guide.md) | [Development guide](docs/en/development.md) | [Deployment and operations](docs/en/docker-deployment.md) |

三语文档总入口：[docs/README.md](docs/README.md)。开发约定见 [AGENTS.md](AGENTS.md)。

## 开发与验证

```bash
./gev test -q
./gev compose run --rm --no-deps tools python -I .agent/check-installed.py
bash .agent/run.sh python .agent/check-docs.py
./gev compose build browser
./gev compose run --rm --no-deps -e GEV_SCALE=1 tools python -m pytest tests/test_scale.py -q -s
./gev compose run --rm -e GEV_E2E=1 browser python -m pytest tests/test_e2e.py -q
```

前三条分别是源码测试、安装包检查和文档一致性校验；后三条是可选的规模测试与 Chromium 端到端测试。
