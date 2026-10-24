"""End-to-end release workflow in isolated Docker browser fixtures."""
import os
import re

import pytest

from test_e2e import browser, fixture_url, login, shot

pytestmark = pytest.mark.skipif(os.environ.get("GEV_E2E") != "1", reason="requires Docker Chromium")


@pytest.mark.parametrize("locale", ["zh-CN", "ja", "en"])
def test_three_repository_release_and_mobile_drawer(browser, fixture_url, locale):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, fixture_url, "demo-admin", "browser-admin-password")
    page.goto(fixture_url + "/projects/1?lang=" + locale)
    for rid in (1, 2, 3):
        page.locator(f'[form="release-selection"][value="{rid}"]').check()
    page.locator("#release-selection button").click()
    expect(page.locator("[data-repository-version]:visible")).to_have_count(3)
    for rid in (1, 2, 3):
        page.locator(f'[name="r{rid}_branch"]').select_option("release/2026.09")
        page.locator(f'[name="r{rid}_tag"]').select_option("v2026.09")
        assert len(page.locator(f'[name="r{rid}_oid"]').input_value()) == 40
    title = "October 10 · " + locale
    page.locator('[name="title"]').fill(title)
    page.locator('[name="scheduled_date"]').fill("2026-10-10")
    shot(page, "release-plan-form-" + locale)
    page.locator('[name="intent"][value="draft"]').click()
    expect(page).to_have_url(re.compile(r"/projects/1/releases/\d+(?:\?.*)?$"))
    plan_url = page.url.split("?")[0]
    page.locator('[name="action"][value="activate"]').click()
    expect(page.locator(".release-badge.active")).to_have_count(1)
    shot(page, "release-plan-" + locale)

    def complete_row(index):
        row = page.locator("[data-release-row]").nth(index)
        row.locator("[data-next-action]").click()
        drawer = page.locator("dialog[open]")
        deploy = drawer.locator('form[data-operation="deploy"]')
        expect(deploy).to_be_in_viewport()
        deploy.locator('[type="checkbox"]').check()
        deploy.locator('button[type="submit"]').click()
        page.locator("[data-release-row]").nth(index).locator("[data-next-action]").click()
        drawer = page.locator("dialog[open]")
        merge = drawer.locator("form").filter(has=page.locator('[name="action"][value="merge"]'))
        if not merge.is_visible():
            drawer.locator("details").filter(has=merge).locator("summary").click()
        merge.locator('[name="notes"]').fill("Verified merged into main after production deployment")
        merge.locator("button").click()

    complete_row(0)
    expect(page.locator(".release-badge.active")).to_have_count(1)  # 1/3 is not complete.
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("[data-release-row]").first.locator(".row-details").click()
    expect(page.locator("dialog[open]")).to_be_visible()
    shot(page, "release-drawer-mobile-" + locale)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.keyboard.press("Escape")
    page.goto(fixture_url + "/projects/1/releases")
    card = page.locator(".plan-card").filter(has_text=title)
    expect(card.locator("progress")).to_have_attribute("value", "1")
    page.goto(plan_url)
    for index in (1, 2):
        complete_row(index)
    expect(page.locator(".release-heading .release-badge.complete")).to_have_count(1)
    assert not errors
    page.close()

    public = browser.new_page()
    public.goto(plan_url)
    expect(public.locator("[data-release-row]")).to_have_count(3)
    expect(public.locator("[data-next-action]")).to_have_count(0)
    public.locator(".row-details").first.click()
    expect(public.locator("dialog[open]")).to_be_visible()
    expect(public.locator('dialog[open] form[method="post"]')).to_have_count(0)
    public.close()
