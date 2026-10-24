import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TodosView, TODOS_EMPTY_MESSAGE, TODOS_UNAVAILABLE_MESSAGE } from "./TodosView";

const res = (status: number, body: unknown) =>
  vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) =>
    ({ ok: status < 400, status, json: async () => body }) as Response);

describe("TodosView", () => {
  it("groups items by recording as dir=auto text, with the provenance header", async () => {
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
    // without an id an item cannot be marked: its checkbox is disabled
    const boxes = container.querySelectorAll("input[type=checkbox]");
    expect(boxes).toHaveLength(3);
    boxes.forEach((b) => expect(b).toBeDisabled());
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

describe("TodosView checking (d16)", () => {
  const groups = {
    groups: [
      { recording_id: "r1", title: "Standup", date: "2026-01-02", items: [{ id: "t1", text: "call Dana", created: "", done: false }] },
    ],
  };

  it("ticking a to-do posts it as done and strikes it through", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchFn = vi.fn(async (u: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(u), init });
      return { ok: true, status: 200, json: async () => (init?.method === "POST" ? { ok: true } : groups) } as Response;
    });
    const { container } = render(<TodosView secret="k" clientOptions={{ fetchFn }} />);
    const box = await screen.findByRole("checkbox", { name: "call Dana" });
    fireEvent.click(box);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/todos/t1")).toBe(true));
    expect(box).toBeChecked();
    expect(container.querySelector("li")).toHaveAttribute("data-done", "true");
  });

  it("a refused mark is undone and said", async () => {
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST"
        ? ({ ok: false, status: 404, json: async () => ({ error: { code: "todo-unknown" } }) } as Response)
        : ({ ok: true, status: 200, json: async () => groups } as Response),
    );
    render(<TodosView clientOptions={{ fetchFn }} />);
    const box = await screen.findByRole("checkbox", { name: "call Dana" });
    fireEvent.click(box);
    expect(await screen.findByRole("alert")).toHaveTextContent("todo-unknown");
    expect(box).not.toBeChecked();
  });
});

describe("TodosView after Gwen marks one (review of PR #103)", () => {
  it("reloads when the refresh key changes, showing Gwen's mark", async () => {
    let done = false;
    const fetchFn = vi.fn(async () =>
      ({
        ok: true,
        status: 200,
        json: async () => ({
          groups: [{ recording_id: "r1", title: "Standup", date: "", items: [{ id: "t1", text: "call Dana", created: "", done, done_by: done ? "gwen" : null }] }],
        }),
      }) as Response,
    );
    const { rerender } = render(<TodosView clientOptions={{ fetchFn }} refreshKey={0} />);
    expect(await screen.findByRole("checkbox", { name: "call Dana" })).not.toBeChecked();
    done = true;
    rerender(<TodosView clientOptions={{ fetchFn }} refreshKey={1} />);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "call Dana" })).toBeChecked());
    expect(screen.getByText("marked by Gwen")).toBeInTheDocument();
  });
});
