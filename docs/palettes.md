# Palette library

Palettes are data. They live in `src/buckwheat/plan/palettes/<name>.toml`
(package data) and are loaded by `plan/palettes.py` into a validated
`Palette` model. One file per palette *family*; variants are tables in it.

## File format

```toml
name = "catppuccin"
description = "Soothing pastel theme"
default_variant = "mocha"

[variants.mocha]
dark = true

[variants.mocha.colors]
bg        = "#1e1e2e"
bg_alt    = "#181825"
surface   = "#313244"
fg        = "#cdd6f4"
fg_dim    = "#a6adc8"
accent    = "#89b4fa"
accent_alt = "#cba6f7"
warning   = "#f9e2af"
error     = "#f38ba8"
cursor    = "#f5e0dc"
selection_bg = "#585b70"
selection_fg = "#cdd6f4"

[variants.mocha.ansi]
black = "#45475a"
red = "#f38ba8"
green = "#a6e3a1"
yellow = "#f9e2af"
blue = "#89b4fa"
magenta = "#f5c2e7"
cyan = "#94e2d5"
white = "#bac2de"
bright_black = "#585b70"
bright_red = "#f38ba8"
bright_green = "#a6e3a1"
bright_yellow = "#f9e2af"
bright_blue = "#89b4fa"
bright_magenta = "#f5c2e7"
bright_cyan = "#94e2d5"
bright_white = "#a6adc8"
```

Rules (enforced by the loader and a test over every file):
- All colour slots above are required; all values match `^#[0-9a-f]{6}$`.
- `name` and variant names match `^[a-z0-9-]+$`; filename equals `name`.
- Single-variant palettes use a variant named `default`.

## Consumption

- `PaletteOp(palette, variant?)` is resolved by name to a `Palette` variant;
  unknown name/variant is a validation error. `variant` omitted means
  `default_variant`.
- Each adapter has a **slot map** in code, e.g. kitty:
  `{"background": "colors.bg", "foreground": "colors.fg", "color1": "ansi.red", ...}`,
  Hyprland: `general.col.active_border <- colors.accent`, waybar: CSS
  `@define-color` names (`bg`, `fg`, `accent`, ...). `render` applies the map
  and treats the result as ordinary registry `set` values (so the same
  colour validators run).
- Explicit `set` ops on colour keys for the same target conflict with a
  palette op and are rejected (see design.md section 2.3).
- The LLM sees only palette names and descriptions in its context, never
  hex values.
- Colour values for Hyprland are converted to `rgba(rrggbbff)` by the
  adapter; no format conversion happens in the palette files.
