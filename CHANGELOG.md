# Changelog

Entries are for people who use buckwheat. Newest first. See
[docs/release-checklist.md](docs/release-checklist.md) for how a release is cut.

## Unreleased

- New: on Hyprland 0.57+ (Lua config) buckwheat can set any appearance
  setting Hyprland has, about 240 of them: shadows, glow, blur passes and
  effects, dimming, group bars, gaps per side, gradient borders at an angle
  (`"#88c0d0 #b48ead 45deg"`), and more. The list comes from Hyprland's own
  typed key list, so a newer Hyprland brings its new keys along. Keyboard,
  input, key binding, GPU/render, debug and lock-screen settings stay off
  limits. Palettes now also colour group bars and group borders, the shadow
  and the background colour.
- Safety: nothing buckwheat writes can start a program or reach the network.
  Every generated file is checked before the preview: no `exec`, key binds,
  event hooks or timers in Hyprland files, no Waybar custom modules or click
  commands, and no CSS `url()` outside your own files.
- Fixed: a change to a program that was not set up yet was written to a file
  that program does not read, so nothing changed on screen, and it was still
  reported as applied. Now the preview says **Set up and apply**: it names the
  programs it will set up, shows the include block it adds to their main
  config next to your change, and Enter does both as one change with one
  countdown (Esc undoes both; History shows one entry). Programs that cannot
  be set up are listed as skipped with the reason, and the rest still apply.
  The separate "Set up first" (`i`) step is gone. With `bootstrap = "manual"`,
  the preview shows the lines to add and the change is not applied to programs
  you have not set up yet.
- Fixed: every request to Anthropic failed with HTTP 400, because current
  models reject forced tool use. The model is now asked to call the plan tool
  instead. The default `max_tokens` for Anthropic is now 16000 (thinking counts
  against it, so 2048 could cut a plan short). When Anthropic rejects a
  request, the error now includes Anthropic's own reason, for example
  `Anthropic request failed (HTTP 400): credit balance too low`.
- Plain-text requests produce a validated change plan. Nothing is written
  until you apply it, and every apply is undone unless you keep it within the
  countdown.
- Targets: kitty, alacritty, Hyprland, Waybar, and the hyprpaper wallpaper.
- Built-in palettes: catppuccin, dracula, gruvbox, nord, rose-pine, solarized,
  tokyo-night.
- Providers: Anthropic (default) and OpenAI-compatible endpoints (OpenAI,
  OpenRouter, Gemini, Ollama). API keys are read from environment variables.
- `buckwheat check`: a read-only report on your setup.
- Setup: when a target does not include buckwheat's file yet, the TUI preview
  sets it up together with your change ("Set up and apply"), adding the
  marked include block to your config. With `bootstrap = "manual"`, it shows
  the lines to paste instead. There is no `buckwheat install` command.
- `buckwheat uninstall` (and F3 in the TUI): removes the include blocks and
  buckwheat's own files, after showing the diff. If it leaves a line behind,
  such as a bare `include buckwheat.conf` in `kitty.conf`, it says
  `Removed what buckwheat could remove.` and lists the lines to remove by hand.
- alacritty is found the way alacritty finds its config:
  `$XDG_CONFIG_HOME/alacritty`, then `$XDG_CONFIG_HOME`, then
  `~/.config/alacritty`.
- History (F2) lists kept and reverted changes, and lets you roll them back.
- alacritty setup refuses, and writes nothing, when alacritty actually reads
  another file (`~/.alacritty.toml`, a system file in `$XDG_CONFIG_DIRS`, or
  `/etc/alacritty/alacritty.toml`). The setup screen names that file so you
  can add the include there by hand.
- alacritty setup refuses, and writes nothing, when alacritty reads an older
  `alacritty.yml`. The setup screen then lists alacritty as skipped with
  `alacritty reads ... a YAML file buckwheat does not edit; run "alacritty
  migrate" to convert it to TOML, then install again`.
- Hyprland 0.57 and newer: the Lua config (`hyprland.lua`) is supported.
  Buckwheat uses it when `hyprland.lua` exists or `hyprctl version` reports
  0.57 or newer; otherwise it keeps using the classic `hyprland.conf`. It
  writes `~/.config/hypr/buckwheat.lua` (one `hl.config({...})` call) and
  setup adds a marked block with `pcall(require, "buckwheat")` at the end of
  `hyprland.lua`, so buckwheat's values win over your own modules. A
  `hyprland.lua` that is a symlink (dotfiles) is edited at its target and
  stays a symlink. When the `Hyprland` binary is installed, every change is
  first checked with `Hyprland --verify-config` on a temporary copy of your
  config, and any error it reports cancels the change before anything is
  written. Reloads use `hyprctl reload config-only`, so monitors are not set
  up again. With Hyprland 0.57+ and no `hyprland.lua`, setup skips Hyprland
  instead of creating a main config. `buckwheat check` shows which config
  format is in use. `buckwheat uninstall` lists any other
  `require`/`pcall` line that still loads `buckwheat` for you to remove.
- Known limitations: Setup of a target is a step in the TUI, with no
  command-line equivalent. See the user guide, section 6. Hyprland started
  with `-c` pointing at a symlinked `hyprland.lua` looks for `buckwheat.lua`
  next to the symlink's target, not in `~/.config/hypr` (see the user guide,
  section 6).
