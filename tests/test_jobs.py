from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import jobs
from app.config import Settings
from app.db import make_engine
from app.models import Base, PlatformConnection, Repository, SyncJob, utc_now


class Queue:
    def __init__(self):
        self.items = {}
        self.fail = False

    def ensure(self, job_id):
        if self.fail:
            raise ConnectionError("secret must not leak")
        self.items[job_id] = "queued"

    def state(self, job_id):
        if self.fail:
            raise ConnectionError("secret must not leak")
        return self.items.get(job_id)


@pytest.fixture
def system(tmp_path):
    settings = Settings(production=False, database_url=f"sqlite:///{tmp_path / 'db'}",
                        cache_root=str(tmp_path / "cache"))
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        connection = PlatformConnection(name="test", kind="github", base_url="https://github.com")
        session.add(connection)
        session.flush()
        repo = Repository(connection_id=connection.id, external_id="1", name="repo", full_name="team/repo",
                          clone_url="https://github.com/team/repo.git", web_url="https://github.com/team/repo", default_branch="main")
        session.add(repo)
        session.commit()
        repo_id = repo.id
    yield SimpleNamespace(settings=settings, engine=engine, repo_id=repo_id, queue=Queue(), tmp=tmp_path)
    engine.dispose()


def enqueue(system, **kwargs):
    with Session(system.engine) as session:
        return jobs.enqueue_sync(session, system.repo_id, settings=system.settings, queue=system.queue, **kwargs)


def test_outbox_is_durable_before_queue_call_and_duplicate_is_one_job(system):
    def ensure(job_id):
        with Session(system.engine) as observer:
            assert observer.get(SyncJob, job_id) is not None
        system.queue.items[job_id] = "queued"
    system.queue.ensure = ensure
    first = enqueue(system)
    second = enqueue(system, history_since=utc_now() - timedelta(days=365))
    assert first.id == second.id
    with Session(system.engine) as session:
        assert len(session.scalars(select(SyncJob)).all()) == 1
        assert session.get(SyncJob, first.id).history_since is not None


def test_queue_outage_and_lost_redis_jobs_recover(system):
    system.queue.fail = True
    job = enqueue(system)
    with Session(system.engine) as session:
        row = session.get(SyncJob, job.id)
        assert row.status == "pending" and row.enqueue_error == "Queue unavailable"
    system.queue.fail = False
    jobs.dispatch_pending(settings=system.settings, queue=system.queue)
    assert system.queue.items[job.id] == "queued"
    system.queue.items.clear()
    jobs.dispatch_pending(settings=system.settings, queue=system.queue)
    assert system.queue.items[job.id] == "queued"


def test_force_invalidates_running_generation_and_wider_history_is_not_lost(system):
    first = enqueue(system, history_since=utc_now() - timedelta(days=365))
    with Session(system.engine) as session:
        session.get(SyncJob, first.id).status = "running"
        session.commit()
    second = enqueue(system, force=True)
    assert second.id != first.id and second.generation > first.generation
    assert second.history_since == first.history_since


def test_dispatch_recovers_dead_worker_but_respects_live_repository_lock(system):
    job = enqueue(system)
    with Session(system.engine) as session:
        row = session.get(SyncJob, job.id)
        row.status = "running"
        row.heartbeat_at = utc_now() - timedelta(hours=2)
        row.attempts = 1
        session.commit()
    with jobs.repository_lock(system.settings, system.repo_id):
        jobs.dispatch_pending(settings=system.settings, queue=system.queue)
        with Session(system.engine) as session:
            assert session.get(SyncJob, job.id).status == "running"
    jobs.dispatch_pending(settings=system.settings, queue=system.queue)
    with Session(system.engine) as session:
        assert session.get(SyncJob, job.id).status == "queued"


def test_scheduler_skips_disabled_connections_and_continues_after_queue_failure(system):
    from app.scheduler import tick
    system.queue.fail = True
    tick(settings=system.settings, queue=system.queue)
    with Session(system.engine) as session:
        assert len(session.scalars(select(SyncJob)).all()) == 1
        session.get(PlatformConnection, 1).auth_disabled = True
        session.get(Repository, system.repo_id).next_sync_at = None
        session.commit()
    tick(settings=system.settings, queue=system.queue)
    with Session(system.engine) as session:
        assert len(session.scalars(select(SyncJob)).all()) == 1


def test_concurrent_requests_allocate_one_durable_job(system):
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: enqueue(system).id, range(8)))
    assert len(set(ids)) == 1
    with Session(system.engine) as session:
        assert session.get(Repository, system.repo_id).requested_generation == 1


@pytest.mark.parametrize("field", ["credential_key", "session_secret"])
def test_invalid_settings_do_not_render_submitted_secrets(field):
    with pytest.raises(ValueError) as error:
        Settings(_env_file=None, **{field: "SECRET_PROBE"})
    assert "SECRET_PROBE" not in str(error.value)
