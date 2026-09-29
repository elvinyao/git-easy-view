"""Bounded Git history inspection and bare-cache transport helpers.

Comparisons use immutable commit OIDs after explicit ref resolution. ``ahead``
is unique to the source. A source ahead of its target, even with ``behind=0``,
is ``diverged``; ``contained`` means source is reachable from target. These
states say nothing about equivalent file content.
"""

from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


MAX_OUTPUT = 4 * 1024 * 1024
MAX_BRANCHES = 10000
MAX_COMMITS = 500
OID = re.compile(r"^[0-9a-f]{40,64}$")


class GitError(RuntimeError):
    """A Git command failed; messages never contain transport credentials."""


class InvalidRef(GitError, ValueError):
    """A caller supplied an unsafe revision token."""


class MissingRef(GitError, ValueError):
    """A ref is absent, deleted, unborn, or not a commit."""


class GitTimeout(GitError):
    """A Git subprocess exceeded its deadline."""


class GitOutputLimit(GitError):
    """A Git subprocess produced more than the permitted output."""


@dataclass(frozen=True)
class BranchHead:
    name: str
    oid: str
    commit_time: datetime


@dataclass(frozen=True)
class ComparisonResult:
    source_oid: str
    target_oid: str
    ahead: int
    behind: int
    merge_bases: tuple[str, ...]
    state: str  # identical, contained, diverged, unrelated


@dataclass(frozen=True)
class CommitInfo:
    oid: str
    author_name: str
    authored_at: datetime
    subject: str


def _environment(extra: dict[str, str] | None, *, allow_file_transport: bool) -> dict[str, str]:
    # Avoid ambient Git configuration, replacement objects and prompts.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/nonexistent",
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_ALLOW_PROTOCOL": "https:file" if allow_file_transport else "https",
    }
    if extra:
        for key, value in extra.items():
            if key.startswith("GIT_") and key not in {"GIT_ASKPASS", "GIT_SSL_CAINFO"}:
                raise ValueError(f"Git environment override is not allowed: {key}")
            if key in {"HOME", "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH"}:
                raise ValueError(f"Environment override is not allowed: {key}")
            env[key] = value
    return env


def _run(
    args: list[str], *, env: dict[str, str] | None = None,
    allow_file_transport: bool = False, timeout: float = 30,
    allowed_codes: tuple[int, ...] = (0,),
) -> tuple[int, str]:
    command = [
        "git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false", "-c", "credential.helper=",
        "-c", "protocol.ext.allow=never", "-c", "http.followRedirects=false", *args,
    ]
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_environment(env, allow_file_transport=allow_file_transport),
            start_new_session=True,
        )
    except OSError as exc:
        raise GitError("Unable to start Git") from exc
    output = bytearray()
    total = 0
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None and process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GitTimeout("Git operation timed out")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > MAX_OUTPUT:
                        raise GitOutputLimit("Git output limit exceeded")
                    # Remote URLs may contain secrets; never surface stderr.
                    if key.fileobj is process.stdout:
                        output.extend(chunk)
            try:
                code = process.wait(timeout=max(deadline - time.monotonic(), 0.001))
            except subprocess.TimeoutExpired as exc:
                raise GitTimeout("Git operation timed out") from exc
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    if code not in allowed_codes:
        raise GitError(f"Git operation failed (exit {code})")
    return code, output.decode("utf-8", errors="replace")


def _safe_ref(ref: str) -> str:
    if (not isinstance(ref, str) or not ref or ref.startswith("-")
            or any(ord(c) < 32 or ord(c) == 127 for c in ref)):
        raise InvalidRef("Invalid revision token")
    return ref


class GitService:
    """Inspect one Git repository, including a bare cache."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _git(self, *args: str, allowed_codes: tuple[int, ...] = (0,)) -> tuple[int, str]:
        return _run(["-C", str(self.path), *args], allowed_codes=allowed_codes)

    def resolve(self, ref: str) -> str:
        ref = _safe_ref(ref)
        code, output = self._git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", allowed_codes=(0, 1, 128))
        oid = output.strip()
        if code != 0 or not OID.fullmatch(oid):
            raise MissingRef("Revision is missing or does not name a commit")
        return oid

    def branches(self) -> list[BranchHead]:
        _, output = self._git(
            "for-each-ref", f"--count={MAX_BRANCHES + 1}",
            "--format=%(refname:strip=2)%00%(objectname)%00%(committerdate:iso8601-strict)",
            "refs/heads",
        )
        # Git separates records with literal LF. Python splitlines() also
        # splits legal Unicode branch characters such as U+2028.
        lines = [line for line in output.split("\n") if line]
        if len(lines) > MAX_BRANCHES:
            raise GitOutputLimit("Branch count limit exceeded")
        branches = []
        for line in lines:
            name, oid, timestamp = line.split("\x00", 2)
            if not OID.fullmatch(oid):
                raise GitError("Branch does not point to a commit")
            branches.append(BranchHead(name, oid, datetime.fromisoformat(timestamp)))
        return branches

    def compare(self, source_ref: str, target_ref: str) -> ComparisonResult:
        source_oid = self.resolve(source_ref)
        target_oid = self.resolve(target_ref)
        _, counts = self._git("rev-list", "--left-right", "--count", f"{source_oid}...{target_oid}")
        ahead, behind = map(int, counts.split())
        code, bases = self._git("merge-base", "--all", source_oid, target_oid, allowed_codes=(0, 1))
        merge_bases = tuple(sorted(bases.splitlines())) if code == 0 else ()
        if source_oid == target_oid:
            state = "identical"
        elif not merge_bases:
            state = "unrelated"
        elif ahead == 0:
            state = "contained"
        else:
            state = "diverged"
        return ComparisonResult(source_oid, target_oid, ahead, behind, merge_bases, state)

    def commits(self, source_oid: str, target_oid: str, limit: int = 50) -> list[CommitInfo]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_COMMITS:
            raise ValueError(f"limit must be between 1 and {MAX_COMMITS}")
        source = self.resolve(source_oid)
        target = self.resolve(target_oid)
        _, output = self._git(
            "log", "--no-show-signature", "--topo-order", f"--max-count={limit}",
            "--format=%H%x00%an%x00%aI%x00%s", source, "--not", target,
        )
        commits = []
        for line in output.split("\n"):
            if not line:
                continue
            oid, author, timestamp, subject = line.split("\x00", 3)
            commits.append(CommitInfo(oid, author, datetime.fromisoformat(timestamp), subject))
        return commits


def _validate_transport(source: str, *, allow_file_transport: bool) -> None:
    source = str(source)
    parts = urlsplit(source)
    if (parts.scheme == "https" and parts.hostname and not parts.username
            and not parts.password and not parts.query and not parts.fragment):
        return
    if (allow_file_transport and parts.scheme == "file" and
            parts.hostname in {None, "", "localhost"} and not parts.username
            and not parts.password and not parts.query and not parts.fragment):
        return
    if allow_file_transport and not parts.scheme and ":" not in source and not source.startswith("-"):
        return
    raise ValueError("Clone source must be an HTTPS URL without embedded credentials")


def clone_bare(
    source: str, destination: str | Path, *, env: dict[str, str] | None = None,
    allow_file_transport: bool = False,
) -> None:
    """Clone all branch heads into a bare cache. File transport is opt-in."""
    _validate_transport(source, allow_file_transport=allow_file_transport)
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    _run(["clone", "--bare", "--", str(source), str(destination)],
         env=env, allow_file_transport=allow_file_transport, timeout=120)
    _run(["-C", str(destination), "config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*"],
         allow_file_transport=allow_file_transport)


def fetch_bare(
    path: str | Path, *, remote: str = "origin", env: dict[str, str] | None = None,
    allow_file_transport: bool = False,
) -> None:
    """Force refresh and prune branch heads in an existing bare cache."""
    if remote != "origin":
        raise ValueError("Only the configured origin remote is supported")
    path = Path(path)
    _, url = _run(["-C", str(path), "remote", "get-url", remote])
    _validate_transport(url.strip(), allow_file_transport=allow_file_transport)
    _run(["-C", str(path), "fetch", "--prune", "--force", "--no-tags", remote,
          "+refs/heads/*:refs/heads/*"], env=env,
         allow_file_transport=allow_file_transport, timeout=120)
