# Development Guide

[Documentation index](../README.md) · [简体中文](../zh-CN/development.md) · [日本語](../ja/development.md)

## Release management implementation

`app/releases.py` builds production summaries and validates MR evidence; `app/routes/releases.py` exposes public GET routes and admin/CSRF-protected POST routes. Migration `0003` adds `git_tags`, `release_sources`, `release_plans`, `release_items` and `deployments`. Deployment history is append-only; production uses the latest record ID per project/repository. Plan version checks reject duplicate/stale writes, and project write locks serialize production records.

Git synchronization atomically publishes branch/tag snapshots, retaining old snapshots on failure. Optional local MR bindings fetch platform records separately; changed bindings or credentials invalidate in-flight results. Only fresh, complete evidence matching the fixed SHA and specific MR confirms a merge. Branch names alone never imply completion. Completion requires deployment and merge; reconciliation never writes production history. Tests: `tests/test_releases.py`, `tests/test_release_tags.py`, `tests/test_releases_browser.py`.

## 1. Stack and layout

Python 3.13, FastAPI, SQLAlchemy / Alembic, Jinja2, CSS / JavaScript, and vendored jQuery. SQLite is the default database; Redis is unnecessary. There is no Node build.

| Path | Purpose |
|---|---|
| `app/main.py`, `config.py`, `db.py`, `models.py` | Application factory, settings, database, models |
| `app/routes/` | Authentication, public reads, and administration HTTP routes |
| `app/providers/` | GitHub / GitLab APIs and HTTPS / URL validation |
| `app/git_service.py`, `git_workspace.py` | Local Git execution, history, and file details |
| `app/sync.py`, `jobs.py`, `scheduler.py`, `worker.py`, `server.py` | Snapshots, database queue, scheduling, subprocesses, single-container startup |
| `app/relations.py`, `project_overview.py` | Relationship evidence and batched project summaries |
| `app/templates/`, `app/static/`, `app/locales/` | Pages, static assets, and three-language messages |
| `alembic/versions/` | Database migrations |
| `tests/` | Unit, integration, scale, and Chromium regression tests |
| `.agent/` | Docker runner, deployment, demo, package checks, verification images |
| `runtime/` | Ignored configuration, CA, access files, screenshots, and verification output |

Read the root [AGENTS.md](../../AGENTS.md) before editing.

## 2. Execution policy and setup

Use the host for reading, searching, editing source, and read-only Git inspection. Run Python, pip, pytest, migrations, servers, formatters, and Git mutations through `bash .agent/run.sh ...`. Do not execute project code or Docker Compose directly on the host.

The fastest way to obtain dependencies and services is:

```bash
bash .agent/run.sh deploy
```

This needs no `.venv`. To create an independent development instance, choose its configuration, project name, and port on the first run:

```bash
bash .agent/run.sh deploy --env-file runtime/development.env --project-name gev-development --port 8770
```

Use `--env-file runtime/development.env` for subsequent Compose commands. Never run development utilities or demo seeding with production configuration.

You can also create a container-only virtual environment for convenient source testing:

```bash
bash .agent/run.sh python -m venv .venv
bash .agent/run.sh .venv/bin/pip install -r requirements.lock
bash .agent/run.sh .venv/bin/pip install -e . --no-deps
```

The mounted `.venv` contains Linux container executables; do not run them on macOS. Use `requirements.lock` and retain Python 3.13 rather than arbitrarily upgrading the runtime.

## 3. Editing and live reload

The normal `web` service runs installed code from its image. Redeploy after editing source to update it. For page development, run the source-mounted `tools` container on a separate port:

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps -p 127.0.0.1:8768:8000 tools uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

Open [http://127.0.0.1:8768](http://127.0.0.1:8768); stop with Ctrl+C. It shares the local database and cache. Use the development configuration above for isolation. `--reload` detects Python changes; refresh the browser for changed templates and static assets. Workers still use built code, so redeploy the development instance after changing synchronization behavior.

Start an interactive shell inside Docker too:

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools bash
```

## 4. Architecture and behavior to preserve

`app.server` migrates and starts HTTP plus `app.worker`. The worker schedules due repositories into SQLite and launches job subprocesses sequentially. Each job holds the repository lock, fetches Git/API data, and publishes fixed-OID snapshots. Interrupted jobs recover from the database without Redis. SQLite uses WAL and a 30-second lock wait; keep transactions short and never hold a write lock across network calls. Timed-out subprocesses are terminated, with at most five attempts.

Git and API freshness are independent. Comparison caches use immutable OIDs and a rule version; do not mix moving refs during analysis. Keep inferred sources, manual claims, planned targets, current reachability, and historical PR/MR events distinct.

Public GET routes use `optional_user`. Project reads are public; non-administrator repository reads must verify a current project association. Configuration and new synchronization / comparison requests require `require_admin`. Preserve POST CSRF validation. Apply the same scope checks to HTML fragments, JSON, and commit URLs.

Public GET may read the database and local Git objects, but should not initiate remote fetching or platform writes. Administrator connection checks and repository discovery contact the platform through their existing workflows. Preserve TLS, HTTPS URL validation, output / time limits, cache locks, and credential redaction.

Frontend updates must handle superseded requests and cancellation callbacks so slow responses cannot replace newer filters. Restore branch, commit, filters, pagination, and density from the URL.

Local sources use `app/providers/local.py` and reuse connection, import, and synchronization models without new tables. Host setting `GEV_LOCAL_GIT_DIRECTORY` controls deployment mounting; service setting `GEV_LOCAL_GIT_ROOT` (default `/data/local-git`) restricts access. Discovery and import validate resolved paths and Git metadata. Cache copies allow only the file protocol and keep sources read-only. An absent API timestamp means “Not applicable” for local sources, rather than pending synchronization. Regression coverage lives in `tests/test_local_repositories.py`; three-language directory selection, retry, and batch import are covered in `tests/test_admin_experience_browser.py`.

## 5. Style, localization, and dependencies

Use four-space Python indentation, `snake_case` functions / modules, `PascalCase` classes, and service interface type hints. Match existing two-space HTML / JavaScript style. Keep provider behavior in the provider layer. No shared formatter or linter is configured.

`app/locales/messages.json` uses Chinese messages as keys with `ja` and `en` values. Use named placeholders such as `{name}`. Templates use `t(...)`; services can use `Message` for errors. Preserve HTML escaping and do not translate user data such as repository names or commits. Run localization tests and review all three layouts after text changes.

Dependency changes require corresponding `pyproject.toml` and `requirements.lock` updates and Python 3.13 Docker validation. `Dockerfile` installs locked dependencies and a wheel. Check package resource declarations when adding templates, assets, or locale files so they work without source mounts.

## 6. Database migrations

Add an Alembic migration when changing persisted models. Generate and check changes against a deployed local development database:

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools alembic revision --autogenerate -m "describe schema change"
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps migrate
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools alembic check
```

Review generated operations, data conversion, constraints, and downgrade behavior. `init-db` is for empty non-production development databases and does not replace production migrations. Test downgrades against disposable databases, not databases containing user data.

## 7. Testing and validation

Name pytest files / functions `test_*.py` / `test_*` and reuse `tests/conftest.py`. There is no numeric coverage threshold. Add meaningful regression tests for permissions, snapshot consistency, recovery, and changed interactions.

Use the deployed image's dependencies and source mount:

```bash
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python .agent/test-unit.py -q
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python .agent/test-unit.py tests/test_deploy.py tests/test_i18n.py -q
bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps tools python -I .agent/check-installed.py
bash .agent/run.sh python .agent/check-docs.py
```

`test-unit.py` removes inherited `GEV_*` deployment settings so they cannot affect isolated unit tests. If `.venv` is available, `bash .agent/run.sh .venv/bin/pytest -q` also works. The `-I` check validates installed wheel resources; source tests do not replace it. The documentation check validates local links, headings, identical commands across languages, and shell syntax.

Run optional suites with **local** configuration after building the default application image (`deploy`) and verification images:

```bash
bash .agent/run.sh compose --env-file runtime/local.env build integration browser
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_SCALE=1 integration python -m pytest tests/test_scale.py -q -s
bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_E2E=1 browser python -m pytest tests/test_e2e.py -q
```

Default tests exercise real SQLite worker subprocesses and migration copies. The legacy PostgreSQL/RQ suite in `tests/test_integration.py` requires a separate test PostgreSQL URL with database creation privileges, `GEV_JOB_BACKEND=redis`, and `GEV_INTEGRATION=1`. Those services are unnecessary for routine development. Scale tests now use isolated SQLite with 100 small repositories (55 refs and three commits each); they do not establish capacity for large histories.

The full E2E suite includes `test_compose_public_demo_screenshots`, which needs an explicitly initialized local demo (3 repositories × 55 branches) and matching administrator credentials in `runtime/demo-access.json`. On an empty instance, append `-k 'not compose_public_demo_screenshots'` to run isolated fixtures only; do not seed an existing database. Screenshots go to `runtime/screenshots/` and scale results to `runtime/verification/`.

## 8. Commits and pull requests

History uses imperative summaries, commonly `feat:`, `fix:`, and `docs:`, alongside unprefixed commits. Keep each commit focused.

A PR should explain the problem, behavior change, linked issues, and validation commands / results. Include screenshots for UI changes and migration / operational effects for configuration or schema changes. Update Chinese, Japanese, and English documentation together. Do not commit runtime files, env secrets, databases, caches, access credentials, or secrets visible in screenshots.

Git mutations also run inside Docker:

```bash
bash .agent/run.sh git -c safe.directory=/workspace switch -c codex/describe-change
bash .agent/run.sh git -c safe.directory=/workspace add app tests docs
bash .agent/run.sh git -c safe.directory=/workspace commit -m "docs: describe deployment workflow"
```

Read-only `git status`, `git diff`, and `git log` can run on the host. Check for sensitive content and run validation appropriate to the change before committing.
