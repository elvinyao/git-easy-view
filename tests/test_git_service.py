import os
import subprocess

import pytest

from app.git_service import (
    GitService,
    InvalidRef,
    MissingRef,
    clone_bare,
    fetch_bare,
)


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
    ).stdout.strip()


def commit(path, label):
    git(path, "commit", "--allow-empty", "-m", label)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path):
    path = tmp_path / "history"
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    git(path, "config", "user.name", "Fixture")
    git(path, "config", "user.email", "fixture@example.test")
    base = commit(path, "base")
    return path, base


def test_branches_list_live_heads_and_recreated_name(history):
    path, base = history
    git(path, "branch", "topic")
    service = GitService(path)
    assert {branch.name: branch.oid for branch in service.branches()} == {"main": base, "topic": base}
    git(path, "branch", "-D", "topic")
    git(path, "checkout", "-b", "topic")
    new_oid = commit(path, "replacement")
    assert {branch.name: branch.oid for branch in service.branches()}["topic"] == new_oid


def test_branches_preserve_unicode_line_separator_in_name(history):
    path, base = history
    name = "feature/first\u2028second"
    git(path, "branch", name)
    assert {branch.name: branch.oid for branch in GitService(path).branches()}[name] == base


def test_compare_direction_and_states_on_real_history(history):
    path, base = history
    git(path, "checkout", "-b", "feature")
    feature = commit(path, "feature")
    git(path, "checkout", "main")
    main = commit(path, "main work")
    service = GitService(path)
    result = service.compare("feature", "main")
    assert (result.source_oid, result.target_oid) == (feature, main)
    assert (result.ahead, result.behind, result.state, result.merge_bases) == (1, 1, "diverged", (base,))
    assert (service.compare("main", base).ahead, service.compare("main", base).behind) == (1, 0)
    assert service.compare(base, "main").state == "contained"
    assert service.compare("main", "main").state == "identical"
    assert service.compare("main", base).state == "diverged"


def test_merge_and_cherry_pick_are_ancestry_not_content(history):
    path, base = history
    git(path, "checkout", "-b", "feature")
    (path / "original.txt").write_text("original\n")
    git(path, "add", "original.txt")
    original = commit(path, "original")
    git(path, "checkout", "main")
    git(path, "merge", "--no-ff", "feature", "-m", "merge feature")
    assert GitService(path).compare(original, "main").state == "contained"
    git(path, "checkout", "-b", "other", base)
    git(path, "cherry-pick", "-x", original)
    picked = git(path, "rev-parse", "HEAD")
    result = GitService(path).compare(original, picked)
    assert result.state == "diverged"
    assert result.ahead == 1 and result.behind == 1
    git(path, "checkout", "main")
    git(path, "revert", "--no-edit", "-m", "1", "HEAD")
    assert GitService(path).compare(original, "main").state == "contained"


def test_squash_and_revert_do_not_contain_original_commit(history):
    path, _ = history
    git(path, "checkout", "-b", "feature")
    (path / "change.txt").write_text("value\n")
    git(path, "add", "change.txt")
    original = commit(path, "feature change")
    git(path, "checkout", "main")
    git(path, "merge", "--squash", "feature")
    squashed = commit(path, "squash feature")
    assert GitService(path).compare(original, squashed).state == "diverged"
    git(path, "revert", "--no-edit", squashed)
    assert GitService(path).compare(original, "main").state == "diverged"


def test_unrelated_and_missing_refs_are_explicit(history):
    path, _ = history
    git(path, "checkout", "--orphan", "other")
    other = commit(path, "orphan")
    result = GitService(path).compare("main", other)
    assert result.state == "unrelated" and result.merge_bases == ()
    with pytest.raises(MissingRef):
        GitService(path).compare("deleted", "main")
    with pytest.raises(InvalidRef):
        GitService(path).compare("--help", "main")


def test_criss_cross_merge_returns_every_merge_base(history):
    path, base = history
    git(path, "checkout", "-b", "left")
    left = commit(path, "left")
    git(path, "checkout", "-b", "right", base)
    right = commit(path, "right")
    git(path, "checkout", "left")
    git(path, "merge", "--no-ff", "right", "-m", "merge right into left")
    git(path, "checkout", "right")
    git(path, "merge", "--no-ff", left, "-m", "merge left into right")
    comparison = GitService(path).compare("left", "right")
    assert comparison.state == "diverged"
    assert set(comparison.merge_bases) == {left, right}


def test_empty_repository_and_deleted_branch(history, tmp_path):
    empty = tmp_path / "empty"
    subprocess.run(["git", "init", "-b", "main", str(empty)], check=True, capture_output=True)
    assert GitService(empty).branches() == []
    with pytest.raises(MissingRef):
        GitService(empty).compare("main", "main")
    path, _ = history
    git(path, "branch", "deleted")
    git(path, "branch", "-D", "deleted")
    with pytest.raises(MissingRef):
        GitService(path).compare("deleted", "main")


def test_commits_are_source_unique_and_limited(history):
    path, base = history
    first = commit(path, "first")
    second = commit(path, "second")
    service = GitService(path)
    entries = service.commits(second, base, limit=1)
    assert [item.oid for item in entries] == [second]
    assert service.commits(base, second) == []
    assert [item.oid for item in service.commits(second, base)] == [second, first]


def test_commits_preserve_unicode_line_separator_in_subject(history):
    path, base = history
    subject = "fix UI\u2028layout"
    oid = commit(path, subject)
    assert GitService(path).commits(oid, base)[0].subject == subject


def test_commits_does_not_execute_configured_signature_verifier(history):
    path, base = history
    tree = git(path, "rev-parse", "HEAD^{tree}")
    raw_commit = (
        f"tree {tree}\nparent {base}\n"
        "author Fixture <fixture@example.test> 1700000000 +0000\n"
        "committer Fixture <fixture@example.test> 1700000000 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n"
        " fake-signature\n"
        " -----END PGP SIGNATURE-----\n"
        "\nsigned commit\n"
    )
    signed_oid = subprocess.run(
        ["git", "-C", str(path), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=raw_commit, capture_output=True, text=True, check=True,
    ).stdout.strip()
    git(path, "update-ref", "refs/heads/signed", signed_oid)
    marker = path / "verifier-ran"
    verifier = path / "verifier.sh"
    verifier.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
    verifier.chmod(0o755)
    git(path, "config", "log.showSignature", "true")
    git(path, "config", "gpg.program", str(verifier))

    assert [item.oid for item in GitService(path).commits(signed_oid, base)] == [signed_oid]
    assert not marker.exists()


def test_bare_clone_and_force_prune_fetch_all_branches(history, tmp_path):
    source, base = history
    git(source, "branch", "removed")
    git(source, "checkout", "-b", "moving")
    old_oid = commit(source, "old moving")
    bare = tmp_path / "cache.git"
    clone_bare(str(source), bare, allow_file_transport=True)
    assert {b.name: b.oid for b in GitService(bare).branches()}["moving"] == old_oid
    git(source, "checkout", "main")
    git(source, "branch", "-D", "moving")
    git(source, "branch", "-D", "removed")
    git(source, "checkout", "-b", "moving", base)
    new_oid = commit(source, "new moving")
    fetch_bare(bare, allow_file_transport=True)
    branches = {b.name: b.oid for b in GitService(bare).branches()}
    assert branches == {"main": base, "moving": new_oid}


def test_unsafe_clone_transport_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        clone_bare("file:///tmp/source.git", tmp_path / "cache.git")
    with pytest.raises(ValueError):
        clone_bare("user:secret@example.test/repo.git", tmp_path / "cache.git", allow_file_transport=True)
    with pytest.raises(ValueError):
        clone_bare("file://remotehost/tmp/repo.git", tmp_path / "cache.git", allow_file_transport=True)
    with pytest.raises(ValueError):
        clone_bare("https://example.test/repo.git?token=secret", tmp_path / "cache.git")
