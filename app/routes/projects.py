from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import current_user, require_project_access
from app.db import get_session
from app.models import Branch, ProjectRepository, Repository, User
from app.web import (
    branch_views,
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
    user: User = Depends(current_user),
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
    user: User = Depends(current_user),
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
    totals = dict(
        session.execute(
            select(Branch.repository_id, func.count())
            .where(
                Branch.repository_id.in_([r.id for r in repos]),
                Branch.deleted.is_(False),
            )
            .group_by(Branch.repository_id)
        ).all()
    )
    all_count = len(repos)
    statuses = repository_statuses(session, repos)
    summary = summarize_statuses(statuses.values())
    if query["q"] or query["role"] or query["relation"]:
        views = branch_views(session, repos)
        repos = [r for r in repos if filtered(views[r.id], query)]
    count = len(repos)
    page = query["page"]
    return render(
        request,
        "project.html",
        session,
        user,
        project=project,
        repositories=repos[(page - 1) * 50 : page * 50],
        total=count,
        all_count=all_count,
        branch_counts=totals,
        stale_count=summary["attention"],
        summary=summary,
        repo_statuses=statuses,
    )
