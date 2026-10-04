"""Opt in: GEV_E2E=1 in the Docker browser service."""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("GEV_E2E") != "1", reason="requires Docker Chromium")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def fixture_url(tmp_path_factory, request):
    from app.config import Settings
    from app.db import make_engine
    from app.demo import create_demo
    from app.models import Base
    root = tmp_path_factory.mktemp("browser")
    settings = Settings(production=False, database_url=f"sqlite:///{root}/e2e.db", cache_root=str(root / "git"), cookie_secure=False)
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    fixture_size = getattr(request, "param", {})
    create_demo(settings, admin_password="browser-admin-password", member_password="browser-member-password",
                repositories=fixture_size.get("repositories", 12), branches=fixture_size.get("branches", 55))
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.models import Branch, GitTag
    with Session(engine) as session:
        for branch in session.scalars(select(Branch).where(Branch.name == "release/2026.09")):
            session.add(GitTag(repository_id=branch.repository_id, name="v2026.09", object_oid=branch.head_oid,
                               commit_oid=branch.head_oid, commit_time=branch.commit_time, subject="Browser release fixture"))
        session.commit()
    engine.dispose()
    local_root = root / "local-workspace"
    for name in ("api", "team/web"):
        source = local_root / name
        source.mkdir(parents=True)
        for command in (
            ["init", "-b", "main"], ["config", "user.name", "Browser fixture"],
            ["config", "user.email", "browser@example.test"],
            ["commit", "--allow-empty", "-m", "Local fixture"],
            ["branch", "feature/local"],
        ):
            subprocess.run(["git", "-C", str(source), *command], check=True, capture_output=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, GEV_DATABASE_URL=settings.database_url, GEV_CACHE_ROOT=settings.cache_root,
               GEV_LOCAL_GIT_ROOT=str(local_root), GEV_LOCAL_GIT_DIRECTORY="/example/workspace",
               GEV_PRODUCTION="false", GEV_COOKIE_SECURE="false")
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "tests.e2e_server:application", "--factory", "--host", "127.0.0.1", "--port", str(port)], env=env, stdout=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/login").status_code == 200: break
            except httpx.ConnectError: pass
            time.sleep(.1)
        else: raise AssertionError("Fixture server failed to start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


def login(page, url, username, password):
    page.goto(url + "/login")
    page.locator('[name="username"]').fill(username)
    page.locator('[name="password"]').fill(password)
    page.locator('form[method="post"][action="/login"] button[type="submit"]').click()
    page.wait_for_url(url + "/")


def shot(page, name):
    root = Path("runtime/screenshots")
    root.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(root / f"{name}.png"), full_page=True)
    page.screenshot(path=str(root / f"{name}-viewport.png"), full_page=False)


def test_compose_public_demo_screenshots(browser):
    access = json.loads(Path("runtime/demo-access.json").read_text())
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
    assert page.goto("http://web:8000/").status == 200
    assert "匿名访问" in page.locator(".sidebar-footer").inner_text()
    assert page.locator('.sidebar a[href="/admin"]').count() == 0
    shot(page, "01-public-projects")
    page.goto("http://web:8000/projects/1")
    page.locator('[data-overview-repository]').first.wait_for()
    assert page.locator("[data-summary-branch-id]").count() == 165
    shot(page, "project-branches-public-zh-CN")
    page.locator('.overview-density a[aria-current="false"]').click()
    assert page.locator("[data-summary-branch-id]").count() == 165
    shot(page, "project-branches-public-compact-zh-CN")
    page.locator('.repository-branches > summary').first.click()
    page.locator('.expand').first.click()
    page.locator('[data-branch-id]').first.wait_for()
    shot(page, "02-project-expanded")
    page.goto("http://web:8000/projects/1?q=login&expanded=1")
    page.locator('a').filter(has_text="feature/login").first.click()
    shot(page, "03-branch-evidence")
    assert page.goto("http://web:8000/projects/2").status == 200
    shot(page, "04-public-empty-project")
    page.close()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
    login(page, "http://web:8000", **access["admin"])
    page.goto("http://web:8000/admin")
    shot(page, "05-admin")
    page.goto("http://web:8000/repositories/1/branches?lang=ja")
    page.locator(".git-workspace").wait_for()
    assert page.locator(".git-log-row").count() == 3
    target = page.locator(".git-log-row").nth(1).get_attribute("data-commit")
    page.locator(".git-commit-link").nth(1).click()
    from playwright.sync_api import expect
    expect(page).to_have_url(re.compile(f"commit={target}"))
    shot(page, "git-workspace-live-ja")
    page.close()


def test_browser_permissions_filters_url_and_forms(browser, fixture_url):
    from playwright.sync_api import expect
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, fixture_url, "demo-member", "browser-member-password")
    expect(page.locator("body")).to_contain_text("管理员专属项目")
    assert page.goto(fixture_url + "/projects/2").status == 200
    assert page.goto(fixture_url + "/admin").status == 403
    page.goto(fixture_url + "/projects/1")
    page.locator('.expand').first.click()
    slot = page.locator("#branches-1")
    expect(slot.locator('[data-branch-id]')).to_have_count(50)
    slot.locator('.branch-page[data-page="2"]').click()
    expect(slot.locator('[data-branch-id]')).to_have_count(5)
    assert "bp_1=2" in page.url and "expanded=1" in page.url
    saved = page.url
    page.reload()
    expect(slot.locator('[data-branch-id]')).to_have_count(5)
    page.locator('.filters [name="q"]').fill("login")
    page.get_by_role("button", name="应用筛选").click()
    expect(page.locator("#branches-1")).to_contain_text("feature/login")
    page.reload()
    expect(page.locator("#branches-1")).to_contain_text("feature/login")
    page.goto(saved)
    expect(page.locator("#branches-1 [data-branch-id]")).to_have_count(5)
    page.goto(fixture_url + "/projects/1?q=login&expanded=1")
    page.locator('a').filter(has_text="feature/login").first.click()
    expect(page.locator("body")).to_contain_text("来源")
    expect(page.locator("body")).to_contain_text("示例 PR")
    shot(page, "06-fixture-branch")
    page.close()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
    login(page, fixture_url, "demo-admin", "browser-admin-password")
    page.goto(fixture_url + "/admin")
    form = page.locator('form[action="/admin/projects"]')
    form.locator('[name="name"]').fill("浏览器导入验收")
    form.locator('[name="slug"]').fill("browser-import")
    form.get_by_role("button").click()
    page.goto(fixture_url + "/admin/connections/new")
    page.locator('[name="name"]').fill("EXAMPLE fixture import")
    page.locator('[name="base_url"]').fill("https://example.invalid")
    page.get_by_role("button", name="保存连接").click()
    page.get_by_role("button", name="检查身份与平台能力").click()
    page.get_by_role("link", name="选择仓库并导入 →").click()
    expect(page.locator('[name="external_ids"]')).to_have_count(50)
    page.locator('[name="external_ids"][value="20"]').check()
    page.locator('select[name="project_id"]').select_option(label="浏览器导入验收")
    shot(page, "07-fixture-import")
    page.get_by_role("button", name="导入选中仓库").click()
    expect(page.locator("body")).to_contain_text("repository-020")
    page.goto(fixture_url + "/admin/repositories/1")
    page.locator('[name="sync_interval_seconds"]').fill("600")
    page.get_by_role("button", name="保存并安排分析").click()
    expect(page.locator('[name="sync_interval_seconds"]')).to_have_value("600")
    shot(page, "08-fixture-config")
    assert not errors
    page.close()


@pytest.mark.parametrize(
    "locale,log,branch_tab,empty_files",
    [
        ("zh-CN", "提交历史", "分支关系", "此提交没有文件变更。"),
        ("ja", "コミット履歴", "ブランチの関係", "このコミットにはファイルの変更がありません。"),
        ("en", "Log", "Branch relations", "This commit has no file changes."),
    ],
)
def test_git_workspace_selection_history_filters_and_mobile(browser, fixture_url, locale, log, branch_tab, empty_files):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, fixture_url, "demo-member", "browser-member-password")
    page.goto(fixture_url + f"/repositories/1/branches?lang={locale}")
    expect(page.locator(".git-workspace")).to_be_visible()
    expect(page.locator(".git-log h2")).to_contain_text(log)
    expect(page.locator(".git-branch-tree [data-branch-id]")).to_have_count(50)
    expect(page.locator(".git-log-row")).to_have_count(3)
    expect(page.locator(".git-graph").first).to_be_visible()
    assert page.locator(".git-graph path").count() >= 3

    main = page.locator('.git-branch-link').filter(has=page.locator('.git-branch-name').filter(has_text=re.compile(r'^main$')))
    main_id = main.get_attribute("data-branch-id")
    main.click()
    expect(page.locator(".git-log-scope")).to_have_text("main")
    expect(page.locator(".git-log-row")).to_have_count(2)
    expect(main).to_have_attribute("aria-current", "true")
    assert f"branch_id={main_id}" in page.url
    assert f"branch_id={main_id}" in page.locator('.git-tree-pagination a').get_attribute('href')
    assert f"branch_id={main_id}" in page.locator('.git-toolbar-filters .git-icon-button').get_attribute('href')
    page.locator(".git-all-branches").click()
    expect(page.locator(".git-log-row")).to_have_count(3)
    page.go_back()
    expect(page.locator(".git-log-scope")).to_have_text("main")
    page.reload()
    expect(page.locator(".git-log-row")).to_have_count(2)
    page.locator(".git-all-branches").click()
    expect(page.locator(".git-log-row")).to_have_count(3)
    selected = page.locator(".git-log-row").last.get_attribute("data-commit")
    page.locator(".git-commit-link").last.click()
    expect(page).to_have_url(re.compile(f"commit={selected}"))
    expect(page.locator(".git-log-row.selected")).to_have_attribute("data-commit", selected)
    expect(page.locator(".git-file-list")).to_contain_text(empty_files)

    page.locator('#git-filters [name="q"]').fill("login")
    page.locator('#git-filters [name="q"]').press("Enter")
    expect(page.locator(".git-branch-tree [data-branch-id]")).to_have_count(1)
    page.locator(".git-branch-tree [data-branch-id]").click()
    expect(page.locator(".git-log-scope")).to_have_text("feature/login")
    expect(page.get_by_role("tab", name=branch_tab, exact=True)).to_have_attribute("aria-selected", "true")
    page.get_by_role("tab", name=branch_tab, exact=True).press("ArrowLeft")
    expect(page.locator("#git-commit-tab")).to_have_attribute("aria-selected", "true")
    page.locator("#git-commit-tab").press("ArrowRight")
    expect(page.locator("#git-branch-tab")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#git-branch-details")).to_contain_text("main")
    expect(page.locator(".git-comparison-counts strong").first).to_have_text("1")
    assert "q=login" in page.url
    selected_branch = page.locator(".git-branch-tree [data-branch-id]").get_attribute("data-branch-id")
    shot(page, f"git-workspace-{locale}-desktop")

    # The saved language survives AJAX selections and reload without a picker.
    expect(page.locator(".language-picker")).to_have_count(0)
    page.reload()
    expect(page.locator("html")).to_have_attribute("lang", locale)
    assert "q=login" in page.url and f"branch_id={selected_branch}" in page.url
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".language-picker")).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    expect(page.locator("#git-load-status")).to_be_visible()
    page.route("**/repositories/1/log?**", lambda route: route.abort())
    page.locator(".git-all-branches").click()
    expect(page.locator("#git-load-status a")).to_be_visible()
    page.unroute("**/repositories/1/log?**")
    shot(page, f"git-workspace-{locale}-mobile")
    assert not errors
    page.close()


@pytest.mark.parametrize("locale,headline", [
    ("zh-CN", "仓库分支总览"), ("ja", "リポジトリのブランチ一覧"), ("en", "Repository branch overview"),
])
def test_project_overview_twelve_repositories_and_mobile(browser, fixture_url, locale, headline):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1440, "height": 1200})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(fixture_url + f"/projects/1?lang={locale}")
    expect(page.get_by_role("heading", name=headline, exact=True)).to_be_visible()
    expect(page.locator("[data-overview-repository]")).to_have_count(12)
    expect(page.locator("[data-repository-summary]")).to_have_count(12)
    expect(page.locator(".overview-repo-nav a")).to_have_count(12)
    expect(page.locator("[data-summary-branch-id]")).to_have_count(12 * 55)
    expect(page.locator('form[method="post"]')).to_have_count(0)
    expect(page.locator(".overview-main .overview-subject").first).to_have_text("Example main advance")
    expect(page.locator(".overview-releases .overview-subject").first).to_have_text("Example root")
    expect(page.locator(".overview-features .overview-branch-name").first).to_have_text("feature/login")
    assert page.locator(".overview-releases .overview-branch").count() == 12
    assert page.locator(".overview-features .overview-branch").count() == 12 * 53
    # Exercise wrapping with a legal long ref/message without altering shared data.
    page.locator('.summary-branch-name').first.evaluate("el => el.textContent = 'release/' + 'long-reference-'.repeat(18)")
    page.locator('.overview-branch-name').first.evaluate("el => el.textContent = 'release/' + 'long-reference-'.repeat(18)")
    page.locator('.overview-subject').first.evaluate("el => el.textContent = 'Long commit summary '.repeat(40)")
    shot(page, f"project-overview-{locale}-12-repositories")
    assert page.locator(".overview-branch-scroll").first.evaluate("el => el.scrollHeight <= el.clientHeight + 1")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for width in (720, 1024):
        page.set_viewport_size({"width": width, "height": 1200})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
    page.set_viewport_size({"width": 1440, "height": 1200})

    page.locator('.overview-density a[aria-current="false"]').click()
    assert "density=compact" in page.url
    expect(page.locator("[data-summary-branch-id]")).to_have_count(12 * 55)
    expect(page.locator('.repository-branches[open]')).to_have_count(0)
    page.locator('.repository-branches > summary').first.click()
    page.reload()
    expect(page.locator('.repository-branches[open]')).to_have_count(1)
    shot(page, f"project-branches-{locale}-12-repositories-compact")
    page.locator('.summary-count[href="#features-1"]').click()
    page.reload()
    expect(page.locator('[data-overview-repository="1"] .repository-branches')).to_have_attribute('open', '')
    last = page.locator('[data-overview-repository="1"] .overview-features .overview-branch').last
    last.scroll_into_view_if_needed()
    expect(last).to_be_visible()
    assert page.locator(".overview-branch-scroll").first.evaluate("el => el.scrollHeight <= el.clientHeight + 1")
    page.locator('.overview-repo-nav a[href="#repository-12"]').click()
    assert page.url.endswith("#repository-12")
    expect(page.locator('[data-overview-repository="12"] .overview-repo-header')).to_be_in_viewport()
    locator_bottom = page.locator('.overview-repo-nav').evaluate('el => el.getBoundingClientRect().bottom')
    heading_top = page.locator('[data-overview-repository="12"] .overview-repo-header').evaluate('el => el.getBoundingClientRect().top')
    assert heading_top >= locator_bottom
    first_hash = page.locator(".overview-main .overview-hash").first
    target = first_hash.get_attribute("title")
    first_hash.click()
    expect(page.locator("#project-inspector")).to_be_visible()
    expect(page.locator("#project-inspector [data-copy]")).to_have_attribute("data-copy", target)
    page.go_back()
    page.goto(fixture_url + f"/projects/1?lang={locale}&density=compact")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".language-picker")).to_have_count(0)
    expect(page.locator("[data-repository-summary]").first).to_be_visible()
    expect(page.locator(".mobile-login")).to_be_visible()
    shot(page, f"project-overview-{locale}-mobile")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), page.evaluate("Array.from(document.querySelectorAll('body *')).filter(el => el.getBoundingClientRect().right > innerWidth).map(el => [el.tagName, el.className, el.getBoundingClientRect().width]).slice(0, 20)")
    page.locator(".overview-sync summary").first.click()
    expect(page.locator(".overview-sync-details").first).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(page, f"project-overview-{locale}-mobile")
    assert not errors
    page.close()


def test_delayed_fragment_cannot_overwrite_new_selection(browser, fixture_url):
    from playwright.sync_api import expect
    page = browser.new_page(locale="zh-CN")
    login(page, fixture_url, "demo-member", "browser-member-password")
    page.goto(fixture_url + "/projects/1?expanded=1")
    expect(page.locator("#branches-1 [data-branch-id]")).to_have_count(50)
    # Simulate a transport whose aborted callback arrives late. Real DOM handlers
    # and Deferred ownership run in Chromium; the last response must own the slot.
    page.evaluate("""() => {
      window.pending = [];
      jQuery.ajax = function () {const d = jQuery.Deferred(); const r=d.promise(); r.abort=()=>{}; pending.push(d); return r;};
      jQuery('#branches-1').append('<button class="branch-page" data-page="2">old</button><button class="branch-page" data-page="3">new</button>');
      jQuery('#branches-1 .branch-page[data-page="2"]').last().click();
      jQuery('#branches-1 .branch-page[data-page="3"]').click();
      pending[1].resolve('<p id="newest">Newest response</p>');
      pending[0].resolve('<p id="obsolete">Obsolete response</p>');
    }""")
    expect(page.locator("#newest")).to_be_visible()
    expect(page.locator("#obsolete")).to_have_count(0)
    expect(page.locator("#branches-1")).not_to_have_attribute("aria-busy", "true")
    assert "bp_1=3" in page.url
    page.close()


def test_project_drawer_history_focus_retry_and_response_ownership(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto(fixture_url + "/projects/1?q=login")
    link = page.locator(".overview-features .overview-branch-name").first
    link.click()
    drawer = page.locator("#project-inspector")
    expect(drawer).to_be_visible()
    expect(drawer.locator(".project-inspector-title")).to_have_text("feature/login")
    expect(drawer.locator(".git-target-name")).to_have_text("main")
    assert "/projects/1?" in page.url and "q=login" in page.url and "inspect_branch=" in page.url
    page.keyboard.press("Escape")
    expect(drawer).not_to_be_visible()
    expect(link).to_be_focused()
    assert "inspect_branch=" not in page.url and "q=login" in page.url
    link.click()
    page.go_back()
    expect(drawer).not_to_be_visible()
    page.go_forward()
    expect(drawer).to_be_visible()
    page.reload()
    expect(drawer.locator(".project-inspector-title")).to_have_text("feature/login")
    page.keyboard.press("Escape")
    page.route("**/repositories/*/inspect?**", lambda route: route.abort())
    link.click()
    expect(drawer.locator(".inspector-retry")).to_be_visible()
    page.unroute("**/repositories/*/inspect?**")
    drawer.locator(".inspector-retry").click()
    expect(drawer.locator(".project-inspector-title")).to_have_text("feature/login")
    shot(page, "project-inspector-desktop")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    shot(page, "project-inspector-mobile")
    page.keyboard.press("Escape")
    page.evaluate("""() => {
      window.pending = [];
      jQuery.ajax = function () {const d=jQuery.Deferred(); const r=d.promise(); r.abort=()=>{}; pending.push(d); return r;};
      jQuery('[data-inspect-url]').eq(0).click();
      jQuery('[data-inspect-url]').eq(1).click();
      pending[1].resolve('<p id="new-inspection">Newest detail</p>');
      pending[0].resolve('<p id="old-inspection">Obsolete detail</p>');
    }""")
    expect(drawer.locator("#new-inspection")).to_be_visible()
    expect(drawer.locator("#old-inspection")).to_have_count(0)
    expect(drawer.locator("#project-inspector-body")).not_to_have_attribute("aria-busy", "true")
    page.close()


def test_project_collapsed_legacy_state_survives_view_refresh(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page()
    page.goto(fixture_url + '/projects/1?density=compact&expanded=1')
    details = page.locator('[data-overview-repository="1"] .repository-branches')
    expect(details).to_have_attribute('open', '')
    details.locator('summary').first.click()
    page.locator('.overview-heading-actions > a.button').click()
    expect(details).not_to_have_attribute('open', '')
    page.reload()
    expect(details).not_to_have_attribute('open', '')
    page.close()


def test_git_workspace_ignores_obsolete_selection_responses(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page()
    login(page, fixture_url, "demo-member", "browser-member-password")
    page.goto(fixture_url + "/repositories/1/branches")
    page.evaluate("""() => {
      window.pending = [];
      jQuery.ajax = function () {const d = jQuery.Deferred(); const r = d.promise(); r.abort = () => {}; pending.push(d); return r;};
      const links = jQuery('.git-branch-link[data-branch-id]');
      links.eq(0).click();
      links.eq(1).click();
      window.latestBranch = String(links.eq(1).data('branch-id'));
      pending[1].resolve('<section id="latest-log">Latest selection</section>');
      pending[0].resolve('<section id="obsolete-log">Obsolete selection</section>');
    }""")
    expect(page.locator("#latest-log")).to_be_visible()
    expect(page.locator("#obsolete-log")).to_have_count(0)
    expect(page.locator("#git-content")).not_to_have_attribute("aria-busy", "true")
    assert f"branch_id={page.evaluate('window.latestBranch')}" in page.url
    page.close()


@pytest.mark.parametrize(
    "locale,overview,retry,pending",
    [
        ("zh-CN", "项目概览", "重试", "请求已保存，等待队列恢复。"),
        ("ja", "プロジェクト一覧", "再試行", "リクエストを保存しました。キューの復旧を待っています。"),
        ("en", "Project overview", "Retry", "Request saved. Waiting for the queue to recover."),
    ],
)
def test_login_language_persists_for_pages_fragments_notifications_and_mobile(browser, fixture_url, locale, overview, retry, pending):
    from playwright.sync_api import expect

    page = browser.new_page(locale="zh-CN", viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(fixture_url + "/login")
    page.set_viewport_size({"width": 390, "height": 844})
    picker = page.locator(".language-picker")
    expect(picker).to_be_visible()
    picker.locator("select").select_option(locale)
    picker.locator("button").click()
    expect(page.locator("html")).to_have_attribute("lang", locale)
    expect(picker.locator("select")).to_have_value(locale)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(page, f"login-language-{locale}-mobile")
    page.locator(".login-browse").click()
    expect(page.locator("html")).to_have_attribute("lang", locale)
    expect(page.locator(".language-picker")).to_have_count(0)
    expect(page.locator(".mobile-login")).to_be_visible()
    page.locator(".mobile-login").click()
    expect(page.locator("html")).to_have_attribute("lang", locale)
    expect(picker).to_be_visible()
    page.set_viewport_size({"width": 1440, "height": 1000})
    login(page, fixture_url, "demo-member", "browser-member-password")
    expect(page.get_by_role("heading", name=overview, exact=True)).to_be_visible()
    expect(page.locator(".language-picker")).to_have_count(0)

    page.goto(fixture_url + "/projects/1?q=login&expanded=1")
    slot = page.locator("#branches-1")
    expect(slot).to_contain_text("feature/login")
    page.reload()
    expect(page.locator("html")).to_have_attribute("lang", locale)
    expect(page.locator(".language-picker")).to_have_count(0)
    expect(slot).to_contain_text("feature/login")
    assert "q=login" in page.url and "expanded=1" in page.url
    page.locator('a').filter(has_text="feature/login").first.click()
    page.locator('#project-inspector .project-inspector-actions a').first.click()
    branch_url = page.url
    page.route("**/jobs/*", lambda route: route.fulfill(
        json={"status": "queued", "queue_pending": True},
    ))
    page.goto(branch_url + ("&" if "?" in branch_url else "?") + "job_id=123")
    expect(page.locator(".job-status")).to_have_text(pending)
    shot(page, f"i18n-{locale}-branch")

    page.route("**/repositories/1/branches?**", lambda route: route.fulfill(status=503, body="unavailable"))
    page.goto(fixture_url + "/projects/1?expanded=1")
    expect(slot.locator(".retry-branches")).to_have_text(retry)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".language-picker")).to_have_count(0)
    expect(page.locator("html")).to_have_attribute("lang", locale)
    shot(page, f"i18n-{locale}-mobile")
    assert not errors
    page.close()
