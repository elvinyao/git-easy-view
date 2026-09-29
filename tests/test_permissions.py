import pytest
from sqlalchemy.orm import Session

from app.models import ProjectMember, PullRequest, SyncJob, User
from test_pages import pages
from test_auth import csrf_from


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "/branches",
        "/branches/1",
        "/branches/1/fragment",
        "/status",
        "/pull-requests",
        "/pull-requests/1",
        "/jobs/1",
    ],
)
def test_private_repository_routes_deny_next_request(app, client, pages, suffix):
    with Session(app.state.engine) as s:
        s.query(ProjectMember).delete()
        s.commit()
    assert client.get(f"/repositories/{pages['repo']}{suffix}").status_code == 404


def test_private_project_and_member_admin_denial(client, pages):
    assert client.get(f"/projects/{pages['private']}").status_code == 404
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


def test_existing_pr_job_and_comparison_deny_revoked_member(client, app, pages):
    with Session(app.state.engine) as s:
        pr = PullRequest(
            connection_id=pages["connection"],
            repository_id=pages["repo"],
            external_id="record",
            title="sensitive title",
            web_url="https://github.com/team/api/pull/1",
            state="merged",
        )
        job = SyncJob(repository_id=pages["repo"], generation=1, status="queued")
        s.add_all([pr, job])
        s.commit()
        pid, jid = pr.id, job.id
    pr_path = f"/repositories/{pages['repo']}/pull-requests/{pid}"
    job_path = f"/repositories/{pages['repo']}/jobs/{jid}"
    assert client.get(pr_path).status_code == 200
    assert client.get(job_path).status_code == 200
    csrf = csrf_from(client.get("/"))
    with Session(app.state.engine) as s:
        s.query(ProjectMember).delete()
        s.commit()
    assert client.get(pr_path).status_code == 404
    assert client.get(job_path).status_code == 404
    assert (
        client.post(
            f"/repositories/{pages['repo']}/branches/{pages['feature']}/compare",
            data={"csrf_token": csrf, "target_id": pages["main"]},
        ).status_code
        == 404
    )
