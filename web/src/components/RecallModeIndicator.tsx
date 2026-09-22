import type { EventEnvelope } from "../api/events";

export interface RecallModeIndicatorProps {
  degradations: EventEnvelope<"degradation">[];
}

export type RecallMode = "unknown" | "semantic" | "lexical-fallback";

/**
 * Inferred, not authoritative: the bus schema (schema.json) has no
 * dedicated "recall mode" kind today, so this reads the same degradation
 * stream everything else does. CLAUDE.md's C3 names the known trap
 * directly — "eidetic falls back to lexical recall SILENTLY when the
 * embedder is down ... so the daemon must report the recall mode actually
 * in effect" — this component is the dashboard's best-effort surface of
 * that until a daemon task (t16 or later) emits an explicit event.
 *
 * Round 2 correction: the default used to be "semantic" — that asserted
 * the BETTER state with no evidence for it, which is exactly backwards on
 * this rig, where the embedder is not ready (CLAUDE.md's rig table:
 * `embedder` — "not ready") and recall is lexical-only *today*, silently,
 * unless something reports otherwise. The honest default before any signal
 * has arrived is "unknown", not a guess dressed as a positive claim.
 *
 * "semantic" is reachable only via a POSITIVE signal that the embedder is
 * up — no such event exists in the schema yet, so `inferRecallMode` never
 * returns "semantic" in v1; this is a real state the type keeps room for
 * once a daemon task adds that signal, not a state this build can reach.
 * "lexical-fallback" is the one degraded state this build can detect: the
 * most recent `degradation` event whose `source` is "continuity".
 */
export function inferRecallMode(degradations: EventEnvelope<"degradation">[]): RecallMode {
  const latestContinuity = [...degradations]
    .reverse()
    .find((entry) => entry.data.source === "continuity");
  if (latestContinuity) return "lexical-fallback";
  return "unknown";
}

const LABEL: Record<RecallMode, string> = {
  unknown: "recall: unknown",
  semantic: "recall: semantic",
  "lexical-fallback": "recall: lexical fallback",
};

export function RecallModeIndicator({ degradations }: RecallModeIndicatorProps) {
  const mode = inferRecallMode(degradations);
  return (
    <span
      data-recall-mode={mode}
      title="inferred from the degradation stream, not an authoritative daemon signal"
    >
      {LABEL[mode]}
    </span>
  );
}
