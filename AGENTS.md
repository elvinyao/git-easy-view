# Repository Guidelines

## Project Structure & Module Organization

`app/` contains the Python 3.13 FastAPI application: routes in `app/routes/`, GitHub/GitLab adapters in `app/providers/`, and Git analysis, synchronization, and database-backed jobs in service modules. Jinja templates live in `app/templates/`; CSS, JavaScript, and vendored jQuery live in `app/static/`. Database migrations are in `alembic/versions/`, tests in `tests/`, and design/deployment notes in `docs/`.

## Docker Execution Policy

Use the host for inspection and source editing through file tools. Run project execution, dependencies, builds, tests, formatting, and Git mutations through `bash .agent/run.sh ...`. The authorized deployment exception is `bash deploy/deploy.sh ...`: this standalone host wrapper only launches Docker; configuration and deployment execute inside containers. Reuse Python 3.13; never invoke `docker run` directly or use host shell redirection. Put compound commands inside Docker: `bash .agent/run.sh sh -lc 'command1 && command2'`. If Docker fails, report it; never execute project code on the host.

## Build, Test, and Development Commands

Run from the repository root. `bash .agent/run.sh deploy` configures, builds, migrates, and starts healthy services. Optional development dependencies:

```bash
bash .agent/run.sh python -m venv .venv
bash .agent/run.sh .venv/bin/pip install -r requirements.lock
bash .agent/run.sh .venv/bin/pip install -e . --no-deps
bash .agent/run.sh .venv/bin/python .agent/setup-local.py
```

- `bash .agent/run.sh compose --env-file runtime/local.env up -d --build`: build and start one web container with SQLite, scheduling, and background jobs; browse `http://127.0.0.1:8766`.
- `bash .agent/run.sh .venv/bin/pytest -q`: run the default test suite.

Frontend assets require no Node build. Account/demo creation is explicit. See `docs/README.md` for Chinese, Japanese, and English guides; keep editions synchronized.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions/modules, `PascalCase` classes, and type hints for service interfaces. Match existing two-space JavaScript/HTML indentation. Keep provider logic outside routes and add Alembic migrations for schema changes. No formatter or linter is configured.

## Testing Guidelines

Use pytest files/functions named `test_*.py`/`test_*`; reuse `tests/conftest.py` fixtures. Add regression tests for changed behavior. No numeric coverage threshold is configured. Integration, scale, and Playwright/Chromium suites require `GEV_INTEGRATION=1`, `GEV_SCALE=1`, and `GEV_E2E=1`, respectively; use the corresponding Docker commands and prerequisites in `README.md`.

## Commit & Pull Request Guidelines

History uses imperative summaries, often prefixed `feat:`, `fix:`, or `docs:`. Keep commits focused. PRs should explain behavior changes, link relevant issues, report validation, and include screenshots for UI changes. Document configuration or migration impacts.

## Security & Configuration

Keep credentials, `.env` files, and `runtime/` artifacts untracked. Project dashboards allow anonymous reads; configuration and job requests require an administrator. Preserve TLS verification and CSRF protection. Remote providers perform reads only.
