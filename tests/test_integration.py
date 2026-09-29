"""Real PostgreSQL migrations and isolated Redis/RQ failures; opt-in only."""
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

import pytest
from redis import Redis
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import make_engine
from app.demo import create_demo
from app.jobs import RQDelivery, dispatch_pending, enqueue_comparison
from app.models import Branch, Comparison, SyncJob

pytestmark = pytest.mark.skipif(os.environ.get("GEV_INTEGRATION") != "1", reason="requires isolated real services")


@pytest.fixture
def postgres(tmp_path, monkeypatch):
    url = make_url(os.environ["GEV_DATABASE_URL"])
    assert url.get_backend_name() == "postgresql"
    database = "gev_test_" + uuid.uuid4().hex
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    settings = Settings(production=False, database_url=url.set(database=database).render_as_string(hide_password=False), cache_root=str(tmp_path / "cache"))
    monkeypatch.setenv("GEV_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("GEV_PRODUCTION", "false")
    monkeypatch.setenv("GEV_CACHE_ROOT", settings.cache_root)
    from alembic import command
    from alembic.config import Config
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    try:
        yield settings, config
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        admin.dispose()


def test_real_postgres_migrations_up_down_up(postgres):
    from alembic import command
    settings, config = postgres
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)
    engine = make_engine(settings.database_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
    engine.dispose()


@pytest.fixture
def redis_process(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    processes = []
    connection = Redis(host="127.0.0.1", port=port, socket_timeout=1, socket_connect_timeout=1)
    def start():
        process = subprocess.Popen(["redis-server", "--port", str(port), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no", "--dir", str(tmp_path)], stdout=subprocess.DEVNULL)
        processes.append(process)
        for _ in range(100):
            try:
                if connection.ping(): return process
            except Exception: pass
            time.sleep(.05)
        raise AssertionError("Redis did not become ready")
    start()
    try:
        yield connection, f"redis://127.0.0.1:{port}/0", start, processes
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def worker(settings, **extra):
    env = dict(os.environ, GEV_DATABASE_URL=settings.database_url, GEV_REDIS_URL=settings.redis_url,
               GEV_QUEUE_NAME=settings.queue_name, GEV_CACHE_ROOT=settings.cache_root, GEV_PRODUCTION="false",
               PYTHONPATH=str(Path.cwd()), **extra)
    return subprocess.Popen([sys.executable, "tests/rq_worker_probe.py"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_real_rq_outage_lost_delivery_crash_restart_and_two_workers(postgres, redis_process, tmp_path):
    settings, _ = postgres
    redis, settings.redis_url, restart, processes = redis_process
    settings.queue_name = "verification-" + uuid.uuid4().hex
    create_demo(settings, admin_password="integration-admin", member_password="integration-member", repositories=2, branches=5)
    engine = make_engine(settings.database_url)
    queue = RQDelivery(settings)
    with Session(engine) as session:
        session.execute(delete(Comparison))
        session.commit()
        branches = list(session.scalars(select(Branch).order_by(Branch.id)))
        pairs = [(next(b for b in branches if b.repository_id == rid and b.name == "feature/login"), next(b for b in branches if b.repository_id == rid and b.name == "main")) for rid in (1, 2)]
        pairs = [(a.repository_id, a.id, b.id) for a, b in pairs]
    # Actual Redis process outage: outbox remains durable and reads remain possible.
    processes[-1].terminate()
    processes[-1].wait(timeout=10)
    with Session(engine) as session:
        job = enqueue_comparison(session, *pairs[0], settings=settings, queue=queue)
        first = job.id
        assert job.status == "pending" and job.enqueue_error
        assert session.get(Branch, pairs[0][1]) is not None
    from fastapi.testclient import TestClient
    from app.main import create_app
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": "demo-member", "password": "integration-member", "csrf_token": csrf}).status_code == 200
        assert client.get("/projects/1").status_code == 200
        assert client.get("/health").status_code == 200
    app.state.engine.dispose()
    restart()
    dispatch_pending(settings=settings)
    assert queue.state(first) == "queued"
    # Simulate a delivery lost while its job hash survives.
    redis.lrem(queue.queue.key, 0, queue.key(first))
    assert queue.state(first) is None
    dispatch_pending(settings=settings)
    assert queue.state(first) == "queued"
    crashing = worker(settings, GEV_PROBE_CRASH="1")
    assert crashing.wait(timeout=30) == 0
    with Session(engine) as session:
        assert session.get(SyncJob, first).status == "running"
    dispatch_pending(settings=settings)
    recovered = worker(settings)
    assert recovered.wait(timeout=30) == 0
    with Session(engine) as session:
        assert session.get(SyncJob, first).status == "succeeded"
        assert session.get(SyncJob, first).attempts == 2
        session.execute(delete(Comparison))
        session.commit()
        jobs = [enqueue_comparison(session, *pair, settings=settings, queue=queue).id for pair in pairs]
    # Redis restart loses all its state; dispatcher recreates both deliveries.
    processes[-1].terminate()
    processes[-1].wait(timeout=10)
    restart()
    assert all(queue.state(jid) is None for jid in jobs)
    dispatch_pending(settings=settings)
    probe_dir = tmp_path / "probes"
    probe_dir.mkdir()
    workers = [worker(settings, GEV_PROBE_DIRECTORY=str(probe_dir)) for _ in range(2)]
    assert all(process.wait(timeout=30) == 0 for process in workers)
    spans = [json.loads(path.read_text()) for path in probe_dir.glob("*.json")]
    assert len(spans) == 2 and max(span[0] for span in spans) < min(span[1] for span in spans)
    with Session(engine) as session:
        assert all(session.get(SyncJob, jid).status == "succeeded" for jid in jobs)
    engine.dispose()
    print("REAL SERVICES: PostgreSQL; Redis 7.4 outage/restart; lost queue entry; SIGKILL workhorse recovered; two overlapping RQ workers")
