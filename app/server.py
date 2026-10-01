"""One container: migrate, then supervise HTTP and the database worker."""
import signal
import subprocess
import sys
import threading
from pathlib import Path

from sqlalchemy.engine import make_url

from .config import Settings
from .worker import stop_child


def main():
    settings = Settings()
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite" and url.database:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    children = []
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "app.worker"]))
        children.append(subprocess.Popen([
            sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
            "--host", "0.0.0.0", "--port", "8000",
        ]))
        while not stopped.wait(0.5):
            if any(child.poll() is not None for child in children):
                return 1  # Docker restarts the whole application if either process exits.
        return 0
    finally:
        for child in reversed(children):
            stop_child(child)


if __name__ == "__main__":
    raise SystemExit(main())
