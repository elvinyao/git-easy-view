from app.relations import (
    BranchRef,
    PullRequestRef,
    cache_identity,
    infer_source_candidates,
    manual_source_claim,
    planned_targets,
)


def branch(repo, name, oid, role=None):
    return BranchRef(repository_id=repo, branch_id=hash((repo, name)), name=name, oid=oid, role=role)


def test_cache_identity_uses_repository_and_oids():
    assert cache_identity(1, "a" * 40, "b" * 40, 2) == (1, "a" * 40, "b" * 40, 2)
    assert cache_identity(1, "a" * 40, "b" * 40, 2) != cache_identity(2, "a" * 40, "b" * 40, 2)


def test_candidates_are_inferred_from_configured_refs_only():
    topic = branch(1, "topic", "t" * 40)
    main = branch(1, "main", "m" * 40, "main")
    release = branch(1, "release", "r" * 40, "release")
    foreign = branch(2, "main", "f" * 40, "main")

    class Evidence:
        state = "diverged"
        merge_bases = ("b" * 40,)
        ahead = 2
        behind = 1

    seen = []

    def compare(source_oid, target_oid):
        seen.append((source_oid, target_oid))
        return Evidence()

    candidates = infer_source_candidates(topic, [topic, foreign, release, main], compare)
    assert [c.branch.name for c in candidates] == ["main", "release"]
    assert all(c.kind == "inferred" and c.merge_bases == ("b" * 40,) for c in candidates)
    assert seen == [(main.oid, topic.oid), (release.oid, topic.oid)]


def test_manual_source_is_labeled_separately_from_inference():
    topic, main = branch(1, "topic", "t" * 40), branch(1, "main", "m" * 40)
    claim = manual_source_claim(topic, main)
    assert claim.kind == "manual" and claim.branch == main
    assert claim.merge_bases == ()


def test_planned_targets_ignore_closed_prs_and_fork_name_collisions():
    topic = branch(1, "topic", "t" * 40)
    main = branch(1, "main", "m" * 40)
    prs = [
        PullRequestRef("open", 1, "topic", 1, "main", "1"),
        PullRequestRef("merged", 1, "topic", 1, "main", "2"),
        PullRequestRef("closed", 1, "topic", 1, "main", "3"),
        PullRequestRef("open", 2, "topic", 1, "main", "4"),
    ]
    targets = planned_targets(topic, prs, [main])
    assert [(t.kind, t.target.name, t.pr_id) for t in targets] == [
        ("manual", "main", None),
        ("open_pr", "main", "1"),
    ]


def test_open_pr_target_needs_no_manual_target_and_keeps_fork_identity():
    topic = branch(1, "topic", "t" * 40)
    prs = [
        PullRequestRef("open", 1, "topic", 1, "main", "1"),
        PullRequestRef("open", 1, "topic", 2, "main", "2"),
        PullRequestRef("open", 2, "topic", 1, "main", "wrong-source"),
    ]
    plans = planned_targets(topic, prs, [])
    assert [(p.target.repository_id, p.target.name, p.pr_id) for p in plans] == [
        (1, "main", "1"), (2, "main", "2"),
    ]
    assert all(p.target.oid is None for p in plans)


def test_external_fork_identity_is_required_when_internal_id_is_missing():
    topic = BranchRef(1, 1, "topic", "t" * 40, external_repository_id="owned", connection_id=10)
    prs = [
        PullRequestRef("open", None, "topic", None, "main", "right",
                       source_external_repo_id="owned", target_external_repo_id="fork", connection_id=10),
        PullRequestRef("open", None, "topic", None, "main", "wrong",
                       source_external_repo_id="other", target_external_repo_id="fork", connection_id=10),
        PullRequestRef("open", None, "topic", None, "main", "wrong-connection",
                       source_external_repo_id="owned", target_external_repo_id="fork", connection_id=11),
    ]
    plans = planned_targets(topic, prs, [])
    assert len(plans) == 1
    assert plans[0].target.external_repository_id == "fork"
    assert plans[0].target.connection_id == 10
