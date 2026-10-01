"""Single local worker and scheduler. Jobs survive restarts in the database."""
import logging
import signal
import subprocess
import sys
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import make_engine
from .jobs import repository_lock
from .models import SyncJob, utc_now
from .scheduler import tick


def next_job(settings, engine):
    with Session(engine) as session:
        rows = session.execute(select(SyncJob.id, SyncJob.repository_id).where(
            SyncJob.status == "queued",
            (SyncJob.retry_at.is_(None)) | (SyncJob.retry_at <= utc_now()),
        ).order_by(SyncJob.requested_at, SyncJob.id)).all()
    for job_id, repository_id in rows:
        with repository_lock(settings, repository_id, blocking=False) as acquired:
            if acquired:
                return job_id
    return None


def stop_child(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def terminate_job(*_):
    # Unwind GitService's BaseException cleanup so its separate Git process
    # group cannot survive a job timeout while retaining the repository lock.
    raise SystemExit(143)


def main():
    settings = Settings()
    if settings.job_backend != "database":
        raise ValueError("The bundled worker requires GEV_JOB_BACKEND=database")
    engine = make_engine(settings.database_url)
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    child = None
    scheduled = 0
    started = 0
    try:
        while not stopped.is_set():
            now = time.monotonic()
            if child is not None:
                if child.poll() is not None:
                    child = None
                    scheduled = 0  # Recover interrupted jobs through the existing outbox.
                elif now - started >= settings.job_timeout_seconds:
                    stop_child(child)
                    child = None
                    scheduled = 0
            try:
                if now >= scheduled:
                    tick(settings=settings)
                    scheduled = now + max(1, settings.scheduler_interval_seconds)
                if child is None:
                    job_id = next_job(settings, engine)
                    if job_id is not None:
                        child = subprocess.Popen([sys.executable, "-m", "app.worker", str(job_id)])
                        started = time.monotonic()
            except Exception:
                logging.warning("Background database scan failed; retrying")
            stopped.wait(1)
    finally:
        if child is not None:
            stop_child(child)
        engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) == 2:
        from .jobs import run_sync_job
        signal.signal(signal.SIGTERM, terminate_job)
        signal.signal(signal.SIGINT, terminate_job)
        run_sync_job(int(sys.argv[1]))
    else:
        main()
