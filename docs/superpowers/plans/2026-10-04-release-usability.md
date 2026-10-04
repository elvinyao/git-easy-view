# Release Usability Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for bounded backend implementation and independent spec/code reviews; keep tightly coupled UI integration in the current workspace. Steps use checkboxes for tracking.

**Goal:** Make selecting repositories, planning a release and confirming production understandable without memorizing names or navigating long forms.

**Architecture:** Retain the approved summary/list/drawer arrangement and existing release evidence model. Add a progressively enhanced creation wizard, focused next-action controls and server-side form recovery. Preserve all current uncommitted work and the Docker-only execution policy.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, Jinja, native HTML controls, JavaScript, pytest and Docker Chromium.

## Task 1: Establish behavior tests and form recovery

Files: `tests/test_release_experience.py`, `app/release_forms.py`, `app/routes/releases.py`, `app/main.py`.

- [x] Add failing tests: invalid title/date/tag keeps submitted fields on the creation page; editing and production errors keep the correct immutable version; access/CSRF failures do not expose form values.
- [x] Run `bash .agent/run.sh .venv/bin/pytest tests/test_release_experience.py -q --tb=short`; confirm failures demonstrate the absent recovery behavior.
- [x] Implement a release-specific form error carrying a field key, sanitized submitted values and a recovery renderer using a fresh session after rollback.
- [x] Add creation intent `start` that performs the existing tag validation and pins versions in one transaction, preserving draft default for existing callers.
- [x] Run focused tests and request spec then code review.

## Task 2: Guided selection and version preview

Files: `app/templates/release_form.html`, `release_fields.html`, `app/static/releases.js`, `releases.css`, `tests/test_release_experience_browser.py`.

- [x] Write browser assertions for `data-release-wizard`, searchable repository selection, selected count, forward/back navigation, searchable native version selects, preview and review table. Run with the existing browser image through the runner and confirm red.
- [x] Render all project repositories as compact selectors; build version fields only for selected rows at display time. Submit only selected IDs and keep entered fields when selection changes.
- [x] Add three steps with hidden/inert noncurrent panels, validation on next, live count and summary; preserve a complete no-JS form.
- [x] Replace datalist with search plus select, update fixed-SHA preview and hide advanced MR/target controls by default. Show no-tag and no-match guidance.
- [x] Offer start and draft buttons on review; explain why start is unavailable.
- [x] Verify keyboard/mobile navigation and three-language browser workflows.

## Task 3: Daily release and production workspace

Files: `app/templates/releases.html`, `release_detail.html`, `production.html`, `app/releases.py`, `app/routes/releases.py`, frontend files above.

- [x] Add release/prod navigation, readable counters, repository search and actionable empty states.
- [x] Replace large release cards with comparable table rows, completion counts and next-action buttons opening/focusing the right existing drawer form.
- [x] Simplify initial production confirmation and group history/rollback/MR setup separately.
- [x] Add admin-only explicit platform repository lookup with names, searchable selection and advanced numeric fallback. Keep network reads off normal page rendering.
- [x] Verify selected-record scoping, MR filtering, paused connections and read-only users.

## Task 4: Localization, review and delivery

Files: `app/locales/messages.json`, `docs/{zh-CN,ja,en}/user-guide.md`, browser/server tests.

- [x] Translate every new label, error and browser message into Chinese, Japanese and English; update instructions to match the real steps.
- [x] Run `bash .agent/run.sh .venv/bin/pytest -q --tb=short` and `bash .agent/run.sh python .agent/check-docs.py`.
- [x] Run the release browser suites through `bash .agent/run.sh compose --env-file runtime/local.env run --rm --no-deps --pull never -T -e GEV_E2E=1 -w /workspace browser python -m pytest tests/test_release_experience_browser.py tests/test_releases_browser.py -q --tb=short`.
- [x] Inspect screenshots, obtain independent requirement and code reviews, fix actionable findings and rerun affected checks.
- [x] Deploy with `bash .agent/run.sh deploy`; inspect real project 3, anonymous read/admin routes and persistent container count. Record measured results before marking the goal complete.

## Verification record — 2026-10-04

- The initial browser test failed because no release wizard existed; the previous page expanded all 42 repository forms. Recovery tests and subsequent review regressions were observed failing before their corresponding fixes.
- Full default suite: **494 passed, 42 skipped** (opt-in suites), before the final two narrowly scoped review fixes. Final release/recovery/localization regression run: **78 passed**, including the newly added mismatched-summary test.
- Both complete release browser suites: **11 passed**, covering Chinese/Japanese/English, a 42-repository project, selecting 3 repositories, draft/start/deploy/manual merge through full completion, anonymous access, no-JavaScript draft, keyboard operation, moved tags, production/rollback history, stale forms, and platform lookup retry/binding.
- After consolidating search Enter handling, the affected keyboard/production/source browser cases passed again: **3 passed**. Enter on a search field cannot register production or bind a previously selected repository.
- Documentation check: **15 documents passed**. Diff whitespace check passed. Desktop and 390px screenshots were inspected.
- Independent backend and UI specification and code-quality reviews completed. Fixed all findings: true bounded provider pagination, date overflow recovery, current production in the picker, stale tag pin protection, branch commit summaries, search Enter submission, and mismatched recovered metadata.
- Deployment: `bash .agent/run.sh deploy` succeeded. Live read-only checks confirmed project 3 has **42 repositories**, all three localized release/production views return successfully, creation redirects anonymous visitors to login, health passes, no browser page errors, and no mobile horizontal overflow.
- Compose reports exactly one persistent application service, `git-easy-view-web-1`, running and healthy on `127.0.0.1:8766`. No live release or production records were created by verification.
- Existing workspace changes and configuration were preserved. No host dependency installation, Git commit, push or merge was performed.
