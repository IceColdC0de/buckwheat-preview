# buckwheat design

Status: proposed. Companion docs: [palettes.md](palettes.md), [milestones.md](milestones.md).

buckwheat lets a user customise ("rice") a Linux desktop by typing plain-text
requests ("make everything Nord, use JetBrains Mono 12, dim the bar").
An LLM turns the request into a typed, validated **ChangePlan**. Deterministic
adapter code turns the plan into config text. The LLM never emits shell
commands and never writes files.

Settled inputs:
Python 3.11+; Textual TUI first; MVP targets Hyprland + waybar and
kitty + alacritty; v1 providers are API-key only (Anthropic SDK, and an
OpenAI-compatible provider with `base_url`); key from an env var, config stores
only the var name; config at `~/.config/buckwheat/config.toml`; pydantic
ChangePlan; managed include files; deps `anthropic`, `openai`, `pydantic`,
`textual`; CLI-delegation provider deferred.

## 0. Problems found in the settled decisions

Nothing blocks the plan, but three points need the owner's eyes:

1. **"Never edit the user's config" is not fully achievable.** A managed
   include only takes effect if the user's main file references it. Each
   adapter therefore makes one *bootstrap edit*: it adds a single marked
   include line to the user's file, only after explicit confirmation, with a
   backup and a diff, and `uninstall` removes exactly that line. Everything
   else buckwheat writes goes in files it owns. Proposed reading of the rule:
   "never edit in place beyond the one-line include." If the owner disagrees,
   the fallback is to print the line and have the user paste it (`bootstrap =
   "manual"` in config).
2. **Alacritty imports have the lowest priority.** In alacritty, values in the
   main `alacritty.toml` override values from `import`ed files, so a managed
   import cannot change a key the user already set. Kitty and Hyprland are the
   opposite (later wins, so the include goes at the end). The alacritty adapter
   must detect shadowed keys in `read_state` and surface them as a warning
   ("`font.size` is set in your alacritty.toml and overrides buckwheat"). It
   will not edit the user's value. See open question Q1.
3. **Waybar's config is JSONC and has no comment-safe edit.** Adding an
   `"include"` entry means editing a JSONC file. v1 does a conservative text
   insertion (only if the file parses and has a top-level object with no
   existing `include`; otherwise fall back to manual instructions).

## 1. Architecture and request flow

```
 user text
    |
    v
 [tui] request ──> [context builder] ──> [provider.plan()] ──> ChangePlan (raw JSON)
                    (detected targets,       (Anthropic /            |
                     current state,           OpenAI-compat)         v
                     palette names,                         [plan] pydantic validation
                     allowed keys)                                   |
                                                                     v
                                   per op: [adapter.validate] ─ reject whole plan on any error
                                                                     |
                                                                     v
                                   [adapter.render] -> proposed file contents (no I/O yet)
                                                                     |
                                                                     v
                              [safety] snapshot current files + unified diff ──> [tui] preview
                                                                     |
                                                            user confirms? no -> discard
                                                                     v
                              [safety] atomic write ──> [adapter.reload] ──> keep-or-revert timer
                                                                     |
                                              keep -> commit snapshot as history entry
                                              revert / timeout / crash -> restore snapshot + reload
```

Rules of the flow:

- The orchestrator (`buckwheat.core`-style function `run_request`, lives in
  `tui`-independent module `buckwheat/session.py`) is a plain Python object
  with no Textual imports, so tests and a future CLI/GUI reuse it.
- Steps up to "preview" have **no side effects** on the user's system.
- Validation is all-or-nothing: one bad op rejects the plan; the user sees
  the reason. We do not apply partial plans.
- A provider failure or invalid plan allows one automatic retry with the
  validation error text appended to the request (max 1 retry, configurable),
  then surfaces the error.
- The context builder only reads. Context is a pydantic `PlanContext`:
  `request_id`, `targets` (list of `TargetInfo`: name, detected, version,
  managed-installed, current values for allowed keys, shadowed keys),
  `palettes` (names + one-line descriptions only), `limits`
  (allowed ops/keys, see section 2). It does not include whole dotfiles
  (see prompt-injection, section 5).
- Keep-or-revert: after reload the TUI shows a countdown (default 15 s,
  `safety.revert_timeout_seconds`). Enter/`k` keeps; Esc/`r` or timeout
  reverts. The revert deadline is persisted in the snapshot's `meta.json`,
  and on startup buckwheat reverts any snapshot still in state `pending`
  (covers crashes and closed terminals).

## 2. ChangePlan schema

A plan is a list of ops. Ops are a pydantic *discriminated union* on `op`.
Everything is `extra="forbid"`, frozen, with length limits.

### 2.1 Example

```json
{
  "version": 1,
  "summary": "Nord theme with JetBrains Mono 12 and a dimmer wallpaper",
  "ops": [
    {"op": "palette", "palette": "nord", "targets": ["kitty", "alacritty", "hyprland", "waybar"]},
    {"op": "font", "target": "kitty", "role": "terminal", "family": "JetBrains Mono", "size": 12.0},
    {"op": "set", "target": "hyprland", "key": "general.gaps_in", "value": 6},
    {"op": "set", "target": "waybar", "key": "bar.height", "value": 30},
    {"op": "wallpaper", "path": "~/Pictures/walls/forest.jpg", "mode": "fill"}
  ]
}
```

### 2.2 Model sketches (illustrative; implement in `buckwheat/plan/models.py`)

```python
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field

TargetName = Literal[
    "kitty", "alacritty", "hyprland", "waybar"
]  # extended only in code
Scalar = Union[bool, int, float, str]  # no lists/dicts in v1


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SetOp(_Op):
    op: Literal["set"]
    target: TargetName
    key: str = Field(max_length=64)  # must be in the adapter's key registry
    value: Scalar


class PaletteOp(_Op):
    op: Literal["palette"]
    palette: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")  # name from library
    variant: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,20}$")
    targets: list[TargetName] = Field(min_length=1, max_length=4)


class FontOp(_Op):
    op: Literal["font"]
    target: TargetName
    role: Literal["terminal", "ui", "bar"]
    family: str = Field(min_length=1, max_length=80)
    size: float | None = Field(default=None, ge=6, le=48)


class WallpaperOp(_Op):
    op: Literal["wallpaper"]
    path: str = Field(max_length=512)
    mode: Literal["fill", "fit", "center", "tile"] = "fill"


Op = Annotated[Union[SetOp, PaletteOp, FontOp, WallpaperOp], Field(discriminator="op")]


class ChangePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1]
    summary: str = Field(max_length=200)
    ops: list[Op] = Field(min_length=1, max_length=20)
```

Palette ops carry a *name*, never colour values: colours come from the
built-in library (section 6), so the LLM cannot smuggle arbitrary strings
through them. (Custom user palettes are Q4.)

### 2.3 How hostile plans are rejected

Validation has three layers. All must pass.

1. **Schema layer (pydantic).** Unknown fields, unknown ops, wrong types,
   over-long strings, too many ops → rejected.
2. **Common value layer (`plan/validators.py`).**
   - Strings: no control characters (including `\n`, `\r`, `\0`), no
     backticks, `$(`, `${`, `;`, `|`, `&`, `<`, `>` or `\` in any value that is
     not a path (`bad-chars` rule). Hyprland config interprets `exec`-family
     keywords, so keys are *whitelisted*, not blacklisted.
   - Font family: regex `^[A-Za-z0-9 ._+\-]{1,80}$`, and (best effort) must
     appear in `fc-list` output; otherwise a warning, not an error, so unusual
     installed fonts are not blocked — but the regex is mandatory.
   - Colours: only `#rrggbb` produced from palettes; a `set` op whose key is
     typed `color` must match `^#[0-9a-fA-F]{6}$`.
   - Wallpaper path: expanded, `realpath`-resolved, must be a regular file,
     under an allowed root (`~/Pictures`, `~/.local/share/backgrounds`,
     `/usr/share/backgrounds`, plus `paths.extra_wallpaper_dirs` in config),
     extension in `{.png,.jpg,.jpeg,.webp}`, size ≤ 100 MB. The path is
     checked after symlink resolution, so a symlink to `~/.ssh` fails.
3. **Adapter layer (`adapter.validate(op) -> list[Problem]`).**
   Each adapter owns a **key registry**: a dict
   `key -> KeySpec(type, min, max, enum, render)` — the only keys it accepts.
   Examples: `kitty: background_opacity (float 0-1)`,
   `hyprland: general.gaps_in (int 0-100)`, `hyprland: decoration.rounding (int 0-50)`,
   `waybar: bar.position (enum top|bottom)`. A `set` for an unregistered key,
   a value out of range, or the wrong type is an error. The registry is
   data, not LLM-controlled, so adding a new settable key is a code change
   reviewed in a PR. Keys that run programs (`exec`, `exec-once`, `bind*`,
   `windowrule` exec, waybar `custom/*` `exec`, kitty `launch`/`map`, alacritty
   `terminal.shell`/`hints`/`keyboard.bindings`) are **never** registered.

Additional plan-level rules: ops may not target an adapter that is not
`detected`; two ops may not set the same (target, key) differently; a
`palette` op and a `set` op on a colour key for the same target conflict.
Each rejection is a `Problem(op_index, code, message)`, shown to the user and
fed back in the single retry.

## 3. Adapter interface

```python
class Adapter(Protocol):
    name: ClassVar[TargetName]
    managed_files: ClassVar[
        list[Path]
    ]  # the ONLY files this adapter may write (allowlist)

    def detect(self) -> Detection: ...  # installed? version? config dir? running?
    def read_state(
        self,
    ) -> (
        TargetState
    ): ...  # current values for registry keys, bootstrap status, shadowed keys
    def validate(self, op: Op) -> list[Problem]: ...
    def render(self, ops: list[Op], state: TargetState) -> RenderResult:
        ...
        # pure function: returns {path: new_text} for managed files (full file content,
        # generated from ALL buckwheat-managed values = previous managed values merged with ops)

    def reload(self) -> ReloadResult: ...  # side effect: tell the app to re-read config
    def install(
        self, plan: BootstrapPlan
    ) -> RenderResult: ...  # the one-line include (needs confirm)
    def uninstall(self) -> RenderResult: ...  # removes include line + managed file
```

Managed files are fully regenerated from a small **state file**
`~/.local/state/buckwheat/managed.json` (target → key → value), so `render`
is deterministic and idempotent: new state = old state overlaid with ops.
Managed files start with the header
`# Managed by buckwheat. Edits are overwritten. Remove with: buckwheat uninstall <target>`.

The include line is wrapped in markers so removal is exact:
`# >>> buckwheat (managed include) >>>` / `# <<< buckwheat <<<`.

### 3.1 kitty
- Config: `~/.config/kitty/kitty.conf`; managed file `~/.config/kitty/buckwheat.conf`.
- Include: `include buckwheat.conf` appended at end of `kitty.conf` (later wins).
- Keys: `font_family`, `font_size`, `background_opacity`, `window_padding_width`,
  `cursor_shape`, colours (`foreground`, `background`, `color0..15`, `cursor`,
  `selection_*`).
- Reload: send `SIGUSR1` to running kitty processes (found by exact process
  name `kitty`, same uid). No remote-control dependency. Fallback: tell the
  user to press `ctrl+shift+f5`.

### 3.2 alacritty
- Config: `~/.config/alacritty/alacritty.toml`; managed `~/.config/alacritty/buckwheat.toml`.
- Include: add the managed path to `[general] import = [...]` (0.14+; top-level
  `import` for older). Parse with `tomllib` to check; text-edit the one array.
  Because TOML writing is not in the stdlib, the edit is textual and only
  attempted when the parse shows `general.import` absent or a simple array.
- Priority gotcha: see section 0 point 2. `read_state` parses the user file
  and reports registry keys defined there as `shadowed`.
- Keys: `font.normal.family`, `font.size`, `window.opacity`, `window.padding.x/y`,
  `colors.primary.*`, `colors.normal.*`, `colors.bright.*`, `colors.cursor.*`.
- Reload: alacritty live-reloads on file change by default
  (`general.live_config_reload`). `reload()` is a no-op that verifies the
  setting isn't false (else warns). Note: it watches the main file and
  imports (verify in M-alacritty tests on real binary; Q5).
- Rendering TOML: small hand-written emitter for scalars/tables only (we emit
  no arrays except none); strings are escaped by a single `toml_str()` helper.

### 3.3 Hyprland
- Two formats, chosen by detection: the Lua config when
  `~/.config/hypr/hyprland.lua` exists or `hyprctl version` is 0.57+, else the
  classic hyprlang config.
- Lua (0.57+): managed `~/.config/hypr/buckwheat.lua`, one `hl.config({...})`
  call with nested tables and `"rgb(rrggbb)"` colours. Include: a marked block
  (`-- # >>> ...` markers) holding `pcall(require, "buckwheat")` at the end of
  `hyprland.lua` (a symlinked `hyprland.lua` is edited at its target). Never
  create `hyprland.lua`. Before writing, the proposed files are checked with
  `Hyprland --verify-config -c` on a temp copy of the config folder (when the
  binary is installed); any error refuses the change. Reload:
  `hyprctl reload config-only`.
- Classic: config `~/.config/hypr/hyprland.conf`; managed
  `~/.config/hypr/buckwheat.conf`.
- Include: `source = ~/.config/hypr/buckwheat.conf` at the end of `hyprland.conf`.
- Keys, Lua (0.57+): **every appearance key Hyprland has.** The typed key
  list comes from Hyprland's own stub file
  `/usr/share/hypr/stubs/hl.meta.lua` (class `HL.ConfigValueTypes`) when it
  is installed, else from `adapters/hyprland_keys.json`, generated from it by
  `scripts/gen_hyprland_keys.py` and committed (CI has no Hyprland).
  `adapters/hyprland_keys.py` gives each key a kind and validates values by
  it: bool, int, number, text, colour (`#rrggbb`/`#rrggbbaa`), gradient
  (`"#88c0d0 #b48ead 45deg"` -> `{ colors = {...}, angle = 45 }`), CSS gap
  (`"5 10"` -> `{ top, right, bottom, left }`), vec2 (`"2 3"` -> `{ 2, 3 }`),
  font weight. Compound values are short strings so plans and
  `managed.json` stay scalar. Left out: `debug.*`, `ecosystem.*`,
  `xwayland.*`, `input.*`, `input_capture.*`, `binds.*`, `gestures.*`,
  `render.*`, `opengl.*`, `quirks.*`, `experimental.*`, lock-screen, helper
  program, autoreload and file-path keys, and any key naming exec, plugin or
  permission. The planner gets the keys grouped by section and type, one line
  per section (about 5.4k characters).
- Keys, classic (sections rendered as `section { key = value }`):
  `general.gaps_in`,
  `general.gaps_out`, `general.border_size`, `general.col.active_border`,
  `general.col.inactive_border`, `decoration.rounding`, `decoration.active_opacity`,
  `decoration.inactive_opacity`, `decoration.blur.enabled`, `decoration.blur.size`,
  `animations.enabled`. Colours render as `rgba(rrggbbff)` (classic). These
  eleven keys keep the same ranges and output in Lua mode too.
- Palettes: the two border colours (classic); in Lua mode also group borders,
  the group bar (colours and title text), the shadow colour and
  `misc.background_color`.
- Wallpaper: Hyprland has no built-in wallpaper tool. v1 supports **hyprpaper**
  only, via a separate `wallpaper` adapter (`adapters/wallpaper.py`). It calls
  `hyprctl hyprpaper preload` / `wallpaper` at runtime, and persists the choice
  by writing `~/.config/hypr/hyprpaper.conf` only if that file is absent or
  already marked managed. If it is user-owned, the op is reported as
  unsupported rather than edited (Q2).
- Reload: `hyprctl reload` (classic; Lua adds `config-only`; argv list, no
  shell). Check exit code and
  `hyprctl configerrors` output; non-empty errors after reload = failed apply,
  triggers automatic revert.

### 3.4 waybar
- Config: `~/.config/waybar/config` or `config.jsonc`; style `~/.config/waybar/style.css`.
  Managed: `~/.config/waybar/buckwheat.jsonc` (via `"include": [...]`) and
  `~/.config/waybar/buckwheat.css` (via `@import url("buckwheat.css");` placed
  at the **end** of style.css so it wins by cascade order).
- Keys (JSON): `bar.position`, `bar.height`, `bar.spacing`, `bar.layer`.
  CSS is generated from registry templates with CSS variables
  (`@define-color`) for palette colours and `font-family`/`font-size` on `*`.
  Waybar include merges only top-level keys; module arrays in the user's file
  are not changed by us.
- Reload: `SIGUSR2` to waybar processes (reloads config and style). Success
  check: process still alive after 1 s; waybar logs CSS errors to stderr, which
  we cannot see, so a dead process = failure → revert and restart attempt. If
  waybar was running before the change, buckwheat restarts it detached (fixed
  argv `["waybar"]`, new session, stdout/stderr to `/dev/null`) when the revert
  reloads.

### 3.5 Common adapter requirements
- Atomic write only through `safety.atomic_write` (section 5); adapters
  return text and never open files for write.
- Subprocesses: `subprocess.run([...], shell=False, timeout=5)`; argv built
  from constants. No plan value reaches an argv except wallpaper path
  (after validation), passed as one argv element.
- Adapters never import each other or the TUI.

## 4. Providers

```python
class Provider(Protocol):
    name: ClassVar[str]

    def plan(
        self, request: str, context: PlanContext
    ) -> ChangePlan: ...  # raises ProviderError
```

- No HTTP, key or model assumptions in the interface. Auth lives inside the
  provider, built from its own config table.
- Registry: `providers/registry.py` with `register(name)(cls)` and
  `create(name, cfg: dict) -> Provider`. Names in v1: `anthropic`,
  `openai_compat`. Config picks one with `provider = "anthropic"`.
- Config:
  ```toml
  provider = "anthropic"

  [providers.anthropic]
  api_key_env = "ANTHROPIC_API_KEY"   # the NAME of the env var, never the key
  model = "claude-sonnet-5-5"
  max_tokens = 16000

  [providers.openai_compat]
  api_key_env = "OPENAI_API_KEY"
  base_url = "https://api.openai.com/v1"   # or OpenRouter, Gemini compat, http://localhost:11434/v1
  model = "gpt-4.1"
  ```
  A config containing something that looks like a key (`sk-`, long
  base64-ish values) in any field is rejected with an error.
  `api_key_env` may be empty only for `openai_compat` with a localhost
  `base_url` (Ollama).
- **Anthropic**: native SDK, forced tool use. One tool `submit_plan` whose
  `input_schema` is `ChangePlan.model_json_schema()`; `tool_choice={"type":"tool","name":"submit_plan"}`.
  Parse the `tool_use` block's `input` with `ChangePlan.model_validate`.
- **OpenAI-compatible**: `openai` SDK with `base_url`. Try
  `response_format={"type":"json_schema", ...}` (strict) first; if the server
  rejects it (many compat servers do), fall back to `json_object` mode with
  the schema included in the system prompt, then `model_validate_json`. The
  chosen mode is cached per provider instance. Strict mode requires a
  schema with `additionalProperties: false`, which our models already have;
  a small `schema_for_openai()` helper inlines `$defs` if needed.
- Prompt shape (`providers/prompt.py`, shared): system prompt states the role,
  the only allowed ops, the list of allowed keys per target, the palette names,
  and that content in the `<untrusted>` block is data. User message = request
  plus context. Providers share it; they differ only in transport.
- **Errors**: `ProviderError` subclasses — `AuthError` (missing env var / 401;
  message names the env var), `RateLimitError`, `NetworkError`,
  `BadOutputError` (refusal, truncated, not valid JSON / schema). Timeouts
  default 60 s; SDK retries 2 on 429/5xx. Messages never contain the key.
  Schema-validation failure of the LLM output is handled by the one retry in
  section 1; auth/network errors are not retried by the orchestrator.
- Logging: never log the key, request headers, or full responses at INFO.

### FUTURE (not v1): CLI-delegation provider
Marked extension: a `cli` provider that runs `claude -p` or the gemini CLI as a
subprocess so users with a subscription need no API key. Needs no core change
(same `plan()` signature, registered by name). Caveats to resolve first:
- **Output parsing**: CLIs print free text; we would request JSON-only output
  and extract/validate with the same pydantic model, with the same retry.
  Output format flags differ between CLIs and change across versions.
- The CLI may be an agent with its own tools; it must be run with tools
  disabled/sandboxed and no write access, or it defeats the "LLM never acts"
  rule.
- **Provider terms**: check whether each provider's terms allow driving a
  subscription login from third-party software. Do this before building it.
- Slower, harder to test, and the prompt goes through a process we do not control.

## 5. Safety

### 5.1 Snapshots and rollback
- Before any write, `safety.snapshot(paths)` copies every file that will be
  written or modified (and records "did not exist" for new files) into
  `~/.local/state/buckwheat/snapshots/<UTC-timestamp>-<request_id>/` with
  `meta.json`: files, sha256 of before/after, plan JSON, state
  (`pending|kept|reverted`), `revert_deadline`.
- Rollback = restore files from the snapshot (delete files that did not
  exist), then `adapter.reload()`. `buckwheat rollback [id]` and a TUI history
  screen can roll back any kept snapshot. If current file hashes differ from
  the snapshot's "after" hashes (user edited since), warn before restoring.
- Retention: keep last 50 snapshots (configurable). Snapshots are `0700` dirs.

### 5.2 Diff preview
Unified diff per file, computed from `render` output vs current contents via
`difflib`, shown in the TUI before anything is written, with a one-line plain
summary per op. Includes bootstrap edits to user files. No confirm, no write.

### 5.3 Atomic writes and permissions
`atomic_write(path, text)`: write to a temp file in the **same directory**
(`tempfile.mkstemp`), `fsync`, copy mode from the existing file (default
`0644`; config files with possible secrets are not touched by us), `os.replace`,
then fsync the directory. Symlinks: if the target is a symlink (dotfile
managers like stow), write to the resolved real path only if it is inside the
adapter's allowlist after resolution; otherwise refuse. Files we create get
`0644`; buckwheat's own config/state/snapshots `0600`/`0700`. Never run as root;
refuse if `euid == 0`.

### 5.4 Path allowlist
`safety.guard_path(path, adapter)` is the only gate before write. Allowed
paths = `adapter.managed_files` plus the adapter's declared bootstrap file
(the user's main config, writable *only* for the marked include block).
Resolve with `Path.resolve()` and compare to the allowlist after resolving;
reject `..`, symlink escapes, and anything else. `atomic_write` calls
`guard_path` itself so adapters cannot bypass it. The bootstrap writer checks
that the new text differs from the old only inside the marker block.

### 5.5 Prompt injection
Content that can enter the prompt: the user's request (trusted), current
config values (read from files), palette names, font lists, wallpaper file
names. Defences, in order of importance:
1. **The model has no capabilities.** Output is only a ChangePlan checked by
   section 2 rules; the worst a successful injection can do is propose a
   bad-but-valid change (e.g. ugly colours), which the user sees as a diff.
2. Context contains values only for registry keys, run through the same value
   validators; we do not include comments or free-form file text. Free text
   from files, if ever needed, is truncated (200 chars), stripped of control
   characters and wrapped in `<untrusted>...</untrusted>` with the system
   prompt saying that block is data, never instructions.
3. File names (wallpaper) are listed only as validated basenames.
4. Plan paths and strings are validated again at render and at write time
   (defence in depth, not trusting the earlier layer).
5. Confirmation is always required for writes; there is no "auto-apply" mode in v1.

### 5.6 Nothing buckwheat writes may run programs or reach the network
Owner decision (2026-11-01): buckwheat's AI may be very free. It may set any
appearance value and write complete stylesheets. The one hard line, enforced
in code (`safety/content.py`, `check_generated`) and covered by tests:
**nothing buckwheat writes may run a program or reach the network.** Every
file an adapter generates for buckwheat's own managed files is checked before
the preview is built and again before an install writes it; a file that fails
is refused with a plain reason. The rules, by file type:
- Lua (`buckwheat.lua`): no `exec`/`exec-once`, `hl.exec_cmd`, `hl.on`,
  `hl.bind`, `hl.timer`, `hl.dispatch`, `hl.plugin` anywhere (strings
  included); no `require`, `dofile`, `load`, `pcall`, `os`, `io`, `package`,
  `debug`, functions or long strings; the only call allowed is `hl.config`.
- hyprlang / kitty / hyprpaper `.conf`: no `exec*`, `bind*`, `source`,
  `plugin`, `permission`, `env`, kitty `map`/`launch`/`shell`/`include` and
  similar, and no `$(` or backticks.
- alacritty TOML: no `shell`, `hints`, `keyboard`/`mouse` bindings,
  `program`/`command`/`args`, `import`.
- Waybar config: no `custom/...` modules, no `exec`, `exec-if`,
  `exec-on-event`, `on-click*`, `on-scroll*`, `on-update`, `include`.
- CSS: `url()` only for files in the user's home (absolute under `~`,
  `~/...`, `file://` under `~`, or relative without `..`); no `http(s):`,
  `//host`, `data:` or other schemes; no `@import`; no backslash escapes.
The user's own config files are never generated; they only ever get the fixed
include block (section 5.4). Every change still goes through preview,
snapshot, and the keep-or-revert countdown (5.1, 5.2).

## 6. Palette library

See [palettes.md](palettes.md) for the full format. Summary: palettes are TOML
files shipped as package data in `buckwheat/plan/palettes/*.toml`, loaded
and validated by `plan/palettes.py` into a `Palette` pydantic model with
semantic slots (`bg`, `fg`, `accent`, `ansi.black`..`ansi.bright_white`, ...).
Adapters consume a palette only through a per-adapter **slot map** in code
(e.g. kitty `color4 <- ansi.blue`), so adding a palette needs no adapter
changes. Initial set: nord, gruvbox (dark, light), catppuccin (latte,
frappe, macchiato, mocha), dracula, tokyo-night, solarized (dark, light), rose-pine.

## 7. Testing

- **Fake provider** (`tests/fakes.py`, registered as `fake`): returns canned
  `ChangePlan`s keyed by request text (from `tests/plans/*.json`), or
  raises a configured `ProviderError`. Used by the session/TUI tests.
- **Fixture dotfiles** under `tests/fixtures/<target>/<scenario>/`: `pristine`
  (no include), `bootstrapped`, `user-shadowed` (alacritty), `weird` (comments,
  CRLF, symlinked, no trailing newline, JSONC with comments). Tests copy a
  fixture into `tmp_path`, point adapters at it via `HOME`/`XDG_CONFIG_HOME`
  monkeypatch, and compare rendered output with golden files (`*.golden`).
- **Hostile plan corpus** `tests/plans/hostile/*.json`: path traversal,
  symlink wallpaper, newline injection into values, unregistered keys,
  `exec-once` keys, oversized strings, extra fields, wrong types. Each must be
  rejected with a specific `Problem.code`.
- **Provider tests** mock the SDK client (`respx`-free: inject a fake client
  object) to check request shape, structured-output fallback, and error
  mapping. No network: CI sets `BUCKWHEAT_NO_NETWORK=1`, and a conftest
  autouse fixture patches `socket.socket.connect` to fail the test.
- Reload tests inject a fake `CommandRunner`; real `hyprctl`/signals are never
  called in unit tests. Optional marked `@pytest.mark.integration` tests may
  run against real binaries locally and are skipped in CI.
- Safety: property tests (hypothesis, optional) for snapshot→restore round-trip;
  crash test kills the process between write and keep and checks startup revert.
- TUI: Textual `Pilot` tests for the flow with the fake provider.
- Tooling: pytest, ruff, mypy (strict on `plan` and `safety`). Palette test:
  every palette file validates and every slot map resolves.

## 8. Module layout

```
src/buckwheat/
  __init__.py
  config.py            # load/validate ~/.config/buckwheat/config.toml (pydantic Settings), XDG paths
  session.py           # run_request(): the flow in section 1; no UI imports
  plan/
    models.py          # ChangePlan, ops (section 2)
    validators.py      # common value rules
    context.py         # PlanContext, TargetInfo, Problem
    palettes.py        # Palette model + loader
    palettes/*.toml
  providers/
    base.py            # Provider protocol, ProviderError family
    registry.py
    prompt.py
    anthropic.py
    openai_compat.py
  adapters/
    base.py            # Adapter protocol, KeySpec, Detection, TargetState, RenderResult, CommandRunner
    kitty.py  alacritty.py  hyprland.py  waybar.py  wallpaper.py
    registry.py        # name -> adapter, detect all
  safety/
    paths.py           # guard_path
    atomic.py          # atomic_write
    snapshot.py        # snapshot, restore, history, pending-revert on startup
    diff.py
  tui/
    app.py  screens/   # request, preview, confirm-countdown, history, settings
tests/ ...
```

Dependency direction: `tui -> session -> {providers, adapters, safety} -> plan`;
`config` is importable by all. `plan` imports nothing from the others.
Adapters and providers never import each other.

## 9. Milestones

See [milestones.md](milestones.md) for the PR-sized task list, dependencies and
interface contracts.

## 10. Open questions and risks

Questions (owner decision needed unless a default is stated):
- **Q1** Alacritty shadowing: warn only (default), or offer to comment out the
  user's conflicting key (violates no-edit rule)?
- **Q2** Wallpaper: hyprpaper only in v1 (default); swww/swaybg later? Is
  `hyprpaper.conf` treated as user-owned (default yes)?
- **Q3** Bootstrap edit consent model (section 0.1): confirm-and-edit
  (default) vs manual paste only.
- **Q4** User-defined palettes in `~/.config/buckwheat/palettes/`: v1 or later (default later)?
- **Q5** Does alacritty live-reload watch imported files on all supported versions? Verify on real binary.
- **Q6** Minimum versions: Hyprland (config keys moved between releases, e.g.
  blur options), alacritty ≥ 0.14 for TOML `general.import`, kitty ≥ 0.26.
- **Q7** Default models and rough cost per request; do we show token usage?
- **Q8** Telemetry: none (default).

Risks:
- Hyprland/waybar config schemas change; key registries need version gating
  (`KeySpec.since`/`until`).
- Waybar CSS errors can leave the bar dead; mitigated by liveness check and
  auto-revert but not provable offline.
- Textual keep-or-revert timer must work when the TUI itself is themed badly
  by the change (we only theme other apps, but the terminal running the TUI
  may be kitty/alacritty and change under it). The countdown screen must stay
  readable: use terminal default colours and plain text.
- OpenAI-compatible servers vary widely in structured-output support; the
  fallback and retry reduce but do not remove failures.
- LLMs may choose poor but valid values; mitigated by preview and revert.
- Dotfile-manager setups (stow, chezmoi, nix home-manager with read-only
  symlinks) may refuse writes; detect read-only targets and report clearly.
