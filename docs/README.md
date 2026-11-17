# 使用与开发文档 / ドキュメント / Documentation

| 语言 / 言語 / Language | 用户 / ユーザー / Users | 开发 / 開発 / Development | 部署与运维 / デプロイ・運用 / Deployment |
|---|---|---|---|
| 简体中文 | [用户指南](zh-CN/user-guide.md) | [开发指南](zh-CN/development.md) | [Docker 与自动部署](zh-CN/docker-deployment.md) |
| 日本語 | [ユーザーガイド](ja/user-guide.md) | [開発ガイド](ja/development.md) | [Docker・自動デプロイ](ja/docker-deployment.md) |
| English | [User guide](en/user-guide.md) | [Development guide](en/development.md) | [Docker and automated deployment](en/docker-deployment.md) |

代理与公司 CA / プロキシと社内 CA / Proxy and company CA：`./gev up --init` → `./gev up`（[中文](../deploy/README.md) · [日本語](../deploy/README.ja.md) · [English](../deploy/README.en.md)）。

## 快速启动与初始账号 / クイックスタートと初期アカウント / Quick start & initial accounts

```bash
./gev up
```

默认访问 / 既定 URL / Default URL: [http://127.0.0.1:8766](http://127.0.0.1:8766)。
首次打开页面时在浏览器中创建管理员；应用无硬编码默认密码。全部命令见 `./gev help`。
最初にページを開いたとき、ブラウザーで管理者を作成します。ハードコードされた初期パスワードはありません。全コマンドは `./gev help` で確認できます。
You create the administrator in the browser on the first visit; there is no hardcoded default password. `./gev help` lists every command.

- **导入示例数据与演示账号 / サンプルとデモアカウント作成 / Seed demo data & accounts**（`demo-admin` / `demo-member`，密码见 `runtime/demo-access.json`）：

```bash
./gev seed
```

- **在终端创建管理员 / 端末から管理者を作成 / Create an admin from the terminal**（按提示输入密码 / プロンプト入力 / interactive prompt）：

```bash
./gev create-admin admin
```

- **生产后端部署 / 本番バックエンド / Production backend**（需另配 HTTPS 反向代理；首次设置需要设置码 / 要 HTTPS リバースプロキシ、初期設定にセットアップコードが必要 / requires HTTPS reverse proxy; first-run setup asks for the setup code）：

```bash
./gev -p up --wait-timeout 300
./gev -p setup-code
```

修改功能或命令时，请同步更新中、日、英三语文档并运行 `bash .agent/run.sh python .agent/check-docs.py`。
