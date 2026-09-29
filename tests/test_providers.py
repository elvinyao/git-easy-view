from datetime import datetime, timezone
from importlib import import_module

import httpx
import pytest

from app.models import PlatformConnection


def provider(kind, base_url, handler, token="secret", **connection_kwargs):
    module = import_module("app.providers")
    connection = PlatformConnection(name="test", kind=kind, base_url=base_url, **connection_kwargs)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return module.get_provider(connection, token, client=client)


def gh_repo(id=1, name="org/repo", host="github.com", prefix=""):
    return {"id": id, "full_name": name, "html_url": f"https://{host}{prefix}/{name}",
            "clone_url": f"https://{host}{prefix}/{name}.git", "default_branch": "main"}


def gl_repo(id=1, name="team/sub/repo", host="gitlab.com"):
    return {"id": id, "path_with_namespace": name, "web_url": f"https://{host}/{name}",
            "http_url_to_repo": f"https://{host}/{name}.git", "default_branch": "main"}


def test_github_public_and_enterprise_api_paths_and_lookup():
    for root, api in (("https://github.com/", "https://api.github.com"),
                      ("https://git.example/corp/", "https://git.example/corp/api/v3")):
        seen = []

        def respond(req):
            seen.append((str(req.url), req.headers.get("authorization")))
            host = "github.com" if "api.github" in api else "git.example"
            return httpx.Response(200, json=gh_repo(host=host, prefix="" if host == "github.com" else "/corp"))

        p = provider("github", root, respond)
        repo = p.repository("1", full_name="org/repo")
        assert repo.external_id == "1"
        assert seen == [(f"{api}/repos/org/repo", "Bearer secret")]


def test_github_all_pages_and_pr_identity_fork_and_merge_evidence():
    seen = []

    def respond(req):
        seen.append((req.url.path, dict(req.url.params)))
        if req.url.path == "/user/repos":
            if req.url.params.get("page") == "2":
                return httpx.Response(200, json=[gh_repo(2, "org/second")])
            return httpx.Response(200, headers={"Link": '<https://api.github.com/user/repos?page=2>; rel="next"'}, json=[gh_repo()])
        if req.url.path == "/repos/org/repo" and "pulls" not in req.url.path:
            return httpx.Response(200, json=gh_repo())
        if req.url.path == "/repos/org/repo/pulls":
            if req.url.params["state"] == "open":
                if req.url.params.get("page") == "2":
                    return httpx.Response(200, json=[{"id": 102, "number": 9, "title": "missing fork", "html_url": "https://github.com/org/repo/pull/9", "state": "open", "head": {"ref": "work", "sha": "def", "repo": None}, "base": {"ref": "main", "repo": {"id": 1}}, "updated_at": "2024-01-01T00:00:00Z"}])
                return httpx.Response(200, headers={"Link": '<https://api.github.com/repos/org/repo/pulls?state=open&page=2>; rel="next"'}, json=[{"id": 101, "number": 8, "title": "fork", "html_url": "https://github.com/org/repo/pull/8", "state": "open", "merge_commit_sha": "preview", "head": {"ref": "work", "sha": "abc", "repo": {"id": 99}}, "base": {"ref": "main", "repo": {"id": 1}}, "updated_at": "2025-01-01T00:00:00Z"}])
            return httpx.Response(200, json=[{"id": 103, "number": 10, "title": "merged", "html_url": "https://github.com/org/repo/pull/10", "state": "closed", "merged_at": "2025-01-02T00:00:00Z", "merge_commit_sha": "real", "head": {"ref": "work", "repo": {"id": 99}}, "base": {"ref": "main", "repo": {"id": 1}}, "updated_at": "2025-01-02T00:00:00Z"}])
        raise AssertionError(req.url)

    p = provider("github", "https://github.com", respond)
    assert [r.external_id for r in p.repositories()] == ["1", "2"]
    prs = p.pull_requests("1", datetime(2024, 12, 1, tzinfo=timezone.utc), full_name="org/repo")
    assert [pr.external_id for pr in prs] == ["101", "102", "103"]
    assert prs[0].source_repo_id == "99" and prs[0].merge_oid is None
    assert prs[1].source_repo_id is None and prs[1].source_branch == "work"
    assert prs[2].state == "merged" and prs[2].merge_oid == "real"
    assert any(q.get("state") == "closed" and q.get("sort") == "updated" and q.get("direction") == "desc" for _, q in seen)


def test_gitlab_subgroup_lookup_pages_and_mr_scope():
    seen = []

    def respond(req):
        seen.append((req.url.path, dict(req.url.params)))
        if req.url.path == "/api/v4/projects" and req.url.params.get("page") != "2":
            return httpx.Response(200, headers={"X-Next-Page": "2"}, json=[gl_repo()])
        if req.url.path == "/api/v4/projects" and req.url.params.get("page") == "2":
            return httpx.Response(200, json=[gl_repo(2, "team/sub/other")])
        if req.url.raw_path.split(b"?")[0] == b"/api/v4/projects/team%2Fsub%2Frepo":
            return httpx.Response(200, json=gl_repo())
        if req.url.path == "/api/v4/projects/1/merge_requests":
            state = req.url.params.get("state")
            if state == "opened":
                return httpx.Response(200, json=[{"id": 301, "iid": 4, "title": "fork", "web_url": "https://gitlab.com/team/sub/repo/-/merge_requests/4", "state": "opened", "source_project_id": 8, "target_project_id": 1, "source_branch": "topic", "target_branch": "main", "sha": "abc", "updated_at": "2020-01-01T00:00:00Z"}])
            if state == "locked":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"id": 302, "iid": 5, "title": "merged", "web_url": "https://gitlab.com/team/sub/repo/-/merge_requests/5", "state": "merged", "source_project_id": None, "target_project_id": 1, "source_branch": "gone", "target_branch": "main", "merge_commit_sha": "merge", "updated_at": "2025-01-01T00:00:00Z", "merged_at": "2025-01-01T00:00:00Z"}])
        raise AssertionError(req.url)

    p = provider("gitlab", "https://gitlab.com/", respond)
    assert [r.external_id for r in p.repositories()] == ["1", "2"]
    assert p.repository("team/sub/repo").external_id == "1"
    prs = p.pull_requests("1", datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert [pr.external_id for pr in prs] == ["301", "302"]
    assert prs[0].source_repo_id == "8" and prs[0].source_oid == "abc"
    assert prs[1].source_repo_id is None and prs[1].merge_oid == "merge"
    assert any(q.get("state") == "opened" and "updated_after" not in q for _, q in seen)
    assert any(q.get("updated_after") for _, q in seen)


@pytest.mark.parametrize("kind,root,path", [("github", "https://github.com", "/user/repos"), ("gitlab", "https://gitlab.com", "/api/v4/projects")])
def test_interrupted_pagination_and_bad_shapes_fail(kind, root, path):
    errors = import_module("app.providers.base")
    for mode in ("next_fails", "malformed"):
        def respond(req):
            if mode == "malformed":
                return httpx.Response(200, json=[{"name": "identity missing"}])
            if req.url.params.get("page") == "2":
                return httpx.Response(503, text="secret response")
            headers = {"Link": f'<https://{req.url.host}{path}?page=2>; rel="next"'} if kind == "github" else {"X-Next-Page": "2"}
            return httpx.Response(200, headers=headers, json=[gh_repo() if kind == "github" else gl_repo()])
        with pytest.raises(errors.ProviderError) as exc:
            provider(kind, root, respond).repositories()
        assert "secret response" not in str(exc.value)


def test_auth_rate_limit_and_redirect_are_typed_and_do_not_leak_token():
    errors = import_module("app.providers.base")
    for status, headers, error_type in ((401, {}, errors.AuthenticationError), (429, {"Retry-After": "12"}, errors.RateLimitError), (302, {"Location": "https://evil.example/steal"}, errors.ProviderError)):
        seen = []
        def respond(req):
            seen.append(str(req.url))
            return httpx.Response(status, headers=headers, text="secret")
        with pytest.raises(error_type) as exc:
            provider("github", "https://github.com", respond).repositories()
        assert len(seen) == 1
        assert "secret" not in str(exc.value)
        if status == 429:
            assert exc.value.retry_after == 12


def test_cross_origin_and_path_escape_links_are_rejected():
    errors = import_module("app.providers.base")
    for url in ("https://evil.example/steal", "//evil.example/steal", "https://api.github.com/evil", "https://user:pass@api.github.com/user/repos?page=2"):
        seen = []
        def respond(req):
            seen.append(req.url.host)
            return httpx.Response(200, headers={"Link": f'<{url}>; rel="next"'}, json=[gh_repo()])
        with pytest.raises(errors.ProviderError):
            provider("github", "https://github.com", respond).repositories()
        assert seen == ["api.github.com"]


def test_repository_urls_must_match_configured_web_instance():
    errors = import_module("app.providers.base")
    for bad in (dict(gh_repo(), clone_url="https://evil.example/org/repo.git"), dict(gh_repo(), html_url="https://evil.example/org/repo")):
        with pytest.raises(errors.ProviderError):
            provider("github", "https://github.com", lambda req: httpx.Response(200, json=[bad])).repositories()


def test_https_required_and_page_limit_fails_explicitly():
    errors = import_module("app.providers.base")
    with pytest.raises(errors.ProviderError):
        provider("github", "http://github.com", lambda req: httpx.Response(200, json=[]))
    def respond(req):
        return httpx.Response(200, headers={"Link": f'<https://api.github.com/user/repos?page={int(req.url.params.get("page", "1")) + 1}>; rel="next"'}, json=[gh_repo()])
    p = provider("github", "https://github.com", respond)
    p.max_pages = 2
    with pytest.raises(errors.ProviderError, match="page limit"):
        p.repositories()


def test_gitlab_inconsistent_page_headers_fail_instead_of_publishing_partial_list():
    errors = import_module("app.providers.base")
    def respond(req):
        return httpx.Response(200, headers={"X-Page": "1", "X-Total-Pages": "3", "X-Next-Page": ""}, json=[gl_repo()])
    with pytest.raises(errors.ProviderError, match="pagination"):
        provider("gitlab", "https://gitlab.com", respond).repositories()


def test_check_uses_identity_endpoint_without_version_dependency():
    for kind, root, path in (("github", "https://github.com", "/user"),
                             ("gitlab", "https://gitlab.example/corp", "/corp/api/v4/user")):
        seen = []
        def respond(req):
            seen.append(req.url.path)
            if req.url.path.endswith("/version"):
                return httpx.Response(404, json={"message": "unsupported"})
            return httpx.Response(200, json={"id": 77, "login": "alice", "username": "alice"})
        result = provider(kind, root, respond).check()
        assert result["external_user_id"] == "77"
        assert result["version"] is None
        assert seen == ([path] if kind == "github" else [path, "/corp/api/v4/version"])


def test_company_ca_bundle_is_passed_to_http_client(monkeypatch):
    created = []
    original = httpx.Client
    def capture(*args, **kwargs):
        created.append(kwargs)
        return original(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[])), **kwargs)
    monkeypatch.setattr(httpx, "Client", capture)
    connection = PlatformConnection(name="corp", kind="gitlab", base_url="https://gitlab.example", ca_bundle_path="/company/roots.pem")
    import_module("app.providers").get_provider(connection, "secret")
    assert created[0]["verify"] == "/company/roots.pem"
    assert created[0]["timeout"] > 0


def test_repository_detail_rejects_stale_name_with_same_id():
    errors = import_module("app.providers.base")
    with pytest.raises(errors.ProviderError):
        provider("github", "https://github.com", lambda req: httpx.Response(200, json=gh_repo(1, "other/repo"))).repository("1", full_name="org/repo")


def test_pull_request_fetch_validates_selected_repository_id():
    errors = import_module("app.providers.base")
    seen = []
    def respond(req):
        seen.append(req.url.path)
        return httpx.Response(200, json=gh_repo(2))
    with pytest.raises(errors.ProviderError):
        provider("github", "https://github.com", respond).pull_requests("1", None, full_name="org/repo")
    assert seen == ["/repos/org/repo"]


def test_secondary_rate_limit_and_replacement_token():
    errors = import_module("app.providers.base")
    seen = []
    def respond(req):
        seen.append(req.headers.get("authorization"))
        return httpx.Response(403, headers={"Retry-After": "30"}, text="hidden")
    with pytest.raises(errors.RateLimitError) as exc:
        provider("github", "https://github.com", respond, auth_disabled=True).check()
    assert exc.value.retry_after == 30
    assert seen == ["Bearer secret"]


def test_gitlab_locked_merge_request_is_not_claimed_closed_or_merged():
    def respond(req):
        if req.url.params.get("state") != "locked":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[{"id": 77, "title": "locked", "web_url": "https://gitlab.com/team/repo/-/merge_requests/1", "state": "locked", "source_project_id": 2, "target_project_id": 1, "updated_at": "2020-01-01T00:00:00Z"}])
    prs = provider("gitlab", "https://gitlab.com", respond).pull_requests("1", datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert len(prs) == 1
    assert prs[0].state == "open" and prs[0].merge_oid is None


def test_provider_owns_only_its_created_client():
    provided = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[])))
    p = import_module("app.providers").get_provider(PlatformConnection(name="test", kind="github", base_url="https://github.com"), "secret", provided)
    p.close()
    assert not provided.is_closed
    provided.close()


@pytest.mark.parametrize("kind,root", [("github", "https://github.com"), ("gitlab", "https://gitlab.com")])
def test_full_page_without_pagination_headers_fetches_next_page(kind, root):
    seen = []
    def respond(req):
        seen.append(req.url.params.get("page"))
        if req.url.params.get("page") == "2":
            item = gh_repo(101, "org/next") if kind == "github" else gl_repo(101, "team/next")
            return httpx.Response(200, json=[item])
        items = [gh_repo(i, f"org/repo{i}") if kind == "github" else gl_repo(i, f"team/repo{i}") for i in range(1, 101)]
        return httpx.Response(200, json=items)
    assert len(provider(kind, root, respond).repositories()) == 101
    assert seen == [None, "2"]


def test_github_secondary_limit_body_without_headers_is_typed_and_sanitized():
    errors = import_module("app.providers.base")
    def respond(req):
        return httpx.Response(403, headers={"X-RateLimit-Remaining": "42"},
                              json={"message": "You have exceeded a secondary rate limit. secret-detail"})
    with pytest.raises(errors.RateLimitError) as exc:
        provider("github", "https://github.com", respond).check()
    assert exc.value.retry_after >= 60
    assert "secret-detail" not in str(exc.value)


def test_check_reports_available_version_without_requiring_it():
    def github(req):
        return httpx.Response(200, headers={"X-GitHub-Enterprise-Version": "3.15.0"}, json={"id": 1})
    result = provider("github", "https://gh.example", github).check()
    assert result["version"] == "3.15.0"
    assert "capabilities" in result

    def gitlab(req):
        if req.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "17.8.0", "revision": "abc"})
        return httpx.Response(200, json={"id": 1})
    assert provider("gitlab", "https://gitlab.example", gitlab).check()["version"] == "17.8.0"
    def gitlab_no_version(req):
        if req.url.path.endswith("/version"):
            return httpx.Response(403, json={"message": "forbidden"})
        return httpx.Response(200, json={"id": 1})
    assert provider("gitlab", "https://gitlab.example", gitlab_no_version).check()["version"] is None


def test_github_closed_history_stops_after_ordered_cutoff():
    seen = []
    def respond(req):
        if req.url.path == "/repos/org/repo":
            return httpx.Response(200, json=gh_repo())
        state = req.url.params["state"]
        seen.append((state, req.url.params.get("page")))
        if state == "open":
            return httpx.Response(200, json=[])
        if req.url.params.get("page"):
            raise AssertionError("older history should not be fetched")
        return httpx.Response(200, headers={"Link": '<https://api.github.com/repos/org/repo/pulls?state=closed&page=2>; rel="next"'},
                              json=[{"id": 1, "title": "old", "html_url": "https://github.com/org/repo/pull/1", "state": "closed", "head": {}, "base": {}, "updated_at": "2020-01-01T00:00:00Z"}])
    p = provider("github", "https://github.com", respond)
    p.max_pages = 2
    assert p.pull_requests("1", datetime(2025, 1, 1, tzinfo=timezone.utc), full_name="org/repo") == []
    assert seen == [("open", None), ("closed", None)]


def test_github_closed_history_missing_time_keeps_paging():
    seen = []
    def respond(req):
        if req.url.path == "/repos/org/repo":
            return httpx.Response(200, json=gh_repo())
        state = req.url.params["state"]
        if state == "open":
            return httpx.Response(200, json=[])
        page = req.url.params.get("page", "1")
        seen.append(page)
        item = {"id": int(page), "title": "unknown update", "html_url": f"https://github.com/org/repo/pull/{page}", "state": "closed", "head": {}, "base": {}}
        headers = {"Link": '<https://api.github.com/repos/org/repo/pulls?state=closed&page=2>; rel="next"'} if page == "1" else {}
        return httpx.Response(200, headers=headers, json=[item])
    prs = provider("github", "https://github.com", respond).pull_requests("1", datetime(2025, 1, 1, tzinfo=timezone.utc), full_name="org/repo")
    assert [pr.external_id for pr in prs] == ["1", "2"]
    assert seen == ["1", "2"]


def test_github_closed_history_disordered_page_disables_cutoff():
    seen = []
    def pr(id, updated):
        return {"id": id, "title": "history", "html_url": f"https://github.com/org/repo/pull/{id}",
                "state": "closed", "head": {}, "base": {}, "updated_at": updated}
    def respond(req):
        if req.url.path == "/repos/org/repo":
            return httpx.Response(200, json=gh_repo())
        if req.url.params["state"] == "open":
            return httpx.Response(200, json=[])
        page = req.url.params.get("page", "1")
        seen.append(page)
        if page == "1":
            return httpx.Response(200, headers={"Link": '<https://api.github.com/repos/org/repo/pulls?state=closed&page=2>; rel="next"'},
                                  json=[pr(1, "2020-01-01T00:00:00Z"), pr(2, "2026-01-01T00:00:00Z")])
        return httpx.Response(200, json=[pr(3, "2025-06-01T00:00:00Z")])
    prs = provider("github", "https://github.com", respond).pull_requests("1", datetime(2025, 1, 1, tzinfo=timezone.utc), full_name="org/repo")
    assert [pr.external_id for pr in prs] == ["2", "3"]
    assert seen == ["1", "2"]


def test_encoded_path_traversal_is_rejected_for_remote_urls():
    errors = import_module("app.providers.base")
    for path in ("/corp/%2e%2e/outside.git", "/corp/%252e%252e/outside.git"):
        with pytest.raises(errors.ProviderError):
            errors.validate_remote_url("https://git.example" + path, "https://git.example/corp")
    assert errors.validate_remote_url("https://git.example/corp/group%2Fsub/repo.git", "https://git.example/corp")
