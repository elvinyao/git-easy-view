# Repository Guidelines

## Project Structure & Module Organization

`app/` contains the Python 3.13 FastAPI application: HTTP routes in `app/routes/`, GitHub/GitLab/local Git adapters in `app/providers/`, shared web/form helpers in `app/web.py`, release tracking in `app/releases.py`, and Git analysis, synchronization, and SQLite job execution in service modules. Jinja templates live in `app/templates/`, static CSS/JS and vendored jQuery in `app/static/`, and three-language strings (`zh-CN`, `ja`, `en`) in `app/locales/messages.json`. Migrations are in `alembic/versions/`, tests in `tests/`, and three-language guides in `docs/`.

## Docker Execution Policy

Use host file tools only for inspection and editing. Run all execution, dependencies, builds, tests, migrations, and Git mutations through `bash .agent/run.sh ...` (exception: standalone deployment wrapper `bash deploy/deploy.sh ...`). Never invoke `docker run` directly, use host shell redirection, or execute project code on the host. Put compound commands inside Docker: `bash .agent/run.sh sh -lc 'cmd1 && cmd2'`.

## Build, Test, and Development Commands

```bash
bash .agent/run.sh deploy
bash .agent/run.sh .venv/bin/pytest -q
bash .agent/run.sh python .agent/check-docs.py
```

- `deploy` configures `runtime/local.env` and `runtime/demo-access.json`, builds the single SQLite web/worker container, runs migrations, and serves `http://127.0.0.1:8766`.
- No accounts are created automatically. Seed demo accounts (`demo-admin` / `demo-member`, passwords in `runtime/demo-access.json`) via `seed-local.py` or create an admin via `python -m app.cli create-user --username admin --admin` (see `docs/README.md`).
- Keep `docs/{zh-CN,ja,en}/` and `deploy/README{,.ja,.en}.md` synchronized when commands or behavior change.

## Coding Style, Testing & Security

- **Style**: 4-space Python (`snake_case` functions/modules, `PascalCase` classes, typed service interfaces); 2-space HTML/JS. Keep provider logic in `app/providers/` and add Alembic migrations for schema changes.
- **Testing**: `tests/test_*.py` reusing `tests/conftest.py` fixtures. Optional suites require `GEV_INTEGRATION=1`, `GEV_SCALE=1`, or `GEV_E2E=1` (see `README.md`).
- **Commits & Security**: Imperative commit summaries (`feat:`, `fix:`, `docs:`). Keep `.env` and `runtime/` untracked. Project/release dashboards are public read-only; configuration and job triggers require an admin with CSRF validation; remote providers are strictly read-only with TLS verification.
