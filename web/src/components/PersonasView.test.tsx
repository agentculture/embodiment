import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { PersonasView } from "./PersonasView";
import type { ModelsInfo } from "../api/models";

const gwen = { id: "gwen", name: "Gwen", language: "he", length: "unset", allow_formatting: false, translate_via: null, mesh_enabled: false, text: "", default_role: "senses" };
const mine = { id: "p1", name: "Explainer", language: "he", length: "detailed", allow_formatting: true, translate_via: "senses", mesh_enabled: false, text: "את מסבירה", default_role: "worker" };
const models: ModelsInfo = {
  roles: [
    { role: "senses", model: "Gemma", ready: true, host: "spark", voice: "unmeasured", voiceP90S: null },
    { role: "worker", model: "Bolt", ready: true, host: "spark", voice: "unmeasured", voiceP90S: null },
  ],
  stale: false, ageS: 1, conversationRole: "senses", live: {},
};

function api(listBody: unknown = { personas: [gwen, mine], builtin_id: "gwen" }) {
  return vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "GET") return { ok: true, status: 200, json: async () => listBody } as Response;
    const body = JSON.parse(String(init?.body ?? "{}"));
    return { ok: true, status: 200, json: async () => ({ id: method === "POST" ? "p2" : "p1", ...body }) } as Response;
  });
}

describe("PersonasView", () => {
  it("lists personas, the built-in one marked and read-only", async () => {
    const fetchFn = api();
    render(<PersonasView secret="k" models={models} clientOptions={{ fetchFn }} />);
    const nav = await screen.findByRole("navigation", { name: "Personas" });
    expect(within(nav).getByText("built in")).toBeInTheDocument();
    fireEvent.click(within(nav).getByText("Gwen"));
    expect(screen.getByLabelText("Name")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save persona" })).toBeNull();
    expect(screen.getByRole("button", { name: /Duplicate/ })).toBeInTheDocument();
  });

  it("edits every field of a persona and saves with PUT", async () => {
    const fetchFn = api();
    render(<PersonasView secret="k" models={models} clientOptions={{ fetchFn }} />);
    const nav = await screen.findByRole("navigation", { name: "Personas" });
    fireEvent.click(within(nav).getByText("Explainer"));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Explainer 2" } });
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "senses" } });
    fireEvent.click(within(screen.getByRole("group", { name: "Default language" })).getByText("English"));
    fireEvent.click(within(screen.getByRole("group", { name: "Length" })).getByText("Brief"));
    fireEvent.click(screen.getByRole("switch", { name: /formatting/ }));
    fireEvent.click(screen.getByRole("switch", { name: /Translate/ }));
    fireEvent.click(screen.getByRole("switch", { name: /Culture chat/ }));
    fireEvent.change(screen.getByLabelText("How this persona talks"), { target: { value: "קצר" } });
    fireEvent.click(screen.getByRole("button", { name: "Save persona" }));
    await waitFor(() => expect(fetchFn.mock.calls.some((c) => c[1]?.method === "PUT")).toBe(true));
    const put = fetchFn.mock.calls.find((c) => c[1]?.method === "PUT")!;
    expect(String(put[0])).toBe("/api/personas/p1");
    expect(JSON.parse(String(put[1]!.body))).toEqual({
      name: "Explainer 2", language: "en", length: "brief", allow_formatting: false,
      translate_via: null, mesh_enabled: true, text: "קצר", default_role: "senses",
    });
  });

  it("a new persona is created with POST", async () => {
    const fetchFn = api();
    render(<PersonasView secret="" models={models} clientOptions={{ fetchFn }} />);
    fireEvent.click(await screen.findByRole("button", { name: "New persona" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Fresh" } });
    fireEvent.click(screen.getByRole("button", { name: "Save persona" }));
    await waitFor(() => expect(fetchFn.mock.calls.some((c) => c[1]?.method === "POST")).toBe(true));
    const post = fetchFn.mock.calls.find((c) => c[1]?.method === "POST")!;
    expect(String(post[0])).toBe("/api/personas");
    expect(JSON.parse(String(post[1]!.body)).name).toBe("Fresh");
  });

  it("shows a refusal code from the server instead of pretending it saved", async () => {
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) =>
      (init?.method ?? "GET") === "GET"
        ? ({ ok: true, status: 200, json: async () => ({ personas: [gwen, mine], builtin_id: "gwen" }) } as Response)
        : ({ ok: false, status: 409, json: async () => ({ error: { code: "too_many" } }) } as Response),
    );
    render(<PersonasView secret="" models={models} clientOptions={{ fetchFn }} />);
    fireEvent.click(await screen.findByRole("button", { name: "New persona" }));
    fireEvent.click(screen.getByRole("button", { name: "Save persona" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("too_many");
  });

  it("previews what Gwen will be told: the persona's text verbatim as yours, fixed sections by name only", async () => {
    render(<PersonasView secret="" models={models} clientOptions={{ fetchFn: api() }} />);
    const nav = await screen.findByRole("navigation", { name: "Personas" });
    fireEvent.click(within(nav).getByText("Explainer"));
    const panel = screen.getByLabelText("Composed prompt preview");
    const own = within(panel).getByText("את מסבירה");
    expect(own).toHaveAttribute("dir", "auto");
    expect(own.closest(".sec")).toHaveClass("mine");
    for (const name of ["Clock", "Memory and tool rules", "The conversation so far", "Recalled memory", "Speakability rules"]) {
      const sec = within(panel).getByText(name).closest(".sec");
      expect(sec).not.toHaveClass("mine");
    }
    expect(within(panel).getAllByText("yours").length).toBeGreaterThan(0);
    expect(within(panel).getByText(/byte for byte/)).toBeInTheDocument();
  });

  it("says so when the list cannot be read", async () => {
    const fetchFn = vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) }) as Response);
    render(<PersonasView secret="" models={models} clientOptions={{ fetchFn }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/personas/i);
  });
});
