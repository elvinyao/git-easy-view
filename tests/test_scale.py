"""Bounded synthetic exercise, explicitly not a production capacity benchmark."""
import json
import os
from pathlib import Path
import platform
import time

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import make_engine
from app.demo import create_demo
from app.git_service import GitService, clone_bare
from app.models import Branch, Comparison, PlatformConnection, Repository, SyncJob
from app.sync import _analyze, _refresh_cache, cache_path
from test_integration import postgres

pytestmark = pytest.mark.skipif(os.environ.get("GEV_SCALE") != "1", reason="100-repository fixture opt-in")


def test_hundred_real_bare_repositories_and_cache_reuse(postgres, tmp_path, monkeypatch):
    settings, _ = postgres
    calls = 0
    original = GitService.compare
    def observed(self, source, target):
        nonlocal calls
        calls += 1
        return original(self, source, target)
    monkeypatch.setattr(GitService, "compare", observed)
    started = time.monotonic()
    ids = create_demo(settings, admin_password="scale-admin", member_password="scale-member", repositories=100, branches=55)
    initial = time.monotonic() - started
    initial_calls = calls
    source = tmp_path / "stable-source.git"
    clone_bare(str(cache_path(settings, ids[0][0])), source, allow_file_transport=True)
    engine = make_engine(settings.database_url)
    started = time.monotonic()
    for rid, jid in ids:
        with Session(engine, expire_on_commit=False) as session:
            repository = session.get(Repository, rid)
            job = session.get(SyncJob, jid)
            connection = session.get(PlatformConnection, repository.connection_id)
        git = _refresh_cache(settings, repository, connection, None, local_source=source)
        heads, evidence, comparisons = _analyze(engine, job, git)
        assert len(heads) == 55 and len({head.oid for head in heads}) == 3
        assert len(evidence) == 55
        assert {c.state for c in comparisons} >= {"identical", "contained", "diverged"}
    repeat = time.monotonic() - started
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Repository)) == 100
        assert session.scalar(select(func.count()).select_from(Branch)) == 5500
        comparison_count = session.scalar(select(func.count()).select_from(Comparison))
    assert calls == initial_calls
    result = dict(repositories=100, branches_per_repository=55, total_refs=5500, distinct_oids_per_repo=3,
                  initial_seconds=round(initial, 3), repeat_fetch_and_analysis_seconds=round(repeat, 3),
                  initial_git_comparisons=initial_calls, repeated_git_comparisons=calls-initial_calls,
                  cached_comparison_rows=comparison_count,
                  cache_bytes=sum(p.stat().st_size for p in Path(settings.cache_root).rglob("*") if p.is_file()),
                  python=platform.python_version(), architecture=platform.machine(),
                  limitation="Tiny generated 3-commit histories, PostgreSQL + Docker local storage; not large-repository capacity evidence")
    directory = Path("runtime/verification")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "scale.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    engine.dispose()
