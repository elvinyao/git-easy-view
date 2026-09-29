import secrets

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEV_", env_file=".env", extra="ignore")

    production: bool = True
    database_url: str | None = None
    session_secret: str | None = None
    credential_key: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    cookie_secure: bool | None = None
    session_max_age: int = 60 * 60 * 12

    @model_validator(mode="after")
    def validate_deployment(self):
        if self.production:
            if not self.database_url or not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires an explicit PostgreSQL GEV_DATABASE_URL")
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
