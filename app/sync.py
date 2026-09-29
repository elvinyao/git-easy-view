"""Independent, generation-guarded Git and provider snapshots.

No repository worktree is created. All history analysis uses captured immutable
OIDs. Credentials live only in a worker's request context and askpass env.
"""
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
import tempfile
import threading

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from .db import make_engine
from .git_service import GitService, ComparisonResult, clone_bare, fetch_bare, _run
from .jobs import TERMINAL, locked_repository, repository_lock
from .models import Branch, BranchConfig, Comparison, PlatformConnection, PullRequest, Repository, SyncJob, utc_now
from .providers import AuthenticationError, RateLimitError, get_provider, validate_remote_url
from .providers.base import Provider
from .relations import BranchRef, PullRequestRef, RULE_VERSION, infer_source_candidates, manual_source_claim, planned_targets


class Superseded(Exception):
    pass


class RepositoryBusy(RuntimeError):
    """The cache is being refreshed; a web caller should offer a retry."""


def cache_path(settings, repository_id):
    return Path(settings.cache_root).resolve() / f"{int(repository_id)}.git"


def branch_role(repository, branch, config=None):
    """UI-compatible role resolution; configured role wins over name rules."""
    if config is not None and config.role:
        return config.role
    if branch.name == repository.default_branch:
        return "main"
    for role, prefixes in (repository.role_prefixes or {}).items():
        if role in ("release", "feature") and any(branch.name.startswith(p) for p in prefixes if isinstance(p, str) and p):
            return role
    return "other"


@contextmanager
def credential_environment(connection, token):
    # The script contains no credential; only the process environment does.
    with tempfile.TemporaryDirectory(prefix="gev-askpass-") as directory:
        helper = Path(directory) / "askpass"
        helper.write_text('#!/bin/sh\ncase "$1" in\n*Username*) printf "%s\\n" "$GEV_GIT_USERNAME" ;;\n*) printf "%s\\n" "$GEV_GIT_TOKEN" ;;\nesac\n')
        helper.chmod(0o700)
        env = {"GIT_ASKPASS": str(helper), "GEV_GIT_TOKEN": token or "",
               "GEV_GIT_USERNAME": "x-access-token" if connection.kind == "github" else "oauth2"}
        if connection.ca_bundle_path:
            env["GIT_SSL_CAINFO"] = connection.ca_bundle_path
        yield env


@contextmanager
def heartbeat(engine, job_id, seconds):
    stopped = threading.Event()
    def beat():
        while not stopped.wait(max(1, seconds)):
            try:
                with Session(engine) as session:
                    session.execute(update(SyncJob).where(SyncJob.id == job_id, SyncJob.status == "running")
                                    .values(heartbeat_at=utc_now()))
                    session.commit()
            except Exception:
                pass  # File lock remains authoritative even during DB outages.
    thread = threading.Thread(target=beat, name=f"sync-heartbeat-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=6)


def _guard(session, job):
    repo = locked_repository(session, job.repository_id)
    if repo.requested_generation != job.generation:
        raise Superseded()
    return repo


def _connection_guard(session, captured):
    current = session.scalar(select(PlatformConnection).where(PlatformConnection.id == captured.id)
                             .with_for_update().execution_options(populate_existing=True))
    identity = ("encrypted_token", "base_url", "kind", "ca_bundle_path", "last_checked_at")
    if current is None or any(getattr(current, key) != getattr(captured, key) for key in identity):
        raise Superseded()
    return current


def _api_snapshot(engine, job, connection, token, provider_factory):
    with Session(engine) as session:
        repo = session.get(Repository, job.repository_id)
        started = utc_now()
        since = min(value for value in (started - timedelta(days=90), repo.api_synced_at, job.history_since) if value is not None)
        external_id, kind = repo.external_id, connection.kind
    with provider_factory(connection, token) as provider:
        metadata = provider.repository(external_id)
        if metadata.external_id != external_id:
            raise ValueError("Provider repository identity changed")
        validate_remote_url(metadata.clone_url, provider.web_root)
        validate_remote_url(metadata.web_url, provider.web_root)
        kwargs = {"full_name": metadata.full_name} if kind == "github" else {}
        records = list(provider.pull_requests(external_id, since, **kwargs))
    # Full traversal completed. Publish all records and coverage together.
    with Session(engine) as session:
        repo = _guard(session, job)
        _connection_guard(session, connection)
        repo.full_name, repo.name = metadata.full_name, metadata.full_name.rsplit("/", 1)[-1]
        repo.web_url, repo.clone_url, repo.default_branch = metadata.web_url, metadata.clone_url, metadata.default_branch
        repo_map = {r.external_id: r.id for r in session.scalars(select(Repository).where(Repository.connection_id == connection.id))}
        seen = set()
        for record in records:
            row = session.scalar(select(PullRequest).where(PullRequest.connection_id == connection.id,
                                                          PullRequest.external_id == record.external_id))
            if row is None:
                row = PullRequest(connection_id=connection.id, external_id=record.external_id, repository_id=repo.id)
                session.add(row)
            seen.add(record.external_id)
            for attr in ("title", "web_url", "state", "source_branch", "source_oid", "target_branch", "merge_oid", "updated_at", "merged_at"):
                setattr(row, attr, getattr(record, attr))
            row.source_external_repo_id, row.target_external_repo_id = record.source_repo_id, record.target_repo_id
            row.source_repository_id = repo_map.get(record.source_repo_id)
            row.target_repository_id = repo_map.get(record.target_repo_id)
            row.fetched_at, row.api_generation = started, job.generation
        for old in session.scalars(select(PullRequest).where(PullRequest.repository_id == repo.id,
                                     PullRequest.state.in_(("open", "opened")))):
            if old.external_id not in seen:
                old.state, old.fetched_at, old.api_generation = "unknown", started, job.generation
        repo.api_synced_at, repo.api_generation, repo.api_error = started, job.generation, None
        repo.api_coverage_since = min(since, repo.api_coverage_since) if repo.api_coverage_since else since
        session.commit()


def _refresh_cache(settings, repo, connection, token, *, local_source=None, fetch=fetch_bare, clone=clone_bare):
    path = cache_path(settings, repo.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if local_source is None:
        with Provider(connection, token) as provider:
            source = validate_remote_url(repo.clone_url, provider.web_root)
    else:
        source = str(local_source)
    with credential_environment(connection, token) as env:
        if not path.exists():
            with tempfile.TemporaryDirectory(prefix=f".{repo.id}-clone-", dir=path.parent) as directory:
                staged = Path(directory) / "repository.git"
                clone(source, staged, env=env, allow_file_transport=local_source is not None)
                staged.rename(path)
        else:
            # Revalidate approved metadata every time, including renamed repos;
            # never trust an on-disk origin changed outside the application.
            _run(["-C", str(path), "config", "remote.origin.url", source])
            fetch(path, env=env, allow_file_transport=local_source is not None)
    return GitService(path)


def _comparison(session, repository_id, source_oid, target_oid):
    return session.scalar(select(Comparison).where(Comparison.repository_id == repository_id,
                          Comparison.source_oid == source_oid, Comparison.target_oid == target_oid,
                          Comparison.rule_version == RULE_VERSION))


def _put_comparison(session, repo_id, result, source_id=None, target_id=None):
    row = _comparison(session, repo_id, result.source_oid, result.target_oid)
    if row is None:
        row = Comparison(repository_id=repo_id, source_branch_id=source_id, target_branch_id=target_id,
                         source_oid=result.source_oid, target_oid=result.target_oid, ahead=result.ahead,
                         behind=result.behind, state=result.state, merge_bases=list(result.merge_bases), rule_version=RULE_VERSION)
        session.add(row)
    return row


def _analyze(engine, job, git):
    heads = git.branches()  # Capture exactly once; never compare moving refs.
    current_oids = {head.oid for head in heads}
    with Session(engine) as session:
        repo = session.get(Repository, job.repository_id)
        branches = {b.name: b for b in session.scalars(select(Branch).where(Branch.repository_id == repo.id))}
        configs = {c.branch_id: c for c in session.scalars(select(BranchConfig).join(Branch, BranchConfig.branch_id == Branch.id)
                                                        .where(Branch.repository_id == repo.id))}
        refs = [BranchRef(repo.id, branches[head.name].id if head.name in branches else -(index + 1), head.name, head.oid,
                          branch_role(repo, head, configs.get(branches[head.name].id) if head.name in branches else None),
                          repo.external_id, repo.connection_id) for index, head in enumerate(heads)]
        by_id = {ref.branch_id: ref for ref in refs}
        primary = [ref for ref in refs if ref.name == repo.default_branch or
                   (ref.role == "release" and configs.get(ref.branch_id) and configs[ref.branch_id].active_release)]
        # A default branch remains an ancestry candidate even if its role override
        # is unusual; only explicit active releases augment it.
        candidates = [BranchRef(r.repository_id, r.branch_id, r.name, r.oid, "main" if r.name == repo.default_branch else "release",
                                r.external_repository_id, r.connection_id) for r in primary]
        prs = [PullRequestRef(p.state, p.source_repository_id, p.source_branch, p.target_repository_id,
                              p.target_branch, p.external_id, p.source_external_repo_id, p.target_external_repo_id, p.connection_id)
               for p in session.scalars(select(PullRequest).where(PullRequest.connection_id == repo.connection_id,
                                            or_(PullRequest.source_repository_id == repo.id,
                                                and_(PullRequest.source_repository_id.is_(None),
                                                     PullRequest.source_external_repo_id == repo.external_id))))]
        cached = {(c.source_oid, c.target_oid): ComparisonResult(c.source_oid, c.target_oid, c.ahead, c.behind, tuple(c.merge_bases), c.state)
                  for c in session.scalars(select(Comparison).where(Comparison.repository_id == repo.id, Comparison.rule_version == RULE_VERSION,
                                          Comparison.source_oid.in_(current_oids), Comparison.target_oid.in_(current_oids)))}
    calculated = {}
    def compare(source_oid, target_oid):
        key = (source_oid, target_oid)
        if key not in calculated:
            calculated[key] = cached.get(key) or (ComparisonResult(source_oid, target_oid, 0, 0, (source_oid,), "identical")
                                                    if source_oid == target_oid else git.compare(source_oid, target_oid))
        return calculated[key]
    evidence = {}
    for ref in refs:
        config = configs.get(ref.branch_id)
        sources = infer_source_candidates(ref, candidates, compare)
        if config and config.manual_source_branch_id in by_id and config.manual_source_branch_id != ref.branch_id:
            sources.append(manual_source_claim(ref, by_id[config.manual_source_branch_id]))
        evidence[ref.name] = [{"name": item.branch.name, "oid": item.branch.oid, "branch_oid": ref.oid,
                               "kind": item.kind, "state": item.state, "merge_bases": list(item.merge_bases),
                               "ahead": item.ahead, "behind": item.behind, "rule_version": RULE_VERSION}
                              for item in sources]
        manual_ids = [(item.get("branch_id") if isinstance(item, dict) else item) for item in (config.manual_targets if config else [])]
        manual = [by_id[bid] for bid in manual_ids if isinstance(bid, int) and bid in by_id]
        targets = {item.branch_id: item for item in primary}
        for plan in planned_targets(ref, prs, manual, refs):
            if plan.target.repository_id == ref.repository_id and plan.target.branch_id in by_id:
                targets[plan.target.branch_id] = by_id[plan.target.branch_id]
        if config and config.main_comparison_branch_id in by_id:
            targets[config.main_comparison_branch_id] = by_id[config.main_comparison_branch_id]
        for target in targets.values():
            compare(ref.oid, target.oid)
    return heads, evidence, calculated.values()


def _publish_git(engine, job, connection, heads, evidence, comparisons):
    with Session(engine) as session:
        repo = _guard(session, job)
        _connection_guard(session, connection)
        branches = {b.name: b for b in session.scalars(select(Branch).where(Branch.repository_id == repo.id))}
        live = {head.name for head in heads}
        for name, row in branches.items():
            if name not in live:
                row.deleted, row.head_oid, row.commit_time, row.source_candidates = True, None, None, []
                row.snapshot_generation = job.generation
        for head in heads:
            row = branches.get(head.name)
            if row is None:
                row = Branch(repository_id=repo.id, name=head.name)
                session.add(row)
                branches[head.name] = row
            row.head_oid, row.commit_time, row.deleted = head.oid, head.commit_time, False
            row.source_candidates, row.snapshot_generation = evidence[head.name], job.generation
        session.flush()
        for row in branches.values():
            if not row.deleted:
                row.source_candidates = [dict(item, branch_id=branches[item["name"]].id) for item in row.source_candidates]
        oid_ids = {row.head_oid: row.id for row in branches.values() if not row.deleted}
        for result in comparisons:
            _put_comparison(session, repo.id, result, oid_ids.get(result.source_oid), oid_ids.get(result.target_oid))
        repo.git_synced_at, repo.snapshot_generation, repo.sync_error = utc_now(), job.generation, None
        session.commit()


def _record_failure(engine, job, captured_connection, field, error):
    with Session(engine) as session:
        repo = _guard(session, job)
        connection = _connection_guard(session, captured_connection)
        message = "Platform synchronization failed" if field == "api_error" else "Git synchronization failed"
        if isinstance(error, AuthenticationError):
            message = "Connection credentials rejected; administrator action required"
            connection.auth_disabled, connection.last_error = True, message
        elif isinstance(error, RateLimitError):
            message = "Platform rate limit reached"
        setattr(repo, field, message)
        session.commit()


def _retry_at(errors, attempts):
    now = utc_now()
    delay = min(3600, 30 * 2 ** min(attempts - 1, 7))
    for error in errors:
        if isinstance(error, RateLimitError):
            if error.retry_after is not None:
                delay = max(delay, error.retry_after)
            if error.retry_at is not None:
                delay = max(delay, (error.retry_at - now).total_seconds())
    return now + timedelta(seconds=min(86400, max(1, delay)))


def execute_job(job_id, *, settings, provider_factory=get_provider, local_source=None, fetch=fetch_bare, clone=clone_bare):
    if local_source is not None and settings.production:
        raise ValueError("Local fixtures are permitted only in explicit development mode")
    engine = make_engine(settings.database_url)
    try:
        with Session(engine) as session:
            job = session.get(SyncJob, job_id)
            if job is None:
                return
            repository_id = job.repository_id
        with repository_lock(settings, repository_id):
            with Session(engine, expire_on_commit=False) as session:
                job = session.get(SyncJob, job_id)
                if job.status in TERMINAL or (job.retry_at and job.retry_at > utc_now()):
                    return
                repo = locked_repository(session, repository_id)
                connection = session.get(PlatformConnection, repo.connection_id)
                if job.kind == "sync" and job.generation != repo.requested_generation:
                    job.status, job.finished_at = "superseded", utc_now()
                    session.commit()
                    return
                if job.kind == "sync" and connection.auth_disabled:
                    job.status, job.finished_at = "auth_failed", utc_now()
                    session.commit()
                    return
                if job.attempts >= job.max_attempts:
                    job.status, job.finished_at = "failed", utc_now()
                    session.commit()
                    return
                job.status, job.started_at, job.heartbeat_at = "running", utc_now(), utc_now()
                job.attempts += 1
                job.retry_at = None
                session.commit()
            errors = []
            superseded = False
            with heartbeat(engine, job.id, settings.heartbeat_interval_seconds):
                if job.kind == "comparison":
                    try:
                        params = job.parameters
                        with Session(engine) as session:
                            existing = _comparison(session, repository_id, params["source_oid"], params["target_oid"])
                        if existing is None:
                            result = GitService(cache_path(settings, repository_id)).compare(params["source_oid"], params["target_oid"])
                            with Session(engine) as session:
                                _put_comparison(session, repository_id, result, params["source_branch_id"], params["target_branch_id"])
                                session.commit()
                    except Exception as error:
                        errors.append(error)
                else:
                    try:
                        token = settings.decrypt_token(connection.encrypted_token) if connection.encrypted_token else None
                    except Exception:
                        token = None
                        errors.append(AuthenticationError("Stored credential cannot be decrypted"))
                    for field in ("api_error", "sync_error"):
                        try:
                            if errors and isinstance(errors[0], AuthenticationError) and connection.encrypted_token and token is None:
                                raise errors[0]
                            if field == "api_error":
                                _api_snapshot(engine, job, connection, token, provider_factory)
                            else:
                                with Session(engine) as session:
                                    repo = session.get(Repository, repository_id)
                                git = _refresh_cache(settings, repo, connection, token, local_source=local_source, fetch=fetch, clone=clone)
                                _publish_git(engine, job, connection, *_analyze(engine, job, git))
                        except Superseded:
                            superseded = True
                            break
                        except Exception as error:
                            errors.append(error)
                            try:
                                _record_failure(engine, job, connection, field, error)
                            except Superseded:
                                superseded = True
                                break
            with Session(engine) as session:
                row = session.get(SyncJob, job_id)
                repo = locked_repository(session, repository_id)
                if superseded or (job.kind == "sync" and repo.requested_generation != job.generation):
                    row.status = "superseded"
                elif any(isinstance(error, AuthenticationError) for error in errors):
                    row.status, row.error = "auth_failed", "Connection credentials rejected; administrator action required"
                elif errors:
                    row.error = "Background synchronization failed"
                    row.status = "retry" if row.attempts < row.max_attempts else "failed"
                    row.retry_at = _retry_at(errors, row.attempts) if row.status == "retry" else None
                else:
                    row.status, row.error = "succeeded", None
                if row.status in TERMINAL:
                    row.finished_at = utc_now()
                row.heartbeat_at = utc_now()
                session.commit()
    finally:
        engine.dispose()


def read_unique_commits(settings, repository_id, source_oid, target_oid, limit=50):
    """Bounded local-only excerpt; callers must authorize repository membership.

    OIDs should come from a displayed snapshot/comparison, never a moving ref.
    """
    from .git_service import OID
    if not OID.fullmatch(source_oid) or not OID.fullmatch(target_oid):
        raise ValueError("Commit excerpts require immutable OIDs")
    with repository_lock(settings, repository_id, blocking=False) as acquired:
        if not acquired:
            raise RepositoryBusy("Repository cache is busy; retry after synchronization")
        return GitService(cache_path(settings, repository_id)).commits(source_oid, target_oid, limit)
