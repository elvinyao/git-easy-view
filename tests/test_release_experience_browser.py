"""User-oriented release acceptance, including a project with 42 repositories."""
import os
import re

import pytest

from test_e2e import browser, fixture_url, login, shot

pytestmark = [
    pytest.mark.skipif(os.environ.get("GEV_E2E") != "1", reason="requires Docker Chromium"),
    pytest.mark.parametrize("fixture_url", [{"repositories": 42, "branches": 6}], indirect=True, scope="module"),
]


@pytest.mark.parametrize("locale", ["zh-CN", "ja", "en"])
def test_guided_release_selection_review_and_next_action(browser, fixture_url, locale):
    from playwright.sync_api import expect
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, fixture_url, "demo-admin", "browser-admin-password")
    page.goto(fixture_url + "/projects/1/releases/new?lang=" + locale)
    wizard = page.locator("[data-release-wizard]")
    expect(wizard).to_be_visible()
    expect(page.locator("[data-repository-choice]")).to_have_count(42)
    page.locator("[data-repository-search]").fill("repository-00")
    for rid in (1, 2, 3):
        page.locator(f'[name="repos"][value="{rid}"]').check()
    expect(page.locator("[data-selected-count]")).to_have_text("3")
    page.locator("[data-step-next]:visible").click()
    expect(page.locator("[data-repository-version]:visible")).to_have_count(3)
    for rid in (1, 2, 3):
        page.locator(f'select[name="r{rid}_branch"]').select_option("release/2026.09")
        page.locator(f'select[name="r{rid}_tag"]').select_option("v2026.09")
        assert len(page.locator(f'[name="r{rid}_oid"]').input_value()) == 40
    page.locator("[data-step-back]:visible").click()
    expect(page.locator('[name="repos"][value="1"]')).to_be_checked()
    page.locator("[data-step-next]:visible").click()
    expect(page.locator('select[name="r1_tag"]')).to_have_value("v2026.09")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    shot(page, "release-wizard-mobile-" + locale)
    page.locator("[data-step-next]:visible").click()
    page.locator('[name="title"]').fill("Usable release · " + locale)
    page.locator('[name="scheduled_date"]').fill("2026-10-10")
    expect(page.locator("[data-review-row]")).to_have_count(3)
    page.set_viewport_size({"width": 1440, "height": 1000})
    shot(page, "release-wizard-review-" + locale)
    page.locator('[name="intent"][value="start"]').click()
    expect(page).to_have_url(re.compile(r"/projects/1/releases/\d+(?:\?.*)?$"))
    expect(page.locator("[data-release-row]")).to_have_count(3)
    page.locator("[data-next-action]").first.click()
    drawer = page.locator("dialog[open]")
    expect(drawer).to_be_visible()
    deploy = drawer.locator('form').filter(has=page.locator('[name="action"][value="deploy"]'))
    expect(deploy).to_be_in_viewport()
    deploy.locator('[type="checkbox"]').check()
    deploy.locator('button[type="submit"]').click()
    expect(page.locator('[data-release-row]').first).to_contain_text({"zh-CN":"已确认上线", "ja":"本番稼働確認済み", "en":"Deployment confirmed"}[locale])
    if page.locator("dialog[open]").count():
        page.keyboard.press("Escape")
    shot(page, "release-workspace-" + locale)
    assert not errors
    page.close()


def test_no_tag_draft_and_server_error_keep_user_work(browser, fixture_url):
    from playwright.sync_api import expect
    page = browser.new_page()
    login(page, fixture_url, "demo-admin", "browser-admin-password")
    page.goto(fixture_url + "/projects/1/releases/new?repos=1")
    expect(page.locator('[data-repository-version]:visible')).to_have_count(1)
    page.locator('select[name="r1_branch"]').select_option('release/2026.09')
    page.locator('[data-step-next]:visible').click()
    page.locator('[name="title"]').fill('Keep my draft')
    expect(page.locator('[name="intent"][value="start"]')).to_be_disabled()
    page.locator('[name="intent"][value="draft"]').click()
    expect(page).to_have_url(re.compile(r"/projects/1/releases/\d+(?:\?.*)?$"))
    page.locator('.release-heading a[href$="/edit"]').click()
    page.locator('select[name="r1_tag"]').evaluate("el => { el.add(new Option('missing-tag', 'missing-tag')); el.value='missing-tag'; el.dispatchEvent(new Event('change', {bubbles:true})); }")
    page.locator('[data-step-next]:visible').click()
    page.locator('[name="intent"][value="draft"]').click()
    expect(page.locator('#form-errors')).to_be_visible()
    expect(page.locator('select[name="r1_tag"]')).to_have_value('missing-tag')
    expect(page.locator('[name="title"]')).to_have_value('Keep my draft')
    expect(page.locator('[name="repos"][value="1"]')).to_be_checked()
    page.close()


def test_keyboard_navigation_and_no_javascript_draft(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page()
    login(page, fixture_url, "demo-admin", "browser-admin-password")
    page.goto(fixture_url + "/projects/1/releases/new?lang=en")
    search = page.locator('[data-repository-search]')
    search.fill('repository-004')
    search.press('Enter')
    expect(page.locator('[data-wizard-step="1"]')).to_be_visible()
    box = page.locator('[name="repos"][value="4"]')
    box.focus()
    page.keyboard.press('Space')
    page.locator('[data-step-next]:visible').focus()
    page.keyboard.press('Enter')
    expect(page.locator('#step-versions')).to_be_focused()
    page.locator('[data-select-search="field-r4_branch"]').fill('release/')
    page.locator('[name="r4_branch"]').select_option('release/2026.09')
    page.locator('[data-step-next]:visible').focus()
    page.keyboard.press('Enter')
    expect(page.locator('#step-review')).to_be_focused()
    page.close()

    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    login(page, fixture_url, "demo-admin", "browser-admin-password")
    page.goto(fixture_url + '/projects/1/releases/new?repos=4&lang=en')
    page.locator('[name="r4_branch"]').select_option('release/2026.09')
    page.locator('[name="title"]').fill('No JavaScript draft')
    page.locator('[name="intent"][value="draft"]').click()
    expect(page).to_have_url(re.compile(r'/projects/1/releases/\d+\?saved=created'))
    expect(page.locator('[data-release-row]')).to_have_count(1)
    context.close()


def test_moved_tag_requires_explicit_tag_reselection(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page()
    login(page, fixture_url, 'demo-admin', 'browser-admin-password')
    original_oid = 'a' * 40

    def recovered_page(route):
        response = route.fetch()
        html = response.text().replace('name="r6_oid" value=""', f'name="r6_oid" value="{original_oid}"')
        html = html.replace('value="v2026.09" data-oid=', 'value="v2026.09" selected data-oid=')
        route.fulfill(response=response, body=html)

    page.route('**/projects/1/releases/new?repos=6', recovered_page)
    page.goto(fixture_url + '/projects/1/releases/new?repos=6')
    row = page.locator('[data-repository-version="6"]')
    expect(row.locator('[name="r6_oid"]')).to_have_value(original_oid)
    expect(row.locator('[data-version-changed]')).to_be_visible()
    row.locator('[name="r6_branch"]').select_option('feature/login')
    expect(row.locator('[name="r6_oid"]')).to_have_value(original_oid)
    expect(row.locator('[data-version-changed]')).to_be_visible()
    row.locator('[name="r6_branch"]').select_option('release/2026.09')
    page.locator('[data-step-next]:visible').click()
    expect(page.locator('[name="intent"][value="start"]')).to_be_disabled()
    page.locator('[data-step-back]:visible').click()
    row.locator('[name="r6_tag"]').select_option('')
    row.locator('[name="r6_tag"]').select_option('v2026.09')
    expect(row.locator('[name="r6_oid"]')).not_to_have_value(original_oid)
    expect(row.locator('[data-version-changed]')).not_to_be_visible()
    page.close()


def test_production_registration_history_and_stale_form(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    login(page, fixture_url, "demo-admin", "browser-admin-password")
    page.goto(fixture_url + '/projects/1/releases?tab=production&lang=ja')
    expect(page.locator('[data-search-row]')).to_have_count(42)
    page.locator('[data-list-search]').fill('repository-004')
    expect(page.locator('[data-search-row]:visible')).to_have_count(1)
    page.locator('[data-search-row]:visible a').click()
    form = page.locator('.production-editor form')
    form.locator('[name="branch"]').select_option('release/2026.09')
    expect(form.locator('[data-version-subject]')).not_to_be_empty()
    form.locator('[type="checkbox"]').check()
    form.locator('[data-select-search="field-branch"]').press('Enter')
    expect(page).to_have_url(fixture_url + '/projects/1/production/4')
    form.locator('button[type="submit"]').click()  # Notes are optional.
    expect(page.locator('.release-success')).to_be_visible()
    expect(page.locator('.production-current h2')).to_have_text('release/2026.09')
    page.locator('.production-editor > summary').click()
    expect(form.locator('[data-version-subject]')).not_to_be_empty()
    form.locator('[name="tag"]').select_option('v2026.09')
    form.locator('[name="current_id"]').evaluate("el => el.value = '0'")
    form.locator('[type="checkbox"]').check()
    form.locator('button[type="submit"]').click()
    expect(page.locator('#form-errors')).to_be_visible()
    expect(form.locator('[name="current_id"]')).to_have_value('0')
    expect(form.locator('[name="tag"]')).to_have_value('v2026.09')
    expect(page.locator('.production-history-entry')).to_have_count(1)
    page.goto(fixture_url + '/projects/1/production/4')
    page.locator('.production-editor > summary').click()
    form.locator('[name="tag"]').select_option('v2026.09')
    form.locator('[type="checkbox"]').check()
    form.locator('button[type="submit"]').click()
    expect(page.locator('.production-current h2')).to_have_text('v2026.09')
    history = page.locator('details.panel').filter(has=page.locator('.production-history-entry'))
    history.locator(':scope > summary').click()
    previous = page.locator('.production-history-entry').last
    previous.locator('summary').click()
    previous.locator('[name="notes"]').fill('Rollback verified in production')
    previous.locator('[type="checkbox"]').check()
    previous.locator('button[type="submit"]').click()
    expect(page.locator('.production-current h2')).to_have_text('release/2026.09')
    expect(page.locator('.production-history-entry')).to_have_count(3)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    shot(page, 'release-production-mobile-ja')
    page.goto(fixture_url + '/projects/1/releases/new')
    expect(page.locator('[data-repository-choice]').filter(has=page.locator('[name="repos"][value="4"]'))).to_contain_text('release/2026.09')
    page.close()


def test_platform_repository_picker_retry_and_binding(browser, fixture_url):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    login(page, fixture_url, 'demo-admin', 'browser-admin-password')
    page.goto(fixture_url + '/admin/connections/new?lang=en')
    page.locator('[name="name"]').fill('Release metadata lookup')
    page.locator('[name="base_url"]').fill('https://example.invalid')
    page.locator('[data-source-save]').click()
    page.locator('form[action$="/check"] button').click()
    page.goto(fixture_url + '/admin/connections/new?kind=local')
    page.locator('[name="name"]').fill('Release local source')
    page.locator('[data-source-save]').click()
    page.locator('[name="external_ids"]').first.check()
    page.locator('select[name="project_id"]').select_option('1')
    page.locator('[data-import-basket] button.primary').click()
    row = page.locator('[data-overview-repository]').filter(has_text='local-workspace/api')
    expect(row).to_have_count(1)
    rid = row.get_attribute('data-overview-repository')
    page.goto(fixture_url + f'/projects/1/production/{rid}')
    page.locator('.source-settings > summary').click()
    form = page.locator('[data-source-picker]')
    form.locator('[name="connection_id"]').select_option(label='Release metadata lookup')
    form.locator('[data-load-repositories]').click()
    expect(form.locator('[data-source-repository] option')).to_have_count(57)
    form.locator('[data-source-search]').fill('repository-003')
    form.locator('[data-source-repository]').select_option('3')
    expect(form.locator('[name="external_id"]')).to_have_value('3')
    form.locator('[data-source-search]').press('Enter')
    expect(page).to_have_url(fixture_url + f'/projects/1/production/{rid}')
    expect(form.locator('[data-source-repository]')).to_have_value('3')
    page.route('**/source/repositories?*', lambda route: route.fulfill(status=503, body='unavailable'))
    form.locator('[data-load-repositories]').click()
    expect(form.locator('[data-source-status]')).to_contain_text('Try again')
    expect(form.locator('[data-source-repository]')).to_have_value('3')
    expect(form.locator('[name="external_id"]')).to_have_value('3')
    page.unroute('**/source/repositories?*')
    form.locator('[data-load-repositories]').click()
    expect(form.locator('[data-source-status]')).to_contain_text('search')
    expect(form.locator('[data-source-repository]')).to_have_value('3')
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    shot(page, 'release-platform-picker-mobile-en')
    form.locator('button[type="submit"]').click()
    expect(page.locator('.release-success')).to_be_visible()
    expect(page.locator('.source-settings')).to_contain_text('example/repository-003')
    assert not errors
    page.close()
