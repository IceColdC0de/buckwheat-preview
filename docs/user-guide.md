# Buckwheat user guide

This guide describes buckwheat as it is on `main`. Every command and config
example here was run against that code. Anything that is not yet built is
marked as such.

- [1. How a change happens](#1-how-a-change-happens)
- [2. Config file](#2-config-file)
- [3. Providers (LLMs)](#3-providers-llms)
- [4. What you can ask for](#4-what-you-can-ask-for)
- [5. Palettes](#5-palettes)
- [6. The include line](#6-the-include-line)
- [7. Commands: `check` and `uninstall`](#7-commands-check-and-uninstall)
- [8. Troubleshooting and recovery](#8-troubleshooting-and-recovery)

---

## 1. How a change happens

1. You type a request in the TUI (run `buckwheat` with no arguments).
2. The LLM returns a **change plan**: a list of structured operations, such as
   "set kitty's `font_size` to 13" or "use the `gruvbox` palette for Waybar".
   The model never writes shell commands or free-form config text.
3. Buckwheat checks the plan against its rules (allowed keys, colour and font
   formats, allowed wallpaper folders). A bad plan is rejected and nothing
   changes. The model is asked again up to `max_retries` times, with the
   validation errors.
4. You see a **diff preview** of every file the change would touch.
5. You press Enter (or `y`) to apply. Buckwheat first saves a **snapshot** of
   the current files, then writes the files and reloads the programs.
6. A **countdown** starts (15 seconds by default). Press Enter or `k` to keep
   the change, or Esc or `r` to undo it. If you do nothing until the countdown
   ends, the change is undone.

Kept changes stay in the history. Press **F2** in the TUI to see them and roll
any of them back.

---

## 2. Config file

### Location

- Default: `~/.config/buckwheat/config.toml`. If `XDG_CONFIG_HOME` is set to an
  absolute path, the file is `$XDG_CONFIG_HOME/buckwheat/config.toml`.
- Another file: `buckwheat --config /path/to/file.toml` (this works with any
  command).
- If the default file does not exist, buckwheat uses the built-in defaults.
  If a file named with `--config` does not exist, that is an error.

Buckwheat's own state lives in `~/.local/state/buckwheat/` (or
`$XDG_STATE_HOME/buckwheat/`): `managed.json` holds the values buckwheat
manages, and `snapshots/` holds the saved copies of your files.

### Rules

- **Top-level keys go first**, before any `[table]` header. A key written
  after `[providers.anthropic]` belongs to that table, and buckwheat rejects it
  with `Extra inputs are not permitted`.
- **Unknown keys are errors**, so a typo is reported instead of ignored.
- **Store the name of an environment variable, never the key.** Buckwheat
  refuses any value that looks like an API key (for example `sk-...` or a long
  random token). The error names the setting but does not print the value.

### Options

| Option | Default | Allowed values | What it does |
|---|---|---|---|
| `provider` | `"anthropic"` | `"anthropic"`, `"openai_compat"` | Which LLM turns requests into plans. |
| `bootstrap` | `"confirm"` | `"confirm"`, `"manual"` | Controls edits to your own config files, made by install and uninstall (see [section 6](#6-the-include-line)). `confirm`: buckwheat may make the edit, after you have seen the diff and confirmed it (in the TUI, then the keep-or-revert countdown applies; in `buckwheat uninstall`, a y/N question). `manual`: it never edits your files. It shows the lines to add or remove, and install writes nothing. Changes you apply in the TUI still write buckwheat's own files in both modes. |
| `[safety] revert_timeout_seconds` | `15` | 1 to 3600 | Seconds the keep-or-revert countdown runs before the change is undone. |
| `[safety] max_retries` | `1` | 0 to 5 | How many times the model is asked again, with the validation errors, when its plan is rejected. |
| `[safety] snapshot_retention` | `50` | 1 to 10000 | How many finished snapshots (kept or reverted) to keep. Older ones are deleted. Pending snapshots are never deleted. |
| `[providers.anthropic] api_key_env` | `"ANTHROPIC_API_KEY"` when there is no table | Environment variable name | The variable that holds the Anthropic key. Required whenever the `[providers.anthropic]` table exists. |
| `[providers.anthropic] model` | `"claude-sonnet-5-5"` | Model name, up to 200 characters | The Anthropic model. Leave it out to use the default. |
| `[providers.anthropic] max_tokens` | `16000` | 1 to 200000 | Output token cap for one plan. The model's thinking counts against it, so a low value can cut a plan short. |
| `[providers.anthropic] base_url` | unset | URL | A custom Anthropic API endpoint. Leave it out for the default. |
| `[providers.openai_compat] model` | required | Model name, up to 200 characters | The model name at the endpoint. Buckwheat refuses to run without it. |
| `[providers.openai_compat] api_key_env` | required (may be empty only for a `localhost` `base_url`) | Environment variable name | The variable that holds the endpoint's key. |
| `[providers.openai_compat] base_url` | `"https://api.openai.com/v1"` | URL | The OpenAI-compatible endpoint. |
| `[providers.openai_compat] max_tokens` | `2048` | 1 to 200000 | Output token cap for one plan. |

Both `[providers.*]` tables are checked whenever the file is loaded, even the
one that is not selected. Only the table matching `provider` is used.

### Example

```toml
# Top-level keys come first, before any [table].
provider = "anthropic"
bootstrap = "confirm"

[providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
model = "claude-sonnet-5-5"

[safety]
revert_timeout_seconds = 15
max_retries = 1
snapshot_retention = 50
```

---

## 3. Providers (LLMs)

Buckwheat reads the API key from the environment, at the moment it makes a
request. The config file holds only the variable name. Export the key in the
shell or session that starts buckwheat, for example:

```sh
export ANTHROPIC_API_KEY=...   # your key; do not put it in config.toml
buckwheat check                # confirms the variable is set (never prints it)
```

Use `buckwheat check` to confirm the setup. It reports whether the variable is
set, but it never prints the value and never makes a network call.

### Anthropic

The default. Leave `provider` out, or set it to `"anthropic"`. The default
model is `claude-sonnet-5-5`. Set `model` in `[providers.anthropic]` to use a
different one.

### OpenAI-compatible endpoints

Set `provider = "openai_compat"` and a `[providers.openai_compat]` table. The
same table works for any service with an OpenAI-compatible chat API:

| Service | `base_url` | Example `model` | Key variable |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` (the default, can be left out) | `gpt-4.1` | `OPENAI_API_KEY` |
| OpenRouter | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-4.5` | `OPENROUTER_API_KEY` |
| Gemini (OpenAI compatibility) | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.5-flash` | `GEMINI_API_KEY` |
| Ollama (local) | `http://localhost:11434/v1` | a model you have pulled, such as `llama3.1` | none (see below) |

Model names and endpoint URLs change. Check the provider's own documentation
before you copy them.

OpenAI example:

```toml
provider = "openai_compat"

[providers.openai_compat]
api_key_env = "OPENAI_API_KEY"
base_url = "https://api.openai.com/v1"
model = "gpt-4.1"
```

OpenRouter example (the only differences are the URL, the model and the variable):

```toml
provider = "openai_compat"

[providers.openai_compat]
api_key_env = "OPENROUTER_API_KEY"
base_url = "https://openrouter.ai/api/v1"
model = "anthropic/claude-sonnet-4.5"
```

Gemini example:

```toml
provider = "openai_compat"

[providers.openai_compat]
api_key_env = "GEMINI_API_KEY"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
model = "gemini-2.5-flash"
```

**Ollama** runs on your own machine and needs no key. Leave `api_key_env` out.
Buckwheat accepts an empty key only when `base_url` points at `localhost`,
`127.0.0.1` or `::1`. Ollama must be running, and the model must already be
pulled (`ollama pull llama3.1`):

```toml
provider = "openai_compat"

[providers.openai_compat]
base_url = "http://localhost:11434/v1"
model = "llama3.1"
```

Output quality depends on the model. A weak model may produce plans that
fail validation. Buckwheat then rejects the plan and changes nothing, so a
failed request is safe, but it can be frustrating.

---

## 4. What you can ask for

Requests describe a look. The model turns them into operations on the keys
below. A request that asks for something outside these is rejected with a
message, and nothing is changed.

**Colours and palettes.** Ask for a named palette (see [section 5](#5-palettes)),
or name individual colours where a target supports them.

**kitty:** `background`, `foreground`, `color0` to `color15`, `cursor`,
`cursor_shape`, `selection_background`, `selection_foreground`,
`background_opacity`, `font_family`, `font_size`, `window_padding_width`.

**alacritty:** `colors.primary.background` and `colors.primary.foreground`,
the `colors.normal.*` and `colors.bright.*` colours, `colors.cursor.*`,
`font.normal.family`, `font.size`, `window.opacity`, `window.padding.x`,
`window.padding.y`.

**Hyprland:** `general.border_size`, `general.col.active_border`,
`general.col.inactive_border`, `general.gaps_in`, `general.gaps_out`,
`decoration.rounding`, `decoration.blur.enabled`, `decoration.blur.size`,
`decoration.active_opacity`, `decoration.inactive_opacity`,
`animations.enabled`.

**Waybar:** `bar.position`, `bar.height`, `bar.spacing`, `bar.layer`, the
colour roles (`bg`, `bg_alt`, `surface`, `fg`, `fg_dim`, `accent`,
`accent_alt`, `warning`, `error`), `font_family`, `font_size`.

**Wallpaper (hyprpaper):** an image path under `~/Pictures` or
`/usr/share/backgrounds`, and a `mode` of `fill` (cover), `fit` (contain) or
`tile`. Buckwheat rejects any other mode, because hyprpaper does not support it. It only writes
`hyprpaper.conf` when that file is absent, empty, or already managed by
buckwheat. If you have your own `hyprpaper.conf`, a wallpaper request is
rejected with `hyprpaper.conf is not managed by buckwheat; it will not be
edited`, and nothing changes.

### Example requests

These are examples of what to type. They are not outputs of a model run.
Each needs a configured provider.

- `Warm earthy tones: use the gruvbox palette everywhere.`
- `Use the nord palette, but keep the terminal background darker.`
- `Make the terminal font 13 points and add a little window padding.`
- `Give Hyprland thin borders, 8 pixel gaps and rounded corners.`
- `Waybar: tokyo-night storm colours, a 30 pixel bar at the top.`
- `Use ~/Pictures/forest.jpg as the wallpaper, fit mode.`
- `Turn off window animations in Hyprland.`

---

## 5. Palettes

Palettes are built in. Name one in a request, and name a variant if you want
one other than the default (`solarized light`, `tokyo-night day`).

| Palette | Variants (default first) | Description |
|---|---|---|
| `catppuccin` | `mocha`, `latte`, `frappe`, `macchiato` | Soothing pastel theme |
| `dracula` | `default` | Dark theme with vivid purple, pink and green accents |
| `gruvbox` | `dark`, `light` | Retro groove palette with warm contrast |
| `nord` | `default` | Arctic, north-bluish palette |
| `rose-pine` | `main`, `moon`, `dawn` | All natural pine, faux fur and a bit of soho vibes |
| `solarized` | `dark`, `light` | Precision-balanced low-contrast palette |
| `tokyo-night` | `night`, `storm`, `day` | Clean dark theme inspired by Tokyo city lights |

A palette change names the targets it applies to (up to four). Each target
takes the palette's colours for its own keys. Waybar, for example, gets the
colour roles as CSS colours.

---

## 6. The include line

Each program reads buckwheat's file through one **include line** in your own
main config. Buckwheat never edits any other part of your config. The include
is always wrapped in these markers:

```text
# >>> buckwheat (managed include) >>>
...the one include line...
# <<< buckwheat <<<
```

What the include edit does for each target. The paths below are for a default
setup. kitty, Hyprland, Waybar and alacritty follow `XDG_CONFIG_HOME`: with it
set to an absolute path, `~/.config` becomes `$XDG_CONFIG_HOME` in the table.
alacritty looks for `alacritty.toml` the way alacritty does, in
`$XDG_CONFIG_HOME/alacritty/`, then in `$XDG_CONFIG_HOME/`, then in
`~/.config/alacritty/`. Buckwheat uses the first of these that holds an
`alacritty.toml`. If none does, it uses `$XDG_CONFIG_HOME/alacritty/`. Only
hyprpaper is fixed to `~/.config/hypr/`. buckwheat reads `XDG_CONFIG_HOME` when
it starts, so set it before you run `buckwheat`.

alacritty can also read its config from other places: system folders (each
entry of `$XDG_CONFIG_DIRS`, `/etc/xdg` by default), `~/.alacritty.toml`, and
`/etc/alacritty/alacritty.toml`. Before setting alacritty up, buckwheat works
out which file alacritty actually reads, in alacritty's own order. If that is
not the file buckwheat would edit, setup refuses and writes nothing. The
*Set up buckwheat* screen then lists alacritty as skipped:

```text
! skipped alacritty: alacritty reads /home/you/.alacritty.toml, not /home/you/.config/alacritty/alacritty.toml; add the include to /home/you/.alacritty.toml by hand
```

Either add the alacritty include block (below) to the file it names yourself,
or move or copy that file to `~/.config/alacritty/alacritty.toml`
(`$XDG_CONFIG_HOME/alacritty/` if you set it). alacritty then reads that file,
and setup works. A file under `/etc` usually needs root to edit, so copying it
is easier. If alacritty actually reads an `alacritty.yml`, setup refuses with
`alacritty reads ... a YAML file buckwheat does not edit; run "alacritty
migrate" to convert it to TOML, then install again`, and writes nothing.

**Hyprland: Lua config (0.57 and newer) or classic config.** Hyprland 0.57
dropped the classic `hyprland.conf` format and reads only
`~/.config/hypr/hyprland.lua`. Buckwheat uses the Lua config when
`hyprland.lua` exists or `hyprctl version` reports 0.57 or newer, and the
classic `hyprland.conf` otherwise. `buckwheat check` shows which one, as
`config: Lua config (hyprland.lua)` or `config: classic config
(hyprland.conf)`.

With the Lua config:

- Buckwheat's file is `~/.config/hypr/buckwheat.lua`, with one
  `hl.config({...})` call holding only the settings buckwheat manages.
- Setup adds a marked block at the end of `hyprland.lua` with one line,
  `pcall(require, "buckwheat")`. Because it is last, buckwheat's values win
  over the same settings in your own modules. `pcall` means a missing
  `buckwheat.lua` is not an error, while a mistake inside it still is.
- If `hyprland.lua` is a symlink (for example into `~/dotfiles`), buckwheat
  edits the file it points to and keeps the symlink. `buckwheat.lua` goes in
  `~/.config/hypr`, next to the symlink: that is where Hyprland looks when it
  starts normally. If you start Hyprland with `-c` pointing at the symlink,
  Hyprland looks next to the symlink's target instead and does not find
  `buckwheat.lua` (no error, but no change either).
- Before anything is written, buckwheat copies your config folder to a
  temporary folder, adds its changes, and runs `Hyprland --verify-config` on
  the copy. If Hyprland reports any error, the change is cancelled with
  Hyprland's message, for example `Hyprland --verify-config:
  /home/you/.config/hypr/mytheme/look.lua:3: unknown config key
  'general.gaps_inx'`.
  This also happens when the error is in one of your own files, so fix those
  first. Files over 4 MB (wallpapers) are left out of the copy. If the
  `Hyprland` binary is not on your `PATH`, only buckwheat's own checks run.
- Reloads use `hyprctl reload config-only`, so your monitors are not set up
  again, followed by `hyprctl configerrors`.
- If Hyprland is 0.57 or newer and there is no `hyprland.lua`, setup skips
  Hyprland with `Hyprland 0.57+ reads hyprland.lua, which does not exist yet;
  buckwheat does not create your main config. Create it (Hyprland makes one on
  first start), then set Hyprland up again`, and writes nothing.

| Target | Your file | Include block | Buckwheat's file |
|---|---|---|---|
| kitty | `~/.config/kitty/kitty.conf` | `include buckwheat.conf` | `~/.config/kitty/buckwheat.conf` |
| Hyprland 0.57+ (Lua) | `~/.config/hypr/hyprland.lua` | `pcall(require, "buckwheat")` | `~/.config/hypr/buckwheat.lua` |
| Hyprland (classic) | `~/.config/hypr/hyprland.conf` | `source = ~/.config/hypr/buckwheat.conf` | `~/.config/hypr/buckwheat.conf` |
| alacritty | `~/.config/alacritty/alacritty.toml` (see above for other places) | a `[general]` table with `import = ["~/.config/alacritty/buckwheat.toml"]`. Buckwheat writes the path as `~/...` when it is under your home folder, and as an absolute path otherwise. | `buckwheat.toml` in the same folder as your `alacritty.toml` |
| Waybar | `~/.config/waybar/config.jsonc` (or `config`) and `style.css` | a `"include"` entry in the config, and an `@import url("buckwheat.css");` in `style.css` | `buckwheat.jsonc` and `buckwheat.css` in `~/.config/waybar/` |
| hyprpaper | `~/.config/hypr/hyprpaper.conf` | none. Buckwheat writes this file itself (see [section 4](#4-what-you-can-ask-for)). | `~/.config/hypr/hyprpaper.conf` |

### Setting up a target (install)

A program reads buckwheat's file only after your main config includes it.
There is no `buckwheat install` command. Setup happens in the TUI, as part of
the first change that needs it:

1. Type a request and press Enter. If a target in the change is detected but
   its include is missing, the preview starts with **Set up and apply**: it
   names the programs that will be set up and says that this adds a marked
   include block to their main config (for example
   `~/.config/kitty/kitty.conf`). Only the targets in this change are set up.
   Others stay as they are.
2. The preview shows two diffs: **Setup** (the marked include block added to
   each main config) and **Your change** (buckwheat's own files with the new
   values). Nothing is written yet. Press Esc to change nothing.
3. Press Enter (or `y`). The button says **Set up and apply (Enter)**. The setup
   and the change are written together, as one snapshot, and go through one
   countdown: press `k` or Enter to keep both, or Esc or `r` to undo both. If
   you do nothing, both are undone. History (F2) shows them as one entry.

You do this once per target. After it, the preview is a plain Apply and
`buckwheat check` shows `bootstrapped: yes`. hyprpaper has no include, so it
never needs setup.

If buckwheat cannot set a target up (for example an alacritty
`import = [...]` that spans several lines, or Hyprland 0.57+ without a
`hyprland.lua`), the preview lists it as *skipped* with the reason, and the
change is not applied to it. The other targets are still set up and changed.
If every target is skipped, there is nothing to apply and the preview has only
Cancel.

If a program still does not include buckwheat's file after a change (this
should not happen), the countdown and the result say which programs will not
show the change, instead of a plain "Kept".

#### With `bootstrap = "manual"`

With `bootstrap = "manual"`, buckwheat never edits your own files. When a
change touches a target that is not set up, the preview lists that target as
*skipped* (not set up yet), shows the exact lines to add, and does not apply the
change to it, for example:

```text
Add these lines at the end of ~/.config/kitty/kitty.conf:

# >>> buckwheat (managed include) >>>
include buckwheat.conf
# <<< buckwheat <<<
```

Targets that are already set up are changed as usual. If no target in the
change is set up, the preview has only Cancel and writes nothing. Add the lines
yourself:

1. Paste the block into your main config, exactly as shown. Do not add extra
   blank lines, and do not change the marker lines. Uninstall finds the block
   by its markers, and an extra blank line left behind is not removed.
2. Ask for your change again in buckwheat and keep it. This creates
   buckwheat's managed file. Until then the include points at a file that does
   not exist yet, and a program such as Hyprland may report that as a config
   error.
3. Reload or restart the program if buckwheat did not.

The same steps work if you would rather add the include without the TUI.

Exact blocks (for a default setup, with the same markers that setup adds):

kitty, at the end of `kitty.conf`:

```text
# >>> buckwheat (managed include) >>>
include buckwheat.conf
# <<< buckwheat <<<
```

Hyprland 0.57+, at the end of `hyprland.lua` (Lua comments, so the markers
start with `--`):

```lua
-- # >>> buckwheat (managed include) >>>
pcall(require, "buckwheat")
-- # <<< buckwheat <<<
```

Hyprland before 0.57, at the end of `hyprland.conf`:

```text
# >>> buckwheat (managed include) >>>
source = ~/.config/hypr/buckwheat.conf
# <<< buckwheat <<<
```

alacritty, at the end of `alacritty.toml`, if the file has no `[general]` table:

```text
# >>> buckwheat (managed include) >>>
[general]
import = ["~/.config/alacritty/buckwheat.toml"]
# <<< buckwheat <<<
```

If `alacritty.toml` already has a `[general]` table, do not add a second one.
Put the marked `import = [...]` lines inside it. Setup does the same, and an
`import` list you already have gets buckwheat's path added to it. If that list
was empty, uninstall leaves `import = []` behind, which does nothing. See
[`buckwheat uninstall`](#buckwheat-uninstall).

Waybar, in `config.jsonc`, directly after the opening `{`. Replace the path
with your own absolute path to `buckwheat.jsonc`:

```jsonc
  // # >>> buckwheat (managed include) >>>
  "include": ["/home/you/.config/waybar/buckwheat.jsonc"],
  // # <<< buckwheat <<<
```

Waybar, at the end of `style.css`. Keep the blank line before the block:

```css

/* # >>> buckwheat (managed include) >>> */
@import url("buckwheat.css");
/* # <<< buckwheat <<< */
```

### Alacritty: shadowed keys

Alacritty gives your main `alacritty.toml` priority over imported files. If
you set a key there that buckwheat also manages (for example `font.size` or
`window.opacity`), your value wins and buckwheat's value is silently ignored.
Buckwheat never edits your file to fix this. Instead, `buckwheat check` lists
the keys under `shadowed keys` for alacritty. Once alacritty is detected, that
is reported as a problem, `your own config overrides ...`. To fix it, remove
the key from `alacritty.toml` yourself.

---

## 7. Commands: `check` and `uninstall`

Running `buckwheat` with no arguments opens the TUI. `buckwheat --version`
prints the version, and `buckwheat --help` lists the commands.

### `buckwheat check`

A read-only report. It writes nothing, changes nothing, and makes no network
call. For each target it shows whether the program is detected, which files
buckwheat uses, whether the include is in place, and the managed values. It
also shows the provider, whether its key variable is set, and whether its SDK
imports.

```sh
buckwheat check                  # the default config
buckwheat --config my.toml check # another config
buckwheat check --hyprland-errors  # also runs `hyprctl configerrors` and prints its raw output
```

Exit status: `0` means everything that is set up looks usable. `1` means
there is at least one problem. Problems include an unset key variable, a
config that will not load, a config buckwheat cannot manage (`status: weird`),
shadowed keys, and no target installed at all. Targets that are not installed
are listed but are not problems, unless none is installed.

### `buckwheat uninstall`

Removes the include blocks buckwheat added to your config files, and deletes
buckwheat's managed files (only the ones that carry buckwheat's header). It
never deletes a file you wrote, and it never edits a line outside its own
marked block.

```sh
buckwheat uninstall              # all targets: shows the diff, then asks y/N
buckwheat uninstall kitty        # one target
buckwheat uninstall --yes        # no question
```

It prints the full diff before anything is written. With `bootstrap = "manual"`
it does not edit your files. It prints the lines to remove, and the managed
files stay until those lines are gone. Then run `buckwheat uninstall` again.

When it is done, it prints one of these:

- `Removed. A backup is kept as snapshot <id>.` Everything it was asked to
  remove is gone.
- `Removed what buckwheat could remove. A backup is kept as snapshot <id>.`
  followed by `Still in your files. Buckwheat never edits lines outside its
  own block, so remove these by hand:` and a list. Something is left in your
  files that buckwheat does not edit. For example, a bare `include
  buckwheat.conf` line in `kitty.conf`, outside the marked block, a
  `require("buckwheat")` line in `hyprland.lua`, or an empty `import = []` in
  `alacritty.toml` (when that list had only buckwheat's entry in it). Remove
  those lines yourself.
- `Nothing for buckwheat to remove.` There was no change to make. If lines are
  still left from an earlier run, the same list follows. It is shown again every
  time you run the command.

Exit status: `0` when done or when there is nothing to remove (also when lines
are left for you to remove), `1` when you declined or the change failed, `2`
for bad input (an unknown target, or a config that will not load).

After an uninstall, buckwheat keeps a snapshot of the files as it was before.
Its id is printed, and you can roll back to it from the F2 history screen.

The TUI has an uninstall screen too: press **F3** on the request screen, pick
"All targets" or one target, and look at the diff. Enter applies it, and it
goes through the keep-or-revert countdown like any change. Lines that stay in
your files are shown in the preview as `! remove by hand: ...`. When you keep
the change, the TUI shows the same `Removed what buckwheat could remove.` line
and list as the command line does. If you revert, nothing was left behind.

---

## 8. Troubleshooting and recovery

| Symptom | Cause and fix |
|---|---|
| `ANTHROPIC_API_KEY is not set` (or your variable) | The variable is not in the environment that started buckwheat. Export it in that shell or session. A launcher that does not read your shell profile may not see it. |
| `value at '...' looks like an API key` | A key was pasted into the config. Remove it, put the key in an environment variable, and store only the variable's name. Buckwheat never prints the key. |
| `... needs a model in its config table` | An `openai_compat` setup needs `model`. Add it. |
| `... may be empty only for openai_compat with a localhost base_url` | An Anthropic or remote endpoint table has no `api_key_env`. Set it. |
| `Extra inputs are not permitted` | A key is in the wrong table or after a `[table]` header. Move top-level keys like `bootstrap` above the first `[table]`. |
| The change is applied, but the program looks unchanged | The include block is missing. `buckwheat check` shows `bootstrapped: no`. Set the target up: ask for a change in the TUI and press Enter on **Set up and apply** ([section 6](#setting-up-a-target-install)). With `bootstrap = "manual"`, add the lines by hand. |
| `your own config overrides font.size` (or another key) | Your main file sets a key that buckwheat manages. Remove it from your file. See [Alacritty: shadowed keys](#alacritty-shadowed-keys). |
| `status: weird` | Buckwheat cannot parse that file, or it is in a shape it will not edit. Fix the TOML or JSON, or edit it by hand. |
| `Reload failed, so the change was undone` | Printed by `buckwheat uninstall`. The program did not reload after the files changed, so buckwheat put the files back. The reason follows the message. Fix it and try again. For Hyprland, `hyprctl` must be on your `PATH`. A change made in the TUI is reverted the same way, with the reason shown. |
| `Could not check for unfinished changes` | When the TUI started, buckwheat could not read its snapshots. Check that `~/.local/state/buckwheat/` can be read. |
| `Undid N unfinished change(s) from a previous run.` | This is recovery, not an error. See below. |

### Recovery after a crash

A change is saved as a **pending** snapshot until you keep or revert it. If the
terminal closes or buckwheat crashes during the countdown, the snapshot stays
pending.

When the TUI starts, it checks for pending snapshots. Any whose countdown has
already passed are reverted, and you see the notice `Undid N unfinished
change(s) from a previous run.` A snapshot still inside its countdown is left
alone, because it may belong to a session that is still running. It is
checked again the next time the TUI starts.

Recovery runs when the TUI starts, not in `buckwheat check` or in
`buckwheat uninstall`.

### Going back to an earlier state

1. Press **F2** in the TUI to open the history. It lists your recent changes,
   newest first.
2. Select the change you want, and press Enter or `r` to roll it back.
   Rolling back restores the files from that snapshot and reloads the
   programs. If a file was changed after the snapshot, buckwheat asks before
   overwriting it.

Snapshots are stored in `~/.local/state/buckwheat/snapshots/`, and the most
recent `snapshot_retention` finished snapshots are kept.

### Removing buckwheat completely

Run `buckwheat uninstall`, check the diff, and answer `y`. This removes the
include blocks and buckwheat's managed files. Your own files keep everything
else you wrote. If it lists lines under `Still in your files`, remove those
by hand to finish. Buckwheat's state in `~/.local/state/buckwheat/` (saved values
and snapshots) is kept, so a later install can reuse the values. Delete that
folder by hand if you want it gone. If you made a backup of your config
directories before you started, compare them with it to confirm the result.
