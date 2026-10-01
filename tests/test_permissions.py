import pytest
from urllib.parse import parse_qs, urlsplit
from sqlalchemy.orm import Session

from app.models import AuditEvent, Branch, PlatformConnection, ProjectMember, ProjectRepository, PullRequest, Repository, SyncJob, User
from test_pages import pages
from test_auth import csrf_from


@pytest.fixture
def public_pages(app, client, pages):
    with Session(app.state.engine) as s:
        s.query(ProjectMember).delete()
        connection = s.get(PlatformConnection, pages["connection"])
        connection.encrypted_token = app.state.settings.encrypt_token("never-publish-platform-token")
        pr = PullRequest(connection_id=pages["connection"], repository_id=pages["repo"], external_id="public-record", title="Public PR", web_url="https://github.com/team/api/pull/1", state="merged")
        job = SyncJob(repository_id=pages["repo"], generation=1, status="failed", error="never-publish-job-error", enqueue_error="never-publish-queue-error")
        s.add_all([pr, job])
        s.commit()
        ids = dict(pages, pr=pr.id, job=job.id, encrypted_token=connection.encrypted_token)
    client.cookies.clear()
    return ids


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "/branches",
        "/branches?fragment=1",
        "/log",
        "/branches/{feature}",
        "/branches/{feature}/fragment",
        "/status",
        "/pull-requests",
        "/pull-requests/{pr}",
        "/jobs/{job}",
    ],
)
def test_repository_views_allow_anonymous_reads_without_membership(app, client, public_pages, suffix):
    response = client.get(f"/repositories/{public_pages['repo']}" + suffix.format(**public_pages), follow_redirects=False)
    assert response.status_code == 200
    for secret in ("never-publish-platform-token", public_pages["encrypted_token"], "never-publish-job-error", "never-publish-queue-error"):
        assert secret not in response.text
    assert 'class="compare-form"' not in response.text
    assert 'href="/admin' not in response.text
    assert "gev_session=" not in response.headers.get("set-cookie", "")
    with Session(app.state.engine) as s:
        assert s.query(SyncJob).count() == 1
        assert s.query(AuditEvent).count() == 0


def test_public_project_and_member_admin_denial(client, pages):
    assert client.get(f"/projects/{pages['private']}").status_code == 200
    assert client.get("/admin").status_code == 403
    assert client.post("/admin/repositories/import").status_code == 403
    assert client.post(f"/admin/repositories/{pages['repo']}/sync").status_code == 403


def test_admin_forms_require_csrf(client, app, user, pages):
    with Session(app.state.engine) as s:
        s.get(User, user.id).is_admin = True
        s.commit()
    assert client.get("/admin").status_code == 200
    assert (
        client.post("/admin/projects", data={"name": "A", "slug": "a"}).status_code
        == 403
    )
    response = client.post(
        "/admin/projects",
        data={"name": "A", "slug": "a", "csrf_token": csrf_from(client.get("/"))},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_member_cannot_enqueue_analysis_even_with_valid_csrf(client, app, pages):
    csrf = csrf_from(client.get("/"))
    assert (
        client.post(
            f"/repositories/{pages['repo']}/branches/{pages['feature']}/compare",
            data={"csrf_token": csrf, "target_id": pages["main"]},
        ).status_code
        == 403
    )
    with Session(app.state.engine) as s:
        assert s.query(SyncJob).count() == s.query(AuditEvent).count() == 0


@pytest.mark.parametrize("path", ["/", "/projects/{project}", "/projects/{private}"])
def test_projects_allow_anonymous_reads(client, public_pages, path):
    response = client.get(path.format(**public_pages), follow_redirects=False)
    assert response.status_code == 200
    assert "匿名访问" in response.text
    assert 'href="/login?next=' in response.text
    assert 'class="language-picker"' not in response.text
    assert 'href="/admin' not in response.text


@pytest.mark.parametrize("path", [
    "/admin", "/admin/connections/new", "/admin/connections/{connection}",
    "/admin/connections/{connection}/repositories", "/admin/projects/{project}",
    "/admin/repositories/{repo}", "/admin/branches/{feature}", "/admin/users/new",
])
def test_anonymous_configuration_requires_login(client, public_pages, path):
    response = client.get(path.format(**public_pages), follow_redirects=False)
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query)["next"] == [path.format(**public_pages)]


@pytest.mark.parametrize("path", [
    "/admin/projects", "/admin/users", "/admin/connections",
    "/admin/connections/{connection}/check", "/admin/repositories/import",
    "/admin/repositories/{repo}/sync", "/admin/repositories/{repo}/projects",
    "/admin/branches/{feature}", "/repositories/{repo}/branches/{feature}/compare",
])
def test_anonymous_mutations_require_login_and_do_not_enqueue(app, client, public_pages, path):
    csrf = csrf_from(client.get("/login"))  # Even a valid anonymous CSRF grants no write access.
    response = client.post(path.format(**public_pages), data={"csrf_token": csrf}, follow_redirects=False)
    assert response.status_code == 303
    destinations = {
        "/admin/projects": "/admin", "/admin/users": "/admin/users/new",
        "/admin/connections": "/admin/connections/new", "/admin/repositories/import": "/admin",
        "/admin/connections/{connection}/check": "/admin/connections/{connection}",
        "/admin/repositories/{repo}/sync": "/admin/repositories/{repo}",
        "/admin/repositories/{repo}/projects": "/admin/repositories/{repo}",
        "/repositories/{repo}/branches/{feature}/compare": "/repositories/{repo}/branches/{feature}",
    }
    assert parse_qs(urlsplit(response.headers["location"]).query)["next"] == [destinations.get(path, path).format(**public_pages)]
    with Session(app.state.engine) as s:
        assert s.query(SyncJob).count() == 1
        assert s.query(AuditEvent).count() == 0


def test_detached_repository_is_admin_only(app, client, public_pages, user, monkeypatch):
    with Session(app.state.engine) as s:
        s.query(ProjectRepository).delete()
        s.commit()
    monkeypatch.setattr("app.routes.repositories.branch_views", lambda *_: pytest.fail("Unpublished repository must not be read"))
    for suffix in ("", "/branches", "/log", "/status", f"/pull-requests/{public_pages['pr']}", f"/jobs/{public_pages['job']}"):
        assert client.get(f"/repositories/{public_pages['repo']}{suffix}").status_code == 404
    from test_auth import login
    with Session(app.state.engine) as s:
        s.get(User, user.id).is_admin = True
        s.commit()
    login(client)
    assert client.get(f"/repositories/{public_pages['repo']}").status_code == 200


def test_public_routes_still_scope_records_to_their_repository(app, client, public_pages):
    with Session(app.state.engine) as s:
        other = Repository(connection_id=public_pages["connection"], external_id="other", name="other", full_name="team/other", web_url="https://github.com/team/other", clone_url="https://github.com/team/other.git")
        s.add(other)
        s.flush()
        branch = Branch(repository_id=other.id, name="feature/other", head_oid="d" * 40)
        pr = PullRequest(connection_id=public_pages["connection"], repository_id=other.id, external_id="other-pr", title="Other PR", web_url="https://github.com/team/other/pull/1", state="open")
        job = SyncJob(repository_id=other.id, generation=1, status="queued")
        s.add_all([branch, pr, job, ProjectRepository(project_id=public_pages["project"], repository_id=other.id)])
        s.commit()
        rid, bid, pid, jid = other.id, branch.id, pr.id, job.id
    assert client.get(f"/repositories/{rid}").status_code == 200
    for suffix in (f"/branches/{bid}", f"/branches/{bid}/fragment", f"/pull-requests/{pid}", f"/jobs/{jid}", f"/log?branch_id={bid}"):
        assert client.get(f"/repositories/{public_pages['repo']}{suffix}").status_code == 404
    assert client.get(f"/repositories/{public_pages['repo']}/branches/{public_pages['feature']}?target_id={bid}").status_code == 404
