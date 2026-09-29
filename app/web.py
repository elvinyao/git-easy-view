"""Shared server-rendered view helpers; no network calls on member reads."""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth import session_data, verify_csrf
from app.models import Branch, Comparison, Project, ProjectMember, PullRequest
from app.relations import BranchRef, PullRequestRef, TargetRef, planned_targets
from app.sync import branch_role

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
STATES = {
    "identical": "相同",
    "contained": "提交已包含",
    "diverged": "未全部包含",
    "unrelated": "无共同历史",
    "missing": "未分析",
}
ROLES = {"main": "主干", "release": "发布", "feature": "功能", "other": "其他"}


def safe_url(value, connection=None):
    try:
        parts = urlsplit(value or "")
        if not (
            parts.scheme == "https"
            and parts.hostname
            and not parts.username
            and not parts.password
            and not any(ord(c) < 32 for c in value)
        ):
            return None
        if connection:
            from app.providers import validate_remote_url

            root = (
                connection.base_url.rstrip("/")
                .removesuffix("/api/v3")
                .removesuffix("/api/v4")
            )
            if root == "https://api.github.com":
                root = "https://github.com"
            validate_remote_url(value, root)
        return value
    except Exception:
        return None


templates.env.filters["safe_url"] = safe_url
templates.env.filters["date"] = lambda value: (
    value.strftime("%Y-%m-%d %H:%M UTC") if value else "尚未同步"
)
templates.env.globals.update(states=STATES, roles=ROLES)


def number(value, default=1, maximum=10000):
    text = str(value or "")
    if not (text.isascii() and text.isdecimal() and len(text) <= 10):
        return default
    parsed = int(text)
    # IDs must not be coerced into another existing entity; pagination may clamp.
    if default == 0:
        return parsed if 1 <= parsed <= maximum else 0
    return max(1, min(parsed, maximum))


def query_state(request):
    q = request.query_params
    expanded = {
        number(x, 0, 2**31 - 1) for x in q.get("expanded", "")[:1200].split(",")[:100]
    }
    return dict(
        q=q.get("q", "")[:200],
        role=q.get("role", "") if q.get("role") in ROLES else "",
        relation=q.get("relation", "") if q.get("relation") in STATES else "",
        page=number(q.get("page")),
        expanded=expanded - {0},
        branch_pages={
            key: number(value)
            for key, value in list(q.items())[:150]
            if key.startswith("bp_") and key[3:].isascii() and key[3:].isdecimal()
        },
    )


def query_url(request, **changes):
    state = query_state(request)
    values = {k: state[k] for k in ("q", "role", "relation", "page")}
    values.update(state["branch_pages"])
    values["expanded"] = ",".join(str(x) for x in sorted(state["expanded"]))
    target = number(request.query_params.get("target_id"), 0, 2**31 - 1)
    if target:
        values["target_id"] = target
    values.update(changes)
    return "?" + urlencode({k: v for k, v in values.items() if v not in ("", None)})


def visible_projects(session, user):
    stmt = select(Project).order_by(Project.name)
    if not user.is_admin:
        stmt = stmt.join(ProjectMember).where(ProjectMember.user_id == user.id)
    return session.scalars(stmt).all()


def render(request, template, session, user, *, status_code=200, **context):
    base = dict(
        user=user,
        projects=visible_projects(session, user),
        csrf_token=session_data(request)["csrf"],
        query=query_state(request),
        query_url=lambda **changes: query_url(request, **changes),
    )
    return templates.TemplateResponse(
        request, template, {**base, **context}, status_code=status_code
    )


async def form_data(request: Request):
    await verify_csrf(request)
    return await request.form()


def require_branch(session, repo, branch_id):
    branch = session.get(Branch, branch_id)
    if branch is None or branch.repository_id != repo.id:
        raise HTTPException(404)
    return branch


def branch_views(session, repositories):
    """Batch relations for a set of authorized repositories. Never resolve fork metadata."""
    repos = {r.id: r for r in repositories}
    if not repos:
        return {}
    branches = session.scalars(
        select(Branch)
        .where(Branch.repository_id.in_(repos))
        .options(selectinload(Branch.config))
    ).all()
    by_repo = {rid: [] for rid in repos}
    for branch in branches:
        by_repo[branch.repository_id].append(branch)
    prs = session.scalars(
        select(PullRequest).where(PullRequest.repository_id.in_(repos))
    ).all()
    current_oids = {b.head_oid for b in branches if b.head_oid and not b.deleted}
    comparisons = session.scalars(
        select(Comparison).where(
            Comparison.repository_id.in_(repos),
            Comparison.rule_version == 1,
            Comparison.source_oid.in_(current_oids),
            Comparison.target_oid.in_(current_oids),
        )
    ).all()
    cache = {(c.repository_id, c.source_oid, c.target_oid): c for c in comparisons}
    result = {}
    for rid, items in by_repo.items():
        repo = repos[rid]
        by_id = {b.id: b for b in items}
        all_refs = {
            b.id: BranchRef(
                rid,
                b.id,
                b.name,
                b.head_oid if not b.deleted else None,
                branch_role(repo, b, b.config),
                repo.external_id,
                repo.connection_id,
            )
            for b in items
        }
        refs = {
            b.id: BranchRef(
                rid,
                b.id,
                b.name,
                b.head_oid,
                branch_role(repo, b, b.config),
                repo.external_id,
                repo.connection_id,
            )
            for b in items
            if not b.deleted and b.head_oid
        }
        base_targets = [
            r
            for r in refs.values()
            if r.name == repo.default_branch
            or (
                r.role == "release"
                and by_id[r.branch_id].config
                and by_id[r.branch_id].config.active_release
            )
        ]
        own_prs = [
            p
            for p in prs
            if p.repository_id == rid and p.connection_id == repo.connection_id
        ]
        prs_by_branch = {}
        for pr in own_prs:
            if (
                pr.source_repository_id == rid
                if pr.source_repository_id is not None
                else pr.source_external_repo_id == repo.external_id
            ):
                prs_by_branch.setdefault(pr.source_branch, []).append(pr)
        default_ref = next(
            (r for r in refs.values() if r.name == repo.default_branch), None
        )
        views = []
        for branch in items:
            cfg = branch.config
            ref = refs.get(branch.id)
            relevant = prs_by_branch.get(branch.name, [])
            pr_refs = [
                PullRequestRef(
                    p.state,
                    p.source_repository_id,
                    p.source_branch,
                    p.target_repository_id,
                    p.target_branch,
                    str(p.id),
                    p.source_external_repo_id,
                    p.target_external_repo_id,
                    p.connection_id,
                )
                for p in relevant
            ]
            manual_ids = [
                (x.get("branch_id") if isinstance(x, dict) else x)
                for x in (cfg.manual_targets if cfg else [])
            ]
            plans = planned_targets(
                ref or all_refs[branch.id],
                pr_refs if ref else [],
                [
                    all_refs[i]
                    for i in manual_ids
                    if isinstance(i, int) and i in all_refs
                ],
                refs.values(),
            )
            distinct = {}
            for plan in plans:
                t = plan.target
                distinct[
                    (t.repository_id, t.external_repository_id, t.name, t.connection_id)
                ] = t

            def as_target(item):
                return (
                    TargetRef(
                        rid,
                        repo.external_id,
                        item.branch_id,
                        item.name,
                        item.oid,
                        repo.connection_id,
                    )
                    if item
                    else None
                )

            primary = (
                as_target(all_refs.get(cfg.main_comparison_branch_id)) if cfg else None
            )
            reason = "人工指定比较对象" if primary else ""
            if primary is None and len(distinct) == 1:
                primary, reason = next(iter(distinct.values())), "唯一计划目标"
            if primary is None:
                primary = as_target(default_ref)
                reason = "默认主干比较（不代表计划合入）"
            comparison = (
                cache.get((rid, branch.head_oid, primary.oid))
                if primary and primary.repository_id == rid and not branch.deleted
                else None
            )
            targets = dict(distinct)
            for r in base_targets:
                t = as_target(r)
                targets[
                    (t.repository_id, t.external_repository_id, t.name, t.connection_id)
                ] = t
            if primary:
                targets[
                    (
                        primary.repository_id,
                        primary.external_repository_id,
                        primary.name,
                        primary.connection_id,
                    )
                ] = primary
            target_views = [
                (
                    t,
                    (
                        cache.get((rid, branch.head_oid, t.oid))
                        if t.repository_id == rid and not branch.deleted
                        else None
                    ),
                )
                for t in targets.values()
            ]
            sources = list(branch.source_candidates or [])
            if cfg and cfg.manual_source_branch_id:
                source = by_id.get(cfg.manual_source_branch_id)
                sources = (
                    [
                        dict(
                            kind="manual",
                            name=source.name,
                            oid=source.head_oid,
                            deleted=source.deleted,
                        )
                    ]
                    if source
                    else []
                )
            views.append(
                SimpleNamespace(
                    branch=branch,
                    role=branch_role(repo, branch, cfg),
                    config=cfg,
                    sources=sources,
                    plans=plans,
                    primary=primary,
                    primary_reason=reason,
                    comparison=comparison,
                    state=comparison.state if comparison else "missing",
                    targets=target_views,
                    prs=relevant,
                    refs=refs,
                )
            )
        views.sort(
            key=lambda v: (
                0 if v.role == "main" else 1 if v.role == "release" else 2,
                -(v.branch.commit_time.timestamp() if v.branch.commit_time else 0),
                v.branch.name,
            )
        )
        result[rid] = views
    return result


def filtered(views, query):
    return [
        v
        for v in views
        if not v.branch.deleted
        and query["q"].casefold() in v.branch.name.casefold()
        and (not query["role"] or v.role == query["role"])
        and (not query["relation"] or v.state == query["relation"])
    ]
