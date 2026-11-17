"""Conclusions over existing snapshots: what in a project needs action.

Everything here reads published rows only. No Git process or remote request
runs, so the home page can afford to call it for every project.
"""
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.i18n import Message
from app.models import utc_now
from app.releases import plan_state

STALE_DAYS = 90
GROUP_LIMIT = 50
WORK_ROLES = ("feature", "other")


def _days(now, moment):
    return max(0, (now - moment).days) if moment else None


def _branch_item(project_id, repo, view, detail, weight):
    params = dict(return_to=f"/projects/{project_id}")
    if view.baseline:
        params["target_id"] = view.baseline.id
    return SimpleNamespace(
        repository=repo.full_name, label=view.branch.name, detail=detail, weight=weight,
        href=f"/repositories/{repo.id}/branches/{view.branch.id}?" + urlencode(params),
    )


def project_attention(project_id, repos, statuses, views, plans, *, now=None):
    """Return ordered groups; a group is listed only when it has items."""
    now = now or utc_now()
    sync, unmerged_release, merged, stale, overdue = [], [], [], [], []
    for repo in repos:
        status = statuses.get(repo.id)
        if status and (status.error or status.disabled):
            sync.append(SimpleNamespace(
                repository=repo.full_name, label=repo.name, weight=0,
                detail=Message("连接已暂停，需要管理员重新检查凭据") if status.disabled else Message("最近一次同步失败"),
                href=f"/repositories/{repo.id}?" + urlencode(dict(return_to=f"/projects/{project_id}")),
            ))
        for view in views.get(repo.id, ()):
            if view.is_default or not view.baseline:
                continue
            ahead = view.comparison.ahead if view.comparison else None
            age = _days(now, view.branch.commit_time)
            base = view.baseline.name
            if view.role == "release" and ahead:
                unmerged_release.append(_branch_item(project_id, repo, view, Message(
                    "{count} 个提交尚未合回 {base}", count=ahead, base=base), -ahead))
            elif view.role in WORK_ROLES and view.state == "contained":
                merged.append(_branch_item(project_id, repo, view, Message(
                    "提交已全部包含在 {base} 中，可以删除", base=base), -(age or 0)))
            elif view.role in WORK_ROLES and view.state == "diverged" and age is not None and age >= STALE_DAYS:
                stale.append(_branch_item(project_id, repo, view, Message(
                    "{days} 天没有新提交，仍有 {count} 个提交未合入 {base}", days=age, count=ahead or 0, base=base), -age))
    for plan in plans:
        if plan_state(plan) != "active":
            continue
        today = now.astimezone(ZoneInfo(plan.timezone or "Asia/Tokyo")).date().isoformat()
        if plan.scheduled_date < today:
            done = sum(bool(item.deployed_at and item.merged_at) for item in plan.items)
            overdue.append(SimpleNamespace(
                repository=None, label=plan.title, weight=plan.scheduled_date,
                detail=Message("计划日期 {date}，已完成 {done} / {total} 个仓库",
                               date=plan.scheduled_date, done=done, total=len(plan.items)),
                href=f"/projects/{project_id}/releases/{plan.id}",
            ))
    groups = []
    for key, title, hint, items in (
        ("sync", "同步失败的仓库", "这些仓库的数据可能已经过时，下面的结论对它们不可靠。", sync),
        ("overdue", "已超期的发布计划", "已过计划日期，但仍有仓库未确认上线或合入。", overdue),
        ("release", "发布分支有提交未合回主干", "通常是发布分支上的修复。不合回主干，下次发布会丢失这些修改。", unmerged_release),
        ("merged", "已合入、可以清理的分支", "这些分支的提交已全部在主干中，删除不会丢失任何内容。", merged),
        ("stale", "长期没有进展的分支", "超过 {days} 天没有新提交，且仍有未合入的修改。请确认是继续还是放弃。", stale),
    ):
        if items:
            items.sort(key=lambda item: (item.weight, item.repository or "", item.label))
            groups.append(SimpleNamespace(
                key=key, title=title, hint=Message(hint, days=STALE_DAYS) if key == "stale" else Message(hint),
                total=len(items), items=items[:GROUP_LIMIT],
            ))
    return groups


def attention_total(groups):
    return sum(group.total for group in groups)
