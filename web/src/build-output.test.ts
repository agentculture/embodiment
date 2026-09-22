import { existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  DIST_DIR,
  checkCssText,
  checkDist,
  checkHtmlText,
} from "../scripts/check-no-external-origin.mjs";

describe("check-no-external-origin.mjs — detection logic", () => {
  it("passes clean, self-hosted-only index.html", () => {
    const html = `<!doctype html><html><head>
      <link rel="icon" href="/favicon.svg">
      <script type="module" src="/assets/index-abc123.js"></script>
    </head><body><div id="root"></div></body></html>`;
    expect(checkHtmlText(html)).toEqual([]);
  });

  it("flags an external <script src>", () => {
    const html = `<script src="https://cdn.example.com/lib.js"></script>`;
    const findings = checkHtmlText(html, "index.html");
    expect(findings).toHaveLength(1);
    expect(findings[0]).toContain("https://cdn.example.com/lib.js");
  });

  it("flags a protocol-relative external <link href>", () => {
    const html = `<link rel="stylesheet" href="//fonts.googleapis.com/css">`;
    expect(checkHtmlText(html)).toHaveLength(1);
  });

  it("passes clean CSS with only local url()s", () => {
    const css = `@font-face { src: url(/assets/font-abc123.woff2) format("woff2"); }`;
    expect(checkCssText(css)).toEqual([]);
  });

  it("flags an external @font-face url()", () => {
    const css = `@font-face { src: url(https://fonts.gstatic.com/font.woff2); }`;
    const findings = checkCssText(css, "styles.css");
    expect(findings).toHaveLength(1);
    expect(findings[0]).toContain("fonts.gstatic.com");
  });

  it("flags an external @import", () => {
    const css = `@import url("https://fonts.googleapis.com/css2?family=Inter");`;
    const findings = checkCssText(css);
    expect(findings.length).toBeGreaterThanOrEqual(1);
    expect(findings.some((f) => f.includes("fonts.googleapis.com"))).toBe(true);
  });

  it("does not false-positive on a relative url() with no scheme", () => {
    const css = `.icon { background: url(icons.svg#gear); }`;
    expect(checkCssText(css)).toEqual([]);
  });
});

describe("the actual built dist/ (acceptance criterion #3)", () => {
  const distBuilt = existsSync(join(DIST_DIR, "index.html"));

  it.skipIf(!distBuilt)(
    "references no external origin in index.html or any bundled CSS",
    () => {
      const findings = checkDist();
      expect(findings).toEqual([]);
    },
  );

  if (!distBuilt) {
    it("is skipped: run `npm run build` first to exercise this against the real bundle", () => {
      expect(distBuilt).toBe(false);
    });
  }
});
