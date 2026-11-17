# Repository Guidelines

## Project Structure & Module Organization

`app/` contains the Python 3.13 FastAPI application: HTTP routes in `app/routes/`, GitHub/GitLab/local Git adapters in `app/providers/`, shared web/form helpers in `app/web.py`, first-run setup in `app/first_run.py`, needs-action and unreleased-change views in `app/attention.py` and `app/unreleased.py`, release tracking in `app/releases.py`, and Git analysis, synchronization, and SQLite job execution in service modules. Jinja templates live in `app/templates/`, static CSS/JS and vendored jQuery in `app/static/`, and three-language strings (`zh-CN`, `ja`, `en`) in `app/locales/messages.json`. Migrations are in `alembic/versions/`, tests in `tests/`, three-language guides in `docs/`, and the single deployment implementation (Dockerfile, Compose, proxy/CA support) in `deploy/`.

## Docker Execution Policy

Use host file tools only for inspection and editing. Deploy and operate through `./gev ...`; run every other command (dependencies, builds, tests, migrations, Git mutations) through `bash .agent/run.sh ...`. Never invoke `docker run` directly, use host shell redirection, or execute project code on the host. Put compound commands inside Docker: `bash .agent/run.sh sh -lc 'cmd1 && cmd2'`.

## Build, Test, and Development Commands

```bash
./gev up
./gev test -q
bash .agent/run.sh .venv/bin/pytest -q
bash .agent/run.sh python .agent/check-docs.py
```

- `./gev up` configures `runtime/local.env`, builds the single SQLite web/worker container, runs migrations, and serves `http://127.0.0.1:8766`. `./gev help` lists start/stop, logs, backup and the other commands; add `-p` for the production environment.
- No accounts are created automatically. The first browser visit creates the administrator at `/setup` (production also asks for the code from `./gev -p setup-code`). `./gev seed` loads demo accounts (`demo-admin` / `demo-member`, passwords in `runtime/demo-access.json`) into an empty local instance, and `./gev create-admin NAME` creates an administrator from the terminal.
- Keep `docs/{zh-CN,ja,en}/` and `deploy/README{,.ja,.en}.md` synchronized when commands or behavior change.

## Coding Style, Testing & Security

- **Style**: 4-space Python (`snake_case` functions/modules, `PascalCase` classes, typed service interfaces); 2-space HTML/JS. Keep provider logic in `app/providers/` and add Alembic migrations for schema changes.
- **Testing**: `tests/test_*.py` reusing `tests/conftest.py` fixtures. Optional suites require `GEV_SCALE=1` or `GEV_E2E=1` (see `README.md`).
- **Commits & Security**: Imperative commit summaries (`feat:`, `fix:`, `docs:`). Keep `.env` and `runtime/` untracked. Project/release dashboards are public read-only unless an administrator turns on “Require sign-in to view”; public routes must depend on `optional_user`, which enforces that switch. Configuration and job triggers require an admin with CSRF validation; remote providers are strictly read-only with TLS verification.
