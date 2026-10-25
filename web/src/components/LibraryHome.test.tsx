import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { LibraryHome } from "./LibraryHome";
import type { LibraryItem } from "../api/library";

// Synthetic items only.
const items: LibraryItem[] = [
  { id: "syn-a", title: "A synthetic book", kind: "book", createdAt: "2026-01-03T00:00:00Z", indexed: true },
  { id: "syn-b", title: "ספר סינתטי", kind: "document", createdAt: "2026-01-02T00:00:00Z", indexed: true },
  { id: "syn-c", title: "A removed one", kind: "book", createdAt: "2026-01-01T00:00:00Z", indexed: true },
];
const pos = (itemId: string, gone = false) => ({ itemId, sha256: "s", section: 2, paragraph: 4, part: 0, updatedAt: "", gone });

describe("LibraryHome: Continue listening (t4 AC4)", () => {
  it("a card with a saved position offers Continue listening; others Read aloud; a gone position offers no continue", () => {
    const onListen = vi.fn();
    const onOpen = vi.fn();
    render(<LibraryHome items={items} onOpen={onOpen} onAsk={() => {}} positions={[pos("syn-a"), pos("syn-c", true)]} onListen={onListen} />);
    const cont = screen.getByRole("button", { name: "Continue listening: section 3, paragraph 5" });
    expect(cont.querySelector("[dir=auto]")).not.toBeNull();
    expect(screen.getAllByRole("button", { name: /Continue listening/ })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Read aloud" })).toHaveLength(2);
    cont.focus();
    expect(document.activeElement).toBe(cont);
    fireEvent.click(cont);
    expect(onListen).toHaveBeenCalledWith("syn-a");
    expect(onOpen).not.toHaveBeenCalled();
    expect(screen.getByText("ספר סינתטי")).toHaveAttribute("dir", "auto");
  });

  it("without onListen the cards stay as they were", () => {
    const { container } = render(<LibraryHome items={items} onOpen={() => {}} onAsk={() => {}} positions={[pos("syn-a")]} />);
    expect(container.querySelector(".card-cell")).toBeNull();
    expect(screen.queryByText(/Continue listening/)).toBeNull();
  });
});
