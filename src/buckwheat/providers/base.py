"""Provider protocol and error family (design.md section 4).

The interface assumes no HTTP, API key or model: auth and transport live
inside each provider, built from its own config table.
"""

from typing import ClassVar, Protocol, runtime_checkable

from buckwheat.plan import ChangePlan, PlanContext


class ProviderError(Exception):
    """Base class for provider failures. Messages must never contain a key."""


class AuthError(ProviderError):
    """Missing credentials or a 401; the message names the env var."""


class RateLimitError(ProviderError):
    """The service asked us to slow down (429)."""


class NetworkError(ProviderError):
    """Could not reach the service, or it timed out."""


class BadOutputError(ProviderError):
    """Refusal, truncated output, or output that is not a valid ChangePlan."""


@runtime_checkable
class Provider(Protocol):
    name: ClassVar[str]

    def plan(self, request: str, context: PlanContext) -> ChangePlan:
        """Turn a request into a plan. Raises ProviderError on failure."""
        ...
