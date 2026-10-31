import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Chat } from "./Chat";
import type { LiveMap } from "../api/liveText";

async function send(text: string) {
  await userEvent.type(screen.getByLabelText("message"), text);
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
}

/** A POST that stays open until `release` is called; returns the request id it was sent. */
function heldPost() {
  let release: (body: unknown) => void = () => {};
  const sent: { id?: string } = {};
  const fetchFn = vi.fn((_u: RequestInfo | URL, init?: RequestInit) => {
    sent.id = JSON.parse(String(init?.body)).request_id;
    return new Promise<Response>((resolve) => {
      release = (body) => resolve({ ok: true, status: 200, json: async () => body } as Response);
    });
  });
  return { fetchFn, sent, release: (b: unknown) => release(b) };
}

describe("live reasoning and answer (deviation d5)", () => {
  it("shows the reasoning and the answer as they stream, for this request only", async () => {
    const { fetchFn, sent } = heldPost();
    const { rerender } = render(<Chat secret="k" clientOptions={{ fetchFn }} liveText={{}} />);
    await send("why?");
    const id = sent.id as string;
    const live: LiveMap = {
      [id]: { step: 0, reasoning: "Thinking about the sky", content: "Because of scat", earlier: [] },
      other: { step: 0, reasoning: "NOT MINE", content: "", earlier: [] },
    };
    rerender(<Chat secret="k" clientOptions={{ fetchFn }} liveText={live} />);
    expect(screen.getByText("Thinking about the sky")).toHaveAttribute("dir", "auto");
    expect(screen.getByText("Because of scat")).toBeInTheDocument();
    expect(screen.queryByText("NOT MINE")).toBeNull();
  });

  it("keeps the reasoning under the finished answer, folded", async () => {
    const { fetchFn, sent, release } = heldPost();
    const { rerender } = render(<Chat secret="k" clientOptions={{ fetchFn }} liveText={{}} />);
    await send("why?");
    const id = sent.id as string;
    const live: LiveMap = { [id]: { step: 1, reasoning: "then answer", content: "", earlier: ["search first"] } };
    rerender(<Chat secret="k" clientOptions={{ fetchFn }} liveText={live} />);
    release({ reply: "Rayleigh scattering.", sources: [] });
    expect(await screen.findByText(/Rayleigh scattering/)).toBeInTheDocument();
    const details = screen.getByText("Reasoning").closest("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("search first");
    expect(details).toHaveTextContent("then answer");
  });

  it("an answer with no streamed reasoning has no Reasoning fold", async () => {
    const { fetchFn, release } = heldPost();
    render(<Chat secret="k" clientOptions={{ fetchFn }} liveText={{}} />);
    await send("hi");
    release({ reply: "Hello.", sources: [] });
    await screen.findByText(/Hello/);
    expect(screen.queryByText("Reasoning")).toBeNull();
  });
});
