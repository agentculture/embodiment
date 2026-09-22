import type { EventEnvelope } from "../api/events";

export interface RecallModeIndicatorProps {
  degradations: EventEnvelope<"degradation">[];
}

export type RecallMode = "semantic" | "lexical-fallback";

/**
 * Inferred, not authoritative: the bus schema (schema.json) has no
 * dedicated "recall mode" kind today, so this reads the same degradation
 * stream everything else does. CLAUDE.md's C3 names the known trap
 * directly — "eidetic falls back to lexical recall SILENTLY when the
 * embedder is down ... so the daemon must report the recall mode actually
 * in effect" — this component is the dashboard's best-effort surface of
 * that until a daemon task (t16 or later) emits an explicit event. It
 * looks for the most recent `degradation` whose `source` is "continuity"
 * and treats that as "lexical fallback in effect"; anything else (or no
 * degradation yet) is reported as "semantic", which is honest only insofar
 * as no contradicting signal has arrived — never claimed as a positive
 * confirmation the embedder is up.
 */
export function inferRecallMode(degradations: EventEnvelope<"degradation">[]): RecallMode {
  const latestContinuity = [...degradations]
    .reverse()
    .find((entry) => entry.data.source === "continuity");
  return latestContinuity ? "lexical-fallback" : "semantic";
}

const LABEL: Record<RecallMode, string> = {
  semantic: "recall: semantic",
  "lexical-fallback": "recall: lexical fallback",
};

export function RecallModeIndicator({ degradations }: RecallModeIndicatorProps) {
  const mode = inferRecallMode(degradations);
  return (
    <span data-recall-mode={mode} title="inferred from the degradation stream, not an authoritative daemon signal">
      {LABEL[mode]}
    </span>
  );
}
