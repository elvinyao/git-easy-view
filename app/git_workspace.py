"""Local Git log and graph presentation for an already-authorized repository."""

from fastapi import HTTPException

from app.git_service import GitError, GitService, OID
from app.jobs import repository_lock
from app.sync import cache_path
from app.web import number, query_url


GRAPH_COLORS = ("#72a7ff", "#c091ef", "#68c4a3", "#e4b868", "#ef8e96", "#6fc5d2")


def graph_rows(commits):
    """Keep parent lanes connected across rows, including forks and merges."""
    lanes, colors, rows = [], {}, []
    maximum = 1
    for commit in commits:
        continued = commit.oid in lanes
        if not continued:
            lanes.append(commit.oid)
        before = list(lanes)
        lane = before.index(commit.oid)
        colors.setdefault(commit.oid, GRAPH_COLORS[len(colors) % len(GRAPH_COLORS)])
        color = colors[commit.oid]
        lanes.pop(lane)
        for offset, parent in enumerate(commit.parent_oids):
            if parent not in lanes:
                lanes.insert(min(lane + offset, len(lanes)), parent)
            colors.setdefault(parent, color if offset == 0 else GRAPH_COLORS[len(colors) % len(GRAPH_COLORS)])
        paths = []
        for index, oid in enumerate(before):
            if oid != commit.oid:
                paths.append((index, 0, lanes.index(oid), 36, colors[oid]))
        if continued:
            paths.append((lane, 0, lane, 18, color))
        for parent in commit.parent_oids:
            paths.append((lane, 18, lanes.index(parent), 36, colors[parent]))
        maximum = max(maximum, len(before), len(lanes))
        rows.append(dict(commit=commit, lane=lane, color=color, paths=paths))
    width = max(52, min(164, maximum * 16 + 20))
    gap = (width - 24) / max(maximum - 1, 1)
    for row in rows:
        row["x"] = round(12 + row.pop("lane") * gap, 2)
        row["paths"] = [
            dict(d=f"M {12 + start * gap:.2f} {top} C {12 + start * gap:.2f} {(top + bottom) / 2} {12 + end * gap:.2f} {(top + bottom) / 2} {12 + end * gap:.2f} {bottom}", color=color)
            for start, top, end, bottom, color in row["paths"]
        ]
    return rows, width


def workspace_context(request, repo, views, *, total_branches=None):
    """Only expose commits reachable from the displayed, authorized snapshot."""
    selected_id = number(request.query_params.get("branch_id"), 0, 2**31 - 1)
    selected_view = next((view for view in views if view.branch.id == selected_id), None)
    if "branch_id" in request.query_params and not selected_view:
        raise HTTPException(404)
    if not selected_view and len(views) == 1:
        selected_view = views[0]
        selected_id = selected_view.branch.id
    heads = [selected_view.branch.head_oid] if selected_view else [view.branch.head_oid for view in views]
    heads = [oid for oid in heads if oid]
    requested_commit = request.query_params.get("commit", "")
    if requested_commit and not OID.fullmatch(requested_commit):
        raise HTTPException(404)
    commits, changes, selected_commit, history_error, files_error = [], [], None, None, None
    if heads:
        with repository_lock(request.app.state.settings, repo.id, blocking=False) as acquired:
            if not acquired:
                history_error = "仓库正在同步，提交历史暂不可读取。请稍后刷新。"
            else:
                git = GitService(cache_path(request.app.state.settings, repo.id))
                try:
                    commits = git.history(heads, limit=100)
                except (GitError, OSError, ValueError):
                    history_error = "本地提交历史暂不可读取，已缓存的分支关系仍可查看。"
                if commits:
                    selected_commit = next((commit for commit in commits if commit.oid == requested_commit), None) if requested_commit else commits[0]
                    if not selected_commit:
                        raise HTTPException(404)
                    try:
                        changes = git.changed_files(selected_commit)
                    except (GitError, OSError, ValueError):
                        files_error = "变更文件暂不可读取，请稍后重试。"
                elif requested_commit and not history_error:
                    raise HTTPException(404)
    elif requested_commit:
        raise HTTPException(404)
    rows, graph_width = graph_rows(commits)
    references = {}
    for view in views:
        references.setdefault(view.branch.head_oid, []).append(view)
    for row in rows:
        row["references"] = references.get(row["commit"].oid, [])
    detail_tab = request.query_params.get("detail", "commit")
    if detail_tab not in {"commit", "branch"}:
        detail_tab = "commit"
    empty_state, empty_message = None, None
    if not heads and repo.git_synced_at is None:
        empty_state, empty_message = (
            ("failed", "首次同步失败，尚无可用快照。") if repo.sync_error
            else ("pending", "正在等待首次同步，分支和提交将在同步完成后显示。")
        )
    elif not views:
        empty_state, empty_message = (
            ("filtered", "没有符合筛选条件的分支。") if total_branches
            else ("empty", "空仓库：当前没有分支。")
        )

    def workspace_url(**changes):
        values = dict(branch_id=selected_id or None, commit=requested_commit or None, detail=detail_tab)
        values.update(changes)
        return query_url(request, **values)

    return dict(
        selected_view=selected_view, selected_commit=selected_commit, selected_branch_id=selected_id,
        log_rows=rows, graph_width=graph_width, file_changes=changes[:200], file_total=len(changes),
        history_error=history_error, files_error=files_error, detail_tab=detail_tab,
        workspace_url=workspace_url, empty_state=empty_state, empty_message=empty_message,
    )
