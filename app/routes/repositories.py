from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.auth import optional_user, require_admin, require_repository_access
from app.db import get_session
from app.git_service import GitError
from app.git_workspace import workspace_context
from app.jobs import enqueue_comparison
from app.job_progress import progress_batch
from app.models import AuditEvent, PullRequest, SyncJob, User
from app.sync import RepositoryBusy, read_unique_commits
from app.web import (
    apply_target_override,
    branch_views,
    filtered,
    form_data,
    number,
    query_state,
    render,
    require_branch,
)

router = APIRouter()


@router.get("/repositories/{repository_id}")
def repository_page(
    repository_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    return render(request, "repository.html", session, user, repo=repo)


@router.get("/repositories/{repository_id}/branches")
@router.get("/repositories/{repository_id}/log")
def branches(
    repository_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    query = query_state(request)
    all_views = apply_target_override(session, repo, branch_views(session, [repo])[repo.id], request)
    views = filtered(all_views, query)
    total_branches = sum(not view.branch.deleted for view in all_views)
    page = query["page"]
    if request.url.path.endswith("/log"):
        return render(request, "git_log.html", session, user, repo=repo, **workspace_context(request, repo, views, total_branches=total_branches))
    if request.query_params.get("fragment") != "1":
        return render(
            request, "git_workspace.html", session, user, repo=repo,
            views=views[(page - 1) * 50 : page * 50], total=len(views),
            **workspace_context(request, repo, views, total_branches=total_branches),
        )
    return render(
        request,
        "branches.html",
        session,
        user,
        repo=repo,
        views=views[(page - 1) * 50 : page * 50],
        total=len(views),
    )


@router.get("/repositories/{repository_id}/branches/{branch_id}")
@router.get("/repositories/{repository_id}/branches/{branch_id}/fragment")
def branch_detail(
    repository_id: int,
    branch_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    branch = require_branch(session, repo, branch_id)
    view = next(
        v for v in apply_target_override(session, repo, branch_views(session, [repo])[repo.id], request) if v.branch.id == branch.id
    )
    commits, commit_error = [], None
    if view.comparison and view.comparison.ahead != 0:
        try:
            commits = read_unique_commits(
                request.app.state.settings,
                repo.id,
                view.comparison.source_oid,
                view.comparison.target_oid,
                limit=50,
            )
        except RepositoryBusy:
            commit_error = "仓库正在同步，请稍后重试独有提交读取。"
        except (GitError, ValueError):
            commit_error = "本地提交暂不可读取，请稍后重试。已缓存比较结果仍可查看。"
    actor = (
        session.get(User, view.config.updated_by_id)
        if view.config and view.config.updated_by_id
        else None
    )
    return render(
        request,
        (
            "branch_fragment.html"
            if request.url.path.endswith("/fragment")
            else "branch.html"
        ),
        session,
        user,
        repo=repo,
        view=view,
        commits=commits,
        commit_error=commit_error,
        actor=actor,
    )


@router.get("/repositories/{repository_id}/inspect")
def inspect(
    repository_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    branch = require_branch(session, repo, number(request.query_params.get("branch_id"), 0, 2**31 - 1))
    if branch.deleted:
        raise HTTPException(404)
    views = apply_target_override(session, repo, branch_views(session, [repo])[repo.id], request)
    view = next(view for view in views if view.branch.id == branch.id)
    return render(
        request, "project_inspector.html", session, user, repo=repo, view=view,
        **workspace_context(request, repo, [view]),
    )


@router.post("/repositories/{repository_id}/branches/{branch_id}/compare")
def compare(
    repository_id: int,
    branch_id: int,
    request: Request,
    user: User = Depends(require_admin),
    form=Depends(form_data),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    require_branch(session, repo, branch_id)
    target_id = number(form.get("target_id"), 0, 2**31 - 1)
    require_branch(session, repo, target_id)
    session.add(
        AuditEvent(
            actor_id=user.id,
            action="comparison.request",
            entity_type="repositories",
            entity_id=repo.id,
            details={"source_branch_id": branch_id, "target_branch_id": target_id},
        )
    )
    try:
        job = enqueue_comparison(
            session,
            repo.id,
            branch_id,
            target_id,
            requested_by=user,
            settings=request.app.state.settings,
            queue=getattr(request.app.state, "job_queue", None),
        )
    except ValueError:
        raise HTTPException(400, "请选择同仓库中仍存在的分支") from None
    if "application/json" in request.headers.get("accept", ""):
        return {"job_id": job.id, "status": job.status}
    return RedirectResponse(
        f"/repositories/{repo.id}/branches/{branch_id}?target_id={target_id}&job_id={job.id}",
        303,
    )


@router.get("/repositories/{repository_id}/status")
def status(
    repository_id: int,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    return {
        "git_synced_at": repo.git_synced_at,
        "api_synced_at": repo.api_synced_at,
        "git_error": bool(repo.sync_error),
        "api_error": bool(repo.api_error),
    }


@router.get("/repositories/{repository_id}/jobs")
def repository_jobs(
    repository_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    require_repository_access(session, user, repository_id)
    return JSONResponse(
        progress_batch(session, request.query_params.get("jobs"), repository_id=repository_id, user=user),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/repositories/{repository_id}/jobs/{job_id}")
def job_status(
    repository_id: int,
    job_id: int,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    require_repository_access(session, user, repository_id)
    job = session.get(SyncJob, job_id)
    if not job or job.repository_id != repository_id:
        raise HTTPException(404)
    return {
        "job_id": job.id,
        "status": job.status,
        "queue_pending": bool(job.enqueue_error),
        "failed": bool(job.error),
    }


@router.get("/repositories/{repository_id}/pull-requests/{pr_id}")
def pull_request(
    repository_id: int,
    pr_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    pr = session.get(PullRequest, pr_id)
    if (
        not pr
        or pr.repository_id != repository_id
        or pr.connection_id != repo.connection_id
    ):
        raise HTTPException(404)
    return render(request, "pull_request.html", session, user, repo=repo, pr=pr)


@router.get("/repositories/{repository_id}/pull-requests")
def pull_requests(
    repository_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    clause = (
        PullRequest.repository_id == repo.id,
        PullRequest.connection_id == repo.connection_id,
    )
    page = query_state(request)["page"]
    records = session.scalars(
        select(PullRequest)
        .where(*clause)
        .order_by(PullRequest.updated_at.desc(), PullRequest.id.desc())
        .offset((page - 1) * 50)
        .limit(50)
    ).all()
    total = session.scalar(select(func.count()).select_from(PullRequest).where(*clause))
    return render(
        request,
        "pull_requests.html",
        session,
        user,
        repo=repo,
        records=records,
        total=total,
    )
