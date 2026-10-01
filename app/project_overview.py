"""Batch snapshot summaries for repositories the project route has authorized."""

from functools import lru_cache
from types import SimpleNamespace

from sqlalchemy import exists, select
from sqlalchemy.orm import selectinload

from app.git_service import MAX_COMMITS, GitError, GitService
from app.jobs import repository_lock
from app.models import Branch, Comparison
from app.sync import branch_role, cache_path


def snapshot_views(session, repositories):
    """Compare every current tip to its repository's default main/master."""
    repos = {repo.id: repo for repo in repositories}
    if not repos:
        return {}, {}
    branches = session.scalars(
        select(Branch).where(Branch.repository_id.in_(repos), Branch.deleted.is_(False))
        .options(selectinload(Branch.config))
    ).all()
    by_repo = {rid: [] for rid in repos}
    for branch in branches:
        by_repo[branch.repository_id].append(branch)
    defaults = {}
    for rid, items in by_repo.items():
        names = {branch.name: branch for branch in items}
        defaults[rid] = next(
            (names[name] for name in (repos[rid].default_branch, "main", "master") if name in names),
            next((branch for branch in items if branch_role(repos[rid], branch, branch.config) == "main"), None),
        )
    target_oids = {branch.head_oid for branch in defaults.values() if branch and branch.head_oid}
    comparisons = session.scalars(
        select(Comparison).where(
            Comparison.repository_id.in_(repos), Comparison.target_oid.in_(target_oids),
            Comparison.rule_version == 1,
            exists(select(Branch.id).where(
                Branch.repository_id == Comparison.repository_id,
                Branch.head_oid == Comparison.source_oid, Branch.deleted.is_(False),
            )),
        )
    ).all() if target_oids else []
    cache = {(row.repository_id, row.source_oid, row.target_oid): row for row in comparisons}
    result = {}
    for rid, items in by_repo.items():
        baseline = defaults[rid]
        views = []
        for branch in items:
            comparison = cache.get((rid, branch.head_oid, baseline.head_oid)) if baseline else None
            state = (
                "identical" if baseline and branch.head_oid and branch.head_oid == baseline.head_oid
                else comparison.state if comparison else "missing"
            )
            views.append(SimpleNamespace(
                branch=branch, role=branch_role(repos[rid], branch, branch.config), state=state,
                comparison=comparison, baseline=baseline, is_default=branch is baseline,
                subject=None, subject_error=None,
            ))
        views.sort(key=lambda view: (
            -(view.branch.commit_time.timestamp() if view.branch.commit_time else 0), view.branch.name,
        ))
        result[rid] = views
    return result, defaults


@lru_cache(maxsize=256)
def _tip_subjects(path, oids):
    # Commit objects are immutable. An updated branch uses a different cache key.
    subjects = {}
    service = GitService(path)
    for start in range(0, len(oids), MAX_COMMITS):
        subjects.update(service.commit_subjects(list(oids[start:start + MAX_COMMITS])))
    return subjects


def repository_overview(settings, repo, views, matches):
    """Keep the default as context and include every matching live branch."""
    main = next((view for view in views if view.is_default), None)
    releases = [view for view in matches if view.role == "release" and not view.is_default]
    releases.sort(key=lambda view: not bool(view.branch.config and view.branch.config.active_release))
    features = [view for view in matches if view.role == "feature" and not view.is_default]
    all_releases = [view for view in views if view.role == "release" and not view.is_default]
    all_releases.sort(key=lambda view: not bool(view.branch.config and view.branch.config.active_release))
    all_features = [view for view in views if view.role == "feature" and not view.is_default]
    summary_release = next(iter(all_releases), None)
    summary_feature = next(iter(all_features), None)
    additional_main = [view for view in matches if view.role == "main" and not view.is_default]
    others = [view for view in matches if view.role == "other" and not view.is_default]
    groups = [SimpleNamespace(role=role, label=label, views=items) for role, label, items in (
        ("main", "其他主干", additional_main), ("release", "发布分支", releases),
        ("feature", "功能分支", features), ("other", "其他分支", others),
    ) if items]
    displayed = ([main] if main else []) + [view for group in groups for view in group.views]
    relations = {state: sum(view.state == state for view in displayed if not view.is_default)
                 for state in ("identical", "contained", "diverged", "unrelated", "missing")}
    overview = SimpleNamespace(
        main=main, releases=releases, features=features, others=others, groups=groups,
        release_count=len(all_releases), feature_count=len(all_features), branch_count=len(views),
        summary_release=summary_release, summary_feature=summary_feature,
        filtered_count=len(matches), displayed_count=len(displayed), relations=relations,
        is_empty=not views,
    )
    with_summaries = displayed + [view for view in (summary_release, summary_feature) if view is not None]
    oids = tuple(sorted({view.branch.head_oid for view in with_summaries if view.branch.head_oid}))
    if not oids:
        return overview
    subjects, error = {}, None
    try:
        with repository_lock(settings, repo.id, blocking=False) as acquired:
            if acquired:
                subjects = _tip_subjects(str(cache_path(settings, repo.id)), oids)
            else:
                error = "正在同步，提交摘要稍后可用"
    except (GitError, OSError, ValueError):
        error = "本地提交摘要暂不可读取"
    for view in with_summaries:
        view.subject = subjects.get(view.branch.head_oid)
        view.subject_error = error
    return overview
