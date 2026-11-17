"""What the default branch contains that production does not.

Both ends are immutable OIDs (the recorded production commit and the snapshot
tip of the default branch), so results are cached for the process lifetime.
"""
from functools import lru_cache
from types import SimpleNamespace

from sqlalchemy import select

from app.git_service import MAX_COMMITS, GitError, GitService
from app.jobs import repository_lock
from app.models import PullRequest
from app.sync import cache_path

SHOWN_COMMITS = 50


@lru_cache(maxsize=512)
def _range(path, main_oid, production_oid):
    service = GitService(path)
    comparison = service.compare(main_oid, production_oid)
    commits = service.commits(main_oid, production_oid, MAX_COMMITS) if comparison.ahead else []
    return comparison.ahead, comparison.behind, tuple(commits)


def repository_unreleased(session, settings, repo, baseline, production):
    """One row of the project view; never raises for an unreadable cache."""
    row = SimpleNamespace(repo=repo, baseline=baseline, production=production, ahead=None, behind=None,
                          commits=[], pull_requests=[], error=None, truncated=False)
    if production is None or baseline is None or not baseline.head_oid:
        return row
    if production.commit_oid == baseline.head_oid:
        row.ahead = row.behind = 0
        return row
    try:
        with repository_lock(settings, repo.id, blocking=False) as acquired:
            if not acquired:
                row.error = "仓库正在同步，请稍后刷新。"
                return row
            row.ahead, row.behind, commits = _range(
                str(cache_path(settings, repo.id)), baseline.head_oid, production.commit_oid)
    except (GitError, OSError, ValueError):
        row.error = "本地缓存中找不到本番提交或主干提交，请等待下一次同步。"
        return row
    row.commits = list(commits[:SHOWN_COMMITS])
    row.truncated = row.ahead > len(row.commits)
    oids = {commit.oid for commit in commits}
    if oids:
        merged = session.scalars(select(PullRequest).where(
            PullRequest.repository_id == repo.id, PullRequest.state == "merged",
            PullRequest.merge_oid.is_not(None)).order_by(PullRequest.merged_at.desc())).all()
        row.pull_requests = [pr for pr in merged if pr.merge_oid in oids]
    return row
