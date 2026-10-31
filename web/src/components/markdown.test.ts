import { describe, expect, it } from "vitest";
import { parseBlocks, parseInline } from "./markdown";

describe("parseInline", () => {
  it("parses bold, italic, code and plain text", () => {
    expect(parseInline("a **b** *c* _d_ `e`")).toEqual([
      { k: "text", text: "a " },
      { k: "strong", c: [{ k: "text", text: "b" }] },
      { k: "text", text: " " },
      { k: "em", c: [{ k: "text", text: "c" }] },
      { k: "text", text: " " },
      { k: "em", c: [{ k: "text", text: "d" }] },
      { k: "text", text: " " },
      { k: "code", text: "e" },
    ]);
  });
  it("leaves unmatched markers and snake_case literal", () => {
    expect(parseInline("a * b and snake_case_name **open")).toEqual([
      { k: "text", text: "a * b and snake_case_name **open" },
    ]);
  });
  it("renders a link as plain text with its url", () => {
    expect(parseInline("see [docs](https://x.test/a)")).toEqual([
      { k: "text", text: "see docs (https://x.test/a)" },
    ]);
  });
  it("keeps HTML as literal text", () => {
    expect(parseInline("<img src=x onerror=alert(1)>")).toEqual([
      { k: "text", text: "<img src=x onerror=alert(1)>" },
    ]);
  });
});

describe("parseBlocks", () => {
  it("splits paragraphs on blank lines and keeps single newlines as breaks", () => {
    const b = parseBlocks("one\ntwo\n\nthree");
    expect(b).toHaveLength(2);
    expect(b[0]).toEqual({
      type: "p",
      inline: [{ k: "text", text: "one" }, { k: "br" }, { k: "text", text: "two" }],
    });
  });
  it("parses the '*   ' spaced unordered list", () => {
    const b = parseBlocks("*   **Jetson Optimization:** fast\n*   second\n- third\n+ fourth");
    expect(b).toHaveLength(1);
    const l = b[0];
    expect(l.type).toBe("list");
    if (l.type === "list") {
      expect(l.ordered).toBe(false);
      expect(l.items).toHaveLength(4);
      expect(l.items[0].inline[0]).toEqual({ k: "strong", c: [{ k: "text", text: "Jetson Optimization:" }] });
    }
  });
  it("parses ordered lists and one level of nesting", () => {
    const b = parseBlocks("1. a\n   - x\n   - y\n2. b");
    expect(b).toHaveLength(1);
    const l = b[0];
    if (l.type !== "list") throw new Error("list");
    expect(l.ordered).toBe(true);
    expect(l.items).toHaveLength(2);
    expect(l.items[0].children?.ordered).toBe(false);
    expect(l.items[0].children?.items).toHaveLength(2);
  });
  it("parses headings, capped at level 3", () => {
    expect(parseBlocks("# A\n#### B")).toEqual([
      { type: "h", level: 1, inline: [{ k: "text", text: "A" }] },
      { type: "h", level: 3, inline: [{ k: "text", text: "B" }] },
    ]);
  });
  it("parses fenced code verbatim, even unclosed", () => {
    expect(parseBlocks("```js\n**not bold**\n\n- no\n```")).toEqual([
      { type: "code", text: "**not bold**\n\n- no" },
    ]);
    expect(parseBlocks("```\nopen")).toEqual([{ type: "code", text: "open" }]);
  });
  it("does not treat **bold** at line start as a list", () => {
    expect(parseBlocks("**Hi** there")[0].type).toBe("p");
  });
});
