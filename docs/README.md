# 使用与开发文档 / ドキュメント / Documentation

以下三套指南对应相同功能、命令和部署流程。请选择语言与用途。
同じ機能・コマンド・デプロイ手順を中国語、日本語、英語で説明します。
All three editions cover the same features, commands, and deployment procedure.

| 语言 / 言語 / Language | 用户 / ユーザー / Users | 开发 / 開発 / Development | Docker、部署与运维 / デプロイ・運用 / Deployment |
|---|---|---|---|
| 简体中文 | [用户指南](zh-CN/user-guide.md) | [开发指南](zh-CN/development.md) | [Docker 与自动部署](zh-CN/docker-deployment.md) |
| 日本語 | [ユーザーガイド](ja/user-guide.md) | [開発ガイド](ja/development.md) | [Docker・自動デプロイ](ja/docker-deployment.md) |
| English | [User guide](en/user-guide.md) | [Development guide](en/development.md) | [Docker and automated deployment](en/docker-deployment.md) |

## 快速启动 / クイックスタート / Quick start

在仓库根目录执行；需要 Docker 正在运行，无需宿主 Python 或 Node.js。
リポジトリ直下で実行します。Docker が必要です。ホストの Python / Node.js は不要です。
Run from the repository root with Docker running. Host Python and Node.js are unnecessary.

```bash
bash .agent/run.sh deploy
```

访问 / アクセス / Open: [http://127.0.0.1:8766](http://127.0.0.1:8766)。首次为一个空实例；管理员及示例数据需显式创建。
初回は空のインスタンスです。管理者とサンプルデータは明示的に作成します。
The first deployment is empty. Create an administrator or seed a demo explicitly.

生产环境 / 本番環境 / Production:

```bash
bash .agent/run.sh deploy --production --wait-timeout 300
```

此命令配置后端；浏览器访问需要另行配置 HTTPS 反向代理。
このコマンドはバックエンドを構成します。ブラウザーからの利用には HTTPS リバースプロキシを設定してください。
This configures the backend. Configure an HTTPS reverse proxy separately for browser access.

## 文档维护 / 更新方針 / Maintenance

修改功能或命令时同步更新三种语言。`deployment-verification.md` 与 `superpowers/` 是历史记录；当前访问规则及部署操作以本目录三套指南和当前代码为准。
機能やコマンドの変更時には 3 言語を更新してください。過去の検証・設計記録より、現在のガイドとコードを優先します。
Update all three languages when behavior or commands change. Historical verification and design records may describe earlier behavior; use these guides and current code for operation.
