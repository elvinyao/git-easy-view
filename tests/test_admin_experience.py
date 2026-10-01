import re
from urllib.parse import parse_qs, urlsplit

import pytest

from test_auth import csrf_from, login
from test_pages import ProviderFixture, admin_pages, pages, post_form


@pytest.mark.parametrize("path,fields,field,value", [
    ("/admin/connections", dict(name='<script>named</script>', kind="github", base_url="http://insecure.test", token="secret-token"), "base_url", "http://insecure.test"),
    ("/admin/projects", dict(name="Keep project", slug="", description="Keep description"), "slug", "Keep project"),
    ("/admin/projects/{project}", dict(name="", description="Keep description"), "name", "Keep description"),
    ("/admin/repositories/{repo}", dict(sync_interval_seconds="29", release_prefixes="release/retained", feature_prefixes="feature/"), "sync_interval_seconds", "release/retained"),
    ("/admin/branches/{feature}", dict(role="feature", active_release="on"), "active_release", "feature"),
    ("/admin/users", dict(username="retained-user", password="tiny"), "password", "retained-user"),
])
def test_form_errors_restore_original_form(client, admin_pages, path, fields, field, value):
    path = path.format(**admin_pages)
    response = post_form(client, path, **fields)
    assert response.status_code == 400
    assert 'id="form-errors"' in response.text
    assert f'href="#field-{field}"' in response.text
    assert f'id="field-{field}"' in response.text
    assert f'aria-describedby="error-{field}"' in response.text
    assert value in response.text
    assert "secret-token" not in response.text and 'value="tiny"' not in response.text
    assert "<script>named</script>" not in response.text
    if "name" in fields and fields["name"].startswith("<script>"):
        assert "&lt;script&gt;named&lt;/script&gt;" in response.text


def test_import_search_existing_and_selection_controls(app, client, admin_pages):
    app.state.provider_factory = ProviderFixture
    path = f"/admin/connections/{admin_pages['connection']}/repositories"
    response = client.get(path, params={"q": "TEAM/repo-00", "project_id": admin_pages["private"]})
    assert response.status_code == 200
    assert response.text.count('name="external_ids"') == 9
    assert "已导入" in response.text
    assert 'data-import-basket=' in response.text
    assert 'data-select-page' in response.text and 'data-deselect-page' in response.text
    assert re.search(rf'value="{admin_pages["private"]}"\s+selected', response.text)
    second = client.get(path, params={"page": 2})
    assert second.text.count('name="external_ids"') == 6


def test_import_limit_keeps_project_and_selection(app, client, admin_pages):
    app.state.provider_factory = ProviderFixture
    response = post_form(client, "/admin/repositories/import", connection_id=admin_pages["connection"],
                         project_id=admin_pages["private"], external_ids=[str(i) for i in range(1, 52)])
    assert response.status_code == 400
    assert 'data-import-basket=' in response.text
    assert 'id="error-external_ids"' in response.text
    assert re.search(rf'value="{admin_pages["private"]}"\s+selected', response.text)
    assert '"51"' in response.text


def test_provider_list_failure_has_safe_retry_and_connection_links(app, client, admin_pages):
    class Failed(ProviderFixture):
        def repositories(self):
            raise RuntimeError("private-provider-token")
    app.state.provider_factory = Failed
    path = f"/admin/connections/{admin_pages['connection']}/repositories"
    response = client.get(path, headers={"Accept": "text/html"})
    assert response.status_code == 400
    assert 'text/html' in response.headers['content-type']
    assert "private-provider-token" not in response.text
    assert f'href="{path}"' in response.text
    assert f'href="/admin/connections/{admin_pages["connection"]}"' in response.text


def test_html_access_errors_offer_safe_context_and_json_stays_json(client, pages):
    forbidden = client.get('/admin', headers={"Accept": "text/html"})
    assert forbidden.status_code == 403
    assert "管理员" in forbidden.text and 'href="/"' in forbidden.text
    missing = client.get('/projects/999999', headers={"Accept": "text/html"})
    assert missing.status_code == 404 and 'href="/"' in missing.text
    response = client.get('/projects/999999', headers={"Accept": "application/json"})
    assert response.status_code == 404 and response.json()["detail"]


def test_login_next_survives_language_and_invalid_password(client, user):
    destination = '/admin/projects/12?tab=settings&sort=name'
    page = client.get('/login', params={"next": destination, "lang": "ja"})
    assert page.text.count('name="next"') == 2
    assert '/admin/projects/12?tab=settings&amp;sort=name' in page.text
    csrf = csrf_from(page)
    failed = client.post('/login', data={"csrf_token": csrf, "username": "alice", "password": "wrong", "next": destination})
    assert failed.status_code == 401 and 'name="next"' in failed.text
    response = client.post('/login', data={"csrf_token": csrf, "username": "alice", "password": "correct horse battery staple", "next": destination}, follow_redirects=False)
    assert response.headers['location'] == destination


@pytest.mark.parametrize("destination", ['https://evil.test/', '//evil.test/', '/\\evil.test', '/%2f%2fevil.test', '/admin\nX-Test: bad', '/admin%0d%0aX-Test:bad'])
def test_login_rejects_unsafe_next(client, user, destination):
    csrf = csrf_from(client.get('/login', params={"next": destination}))
    response = client.post('/login', data={"csrf_token": csrf, "username": "alice", "password": "correct horse battery staple", "next": destination}, follow_redirects=False)
    assert response.headers['location'] == '/'


def test_admin_login_redirect_keeps_local_destination(client):
    response = client.get('/admin/projects/12?tab=settings', follow_redirects=False)
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers['location']).query)['next'] == ['/admin/projects/12?tab=settings']


def test_login_rejects_file_as_next(client, user):
    csrf = csrf_from(client.get('/login'))
    response = client.post('/login', data={"csrf_token": csrf, "username": "alice", "password": "correct horse battery staple"},
                           files={"next": ("next.txt", b"https://evil.test/")}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/'


def test_invalid_sync_date_returns_repository_forms_to_their_own_actions(client, admin_pages):
    repo_id = admin_pages['repo']
    response = post_form(client, f'/admin/repositories/{repo_id}/sync', history_since='2999-01-01')
    assert response.status_code == 400
    assert f'action="/admin/repositories/{repo_id}"' in response.text
    assert 'value="2999-01-01"' in response.text
    assert 'href="#field-history_since"' in response.text


def test_import_failure_does_not_signal_basket_clear_and_success_does(app, client, admin_pages):
    app.state.provider_factory = ProviderFixture
    invalid = post_form(client, '/admin/repositories/import', connection_id=admin_pages['connection'],
                        project_id=admin_pages['project'], external_ids=['missing'])
    assert invalid.status_code == 400
    assert 'imported_connection' not in invalid.headers.get('location', '')
    valid = post_form(client, '/admin/repositories/import', connection_id=admin_pages['connection'],
                      project_id=admin_pages['project'], external_ids=['2'])
    assert valid.status_code == 303
    assert parse_qs(urlsplit(valid.headers['location']).query)['imported_connection'] == [str(admin_pages['connection'])]
    assert 'gev_imported_connection' not in valid.headers.get('set-cookie', '')


@pytest.mark.parametrize('action,expected', [
    ('/admin/repositories/{repo}/sync', '/admin/repositories/{repo}'),
    ('/admin/connections/{connection}/check', '/admin/connections/{connection}'),
    ('/admin/projects/{project}/members', '/admin/projects/{project}'),
    ('/admin/projects', '/admin'),
    ('/admin/repositories/import', '/admin'),
    ('/admin/users', '/admin/users/new'),
    ('/admin/connections', '/admin/connections/new'),
])
def test_expired_post_login_returns_to_get_form(client, admin_pages, action, expected):
    client.cookies.clear()
    response = client.post(action.format(**admin_pages) + '?lang=en', follow_redirects=False)
    target = parse_qs(urlsplit(response.headers['location']).query)['next'][0]
    assert target == expected.format(**admin_pages) + '?lang=en'
    csrf = csrf_from(client.get(response.headers['location']))
    logged_in = client.post('/login', data={'csrf_token': csrf, 'username': 'alice',
                            'password': 'correct horse battery staple', 'next': target}, follow_redirects=False)
    assert client.get(logged_in.headers['location']).status_code == 200


@pytest.mark.parametrize('referer,expected', [
    ('http://testserver/admin/connections/1/repositories?page=2&q=team', '/admin/connections/1/repositories?page=2&q=team'),
    ('https://evil.test/admin/connections/1/repositories', '/admin'),
    ('http://testserver/admin/connections/1/check', '/admin'),
])
def test_expired_import_uses_only_same_origin_get_list_referer(client, referer, expected):
    response = client.post('/admin/repositories/import', headers={'Referer': referer}, follow_redirects=False)
    assert parse_qs(urlsplit(response.headers['location']).query)['next'] == [expected]


@pytest.mark.parametrize('url', [
    'https://git.example/api?private_token=pasted-secret',
    'https://git.example/api?access_token=pasted-secret',
    'https://git.example/api#pasted-secret',
])
def test_invalid_connection_url_does_not_retain_credentials(client, admin_pages, url):
    response = post_form(client, '/admin/connections', name='retained', kind='gitlab', base_url=url)
    assert response.status_code == 400
    assert 'pasted-secret' not in response.text
    assert 'value="retained"' in response.text


def test_csrf_html_recovery_login_link_returns_to_get_form(client, admin_pages):
    response = client.post(f'/admin/repositories/{admin_pages["repo"]}/sync', headers={'Accept': 'text/html'})
    assert response.status_code == 403
    assert f'next=/admin/repositories/{admin_pages["repo"]}"' in response.text


def test_missing_branch_selection_retains_json_not_found_and_recovers_html(client, admin_pages):
    data = {'csrf_token': csrf_from(client.get('/')), 'role': 'feature', 'manual_source_branch_id': '999999'}
    path = f'/admin/branches/{admin_pages["feature"]}'
    response = client.post(path, data=data, headers={'Accept': 'application/json'})
    assert response.status_code == 404 and isinstance(response.json()['detail'], str)
    html = client.post(path, data=data, headers={'Accept': 'text/html'})
    assert html.status_code == 404 and 'href="#field-manual_source_branch_id"' in html.text
