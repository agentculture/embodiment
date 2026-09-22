#!/usr/bin/env node
// scripts/check-no-external-origin.mjs
//
// Acceptance criterion #3 (verbatim, task t17): "the built web/dist
// references no external origin." Run AFTER `npm run build` (dist/ must
// exist) — checks the load-bearing surfaces a browser actually resolves at
// runtime, not every string literal that happens to contain "http":
//
//   (a) dist/index.html: no <link href="http(s)://...">, no
//       <script src="http(s)://...">, no <img src=...> etc pointing at a
//       remote origin.
//   (b) every dist/assets/*.css: no `@import url(http...)`, no
//       `url(http...)` inside e.g. @font-face src (fonts are bundled --
//       @fontsource-variable ships local woff2 files Vite hashes into
//       dist/assets/, never a Google Fonts / CDN URL).
//   (c) every dist/assets/*.js: no `http(s)://` literal AT ALL, except an
//       explicit, narrow allow-list (ALLOWED_JS_ORIGIN_PATTERNS below) for
//       the known false positives a JS bundle legitimately carries:
//         - XML/SVG/MathML/XHTML namespace URIs (http://www.w3.org/...) --
//           identifiers the XML spec requires, never resolved over the
//           network;
//         - React's dev-mode invariant error-decoder link
//           (https://reactjs.org/docs/error-decoder.html) -- a
//           human-readable link printed in a thrown Error's message text
//           on a React invariant violation, never fetched by the running
//           page.
//       Reviewer finding (t17 round 4, MINOR): the previous version of
//       this script scanned only index.html and *.css, so a real
//       `fetch("https://example.com")` (or any other external network
//       call) anywhere in application source would pass silently once
//       bundled into JS. culture-nodes' own dist/ carries the same class
//       of inert literals from its dependencies (elkjs embeds
//       Eclipse/Apache/W3C license URLs), which is why the allow-list is
//       narrow and explicit rather than "skip JS entirely".
//
// The functions below are exported so web/src/build-output.test.ts can
// exercise the detection logic directly against synthetic fixtures (fast,
// no build required) as well as against the real dist/ when present.
//
// Run standalone with: node scripts/check-no-external-origin.mjs

import { readFileSync, readdirSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import path from "node:path";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(SCRIPT_DIR, "..");
export const DIST_DIR = path.join(WEB_ROOT, "dist");

const EXTERNAL_RE = /^(https?:)?\/\//;

/** Narrow, explicit allow-list for the two known-inert classes of
 *  http(s):// literal a React JS bundle legitimately carries. Anything
 *  else found in a *.js file fails the check -- see the module docstring
 *  for why this exists (round 4's MINOR finding) and what each entry is. */
export const ALLOWED_JS_ORIGIN_PATTERNS = [
  /^https?:\/\/www\.w3\.org\//i,
  /^https?:\/\/reactjs\.org\/docs\/error-decoder\.html/i,
];

function isAllowedJsOrigin(url) {
  return ALLOWED_JS_ORIGIN_PATTERNS.some((pattern) => pattern.test(url));
}

export function walk(dir, exts) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) return walk(p, exts);
    return exts.includes(extname(p)) ? [p] : [];
  });
}

/** Check one index.html's text for an external href/src attribute. Returns
 *  a list of finding strings (empty if clean). Pure — takes text, not a
 *  path, so it is testable without touching the filesystem. */
export function checkHtmlText(html, label = "index.html") {
  const findings = [];
  const attrRe = /(href|src)\s*=\s*"([^"]*)"/g;
  let match;
  while ((match = attrRe.exec(html))) {
    const [, attr, value] = match;
    if (EXTERNAL_RE.test(value)) {
      findings.push(`${label}: ${attr}="${value}" is an external origin`);
    }
  }
  return findings;
}

/** Check one CSS file's text for an external url()/@import. Pure. */
export function checkCssText(css, label = "styles.css") {
  const findings = [];
  const urlRe = /url\(\s*['"]?([^'")]+)['"]?\s*\)/g;
  let match;
  while ((match = urlRe.exec(css))) {
    const value = match[1];
    if (EXTERNAL_RE.test(value)) {
      findings.push(`${label}: url(${value}) is an external origin`);
    }
  }
  const importRe = /@import\s+(?:url\()?['"]?([^'")\s;]+)/g;
  while ((match = importRe.exec(css))) {
    const value = match[1];
    if (EXTERNAL_RE.test(value)) {
      findings.push(`${label}: @import ${value} is an external origin`);
    }
  }
  return findings;
}

/** Check one JS file's text for a http(s):// literal not on the allow-list.
 *  Pure. Matches a run of non-whitespace, non-quote, non-paren/backtick
 *  characters starting at "http://" or "https://" -- generous enough to
 *  catch `fetch("https://example.com")`, a template-literal URL, or a
 *  bare string constant, while stopping at the closing quote/paren a real
 *  URL literal would be wrapped in. */
export function checkJsText(js, label = "bundle.js") {
  const findings = [];
  const urlRe = /https?:\/\/[^\s"'`)]+/g;
  let match;
  while ((match = urlRe.exec(js))) {
    const url = match[0];
    if (!isAllowedJsOrigin(url)) {
      findings.push(`${label}: external origin literal "${url}" is not on the allow-list`);
    }
  }
  return findings;
}

/** Run every check against the real, already-built dist/ directory. Throws
 *  if dist/ or dist/index.html is missing (the caller decides whether that
 *  is a skip or a failure — see build-output.test.ts). */
export function checkDist(distDir = DIST_DIR) {
  const indexPath = join(distDir, "index.html");
  const findings = [
    ...checkHtmlText(readFileSync(indexPath, "utf8"), indexPath),
  ];
  const assetsDir = join(distDir, "assets");
  let cssFiles = [];
  let jsFiles = [];
  try {
    cssFiles = walk(assetsDir, [".css"]);
    jsFiles = walk(assetsDir, [".js"]);
  } catch {
    cssFiles = []; // no assets/ dir at all (e.g. a CSS-free build) is fine
    jsFiles = [];
  }
  for (const file of cssFiles) {
    findings.push(...checkCssText(readFileSync(file, "utf8"), file));
  }
  for (const file of jsFiles) {
    findings.push(...checkJsText(readFileSync(file, "utf8"), file));
  }
  return findings;
}

function main() {
  const findings = checkDist();
  if (findings.length > 0) {
    console.error("FAIL - external origin(s) referenced by dist/:");
    for (const f of findings) console.error(`  ${f}`);
    process.exit(1);
  }
  console.log(
    "ok   - dist/index.html, dist/assets/*.css and dist/assets/*.js reference no external origin",
  );
  console.log("check-no-external-origin: PASS");
}

// Only run as a CLI when invoked directly (`node scripts/check-no-external-origin.mjs`),
// never as a side effect of being imported by the vitest test.
if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}
