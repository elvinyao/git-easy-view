import ast
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Formatter

import pytest
from fastapi.testclient import TestClient
from jinja2 import nodes
from markupsafe import escape
from sqlalchemy.orm import Session

from app.i18n import CLIENT_MESSAGES, LOCALE_COOKIE, LOCALES, MESSAGES
from app.models import Project, PullRequest
from app.web import templates
from test_auth import csrf_from, login
from test_pages import ProviderFixture, admin_pages, pages, post_form

HEADLINES = {
    "zh-CN": "看清每条分支的进展",
    "ja": "各ブランチの進捗を把握",
    "en": "See the progress of every branch",
}


@pytest.mark.parametrize(
    "header,locale",
    [
        ("", "zh-CN"),
        ("zh-CN,zh;q=0.9,en;q=0.8", "zh-CN"),
        ("zh-TW", "zh-CN"),
        ("ja-JP,en-US;q=0.8", "ja"),
        ("en-GB", "en"),
        ("fr,ja;q=0.8,en;q=0.5", "ja"),
        ("ja;q=0.2,en;q=0.9", "en"),
        ("en;q=0,ja;q=0.1", "ja"),
        ("en;q=bad,ja", "ja"),
        ("en;q=NaN,ja", "ja"),
        ("en;q=1.1,ja", "ja"),
        ("fr,de", "zh-CN"),
        ("*", "zh-CN"),
    ],
)
def test_browser_language_negotiation(client, header, locale):
    response = client.get("/login", headers={"Accept-Language": header})
    assert response.status_code == 200
    assert response.headers["content-language"] == locale
    assert f'<html lang="{locale}">' in response.text
    assert HEADLINES[locale] in response.text
    assert {"accept-language", "cookie"} <= {
        header.strip().lower() for header in response.headers["vary"].split(",")
    }


def test_explicit_language_persists_across_login_and_logout(client, user):
    response = client.get("/login?lang=ja-JP", headers={"Accept-Language": "en-US"})
    assert response.headers["content-language"] == "ja"
    assert client.cookies[LOCALE_COOKIE] == "ja"
    assert login(client).status_code == 303
    assert client.get("/", headers={"Accept-Language": "en-US"}).headers["content-language"] == "ja"
    assert client.get("/?lang=en").headers["content-language"] == "en"
    csrf = csrf_from(client.get("/"))
    assert client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False).status_code == 303
    assert client.get("/login").headers["content-language"] == "en"
    assert client.get("/login?lang=zh").headers["content-language"] == "zh-CN"


def test_invalid_preferences_are_ignored_and_not_reflected(client):
    client.cookies.set(LOCALE_COOKIE, "bogus")
    response = client.get("/login", params={"lang": '<script>alert("x")</script>'}, headers={"Accept-Language": "ja"})
    assert response.headers["content-language"] == "ja"
    assert '<script>alert("x")</script>' not in response.text
    assert "gev_language=" not in response.headers.get("set-cookie", "")
    client.cookies.set(LOCALE_COOKIE, "en")
    assert client.get("/login?lang=invalid", headers={"Accept-Language": "ja"}).headers["content-language"] == "en"


def test_language_cookie_uses_production_cookie_security(app, client):
    app.state.settings.cookie_secure = True
    cookie = client.get("/login?lang=ja").headers.get_list("set-cookie")
    preference = next(value for value in cookie if value.startswith(LOCALE_COOKIE + "="))
    assert "Secure" in preference and "HttpOnly" in preference
    assert "SameSite=lax" in preference and "Path=/" in preference


def test_concurrent_requests_do_not_share_language(app):
    def read(locale):
        with TestClient(app) as separate:
            response = separate.get("/login", headers={"Accept-Language": locale})
            return response.headers["content-language"], response.text

    locales = ["ja", "en", "zh-CN"] * 3
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(read, locales))
    for locale, (actual, body) in zip(locales, results):
        assert actual == locale
        assert HEADLINES[locale] in body


@pytest.mark.parametrize("locale", LOCALES)
def test_member_pages_fragments_and_browser_messages_are_localized(app, client, pages, locale):
    with Session(app.state.engine) as session:
        record = PullRequest(
            connection_id=pages["connection"], repository_id=pages["repo"],
            external_id="i18n", title="计划合入目标", web_url="https://github.com/team/api/pull/1",
            state="merged", source_repository_id=pages["repo"], source_external_repo_id="1",
            source_branch="feature/login", target_repository_id=pages["repo"],
            target_external_repo_id="1", target_branch="main",
        )
        session.add(record)
        session.commit()
        record_id = record.id
    client.get("/", params={"lang": locale})
    for path in (
        "/",
        f"/projects/{pages['project']}",
        f"/repositories/{pages['repo']}",
        f"/repositories/{pages['repo']}/branches",
        f"/repositories/{pages['repo']}/branches/{pages['feature']}",
        f"/repositories/{pages['repo']}/branches/{pages['feature']}/fragment",
        f"/repositories/{pages['repo']}/pull-requests",
        f"/repositories/{pages['repo']}/pull-requests/{record_id}",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-language"] == locale
        if '<html ' in response.text:
            assert f'<html lang="{locale}">' in response.text
            assert 'class="language-picker"' not in response.text
            payload = re.search(r'id="i18n-messages">(.*?)</script>', response.text, re.S)
            messages = json.loads(payload[1])
            assert messages["retry"] == {"zh-CN": "重试", "ja": "再試行", "en": "Retry"}[locale]
        if path.endswith("/branches"):
            assert {"zh-CN": "未全部包含", "ja": "すべてのコミットは含まれていません", "en": "Not fully contained"}[locale] in response.text
            assert {"zh-CN": "尚未同步", "ja": "未同期", "en": "Not synchronized yet"}[locale] in response.text
        if path.endswith(f"/pull-requests/{record_id}"):
            assert "<h1>计划合入目标</h1>" in response.text
            assert {"zh-CN": "历史已合并", "ja": "過去にマージ済み", "en": "Historically merged"}[locale] in response.text


@pytest.mark.parametrize("locale", LOCALES)
def test_admin_pages_and_parameterized_form_errors(app, client, admin_pages, user, locale):
    app.state.provider_factory = ProviderFixture
    client.get("/admin", params={"lang": locale})
    for path in (
        "/admin",
        "/admin/users/new",
        f"/admin/users/{user.id}",
        f"/admin/projects/{admin_pages['project']}",
        "/admin/connections/new",
        f"/admin/connections/{admin_pages['connection']}",
        f"/admin/connections/{admin_pages['connection']}/repositories",
        f"/admin/repositories/{admin_pages['repo']}",
        f"/admin/branches/{admin_pages['feature']}",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert f'<html lang="{locale}">' in response.text
        assert 'class="language-picker"' not in response.text
    response = post_form(client, "/admin/projects", name="x" * 256, slug="i18n")
    assert response.status_code == 400
    assert 'class="language-picker"' not in response.text
    assert {"zh-CN": "最多 255 字符", "ja": "最大 255 文字", "en": "up to 255 characters"}[locale] in response.text
    assert "x" * 256 in response.text  # Nonsecret input remains available for correction.
    response = post_form(client, "/admin/users", username="i18n", password="secret!")
    assert response.status_code == 400
    assert "secret!" not in response.text
    assert {"zh-CN": "密码长度须为", "ja": "パスワードは 12", "en": "Passwords must contain"}[locale] in response.text


def test_localized_json_errors_preserve_status_and_redirect_headers(client, user):
    response = client.get("/admin?lang=ja", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fadmin%3Flang%3Dja"
    assert login(client).status_code == 303
    response = client.post("/logout", headers={"Accept": "application/json"})
    assert response.status_code == 403
    assert response.json()["detail"] == "セキュリティトークンが無効です。ページを再読み込みしてください。"
    response = client.get("/missing?lang=en")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


@pytest.mark.parametrize(
    "locale,message",
    [
        ("zh-CN", "请求参数无效，请检查后重试。"),
        ("ja", "リクエストのパラメーターが無効です。確認して再試行してください。"),
        ("en", "Invalid request parameters. Check them and try again."),
    ],
)
def test_request_validation_is_localized_without_changing_error_codes(client, pages, locale, message):
    response = client.get("/repositories/invalid", params={"lang": locale})
    assert response.status_code == 422
    assert message in response.text
    response = client.get("/repositories/invalid", headers={"Accept": "application/json"})
    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["type"] == "int_parsing"
    assert error["loc"] == ["path", "repository_id"]
    assert error["msg"] == message


def test_language_preference_preserves_filters_and_escapes_user_content(app, client, pages):
    with Session(app.state.engine) as session:
        project = session.get(Project, pages["project"])
        project.name = "项目概览"
        project.description = "看清每条分支的进展"
        session.commit()
    query = '<script>alert("x")</script>'
    response = client.get(
        f"/projects/{pages['project']}",
        params={"lang": "en", "q": query, "role": "feature", "expanded": str(pages["repo"]), "bp_1": "2"},
    )
    assert "<h1>项目概览</h1>" in response.text
    assert "看清每条分支的进展" in response.text
    assert query not in response.text
    assert escape(query) in response.text
    assert 'class="language-picker"' not in response.text
    filters = re.search(r'<form[^>]*class="filters(?: [^"]*)?".*?</form>', response.text, re.S)[0]
    assert 'value="feature" selected' in filters
    assert 'name="expanded"' in filters and 'name="bp_1" value="2"' in filters
    assert 'name="lang" value="en"' in filters
    assert "lang=en" in response.text


def test_catalog_covers_visible_templates_and_dynamic_messages():
    sources = set(CLIENT_MESSAGES.values())
    for name in templates.env.list_templates():
        source, _, _ = templates.env.loader.get_source(templates.env, name)
        tree = templates.env.parse(source)
        for node in tree.find_all(nodes.TemplateData):
            assert not re.search(r"[\u3400-\u9fff]", node.data), name
        for node in tree.find_all(nodes.Call):
            if isinstance(node.node, nodes.Name) and node.node.name == "t" and node.args and isinstance(node.args[0], nodes.Const):
                sources.add(node.args[0].value)
        sources.update(
            node.value for node in tree.find_all(nodes.Const)
            if isinstance(node.value, str) and re.search(r"[\u3400-\u9fff]", node.value)
        )
    for path in (
        "app/web.py", "app/auth.py", "app/main.py", "app/routes/admin.py",
        "app/routes/repositories.py", "app/routes/projects.py", "app/git_workspace.py",
        "app/project_overview.py", "app/job_progress.py",
        "app/routes/releases.py", "app/releases.py", "app/release_forms.py",
    ):
        tree = ast.parse(Path(path).read_text())
        sources.update(
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(r"[\u3400-\u9fff]", node.value)
        )
    assert not sources - MESSAGES.keys(), sorted(sources - MESSAGES.keys())
    for source, translations in MESSAGES.items():
        fields = {field for _, field, _, _ in Formatter().parse(source) if field is not None}
        assert {"ja", "en"} <= set(translations) <= LOCALES.keys()
        for locale, text in translations.items():
            assert text.strip(), (source, locale)
            assert fields == {field for _, field, _, _ in Formatter().parse(text) if field is not None}, (source, locale)
