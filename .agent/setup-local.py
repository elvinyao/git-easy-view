"""Generate local-only secrets. Run inside .agent/run.sh, never on the host."""
import json
import os
from pathlib import Path
import secrets
from cryptography.fernet import Fernet

os.umask(0o077)
root = Path("runtime")
root.mkdir(exist_ok=True)
(root / "ca").mkdir(exist_ok=True)
target = root / "local.env"
password = secrets.token_hex(32)
values = {
    "POSTGRES_PASSWORD": password,
    "GEV_DATABASE_URL": f"postgresql+psycopg://gev:{password}@postgres:5432/gev",
    "GEV_SESSION_SECRET": secrets.token_urlsafe(48),
    "GEV_CREDENTIAL_KEY": Fernet.generate_key().decode(),
    "GEV_REDIS_URL": "redis://redis:6379/0",
    "GEV_CACHE_ROOT": "/data/git",
    "GEV_PRODUCTION": "false",
    "GEV_COOKIE_SECURE": "false",
    "GEV_PORT": "8766",
}
with target.open("x") as file:
    file.write("".join(f"{key}={value}\n" for key, value in values.items()))
with (root / "demo-access.json").open("x") as file:
    json.dump({"url": "http://127.0.0.1:8766", "admin": {"username": "demo-admin", "password": secrets.token_urlsafe(18)},
               "member": {"username": "demo-member", "password": secrets.token_urlsafe(18)}}, file, indent=2)
print("Created runtime/local.env and runtime/demo-access.json (0600); existing files are never overwritten.")
