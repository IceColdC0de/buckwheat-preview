"""Configuration: XDG paths and the pydantic ``Settings`` model.

The config file stores only the *name* of the environment variable that holds
the API key, never the key itself. Any string value that looks like a key is
rejected (design.md, section 4).
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

APP_NAME = "buckwheat"
CONFIG_FILENAME = "config.toml"

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Well-known key shapes: "sk-..." (OpenAI/Anthropic/OpenRouter), GitHub, AWS, Google.
_KEY_PREFIXES = re.compile(
    r"^(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})$"
)
# Long opaque token: base64/base64url alphabet only, with letters AND digits.
# Paths, URLs and model names contain "/", ".", ":" or "~" often or are short.
_OPAQUE = re.compile(r"^[A-Za-z0-9+_=-]{40,}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ConfigError(Exception):
    """The config file could not be read or is invalid. Never includes values."""


# --- XDG paths -------------------------------------------------------------


def _xdg_dir(env_var: str, default_rel: str) -> Path:
    """Return the XDG base dir; relative or empty values are ignored per spec."""
    value = os.environ.get(env_var, "")
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / default_rel


def config_dir() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", ".config") / APP_NAME


def state_dir() -> Path:
    return _xdg_dir("XDG_STATE_HOME", ".local/state") / APP_NAME


def data_dir() -> Path:
    return _xdg_dir("XDG_DATA_HOME", ".local/share") / APP_NAME


def default_config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


# --- Settings --------------------------------------------------------------


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key_env: str = ""  # the NAME of an env var, never a key
    model: str = Field(default="", max_length=200)
    base_url: str | None = None
    # None means "use the provider's own default" (anthropic 16000, openai_compat 2048).
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)

    @model_validator(mode="after")
    def _check_env_name(self) -> ProviderSettings:
        if self.api_key_env and not _ENV_NAME.match(self.api_key_env):
            raise ValueError("api_key_env must be an environment variable name")
        return self


class SafetySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revert_timeout_seconds: int = Field(default=15, ge=1, le=3600)
    max_retries: int = Field(default=1, ge=0, le=5)  # automatic planner retries
    snapshot_retention: int = Field(default=50, ge=1, le=10_000)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["anthropic", "openai_compat"] = "anthropic"
    # "confirm": edit the user's main config (include block only) after showing
    # a diff and asking. "manual": never edit it; show the lines to paste.
    bootstrap: Literal["confirm", "manual"] = "confirm"
    safety: SafetySettings = Field(default_factory=SafetySettings)
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_providers(self) -> Settings:
        for name, cfg in self.providers.items():
            if name not in ("anthropic", "openai_compat"):
                raise ValueError(f"unknown provider table: {name}")
            if not cfg.api_key_env and not _is_local_openai_compat(name, cfg):
                raise ValueError(
                    f"providers.{name}.api_key_env may be empty only for "
                    "openai_compat with a localhost base_url"
                )
        return self


def _is_local_openai_compat(name: str, cfg: ProviderSettings) -> bool:
    if name != "openai_compat" or not cfg.base_url:
        return False
    return urlparse(cfg.base_url).hostname in _LOCAL_HOSTS


def looks_like_key(value: str) -> bool:
    """Heuristic: known key prefixes, or a long opaque token with letters+digits."""
    if _KEY_PREFIXES.match(value):
        return True
    return bool(
        _OPAQUE.match(value)
        and re.search(r"[A-Za-z]", value)
        and re.search(r"[0-9]", value)
    )


def _find_key_like(node: Any, where: str = "") -> str | None:
    """Return the dotted location of the first key-like string, or None."""
    if isinstance(node, str):
        return where if looks_like_key(node) else None
    if isinstance(node, dict):
        for k, v in node.items():
            found = _find_key_like(v, f"{where}.{k}" if where else str(k))
            if found:
                return found
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found = _find_key_like(v, f"{where}[{i}]")
            if found:
                return found
    return None


def load_settings(path: Path | str | None = None) -> Settings:
    """Load settings from ``path`` (default: the XDG config file).

    A missing default file yields default settings; a missing explicit path is
    an error. Errors never echo config values, so a pasted key is not leaked.
    """
    explicit = path is not None
    p = Path(path) if path is not None else default_config_path()
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        if explicit:
            raise ConfigError(f"config file not found: {p}") from None
        return Settings()
    except OSError as exc:
        raise ConfigError(f"cannot read {p}: {exc.strerror}") from None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        # TOMLDecodeError messages carry no values, only positions.
        raise ConfigError(f"invalid TOML in {p}: {exc}") from None
    bad = _find_key_like(data)
    if bad:
        raise ConfigError(
            f"{p}: value at '{bad}' looks like an API key. Store the NAME of "
            "an environment variable in api_key_env instead."
        )
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(x) for x in e['loc']) or '<root>'}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        )
        raise ConfigError(f"invalid config {p}: {problems}") from None
