# Release checklist

Use this list for every release of buckwheat. Work from a clean checkout of
`main` (`git status` shows nothing). Agents open pull requests. The owner
reviews and merges them, and tags the release.

## 1. Prepare the release

- [ ] Pick the new version number.
- [ ] Bump the version in `src/buckwheat/__init__.py` (`__version__`). That
      is the only place it is set. `pyproject.toml` reads it from there.
      `website/package.json` has its own version. Change it only if the
      website changed.
- [ ] Update `CHANGELOG.md`. Move the entries under `## Unreleased` into a new
      heading for the version and the date, and leave `## Unreleased` empty
      for the next cycle. Each entry says what users will notice. Do not list
      internal refactors.
- [ ] Check that the docs match the release. Run each documented command you
      changed (see the user guide and README).
- [ ] No new dependency unless the release needs it, and each one is justified
      in its pull request.

## 2. Run the checks (all must pass)

From the repository root, in a virtual environment with the dev extras
installed (`pip install -e ".[dev]"`):

```sh
pytest              # full test suite
ruff check .        # lint
mypy                # type-check (strict, src/)
cd website && node --test   # website tests
```

- [ ] `pytest` passes.
- [ ] `ruff check .` is clean.
- [ ] `mypy` is clean.
- [ ] The website tests pass.
- [ ] CI is green on the release pull request. CI runs these checks on Python
      3.11 and 3.14.

## 3. Clean-venv install smoke test

This checks that the package installs from source into a fresh environment,
with its data files, and that the entry point runs. It uses the release
commit, not the editable development install.

```sh
python3.11 -m venv /tmp/buckwheat-smoke
/tmp/buckwheat-smoke/bin/pip install .
/tmp/buckwheat-smoke/bin/buckwheat --version
/tmp/buckwheat-smoke/bin/buckwheat --help
/tmp/buckwheat-smoke/bin/python -c "from buckwheat.plan.palettes import list_palettes; print(len(list_palettes()), 'palettes')"
/tmp/buckwheat-smoke/bin/buckwheat check; echo "exit $?"
```

- [ ] `pip install .` succeeds in the clean environment.
- [ ] `--version` prints the new version.
- [ ] The palette count is 7. A smaller number means a palette file was not
      packaged.
- [ ] `buckwheat check` runs without a traceback. Exit `1` is expected on a
      machine with no API key set and no target installed. Exit `0` is
      expected on a desktop that is set up.

Remove the smoke environment when you are done: `rm -rf /tmp/buckwheat-smoke`.

## 4. Manual check on a real desktop

Automated tests run against fixture files. This step checks the real programs.
Run it on a Hyprland session with kitty, alacritty, waybar and hyprpaper
installed. Skip a target that you do not use, and say so in the release notes.

Before you start, back up the config directories you use, so you can compare
them after uninstall:

```sh
tar -czf ~/buckwheat-before.tgz -C ~ .config/kitty .config/alacritty .config/hypr .config/waybar
```

Leave out any directory that does not exist on your machine.

- [ ] `buckwheat check` reports no problems, and each target you use shows
      `detected: yes`. Also run `buckwheat check --hyprland-errors`, and
      confirm that `hyprctl configerrors` reports nothing.
- [ ] Set up each target from the TUI: make a request, check that the
      preview says "Set up and apply" and names the targets, press Enter, and
      keep it (see
      [the user guide, section 6](user-guide.md#setting-up-a-target-install)).
      The setup diff must add only the marked block to your own file, and the
      change must show on screen. Afterwards, `buckwheat check` shows
      `bootstrapped: yes` for each.
- [ ] With `bootstrap = "manual"` in a copy of the config, make a request and
      check that the preview shows the lines to paste, lists the target as
      skipped, and writes nothing to it.
- [ ] Hyprland expands `~` in the `source = ~/.config/hypr/buckwheat.conf`
      line. This has not been checked on a real Hyprland. Run
      `hyprctl configerrors` after setup and confirm it reports nothing, and
      that a Hyprland change shows.
- [ ] Hyprland 0.57+ (Lua config): `buckwheat check` shows
      `config: Lua config (hyprland.lua)`. After setup, a gap or border
      colour change shows after keep without the monitors flickering
      (`hyprctl reload config-only`), wins over the same setting in your own
      modules, and `hyprctl configerrors` reports nothing. Live reload has
      not been checked on a running Hyprland. Then make a change that
      Hyprland rejects (a bad value in one of your own modules) and confirm
      the plan is refused with Hyprland's message.
- [ ] One request per target. For each one:
  - [ ] the diff shows only the expected file changes;
  - [ ] the program shows the change after you keep it;
  - [ ] the request is reverted (press `r` in the countdown) and the program
        goes back to the previous look.
  - Requests to try: a kitty palette change, an alacritty font size change,
    a Hyprland gap and rounding change, a Waybar palette change, and a
    wallpaper change.
- [ ] Waybar shows the colours from `buckwheat.css`. Check this after a
      restart of waybar. The `@import` sits at the end of `style.css`, and
      this has not been checked on a real Waybar. A CSS `@import` is normally
      expected before other rules, so confirm that it is applied.
- [ ] `buckwheat uninstall` shows the diff. Answer `y`. Note whether it
      printed `Removed.` or `Removed what buckwheat could remove.` with a
      list. Remove the listed lines by hand before the comparison below, and
      record them in the release pull request.
- [ ] After uninstall, compare the directories with the backup. Your own files
      must match the backup exactly:

      ```sh
      mkdir -p /tmp/bw-before && tar -xzf ~/buckwheat-before.tgz -C /tmp/bw-before
      for d in kitty alacritty hypr waybar; do
        [ -d /tmp/bw-before/.config/$d ] && diff -r /tmp/bw-before/.config/$d ~/.config/$d && echo "$d: same"
      done
      ```

      Every directory should print `same`. The managed files buckwheat
      created, such as `buckwheat.conf`, should be gone, so they do not show
      up in the comparison. Any other difference needs an explanation in the
      release pull request.
- [ ] Record each result in the release pull request: what passed, what was
      skipped, and any difference you could not explain.

## 5. Changelog and release notes

- [ ] `CHANGELOG.md` has the new version heading, with the date.
- [ ] The release notes list any known limitation a user will hit. Include
      that setup is a TUI step with no `buckwheat install` command.
- [ ] With only `~/.alacritty.toml` present (in a test HOME), setup lists
      alacritty as skipped with "alacritty reads ... add the include to ...
      by hand", and writes nothing.
- [ ] With only `~/.config/alacritty/alacritty.yml` in a test HOME, setup lists
      alacritty as skipped with "alacritty reads ... a YAML file buckwheat does
      not edit; run "alacritty migrate" to convert it to TOML, then install
      again", and writes nothing.

## 6. Merge and tag (owner)

- [ ] The owner merges the release pull request after review.
- [ ] The owner tags the merged commit as `vX.Y.Z` on `main`.
- [ ] Remove the backup archive, or keep it outside the repository:
      `rm ~/buckwheat-before.tgz`.
