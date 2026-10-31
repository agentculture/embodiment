import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { EventEnvelope } from "../api/events";
import { MeshCards } from "./MeshCards";

let seq = 0;
const ev = (data: Record<string, unknown>): EventEnvelope<"mesh"> => ({
  v: 1, kind: "mesh", ts: "t", seq: ++seq, source: "daemon",
  data: { conversation_id: "c1", at: 100, ...data } as EventEnvelope<"mesh">["data"],
});
const ask = ev({ event: "ask_posted", ask_id: "a1", channel: "#general", text: "Has anyone served it?", expires_at: 400 });

describe("MeshCards", () => {
  it("renders a waiting ask with the question, channel and the only-this-went-out line", () => {
    render(<MeshCards events={[ask]} conversationId={null} now={142} />);
    const card = screen.getByLabelText("Question asked on Culture chat");
    expect(within(card).getByText(/waiting/)).toHaveTextContent("0:42");
    expect(within(card).getByText("#general")).toHaveClass("nick");
    expect(within(card).getByText("Has anyone served it?")).toHaveAttribute("dir", "auto");
    expect(within(card).getByText(/Only this question went out/)).toBeInTheDocument();
  });
  it("marks expired and answered asks", () => {
    const { rerender } = render(<MeshCards events={[ask, ev({ event: "ask_expired", ask_id: "a1" })]} conversationId={null} now={500} />);
    expect(screen.getByText(/expired/i)).toBeInTheDocument();
    rerender(<MeshCards events={[ask, ev({ event: "answer_arrived", ask_id: "a1", nick: "thor", text: "ok", at: 110 })]} conversationId={null} now={500} />);
    expect(screen.getByLabelText("Question asked on Culture chat")).toHaveTextContent(/answered/i);
  });
  it("renders the answer as quoted plain text, marked as another agent's words", () => {
    const answer = ev({ event: "answer_arrived", ask_id: "a1", nick: "thor-lobes", text: "<img src=x onerror=alert(1)>", late: true, after_expiry: true, rule: "fallback", ambiguous: true, at: 290 });
    const { container } = render(<MeshCards events={[ask, answer]} conversationId={null} now={500} />);
    const card = screen.getByLabelText("Answer from Culture chat");
    expect(within(card).getByText("thor-lobes")).toHaveClass("nick");
    expect(card).toHaveTextContent("3 min 10 s later");
    expect(card).toHaveTextContent(/late/i);
    expect(card).toHaveTextContent(/after expiry/i);
    expect(card).toHaveTextContent(/ambiguous/i);
    expect(within(card).getByText("<img src=x onerror=alert(1)>").tagName).toBe("BLOCKQUOTE");
    expect(container.querySelector("img")).toBeNull();
    expect(within(card).getByText(/Another agent's words, quoted as data/)).toBeInTheDocument();
  });
  it("renders an agent-contacted card", () => {
    render(<MeshCards events={[ev({ event: "agent_contacted", nick: "ada", channel: "#lobes", direct: false, text: "ping" })]} conversationId={null} now={0} />);
    const card = screen.getByLabelText("Agent contacted Gwen");
    expect(within(card).getByText("ada")).toHaveClass("nick");
    expect(within(card).getByText("ping")).toHaveAttribute("dir", "auto");
    expect(card).toHaveTextContent(/another agent's words/i);
  });
  it("shows only the reopened conversation's cards", () => {
    render(<MeshCards events={[ask, ev({ event: "agent_contacted", nick: "ada", conversation_id: "other", text: "x" })]} conversationId="c1" now={0} />);
    expect(screen.queryByLabelText("Agent contacted Gwen")).toBeNull();
    expect(screen.getByLabelText("Question asked on Culture chat")).toBeInTheDocument();
  });
  it("renders nothing without events", () => {
    const { container } = render(<MeshCards events={[]} conversationId={null} now={0} />);
    expect(container).toBeEmptyDOMElement();
  });
});
