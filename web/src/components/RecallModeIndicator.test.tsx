import { describe, expect, it } from "vitest";
import { inferRecallMode } from "./RecallModeIndicator";
import type { EventEnvelope } from "../api/events";

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
  it("is 'unknown' with no degradations at all -- never asserts the better state with no evidence", () => {
    expect(inferRecallMode([])).toBe("unknown");
  });

  it("is 'unknown' when only unrelated degradations have arrived", () => {
    expect(inferRecallMode([degradation("gateway"), degradation("stt")])).toBe("unknown");
  });

  it("is 'lexical-fallback' once a continuity degradation arrives", () => {
    expect(inferRecallMode([degradation("continuity")])).toBe("lexical-fallback");
  });

  it("uses the MOST RECENT continuity-sourced degradation, not the first", () => {
    const entries = [degradation("continuity"), degradation("gateway"), degradation("stt")];
    // still lexical-fallback: an earlier continuity degradation still counts
    // even if later ones are unrelated -- recall never silently recovers
    // itself without a signal saying so.
    expect(inferRecallMode(entries)).toBe("lexical-fallback");
  });

  it("never returns 'semantic' in v1 -- no positive signal exists yet in the schema", () => {
    const many = Array.from({ length: 50 }, (_, i) => degradation(`source-${i}`));
    expect(inferRecallMode(many)).not.toBe("semantic");
  });
});
