# 内网部署：代理与公司 CA

[日本語](README.ja.md) · [English](README.en.md) · [完整文档](../docs/README.md)

本目录提供独立的 Dockerfile、Compose 配置和部署脚本，适用于需要 HTTP/HTTPS 出站代理和公司 CA 的内网，也支持个人电脑直连网络。部署后仍只有一个常驻应用容器，SQLite 与 Git 缓存保存在原有持久卷中。

## 1. 先让 Docker 能拉取镜像

需要 Docker Engine 20.10+（或对应的 Docker Desktop）和 BuildKit。从仓库根目录运行独立入口 `bash deploy/deploy.sh`；它在宿主只调用 Docker，配置生成、构建、迁移和应用命令均在容器内执行，无需宿主 Python 或 Node，也不依赖 `.agent/run.sh`。

**兼容新版 `docker compose` 和旧版 `docker-compose`（最低 1.29.2）。** 默认使用 `docker:28-cli` 工具容器，宿主是否安装 Compose、安装什么版本都不影响部署。脚本优先选择工具容器里的 Compose 插件，没有插件时使用 `docker-compose`。旧版自动使用兼容配置与健康状态轮询，仍保留 BuildKit secret、代理值和 CA 配置，不增加常驻容器。

使用 v1 更新时，部署脚本在数据库迁移成功后替换 web 容器，保留 SQLite 和 Git 缓存的命名卷。请通过 `bash deploy/deploy.sh` 更新；直接调用旧版 `docker-compose up` 在新 Docker Engine 上可能因已废弃的 `ContainerConfig` 字段而失败。

内网已有工具镜像时，可指定 `GEV_DOCKER_CLI_IMAGE`；镜像必须包含 `docker`、`sh`、`awk`、`date`，以及上述任一 Compose 实现。将示例地址换成实际镜像，并在后续部署和运维命令中使用同一设置；个人电脑无需设置：

```bash
GEV_DOCKER_CLI_IMAGE=registry.example.com/tools/docker-cli-compose:1.29.2 bash deploy/deploy.sh
GEV_DOCKER_CLI_IMAGE=registry.example.com/tools/docker-cli-compose:1.29.2 bash deploy/deploy.sh compose --env-file runtime/local.env ps
```

代理需要分两层配置：

| 层次 | 配置位置 | 用途 |
|---|---|---|
| Docker 本身 | Docker Desktop 设置，或 Engine 的 daemon 配置及主机信任库 | 拉取 Python、Docker CLI 和构建基础镜像 |
| 镜像构建及应用 | 本指南的 `runtime/*.network.env` 和 CA 目录 | pip 下载依赖，以及运行时 Git/API 请求 |

**必须先完成第一层。** 应用的配置文件无法修复尚未拉取成功的工具镜像。

Docker Desktop：在 Settings 的 Proxies 中配置代理；将公司根证书加入主机信任库，然后重启 Desktop。Desktop 不读取 `daemon.json` 的代理配置。参见 [Desktop 代理设置](https://docs.docker.com/desktop/settings-and-maintenance/settings/#proxies) 和 [Docker CA 说明](https://docs.docker.com/engine/network/ca-certs/)。

Linux Docker Engine：由管理员将下面的代理字段合并到 `/etc/docker/daemon.json`，保留原有设置，并配置系统 CA 信任后重启 Docker。参见 [Engine 代理配置](https://docs.docker.com/engine/daemon/proxy/)。

```json
{
  "proxies": {
    "http-proxy": "http://proxy.corp.example:3128",
    "https-proxy": "http://proxy.corp.example:3128",
    "no-proxy": "localhost,127.0.0.1,.corp.example"
  }
}
```

## 2. 首次准备配置

```bash
bash deploy/deploy.sh --init
```

该命令只准备文件，不构建或启动应用，也不创建数据库账号。已有应用配置和密钥会保留。

| 文件 / 目录 | 内容 |
|---|---|
| `runtime/local.env` | 应用密钥、SQLite 地址、Compose 项目名和端口 |
| `runtime/local.network.env` | 出站代理、可选 pip 镜像源 |
| `runtime/local.deploy.env` | 网络文件路径、CA 目录、监听地址等部署选项 |
| `runtime/local.deploy/` | 自动生成的构建 secret 与运行时网络文件，不手工编辑 |
| `runtime/ca/` | 公司根证书 / 中间 CA，PEM 格式的 `.pem` 或 `.crt` 文件 |

使用编辑器修改 `runtime/local.network.env`，例如：

```dotenv
HTTP_PROXY=http://proxy.corp.example:3128
HTTPS_PROXY=http://proxy.corp.example:3128
NO_PROXY=localhost,127.0.0.1,::1,web,gitlab.corp.example,.corp.example
PIP_INDEX_URL=
PIP_EXTRA_INDEX_URL=
```

`HTTPS_PROXY` 表示用于 HTTPS 目标的代理；普通 HTTP CONNECT 代理的地址通常仍以 `http://` 开头，以公司提供的地址为准。无需代理时保留空值。

- 文件是一行一个 `KEY=value`，不是 shell 脚本；不要写 `export`、变量引用或命令替换。脚本会同时提供大写和小写代理变量，并保留本机健康检查的代理绕过项。
- `NO_PROXY` 加入应直接访问的内网 Git / 镜像源域名。不要把仍须经过代理的外部目标加入其中。
- 若代理运行在个人电脑上，容器内使用 `host.docker.internal:端口`；`127.0.0.1` 指的是容器自己。部署配置为 Linux 提供 `host-gateway` 映射；代理也必须允许来自 Docker 网络的连接。
- 需要认证时，在 URL 中对用户名和密码的特殊字符进行百分号编码。网络文件可能包含凭据，保持 0600，不提交到 Git，不贴到日志或工单。
- pip 镜像源仅用于构建；应用只接收代理配置。优先使用公司统一的 `PIP_INDEX_URL`，仅在确有需要时配置额外源。

将公司提供的 CA 文件放入 `runtime/ca/`，例如 `runtime/ca/company-root.crt`。不要放私钥、PFX/P12 或仅含服务器证书的文件；目录中可以有多个 CA。无公司 CA 的个人环境可以保留空目录。

## 3. 部署、更新和个人电脑兼容

```bash
bash deploy/deploy.sh
bash deploy/deploy.sh --no-build
bash deploy/deploy.sh --pull --wait-timeout 300
bash deploy/deploy.sh --help
```

首次部署和源码更新用第一条；第二条复用已有内网镜像；第三条同时刷新构建基础镜像。脚本验证配置、构建、停止 web、迁移数据库、重新启动并等待健康检查。失败返回非零，保留配置和数据卷。默认访问 [http://127.0.0.1:8766](http://127.0.0.1:8766)。

同一个 `COMPOSE_PROJECT_NAME` 使用原有 `app-data` 和 `git-cache` 卷，不复制或清空 SQLite。切换部署方式前备份现有实例；不要用两个部署流程并发操作同一项目。

个人电脑也使用同一个 `bash deploy/deploy.sh`，将代理留空、CA 目录留空即可。脚本使用独立的 `-intranet` 镜像标签；第一次使用必须构建，之后才能用 `--no-build`。此入口支持当前 SQLite 单容器模式；旧 PostgreSQL 实例先按[数据库迁移指南](../docs/zh-CN/docker-deployment.md)完成 `--migrate-sqlite`，不要直接改数据库 URL。

### 3.1 使用服务器上的本地 Git 仓库

在 `runtime/local.env`（生产为 `runtime/production.env`）设置已有目录，例如 `GEV_LOCAL_GIT_DIRECTORY=/Users/elvinyao/workspace`，然后重新运行 `bash deploy/deploy.sh`。镜像已更新、只改映射时可加 `--no-build`。新版 Compose 与旧版兼容配置都会将目录只读映射到 `/data/local-git`，未设置时使用空的 `runtime/local-repositories/`，不增加常驻容器。

管理员在添加来源页选择“本地 Git 目录”，浏览子目录、扫描并批量导入。无需 token 或远程连接。目录必须可被 Docker 读取，导入后保持映射稳定。详见[本地导入说明](../docs/zh-CN/user-guide.md#71-批量添加本地-git-仓库)。

## 4. 正式内网服务与独立实例

```bash
bash deploy/deploy.sh --production --init
bash deploy/deploy.sh --production --wait-timeout 300
```

在两条命令之间编辑 `runtime/production.network.env` 并安装 CA。生产默认使用独立的 `runtime/production.env`、Compose 项目和数据卷，后端端口为 8767。正式环境必须通过 HTTPS 反向代理访问，才能正常使用 Secure 登录 cookie；出站 `HTTPS_PROXY` 不会替用户浏览器提供 HTTPS。

默认仅监听 `127.0.0.1`，适合宿主上的反向代理。需要让其他主机的反向代理访问后端时，明确指定服务器的内网 IP，或以下所有接口地址，并由防火墙限制访问来源：

```bash
bash deploy/deploy.sh --production --bind-address 0.0.0.0 --no-build
```

项目页面支持匿名读取；开放监听地址后，能连到该端口的人可以浏览项目。生产管理员仍应通过 HTTPS 登录。

独立测试实例可同时指定应用配置、网络配置、CA 目录、项目名和端口：

```bash
bash deploy/deploy.sh --env-file runtime/intranet-test.env --project-name gev-intranet-test --port 8772 --network-file runtime/intranet-test.network.env --ca-dir runtime/company-ca --init
bash deploy/deploy.sh --env-file runtime/intranet-test.env
```

自定义文件和目录应位于 `runtime/` 内。网络路径、CA 路径和监听地址会保存在对应的 `.deploy.env`，后续无需重复输入。已有 env 的端口和项目名不会被命令行静默改写；需要新实例时选择新的 env 和项目名。`--production` 部署在后续操作中也要继续带上 `--production`。

## 5. 代理和 CA 如何进入容器

构建阶段使用 BuildKit secret 挂载网络配置和 CA，pip 验证系统 CA 与公司 CA 的合并证书链。代理凭据和公司 CA 不作为 Dockerfile `ENV` / `ARG` 固化到镜像。

运行时，CA 目录只读映射到 `/etc/gev/ca`。入口程序生成 `/tmp/gev-ca-bundle.pem`，包含系统 CA 与公司 CA，并为 Python/HTTPX、pip、Git 和 HTTPS 代理证书验证配置相应的信任变量。CA 私钥完全不需要进入容器。TLS 验证始终开启。

通常可以将管理界面中的连接 CA 路径留空，使用这套默认信任。若填写连接专属 CA 路径，它会覆盖该连接的 Git / API 信任，因此应包含连接所需的完整 CA 链；也可以填写 `/tmp/gev-ca-bundle.pem` 使用合并后的信任。

代理凭据虽然不写进镜像，仍可被拥有 Docker 管理权限的人读取运行容器的环境。按公司凭据管理规范保护主机与 `runtime/`。更换网络配置或 CA 后重新运行 `bash deploy/deploy.sh`；只更新运行时配置且已有镜像时可用 `--no-build`。

## 6. 管理、备份与排错

日常操作使用同一脚本的 `compose` 子命令，自动选择独立 Compose 文件并读取该实例保存的部署配置：

```bash
bash deploy/deploy.sh compose --env-file runtime/local.env ps
bash deploy/deploy.sh compose --env-file runtime/local.env logs --tail=100 web
bash deploy/deploy.sh compose --env-file runtime/local.env run --rm --no-deps tools python -m app.cli create-user --username your-admin --admin
bash deploy/deploy.sh compose --env-file runtime/local.env run --rm --no-deps -T tools python .agent/sqlite-backup.py backup runtime/backups/intranet-before-update --env-file runtime/local.env
```

管理员密码交互输入。生产实例把命令中的 env 路径改为 `runtime/production.env`。备份另用新的目录名；除数据库及配套应用密钥外，也需安全保存网络文件、部署选项与 CA。恢复数据库流程见[运维指南](../docs/zh-CN/docker-deployment.md)，对应 Compose 命令以 `bash deploy/deploy.sh compose` 开头，重启用 `bash deploy/deploy.sh --no-build`。不要执行 `down -v`。

| 现象 | 检查位置 |
|---|---|
| 工具或基础镜像拉取失败 | Docker Desktop / daemon 的代理和系统信任，尚未进入应用部署阶段 |
| pip 超时、407 或证书失败 | 网络文件中的代理、认证、pip 源，以及构建使用的公司 CA |
| 页面可用但 Git 同步失败 | 应用日志、代理可达性、`NO_PROXY`、token 权限和连接 CA 路径 |
| 更新 CA 后仍失败 | 重新部署以生成新证书包；检查是否有连接专属 CA 覆盖默认配置 |
| 正式环境登录循环 | 浏览器是否使用 HTTPS，反向代理和 Secure cookie 是否配置正确 |

部署健康检查只验证应用及数据库就绪。上线前还需在公司真实网络中检查一个真实连接，导入仓库并等待同步成功；本地验证不能替代公司代理认证、CA 链和平台权限验收。
