"""Install/uninstall through Session for every adapter fixture scenario (M13).

Each scenario: install (confirm mode), apply one change so every managed file
exists, uninstall, and check the config tree is back byte-for-byte with no
managed files left behind.
"""

import os
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from buckwheat import cli
from buckwheat.adapters.alacritty import AlacrittyAdapter, toml_str
from buckwheat.adapters.base import (
    INCLUDE_BEGIN,
    INCLUDE_END,
    Adapter,
    CommandResult,
    FakeRunner,
    RenderResult,
)
from buckwheat.adapters.hyprland import HyprlandAdapter
from buckwheat.adapters.kitty import KittyAdapter
from buckwheat.adapters.wallpaper import WallpaperAdapter
from buckwheat.adapters.waybar import WaybarAdapter
from buckwheat.config import Settings
from buckwheat.plan import ChangePlan
from buckwheat.providers.fake import FakeProvider
from buckwheat.safety import snapshot as snaps
from buckwheat.safety.bootstrap import BootstrapEditError, check_include_edit
from buckwheat.session import Session, SessionError

FIXTURES = Path(__file__).parent / "fixtures"
V014 = {("alacritty", "--version"): CommandResult(0, "alacritty 0.14.0 (abc)\n")}
V013 = {("alacritty", "--version"): CommandResult(0, "alacritty 0.13.2 (abc)\n")}


@dataclass
class Home:
    root: Path
    config: Path
    state: Path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Home]:
    root = tmp_path / "home"
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(root / ".local" / "state"))
    if os.geteuid() == 0:  # atomic_write refuses root; the sandbox may be root
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
    state = root / ".local" / "state" / "buckwheat" / "managed.json"
    yield Home(root, root / ".config", state)


def tree(d: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(d)): p.read_bytes()
        for p in sorted(d.rglob("*"))
        if p.is_file()
    }


def own(adapter: Adapter, files: list[Path]) -> Adapter:
    # Instance-level allowlist: the class default is fixed at import time.
    adapter.managed_files = files  # type: ignore[misc]
    return adapter


# --- scenario builders: (adapter, op that writes every managed file) -------

Op = dict[str, object]
Builder = Callable[[Home], tuple[Adapter, Op]]


def kitty(text: bytes) -> Builder:
    def build(h: Home) -> tuple[Adapter, Op]:
        d = h.config / "kitty"
        d.mkdir(parents=True)
        (d / "kitty.conf").write_bytes(text)
        a = KittyAdapter(FakeRunner(), d, h.state)
        op = {"op": "set", "target": "kitty", "key": "font_size", "value": 12.0}
        return own(a, [a.managed_file]), op

    return build


def hyprland(scenario: str) -> Builder:
    def build(h: Home) -> tuple[Adapter, Op]:
        d = h.config / "hypr"
        shutil.copytree(FIXTURES / "hyprland" / scenario, d)
        a = HyprlandAdapter(FakeRunner(), config_dir=d)
        op = {"op": "set", "target": "hyprland", "key": "general.gaps_in", "value": 4}
        return own(a, [a.managed_path]), op

    return build


def alacritty(fixture: str, versions: dict[tuple[str, ...], CommandResult]) -> Builder:
    def build(h: Home) -> tuple[Adapter, Op]:
        d = h.config / "alacritty"
        d.mkdir(parents=True)
        (d / "alacritty.toml").write_bytes(
            (FIXTURES / "alacritty" / fixture).read_bytes()
        )
        a = AlacrittyAdapter(
            FakeRunner(versions),
            user_conf=d / "alacritty.toml",
            managed_conf=d / "buckwheat.toml",
            state_path=h.state,
            home=h.root,
            which=lambda name: f"/usr/bin/{name}",
        )
        op = {"op": "set", "target": "alacritty", "key": "font.size", "value": 11.0}
        return own(a, [a.managed_conf]), op

    return build


def waybar(scenario: str) -> Builder:
    def build(h: Home) -> tuple[Adapter, Op]:
        d = h.config / "waybar"
        shutil.copytree(FIXTURES / "waybar" / scenario, d)
        a = WaybarAdapter(d, FakeRunner(), sleep=lambda _s: None)
        op = {"op": "set", "target": "waybar", "key": "bar.height", "value": 30}
        return own(a, list(a.files)), op

    return build


def stowed(inner: Builder, rel: str) -> Builder:
    """The user's main config is a symlink into a dotfiles repo (stow)."""

    def build(h: Home) -> tuple[Adapter, Op]:
        adapter, op = inner(h)
        link = h.config / rel
        real = h.root / "dotfiles" / rel
        real.parent.mkdir(parents=True)
        link.replace(real)
        link.symlink_to(real)
        return adapter, op

    return build


KITTY = (FIXTURES / "kitty" / "kitty.conf").read_bytes()
SCENARIOS: dict[str, Builder] = {
    "kitty-pristine": kitty(KITTY),
    "kitty-crlf-no-final-newline": kitty(KITTY.replace(b"\n", b"\r\n").rstrip()),
    "kitty-empty": kitty(b""),
    "kitty-symlinked": stowed(kitty(KITTY), "kitty/kitty.conf"),
    "hyprland-pristine": hyprland("pristine"),
    "hyprland-weird": hyprland("weird"),
    "hyprland-symlinked": stowed(hyprland("pristine"), "hypr/hyprland.conf"),
    "alacritty-pristine": alacritty("pristine.toml", V014),
    "alacritty-crlf": alacritty("no_trailing_newline_crlf.toml", V014),
    "alacritty-general-table": alacritty("general_table.toml", V014),
    "alacritty-existing-import": alacritty("existing_import.toml", V014),
    "alacritty-legacy": alacritty("legacy_top_level.toml", V013),
    "alacritty-comments": alacritty("user_shadowed.toml", V014),
    "alacritty-symlinked": stowed(
        alacritty("pristine.toml", V014), "alacritty/alacritty.toml"
    ),
    "waybar-pristine": waybar("pristine"),
    "waybar-weird-jsonc-crlf": waybar("weird"),
    "waybar-symlinked": stowed(waybar("pristine"), "waybar/config.jsonc"),
}


def session(h: Home, adapter: Adapter, op: Op | None = None, **cfg: object) -> Session:
    provider = FakeProvider()
    if op is not None:
        plan = ChangePlan.model_validate({"version": 1, "summary": "t", "ops": [op]})
        provider = FakeProvider(default=plan)
    return Session(
        provider,
        Settings.model_validate(cfg),
        {adapter.name: adapter},
        home=h.root,
        state_path=h.state,
    )


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_install_then_uninstall_restores_files_byte_for_byte(
    home: Home, name: str
) -> None:
    adapter, op = SCENARIOS[name](home)
    original = tree(home.root)
    links = {p: p.readlink() for p in home.root.rglob("*") if p.is_symlink()}
    s = session(home, adapter, op)
    assert s.needs_install([adapter.name]) == [adapter.name]

    install = s.propose_install([adapter.name])
    assert install.skipped == () and not install.manual
    assert INCLUDE_BEGIN in install.diff or "import = [" in install.diff
    assert tree(home.root) == original  # previewing writes nothing
    s.keep(s.apply_bootstrap(install).id)
    assert s.needs_install([adapter.name]) == []
    assert adapter.read_state().bootstrap in ("bootstrapped", "user-shadowed")

    s.keep(s.apply(s.propose("change something")).id)  # all managed files exist

    removal = s.propose_uninstall()
    assert removal.skipped == ()
    s.keep(s.apply_bootstrap(removal).id)
    after = {k: v for k, v in tree(home.root).items() if "/state/" not in f"/{k}"}
    assert after == original, "files not restored byte-for-byte"
    for path, target in links.items():
        assert path.is_symlink() and path.readlink() == target
    assert s.needs_install([adapter.name]) == [adapter.name]


def test_install_then_revert_restores_and_reloads(home: Home) -> None:
    adapter, _ = SCENARIOS["waybar-pristine"](home)
    original = tree(home.config)
    s = session(home, adapter)
    change = s.apply_bootstrap(s.propose_install(["waybar"]))
    assert tree(home.config) != original
    runner = adapter.runner  # type: ignore[attr-defined]
    runner.calls.clear()
    result = s.revert(change.id)
    assert result.ok and tree(home.config) == original
    # waybar's install touches only user files; the revert still reloads it.
    assert any("-USR2" in c[0] for c in runner.calls)


def test_manual_mode_writes_nothing_and_shows_lines(home: Home) -> None:
    adapter, _ = SCENARIOS["hyprland-pristine"](home)
    original = tree(home.root)
    s = session(home, adapter, bootstrap="manual")
    p = s.propose_install(["hyprland"])
    assert p.manual and p.empty and p.adapters == ()
    assert "Add these lines at the end of ~/.config/hypr/hyprland.conf" in (
        p.instructions
    )
    block = f"{INCLUDE_BEGIN}\nsource = ~/.config/hypr/buckwheat.conf\n{INCLUDE_END}"
    assert p.instructions.endswith(block)
    with pytest.raises(SessionError):
        s.apply_bootstrap(p)
    assert tree(home.root) == original
    assert snaps.history() == []


def test_manual_mode_waybar_instructions_include_a_diff(home: Home) -> None:
    adapter, _ = SCENARIOS["waybar-pristine"](home)
    p = session(home, adapter, bootstrap="manual").propose_install(["waybar"])
    assert p.empty
    assert "Make this change to ~/.config/waybar/config.jsonc" in p.instructions
    assert '+  "include": [' in p.instructions
    assert "Add these lines at the end of ~/.config/waybar/style.css" in (
        p.instructions
    )


def test_manual_uninstall_keeps_files_until_the_include_is_removed(
    home: Home,
) -> None:
    adapter, op = SCENARIOS["kitty-pristine"](home)
    s = session(home, adapter, op)
    s.keep(s.apply_bootstrap(s.propose_install(["kitty"])).id)
    manual = session(home, adapter, bootstrap="manual")
    p = manual.propose_uninstall(["kitty"])
    assert p.empty and "Remove these lines" in p.instructions
    # The user removes the block by hand; then uninstall deletes our file.
    conf = home.config / "kitty" / "kitty.conf"
    conf.write_bytes(KITTY)
    p = manual.propose_uninstall(["kitty"])
    assert p.instructions == ""
    assert p.deletes == {(home.config / "kitty" / "buckwheat.conf").resolve()}


def test_wallpaper_has_no_install_but_uninstall_deletes_its_file(home: Home) -> None:
    conf = home.config / "hypr" / "hyprpaper.conf"
    adapter = own(
        WallpaperAdapter(
            FakeRunner(), conf_path=conf, state_path=home.state, home=home.root
        ),
        [conf],
    )
    conf.parent.mkdir(parents=True)
    conf.write_text(adapter._format({}))  # type: ignore[attr-defined]
    s = session(home, adapter)
    assert s.propose_install(["wallpaper"]).skipped == (
        "wallpaper: has no include to install",
    )
    p = s.propose_uninstall()
    assert p.deletes == {conf.resolve()}
    s.apply_bootstrap(p)
    assert not conf.exists()


def test_refused_install_is_skipped_with_reason(home: Home) -> None:
    adapter, _ = alacritty("multiline_import.toml", V014)(home)
    p = session(home, adapter).propose_install(["alacritty"])
    assert p.empty and p.skipped and p.skipped[0].startswith("alacritty: ")


class _Rogue:
    """Wraps a real adapter but returns a bad install edit."""

    def __init__(self, inner: Adapter, result: Callable[[Path], RenderResult]):
        self._inner = inner
        self._result = result

    def __getattr__(self, attr: str) -> object:
        return getattr(self._inner, attr)

    def install(self, plan: object) -> RenderResult:
        return self._result(self._inner.bootstrap_files()[0])


@pytest.mark.parametrize(
    "bad",
    [
        lambda p: RenderResult({p: "rewritten\n"}),  # edits outside a block
        lambda p: RenderResult({p.parent / "other.conf": "x"}),  # not allowlisted
        lambda p: RenderResult({p: p.read_text() + "evil\n# >>> buckwheat x"}),
    ],
)
def test_session_rejects_edits_outside_the_marker_block(
    home: Home, bad: Callable[[Path], RenderResult]
) -> None:
    adapter, _ = SCENARIOS["kitty-pristine"](home)
    original = tree(home.root)
    rogue = _Rogue(adapter, bad)
    s = Session(
        FakeProvider(),
        Settings(),
        {"kitty": rogue},  # type: ignore[dict-item]
        home=home.root,
        state_path=home.state,
    )
    p = s.propose_install(["kitty"])
    assert p.empty and p.skipped and p.skipped[0].startswith("kitty: ")
    assert tree(home.root) == original


def test_uninstall_never_deletes_the_user_file(home: Home) -> None:
    adapter, _ = SCENARIOS["kitty-pristine"](home)
    rogue = _Rogue(adapter, lambda p: RenderResult())
    rogue.uninstall = lambda: RenderResult(  # type: ignore[method-assign]
        deletes=frozenset({adapter.bootstrap_files()[0]})
    )
    s = Session(
        FakeProvider(),
        Settings(),
        {"kitty": rogue},  # type: ignore[dict-item]
        home=home.root,
        state_path=home.state,
    )
    p = s.propose_uninstall()
    assert p.empty and p.skipped


# --- check_include_edit ------------------------------------------------------

BLOCK = f"{INCLUDE_BEGIN}\ninclude buckwheat.conf\n{INCLUDE_END}\n"
ENTRY = toml_str("~/.config/alacritty/buckwheat.toml")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("a\n", "a\n" + BLOCK),
        ("a", "a\n" + BLOCK.rstrip("\n")),
        ("", BLOCK),
        ("{\n}", "{\n  // " + BLOCK.rstrip("\n").replace("\n", "\n  ") + "\n}"),
        ('import = ["a.toml"]\n', f'import = ["a.toml", {ENTRY}]\n'),
        ("x = 1\r\nimport = []\r\n", f"x = 1\r\nimport = [{ENTRY}]\r\n"),
    ],
)
def test_check_allows_block_and_array_edits(old: str, new: str) -> None:
    check_include_edit(old, new, array_entry=ENTRY)
    check_include_edit(new, old, array_entry=ENTRY)  # the uninstall direction


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("a\n", "b\n" + BLOCK),  # user text changed too
        ("a\n", "a\nfont_size 9\n"),  # no markers at all
        ("a\n", "a\n" + BLOCK + "x\n"),  # extra text after the block
        ("a\n", "a\n" + BLOCK + BLOCK),  # two blocks
        ("a\n" + BLOCK, "a\n" + BLOCK + BLOCK),  # a block already there
        ('import = ["a"]\n', f'import = [{ENTRY}, "a"]\n'),  # not appended
        ('import = ["a"]\n', 'import = ["a", "/etc/evil.toml"]\n'),  # other entry
        ('x = 1\nimport = ["a"]\n', f'x = 2\nimport = ["a", {ENTRY}]\n'),
    ],
)
def test_check_rejects_other_edits(old: str, new: str) -> None:
    with pytest.raises(BootstrapEditError):
        check_include_edit(old, new, array_entry=ENTRY)


def test_array_exception_needs_the_entry() -> None:
    with pytest.raises(BootstrapEditError):
        check_include_edit('import = ["a"]\n', f'import = ["a", {ENTRY}]\n')


# --- buckwheat uninstall ------------------------------------------------------


def installed(home: Home, monkeypatch: pytest.MonkeyPatch, **cfg: object) -> Session:
    """kitty and hyprland, both installed; cli.make_session returns them."""
    k, _ = SCENARIOS["kitty-pristine"](home)
    hy, _ = SCENARIOS["hyprland-pristine"](home)
    s = Session(
        FakeProvider(),
        Settings.model_validate(cfg),
        {"kitty": k, "hyprland": hy},
        home=home.root,
        state_path=home.state,
    )
    s.keep(s.apply_bootstrap(s.propose_install(["kitty", "hyprland"])).id)
    monkeypatch.setattr(cli, "make_session", lambda settings: s)
    return s


def test_cli_uninstall_asks_and_no_means_nothing_changes(
    home: Home, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installed(home, monkeypatch)
    before = tree(home.config)
    prompts: list[str] = []

    def no(prompt: str) -> str:
        prompts.append(prompt)
        return "n"

    assert cli.run(["uninstall"], ask=no) == 1
    out = capsys.readouterr().out
    assert "-# >>> buckwheat (managed include) >>>" in out  # the diff is shown
    assert prompts == ["Remove buckwheat from hyprland, kitty? [y/N] "]
    assert "Nothing changed." in out
    assert tree(home.config) == before


def test_cli_uninstall_one_target_with_yes(
    home: Home, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installed(home, monkeypatch)

    def never(prompt: str) -> str:
        raise AssertionError("--yes must not ask")

    assert cli.run(["uninstall", "kitty", "--yes"], ask=never) == 0
    assert "Removed." in capsys.readouterr().out
    assert (home.config / "kitty" / "kitty.conf").read_bytes() == KITTY
    assert not (home.config / "kitty" / "buckwheat.conf").exists()
    assert INCLUDE_BEGIN in (home.config / "hypr" / "hyprland.conf").read_text()


def test_cli_uninstall_yes_answer_removes_all(
    home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed(home, monkeypatch)
    assert cli.run(["uninstall"], ask=lambda _p: "y") == 0
    assert INCLUDE_BEGIN not in (home.config / "hypr" / "hyprland.conf").read_text()
    assert not list(home.config.rglob("buckwheat.*"))
    assert cli.run(["uninstall", "--yes"]) == 0  # second run: nothing to do


def test_cli_uninstall_unknown_target(
    home: Home, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installed(home, monkeypatch)
    assert cli.run(["uninstall", "nope", "--yes"]) == 2
    assert "unknown target 'nope'" in capsys.readouterr().err


def test_cli_uninstall_manual_prints_lines_and_writes_nothing(
    home: Home, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    s = installed(home, monkeypatch)
    manual = Session(
        FakeProvider(),
        Settings(bootstrap="manual"),
        s.adapters,
        home=home.root,
        state_path=home.state,
    )
    monkeypatch.setattr(cli, "make_session", lambda settings: manual)
    before = tree(home.config)
    assert cli.run(["uninstall", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Remove these lines from the end of ~/.config/kitty/kitty.conf" in out
    assert tree(home.config) == before


# --- leftovers: include lines uninstall leaves in the user's files -------------


def test_uninstall_proposal_has_no_leftovers_when_it_round_trips(home: Home) -> None:
    adapter, op = SCENARIOS["kitty-pristine"](home)
    s = session(home, adapter, op)
    s.keep(s.apply_bootstrap(s.propose_install(["kitty"])).id)
    p = s.propose_uninstall(["kitty"])
    assert p.deletes and p.left_behind == ()


def test_cli_uninstall_reports_a_bare_include_left_in_kitty(
    home: Home, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Repro 1: a bare `include buckwheat.conf` outside any marker block.
    installed(home, monkeypatch)
    conf = home.config / "kitty" / "kitty.conf"
    bare = KITTY + b"include buckwheat.conf\n"
    conf.write_bytes(bare)

    assert cli.run(["uninstall", "kitty", "--yes"]) == 0
    out = capsys.readouterr().out
    assert conf.read_bytes() == bare  # nothing outside the markers is edited
    assert not (home.config / "kitty" / "buckwheat.conf").exists()
    assert "kitty:" in out and "include buckwheat.conf" in out
    assert "by hand" in out
    assert not any(line.startswith("Removed.") for line in out.splitlines())

    # Nothing left for buckwheat to delete now, but the leftover is still shown.
    assert cli.run(["uninstall", "kitty", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Nothing for buckwheat to remove." in out
    assert "include buckwheat.conf" in out
    assert conf.read_bytes() == bare


def test_cli_uninstall_reports_alacritty_import_left_empty(
    home: Home, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Repro 2: buckwheat's entry sits in the user's [general] import list.
    # The array_entry exception lets uninstall remove exactly that entry; the
    # empty list it leaves behind must be reported, not hidden by "Removed.".
    adapter, op = SCENARIOS["alacritty-pristine"](home)
    s = session(home, adapter, op)
    s.keep(s.apply_bootstrap(s.propose_install(["alacritty"])).id)
    conf = home.config / "alacritty" / "alacritty.toml"
    conf.write_bytes(b'[general]\nimport = ["~/.config/alacritty/buckwheat.toml"]\n')
    monkeypatch.setattr(cli, "make_session", lambda settings: s)

    assert cli.run(["uninstall", "alacritty", "--yes"]) == 0
    out = capsys.readouterr().out
    assert conf.read_bytes() == b"[general]\nimport = []\n"
    assert not (home.config / "alacritty" / "buckwheat.toml").exists()
    assert "import = []" in out and "by hand" in out
    assert not any(line.startswith("Removed.") for line in out.splitlines())


def test_cli_uninstall_reports_a_failed_target_as_left(
    home: Home, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The include cannot be removed safely: alacritty refuses, so it is left.
    adapter, op = SCENARIOS["alacritty-pristine"](home)
    s = session(home, adapter, op)
    s.keep(s.apply_bootstrap(s.propose_install(["alacritty"])).id)
    conf = home.config / "alacritty" / "alacritty.toml"
    conf.write_bytes(
        b'[general]\nimport = ["~/.config/alacritty/buckwheat.toml", "x"]\n'
        b"live_config_reload = true\n"
    )
    monkeypatch.setattr(cli, "make_session", lambda settings: s)
    before = conf.read_bytes()

    assert cli.run(["uninstall", "alacritty", "--yes"]) == 0
    out = capsys.readouterr().out
    assert conf.read_bytes() == before
    assert "alacritty:" in out and "remove it by hand" in out
    assert not any(line.startswith("Removed.") for line in out.splitlines())


def test_cli_uninstall_reports_a_bare_source_line_left_in_hyprland(
    home: Home, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The block is removed; a copy of its `source` line outside it is reported.
    installed(home, monkeypatch)
    conf = home.config / "hypr" / "hyprland.conf"
    source = next(ln for ln in conf.read_text().splitlines() if ln.startswith("source"))
    conf.write_text(conf.read_text() + source + "\n")

    assert cli.run(["uninstall", "hyprland", "--yes"]) == 0
    out = capsys.readouterr().out
    assert not (home.config / "hypr" / "buckwheat.conf").exists()
    assert "hyprland:" in out and source in out and "by hand" in out
    assert not any(line.startswith("Removed.") for line in out.splitlines())


def test_uninstall_proposal_reports_a_bare_waybar_import_left_in_style_css(
    home: Home,
) -> None:
    adapter, op = SCENARIOS["waybar-pristine"](home)
    s = session(home, adapter, op)
    s.keep(s.apply_bootstrap(s.propose_install(["waybar"])).id)
    css = home.config / "waybar" / "style.css"
    css.write_text(css.read_text() + '@import url("buckwheat.css");\n')

    p = s.propose_uninstall(["waybar"])
    assert any(line.startswith("waybar: style.css still has") for line in p.left_behind)
