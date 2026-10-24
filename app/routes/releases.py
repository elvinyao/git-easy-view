from datetime import date, datetime, timezone
from itertools import islice
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.auth import optional_user, require_admin, require_project_access
from app.db import get_session
from app.models import (AuditEvent, Branch, Deployment, GitTag, PlatformConnection, Project,
                        ProjectRepository, ReleaseItem, ReleasePlan, ReleaseSource, Repository, utc_now)
from app.releases import current_production, mr_snapshot, plan_state, reconcile
from app.release_forms import FormError, form_data, render, text_field
from app.jobs import enqueue_sync, locked_repository
from app.providers import get_provider, validate_remote_url
from app.providers.base import identity, optional_string, required_string
from app.sync import branch_role

router = APIRouter()


def fail(message="发布数据无效，请刷新后重试。", code=400, *, field="_form"):
    raise FormError(field, message, code)


def field(form, name, limit=1024, required=True):
    return text_field(form, name, limit, required)


def integer(value, name="_form"):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 10:
        fail(field=name)
    return int(value)


def linked_repos(session, project_id):
    return session.scalars(select(Repository).join(ProjectRepository).where(
        ProjectRepository.project_id == project_id).order_by(Repository.full_name)).all()


def get_plan(session, project_id, plan_id):
    plan = session.get(ReleasePlan, plan_id)
    if plan is None or plan.project_id != project_id:
        fail("Not Found", 404)
    return plan


def lock_project(session, project_id):
    # Also serializes production history appends on SQLite.
    session.execute(update(Project).where(Project.id == project_id).values(created_at=Project.created_at))


def touch_plan(session, plan, form):
    version = integer(str(form.get("version", "")), "version")
    changed = session.execute(update(ReleasePlan).where(ReleasePlan.id == plan.id, ReleasePlan.version == version)
                              .values(version=ReleasePlan.version + 1))
    if changed.rowcount != 1:
        fail("记录已更新，请刷新后重试。", 409, field="version")
    session.refresh(plan)


def audit(session, user, action, entity, details=None):
    session.flush()
    session.add(AuditEvent(actor_id=user.id, action=action, entity_type=type(entity).__name__,
                           entity_id=entity.id, details=details or {}))


def choices(session, repo, settings=None):
    branches = session.scalars(select(Branch).where(Branch.repository_id == repo.id, Branch.deleted.is_(False))
                               .options(selectinload(Branch.config)).order_by(Branch.name)).all()
    tags = session.scalars(select(GitTag).where(GitTag.repository_id == repo.id, GitTag.deleted.is_(False))
                           .order_by(GitTag.commit_time.desc().nulls_last(), GitTag.name.desc())).all()
    candidates = [branch for branch in branches if branch.head_oid and branch_role(repo, branch, branch.config) == "release"]
    recommended = max(candidates, key=lambda branch: (
        bool(branch.config and branch.config.active_release),
        branch.commit_time or datetime.min.replace(tzinfo=timezone.utc),
        branch.name,
    ), default=None)
    cid, external_id, records, synced, error = mr_snapshot(session, repo)
    subjects = {}
    if settings:
        from app.git_service import GitError
        from app.jobs import repository_lock
        from app.project_overview import _tip_subjects
        from app.sync import cache_path
        oids = tuple(sorted({branch.head_oid for branch in branches if branch.head_oid}))
        try:
            with repository_lock(settings, repo.id, blocking=False) as acquired:
                if acquired:
                    subjects = _tip_subjects(str(cache_path(settings, repo.id)), oids)
        except (GitError, OSError):
            pass  # An unavailable cache must not prevent editing the plan.
    return dict(branches=branches, tags=tags, mrs=records, synced=synced, error=error,
                branch_subjects=subjects,
                connection_id=cid, external_id=external_id,
                recommended_branch=recommended.name if recommended else "")


def version_fields(session, repo, form, prefix="", *, require_tag=False, settings=None):
    branch_name = field(form, prefix + "branch")
    branch = session.scalar(select(Branch).where(Branch.repository_id == repo.id, Branch.name == branch_name,
                                                Branch.deleted.is_(False)))
    if not branch or not branch.head_oid:
        fail("分支已删除或尚未同步。", field=prefix + "branch")
    tag_name = field(form, prefix + "tag", required=require_tag)
    tag = session.scalar(select(GitTag).where(GitTag.repository_id == repo.id, GitTag.name == tag_name,
                                             GitTag.deleted.is_(False))) if tag_name else None
    if tag_name and tag is None:
        fail("Tag 已删除或尚未同步。", field=prefix + "tag")
    expected = field(form, prefix + "oid", 128, required=False)
    if expected and expected != (tag.commit_oid if tag else branch.head_oid):
        fail("Tag 已变化，请刷新后重新选择。", 409, field=prefix + ("tag" if tag else "branch"))
    subject = tag.subject if tag else ""
    if settings:
        from app.git_service import GitService, GitError
        from app.jobs import repository_lock
        from app.sync import cache_path
        git = GitService(cache_path(settings, repo.id))
        if tag and tag.commit_oid != branch.head_oid:
            try:
                with repository_lock(settings, repo.id, blocking=False) as acquired:
                    if not acquired or git.compare(tag.commit_oid, branch.head_oid).state not in ("identical", "contained"):
                        fail("Tag 不在所选分支历史中，或缓存正在更新。请检查后重试。", field=prefix + "tag")
            except GitError:
                fail("Tag 不在所选分支历史中，或缓存正在更新。请检查后重试。", field=prefix + "tag")
        if not tag:
            try:
                subject = git.commit_subjects([branch.head_oid]).get(branch.head_oid, "")
            except GitError:
                pass
    return dict(branch_name=branch.name, tag_name=tag.name if tag else None,
                commit_oid=tag.commit_oid if tag else branch.head_oid,
                subject=subject), tag


@router.get("/projects/{project_id}/releases")
def release_list(project_id: int, request: Request, user=Depends(optional_user), session: Session = Depends(get_session)):
    project = require_project_access(session, user, project_id)
    plans = session.scalars(select(ReleasePlan).where(ReleasePlan.project_id == project_id)
                            .options(selectinload(ReleasePlan.items)).order_by(ReleasePlan.scheduled_date.desc(), ReleasePlan.id.desc())).all()
    repos = linked_repos(session, project_id)
    return render(request, "releases.html", session, user, project=project, plans=plans, plan_state=plan_state,
                  production=current_production(session, project_id), repositories=repos,
                  today=utc_now().astimezone(ZoneInfo("Asia/Tokyo")).date().isoformat())


@router.get("/projects/{project_id}/releases/new")
@router.get("/projects/{project_id}/releases/{plan_id}/edit")
def release_form(project_id: int, request: Request, plan_id: int | None = None,
                 user=Depends(require_admin), session: Session = Depends(get_session)):
    project = require_project_access(session, user, project_id)
    plan = get_plan(session, project_id, plan_id) if plan_id else None
    recovering = getattr(request.state, "release_form_submitted", False)
    if plan and plan.status != "draft" and not recovering:
        fail("只有草稿可以修改版本。", 409)
    repos = linked_repos(session, project_id)
    if recovering:
        selected = {int(value) for value in request.state.form_lists.get("repos", [])
                    if value.isascii() and value.isdecimal() and len(value) <= 10}
        selected &= {repo.id for repo in repos}
    else:
        selected = {item.repository_id for item in plan.items} if plan else {integer(x, "repos") for x in request.query_params.getlist("repos")}
        if selected - {repo.id for repo in repos}:
            fail(field="repos")
    return render(request, "release_form.html", session, user, project=project, plan=plan, repositories=repos,
                  choices={r.id: choices(session, r, request.app.state.settings) for r in repos}, selected=selected,
                  production=current_production(session, project_id),
                  items={item.repository_id: item for item in plan.items} if plan else {},
                  today=utc_now().astimezone(ZoneInfo("Asia/Tokyo")).date().isoformat())


@router.post("/projects/{project_id}/releases")
@router.post("/projects/{project_id}/releases/{plan_id}/edit")
def save_plan(project_id: int, request: Request, plan_id: int | None = None, user=Depends(require_admin),
              form=Depends(form_data), session: Session = Depends(get_session)):
    require_project_access(session, user, project_id)
    lock_project(session, project_id)
    intent = field(form, "intent", 16, False) or "draft"
    if intent not in ("draft", "start"):
        fail(field="intent")
    title, scheduled = field(form, "title", 255), field(form, "scheduled_date", 10)
    try:
        if date.fromisoformat(scheduled).isoformat() != scheduled:
            fail("请输入有效的计划日期。", field="scheduled_date")
    except ValueError:
        fail("请输入有效的计划日期。", field="scheduled_date")
    ids = list(dict.fromkeys(integer(x, "repos") for x in form.getlist("repos")))
    if not 1 <= len(ids) <= 50:
        fail("请一次选择 1–50 个仓库。", field="repos")
    repos = {repo.id: repo for repo in linked_repos(session, project_id)}
    if set(ids) - repos.keys():
        fail(field="repos")
    plan = get_plan(session, project_id, plan_id) if plan_id else ReleasePlan(project_id=project_id, created_by_id=user.id)
    if plan_id:
        touch_plan(session, plan, form)
        if plan.status != "draft":
            fail("只有草稿可以修改版本。", 409, field="version")
        plan.items.clear()
        session.flush()
    plan.title, plan.scheduled_date, plan.notes = title, scheduled, field(form, "notes", 4000, False)
    session.add(plan)
    for rid in ids:
        prefix = f"r{rid}_"
        values, tag = version_fields(session, repos[rid], form, prefix, require_tag=intent == "start",
                                     settings=request.app.state.settings)
        target = field(form, prefix + "target")
        if not session.scalar(select(Branch.id).where(Branch.repository_id == rid, Branch.name == target, Branch.deleted.is_(False))):
            fail("分支已删除或尚未同步。", field=prefix + "target")
        if target == values["branch_name"]:
            fail("发布分支和合并目标必须不同。", field=prefix + "target")
        # A draft without a tag has no final pinned release version.
        item = ReleaseItem(repository_id=rid, **values, tag_oid=tag.object_oid if tag else None,
                           commit_time=tag.commit_time if tag else None, target_branch=target)
        if not tag:
            item.commit_oid = None
        mr_id = field(form, prefix + "mr", 255, False)
        if mr_id:
            cid, external_id, records, _, _ = mr_snapshot(session, repos[rid])
            record = next((r for r in records if r["id"] == mr_id), None)
            if not record or record["source_repo"] != external_id or record["target_repo"] != external_id or record["source_branch"] != item.branch_name or record["target_branch"] != target:
                fail("MR 与发布仓库或分支不匹配。", field=prefix + "mr")
            item.mr_id, item.mr_connection_id, item.mr_repository_id = mr_id, cid, external_id
        plan.items.append(item)
    audit(session, user, "release.saved", plan)
    if intent == "start":
        plan.status = "active"
        session.flush()
        for item in plan.items:
            reconcile(session, item.repository_id)
        audit(session, user, "release.activate", plan)
    session.commit()
    notice = "started" if intent == "start" else "saved" if plan_id else "created"
    query = f"?saved={notice}" if "intent" in form or plan_id else ""
    return RedirectResponse(f"/projects/{project_id}/releases/{plan.id}{query}", 303)


@router.get("/projects/{project_id}/releases/{plan_id}")
def release_detail(project_id: int, plan_id: int, request: Request, user=Depends(optional_user), session: Session = Depends(get_session)):
    project = require_project_access(session, user, project_id)
    plan = get_plan(session, project_id, plan_id)
    repos = {item.repository_id: session.get(Repository, item.repository_id) for item in plan.items}
    changes = {}
    for item in plan.items:
        tag = session.scalar(select(GitTag).where(GitTag.repository_id == item.repository_id, GitTag.name == item.tag_name))
        changes[item.id] = bool(item.tag_name and (not tag or tag.deleted or tag.commit_oid != item.commit_oid or tag.object_oid != item.tag_oid))
    return render(request, "release_detail.html", session, user, project=project, plan=plan,
                  state=plan_state(plan), repositories=repos, tag_changes=changes,
                  choices={rid: choices(session, repo) for rid, repo in repos.items()},
                  production=current_production(session, project_id),
                  audits=session.scalars(select(AuditEvent).where(AuditEvent.entity_type == "ReleasePlan", AuditEvent.entity_id == plan.id).order_by(AuditEvent.id.desc())).all())


@router.post("/projects/{project_id}/releases/{plan_id}/action")
def plan_action(project_id: int, plan_id: int, request: Request, user=Depends(require_admin), form=Depends(form_data), session: Session = Depends(get_session)):
    require_project_access(session, user, project_id)
    lock_project(session, project_id)
    plan = get_plan(session, project_id, plan_id)
    touch_plan(session, plan, form)
    action = field(form, "action", 32)
    if action == "activate" and plan.status == "draft":
        if not plan.items or any(not i.tag_name or not i.commit_oid for i in plan.items):
            fail("发布前请为每个仓库选择 Tag。", field="action")
        for item in plan.items:
            tag = session.scalar(select(GitTag).where(GitTag.repository_id == item.repository_id, GitTag.name == item.tag_name))
            if not tag or tag.deleted or tag.commit_oid != item.commit_oid or tag.object_oid != item.tag_oid:
                fail("Tag 已变化，请刷新后重新选择。", 409, field="action")
        plan.status = "active"
        session.flush()
        for item in plan.items:
            reconcile(session, item.repository_id)
    elif action == "cancel" and plan_state(plan) in ("draft", "active"):
        plan.status = "cancelled"
    else:
        fail("记录已更新，请刷新后重试。", 409, field="action")
    audit(session, user, "release." + action, plan)
    session.commit()
    notice = "started" if action == "activate" else "cancelled"
    return RedirectResponse(f"/projects/{project_id}/releases/{plan_id}?saved={notice}", 303)


@router.post("/projects/{project_id}/releases/{plan_id}/items/{item_id}")
def item_action(project_id: int, plan_id: int, item_id: int, request: Request,
                user=Depends(require_admin), form=Depends(form_data), session: Session = Depends(get_session)):
    require_project_access(session, user, project_id)
    lock_project(session, project_id)
    plan = get_plan(session, project_id, plan_id)
    touch_plan(session, plan, form)
    item = next((i for i in plan.items if i.id == item_id), None)
    if not item or plan.status != "active":
        fail("记录已更新，请刷新后重试。", 409, field="action")
    action = field(form, "action", 32)
    notes = field(form, "notes", 4000, action == "merge")
    if action == "deploy" and not item.deployed_at:
        if field(form, "confirmed_oid", 128) != item.commit_oid:
            fail("固定版本已变化，请刷新后重试。", field="confirmed_oid")
        item.deployed_at = deployment_time(form)
        session.add(Deployment(project_id=project_id, repository_id=item.repository_id, release_item_id=item.id,
            branch_name=item.branch_name, tag_name=item.tag_name, commit_oid=item.commit_oid, subject=item.subject,
            deployed_at=item.deployed_at, actor_id=user.id, notes=notes))
    elif action == "merge" and not item.merged_at:
        item.merged_at = utc_now()
        item.merge_evidence = dict(kind="manual", actor_id=user.id, reason=notes, confirmed_at=utc_now().isoformat())
    elif action == "bind" and not item.merged_at:
        repo = session.get(Repository, item.repository_id)
        cid, external, records, _, _ = mr_snapshot(session, repo)
        mr_id = field(form, "mr", 255)
        record = next((r for r in records if r["id"] == mr_id), None)
        if not record or record["source_repo"] != external or record["target_repo"] != external or record["source_branch"] != item.branch_name or record["target_branch"] != item.target_branch:
            fail("MR 与发布仓库或分支不匹配。", field="mr")
        item.mr_id, item.mr_connection_id, item.mr_repository_id = mr_id, cid, external
        session.flush()
        reconcile(session, repo.id)
    else:
        fail("记录已更新，请刷新后重试。", 409, field="action")
    audit(session, user, "release." + action, plan, {"item_id": item.id, "notes": notes})
    session.commit()
    notice = {"deploy": "deployed", "merge": "merged", "bind": "bound"}[action]
    return RedirectResponse(f"/projects/{project_id}/releases/{plan_id}?saved={notice}#item-{item_id}", 303)


def deployment_time(form):
    value = field(form, "deployed_at", 32, False)
    if not value:
        return utc_now()
    try:
        result = datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Asia/Tokyo")).astimezone(timezone.utc)
        if result > utc_now():
            fail("上线时间不能晚于当前时间。", field="deployed_at")
        return result
    except (ValueError, OverflowError):
        fail("请输入有效的上线时间。", field="deployed_at")


@router.get("/projects/{project_id}/production/{repository_id}")
def production_form(project_id: int, repository_id: int, request: Request, user=Depends(optional_user), session: Session = Depends(get_session)):
    project = require_project_access(session, user, project_id)
    repo = next((r for r in linked_repos(session, project_id) if r.id == repository_id), None)
    if repo is None:
        fail("Not Found", 404)
    history = session.scalars(select(Deployment).where(Deployment.project_id == project_id, Deployment.repository_id == repository_id)
                              .order_by(Deployment.id.desc()).limit(100)).all()
    return render(request, "production.html", session, user, project=project, repo=repo, history=history,
                  current=history[0] if history else None, options=choices(session, repo, request.app.state.settings),
                  source=session.get(ReleaseSource, repo.id),
                  connections=session.scalars(select(PlatformConnection).where(PlatformConnection.kind.in_(("github", "gitlab"))).order_by(PlatformConnection.name)).all() if user and user.is_admin else [])


@router.post("/projects/{project_id}/production/{repository_id}")
def set_production(project_id: int, repository_id: int, request: Request, user=Depends(require_admin), form=Depends(form_data), session: Session = Depends(get_session)):
    require_project_access(session, user, project_id)
    lock_project(session, project_id)
    repo = next((r for r in linked_repos(session, project_id) if r.id == repository_id), None)
    if repo is None:
        fail("Not Found", 404)
    current = current_production(session, project_id).get(repository_id)
    if integer(str(form.get("current_id", "0")), "current_id") != (current.id if current else 0):
        fail("记录已更新，请刷新后重试。", 409, field="current_id")
    rollback_id = field(form, "rollback_id", 20, False)
    notes = field(form, "notes", 4000, bool(rollback_id))
    if rollback_id:
        previous = session.get(Deployment, integer(rollback_id, "rollback_id"))
        if not previous or (previous.project_id, previous.repository_id) != (project_id, repository_id):
            fail(field="rollback_id")
        values = {key: getattr(previous, key) for key in ("branch_name", "tag_name", "commit_oid", "subject")}
    else:
        values, _ = version_fields(session, repo, form, settings=request.app.state.settings)
    deployment = Deployment(project_id=project_id, repository_id=repository_id, **values, notes=notes,
                            actor_id=user.id, deployed_at=deployment_time(form), rollback_of_id=int(rollback_id) if rollback_id else None)
    session.add(deployment)
    audit(session, user, "production.rollback" if rollback_id else "production.set", deployment, {"previous_id": current.id if current else None})
    session.commit()
    notice = "rollback" if rollback_id else "production"
    return RedirectResponse(f"/projects/{project_id}/production/{repository_id}?saved={notice}", 303)


@router.get("/projects/{project_id}/production/{repository_id}/source/repositories")
def source_repositories(project_id: int, repository_id: int, request: Request,
                        connection_id: str = "", user=Depends(require_admin), session: Session = Depends(get_session)):
    """Discover remote metadata only when the administrator explicitly requests it."""
    require_project_access(session, user, project_id)
    repo = next((r for r in linked_repos(session, project_id) if r.id == repository_id), None)
    if repo is None:
        fail("Not Found", 404)
    if repo.connection.kind != "local":
        fail()
    connection = session.get(PlatformConnection, integer(connection_id, "connection_id"))
    if not connection or connection.kind not in ("github", "gitlab") or connection.auth_disabled:
        fail("请先检查平台连接。", field="connection_id")
    try:
        token = request.app.state.settings.decrypt_token(connection.encrypted_token) if connection.encrypted_token else None
        factory = getattr(request.app.state, "provider_factory", get_provider)
        with factory(connection, token) as provider:
            repositories = []
            # Use paginated iteration in the remote adapters so the response cap
            # also stops network reads. Keep custom provider factories compatible.
            records = provider.iter_repositories() if hasattr(provider, "iter_repositories") else provider.repositories()
            for metadata in islice(records, 2001):
                validate_remote_url(metadata.web_url, provider.web_root)
                validate_remote_url(metadata.clone_url, provider.web_root)
                repositories.append(dict(
                    external_id=identity(metadata.external_id, "repository identity"),
                    full_name=required_string(metadata.full_name, "repository name"),
                    default_branch=optional_string(metadata.default_branch),
                ))
    except Exception:
        fail("无法读取平台仓库，请检查连接后重试。", field="connection_id")
    return {"repositories": repositories[:2000], "incomplete": len(repositories) > 2000}


@router.post("/projects/{project_id}/production/{repository_id}/source")
def bind_source(project_id: int, repository_id: int, request: Request, user=Depends(require_admin), form=Depends(form_data), session: Session = Depends(get_session)):
    require_project_access(session, user, project_id)
    repo = next((r for r in linked_repos(session, project_id) if r.id == repository_id), None)
    if repo is None or repo.connection.kind != "local":
        fail()
    source = session.get(ReleaseSource, repo.id)
    action = field(form, "action", 32)
    if action == "unbind":
        locked_repository(session, repo.id)
        source = session.scalar(select(ReleaseSource).where(ReleaseSource.repository_id == repo.id)
                                .with_for_update().execution_options(populate_existing=True))
        if source:
            session.delete(source)
    else:
        connection = session.get(PlatformConnection, integer(str(form.get("connection_id", "")), "connection_id"))
        if not connection or connection.kind not in ("github", "gitlab") or connection.auth_disabled:
            fail("请先检查平台连接。", field="connection_id")
        external_id = field(form, "external_id", 255)
        fingerprint = (connection.encrypted_token, connection.base_url, connection.ca_bundle_path, connection.kind)
        try:
            token = request.app.state.settings.decrypt_token(connection.encrypted_token) if connection.encrypted_token else None
            factory = getattr(request.app.state, "provider_factory", get_provider)
            with factory(connection, token) as provider:
                metadata = provider.repository(external_id)
                validate_remote_url(metadata.web_url, provider.web_root)
                validate_remote_url(metadata.clone_url, provider.web_root)
                if metadata.external_id != external_id:
                    raise ValueError("Provider repository identity changed")
        except Exception:
            fail("MR 同步失败，请检查平台连接。", field="external_id")
        locked_repository(session, repo.id)
        connection = session.scalar(select(PlatformConnection).where(PlatformConnection.id == connection.id)
                                    .with_for_update().execution_options(populate_existing=True))
        if not connection or connection.auth_disabled or fingerprint != (connection.encrypted_token, connection.base_url, connection.ca_bundle_path, connection.kind):
            fail("记录已更新，请刷新后重试。", 409, field="connection_id")
        source = session.scalar(select(ReleaseSource).where(ReleaseSource.repository_id == repo.id)
                                .with_for_update().execution_options(populate_existing=True))
        source = source or ReleaseSource(repository_id=repo.id, revision=0)
        source.connection_id, source.external_id, source.full_name = connection.id, metadata.external_id, metadata.full_name
        source.records, source.synced_at, source.error = [], None, None
        source.revision += 1
        session.add(source)
    audit(session, user, "release.source", repo)
    session.commit()
    if not repo.connection.auth_disabled:
        enqueue_sync(session, repo.id, user.id, force=True, settings=request.app.state.settings,
                     queue=getattr(request.app.state, "job_queue", None))
    notice = "unbound" if action == "unbind" else "source"
    return RedirectResponse(f"/projects/{project_id}/production/{repository_id}?saved={notice}", 303)
