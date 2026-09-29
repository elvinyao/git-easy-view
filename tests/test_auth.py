import re
import asyncio
from datetime import timedelta

from fastapi import Depends
import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import current_user, require_project_access
from app.cli import main as cli_main
from app.config import Settings
from app.db import get_session
from app.models import Project, ProjectMember, User


def csrf_from(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, response.text
    return match.group(1)


def login(client):
    csrf = csrf_from(client.get("/login"))
    return client.post(
        "/login",
        data={"username": "alice", "password": "correct horse battery staple", "csrf_token": csrf},
        follow_redirects=False,
    )


def test_anonymous_dashboard_redirects(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_uses_real_password_hash_and_sets_session(client, user):
    response = login(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "alice" in dashboard.text


def test_wrong_password_does_not_log_in(client, user):
    csrf = csrf_from(client.get("/login"))
    response = client.post(
        "/login",
        data={"username": "alice", "password": "incorrect", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303


def test_logout_without_csrf_is_rejected(client, user):
    assert login(client).status_code == 303
    assert client.post("/logout").status_code == 403
    assert client.get("/").status_code == 200


def test_logout_with_csrf_clears_session(client, user):
    assert login(client).status_code == 303
    csrf = csrf_from(client.get("/"))
    response = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303


def test_unicode_csrf_is_rejected_without_server_error(client, user):
    assert login(client).status_code == 303
    assert client.post("/logout", data={"csrf_token": "🌸"}).status_code == 403


def test_revoked_project_membership_denies_next_request(app, client, user):
    with Session(app.state.engine) as session:
        project = Project(name="Private", slug="private")
        session.add(project)
        session.flush()
        session.add(ProjectMember(project_id=project.id, user_id=user.id))
        session.commit()
        project_id = project.id

    @app.get("/test-project/{project_id}")
    def project_page(
        project_id: int,
        authenticated_user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ):
        require_project_access(session, authenticated_user, project_id)
        return {"visible": True}

    assert login(client).status_code == 303
    assert client.get(f"/test-project/{project_id}").status_code == 200
    with Session(app.state.engine) as session:
        membership = session.query(ProjectMember).filter_by(project_id=project_id, user_id=user.id).one()
        session.delete(membership)
        session.commit()
    assert client.get(f"/test-project/{project_id}").status_code == 404


def test_cli_creates_hashed_admin_without_default_account(tmp_path, monkeypatch):
    from app.db import make_engine

    database_url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("GEV_PRODUCTION", "false")
    monkeypatch.setenv("GEV_DATABASE_URL", database_url)
    monkeypatch.setenv("GEV_ADMIN_PASSWORD", "admin password supplied explicitly")
    assert cli_main(["init-db"]) == 0
    engine = make_engine(database_url)
    with Session(engine) as session:
        assert session.query(User).count() == 0
    assert cli_main(["create-user", "--username", "boss", "--admin"]) == 0
    with Session(engine) as session:
        admin = session.query(User).filter_by(username="boss").one()
        assert admin.is_admin
        assert admin.password_hash != "admin password supplied explicitly"
        assert admin.check_password("admin password supplied explicitly")
    engine.dispose()


def test_sqlite_timestamps_reload_as_utc(app, user):
    with Session(app.state.engine) as session:
        loaded = session.get(User, user.id)
        assert loaded.created_at.tzinfo is not None
        assert loaded.created_at.utcoffset() == timedelta(0)


def test_production_rejects_missing_database_and_secrets():
    with pytest.raises(ValueError):
        Settings(production=True, database_url="sqlite:///unsafe.db", session_secret=None, credential_key=None)


def test_platform_token_encryption_does_not_store_plaintext(app):
    settings = app.state.settings
    token = "secret-platform-token"
    ciphertext = settings.encrypt_token(token)
    assert token not in ciphertext
    assert settings.decrypt_token(ciphertext) == token


def test_sqlite_rejects_orphan_project_membership(app):
    with Session(app.state.engine) as session:
        session.add(ProjectMember(project_id=987654, user_id=987654))
        with pytest.raises(IntegrityError):
            session.commit()


def test_login_database_and_hash_run_outside_event_loop(app, client, user, monkeypatch):
    csrf = csrf_from(client.get("/login"))
    observed = {"query": False, "hash": False}
    original_check_password = User.check_password

    def assert_no_loop(label):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            observed[label] = True

    def check_password(self, password):
        assert_no_loop("hash")
        return original_check_password(self, password)

    def before_cursor_execute(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "users" in statement:
            assert_no_loop("query")

    monkeypatch.setattr(User, "check_password", check_password)
    event.listen(app.state.engine, "before_cursor_execute", before_cursor_execute)
    try:
        response = client.post(
            "/login",
            data={"username": "alice", "password": "correct horse battery staple", "csrf_token": csrf},
            follow_redirects=False,
        )
    finally:
        event.remove(app.state.engine, "before_cursor_execute", before_cursor_execute)
    assert response.status_code == 303
    assert observed == {"query": True, "hash": True}
