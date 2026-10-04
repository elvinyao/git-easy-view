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
    page.locator('form[action="/admin/connections"] button.primary').click()
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


@pytest.mark.parametrize('locale,title', [
    ('zh-CN', '选择本地目录'), ('ja', 'ローカルディレクトリを選択'), ('en', 'Choose local directory'),
])
def test_local_directory_selection_retry_and_batch_import(browser, fixture_url, locale, title):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    login(page, fixture_url, 'demo-admin', 'browser-admin-password')
    page.goto(fixture_url + f'/admin/connections/new?lang={locale}')
    page.locator('[name="name"]').fill('Local workspace ' + locale)
    page.locator('[name="kind"]').select_option('local')
    expect(page.locator('[name="base_url"]')).not_to_be_visible()
    expect(page.locator('[name="token"]')).to_be_disabled()
    root = page.locator('[name="local_path"]').input_value()
    opener = page.locator('[data-directory-open]')
    opener.click()
    dialog = page.locator('[data-directory-dialog]')
    expect(dialog.locator('h2')).to_have_text(title)
    expect(dialog.locator('[data-directory-list] button')).to_have_count(2)
    expect(dialog.locator('[data-directory-parent]')).to_be_disabled()
    dialog.get_by_role('button', name='team', exact=True).click()
    expect(dialog.locator('[data-directory-current]')).to_have_text(root + '/team')
    expect(dialog.locator('[data-directory-list] button')).to_have_count(1)
    dialog.locator('[data-directory-parent]').click()
    expect(dialog.locator('[data-directory-current]')).to_have_text(root)
    page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    expect(opener).to_be_focused()

    page.route('**/admin/local/directories?*', lambda route: route.abort())
    opener.click()
    expect(dialog.locator('[data-directory-status]')).to_have_text(dialog.get_attribute('data-error-label'))
    expect(dialog.locator('[data-directory-select]')).to_be_disabled()
    page.unroute('**/admin/local/directories?*')
    dialog.locator('[data-directory-retry]').click()
    expect(dialog.locator('[data-directory-list] button')).to_have_count(2)
    shot(page, 'local-directory-' + locale)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    shot(page, 'local-directory-' + locale + '-mobile')
    dialog.locator('[data-directory-select]').click()
    expect(page.locator('[name="local_path"]')).to_have_value(root)
    expect(page.locator('[name="local_path"]')).to_be_focused()
    page.locator('[data-source-save]').click()
    expect(page).to_have_url(re.compile('/admin/connections/\\d+/repositories'))
    expect(page.locator('[name="external_ids"]')).to_have_count(2)
    page.locator('select[name="project_id"]').select_option('2')
    page.locator('[data-select-page]').click()
    expect(page.locator('[data-selected-count]')).to_contain_text('2 / 50')
    shot(page, 'local-import-' + locale + '-mobile')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('[data-import-basket] button.primary').click()
    expect(page).to_have_url(re.compile('/projects/2\\?jobs='))
    expect(page.locator('[data-progress-job]')).to_have_count(2)
    expect(page.locator('body')).to_contain_text('local-workspace/team/web')
    assert not errors
    page.close()
