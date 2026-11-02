import json

import pytest
from pydantic import TypeAdapter, ValidationError

from buckwheat.plan import (
    ChangePlan,
    FontOp,
    PaletteOp,
    PlanContext,
    Problem,
    SetOp,
    TargetInfo,
    WallpaperOp,
)

EXAMPLE = {
    "version": 1,
    "summary": "Nord theme with JetBrains Mono 12 and a dimmer wallpaper",
    "ops": [
        {
            "op": "palette",
            "palette": "nord",
            "targets": ["kitty", "alacritty", "hyprland", "waybar"],
        },
        {
            "op": "font",
            "target": "kitty",
            "role": "terminal",
            "family": "JetBrains Mono",
            "size": 12.0,
        },
        {"op": "set", "target": "hyprland", "key": "general.gaps_in", "value": 6},
        {"op": "set", "target": "waybar", "key": "bar.height", "value": 30},
        {"op": "wallpaper", "path": "~/Pictures/walls/forest.jpg", "mode": "fill"},
    ],
}


def plan_with(op: dict) -> dict:
    return {"version": 1, "summary": "x", "ops": [op]}


def test_example_round_trip():
    plan = ChangePlan.model_validate(EXAMPLE)
    assert [type(o) for o in plan.ops] == [
        PaletteOp,
        FontOp,
        SetOp,
        SetOp,
        WallpaperOp,
    ]
    dumped = json.loads(plan.model_dump_json())
    assert dumped["ops"][0] == {**EXAMPLE["ops"][0], "variant": None}
    assert dumped["ops"][1:] == EXAMPLE["ops"][1:]
    assert ChangePlan.model_validate_json(plan.model_dump_json()) == plan


def test_discriminator_selects_model():
    op = TypeAdapter(ChangePlan).validate_python(EXAMPLE).ops[2]
    assert isinstance(op, SetOp)


def test_unknown_op_rejected():
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        ChangePlan.model_validate(plan_with({"op": "exec", "cmd": "id"}))


def test_missing_discriminator_rejected():
    with pytest.raises(ValidationError, match="union_tag_not_found"):
        ChangePlan.model_validate(plan_with({"target": "kitty"}))


def test_fields_of_other_op_rejected():
    bad = {"op": "set", "target": "kitty", "key": "a", "value": 1, "path": "/x"}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ChangePlan.model_validate(plan_with(bad))


def test_unknown_plan_field_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ChangePlan.model_validate({**EXAMPLE, "extra": 1})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(version=2),
        lambda p: p.update(summary="x" * 201),
        lambda p: p.update(ops=[]),
        lambda p: p.update(ops=[EXAMPLE["ops"][2]] * 21),
    ],
)
def test_invalid_plans(mutate):
    data = json.loads(json.dumps(EXAMPLE))
    mutate(data)
    with pytest.raises(ValidationError):
        ChangePlan.model_validate(data)


@pytest.mark.parametrize(
    "op",
    [
        {"op": "set", "target": "bash", "key": "a", "value": 1},
        {"op": "set", "target": "kitty", "key": "k" * 65, "value": 1},
        {"op": "set", "target": "kitty", "key": "a", "value": ["x"]},
        {"op": "set", "target": "kitty", "key": "a", "value": {"x": 1}},
        {"op": "palette", "palette": "Nord", "targets": ["kitty"]},
        {"op": "palette", "palette": "nord", "targets": []},
        {"op": "palette", "palette": "nord", "variant": "A B", "targets": ["kitty"]},
        {"op": "font", "target": "kitty", "role": "x", "family": "F"},
        {"op": "font", "target": "kitty", "role": "ui", "family": ""},
        {"op": "font", "target": "kitty", "role": "ui", "family": "F", "size": 5},
        {"op": "font", "target": "kitty", "role": "ui", "family": "F", "size": 49},
        {"op": "wallpaper", "path": "p" * 513},
        {"op": "wallpaper", "path": "p", "mode": "stretch"},
    ],
)
def test_invalid_ops(op):
    with pytest.raises(ValidationError):
        ChangePlan.model_validate(plan_with(op))


def test_models_are_frozen():
    plan = ChangePlan.model_validate(EXAMPLE)
    with pytest.raises(ValidationError):
        plan.summary = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        plan.ops[0].op = "set"  # type: ignore[misc]


def test_wallpaper_mode_default():
    plan = ChangePlan.model_validate(plan_with({"op": "wallpaper", "path": "a.png"}))
    assert plan.ops[0].mode == "fill"  # type: ignore[union-attr]


def test_json_schema_export():
    schema = ChangePlan.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["version"]["const"] == 1
    ops = schema["properties"]["ops"]
    assert ops["minItems"] == 1 and ops["maxItems"] == 20
    assert ops["items"]["discriminator"]["propertyName"] == "op"
    assert set(ops["items"]["discriminator"]["mapping"]) == {
        "set",
        "palette",
        "font",
        "wallpaper",
    }
    for name in ("SetOp", "PaletteOp", "FontOp", "WallpaperOp"):
        assert schema["$defs"][name]["additionalProperties"] is False
    json.dumps(schema)  # serialisable


def test_problem_and_context():
    p = Problem(op_index=0, code="unknown_key", message="nope")
    assert p.op_index == 0
    with pytest.raises(ValidationError):
        Problem(op_index=-1, code="c", message="m")
    with pytest.raises(ValidationError):
        Problem(code="", message="m")
    ctx = PlanContext(
        request_id="r1",
        targets=[TargetInfo(name="kitty", detected=True, current_values={"a": 1})],
    )
    assert ctx.targets[0].managed_installed is False
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PlanContext(request_id="r1", dotfiles="whole file")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        TargetInfo(name="bash", detected=True)  # type: ignore[arg-type]
