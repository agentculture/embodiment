import type { EventEnvelope } from "../api/events";

export interface DegradationsLogProps {
  entries: EventEnvelope<"degradation">[];
}

/**
 * degradation.code/reason never carry speech, secrets or raw
 * attacker-controlled ids (embodiment/bus.py's own contract, mirrored in
 * schema.json's note) — this component renders them as opaque tokens, and
 * intentionally does NOT interpret or reformat `reason` beyond plain text
 * rendering (React escapes it; no dangerouslySetInnerHTML anywhere here).
 */
export function DegradationsLog({ entries }: DegradationsLogProps) {
  if (entries.length === 0) {
    return <p className="transcript__empty">No degradations recorded.</p>;
  }
  return (
    <ul className="degradations">
      {entries.map((entry, index) => (
        <li key={`${index}-${entry.seq}`} className="degradation-row">
          [{String(entry.data.source ?? "?")}] {String(entry.data.code ?? "?")}:{" "}
          {String(entry.data.reason ?? "")}
        </li>
      ))}
    </ul>
  );
}
