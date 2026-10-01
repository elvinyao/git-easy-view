"""Scheduling shared by the bundled database worker and legacy RQ deployments."""
import signal
import threading
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .db import make_engine
from .jobs import dispatch_pending, enqueue_sync, locked_repository, delivery
from .models import PlatformConnection, Repository, utc_now


def tick(*, settings=None, queue=None):
    settings = settings or Settings()
    queue = queue if queue is not None else delivery(settings)
    dispatch_pending(settings=settings, queue=queue)
    engine = make_engine(settings.database_url)
    try:
        with Session(engine) as session:
            ids = list(session.scalars(select(Repository.id).join(PlatformConnection)
                       .where(PlatformConnection.auth_disabled.is_(False),
                              or_(Repository.next_sync_at.is_(None), Repository.next_sync_at <= utc_now()))))
        for repo_id in ids:
            try:
                with Session(engine) as session:
                    repo = locked_repository(session, repo_id)
                    if repo.next_sync_at and repo.next_sync_at > utc_now():
                        continue
                    repo.next_sync_at = utc_now() + timedelta(seconds=max(1, repo.sync_interval_seconds))
                    enqueue_sync(session, repo_id, settings=settings, queue=queue)
            except Exception:
                continue
    finally:
        engine.dispose()


def main():
    settings = Settings()
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            tick(settings=settings)
        except Exception:
            pass  # A database outage must not permanently kill scheduling.
        stopped.wait(max(1, min(60, settings.scheduler_interval_seconds)))


if __name__ == "__main__":
    main()
