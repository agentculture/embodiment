import type { EventEnvelope } from "../api/events";

export interface RecallModeIndicatorProps {
  degradations: EventEnvelope<"degradation">[];
  /** `status()["recall"]["mode"]`, seeded from `GET /api/status` on
   *  connect (round 5) -- `null` before the daemon has ever completed a
   *  recall call this process, `"lexical"` or `"semantic"` after. This is
   *  a REAL daemon-reported value, not inferred. */
  seededMode?: string | null;
}

export type RecallMode = "unknown" | "semantic" | "lexical" | "lexical-fallback";

/**
 * Round 5 correction: the live daemon (`embodiment/daemon/app.py`, read
 * directly, not assumed) folds recall-related degradations with
 * `source="memory"` (`self._fold("memory", degradation)`), never
 * `"continuity"` — the committed bus fixture (`tests/fixtures/events/
 * degradation.json`, t13's own example) happens to use `"continuity"` as
 * its sample value, which is a valid source for OTHER kinds of
 * degradation but not what this daemon's memory/recall path actually
 * publishes. `inferRecallMode` now matches `source === "memory"`.
 *
 * `seededMode` (new in round 5) is the first REAL, non-inferred signal
 * this indicator has ever had: `status()["recall"]["mode"]`, read once
 * per connect via `GET /api/status` (`useEventStream.ts`'s
 * `refreshStatus`). Before that seed lands, or when the daemon has never
 * completed a recall call yet, the honest state is still "unknown" — this
 * is NOT the round-2 mistake of defaulting to "semantic" with no
 * evidence; `seededMode` is either a real reported value or `null`, never
 * guessed.
 *
 * A live "lexical-fallback" signal (a `memory`-sourced degradation) always
 * wins over the seeded mode: a degradation is fresher, stronger evidence
 * that something is currently wrong than a one-time snapshot taken at
 * connect time.
 */
export function inferRecallMode(
  degradations: EventEnvelope<"degradation">[],
  seededMode: string | null = null,
): RecallMode {
  const latestMemoryDegradation = [...degradations]
    .reverse()
    .find((entry) => entry.data.source === "memory");
  if (latestMemoryDegradation) return "lexical-fallback";
  if (seededMode === "semantic") return "semantic";
  if (seededMode === "lexical") return "lexical";
  return "unknown";
}

const LABEL: Record<RecallMode, string> = {
  unknown: "recall: unknown",
  semantic: "recall: semantic",
  lexical: "recall: lexical",
  "lexical-fallback": "recall: lexical fallback",
};

export function RecallModeIndicator({ degradations, seededMode = null }: RecallModeIndicatorProps) {
  const mode = inferRecallMode(degradations, seededMode);
  return (
    <span
      data-recall-mode={mode}
      title="seeded from GET /api/status on connect; refined live by memory-sourced degradations"
    >
      {LABEL[mode]}
    </span>
  );
}
