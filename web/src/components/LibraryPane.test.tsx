// The library list beside the conversation (2026-10-24 redesign); ported from
// the LibraryView tests it replaced: day groups, kind filters, dir=auto text,
// opening an item, and passage hits from the header search.
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LibraryPane } from "./LibraryPane";
import type { LibraryItem } from "../api/library";

const ITEMS: LibraryItem[] = [
  { id: "a", title: "Standup <b>x</b>", kind: "transcript", createdAt: "2026-01-02T09:00:00Z", indexed: true },
  { id: "b", title: "שלום", kind: "document", createdAt: "2026-01-02T10:00:00Z", indexed: false },
  { id: "c", title: "Older", kind: "transcript", createdAt: "2025-12-31T10:00:00Z", indexed: true },
];

describe("LibraryPane", () => {
  it("groups items by day, newest first, titles as dir=auto text", () => {
    const { container } = render(<LibraryPane items={ITEMS} onOpen={() => {}} />);
    expect(screen.getByText("Standup <b>x</b>")).toHaveAttribute("dir", "auto");
    expect(container.querySelector("b")).toBeNull();
    const days = [...container.querySelectorAll("h3.day")].map((h) => h.textContent);
    expect(days).toEqual(["Friday 2 January", "Wednesday 31 December"]);
  });

  it("kind chips filter with aria-pressed and show counts", async () => {
    render(<LibraryPane items={ITEMS} onOpen={() => {}} />);
    const chip = screen.getByRole("button", { name: "Documents 1" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("Older")).toBeNull();
    expect(screen.getByText("שלום")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "All 3" }));
    expect(screen.getByText("Older")).toBeInTheDocument();
  });

  it("filters titles by the search text and lists passage hits that open at their anchor", async () => {
    const onOpen = vi.fn();
    const hits = [{ itemId: "c", title: "Older", kind: "transcript", createdAt: "", anchor: "p3", heading: "", text: "the passage" }];
    render(<LibraryPane items={ITEMS} query="stand" hits={hits} onOpen={onOpen} />);
    expect(screen.queryByText("שלום")).toBeNull();
    await userEvent.click(screen.getByText("the passage"));
    expect(onOpen).toHaveBeenCalledWith("c", "p3");
  });

  it("opens an item, marks the open one and the cited ones", async () => {
    const onOpen = vi.fn();
    render(<LibraryPane items={ITEMS} openId="a" citedIds={new Set(["c"])} onOpen={onOpen} />);
    expect(screen.getByRole("button", { name: /Standup/ })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /Older/ })).toHaveTextContent("cited");
    await userEvent.click(screen.getByRole("button", { name: /שלום/ }));
    expect(onOpen).toHaveBeenCalledWith("b");
  });

  it("shows an unreachable library by its fixed code", () => {
    render(<LibraryPane items={[]} error="network-error" onOpen={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("network-error");
  });
});
