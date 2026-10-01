"""Project drill-downs preserve their baseline and authorized return context."""

from html import unescape
from urllib.parse import parse_qs, urlsplit
import re

import pytest
from sqlalchemy.orm import Session

from app.git_service import FileChange, GitError, GitService, LogCommit
from app.jobs import repository_lock
from app.models import AuditEvent, Branch, BranchConfig, Comparison, ProjectRepository, PullRequest, Repository, SyncJob, utc_now
from test_pages import pages


@pytest.fixture
def snapshot(client, app, pages, monkeypatch):
    def history(self, heads, limit=100):
        return [LogCommit(heads[0], "<script>author</script>", utc_now(), "Full <img src=x> subject", ())]

    monkeypatch.setattr(GitService, "history", history)
    monkeypatch.setattr(GitService, "changed_files", lambda self, entry: [FileChange("<file>.txt", "A")])
    with Session(app.state.engine) as session:
        release = Branch(repository_id=pages["repo"], name="release/1", head_oid="d" * 40)
        session.add(release)
        session.flush()
        session.add_all([
            BranchConfig(branch_id=pages["feature"], manual_targets=[release.id]),
            Comparison(repository_id=pages["repo"], source_oid="b" * 40, target_oid=release.head_oid,
                       state="contained", ahead=0, behind=1),
        ])
        session.commit()
        return dict(pages, release=release.id)


@pytest.mark.parametrize("suffix", ["/branches", "/log", "/branches/{feature}", "/inspect"])
def test_drilldown_uses_project_baseline_without_rewriting_plan(client, snapshot, suffix):
    response = client.get(f"/repositories/{snapshot['repo']}" + suffix.format(**snapshot), params={
        "branch_id": snapshot["feature"], "target_id": snapshot["main"], "detail": "branch",
    })
    assert response.status_code == 200
    assert 'state-diverged' in response.text and "release/1" in response.text
    assert "本次选择的比较对象" in response.text
    assert 'state-contained' not in response.text or suffix.startswith("/branches/{")


@pytest.mark.parametrize("suffix", ["", "/branches", "/log", "/branches/{feature}", "/inspect"])
def test_project_return_and_baseline_survive_navigation(client, snapshot, suffix):
    return_to = f"/projects/{snapshot['project']}?repo_q=api&attention=1&q=login#repository-{snapshot['repo']}"
    response = client.get(f"/repositories/{snapshot['repo']}" + suffix.format(**snapshot), params={
        "return_to": return_to, "target_id": snapshot["main"], "branch_id": snapshot["feature"], "detail": "branch",
    })
    assert response.status_code == 200
    assert f'href="{return_to.replace("&", "&amp;")}"' in response.text
    assert "返回项目" in response.text
    if suffix in ("/branches", "/log", "/inspect"):
        links = [unescape(value) for value in re.findall(r'href="([^"]+)"', response.text)]
        context_links = [link for link in links if "detail=" in link or f"/branches/{snapshot['feature']}?" in link]
        assert context_links
        for link in context_links:
            params = parse_qs(urlsplit(link).query)
            assert params["target_id"] == [str(snapshot["main"])]
            assert params["return_to"] == [return_to]


@pytest.mark.parametrize("unsafe", ["https://evil.example/projects/1", "//evil.example", "/projects/{private}",
                                    "/projects/{project}/edit", "/projects/{project}?q=x\n", "/projects/{project}#%0d", "/projects/0"])
def test_unsafe_or_unrelated_project_return_is_discarded(client, snapshot, unsafe):
    response = client.get(f"/repositories/{snapshot['repo']}/branches", params={"return_to": unsafe.format(**snapshot)})
    assert response.status_code == 200
    assert 'class="project-return"' not in response.text
    assert "return_to=" not in response.text


@pytest.mark.parametrize("suffix", ["/branches", "/log", "/branches/{feature}", "/inspect"])
def test_foreign_and_deleted_baselines_are_rejected(client, app, snapshot, suffix):
    with Session(app.state.engine) as session:
        foreign = Repository(connection_id=snapshot["connection"], external_id="foreign", name="foreign",
                             full_name="foreign", web_url="https://example.com/foreign", clone_url="https://example.com/foreign.git")
        session.add(foreign)
        session.flush()
        target = Branch(repository_id=foreign.id, name="main", head_oid="c" * 40)
        session.add(target)
        session.get(Branch, snapshot["release"]).deleted = True
        session.commit()
        foreign_id = target.id
    path = f"/repositories/{snapshot['repo']}" + suffix.format(**snapshot)
    for target, expected in ((foreign_id, 404), (snapshot["release"], 400), ("invalid", 404)):
        assert client.get(path, params={"branch_id": snapshot["feature"], "target_id": target}).status_code == expected


@pytest.mark.parametrize("suffix", ["/branches", "/log", "/branches/{feature}", "/inspect"])
def test_same_oid_is_identical_without_comparison_row(client, app, snapshot, suffix):
    with Session(app.state.engine) as session:
        session.get(Branch, snapshot["feature"]).head_oid = "a" * 40
        session.commit()
    response = client.get(f"/repositories/{snapshot['repo']}" + suffix.format(**snapshot), params={
        "branch_id": snapshot["feature"], "target_id": snapshot["main"], "detail": "branch",
    })
    assert response.status_code == 200
    assert 'state-identical' in response.text
    assert re.search(r'<strong>0</strong>', response.text)
    with Session(app.state.engine) as session:
        assert session.query(Comparison).count() == 2


def test_inspect_is_scoped_read_only_and_escaped(client, app, snapshot, monkeypatch):
    client.cookies.clear()
    monkeypatch.setattr("app.routes.repositories.enqueue_comparison", lambda *a, **k: pytest.fail("Read must not enqueue"))
    path = f"/repositories/{snapshot['repo']}/inspect"
    response = client.get(path, params={"branch_id": snapshot["feature"], "target_id": snapshot["main"], "detail": "commit", "commit": "b" * 40})
    assert response.status_code == 200 and "<!DOCTYPE" not in response.text
    assert 'data-detail="commit"' in response.text and 'data-copy="' + "b" * 40 in response.text
    assert "Full &lt;img src=x&gt; subject" in response.text and "&lt;file&gt;.txt" in response.text
    assert "<script>author" not in response.text and "<img src=x>" not in response.text
    for params in ({}, {"branch_id": 0}, {"branch_id": 999999},
                   {"branch_id": snapshot["feature"], "commit": "--all"},
                   {"branch_id": snapshot["feature"], "commit": "e" * 40}):
        assert client.get(path, params=params).status_code == 404
    with Session(app.state.engine) as session:
        assert session.query(SyncJob).count() == session.query(AuditEvent).count() == 0
        session.query(ProjectRepository).delete()
        session.commit()
    assert client.get(path, params={"branch_id": snapshot["feature"]}).status_code == 404


def test_inspect_busy_and_unavailable_keep_cached_evidence(client, app, snapshot, monkeypatch):
    path = f"/repositories/{snapshot['repo']}/inspect"
    params = {"branch_id": snapshot["feature"], "target_id": snapshot["main"], "detail": "branch"}
    with repository_lock(app.state.settings, snapshot["repo"]):
        response = client.get(path, params=params)
    assert response.status_code == 200 and "仓库正在同步" in response.text
    assert "未全部包含" in response.text
    monkeypatch.setattr(GitService, "history", lambda *a, **k: (_ for _ in ()).throw(GitError("private filesystem details")))
    response = client.get(path, params=params)
    assert response.status_code == 200 and "本地提交历史暂不可读取" in response.text
    assert "private filesystem details" not in response.text


def test_inspect_file_failure_and_initial_snapshot_are_explicit(client, app, snapshot, monkeypatch):
    path = f"/repositories/{snapshot['repo']}/inspect"
    params = {"branch_id": snapshot["feature"], "detail": "commit"}
    monkeypatch.setattr(GitService, "changed_files", lambda *a, **k: (_ for _ in ()).throw(GitError("private files")))
    response = client.get(path, params=params)
    assert response.status_code == 200 and "变更文件暂不可读取" in response.text
    assert "此提交没有文件变更。" not in response.text and "private files" not in response.text
    with Session(app.state.engine) as session:
        session.get(Branch, snapshot["feature"]).head_oid = None
        repo = session.get(Repository, snapshot["repo"])
        repo.git_synced_at = None
        repo.snapshot_generation = 0
        session.commit()
    response = client.get(path, params=params)
    assert response.status_code == 200 and "首次同步" in response.text


def test_inspect_rejects_deleted_branches_and_unreachable_empty_history(client, app, snapshot, monkeypatch):
    path = f"/repositories/{snapshot['repo']}/inspect"
    monkeypatch.setattr(GitService, "history", lambda *a, **k: [])
    assert client.get(path, params={"branch_id": snapshot["feature"], "commit": "b" * 40}).status_code == 404
    with Session(app.state.engine) as session:
        session.get(Branch, snapshot["feature"]).deleted = True
        session.commit()
    assert client.get(path, params={"branch_id": snapshot["feature"]}).status_code == 404


@pytest.mark.parametrize("state,message", [("pending", "首次同步"), ("failed", "首次同步失败"), ("empty", "空仓库"), ("filtered", "没有符合筛选条件")])
def test_workspace_empty_lifecycle(client, app, pages, state, message):
    with Session(app.state.engine) as session:
        if state != "filtered":
            session.query(Branch).delete()
        repo = session.get(Repository, pages["repo"])
        if state in {"pending", "failed"}:
            repo.git_synced_at = None
            repo.snapshot_generation = 0
        repo.sync_error = "private failure" if state == "failed" else None
        session.commit()
    response = client.get(f"/repositories/{pages['repo']}/branches", params={"q": "nothing"} if state == "filtered" else {})
    assert response.status_code == 200 and message in response.text
    if state != "empty":
        assert "空仓库：当前没有分支。" not in response.text


def test_project_query_fields_are_bounded_and_preserved(client, pages):
    response = client.get(f"/repositories/{pages['repo']}/branches", params={"repo_q": "a" * 300, "attention": "1"})
    links = [parse_qs(urlsplit(unescape(value)).query) for value in re.findall(r'href="([^"]+)"', response.text)]
    assert any(params.get("repo_q") == ["a" * 200] and params.get("attention") == ["1"] for params in links)
    response = client.get(f"/repositories/{pages['repo']}/branches", params={"attention": "true"})
    assert "attention=true" not in response.text


def test_legacy_fragment_and_repository_filters_keep_project_context(client, snapshot):
    return_to = f"/projects/{snapshot['project']}?repo_q=api#repository-{snapshot['repo']}"
    params = {"return_to": return_to, "target_id": snapshot["main"], "repo_q": "api", "attention": "1"}
    response = client.get(f"/repositories/{snapshot['repo']}/branches", params={**params, "fragment": "1"})
    evidence = re.search(r'href="(/repositories/\d+/branches/' + str(snapshot["feature"]) + r'[^\"]*)"', response.text)
    assert evidence
    query = parse_qs(urlsplit(unescape(evidence.group(1))).query)
    assert query["return_to"] == [return_to] and query["target_id"] == [str(snapshot["main"])]
    response = client.get(f"/repositories/{snapshot['repo']}", params=params)
    for name, value in params.items():
        assert f'name="{name}" value="{value}"' in response.text
    reset = re.search(r'class="muted" href="([^"]+)"', response.text)
    assert reset and parse_qs(urlsplit(unescape(reset.group(1))).query)["return_to"] == [return_to]


@pytest.fixture
def platform_snapshot(app, snapshot):
    with Session(app.state.engine) as session:
        pr = PullRequest(
            connection_id=snapshot["connection"], repository_id=snapshot["repo"],
            external_id="navigation", title="Feature navigation", state="open",
            web_url="https://github.com/team/api/pull/1",
            source_repository_id=snapshot["repo"], source_branch="feature/login",
            target_repository_id=snapshot["repo"], target_branch="main",
        )
        session.add(pr)
        session.commit()
        return dict(snapshot, pr=pr.id)


@pytest.mark.parametrize("suffix", ["/branches", "/log", "/inspect", "/pull-requests"])
def test_platform_evidence_round_trip_preserves_project_and_baseline(client, platform_snapshot, suffix):
    snapshot = platform_snapshot
    client.cookies.clear()
    root = f"/repositories/{snapshot['repo']}"
    return_to = f"/projects/{snapshot['project']}?repo_q=api&attention=1&q=login#repository-{snapshot['repo']}"
    response = client.get(root + suffix, params={
        "branch_id": snapshot["feature"], "target_id": snapshot["main"],
        "return_to": return_to, "detail": "branch",
    })

    def links(response):
        assert response.status_code == 200
        return [unescape(value) for value in re.findall(r'href="([^"]+)"', response.text)]

    def assert_context(link):
        query = parse_qs(urlsplit(link).query)
        assert query.get("return_to") == [return_to]
        assert query.get("target_id") == [str(snapshot["main"])]

    assert return_to in links(response)
    detail_link = next(link for link in links(response) if urlsplit(link).path == root + f"/pull-requests/{snapshot['pr']}")
    assert_context(detail_link)
    detail = client.get(detail_link)
    assert return_to in links(detail)
    repository_link = next(link for link in links(detail) if urlsplit(link).path == root)
    assert_context(repository_link)
    assert return_to in links(client.get(repository_link))
    if suffix == "/pull-requests":
        assert_context(next(link for link in links(response) if urlsplit(link).path == root))


@pytest.mark.parametrize("suffix", ["/pull-requests", "/pull-requests/{pr}"])
@pytest.mark.parametrize("unsafe", ["https://evil.example/projects/1", "/projects/{private}", "/projects/{project}#%0d"])
def test_platform_evidence_discards_unsafe_project_return(client, platform_snapshot, suffix, unsafe):
    response = client.get(f"/repositories/{platform_snapshot['repo']}" + suffix.format(**platform_snapshot), params={
        "return_to": unsafe.format(**platform_snapshot),
    })
    assert response.status_code == 200
    assert 'class="project-return"' not in response.text
    assert "return_to=" not in response.text
