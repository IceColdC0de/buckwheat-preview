/**
 * Buckwheat Website Interactive Logic
 * Follows BRANDING.md & UI vocabulary guidelines
 */

document.addEventListener("DOMContentLoaded", () => {
  // --------------------------------------------------------------------------
  // Preset Data for the Vibe to Diff Playground
  // --------------------------------------------------------------------------
  const PRESETS = {
    autumn: {
      name: "Autumn Harvest",
      prompt: "Warm autumn cabin, earthy tones, muted amber borders, clean waybar",
      diff: `--- a/.config/hypr/buckwheat.conf
+++ b/.config/hypr/buckwheat.conf
@@ -1,6 +1,8 @@
 # Managed by buckwheat. Do not edit directly.
-general:col.active_border = rgba(33ccffee) rgba(00ff99ee) 45deg
-general:col.inactive_border = rgba(595959aa)
+general:col.active_border = rgba(e0a96dff) rgba(8c5835ff) 45deg
+general:col.inactive_border = rgba(2b2825aa)
+decoration:rounding = 8
+decoration:active_opacity = 0.96

--- a/.config/waybar/buckwheat.css
+++ b/.config/waybar/buckwheat.css
@@ -1,5 +1,8 @@
 /* Buckwheat managed theme overlay */
-@define-color accent #33ccff;
+@define-color bg #201e1c;
+@define-color fg #f7f4ee;
+@define-color accent #e0a96d;
+@define-color warning #c86452;`,
      plan: {
        schema_version: "1.0",
        vibe_description: "Warm autumn cabin, earthy tones, muted amber borders",
        ops: [
          {
            target: "hyprland",
            action: "set",
            key: "general:col.active_border",
            value: "rgba(e0a96dff) rgba(8c5835ff) 45deg"
          },
          {
            target: "hyprland",
            action: "set",
            key: "general:col.inactive_border",
            value: "rgba(2b2825aa)"
          },
          {
            target: "waybar",
            action: "palette_define",
            palette: "buckwheat",
            variant: "warm-earth"
          }
        ]
      }
    },
    arctic: {
      name: "Arctic Frost",
      prompt: "Nordic frosty morning, polar night dark, soft blue accents for Hyprland",
      diff: `--- a/.config/hypr/buckwheat.conf
+++ b/.config/hypr/buckwheat.conf
@@ -1,6 +1,8 @@
 # Managed by buckwheat. Do not edit directly.
-general:col.active_border = rgba(e0a96dff)
+general:col.active_border = rgba(88c0d0ff) rgba(81a1c1ff) 45deg
+general:col.inactive_border = rgba(2e3440aa)
+decoration:rounding = 10

--- a/.config/kitty/buckwheat.conf
+++ b/.config/kitty/buckwheat.conf
@@ -1,4 +1,7 @@
 # Managed by buckwheat
-background #201e1c
-foreground #f7f4ee
+background #2e3440
+foreground #d8dee9
+color4 #88c0d0`,
      plan: {
        schema_version: "1.0",
        vibe_description: "Nordic frosty morning, polar night dark, soft blue accents",
        ops: [
          {
            target: "hyprland",
            action: "set",
            key: "general:col.active_border",
            value: "rgba(88c0d0ff) rgba(81a1c1ff) 45deg"
          },
          {
            target: "kitty",
            action: "palette_apply",
            palette: "nord",
            variant: "polar-night"
          }
        ]
      }
    },
    rose: {
      name: "Rosé Pine",
      prompt: "Soothing dusk, muted pine, moonlit blush, translucent borders",
      diff: `--- a/.config/hypr/buckwheat.conf
+++ b/.config/hypr/buckwheat.conf
@@ -1,6 +1,8 @@
 # Managed by buckwheat. Do not edit directly.
-general:col.active_border = rgba(88c0d0ff)
+general:col.active_border = rgba(eb6f92ff) rgba(9ccfd8ff) 45deg
+general:col.inactive_border = rgba(191724aa)
+decoration:blur:enabled = true

--- a/.config/waybar/buckwheat.css
+++ b/.config/waybar/buckwheat.css
@@ -1,4 +1,6 @@
-@define-color accent #88c0d0;
+@define-color bg #191724;
+@define-color accent #eb6f92;
+@define-color muted #6e6a86;`,
      plan: {
        schema_version: "1.0",
        vibe_description: "Soothing dusk, muted pine, moonlit blush",
        ops: [
          {
            target: "hyprland",
            action: "set",
            key: "general:col.active_border",
            value: "rgba(eb6f92ff) rgba(9ccfd8ff) 45deg"
          },
          {
            target: "waybar",
            action: "palette_define",
            palette: "rose-pine",
            variant: "main"
          }
        ]
      }
    },
    gruvbox: {
      name: "Retro Roast",
      prompt: "Gruvbox warm espresso, toasted groat gold, high contrast",
      diff: `--- a/.config/hypr/buckwheat.conf
+++ b/.config/hypr/buckwheat.conf
@@ -1,6 +1,8 @@
 # Managed by buckwheat. Do not edit directly.
-general:col.active_border = rgba(eb6f92ff)
+general:col.active_border = rgba(fabd2fff) rgba(d65d0eff) 45deg
+general:col.inactive_border = rgba(282828aa)
+decoration:rounding = 4

--- a/.config/kitty/buckwheat.conf
+++ b/.config/kitty/buckwheat.conf
@@ -1,4 +1,6 @@
-background #191724
+background #282828
+foreground #ebdbb2
+color3 #fabd2f`,
      plan: {
        schema_version: "1.0",
        vibe_description: "Gruvbox warm espresso, toasted groat gold",
        ops: [
          {
            target: "hyprland",
            action: "set",
            key: "general:col.active_border",
            value: "rgba(fabd2fff) rgba(d65d0eff) 45deg"
          },
          {
            target: "kitty",
            action: "palette_apply",
            palette: "gruvbox",
            variant: "dark-hard"
          }
        ]
      }
    }
  };

  // --------------------------------------------------------------------------
  // Palettes Data for the Palette Library Showcase
  // --------------------------------------------------------------------------
  const PALETTES = {
    buckwheat: {
      name: "Buckwheat Earth & Grain",
      desc: "Warm earth, toasted groats, and organic culinary tones. Official theme.",
      swatches: [
        { role: "Accent (Groat)", hex: "#E0A96D" },
        { role: "Parchment", hex: "#EBDCB9" },
        { role: "Husk Brown", hex: "#8C5835" },
        { role: "Sprout Sage", hex: "#8F9E8B" },
        { role: "Hearth Red", hex: "#C86452" },
        { role: "Soil Background", hex: "#201E1C" },
        { role: "Flour White", hex: "#F7F4EE" }
      ]
    },
    catppuccin: {
      name: "Catppuccin Mocha",
      desc: "Soothing pastel palette designed for high contrast and readability.",
      swatches: [
        { role: "Accent (Blue)", hex: "#89B4FA" },
        { role: "Mauve", hex: "#CBA6F7" },
        { role: "Green", hex: "#A6E3A1" },
        { role: "Peach", hex: "#FAB387" },
        { role: "Red", hex: "#F38BA8" },
        { role: "Base Background", hex: "#1E1E2E" },
        { role: "Text", hex: "#CDD6F4" }
      ]
    },
    gruvbox: {
      name: "Gruvbox Dark",
      desc: "Retro groove warm earth colors for long programming sessions.",
      swatches: [
        { role: "Yellow Accent", hex: "#FABD2F" },
        { role: "Orange", hex: "#FE8019" },
        { role: "Green", hex: "#B8BB26" },
        { role: "Aqua", hex: "#8EC07C" },
        { role: "Red", hex: "#FB4934" },
        { role: "Dark0 Background", hex: "#282828" },
        { role: "Light0 Text", hex: "#FBF1C7" }
      ]
    },
    nord: {
      name: "Nord Frost",
      desc: "An arctic, north-bluish color palette made for clean minimalism.",
      swatches: [
        { role: "Frost Cyan", hex: "#88C0D0" },
        { role: "Frost Blue", hex: "#81A1C1" },
        { role: "Aurora Green", hex: "#A3BE8C" },
        { role: "Aurora Yellow", hex: "#EBCB8B" },
        { role: "Aurora Red", hex: "#BF616A" },
        { role: "Polar Night 0", hex: "#2E3440" },
        { role: "Snow Storm 0", hex: "#ECEFF4" }
      ]
    },
    rosepine: {
      name: "Rosé Pine",
      desc: "All natural pine, faux fur, and a bit of soho vibes for the modern minimalist.",
      swatches: [
        { role: "Rose Accent", hex: "#EB6F92" },
        { role: "Foam Cyan", hex: "#9CCFD8" },
        { role: "Gold", hex: "#F6C177" },
        { role: "Pine Green", hex: "#31748F" },
        { role: "Love Red", hex: "#EB6F92" },
        { role: "Base Background", hex: "#191724" },
        { role: "Text", hex: "#E0DEF4" }
      ]
    }
  };

  // --------------------------------------------------------------------------
  // Elements
  // --------------------------------------------------------------------------
  const promptInput = document.getElementById("demo-prompt-input");
  const promptForm = document.getElementById("demo-prompt-form");
  const presetPills = document.querySelectorAll(".preset-pill");
  const diffViewer = document.getElementById("diff-viewer-content");
  const jsonViewer = document.getElementById("json-viewer-content");
  const statusBanner = document.getElementById("demo-status-banner");
  const statusText = document.getElementById("status-text");
  const revertBanner = document.getElementById("safe-revert-banner");
  const revertProgressBar = document.getElementById("revert-progress-bar");
  const revertTimerText = document.getElementById("revert-timer-text");
  const btnKeepChanges = document.getElementById("btn-keep-changes");
  const btnRevertChanges = document.getElementById("btn-revert-changes");
  const tabBtns = document.querySelectorAll(".demo-tab-btn");
  const tabPanes = document.querySelectorAll(".tab-pane");
  const paletteBtns = document.querySelectorAll(".palette-btn");
  const swatchesContainer = document.getElementById("swatches-grid");
  const paletteTitle = document.getElementById("palette-display-name");
  const paletteDesc = document.getElementById("palette-display-desc");
  const btnToggleAscii = document.getElementById("btn-toggle-ascii");
  const copyInstallBtn = document.getElementById("copy-install-btn");

  let activePresetKey = "autumn";
  let revertCountdown = 30;
  let revertInterval = null;

  // --------------------------------------------------------------------------
  // Diff Renderer (Colorizes Unified Diff)
  // --------------------------------------------------------------------------
  function renderDiff(diffText) {
    if (!diffViewer) return;
    const lines = diffText.split("\n");
    diffViewer.innerHTML = "";

    lines.forEach(line => {
      const lineDiv = document.createElement("div");
      lineDiv.className = "diff-line";

      if (line.startsWith("+++") || line.startsWith("---")) {
        lineDiv.className += " diff-header";
        lineDiv.textContent = line;
      } else if (line.startsWith("@@")) {
        lineDiv.className += " diff-hunk";
        lineDiv.textContent = line;
      } else if (line.startsWith("+")) {
        lineDiv.className += " add";
        lineDiv.textContent = line;
      } else if (line.startsWith("-")) {
        lineDiv.className += " del";
        lineDiv.textContent = line;
      } else {
        lineDiv.className += " ctx";
        lineDiv.textContent = line || " ";
      }
      diffViewer.appendChild(lineDiv);
    });
  }

  // --------------------------------------------------------------------------
  // JSON Plan Renderer
  // --------------------------------------------------------------------------
  function renderPlan(planObj) {
    if (!jsonViewer) return;
    const raw = JSON.stringify(planObj, null, 2);
    // Simple syntax colorizer for JSON
    const highlighted = raw
      .replace(/"(.*?)":/g, '<span class="json-key">"$1"</span>:')
      .replace(/: "(.*?)"/g, ': <span class="json-str">"$1"</span>')
      .replace(/: (true|false)/g, ': <span class="json-bool">$1</span>');
    jsonViewer.innerHTML = highlighted;
  }

  // --------------------------------------------------------------------------
  // Revert Countdown Simulator
  // --------------------------------------------------------------------------
  function startRevertCountdown() {
    clearInterval(revertInterval);
    revertCountdown = 30;
    if (revertBanner) revertBanner.style.display = "block";
    updateRevertUI();

    revertInterval = setInterval(() => {
      revertCountdown--;
      updateRevertUI();

      if (revertCountdown <= 0) {
        clearInterval(revertInterval);
        handleRevert("Auto-reverted: 30s countdown expired. Restored previous snapshot.");
      }
    }, 1000);
  }

  function updateRevertUI() {
    if (revertTimerText) {
      revertTimerText.textContent = `${revertCountdown}s`;
    }
    if (revertProgressBar) {
      const pct = (revertCountdown / 30) * 100;
      revertProgressBar.style.width = `${pct}%`;
    }
  }

  function handleKeep() {
    clearInterval(revertInterval);
    if (revertBanner) revertBanner.style.display = "none";
    if (statusText) {
      statusText.textContent = "Changes accepted. Overlay applied safely to ~/.config/hypr/buckwheat.conf";
    }
  }

  function handleRevert(reason = "Changes rolled back safely [Esc]. Working configs untouched.") {
    clearInterval(revertInterval);
    if (revertBanner) revertBanner.style.display = "none";
    if (statusText) {
      statusText.textContent = reason;
    }
  }

  if (btnKeepChanges) btnKeepChanges.addEventListener("click", handleKeep);
  if (btnRevertChanges) btnRevertChanges.addEventListener("click", () => handleRevert());

  // Keyboard shortcut listener for Enter and Escape during banner
  window.addEventListener("keydown", (e) => {
    if (revertBanner && revertBanner.style.display !== "none") {
      if (e.key === "Enter") {
        e.preventDefault();
        handleKeep();
      } else if (e.key === "Escape") {
        e.preventDefault();
        handleRevert();
      }
    }
  });

  // --------------------------------------------------------------------------
  // Batch Preparation Simulator (Voice: "Preparing the batch...")
  // --------------------------------------------------------------------------
  function prepareBatch(presetKey) {
    const data = PRESETS[presetKey] || PRESETS.autumn;
    if (statusBanner) statusBanner.style.display = "flex";
    if (statusText) statusText.textContent = "Preparing the batch...";

    setTimeout(() => {
      renderDiff(data.diff);
      renderPlan(data.plan);
      if (statusText) statusText.textContent = "Freshly prepared diff ready for inspection.";
      startRevertCountdown();
    }, 450);
  }

  // Preset pill click handlers
  presetPills.forEach(pill => {
    pill.addEventListener("click", () => {
      presetPills.forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
      const key = pill.dataset.preset;
      activePresetKey = key;
      if (promptInput && PRESETS[key]) {
        promptInput.value = PRESETS[key].prompt;
      }
      prepareBatch(key);
    });
  });

  if (promptForm) {
    promptForm.addEventListener("submit", (e) => {
      e.preventDefault();
      prepareBatch(activePresetKey);
    });
  }

  // --------------------------------------------------------------------------
  // Tab Switching
  // --------------------------------------------------------------------------
  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      tabBtns.forEach(b => b.classList.remove("active"));
      tabPanes.forEach(p => p.classList.remove("active"));

      btn.classList.add("active");
      const targetId = btn.dataset.tab;
      const targetPane = document.getElementById(targetId);
      if (targetPane) targetPane.classList.add("active");
    });
  });

  // --------------------------------------------------------------------------
  // Palette Showcase
  // --------------------------------------------------------------------------
  function renderPaletteSwatches(paletteKey) {
    const pal = PALETTES[paletteKey];
    if (!pal || !swatchesContainer) return;

    if (paletteTitle) paletteTitle.textContent = pal.name;
    if (paletteDesc) paletteDesc.textContent = pal.desc;

    swatchesContainer.innerHTML = "";
    pal.swatches.forEach(s => {
      const swatch = document.createElement("div");
      swatch.className = "swatch-item";
      swatch.title = `Click to copy ${s.hex}`;
      swatch.style.cursor = "pointer";

      swatch.innerHTML = `
        <div class="swatch-color-box" style="background-color: ${s.hex};"></div>
        <div class="swatch-labels">
          <span class="swatch-role">${s.role}</span>
          <span class="swatch-hex">${s.hex}</span>
        </div>
      `;

      swatch.addEventListener("click", () => {
        navigator.clipboard?.writeText(s.hex);
        const originalText = swatch.querySelector(".swatch-hex").textContent;
        swatch.querySelector(".swatch-hex").textContent = "Copied!";
        setTimeout(() => {
          swatch.querySelector(".swatch-hex").textContent = originalText;
        }, 1200);
      });

      swatchesContainer.appendChild(swatch);
    });
  }

  paletteBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      paletteBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      renderPaletteSwatches(btn.dataset.palette);
    });
  });

  // --------------------------------------------------------------------------
  // ASCII Toggle Mode
  // --------------------------------------------------------------------------
  if (btnToggleAscii) {
    btnToggleAscii.addEventListener("click", () => {
      document.body.classList.toggle("ascii-mode");
      const isAscii = document.body.classList.contains("ascii-mode");
      btnToggleAscii.textContent = isAscii ? "SVG View" : "ASCII View";
    });
  }

  // --------------------------------------------------------------------------
  // Copy Install Command
  // --------------------------------------------------------------------------
  if (copyInstallBtn) {
    copyInstallBtn.addEventListener("click", () => {
      const codeText = "pip install buckwheat";
      navigator.clipboard?.writeText(codeText);
      const originalText = copyInstallBtn.textContent;
      copyInstallBtn.textContent = "Copied!";
      setTimeout(() => {
        copyInstallBtn.textContent = originalText;
      }, 1500);
    });
  }

  // --------------------------------------------------------------------------
  // Mobile navigation menu
  // --------------------------------------------------------------------------
  const navToggle = document.getElementById("nav-toggle");
  const mainNav = document.getElementById("main-nav");
  const setNavOpen = (open) => {
    if (!navToggle || !mainNav) return;
    navToggle.setAttribute("aria-expanded", String(open));
    mainNav.classList.toggle("is-open", open);
  };
  if (navToggle && mainNav) {
    navToggle.addEventListener("click", () => {
      setNavOpen(navToggle.getAttribute("aria-expanded") !== "true");
    });
    mainNav.querySelectorAll("a").forEach((a) => a.addEventListener("click", () => setNavOpen(false)));
  }

  // --------------------------------------------------------------------------
  // Active nav link follows scroll position
  // --------------------------------------------------------------------------
  const navLinks = Array.from(document.querySelectorAll(".nav-link"));
  if ("IntersectionObserver" in window && navLinks.length) {
    const spy = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        navLinks.forEach((link) => {
          link.classList.toggle("is-active", link.getAttribute("href") === "#" + entry.target.id);
        });
      });
    }, { rootMargin: "-40% 0px -55% 0px" });
    navLinks.forEach((link) => {
      const target = document.querySelector(link.getAttribute("href"));
      if (target) spy.observe(target);
    });
  }

  // --------------------------------------------------------------------------
  // Hero terminal: type out each sample vibe in turn
  // --------------------------------------------------------------------------
  const typingText = document.getElementById("typing-text");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (typingText && !reducedMotion) {
    const vibes = Object.values(PRESETS).map((preset) => preset.prompt);
    let vibeIndex = 0;
    let charIndex = 0;
    let deleting = false;
    const tick = () => {
      const vibe = vibes[vibeIndex];
      charIndex += deleting ? -1 : 1;
      typingText.textContent = vibe.slice(0, charIndex);
      let delay = deleting ? 18 : 42;
      if (!deleting && charIndex === vibe.length) {
        deleting = true;
        delay = 2200;
      } else if (deleting && charIndex === 0) {
        deleting = false;
        vibeIndex = (vibeIndex + 1) % vibes.length;
        delay = 400;
      }
      setTimeout(tick, delay);
    };
    typingText.textContent = "";
    tick();
  }

  // --------------------------------------------------------------------------
  // Scroll reveal
  // --------------------------------------------------------------------------
  if ("IntersectionObserver" in window && !reducedMotion) {
    const revealTargets = document.querySelectorAll(
      ".section-header, .demo-card, .guardrail-card, .palette-selector-pills, .palette-detail-card, .target-badge-card"
    );
    const reveal = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        reveal.unobserve(entry.target);
      });
    }, { threshold: 0.12 });
    document.documentElement.classList.add("reveal-ready");
    revealTargets.forEach((el, i) => {
      el.classList.add("reveal");
      el.style.setProperty("--reveal-delay", (i % 4) * 70 + "ms");
      reveal.observe(el);
    });
  }

  // Initial render
  prepareBatch("autumn");
  renderPaletteSwatches("buckwheat");
});
