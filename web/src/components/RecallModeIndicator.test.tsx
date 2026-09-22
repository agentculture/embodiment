import { describe, expect, it } from "vitest";
import { inferRecallMode } from "./RecallModeIndicator";
import type { EventEnvelope } from "../api/events";
import degradationMemoryFixture from "../fixtures/daemon/degradation-memory.json";

function degradation(source: string): EventEnvelope<"degradation"> {
  return {
    v: 1,
    kind: "degradation",
    ts: "2026-09-22T12:00:00.000Z",
    seq: 1,
    source: "app://embodiment",
    data: { source, code: "x", reason: "y" },
  };
}

describe("inferRecallMode", () => {
  it("is 'unknown' with no degradations and no seeded mode -- never asserts the better state with no evidence", () => {
    expect(inferRecallMode([])).toBe("unknown");
    expect(inferRecallMode([], null)).toBe("unknown");
  });

  it("is 'unknown' when only unrelated degradations have arrived", () => {
    expect(inferRecallMode([degradation("gateway"), degradation("stt")])).toBe("unknown");
  });

  // Round 5: the real daemon (embodiment/daemon/app.py) folds recall
  // degradations with source="memory", never "continuity" -- verified by
  // reading `self._fold("memory", degradation)` directly, not assumed from
  // the committed bus fixture (which uses "continuity" as an unrelated
  // example value).
  it("is 'lexical-fallback' once a memory-sourced degradation arrives", () => {
    expect(inferRecallMode([degradation("memory")])).toBe("lexical-fallback");
  });

  it("is 'lexical-fallback' against the real degradation-memory.json fixture (reconstructed from the daemon source)", () => {
    expect(inferRecallMode([degradationMemoryFixture as EventEnvelope<"degradation">])).toBe(
      "lexical-fallback",
    );
  });

  it("does NOT treat a 'continuity'-sourced degradation as recall-related (that was the fixture's value, not the daemon's)", () => {
    expect(inferRecallMode([degradation("continuity")])).toBe("unknown");
  });

  it("uses the MOST RECENT memory-sourced degradation, not just any", () => {
    const entries = [degradation("memory"), degradation("gateway"), degradation("stt")];
    // still lexical-fallback: an earlier memory degradation still counts
    // even if later ones are unrelated -- recall never silently recovers
    // itself without a signal saying so.
    expect(inferRecallMode(entries)).toBe("lexical-fallback");
  });

  // Round 5: seededMode is a REAL daemon-reported value
  // (status()["recall"]["mode"]), not inferred.
  it("reflects a seeded 'lexical' mode with no degradations", () => {
    expect(inferRecallMode([], "lexical")).toBe("lexical");
  });

  it("reflects a seeded 'semantic' mode with no degradations", () => {
    expect(inferRecallMode([], "semantic")).toBe("semantic");
  });

  it("is 'unknown' for a seeded mode the daemon doesn't actually use (defensive, not guessed)", () => {
    expect(inferRecallMode([], "quantum")).toBe("unknown");
  });

  it("a live memory degradation overrides an already-seeded mode -- fresher evidence wins", () => {
    expect(inferRecallMode([degradation("memory")], "semantic")).toBe("lexical-fallback");
  });
});
