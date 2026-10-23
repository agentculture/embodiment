import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { TodosView, TODOS_EMPTY_MESSAGE, TODOS_UNAVAILABLE_MESSAGE } from "./TodosView";

const res = (status: number, body: unknown) =>
  vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) =>
    ({ ok: status < 400, status, json: async () => body }) as Response);

describe("TodosView", () => {
  it("groups items by recording as dir=auto text, read-only, with the provenance header", async () => {
    const fetchFn = res(200, {
      groups: [
        { recording_id: "r1", title: "Synthetic standup", date: "2026-01-02", items: [{ text: "<b>שלום</b> call Dana", created: "2026-01-02" }, { text: "send notes", created: "" }] },
        { recording_id: "r2", title: "", date: "", items: [{ text: "second", created: "" }] },
      ],
    });
    const { container } = render(<TodosView secret="k" clientOptions={{ fetchFn }} />);
    expect(await screen.findByText("<b>שלום</b> call Dana")).toHaveAttribute("dir", "auto");
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText("Synthetic standup")).toHaveAttribute("dir", "auto");
    expect(screen.getByText("Untitled recording")).toBeInTheDocument();
    expect(container.querySelectorAll(".todos__group")).toHaveLength(2);
    expect(container.querySelectorAll("li")).toHaveLength(3);
    expect(container.querySelector("input, button")).toBeNull();
    expect(container.textContent).toContain("pocketsync/web/static/app.js:740-766");
  });

  it("an empty list renders the fixed empty message", async () => {
    render(<TodosView clientOptions={{ fetchFn: res(200, { groups: [] }) }} />);
    expect(await screen.findByText(TODOS_EMPTY_MESSAGE)).toBeInTheDocument();
  });

  it("a 503 renders the fixed unavailable message, never the code or body", async () => {
    const { container } = render(<TodosView clientOptions={{ fetchFn: res(503, { code: "library-unavailable" }) }} />);
    expect(await screen.findByText(TODOS_UNAVAILABLE_MESSAGE)).toBeInTheDocument();
    expect(container.textContent).not.toContain("library-unavailable");
  });
});
