from contextlib import contextmanager
from datetime import timedelta
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import jobs, sync
from app.git_service import GitError
from app.models import Branch, BranchConfig, Comparison, PlatformConnection, PullRequest, Repository, SyncJob, utc_now
from app.providers import AuthenticationError, ProviderError, RateLimitError, Repo, PR
from test_jobs import system, enqueue


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def remote(system):
    path = system.tmp / "remote"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Fixture")
    git(path, "config", "user.email", "fixture@example.test")
    git(path, "commit", "--allow-empty", "-m", "root")
    git(path, "checkout", "-b", "feature/one")
    git(path, "commit", "--allow-empty", "-m", "feature")
    git(path, "checkout", "main")
    git(path, "branch", "release/one")
    return path


class Provider:
    web_root = "https://github.com"

    def __init__(self):
        self.prs = []
        self.failure = None
        self.before_list = None
        self.since = []

    def repository(self, external_id):
        return Repo("1", "renamed/repo", "https://github.com/renamed/repo", "https://github.com/renamed/repo.git", "main")

    def pull_requests(self, external_id, since, **kwargs):
        self.since.append(since)
        if self.before_list:
            self.before_list()
        if self.failure:
            raise self.failure
        return self.prs

    @contextmanager
    def factory(self, connection, token):
        yield self


def run(system, remote, provider=None, **kwargs):
    job = enqueue(system)
    jobs.run_sync_job(job.id, settings=system.settings, provider_factory=(provider or Provider()).factory,
                      local_source=remote, **kwargs)
    return job


def branch_rows(session, system):
    return {b.name: b for b in session.scalars(select(Branch).where(Branch.repository_id == system.repo_id))}


def pr(state="open", target="release/one"):
    return PR("123", "Example", "https://github.com/renamed/repo/pull/1", state, "1", "feature/one", None,
              "1", target, None, utc_now(), None)


def test_git_snapshot_and_fresh_api_targets_use_fixed_oids(system, remote):
    git(remote, "checkout", "release/one")
    git(remote, "commit", "--allow-empty", "-m", "release fix")
    git(remote, "checkout", "main")
    provider = Provider()
    provider.prs = [pr()]
    job = run(system, remote, provider)
    with Session(system.engine) as session:
        repo = session.get(Repository, system.repo_id)
        assert repo.git_synced_at and repo.api_synced_at
        assert repo.full_name == "renamed/repo"
        assert session.get(SyncJob, job.id).status == "succeeded"
        branches = branch_rows(session, system)
        feature, target = branches["feature/one"], branches["release/one"]
        comparison = session.scalar(select(Comparison).where(Comparison.source_oid == feature.head_oid,
                                                             Comparison.target_oid == target.head_oid))
        assert comparison.ahead == 1 and comparison.behind == 1
        assert feature.source_candidates[0]["name"] == "main"
        assert feature.source_candidates[0]["branch_oid"] == feature.head_oid


def test_failed_fetch_preserves_git_snapshot_but_api_advances(system, remote):
    run(system, remote)
    with Session(system.engine) as session:
        old_time = session.get(Repository, system.repo_id).git_synced_at
        old_oid = branch_rows(session, system)["feature/one"].head_oid
    git(remote, "checkout", "feature/one")
    git(remote, "commit", "--allow-empty", "-m", "new")
    def fail(*args, **kwargs):
        raise GitError("secret from remote")
    provider = Provider()
    provider.prs = [pr()]
    run(system, remote, provider, fetch=fail)
    with Session(system.engine) as session:
        repo = session.get(Repository, system.repo_id)
        assert repo.git_synced_at == old_time and repo.api_synced_at > old_time
        assert repo.sync_error == "Git synchronization failed"
        assert branch_rows(session, system)["feature/one"].head_oid == old_oid
        assert session.scalar(select(PullRequest)).state == "open"


def test_api_failure_keeps_coverage_and_old_plans_but_git_advances(system, remote):
    provider = Provider()
    provider.prs = [pr()]
    run(system, remote, provider)
    with Session(system.engine) as session:
        old = session.get(Repository, system.repo_id).api_synced_at
        coverage = session.get(Repository, system.repo_id).api_coverage_since
    provider.failure = ProviderError("token should not be stored")
    git(remote, "checkout", "feature/one")
    git(remote, "commit", "--allow-empty", "-m", "new")
    run(system, remote, provider)
    with Session(system.engine) as session:
        repo = session.get(Repository, system.repo_id)
        assert repo.git_synced_at > old and repo.api_synced_at == old
        assert repo.api_coverage_since == coverage and repo.api_error == "Platform synchronization failed"
        assert branch_rows(session, system)["feature/one"].head_oid == git(remote, "rev-parse", "HEAD")
        assert session.scalar(select(PullRequest)).state == "open"


def test_deleted_branches_clear_current_evidence_and_recreated_heads_recompute(system, remote):
    run(system, remote)
    git(remote, "branch", "-D", "feature/one")
    run(system, remote)
    with Session(system.engine) as session:
        branch = branch_rows(session, system)["feature/one"]
        assert branch.deleted and branch.head_oid is None and branch.source_candidates == []
    git(remote, "branch", "feature/one")
    run(system, remote)
    with Session(system.engine) as session:
        branch = branch_rows(session, system)["feature/one"]
        assert not branch.deleted and branch.head_oid == git(remote, "rev-parse", "main")


def test_new_requested_generation_blocks_old_publications(system, remote):
    provider = Provider()
    provider.before_list = lambda: enqueue(system, force=True)
    job = run(system, remote, provider)
    with Session(system.engine) as session:
        repo = session.get(Repository, system.repo_id)
        assert repo.snapshot_generation == 0 and repo.api_generation == 0
        assert session.get(SyncJob, job.id).status == "superseded"
        assert branch_rows(session, system) == {}


def test_authentication_failure_disables_automatic_retry_but_git_can_publish(system, remote):
    provider = Provider()
    provider.failure = AuthenticationError("secret")
    job = run(system, remote, provider)
    with Session(system.engine) as session:
        assert session.get(PlatformConnection, 1).auth_disabled
        assert session.get(SyncJob, job.id).status == "auth_failed"
        assert session.get(Repository, system.repo_id).git_synced_at
    with pytest.raises(ValueError, match="credentials"):
        enqueue(system)


def test_rate_limit_and_retry_attempts_are_bounded(system, remote):
    provider = Provider()
    provider.failure = RateLimitError(retry_after=600)
    start = utc_now()
    job = run(system, remote, provider)
    with Session(system.engine) as session:
        row = session.get(SyncJob, job.id)
        assert row.status == "retry" and row.retry_at >= start + timedelta(seconds=600)
        row.attempts = row.max_attempts - 1
        row.retry_at = utc_now() - timedelta(seconds=1)
        session.commit()
    jobs.run_sync_job(job.id, settings=system.settings, provider_factory=provider.factory, local_source=remote)
    with Session(system.engine) as session:
        assert session.get(SyncJob, job.id).status == "failed"


def test_outage_window_backfill_and_missing_open_pr_have_honest_coverage(system, remote):
    provider = Provider()
    provider.prs = [pr()]
    run(system, remote, provider)
    long_ago = utc_now() - timedelta(days=150)
    with Session(system.engine) as session:
        repo = session.get(Repository, system.repo_id)
        repo.api_synced_at = long_ago
        session.commit()
    provider.prs = []
    run(system, remote, provider)
    assert provider.since[-1] <= long_ago
    with Session(system.engine) as session:
        assert session.scalar(select(PullRequest)).state == "unknown"
    requested = utc_now() - timedelta(days=400)
    job = enqueue(system, history_since=requested)
    jobs.run_sync_job(job.id, settings=system.settings, provider_factory=provider.factory, local_source=remote)
    run(system, remote, provider)
    assert provider.since[-1] > requested + timedelta(days=300)
    with Session(system.engine) as session:
        assert session.get(Repository, system.repo_id).api_coverage_since <= requested


def test_on_demand_comparison_is_local_and_does_not_invalidate_sync(system, remote):
    run(system, remote)
    pending = enqueue(system)
    with Session(system.engine) as session:
        branches = branch_rows(session, system)
        job = jobs.enqueue_comparison(session, system.repo_id, branches["main"].id, branches["feature/one"].id,
                                      settings=system.settings, queue=system.queue)
        assert session.get(Repository, system.repo_id).requested_generation == pending.generation
        source_oid, target_oid = branches["feature/one"].head_oid, branches["main"].head_oid
    def forbidden(*args, **kwargs):
        pytest.fail("Comparison work must never contact a remote")
    jobs.run_sync_job(job.id, settings=system.settings, provider_factory=forbidden, fetch=forbidden)
    with Session(system.engine) as session:
        assert session.get(SyncJob, job.id).status == "succeeded"
        assert session.get(SyncJob, pending.id).status == "queued"
    commits = sync.read_unique_commits(system.settings, system.repo_id, source_oid, target_oid)
    assert len(commits) == 1 and commits[0].subject == "feature"


def test_local_transport_is_never_accepted_in_production(system, remote):
    system.settings.production = True
    job = enqueue(system)
    with pytest.raises(ValueError, match="development"):
        jobs.run_sync_job(job.id, settings=system.settings, provider_factory=Provider().factory, local_source=remote)


def test_old_auth_failure_cannot_disable_rotated_credentials(system, remote):
    provider = Provider()
    provider.failure = AuthenticationError("expired previous token")
    def rotate():
        with Session(system.engine) as session:
            connection = session.get(PlatformConnection, 1)
            connection.encrypted_token = system.settings.encrypt_token("fresh-token")
            connection.last_checked_at = utc_now()
            connection.last_error = None
            session.commit()
    provider.before_list = rotate
    job = run(system, remote, provider)
    with Session(system.engine) as session:
        assert not session.get(PlatformConnection, 1).auth_disabled
        assert session.get(PlatformConnection, 1).last_error is None
        assert session.get(SyncJob, job.id).status == "superseded"


def test_partial_clone_is_discarded_and_successful_retry_is_idempotent(system, remote):
    def fail(source, destination, **kwargs):
        destination.mkdir()
        (destination / "partial").write_text("interrupted")
        raise GitError("remote secret")
    job = run(system, remote, clone=fail)
    assert not sync.cache_path(system.settings, system.repo_id).exists()
    with Session(system.engine) as session:
        row = session.get(SyncJob, job.id)
        assert row.status == "retry"
        row.retry_at = utc_now() - timedelta(seconds=1)
        session.commit()
    jobs.run_sync_job(job.id, settings=system.settings, local_source=remote, provider_factory=Provider().factory)
    with Session(system.engine) as session:
        old_time = session.get(Repository, system.repo_id).git_synced_at
        count = len(session.scalars(select(Comparison)).all())
    jobs.run_sync_job(job.id, settings=system.settings, provider_factory=lambda *_: pytest.fail("terminal delivery repeated"))
    with Session(system.engine) as session:
        assert session.get(SyncJob, job.id).status == "succeeded"
        assert session.get(Repository, system.repo_id).git_synced_at == old_time
        assert len(session.scalars(select(Comparison)).all()) == count


def test_active_release_manual_claim_roles_and_oid_cache_reuse(system, remote, monkeypatch):
    run(system, remote)
    with Session(system.engine) as session:
        branches = branch_rows(session, system)
        session.add(BranchConfig(branch_id=branches["release/one"].id, active_release=True))
        session.add(BranchConfig(branch_id=branches["feature/one"].id, role="other",
                                 manual_source_branch_id=branches["release/one"].id,
                                 manual_targets=[branches["release/one"].id],
                                 main_comparison_branch_id=branches["release/one"].id))
        session.commit()
    monkeypatch.setattr(sync.GitService, "compare", lambda *_: pytest.fail("OID cache should be reused"))
    run(system, remote)
    with Session(system.engine) as session:
        branches = branch_rows(session, system)
        feature = branches["feature/one"]
        assert sync.branch_role(session.get(Repository, system.repo_id), feature, feature.config) == "other"
        assert [(c["name"], c["kind"]) for c in feature.source_candidates] == [
            ("main", "inferred"), ("release/one", "inferred"), ("release/one", "manual")]


def test_partial_api_traversal_preserves_records_and_coverage(system, remote):
    provider = Provider()
    provider.prs = [pr()]
    run(system, remote, provider)
    with Session(system.engine) as session:
        old = session.get(Repository, system.repo_id).api_synced_at
    def partial(*args, **kwargs):
        yield pr("closed")
        raise ProviderError("second page failed")
    provider.pull_requests = partial
    run(system, remote, provider)
    with Session(system.engine) as session:
        assert session.scalar(select(PullRequest)).state == "open"
        assert session.get(Repository, system.repo_id).api_synced_at == old


def test_credentials_only_reach_temporary_askpass_environment(system, remote):
    from app.git_service import clone_bare
    with Session(system.engine) as session:
        session.get(PlatformConnection, 1).encrypted_token = system.settings.encrypt_token("top-secret")
        session.commit()
    helpers = []
    def inspect_clone(source, destination, *, env, **kwargs):
        from pathlib import Path
        helper = Path(env["GIT_ASKPASS"])
        helpers.append(helper)
        assert env["GEV_GIT_TOKEN"] == "top-secret"
        assert "top-secret" not in source and "top-secret" not in helper.read_text()
        clone_bare(source, destination, env=env, **kwargs)
    run(system, remote, clone=inspect_clone)
    assert helpers and not helpers[0].exists()


def test_unique_commit_read_returns_busy_instead_of_waiting_for_sync(system, remote):
    run(system, remote)
    main_oid = git(remote, "rev-parse", "main")
    source_oid = git(remote, "rev-parse", "feature/one")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with jobs.repository_lock(system.settings, system.repo_id):
            future = pool.submit(sync.read_unique_commits, system.settings, system.repo_id, source_oid, main_oid)
            with pytest.raises(RuntimeError, match="busy"):
                future.result(timeout=0.2)


def test_single_main_branch_has_identical_comparison(system, remote):
    git(remote, "branch", "-D", "release/one", "feature/one")
    run(system, remote)
    with Session(system.engine) as session:
        row = session.scalar(select(Comparison))
        assert row is not None and row.state == "identical" and row.ahead == row.behind == 0
