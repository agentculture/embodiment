// Stats (2026-10-24): culture-library's "Site state" pattern from GET
// /api/library and GET /api/status. Status rows carry an icon and a label,
// never colour alone; every chart has a Table view.
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StatsView } from "./StatsView";
import type { StatusEnvelopeResponse } from "../api/control";

const ITEMS = [
  { id: "1", title: "a", kind: "transcript", createdAt: "2026-10-21T05:00:00Z", indexed: true },
  { id: "2", title: "b", kind: "newspaper", createdAt: "2026-10-22T05:00:00Z", indexed: true },
];
const STATUS = {
  daemon: {
    library: { configured: true, items_indexed: 2, chunks: 40, last_sync_at: null, failures_by_code: {}, similarity_threshold: 0.715, tool_calls: { search_library: 3, read_passage: 1 } },
    recall: { mode: "semantic", calls: 9, hits_total: 0, empty_total: 9 },
    access: { mode: "on", admitted: 5, refused: 1 },
    ear: { active: null },
    text_turns: { run: 6 },
    turns: { completed: 2 },
  },
  http: {},
} as unknown as StatusEnvelopeResponse;

describe("StatsView", () => {
  it("tiles, status rows and charts from the library and the status", async () => {
    const fetchStatusFn = vi.fn(async () => STATUS);
    render(<StatsView items={ITEMS} fetchStatusFn={fetchStatusFn} />);
    expect(await screen.findByText("Finds nothing")).toBeInTheDocument();
    expect(screen.getByText("Passages Gwen can search").previousSibling).toHaveTextContent("40");
    expect(screen.getByText("Questions since Gwen started").previousSibling).toHaveTextContent("8");
    expect(screen.getByText("1 newspaper, 1 transcript")).toBeInTheDocument();
    expect(screen.getByText("Cloudflare Access")).toBeInTheDocument();
    expect(screen.getByText(/no history is kept yet/)).toBeInTheDocument();
  });

  it("every chart toggles to a table", async () => {
    render(<StatsView items={ITEMS} fetchStatusFn={vi.fn(async () => STATUS)} />);
    await screen.findByText("Finds nothing");
    const toggles = screen.getAllByRole("button", { name: "Table" });
    expect(toggles).toHaveLength(3);
    await userEvent.click(toggles[2]);
    expect(screen.getByRole("table")).toHaveTextContent("Library searches3");
  });

  it("says so when the status cannot be read", async () => {
    render(<StatsView items={ITEMS} fetchStatusFn={vi.fn(async () => { throw new Error("x"); })} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be read/);
  });
});
