"""Real browser coverage for recovery, import selection, and mobile accounts."""

import os
import re

import pytest

from test_e2e import browser, fixture_url, login, shot

pytestmark = pytest.mark.skipif(os.environ.get("GEV_E2E") != "1", reason="requires Docker Chromium")


def test_login_returns_to_settings_and_mobile_logout(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(fixture_url + '/admin/repositories/1?tab=configuration')
    expect(page).to_have_url(re.compile('/login\\?next='))
    page.locator('.language-picker select').select_option('ja')
    page.locator('.language-picker button').click()
    page.locator('[name="username"]').fill('demo-admin')
    page.locator('[name="password"]').fill('browser-admin-password')
    page.locator('form[method="post"][action="/login"] button[type="submit"]').click()
    expect(page).to_have_url(fixture_url + '/admin/repositories/1?tab=configuration')
    expect(page.locator('html')).to_have_attribute('lang', 'ja')
    expect(page.locator('.language-picker')).to_have_count(0)
    footer = page.locator('.sidebar-footer.authenticated')
    expect(footer).to_contain_text('demo-admin')
    expect(footer.locator('button')).to_be_visible()
    footer.locator('strong').evaluate("el => el.textContent = 'long-account-'.repeat(12)")
    shot(page, 'admin-account-ja-mobile')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate("Array.from(document.querySelectorAll('body *')).filter(el => el.getBoundingClientRect().right > innerWidth).map(el => [el.tagName, el.className, el.getBoundingClientRect().width]).slice(0, 15)")
    footer.locator('button').click()
    expect(page).to_have_url(fixture_url + '/login')
    page.close()


def test_connection_error_preserves_inputs_without_token(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page()
    login(page, fixture_url, 'demo-admin', 'browser-admin-password')
    page.goto(fixture_url + '/admin/connections/new')
    page.locator('[name="name"]').fill('Keep connection name')
    page.locator('[name="base_url"]').fill('http://insecure.test')
    page.locator('[name="token"]').fill('do-not-reflect-this-token')
    page.locator('form[action="/admin/connections"] button').click()
    expect(page.locator('#form-errors')).to_be_focused()
    expect(page.locator('[name="name"]')).to_have_value('Keep connection name')
    expect(page.locator('[name="base_url"]')).to_have_value('http://insecure.test')
    expect(page.locator('[name="base_url"]')).to_have_attribute('aria-invalid', 'true')
    expect(page.locator('[name="token"]')).to_have_value('')
    assert 'do-not-reflect-this-token' not in page.content()
    page.locator('#form-errors a').click()
    expect(page.locator('[name="base_url"]')).to_be_focused()
    shot(page, 'admin-connection-recovery')
    page.close()


def test_import_selection_survives_paging_search_and_clears_on_success(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page()
    login(page, fixture_url, 'demo-admin', 'browser-admin-password')
    page.goto(fixture_url + '/admin/connections/1')
    page.locator('form[action="/admin/connections/1/check"] button').click()
    page.goto(fixture_url + '/admin/connections/1/repositories')
    form = page.locator('[data-import-basket]')
    form.locator('[name="project_id"]').select_option('2')
    form.locator('[name="external_ids"][value="1"]').check()
    page.locator('[data-import-pagination] a').last.click()
    expect(form.locator('[name="project_id"]')).to_have_value('2')
    form.locator('[name="external_ids"][value="51"]').check()
    expect(form.locator('[data-selected-count]')).to_contain_text('2 / 50')
    page.reload()
    expect(form.locator('[name="external_ids"][value="51"]')).to_be_checked()
    page.locator('.import-search [name="q"]').fill('repository-001')
    page.locator('.import-search button').click()
    expect(form.locator('[name="external_ids"]')).to_have_count(1)
    expect(form.locator('[name="external_ids"][value="1"]')).to_be_checked()
    expect(form.locator('[data-selected-count]')).to_contain_text('2 / 50')
    expect(form.locator('.badge')).to_contain_text('已导入')
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    shot(page, 'admin-import-basket-mobile')
    form.locator('button.primary').click()
    expect(page).to_have_url(re.compile('/projects/2\\?jobs='))
    expect(page.locator('[data-progress-job]')).to_have_count(2)
    assert page.evaluate("sessionStorage.getItem('gev-import:1')") is None
    page.go_back()
    expect(page.locator('[data-selected-count]')).to_contain_text('0 / 50')
    expect(page.locator('[name="external_ids"]:checked')).to_have_count(0)
    page.close()
