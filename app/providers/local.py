"""Bounded discovery inside the administrator's explicitly mounted Git root."""

from collections import deque
from pathlib import Path
import time

from app.git_service import _run
from .base import ProviderError, Repo


MAX_DIRECTORIES = 5000
MAX_REPOSITORIES = 1000
MAX_DEPTH = 12
SCAN_SECONDS = 15
SKIP_DIRECTORIES = {'.git', 'node_modules', 'vendor', 'venv', '.venv', '__pycache__', '.tox'}


def directory(root, value=None):
    try:
        root = Path(root).resolve(strict=True)
        text = str(value or root)
        if len(text) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in text) or '\\' in text:
            raise ValueError()
        candidate = Path(text)
        path = (candidate if candidate.is_absolute() else root / candidate).resolve(strict=True)
        if not root.is_dir() or not path.is_dir() or not path.is_relative_to(root):
            raise ValueError()
        return path
    except (OSError, RuntimeError, ValueError):
        raise ProviderError('Local directory is unavailable or outside the mounted root') from None


def git_marker(path):
    return (path / '.git').exists() or ((path / 'HEAD').is_file() and (path / 'objects').is_dir())


def browse(root, value=None):
    path = directory(root, value)
    root = directory(root)
    children = []
    try:
        for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
            if child.name.startswith('.') or child.name in SKIP_DIRECTORIES:
                continue
            try:
                resolved = directory(root, child)
            except ProviderError:
                continue
            children.append(dict(name=child.name, path=str(resolved), git=git_marker(resolved)))
            if len(children) > 500:
                raise ProviderError('Local directory listing limit exceeded; choose a narrower directory')
    except OSError:
        raise ProviderError('Local directory cannot be read') from None
    return dict(path=str(path), parent=str(path.parent) if path != root else None, directories=children)


class LocalProvider:
    def __init__(self, connection, root):
        self.root = directory(root)
        self.path = directory(self.root, connection.base_url)
        self.scan_incomplete = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def check(self):
        # Opening the directory checks access without downloading anything.
        browse(self.root, self.path)
        return dict(identity=self.path.name, local=True)

    def repository(self, external_id):
        if (not isinstance(external_id, str) or not external_id or len(external_id) > 255
                or (external_id != '.' and any(part in ('', '.', '..') for part in external_id.split('/')))
                or Path(external_id).is_absolute()):
            raise ProviderError('Invalid local repository identity')
        path = directory(self.path, external_id)
        if not git_marker(path):
            raise ProviderError('Local directory is not a Git repository')
        try:
            args = ['-c', f'safe.directory={path}', '-C', str(path)]
            _, metadata = _run([*args, 'rev-parse', '--path-format=absolute', '--absolute-git-dir', '--git-common-dir'])
            for raw in metadata.strip().split('\n'):
                gitdir = directory(self.root, raw)
                for entry in ('config', 'objects', 'objects/info/alternates'):
                    candidate = gitdir / entry
                    if candidate.exists() and not candidate.resolve().is_relative_to(self.root):
                        raise ProviderError('Git metadata is outside the mounted root')
                alternates = gitdir / 'objects/info/alternates'
                if alternates.is_file():
                    for raw_alternate in alternates.read_text().splitlines():
                        alternate = Path(raw_alternate)
                        directory(self.root, alternate if alternate.is_absolute() else gitdir / 'objects' / alternate)
            _, names = _run([*args, 'for-each-ref', '--format=%(refname:strip=2)', 'refs/heads'])
            branches = names.split('\n')
            _, head = _run([*args, 'symbolic-ref', '--quiet', '--short', 'HEAD'], allowed_codes=(0, 1))
            default = next((name for name in ('main', 'master', head.strip()) if name and name in branches), head.strip() or None)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('Local Git metadata cannot be read') from None
        name = self.path.name if external_id == '.' else f'{self.path.name}/{external_id}'
        return Repo(external_id, name, '', str(path), default)

    def repositories(self):
        results, visited = [], set()
        self.scan_incomplete = False
        # Discover nearby repositories before spending the budget on deep trees.
        pending = deque([(self.path, 0)])
        deadline = time.monotonic() + SCAN_SECONDS
        while pending:
            path, depth = pending.popleft()
            if path in visited:
                continue
            visited.add(path)
            if len(visited) > MAX_DIRECTORIES or time.monotonic() > deadline:
                self.scan_incomplete = True
                break
            if git_marker(path):
                relative = path.relative_to(self.path).as_posix()
                try:
                    results.append(self.repository(relative))
                except ProviderError:
                    self.scan_incomplete = True
                if len(results) >= MAX_REPOSITORIES:
                    self.scan_incomplete |= bool(pending)
                    break
                continue
            try:
                for child in path.iterdir():
                    if child.name.startswith('.') or child.name in SKIP_DIRECTORIES:
                        continue
                    try:
                        resolved = directory(self.path, child)
                    except ProviderError:
                        continue
                    if depth >= MAX_DEPTH:
                        self.scan_incomplete = True
                        continue
                    if len(visited) + len(pending) >= MAX_DIRECTORIES or time.monotonic() > deadline:
                        self.scan_incomplete = True
                        break
                    pending.append((resolved, depth + 1))
            except OSError:
                self.scan_incomplete = True
        return sorted(results, key=lambda repo: repo.full_name.casefold())

    def pull_requests(self, external_id, since, **kwargs):
        return []
