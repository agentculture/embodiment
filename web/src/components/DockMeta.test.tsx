import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { DockMeta } from "./DockMeta";
import type { ModelsInfo } from "../api/models";
import type { Persona } from "../api/personas";

const P = (o: Partial<Persona>): Persona => ({
  id: "x", name: "x", language: "he", length: "unset", allow_formatting: false,
  translate_via: null, mesh_enabled: false, text: "", default_role: "senses", ...o,
});
const personas = [
  P({ id: "gwen", name: "Gwen" }),
  P({ id: "p1", name: "Explainer", length: "detailed", default_role: "worker", translate_via: "senses" }),
];
const models: ModelsInfo = {
  roles: [
    { role: "senses", model: "Gemma-4", ready: true, host: "spark", voice: "measured", voiceP90S: 1.4 },
    { role: "worker", model: "Bolt", ready: true, host: "spark", voice: "unmeasured", voiceP90S: null },
    { role: "muse", model: "G31", ready: false, host: "spark2", voice: "unmeasured", voiceP90S: null },
  ],
  stale: false, ageS: 14, conversationRole: "senses",
};

function setup(over: Partial<React.ComponentProps<typeof DockMeta>> = {}) {
  const onChange = vi.fn();
  const utils = render(
    <DockMeta personas={personas} builtinId="gwen" models={models} settings={{}} onChange={onChange} {...over} />,
  );
  return { onChange, ...utils };
}

describe("DockMeta", () => {
  it("shows Persona, Language and Model with their current values; labels sit outside dir=auto", () => {
    setup({ settings: { persona: "p1", model: "worker" } });
    const persona = screen.getByRole("button", { name: /Persona/ });
    expect(within(persona).getByText("Explainer")).toHaveAttribute("dir", "auto");
    expect(within(persona).getByText("Persona")).not.toHaveAttribute("dir");
    expect(within(screen.getByRole("button", { name: /Model/ })).getByText("worker")).toHaveAttribute("dir", "auto");
    // language defaults to the persona's own
    expect(within(screen.getByRole("button", { name: /Language/ })).getByText("עברית")).toBeInTheDocument();
  });

  it("the language menu lists Hebrew, English (text only) and None, and reports a choice", () => {
    const { onChange } = setup();
    fireEvent.click(screen.getByRole("button", { name: /Language/ }));
    const list = screen.getByRole("listbox", { name: "Language" });
    expect(list).toHaveClass("pop");
    const opts = within(list).getAllByRole("option");
    expect(opts).toHaveLength(3);
    expect(within(list).getByText("text only")).toBeInTheDocument();
    fireEvent.click(within(list).getByRole("option", { name: /English/ }));
    expect(onChange).toHaveBeenCalledWith({ language: "en" });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("the persona menu marks built in and yours, and selects by id", () => {
    const { onChange } = setup();
    fireEvent.click(screen.getByRole("button", { name: /Persona/ }));
    const list = screen.getByRole("listbox", { name: "Persona" });
    expect(within(list).getByText("built in")).toBeInTheDocument();
    expect(within(list).getByText("yours")).toBeInTheDocument();
    expect(within(list).getByRole("option", { name: /Gwen/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(within(list).getByRole("option", { name: /Explainer/ }));
    expect(onChange).toHaveBeenCalledWith({ persona: "p1" });
  });

  it("the model menu greys not-ready roles, shows measured voice speed, and switches in one tap", () => {
    const { onChange } = setup();
    fireEvent.click(screen.getByRole("button", { name: /Model/ }));
    const list = screen.getByRole("listbox", { name: "Reply model" });
    const muse = within(list).getByRole("option", { name: /muse/ });
    expect(muse).toHaveAttribute("aria-disabled", "true");
    expect(within(muse).getByText("not ready")).toBeInTheDocument();
    fireEvent.click(muse);
    expect(onChange).not.toHaveBeenCalled();
    expect(within(list).getByText(/voice p90 1\.4 s/)).toBeInTheDocument();
    expect(within(list).getAllByText(/voice speed unmeasured/).length).toBeGreaterThan(0);
    fireEvent.click(within(list).getByRole("option", { name: /worker/ }));
    expect(onChange).toHaveBeenCalledWith({ model: "worker" });
  });

  it("opens one menu at a time and Escape closes it", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: /Language/ }));
    fireEvent.click(screen.getByRole("button", { name: /Model/ }));
    expect(screen.getAllByRole("listbox")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Model/ })).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("with the model unavailable the Model control is amber and Retry is offered", () => {
    const onRetry = vi.fn();
    setup({ unavailable: "cortex", onRetry });
    const btn = screen.getByRole("button", { name: /Model/ });
    expect(btn).toHaveClass("warn");
    expect(btn.textContent).toContain("unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("without model data the row still renders and the model menu says so", () => {
    setup({ models: null });
    fireEvent.click(screen.getByRole("button", { name: /Model/ }));
    expect(screen.getByText(/model list is not available/i)).toBeInTheDocument();
  });
});
