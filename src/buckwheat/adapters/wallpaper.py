"""Wallpaper adapter for hyprpaper (design.md sections 3 and 3.5).

Persists the choice in ``~/.config/hypr/hyprpaper.conf`` only when that file is
absent, empty, or already carries the managed header. A user-owned file is never
edited: ops are reported as unsupported instead. Applying at runtime goes through
``hyprctl hyprpaper`` with the path as a single argv element.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import ClassVar

from buckwheat.adapters.base import (
    MANAGED_HEADER,
    BootstrapPlan,
    BootstrapStatus,
    CommandError,
    CommandRunner,
    Detection,
    ReloadResult,
    RenderResult,
    SubprocessRunner,
    TargetState,
    load_managed_state,
    merge_state,
    register,
)
from buckwheat.plan.context import Problem
from buckwheat.plan.models import ChangePlan, Op, Scalar, WallpaperOp
from buckwheat.plan.validators import validate_common


def _default_conf_path() -> Path:
    # Looked up on each call, not once at import, so a changed HOME is honoured.
    return Path.home() / ".config" / "hypr" / "hyprpaper.conf"


CONF_PATH = _default_conf_path()  # class-level default only
# hyprpaper's own prefixes: none = cover (fill), "contain:", "tile:".
MODE_PREFIX = {"fill": "", "fit": "contain:", "tile": "tile:"}


class WallpaperAdapter:
    name: ClassVar[str] = "wallpaper"
    managed_files: ClassVar[list[Path]] = [CONF_PATH]

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        conf_path: Path | None = None,
        state_path: Path | None = None,
        home: Path | None = None,
        extra_wallpaper_dirs: Sequence[str | Path] = (),
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.runner = runner or SubprocessRunner()
        self.conf_path = conf_path or _default_conf_path()
        self.state_path = state_path
        self.home = home if home is not None else Path.home()
        self.extra_wallpaper_dirs = extra_wallpaper_dirs
        self.which = which

    # --- ownership of hyprpaper.conf ---------------------------------------

    def _ownership(self) -> BootstrapStatus:
        try:
            text = self.conf_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "none"
        except (OSError, UnicodeDecodeError):
            return "weird"
        if not text.strip():
            return "none"
        if text.startswith(MANAGED_HEADER):
            return "bootstrapped"
        return "user-shadowed"

    # --- Adapter protocol ---------------------------------------------------

    def detect(self) -> Detection:
        installed = self.which("hyprpaper") is not None
        running = False
        if installed:
            try:
                running = self.runner.run(["pgrep", "-x", "hyprpaper"]).returncode == 0
            except CommandError:
                running = False
        return Detection(
            installed=installed,
            config_dir=self.conf_path.parent,
            running=running,
        )

    def read_state(self) -> TargetState:
        values = load_managed_state(self.state_path).get("wallpaper", {})
        status = self._ownership()
        shadowed = ("path", "mode") if status == "user-shadowed" else ()
        return TargetState(values=values, bootstrap=status, shadowed_keys=shadowed)

    def validate(self, op: Op) -> list[Problem]:
        if not isinstance(op, WallpaperOp):
            return [
                Problem(
                    code="unsupported-op",
                    message="the wallpaper adapter only handles wallpaper ops",
                )
            ]
        problems = validate_common(
            ChangePlan(version=1, summary="wallpaper", ops=[op]),
            home=self.home,
            extra_wallpaper_dirs=self.extra_wallpaper_dirs,
        )
        if op.mode not in MODE_PREFIX:
            problems.append(
                Problem(
                    code="unsupported-mode",
                    message=f"hyprpaper does not support mode {op.mode!r}",
                )
            )
        if self._ownership() in ("user-shadowed", "weird"):
            problems.append(
                Problem(
                    code="unsupported",
                    message=(
                        "hyprpaper.conf is not managed by buckwheat; "
                        "it will not be edited"
                    ),
                )
            )
        return problems

    def _checked_path(self, op: WallpaperOp) -> str:
        """Re-validate (defence in depth) and return the resolved real path."""
        problems = self.validate(op)
        if problems:
            raise ValueError("; ".join(p.message for p in problems))
        raw = op.path
        if raw == "~" or raw.startswith("~/"):
            raw = str(self.home) + raw[1:]
        return str(Path(raw).resolve(strict=True))

    def allowed_keys(self) -> list[str]:
        return ["mode", "path"]

    def state_updates(
        self, ops: Sequence[Op], state: TargetState
    ) -> Mapping[str, Scalar]:
        """The resolved path and mode of the last wallpaper op."""
        updates: dict[str, Scalar] = {}
        for op in ops:
            if not isinstance(op, WallpaperOp):
                raise ValueError("the wallpaper adapter only handles wallpaper ops")
            updates = {"path": self._checked_path(op), "mode": op.mode}
        return updates

    def render(self, ops: list[Op], state: TargetState) -> RenderResult:
        updates = self.state_updates(ops, state)
        values = merge_state(state.values, updates)
        return RenderResult(files={self.conf_path: self._format(values)})

    @staticmethod
    def _format(values: Mapping[str, Scalar]) -> str:
        lines = [MANAGED_HEADER]
        path = values.get("path")
        if isinstance(path, str) and path:
            mode = str(values.get("mode", "fill"))
            lines.append(f"preload = {path}")
            lines.append(f"wallpaper = ,{MODE_PREFIX.get(mode, '')}{path}")
        return "\n".join(lines) + "\n"

    def reload(self) -> ReloadResult:
        values = load_managed_state(self.state_path).get("wallpaper", {})
        path, mode = values.get("path"), str(values.get("mode", "fill"))
        if not isinstance(path, str) or not path:
            return ReloadResult(True, "no wallpaper set")
        spec = f",{MODE_PREFIX.get(mode, '')}{path}"
        for argv in (
            ["hyprctl", "hyprpaper", "preload", path],
            ["hyprctl", "hyprpaper", "wallpaper", spec],
        ):
            try:
                res = self.runner.run(argv)
            except CommandError as exc:
                return ReloadResult(False, str(exc))
            out = res.stdout.strip()
            if res.returncode != 0 or out.lower() not in ("", "ok"):
                return ReloadResult(
                    False, out or res.stderr.strip() or "hyprctl failed"
                )
        try:  # best effort: free the previous image
            self.runner.run(["hyprctl", "hyprpaper", "unload", "unused"])
        except CommandError:
            pass
        return ReloadResult(True, "wallpaper applied")

    def bootstrap_plan(self) -> BootstrapPlan | None:
        """None: hyprpaper.conf is ours entirely, no include to confirm."""
        return None

    def bootstrap_files(self) -> list[Path]:
        return []

    def install(self, plan: BootstrapPlan) -> RenderResult:
        """No include line is needed: the whole file is ours. Header only."""
        if self._ownership() not in ("none", "bootstrapped"):
            raise ValueError("hyprpaper.conf is user-owned; refusing to replace it")
        values = load_managed_state(self.state_path).get("wallpaper", {})
        return RenderResult(files={self.conf_path: self._format(values)})

    def uninstall(self) -> RenderResult:
        """Delete our hyprpaper.conf. A user-owned file is left untouched."""
        if self._ownership() != "bootstrapped":
            return RenderResult()
        return RenderResult(deletes=frozenset({self.conf_path}))


register("wallpaper")(WallpaperAdapter)
