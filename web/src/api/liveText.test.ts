import { describe, expect, it } from "vitest";
import { applyStream, LIVE_KEPT, liveReasoning, type LiveMap } from "./liveText";

const ev = (request_id: string, channel: string, text: string, step = 0) => ({ request_id, channel, text, step });

describe("live text (deviation d5)", () => {
  it("keeps each channel's newest cumulative text per request", () => {
    let m: LiveMap = {};
    m = applyStream(m, ev("r1", "reasoning", "Look"));
    m = applyStream(m, ev("r1", "reasoning", "Look it up"));
    m = applyStream(m, ev("r1", "content", "Here"));
    expect(m.r1).toMatchObject({ step: 0, reasoning: "Look it up", content: "Here" });
  });

  it("a new step starts a fresh view and keeps the earlier reasoning", () => {
    let m: LiveMap = {};
    m = applyStream(m, ev("r1", "reasoning", "search first", 0));
    m = applyStream(m, ev("r1", "content", "", 0));
    m = applyStream(m, ev("r1", "reasoning", "now answer", 1));
    expect(m.r1).toMatchObject({ step: 1, reasoning: "now answer", content: "" });
    expect(liveReasoning(m.r1)).toBe("search first\n\nnow answer");
  });

  it("ignores a late event from an earlier step, and junk", () => {
    let m: LiveMap = {};
    m = applyStream(m, ev("r1", "reasoning", "step one", 1));
    m = applyStream(m, ev("r1", "reasoning", "stale", 0));
    expect(m.r1.reasoning).toBe("step one");
    expect(applyStream(m, { request_id: 5, channel: "content", text: "x" })).toBe(m);
    expect(applyStream(m, ev("r1", "tool", "x"))).toBe(m);
  });

  it("is bounded: the oldest requests go first", () => {
    let m: LiveMap = {};
    for (let i = 0; i < LIVE_KEPT + 3; i++) m = applyStream(m, ev(`r${i}`, "content", "x"));
    expect(Object.keys(m)).toHaveLength(LIVE_KEPT);
    expect(m.r0).toBeUndefined();
  });
});
