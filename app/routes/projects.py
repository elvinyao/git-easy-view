from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import optional_user, require_project_access
from app.db import get_session
from app.models import ProjectRepository, Repository, User
from app.project_overview import repository_overview, snapshot_views
from app.web import (
    filtered,
    query_state,
    render,
    visible_projects,
    repository_statuses,
    summarize_statuses,
)

router = APIRouter()


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
    if query["q"] or query["role"] or query["relation"]:
        repos = [repo for repo in repos if matches[repo.id]]
    count = len(repos)
    page = query["page"]
    displayed = repos[(page - 1) * 50 : page * 50]
    overviews = {
        repo.id: repository_overview(request.app.state.settings, repo, views[repo.id], matches[repo.id])
        for repo in displayed
    }
    return render(
        request,
        "project.html",
        session,
        user,
        project=project,
        repositories=displayed,
        total=count,
        all_count=all_count,
        branch_count=sum(len(items) for items in views.values()),
        overviews=overviews,
        stale_count=summary["attention"],
        summary=summary,
        repo_statuses=statuses,
    )
