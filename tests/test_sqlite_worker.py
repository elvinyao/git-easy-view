"""Real subprocess queue execution."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.jobs import enqueue_comparison, repository_lock
from app.models import Comparison, PlatformConnection, SyncJob
from app.worker import next_job, stop_child
from test_jobs import system
from test_sync import remote, run, branch_rows


def test_production_accepts_persistent_sqlite_and_rejects_memory():
    kwargs = dict(production=True, session_secret="x" * 48, credential_key=Fernet.generate_key().decode())
    assert Settings(database_url="sqlite:////data/db/gev.sqlite3", **kwargs).cookie_secure
    for url in ("sqlite://", "sqlite:///:memory:", "sqlite:///relative.db", "sqlite:///file:db?mode=memory&uri=true"):
        with pytest.raises(ValueError):
            Settings(database_url=url, **kwargs)


def test_worker_recovers_abandoned_comparison_and_publishes(system, remote):
    run(system, remote)
    with Session(system.engine) as session:
        session.get(PlatformConnection, 1).auth_disabled = True  # Never contact a real provider.
        session.execute(delete(Comparison))
        branches = branch_rows(session, system)
        job = enqueue_comparison(session, system.repo_id, branches["feature/one"].id,
                                 branches["main"].id, settings=system.settings)
        job_id = job.id
        job.status = "running"  # Simulate a stopped process; no live file lock.
        job.attempts = 1
        session.commit()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GEV_")}
    env.update(GEV_PRODUCTION="false", GEV_DATABASE_URL=system.settings.database_url,
               GEV_CACHE_ROOT=system.settings.cache_root,
               GEV_SCHEDULER_INTERVAL_SECONDS="1")
    process = subprocess.Popen([sys.executable, "-m", "app.worker"], env=env)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            assert process.poll() is None
            with Session(system.engine) as session:
                row = session.get(SyncJob, job_id)
                if row.status == "succeeded":
                    assert row.attempts == 2
                    comparison = session.scalar(select(Comparison))
                    assert comparison.ahead == 1 and comparison.behind == 0
                    break
            time.sleep(0.1)
        else:
            pytest.fail("Database worker did not finish the recovered job")
        assert next_job(system.settings, system.engine) is None
    finally:
        stop_child(process)
    assert process.returncode == 0


def test_job_termination_stops_git_and_releases_repository_lock(system):
    """Exercise the actual job entry point and the timeout termination path."""
    ready = system.tmp / "held-git-ready"
    helper = system.tmp / "hold-git.sh"
    helper.write_text(f'#!/bin/sh\nprintf "%s" "$$" > "{ready}"\nexec sleep 120\n')
    helper.chmod(0o700)
    worker_code = '''
import runpy
import shlex
import sys
from app.config import Settings
from app.git_service import _run
from app import jobs
settings = Settings(_env_file=None, production=False, cache_root=sys.argv[1])
helper = sys.argv[2]
def held_job(job_id):
    with jobs.repository_lock(settings, 1):
        _run(["-c", "alias.hold=!" + shlex.quote(helper), "hold"], timeout=120)
jobs.run_sync_job = held_job
sys.argv = ["app.worker", "1"]
runpy.run_module("app.worker", run_name="__main__")
'''
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GEV_")}
    process = subprocess.Popen(
        [sys.executable, "-c", worker_code, system.settings.cache_root, str(helper)],
        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    git_group = None
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() or not ready.read_text():
            assert process.poll() is None, process.stderr.read().decode()
            assert time.monotonic() < deadline, "Git child did not become ready"
            time.sleep(0.02)
        git_group = os.getpgid(int(ready.read_text()))
        with repository_lock(system.settings, 1, blocking=False) as acquired:
            assert not acquired

        stop_child(process)  # The worker uses this exact path on job timeout.

        assert process.returncode == 143, process.stderr.read().decode()
        assert not Path(f"/proc/{git_group}").exists(), "Git group leader was not reaped"
        deadline = time.monotonic() + 5
        while True:
            with repository_lock(system.settings, 1, blocking=False) as acquired:
                if acquired:
                    break
            assert time.monotonic() < deadline, "Terminated Git retained the repository lock"
            time.sleep(0.02)
    finally:
        stop_child(process)
        process.stderr.close()
        if git_group is not None:
            try:
                os.killpg(git_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
