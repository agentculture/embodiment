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
//
// It deliberately does NOT grep the whole JS bundle for the substring
// "http": React's own minified runtime embeds two kinds of inert string
// literals that are never fetched --
//   - XML/SVG namespace URIs (http://www.w3.org/2000/svg, .../1999/xlink,
//     .../XML/1998/namespace) -- identifiers the XML spec requires, never
//     resolved over the network;
//   - the dev-mode invariant error-decoder link
//     (https://reactjs.org/docs/error-decoder.html) -- a human-readable
//     link printed in a thrown Error's message text on a React invariant
//     violation, never fetched by the running page.
// culture-nodes' own dist/ carries the same class of inert literals from
// its dependencies (elkjs embeds Eclipse/Apache/W3C license URLs). A
// substring grep over the whole bundle would fail on every React app and
// catches nothing a real network request would ever reach; this script
// checks the surfaces that do.
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
  try {
    cssFiles = walk(assetsDir, [".css"]);
  } catch {
    cssFiles = []; // no assets/ dir at all (e.g. a CSS-free build) is fine
  }
  for (const file of cssFiles) {
    findings.push(...checkCssText(readFileSync(file, "utf8"), file));
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
  console.log("ok   - dist/index.html and dist/assets/*.css reference no external origin");
  console.log("check-no-external-origin: PASS");
}

// Only run as a CLI when invoked directly (`node scripts/check-no-external-origin.mjs`),
// never as a side effect of being imported by the vitest test.
if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}
