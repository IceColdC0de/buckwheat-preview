"""Anthropic provider: tool use (design.md section 4). Registered as
"anthropic".

The model is told to answer only by calling the single tool `submit_plan`,
whose input schema is the ChangePlan JSON schema. The tool input is parsed with
`ChangePlan.model_validate`. The call is not forced with tool_choice "tool" or
"any": current models (sonnet-5-5, opus-5-5, fable-5-1) reject that with HTTP
400. We use tool_choice "auto" plus a system prompt instruction, and a reply
without the tool call becomes BadOutputError (the session retries).

The `anthropic` SDK is imported only when a real client is built, so the rest
of buckwheat (and the unit tests, which inject a fake client) work without it.
Errors are mapped by SDK exception class name, so no SDK import is needed here.
"""

import importlib
import logging
import os
from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import ValidationError

from buckwheat.plan import ChangePlan, PlanContext
from buckwheat.providers.base import (
    AuthError,
    BadOutputError,
    NetworkError,
    ProviderError,
    RateLimitError,
)
from buckwheat.providers.prompt import build_prompt
from buckwheat.providers.registry import register

log = logging.getLogger(__name__)

TOOL_NAME = "submit_plan"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_ENV = "ANTHROPIC_API_KEY"
TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2
DEFAULT_MAX_TOKENS = 16000  # thinking tokens count against max_tokens
EFFORT = "medium"
MAX_ERROR_CHARS = 300

TOOL_INSTRUCTION = (
    f"Deliver the plan by calling the {TOOL_NAME} tool exactly once. Answer "
    f"only by calling {TOOL_NAME}: do not reply with plain text, and do not "
    "call any other tool or call it more than once."
)

_AUTH = {"AuthenticationError", "PermissionDeniedError"}
_NETWORK = {"APITimeoutError", "APIConnectionError", "InternalServerError"}


def _class_names(exc: BaseException) -> set[str]:
    return {c.__name__ for c in type(exc).__mro__}


def _api_message(exc: Exception) -> str:
    """Anthropic's own short error message, or "". Never the request or key."""
    message: Any = None
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
    if not isinstance(message, str) or not message.strip():
        message = getattr(exc, "message", None)
    if not isinstance(message, str):
        return ""
    return " ".join(message.split())[:MAX_ERROR_CHARS]


class AnthropicProvider:
    name: ClassVar[str] = "anthropic"

    def __init__(
        self,
        *,
        api_key_env: str = DEFAULT_ENV,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: str | None = None,
        client: Any = None,
    ) -> None:
        self.api_key_env = api_key_env or DEFAULT_ENV
        self.model = model or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.base_url = base_url
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        key = os.environ.get(self.api_key_env)
        if not key:
            raise AuthError(f"environment variable {self.api_key_env} is not set")
        try:
            sdk = importlib.import_module("anthropic")
        except ImportError:
            raise ProviderError(
                "the 'anthropic' package is not installed; install it to use "
                "the anthropic provider"
            ) from None
        self._client = sdk.Anthropic(
            api_key=key,
            base_url=self.base_url,
            timeout=TIMEOUT_SECONDS,
            max_retries=MAX_RETRIES,
        )
        return self._client

    def plan(self, request: str, context: PlanContext) -> ChangePlan:
        client = self._get_client()
        prompt = build_prompt(request, context)
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=f"{prompt.system}\n\n{TOOL_INSTRUCTION}",
                messages=[{"role": "user", "content": prompt.user}],
                tools=[
                    {
                        "name": TOOL_NAME,
                        "description": "Submit the change plan for the request.",
                        "input_schema": ChangePlan.model_json_schema(),
                    }
                ],
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                output_config={"effort": EFFORT},
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise self._map_error(exc) from None
        return self._parse(response)

    def _map_error(self, exc: Exception) -> ProviderError:
        names = _class_names(exc)
        # Log only the exception type; messages could echo request details.
        log.debug("anthropic call failed: %s", type(exc).__name__)
        if names & _AUTH:
            return AuthError(
                f"Anthropic rejected the API key in {self.api_key_env}; "
                "check that it is valid"
            )
        if "RateLimitError" in names:
            return RateLimitError("Anthropic rate limit reached; try again shortly")
        if names & _NETWORK:
            return NetworkError(f"could not reach Anthropic ({type(exc).__name__})")
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status >= 500:
            return NetworkError(f"Anthropic server error (HTTP {status})")
        if isinstance(status, int):
            detail = _api_message(exc)
            suffix = f": {detail}" if detail else ""
            return ProviderError(f"Anthropic request failed (HTTP {status}){suffix}")
        return ProviderError(f"Anthropic request failed ({type(exc).__name__})")

    def _parse(self, response: Any) -> ChangePlan:
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise BadOutputError("the model refused the request")
        if stop == "max_tokens":
            raise BadOutputError("the model's output was truncated (max_tokens)")
        for block in getattr(response, "content", None) or []:
            if getattr(block, "type", None) == "tool_use" and (
                getattr(block, "name", None) == TOOL_NAME
            ):
                try:
                    return ChangePlan.model_validate(block.input)
                except ValidationError as exc:
                    raise BadOutputError(
                        f"the model returned an invalid plan ({exc.error_count()} "
                        "validation errors)"
                    ) from None
        raise BadOutputError(f"the model did not call {TOOL_NAME}")


@register("anthropic")
def _create_anthropic(cfg: Mapping[str, Any]) -> AnthropicProvider:
    return AnthropicProvider(
        api_key_env=cfg.get("api_key_env") or DEFAULT_ENV,
        model=cfg.get("model") or DEFAULT_MODEL,
        max_tokens=cfg.get("max_tokens") or DEFAULT_MAX_TOKENS,
        base_url=cfg.get("base_url"),
    )
