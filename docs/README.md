# 使用与开发文档 / ドキュメント / Documentation

| 语言 / 言語 / Language | 用户 / ユーザー / Users | 开发 / 開発 / Development | 部署与运维 / デプロイ・運用 / Deployment |
|---|---|---|---|
| 简体中文 | [用户指南](zh-CN/user-guide.md) | [开发指南](zh-CN/development.md) | [Docker 与自动部署](zh-CN/docker-deployment.md) |
| 日本語 | [ユーザーガイド](ja/user-guide.md) | [開発ガイド](ja/development.md) | [Docker・自動デプロイ](ja/docker-deployment.md) |
| English | [User guide](en/user-guide.md) | [Development guide](en/development.md) | [Docker and automated deployment](en/docker-deployment.md) |

独立部署（内网代理/CA 与个人电脑直连）：`bash deploy/deploy.sh --init` → `bash deploy/deploy.sh`（[中文](../deploy/README.md) · [日本語](../deploy/README.ja.md) · [English](../deploy/README.en.md)）。

## 快速启动与初始账号 / クイックスタートと初期アカウント / Quick start & initial accounts

```bash
bash .agent/run.sh deploy
```

默认访问 / 既定 URL / Default URL: [http://127.0.0.1:8766](http://127.0.0.1:8766)。
应用无硬编码默认密码，首次部署后 `users` 表为空；`deploy` 会在未跟踪的 `runtime/demo-access.json` 中生成随机密码。
ハードコードされた初期パスワードはなく、初回デプロイ直後の `users` テーブルは空です（`runtime/demo-access.json` にランダムなパスワードを生成）。
No hardcoded default password exists (`users` table starts empty; random demo passwords are generated in untracked `runtime/demo-access.json`).

- **导入示例数据与演示账号 / サンプルとデモアカウント作成 / Seed demo data & accounts**（`demo-admin` / `demo-member`，密码见 `runtime/demo-access.json`）：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps -T tools python .agent/seed-local.py
```

- **仅创建自定义管理员 / 管理者のみ作成 / Create custom admin only**（按提示输入密码 / プロンプト入力 / interactive prompt）：

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python -m app.cli create-user --username admin --admin
```

- **生产后端部署 / 本番バックエンド / Production backend**（需另配 HTTPS 反向代理 / 要 HTTPS リバースプロキシ / requires HTTPS reverse proxy）：

```bash
bash .agent/run.sh deploy --production --wait-timeout 300
```

修改功能或命令时，请同步更新中、日、英三语文档并运行 `bash .agent/run.sh python .agent/check-docs.py`。
