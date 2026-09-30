from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import current_user, session_data, set_session, verify_csrf
from app.config import Settings
from app.db import make_engine
from app.i18n import (
    LOCALE_COOKIE,
    create_templates,
    normalize_locale,
    select_locale,
    translate,
)
from app.models import User

templates = create_templates()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI()
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)

    @app.middleware("http")
    async def language_preference(request: Request, call_next):
        request.state.locale = select_locale(request)
        response = await call_next(request)
        if not request.url.path.startswith("/static/"):
            response.headers["Content-Language"] = request.state.locale
            vary = [
                value.strip()
                for value in response.headers.get("Vary", "").split(",")
                if value.strip()
            ]
            for header in ("Accept-Language", "Cookie"):
                if header.lower() not in {value.lower() for value in vary}:
                    vary.append(header)
            response.headers["Vary"] = ", ".join(vary)
            explicit = normalize_locale(request.query_params.get("lang"))
            if explicit:
                response.set_cookie(
                    LOCALE_COOKIE,
                    explicit,
                    max_age=365 * 24 * 60 * 60,
                    httponly=True,
                    secure=settings.cookie_secure,
                    samesite="lax",
                    path="/",
                )
        return response

    from app.routes import projects, repositories, admin

    app.include_router(projects.router)
    app.include_router(repositories.router)
    app.include_router(admin.router)
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )

    @app.get("/health", include_in_schema=False)
    def readiness():
        try:
            with app.state.engine.connect() as connection:
                connection.execute(select(1))
        except SQLAlchemyError:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    @app.exception_handler(StarletteHTTPException)
    async def friendly_form_error(request: Request, exc):
        detail = (
            translate(request.state.locale, exc.detail)
            if isinstance(exc.detail, str)
            else exc.detail
        )
        if (
            request.method == "POST"
            and exc.status_code in (400, 409, 422)
            and "application/json" not in request.headers.get("accept", "")
        ):
            return templates.TemplateResponse(
                request,
                "form_error.html",
                {"message": detail},
                status_code=exc.status_code,
            )
        return await http_exception_handler(
            request,
            StarletteHTTPException(exc.status_code, detail=detail, headers=exc.headers),
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc):
        message = translate(request.state.locale, "请求参数无效，请检查后重试。")
        if "application/json" in request.headers.get("accept", ""):
            errors = [{**error, "msg": message} for error in exc.errors()]
            return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)
        return templates.TemplateResponse(
            request, "form_error.html", {"message": message}, status_code=422
        )

    @app.get("/login")
    def login_page(request: Request):
        existing = session_data(request)
        csrf = existing["csrf"] if existing else None
        if csrf is None:
            import secrets

            csrf = secrets.token_urlsafe(32)
        response = templates.TemplateResponse(
            request, "login.html", {"csrf_token": csrf, "error": None}
        )
        if not existing:
            set_session(response, request, None, csrf)
        return response

    async def login_credentials(request: Request) -> tuple[str, str]:
        await verify_csrf(request)
        form = await request.form()
        return str(form.get("username", "")), str(form.get("password", ""))

    @app.post("/login")
    def login(
        request: Request, credentials: tuple[str, str] = Depends(login_credentials)
    ):
        username, password = credentials
        with Session(app.state.engine) as session:
            user = session.scalar(select(User).where(User.username == username))
            if user is None or not user.is_active or not user.check_password(password):
                return templates.TemplateResponse(
                    request,
                    "login.html",
                    {
                        "csrf_token": session_data(request)["csrf"],
                        "error": "Invalid username or password",
                    },
                    status_code=401,
                )
            user_id = user.id
        response = RedirectResponse("/", status_code=303)
        set_session(response, request, user_id)
        return response

    @app.post("/logout")
    async def logout(request: Request, user: User = Depends(current_user)):
        await verify_csrf(request)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("gev_session", path="/")
        return response

    return app
