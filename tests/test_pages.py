import re
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Branch,
    BranchConfig,
    Comparison,
    PlatformConnection,
    Project,
    ProjectMember,
    ProjectRepository,
    PullRequest,
    Repository,
    SyncJob,
    User,
    AuditEvent,
    utc_now,
)
from test_auth import csrf_from, login


class Queue:
    def ensure(self, job_id):
        pass


@pytest.fixture
def pages(app, client, user, tmp_path):
    app.state.settings.cache_root = str(tmp_path / "cache")
    app.state.job_queue = Queue()
    with Session(app.state.engine) as s:
        connection = PlatformConnection(
            name="GitHub", kind="github", base_url="https://github.com"
        )
        project = Project(name="产品研发", slug="product")
        private = Project(name="机密项目", slug="secret")
        s.add_all([connection, project, private])
        s.flush()
        repo = Repository(
            connection_id=connection.id,
            external_id="1",
            name="api",
            full_name="team/api",
            web_url="https://github.com/team/api",
            clone_url="https://github.com/team/api.git",
            default_branch="main",
            git_synced_at=utc_now(),
            snapshot_generation=1,
        )
        s.add(repo)
        s.flush()
        s.add_all(
            [
                ProjectMember(project_id=project.id, user_id=user.id),
                ProjectRepository(project_id=project.id, repository_id=repo.id),
            ]
        )
        main = Branch(
            repository_id=repo.id, name="main", head_oid="a" * 40, commit_time=utc_now()
        )
        feature = Branch(
            repository_id=repo.id,
            name="feature/login",
            head_oid="b" * 40,
            commit_time=utc_now(),
        )
        s.add_all([main, feature])
        s.flush()
        s.add(
            Comparison(
                repository_id=repo.id,
                source_oid=feature.head_oid,
                target_oid=main.head_oid,
                state="diverged",
                ahead=3,
                behind=2,
                merge_bases=["c" * 40],
            )
        )
        s.commit()
        ids = dict(
            project=project.id,
            private=private.id,
            repo=repo.id,
            main=main.id,
            feature=feature.id,
            connection=connection.id,
        )
    login(client)
    return ids


def test_project_list_and_lazy_branches(client, pages):
    home = client.get("/")
    assert "产品研发" in home.text and "机密项目" not in home.text
    response = client.get(
        f"/projects/{pages['project']}?expanded={pages['repo']}&bp_{pages['repo']}=2&q=login"
    )
    assert response.status_code == 200
    assert 'data-expanded="true"' in response.text
    assert "bp_" in response.text
    assert "data-branch-id=" not in response.text
    assert "Git 同步" in response.text and "API 同步" in response.text
    fragment = client.get(f"/repositories/{pages['repo']}/branches?q=login")
    assert fragment.status_code == 200
    assert "feature/login" in fragment.text and "未全部包含" in fragment.text
    assert "比较对象" in fragment.text and "无计划目标" in fragment.text


def test_branch_page_uses_oid_cache_and_preserves_history(client, app, pages):
    with Session(app.state.engine) as s:
        s.add(
            PullRequest(
                connection_id=pages["connection"],
                repository_id=pages["repo"],
                external_id="pr1",
                title="<script>bad</script>",
                web_url="javascript:alert(1)",
                state="merged",
                source_repository_id=pages["repo"],
                source_branch="feature/login",
                source_oid="d" * 40,
                target_repository_id=pages["repo"],
                target_branch="main",
            )
        )
        s.commit()
    response = client.get(f"/repositories/{pages['repo']}/branches/{pages['feature']}")
    assert response.status_code == 200
    assert "未全部包含" in response.text and "历史记录" in response.text
    assert "b" * 40 in response.text and "c" * 40 in response.text
    assert (
        "&lt;script&gt;" in response.text and 'href="javascript:' not in response.text
    )
    assert "当前头提交不同" in response.text


def test_pagination_and_filters(client, app, pages):
    with Session(app.state.engine) as s:
        s.add_all(
            [
                Branch(
                    repository_id=pages["repo"],
                    name=f"feature/item-{i:03}",
                    head_oid="e" * 40,
                    commit_time=utc_now() - timedelta(minutes=i),
                )
                for i in range(55)
            ]
        )
        s.commit()
    first = client.get(f"/repositories/{pages['repo']}/branches")
    second = client.get(f"/repositories/{pages['repo']}/branches?page=2")
    assert first.text.count("data-branch-id=") == 50
    assert second.text.count("data-branch-id=") == 7
    assert "main" in first.text
    assert (
        client.get(f"/repositories/{pages['repo']}/branches?role=release").text.count(
            "data-branch-id="
        )
        == 0
    )


def test_member_comparison_local_queue_and_status(client, app, pages):
    path = f"/repositories/{pages['repo']}/branches/{pages['feature']}/compare"
    assert client.post(path, data={"target_id": pages["main"]}).status_code == 403
    response = client.post(
        path,
        data={"target_id": pages["main"], "csrf_token": csrf_from(client.get("/"))},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    with Session(app.state.engine) as s:
        job = s.get(SyncJob, response.json()["job_id"])
        assert job.kind == "comparison"
    assert client.get(f"/repositories/{pages['repo']}/jobs/{job.id}").status_code == 200


def test_importing_empty_stale_states(client, app, pages):
    with Session(app.state.engine) as s:
        repo = s.get(Repository, pages["repo"])
        repo.git_synced_at = None
        repo.snapshot_generation = 0
        s.query(Branch).filter_by(repository_id=repo.id).delete()
        s.commit()
    assert "正在导入" in client.get(f"/repositories/{pages['repo']}/branches").text
    with Session(app.state.engine) as s:
        repo = s.get(Repository, pages["repo"])
        repo.git_synced_at = utc_now()
        repo.snapshot_generation = 1
        s.commit()
    assert "空仓库" in client.get(f"/repositories/{pages['repo']}/branches").text
    with Session(app.state.engine) as s:
        s.get(Repository, pages["repo"]).sync_error = "failed"
        s.commit()
    assert "保留上次快照" in client.get(f"/repositories/{pages['repo']}/branches").text


@pytest.fixture
def admin_pages(app, client, user, pages):
    with Session(app.state.engine) as s:
        s.get(User, user.id).is_admin = True
        s.commit()
    return pages


def post_form(client, path, **fields):
    fields["csrf_token"] = csrf_from(client.get("/"))
    return client.post(path, data=fields, follow_redirects=False)


def test_admin_user_project_membership_flow(client, app, admin_pages):
    assert client.get("/admin/users/new").status_code == 200
    assert (
        post_form(
            client, "/admin/users", username="bob", password="password long enough"
        ).status_code
        == 303
    )
    with Session(app.state.engine) as s:
        bob = s.scalar(select(User).where(User.username == "bob"))
        bid = bob.id
        assert bob.check_password("password long enough")
    assert (
        post_form(
            client,
            f"/admin/users/{bid}",
            username="bob",
            password="new long password",
            is_active="on",
            is_admin="on",
        ).status_code
        == 303
    )
    project = admin_pages["project"]
    assert client.get(f"/admin/projects/{project}").status_code == 200
    assert (
        post_form(
            client, f"/admin/projects/{project}/members", user_id=bid, action="grant"
        ).status_code
        == 303
    )
    assert (
        post_form(
            client, f"/admin/projects/{project}/members", user_id=bid, action="revoke"
        ).status_code
        == 303
    )
    with Session(app.state.engine) as s:
        assert (
            s.scalar(
                select(ProjectMember).where(
                    ProjectMember.project_id == project, ProjectMember.user_id == bid
                )
            )
            is None
        )
        assert s.scalar(select(User).where(User.id == bid)).is_admin
        assert s.query(AuditEvent).count() >= 4


class ProviderFixture:
    web_root = "https://github.com"

    def __init__(self, connection, token):
        self.token = token

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def check(self):
        return {"identity": "fixture", "version": None, "capabilities": {}}

    def repositories(self):
        from app.providers import Repo

        return [
            Repo(
                str(i),
                f"team/repo-{i:03}",
                f"https://github.com/team/repo-{i:03}",
                f"https://github.com/team/repo-{i:03}.git",
                "main",
            )
            for i in range(1, 57)
        ]

    def repository(self, external_id):
        return next(r for r in self.repositories() if r.external_id == external_id)


def test_admin_connection_import_and_rotation(client, app, admin_pages):
    app.state.provider_factory = ProviderFixture
    response = post_form(
        client,
        "/admin/connections",
        name="new",
        kind="github",
        base_url="https://github.com",
        token="super-secret",
    )
    assert response.status_code == 303
    with Session(app.state.engine) as s:
        connection = s.scalar(
            select(PlatformConnection).where(PlatformConnection.name == "new")
        )
        cid = connection.id
        assert connection.encrypted_token != "super-secret"
        assert connection.auth_disabled
    response = post_form(client, f"/admin/connections/{cid}/check")
    assert response.status_code == 200 and "未知" in response.text
    page = client.get(f"/admin/connections/{cid}/repositories")
    assert page.status_code == 200 and page.text.count('name="external_ids"') == 50
    response = post_form(
        client,
        "/admin/repositories/import",
        connection_id=cid,
        project_id=admin_pages["project"],
        external_ids=["2", "3"],
        clone_url="https://evil.example/steal",
    )
    assert response.status_code == 303
    with Session(app.state.engine) as s:
        repo = s.scalar(
            select(Repository).where(
                Repository.connection_id == cid, Repository.external_id == "2"
            )
        )
        rid = repo.id
        assert repo.clone_url == "https://github.com/team/repo-002.git"
        old_token = s.get(PlatformConnection, cid).encrypted_token
    assert (
        post_form(
            client,
            f"/admin/connections/{cid}",
            name="renamed",
            kind="github",
            base_url="https://github.com",
            token="",
        ).status_code
        == 303
    )
    assert (
        post_form(
            client,
            f"/admin/connections/{cid}",
            name="renamed",
            kind="gitlab",
            base_url="https://other.example",
            token="",
        ).status_code
        == 400
    )
    assert "super-secret" not in client.get(f"/admin/connections/{cid}").text
    with Session(app.state.engine) as s:
        assert s.get(PlatformConnection, cid).encrypted_token == old_token
        assert s.query(SyncJob).filter_by(repository_id=rid).count() >= 1


def test_admin_config_sync_backfill_queue_outage_and_disabled_connection(
    client, app, admin_pages
):
    class BrokenQueue:
        def ensure(self, job_id):
            raise RuntimeError("secret")

    app.state.job_queue = BrokenQueue()
    ids = admin_pages
    assert client.get(f"/admin/repositories/{ids['repo']}").status_code == 200
    response = post_form(
        client, f"/admin/repositories/{ids['repo']}/sync", history_since="2020-01-02"
    )
    assert response.status_code == 303
    with Session(app.state.engine) as s:
        job = s.scalar(select(SyncJob).where(SyncJob.repository_id == ids["repo"]))
        assert job.history_since.year == 2020 and job.enqueue_error
        connection = s.get(PlatformConnection, ids["connection"])
        connection.auth_disabled = True
        s.get(Branch, ids["feature"]).source_candidates = [
            {"kind": "inferred", "name": "old"}
        ]
        s.commit()
    assert (
        post_form(
            client,
            f"/admin/repositories/{ids['repo']}",
            sync_interval_seconds="600",
            release_prefixes="release/,stable/",
            feature_prefixes="feature/",
        ).status_code
        == 303
    )
    assert "待恢复连接后分析" in client.get(f"/admin/repositories/{ids['repo']}").text
    assert client.get(f"/admin/branches/{ids['feature']}").status_code == 200
    assert (
        post_form(
            client,
            f"/admin/branches/{ids['feature']}",
            role="release",
            active_release="on",
            manual_source_branch_id=ids["main"],
            manual_targets=[str(ids["main"])],
            main_comparison_branch_id=ids["main"],
        ).status_code
        == 303
    )
    with Session(app.state.engine) as s:
        repo = s.get(Repository, ids["repo"])
        branch = s.get(Branch, ids["feature"])
        assert (
            repo.sync_interval_seconds == 600
            and "stable/" in repo.role_prefixes["release"]
        )
        assert branch.source_candidates == [] and branch.config.active_release
        assert branch.config.manual_source_branch_id == ids["main"]
        assert s.query(Comparison).count() == 1


def test_deleted_targets_and_duplicate_plan_evidence(client, app, pages):
    with Session(app.state.engine) as s:
        target = Branch(
            repository_id=pages["repo"], name="release/deleted", deleted=True
        )
        s.add(target)
        s.flush()
        s.add(
            BranchConfig(
                branch_id=pages["feature"],
                manual_targets=[target.id],
                main_comparison_branch_id=target.id,
            )
        )
        s.commit()
    page = client.get(f"/repositories/{pages['repo']}/branches/{pages['feature']}")
    assert "release/deleted" in page.text and "目标当前不存在" in page.text
    assert "人工指定比较对象" in page.text


def test_repository_pr_list_covers_incoming_forks(client, app, pages):
    with Session(app.state.engine) as s:
        s.add(
            PullRequest(
                connection_id=pages["connection"],
                repository_id=pages["repo"],
                external_id="fork-pr",
                title="fork contribution",
                web_url="https://evil.example/fake",
                state="open",
                source_external_repo_id="fork-999",
                source_branch="feature/login",
                target_repository_id=pages["repo"],
                target_branch="main",
            )
        )
        s.commit()
    page = client.get(f"/repositories/{pages['repo']}/pull-requests")
    assert page.status_code == 200 and "fork-999" in page.text
    assert 'href="https://evil.example' not in page.text
    assert (
        "fork contribution"
        not in client.get(
            f"/repositories/{pages['repo']}/branches/{pages['feature']}"
        ).text
    )


def test_duplicate_target_evidence_selects_single_target_and_other_oid_alias_cache(
    client, app, pages
):
    with Session(app.state.engine) as s:
        alias = Branch(
            repository_id=pages["repo"], name="release/alias", head_oid="a" * 40
        )
        s.add(alias)
        s.flush()
        s.add(BranchConfig(branch_id=pages["feature"], manual_targets=[alias.id]))
        s.add(
            PullRequest(
                connection_id=pages["connection"],
                repository_id=pages["repo"],
                external_id="same-target",
                title="open target",
                web_url="https://github.com/team/api/pull/2",
                state="open",
                source_repository_id=pages["repo"],
                source_branch="feature/login",
                target_repository_id=pages["repo"],
                target_branch=alias.name,
            )
        )
        s.commit()
    response = client.get(f"/repositories/{pages['repo']}/branches/{pages['feature']}")
    assert "唯一计划目标" in response.text and "未全部包含" in response.text
    assert "人工指定" in response.text and "开放 PR/MR 提供的计划" in response.text


def test_cross_fork_identity_visible_without_local_counts(client, app, pages):
    with Session(app.state.engine) as s:
        s.add(
            PullRequest(
                connection_id=pages["connection"],
                repository_id=pages["repo"],
                external_id="outgoing",
                title="outgoing",
                web_url="https://github.com/team/api/pull/3",
                state="open",
                source_repository_id=pages["repo"],
                source_branch="feature/login",
                target_external_repo_id="fork-888",
                target_branch="main",
            )
        )
        s.commit()
    response = client.get(f"/repositories/{pages['repo']}/branches/{pages['feature']}")
    assert "外部仓库 #fork-888" in response.text
    assert "唯一计划目标" in response.text and "未分析" in response.text


def test_busy_unique_commit_retry_preserves_selected_target(
    client, app, pages, monkeypatch
):
    from app.sync import RepositoryBusy

    def busy(*args, **kwargs):
        raise RepositoryBusy()

    monkeypatch.setattr("app.routes.repositories.read_unique_commits", busy)
    response = client.get(
        f"/repositories/{pages['repo']}/branches/{pages['feature']}?target_id={pages['main']}"
    )
    assert "稍后重试" in response.text and f'target_id={pages["main"]}' in response.text


def test_admin_invalid_form_renders_useful_page_without_secrets(
    client, app, admin_pages
):
    response = post_form(
        client,
        "/admin/connections",
        name="bad",
        kind="github",
        base_url="http://insecure.test",
        token="never-render-me",
    )
    assert response.status_code == 400
    assert "text/html" in response.headers["content-type"] and "HTTPS" in response.text
    assert "never-render-me" not in response.text


def test_repository_pr_navigation_and_shared_repo_does_not_expose_hidden_project(
    client, app, pages
):
    with Session(app.state.engine) as s:
        s.add(
            ProjectRepository(project_id=pages["private"], repository_id=pages["repo"])
        )
        s.commit()
    response = client.get(f"/repositories/{pages['repo']}")
    assert f'/repositories/{pages["repo"]}/pull-requests' in response.text
    assert "机密项目" not in response.text


def test_repository_pagination_and_cross_repository_filter(client, app, pages):
    with Session(app.state.engine) as s:
        for i in range(55):
            repo = Repository(
                connection_id=pages["connection"],
                external_id=f"p{i}",
                name=f"z{i:03}",
                full_name=f"team/z{i:03}",
                web_url="https://github.com/team/x",
                clone_url="https://github.com/team/x.git",
                default_branch="main",
                git_synced_at=utc_now(),
            )
            s.add(repo)
            s.flush()
            s.add(ProjectRepository(project_id=pages["project"], repository_id=repo.id))
            s.add(
                Branch(repository_id=repo.id, name="release/visible", head_oid="f" * 40)
            )
        s.commit()
    first = client.get(f"/projects/{pages['project']}")
    second = client.get(f"/projects/{pages['project']}?page=2")
    assert first.text.count('class="repository-row"') == 50
    assert second.text.count('class="repository-row"') == 6
    assert (
        client.get(f"/projects/{pages['project']}?role=feature&q=login").text.count(
            'class="repository-row"'
        )
        == 1
    )


@pytest.mark.parametrize("fails", [False, True])
def test_connection_check_cannot_publish_over_credential_rotation(
    client, app, admin_pages, fails
):
    cid = admin_pages["connection"]
    replacement = app.state.settings.encrypt_token("replacement-secret")

    class Interleave(ProviderFixture):
        def check(self):
            with Session(app.state.engine) as s:
                connection = s.get(PlatformConnection, cid)
                connection.encrypted_token = replacement
                connection.auth_disabled = True
                connection.last_checked_at = utc_now()
                s.commit()
            if fails:
                raise RuntimeError("old secret")
            return super().check()

    app.state.provider_factory = Interleave
    response = post_form(client, f"/admin/connections/{cid}/check")
    assert response.status_code == 409
    with Session(app.state.engine) as s:
        connection = s.get(PlatformConnection, cid)
        assert connection.auth_disabled and connection.encrypted_token == replacement


def test_import_cannot_publish_over_connection_rotation(client, app, admin_pages):
    cid = admin_pages["connection"]

    class Interleave(ProviderFixture):
        def repository(self, external_id):
            with Session(app.state.engine) as s:
                connection = s.get(PlatformConnection, cid)
                connection.encrypted_token = app.state.settings.encrypt_token(
                    "replacement"
                )
                connection.last_checked_at = utc_now()
                s.commit()
            return super().repository(external_id)

    app.state.provider_factory = Interleave
    response = post_form(
        client,
        "/admin/repositories/import",
        connection_id=cid,
        project_id=admin_pages["project"],
        external_ids=["20"],
    )
    assert response.status_code == 409
    with Session(app.state.engine) as s:
        assert (
            s.scalar(select(Repository).where(Repository.external_id == "20")) is None
        )


def test_member_comparison_is_audited_and_rejects_arbitrary_oids(client, app, pages):
    path = f"/repositories/{pages['repo']}/branches/{pages['feature']}/compare"
    assert post_form(client, path, target_id="f" * 40).status_code == 404
    assert post_form(client, path, target_id=pages["main"]).status_code == 303
    with Session(app.state.engine) as s:
        assert (
            s.scalar(
                select(AuditEvent).where(AuditEvent.action == "comparison.request")
            )
            is not None
        )


def test_zero_branch_id_is_not_coerced_to_first_branch(client, pages):
    path = f"/repositories/{pages['repo']}/branches/{pages['feature']}/compare"
    assert post_form(client, path, target_id="0").status_code == 404


def test_admin_successful_attach_and_input_validation(client, app, admin_pages):
    ids = admin_pages
    assert (
        post_form(
            client,
            f"/admin/repositories/{ids['repo']}/projects",
            project_id=ids["private"],
            action="attach",
        ).status_code
        == 303
    )
    assert (
        post_form(
            client,
            f"/admin/repositories/{ids['repo']}/projects",
            project_id=ids["private"],
            action="detach",
        ).status_code
        == 303
    )
    assert (
        post_form(
            client, f"/admin/repositories/{ids['repo']}", sync_interval_seconds="zero"
        ).status_code
        == 400
    )
    assert (
        post_form(
            client, f"/admin/repositories/{ids['repo']}/sync", history_since="garbage"
        ).status_code
        == 400
    )
    assert (
        post_form(
            client,
            f"/admin/branches/{ids['feature']}",
            role="feature",
            active_release="on",
        ).status_code
        == 400
    )
    assert (
        post_form(client, "/admin/users", username="weak", password="short").status_code
        == 400
    )


def test_read_pages_never_invoke_provider(client, app, pages):
    def forbidden(*args, **kwargs):
        raise AssertionError("Member read called remote API")

    app.state.provider_factory = forbidden
    for path in (
        "/",
        f"/projects/{pages['project']}",
        f"/repositories/{pages['repo']}",
        f"/repositories/{pages['repo']}/branches",
        f"/repositories/{pages['repo']}/branches/{pages['feature']}",
        f"/repositories/{pages['repo']}/pull-requests",
    ):
        assert client.get(path).status_code == 200


def test_branch_names_and_source_candidates_are_escaped(client, app, pages):
    with Session(app.state.engine) as s:
        branch = s.get(Branch, pages["feature"])
        branch.name = "<img src=x onerror=alert(1)>"
        branch.source_candidates = [
            dict(
                name="<script>source</script>",
                kind="inferred",
                ahead=2,
                behind=5,
                merge_bases=["c" * 40],
            )
        ]
        s.commit()
    response = client.get(f"/repositories/{pages['repo']}/branches/{pages['feature']}")
    assert "<img src=x" not in response.text and "&lt;img" in response.text
    assert (
        "<script>source" not in response.text
        and "&lt;script&gt;source" in response.text
    )
    assert "候选分支独有 2" in response.text and "当前分支独有 5" in response.text


def test_pending_snapshot_and_disabled_credentials_remain_explicit(client, app, pages):
    with Session(app.state.engine) as s:
        repo = s.get(Repository, pages["repo"])
        repo.requested_generation = repo.snapshot_generation + 1
        s.get(PlatformConnection, pages["connection"]).auth_disabled = True
        s.commit()
    response = client.get(f"/repositories/{pages['repo']}/branches")
    assert "仍显示上次快照" in response.text
    assert "连接凭据待恢复" in response.text


def test_pr_detail_preserves_platform_identities_and_unknown_merge_time(
    client, app, pages
):
    with Session(app.state.engine) as s:
        pr = PullRequest(
            connection_id=pages["connection"],
            repository_id=pages["repo"],
            external_id="identity-test",
            title="identity",
            web_url="https://github.com/team/api/pull/4",
            state="open",
            source_external_repo_id="external-source-2",
            target_external_repo_id="external-target-3",
        )
        s.add(pr)
        s.commit()
        pid = pr.id
    response = client.get(f"/repositories/{pages['repo']}/pull-requests/{pid}")
    assert "external-source-2" in response.text and "external-target-3" in response.text
    assert "合并时间 未知" in response.text


@pytest.mark.parametrize(
    ("scenario", "expected_git", "expected_api", "summary_label"),
    [
        ("aged", "已过期", "已过期", "数据过期"),
        ("api_aged", "正常", "已过期", "数据过期"),
        ("git_aged", "已过期", "正常", "数据过期"),
        ("api_missing", "正常", "尚未同步", "等待首次同步"),
        ("new", "正在导入", "尚未同步", "等待首次同步"),
        ("pending", "待更新", "待更新", "待发布更新"),
        ("disabled", "正常", "正常", "连接已暂停"),
    ],
)
def test_sync_age_and_lifecycle_are_consistent_across_pages(
    client, app, pages, scenario, expected_git, expected_api, summary_label
):
    now = utc_now()
    with Session(app.state.engine) as s:
        repo = s.get(Repository, pages["repo"])
        repo.git_synced_at = repo.api_synced_at = now
        repo.snapshot_generation = repo.api_generation = repo.requested_generation = 1
        repo.sync_interval_seconds = 300
        if scenario in ("aged", "api_aged", "git_aged"):
            if scenario != "git_aged":
                repo.api_synced_at = now - timedelta(days=30)
            if scenario != "api_aged":
                repo.git_synced_at = now - timedelta(days=30)
        elif scenario == "api_missing":
            repo.api_synced_at = None
        elif scenario == "new":
            repo.git_synced_at = repo.api_synced_at = None
            repo.snapshot_generation = repo.api_generation = 0
        elif scenario == "pending":
            repo.requested_generation = 2
        else:
            s.get(PlatformConnection, pages["connection"]).auth_disabled = True
        s.commit()
    fragment = client.get(f"/repositories/{pages['repo']}/branches").text
    assert f"Git 数据：{expected_git}" in fragment
    assert f"API 数据：{expected_api}" in fragment
    assert "600 秒" in fragment and "同步间隔的 2 倍，最少 60 秒" in fragment
    for path in ("/", f"/projects/{pages['project']}"):
        html = client.get(path).text
        assert 'data-sync-attention="1"' in html
        assert summary_label in html


def test_recent_complete_snapshots_are_not_reported_as_aged(client, app, pages):
    with Session(app.state.engine) as s:
        repo = s.get(Repository, pages["repo"])
        repo.git_synced_at = repo.api_synced_at = utc_now()
        repo.snapshot_generation = repo.api_generation = repo.requested_generation = 1
        s.commit()
    html = client.get("/").text
    assert 'data-sync-attention="0"' in html and "同步正常" in html
    assert (
        "Git 数据：正常" in client.get(f"/repositories/{pages['repo']}/branches").text
    )


def test_sync_status_batches_connections_and_shows_threshold_floor(client, app, pages):
    from sqlalchemy import event

    with Session(app.state.engine) as s:
        s.get(Repository, pages["repo"]).sync_interval_seconds = 30
        for index in range(12):
            connection = PlatformConnection(
                name=f"instance-{index}",
                kind="gitlab",
                base_url=f"https://git-{index}.example.com",
            )
            s.add(connection)
            s.flush()
            repo = Repository(
                connection_id=connection.id,
                external_id="1",
                name=f"repo-{index}",
                full_name=f"team/repo-{index}",
                web_url=f"https://git-{index}.example.com/team/repo",
                clone_url=f"https://git-{index}.example.com/team/repo.git",
            )
            s.add(repo)
            s.flush()
            s.add(ProjectRepository(project_id=pages["project"], repository_id=repo.id))
        s.commit()
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(app.state.engine, "before_cursor_execute", observe)
    try:
        assert client.get("/").status_code == 200
        assert len(statements) <= 6
        statements.clear()
        assert client.get(f"/projects/{pages['project']}").status_code == 200
        assert len(statements) <= 10
    finally:
        event.remove(app.state.engine, "before_cursor_execute", observe)
    assert (
        "60 秒（同步间隔的 2 倍，最少 60 秒）"
        in client.get(f"/repositories/{pages['repo']}/branches").text
    )


def test_connection_edit_serializes_against_first_import(
    client, app, admin_pages, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    from fastapi.testclient import TestClient

    with Session(app.state.engine) as s:
        connection = PlatformConnection(
            name="first-import", kind="github", base_url="https://github.com"
        )
        s.add(connection)
        s.commit()
        cid = connection.id
    encrypt_entered, release_edit, import_metadata_read = Event(), Event(), Event()
    original_encrypt = type(app.state.settings).encrypt_token

    def paused_encrypt(settings, token):
        if token == "rotated-token":
            encrypt_entered.set()
            assert release_edit.wait(5), "test did not release connection editor"
        return original_encrypt(settings, token)

    class ObservedProvider(ProviderFixture):
        def repository(self, external_id):
            record = super().repository(external_id)
            import_metadata_read.set()
            return record

    monkeypatch.setattr(type(app.state.settings), "encrypt_token", paused_encrypt)
    app.state.provider_factory = ObservedProvider
    csrf = csrf_from(client.get("/"))
    cookies = dict(client.cookies)

    def post(path, fields):
        with TestClient(app, cookies=cookies) as separate_client:
            return separate_client.post(
                path, data={**fields, "csrf_token": csrf}, follow_redirects=False
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        edit = pool.submit(
            post,
            f"/admin/connections/{cid}",
            dict(
                name="rotated",
                kind="github",
                base_url="https://new.example",
                token="rotated-token",
            ),
        )
        try:
            assert encrypt_entered.wait(5)
            importing = pool.submit(
                post,
                "/admin/repositories/import",
                dict(
                    connection_id=cid,
                    project_id=admin_pages["project"],
                    external_ids=["20"],
                ),
            )
            assert import_metadata_read.wait(5)
            try:
                importing.result(timeout=1)
            except TimeoutError:
                pass  # Correct implementation waits for the editor's connection lock.
        finally:
            release_edit.set()
        assert edit.result(timeout=5).status_code == 303
        assert importing.result(timeout=5).status_code == 409
    with Session(app.state.engine) as s:
        assert s.get(PlatformConnection, cid).base_url == "https://new.example"
        assert (
            s.scalar(select(Repository.id).where(Repository.connection_id == cid))
            is None
        )


def test_connection_edit_rechecks_imported_repositories_after_lock_wait(
    client, app, admin_pages, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from fastapi.testclient import TestClient
    from sqlalchemy import update

    with Session(app.state.engine) as s:
        connection = PlatformConnection(
            name="locked-import", kind="github", base_url="https://github.com"
        )
        s.add(connection)
        s.commit()
        cid = connection.id
    encrypt_reached = Event()
    original_encrypt = type(app.state.settings).encrypt_token

    def observed_encrypt(settings, token):
        if token == "rotated-token":
            encrypt_reached.set()
        return original_encrypt(settings, token)

    monkeypatch.setattr(type(app.state.settings), "encrypt_token", observed_encrypt)
    csrf = csrf_from(client.get("/"))
    cookies = dict(client.cookies)

    def save():
        with TestClient(app, cookies=cookies) as separate_client:
            return separate_client.post(
                f"/admin/connections/{cid}",
                data=dict(
                    name="rotated",
                    kind="github",
                    base_url="https://new.example",
                    token="rotated-token",
                    csrf_token=csrf,
                ),
                follow_redirects=False,
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(app.state.engine) as importing:
            # Match the import publication transaction: connection locked before
            # the first repository insert, then published together on commit.
            importing.execute(
                update(PlatformConnection)
                .where(PlatformConnection.id == cid)
                .values(auth_disabled=PlatformConnection.auth_disabled)
            )
            importing.add(
                Repository(
                    connection_id=cid,
                    external_id="20",
                    name="repo",
                    full_name="team/repo",
                    web_url="https://github.com/team/repo",
                    clone_url="https://github.com/team/repo.git",
                )
            )
            importing.flush()
            editing = pool.submit(save)
            encrypt_reached.wait(
                1
            )  # Before the fix, the stale has_repos check reaches encryption.
            importing.commit()
        assert editing.result(timeout=5).status_code == 400
    with Session(app.state.engine) as s:
        assert s.get(PlatformConnection, cid).base_url == "https://github.com"
        assert (
            s.scalar(
                select(Repository).where(Repository.connection_id == cid)
            ).clone_url
            == "https://github.com/team/repo.git"
        )
