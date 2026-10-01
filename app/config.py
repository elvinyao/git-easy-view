import secrets
from typing import Literal
from sqlalchemy.engine import make_url

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEV_", env_file=".env", extra="ignore", hide_input_in_errors=True)

    production: bool = True
    database_url: str | None = Field(default=None, repr=False)
    session_secret: str | None = Field(default=None, repr=False)
    credential_key: str | None = Field(default=None, repr=False)
    redis_url: str = Field(default="redis://localhost:6379/0", repr=False)
    job_backend: Literal["database", "redis"] = "database"
    cache_root: str = "./data/git"
    queue_name: str = "git-sync"
    job_timeout_seconds: int = 3600
    scheduler_interval_seconds: int = 15
    heartbeat_interval_seconds: int = 15
    cookie_secure: bool | None = None
    session_max_age: int = 60 * 60 * 12

    @model_validator(mode="after")
    def validate_deployment(self):
        if self.production:
            if not self.database_url:
                raise ValueError("Production requires an explicit persistent GEV_DATABASE_URL")
            url = make_url(self.database_url)
            if url.get_backend_name() not in ("sqlite", "postgresql") or (
                url.get_backend_name() == "sqlite" and (
                    not url.database or not url.database.startswith("/")
                    or url.database == ":memory:" or url.query.get("mode") == "memory"
                )
            ):
                raise ValueError("Production requires PostgreSQL or an absolute SQLite file path")
            if not self.session_secret or len(self.session_secret) < 32:
                raise ValueError("Production requires GEV_SESSION_SECRET of at least 32 characters")
            if not self.credential_key:
                raise ValueError("Production requires GEV_CREDENTIAL_KEY")
            if self.cookie_secure is False:
                raise ValueError("Production cookies must be Secure")
            self.cookie_secure = True
        else:
            self.database_url = self.database_url or "sqlite:///./git-easy-view.db"
            self.session_secret = self.session_secret or secrets.token_urlsafe(48)
            self.credential_key = self.credential_key or Fernet.generate_key().decode()
            if self.cookie_secure is None:
                self.cookie_secure = False
        Fernet(self.credential_key.encode())
        return self

    def encrypt_token(self, token: str) -> str:
        return Fernet(self.credential_key.encode()).encrypt(token.encode()).decode()

    def decrypt_token(self, ciphertext: str) -> str:
        return Fernet(self.credential_key.encode()).decrypt(ciphertext.encode()).decode()
