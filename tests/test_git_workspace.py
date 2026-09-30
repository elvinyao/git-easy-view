import pytest

from app.git_service import FileChange, GitService, InvalidRef, LogCommit
from app.git_workspace import graph_rows
from app.jobs import repository_lock
from app.models import utc_now
from test_git_service import commit, git, history
from test_pages import pages


def test_log_real_forks_merges_and_file_names(history):
    path, base = history
    name = "notes\t日本語\n.txt"
    (path / name).write_text("initial\n")
    git(path, "add", "--", name)
    initial = commit(path, "initial file")
    git(path, "checkout", "-b", "topic")
    (path / "feature.txt").write_text("feature\n")
    git(path, "add", "feature.txt")
    feature = commit(path, "feature")
    git(path, "checkout", "main")
    (path / name).write_text("main change\n")
    git(path, "add", "--", name)
    main = commit(path, "main work")
    git(path, "merge", "--no-ff", "topic", "-m", "merge topic")
    merged = git(path, "rev-parse", "HEAD")
    service = GitService(path)
    entries = service.history([merged, feature, merged])
    by_oid = {entry.oid: entry for entry in entries}
    assert entries[0].oid == merged and len(entries) == 5
    assert by_oid[merged].parent_oids == (main, feature)
    assert by_oid[main].parent_oids == (initial,)
    assert by_oid[feature].parent_oids == (initial,)
    assert service.changed_files(by_oid[merged]) == [FileChange("feature.txt", "A")]
    assert service.changed_files(by_oid[main]) == [FileChange(name, "M")]
    assert service.changed_files(by_oid[base]) == []
    assert len(service.history([merged], limit=2)) == 2
    rows, width = graph_rows(entries)
    assert len(rows[0]["paths"]) == 2  # Both actual merge parents leave this node.
    assert rows[-1]["paths"] and 52 <= width <= 164


def test_log_root_file_changes_and_immutable_inputs(history):
    path, _ = history
    git(path, "checkout", "--orphan", "root")
    (path / "root.txt").write_text("root\n")
    git(path, "add", "root.txt")
    root = commit(path, "new root")
    service = GitService(path)
    entry = service.history([root])[0]
    assert entry.parent_oids == ()
    assert service.changed_files(entry) == [FileChange("root.txt", "A")]
    for value in ("--all", "HEAD", "main", "0" * 39):
        with pytest.raises(InvalidRef):
            service.history([value])
    for limit in (True, 0, 501):
        with pytest.raises(ValueError):
            service.history([root], limit=limit)


def test_log_normalizes_author_dates_to_displayed_utc(history):
    path, _ = history
    git(path, "commit", "--allow-empty", "--date", "2026-09-29T10:20:00+09:00", "-m", "Tokyo author date")
    entry = GitService(path).history([git(path, "rev-parse", "HEAD")])[0]
    assert entry.authored_at.isoformat() == "2026-09-29T01:20:00+00:00"


def test_direct_branch_url_is_a_page_and_lazy_request_is_a_fragment(client, pages):
    path = f"/repositories/{pages['repo']}/branches"
    page = client.get(path)
    assert page.status_code == 200 and "<!DOCTYPE html>" in page.text
    assert 'class="git-workspace"' in page.text and 'href="/static/app.css"' in page.text
    fragment = client.get(path, params={"fragment": "1"})
    assert fragment.status_code == 200
    assert "<!DOCTYPE html>" not in fragment.text and 'class="branch-table"' in fragment.text


def test_log_scope_escapes_content_and_rejects_unlisted_commits(client, pages, monkeypatch):
    captured = []

    def history(self, heads, limit=100):
        captured.append(heads)
        return [LogCommit(heads[0], "<script>author</script>", utc_now(), "<img src=x onerror=alert(1)>", ())]

    monkeypatch.setattr(GitService, "history", history)
    monkeypatch.setattr(GitService, "changed_files", lambda self, entry: [FileChange("<img src=x>.txt", "A")])
    path = f"/repositories/{pages['repo']}/log"
    response = client.get(path, params={"branch_id": pages["feature"]})
    assert response.status_code == 200 and captured[-1] == ["b" * 40]
    assert "&lt;img" in response.text and "&lt;script&gt;author" in response.text
    assert "<img src=x" not in response.text
    for params in ({"branch_id": 0}, {"branch_id": 999999}, {"commit": "--all"}, {"commit": "e" * 40}):
        assert client.get(path, params=params).status_code == 404


def test_sync_lock_keeps_relations_available_without_reading_git(app, client, pages, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Git must not be read under the writer lock")

    monkeypatch.setattr(GitService, "history", forbidden)
    with repository_lock(app.state.settings, pages["repo"]):
        response = client.get(f"/repositories/{pages['repo']}/log", params={"branch_id": pages["feature"], "detail": "branch", "lang": "en"})
    assert response.status_code == 200
    assert "The repository is synchronizing" in response.text
    assert "Not fully contained" in response.text and "feature/login" in response.text
