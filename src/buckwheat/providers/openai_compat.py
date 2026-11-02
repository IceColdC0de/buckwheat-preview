"""OpenAI-compatible provider. Registered as "openai_compat".

Uses the `openai` SDK with a configurable base_url (OpenAI, OpenRouter,
Ollama, ...). Tries a strict `json_schema` response format first; if the
server rejects it, falls back to `json_object` mode with the schema in the
system prompt. The mode that worked is cached on the instance.

The SDK is imported only when no client is injected, so tests (and users of
other providers) do not need it installed. Errors are mapped by status code
and exception class name, so no SDK types are needed here.
"""

import copy
import importlib
import json
import os
from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import urlparse

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

TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2
DEFAULT_BASE_URL = "https://api.openai.com/v1"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_MAX_REF_DEPTH = 20

_JSON_SCHEMA = "json_schema"
_JSON_OBJECT = "json_object"


def schema_for_openai(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return a strict-mode-friendly copy of a JSON schema.

    Inlines `$defs` / `$ref`, sets `additionalProperties: false` on every
    object, and lists every property as required (strict mode demands it).
    """
    root = copy.deepcopy(dict(schema))
    defs = root.pop("$defs", {})

    def walk(node: Any, depth: int) -> Any:
        if depth > _MAX_REF_DEPTH:
            raise ValueError("schema is too deeply nested or recursive")
        if isinstance(node, list):
            return [walk(item, depth + 1) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs[ref.removeprefix("#/$defs/")]
            extra = {k: v for k, v in node.items() if k != "$ref"}
            return walk({**copy.deepcopy(target), **extra}, depth + 1)
        out = {k: walk(v, depth + 1) for k, v in node.items()}
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out.get("properties", {}))
        return out

    result: dict[str, Any] = walk(root, 0)
    return result


def _status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _is_format_rejection(exc: Exception) -> bool:
    """True if the server refused the response_format (not auth/rate/network)."""
    status = _status(exc)
    if status is not None:
        return status in (400, 404, 405, 415, 422, 501)
    return type(exc).__name__ in ("BadRequestError", "UnprocessableEntityError")


class OpenAICompatProvider:
    name: ClassVar[str] = "openai_compat"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        api_key_env: str = "",
        max_tokens: int = 2048,
        client: Any = None,
    ) -> None:
        if not model:
            raise ProviderError("openai_compat needs a model in its config table")
        host = urlparse(base_url).hostname
        if not api_key_env and host not in _LOCAL_HOSTS:
            raise AuthError(
                "openai_compat: api_key_env may be empty only for a localhost base_url"
            )
        self.model = model
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.mode: str | None = None  # cached: "json_schema" or "json_object"
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        key = "not-needed"  # local servers ignore it; the SDK wants something
        if self.api_key_env:
            value = os.environ.get(self.api_key_env, "")
            if not value:
                raise AuthError(f"environment variable {self.api_key_env} is not set")
            key = value
        try:
            openai = importlib.import_module("openai")
        except ImportError:
            raise ProviderError(
                "the openai package is required for the openai_compat provider "
                "(pip install openai)"
            ) from None
        self._client = openai.OpenAI(
            api_key=key,
            base_url=self.base_url,
            timeout=TIMEOUT_SECONDS,
            max_retries=MAX_RETRIES,
        )
        return self._client

    def plan(self, request: str, context: PlanContext) -> ChangePlan:
        client = self._get_client()
        prompt = build_prompt(request, context)
        schema = ChangePlan.model_json_schema()
        modes = [self.mode] if self.mode else [_JSON_SCHEMA, _JSON_OBJECT]
        for i, mode in enumerate(modes):
            kwargs = self._request_kwargs(mode, prompt.system, prompt.user, schema)
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as exc:
                # Only the first, uncached attempt may fall back.
                if mode == _JSON_SCHEMA and i + 1 < len(modes):
                    if _is_format_rejection(exc):
                        continue
                raise self._map_error(exc) from None
            plan = self._parse(response)
            self.mode = mode
            return plan
        raise BadOutputError("no response mode succeeded")  # pragma: no cover

    def _request_kwargs(
        self, mode: str, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        if mode == _JSON_SCHEMA:
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "change_plan",
                    "strict": True,
                    "schema": schema_for_openai(schema),
                },
            }
        else:
            response_format = {"type": "json_object"}
            system = (
                f"{system}\n\nReply with a single JSON object, and nothing "
                "else, that matches this JSON schema:\n"
                f"{json.dumps(schema, sort_keys=True)}"
            )
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": response_format,
        }

    def _parse(self, response: Any) -> ChangePlan:
        try:
            choice = response.choices[0]
            message = choice.message
        except (AttributeError, IndexError, TypeError):
            raise BadOutputError("the server returned no choices") from None
        if getattr(message, "refusal", None):
            raise BadOutputError("the model refused the request")
        if getattr(choice, "finish_reason", None) == "length":
            raise BadOutputError("the model's output was truncated")
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise BadOutputError("the model returned an empty reply")
        try:
            return ChangePlan.model_validate_json(content)
        except ValidationError as exc:
            raise BadOutputError(
                f"the reply is not a valid ChangePlan ({exc.error_count()} errors)"
            ) from None

    def _map_error(self, exc: Exception) -> ProviderError:
        if isinstance(exc, ProviderError):
            return exc
        status = _status(exc)
        cls = type(exc).__name__
        if status in (401, 403):
            env = self.api_key_env or "(no api_key_env set)"
            return AuthError(f"the server rejected the API key from {env}")
        if status == 429:
            return RateLimitError("the server is rate limiting requests")
        if cls in ("APIConnectionError", "APITimeoutError") or (
            isinstance(exc, (ConnectionError, TimeoutError))
        ):
            return NetworkError("could not reach the server (or it timed out)")
        if status is not None and status >= 500:
            return NetworkError(f"the server failed with HTTP {status}")
        if status is not None:
            return BadOutputError(f"the server rejected the request: HTTP {status}")
        return ProviderError(f"unexpected {cls} from the server")


@register("openai_compat")
def _create_openai_compat(cfg: Mapping[str, Any]) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        model=cfg.get("model", ""),
        base_url=cfg.get("base_url") or DEFAULT_BASE_URL,
        api_key_env=cfg.get("api_key_env", ""),
        max_tokens=cfg.get("max_tokens") or 2048,
    )
