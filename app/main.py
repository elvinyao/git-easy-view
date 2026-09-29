from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import current_user, session_data, set_session, verify_csrf
from app.config import Settings
from app.db import make_engine
from app.models import User

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI()
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)

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
        if (
            request.method == "POST"
            and exc.status_code in (400, 409, 422)
            and "application/json" not in request.headers.get("accept", "")
        ):
            return templates.TemplateResponse(
                request,
                "form_error.html",
                {"message": str(exc.detail)},
                status_code=exc.status_code,
            )
        return await http_exception_handler(request, exc)

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
