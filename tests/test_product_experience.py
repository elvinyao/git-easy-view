"""First-run setup, access control, the attention view and unreleased changes."""
from datetime import timedelta

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.first_run import setup_code
from app.main import create_app
from app.models import (
    Base, Branch, Comparison, Deployment, PullRequest, ReleaseItem, ReleasePlan, Repository, User, utc_now,
)
from app.sync import cache_path
from test_auth import csrf_from, login
from test_git_service import git
from test_pages import admin_pages, pages, post_form


def setup_form(client, **fields):
    values = dict(username="owner", password="a long enough password", password_confirm="a long enough password")
    values.update(fields, csrf_token=csrf_from(client.get("/setup")))
    return client.post("/setup", data=values, follow_redirects=False)


def test_new_instance_guides_first_visitor_to_create_the_administrator(app, client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/setup"
    assert client.get("/login", follow_redirects=False).headers["location"] == "/setup"
    page = client.get("/setup")
    assert page.status_code == 200 and "设置码" not in page.text
    assert setup_form(client, password="short", password_confirm="short").status_code == 400
    assert setup_form(client, password_confirm="a different long password").status_code == 400
    with Session(app.state.engine) as session:
        assert session.scalar(select(func.count(User.id))) == 0
    response = setup_form(client)
    assert response.status_code == 303 and response.headers["location"] == "/admin"
    workbench = client.get("/admin")
    assert workbench.status_code == 200 and "三步完成配置" in workbench.text
    assert "开始配置" in client.get("/").text
    # Setup closes for good once any account exists.
    assert client.get("/setup", follow_redirects=False).headers["location"] == "/login"
    client.cookies.clear()
    csrf = csrf_from(client.get("/login"))
    again = client.post("/setup", data=dict(username="intruder", password="another long password",
                        password_confirm="another long password", csrf_token=csrf), follow_redirects=False)
    assert again.headers["location"] == "/login"
    with Session(app.state.engine) as session:
        users = session.scalars(select(User)).all()
        assert [(user.username, user.is_admin) for user in users] == [("owner", True)]


def test_production_setup_requires_the_code_from_the_server(tmp_path):
    settings = Settings(production=True, database_url=f"sqlite:///{tmp_path / 'production.db'}",
                        session_secret="production-session-secret-with-enough-entropy",
                        credential_key=Fernet.generate_key().decode())
    application = create_app(settings)
    Base.metadata.create_all(application.state.engine)
    try:
        with TestClient(application, base_url="https://testserver") as client:
            assert "设置码" in client.get("/setup").text
            assert setup_form(client, code="AAAA-AAAA").status_code == 400
            assert setup_form(client).status_code == 400
            with Session(application.state.engine) as session:
                assert session.scalar(select(func.count(User.id))) == 0
            accepted = setup_form(client, code=setup_code(settings).lower().replace("-", " "))
            assert accepted.status_code == 303 and accepted.headers["location"] == "/admin"
    finally:
        application.state.engine.dispose()


def test_administrator_can_require_sign_in_for_every_page(app, client, admin_pages):
    project = f"/projects/{admin_pages['project']}"
    assert post_form(client, "/admin/settings/access", require_login="on").status_code == 303
    assert "checked" in client.get("/admin").text.split('name="require_login"')[1][:40]
    client.cookies.clear()
    for path in ("/", project, project + "/attention", project + "/unreleased",
                 f"/repositories/{admin_pages['repo']}/branches"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"].startswith("/login?next="), path
    assert client.get("/health").status_code == 200
    assert client.get("/static/app.css").status_code == 200
    with Session(app.state.engine) as session:
        session.scalar(select(User).where(User.username == "alice")).is_admin = False
        session.commit()
    assert login(client).status_code == 303
    assert client.get(project).status_code == 200  # A read-only member may view.
    assert client.get("/admin", follow_redirects=False).status_code == 403
    with Session(app.state.engine) as session:
        session.scalar(select(User).where(User.username == "alice")).is_admin = True
        session.commit()
    assert post_form(client, "/admin/settings/access").status_code == 303
    client.cookies.clear()
    assert client.get(project).status_code == 200


def test_attention_view_names_what_to_act_on(app, client, pages, user):
    with Session(app.state.engine) as session:
        repo = session.get(Repository, pages["repo"])
        repo.sync_error = "boom"
        old = utc_now() - timedelta(days=200)
        rows = {
            "feature/abandoned": ("d" * 40, "diverged", 4, old),
            "feature/merged": ("e" * 40, "contained", 0, old),
            "release/1.0": ("f" * 40, "diverged", 2, utc_now()),
        }
        for name, (oid, state, ahead, moment) in rows.items():
            session.add(Branch(repository_id=repo.id, name=name, head_oid=oid, commit_time=moment))
            session.add(Comparison(repository_id=repo.id, source_oid=oid, target_oid="a" * 40,
                                   state=state, ahead=ahead, behind=1, merge_bases=["c" * 40]))
        plan = ReleasePlan(project_id=pages["project"], title="Spring release", scheduled_date="2020-01-01",
                           status="active", created_by_id=user.id)
        session.add(plan)
        session.flush()
        session.add(ReleaseItem(plan_id=plan.id, repository_id=repo.id, branch_name="release/1.0", target_branch="main"))
        session.commit()
    page = client.get(f"/projects/{pages['project']}/attention")
    assert page.status_code == 200
    for expected in ("同步失败的仓库", "已超期的发布计划", "Spring release", "发布分支有提交未合回主干",
                     "2 个提交尚未合回 main", "已合入、可以清理的分支", "feature/merged",
                     "长期没有进展的分支", "feature/abandoned", "仍有 4 个提交未合入 main"):
        assert expected in page.text, expected
    # A recently active, unmerged feature branch is ordinary work, not a finding.
    assert "feature/login" not in page.text
    assert "5 项需要处理" in client.get("/").text
    assert "需要处理" in client.get(f"/projects/{pages['project']}").text
    empty = client.get(f"/projects/{pages['private']}/attention")
    assert empty.status_code == 200 and "这个项目还没有仓库。" in empty.text


def test_unreleased_view_lists_commits_and_merged_requests_after_production(app, client, pages, user):
    path = cache_path(app.state.settings, pages["repo"])
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Fixture")
    git(path, "config", "user.email", "fixture@example.test")
    oids = []
    for subject in ("shipped", "fix checkout rounding", "add export"):
        git(path, "commit", "--allow-empty", "-m", subject)
        oids.append(git(path, "rev-parse", "HEAD"))
    page = client.get(f"/projects/{pages['project']}/unreleased")
    assert page.status_code == 200 and "尚未记录本番" in page.text
    with Session(app.state.engine) as session:
        session.get(Branch, pages["main"]).head_oid = oids[2]
        session.add(Deployment(project_id=pages["project"], repository_id=pages["repo"], branch_name="main",
                               tag_name="v1.0", commit_oid=oids[0], deployed_at=utc_now(), actor_id=user.id))
        session.add(PullRequest(connection_id=pages["connection"], repository_id=pages["repo"], external_id="7",
                                title="Add CSV export", web_url="https://github.com/team/api/pull/7", state="merged",
                                source_branch="feature/export", target_branch="main", merge_oid=oids[2],
                                merged_at=utc_now()))
        session.add(PullRequest(connection_id=pages["connection"], repository_id=pages["repo"], external_id="3",
                                title="Already shipped", web_url="https://github.com/team/api/pull/3", state="merged",
                                source_branch="feature/old", target_branch="main", merge_oid=oids[0],
                                merged_at=utc_now()))
        session.commit()
    page = client.get(f"/projects/{pages['project']}/unreleased")
    assert page.status_code == 200
    for expected in ("v1.0", "2 个提交未上线", "fix checkout rounding", "add export", "Add CSV export"):
        assert expected in page.text, expected
    assert "Already shipped" not in page.text and ">shipped<" not in page.text
    with Session(app.state.engine) as session:
        session.add(Deployment(project_id=pages["project"], repository_id=pages["repo"], branch_name="main",
                               commit_oid=oids[2], deployed_at=utc_now(), actor_id=user.id))
        session.commit()
    assert "主干已全部上线" in client.get(f"/projects/{pages['project']}/unreleased").text
