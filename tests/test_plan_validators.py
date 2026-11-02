from pathlib import Path

import pytest

from buckwheat.plan import ChangePlan, Problem
from buckwheat.plan.validators import validate_common


@pytest.fixture
def home(tmp_path: Path) -> Path:
    walls = tmp_path / "Pictures" / "walls"
    walls.mkdir(parents=True)
    (walls / "forest.jpg").write_bytes(b"x")
    (walls / "notes.txt").write_text("x")
    (tmp_path / "Pictures" / "sub").mkdir()
    secret = tmp_path / ".ssh"
    secret.mkdir()
    (secret / "id.png").write_bytes(b"x")
    (walls / "link.png").symlink_to(secret / "id.png")
    return tmp_path


def plan(*ops: dict, summary: str = "ok") -> ChangePlan:
    return ChangePlan.model_validate(
        {"version": 1, "summary": summary, "ops": list(ops)}
    )


def set_op(key: str, value: object, target: str = "kitty") -> dict:
    return {"op": "set", "target": target, "key": key, "value": value}


def font_op(family: str, size: float | None = 12.0) -> dict:
    return {
        "op": "font",
        "target": "kitty",
        "role": "terminal",
        "family": family,
        "size": size,
    }


def wall_op(path: str) -> dict:
    return {"op": "wallpaper", "path": path}


VALID_OPS = [
    set_op("background_opacity", 0.9),
    set_op("general.gaps_in", 6, "hyprland"),
    set_op("cursor_shape", "block"),
    set_op("background", "#2e3440"),
    set_op("colors.primary.foreground", "#D8DEE9", "alacritty"),
    set_op("decoration.blur.enabled", True, "hyprland"),
    font_op("JetBrains Mono"),
    font_op("Fira Code+Nerd_Font-1.0", None),
    {"op": "palette", "palette": "nord", "targets": ["kitty", "waybar"]},
    wall_op("~/Pictures/walls/forest.jpg"),
]


@pytest.mark.parametrize("op", VALID_OPS)
def test_valid_ops_have_no_problems(op: dict, home: Path) -> None:
    assert validate_common(plan(op), home=home) == []


def test_valid_full_plan(home: Path) -> None:
    assert validate_common(plan(*VALID_OPS, summary="Nord theme"), home=home) == []


# (id, op, expected code); each op is index 0 of its plan.
HOSTILE: list[tuple[str, dict, str]] = [
    ("newline-value", set_op("cursor_shape", "block\nexec = evil"), "control-chars"),
    ("cr-value", set_op("cursor_shape", "a\rb"), "control-chars"),
    ("nul-value", set_op("cursor_shape", "a\0b"), "control-chars"),
    ("escape-value", set_op("cursor_shape", "a\x1b[31m"), "control-chars"),
    ("line-separator", set_op("cursor_shape", "a b"), "control-chars"),
    ("newline-key", set_op("a\nexec", 1), "control-chars"),
    ("backtick", set_op("cursor_shape", "`id`"), "bad-chars"),
    ("dollar-paren", set_op("cursor_shape", "$(id)"), "bad-chars"),
    ("dollar-brace", set_op("cursor_shape", "${HOME}"), "bad-chars"),
    ("semicolon", set_op("cursor_shape", "a; rm -rf ~"), "bad-chars"),
    ("pipe", set_op("cursor_shape", "a|b"), "bad-chars"),
    ("ampersand", set_op("cursor_shape", "a&b"), "bad-chars"),
    ("redirect-out", set_op("cursor_shape", "a>b"), "bad-chars"),
    ("redirect-in", set_op("cursor_shape", "a<b"), "bad-chars"),
    ("backslash", set_op("cursor_shape", "a\\b"), "bad-chars"),
    ("meta-in-key", set_op("a;b", 1), "bad-chars"),
    ("oversized-value", set_op("cursor_shape", "a" * 201), "too-long"),
    ("nan-value", set_op("background_opacity", float("nan")), "bad-number"),
    ("inf-value", set_op("background_opacity", float("inf")), "bad-number"),
    ("color-short", set_op("background", "#fff"), "bad-color"),
    ("color-no-hash", set_op("foreground", "2e3440"), "bad-color"),
    ("color-nonhex", set_op("color3", "#gggggg"), "bad-color"),
    ("color-trailing-newline", set_op("background", "#2e3440\n"), "control-chars"),
    ("color-name", set_op("general.col.active_border", "red", "hyprland"), "bad-color"),
    ("color-not-string", set_op("background", 12345), "bad-color"),
    ("hash-value-non-color-key", set_op("cursor_shape", "#abc"), "bad-color"),
    ("font-shell", font_op("Mono; rm -rf /"), "bad-font"),
    ("font-newline", font_op("Mono\nexec"), "bad-font"),
    ("font-trailing-newline", font_op("Mono\n"), "bad-font"),
    ("font-quote", font_op('Mono"'), "bad-font"),
    ("font-unicode", font_op("Mönö"), "bad-font"),
    ("wall-relative", wall_op("Pictures/walls/forest.jpg"), "bad-path"),
    ("wall-empty", wall_op(""), "bad-path"),
    ("wall-newline", wall_op("~/Pictures/walls/forest.jpg\n"), "control-chars"),
    ("wall-nul", wall_op("~/Pictures/walls/f\0.jpg"), "control-chars"),
    ("wall-traversal", wall_op("~/Pictures/../.ssh/id.png"), "path-traversal"),
    ("wall-absolute-outside", wall_op("/etc/passwd"), "path-not-allowed"),
    ("wall-other-home", wall_op("~/.ssh/id.png"), "path-not-allowed"),
    ("wall-symlink-escape", wall_op("~/Pictures/walls/link.png"), "path-not-allowed"),
    ("wall-missing", wall_op("~/Pictures/walls/nope.jpg"), "bad-path"),
    ("wall-directory", wall_op("~/Pictures/sub"), "not-a-file"),
    ("wall-bad-extension", wall_op("~/Pictures/walls/notes.txt"), "bad-extension"),
]


@pytest.mark.parametrize(
    ("op", "code"), [(o, c) for _, o, c in HOSTILE], ids=[i for i, _, _ in HOSTILE]
)
def test_hostile_op_rejected(op: dict, code: str, home: Path) -> None:
    problems = validate_common(plan(op), home=home)
    assert code in {p.code for p in problems}
    assert all(p.op_index == 0 for p in problems)


def test_problem_index_points_at_bad_op(home: Path) -> None:
    problems = validate_common(
        plan(set_op("cursor_shape", "block"), font_op("a;b")), home=home
    )
    assert [(p.op_index, p.code) for p in problems] == [(1, "bad-font")]


def test_summary_control_chars_is_plan_level(home: Path) -> None:
    problems = validate_common(plan(font_op("Mono"), summary="hi\nthere"), home=home)
    assert problems == [
        Problem(
            op_index=None,
            code="control-chars",
            message="summary contains control characters",
        )
    ]


def test_wallpaper_too_large(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("buckwheat.plan.validators.MAX_WALLPAPER_BYTES", 0)
    problems = validate_common(plan(wall_op("~/Pictures/walls/forest.jpg")), home=home)
    assert [p.code for p in problems] == ["too-large"]


def test_extra_wallpaper_dir_allowed(
    home: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    extra = tmp_path_factory.mktemp("extra")
    img = extra / "a.webp"
    img.write_bytes(b"x")
    p = plan(wall_op(str(img)))
    assert [x.code for x in validate_common(p, home=home)] == ["path-not-allowed"]
    assert validate_common(p, home=home, extra_wallpaper_dirs=[extra]) == []
