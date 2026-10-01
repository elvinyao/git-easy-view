from contextlib import contextmanager
from datetime import timedelta
from html import escape
import re

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.demo import generate_history
from app.git_service import GitService
from app.models import Branch, BranchConfig, Comparison, ProjectRepository, Repository, utc_now
from app.project_overview import _tip_subjects, repository_overview, snapshot_views
from app.sync import cache_path
from test_pages import pages


@pytest.fixture
def overview_history(app, pages):
    path = cache_path(app.state.settings, pages["repo"])
    tips = generate_history(path, branches=9)
    now = utc_now()
    with Session(app.state.engine) as session:
        existing = {branch.name: branch for branch in session.scalars(select(Branch))}
        for index, (name, oid) in enumerate(tips.items()):
            branch = existing.get(name) or Branch(repository_id=pages["repo"], name=name)
            branch.head_oid = oid
            branch.commit_time = now - timedelta(minutes=index + 10)
            session.add(branch)
        newest = Branch(repository_id=pages["repo"], name="feature/z-latest", head_oid=tips["feature/login"], commit_time=now)
        release = Branch(repository_id=pages["repo"], name="release/2026.10", head_oid=tips["feature/login"], commit_time=now - timedelta(minutes=1))
        deleted = Branch(repository_id=pages["repo"], name="feature/deleted", head_oid=tips["main"], commit_time=now + timedelta(days=1), deleted=True)
        session.add_all([newest, release, deleted])
        session.flush()
        session.add(BranchConfig(branch_id=release.id, active_release=True))
        for oid in set(tips.values()):
            result = GitService(path).compare(oid, tips["main"])
            session.add(Comparison(repository_id=pages["repo"], source_oid=oid, target_oid=tips["main"], state=result.state, ahead=result.ahead, behind=result.behind))
        session.commit()
    _tip_subjects.cache_clear()
    return tips


def test_project_shows_default_all_releases_and_all_features(client, pages, overview_history):
    response = client.get(f"/projects/{pages['project']}")
    assert response.status_code == 200
    body = response.text
    main = re.search(r'<section class="overview-cell overview-main".*?</section>', body, re.S)[0]
    releases = re.search(r'<section class="overview-cell overview-releases".*?</section>', body, re.S)[0]
    features = re.search(r'<section class="overview-cell overview-features".*?</section>', body, re.S)[0]
    assert "Example main advance" in main and overview_history["main"][:7] in main
    assert "datetime=" in main and "UTC" in main
    assert releases.index("release/2026.10") < releases.index("release/2026.09")
    assert "Example root" in releases and "Example independent feature" in releases
    assert 'data-state="contained"' in releases and 'data-state="diverged"' in releases
    assert 'aria-label="活跃发布"' in releases
    assert features.index("feature/z-latest") < features.index("feature/login")
    assert features.count("data-summary-branch-id=") == 8
    assert 'data-relation-count="contained"' in body
    assert "显示 11 / 11 分支" in body
    assert "feature/deleted" not in body
    assert "↑1" in features and "↓1" in features
    assert f'commit={overview_history["main"]}' in main
    # Filtering surfaces the matching feature while preserving the default context.
    filtered = client.get(f"/projects/{pages['project']}?q=login&role=feature")
    filtered_features = re.search(r'<section class="overview-cell overview-features".*?</section>', filtered.text, re.S)[0]
    assert "feature/login" in filtered_features and "feature/z-latest" not in filtered_features
    assert "Example main advance" in filtered.text


@pytest.mark.parametrize("declared,remove_main,expected", [
    ("main", False, "main"), ("master", False, "master"),
    (None, False, "main"), (None, True, "master"), ("trunk", False, "trunk"),
])
def test_default_branch_respects_provider_then_main_master_fallback(app, pages, declared, remove_main, expected):
    with Session(app.state.engine) as session:
        repo = session.get(Repository, pages["repo"])
        repo.default_branch = declared
        session.get(Branch, pages["main"]).deleted = remove_main
        session.add_all([Branch(repository_id=repo.id, name=name, head_oid="c" * 40) for name in ("master", "trunk")])
        session.flush()
        views, defaults = snapshot_views(session, [repo])
        assert defaults[repo.id].name == expected
        assert sum(view.is_default for view in views[repo.id]) == 1


def test_stale_comparisons_are_not_used_for_current_tips(app, pages):
    with Session(app.state.engine) as session:
        repo = session.get(Repository, pages["repo"])
        session.get(Branch, pages["feature"]).head_oid = "c" * 40
        session.add(Comparison(repository_id=repo.id, source_oid="c" * 40, target_oid="a" * 40, rule_version=2, state="contained", ahead=0, behind=1))
        session.flush()
        views, _ = snapshot_views(session, [repo])
        feature = next(view for view in views[repo.id] if view.branch.id == pages["feature"])
        assert feature.state == "missing" and feature.comparison is None


def test_subjects_are_local_batched_cached_and_escaped(app, client, pages, overview_history, monkeypatch):
    calls = []
    subject = '<script>alert(1)</script> fix summary'
    def subjects(self, oids):
        calls.append(tuple(oids))
        return {oid: subject for oid in oids}
    monkeypatch.setattr(GitService, "commit_subjects", subjects)
    for _ in range(2):
        response = client.get(f"/projects/{pages['project']}")
        assert subject not in response.text
        assert escape(subject, quote=False) in response.text
        assert "overview-subject" in response.text
    assert len(calls) == 1 and set(calls[0]) == set(overview_history.values())
    with Session(app.state.engine) as session:
        session.get(Branch, pages["main"]).head_oid = "d" * 40
        session.commit()
    client.get(f"/projects/{pages['project']}")
    assert len(calls) == 2 and "d" * 40 in calls[1]


def test_busy_cache_keeps_oid_times_and_relations_readable(app, client, pages, overview_history, monkeypatch):
    @contextmanager
    def busy(*args, **kwargs):
        assert kwargs["blocking"] is False
        yield False
    monkeypatch.setattr("app.project_overview.repository_lock", busy)
    monkeypatch.setattr(GitService, "commit_subjects", lambda *_: pytest.fail("Busy cache must not be read"))
    response = client.get(f"/projects/{pages['project']}")
    assert response.status_code == 200
    assert "正在同步，提交摘要稍后可用" in response.text
    assert overview_history["main"][:7] in response.text and "datetime=" in response.text
    assert 'data-state="contained"' in response.text


def test_empty_importing_and_unknown_default_are_explicit(app, client, pages):
    with Session(app.state.engine) as session:
        repo = session.get(Repository, pages["repo"])
        for branch in session.scalars(select(Branch)):
            branch.deleted = True
        repo.git_synced_at = None
        session.commit()
    assert "正在导入" in client.get(f"/projects/{pages['project']}").text
    with Session(app.state.engine) as session:
        repo = session.get(Repository, pages["repo"])
        repo.git_synced_at = utc_now()
        session.commit()
    assert "空仓库：当前没有分支。" in client.get(f"/projects/{pages['project']}").text
    with Session(app.state.engine) as session:
        session.get(Branch, pages["feature"]).deleted = False
        session.commit()
    body = client.get(f"/projects/{pages['project']}").text
    assert "默认主干不可用" in body and "本地提交摘要暂不可读取" in body


def test_twelve_repository_branch_queries_are_batched(app, pages):
    with Session(app.state.engine) as session:
        for index in range(11):
            repo = Repository(connection_id=pages["connection"], external_id=f"overview-{index}", name=f"api-{index}", full_name=f"team/api-{index}", web_url="https://github.com/team/api", clone_url="https://github.com/team/api.git", default_branch="main")
            session.add(repo)
            session.flush()
            session.add(ProjectRepository(project_id=pages["project"], repository_id=repo.id))
            session.add_all([Branch(repository_id=repo.id, name=name, head_oid="a" * 40) for name in ("main", "release/one", "feature/one")])
        session.commit()
        repositories = session.scalars(select(Repository)).all()
        statements = []
        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(app.state.engine, "before_cursor_execute", record)
        try:
            views, defaults = snapshot_views(session, repositories)
        finally:
            event.remove(app.state.engine, "before_cursor_execute", record)
        assert len(views) == len(defaults) == 12
        assert len(statements) == 3  # Branches, role configurations, current comparisons.


def test_nonexistent_project_does_not_read_git(app, client, pages, monkeypatch):
    monkeypatch.setattr("app.routes.projects.snapshot_views", lambda *_: pytest.fail("Authorization must happen first"))
    assert client.get("/projects/999999").status_code == 404


def test_all_roles_and_more_than_fifty_releases_are_included(app, client, pages, overview_history):
    with Session(app.state.engine) as session:
        session.add_all([Branch(repository_id=pages["repo"], name=f"release/many-{index:03}", head_oid=overview_history["main"]) for index in range(55)])
        session.add_all([Branch(repository_id=pages["repo"], name=name, head_oid=overview_history["main"]) for name in ("master", "hotfix/urgent")])
        session.flush()
        master = session.scalar(select(Branch).where(Branch.repository_id == pages["repo"], Branch.name == "master"))
        session.add(BranchConfig(branch_id=master.id, role="main"))
        session.commit()
    client.cookies.clear()
    response = client.get(f"/projects/{pages['project']}")
    assert response.status_code == 200
    assert response.text.count("data-summary-branch-id=") == 68
    for name in ("release/many-000", "release/many-054", "master", "hotfix/urgent"):
        assert name in response.text
    assert "其他主干" in response.text and "其他分支" in response.text
    assert "显示 68 / 68 分支" in response.text


def test_tip_subjects_batch_more_than_five_hundred_distinct_commits(monkeypatch):
    calls = []
    oids = tuple(f"{index:040x}" for index in range(501))
    def subjects(self, requested):
        assert len(requested) <= 500
        calls.append(requested)
        return {oid: oid[:7] for oid in requested}
    monkeypatch.setattr(GitService, "commit_subjects", subjects)
    _tip_subjects.cache_clear()
    assert set(_tip_subjects("/unused-cache", oids)) == set(oids)
    assert [len(items) for items in calls] == [500, 1]


def test_summary_precedes_complete_branches_and_links_preserve_baseline(client, pages, overview_history):
    body = client.get(f"/projects/{pages['project']}?q=login").text
    assert 'data-repository-summary=' in body
    assert body.index('data-repository-summary=') < body.index('data-overview-repository=')
    assert '最新功能分支' in body and '仓库摘要' in body
    assert f'target_id={pages["main"]}' in body
    assert 'data-inspect-url=' in body
    assert 'id="project-inspector"' in body
    assert '匹配 1 / 全部 11 分支' in body
    assert '主干作为比较上下文保留' in body


def test_active_release_is_text_and_summary_does_not_duplicate_branch_rows(client, pages, overview_history):
    body = client.get(f"/projects/{pages['project']}").text
    assert '当前发布' in body
    assert '当前发布优先 · 最近提交排序' in body
    assert body.count('data-summary-branch-id=') == 11
    assert '有独有提交' not in body
    assert '未全部包含' in body


def test_repository_and_attention_filters_preserve_scope(app, client, pages):
    with Session(app.state.engine) as session:
        repo = session.get(Repository, pages['repo'])
        repo.api_synced_at = repo.git_synced_at = utc_now()
        repo.snapshot_generation = repo.api_generation = repo.requested_generation = 1
        session.commit()
    base = f"/projects/{pages['project']}"
    assert 'data-overview-repository=' not in client.get(base + '?repo_q=unmatched').text
    assert 'data-overview-repository=' in client.get(base + '?repo_q=TEAM/API').text
    assert 'data-overview-repository=' not in client.get(base + '?attention=1').text
    with Session(app.state.engine) as session:
        session.get(Repository, pages['repo']).sync_error = 'private diagnostic'
        session.commit()
    body = client.get(base + '?attention=1&repo_q=api').text
    assert 'data-overview-repository=' in body
    assert 'private diagnostic' not in body


def test_summary_keeps_latest_repository_tips_when_branch_list_is_filtered(client, pages, overview_history):
    body = client.get(f"/projects/{pages['project']}?q=login").text
    summary = re.search(r'<tr data-repository-summary=.*?</tr>', body, re.S)[0]
    assert 'release/2026.10' in summary and 'feature/z-latest' in summary
    assert '全部 2 条' in summary and '全部 8 条' in summary
    assert '摘要显示完整仓库，分支列表遵循筛选。' in body


def test_complete_group_link_finds_repository_after_clearing_branch_filter(app, client, pages):
    from html import unescape
    with Session(app.state.engine) as session:
        for index in range(50):
            repo = Repository(connection_id=pages['connection'], external_id=f'page-{index}', name=f'zzz-{index:03}',
                              full_name=f'zzz/team-{index:03}', web_url='https://github.com/team/api', clone_url='https://github.com/team/api.git')
            session.add(repo)
            session.flush()
            session.add(ProjectRepository(project_id=pages['project'], repository_id=repo.id))
            session.add(Branch(repository_id=repo.id, name='feature/needle' if index == 49 else 'feature/other', head_oid='f' * 40))
        session.commit()
        last_id = repo.id
    body = client.get(f"/projects/{pages['project']}?q=needle").text
    link = re.search(r'href="([^"]*#features-' + str(last_id) + r')"', body)[1]
    assert 'page=2' in link and 'q=needle' not in link
    target = client.get(f"/projects/{pages['project']}" + unescape(link)).text
    assert f'id="features-{last_id}"' in target
