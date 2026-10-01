"""Offline, verified transfer. Called by deploy after stopping all writers."""
import argparse
from contextlib import closing
import importlib.util
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

from sqlalchemy import select, text

from app.db import make_engine
from app.models import Base


def copy_database(source_url, target):
    """Copy typed rows in one source snapshot; never overwrite an existing DB."""
    if target.exists():
        raise ValueError("SQLite destination already exists; it was preserved. Inspect before retrying.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".transfer-{uuid.uuid4().hex}.sqlite3")
    source = make_engine(source_url)
    destination = None
    try:
        env = dict(os.environ, GEV_DATABASE_URL=f"sqlite:///{temporary.resolve()}")
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
        destination = make_engine(env["GEV_DATABASE_URL"])
        counts = {}
        with source.connect() as src, destination.begin() as dst:
            if source.dialect.name == "postgresql":
                src = src.execution_options(isolation_level="REPEATABLE READ")
            with src.begin():
                if src.execute(text("SELECT version_num FROM alembic_version")).scalar_one() != dst.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one():
                    raise ValueError("Source schema must be at the current migration revision")
                for table in Base.metadata.sorted_tables:
                    rows = [dict(row) for row in src.execute(select(table).order_by(*table.primary_key)).mappings()]
                    if rows:
                        dst.execute(table.insert(), rows)
                    copied = [dict(row) for row in dst.execute(select(table).order_by(*table.primary_key)).mappings()]
                    if copied != rows:
                        raise ValueError(f"Verification failed for {table.name}")
                    counts[table.name] = len(rows)
                if dst.execute(text("PRAGMA foreign_key_check")).all():
                    raise ValueError("Foreign key verification failed")
        destination.dispose()
        with closing(sqlite3.connect(temporary)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity verification failed")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.chmod(temporary, 0o600)
        # link is atomic and refuses an existing target, including a raced creation.
        os.link(temporary, target)
        return counts
    finally:
        source.dispose()
        if destination is not None:
            destination.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(str(temporary) + suffix).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location("setup", Path(__file__).with_name("setup-local.py"))
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    target = Path(args.env_file)
    values = setup.read_env(target)
    if values["GEV_DATABASE_URL"] == "sqlite:////data/db/gev.sqlite3":
        print("SQLite already configured; no transfer needed.")
        return
    original = target.read_bytes()
    backup = target.with_name(target.name + ".postgres-backup")
    if backup.exists() and backup.read_bytes() != original:
        raise ValueError("A different PostgreSQL configuration backup exists; preserved")
    if not backup.exists():
        with backup.open("xb") as file:
            file.write(original)
    counts = copy_database(values["GEV_DATABASE_URL"], Path("/data/db/gev.sqlite3"))
    # Preserve literal quoting and comments on every unrelated line. Rewriting
    # parsed secrets could change Compose's handling of dollar signs or quotes.
    changed = {"GEV_DATABASE_URL": "sqlite:////data/db/gev.sqlite3", "GEV_JOB_BACKEND": "database"}
    lines = []
    for line in original.decode().splitlines(keepends=True):
        match = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=", line)
        key = match[1] if match else None
        if key in ("POSTGRES_PASSWORD", "GEV_REDIS_URL", "GEV_JOB_BACKEND"):
            continue
        lines.append(f"{key}={changed[key]}\n" if key in changed else line)
    updated = "".join(lines).rstrip("\n") + "\nGEV_JOB_BACKEND=database\n"
    replacement = target.with_name(target.name + ".sqlite-new")
    with replacement.open("x") as file:
        file.write(updated)
        file.flush()
        os.fsync(file.fileno())
    if target.read_bytes() != original:
        raise ValueError("Configuration changed during transfer; refusing to replace it")
    replacement.replace(target)
    print("Verified SQLite transfer:", counts)
    print("Preserved original environment and PostgreSQL volume. Session and credential keys unchanged.")


if __name__ == "__main__":
    main()
