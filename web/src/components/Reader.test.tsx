import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { createRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { Reader, type ReaderHandle } from "./Reader";

const timed = {
  item: { id: "a", title: "Standup", kind: "recording", author: "Dana", created_at: "2026-01-02" },
  passages: [
    { anchor: "p1", heading: "Intro", text: "first", start_s: 0, end_s: 10, speaker: "A" },
    { anchor: "p2", heading: "", text: "<i>second</i>", start_s: 10, end_s: 40, speaker: "B" },
    { anchor: "p3", heading: "", text: "third", start_s: 40, end_s: 100, speaker: "A" },
  ],
};
const untimed = {
  ...timed,
  passages: timed.passages.map((p) => ({ ...p, start_s: null, end_s: null })),
};
const fetchWith = (body: unknown) =>
  vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) =>
    ({ ok: true, status: 200, json: async () => body }) as Response);

describe("Reader", () => {
  it("shows title, meta and passages as dir=auto text nodes", async () => {
    const { container } = render(<Reader id="a" onClose={() => {}} clientOptions={{ fetchFn: fetchWith(timed) }} />);
    expect(await screen.findByRole("heading", { name: "Standup" })).toHaveAttribute("dir", "auto");
    expect(screen.getByText(/Dana/)).toHaveAttribute("dir", "auto");
    expect(screen.getByText("<i>second</i>")).toHaveAttribute("dir", "auto");
    expect(container.querySelector("i")).toBeNull();
  });

  it("anchor prop and openAt() highlight and scroll to that passage", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    const ref = createRef<ReaderHandle>();
    render(<Reader ref={ref} id="a" anchor="p2" onClose={() => {}} clientOptions={{ fetchFn: fetchWith(timed) }} />);
    const second = (await screen.findByText("<i>second</i>")).closest("li")!;
    expect(second).toHaveClass("cited");
    expect(scroll).toHaveBeenCalled();
    act(() => ref.current!.openAt("p3"));
    expect(screen.getByText("third").closest("li")).toHaveClass("cited");
    expect(second).not.toHaveClass("cited");
  });

  it("draws a tape when passages are timed, and clicking seeks to the passage there", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const { container } = render(<Reader id="a" onClose={() => {}} clientOptions={{ fetchFn: fetchWith(timed) }} />);
    await screen.findByText("first");
    const tape = container.querySelector(".tape") as HTMLElement;
    expect(tape).not.toBeNull();
    expect(tape.querySelectorAll(".turnmark")).toHaveLength(3);
    tape.getBoundingClientRect = () => ({ left: 0, width: 200, top: 0, right: 200, bottom: 10, height: 10, x: 0, y: 0, toJSON() {} });
    fireEvent.click(tape, { clientX: 70 }); // 35% of 100s = 35s -> p2 (10-40)
    expect(screen.getByText("<i>second</i>").closest("li")).toHaveClass("cited");
  });

  it("draws no tape for untimed passages and still renders and highlights", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const { container } = render(<Reader id="a" anchor="p1" onClose={() => {}} clientOptions={{ fetchFn: fetchWith(untimed) }} />);
    expect(await screen.findByText("first")).toBeInTheDocument();
    expect(container.querySelector(".tape")).toBeNull();
    expect(screen.getByText("first").closest("li")).toHaveClass("cited");
  });

  it("closes on Escape and via the close button; failure shows a code", async () => {
    const onClose = vi.fn();
    render(<Reader id="a" onClose={onClose} clientOptions={{ fetchFn: fetchWith(untimed) }} />);
    await screen.findByText("first");
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("reports a fetch failure by fixed code", async () => {
    const fetchFn = vi.fn(async () => { throw new Error("x"); });
    render(<Reader id="a" onClose={() => {}} clientOptions={{ fetchFn }} />);
    expect(await screen.findByText(/network-error/)).toBeInTheDocument();
  });

  it("layout: a pane beside the thread (a drawer at 860px and below) or the library page; logical properties; provenance cited", () => {
    const css = readFileSync("src/styles/library.css", "utf8");
    expect(css).toMatch(/\.reader--page\s*\{/);
    expect(css).toMatch(/@media \(max-width: 860px\)/);
    expect(css).not.toMatch(/\b(margin|padding)-(left|right)\b|(^|[\s{;])(left|right):/m);
    const src = readFileSync("src/components/Reader.tsx", "utf8").slice(0, 1200);
    expect(src).toContain("pocketsync/web/static/app.js:461-626");
    expect(src).toContain("index.html:110-145");
  });
});

describe("Reader recovers from a failed open (live 2026-10-24, mid re-index)", () => {
  it("says the item is not ready yet, and Try again fetches it once it is", async () => {
    let ready = false;
    const fetchFn = vi.fn(async () =>
      ready
        ? ({ ok: true, status: 200, json: async () => ({ item: { id: "54", title: "Daily newspaper", kind: "article", created_at: "" }, passages: [{ anchor: "p1", heading: "", text: "the news", start_s: null, end_s: null, speaker: "" }] }) } as Response)
        : ({ ok: false, status: 404, json: async () => ({ error: { code: "library-item-not-found" } }) } as Response),
    );
    render(<Reader id="54" onClose={() => {}} clientOptions={{ fetchFn }} />);
    expect(await screen.findByText(/still indexing it/)).toBeInTheDocument();
    ready = true;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("the news")).toBeInTheDocument();
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });
});
