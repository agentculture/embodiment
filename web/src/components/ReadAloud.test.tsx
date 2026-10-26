import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Reader } from "./Reader";
import { progressOf } from "./ReadAloud";

// Synthetic data only: no real library item, text or id.
const PARAS = ["Alpha synthetic paragraph.", "Beta synthetic paragraph.", "Gamma synthetic paragraph.", "Delta synthetic paragraph."];
const doc = {
  item: { id: "syn-1", title: "A synthetic book", kind: "book", author: "", created_at: "2026-01-02" },
  passages: PARAS.map((text, i) => ({ anchor: `s0.p${i}`, heading: i === 0 ? "One" : "", text, start_s: null, end_s: null, speaker: "" })),
};
const unit = (paragraph: number) => ({
  item_id: "syn-1",
  sha256: "sha-syn",
  title: "A synthetic book",
  section: 0,
  section_count: 3,
  heading: "One",
  paragraph,
  paragraph_count: PARAS.length,
  part: 0,
  part_count: 1,
  text: PARAS[paragraph],
  position: `pos-0-${paragraph}`,
});

class FakeAudio {
  static made: FakeAudio[] = [];
  src = "";
  currentTime = 0;
  duration = NaN;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeupdate: (() => void) | null = null;
  play = vi.fn(async () => {});
  pause = vi.fn(() => {});
  constructor() {
    FakeAudio.made.push(this);
  }
  fire(name: "ended" | "error" | "timeupdate") {
    const h = this[`on${name}` as "onended"];
    if (h) act(() => h());
  }
}

interface Rig {
  fetchFn: ReturnType<typeof vi.fn>;
  heard: string[];
  spoken: string[];
  nexts: string[];
  cursor: { p: number };
  /** With `hold`, every /next after the first waits here until `release()`. */
  release: () => void;
}

function rig(opts: { gone?: boolean; done?: number; hold?: boolean } = {}): Rig {
  let gate: Promise<void> = Promise.resolve();
  let open = () => {};
  if (opts.hold) gate = new Promise<void>((done) => (open = done));
  const r: Rig = { heard: [], spoken: [], nexts: [], cursor: { p: 0 }, fetchFn: vi.fn(), release: () => open() };
  r.fetchFn = vi.fn(async (u: RequestInfo | URL, init?: RequestInit) => {
    const url = String(u);
    const json = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body }) as Response;
    if (url === "/api/library/syn-1") return json(200, doc);
    if (url.startsWith("/api/reading/syn-1/next")) {
      r.nexts.push(url);
      if (r.nexts.length > 1) await gate;
      if (opts.gone) return json(410, { error: { code: "library-item-gone" } });
      const m = /paragraph=(\d+)/.exec(url);
      const p = m ? Number(m[1]) : r.cursor.p;
      if (p >= (opts.done ?? PARAS.length)) return json(200, { done: true });
      return json(200, unit(p));
    }
    if (url === "/api/reading/syn-1/heard") {
      const pos = JSON.parse(String(init?.body)).position as string;
      r.heard.push(pos);
      r.cursor.p = Number(pos.split("-")[2]) + 1;
      return json(200, { moved: true, cursor: {} });
    }
    if (url === "/api/speak") {
      r.spoken.push(JSON.parse(String(init?.body)).text);
      return { ok: true, status: 200, blob: async () => new Blob(["RIFF"], { type: "audio/wav" }) } as unknown as Response;
    }
    return json(404, {});
  });
  return r;
}

const audio = () => FakeAudio.made[FakeAudio.made.length - 1];

async function openAndStart(r: Rig, props: Record<string, unknown> = {}) {
  const view = render(
    <Reader id="syn-1" onClose={() => {}} clientOptions={{ fetchFn: r.fetchFn as unknown as typeof fetch }} audioFactory={() => new FakeAudio() as unknown as HTMLAudioElement} {...props} />,
  );
  await screen.findByText(PARAS[0]);
  fireEvent.click(screen.getByRole("button", { name: /Read aloud on this/ }));
  await waitFor(() => expect(screen.getByRole("group", { name: "Reading aloud" })).toHaveAttribute("data-status", "playing"));
  return view;
}

beforeEach(() => {
  FakeAudio.made = [];
  Element.prototype.scrollIntoView = vi.fn();
  URL.createObjectURL = vi.fn(() => `blob:syn-${Math.random()}`);
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => vi.restoreAllMocks());

describe("ReadAloud: heard only on ended, prefetch while playing (t4 AC1)", () => {
  it("speaks each unit through /api/speak, reports heard on ended with that unit's position, and prefetches the next", async () => {
    const r = rig();
    await openAndStart(r);
    expect(r.nexts[0]).toBe("/api/reading/syn-1/next"); // continue at the cursor
    expect(r.spoken[0]).toBe(PARAS[0]);
    expect(audio().play).toHaveBeenCalled();
    // while paragraph 1 plays, paragraph 2's audio is made from the reader's copy
    await waitFor(() => expect(r.spoken).toContain(PARAS[1]));
    // ...without a seek: next?section=&paragraph= re-pins the cursor (d1)
    expect(r.nexts.some((u) => u.includes("?"))).toBe(false);
    expect(r.heard).toEqual([]);
    audio().fire("ended");
    await waitFor(() => expect(r.heard).toEqual(["pos-0-0"]));
    // continue at the cursor, playing the prefetched audio: paragraph 2 is synthesised once
    await waitFor(() => expect(screen.getByText(PARAS[1]).closest("li")).toHaveClass("speaking"));
    expect(r.nexts).toEqual(["/api/reading/syn-1/next", "/api/reading/syn-1/next"]);
    expect(r.spoken.filter((t) => t === PARAS[1])).toHaveLength(1);
    expect(screen.getByText(PARAS[0]).closest("li")).toHaveClass("heard");
  });

  it("past the end says so and reports nothing more", async () => {
    const r = rig({ done: 1 });
    await openAndStart(r);
    audio().fire("ended");
    await waitFor(() => expect(screen.getByText(/reached the end/)).toBeInTheDocument());
    expect(r.heard).toEqual(["pos-0-0"]);
  });
});

describe("ReadAloud: nothing short of ended reports heard (t4 AC2)", () => {
  it("pause, then a late ended: not heard; Continue plays the paragraph again from its start", async () => {
    const r = rig();
    await openAndStart(r);
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(audio().pause).toHaveBeenCalled();
    audio().fire("ended");
    expect(screen.getByText(/Paused at paragraph 1 of 4/)).toBeInTheDocument();
    expect(screen.getByText("Paused here")).toBeInTheDocument();
    const before = r.nexts.length;
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    // Continue asks for the saved cursor (no seek): it is still paragraph 1, not heard.
    await waitFor(() => expect(r.nexts.length).toBeGreaterThan(before));
    expect(r.nexts[r.nexts.length - 1]).toBe("/api/reading/syn-1/next");
    expect(r.nexts.some((u) => u.includes("section="))).toBe(false);
    expect(r.heard).toEqual([]);
  });

  it("an audio error: not heard, shown as a fixed code", async () => {
    const r = rig();
    await openAndStart(r);
    audio().fire("error");
    expect(screen.getByRole("alert")).toHaveTextContent("playback-failed");
    audio().fire("ended");
    expect(r.heard).toEqual([]);
  });

  it("Stop reading: not heard, and the reader marks where you stopped", async () => {
    const r = rig();
    await openAndStart(r);
    fireEvent.click(screen.getByRole("button", { name: "Stop reading" }));
    audio().fire("ended");
    expect(r.heard).toEqual([]);
    expect(screen.getByText("You stopped here")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Continue listening: section 1, paragraph 1/ })).toBeInTheDocument();
  });

  it("moving to another paragraph before ended: the old one is never reported", async () => {
    const r = rig();
    await openAndStart(r);
    fireEvent.click(screen.getByRole("button", { name: "Next paragraph" }));
    audio().fire("ended"); // a late ended from the paragraph left behind
    await waitFor(() => expect(screen.getByText(PARAS[1]).closest("li")).toHaveClass("speaking"));
    expect(r.heard).not.toContain("pos-0-0");
    expect(r.nexts).toContain("/api/reading/syn-1/next?section=0&paragraph=1");
    // a seek's unit is reported heard on ended like any other (d1)
    audio().fire("ended");
    await waitFor(() => expect(r.heard).toEqual(["pos-0-1"]));
  });

  it("a reload (the reader unmounts) mid-paragraph: not heard", async () => {
    const r = rig();
    const view = await openAndStart(r);
    const a = audio();
    const ended = a.onended;
    view.unmount();
    expect(a.pause).toHaveBeenCalled();
    if (ended) act(() => ended());
    expect(r.heard).toEqual([]);
  });

  it("a play() the browser refuses is not heard and asks for a tap", async () => {
    const r = rig();
    const blocked = Object.assign(new Error("no"), { name: "NotAllowedError" });
    render(
      <Reader
        id="syn-1"
        listen
        onClose={() => {}}
        clientOptions={{ fetchFn: r.fetchFn as unknown as typeof fetch }}
        audioFactory={() => {
          const a = new FakeAudio();
          a.play = vi.fn(async () => { throw blocked; });
          return a as unknown as HTMLAudioElement;
        }}
      />,
    );
    expect(await screen.findByText(/waits for a tap/)).toBeInTheDocument();
    expect(r.heard).toEqual([]);
  });
});

describe("ReadAloud: one clock for the dock, the edge line and the map (t4 AC3)", () => {
  it("one segment per paragraph; heard full, the playing one at currentTime/duration, the same value on the paragraph and the map", async () => {
    const r = rig();
    const { container } = await openAndStart(r);
    audio().fire("ended");
    await waitFor(() => expect(screen.getByText(PARAS[1]).closest("li")).toHaveClass("speaking"));
    const a = audio();
    a.duration = 4;
    a.currentTime = 3;
    a.fire("timeupdate");
    const segs = container.querySelectorAll(".where-bar span");
    expect(segs).toHaveLength(PARAS.length);
    expect(segs[0]).toHaveClass("heard");
    expect(segs[1]).toHaveClass("now");
    expect(segs[1]).toHaveAttribute("data-played", "0.75");
    expect((segs[1] as HTMLElement).style.getPropertyValue("--played")).toBe("0.75");
    expect(segs[2].className).toBe("");
    const li = screen.getByText(PARAS[1]).closest("li") as HTMLElement;
    expect(li).toHaveAttribute("data-played", "0.75");
    expect(li.style.getPropertyValue("--played")).toBe("0.75");
    const map = container.querySelectorAll(".reader-map span");
    expect(map).toHaveLength(3); // one per section
    expect(map[0]).toHaveClass("now");
    expect(map[0]).toHaveAttribute("data-share", String((1 + 0.75) / PARAS.length));
    expect(screen.getByText(/Playing on this (phone|laptop)/)).toBeInTheDocument();
  });

  it("progressOf folds a paragraph's parts into one fill", () => {
    const u = { ...unit(1), part: 1, part_count: 2 };
    const ru = { itemId: "x", sha256: "", title: "", section: 0, sectionCount: 1, heading: "", paragraph: u.paragraph, paragraphCount: 4, part: 1, partCount: 2, text: "", position: "p" };
    expect(progressOf(ru, 0.5)).toEqual({ paragraph: 0.75, section: (1 + 0.75) / 4 });
  });
});

describe("ReadAloud: gone, text direction, keyboard, motion (t4 AC4)", () => {
  it("a gone item says it is no longer in the library", async () => {
    const r = rig({ gone: true });
    render(<Reader id="syn-1" listen onClose={() => {}} clientOptions={{ fetchFn: r.fetchFn as unknown as typeof fetch }} audioFactory={() => new FakeAudio() as unknown as HTMLAudioElement} />);
    expect(await screen.findByText("This item is no longer in the library.")).toBeInTheDocument();
    expect(r.spoken).toEqual([]);
  });

  it("a reader that opens on a gone item says the same", async () => {
    const fetchFn = vi.fn(async () => ({ ok: false, status: 410, json: async () => ({ error: { code: "library-item-gone" } }) }) as Response);
    render(<Reader id="syn-1" onClose={() => {}} clientOptions={{ fetchFn }} />);
    expect(await screen.findByText("This item is no longer in the library.")).toBeInTheDocument();
  });

  it("dock text is dir=auto, every control is a focusable button", async () => {
    const r = rig();
    const { container } = await openAndStart(r);
    for (const el of container.querySelectorAll(".where-section, .where-pos, .device")) expect(el).toHaveAttribute("dir", "auto");
    for (const name of ["Previous paragraph", "Pause", "Next paragraph", "Stop reading"]) {
      const b = screen.getByRole("button", { name });
      expect(b.tagName).toBe("BUTTON");
      b.focus();
      expect(document.activeElement).toBe(b);
    }
  });

  it("scrolls the spoken paragraph without smooth motion when reduced motion is asked for", async () => {
    const before = window.matchMedia;
    window.matchMedia = vi.fn((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
    const scroll = vi.fn();
    const r = rig();
    render(<Reader id="syn-1" onClose={() => {}} clientOptions={{ fetchFn: r.fetchFn as unknown as typeof fetch }} audioFactory={() => new FakeAudio() as unknown as HTMLAudioElement} />);
    await screen.findByText(PARAS[0]);
    Element.prototype.scrollIntoView = scroll;
    fireEvent.click(screen.getByRole("button", { name: /Read aloud on this/ }));
    await waitFor(() => expect(scroll).toHaveBeenCalledWith({ block: "center", behavior: "auto" }));
    window.matchMedia = before;
  });

  it("the fill moves only when motion is allowed; colours are page tokens", () => {
    const css = readFileSync("src/styles/library.css", "utf8");
    const listen = css.slice(css.indexOf("/* ---------- read aloud")).replace(/\/\*[\s\S]*?\*\//g, "");
    expect(listen).toMatch(/\.seg\.speaking\s*\{[^}]*var\(--mark\)/);
    expect(listen).toMatch(/var\(--cobalt\)/);
    expect(listen).toMatch(/\.listen-dock\s*\{[^}]*position:\s*sticky/);
    // d3: on a phone the dock is fixed to the screen, so pause is reachable mid-article
    const phone = /@media \(max-width: 860px\) \{([\s\S]*?)\n\}/.exec(listen);
    expect(phone?.[1]).toMatch(/\.listen-dock\s*\{[^}]*position:\s*fixed/);
    expect(phone?.[1]).toMatch(/\.reader-body\s*\{[^}]*padding-block-end/);
    expect(listen).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    const motion = /@media \(prefers-reduced-motion: no-preference\) \{([\s\S]*?)\n\}/.exec(listen);
    expect(motion?.[1]).toMatch(/transition:\s*--played/);
    expect(listen.replace(motion?.[0] ?? "", "")).not.toMatch(/transition:\s*--played/);
  });
});

describe("ReadAloud: what the phone test asked for (d3, live t9 2026-10-26)", () => {
  it("tapping a paragraph starts reading there: a seek, so it is reported heard on ended", async () => {
    const r = rig();
    render(<Reader id="syn-1" onClose={() => {}} clientOptions={{ fetchFn: r.fetchFn as unknown as typeof fetch }} audioFactory={() => new FakeAudio() as unknown as HTMLAudioElement} />);
    await screen.findByText(PARAS[0]);
    fireEvent.click(screen.getByText(PARAS[2]));
    await waitFor(() => expect(screen.getByText(PARAS[2]).closest("li")).toHaveClass("speaking"));
    expect(r.nexts[0]).toBe("/api/reading/syn-1/next?section=0&paragraph=2");
    audio().fire("ended");
    await waitFor(() => expect(r.heard).toEqual(["pos-0-2"]));
  });

  it("tapping another paragraph while one plays jumps there and never reports the one left", async () => {
    const r = rig();
    await openAndStart(r);
    fireEvent.click(screen.getByText(PARAS[3]));
    await waitFor(() => expect(screen.getByText(PARAS[3]).closest("li")).toHaveClass("speaking"));
    expect(r.nexts).toContain("/api/reading/syn-1/next?section=0&paragraph=3");
    expect(r.heard).toEqual([]);
  });

  it("a paragraph is a keyboard target too: Enter starts reading there", async () => {
    const r = rig();
    render(<Reader id="syn-1" onClose={() => {}} clientOptions={{ fetchFn: r.fetchFn as unknown as typeof fetch }} audioFactory={() => new FakeAudio() as unknown as HTMLAudioElement} />);
    await screen.findByText(PARAS[0]);
    const li = screen.getByText(PARAS[1]).closest("li") as HTMLElement;
    expect(li).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(li, { key: "Enter" });
    await waitFor(() => expect(r.nexts[0]).toBe("/api/reading/syn-1/next?section=0&paragraph=1"));
  });

  it("a finished paragraph's bar stays full while the next one loads, never snapping back", async () => {
    const r = rig({ hold: true });
    const { container } = await openAndStart(r);
    const a = audio();
    a.duration = 4;
    a.currentTime = 4;
    a.fire("timeupdate");
    a.fire("ended");
    await waitFor(() => expect(r.heard).toEqual(["pos-0-0"]));
    await waitFor(() => expect(screen.getByRole("group", { name: "Reading aloud" })).toHaveAttribute("data-status", "loading"));
    const now = container.querySelector(".where-bar span.now") as HTMLElement;
    expect(now).toHaveAttribute("data-played", "1");
    const li = screen.getByText(PARAS[0]).closest("li") as HTMLElement;
    expect(li).toHaveAttribute("data-played", "1");
    act(() => r.release());
    await waitFor(() => expect(screen.getByText(PARAS[1]).closest("li")).toHaveClass("speaking"));
    expect(container.querySelector(".where-bar span.heard")).not.toBeNull();
  });

  it("past the end every paragraph reads as heard: the last is not 'paused', the bar and map are full", async () => {
    const r = rig({ done: 1 });
    const { container } = await openAndStart(r);
    audio().fire("ended");
    await waitFor(() => expect(screen.getByText(/reached the end/)).toBeInTheDocument());
    expect(screen.queryByText("Paused here")).toBeNull();
    expect(screen.getByText(PARAS[0]).closest("li")).toHaveClass("heard");
    const segs = container.querySelectorAll(".where-bar span");
    expect(segs).toHaveLength(PARAS.length);
    for (const seg of segs) expect(seg).toHaveClass("heard");
    for (const sec of container.querySelectorAll(".reader-map span")) expect(sec).toHaveClass("heard");
  });

  it("while the next paragraph loads the play button turns a ring and says it is loading (d4)", async () => {
    const r = rig({ hold: true });
    await openAndStart(r);
    const play = screen.getByRole("button", { name: "Pause" });
    expect(play).not.toHaveAttribute("aria-busy");
    fireEvent.click(screen.getByText(PARAS[2]));
    await waitFor(() => expect(screen.getByRole("group", { name: "Reading aloud" })).toHaveAttribute("data-status", "loading"));
    // a ring and nothing else: the label stays Pause, and no loading words are shown or announced
    const busy = screen.getByRole("button", { name: "Pause" });
    expect(busy).toHaveAttribute("aria-busy", "true");
    expect(busy.querySelector(".play-ring")).not.toBeNull();
    expect(screen.queryByText(/ready|Loading/i)).toBeNull();
    act(() => r.release());
    await waitFor(() => expect(screen.getByText(PARAS[2]).closest("li")).toHaveClass("speaking"));
    expect(screen.getByRole("button", { name: "Pause" })).not.toHaveAttribute("aria-busy");
  });

  it("the ring turns only when motion is allowed", () => {
    const css = readFileSync("src/styles/library.css", "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    expect(css).toMatch(/\.play-ring\s*\{/);
    const motion = [...css.matchAll(/@media \(prefers-reduced-motion: no-preference\) \{([\s\S]*?)\n\}/g)].map((m) => m[1]).join("\n");
    expect(motion).toMatch(/\.play-ring\s*\{[^}]*animation/);
    expect(css.replace(/@media \(prefers-reduced-motion: no-preference\) \{[\s\S]*?\n\}/g, "")).not.toMatch(/\.play-ring\s*\{[^}]*animation/);
  });

  it("pause then play reuses the paragraph's audio: it is made once (d6)", async () => {
    const r = rig();
    await openAndStart(r);
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(screen.getByRole("group", { name: "Reading aloud" })).toHaveAttribute("data-status", "playing"));
    expect(r.spoken.filter((t) => t === PARAS[0])).toHaveLength(1);
  });

  it("while a paragraph plays, the next and the previous are made ahead; Previous and Next reuse them (d6)", async () => {
    const r = rig();
    render(<Reader id="syn-1" onClose={() => {}} clientOptions={{ fetchFn: r.fetchFn as unknown as typeof fetch }} audioFactory={() => new FakeAudio() as unknown as HTMLAudioElement} />);
    await screen.findByText(PARAS[0]);
    fireEvent.click(screen.getByText(PARAS[1]));
    await waitFor(() => expect(screen.getByText(PARAS[1]).closest("li")).toHaveClass("speaking"));
    await waitFor(() => expect(r.spoken).toEqual(expect.arrayContaining([PARAS[2], PARAS[0]])));
    fireEvent.click(screen.getByRole("button", { name: "Previous paragraph" }));
    await waitFor(() => expect(screen.getByText(PARAS[0]).closest("li")).toHaveClass("speaking"));
    fireEvent.click(screen.getByRole("button", { name: "Next paragraph" }));
    await waitFor(() => expect(screen.getByText(PARAS[1]).closest("li")).toHaveClass("speaking"));
    for (const p of PARAS.slice(0, 3)) expect(r.spoken.filter((t) => t === p)).toHaveLength(1);
  });

  it("a failed synthesis is not kept: the next try asks again", async () => {
    const r = rig();
    let fail = true;
    const inner = r.fetchFn.getMockImplementation() as (u: RequestInfo | URL, i?: RequestInit) => Promise<Response>;
    r.fetchFn.mockImplementation(async (u: RequestInfo | URL, init?: RequestInit) => {
      if (String(u) === "/api/speak" && fail) {
        fail = false;
        return { ok: false, status: 503, json: async () => ({ error: { code: "tts-unavailable" } }) } as Response;
      }
      return inner(u, init);
    });
    render(<Reader id="syn-1" onClose={() => {}} clientOptions={{ fetchFn: r.fetchFn as unknown as typeof fetch }} audioFactory={() => new FakeAudio() as unknown as HTMLAudioElement} />);
    await screen.findByText(PARAS[0]);
    fireEvent.click(screen.getByRole("button", { name: /Read aloud on this/ }));
    await waitFor(() => expect(screen.getByRole("group", { name: "Reading aloud" })).toHaveAttribute("data-status", "error"));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(screen.getByRole("group", { name: "Reading aloud" })).toHaveAttribute("data-status", "playing"));
  });
});
