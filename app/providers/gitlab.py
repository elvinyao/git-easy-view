from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

from .base import PR, Repo, Provider, ProviderError, identity, optional_identity, optional_string, required_string, timestamp, validate_remote_url


class GitLabProvider(Provider):
    def check(self) -> dict:
        data = self._json(self._request(self.api_root + "/user"))
        if not isinstance(data, dict) or not data.get("id"):
            raise ProviderError("Provider returned an invalid identity")
        return {"external_user_id": identity(data["id"], "user identity"), "username": data.get("username")}

    def repositories(self) -> list[Repo]:
        result = []
        for page in self._pages("/projects", {"membership": "true", "per_page": 100, "order_by": "id", "sort": "asc"}):
            result.extend(self._repo(item) for item in page)
        return result

    def repository(self, external_id: str, *, full_name: str | None = None) -> Repo:
        identifier = full_name if full_name is not None else external_id
        if not identifier or identifier.startswith("/") or ".." in identifier.split("/"):
            raise ProviderError("Invalid GitLab project identity")
        path = "/projects/" + quote(identifier, safe="")
        data = self._json(self._request(self.api_root + path))
        repo = self._repo(data)
        if full_name is None and external_id.isdecimal() and repo.external_id != external_id:
            raise ProviderError("Provider repository identity changed")
        if full_name is not None and repo.external_id != external_id:
            raise ProviderError("Provider repository identity changed")
        return repo

    def pull_requests(self, repo_external_id: str, since: datetime | None) -> list[PR]:
        path = "/projects/" + quote(identity(repo_external_id, "repository identity"), safe="") + "/merge_requests"
        result: dict[str, PR] = {}
        for state, after in (("opened", None), ("locked", None), ("all", since)):
            params = {"state": state, "per_page": 100, "order_by": "updated_at", "sort": "desc"}
            if after is not None:
                params["updated_after"] = after.astimezone(timezone.utc).isoformat()
            for page in self._pages(path, params):
                for raw in page:
                    pr = self._pr(raw)
                    if state == "opened":
                        if pr.state != "open":
                            raise ProviderError("Provider returned an MR outside requested state")
                        result[pr.external_id] = pr
                    elif state == "locked":
                        if raw.get("state") != "locked":
                            raise ProviderError("Provider returned an MR outside requested state")
                        result[pr.external_id] = pr
                    elif pr.state != "open" and (since is None or pr.updated_at is None or pr.updated_at >= since):
                        result[pr.external_id] = pr
        return list(result.values())

    def _repo(self, raw) -> Repo:
        if not isinstance(raw, dict):
            raise ProviderError("Provider returned an invalid repository")
        external_id = identity(raw.get("id"), "repository identity")
        name = required_string(raw.get("path_with_namespace"), "repository name")
        web = validate_remote_url(required_string(raw.get("web_url"), "repository web URL"), self.web_root)
        clone = validate_remote_url(required_string(raw.get("http_url_to_repo"), "repository clone URL"), self.web_root)
        if urlsplit(web).path + ".git" != urlsplit(clone).path:
            raise ProviderError("Provider repository URLs disagree")
        return Repo(external_id, name, web, clone, optional_string(raw.get("default_branch")))

    def _pr(self, raw) -> PR:
        if not isinstance(raw, dict):
            raise ProviderError("Provider returned an invalid merge request")
        external_id = identity(raw.get("id"), "merge request identity")
        title = required_string(raw.get("title"), "merge request title")
        web = validate_remote_url(required_string(raw.get("web_url"), "merge request URL"), self.web_root)
        state = raw.get("state")
        if state not in ("opened", "closed", "merged", "locked"):
            raise ProviderError("Provider returned an invalid merge request state")
        if state == "locked":
            state = "open"
        if state == "opened":
            state = "open"
        merged_at = timestamp(raw.get("merged_at")) if state == "merged" else None
        return PR(external_id, title, web, state,
                  optional_identity(raw.get("source_project_id")), optional_string(raw.get("source_branch")),
                  optional_string(raw.get("sha")), optional_identity(raw.get("target_project_id")),
                  optional_string(raw.get("target_branch")),
                  optional_string(raw.get("merge_commit_sha")) if state == "merged" else None,
                  timestamp(raw.get("updated_at")), merged_at)
