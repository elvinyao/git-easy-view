"""Durable database jobs and shared-volume repository locks.

Every process MUST use the same cache_root. File locks, rather
than an expiring heartbeat, determine whether repository work is alive.
"""
from contextlib import contextmanager
import fcntl
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings
from .db import make_engine
from .models import Branch, PlatformConnection, Repository, SyncJob, utc_now
from .process_locks import inherit_lock_for_git

ACTIVE = ("pending", "queued", "running", "retry")
TERMINAL = ("succeeded", "failed", "superseded", "auth_failed")


class DatabaseDelivery:
    """The committed SyncJob row is the queue; no external delivery is needed."""

    def ensure(self, job_id):
        pass


def delivery(settings):
    return DatabaseDelivery()


@contextmanager
def repository_lock(settings, repository_id, *, blocking=True):
    root = Path(settings.cache_root).resolve() / "locks"
    root.mkdir(parents=True, exist_ok=True)
    with (root / f"{int(repository_id)}.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        with inherit_lock_for_git(handle.fileno()):
            yield True
        # Closing releases our reference. Do not explicitly LOCK_UN: Git may
        # still own the same open-file description if this worker died or its
        # stack unwound. The kernel releases flock after the last holder exits.


def locked_repository(session, repository_id):
    # SQLite ignores FOR UPDATE; acquire its writer lock before reading.
    if session.bind.dialect.name == "sqlite":
        session.execute(update(Repository).where(Repository.id == repository_id)
                        .values(requested_generation=Repository.requested_generation))
    repo = session.scalar(select(Repository).where(Repository.id == repository_id)
                          .with_for_update().execution_options(populate_existing=True))
    if repo is None:
        raise ValueError("Repository does not exist")
    return repo


def _earliest(*values):
    return min((value for value in values if value is not None), default=None)


def _deliver(session, job, queue, *, settings=None, locked=False):
    if not locked:
        with repository_lock(settings, job.repository_id, blocking=False) as acquired:
            if not acquired:
                return job
            return _deliver(session, job, queue, locked=True)
    job_id = job.id
    try:
        queue.ensure(job_id)
    except Exception:
        # Never persist remote exceptions: they may contain URLs or credentials.
        session.execute(update(SyncJob).where(SyncJob.id == job_id, SyncJob.status.in_(("pending", "queued", "retry")))
                        .values(enqueue_error="Queue unavailable"))
    else:
        session.execute(update(SyncJob).where(SyncJob.id == job_id, SyncJob.status.in_(("pending", "queued", "retry")))
                        .values(status="queued", enqueued_at=utc_now(), enqueue_error=None))
    session.commit()
    session.refresh(job)
    return job


def enqueue_sync(session, repository_id, requested_by=None, history_since=None, *, force=False,
                 settings=None, queue=None):
    """Commit a request before delivery. Also commits caller's config changes.

    force=True is mandatory after admin config edits, invalidating old work.
    A wider backfill arriving during execution receives its own generation.
    """
    settings = settings or Settings()
    queue = queue if queue is not None else delivery(settings)
    repo = locked_repository(session, repository_id)
    if session.get(PlatformConnection, repo.connection_id).auth_disabled:
        raise ValueError("Connection credentials require administrator attention")
    active = session.scalars(select(SyncJob).where(SyncJob.repository_id == repository_id,
                             SyncJob.kind == "sync", SyncJob.status.in_(ACTIVE)).order_by(SyncJob.generation.desc())).all()
    latest = active[0] if active else None
    merged = _earliest(history_since, *(job.history_since for job in active))
    wider_running = latest and latest.status == "running" and merged != latest.history_since
    if latest and not force and not wider_running:
        latest.history_since = merged
        session.commit()
        return _deliver(session, latest, queue, settings=settings) if latest.status in ("pending", "queued") else latest
    repo.requested_generation += 1
    for old in active:
        if old.status != "running":
            old.status, old.finished_at = "superseded", utc_now()
    job = SyncJob(repository_id=repository_id, requested_by_id=getattr(requested_by, "id", requested_by),
                  status="pending", kind="sync", generation=repo.requested_generation, history_since=merged)
    session.add(job)
    session.commit()
    return _deliver(session, job, queue, settings=settings)


def enqueue_comparison(session, repository_id, source_branch_id, target_branch_id, *, requested_by=None,
                       settings=None, queue=None):
    """Local-only work; authorization belongs to the requesting web route."""
    settings = settings or Settings()
    queue = queue if queue is not None else delivery(settings)
    repo = locked_repository(session, repository_id)
    branches = [session.get(Branch, bid) for bid in (source_branch_id, target_branch_id)]
    if any(b is None or b.repository_id != repository_id or b.deleted or not b.head_oid for b in branches):
        raise ValueError("Comparison requires two live branches in the same repository")
    params = {"source_oid": branches[0].head_oid, "target_oid": branches[1].head_oid,
              "source_branch_id": source_branch_id, "target_branch_id": target_branch_id}
    active = session.scalars(select(SyncJob).where(SyncJob.repository_id == repository_id,
                            SyncJob.kind == "comparison", SyncJob.status.in_(ACTIVE))).all()
    job = next((job for job in active if job.parameters == params), None)
    if job is None:
        job = SyncJob(repository_id=repository_id, requested_by_id=getattr(requested_by, "id", requested_by),
                      kind="comparison", parameters=params, status="pending", generation=repo.requested_generation)
        session.add(job)
    session.commit()
    return _deliver(session, job, queue, settings=settings) if job.status in ("pending", "queued") else job


def dispatch_pending(*, settings=None, queue=None):
    settings = settings or Settings()
    queue = queue if queue is not None else delivery(settings)
    engine = make_engine(settings.database_url)
    try:
        with Session(engine) as session:
            ids = list(session.scalars(select(SyncJob.id).where(SyncJob.status.in_(ACTIVE))))
        for job_id in ids:
            try:
                with Session(engine) as session:
                    job = session.get(SyncJob, job_id)
                    with repository_lock(settings, job.repository_id, blocking=False) as acquired:
                        if not acquired:
                            continue
                        session.refresh(job)
                        if job.status not in ACTIVE or (job.retry_at and job.retry_at > utc_now()):
                            continue
                        repo = locked_repository(session, job.repository_id)
                        if job.kind == "sync" and repo.requested_generation != job.generation:
                            job.status, job.finished_at = "superseded", utc_now()
                        elif job.kind == "sync" and session.get(PlatformConnection, repo.connection_id).auth_disabled:
                            job.status, job.finished_at = "auth_failed", utc_now()
                        elif job.attempts >= job.max_attempts:
                            job.status, job.finished_at = "failed", utc_now()
                        else:
                            # No live lock holder: a 'running' row is abandoned.
                            job.status = "pending"
                        session.commit()
                        if job.status == "pending":
                            _deliver(session, job, queue, locked=True)
            except Exception:
                # One broken repository cannot stop the scan.
                continue
    finally:
        engine.dispose()


def run_sync_job(job_id, *, settings=None, **dependencies):
    from .sync import execute_job
    return execute_job(job_id, settings=settings or Settings(), **dependencies)
