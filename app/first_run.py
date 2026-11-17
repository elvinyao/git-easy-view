"""First-run setup: the first visitor creates the administrator in the browser."""
import base64
import hashlib
import hmac
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import has_users, session_data, set_session, verify_csrf
from app.i18n import create_templates
from app.models import AuditEvent, User

router = APIRouter()
templates = create_templates()


def setup_code(settings) -> str:
    """Stable per-installation code; only someone with server access can read it."""
    digest = hmac.new(settings.session_secret.encode(), b"gev-first-run-setup-v1", hashlib.sha256).digest()
    text = base64.b32encode(digest[:5]).decode()
    return f"{text[:4]}-{text[4:]}"


def _page(request, *, error=None, username="", status_code=200):
    existing = session_data(request)
    csrf = existing["csrf"] if existing else secrets.token_urlsafe(32)
    response = templates.TemplateResponse(request, "setup.html", {
        "csrf_token": csrf, "error": error, "username": username, "next_url": "/setup",
        "needs_code": request.app.state.settings.production,
    }, status_code=status_code)
    if not existing:
        set_session(response, request, None, csrf)
    return response


@router.get("/setup")
def setup_page(request: Request):
    with Session(request.app.state.engine) as session:
        if has_users(session):
            return RedirectResponse("/login", status_code=303)
    return _page(request)


@router.post("/setup")
async def setup(request: Request):
    await verify_csrf(request)
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    settings = request.app.state.settings
    error = None
    if not 1 <= len(username) <= 150:
        error = "请填写用户名（最多 150 个字符）。"
    elif not 12 <= len(password) <= 1024:
        error = "密码长度须为 12–1024 个字符。"
    elif password != str(form.get("password_confirm", "")):
        error = "两次输入的密码不一致。"
    elif settings.production:
        supplied = "".join(ch for ch in str(form.get("code", "")).upper() if ch.isalnum())
        expected = setup_code(settings).replace("-", "")
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            error = "设置码不正确。请在服务器上运行 ./gev -p setup-code 查看。"
    with Session(request.app.state.engine) as session:
        if has_users(session):
            return RedirectResponse("/login", status_code=303)
        if error:
            return _page(request, error=error, username=username, status_code=400)
        admin = User(username=username, is_admin=True)
        admin.set_password(password)
        session.add(admin)
        session.flush()
        # The insert holds SQLite's write lock: a concurrent first visitor
        # now sees two accounts and backs out instead of creating a second admin.
        if session.scalar(select(func.count(User.id))) != 1:
            session.rollback()
            return RedirectResponse("/login", status_code=303)
        session.add(AuditEvent(actor_id=admin.id, action="setup.first_admin", entity_type="users", entity_id=admin.id))
        session.commit()
        user_id = admin.id
    response = RedirectResponse("/admin", status_code=303)
    set_session(response, request, user_id)
    return response
