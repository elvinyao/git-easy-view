"""Pure, snapshot-based branch relation helpers.

Inferred sources are candidate ancestry evidence only. They never assert an
original parent or equivalent content. A manual source is a separately labeled
human claim. Planned targets come only from open PRs and manual settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol


RULE_VERSION = 1


@dataclass(frozen=True)
class BranchRef:
    repository_id: int
    branch_id: int
    name: str
    oid: str
    role: str | None = None
    external_repository_id: str | None = None
    connection_id: int | None = None


@dataclass(frozen=True)
class PullRequestRef:
    state: str
    source_repository_id: int | None
    source_branch: str | None
    target_repository_id: int | None
    target_branch: str | None
    pr_id: str
    source_external_repo_id: str | None = None
    target_external_repo_id: str | None = None
    connection_id: int | None = None


@dataclass(frozen=True)
class SourceCandidate:
    branch: BranchRef
    kind: str  # inferred or manual
    state: str | None
    merge_bases: tuple[str, ...]
    ahead: int | None  # unique to candidate when inferred
    behind: int | None  # unique to current branch when inferred


@dataclass(frozen=True)
class PlannedTarget:
    target: TargetRef
    kind: str  # manual or open_pr
    pr_id: str | None


@dataclass(frozen=True)
class TargetRef:
    repository_id: int | None
    external_repository_id: str | None
    branch_id: int | None
    name: str
    oid: str | None
    connection_id: int | None = None


class AncestryEvidence(Protocol):
    state: str
    merge_bases: tuple[str, ...]
    ahead: int
    behind: int


def cache_identity(repository_id: int, source_oid: str, target_oid: str, rule_version: int = RULE_VERSION) -> tuple[int, str, str, int]:
    """Comparison cache key; branch IDs and names do not participate."""
    return repository_id, source_oid, target_oid, rule_version


def infer_source_candidates(
    branch: BranchRef,
    configured_refs: Iterable[BranchRef],
    compare: Callable[[str, str], AncestryEvidence],
) -> list[SourceCandidate]:
    """Compare configured main/release OIDs to the branch's captured head OID."""
    refs = sorted(
        (ref for ref in configured_refs if ref.repository_id == branch.repository_id
         and ref.branch_id != branch.branch_id and ref.role in {"main", "release"}),
        key=lambda ref: (0 if ref.role == "main" else 1, ref.name, ref.branch_id),
    )
    candidates = []
    for ref in refs:
        evidence = compare(ref.oid, branch.oid)
        if evidence.state == "unrelated":
            continue
        candidates.append(SourceCandidate(
            ref, "inferred", evidence.state, tuple(evidence.merge_bases),
            evidence.ahead, evidence.behind,
        ))
    return candidates


def manual_source_claim(branch: BranchRef, source: BranchRef) -> SourceCandidate:
    if branch.repository_id != source.repository_id or branch.branch_id == source.branch_id:
        raise ValueError("Manual source must be a different branch in the same repository")
    return SourceCandidate(source, "manual", None, (), None, None)


def planned_targets(
    branch: BranchRef,
    pull_requests: Iterable[PullRequestRef],
    manual_targets: Iterable[BranchRef],
    available_targets: Iterable[BranchRef] = (),
) -> list[PlannedTarget]:
    """Open PRs and human settings only; historical PRs convey no plan."""
    def target_ref(item: BranchRef) -> TargetRef:
        return TargetRef(item.repository_id, item.external_repository_id, item.branch_id,
                         item.name, item.oid, item.connection_id)

    manual = sorted(
        (target for target in manual_targets if not
         (target.repository_id == branch.repository_id and target.branch_id == branch.branch_id)),
        key=lambda target: (target.repository_id, target.name, target.branch_id),
    )
    result = [PlannedTarget(target_ref(target), "manual", None) for target in manual]
    known = [*manual, *available_targets]
    for pr in sorted(pull_requests, key=lambda item: item.pr_id):
        if pr.state not in {"open", "opened"}:
            continue
        source_matches = (pr.source_repository_id == branch.repository_id
                          if pr.source_repository_id is not None else
                          bool(pr.connection_id is not None and pr.connection_id == branch.connection_id
                               and pr.source_external_repo_id and branch.external_repository_id
                               and pr.source_external_repo_id == branch.external_repository_id))
        if not source_matches or pr.source_branch != branch.name:
            continue
        if not pr.target_branch or (pr.target_repository_id is None and not pr.target_external_repo_id):
            continue
        target = next((item for item in known if item.name == pr.target_branch
                       and (item.repository_id == pr.target_repository_id
                            if pr.target_repository_id is not None else
                            item.connection_id is not None and item.connection_id == pr.connection_id
                            and item.external_repository_id == pr.target_external_repo_id)), None)
        reference = target_ref(target) if target else TargetRef(
            pr.target_repository_id, pr.target_external_repo_id, None, pr.target_branch, None,
            pr.connection_id)
        result.append(PlannedTarget(reference, "open_pr", pr.pr_id))
    return result
