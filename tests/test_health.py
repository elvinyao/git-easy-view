from sqlalchemy.exc import OperationalError


def test_readiness_probes_database_without_redis(client, app, monkeypatch):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}

    def unavailable():
        raise OperationalError("private connection detail", None, Exception("secret"))
    monkeypatch.setattr(app.state.engine, "connect", unavailable)
    response = client.get("/health")
    assert response.status_code == 503
    assert "secret" not in response.text and "private" not in response.text


def test_settings_repr_does_not_disclose_deployment_secrets():
    from app.config import Settings
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    settings = Settings(production=False, database_url="postgresql+psycopg://user:database-secret@example.invalid/test",
                        redis_url="redis://:redis-secret@example.invalid/0", session_secret="session-secret", credential_key=key)
    assert not any(secret in repr(settings) for secret in ("database-secret", "redis-secret", "session-secret", key))
