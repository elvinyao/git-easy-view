# Git Easy View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved team Git dashboard with project authorization, GitHub/GitLab import, background Git analysis, and server-rendered HTML/jQuery pages.

**Architecture:** One Python application, PostgreSQL metadata, Redis/RQ workers and a small periodic dispatcher. Git bare repositories live on persistent disk; immutable commit IDs identify analysis results. Every route applies project authorization and every mutation verifies CSRF.

**Tech Stack:** Python 3.13, FastAPI, Jinja2, jQuery, SQLAlchemy 2, Alembic, PostgreSQL, RQ/Redis, native Git, Docker Compose.

---

## Execution rules and file boundaries

Use `/Users/elvinyao/workspace/git-easy-view` on branch `feat/team-dashboard`. All executable commands, dependency installation, Git mutations, tests and servers go through `bash .agent/run.sh ...`. The existing clean checkout is used; do not create another checkout or modify main. Host commands are limited to permitted file reads and Git inspection. Never access real company accounts without configured credentials.

Files and ownership:

- `pyproject.toml`, `.agent/run.sh`, `.gitignore`: project execution and dependencies.
- `app/config.py`, `app/db.py`, `app/models.py`, `alembic/`: settings and persistence.
- `app/auth.py`, `app/main.py`, `app/cli.py`: session security, app factory and administration.
- `app/git_service.py`, `app/relations.py`: native Git and evidence semantics.
- `app/providers/base.py`, `github.py`, `gitlab.py`: normalized external data and pagination.
- `app/sync.py`, `app/jobs.py`, `app/scheduler.py`: snapshots, queue, recovery and polling.
- `app/routes/projects.py`, `repositories.py`, `admin.py`: pages and authorized mutations.
- `app/templates/`, `app/static/`: HTML, CSS, JavaScript and vendored jQuery.
- `tests/`: real Git fixtures, provider HTTP fixtures, authorization, recovery and browser workflows.
- `Dockerfile`, `compose.yaml`, `.env.example`, `README.md`: deployment and operation.

Shared contracts are imported from their owning modules. Tasks may extend schemas with migrations, but existing public functions must remain backward compatible. Configuration comes from environment variables prefixed `GEV_`; a SQLite URL is permitted for isolated tests and a local demo, while production uses PostgreSQL.

## Task 1: Foundation, persistence and session authorization

Files: `pyproject.toml`, `.gitignore`, `app/{__init__,config,db,models,auth,main,cli}.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`, `app/templates/{base,login}.html`, `tests/{conftest,test_auth}.py`.

- [ ] Define dependencies and initialize a container-created `.venv`; pin resolved dependencies in a lock file. FastAPI, uvicorn, Jinja2, python-multipart, SQLAlchemy, Alembic, psycopg, httpx, cryptography, argon2-cffi, itsdangerous, pydantic-settings, Redis, RQ; pytest for tests.
- [ ] Add failing authorization tests using a temporary SQLite database and the real FastAPI TestClient. Required behavior includes anonymous login redirect, successful password login, invalid password rejection, CSRF rejection, and member revocation on the next request:

```python
def test_anonymous_dashboard_redirects(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

def test_mutation_requires_csrf(logged_in_client):
    assert logged_in_client.post("/logout").status_code == 403
```

- [ ] Run `bash .agent/run.sh .venv/bin/pytest tests/test_auth.py -q`; record the missing-behavior failures before implementing.
- [ ] Implement `Settings`, `create_app(settings=None)`, an app-scoped SQLAlchemy session dependency, user password hashing, signed HttpOnly SameSite session cookies, current-user validation against DB on every request, and CSRF form/header checking. Production requires explicit secrets and secure cookies. Sessions contain user ID and CSRF only; platform tokens are encrypted using a deployment key.
- [ ] Define `User`, `Project`, `ProjectMember`, `PlatformConnection`, `Repository`, `ProjectRepository`, `Branch`, `BranchConfig`, `Comparison`, `PullRequest`, `SyncJob`, `AuditEvent`. Use platform connection plus external ID for repository uniqueness. Store head/target OIDs, evidence, independent sync timestamps and coverage start; never imply missing analysis is zero. Model task/snapshot generations for later atomic publication.
- [ ] Provide `require_admin` and project/repository access helpers, constant-time password verification through the hash library, login/logout pages and CLI commands `init-db` (development only) and `create-user --username ... --admin` with password supplied by prompt or environment. Do not create a default production administrator.
- [ ] Run the authorization tests and migration round-trip through the runner. Commit only verified foundation files with `git -c user.name=Codex -c user.email=codex@localhost commit` inside the runner.

## Task 2: Native Git analysis and evidence semantics

Files: `app/git_service.py`, `app/relations.py`, `tests/test_git_service.py`, `tests/test_relations.py`.

- [ ] Create real temporary Git histories inside Docker for divergence, fast-forward, merge, squash, cherry-pick, revert, orphan histories, empty repositories and branch recreation. Each test uses Git's own commit IDs; no mock replacement of ancestry calculations.
- [ ] Write and observe failing tests for this API:

```python
from app.git_service import GitService

def test_diverged_counts(diverged_repo):
    service = GitService(diverged_repo)
    result = service.compare("feature/login", "main")
    assert (result.ahead, result.behind, result.state) == (1, 1, "diverged")
    assert result.source_oid != result.target_oid

def test_squash_does_not_claim_original_commits_contained(squashed_repo):
    result = GitService(squashed_repo).compare("feature/login", "main")
    assert result.state != "contained"
```

- [ ] Implement `GitService(path).branches()`, `.compare(source_ref, target_ref)`, `.commits(source_oid, target_oid, limit=50)` and a bare clone/fetch helper. Return dataclasses with explicit OIDs, ahead/behind, merge bases and `identical/contained/diverged/unrelated` states; errors are exceptions, not zero counts. Validate revisions via `rev-parse --verify` and use fixed OIDs in subsequent commands. Git commands use argv, timeouts, capped output and controlled environment; no shell, hooks or worktree code execution.
- [ ] Implement source candidates with ancestry evidence for configured main/release refs, excluding the branch itself; all inferred candidates remain labeled inferred. Keep manual sources and planned PR/MR targets separate. Closed historical PR/MRs cannot create current planned targets. Do not infer content equivalence from reachability.
- [ ] Provide deterministic cached comparison identity `(repo, source_oid, target_oid, rule_version)` and clear deleted/missing/unborn handling for synchronization.
- [ ] Run `bash .agent/run.sh .venv/bin/pytest tests/test_git_service.py tests/test_relations.py -q`, inspect all results, then commit.

## Task 3: Platform connectors and credential boundaries

Files: `app/providers/{__init__,base,github,gitlab}.py`, `tests/test_providers.py`.

- [ ] Write HTTP-fixture tests that fail for GitHub.com and Enterprise base URLs, GitLab subgroup IDs, multiple repository and PR/MR pages, incomplete pages, missing optional fields, fork identities, expired credentials and rate limits.
- [ ] Normalize repositories and pull requests into dataclasses. Repository fields: `external_id`, `full_name`, `web_url`, `clone_url`, `default_branch`. Pull-request fields: `external_id`, `title`, `web_url`, `state`, `source_repo_id`, `source_branch`, `source_oid`, `target_repo_id`, `target_branch`, `merge_oid`, `updated_at`, `merged_at`. States are `open`, `closed`, `merged`; optional API data stays null.
- [ ] Implement `Provider(connection, token, client=None).check()`, `.repositories()`, `.pull_requests(repo_external_id, since)` with injectable httpx transport. Public GitHub uses api.github.com; Enterprise uses configured instance API prefix; GitLab uses api/v4 and URL-encoded project IDs. Follow pagination only within the configured API origin; never forward Authorization to an unapproved host or redirect.

```python
def test_cross_origin_pagination_rejected(github_provider_with_bad_next):
    import pytest
    from app.providers.base import ProviderError
    with pytest.raises(ProviderError):
        github_provider_with_bad_next.repositories()
```

- [ ] Read all open PR/MRs and closed records within the configured coverage window. Preserve source and target repository identity. Handle API rate limits via typed retry information, authentication failures via a separate error type, and absent version/capability fields without fabricating data. Support a configured CA bundle with TLS verification enabled.
- [ ] Run `bash .agent/run.sh .venv/bin/pytest tests/test_providers.py -q`, then commit.

## Task 4: Reliable synchronization and task orchestration

Files: `app/sync.py`, `app/jobs.py`, `app/scheduler.py`, `tests/test_sync.py`, `tests/test_jobs.py`.

- [ ] Test failed fetch preserves previous snapshot, provider failure permits a new Git snapshot, duplicate enqueues do not cause concurrent writes, queue outage retains a durable request, removed branches lose current status, and old job generations cannot overwrite newer state.

```python
def test_failed_sync_preserves_last_success(sync_case):
    old_generation = sync_case.repository.snapshot_generation
    sync_case.run_with_fetch_failure()
    sync_case.reload()
    assert sync_case.repository.snapshot_generation == old_generation
    assert sync_case.repository.sync_error
```

- [ ] Implement a durable job row before Redis enqueue, queue job IDs based on durable IDs, retry with bounded exponential delays, repository-level file locks shared across worker processes, task generation guards, and independent transaction publication for Git and provider data. Store all Git calculations against immutable OIDs.
- [ ] Clone approved HTTPS URLs into ID-based cache paths. Decrypt credentials only during requests; supply Git credentials using a temporary askpass helper/environment, not logged command-line URLs. Treat queue parameters as IDs only. Never log raw HTTP headers, tokens or authenticated Git errors.
- [ ] Recompute main, active-release and same-repository planned-target comparisons when OIDs or relevant configuration change. Persist all branch rows, deleted flags, source candidates and historical PR/MRs. Publish coverage only after a complete page set; a failed API traversal retains the previous coverage and error.
- [ ] Implement `enqueue_sync(session, repository_id, requested_by=None, history_since=None)`, `run_sync_job(job_id)`, dispatcher `tick()` and runnable `python -m app.scheduler`. Dispatcher repairs unqueued/stale durable jobs, honors disabled credentials and next-sync timestamps; RQ workers have scheduler support. Global worker default is two; one repository is always serialized.
- [ ] Run `bash .agent/run.sh .venv/bin/pytest tests/test_sync.py tests/test_jobs.py -q`, then commit.

## Task 5: Complete server-rendered product interface

Files: `app/routes/{__init__,projects,repositories,admin}.py`, `app/templates/` page and fragment templates, `app/static/{app.css,app.js,vendor/jquery.min.js}`, `tests/test_pages.py`, `tests/test_permissions.py`.

- [ ] Add failing route tests for project visibility, 50-item pagination, query restoration, XSS escaping, direct fragment/JSON denial, CSRF, admin-only import/configuration/sync, and permission removal. Existing app session helpers must be used by every route.
- [ ] Build a restrained, polished Chinese interface using regular HTML/CSS/jQuery. Use a sidebar for projects and administration, compact repository rows with expandable branch tables, clear source/target columns, ahead/behind numbers, status labels and independent Git/API timestamps. Branch detail may be a drawer or separate page with a fragment representation.
- [ ] Use server-rendered fragments for Ajax; preserve filters in query strings, per-repository pagination and accessible expand buttons. Empty, importing, stale, error and unknown states must have distinct text. Vendor a pinned official jQuery distribution and license into static assets using a container download.
- [ ] Implement administrator forms for users, project membership, instance connection/test, paginated remote repository selection, import into projects, manual refresh/history backfill, branch role/source/target/active-release configuration. Credentials are never rendered after submission.
- [ ] Implement ordinary member read-only browsing and comparison requests served from stored results; missing comparisons may be computed by the background system without exposing mutation/admin powers. No remote Git write operations.

```python
def test_member_cannot_import(member_client):
    assert member_client.post("/admin/repositories/import").status_code in (403, 404)

def test_private_fragment_is_not_accessible(other_project_client, private_repo):
    response = other_project_client.get(f"/repositories/{private_repo.id}/branches")
    assert response.status_code == 404
```

- [ ] Run `bash .agent/run.sh .venv/bin/pytest tests/test_pages.py tests/test_permissions.py -q`, then commit.

## Task 6: Deployment, browser verification and release readiness

Files: `Dockerfile`, `compose.yaml`, `.env.example`, `.dockerignore`, `README.md`, `app/cli.py` demo command, `tests/test_e2e.py`, integration and scale tests.

- [ ] Configure Web, two RQ workers, one dispatcher, PostgreSQL and Redis with independent persistent volumes, health checks and production secrets. Runtime startup applies migrations as a one-off action, not from every worker. Include a documented `.agent/run.sh` route for containerized Compose administration without running project code on the host.
- [ ] Add an explicit local demo command using generated Git histories and clearly labeled example project data, with a user-chosen or generated password. The production database never receives automatic demo accounts. Bind local preview only to 127.0.0.1.
- [ ] Document installation, secrets, administrator creation, platform token/CA setup, import, optional history backfill, refresh, backups, restore, tests and operational limitations. Document that remote PR/MR records and current Git reachability can legitimately disagree.
- [ ] Run the full suite, migration verification against real PostgreSQL, queue/recovery checks against Redis, and a 100-repository/50-branch fixture exercise. Record timings and sample size without extrapolating to large real repositories.
- [ ] Run browser checks for login, a permission-restricted user, repository expansion/filtering, branch evidence and administrator forms. Capture and inspect a screenshot. Fix confirmed issues with regression tests and re-run affected checks.
- [ ] Obtain independent final spec review and then code-quality review; resolve actionable findings. Run `git diff --check` and the final relevant verification, commit the verified result and report the runnable URL, setup requirements and any real-platform checks still requiring user credentials.

## Coverage check

Approved scope maps to tasks: identity and project access (1, 5), Git relation accuracy (2), GitHub/GitLab/self-hosted integration (3), consistency and recovery (4), HTML/jQuery workflow (5), Docker and operating instructions (6). Frontend construction uses no React or Node build. Existing source inspection and runner-only execution rules apply to implementers and reviewers alike.
