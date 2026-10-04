"""Release evidence, separate from production deployment confirmations."""
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from .models import (AuditEvent, Deployment, PlatformConnection, PullRequest, ReleaseItem,
                     ReleasePlan, ReleaseSource, Repository, utc_now)
from .providers import get_provider, validate_remote_url


def current_production(session, project_id):
    latest = select(func.max(Deployment.id)).where(Deployment.project_id == project_id).group_by(Deployment.repository_id)
    rows = session.scalars(select(Deployment).where(Deployment.id.in_(latest)))
    result = {}
    for row in rows:
        result.setdefault(row.repository_id, row)
    return result


def plan_state(plan):
    if plan.status in ("draft", "cancelled"):
        return plan.status
    if plan.items and all(item.deployed_at and item.merged_at for item in plan.items):
        return "complete"
    return "active"


def mr_snapshot(session, repo):
    """Read published evidence only; no network calls while rendering pages."""
    source = session.get(ReleaseSource, repo.id)
    if source:
        connection = session.get(PlatformConnection, source.connection_id)
        return source.connection_id, source.external_id, source.records, source.synced_at, source.error or ("paused" if connection.auth_disabled else None)
    if repo.connection.kind == "local":
        return None, None, [], None, None
    records = [dict(id=p.external_id, title=p.title, url=p.web_url, state=p.state,
                    source_repo=p.source_external_repo_id, target_repo=p.target_external_repo_id,
                    source_branch=p.source_branch, target_branch=p.target_branch,
                    source_oid=p.source_oid, merge_oid=p.merge_oid,
                    merged_at=p.merged_at.isoformat() if p.merged_at else None)
               for p in session.scalars(select(PullRequest).where(PullRequest.repository_id == repo.id))]
    return repo.connection_id, repo.external_id, records, repo.api_synced_at, repo.api_error or ("paused" if repo.connection.auth_disabled else None)


def matching_mr(item, connection_id, external_id, records):
    if (item.mr_connection_id, item.mr_repository_id) != (connection_id, external_id):
        return None
    return next((p for p in records if p["id"] == item.mr_id
                 and p["source_repo"] == external_id and p["target_repo"] == external_id
                 and p["source_branch"] == item.branch_name and p["target_branch"] == item.target_branch
                 and p["source_oid"] == item.commit_oid), None)


def reconcile(session, repository_id):
    repo = session.get(Repository, repository_id)
    connection_id, external_id, records, synced_at, error = mr_snapshot(session, repo)
    if error or not synced_at or utc_now() - synced_at > timedelta(seconds=max(900, repo.sync_interval_seconds * 3)):
        return
    items = session.execute(select(ReleaseItem, ReleasePlan.version).join(ReleasePlan).where(
        ReleaseItem.repository_id == repo.id, ReleaseItem.merged_at.is_(None), ReleasePlan.status == "active")).all()
    for item, version in items:
        record = matching_mr(item, connection_id, external_id, records)
        if not record or record["state"] != "merged" or not record["merged_at"]:
            continue
        # A specific MR plus exact frozen source SHA also supports squash merges.
        merged_at = datetime.fromisoformat(record["merged_at"])
        if merged_at > utc_now():
            continue
        changed = session.execute(update(ReleasePlan).where(ReleasePlan.id == item.plan_id,
            ReleasePlan.status == "active", ReleasePlan.version == version)
            .values(version=ReleasePlan.version + 1))
        if changed.rowcount != 1:
            continue  # An administrator cancelled or confirmed while we read evidence.
        item.merged_at = merged_at
        item.merge_evidence = dict(record, connection_id=connection_id, repository_id=external_id,
                                   confirmed_at=utc_now().isoformat(), kind="platform")
        session.add(AuditEvent(action="release.merge_detected", entity_type="ReleaseItem", entity_id=item.id,
                               details=item.merge_evidence))


def sync_release_metadata(engine, repository_id, settings, provider_factory=get_provider):
    """Called under the existing per-repository worker lock, after Git/API sync."""
    with Session(engine) as session:
        source = session.get(ReleaseSource, repository_id)
        if source is None:
            reconcile(session, repository_id)
            session.commit()
            return
        captured = (source.connection_id, source.external_id, source.revision)
        connection = session.get(PlatformConnection, source.connection_id)
        fingerprint = (connection.encrypted_token, connection.base_url, connection.ca_bundle_path,
                       connection.kind, connection.auth_disabled)
        earliest = session.scalar(select(ReleasePlan.created_at).join(ReleaseItem).where(
            ReleaseItem.repository_id == repository_id, ReleasePlan.status == "active")
            .order_by(ReleasePlan.created_at).limit(1))
        since = min(value for value in (utc_now() - timedelta(days=90), earliest, source.synced_at) if value)
        full_name = source.full_name
        session.expunge(connection)
    records, failure = [], None
    started = utc_now()
    try:
        if connection.auth_disabled:
            raise ValueError("disabled")
        token = settings.decrypt_token(connection.encrypted_token) if connection.encrypted_token else None
        with provider_factory(connection, token) as provider:
            metadata = provider.repository(captured[1])
            if metadata.external_id != captured[1]:
                raise ValueError("identity changed")
            validate_remote_url(metadata.web_url, provider.web_root)
            full_name = metadata.full_name
            kwargs = {"full_name": full_name} if connection.kind == "github" else {}
            for p in provider.pull_requests(captured[1], since, **kwargs):
                validate_remote_url(p.web_url, provider.web_root)
                records.append(dict(id=p.external_id, title=p.title, url=p.web_url, state=p.state,
                    source_repo=p.source_repo_id, target_repo=p.target_repo_id,
                    source_branch=p.source_branch, target_branch=p.target_branch,
                    source_oid=p.source_oid, merge_oid=p.merge_oid,
                    merged_at=p.merged_at.isoformat() if p.merged_at else None))
                if len(records) > 10000:
                    raise ValueError("too many records")
    except Exception:
        failure = "MR 同步失败，请检查平台连接。"
    with Session(engine) as session:
        # A binding/credential change invalidates the in-flight response.
        from .jobs import locked_repository
        locked_repository(session, repository_id)
        source = session.scalar(select(ReleaseSource).where(ReleaseSource.repository_id == repository_id).with_for_update())
        current = session.scalar(select(PlatformConnection).where(PlatformConnection.id == captured[0]).with_for_update())
        if source is None or captured != (source.connection_id, source.external_id, source.revision):
            return
        if current is None or fingerprint != (current.encrypted_token, current.base_url, current.ca_bundle_path,
                                               current.kind, current.auth_disabled):
            return
        source.error = failure
        if not failure:
            source.records, source.synced_at, source.full_name = records, started, full_name
        session.flush()
        reconcile(session, repository_id)
        session.commit()


def project_release_summary(session, project_id):
    plans = session.scalars(select(ReleasePlan).where(ReleasePlan.project_id == project_id)
                            .options(selectinload(ReleasePlan.items))
                            .order_by(ReleasePlan.scheduled_date, ReleasePlan.id)).all()
    upcoming = {}
    for plan in plans:
        if plan_state(plan) == "active":
            for item in plan.items:
                if not (item.deployed_at and item.merged_at):
                    upcoming.setdefault(item.repository_id, plan)
    return current_production(session, project_id), upcoming
