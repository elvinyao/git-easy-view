"""Prepare stable Compose configuration using only Python's standard library.

Shared by Docker deployment entry points. Existing configuration is preserved.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import unquote, urlsplit


def positive_seconds(value: str) -> int:
    seconds = int(value)
    if seconds < 1:
        raise argparse.ArgumentTypeError("must be at least 1 second")
    return seconds


def argument_parser(prog="bash deploy/deploy.sh") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Prepare configuration, build, migrate and start the Docker stack.",
    )
    parser.add_argument("--production", action="store_true", help="use secure production settings; HTTPS proxy required")
    parser.add_argument("--env-file", help="relative runtime/*.env path; defaults to local.env or production.env")
    parser.add_argument("--port", type=int, help="host loopback port for a NEW configuration (local 8766, production 8767)")
    parser.add_argument("--project-name", help="Compose project name for a NEW configuration")
    parser.add_argument("--no-build", action="store_true", help="reuse the existing application image")
    parser.add_argument("--migrate-sqlite", action="store_true", help="copy the existing bundled PostgreSQL database to SQLite; retain old volumes")
    parser.add_argument("--pull", action="store_true", help="refresh the Python base image when building")
    parser.add_argument("--wait-timeout", type=positive_seconds, default=180, help="health wait per phase, in seconds (default 180)")
    return parser


def read_env(target: Path) -> dict[str, str]:
    """Read the literal KEY=value files used by this deployment helper."""
    values = {}
    for line in target.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
            raise ValueError(f"{target}: use one literal KEY=value assignment per line")
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"{target}: unmatched quotes in {key.strip()}")
            value = value[1:-1]
        if "${" in value or "\x00" in value:
            raise ValueError(f"{target}: variable interpolation is unsupported in {key.strip()}")
        if key.strip() in values:
            raise ValueError(f"{target}: duplicate field {key.strip()}")
        values[key.strip()] = value
    return values


def validate(values: dict[str, str], target: Path, args) -> None:
    actual_production = values.get("GEV_PRODUCTION", "true").lower() == "true"
    if actual_production != args.production:
        raise ValueError("Existing deployment mode differs; use --production for production. Configuration was preserved.")
    if args.production and values.get("GEV_COOKIE_SECURE", "true").lower() != "true":
        raise ValueError("Production requires GEV_COOKIE_SECURE=true")
    if len(values.get("GEV_SESSION_SECRET", "")) < 32 or values.get("GEV_SESSION_SECRET", "").startswith("REPLACE_"):
        raise ValueError("GEV_SESSION_SECRET must contain at least 32 characters")
    try:
        decoded = base64.b64decode(values["GEV_CREDENTIAL_KEY"], altchars=b"-_", validate=True)
        if len(decoded) != 32:
            raise ValueError()
    except (KeyError, ValueError):
        raise ValueError("GEV_CREDENTIAL_KEY must be a valid Fernet key") from None
    password = values.get("POSTGRES_PASSWORD", "")
    try:
        database = urlsplit(values.get("GEV_DATABASE_URL", ""))
        valid_database = (
            database.scheme == "postgresql+psycopg"
            and database.hostname == "postgres"
            and database.port == 5432
            and database.username == "gev"
            and database.path == "/gev"
            and unquote(database.password or "") == password
        )
    except ValueError:
        valid_database = False
    sqlite_database = values.get("GEV_DATABASE_URL") == "sqlite:////data/db/gev.sqlite3"
    if not sqlite_database and not (args.migrate_sqlite and valid_database and password):
        raise ValueError("Quick deploy requires sqlite:////data/db/gev.sqlite3; upgrade existing PostgreSQL with --migrate-sqlite")
    if sqlite_database and values.get("GEV_JOB_BACKEND", "database") != "database":
        raise ValueError("Single-container deployment requires GEV_JOB_BACKEND=database")
    if values.get("GEV_CACHE_ROOT") != "/data/git":
        raise ValueError("Configure GEV_CACHE_ROOT=/data/git")
    if values.get("GEV_ENV_FILE", "./runtime/local.env").removeprefix("./") != target.as_posix():
        raise ValueError("GEV_ENV_FILE must point to the same file passed with --env-file")
    project = values.get("COMPOSE_PROJECT_NAME", "git-easy-view")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise ValueError("Invalid COMPOSE_PROJECT_NAME; use lowercase letters, digits, hyphens or underscores")
    if target != Path("runtime/local.env") and "COMPOSE_PROJECT_NAME" not in values:
        raise ValueError("A custom environment needs an explicit COMPOSE_PROJECT_NAME to isolate its volumes")
    if args.project_name is not None and args.project_name != project:
        raise ValueError("--project-name cannot change an existing environment; edit its configuration explicitly")
    try:
        port = int(values.get("GEV_PORT", "8766"))
    except ValueError:
        raise ValueError("GEV_PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise ValueError("GEV_PORT must be between 1 and 65535")
    if args.port is not None and args.port != port:
        raise ValueError("--port cannot change an existing environment; edit its configuration explicitly")
    if args.production:
        local = Path("runtime/local.env")
        local_project = read_env(local).get("COMPOSE_PROJECT_NAME", "git-easy-view") if local.exists() else "git-easy-view"
        if project == local_project:
            raise ValueError("Production must have a different COMPOSE_PROJECT_NAME from the local deployment")


def prepare(args) -> tuple[Path, bool]:
    os.umask(0o077)
    target = Path(args.env_file or ("runtime/production.env" if args.production else "runtime/local.env"))
    if (
        not re.fullmatch(r"runtime/[A-Za-z0-9_./-]+\.env", target.as_posix())
        or ".." in target.parts
        or not target.resolve().is_relative_to(Path("runtime").resolve())
    ):
        raise ValueError("--env-file must be a relative .env path inside runtime/")
    if target.exists():
        values = read_env(target)
        validate(values, target, args)
        os.chmod(target, 0o600)
        created = False
    else:
        default = Path("runtime/production.env" if args.production else "runtime/local.env")
        if target != default and not args.project_name:
            raise ValueError("Specify --project-name when creating a custom --env-file")
        values = {
            "COMPOSE_PROJECT_NAME": args.project_name or ("git-easy-view-production" if args.production else "git-easy-view"),
            "GEV_ENV_FILE": f"./{target.as_posix()}",
            "GEV_DATABASE_URL": "sqlite:////data/db/gev.sqlite3",
            "GEV_SESSION_SECRET": secrets.token_urlsafe(48),
            "GEV_CREDENTIAL_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
            "GEV_JOB_BACKEND": "database",
            "GEV_CACHE_ROOT": "/data/git",
            "GEV_PRODUCTION": str(args.production).lower(),
            "GEV_COOKIE_SECURE": str(args.production).lower(),
            "GEV_PORT": str(args.port if args.port is not None else (8767 if args.production else 8766)),
        }
        project = values["COMPOSE_PROJECT_NAME"]
        values["GEV_APP_IMAGE"] = "git-easy-view:local" if project == "git-easy-view" else f"git-easy-view:{project}"
        validate(values, target, args)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x") as file:
            file.write("".join(f"{key}={value}\n" for key, value in values.items()))
        created = True
    Path("runtime/ca").mkdir(parents=True, exist_ok=True)
    Path("runtime/local-repositories").mkdir(parents=True, exist_ok=True)
    # This file only reserves local example passwords; seeding remains explicit.
    access = Path("runtime/demo-access.json")
    if target == Path("runtime/local.env") and not args.production and not access.exists():
        with access.open("x") as file:
            json.dump({
                "url": f"http://127.0.0.1:{values.get('GEV_PORT', '8766')}",
                "admin": {"username": "demo-admin", "password": secrets.token_urlsafe(18)},
                "member": {"username": "demo-member", "password": secrets.token_urlsafe(18)},
            }, file, indent=2)
        print("Created runtime/demo-access.json (0600); no accounts or example data were created.")
    return target, created


def main(argv=None, *, prog="bash deploy/deploy.sh") -> int:
    parser = argument_parser(prog)
    args = parser.parse_args(argv)
    try:
        target, created = prepare(args)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Configuration error: {error}\n")
    print(f"{'Created' if created else 'Preserved'} {target} (0600). Secrets are never printed or regenerated on redeploy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
