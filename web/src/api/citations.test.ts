// Operator feedback 2026-10-24: "[2,3] was merged so the link was broken" and
// speech "reads the number of the link". Every citation shape becomes one
// marker per source, and speech text carries none of them.
import { describe, expect, it } from "vitest";
import { groupNumbers, splitCitations, stripCitations } from "./citations";
import { displayKind, itemDay, shortTitle } from "./kinds";

const cites = (text: string) =>
  splitCitations(text)
    .filter((p) => p.kind === "cite")
    .map((p) => (p.kind === "cite" ? p.n : 0));

describe("splitCitations", () => {
  it("keeps a single [n]", () => {
    expect(splitCitations("a [1] b")).toEqual([
      { kind: "text", text: "a " },
      { kind: "cite", n: 1 },
      { kind: "text", text: " b" },
    ]);
  });

  it("splits a merged group into one cite per source", () => {
    expect(cites("x [2, 3].")).toEqual([2, 3]);
    expect(cites("x [2,3,5]")).toEqual([2, 3, 5]);
    expect(cites("x [2][3]")).toEqual([2, 3]);
    expect(cites("x [ 4 ]")).toEqual([4]);
  });

  it("expands a range, bounded", () => {
    expect(cites("x [2-4]")).toEqual([2, 3, 4]);
    expect(cites("x [2–3]")).toEqual([2, 3]);
    expect(groupNumbers("[1-9999]")).toEqual([1, 9999]);
  });

  it("accepts the Arabic comma Hebrew keyboards can produce", () => {
    expect(cites("x [1، 2]")).toEqual([1, 2]);
  });

  it("leaves brackets that are not citations as text", () => {
    expect(splitCitations("[a] [1x] [] array[0")).toEqual([{ kind: "text", text: "[a] [1x] [] array[0" }]);
  });
});

describe("stripCitations", () => {
  it("removes every marker and the space before punctuation", () => {
    expect(stripCitations("במחסן יש קומקום [1], וקופסה [2, 3].")).toBe("במחסן יש קומקום, וקופסה.");
    expect(stripCitations("one [1][2] two [3-4]")).toBe("one two");
  });

  it("leaves text without markers alone", () => {
    expect(stripCitations("plain text.")).toBe("plain text.");
  });
});


describe("xteink titles carry the kind and the day (live library, 2026-10-24)", () => {
  it("refines kind article from the title, and keeps any other wire kind", () => {
    expect(displayKind("article", "Daily newspaper 2026-10-23")).toBe("newspaper");
    expect(displayKind("article", "2026-10-15 warehouse")).toBe("transcript");
    expect(displayKind("article", "ai-souls-article.md")).toBe("document");
    expect(displayKind("book", "2026-10-15 warehouse")).toBe("book");
  });

  it("dates an item by its title, else by when it was added", () => {
    expect(itemDay("2026-10-24T05:00:00Z", "2026-10-15 warehouse")).toBe("2026-10-15");
    expect(itemDay("2026-10-24T05:00:00Z", "Daily newspaper 2026-10-11 (live test)")).toBe("2026-10-11");
    expect(itemDay("2026-10-06T20:36:53+00:00", "The Port of Least Resistance")).toBe("2026-10-06");
  });

  it("drops the date the day heading already shows", () => {
    expect(shortTitle("2026-10-15 warehouse")).toBe("warehouse");
    expect(shortTitle("Daily newspaper 2026-10-23")).toBe("Daily newspaper");
    expect(shortTitle("The Port of Least Resistance")).toBe("The Port of Least Resistance");
  });
});
