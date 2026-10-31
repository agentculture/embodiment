// The left pane: conversations from GET /api/conversations (operator, 2026-10-24),
// the live one as "Now", the rest grouped by day; a past one opens read-only.
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HistoryPane } from "./HistoryPane";
import { PastConversation } from "./PastConversation";

const ok = (body: unknown) => vi.fn(async () => ({ ok: true, status: 200, json: async () => body }) as Response);
const DAY = Date.UTC(2026, 9, 23, 9, 30) / 1000;
const LIST = {
  conversations: [
    { id: "live", title: "golf?", started: DAY, last: DAY, turns: 1, current: true, agent: null },
    { id: "old", title: "מה במחסן?", started: DAY - 60, last: DAY - 30, turns: 2, current: false, agent: null },
  ],
};

describe("HistoryPane", () => {
  it("lists Now first, then past conversations by day, titles as dir=auto text", async () => {
    render(<HistoryPane openId={null} onOpen={() => {}} clientOptions={{ fetchFn: ok(LIST) }} />);
    const title = await screen.findByText("מה במחסן?");
    expect(title).toHaveAttribute("dir", "auto");
    expect(screen.getByRole("button", { name: /Now/ })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("heading", { name: "Friday 23 October" })).toBeInTheDocument();
    expect(screen.queryByText("golf?")).toBeNull(); // the live one is "Now", not repeated
  });

  it("shows an agent conversation with the nick as a chip", async () => {
    const list = { conversations: [...LIST.conversations, { id: "ag", title: "hello gwen", started: DAY - 20, last: DAY - 10, turns: 1, current: false, agent: "thor-lobes" }] };
    render(<HistoryPane openId={null} onOpen={() => {}} clientOptions={{ fetchFn: ok(list) }} />);
    const chip = await screen.findByText("thor-lobes");
    expect(chip).toHaveClass("nick");
    expect(chip).not.toHaveAttribute("dir", "auto");
    expect(screen.getByText("hello gwen")).toHaveAttribute("dir", "auto");
    expect(screen.queryAllByText(/^thor-lobes$/)).toHaveLength(1);
  });

  it("offers New conversation when the page can start one", async () => {
    const onNew = vi.fn();
    render(<HistoryPane openId={null} onOpen={() => {}} onNew={onNew} clientOptions={{ fetchFn: ok(LIST) }} />);
    await userEvent.click(await screen.findByRole("button", { name: "New conversation" }));
    expect(onNew).toHaveBeenCalledTimes(1);
  });

  it("says so under the button when a new conversation was refused", async () => {
    render(
      <HistoryPane openId={null} onOpen={() => {}} onNew={() => {}} newError="http-unknown-route" clientOptions={{ fetchFn: ok(LIST) }} />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be started (http-unknown-route)");
  });

  it("shows no New conversation button without a handler", async () => {
    render(<HistoryPane openId={null} onOpen={() => {}} clientOptions={{ fetchFn: ok(LIST) }} />);
    await screen.findByText("מה במחסן?");
    expect(screen.queryByRole("button", { name: "New conversation" })).toBeNull();
  });

  it("opens a past conversation, and Now goes back", async () => {
    const onOpen = vi.fn();
    render(<HistoryPane openId="old" onOpen={onOpen} clientOptions={{ fetchFn: ok(LIST) }} />);
    await userEvent.click(await screen.findByRole("button", { name: /מה במחסן/ }));
    expect(onOpen).toHaveBeenLastCalledWith(expect.objectContaining({ id: "old" }));
    await userEvent.click(screen.getByRole("button", { name: /Now/ }));
    expect(onOpen).toHaveBeenLastCalledWith(null);
  });

  it("says so when the history cannot be read", async () => {
    const fetchFn = vi.fn(async () => ({ ok: false, status: 503, json: async () => ({ error: { code: "history-not-configured" } }) }) as Response);
    render(<HistoryPane openId={null} onOpen={() => {}} clientOptions={{ fetchFn }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("history-not-configured");
  });
});

describe("PastConversation (continued, deviation d5)", () => {
  it("shows the old turns and sends a new question INTO that conversation", async () => {
    const onBack = vi.fn();
    const calls: { url: string; body: string }[] = [];
    const fetchFn = vi.fn(async (u: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(u), body: String(init?.body ?? "") });
      if (String(u).endsWith("/chat")) {
        return { ok: true, status: 200, json: async () => ({ reply: "כחולה", sources: [] }) } as Response;
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          conversation: LIST.conversations[1],
          turns: [
            { role: "user", text: "מה במחסן?", ts: DAY - 60 },
            { role: "assistant", text: "קומקום ישן [1].", ts: DAY - 30 },
          ],
        }),
      } as Response;
    });
    render(
      <PastConversation
        conversation={LIST.conversations[1]}
        onBack={onBack}
        clientOptions={{ fetchFn }}
        chat={{ clientOptions: { fetchFn } }}
      />,
    );
    expect(await screen.findByText("קומקום ישן [1].")).toHaveAttribute("dir", "auto");
    await userEvent.type(screen.getByLabelText("message"), "ומה צבע הדלת?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("כחולה")).toBeInTheDocument();
    const chat = calls.find((c) => c.url.endsWith("/chat"));
    expect(JSON.parse(chat!.body)).toEqual({ message: "ומה צבע הדלת?", conversation: "old" });
    await userEvent.click(screen.getByRole("button", { name: "Back to now" }));
    expect(onBack).toHaveBeenCalled();
  });
});
