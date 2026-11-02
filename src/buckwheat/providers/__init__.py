"""LLM providers that turn requests into change plans."""

from buckwheat.providers import anthropic as _anthropic  # noqa: F401  (registers)
from buckwheat.providers import fake as _fake  # noqa: F401  (registers "fake")
from buckwheat.providers import (
    openai_compat as _openai_compat,  # noqa: F401  (registers "openai_compat")
)
from buckwheat.providers.base import (
    AuthError,
    BadOutputError,
    NetworkError,
    Provider,
    ProviderError,
    RateLimitError,
)
from buckwheat.providers.fake import FakeProvider
from buckwheat.providers.prompt import Prompt, build_prompt
from buckwheat.providers.registry import available, create, register

__all__ = [
    "AuthError",
    "BadOutputError",
    "FakeProvider",
    "NetworkError",
    "Prompt",
    "Provider",
    "ProviderError",
    "RateLimitError",
    "available",
    "build_prompt",
    "create",
    "register",
]
