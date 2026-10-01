# Project Overview and Workflow Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make a project with twelve or more repositories easy to scan, while retaining every branch and making evidence, synchronization, and administration understandable.

**Architecture:** Keep FastAPI/Jinja/jQuery, anonymous cached reads, administrator-only mutations, SQLite and the existing single application container. Add a repository summary table above complete branch rows and a progressively enhanced detail drawer. Reuse existing authorized branch/commit readers, carry comparison and project context through links, and poll database job state without initiating remote work from read pages.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, Jinja2, jQuery, CSS, pytest and Chromium/Playwright in Docker.

**Approval:** The user selected “仓库摘要表＋完整分支列表＋详情侧栏” and requested every review improvement be planned and completed on 2026-10-01. Continue execution without an additional plan approval round. Preserve the pending standalone-deployment changes in this checkout; work on the existing feature branch.

## Task 1: Stable comparison and navigation context

**Files:** `app/web.py`, `app/routes/repositories.py`, `app/git_workspace.py`, `app/templates/git_workspace.html`, `app/templates/git_log.html`, `app/templates/branch_fragment.html`, `tests/test_git_workspace.py`, new `tests/test_experience.py`.

- [x] Add regression cases where a feature is contained in an explicitly configured release but diverged from main. Requests carrying `target_id=main.id` must retain main across branch evidence and workspace AJAX responses. Reject targets from other repositories and deleted targets.

```python
response = client.get(f"/repositories/{repo_id}/branches/{feature_id}?target_id={main_id}")
assert response.status_code == 200
assert 'main' in response.text
assert '未全部包含' in response.text
```

- [x] Run the new tests through `bash .agent/run.sh .venv/bin/pytest tests/test_experience.py -q`; confirm the missing workspace behavior fails before implementing it.
- [x] Extract target selection so both branch evidence and workspace use the same validated override. Preserve `target_id` in selection, pagination, full-evidence and query links. A same-OID comparison must be “相同” even when no cached comparison row exists.
- [x] Carry a validated relative project return URL in `return_to`; accept only `/projects/<id>` belonging to the current repository, retain filters and fragment, reject external URLs, control characters and unrelated projects. Render “返回项目” from repository/workspace/evidence pages.
- [x] Add a read-only `/repositories/{repository_id}/inspect` HTML endpoint. Use `branch_id`, optional `target_id`, `commit` and `detail=branch|commit`; validate all identities and commit reachability using existing readers. Return only drawer content with ordinary full-page fallback links.
- [x] Distinguish pending first sync, first-sync failure, genuinely empty repositories, and filtered-out branches in the workspace. Run workspace, overview and permission regressions.

## Task 2: Summary table and complete branch rows

**Files:** `app/project_overview.py`, `app/routes/projects.py`, `app/templates/project.html`, `app/templates/project_commit.html`, new `app/static/project.css`, `app/templates/base.html`, `tests/test_project_overview.py`.

- [x] Test that twelve repositories produce twelve summary rows, every current branch still appears once in the complete list, and deleted branches stay excluded. Include multiple releases, a long name, a missing default, stale data and a failed repository.

```python
assert body.count('data-repository-summary=') == 12
assert body.count('data-summary-branch-id=') == expected_live_branches
assert '当前发布' in body
```

- [x] Run the new overview assertions and observe failures.
- [x] Build summaries from the existing batched snapshots: default tip, active release(s) or latest release, newest feature, total/matching counts, and separate Git/API freshness. Reuse cached commit subjects; do not add per-branch database/provider requests.
- [x] Render columns “仓库 / main或master / 发布分支 / 最新功能分支 / 同步状态”. The summary describes latest tips; role counts link to complete lists. Keep the full list available on the same page; use native collapsible repository sections and explicit expand/collapse all. Preserve explicit `open_repos`/`closed_repos` URL state, legacy `expanded` state and compact links without nested branch scrollbars.
- [x] Convert branch cards to aligned rows containing name, message, relative/absolute time, short hash and explicit baseline relation. Keep original classes/data identities where meaningful. Default role remains visible as context under filtering. Label matched versus total branches.
- [x] Add repository-name filtering separately from branch filtering, a needs-attention filter, and a persistent repository locator. Avoid using “独有提交” as an error: Git relationship and synchronization health have separate visual treatment.
- [x] Render active release as text “当前发布”; explain active-first ordering. Main text 13–14px or larger; hashes/times at least 12px. Wrap long names and allow complete messages in the drawer.
- [x] Run overview/query/permission tests. Keep query counts within existing batched limits.

## Task 3: Accessible detail drawer and clear time/status display

**Files:** new `app/templates/project_inspector.html`, new `app/static/project.js`, `app/templates/project.html`, `app/templates/project_commit.html`, `app/static/project.css`, `app/static/app.js`, `app/static/app.css`, `tests/test_e2e.py`.

- [x] Add browser coverage for branch/hash opening in place, retained baseline, Escape close and restored focus, back/forward state, request failure/retry and late-response ownership.

```python
page.locator('.overview-branch-name').first.click()
expect(page.locator('#project-inspector')).to_be_visible()
page.keyboard.press('Escape')
expect(page.locator('#project-inspector')).not_to_be_visible()
```

- [x] Confirm the interaction tests fail before implementing the drawer.
- [x] Use a native dialog styled as a right drawer, fetched authorized HTML, abort plus response ownership, loading/error/retry feedback, focus restoration and normal-link fallback when JavaScript is unavailable or modifier-clicked. Keep copy buttons and commit/file evidence readable.
- [x] Carry drawer selection in project URL parameters; closing removes only drawer state, preserves filters and scroll. Provide a full-workspace link with a return URL to this project and baseline.
- [x] Display relative time next to a full UTC tooltip/accessibility label. Show “Git … / API …” last successful sync and specific paused/error/pending conditions. Label refresh as updating cached display, with actual sync restricted to admins.
- [x] Keep `#git-load-status` visible on small screens, allow messages to wrap, retain retry and keyboard tab behavior. Keep logged-in account identity/logout reachable on mobile; retain language switching only on login.
- [x] Run Chromium at desktop, 1024px, 720px and 390px; ensure no document horizontal overflow or hidden failure actions.

## Task 4: Import and synchronization progress

**Files:** `app/routes/admin.py`, `app/routes/projects.py`, `app/routes/repositories.py`, new `app/job_progress.py`, new `app/templates/job_progress.html`, new `app/static/job-progress.js`, `app/templates/project.html`, `app/templates/admin_repository.html`, `tests/test_jobs.py`, new `tests/test_job_progress.py`.

- [x] Test queued/running/succeeded/failed/auth_failed/superseded groups; reject job IDs belonging to another repository/project; ensure public progress omits raw error/credential content and never invokes provider calls.
- [x] Confirm failing tests before implementation.
- [x] Return/import relevant job identities and render progress immediately after the redirect. Use bounded same-origin polling for current batch state, explicit stop on terminal state, retry for transient status failures, and refresh affected repository snapshots after completion without reload loops.
- [x] Show per-repository outcomes, last-known data while running, and administrator recovery links. For long-running jobs, stop polling only with an explicit “still running, refresh status” action. A failed task must not be reported as success.
- [x] Run job, permissions and page regressions, then browser-test completion and failure feedback with deterministic responses.

## Task 5: Recoverable forms and efficient repository import

**Files:** `app/routes/admin.py`, `app/main.py`, `app/templates/admin_import.html`, `app/templates/admin_connection.html`, `app/templates/admin_project.html`, `app/templates/admin_repository.html`, `app/templates/admin_branch.html`, `app/templates/admin_user.html`, `app/templates/form_error.html`, new `app/static/admin.js`, new `tests/test_admin_experience.py`.

- [x] Test invalid connection/repository/project/branch/user forms retain nonsecret inputs and field errors, never passwords/tokens; hostile HTML remains escaped. Cover paged import selection, existing imports, filtering and 50-item limit.
- [x] Confirm failures; render validation on the original form with an error summary and field association, preserving submitted nonsecret values. Map errors to their relevant field and focus the summary. Keep unauthorized/CSRF and JSON API error behavior intact.
- [x] Add repository/namespace search, page select/deselect, visible selected count, existing-import labels and a connection-scoped sessionStorage selection basket across pages. Preserve selected project; clear the basket only after successful import, reject more than 50 server-side, and revalidate every submitted remote identity as before.
- [x] Provide contextual recovery actions for HTML 403/404 and provider-list failures, without rendering credentials or raw exception strings. Preserve JSON responses for API consumers.
- [x] Add a clear admin setup path: create project → configure/check connection → select repositories → first sync. Deemphasize membership controls and remove the false implication that membership gates anonymous viewing.
- [x] Run admin/auth/provider/security regressions and browser form/import tests.

## Task 6: Login return path and navigation consistency

**Files:** `app/auth.py`, `app/main.py`, `app/templates/login.html`, `app/templates/language_picker.html`, `app/templates/base.html`, `app/templates/repository.html`, `tests/test_auth.py`, `tests/test_i18n.py`.

- [x] Test admin login returns to a validated local original destination and preserves its query; reject scheme-relative/external/control-character URLs. Language selection retains the intended destination. Anonymous browsing does not require login.
- [x] Implement safe `next` handling, named return links and mobile account/logout access. Keep no language selector on regular pages.
- [x] Run auth/i18n/permissions tests including logout and CSRF.

## Task 7: Three-language copy and documentation

**Files:** `app/locales/messages.json`, `app/i18n.py`, `docs/zh-CN/user-guide.md`, `docs/ja/user-guide.md`, `docs/en/user-guide.md`.

- [x] Translate every new server/client string to Japanese and English, maintaining placeholders and a single vocabulary for comparisons, synchronization and active releases.
- [x] Update all three user guides with summary/full-list/drawer workflows, baseline selection, progress/recovery and anonymous/admin separation. Clarify that release branch state is not deployment state.
- [x] Run `bash .agent/run.sh .venv/bin/pytest tests/test_i18n.py -q` and `bash .agent/run.sh python .agent/check-docs.py`.

## Task 8: Review, verification and local delivery

**Files:** `tests/test_e2e.py`, `tests/e2e_server.py`, this plan; new screenshots under ignored `runtime/screenshots/`.

- [x] Review each task for requirement coverage before code-quality review; repair correctness, accessibility and security findings. Preserve previous deployment edits and existing persistent data.
- [x] Run full Docker pytest and the relevant Docker Chromium suite. Test twelve repositories, 50+ branches, multiple releases, long names, missing/failed/aged states, zh/ja/en, drawer history/focus, async retries, import and validation recovery.
- [x] Build/start the existing local single-container application using the permitted runner; do not recreate or delete volumes. Verify `/health` and inspect project, workspace, drawer and admin flow with the browser.
- [x] Inspect actual desktop/mobile screenshots, fix layout defects, and record measured verification results below. No claim of a measured 10-second human task-completion rate: that requires a real user study.
- [x] Deliver the plan path, running URL, completed behavior and precise test results. Keep changes reviewable; do not publish or mix pending deployment changes into a UI commit.

## Verification record

- Baseline before UI work: 297 passed, 16 skipped (Docker pytest).
- Task 1 red/green: 21 expected failures before implementation; 112 passed across experience/workspace/permissions/pages after implementation. Review reproduced and repaired PR/MR links losing project/baseline context.
- Task 2 red/green: initial summary/filter tests failed as expected; overview regressions cover complete counts, active release ordering, unchanged summary tips under branch filtering, and correct count-link pagination for projects exceeding 50 repositories.
- Task 3 Chromium: drawer behavior failed before JavaScript implementation. Final coverage passes history/reload, Escape/focus restoration, failure/retry, stale-response ownership, immediate-refresh expansion state, long content and mobile error actions.
- Tasks 4–6: backend and browser regressions cover scoped job states, bounded polling, outage/manual retry, BFCache resume, one snapshot refresh after terminal outcomes (including partial sync failure), original-form recovery, secret exclusion, import selection across pages/search/back navigation, tab-local success clearing, and safe login returns from expired form submissions.
- Independent reviews repaired navigation context, repository-anchor positioning, low-contrast evidence labels, large-project pagination, native collapse persistence, mobile account wrapping, import state recovery and partial-sync refresh behavior. No material review findings remain.
- Final default suite: `bash .agent/run.sh .venv/bin/pytest -q --tb=short` — **400 passed, 28 skipped** in 69.07s. Opt-in suites are skipped by default; 25 browser tests were run separately below. The remaining three integration/scale tests were not run for this UI change.
- Final Chromium suite: `bash .agent/run.sh compose --env-file runtime/local.env run --rm -e GEV_E2E=1 -w /workspace browser pytest tests/test_e2e.py tests/test_admin_experience_browser.py tests/test_job_progress_browser.py -q --tb=short` — **25 passed** in 53.14s. Coverage includes 12 repositories × 55 branches, Chinese/Japanese/English, and 1440/1024/720/390px layouts with no document overflow. Both suites report the existing Starlette/httpx deprecation warning.
- Documentation: `bash .agent/run.sh python .agent/check-docs.py` — **15 documents passed**, including local links/headings, synchronized three-language commands and shell syntax. `git diff --check` is clean.
- Local delivery: rebuilt the existing web service with `bash .agent/run.sh compose --env-file runtime/local.env up -d --build web`; retained persistent volumes and previous deployment edits. The app remains one persistent container at http://127.0.0.1:8766/projects/1; `/health` returned `{"status":"ready"}`.
- Visual inspection: inspected the actual running project and drawer, desktop summary and mobile drawer screenshots. Evidence is stored in `runtime/screenshots/project-branches-public-zh-CN-viewport.png`, `runtime/screenshots/project-inspector-desktop-viewport.png` and `runtime/screenshots/project-inspector-mobile-viewport.png`. Readability/layout checks are complete; no human task-completion timing study was performed.
- Delivery preserves anonymous cached reads, administrator-only configuration, SQLite, the existing stack and single-container operation. Changes remain uncommitted on the existing feature branch for review.
