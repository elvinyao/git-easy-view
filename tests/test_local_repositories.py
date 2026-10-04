"""Local discovery and synchronization never contact or change source repositories."""

from pathlib import Path
import subprocess

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import jobs
from app.models import Branch, PlatformConnection, ProjectRepository, Repository, SyncJob
from app.providers import ProviderError
from app.web import repository_statuses
from test_pages import admin_pages, pages, post_form


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def local_repos(app, tmp_path):
    root = tmp_path / 'local-root'
    root.mkdir()
    for name in ('api', 'team/web'):
        source = root / name
        source.mkdir(parents=True)
        git(source, 'init', '-b', 'main')
        git(source, 'config', 'user.name', 'Local fixture')
        git(source, 'config', 'user.email', 'local@example.test')
        git(source, 'commit', '--allow-empty', '-m', f'{name} initial')
        git(source, 'branch', 'release/1.0')
        git(source, 'checkout', '-b', 'feature/local')
        git(source, 'commit', '--allow-empty', '-m', 'local feature commit')
        # A dangerous-looking remote must never be used by local import/sync.
        git(source, 'remote', 'add', 'origin', 'https://remote-must-not-be-used.invalid/repo.git')
        (source / 'uncommitted.txt').write_text('keep my worktree')
    (root / 'not-a-repository').mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'escape').symlink_to(outside, target_is_directory=True)
    return root


def provider(root, directory=None):
    from app.providers.local import LocalProvider

    connection = PlatformConnection(name='Local work', kind='local', base_url=str(directory or root))
    return LocalProvider(connection, root)


def test_local_discovery_nested_bare_and_worktrees_without_network(local_repos, monkeypatch):
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a, **kw: pytest.fail('Local discovery contacted HTTP'))
    bare = local_repos / 'mirror.git'
    subprocess.run(['git', 'clone', '--bare', str(local_repos / 'api'), str(bare)], check=True, capture_output=True)
    worktree = local_repos / 'linked-worktree'
    git(local_repos / 'api', 'worktree', 'add', '-b', 'feature/worktree', str(worktree))
    with provider(local_repos) as source:
        records = source.repositories()
        assert {repo.external_id for repo in records} == {'api', 'team/web', 'mirror.git', 'linked-worktree'}
        assert all(repo.default_branch == 'main' and not repo.web_url for repo in records)
        assert source.repository('team/web').clone_url == str(local_repos / 'team/web')
        assert source.pull_requests('api', None) == []


@pytest.mark.parametrize('identity', ['../outside', '/etc', 'api/../../outside', 'escape', 'https://example.test/repo', 'api\n'])
def test_local_repository_identity_cannot_escape_selected_directory(local_repos, identity):
    with provider(local_repos) as source:
        with pytest.raises(ProviderError):
            source.repository(identity)


def test_local_connection_and_git_metadata_cannot_escape_mounted_root(local_repos, tmp_path):
    with pytest.raises(ProviderError):
        provider(local_repos, tmp_path)
    linked = local_repos / 'bad-worktree'
    linked.mkdir()
    (linked / '.git').write_text(f'gitdir: {tmp_path}/outside\n')
    with provider(local_repos) as source:
        with pytest.raises(ProviderError):
            source.repository('bad-worktree')


def test_discovery_limit_marks_results_as_incomplete(local_repos, monkeypatch):
    from app.providers import local

    monkeypatch.setattr(local, 'MAX_DIRECTORIES', 1)
    with provider(local_repos) as source:
        assert source.repositories() == []
        assert source.scan_incomplete


def test_unreadable_git_metadata_does_not_hide_valid_repositories(local_repos):
    broken = local_repos / 'broken'
    broken.mkdir()
    (broken / '.git').write_text('gitdir: /not-mounted/missing\n')
    with provider(local_repos) as source:
        assert {repo.external_id for repo in source.repositories()} == {'api', 'team/web'}
        assert source.scan_incomplete


def test_deep_tree_does_not_hide_nearby_repositories(local_repos, monkeypatch):
    from app.providers import local

    monkeypatch.setattr(local, 'MAX_DEPTH', 1)
    with provider(local_repos) as source:
        assert {repo.external_id for repo in source.repositories()} == {'api'}
        assert source.scan_incomplete


def test_local_cache_never_uses_network_protocols(local_repos, tmp_path, monkeypatch):
    from app import git_service

    original = subprocess.Popen
    protocols = []

    def checked_popen(command, **kwargs):
        if 'clone' in command or 'fetch' in command:
            protocols.append(kwargs['env']['GIT_ALLOW_PROTOCOL'])
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', checked_popen)
    cache = tmp_path / 'cache.git'
    git_service.clone_local_bare(str(local_repos / 'api'), cache)
    git_service.fetch_local_bare(cache)
    assert protocols == ['file', 'file']


def create_local(client, app, root):
    app.state.settings.local_git_root = str(root)
    response = post_form(client, '/admin/connections', name='Local workspace', kind='local', local_path=str(root))
    assert response.status_code == 303, response.text
    with Session(app.state.engine) as session:
        connection = session.scalar(select(PlatformConnection).where(PlatformConnection.name == 'Local workspace'))
        assert connection.kind == 'local' and not connection.encrypted_token and not connection.auth_disabled
        return connection.id


def test_admin_directory_browser_and_batch_import(client, app, admin_pages, local_repos, monkeypatch):
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a, **kw: pytest.fail('Local import contacted HTTP'))
    connection = create_local(client, app, local_repos)
    response = client.get('/admin/local/directories')
    assert response.status_code == 200
    assert {item['name'] for item in response.json()['directories']} == {'api', 'team', 'not-a-repository'}
    listing = client.get(f'/admin/connections/{connection}/repositories')
    assert listing.status_code == 200 and 'team/web' in listing.text
    imported = post_form(client, '/admin/repositories/import', connection_id=connection,
                         project_id=admin_pages['project'], external_ids=['api', 'team/web'])
    assert imported.status_code == 303
    with Session(app.state.engine) as session:
        rows = session.scalars(select(Repository).where(Repository.connection_id == connection)).all()
        assert len(rows) == 2
        assert all(not repo.web_url and repo.clone_url.startswith(str(local_repos)) for repo in rows)
        assert all(session.scalar(select(ProjectRepository.id).where(ProjectRepository.repository_id == repo.id)) for repo in rows)
        assert all(session.scalar(select(SyncJob.id).where(SyncJob.repository_id == repo.id)) for repo in rows)
        repository_id = rows[0].id
    settings_page = client.get(f'/admin/repositories/{repository_id}')
    assert settings_page.status_code == 200
    assert '同步本地仓库' in settings_page.text and 'name="history_since"' not in settings_page.text
    detail = client.get(f'/repositories/{repository_id}')
    assert detail.status_code == 200 and '不适用' in detail.text
    assert 'PR / MR 平台记录 →' not in detail.text
    repeated = post_form(client, '/admin/repositories/import', connection_id=connection,
                         project_id=admin_pages['project'], external_ids=['api'])
    assert repeated.status_code == 303
    with Session(app.state.engine) as session:
        assert len(session.scalars(select(Repository).where(Repository.connection_id == connection)).all()) == 2


def test_local_form_errors_retain_directory_and_reject_escape(client, app, admin_pages, local_repos):
    app.state.settings.local_git_root = str(local_repos)
    response = post_form(client, '/admin/connections', name='Keep name', kind='local', local_path=str(local_repos.parent))
    assert response.status_code == 400
    assert 'id="error-local_path"' in response.text and 'Keep name' in response.text
    assert 'value="local"' in response.text
    assert client.get('/admin/local/directories', params={'path': str(local_repos.parent)}).status_code == 400


def test_scan_limit_labels_partial_results_and_links_back_to_directory(client, app, admin_pages, local_repos, monkeypatch):
    from app.providers import local

    connection = create_local(client, app, local_repos)
    monkeypatch.setattr(local, 'MAX_DIRECTORIES', 1)
    response = client.get(f'/admin/connections/{connection}/repositories')
    assert response.status_code == 200
    assert '部分目录未扫描' in response.text
    assert f'href="/admin/connections/{connection}"' in response.text


def test_directory_browser_requires_admin(client, app, admin_pages, local_repos):
    app.state.settings.local_git_root = str(local_repos)
    client.cookies.clear()
    assert client.get('/admin/local/directories', follow_redirects=False).status_code == 303


def test_local_sync_refreshes_cache_in_production_without_touching_source(client, app, admin_pages, local_repos, monkeypatch):
    connection_id = create_local(client, app, local_repos)
    imported = post_form(client, '/admin/repositories/import', connection_id=connection_id,
                         project_id=admin_pages['project'], external_ids=['api'])
    assert imported.status_code == 303
    with Session(app.state.engine) as session:
        repo = session.scalar(select(Repository).where(Repository.connection_id == connection_id))
        repository_id = repo.id
        job_id = session.scalar(select(SyncJob.id).where(SyncJob.repository_id == repo.id))
    source = local_repos / 'api'
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a, **kw: pytest.fail('Local synchronization contacted HTTP'))
    app.state.settings.production = True
    jobs.run_sync_job(job_id, settings=app.state.settings, provider_factory=lambda *a: pytest.fail('Remote provider used'))
    with Session(app.state.engine) as session:
        repo = session.get(Repository, repository_id)
        assert session.get(SyncJob, job_id).status == 'succeeded'
        assert repo.git_synced_at and repo.api_synced_at is None and repo.api_coverage_since is None
        status = repository_statuses(session, [repo])[repo.id]
        assert status.api_state == '不适用' and not status.attention
        assert {branch.name for branch in session.scalars(select(Branch).where(Branch.repository_id == repo.id, Branch.deleted.is_(False)))} == {'main', 'release/1.0', 'feature/local'}
    after = {path.relative_to(source): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    assert after == before
    git(source, 'commit', '--allow-empty', '-m', 'next local change')
    git(source, 'branch', '-D', 'release/1.0')
    with Session(app.state.engine) as session:
        queued = jobs.enqueue_sync(session, repository_id, settings=app.state.settings, force=True, queue=app.state.job_queue)
        new_id = queued.id
    jobs.run_sync_job(new_id, settings=app.state.settings, provider_factory=lambda *a: pytest.fail('Remote provider used'))
    with Session(app.state.engine) as session:
        assert session.get(SyncJob, new_id).status == 'succeeded'
        rows = {b.name: b for b in session.scalars(select(Branch).where(Branch.repository_id == repository_id))}
        assert rows['release/1.0'].deleted
        assert rows['feature/local'].head_oid == git(source, 'rev-parse', 'HEAD')
