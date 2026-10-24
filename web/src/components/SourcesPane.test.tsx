// The conversation's right pane (2026-10-24): every answer's sources, newest
// first; opening one shows the reader in place, with a way back.
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SourcesPane } from "./SourcesPane";

const SRC = { source: "library", title: "Garden call", kind: "transcript", date: "", start: null, end: null, section: "", link: "", text: "basil by the fence", item_id: "7", anchor: "p2" };

const TABS = { tab: "sources" as const, onTab: () => {}, library: <p>the library list</p> };

describe("SourcesPane", () => {
  it("has a Sources tab and a Library tab; the Library tab shows the list it is given", async () => {
    const onTab = vi.fn();
    const { rerender } = render(
      <SourcesPane {...TABS} onTab={onTab} answers={[]} open={null} onOpen={() => {}} onBack={() => {}} onClose={() => {}} />,
    );
    expect(screen.getByRole("tab", { name: /Sources/ })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("tab", { name: "Library" }));
    expect(onTab).toHaveBeenCalledWith("library");
    rerender(
      <SourcesPane {...TABS} tab="library" answers={[]} open={null} onOpen={() => {}} onBack={() => {}} onClose={() => {}} />,
    );
    expect(screen.getByText("the library list")).toBeInTheDocument();
  });

  it("invites when empty", () => {
    render(<SourcesPane {...TABS} answers={[]} open={null} onOpen={() => {}} onBack={() => {}} onClose={() => {}} />);
    expect(screen.getByText(/passages she used appear here/)).toBeInTheDocument();
  });

  it("lists answers newest first and opens a source at its anchor", async () => {
    const onOpen = vi.fn();
    const answers = [
      { key: "1", question: "first q", sources: [SRC] },
      { key: "2", question: "second q", sources: [{ ...SRC, title: "Warehouse" }] },
    ];
    render(<SourcesPane {...TABS} answers={answers} open={null} onOpen={onOpen} onBack={() => {}} onClose={() => {}} />);
    const qs = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(qs).toEqual(["second q", "first q"]);
    await userEvent.click(screen.getByRole("button", { name: /Garden call/ }));
    expect(onOpen).toHaveBeenCalledWith("7", "p2");
  });

  it("shows the reader in place with a way back", async () => {
    const onBack = vi.fn();
    const fetchFn = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ item: { id: "7", title: "Garden call", kind: "transcript", created_at: "" }, passages: [] }) }) as Response);
    globalThis.fetch = fetchFn as unknown as typeof fetch;
    render(<SourcesPane {...TABS} answers={[]} open={{ itemId: "7", anchor: "p2" }} onOpen={() => {}} onBack={onBack} onClose={() => {}} />);
    expect(await screen.findByRole("heading", { name: "Garden call" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "All sources" }));
    expect(onBack).toHaveBeenCalled();
  });
});
