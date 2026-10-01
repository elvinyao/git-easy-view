"""Deterministic browser checks for synchronization progress and recovery."""

import os
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from test_e2e import browser, fixture_url, login

pytestmark = pytest.mark.skipif(os.environ.get("GEV_E2E") != "1", reason="requires Docker Chromium")


def start_sync(page, fixture_url):
    login(page, fixture_url, "demo-admin", "browser-admin-password")
    page.goto(fixture_url + "/admin/connections/1")
    page.locator('form[action="/admin/connections/1/check"] button').click()
    page.goto(fixture_url + "/admin/repositories/1")
    page.locator('form[action="/admin/repositories/1/sync"] button').click()
    page.wait_for_url("**/admin/repositories/1?jobs=*")


def status_batch(url, state):
    ids = [int(value) for value in parse_qs(urlsplit(url).query)["jobs"][0].split(",")]
    return {
        "state": state,
        "terminal": state in ("succeeded", "failed", "auth_failed", "superseded"),
        "successful": state == "succeeded",
        "jobs": [{"job_id": identifier, "status": state, "queue_pending": False} for identifier in ids],
    }


@pytest.mark.parametrize("state,label", [
    ("failed", "同步失败，请由管理员检查连接或重试。"),
    ("auth_failed", "凭据失效，请由管理员恢复平台连接。"),
    ("superseded", "本次请求已被新请求取代，请查看最新同步状态。"),
])
def test_progress_terminal_failure_refreshes_once_and_keeps_recovery(browser, fixture_url, state, label):
    from playwright.sync_api import expect

    page = browser.new_page(locale="zh-CN", viewport={"width": 390, "height": 844})
    calls = []
    documents = []
    page.on("request", lambda request: documents.append(request.url) if request.is_navigation_request() and "jobs=" in request.url else None)

    def respond(route):
        calls.append(route.request.url)
        route.fulfill(json=status_batch(route.request.url, state))

    page.route("**/repositories/1/jobs?*", respond)
    start_sync(page, fixture_url)
    expect(page).to_have_url(re.compile("jobs_refreshed=1"))
    expect(page.locator('[data-job-summary]')).to_have_text(label)
    expect(page.locator('[data-progress-job] [data-job-state]')).to_have_text(
        {"failed": "失败", "auth_failed": "凭据失效", "superseded": "已被新请求取代"}[state]
    )
    if state == "auth_failed":
        expect(page.locator('[data-connection-recovery]')).to_be_visible()
    page.clock.install()
    page.clock.run_for(10000)
    assert len(documents) == 2
    assert len(calls) == 2
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.close()


def test_progress_success_refreshes_snapshot_once(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page(locale="zh-CN")
    documents = []
    page.on("request", lambda request: documents.append(request.url) if request.is_navigation_request() and "jobs=" in request.url else None)
    page.route("**/repositories/1/jobs?*", lambda route: route.fulfill(json=status_batch(route.request.url, "succeeded")))
    start_sync(page, fixture_url)
    expect(page).to_have_url(re.compile("jobs_refreshed=1"))
    expect(page.locator('[data-job-summary]')).to_have_text("同步已完成。")
    page.clock.install()
    page.clock.run_for(10000)
    assert len(documents) == 2
    page.close()


def test_progress_status_outage_has_bounded_retry_and_explicit_recovery(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page(locale="zh-CN")
    # Only the status transport is replaced; real templates, timers and event
    # handlers run in Chromium. Immediate promises make virtual time deterministic.
    page.add_init_script("""
      const originalFetch = window.fetch;
      window.statusCalls = 0;
      window.statusBroken = !new URL(location.href).searchParams.has('jobs_refreshed');
      window.fetch = function (url, options) {
        if (!String(url).includes('/repositories/1/jobs?')) return originalFetch(url, options);
        window.statusCalls += 1;
        if (window.statusBroken) return Promise.reject(new Error('offline'));
        const id = Number(new URL(url).searchParams.get('jobs'));
        return Promise.resolve({ok: true, json: async () => ({state: 'failed', terminal: true,
          successful: false, jobs: [{job_id: id, status: 'failed'}]})});
      };
    """)
    page.clock.install()
    start_sync(page, fixture_url)
    expect(page.locator('[data-job-summary]')).to_contain_text("任务状态暂不可用")
    page.clock.run_for(11000)
    expect(page.locator('[data-job-retry]')).to_be_visible()
    assert page.evaluate("statusCalls") == 3
    page.clock.run_for(20000)
    assert page.evaluate("statusCalls") == 3
    page.evaluate("window.statusBroken = false")
    page.locator('[data-job-retry]').click()
    expect(page).to_have_url(re.compile("jobs_refreshed=1"))
    expect(page.locator('[data-job-summary]')).to_contain_text("同步失败")
    # The refresh starts a new document; it reads the final failure once.
    assert page.evaluate("statusCalls") == 1
    page.close()


def test_progress_long_running_pause_offers_refresh_status(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page(locale="zh-CN")
    page.add_init_script("""
      const originalFetch = window.fetch;
      window.statusCalls = 0;
      window.fetch = function (url, options) {
        if (!String(url).includes('/repositories/1/jobs?')) return originalFetch(url, options);
        window.statusCalls += 1;
        const id = Number(new URL(url).searchParams.get('jobs'));
        return Promise.resolve({ok: true, json: async () => ({state: 'running', terminal: false,
          successful: false, jobs: [{job_id: id, status: 'running'}]})});
      };
    """)
    page.clock.install()
    start_sync(page, fixture_url)
    expect(page.locator('[data-job-summary]')).to_contain_text("正在同步")
    page.clock.run_for(241000)
    expect(page.locator('[data-job-summary]')).to_have_text("同步仍在后台进行。已暂停自动检查，请刷新状态。")
    expect(page.locator('[data-job-retry]')).to_have_text("刷新状态")
    assert page.evaluate("statusCalls") == 120
    page.locator('[data-job-retry]').click()
    expect(page.locator('[data-job-summary]')).to_contain_text("正在同步")
    assert page.evaluate("statusCalls") == 121
    page.close()


def test_progress_resumes_after_browser_restores_page(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page(locale="zh-CN")
    page.add_init_script("""
      const originalFetch = window.fetch;
      window.statusCalls = 0;
      window.fetch = function (url, options) {
        if (!String(url).includes('/repositories/1/jobs?')) return originalFetch(url, options);
        window.statusCalls += 1;
        const id = Number(new URL(url).searchParams.get('jobs'));
        return Promise.resolve({ok: true, json: async () => ({state: 'running', terminal: false,
          successful: false, jobs: [{job_id: id, status: 'running'}]})});
      };
    """)
    page.clock.install()
    start_sync(page, fixture_url)
    expect(page.locator('[data-job-summary]')).to_contain_text("正在同步")
    page.evaluate("""() => {
      window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}));
      window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
    }""")
    page.clock.run_for(3000)
    assert page.evaluate("statusCalls") >= 2
    page.close()
