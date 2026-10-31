import { describe, expect, it } from "vitest";
import type { EventEnvelope } from "./events";
import { foldMesh, openAskCount, meshCardsFor } from "./mesh";

let seq = 0;
const ev = (data: Record<string, unknown>): EventEnvelope<"mesh"> => ({
  v: 1, kind: "mesh", ts: "2026-10-31T10:00:00Z", seq: ++seq, source: "daemon",
  data: { conversation_id: "c1", at: 100, ...data } as EventEnvelope<"mesh">["data"],
});
const posted = (id = "a1", over = {}) => ev({ event: "ask_posted", ask_id: id, channel: "#general", thread: "t", text: "q?", expires_at: 400, ...over });

describe("foldMesh", () => {
  it("an ask with no answer is waiting", () => {
    const [c] = foldMesh([posted()]);
    expect(c).toMatchObject({ type: "ask", state: "waiting", askId: "a1", question: "q?", channel: "#general" });
  });
  it("an answer makes it answered, with late and after-expiry marks", () => {
    const cards = foldMesh([posted(), ev({ event: "answer_arrived", ask_id: "a1", nick: "thor-lobes", text: "no", late: true, after_expiry: true, at: 110 })]);
    expect(cards.map((c) => c.type)).toEqual(["ask", "answer"]);
    expect(cards[0]).toMatchObject({ state: "answered" });
    expect(cards[1]).toMatchObject({ type: "answer", nick: "thor-lobes", text: "no", late: true, afterExpiry: true, ambiguous: false });
  });
  it("ambiguous only when the fallback rule matched and ambiguous is true", () => {
    const a = foldMesh([posted(), ev({ event: "answer_arrived", ask_id: "a1", nick: "n", text: "x", rule: "fallback", ambiguous: true })]);
    expect(a[1]).toMatchObject({ ambiguous: true });
    const b = foldMesh([posted(), ev({ event: "answer_arrived", ask_id: "a1", nick: "n", text: "x", rule: "thread", ambiguous: true })]);
    expect(b[1]).toMatchObject({ ambiguous: false });
  });
  it("expiry marks a waiting ask expired, and a late answer still shows answered", () => {
    expect(foldMesh([posted(), ev({ event: "ask_expired", ask_id: "a1" })])[0]).toMatchObject({ state: "expired" });
    const cards = foldMesh([posted(), ev({ event: "ask_expired", ask_id: "a1" }), ev({ event: "answer_arrived", ask_id: "a1", nick: "n", text: "x", after_expiry: true })]);
    expect(cards[0]).toMatchObject({ state: "answered" });
  });
  it("agent_contacted becomes a card; gwen_replied does not", () => {
    const cards = foldMesh([ev({ event: "agent_contacted", nick: "ada", channel: null, direct: true, text: "hi" }), ev({ event: "gwen_replied", nick: "ada", text: "yo", sent: true })]);
    expect(cards).toEqual([expect.objectContaining({ type: "contact", nick: "ada", direct: true, text: "hi" })]);
  });
  it("an answer with no known ask still renders", () => {
    expect(foldMesh([ev({ event: "answer_arrived", ask_id: "zz", nick: "n", text: "x" })])).toHaveLength(1);
  });
});

describe("selection and count", () => {
  const events = [
    posted("a1"), posted("a2", { conversation_id: "c2" }),
    ev({ event: "answer_arrived", ask_id: "a1", nick: "n", text: "x" }),
    ev({ event: "agent_contacted", nick: "ada", conversation_id: "agent-1", text: "hi" }),
  ];
  it("counts open asks", () => {
    expect(openAskCount(events)).toBe(1);
  });
  it("a reopened conversation gets its own cards", () => {
    expect(meshCardsFor(events, "c2").map((c) => c.type)).toEqual(["ask"]);
    expect(meshCardsFor(events, "agent-1").map((c) => c.type)).toEqual(["contact"]);
  });
  it("the live view leaves out other agents' conversations but keeps contacts", () => {
    const live = meshCardsFor(events, null);
    expect(live.map((c) => c.type).sort()).toEqual(["answer", "ask", "ask", "contact"]);
  });
});
