from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.auth import current_user, require_repository_access
from app.db import get_session
from app.git_service import GitError
from app.jobs import enqueue_comparison
from app.models import AuditEvent, PullRequest, SyncJob, User
from app.sync import RepositoryBusy, read_unique_commits
from app.web import (
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
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    return render(request, "repository.html", session, user, repo=repo)


@router.get("/repositories/{repository_id}/branches")
def branches(
    repository_id: int,
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    query = query_state(request)
    views = filtered(branch_views(session, [repo])[repo.id], query)
    page = query["page"]
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
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    branch = require_branch(session, repo, branch_id)
    view = next(
        v for v in branch_views(session, [repo])[repo.id] if v.branch.id == branch.id
    )
    selected = number(request.query_params.get("target_id"), 0, 2**31 - 1)
    if selected:
        target = require_branch(session, repo, selected)
        if target.deleted or not target.head_oid:
            raise HTTPException(400, "比较对象必须是当前分支")
        from app.models import Comparison

        view.comparison = (
            session.scalar(
                select(Comparison).where(
                    Comparison.repository_id == repo.id,
                    Comparison.source_oid == branch.head_oid,
                    Comparison.target_oid == target.head_oid,
                    Comparison.rule_version == 1,
                )
            )
            if not branch.deleted
            else None
        )
        view.primary = view.refs[target.id]
        view.state = view.comparison.state if view.comparison else "missing"
        view.primary_reason = "本次选择的比较对象"
    commits, commit_error = [], None
    if view.comparison:
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


@router.post("/repositories/{repository_id}/branches/{branch_id}/compare")
def compare(
    repository_id: int,
    branch_id: int,
    request: Request,
    user: User = Depends(current_user),
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
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    repo = require_repository_access(session, user, repository_id)
    return {
        "git_synced_at": repo.git_synced_at,
        "api_synced_at": repo.api_synced_at,
        "git_error": bool(repo.sync_error),
        "api_error": bool(repo.api_error),
    }


@router.get("/repositories/{repository_id}/jobs/{job_id}")
def job_status(
    repository_id: int,
    job_id: int,
    user: User = Depends(current_user),
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
    user: User = Depends(current_user),
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
    user: User = Depends(current_user),
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
