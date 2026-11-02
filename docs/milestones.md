# Milestones

Each item is one small PR. "Needs" lists PRs that must merge first; items with
the same needs can run in parallel. Contracts are in design.md; listed here is
what each PR must expose so others can code against it.

| ID | Task | Needs | Contract / deliverable |
|----|------|-------|------------------------|
| M0 | Project skeleton: pyproject (python>=3.11, deps anthropic, openai, pydantic, textual; dev: pytest, ruff, mypy), `src/buckwheat/` empty packages, CI (no network) | design | package imports; `pytest` runs |
| M1 | `plan` models: ChangePlan, ops, Problem, PlanContext, TargetInfo | M0 | Models exactly as design.md 2.2; JSON schema export test |
| M2 | `plan/validators.py` + hostile-plan corpus | M1 | `validate_common(plan) -> list[Problem]`; string/path/font/colour rules |
| M3 | `plan/palettes.py` + palette TOMLs (nord, gruvbox, catppuccin, dracula, tokyo-night, solarized, rose-pine) | M1 | `load_palette(name, variant=None) -> PaletteVariant`; `list_palettes()` |
| M4 | `config.py`: XDG paths, Settings model, rejects key-like values | M0 | `load_settings(path=None) -> Settings` |
| M5 | `safety`: guard_path, atomic_write, diff | M0 | `atomic_write(path, text, allowlist)`, `unified_diff(old, new, path)` |
| M6 | `safety/snapshot.py`: snapshot, restore, history, pending-revert | M5 | `snapshot(paths, plan) -> SnapshotId`, `restore(id)`, `mark(id, state)`, `recover_pending()` |
| M7 | `adapters/base.py`: Adapter protocol, KeySpec, CommandRunner, managed state file, registry | M1, M5 | protocol as in design.md 3; `FakeRunner` for tests |
| M8a | kitty adapter + fixtures | M7, M3 | Adapter impl; golden-file tests |
| M8b | alacritty adapter + fixtures (shadow detection) | M7, M3 | same |
| M8c | Hyprland adapter + fixtures | M7, M3 | same; `hyprctl configerrors` check |
| M8d | waybar adapter + fixtures | M7, M3 | same |
| M8e | wallpaper (hyprpaper) adapter | M7, M2 | same |
| M9 | Provider base, registry, shared prompt, FakeProvider | M1, M4 | `Provider`, `ProviderError` family, `create(name, cfg)` |
| M10a | Anthropic provider (forced tool use) | M9 | injected-client unit tests |
| M10b | OpenAI-compat provider (json_schema -> json_object fallback) | M9 | injected-client unit tests |
| M11 | `session.py`: orchestrator (context build, plan, validate, render, snapshot, apply, reload, revert, retry) | M2, M6, M7, M9 (+ at least one adapter) | `Session.propose(text) -> Proposal(diff, plan)`, `Session.apply(proposal) -> AppliedChange`, `Session.keep(id)`, `Session.revert(id)` |
| M12 | TUI: request screen, diff preview, confirm + countdown, history | M11 | Textual Pilot tests with FakeProvider |
| M13 | Bootstrap/uninstall flows (include line install/removal) in TUI + `buckwheat uninstall` | M8a-d, M12 | removal restores pristine fixtures byte-for-byte |
| M14 | README, user docs, release checklist | M12 | |

Parallel lanes after M0: {M1} -> {M2, M3}; {M4}; {M5} -> {M6}; then adapters
(M8a-e) and providers (M10a-b) are independent of each other.

Definition of done for every PR: tests pass offline, ruff and mypy clean, no
new dependency without a justification line in the PR, no secrets.
