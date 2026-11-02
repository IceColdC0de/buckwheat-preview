from types import SimpleNamespace
from typing import Any

import pytest

from buckwheat.plan import ChangePlan, PlanContext, SetOp
from buckwheat.providers import (
    AuthError,
    BadOutputError,
    NetworkError,
    ProviderError,
    RateLimitError,
    available,
    create,
)
from buckwheat.providers.openai_compat import OpenAICompatProvider, schema_for_openai

PLAN = ChangePlan(
    version=1,
    summary="gaps",
    ops=[SetOp(op="set", target="hyprland", key="general.gaps_in", value=6)],
)
CTX = PlanContext(request_id="r1")


class HTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


class APIConnectionError(Exception):
    pass


def reply(content: str | None, finish: str = "stop", refusal: str | None = None) -> Any:
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)]
    )


class FakeClient:
    """Each script item is a response or an exception to raise."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def provider(client: FakeClient, **kw: Any) -> OpenAICompatProvider:
    return OpenAICompatProvider(model="m", api_key_env="K", client=client, **kw)


def test_registered() -> None:
    assert "openai_compat" in available()
    p = create("openai_compat", {"model": "m", "api_key_env": "K"})
    assert isinstance(p, OpenAICompatProvider)


def test_json_schema_mode() -> None:
    client = FakeClient(reply(PLAN.model_dump_json()))
    p = provider(client)
    assert p.plan("gaps", CTX) == PLAN
    assert p.mode == "json_schema"
    rf = client.calls[0]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True


def test_fallback_to_json_object_and_cached() -> None:
    client = FakeClient(
        HTTPError(400), reply(PLAN.model_dump_json()), reply(PLAN.model_dump_json())
    )
    p = provider(client)
    assert p.plan("gaps", CTX) == PLAN
    assert p.mode == "json_object"
    second = client.calls[1]
    assert second["response_format"] == {"type": "json_object"}
    assert "JSON schema" in second["messages"][0]["content"]
    assert p.plan("gaps", CTX) == PLAN
    assert len(client.calls) == 3  # no json_schema retry the second time
    assert client.calls[2]["response_format"] == {"type": "json_object"}


def test_cached_json_schema_does_not_fall_back() -> None:
    client = FakeClient(reply(PLAN.model_dump_json()), HTTPError(400))
    p = provider(client)
    p.plan("gaps", CTX)
    with pytest.raises(BadOutputError):
        p.plan("gaps", CTX)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (HTTPError(401), AuthError),
        (HTTPError(429), RateLimitError),
        (HTTPError(503), NetworkError),
        (APIConnectionError(), NetworkError),
        (TimeoutError(), NetworkError),
    ],
)
def test_error_mapping_does_not_fall_back(exc: Exception, expected: type) -> None:
    client = FakeClient(exc)
    with pytest.raises(expected):
        provider(client).plan("x", CTX)
    assert len(client.calls) == 1


def test_auth_error_names_env_var() -> None:
    with pytest.raises(AuthError, match="K"):
        provider(FakeClient(HTTPError(401))).plan("x", CTX)


@pytest.mark.parametrize(
    "bad",
    [
        reply("not json"),
        reply('{"version": 1}'),
        reply(None),
        reply("{}", finish="length"),
        reply(None, refusal="no"),
    ],
)
def test_bad_output(bad: Any) -> None:
    with pytest.raises(BadOutputError):
        provider(FakeClient(bad)).plan("x", CTX)


def test_missing_env_var_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("K", raising=False)
    with pytest.raises(AuthError, match="K"):
        OpenAICompatProvider(model="m", api_key_env="K").plan("x", CTX)


def test_empty_api_key_env_only_for_localhost() -> None:
    OpenAICompatProvider(model="m", base_url="http://localhost:11434/v1")
    with pytest.raises(AuthError):
        OpenAICompatProvider(model="m", base_url="https://api.openai.com/v1")
    with pytest.raises(ProviderError):
        OpenAICompatProvider(model="", api_key_env="K")


def test_schema_for_openai_inlines_and_closes() -> None:
    out = schema_for_openai(ChangePlan.model_json_schema())
    text = str(out)
    assert "$defs" not in out
    assert "$ref" not in text

    def objects(node: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(node, dict):
            if "properties" in node:
                found.append(node)
            for v in node.values():
                found += objects(v)
        elif isinstance(node, list):
            for v in node:
                found += objects(v)
        return found

    objs = objects(out)
    assert objs
    for o in objs:
        assert o["additionalProperties"] is False
        assert set(o["required"]) == set(o["properties"])
