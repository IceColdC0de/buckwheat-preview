"""FakeProvider: canned plans for offline tests. Registered as "fake"."""

from collections.abc import Mapping
from typing import Any, ClassVar

from buckwheat.plan import ChangePlan, PlanContext
from buckwheat.providers.base import ProviderError
from buckwheat.providers.registry import register


class FakeProvider:
    """Returns a canned plan keyed by request text, or raises a set error.

    Records every call in `calls` so tests can check what was sent.
    """

    name: ClassVar[str] = "fake"

    def __init__(
        self,
        plans: Mapping[str, ChangePlan] | None = None,
        *,
        default: ChangePlan | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self.plans = dict(plans or {})
        self.default = default
        self.error = error
        self.calls: list[tuple[str, PlanContext]] = []

    def plan(self, request: str, context: PlanContext) -> ChangePlan:
        self.calls.append((request, context))
        if self.error is not None:
            raise self.error
        plan = self.plans.get(request, self.default)
        if plan is None:
            raise ProviderError(f"FakeProvider has no canned plan for {request!r}")
        return plan


@register("fake")
def _create_fake(cfg: Mapping[str, Any]) -> FakeProvider:
    return FakeProvider()
