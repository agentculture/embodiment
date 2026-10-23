import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LibraryView } from "./LibraryView";

const ITEMS = [
  { id: "a", title: "Standup <b>x</b>", kind: "recording", created_at: "2026-01-02T09:00:00Z", indexed: true },
  { id: "b", title: "שלום", kind: "note", created_at: "2026-01-02T10:00:00Z", indexed: false },
  { id: "c", title: "Older", kind: "recording", created_at: "2025-12-31T10:00:00Z", indexed: true },
];
const DETAIL = {
  item: { id: "a", title: "Standup", kind: "recording", author: "me", created_at: "2026-01-02" },
  passages: [{ anchor: "p1", heading: "", text: "hello", start_s: null, end_s: null, speaker: "" }],
};
const fakeFetch = () =>
  vi.fn(async (u: RequestInfo | URL, _i?: RequestInit) => {
    const body = String(u) === "/api/library" ? { items: ITEMS } : DETAIL;
    return { ok: true, status: 200, json: async () => body } as Response;
  });

describe("LibraryView", () => {
  it("groups items by day and renders titles as dir=auto text", async () => {
    const { container } = render(<LibraryView clientOptions={{ fetchFn: fakeFetch() }} />);
    expect(await screen.findByText("Standup <b>x</b>")).toHaveAttribute("dir", "auto");
    expect(container.querySelector("b")).toBeNull();
    const days = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(days).toEqual(["2026-01-02", "2025-12-31"]);
  });

  it("kind chips filter with aria-pressed", async () => {
    render(<LibraryView clientOptions={{ fetchFn: fakeFetch() }} />);
    await screen.findByText("שלום");
    const chip = screen.getByRole("button", { name: "note" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("Older")).toBeNull();
    expect(screen.getByText("שלום")).toBeInTheDocument();
    await userEvent.click(chip);
    expect(screen.getByText("Older")).toBeInTheDocument();
  });

  it("clicking an item opens the reader; Escape closes it", async () => {
    const fetchFn = fakeFetch();
    render(<LibraryView secret="k" clientOptions={{ fetchFn }} />);
    await userEvent.click(await screen.findByRole("button", { name: /Standup/ }));
    expect(await screen.findByText("hello")).toBeInTheDocument();
    expect(fetchFn.mock.calls.some((c) => c[0] === "/api/library/a")).toBe(true);
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText("hello")).toBeNull());
  });

  it("shows a fixed error code when the list fails", async () => {
    const fetchFn = vi.fn(async () => { throw new Error("boom"); });
    render(<LibraryView clientOptions={{ fetchFn }} />);
    expect(await screen.findByText(/network-error/)).toBeInTheDocument();
  });
});
