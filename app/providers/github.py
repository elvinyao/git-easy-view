from datetime import datetime
from urllib.parse import quote, urlsplit

from .base import PR, Repo, Provider, ProviderError, identity, optional_identity, optional_string, required_string, timestamp, validate_remote_url


class GitHubProvider(Provider):
    def check(self) -> dict:
        response = self._request(self.api_root + "/user")
        data = self._json(response)
        if not isinstance(data, dict) or not data.get("id"):
            raise ProviderError("Provider returned an invalid identity")
        return {"external_user_id": identity(data["id"], "user identity"), "login": data.get("login"),
                "version": response.headers.get("x-github-enterprise-version"), "capabilities": {}}

    def repositories(self) -> list[Repo]:
        result = []
        for page in self._pages("/user/repos", {"per_page": 100, "affiliation": "owner,collaborator,organization_member"}):
            result.extend(self._repo(item) for item in page)
        return result

    def repository(self, external_id: str, *, full_name: str | None = None) -> Repo:
        if full_name is not None:
            parts = full_name.split("/")
            if len(parts) != 2 or not all(parts):
                raise ProviderError("Invalid GitHub repository name")
            path = "/repos/" + "/".join(quote(part, safe="") for part in parts)
        else:
            path = "/repositories/" + quote(identity(external_id, "repository identity"), safe="")
        data = self._json(self._request(self.api_root + path))
        repo = self._repo(data)
        if repo.external_id != str(external_id):
            raise ProviderError("Provider repository identity changed")
        if full_name is not None and repo.full_name != full_name:
            raise ProviderError("Provider repository name changed")
        return repo

    def pull_requests(self, repo_external_id: str, since: datetime | None, *, full_name: str | None = None) -> list[PR]:
        repo = self.repository(repo_external_id, full_name=full_name)
        name = repo.full_name
        parts = name.split("/")
        if len(parts) != 2 or not all(parts):
            raise ProviderError("Invalid GitHub repository name")
        path = "/repos/" + "/".join(quote(part, safe="") for part in parts) + "/pulls"
        result = []
        for state in ("open", "closed"):
            params = {"state": state, "sort": "updated", "direction": "desc", "per_page": 100}
            previous = None
            ordered = True
            for page in self._pages(path, params):
                for raw in page:
                    pr = self._pr(raw)
                    if state == "open" and pr.state != "open":
                        raise ProviderError("Provider returned a PR outside requested state")
                    if state == "closed" and pr.state == "open":
                        raise ProviderError("Provider returned a PR outside requested state")
                    if state == "closed":
                        if pr.updated_at is None or (previous is not None and pr.updated_at > previous):
                            ordered = False
                        previous = pr.updated_at
                    if state == "open" or since is None or pr.updated_at is None or pr.updated_at >= since:
                        result.append(pr)
                if state == "closed" and since is not None and ordered and page and previous is not None and previous < since:
                    break
        return result

    def _repo(self, raw) -> Repo:
        if not isinstance(raw, dict):
            raise ProviderError("Provider returned an invalid repository")
        external_id = identity(raw.get("id"), "repository identity")
        full_name = required_string(raw.get("full_name"), "repository name")
        if len(full_name.split("/")) != 2:
            raise ProviderError("Provider returned an invalid repository name")
        web = validate_remote_url(required_string(raw.get("html_url"), "repository web URL"), self.web_root)
        clone = validate_remote_url(required_string(raw.get("clone_url"), "repository clone URL"), self.web_root)
        if urlsplit(web).path + ".git" != urlsplit(clone).path:
            raise ProviderError("Provider repository URLs disagree")
        return Repo(external_id, full_name, web, clone, optional_string(raw.get("default_branch")))

    def _pr(self, raw) -> PR:
        if not isinstance(raw, dict):
            raise ProviderError("Provider returned an invalid pull request")
        external_id = identity(raw.get("id"), "pull request identity")
        title = required_string(raw.get("title"), "pull request title")
        web = validate_remote_url(required_string(raw.get("html_url"), "pull request URL"), self.web_root)
        head, base = raw.get("head"), raw.get("base")
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise ProviderError("Provider returned an invalid pull request branch")
        head_repo, base_repo = head.get("repo"), base.get("repo")
        if head_repo is not None and not isinstance(head_repo, dict) or base_repo is not None and not isinstance(base_repo, dict):
            raise ProviderError("Provider returned an invalid pull request repository")
        merged_at = timestamp(raw.get("merged_at"))
        state = raw.get("state")
        if state not in ("open", "closed"):
            raise ProviderError("Provider returned an invalid pull request state")
        normalized = "merged" if merged_at is not None else state
        return PR(external_id, title, web, normalized,
                  optional_identity(head_repo.get("id")) if head_repo else None,
                  optional_string(head.get("ref")), optional_string(head.get("sha")),
                  optional_identity(base_repo.get("id")) if base_repo else None,
                  optional_string(base.get("ref")),
                  optional_string(raw.get("merge_commit_sha")) if normalized == "merged" else None,
                  timestamp(raw.get("updated_at")), merged_at)
