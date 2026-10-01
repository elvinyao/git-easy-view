"""Backup SQLite and matching secrets; restore only while the app is stopped."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3

from sqlalchemy.engine import make_url
from app.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--env-file", default="runtime/local.env", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    url = make_url(Settings().database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError("This helper requires SQLite")
    database = Path(url.database)
    archive = args.directory / "gev.sqlite3"
    config = args.directory / "deployment.env"
    if args.operation == "backup":
        args.directory.mkdir(parents=True, exist_ok=False)
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(archive)) as destination:
                source.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup integrity check failed")
        shutil.copyfile(args.env_file, config)
        os.chmod(archive, 0o600)
        os.chmod(config, 0o600)
        print("Backed up SQLite and matching environment to", args.directory)
    else:
        # A different deployment environment must not accidentally replace this one.
        if args.env_file.read_bytes() != config.read_bytes():
            raise ValueError("Environment differs from backup; restore the matching configuration explicitly first")
        temporary = database.with_name("restore.sqlite3")
        with temporary.open("xb"):
            pass
        try:
            with closing(sqlite3.connect(archive.resolve().as_uri() + "?mode=ro", uri=True)) as source:
                if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup integrity check failed; original database preserved")
                with closing(sqlite3.connect(temporary)) as destination:
                    source.backup(destination)
            for suffix in ("-wal", "-shm"):
                Path(str(database) + suffix).unlink(missing_ok=True)
            temporary.replace(database)
        finally:
            temporary.unlink(missing_ok=True)
        print("Restored SQLite. Start the application with deploy --no-build.")


if __name__ == "__main__":
    main()
