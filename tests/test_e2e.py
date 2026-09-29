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
def fixture_url(tmp_path_factory):
    from app.config import Settings
    from app.db import make_engine
    from app.demo import create_demo
    from app.models import Base
    root = tmp_path_factory.mktemp("browser")
    settings = Settings(production=False, database_url=f"sqlite:///{root}/e2e.db", cache_root=str(root / "git"), cookie_secure=False)
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    create_demo(settings, admin_password="browser-admin-password", member_password="browser-member-password")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, GEV_DATABASE_URL=settings.database_url, GEV_CACHE_ROOT=settings.cache_root, GEV_PRODUCTION="false", GEV_COOKIE_SECURE="false")
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
    page.locator('button[type="submit"]').click()
    page.wait_for_url(url + "/")


def shot(page, name):
    root = Path("runtime/screenshots")
    root.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(root / f"{name}.png"), full_page=True)
    page.screenshot(path=str(root / f"{name}-viewport.png"), full_page=False)


def test_compose_demo_screenshots(browser):
    access = json.loads(Path("runtime/demo-access.json").read_text())
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    login(page, "http://web:8000", **access["member"])
    shot(page, "01-member-projects")
    page.goto("http://web:8000/projects/1")
    page.locator('.expand').first.click()
    page.locator('[data-branch-id]').first.wait_for()
    shot(page, "02-project-expanded")
    page.goto("http://web:8000/projects/1?q=login&expanded=1")
    page.locator('a').filter(has_text="feature/login").first.click()
    shot(page, "03-branch-evidence")
    assert page.goto("http://web:8000/projects/2").status == 404
    shot(page, "04-member-denied")
    page.close()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    login(page, "http://web:8000", **access["admin"])
    page.goto("http://web:8000/admin")
    shot(page, "05-admin")
    page.close()


def test_browser_permissions_filters_url_and_forms(browser, fixture_url):
    from playwright.sync_api import expect
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, fixture_url, "demo-member", "browser-member-password")
    expect(page.locator("body")).not_to_contain_text("管理员专属项目")
    assert page.goto(fixture_url + "/projects/2").status == 404
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
    page.locator('[name="q"]').fill("login")
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
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
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
    page.locator('[name="project_id"]').select_option(label="浏览器导入验收")
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


def test_delayed_fragment_cannot_overwrite_new_selection(browser, fixture_url):
    from playwright.sync_api import expect
    page = browser.new_page()
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
