"""Bounded, read-only views of explicitly requested synchronization jobs."""

from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs import ACTIVE, TERMINAL
from app.models import ProjectRepository, Repository, SyncJob, User

MAX_JOBS = 50
STATUS_LABELS = {
    "queued": "已排队",
    "running": "执行中",
    "succeeded": "成功",
    "failed": "失败",
    "auth_failed": "凭据失效",
    "superseded": "已被新请求取代",
}
STATUS_MESSAGES = {
    "queued": "请求已保存，等待后台同步。",
    "running": "正在同步；继续显示上次快照。",
    "succeeded": "同步已完成。",
    "failed": "同步失败，请由管理员检查连接或重试。",
    "auth_failed": "凭据失效，请由管理员恢复平台连接。",
    "superseded": "本次请求已被新请求取代，请查看最新同步状态。",
}


def parse_job_ids(raw: str | None) -> list[int]:
    parts = raw.split(",") if raw and len(raw) <= MAX_JOBS * 20 else []
    if not 1 <= len(parts) <= MAX_JOBS or any(
        not value.isascii() or not value.isdecimal() or len(value) > 19
        or not 1 <= int(value) <= 2**63 - 1 for value in parts
    ):
        raise HTTPException(400, "请指定 1–50 个有效任务。")
    return list(dict.fromkeys(map(int, parts)))


def progress_batch(
    session: Session, raw: str | None, *, project_id: int | None = None,
    repository_id: int | None = None, user: User | None = None,
) -> dict:
    """Caller authorizes the container; every job must belong to that container."""
    if (project_id is None) == (repository_id is None):
        raise ValueError("Exactly one progress scope is required")
    identifiers = parse_job_ids(raw)
    query = select(SyncJob, Repository).join(Repository, SyncJob.repository_id == Repository.id)
    query = query.where(SyncJob.id.in_(identifiers), SyncJob.kind == "sync")
    if project_id is not None:
        query = query.join(ProjectRepository, ProjectRepository.repository_id == Repository.id).where(
            ProjectRepository.project_id == project_id
        )
        path = f"/projects/{project_id}/jobs"
    else:
        query = query.where(Repository.id == repository_id)
        path = f"/repositories/{repository_id}/jobs"
    rows = {job.id: (job, repo) for job, repo in session.execute(query)}
    if len(rows) != len(identifiers):
        raise HTTPException(404)
    counts = dict.fromkeys(STATUS_LABELS, 0)
    items = []
    for identifier in identifiers:
        job, repo = rows[identifier]
        status = job.status if job.status in STATUS_LABELS else "queued" if job.status in ACTIVE else "failed"
        counts[status] += 1
        item = {
            "job_id": job.id,
            "repository_id": repo.id,
            "repository_name": repo.full_name,
            "status": status,
            "terminal": status in TERMINAL,
            "queue_pending": bool(job.enqueue_error) and status == "queued",
        }
        if user and user.is_admin:
            item["repository_admin_url"] = f"/admin/repositories/{repo.id}"
            item["connection_admin_url"] = f"/admin/connections/{repo.connection_id}"
        items.append(item)
    terminal = not counts["queued"] and not counts["running"]
    state = next((status for status in ("running", "queued", "auth_failed", "failed", "superseded", "succeeded") if counts[status]), "failed")
    return {
        "state": state,
        "terminal": terminal,
        "successful": counts["succeeded"] == len(items),
        "counts": counts,
        "jobs": items,
        "status_url": path + "?" + urlencode({"jobs": ",".join(map(str, identifiers))}),
    }


def progress_context(session: Session, request, **scope) -> dict | None:
    if "jobs" not in request.query_params:
        return None
    batch = progress_batch(session, request.query_params.get("jobs"), **scope)
    return dict(batch, labels=STATUS_LABELS, messages=STATUS_MESSAGES)
