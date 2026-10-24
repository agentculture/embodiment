import type { EventEnvelope } from "../api/events";

export interface TranscriptProps {
  entries: EventEnvelope<"transcript" | "reply">[];
}

/**
 * Hebrew is the spoken language (CLAUDE.md's "The realtime interface"
 * section): every line sets `dir="auto"` so the browser's bidi algorithm
 * picks the right direction per line from its own first strong character,
 * rather than forcing one document-wide direction that would misrender an
 * English status string next to a Hebrew transcript line.
 */
export function Transcript({ entries: all }: TranscriptProps) {
  // Typed turns are published with `typed: true` (daemon/app.py run_text_turn) and
  // the chat already shows them; this log is the room's spoken conversation only.
  const entries = all.filter((e) => e.data.typed !== true);
  if (entries.length === 0) {
    return <p className="transcript__empty">No speech yet.</p>;
  }
  return (
    <div className="transcript">
      {entries.map((entry, index) => {
        const role = entry.kind === "reply" ? "assistant" : String(entry.data.role ?? "user");
        const text = String(entry.data.text ?? "");
        return (
          <div key={`${entry.kind}-${index}-${entry.seq}`} className="transcript__line" data-role={role}>
            <span className="transcript__who">{role === "assistant" ? "Gwen, spoken" : "Spoken"}</span>
            <span className="sr-only">{role}: </span>
            <p dir="auto">{text}</p>
          </div>
        );
      })}
    </div>
  );
}
