from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PlatformConnection, ProjectRepository, Repository, SyncJob
from test_pages import ProviderFixture, admin_pages, pages, post_form


def add_jobs(app, repository_id, statuses):
    with Session(app.state.engine) as session:
        jobs = [SyncJob(repository_id=repository_id, generation=index + 1,
                        status=status, error="private-error-with-token",
                        enqueue_error="private-queue-credential")
                for index, status in enumerate(statuses)]
        session.add_all(jobs)
        session.commit()
        return [job.id for job in jobs]


@pytest.mark.parametrize("statuses,state,terminal,successful", [
    (["pending", "queued", "retry"], "queued", False, False),
    (["queued", "running", "succeeded"], "running", False, False),
    (["succeeded", "succeeded"], "succeeded", True, True),
    (["failed", "succeeded"], "failed", True, False),
    (["auth_failed", "succeeded"], "auth_failed", True, False),
    (["superseded", "succeeded"], "superseded", True, False),
])
def test_public_progress_reports_explicit_batch_without_remote_work(
    app, client, pages, monkeypatch, statuses, state, terminal, successful
):
    ids = add_jobs(app, pages["repo"], statuses)
    client.cookies.clear()
    monkeypatch.setattr("app.routes.admin.provider_for", lambda *_: pytest.fail("GET must not use provider"))
    monkeypatch.setattr("app.jobs.enqueue_sync", lambda *_args, **_kw: pytest.fail("GET must not enqueue"))
    response = client.get(f"/projects/{pages['project']}/jobs", params={"jobs": ",".join(map(str, ids))})
    assert response.status_code == 200
    body = response.json()
    assert (body["state"], body["terminal"], body["successful"]) == (state, terminal, successful)
    assert [job["job_id"] for job in body["jobs"]] == ids
    assert all(job["repository_id"] == pages["repo"] and job["repository_name"] == "team/api" for job in body["jobs"])
    assert sum(body["counts"].values()) == len(ids)
    assert "private-" not in response.text
    assert "/admin" not in response.text
    assert "no-store" in response.headers["cache-control"]
    with Session(app.state.engine) as session:
        assert session.query(SyncJob).count() == len(ids)


def test_progress_rejects_other_project_repository_or_unknown_jobs(app, client, pages):
    ids = add_jobs(app, pages["repo"], ["queued"])
    with Session(app.state.engine) as session:
        other = Repository(connection_id=pages["connection"], external_id="other", name="other",
                           full_name="team/other", web_url="https://github.com/team/other",
                           clone_url="https://github.com/team/other.git")
        session.add(other)
        session.flush()
        session.add(ProjectRepository(project_id=pages["private"], repository_id=other.id))
        session.commit()
        other_id = other.id
    other_job = add_jobs(app, other_id, ["running"])[0]
    for path in (f"/projects/{pages['project']}/jobs", f"/repositories/{pages['repo']}/jobs"):
        for jobs in (f"{ids[0]},{other_job}", "9999999"):
            assert client.get(path, params={"jobs": jobs}).status_code == 404
    assert client.get(f"/projects/{pages['private']}?jobs={ids[0]}").status_code == 404
    assert client.get(f"/admin/repositories/{other_id}?jobs={ids[0]}").status_code == 403


@pytest.mark.parametrize("value", ["", "x", "1,,2", "0", "-1", "1.5", "1," * 50 + "1", "9" * 1000])
def test_progress_bounds_and_validates_job_ids(client, pages, value):
    assert client.get(f"/projects/{pages['project']}/jobs", params={"jobs": value}).status_code == 400


def test_import_redirect_exposes_only_jobs_for_that_request(app, client, admin_pages):
    app.state.provider_factory = ProviderFixture
    add_jobs(app, admin_pages["repo"], ["succeeded"])
    response = post_form(client, "/admin/repositories/import", connection_id=admin_pages["connection"],
                         project_id=admin_pages["project"], external_ids=["2", "3"])
    assert response.status_code == 303
    location = response.headers["location"]
    ids = [int(value) for value in parse_qs(urlsplit(location).query).get("jobs", [""])[0].split(",") if value]
    assert len(ids) == 2
    with Session(app.state.engine) as session:
        jobs = session.scalars(select(SyncJob).where(SyncJob.id.in_(ids))).all()
        assert len(jobs) == 2
        assert all(job.repository_id != admin_pages["repo"] for job in jobs)
    page = client.get(location)
    assert page.status_code == 200
    assert 'data-job-progress' in page.text
    assert 'team/repo-002' in page.text and 'team/repo-003' in page.text


def test_manual_sync_redirect_renders_progress_and_preserves_snapshot(app, client, admin_pages):
    response = post_form(client, f"/admin/repositories/{admin_pages['repo']}/sync")
    assert response.status_code == 303
    assert "jobs=" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert 'data-job-progress' in page.text
    assert '同步期间继续显示上次快照。' in page.text
    with Session(app.state.engine) as session:
        assert session.get(Repository, admin_pages["repo"]).snapshot_generation == 1
    project = client.get(f"/projects/{admin_pages['project']}")
    assert 'feature/login' in project.text


def test_admin_progress_links_recover_auth_failure_without_exposing_errors(app, client, admin_pages):
    ids = add_jobs(app, admin_pages["repo"], ["auth_failed"])
    with Session(app.state.engine) as session:
        session.get(PlatformConnection, admin_pages["connection"]).last_error = "private-provider-token"
        session.commit()
    response = client.get(f"/admin/repositories/{admin_pages['repo']}?jobs={ids[0]}")
    assert response.status_code == 200
    assert 'data-job-progress' in response.text
    assert f'/admin/connections/{admin_pages["connection"]}' in response.text
    assert "private-" not in response.text
