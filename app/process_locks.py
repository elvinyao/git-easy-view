"""Per-context lock descriptors explicitly inherited by native Git only."""
from contextlib import contextmanager
from contextvars import ContextVar


_git_locks: ContextVar[tuple[int, ...]] = ContextVar("git_repository_locks", default=())


def git_lock_fds() -> tuple[int, ...]:
    return _git_locks.get()


@contextmanager
def inherit_lock_for_git(fd: int):
    token = _git_locks.set((*_git_locks.get(), fd))
    try:
        yield
    finally:
        _git_locks.reset(token)
