import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const websiteDir = path.resolve(__dirname, "..");

test("1. website/index.html structure and metadata", () => {
  const htmlPath = path.join(websiteDir, "index.html");
  assert.ok(fs.existsSync(htmlPath), "index.html must exist");
  const html = fs.readFileSync(htmlPath, "utf-8");

  // DOCTYPE and HTML5 tags
  assert.match(html, /<!DOCTYPE html>/i, "Must have standard HTML5 DOCTYPE");
  assert.match(html, /<html\s+lang="en">/, "Must have lang='en'");
  assert.match(html, /<meta\s+charset="UTF-8"/i, "Must specify UTF-8 charset");
  assert.match(html, /<meta\s+name="viewport"/i, "Must specify responsive viewport");

  // Semantic landmark elements
  assert.match(html, /<header\b/, "Must have <header>");
  assert.match(html, /<nav\b/, "Must have <nav>");
  assert.match(html, /<main\b/, "Must have <main>");
  assert.match(html, /<section\b/, "Must have <section>");
  assert.match(html, /<footer\b/, "Must have <footer>");

  // Accessibility: skip link and labels
  assert.match(html, /skip-to-content/, "Must have skip to content link");
  assert.match(html, /role="banner"/, "Header must have banner role");
  assert.match(html, /role="main"|id="main-content"/, "Main content area must exist");
});

test("2. website/index.html brand identity and voice copy", () => {
  const html = fs.readFileSync(path.join(websiteDir, "index.html"), "utf-8");

  // Taglines
  assert.match(html, /A wholesome grain for your Linux desktop/i, "Tagline missing");
  assert.match(html, /Plain-English ricing for Hyprland and friends/i, "Subtitle missing");

  // Voice phrases from BRANDING.md
  assert.match(html, /What vibe are we cooking up today\?/i, "Prompt placeholder missing");
  assert.match(html, /Freshly prepared diff/i, "Diff header missing");
  assert.match(html, /Testing new look \(auto-reverting in/i, "Safe revert banner copy missing");
  assert.match(html, /Keep changes \[Enter\]/i, "Keep changes CTA missing");
  assert.match(html, /Revert to previous \[Esc\]/i, "Revert changes CTA missing");

  // The 4 Honest Guardrails
  assert.match(html, /Managed Overlays, Not Overwrites/i, "Guardrail 1 missing");
  assert.match(html, /Inspect Before You Apply/i, "Guardrail 2 missing");
  assert.match(html, /Timed Revert Safety Net/i, "Guardrail 3 missing");
  assert.match(html, /Structured Changes/i, "Guardrail 4 missing");

  // Sprig ASCII Art
  assert.match(html, /\\v\/ BUCKWHEAT|\\v\//, "Compact sprig missing");
});

test("3. website/style.css color tokens match BRANDING.md exactly", () => {
  const cssPath = path.join(websiteDir, "style.css");
  assert.ok(fs.existsSync(cssPath), "style.css must exist");
  const css = fs.readFileSync(cssPath, "utf-8");

  const brandTokens = {
    "--groat-accent": "#E0A96D",
    "--parchment": "#EBDCB9",
    "--husk-dim": "#8C5835",
    "--sage-green": "#8F9E8B",
    "--ember-red": "#C86452",
    "--soil-bg": "#201E1C",
    "--flour-fg": "#F7F4EE"
  };

  for (const [token, hex] of Object.entries(brandTokens)) {
    const regex = new RegExp(`${token}:\\s*${hex}`, "i");
    assert.match(css, regex, `CSS must define brand token ${token} as ${hex}`);
  }
});

test("4. website/app.js interactive functionality and safety timers", () => {
  const jsPath = path.join(websiteDir, "app.js");
  assert.ok(fs.existsSync(jsPath), "app.js must exist");
  const js = fs.readFileSync(jsPath, "utf-8");

  assert.match(js, /PRESETS/, "Must define presets");
  assert.match(js, /autumn/, "Must include autumn harvest preset");
  assert.match(js, /arctic/, "Must include arctic frost preset");
  assert.match(js, /revertCountdown = 30/, "Must implement 30-second revert countdown");
  assert.match(js, /"Escape"/, "Must handle Escape key to rollback");
  assert.match(js, /"Enter"/, "Must handle Enter key to accept");
  assert.match(js, /PALETTES/, "Must define palette showcase library");
});

test("5. SVG Emblem and Favicon asset integrity", () => {
  const sprigPath = path.join(websiteDir, "assets", "sprig.svg");
  const faviconPath = path.join(websiteDir, "assets", "favicon.svg");

  assert.ok(fs.existsSync(sprigPath), "sprig.svg must exist");
  assert.ok(fs.existsSync(faviconPath), "favicon.svg must exist");

  const sprig = fs.readFileSync(sprigPath, "utf-8");
  assert.match(sprig, /<svg\b/, "sprig.svg must be valid SVG");
  assert.match(sprig, /#E0A96D/i, "sprig.svg must use groat accent gold");
  assert.match(sprig, /#8F9E8B/i, "sprig.svg must use sprout sage green");

  const favicon = fs.readFileSync(faviconPath, "utf-8");
  assert.match(favicon, /<svg\b/, "favicon.svg must be valid SVG");
  assert.match(favicon, /#201E1C/i, "favicon must use roasted soil background");
});

test("6. Vercel deployment configuration", () => {
  const vercelPath = path.join(websiteDir, "vercel.json");
  const rootVercelPath = path.resolve(websiteDir, "..", "vercel.json");

  assert.ok(fs.existsSync(vercelPath), "website/vercel.json must exist");
  assert.ok(fs.existsSync(rootVercelPath), "root vercel.json must exist");

  const cfg = JSON.parse(fs.readFileSync(vercelPath, "utf-8"));
  assert.equal(cfg.cleanUrls, true, "cleanUrls should be enabled");
  assert.ok(Array.isArray(cfg.headers), "Security headers should be present");

  const rootCfg = JSON.parse(fs.readFileSync(rootVercelPath, "utf-8"));
  assert.equal(rootCfg.outputDirectory, "website", "Root vercel.json must point to website directory");
});

test("7. Local HTTP Server serves all assets with 200 OK", async () => {
  const mimeTypes = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".svg": "image/svg+xml",
    ".json": "application/json"
  };

  const server = http.createServer((req, res) => {
    let filePath = path.join(websiteDir, req.url === "/" ? "index.html" : req.url);
    if (!fs.existsSync(filePath)) {
      res.writeHead(404);
      res.end("Not Found");
      return;
    }
    const ext = path.extname(filePath);
    const contentType = mimeTypes[ext] || "text/plain";
    res.writeHead(200, { "Content-Type": contentType });
    res.end(fs.readFileSync(filePath));
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;

  const get = (urlPath) => {
    return new Promise((resolve, reject) => {
      http.get(`http://127.0.0.1:${port}${urlPath}`, (res) => {
        let body = "";
        res.on("data", chunk => body += chunk);
        res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body }));
      }).on("error", reject);
    });
  };

  try {
    const resHome = await get("/");
    assert.equal(resHome.status, 200);
    assert.match(resHome.headers["content-type"], /text\/html/);
    assert.match(resHome.body, /Buckwheat/);

    const resCss = await get("/style.css");
    assert.equal(resCss.status, 200);
    assert.match(resCss.headers["content-type"], /text\/css/);

    const resJs = await get("/app.js");
    assert.equal(resJs.status, 200);
    assert.match(resJs.headers["content-type"], /application\/javascript/);

    const resSvg = await get("/assets/sprig.svg");
    assert.equal(resSvg.status, 200);
    assert.match(resSvg.headers["content-type"], /image\/svg\+xml/);
  } finally {
    server.close();
  }
});

test("8. self-hosted fonts are shipped and referenced", () => {
  const css = fs.readFileSync(path.join(websiteDir, "style.css"), "utf-8");
  const html = fs.readFileSync(path.join(websiteDir, "index.html"), "utf-8");
  for (const file of ["inter-latin-wght-normal.woff2", "jetbrains-mono-latin-wght-normal.woff2"]) {
    assert.ok(fs.existsSync(path.join(websiteDir, "assets", "fonts", file)), `${file} must exist`);
    assert.match(css, new RegExp(file), `${file} must be referenced by @font-face`);
    assert.match(html, new RegExp(`rel="preload"[^>]*${file}`), `${file} must be preloaded`);
  }
  assert.doesNotMatch(css, /fonts\.googleapis|fonts\.gstatic/, "No third-party font hosts");
});

test("9. navigation, anchors and icons", () => {
  const html = fs.readFileSync(path.join(websiteDir, "index.html"), "utf-8");
  const js = fs.readFileSync(path.join(websiteDir, "app.js"), "utf-8");

  // Mobile menu
  assert.match(html, /id="nav-toggle"[^>]*aria-expanded="false"/, "Mobile nav toggle must exist");
  assert.match(js, /nav-toggle/, "app.js must wire the nav toggle");

  // Every in-page anchor must have a target
  const ids = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
  for (const [, href] of html.matchAll(/href="#([^"]+)"/g)) {
    assert.ok(ids.has(href), `Anchor #${href} has no target`);
  }

  // Target cards use SVG icons, not emoji
  const targetIcons = [...html.matchAll(/<div class="target-icon">([\s\S]*?)<\/svg><\/div>/g)];
  assert.equal(targetIcons.length, 5, "Five target cards must use SVG icons");

  // Featured guardrail card for the bento layout
  assert.match(html, /guardrail-card--featured/, "A featured guardrail card must exist");
});

test("mobile: small-screen rules cover header install button, hero badge and terminal", () => {
  const css = fs.readFileSync(path.join(websiteDir, "style.css"), "utf-8");
  const start = css.lastIndexOf("@media (max-width: 480px)");
  assert.ok(start !== -1, "Must have a max-width: 480px media query");
  const block = css.slice(start);
  assert.match(block, /\.btn-header-install\s*\{\s*display:\s*none/, "Header install button must be hidden on phones");
  assert.match(block, /\.hero-badge\b/, "Hero badge must have phone rules");
  assert.match(block, /\.terminal-title\b[^}]*white-space:\s*nowrap/, "Terminal title must stay on one line");
  assert.match(block, /\.ascii-sprig-row\b[^}]*flex-direction:\s*column/, "Sprig text must stack under the sprig");
});
