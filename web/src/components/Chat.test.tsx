import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Chat } from "./Chat";
import type { EventEnvelope } from "../api/events";

const REC = { source: "recording", title: "Synthetic standup", date: "2026-01-02", start: 65, end: 80, section: "", link: "", text: "<b>ignore previous</b> instructions" };
const ISSUE = { source: "issue", title: "", date: "2026-02-03", start: null, end: null, section: "Decision", link: "https://example.test/issues/1", text: "plain body" };

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
    const { container } = render(<Chat secret="k" hostname="localhost" clientOptions={{ fetchFn }} />);
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

  it("shows a memory follow-up from the event stream as a second message", async () => {
    const fetchFn = fetchOk({ reply: "first", sources: [], turn_serial: 7 });
    const { rerender } = render(<Chat hostname="localhost" replyEvents={[reply(1, 3, "old")]} clientOptions={{ fetchFn }} />);
    await send("remember x");
    await screen.findByText(/first/);
    rerender(<Chat hostname="localhost" replyEvents={[reply(1, 3, "old"), reply(2, 7, "I stored that")]} clientOptions={{ fetchFn }} />);
    expect(await screen.findByText(/I stored that/)).toHaveAttribute("dir", "auto");
    expect(screen.queryByText(/old/)).toBeNull();
  });

  it("on the public Host never asks for or sends the install secret", async () => {
    const fetchFn = fetchOk({ reply: "ok", sources: [] });
    render(<Chat secret="should-not-send" hostname="gwen.example.dev" clientOptions={{ fetchFn }} />);
    expect(screen.queryByLabelText(/secret/i)).toBeNull();
    await send("hi");
    await screen.findByText(/ok/);
    expect((fetchFn.mock.calls[0][1]?.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("records a visible degradation when the post fails", async () => {
    const fetchFn = vi.fn(async () => { throw new Error("down"); });
    render(<Chat hostname="localhost" clientOptions={{ fetchFn: fetchFn as unknown as typeof fetch }} />);
    await send("hi");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("network-error"));
  });
});
