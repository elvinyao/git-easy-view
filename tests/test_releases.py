from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (Branch, Deployment, GitTag, PlatformConnection, ProjectRepository,
                        PullRequest, ReleaseItem, ReleasePlan, ReleaseSource, Repository, User, utc_now)
from app.releases import current_production, plan_state, reconcile, sync_release_metadata
from test_auth import csrf_from
from test_pages import pages, admin_pages, post_form


@pytest.fixture
def release_data(app, admin_pages):
    with Session(app.state.engine) as session:
        session.add(GitTag(repository_id=admin_pages['repo'], name='v1.0', object_oid='b' * 40,
                           commit_oid='b' * 40, subject='Release one', commit_time=utc_now()))
        session.commit()
    return admin_pages


def create_plan(client, ids, **extra):
    rid = ids['repo']
    data = {'title': 'October release', 'scheduled_date': '2026-10-10', 'repos': str(rid),
            f'r{rid}_branch': 'feature/login', f'r{rid}_tag': 'v1.0', f'r{rid}_target': 'main'}
    data.update(extra)
    response = post_form(client, f"/projects/{ids['project']}/releases", **data)
    assert response.status_code == 303, response.text
    return response.headers['location']


def plan_values(app):
    with Session(app.state.engine) as session:
        plan = session.scalar(select(ReleasePlan).order_by(ReleasePlan.id.desc()))
        return plan.version, plan.items[0].id


def activate(client, app, url):
    version, _ = plan_values(app)
    response = post_form(client, url + '/action', version=str(version), action='activate')
    assert response.status_code == 303, response.text


@pytest.mark.parametrize('locale', ['zh-CN', 'ja', 'en'])
def test_release_pages_render_and_remain_public(app, client, release_data, locale):
    ids = release_data
    url = create_plan(client, ids)
    for path in (f"/projects/{ids['project']}/releases", url, url + '/edit',
                 f"/projects/{ids['project']}/production/{ids['repo']}"):
        response = client.get(path, params={'lang': locale})
        assert response.status_code == 200, response.text
        assert f'<html lang="{locale}">' in response.text
    client.cookies.clear()
    assert client.get(url).status_code == 200
    assert client.get(url + '/edit', follow_redirects=False).status_code == 303
    assert client.post(url + '/action', data={'action': 'activate'}, follow_redirects=False).status_code in (303, 403)


def test_deploy_merge_and_rollback_do_not_rewrite_history(app, client, release_data):
    ids = release_data
    url = create_plan(client, ids)
    activate(client, app, url)
    version, item_id = plan_values(app)
    response = post_form(client, url + f'/items/{item_id}', version=str(version), action='deploy', confirmed_oid='b' * 40)
    assert response.status_code == 303
    with Session(app.state.engine) as session:
        first = session.scalar(select(Deployment))
        first_id = first.id
        assert plan_state(session.scalar(select(ReleasePlan))) == 'active'
    production_url = f"/projects/{ids['project']}/production/{ids['repo']}"
    response = post_form(client, production_url, current_id=str(first_id), branch='main', notes='Hotfix now')
    assert response.status_code == 303
    version, _ = plan_values(app)
    assert post_form(client, url + f'/items/{item_id}', version=str(version), action='merge', notes='Verified in local Git').status_code == 303
    with Session(app.state.engine) as session:
        assert plan_state(session.scalar(select(ReleasePlan))) == 'complete'
        current = current_production(session, ids['project'])[ids['repo']]
        assert current.commit_oid == 'a' * 40  # Late merge does not overwrite newer production.
        current_id = current.id
    assert post_form(client, production_url, current_id=str(current_id), rollback_id=str(first_id), notes='Rollback performed').status_code == 303
    with Session(app.state.engine) as session:
        current = current_production(session, ids['project'])[ids['repo']]
        assert current.rollback_of_id == first_id and current.commit_oid == 'b' * 40
        assert len(session.scalars(select(Deployment)).all()) == 3


def test_stale_actions_csrf_and_cross_project_are_rejected(app, client, release_data):
    ids = release_data
    url = create_plan(client, ids)
    activate(client, app, url)
    assert post_form(client, url + '/action', version='1', action='cancel').status_code == 409
    assert client.post(url + '/action', data={'action': 'cancel', 'version': '2'}).status_code == 403
    plan_id = url.rsplit('/', 1)[1]
    assert client.get(f"/projects/{ids['private']}/releases/{plan_id}").status_code == 404
    version, item_id = plan_values(app)
    assert post_form(client, url + f'/items/{item_id}', version=str(version), action='merge', notes='').status_code == 400
    assert post_form(client, url + f'/items/{item_id}', version=str(version), action='deploy', confirmed_oid='a' * 40).status_code == 400
    assert post_form(client, url + f'/items/{item_id}', version=str(version), action='deploy', confirmed_oid='b' * 40, deployed_at='2999-01-01T00:00').status_code == 400


def test_moved_tag_requires_explicit_draft_update_and_history_stays_pinned(app, client, release_data):
    ids = release_data
    url = create_plan(client, ids)
    with Session(app.state.engine) as session:
        tag = session.scalar(select(GitTag))
        tag.commit_oid = 'c' * 40
        session.commit()
    assert post_form(client, url + '/action', version='1', action='activate').status_code == 409
    assert 'Tag 已移动或删除' in client.get(url).text
    with Session(app.state.engine) as session:
        assert session.scalar(select(ReleaseItem)).commit_oid == 'b' * 40


def test_draft_without_tag_can_be_edited_but_not_activated(app, client, release_data):
    rid = release_data['repo']
    url = create_plan(client, release_data, **{f'r{rid}_tag': ''})
    assert post_form(client, url + '/action', version='1', action='activate').status_code == 400
    response = post_form(client, url + '/edit', version='1', title='Updated', scheduled_date='2026-10-11',
        repos=str(rid), **{f'r{rid}_branch':'feature/login', f'r{rid}_tag':'v1.0', f'r{rid}_target':'main'})
    assert response.status_code == 303, response.text
    activate(client, app, url)
    assert client.get(url + '/edit').status_code == 409


@pytest.mark.parametrize('fault', [None, 'sha', 'target', 'fork', 'error', 'stale', 'identity', 'open'])
def test_auto_merge_requires_specific_fresh_exact_evidence(app, client, release_data, fault):
    ids = release_data
    url = create_plan(client, ids)
    activate(client, app, url)
    with Session(app.state.engine) as session:
        repo = session.get(Repository, ids['repo'])
        repo.api_synced_at = utc_now() - timedelta(days=1) if fault == 'stale' else utc_now()
        repo.api_error = 'failed' if fault == 'error' else None
        item = session.scalar(select(ReleaseItem))
        item.mr_id, item.mr_connection_id, item.mr_repository_id = 'mr-123', repo.connection_id, repo.external_id
        p = PullRequest(connection_id=repo.connection_id, repository_id=repo.id, external_id='different' if fault == 'identity' else 'mr-123',
            title='Release merge', web_url='https://github.com/team/api/pull/123',
            state='open' if fault == 'open' else 'merged',
            source_external_repo_id='fork' if fault == 'fork' else repo.external_id, target_external_repo_id=repo.external_id,
            source_branch='feature/login', target_branch='other' if fault == 'target' else 'main',
            source_oid='c' * 40 if fault == 'sha' else 'b' * 40, merge_oid='d' * 40, merged_at=utc_now())
        session.add(p)
        session.flush()
        reconcile(session, repo.id)
        session.commit()
        assert bool(item.merged_at) == (fault is None)
        assert current_production(session, ids['project']) == {}


def test_local_binding_metadata_does_not_change_clone_source(app, client, release_data):
    from contextlib import contextmanager
    from app.providers import Repo, PR
    ids = release_data
    with Session(app.state.engine) as session:
        repo = session.get(Repository, ids['repo'])
        remote_connection = repo.connection_id
        local = PlatformConnection(name='Local', kind='local', base_url='/data/local-git')
        session.add(local)
        session.flush()
        repo.connection_id, repo.clone_url = local.id, '/data/local-git/api'
        session.add(ReleaseSource(repository_id=repo.id, connection_id=remote_connection, external_id='123', full_name='team/api'))
        session.commit()
    class Provider:
        web_root = 'https://github.com'
        def repository(self, external):
            return Repo('123', 'team/api', 'https://github.com/team/api', 'https://github.com/team/api.git', 'main')
        def pull_requests(self, external, since, **kwargs):
            yield PR('pr-1', 'Merge', 'https://github.com/team/api/pull/1', 'merged', '123', 'feature/login', 'b'*40, '123', 'main', 'c'*40, utc_now(), utc_now())
    @contextmanager
    def factory(connection, token):
        yield Provider()
    sync_release_metadata(app.state.engine, ids['repo'], app.state.settings, factory)
    with Session(app.state.engine) as session:
        assert session.get(Repository, ids['repo']).clone_url == '/data/local-git/api'
        source = session.get(ReleaseSource, ids['repo'])
        assert source.records[0]['id'] == 'pr-1' and source.synced_at and not source.error


def test_cancel_keeps_already_deployed_production(app, client, release_data):
    url = create_plan(client, release_data)
    activate(client, app, url)
    version, item_id = plan_values(app)
    assert post_form(client, url + f'/items/{item_id}', version=str(version), action='deploy', confirmed_oid='b'*40).status_code == 303
    version, _ = plan_values(app)
    assert post_form(client, url + '/action', version=str(version), action='cancel').status_code == 303
    with Session(app.state.engine) as session:
        assert plan_state(session.scalar(select(ReleasePlan))) == 'cancelled'
        assert session.scalar(select(Deployment)).commit_oid == 'b'*40


def test_local_source_bind_and_unbind_are_admin_only_and_queue_sync(app, client, release_data):
    from test_pages import ProviderFixture
    ids = release_data
    app.state.provider_factory = ProviderFixture
    with Session(app.state.engine) as session:
        local = PlatformConnection(name='Local source', kind='local', base_url='/data/local-git')
        session.add(local)
        session.flush()
        repo = session.get(Repository, ids['repo'])
        repo.connection_id, repo.clone_url = local.id, '/data/local-git/source'
        session.commit()
    path = f"/projects/{ids['project']}/production/{ids['repo']}/source"
    response = post_form(client, path, action='bind', connection_id=str(ids['connection']), external_id='1')
    assert response.status_code == 303, response.text
    with Session(app.state.engine) as session:
        assert session.get(ReleaseSource, ids['repo']).external_id == '1'
        assert session.get(Repository, ids['repo']).clone_url == '/data/local-git/source'
    assert post_form(client, path, action='unbind').status_code == 303
    with Session(app.state.engine) as session:
        assert session.get(ReleaseSource, ids['repo']) is None


def test_failed_metadata_refresh_keeps_old_data_but_blocks_confirmation(app, client, release_data):
    from contextlib import contextmanager
    ids = release_data
    before = utc_now()
    with Session(app.state.engine) as session:
        session.add(ReleaseSource(repository_id=ids['repo'], connection_id=ids['connection'], external_id='1',
                                   full_name='team/api', synced_at=before, records=[{'id':'old'}]))
        session.commit()
    @contextmanager
    def failed(connection, token):
        raise RuntimeError('never expose sensitive response')
        yield
    sync_release_metadata(app.state.engine, ids['repo'], app.state.settings, failed)
    with Session(app.state.engine) as session:
        source = session.get(ReleaseSource, ids['repo'])
        assert source.synced_at == before and source.records == [{'id':'old'}]
        assert source.error and 'sensitive' not in source.error


def test_rollback_rejects_record_from_another_project(app, client, release_data):
    ids = release_data
    with Session(app.state.engine) as session:
        admin = session.scalar(select(User).where(User.is_admin.is_(True)))
        row = Deployment(project_id=ids['private'], repository_id=ids['repo'], branch_name='main',
                         commit_oid='a'*40, actor_id=admin.id, deployed_at=utc_now())
        session.add(row)
        session.commit()
        rollback_id = row.id
    response = post_form(client, f"/projects/{ids['project']}/production/{ids['repo']}", current_id='0', rollback_id=str(rollback_id), notes='No cross-project rollback')
    assert response.status_code == 400
