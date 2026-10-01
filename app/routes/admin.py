from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import require_admin, session_data
from app.db import get_session
from app.i18n import Message
from app.models import (
    AuditEvent,
    Branch,
    BranchConfig,
    PlatformConnection,
    Project,
    ProjectMember,
    ProjectRepository,
    Repository,
    SyncJob,
    User,
    utc_now,
)
from app.web import form_data as verified_form_data, number, render as web_render
from app.jobs import enqueue_sync, locked_repository
from app.job_progress import progress_context
from app.providers import get_provider, validate_remote_url
from app.providers.base import _url_parts
from app.sync import branch_role
from datetime import datetime, timezone

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
FIELD_LABELS = {
    "name": "名称",
    "username": "用户名",
    "slug": "项目标识",
    "description": "描述",
    "kind": "平台",
    "base_url": "HTTPS 实例地址",
    "token": "访问令牌",
    "ca_bundle_path": "公司 CA 文件路径（可选）",
    "sync_interval_seconds": "同步间隔（秒）",
    "release_prefixes": "发布分支前缀（逗号分隔）",
    "feature_prefixes": "功能分支前缀（逗号分隔）",
    "role": "分支角色",
    "password": "密码",
    "active_release": "当前发布",
    "manual_source_branch_id": "人工指定来源",
    "main_comparison_branch_id": "主要比较对象",
    "manual_targets": "人工计划目标",
    "project_id": "导入到项目",
    "external_ids": "选择仓库",
    "history_since": "历史起始日期（可选）",
}


class FormError(HTTPException):
    def __init__(self, field, detail, status_code=400):
        super().__init__(status_code, detail)
        self.field = field


async def form_data(request: Request):
    form = await verified_form_data(request)
    allowed = set(FIELD_LABELS) | {"connection_id", "is_admin", "is_active"}
    request.state.form_values = {
        key: str(form[key]) for key in allowed - {"token", "password"} if key in form
    }
    # Instance addresses never accept query, fragment, or account components;
    # strip them from error recovery because pasted URLs can contain secrets.
    if "base_url" in request.state.form_values:
        base = request.state.form_values["base_url"]
        request.state.form_values["base_url"] = "" if "@" in base else base.split("?", 1)[0].split("#", 1)[0]
    request.state.form_lists = {
        key: [str(value) for value in form.getlist(key)]
        for key in ("external_ids", "manual_targets")
    }
    return form


def render(request, template, session, user, **context):
    context.setdefault("form_values", getattr(request.state, "form_values", {}))
    context.setdefault("form_lists", getattr(request.state, "form_lists", {}))
    context.setdefault("form_errors", getattr(request.state, "form_errors", {}))
    context.setdefault("form_submitted", hasattr(request.state, "form_values"))
    return web_render(request, template, session, user, **context)


def recover_form(request, exc):
    """Render known admin forms from fresh rows after the failed transaction closes."""
    if not request.url.path.startswith("/admin/") or not hasattr(request.state, "form_values"):
        return None
    values = request.state.form_values
    field = getattr(exc, "field", None)
    if field is None:
        field = "username" if "/users" in request.url.path else "slug" if request.url.path == "/admin/projects" else "name"
    request.state.form_errors = {field: exc.detail}
    identity = session_data(request)
    with Session(request.app.state.engine) as session:
        user = session.get(User, identity["user_id"]) if identity else None
        if not user or not user.is_active or not user.is_admin:
            return None
        params = request.path_params
        path = request.url.path
        if path == "/admin/repositories/import":
            connection = session.get(PlatformConnection, number(values.get("connection_id"), 0))
            if not connection:
                return None
            response = render(request, "admin_import.html", session, user, connection=connection,
                              remote_repositories=[dict(external_id=value, full_name=value, default_branch=None) for value in request.state.form_lists.get("external_ids", [])],
                              total=0, imported_ids=set(),
                              selected_project=values.get("project_id", ""), import_search="",
                              import_recovery=True)
        elif path == "/admin/projects":
            response = home(request, user, session)
        elif "project_id" in params:
            response = project_form(params["project_id"], request, user, session)
        elif path == "/admin/users" or "user_id" in params:
            response = user_form(request, params.get("user_id"), user, session)
        elif path == "/admin/connections" or "connection_id" in params:
            response = connection_form(request, params.get("connection_id"), user, session)
        elif "repository_id" in params:
            response = repository_form(params["repository_id"], request, user, session)
        elif "branch_id" in params:
            response = branch_form(params["branch_id"], request, user, session)
        else:
            return None
        response.status_code = exc.status_code
        return response


def audit(session, user, action, entity, details=None):
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(400, "名称或标识已存在，请修改后重试。") from None
    session.add(
        AuditEvent(
            actor_id=user.id,
            action=action,
            entity_type=entity.__tablename__,
            entity_id=entity.id,
            details=details or {},
        )
    )


def commit(session):
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(400, "名称或标识已存在，请修改后重试。") from None


def text_field(form, name, maximum=255, required=True):
    value = str(form.get(name, "")).strip()
    if len(value) > maximum or (required and not value):
        raise FormError(
            name,
            Message(
                "{name}：请填写有效内容（最多 {maximum} 字符）。",
                name=Message(FIELD_LABELS.get(name, name)),
                maximum=maximum,
            ),
        )
    return value


@router.get("")
def home(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    return render(
        request,
        "admin.html",
        session,
        user,
        users=session.scalars(select(User).order_by(User.username)).all(),
        connections=session.scalars(
            select(PlatformConnection).order_by(PlatformConnection.name)
        ).all(),
        repositories=session.scalars(
            select(Repository).order_by(Repository.full_name)
        ).all(),
    )


@router.post("/projects")
def create_project(
    request: Request,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    project = Project(
        name=text_field(form, "name"),
        slug=text_field(form, "slug"),
        description=text_field(form, "description", 4000, False),
    )
    session.add(project)
    try:
        audit(session, user, "project.create", project)
        commit(session)
    except IntegrityError:
        session.rollback()
        raise HTTPException(400, "项目标识已存在") from None
    return RedirectResponse("/admin", 303)


def get_or_404(session, model, identifier):
    row = session.get(model, identifier)
    if row is None:
        raise HTTPException(404)
    return row


def provider_for(request, connection):
    settings = request.app.state.settings
    token = (
        settings.decrypt_token(connection.encrypted_token)
        if connection.encrypted_token
        else None
    )
    return getattr(request.app.state, "provider_factory", get_provider)(
        connection, token
    )


def connection_fingerprint(connection):
    return tuple(
        getattr(connection, key)
        for key in (
            "kind",
            "base_url",
            "encrypted_token",
            "ca_bundle_path",
            "last_checked_at",
            "auth_disabled",
        )
    )


def locked_connection(session, connection_id):
    """Serialize connection edits with import publication and credential checks."""
    # SQLite ignores FOR UPDATE, so take its writer lock before reloading.
    if session.bind.dialect.name == "sqlite":
        session.execute(
            update(PlatformConnection)
            .where(PlatformConnection.id == connection_id)
            .values(auth_disabled=PlatformConnection.auth_disabled)
        )
    return session.scalar(
        select(PlatformConnection)
        .where(PlatformConnection.id == connection_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def checked_connection(session, connection_id, captured):
    fresh = locked_connection(session, connection_id)
    if fresh is None or connection_fingerprint(fresh) != captured:
        session.rollback()
        raise HTTPException(409, "连接在请求期间已变更，请重新检查或重新选择仓库。")
    return fresh


def enqueue(request, session, repo, user, history_since=None):
    return enqueue_sync(
        session,
        repo.id,
        requested_by=user,
        history_since=history_since,
        force=True,
        settings=request.app.state.settings,
        queue=getattr(request.app.state, "job_queue", None),
    )


def save_config(request, session, repo, user):
    session.execute(
        update(Branch)
        .where(Branch.repository_id == repo.id)
        .values(source_candidates=[])
    )
    connection = session.get(PlatformConnection, repo.connection_id)
    if connection.auth_disabled:
        repo.requested_generation += 1
        commit(session)
    else:
        enqueue(request, session, repo, user)


@router.get("/users/{user_id}")
@router.get("/users/new")
def user_form(
    request: Request,
    user_id: int | None = None,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    return render(
        request,
        "admin_user.html",
        session,
        user,
        person=get_or_404(session, User, user_id) if user_id else None,
    )


@router.post("/users")
@router.post("/users/{user_id}")
def save_user(
    request: Request,
    user_id: int | None = None,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    person = get_or_404(session, User, user_id) if user_id else User()
    username = text_field(form, "username", 150)
    password = str(form.get("password", ""))
    if (not user_id or password) and not 12 <= len(password) <= 1024:
        raise FormError("password", "密码长度须为 12–1024 个字符。")
    active = "is_active" in form if user_id else True
    admin = "is_admin" in form
    if user_id == user.id and (not active or not admin):
        raise FormError(
            "is_admin", "不能停用当前管理员或移除自己的管理权限，请由另一管理员处理。"
        )
    person.username, person.is_active, person.is_admin = username, active, admin
    if password:
        person.set_password(password)
    session.add(person)
    audit(
        session,
        user,
        "user.update" if user_id else "user.create",
        person,
        {"active": active, "admin": admin, "password_changed": bool(password)},
    )
    commit(session)
    return RedirectResponse("/admin", 303)


@router.get("/projects/{project_id}")
def project_form(
    project_id: int,
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    project = get_or_404(session, Project, project_id)
    members = session.scalars(
        select(User)
        .join(ProjectMember)
        .where(ProjectMember.project_id == project.id)
        .order_by(User.username)
    ).all()
    return render(
        request,
        "admin_project.html",
        session,
        user,
        project=project,
        members=members,
        users=session.scalars(select(User).order_by(User.username)).all(),
    )


@router.post("/projects/{project_id}")
def edit_project(
    project_id: int,
    request: Request,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    project = get_or_404(session, Project, project_id)
    project.name, project.description = text_field(form, "name"), text_field(
        form, "description", 4000, False
    )
    audit(session, user, "project.update", project)
    commit(session)
    return RedirectResponse(f"/admin/projects/{project.id}", 303)


@router.post("/projects/{project_id}/members")
def membership(
    project_id: int,
    request: Request,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    project = get_or_404(session, Project, project_id)
    person = get_or_404(session, User, number(form.get("user_id"), 0))
    member = session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == person.id
        )
    )
    action = form.get("action")
    if action not in ("grant", "revoke"):
        raise HTTPException(400, "请选择授予或撤销权限")
    if action == "grant" and not member:
        session.add(ProjectMember(project_id=project.id, user_id=person.id))
    if action == "revoke" and member:
        session.delete(member)
    audit(session, user, "membership." + action, project, {"user_id": person.id})
    commit(session)
    return RedirectResponse(f"/admin/projects/{project.id}", 303)


@router.get("/connections/{connection_id}")
@router.get("/connections/new")
def connection_form(
    request: Request,
    connection_id: int | None = None,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    return render(
        request,
        "admin_connection.html",
        session,
        user,
        connection=(
            get_or_404(session, PlatformConnection, connection_id)
            if connection_id
            else None
        ),
        check_result=None,
    )


@router.post("/connections")
@router.post("/connections/{connection_id}")
def save_connection(
    request: Request,
    connection_id: int | None = None,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    connection = (
        locked_connection(session, connection_id)
        if connection_id
        else PlatformConnection(auth_disabled=True)
    )
    if connection is None:
        raise HTTPException(404)
    name, kind, base = (
        text_field(form, "name"),
        text_field(form, "kind", 32),
        text_field(form, "base_url", 2048).rstrip("/"),
    )
    token = str(form.get("token", ""))
    ca = text_field(form, "ca_bundle_path", 2048, False) or None
    if (
        kind not in ("github", "gitlab")
        or len(token) > 8192
        or any(ord(c) < 32 for c in token)
    ):
        raise FormError("kind" if kind not in ("github", "gitlab") else "token", "请选择有效平台类型并检查令牌格式。")
    try:
        _url_parts(base)
    except Exception:
        raise FormError(
            "base_url", "平台地址须为无账号、查询参数的 HTTPS 实例地址。"
        ) from None
    has_repos = connection_id and session.scalar(
        select(Repository.id).where(Repository.connection_id == connection.id).limit(1)
    )
    if has_repos and (
        kind != connection.kind or base != connection.base_url.rstrip("/")
    ):
        raise FormError(
            "base_url", "已有仓库的连接不能更换平台或实例地址；请为另一实例创建新连接。"
        )
    changed = (
        not connection_id
        or bool(token)
        or kind != connection.kind
        or base != connection.base_url
        or ca != connection.ca_bundle_path
    )
    connection.name, connection.kind, connection.base_url, connection.ca_bundle_path = (
        name,
        kind,
        base,
        ca,
    )
    if token:
        connection.encrypted_token = request.app.state.settings.encrypt_token(token)
    if changed:
        connection.auth_disabled, connection.last_checked_at, connection.last_error = (
            True,
            utc_now(),
            "连接已变更，请检查连接以恢复同步。",
        )
    session.add(connection)
    audit(
        session,
        user,
        "connection.update" if connection_id else "connection.create",
        connection,
        {"credential_changed": bool(token)},
    )
    commit(session)
    return RedirectResponse(f"/admin/connections/{connection.id}", 303)


@router.post("/connections/{connection_id}/check")
def check_connection(
    connection_id: int,
    request: Request,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    connection = get_or_404(session, PlatformConnection, connection_id)
    captured = connection_fingerprint(connection)
    failed = False
    try:
        with provider_for(request, connection) as provider:
            result = provider.check()
    except Exception:
        failed = True
    connection = checked_connection(session, connection_id, captured)
    if failed:
        connection.auth_disabled, connection.last_error, connection.last_checked_at = (
            True,
            "连接检查失败，请检查凭据、地址、网络与 CA。",
            utc_now(),
        )
        audit(session, user, "connection.check_failed", connection)
        commit(session)
        return render(
            request,
            "admin_connection.html",
            session,
            user,
            connection=connection,
            check_result=None,
            status_code=400,
        )
    connection.auth_disabled, connection.last_error, connection.last_checked_at = (
        False,
        None,
        utc_now(),
    )
    audit(session, user, "connection.checked", connection)
    commit(session)  # Commit connection identity before acquiring repository row locks.
    repositories = session.scalars(
        select(Repository).where(Repository.connection_id == connection.id)
    ).all()
    for repo in repositories:
        enqueue(request, session, repo, user)
    return render(
        request,
        "admin_connection.html",
        session,
        user,
        connection=connection,
        check_result=result,
        notice="连接检查成功；已安排关联仓库重新分析。",
    )


@router.get("/connections/{connection_id}/repositories")
def remote_repositories(
    connection_id: int,
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    connection = get_or_404(session, PlatformConnection, connection_id)
    if connection.auth_disabled:
        raise HTTPException(400, "请先检查连接，确认凭据有效。")
    try:
        with provider_for(request, connection) as provider:
            repos = list(provider.repositories())
    except Exception:
        raise HTTPException(400, "平台仓库读取失败，请检查连接或稍后重试。") from None
    page = number(request.query_params.get("page"))
    search = request.query_params.get("q", "").strip()[:255]
    repos = [repo for repo in repos if search.casefold() in repo.full_name.casefold()]
    imported_ids = set(session.scalars(select(Repository.external_id).where(Repository.connection_id == connection.id)))
    return render(
        request,
        "admin_import.html",
        session,
        user,
        connection=connection,
        remote_repositories=repos[(page - 1) * 50 : page * 50],
        total=len(repos),
        imported_ids=imported_ids,
        import_search=search,
        selected_project=request.query_params.get("project_id", ""),
    )


@router.post("/repositories/import")
def import_repositories(
    request: Request,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    connection = get_or_404(
        session, PlatformConnection, number(form.get("connection_id"), 0)
    )
    project = get_or_404(session, Project, number(form.get("project_id"), 0))
    ids = list(dict.fromkeys(form.getlist("external_ids")))
    if (
        not ids
        or len(ids) > 50
        or any(not isinstance(x, str) or not x or len(x) > 255 for x in ids)
    ):
        raise FormError("external_ids", "请一次选择 1–50 个仓库。")
    if connection.auth_disabled:
        raise FormError("external_ids", "请先检查平台连接。")
    captured = connection_fingerprint(connection)
    try:
        with provider_for(request, connection) as provider:
            metadata = []
            for external_id in ids:
                record = provider.repository(external_id)
                if record.external_id != external_id:
                    raise ValueError("Identity changed")
                validate_remote_url(record.clone_url, provider.web_root)
                validate_remote_url(record.web_url, provider.web_root)
                metadata.append(record)
    except Exception:
        raise FormError(
            "external_ids", "仓库身份或地址重新验证失败，请返回列表重试。"
        ) from None
    # Acquire repository locks before the connection lock, matching workers.
    existing = session.scalars(
        select(Repository)
        .where(
            Repository.connection_id == connection.id, Repository.external_id.in_(ids)
        )
        .order_by(Repository.id)
        .with_for_update()
    ).all()
    connection = checked_connection(session, connection.id, captured)
    existing_by_id = {repo.external_id: repo for repo in existing}
    repos = []
    for record in metadata:
        repo = existing_by_id.get(record.external_id)
        if repo is None:
            repo = Repository(
                connection_id=connection.id,
                external_id=record.external_id,
                name=record.full_name.rsplit("/", 1)[-1],
                full_name=record.full_name,
                web_url=record.web_url,
                clone_url=record.clone_url,
                default_branch=record.default_branch,
            )
            session.add(repo)
            session.flush()
        if not session.scalar(
            select(ProjectRepository.id).where(
                ProjectRepository.project_id == project.id,
                ProjectRepository.repository_id == repo.id,
            )
        ):
            session.add(ProjectRepository(project_id=project.id, repository_id=repo.id))
        audit(session, user, "repository.import", repo, {"project_id": project.id})
        repos.append(repo)
    commit(session)
    jobs = [enqueue(request, session, repo, user).id for repo in repos]
    return RedirectResponse(f"/projects/{project.id}?jobs=" + ",".join(map(str, jobs))
                            + f"&imported_connection={connection.id}", 303)


@router.get("/repositories/{repository_id}")
def repository_form(
    repository_id: int,
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    repo = get_or_404(session, Repository, repository_id)
    jobs = session.scalars(
        select(SyncJob)
        .where(SyncJob.repository_id == repo.id)
        .order_by(SyncJob.id.desc())
        .limit(30)
    ).all()
    return render(
        request,
        "admin_repository.html",
        session,
        user,
        repo=repo,
        jobs=jobs,
        job_progress=progress_context(session, request, repository_id=repo.id, user=user),
        notice=(
            "待恢复连接后分析：本地配置可以继续保存。"
            if repo.connection.auth_disabled
            else None
        ),
    )


@router.post("/repositories/{repository_id}")
def repository_config(
    repository_id: int,
    request: Request,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    get_or_404(session, Repository, repository_id)
    interval = text_field(form, "sync_interval_seconds", 7)
    if (
        not interval.isascii()
        or not interval.isdecimal()
        or not 30 <= int(interval) <= 604800
    ):
        raise FormError("sync_interval_seconds", "同步间隔须为 30–604800 秒。")
    prefixes = {
        key: [
            part.strip()
            for part in text_field(form, key + "_prefixes", 2000, False)
            .replace("\n", ",")
            .split(",")
            if part.strip()
        ]
        for key in ("release", "feature")
    }
    if any(len(values) > 50 for values in prefixes.values()):
        raise FormError(next(key + "_prefixes" for key, values in prefixes.items() if len(values) > 50), "每种角色最多设置 50 个前缀。")
    repo = locked_repository(session, repository_id)
    repo.sync_interval_seconds, repo.role_prefixes = int(interval), prefixes
    audit(
        session,
        user,
        "repository.configure",
        repo,
        {"sync_interval_seconds": int(interval), "role_prefixes": prefixes},
    )
    save_config(request, session, repo, user)
    return RedirectResponse(f"/admin/repositories/{repo.id}", 303)


@router.post("/repositories/{repository_id}/sync")
def synchronize(
    repository_id: int,
    request: Request,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    repo = get_or_404(session, Repository, repository_id)
    if repo.connection.auth_disabled:
        raise HTTPException(400, "请先恢复平台连接，再启动同步。")
    since = None
    if form.get("history_since"):
        try:
            since = datetime.strptime(str(form["history_since"]), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            if since > utc_now():
                raise ValueError()
        except ValueError:
            raise FormError("history_since", "历史起始日期须为今天或以前的有效日期。") from None
    audit(
        session,
        user,
        "repository.sync",
        repo,
        {"history_since": since.isoformat() if since else None},
    )
    job = enqueue(request, session, repo, user, since)
    return RedirectResponse(f"/admin/repositories/{repo.id}?jobs={job.id}", 303)


@router.post("/repositories/{repository_id}/projects")
def attach_project(
    repository_id: int,
    request: Request,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    repo = get_or_404(session, Repository, repository_id)
    project = get_or_404(session, Project, number(form.get("project_id"), 0))
    link = session.scalar(
        select(ProjectRepository).where(
            ProjectRepository.project_id == project.id,
            ProjectRepository.repository_id == repo.id,
        )
    )
    if form.get("action") == "detach":
        if link:
            session.delete(link)
    elif form.get("action") == "attach":
        if not link:
            session.add(ProjectRepository(project_id=project.id, repository_id=repo.id))
    else:
        raise HTTPException(400, "请选择关联或移除")
    audit(
        session,
        user,
        "repository.project",
        repo,
        {"project_id": project.id, "action": form["action"]},
    )
    commit(session)
    return RedirectResponse(f"/admin/repositories/{repo.id}", 303)


@router.get("/branches/{branch_id}")
def branch_form(
    branch_id: int,
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    branch = get_or_404(session, Branch, branch_id)
    repo = get_or_404(session, Repository, branch.repository_id)
    branches = session.scalars(
        select(Branch).where(Branch.repository_id == repo.id).order_by(Branch.name)
    ).all()
    config = branch.config
    manual_ids = (
        [
            x.get("branch_id") if isinstance(x, dict) else x
            for x in config.manual_targets
        ]
        if config
        else []
    )
    return render(
        request,
        "admin_branch.html",
        session,
        user,
        branch=branch,
        repo=repo,
        branches=branches,
        config=config,
        manual_ids=manual_ids,
    )


@router.post("/branches/{branch_id}")
def branch_config(
    branch_id: int,
    request: Request,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    branch = get_or_404(session, Branch, branch_id)
    role = text_field(form, "role", 32, False)
    if role not in ("", "main", "release", "feature", "other"):
        raise FormError("role", "请选择有效分支角色")
    if len(form.getlist("manual_targets")) > 100:
        raise FormError("manual_targets", "最多选择 100 个目标")

    def selected(value, field, allow_self=False):
        if not value:
            return None
        target = session.get(Branch, number(value, 0, 2**31 - 1))
        if target is None:
            raise FormError(field, "Not Found", status_code=404)
        if target.repository_id != branch.repository_id or (
            target.id == branch.id and not allow_self
        ):
            raise FormError(field, "来源和目标必须是同仓库的其他分支")
        return target.id

    source = selected(form.get("manual_source_branch_id"), "manual_source_branch_id")
    primary = selected(form.get("main_comparison_branch_id"), "main_comparison_branch_id", True)
    targets = list(dict.fromkeys(selected(x, "manual_targets") for x in form.getlist("manual_targets")))
    repo = locked_repository(session, branch.repository_id)
    config = branch.config or BranchConfig(branch_id=branch.id)
    config.role, config.manual_source_branch_id, config.main_comparison_branch_id = (
        role or None,
        source,
        primary,
    )
    config.manual_targets, config.active_release = targets, "active_release" in form
    if config.active_release and branch_role(repo, branch, config) != "release":
        raise FormError("active_release", "只有发布角色的分支可以设为活跃 release")
    config.updated_by_id, config.updated_at = user.id, utc_now()
    session.add(config)
    audit(
        session,
        user,
        "branch.configure",
        branch,
        {
            "role": role,
            "source": source,
            "targets": targets,
            "primary": primary,
            "active_release": config.active_release,
        },
    )
    save_config(request, session, repo, user)
    return RedirectResponse(f"/admin/branches/{branch.id}", 303)
