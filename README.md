# buckwheat

**Plain-English ricing for Hyprland and friends.**

Describe the look you want, like *"warm earthy tones, rounded Waybar modules,
muted borders"*, and buckwheat turns it into a change you can preview, apply,
and undo.

## How it works

An LLM turns your request into a **change plan**: a short list of structured
operations, such as "set kitty's font size to 13". Buckwheat checks the plan
against its own rules, shows you a diff, and only writes files once you
approve it.

The model never writes shell commands or free-form config text. Every change
goes through a small adapter for each program that knows how its config is
written.

## Supported programs

| Program | What it can change | File buckwheat writes |
|---|---|---|
| kitty | colours, font, opacity, padding | `~/.config/kitty/buckwheat.conf` |
| alacritty | colours, font, opacity, padding | `buckwheat.toml` next to your `alacritty.toml` |
| Hyprland | borders, gaps, rounding, blur, shadows, animations, opacity | `~/.config/hypr/buckwheat.lua` (0.57+) or `buckwheat.conf` (older) |
| Waybar | layout, colours, font | `~/.config/waybar/buckwheat.jsonc` and `buckwheat.css` |
| hyprpaper | wallpaper and fit mode | `~/.config/hypr/hyprpaper.conf`, only if you don't have one |

Built-in palettes: catppuccin, dracula, gruvbox, nord, rose-pine, solarized,
tokyo-night.

## Requirements

- Linux and Python 3.11 or newer
- An API key for Anthropic (default) or any OpenAI-compatible endpoint
  (OpenAI, OpenRouter, Gemini, Ollama)

## Install

```sh
git clone https://github.com/IceColdC0de/buckwheat.git
cd buckwheat
python3 -m venv ~/.venvs/buckwheat
~/.venvs/buckwheat/bin/pip install .
```

Add `~/.venvs/buckwheat/bin` to your `PATH` to run `buckwheat` from anywhere.

## Usage

```sh
export ANTHROPIC_API_KEY=...   # read from the environment, never stored
buckwheat check                # read-only: shows what buckwheat sees
buckwheat                      # start the app
```

Type a request and press Enter. Buckwheat shows the diff. Press Enter again to
apply it, then **keep** it (Enter) or **undo** it (Esc) before the countdown
ends. If you do nothing, the change is undone.

The first time you change a program, buckwheat also adds one marked include
line to that program's config so it loads buckwheat's file. You see that edit
in the same preview.

| Key | Action |
|---|---|
| F2 | History: see past changes and roll any of them back |
| F3 | Remove buckwheat from your configs |

## Configuration

Optional. Buckwheat reads `~/.config/buckwheat/config.toml`:

```toml
provider = "anthropic"          # or "openai_compat"
bootstrap = "confirm"           # "manual" = never edit your own config files

[providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"

[safety]
revert_timeout_seconds = 15
```

For an OpenAI-compatible endpoint:

```toml
provider = "openai_compat"

[providers.openai_compat]
base_url = "https://openrouter.ai/api/v1"
model = "your-model-name"
api_key_env = "OPENROUTER_API_KEY"
```

The config holds the *name* of an environment variable, never the key itself.
Buckwheat refuses values that look like keys.

## Safety

- **Preview first.** You see every file a change touches before anything is
  written.
- **Snapshots.** Your files are saved before each change, and any change can
  be rolled back from History.
- **Undo by default.** A change is reverted unless you keep it.
- **Your files stay yours.** Buckwheat writes its own files and edits yours
  only inside its marked include block.
- **No shell from the model.** Generated files can't run programs or reach the
  network. Buckwheat only runs fixed reload commands such as `hyprctl`.

## Uninstall

```sh
buckwheat uninstall
rm -rf ~/.venvs/buckwheat ~/.config/buckwheat ~/.local/state/buckwheat
```

`buckwheat uninstall` shows a diff, then removes the include lines and
buckwheat's files. Everything else in your configs is left as it was.
