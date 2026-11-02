from types import SimpleNamespace
from typing import Any

import pytest

from buckwheat.plan import ChangePlan, PlanContext
from buckwheat.providers import (
    AuthError,
    BadOutputError,
    NetworkError,
    Provider,
    ProviderError,
    RateLimitError,
    available,
    create,
)
from buckwheat.providers.anthropic import AnthropicProvider

GOOD = {
    "version": 1,
    "summary": "gaps",
    "ops": [
        {"op": "set", "target": "hyprland", "key": "general.gaps_in", "value": 6}
    ],
}
CTX = PlanContext(request_id="r1")


class FakeClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)
        self._response = response
        self._error = error

    def _create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if self._error:
            raise self._error
        return self._response


def tool_use(inp: Any, name: str = "submit_plan") -> Any:
    return SimpleNamespace(type="tool_use", name=name, input=inp)


def resp(*blocks: Any, stop: str = "tool_use") -> Any:
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


def run(client: FakeClient) -> ChangePlan:
    return AnthropicProvider(client=client).plan("more gaps", CTX)


def test_registered_and_protocol() -> None:
    assert "anthropic" in available()
    p = create("anthropic", {"api_key_env": "X_KEY"})
    assert isinstance(p, Provider)


def test_success_and_tool_request() -> None:
    client = FakeClient(resp(tool_use(GOOD)))
    plan = run(client)
    assert plan.summary == "gaps"
    kw = client.calls[0]
    # Forced tool_choice ("tool"/"any") is a 400 on current models.
    assert kw["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert kw["max_tokens"] == 16000
    assert kw["output_config"] == {"effort": "medium"}
    for banned in ("temperature", "top_p", "top_k", "thinking"):
        assert banned not in kw
    assert "calling the submit_plan tool exactly once" in kw["system"]
    assert kw["tools"][0]["name"] == "submit_plan"
    assert kw["tools"][0]["input_schema"] == ChangePlan.model_json_schema()
    assert "more gaps" in kw["messages"][0]["content"]


def test_refusal_truncated_missing_and_invalid() -> None:
    with pytest.raises(BadOutputError, match="refused"):
        run(FakeClient(resp(stop="refusal")))
    with pytest.raises(BadOutputError, match="truncated"):
        run(FakeClient(resp(tool_use(GOOD), stop="max_tokens")))
    with pytest.raises(BadOutputError, match="did not call"):
        run(FakeClient(resp(SimpleNamespace(type="text", text="hi"))))
    with pytest.raises(BadOutputError, match="invalid plan"):
        run(FakeClient(resp(tool_use({"version": 1, "ops": "nope"}))))


def make_exc(name: str, base: type[Exception] = Exception, **attrs: Any) -> Exception:
    exc = type(name, (base,), {})("secret-ish detail")
    for k, v in attrs.items():
        setattr(exc, k, v)
    return exc


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("AuthenticationError", AuthError),
        ("PermissionDeniedError", AuthError),
        ("RateLimitError", RateLimitError),
        ("APITimeoutError", NetworkError),
        ("APIConnectionError", NetworkError),
        ("InternalServerError", NetworkError),
    ],
)
def test_error_mapping(name: str, expected: type[ProviderError]) -> None:
    with pytest.raises(expected) as info:
        run(FakeClient(error=make_exc(name)))
    assert "secret-ish" not in str(info.value)


def test_auth_error_names_env_var() -> None:
    p = AnthropicProvider(
        api_key_env="MY_KEY", client=FakeClient(error=make_exc("AuthenticationError"))
    )
    with pytest.raises(AuthError, match="MY_KEY"):
        p.plan("x", CTX)


def test_missing_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_KEY", raising=False)
    with pytest.raises(AuthError, match="MY_KEY"):
        AnthropicProvider(api_key_env="MY_KEY").plan("x", CTX)


def test_other_status_error() -> None:
    with pytest.raises(NetworkError):
        run(FakeClient(error=make_exc("APIStatusError", status_code=503)))
    with pytest.raises(ProviderError) as info:
        run(FakeClient(error=make_exc("APIStatusError", status_code=400)))
    assert not isinstance(info.value, NetworkError)


def test_default_max_tokens_from_registry() -> None:
    assert create("anthropic", {}).max_tokens == 16000  # type: ignore[attr-defined]
    assert create("anthropic", {"max_tokens": None}).max_tokens == 16000  # type: ignore[attr-defined]
    assert create("anthropic", {"max_tokens": 500}).max_tokens == 500  # type: ignore[attr-defined]


def test_status_error_shows_anthropic_message() -> None:
    exc = make_exc(
        "BadRequestError",
        status_code=400,
        body={
            "error": {
                "type": "invalid_request_error",
                "message": "credit balance too low",
            }
        },
    )
    with pytest.raises(ProviderError) as info:
        run(FakeClient(error=exc))
    assert str(info.value) == (
        "Anthropic request failed (HTTP 400): credit balance too low"
    )
    assert "secret-ish" not in str(info.value)


def test_status_error_falls_back_to_message_attr_and_truncates() -> None:
    exc = make_exc("APIStatusError", status_code=422, message="x" * 500)
    with pytest.raises(ProviderError) as info:
        run(FakeClient(error=exc))
    assert str(info.value) == "Anthropic request failed (HTTP 422): " + "x" * 300


def test_no_tool_call_text_reply_is_bad_output() -> None:
    reply = resp(SimpleNamespace(type="text", text="here you go"), stop="end_turn")
    with pytest.raises(BadOutputError, match="did not call submit_plan"):
        run(FakeClient(reply))
