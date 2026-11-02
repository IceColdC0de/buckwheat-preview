# Buckwheat: Brand Identity & Voice Guide

> *"A wholesome grain for your Linux desktop."*

Buckwheat is a plain-English ricing tool for Linux desktops (Hyprland, Waybar, terminal emulators). It bridges the gap between aspirational desktop aesthetics (like those seen on r/unixporn) and everyday users who want a stunning setup without spending dozens of hours wrestling with complex syntax, CSS selectors, and fragile configs.

---

## 1. Brand Pillars & Mission

1. **Democratize Desktop Aesthetics**  
   Linux ricing should not be locked behind a steep learning curve or gatekept by dotfile complexity. If you can describe what you want, you should be able to see it on your screen.

2. **Honest, Grounded Craftsmanship**  
   No hype, no buzzword soup. Buckwheat is transparent about how it works and what it changes.

3. **Respect the User's System**  
   Never overwrite someone's hard work blindly. Managed overlays, explicit previews, and instant rollbacks ensure ricing is welcoming and low-stress.

---

## 2. The Emblem: The Buckwheat Sprig (Concept B)

The official emblem is the **Buckwheat Sprig**—a minimalist botanical mark featuring Buckwheat's trademark spade/heart-shaped leaf paired with a cluster of faceted groats along a clean stalk.

### ASCII / TUI Header Art

```text
       \   /
        \ /      BUCKWHEAT
         |       A wholesome grain for your Linux desktop
        /▲\
       /▲▲▲\     Plain-English ricing for Hyprland & Waybar
         |
         |
```

### Compact Inline / Single-Line Variant
```text
  \v/ BUCKWHEAT
```

---

## 3. Color Palette: Warm Earth & Grain

Inspired by toasted groats, warm parchment, and organic earth tones. Warm and welcoming, harmonizing seamlessly with terminal schemes like Gruvbox, Everforest, and Catppuccin Warm/Latte.

| Token | Name | Hex Code | Terminal ANSI / Textual Use |
|---|---|---|---|
| `groat-accent` | Toasted Groat (Gold) | `#E0A96D` | Primary brand accent, titles, highlights |
| `parchment` | Oatmeal / Warm Cream | `#EBDCB9` | Subtitles, key UI borders, highlighted text |
| `husk-dim` | Roasted Husk (Brown) | `#8C5835` | Inactive borders, muted metadata |
| `sage-green` | Sprout Sage | `#8F9E8B` | Success states, diff additions (`+`) |
| `ember-red` | Hearth Red | `#C86452` | Warnings, diff deletions (`-`), rollback notice |
| `soil-bg` | Roasted Soil (Dark) | `#201E1C` | TUI background |
| `flour-fg` | Milled Flour (White) | `#F7F4EE` | Primary body text |

---

## 4. Voice, Tone & UI Vocabulary

Buckwheat speaks like a patient, knowledgeable craftsman in a workshop—approachable, encouraging, clear, and never condescending.

### The "Light Thematic Flavor" Rule
We use warm, organic culinary and grain metaphors to add personality to the workflow, while keeping core utility actions crisp and obvious.

| Context | Standard / Bland | Buckwheat Warm Voice |
|---|---|---|
| Prompt placeholder | `Enter prompt:` | `What vibe are we cooking up today?` |
| Loading / Planning | `Generating configs...` | `Preparing the batch...` |
| Diff review heading | `Diff preview` | `Freshly prepared diff` |
| Safe Revert Banner | `Revert timer: 30s` | `Testing new look (auto-reverting in 30s) • Press Esc to cancel` |
| Keep changes | `Accept changes` | `Keep changes [Enter]` |
| Revert changes | `Rollback` | `Revert to previous [Esc]` |

---

## 5. Honest Guardrails Messaging Framework

Our users are often nervous about breaking their working environments. We build trust through **honest, transparent guardrails**:

1. **Managed Overlays, Not Overwrites:**  
   Buckwheat writes to dedicated include files (e.g. `buckwheat.conf`), keeping the user's master configs intact.
2. **Inspect Before You Apply:**  
   Every single config change is displayed in a clean, syntax-highlighted visual diff before touching disk.
3. **Timed Revert Safety Net:**  
   When changes are applied, a keep-or-revert timeout runs automatically. If something looks broken or wrong, pressing `Esc` (or letting the timer expire) restores the previous state immediately.
4. **Structured Changes:**  
   Buckwheat creates targeted, validated configuration adjustments—never opaque, uncontrolled shell scripts.

---

## 6. Official Copy & Tagline Directory

* **Primary Tagline:**  
  `A wholesome grain for your Linux desktop.`

* **Repository Subtitle:**  
  `Plain-English ricing for Hyprland and friends. Describe your vibe; Buckwheat prepares the diff.`

* **Short Bio / Social Blurb:**  
  `Buckwheat brings r/unixporn aesthetics to everyone. Describe the look you want in plain words, inspect the freshly prepared diff, and enjoy your new desktop.`
