"""Regression coverage for release form recovery and explicit source discovery."""

from contextlib import contextmanager
from datetime import timedelta
import re

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (AuditEvent, Branch, BranchConfig, Deployment, GitTag, PlatformConnection,
                        ProjectRepository, ReleaseItem, ReleasePlan, ReleaseSource, Repository, User,
                        utc_now)
from app.providers import Repo, get_provider
from test_pages import ProviderFixture, admin_pages, pages, post_form
from test_providers import gh_repo, gl_repo
from test_releases import activate, create_plan, plan_values, release_data


def plan_form(ids, **overrides):
    rid = ids["repo"]
    values = {
        "title": "A carefully entered release",
        "scheduled_date": "2026-10-10",
        "notes": "Keep these release notes",
        "repos": [str(rid)],
        f"r{rid}_branch": "feature/login",
        f"r{rid}_tag": "v1.0",
        f"r{rid}_oid": "b" * 40,
        f"r{rid}_target": "main",
        f"r{rid}_mr": "",
    }
    values.update(overrides)
    return values


def add_repository(app, ids):
    with Session(app.state.engine) as session:
        repo = Repository(connection_id=ids["connection"], external_id="extra",
                          name="extra", full_name="team/extra", default_branch="main",
                          web_url="https://github.com/team/extra",
                          clone_url="https://github.com/team/extra.git")
        session.add(repo)
        session.flush()
        session.add(ProjectRepository(project_id=ids["project"], repository_id=repo.id))
        session.add(Branch(repository_id=repo.id, name="main", head_oid="c" * 40))
        session.commit()
        return repo.id


def make_local(app, ids):
    with Session(app.state.engine) as session:
        connection = PlatformConnection(name="Local source", kind="local", base_url="/data/local-git")
        session.add(connection)
        session.flush()
        repo = session.get(Repository, ids["repo"])
        repo.connection_id, repo.clone_url = connection.id, "/data/local-git/api"
        session.commit()


@pytest.mark.parametrize("name,value", [("title", "  "), ("scheduled_date", "2026-02-30")])
def test_invalid_plan_recovers_every_selected_field_without_creating_rows(app, client, release_data, name, value):
    ids = release_data
    extra = add_repository(app, ids)
    values = plan_form(ids, **{name: value})
    response = post_form(client, f"/projects/{ids['project']}/releases", **values,
                         password="DO-NOT-RECOVER", token="DO-NOT-RECOVER")
    assert response.status_code == 400
    assert response.context.get("form_values") == {key: val for key, val in values.items() if key != "repos"}
    assert response.context.get("form_errors", {}).get(name)
    assert response.context.get("form_lists") == {"repos": [str(ids["repo"])]}
    assert response.context.get("selected") == {ids["repo"]}
    assert {repo.id for repo in response.context["repositories"]} == {ids["repo"], extra}
    assert response.context["form_submitted"] is True
    assert "DO-NOT-RECOVER" not in response.text
    with Session(app.state.engine) as session:
        assert session.scalar(select(ReleasePlan)) is None
        assert session.scalar(select(ReleaseItem)) is None


def test_selected_query_keeps_unselected_repositories_available(client, app, release_data):
    ids = release_data
    extra = add_repository(app, ids)
    response = client.get(f"/projects/{ids['project']}/releases/new", params={"repos": ids["repo"]})
    assert response.status_code == 200
    assert {repo.id for repo in response.context["repositories"]} == {ids["repo"], extra}
    assert set(response.context["choices"]) == {ids["repo"], extra}
    assert response.context["selected"] == {ids["repo"]}


def test_recovered_version_preview_never_pairs_a_new_pin_with_the_saved_subject(client, app, release_data):
    ids = release_data
    url = create_plan(client, ids)
    version, _ = plan_values(app)
    with Session(app.state.engine) as session:
        session.add(GitTag(repository_id=ids['repo'], name='v2.0', object_oid='d' * 40,
                           commit_oid='d' * 40, subject='Moved version two', commit_time=utc_now()))
        session.commit()
    values = plan_form(ids, title=' ', version=str(version),
                       **{f"r{ids['repo']}_tag": 'v2.0', f"r{ids['repo']}_oid": 'c' * 40})
    response = post_form(client, url + '/edit', **values)
    assert response.status_code == 400
    assert response.context['form_values'][f"r{ids['repo']}_oid"] == 'c' * 40
    subject = re.search(r'<span data-version-subject>(.*?)</span>', response.text)
    timestamp = re.search(r'<small data-version-time>(.*?)</small>', response.text)
    assert subject and subject.group(1) == ''
    assert timestamp and timestamp.group(1) == ''


def test_choices_recommend_active_release_branch_and_show_newest_tags_first(client, app, release_data):
    ids = release_data
    path = f"/projects/{ids['project']}/releases/new"
    assert client.get(path).context["choices"][ids["repo"]].get("recommended_branch") == ""
    with Session(app.state.engine) as session:
        older = Branch(repository_id=ids["repo"], name="release/older", head_oid="b" * 40,
                       commit_time=utc_now() - timedelta(days=1))
        newer = Branch(repository_id=ids["repo"], name="release/newer", head_oid="b" * 40,
                       commit_time=utc_now())
        session.add_all([older, newer])
        session.flush()
        session.add_all([BranchConfig(branch_id=older.id, role="release", active_release=True),
                         BranchConfig(branch_id=newer.id, role="release")])
        session.add(GitTag(repository_id=ids["repo"], name="aaa-newest", object_oid="b" * 40,
                           commit_oid="b" * 40, commit_time=utc_now() + timedelta(hours=1)))
        session.commit()
    options = client.get(path).context["choices"][ids["repo"]]
    assert options["recommended_branch"] == "release/older"
    assert options["tags"][0].name == "aaa-newest"


def test_failed_edit_rolls_back_items_and_recovers_from_fresh_rows(client, app, release_data):
    ids = release_data
    url = create_plan(client, ids)
    values = plan_form(ids, version="1", **{f"r{ids['repo']}_branch": "missing-branch"})
    response = post_form(client, url + "/edit", **values)
    assert response.status_code == 400
    assert response.context.get("form_values", {}).get("version") == "1"
    assert response.context.get("form_errors", {}).get(f"r{ids['repo']}_branch")
    assert response.context["plan"].title == "October release"
    assert response.context["items"][ids["repo"]].branch_name == "feature/login"
    with Session(app.state.engine) as session:
        plan = session.scalar(select(ReleasePlan))
        assert plan.title == "October release" and plan.version == 1
        assert len(plan.items) == 1 and plan.items[0].branch_name == "feature/login"


def test_stale_edit_preserves_submitted_version_and_pinned_oid(client, app, release_data):
    ids = release_data
    url = create_plan(client, ids)
    with Session(app.state.engine) as session:
        plan = session.scalar(select(ReleasePlan))
        plan.version = 2
        plan.title = "Saved by another administrator"
        session.commit()
    response = post_form(client, url + "/edit", **plan_form(ids, version="1"))
    assert response.status_code == 409
    assert response.context.get("form_errors", {}).get("version")
    assert response.context.get("form_values", {}).get("version") == "1"
    assert response.context["form_values"][f"r{ids['repo']}_oid"] == "b" * 40
    assert response.context["plan"].version == 2
    with Session(app.state.engine) as session:
        assert session.scalar(select(ReleasePlan)).title == "Saved by another administrator"


def test_stale_tag_recovers_the_original_oid_without_saving(client, app, release_data):
    ids = release_data
    response = post_form(client, f"/projects/{ids['project']}/releases",
                         **plan_form(ids, **{f"r{ids['repo']}_oid": "a" * 40}))
    assert response.status_code == 409
    assert response.context.get("form_values", {}).get(f"r{ids['repo']}_oid") == "a" * 40
    assert response.context.get("form_errors", {}).get(f"r{ids['repo']}_tag")
    with Session(app.state.engine) as session:
        assert session.scalar(select(ReleasePlan)) is None


def test_item_error_recovers_notes_and_opens_the_submitted_item(client, app, release_data):
    ids = release_data
    url = create_plan(client, ids)
    activate(client, app, url)
    version, item_id = plan_values(app)
    values = {"version": str(version), "action": "deploy", "confirmed_oid": "b" * 40,
              "notes": "Deployment notes to retain", "deployed_at": "not-a-date"}
    response = post_form(client, url + f"/items/{item_id}", **values)
    assert response.status_code == 400
    assert response.context.get("form_values") == values
    assert response.context.get("form_item_id") == item_id
    assert response.context.get("form_kind") == "item"
    assert response.context.get("form_errors", {}).get("deployed_at")
    with Session(app.state.engine) as session:
        assert session.scalar(select(ReleasePlan)).version == version
        assert session.get(ReleaseItem, item_id).deployed_at is None
        assert session.scalar(select(Deployment)) is None


def test_stale_plan_action_keeps_original_version_in_detail_form(client, app, release_data):
    url = create_plan(client, release_data)
    activate(client, app, url)
    response = post_form(client, url + "/action", version="1", action="cancel")
    assert response.status_code == 409
    assert response.context.get("form_kind") == "plan"
    assert response.context.get("form_values", {}).get("version") == "1"
    assert response.context.get("form_errors", {}).get("version")
    with Session(app.state.engine) as session:
        assert session.scalar(select(ReleasePlan)).status == "active"


def test_create_and_start_is_one_atomic_operation(client, app, release_data):
    ids = release_data
    response = post_form(client, f"/projects/{ids['project']}/releases", **plan_form(ids, intent="start"))
    assert response.status_code == 303
    assert response.headers["location"].endswith("?saved=started")
    with Session(app.state.engine) as session:
        plan = session.scalar(select(ReleasePlan))
        assert plan.status == "active"
        assert len(plan.items) == 1 and plan.items[0].commit_oid == "b" * 40
        actions = session.scalars(select(AuditEvent.action)).all()
        assert "release.saved" in actions and "release.activate" in actions


def test_success_redirects_report_safe_action_codes(client, app, release_data):
    ids = release_data
    response = post_form(client, f"/projects/{ids['project']}/releases", **plan_form(ids, intent="draft"))
    assert response.status_code == 303 and response.headers["location"].endswith("?saved=created")
    url = response.headers["location"].split("?", 1)[0]
    response = post_form(client, url + "/edit", **plan_form(ids, version="1", intent="draft"))
    assert response.headers["location"] == url + "?saved=saved"
    version, item_id = plan_values(app)
    response = post_form(client, url + "/action", version=str(version), action="activate")
    assert response.headers["location"] == url + "?saved=started"
    version, item_id = plan_values(app)
    response = post_form(client, url + f"/items/{item_id}", version=str(version),
                         action="deploy", confirmed_oid="b" * 40)
    assert response.headers["location"] == url + f"?saved=deployed#item-{item_id}"
    version, _ = plan_values(app)
    response = post_form(client, url + f"/items/{item_id}", version=str(version),
                         action="merge", notes="Verified merge")
    assert response.headers["location"] == url + f"?saved=merged#item-{item_id}"
    with Session(app.state.engine) as session:
        first_id = session.scalar(select(Deployment.id))
    production_path = f"/projects/{ids['project']}/production/{ids['repo']}"
    response = post_form(client, production_path, current_id=str(first_id), branch="main", notes="Hotfix")
    assert response.headers["location"] == production_path + "?saved=production"
    with Session(app.state.engine) as session:
        current_id = session.scalar(select(Deployment.id).order_by(Deployment.id.desc()))
    response = post_form(client, production_path, current_id=str(current_id), rollback_id=str(first_id), notes="Reverted")
    assert response.headers["location"] == production_path + "?saved=rollback"
    make_local(app, ids)
    app.state.provider_factory = ProviderFixture
    response = post_form(client, production_path + "/source", action="bind",
                         connection_id=str(ids["connection"]), external_id="1")
    assert response.headers["location"] == production_path + "?saved=source"
    response = post_form(client, production_path + "/source", action="unbind")
    assert response.headers["location"] == production_path + "?saved=unbound"


def test_start_requires_tag_and_recovers_as_an_unsaved_form(client, app, release_data):
    ids = release_data
    response = post_form(client, f"/projects/{ids['project']}/releases", **plan_form(
        ids, intent="start", **{f"r{ids['repo']}_tag": ""}))
    assert response.status_code == 400
    assert response.context.get("form_errors", {}).get(f"r{ids['repo']}_tag")
    assert response.context.get("form_values", {}).get("intent") == "start"
    with Session(app.state.engine) as session:
        assert session.scalar(select(ReleasePlan)) is None


def test_initial_production_allows_optional_notes(client, app, release_data):
    ids = release_data
    response = post_form(client, f"/projects/{ids['project']}/production/{ids['repo']}",
                         current_id="0", branch="main", notes="")
    assert response.status_code == 303
    with Session(app.state.engine) as session:
        assert session.scalar(select(Deployment)).notes == ""


@pytest.mark.parametrize("changes,field,status", [
    ({"branch": "deleted"}, "branch", 400),
    ({"deployed_at": "not-a-date"}, "deployed_at", 400),
    ({"deployed_at": "0001-01-01T00:00"}, "deployed_at", 400),
    ({"current_id": "22"}, "current_id", 409),
    ({"rollback_id": "22", "notes": ""}, "notes", 400),
])
def test_production_failure_keeps_input_and_history_unchanged(client, app, release_data, changes, field, status):
    ids = release_data
    values = {"current_id": "0", "branch": "main", "tag": "", "oid": "a" * 40,
              "notes": "Keep operator notes", "deployed_at": "2026-01-01T13:00"}
    values.update(changes)
    response = post_form(client, f"/projects/{ids['project']}/production/{ids['repo']}", **values)
    assert response.status_code == status
    assert response.context.get("form_kind") == "production"
    assert response.context.get("form_errors", {}).get(field)
    assert response.context.get("form_values") == values
    with Session(app.state.engine) as session:
        assert session.scalar(select(Deployment)) is None


def test_source_failure_recovers_selected_connection_and_manual_id(client, app, release_data):
    ids = release_data
    make_local(app, ids)
    app.state.provider_factory = ProviderFixture
    values = {"action": "bind", "connection_id": str(ids["connection"]), "external_id": "99999"}
    response = post_form(client, f"/projects/{ids['project']}/production/{ids['repo']}/source", **values)
    assert response.status_code == 400
    assert response.context.get("form_kind") == "source"
    assert response.context.get("form_values") == values
    assert response.context.get("form_errors", {}).get("external_id")
    with Session(app.state.engine) as session:
        assert session.get(ReleaseSource, ids["repo"]) is None


def test_remote_repository_lookup_is_explicit_and_bounded(client, app, release_data):
    ids = release_data
    make_local(app, ids)
    fetched = []

    class LargeProvider(ProviderFixture):
        def repositories(self):
            fetched.append(True)
            for number in range(2005):
                name = f"team/repo-{number}"
                yield Repo(str(number), name, f"https://github.com/{name}",
                           f"https://github.com/{name}.git", "main")

    app.state.provider_factory = LargeProvider
    base = f"/projects/{ids['project']}/production/{ids['repo']}"
    assert client.get(base).status_code == 200
    assert fetched == []
    response = client.get(base + "/source/repositories", params={"connection_id": ids["connection"]})
    assert response.status_code == 200
    body = response.json()
    assert len(body["repositories"]) == 2000 and body["incomplete"] is True
    assert body["repositories"][0] == {"external_id": "0", "full_name": "team/repo-0", "default_branch": "main"}
    assert fetched == [True]


@pytest.mark.parametrize("kind,raw_repo", [("github", gh_repo), ("gitlab", gl_repo)])
def test_source_lookup_stops_real_provider_pagination_after_result_limit(client, app, release_data, kind, raw_repo):
    ids = release_data
    make_local(app, ids)
    with Session(app.state.engine) as session:
        connection = session.get(PlatformConnection, ids["connection"])
        connection.kind, connection.base_url = kind, f"https://{kind}.com"
        session.commit()
    requested_pages = []

    def respond(request):
        page = int(request.url.params.get("page", "1"))
        requested_pages.append(page)
        if page > 21:
            return httpx.Response(503, text="DO-NOT-FETCH-AFTER-LIMIT")
        rows = [raw_repo(number, f"team/repo-{number}")
                for number in range((page - 1) * 100 + 1, page * 100 + 1)]
        next_url = request.url.copy_set_param("page", str(page + 1))
        headers = {"Link": f'<{next_url}>; rel="next"'} if kind == "github" else {"X-Next-Page": str(page + 1)}
        return httpx.Response(200, headers=headers, json=rows)

    @contextmanager
    def factory(connection, token):
        with httpx.Client(transport=httpx.MockTransport(respond)) as remote:
            with get_provider(connection, token, client=remote) as provider:
                yield provider

    app.state.provider_factory = factory
    response = client.get(f"/projects/{ids['project']}/production/{ids['repo']}/source/repositories",
                          params={"connection_id": ids["connection"]})
    assert response.status_code == 200, response.text
    assert requested_pages == list(range(1, 22))
    payload = response.json()
    assert payload["incomplete"] is True and len(payload["repositories"]) == 2000
    assert payload["repositories"][-1]["external_id"] == "2000"


@pytest.mark.parametrize("kind,raw_repo", [("github", gh_repo), ("gitlab", gl_repo)])
def test_lookup_failure_can_be_retried_without_mutating_source(client, app, release_data, kind, raw_repo):
    ids = release_data
    make_local(app, ids)
    with Session(app.state.engine) as session:
        connection = session.get(PlatformConnection, ids["connection"])
        connection.kind, connection.base_url = kind, f"https://{kind}.com"
        connection.encrypted_token = app.state.settings.encrypt_token("private-fixture-token")
        session.add(ReleaseSource(repository_id=ids["repo"], connection_id=connection.id,
                                  external_id="existing", full_name="team/existing", revision=3,
                                  records=[{"id": "preserved"}]))
        session.commit()
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, text="private-fixture-token")
        return httpx.Response(200, json=[raw_repo(1, "team/retry")])

    @contextmanager
    def factory(connection, token):
        with httpx.Client(transport=httpx.MockTransport(respond)) as remote:
            with get_provider(connection, token, client=remote) as provider:
                yield provider

    app.state.provider_factory = factory
    path = f"/projects/{ids['project']}/production/{ids['repo']}/source/repositories"
    failed = client.get(path, params={"connection_id": ids["connection"]}, headers={"Accept": "application/json"})
    assert failed.status_code == 400
    assert "private-fixture-token" not in failed.text
    recovered = client.get(path, params={"connection_id": ids["connection"]})
    assert recovered.status_code == 200 and recovered.json()["incomplete"] is False
    assert recovered.json()["repositories"][0]["external_id"] == "1"
    assert len(requests) == 2
    with Session(app.state.engine) as session:
        source = session.get(ReleaseSource, ids["repo"])
        assert source.external_id == "existing" and source.revision == 3
        assert source.records == [{"id": "preserved"}]
        assert session.scalar(select(Deployment)) is None


def test_source_lookup_rejects_disabled_connections_remote_repos_and_readers(client, app, release_data):
    ids = release_data
    app.state.provider_factory = ProviderFixture
    path = f"/projects/{ids['project']}/production/{ids['repo']}/source/repositories"
    assert client.get(path, params={"connection_id": ids["connection"]}).status_code == 400
    make_local(app, ids)
    with Session(app.state.engine) as session:
        session.get(PlatformConnection, ids["connection"]).auth_disabled = True
        session.commit()
    assert client.get(path, params={"connection_id": ids["connection"]}).status_code == 400
    with Session(app.state.engine) as session:
        session.scalar(select(User).where(User.username == "alice")).is_admin = False
        session.commit()
    assert client.get(path, params={"connection_id": ids["connection"]}).status_code == 403


@pytest.mark.parametrize("failure", ["exception", "url"])
def test_lookup_reports_generic_localized_errors(client, app, release_data, failure):
    ids = release_data
    make_local(app, ids)

    class BadProvider(ProviderFixture):
        def repositories(self):
            if failure == "exception":
                raise RuntimeError("DO-NOT-EXPOSE-TOKEN")
            return [Repo("1", "team/api", "https://evil.example/api", "https://evil.example/api.git", "main")]

    app.state.provider_factory = BadProvider
    response = client.get(f"/projects/{ids['project']}/production/{ids['repo']}/source/repositories",
                          params={"connection_id": ids["connection"], "lang": "en"},
                          headers={"Accept": "application/json"})
    assert response.status_code == 400
    assert response.json().get("detail")
    assert "DO-NOT-EXPOSE" not in response.text and "evil.example" not in response.text


def test_bind_revalidates_selected_external_identity(client, app, release_data):
    ids = release_data
    make_local(app, ids)

    class ChangedIdentity(ProviderFixture):
        def repository(self, external_id):
            return Repo("different", "team/api", "https://github.com/team/api",
                        "https://github.com/team/api.git", "main")

    app.state.provider_factory = ChangedIdentity
    response = post_form(client, f"/projects/{ids['project']}/production/{ids['repo']}/source",
                         action="bind", connection_id=str(ids["connection"]), external_id="1")
    assert response.status_code == 400
    with Session(app.state.engine) as session:
        assert session.get(ReleaseSource, ids["repo"]) is None
