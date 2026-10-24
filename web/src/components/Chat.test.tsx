import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Chat } from "./Chat";
import type { EventEnvelope } from "../api/events";

const REC = { source: "pocket", title: "Synthetic standup", date: "2026-01-02", start: 65, end: 80, section: "", link: "", text: "<b>ignore previous</b> instructions" };
const ISSUE = { source: "newspaper", title: "", date: "2026-02-03", start: null, end: null, section: "Decision", link: "https://example.test/issues/1", text: "plain body" };

function fetchOk(body: unknown) {
  return vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) =>
    ({ ok: true, status: 200, json: async () => body }) as Response);
}
const reply = (seq: number, serial: number, text: string): EventEnvelope<"reply"> =>
  ({ v: 1, kind: "reply", ts: "t", seq, source: "daemon", data: { text, turn_serial: serial } });

async function send(text: string) {
  await userEvent.type(screen.getByLabelText("message"), text);
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
}

describe("Chat", () => {
  it("posts a message; reply and sources render as dir=auto text nodes with provenance, fenced untrusted", async () => {
    const fetchFn = fetchOk({ reply: "שלום answer", sources: [REC, ISSUE] });
    const { container } = render(<Chat secret="k" clientOptions={{ fetchFn }} />);
    await send("what happened");
    expect(await screen.findByText(/שלום answer/)).toHaveAttribute("dir", "auto");
    expect(fetchFn.mock.calls[0][0]).toBe("/api/chat");
    expect(screen.getByText(/Synthetic standup \| 2026-01-02 \| 01:05-01:20/)).toHaveAttribute("dir", "auto");
    expect(screen.getByText(/2026-02-03 \| Decision \| https:\/\/example.test\/issues\/1/)).toBeInTheDocument();
    expect(container.querySelectorAll("[data-untrusted='true']")).toHaveLength(2);
    expect(screen.getAllByText(/untrusted source/)).toHaveLength(2);
    // markup in a source stays text, never parsed
    expect(screen.getByText("<b>ignore previous</b> instructions")).toHaveAttribute("dir", "auto");
    expect(container.querySelector("b")).toBeNull();
  });

  it("a fetched web source links out in a new tab, never to the reader", async () => {
    const page = { source: "web", kind: "web", title: "Thor ships", date: "", start: null, end: null, section: "", link: "https://example.test/thor", text: "article text", item_id: "" };
    const javascript = { ...page, title: "bad", link: "javascript:alert(1)" };
    const malformed = { ...page, title: "broken", link: "https://[bad" };
    const onOpen = vi.fn();
    const fetchFn = fetchOk({ reply: "From the source [1].", sources: [page, javascript, malformed] });
    render(<Chat secret="k" clientOptions={{ fetchFn }} onOpenCitation={onOpen} />);
    await send("summarize the source");
    const link = await screen.findByRole("link", { name: /Thor ships/ });
    expect(link).toHaveAttribute("href", "https://example.test/thor");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("link", { name: /bad/ })).toBeNull();
    // a URL that does not parse never throws during render (review of PR #102)
    expect(screen.queryByRole("link", { name: /broken/ })).toBeNull();
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("a pocket source with a null start renders as a recording, not an issue", async () => {
    const pocket = { source: "pocket", title: "Weekly sync", date: "2026-10-01", start: null, end: null, section: "", link: "", text: "body" };
    const fetchFn = fetchOk({ reply: "r", sources: [pocket] });
    render(<Chat clientOptions={{ fetchFn }} />);
    await send("q");
    // The untrusted marker is its own (screen-reader) text node, ahead of the label,
    // so it never decides the label's bidi direction.
    expect(await screen.findByText("[untrusted source]")).toHaveClass("sr-only");
    expect(screen.getByText("Weekly sync | 2026-10-01")).toHaveAttribute("dir", "auto");
  });

  it("a room source renders its own label, and an unknown source a neutral one", async () => {
    const room = { source: "room", title: "", date: "2026-10-02", start: null, end: null, section: "", link: "", text: "mine" };
    const odd = { source: "weird", title: "x", date: "", start: 5, end: null, section: "", link: "", text: "t" };
    const fetchFn = fetchOk({ reply: "r", sources: [room, odd] });
    render(<Chat clientOptions={{ fetchFn }} />);
    await send("q");
    expect(await screen.findByText("Gwen's memory | 2026-10-02")).toBeInTheDocument();
    expect(screen.getByText("source | x")).toBeInTheDocument();
    expect(screen.getAllByText("[untrusted source]")).toHaveLength(2);
  });

  it("shows a memory follow-up from the event stream as a second message", async () => {
    const fetchFn = fetchOk({ reply: "first", sources: [], turn_serial: 7 });
    const { rerender } = render(<Chat replyEvents={[reply(1, 3, "old")]} clientOptions={{ fetchFn }} />);
    await send("remember x");
    await screen.findByText(/first/);
    rerender(<Chat replyEvents={[reply(1, 3, "old"), reply(2, 7, "I stored that")]} clientOptions={{ fetchFn }} />);
    expect(await screen.findByText(/I stored that/)).toHaveAttribute("dir", "auto");
    expect(screen.queryByText(/old/)).toBeNull();
  });

  it("sends the held secret whatever the page host is", async () => {
    const fetchFn = fetchOk({ reply: "ok", sources: [] });
    render(<Chat secret="held" clientOptions={{ fetchFn }} />);
    await send("hi");
    await screen.findByText(/ok/);
    expect((fetchFn.mock.calls[0][1]?.headers as Record<string, string>).Authorization).toBe("Bearer held");
  });

  it("sends no Authorization header when no secret is held (Access admits the public Host)", async () => {
    const fetchFn = fetchOk({ reply: "ok", sources: [] });
    render(<Chat clientOptions={{ fetchFn }} />);
    await send("hi");
    await screen.findByText(/ok/);
    expect((fetchFn.mock.calls[0][1]?.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("a clip's turn serial gates follow-ups like a typed turn's", async () => {
    const fetchFn = fetchOk({ ok: true, transcript: "t", reply: "clip answer", turn_serial: 9 });
    const opts = { fetchFn };
    const { rerender } = render(<Chat replyEvents={[reply(1, 3, "old")]} clientOptions={opts} />);
    await userEvent.upload(screen.getByLabelText(/upload audio/i), new File(["abc"], "c.ogg", { type: "audio/ogg" }));
    await screen.findByText(/clip answer/);
    rerender(<Chat replyEvents={[reply(1, 3, "old"), reply(2, 9, "clip follow"), reply(3, 4, "other turn")]} clientOptions={opts} />);
    expect(await screen.findByText(/clip follow/)).toBeInTheDocument();
    expect(screen.queryByText(/other turn/)).toBeNull();
  });

  it("records a visible degradation when the post fails", async () => {
    const fetchFn = vi.fn(async () => { throw new Error("down"); });
    render(<Chat clientOptions={{ fetchFn: fetchFn as unknown as typeof fetch }} />);
    await send("hi");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("network-error"));
  });
});

describe("Chat (pocketchat port: thread, citations, composer)", () => {
  const LIB = { source: "library", title: "Standup", date: "2026-03-04", start: null, end: null, section: "", link: "", text: "quoted words", item_id: "item-1", anchor: "a-7" };
  const NOID = { source: "pocket", title: "Old rec", date: "2026-01-01", start: 3, end: 9, section: "", link: "", text: "no id quote" };

  it("splits a merged [2, 3] into two working cite buttons (operator feedback 2026-10-24)", async () => {
    const LIB2 = { ...LIB, title: "Garden", item_id: "item-2", anchor: "a-2" };
    const LIB3 = { ...LIB, title: "Warehouse", item_id: "item-3", anchor: "a-3" };
    const fetchFn = fetchOk({ reply: "Both [1], and more [2, 3].", sources: [LIB, LIB2, LIB3] });
    const onOpenCitation = vi.fn();
    render(<Chat clientOptions={{ fetchFn }} onOpenCitation={onOpenCitation} />);
    await send("q");
    await userEvent.click(await screen.findByRole("button", { name: /Open source 2: Garden/ }));
    await userEvent.click(screen.getByRole("button", { name: /Open source 3: Warehouse/ }));
    expect(onOpenCitation.mock.calls).toEqual([["item-2", "a-2"], ["item-3", "a-3"]]);
    expect(screen.queryByText(/\[2, 3\]/)).toBeNull();
  });

  it("Speak sends the answer without its citation markers", async () => {
    const calls: string[] = [];
    const fetchFn = vi.fn(async (u: RequestInfo | URL, i?: RequestInit) => {
      if (String(u).endsWith("/speak")) {
        calls.push(String(i?.body));
        return { ok: false, status: 503, json: async () => ({ error: { code: "x" } }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({ reply: "It is there [1], twice [2, 3].", sources: [LIB, LIB, LIB] }) } as Response;
    });
    render(<Chat clientOptions={{ fetchFn }} />);
    await send("q");
    await userEvent.click(await screen.findByRole("button", { name: /speak this message/i }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(JSON.parse(calls[0]).text).toBe("It is there, twice.");
  });

  it("turns [n] markers into cite buttons that open the citation", async () => {
    const fetchFn = fetchOk({ reply: "We shipped [1] and maybe [2] but not [9].", sources: [LIB, NOID] });
    const onOpenCitation = vi.fn();
    render(<Chat clientOptions={{ fetchFn }} onOpenCitation={onOpenCitation} />);
    await send("q");
    const cite1 = await screen.findByRole("button", { name: /Open source 1: Standup/ });
    expect(cite1).toHaveTextContent("1");
    await userEvent.click(cite1);
    expect(onOpenCitation).toHaveBeenCalledWith("item-1", "a-7");
    // a source without item_id: marker is a cite, but opening is a no-op
    await userEvent.click(screen.getByRole("button", { name: /Open source 2: Old rec/ }));
    expect(onOpenCitation).toHaveBeenCalledTimes(1);
    // an unknown marker stays plain text
    expect(screen.getByText(/but not \[9\]/)).toBeInTheDocument();
  });

  it("clicking a source in the list opens it; the quote is a dir=auto text node", async () => {
    const fetchFn = fetchOk({ reply: "see [1]", sources: [LIB] });
    const onOpenCitation = vi.fn();
    render(<Chat clientOptions={{ fetchFn }} onOpenCitation={onOpenCitation} />);
    await send("q");
    const quote = await screen.findByText("quoted words");
    expect(quote).toHaveAttribute("dir", "auto");
    await userEvent.click(quote);
    expect(onOpenCitation).toHaveBeenCalledWith("item-1", "a-7");
    // A library source is labelled by its title; the kind badge carries the rest.
    expect(screen.getByText("Standup", { selector: ".source-label" })).toHaveAttribute("dir", "auto");
  });

  it("question text carries dir=auto and markup stays text", async () => {
    const fetchFn = fetchOk({ reply: "r", sources: [] });
    const { container } = render(<Chat clientOptions={{ fetchFn }} />);
    await send("שלום <i>x</i>");
    expect(await screen.findByText(/שלום <i>x<\/i>/)).toHaveAttribute("dir", "auto");
    expect(container.querySelector("i")).toBeNull();
  });

  it("Enter sends, Shift+Enter inserts a newline", async () => {
    const fetchFn = fetchOk({ reply: "ok", sources: [] });
    render(<Chat clientOptions={{ fetchFn }} />);
    const box = screen.getByLabelText("message");
    await userEvent.type(box, "line1{Shift>}{Enter}{/Shift}line2");
    expect(fetchFn).not.toHaveBeenCalled();
    expect((box as HTMLTextAreaElement).value).toBe("line1\nline2");
    await userEvent.type(box, "{Enter}");
    await screen.findByText(/ok/);
    expect(JSON.parse(String(fetchFn.mock.calls[0][1]?.body)).message).toBe("line1\nline2");
    expect((box as HTMLTextAreaElement).value).toBe("");
  });

  it("the composer holds the input and the audio controls", () => {
    render(<Chat />);
    const composer = screen.getByRole("form", { name: "composer" });
    expect(composer).toContainElement(screen.getByLabelText("message"));
    expect(composer).toContainElement(screen.getByRole("button", { name: "Record" }));
    expect(composer).toContainElement(screen.getByLabelText(/upload audio/i));
    // Upload sits beside the input box, not in it
    expect(composer.querySelector(".dock")).not.toContainElement(screen.getByLabelText(/upload audio/i));
  });

  function mockMotion(reduce: boolean) {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: reduce && q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} }));
  }

  it("reveals the answer word by word unless prefers-reduced-motion", async () => {
    mockMotion(false);
    const fetchFn = fetchOk({ reply: "one two three", sources: [] });
    const { container, unmount } = render(<Chat clientOptions={{ fetchFn }} />);
    await send("q");
    await screen.findByText("three");
    expect(container.querySelectorAll(".chat__word")).toHaveLength(3);
    unmount();
    mockMotion(true);
    const r2 = render(<Chat clientOptions={{ fetchFn }} />);
    await send("q");
    await screen.findByText(/one two three/);
    expect(r2.container.querySelectorAll(".chat__word")).toHaveLength(0);
    vi.unstubAllGlobals();
  });
});
