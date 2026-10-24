"""Release form state and recovery after a rejected transaction."""

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import session_data
from app.models import User
from app.web import (
    FIELD_LABELS,
    FormError,
    REPOSITORY_FIELD,
    form_data as verified_form_data,
    invalid_field,
    render,
    text_field,
)

FORM_FIELDS = {
    "title",
    "scheduled_date",
    "notes",
    "branch",
    "tag",
    "target",
    "mr",
    "oid",
    "confirmed_oid",
    "deployed_at",
    "connection_id",
    "external_id",
    "version",
    "current_id",
    "rollback_id",
    "action",
    "intent",
}


async def form_data(request: Request):
    form = await verified_form_data(request)
    # Capture only known nonsecret inputs, keeping the exact submitted concurrency
    # tokens. A recovered page must never silently adopt a newer version or OID.
    request.state.form_values = {
        key: value for key, value in form.items()
        if isinstance(value, str) and (key in FORM_FIELDS or REPOSITORY_FIELD.fullmatch(key))
    }
    request.state.form_lists = {"repos": [value for value in form.getlist("repos") if isinstance(value, str)]}
    request.state.form_submitted = True
    request.state.release_form_submitted = True
    return form


def recover_form(request: Request, exc):
    """Read committed rows in a new session after the request session rolls back."""
    if not getattr(request.state, "release_form_submitted", False):
        return None
    from app.routes import releases

    params = {key: int(value) for key, value in request.path_params.items()
              if key in ("project_id", "repository_id", "plan_id", "item_id")
              and str(value).isascii() and str(value).isdecimal() and len(str(value)) <= 10}
    project_id = params.get("project_id")
    if project_id is None:
        return None
    request.state.form_errors = {getattr(exc, "field", "_form"): exc.detail}
    request.state.form_item_id = params.get("item_id")
    identity = session_data(request)
    with Session(request.app.state.engine) as session:
        user = session.get(User, identity["user_id"]) if identity else None
        if not user or not user.is_active or not user.is_admin:
            return None
        path = request.url.path
        try:
            if "repository_id" in params:
                request.state.form_kind = "source" if path.endswith("/source") else "production"
                response = releases.production_form(project_id, params["repository_id"], request, user, session)
            elif path.endswith("/action") or "item_id" in params:
                request.state.form_kind = "item" if "item_id" in params else "plan"
                response = releases.release_detail(project_id, params["plan_id"], request, user, session)
            else:
                request.state.form_kind = "release"
                response = releases.release_form(project_id, request, params.get("plan_id"), user, session)
        except HTTPException:
            # A concurrently deleted or inaccessible parent cannot safely be
            # recovered. Let the ordinary error page retain the original error.
            return None
        response.status_code = exc.status_code
        return response
