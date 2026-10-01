import secrets
import re
from urllib.parse import unquote, urlencode, urlsplit

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Project, ProjectRepository, Repository, User


SESSION_COOKIE = "gev_session"


def _serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(request.app.state.settings.session_secret, salt="gev-session-v1")


def session_data(request: Request) -> dict | None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    try:
        data = _serializer(request).loads(cookie, max_age=request.app.state.settings.session_max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or set(data) != {"user_id", "csrf"}:
        return None
    if not isinstance(data["csrf"], str) or not isinstance(data["user_id"], (int, type(None))):
        return None
    return data


def set_session(response, request: Request, user_id: int | None, csrf: str | None = None) -> str:
    csrf = csrf or secrets.token_urlsafe(32)
    token = _serializer(request).dumps({"user_id": user_id, "csrf": csrf})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=request.app.state.settings.session_max_age,
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return csrf


async def verify_csrf(request: Request) -> None:
    data = session_data(request)
    if not data:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    supplied = request.headers.get("X-CSRF-Token")
    if supplied is None:
        form = await request.form()
        supplied = form.get("csrf_token")
    if not isinstance(supplied, str) or not secrets.compare_digest(supplied.encode(), data["csrf"].encode()):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def optional_user(request: Request, session: Session = Depends(get_session)) -> User | None:
    """Resolve a valid account without requiring a session for public reads."""
    data = session_data(request)
    user_id = data["user_id"] if data else None
    user = session.get(User, user_id) if user_id is not None else None
    return user if user is not None and user.is_active else None


def safe_next(value: str | None) -> str:
    """Allow local absolute paths only, including encoded browser ambiguities."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        return "/"
    decoded = value
    for _ in range(4):
        if (not decoded.startswith("/") or decoded.startswith("//")
                or "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded)):
            return "/"
        try:
            parts = urlsplit(decoded)
        except ValueError:
            return "/"
        if parts.scheme or parts.netloc:
            return "/"
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return value
        decoded = next_decoded
    return "/"


def login_destination(request: Request) -> str:
    destination = request.url.path
    if request.method != "GET":
        if destination == "/admin/repositories/import":
            try:
                referer = urlsplit(request.headers.get("referer", ""))
                origin = urlsplit(str(request.url))
                if (referer.scheme == origin.scheme and referer.netloc == origin.netloc
                        and re.fullmatch(r"/admin/connections/[0-9]+/repositories", referer.path)):
                    return safe_next(referer.path + ("?" + referer.query if referer.query else ""))
            except ValueError:
                pass
        destination = {
            "/admin/projects": "/admin",
            "/admin/users": "/admin/users/new",
            "/admin/connections": "/admin/connections/new",
            "/admin/repositories/import": "/admin",
            "/logout": "/",
        }.get(destination, destination)
        destination = re.sub(r"^(/admin/repositories/[0-9]+)/(?:sync|projects)$", r"\1", destination)
        destination = re.sub(r"^(/admin/connections/[0-9]+)/check$", r"\1", destination)
        destination = re.sub(r"^(/admin/projects/[0-9]+)/members$", r"\1", destination)
        destination = re.sub(r"^(/repositories/[0-9]+/branches/[0-9]+)/compare$", r"\1", destination)
    if request.url.query:
        destination += "?" + request.url.query
    return safe_next(destination)


def current_user(request: Request, user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login?" + urlencode({"next": login_destination(request)})})
    return user


get_current_user = current_user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403)
    return user


def require_project_access(session: Session, user: User | None, project_id: int) -> Project:
    """Projects are public dashboards; their configuration uses require_admin."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404)
    return project


def require_repository_access(session: Session, user: User | None, repository_id: int) -> Repository:
    """Publish only project-linked repositories outside the administration area."""
    repository = session.get(Repository, repository_id)
    if repository is None:
        raise HTTPException(status_code=404)
    if not (user and user.is_admin):
        linked = session.scalar(
            select(ProjectRepository.id)
            .where(ProjectRepository.repository_id == repository_id)
            .limit(1)
        )
        if linked is None:
            raise HTTPException(status_code=404)
    return repository
