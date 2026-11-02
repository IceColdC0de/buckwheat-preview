from pathlib import Path

import pytest

from buckwheat.config import (
    ConfigError,
    Settings,
    config_dir,
    data_dir,
    default_config_path,
    load_settings,
    looks_like_key,
    state_dir,
)

FAKE_KEY = "sk-" + "a1B2c3D4" * 4


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


def test_xdg_defaults(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert config_dir() == home / ".config/buckwheat"
    assert state_dir() == home / ".local/state/buckwheat"
    assert data_dir() == home / ".local/share/buckwheat"
    assert default_config_path() == home / ".config/buckwheat/config.toml"


def test_xdg_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    assert config_dir() == tmp_path / "c/buckwheat"
    assert state_dir() == tmp_path / "s/buckwheat"


def test_xdg_relative_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/dir")
    assert config_dir() == tmp_path / "home/.config/buckwheat"


def test_missing_default_gives_defaults() -> None:
    assert load_settings() == Settings()


def test_missing_explicit_path_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.toml")


def test_loads_from_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    d = tmp_path / "xdg/buckwheat"
    d.mkdir(parents=True)
    (d / "config.toml").write_text(
        'provider = "openai_compat"\n'
        '[providers.openai_compat]\napi_key_env = "OPENAI_API_KEY"\n'
    )
    s = load_settings()
    assert s.provider == "openai_compat"
    assert s.providers["openai_compat"].api_key_env == "OPENAI_API_KEY"


def test_full_example(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        """
provider = "anthropic"
[providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
model = "claude-sonnet-5-5"
max_tokens = 2048
[providers.openai_compat]
api_key_env = "OPENAI_API_KEY"
base_url = "https://api.openai.com/v1"
model = "gpt-4.1"
""",
    )
    s = load_settings(p)
    assert s.providers["anthropic"].max_tokens == 2048


def test_rejects_key_in_api_key_env(tmp_path: Path) -> None:
    p = write(tmp_path, f'[providers.anthropic]\napi_key_env = "{FAKE_KEY}"\n')
    with pytest.raises(ConfigError, match="looks like an API key") as ei:
        load_settings(p)
    assert FAKE_KEY not in str(ei.value)


def test_rejects_key_in_unknown_field(tmp_path: Path) -> None:
    p = write(tmp_path, f'note = "{FAKE_KEY}"\n')
    with pytest.raises(ConfigError, match="looks like an API key"):
        load_settings(p)


def test_rejects_opaque_token(tmp_path: Path) -> None:
    token = "Zm9vYmFy" * 6 + "1"
    p = write(
        tmp_path, f'[providers.anthropic]\nmodel = "{token}"\napi_key_env = "X"\n'
    )
    with pytest.raises(ConfigError, match="looks like an API key"):
        load_settings(p)


def test_bad_env_name_rejected(tmp_path: Path) -> None:
    p = write(tmp_path, '[providers.anthropic]\napi_key_env = "not a name!"\n')
    with pytest.raises(ConfigError, match="environment variable name"):
        load_settings(p)


def test_empty_env_only_for_local_openai_compat(tmp_path: Path) -> None:
    ok = write(
        tmp_path, '[providers.openai_compat]\nbase_url = "http://localhost:11434/v1"\n'
    )
    assert load_settings(ok).providers["openai_compat"].api_key_env == ""
    bad = write(tmp_path, '[providers.anthropic]\nmodel = "m"\n')
    with pytest.raises(ConfigError, match="may be empty only"):
        load_settings(bad)


def test_unknown_key_and_bad_toml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid config"):
        load_settings(write(tmp_path, "bogus = 1\n"))
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_settings(write(tmp_path, "provider = \n"))


@pytest.mark.parametrize(
    "value",
    [
        "claude-sonnet-5-5",
        "gpt-4.1",
        "https://openrouter.ai/api/v1",
        "~/Pictures/walls/some/very/long/relative/path/to/a/wallpaper-file.jpg",
        "a-very-long-descriptive-model-name-with-only-letters-and-dashes",
        "",
    ],
)
def test_legit_values_not_flagged(value: str) -> None:
    assert not looks_like_key(value)


def test_key_shapes_flagged() -> None:
    assert looks_like_key(FAKE_KEY)
    assert looks_like_key("ghp_" + "A1" * 15)


def test_safety_defaults_and_bounds(tmp_path: Path) -> None:
    from buckwheat.config import Settings

    s = Settings()
    assert s.safety.revert_timeout_seconds == 15
    assert s.safety.max_retries == 1
    p = tmp_path / "c.toml"
    p.write_text("[safety]\nrevert_timeout_seconds = 0\n")
    with pytest.raises(ConfigError):
        load_settings(p)
