"""Name-based provider registry."""

from collections.abc import Callable, Mapping
from typing import Any

from buckwheat.providers.base import Provider, ProviderError

# A factory takes the provider's own config table.
Factory = Callable[[Mapping[str, Any]], Provider]

_REGISTRY: dict[str, Factory] = {}


def register(name: str) -> Callable[[Factory], Factory]:
    """Class/function decorator: register a factory under `name`."""

    def deco(factory: Factory) -> Factory:
        if name in _REGISTRY:
            raise ValueError(f"provider already registered: {name}")
        _REGISTRY[name] = factory
        return factory

    return deco


def available() -> list[str]:
    return sorted(_REGISTRY)


def create(name: str, cfg: Mapping[str, Any] | None = None) -> Provider:
    try:
        factory = _REGISTRY[name]
    except KeyError:
        known = ", ".join(available()) or "none"
        raise ProviderError(f"unknown provider {name!r} (known: {known})") from None
    return factory(cfg or {})
