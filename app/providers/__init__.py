from .base import AuthenticationError, PR, ProviderError, RateLimitError, Repo, validate_remote_url
from .github import GitHubProvider
from .gitlab import GitLabProvider
from .local import LocalProvider


def get_provider(connection, token, client=None, *, local_root="/data/local-git"):
    if connection.kind == "local":
        return LocalProvider(connection, local_root)
    if connection.kind == "github":
        return GitHubProvider(connection, token, client)
    if connection.kind == "gitlab":
        return GitLabProvider(connection, token, client)
    raise ProviderError("Unsupported provider kind")


__all__ = ["get_provider", "GitHubProvider", "GitLabProvider", "Repo", "PR", "ProviderError", "AuthenticationError", "RateLimitError", "validate_remote_url"]
