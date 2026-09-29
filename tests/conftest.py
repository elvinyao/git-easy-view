import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import Base, User


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-session-secret-with-enough-entropy",
        credential_key=Fernet.generate_key().decode(),
        production=False,
    )
    application = create_app(settings)
    Base.metadata.create_all(application.state.engine)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def user(app):
    with Session(app.state.engine) as session:
        user = User(username="alice", is_admin=False)
        user.set_password("correct horse battery staple")
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
