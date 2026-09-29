"""Safe, bounded REST access and normalized provider data."""

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx


class ProviderError(Exception):
    """A provider response cannot be used as complete, trustworthy data."""


class AuthenticationError(ProviderError):
    """The configured credential was rejected."""


class RateLimitError(ProviderError):
    def __init__(self, retry_after: int | None = None, retry_at: datetime | None = None):
        super().__init__("Provider rate limit reached")
        self.retry_after = retry_after
        self.retry_at = retry_at


@dataclass(frozen=True)
class Repo:
    external_id: str
    full_name: str
    web_url: str
    clone_url: str
    default_branch: str | None


@dataclass(frozen=True)
class PR:
    external_id: str
    title: str
    web_url: str
    state: str
    source_repo_id: str | None
    source_branch: str | None
    source_oid: str | None
    target_repo_id: str | None
    target_branch: str | None
    merge_oid: str | None
    updated_at: datetime | None
    merged_at: datetime | None


def identity(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip():
        raise ProviderError(f"Provider response lacks {label}")
    return str(value)


def optional_identity(value: Any) -> str | None:
    return None if value is None else identity(value, "repository identity")


def required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderError(f"Provider response lacks {label}")
    return value


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderError("Provider response has an invalid optional field")
    return value


def timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderError("Provider response has an invalid timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderError("Provider response has an invalid timestamp") from exc
    if result.tzinfo is None:
        raise ProviderError("Provider response has an invalid timestamp")
    return result.astimezone(timezone.utc)


def _safe_path(path: str) -> None:
    for _ in range(8):
        if "\\" in path or any(part in (".", "..") for part in path.split("/")) or any(ord(c) < 32 for c in path):
            raise ProviderError("Invalid provider URL path")
        decoded = unquote(path)
        if decoded == path:
            return
        path = decoded
    raise ProviderError("Invalid provider URL path")


def _url_parts(url: str) -> tuple[str, str, str]:
    if not isinstance(url, str):
        raise ProviderError("Invalid provider URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ProviderError("Invalid provider URL") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderError("Provider URL must be an HTTPS instance URL")
    origin = f"https://{parsed.hostname.lower()}" + (f":{port}" if port and port != 443 else "")
    path = parsed.path.rstrip("/")
    _safe_path(path)
    return origin, path, parsed.hostname.lower()


def validate_remote_url(url: str, web_root: str) -> str:
    """Validate a provider-supplied web/HTTPS clone URL against its instance."""
    origin, prefix, _ = _url_parts(web_root)
    remote_origin, path, _ = _url_parts(url)
    if remote_origin != origin or not path.startswith(prefix + "/"):
        raise ProviderError("Provider returned a URL outside its configured instance")
    return url


class Provider:
    max_pages = 1000
    timeout = 15.0

    def __init__(self, connection: Any, token: str | None, client: httpx.Client | None = None):
        self.connection = connection
        self.token = token
        self.kind = connection.kind
        origin, path, _ = _url_parts(connection.base_url)
        if self.kind == "github":
            if origin == "https://github.com" and not path:
                self.web_root, self.api_root = origin, "https://api.github.com"
            elif origin == "https://api.github.com" and not path:
                self.web_root, self.api_root = "https://github.com", origin
            else:
                path = path.removesuffix("/api/v3")
                self.web_root = origin + path
                self.api_root = self.web_root + "/api/v3"
        elif self.kind == "gitlab":
            path = path.removesuffix("/api/v4")
            self.web_root = origin + path
            self.api_root = self.web_root + "/api/v4"
        else:
            raise ProviderError("Unsupported provider kind")
        self._owns_client = client is None
        self.client = client if client is not None else httpx.Client(verify=connection.ca_bundle_path or True, timeout=self.timeout, follow_redirects=False)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _validated_api_url(self, url: str, expected_path: str | None = None) -> str:
        if not isinstance(url, str) or url.startswith("//"):
            raise ProviderError("Unsafe provider pagination URL")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise ProviderError("Unsafe provider pagination URL") from exc
        api = urlsplit(self.api_root)
        if (parsed.scheme != "https" or parsed.hostname != api.hostname or port != api.port
                or parsed.username or parsed.password or parsed.fragment):
            raise ProviderError("Unsafe provider pagination URL")
        _safe_path(parsed.path)
        if not parsed.path.startswith(api.path.rstrip("/") + "/") and not (not api.path and parsed.path.startswith("/")):
            raise ProviderError("Unsafe provider pagination URL")
        if expected_path and parsed.path != expected_path:
            raise ProviderError("Provider pagination changed endpoint")
        return url

    def _request(self, url: str, params: dict | None = None) -> httpx.Response:
        self._validated_api_url(url)
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization" if self.kind == "github" else "PRIVATE-TOKEN"] = (
                f"Bearer {self.token}" if self.kind == "github" else self.token)
        try:
            response = self.client.get(url, params=params, headers=headers, timeout=self.timeout, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise ProviderError("Provider request failed") from exc
        if response.status_code in (301, 302, 303, 307, 308):
            raise ProviderError("Provider redirect rejected")
        if response.status_code == 401:
            raise AuthenticationError("Provider credential rejected")
        secondary_limit = (response.status_code == 403 and self.kind == "github" and
                           ("secondary rate limit" in response.content[:4096].decode("utf-8", "ignore").lower()
                            or "abuse detection" in response.content[:4096].decode("utf-8", "ignore").lower()))
        if response.status_code == 429 or secondary_limit or (response.status_code == 403 and (
                response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers)):
            retry_after = response.headers.get("retry-after")
            retry_at = None
            seconds = None
            if retry_after:
                if retry_after.isdecimal():
                    seconds = int(retry_after)
                else:
                    try:
                        retry_at = parsedate_to_datetime(retry_after).astimezone(timezone.utc)
                    except (ValueError, TypeError, OverflowError):
                        pass
            if response.headers.get("x-ratelimit-reset", "").isdecimal():
                retry_at = datetime.fromtimestamp(int(response.headers["x-ratelimit-reset"]), timezone.utc)
            if secondary_limit and seconds is None:
                seconds = 60
            raise RateLimitError(seconds, retry_at)
        if response.status_code == 403:
            raise AuthenticationError("Provider access forbidden")
        if not 200 <= response.status_code < 300:
            raise ProviderError(f"Provider returned HTTP {response.status_code}")
        return response

    def _json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("Provider returned invalid JSON") from exc

    def _pages(self, path: str, params: dict | None = None):
        url = self.api_root + path
        expected_path = urlsplit(url).path
        seen: set[str] = set()
        for _ in range(self.max_pages):
            request = self.client.build_request("GET", url, params=params)
            key = str(request.url)
            if key in seen:
                raise ProviderError("Provider pagination loop")
            seen.add(key)
            response = self._request(url, params)
            body = self._json(response)
            if not isinstance(body, list):
                raise ProviderError("Provider returned an invalid list")
            yield body
            next_url = self._next_page(response, key, len(body), int(params.get("per_page", 100)) if params else 100)
            if not next_url:
                return
            url = self._validated_api_url(next_url, expected_path)
            params = None
        raise ProviderError("Provider page limit exceeded")

    def _next_page(self, response: httpx.Response, current_url: str, page_size: int, per_page: int) -> str | None:
        links = response.links
        if "next" in links:
            return links["next"]["url"]
        if self.kind == "gitlab":
            page = response.headers.get("x-next-page")
            if page:
                if not page.isdecimal() or int(page) < 1:
                    raise ProviderError("Invalid provider next page")
                return str(httpx.URL(current_url).copy_set_param("page", page))
            current = response.headers.get("x-page")
            total = response.headers.get("x-total-pages")
            if current and total and current.isdecimal() and total.isdecimal() and int(current) < int(total):
                raise ProviderError("Provider pagination headers are inconsistent")
            if "x-next-page" in response.headers:
                return None
        if page_size >= per_page:
            current_page = int(httpx.URL(current_url).params.get("page", "1"))
            return str(httpx.URL(current_url).copy_set_param("page", str(current_page + 1)))
        return None
