# buckwheat
```text
       \   /
        \ /      BUCKWHEAT
         |       A wholesome grain for your Linux desktop
        /▲\
       /▲▲▲\     Plain-English ricing for Hyprland & Waybar
         |
         |
```

> **A wholesome grain for your Linux desktop.**  
> Plain-English ricing for Hyprland and friends. Describe your vibe; Buckwheat prepares the diff.

---

## What is Buckwheat?

Buckwheat customises a Linux desktop from plain-text requests. You describe the
look you want, for example *"warm earthy tones, rounded Waybar modules, muted
borders"*. An LLM turns that into a **change plan**: a short list of structured
operations, such as "set kitty's font size to 13". Buckwheat checks the plan
against its own rules, shows you a diff, and only then writes the files.

The model never writes shell commands or free-form config text. Each change goes
through a small set of adapters, one per program, that know how that program's
config is written.

---

## Supported targets

| Target | What buckwheat changes | Files it manages |
|---|---|---|
| kitty | colours, font, opacity, padding | `~/.config/kitty/buckwheat.conf` |
| alacritty | colours, font, opacity, padding | `buckwheat.toml` next to your `alacritty.toml` (`~/.config/alacritty/` by default) |
| Hyprland | borders, gaps, rounding, blur, animations, opacity. Lua config (`hyprland.lua`, Hyprland 0.57+) and classic `hyprland.conf` (older Hyprland) | `~/.config/hypr/buckwheat.lua` (Lua) or `~/.config/hypr/buckwheat.conf` (classic) |
| Waybar | bar layout, colours, font | `~/.config/waybar/buckwheat.jsonc` and `buckwheat.css` |
| Wallpaper (hyprpaper) | wallpaper image and fit mode | `~/.config/hypr/hyprpaper.conf`, only when you have no file of your own |

Built-in palettes: catppuccin, dracula, gruvbox, nord, rose-pine, solarized,
tokyo-night.

Buckwheat needs Linux and Python 3.11 or newer. It uses Anthropic by default, or
any OpenAI-compatible endpoint (OpenAI, OpenRouter, Gemini, Ollama).

---

## Install

From a checkout of this repository, in a virtual environment:

```sh
python3 -m venv ~/.venvs/buckwheat
~/.venvs/buckwheat/bin/pip install .
~/.venvs/buckwheat/bin/buckwheat --version
```

Add `~/.venvs/buckwheat/bin` to your `PATH` to run `buckwheat` directly.

---

## Quick start

1. **Set your API key in the environment.** The key stays out of the config file;
   buckwheat reads it from this variable:

   ```sh
   export ANTHROPIC_API_KEY=...
   ```

2. **Check what buckwheat sees.** This is read-only and writes nothing:

   ```sh
   buckwheat check
   ```

   It lists any problems at the end. The exit status is `0` when everything
   looks usable.

3. **Start the app and describe a change:**

   ```sh
   buckwheat
   ```

   Type a request such as `warm earthy tones with rounded Waybar modules` and
   press Enter. Review the diff.

4. **Set up the target, the first time.** Each program reads buckwheat's file
   through one **include line** in its main config. If a target in your change
   does not have it yet, the preview says **Set up and apply**: it names the
   programs and shows the marked include block it will add to their config,
   next to your change. Press Enter to do both; one countdown keeps or undoes
   both together. There is no `buckwheat install` command. With
   `bootstrap = "manual"`, buckwheat shows the lines instead, skips that
   target, and you paste them yourself. See the
   [user guide](docs/user-guide.md#setting-up-a-target-install).

5. **Apply, then keep or revert.** Press Enter (or `y`) on the preview to apply
   the change. It is live for a countdown (15 seconds by default). Press Enter
   or `k` to keep it, or Esc or `r` to undo it. If you do nothing, it is
   undone.

---

## Safety

- **Diff preview.** You see every file a change touches before anything is written.
- **Snapshots.** Each applied change saves the previous files first. The history
  screen (F2) lists them, and you can roll back any of them.
- **Keep-or-revert countdown.** A change is undone unless you keep it before the
  countdown ends.
- **Your files stay yours.** Buckwheat edits your own config only inside its
  marked include block. It does that when you set a target up in the TUI and in
  `buckwheat uninstall`, after showing the diff, or when you paste the block
  yourself (`bootstrap = "manual"`).
- **No shell from the model.** Plans are validated before anything is written.
  The model's output never becomes a shell command. Buckwheat only runs fixed
  reload commands, such as `hyprctl` or `pkill`.
- **Clean removal.** `buckwheat uninstall` (or F3 in the TUI) removes the
  include blocks and buckwheat's own files, after showing the diff. Everything
  else in your config is left as it was. If a line outside buckwheat's block
  is left, such as a bare `include buckwheat.conf`, the command lists it for you
  to remove.

---

## Documentation

- [User guide](docs/user-guide.md): config options, provider setup, example
  requests, palettes, the include line, `check` and `uninstall`, and
  troubleshooting and recovery.
- [Release checklist](docs/release-checklist.md)
- [Design notes](docs/design.md) and [palette format](docs/palettes.md)

---

## Brand & Design

For design tokens, the warm grain color palette, and voice guidelines, see [BRANDING.md](BRANDING.md).

---

## Website

The Buckwheat showcase website is located in `website/` and configured for turnkey Vercel deployment with `vercel.json`.

Preview locally:
```bash
python3 -m http.server 8080 -d website
# or with npm:
cd website && npm start
```

Run website tests:
```bash
cd website && node --test
# or via pytest:
pytest tests/test_website.py
```

---

## Development setup

Requires Python 3.11 or newer.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

pytest              # run tests
ruff check .        # lint
mypy                # type-check (strict, src/)
buckwheat --version # print the version
buckwheat           # start the TUI
buckwheat check     # read-only report on this desktop; writes nothing
```
