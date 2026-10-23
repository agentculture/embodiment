// styles.test.ts -- t12 AC3: the ported stylesheet (pocketchat tokens from
// pocketsync/web/static/app.css:4-55, dark mode, the 860px breakpoint,
// reduced motion) uses only logical properties, so the layout mirrors for
// Hebrew. Scans every .css file under web/src/styles.
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const STYLES_DIR = dirname(fileURLToPath(import.meta.url));
const CSS_FILES = readdirSync(STYLES_DIR).filter((f) => f.endsWith(".css"));
const read = (name: string): string => readFileSync(join(STYLES_DIR, name), "utf8");
const stripComments = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, "");

/** Physical (non-mirroring) declarations the port must not use. */
const PHYSICAL: [string, RegExp][] = [
  ["margin-left/right", /\bmargin-(left|right)\s*:/],
  ["padding-left/right", /\bpadding-(left|right)\s*:/],
  ["border-left/right", /\bborder-(left|right)(-[a-z]+)?\s*:/],
  ["left:/right:", /(^|[\s;{])(left|right)\s*:/],
  ["text-align: left/right", /\btext-align\s*:\s*(left|right)\b/],
  ["float: left/right", /\bfloat\s*:\s*(left|right)\b/],
];

function physicalFindings(css: string): string[] {
  const out: string[] = [];
  stripComments(css)
    .split("\n")
    .forEach((line, i) => {
      for (const [name, re] of PHYSICAL) if (re.test(line)) out.push(`${i + 1}: ${name}: ${line.trim()}`);
    });
  return out;
}

/** The body of the first `@media (...)` block whose prelude matches `prelude`. */
function mediaBlock(css: string, prelude: RegExp): string | null {
  const src = stripComments(css);
  const re = /@media\s*([^{]+)\{/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    if (!prelude.test(m[1])) continue;
    let depth = 1;
    let j = re.lastIndex;
    while (j < src.length && depth > 0) {
      if (src[j] === "{") depth += 1;
      else if (src[j] === "}") depth -= 1;
      j += 1;
    }
    return src.slice(re.lastIndex, j - 1);
  }
  return null;
}

describe("the logical-properties scanner itself", () => {
  it("flags each physical form and passes the logical ones", () => {
    const bad = [
      ".a { margin-left: 1px; }",
      ".a { padding-right: 1px; }",
      ".a { border-left: 1px solid; }",
      ".a { border-right-color: red; }",
      ".a { left: 0; }",
      ".a { right: 0; }",
      ".a { text-align: left; }",
      ".a { text-align: right; }",
    ];
    for (const css of bad) expect(physicalFindings(css), css).toHaveLength(1);
    const good =
      ".a { margin-inline-start: 1px; padding-inline-end: 1px; border-inline-start: 1px solid;" +
      " inset-inline-start: 0; text-align: start; transform-origin: left center; }";
    expect(physicalFindings(good)).toEqual([]);
  });
});

describe("web/src/styles uses only logical properties (t12 AC3)", () => {
  it("has the stylesheets to scan", () => {
    expect(CSS_FILES).toEqual(expect.arrayContaining(["app.css", "library.css"]));
  });

  for (const file of CSS_FILES) {
    it(`${file} has no physical left/right declarations`, () => {
      expect(physicalFindings(read(file))).toEqual([]);
    });
  }
});

describe("app.css is the ported pocketchat stylesheet", () => {
  const css = read("app.css");

  // pocketsync/web/static/app.css:4-38 (light) -- every token, by name.
  const TOKENS = [
    "--paper", "--sheet", "--ink", "--quiet", "--rule", "--rule-soft", "--cobalt",
    "--cobalt-soft", "--mark", "--mark-ink", "--tape", "--warn", "--warn-soft", "--ok",
    "--read", "--ui", "--step--1", "--step-0", "--step-1", "--step-2", "--step-3",
    "--radius-s", "--radius-m", "--gap", "--measure", "--bar-h", "--spring", "--glide",
  ];
  // app.css:40-57 -- the colour tokens dark mode redefines.
  const DARK = [
    "--paper", "--sheet", "--ink", "--quiet", "--rule", "--rule-soft", "--cobalt",
    "--cobalt-soft", "--mark", "--mark-ink", "--tape", "--warn", "--warn-soft", "--ok",
  ];

  it("defines every pocketchat token", () => {
    for (const t of TOKENS) expect(css, t).toMatch(new RegExp(`${t}\\s*:`));
  });

  it("redefines the colour tokens under prefers-color-scheme: dark", () => {
    const dark = mediaBlock(css, /prefers-color-scheme:\s*dark/);
    expect(dark).not.toBeNull();
    for (const t of DARK) expect(dark as string, t).toMatch(new RegExp(`${t}\\s*:`));
  });

  it("carries the 860px breakpoint", () => {
    expect(mediaBlock(css, /max-width:\s*860px/)).not.toBeNull();
  });

  it("has no hardcoded hex colour outside a token declaration", () => {
    const offenders = stripComments(css)
      .split("\n")
      .filter((l) => !/^\s*--[a-z0-9-]+\s*:/.test(l) && /#[0-9a-fA-F]{3,8}\b/.test(l));
    expect(offenders).toEqual([]);
  });

  it("animates the word reveal only when motion is allowed, and stills everything when reduced", () => {
    const outsideMotion = stripComments(css).replace(
      /@media\s*\(prefers-reduced-motion:\s*no-preference\)\s*\{[\s\S]*?\n\}/g,
      "",
    );
    const motion = mediaBlock(css, /prefers-reduced-motion:\s*no-preference/);
    expect(motion).not.toBeNull();
    expect(motion as string).toMatch(/\.chat__word\s*\{[^}]*animation\s*:/);
    expect(motion as string).toMatch(/@keyframes\s+word\b/);
    // no animation on .chat__word anywhere else
    expect(outsideMotion).not.toMatch(/\.chat__word[^{]*\{[^}]*animation\s*:(?!\s*none)/);
    const reduce = mediaBlock(css, /prefers-reduced-motion:\s*reduce/);
    expect(reduce).not.toBeNull();
    expect(reduce as string).toMatch(/animation\s*:\s*none/);
    expect(reduce as string).toMatch(/transition\s*:\s*none/);
  });

  it("styles the Chat classes t7 left unstyled", () => {
    for (const cls of [".chat__cite", ".chat__source-open", ".chat__source-n", ".chat__word"]) {
      expect(css, cls).toMatch(new RegExp(`\\${cls}[\\s,:{.\\[]`));
    }
  });

  it("keeps the waveform a compact strip: its canvas is at most 64px tall", () => {
    const m = /\.strip\s+\.waveform\s*\{([^}]*)\}/.exec(stripComments(css));
    expect(m).not.toBeNull();
    const size = /block-size\s*:\s*(\d+)px/.exec((m as RegExpExecArray)[1]);
    expect(size).not.toBeNull();
    expect(Number((size as RegExpExecArray)[1])).toBeLessThanOrEqual(64);
  });
});
