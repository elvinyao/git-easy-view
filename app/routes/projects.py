from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from urllib.parse import urlencode
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import optional_user, require_project_access
from app.db import get_session
from app.job_progress import progress_batch, progress_context
from app.models import ProjectRepository, Repository, User
from app.project_overview import repository_overview, snapshot_views
from app.releases import project_release_summary
from app.web import (
    filtered,
    query_state,
    query_url,
    render,
    visible_projects,
    repository_statuses,
    summarize_statuses,
)

router = APIRouter()


@router.get("/projects/{project_id}/jobs")
def project_jobs(
    project_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    require_project_access(session, user, project_id)
    return JSONResponse(
        progress_batch(session, request.query_params.get("jobs"), project_id=project_id, user=user),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/")
def home(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    projects = visible_projects(session, user)
    links = session.execute(
        select(ProjectRepository.project_id, Repository)
        .join(Repository, Repository.id == ProjectRepository.repository_id)
        .where(ProjectRepository.project_id.in_([p.id for p in projects]))
    ).all()
    statuses = repository_statuses(
        session, {repo.id: repo for _, repo in links}.values()
    )
    by_project = {project.id: [] for project in projects}
    for project_id, repo in links:
        by_project[project_id].append(statuses[repo.id])
    return render(
        request,
        "home.html",
        session,
        user,
        counts={pid: len(items) for pid, items in by_project.items()},
        summaries={pid: summarize_statuses(items) for pid, items in by_project.items()},
    )


@router.get("/projects/{project_id}")
def project_page(
    project_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    project = require_project_access(session, user, project_id)
    query = query_state(request)
    repos = session.scalars(
        select(Repository)
        .join(ProjectRepository)
        .where(ProjectRepository.project_id == project.id)
        .options(selectinload(Repository.connection))
        .order_by(Repository.full_name)
    ).all()
    all_count = len(repos)
    statuses = repository_statuses(session, repos)
    summary = summarize_statuses(statuses.values())
    views, _ = snapshot_views(session, repos)
    matches = {repo.id: filtered(views[repo.id], query) for repo in repos}
    if query.get("repo_q"):
        repos = [repo for repo in repos if query["repo_q"].casefold() in repo.full_name.casefold()]
    if query.get("attention"):
        repos = [repo for repo in repos if statuses[repo.id].attention]
    complete_pages = {repo.id: index // 50 + 1 for index, repo in enumerate(repos)}
    if query["q"] or query["role"] or query["relation"]:
        repos = [repo for repo in repos if matches[repo.id]]
    count = len(repos)
    page = query["page"]
    displayed = repos[(page - 1) * 50 : page * 50]
    overviews = {
        repo.id: repository_overview(request.app.state.settings, repo, views[repo.id], matches[repo.id])
        for repo in displayed
    }
    return_query = urlencode([(key, value) for key, value in request.query_params.multi_items()
                              if not key.startswith("inspect_")])
    project_return_path = f"/projects/{project.id}" + ("?" + return_query if return_query else "")

    def branch_urls(view, detail="branch"):
        branch = view.branch
        return_path = project_return_path + f"#repository-{branch.repository_id}"
        params = dict(branch_id=branch.id, detail=detail, lang=request.state.locale, return_to=return_path)
        if view.baseline:
            params["target_id"] = view.baseline.id
        if detail == "commit":
            params["commit"] = branch.head_oid
        root = f"/repositories/{branch.repository_id}"
        return dict(
            href=root + (f"/branches/{branch.id}" if detail == "branch" else "/branches") + "?" + urlencode(params),
            inspect=root + "/inspect?" + urlencode(params),
        )

    def complete_url(repo, group):
        base = query_url(request, q=None, role=None, relation=None, page=complete_pages[repo.id],
                         closed_repos=",".join(map(str, sorted(query["closed_repos"] - {repo.id})))) if query["q"] or query["role"] or query["relation"] else ""
        return f"{base}#{group}-{repo.id}"

    production, upcoming = project_release_summary(session, project.id)
    return render(
        request,
        "project.html",
        session,
        user,
        project=project,
        production=production,
        upcoming=upcoming,
        job_progress=progress_context(session, request, project_id=project.id, user=user),
        repositories=displayed,
        total=count,
        all_count=all_count,
        branch_count=sum(len(items) for items in views.values()),
        overviews=overviews,
        stale_count=summary["attention"],
        summary=summary,
        repo_statuses=statuses,
        branch_urls=branch_urls,
        complete_url=complete_url,
        project_return_path=project_return_path,
    )
