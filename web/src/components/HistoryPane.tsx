// HistoryPane.tsx
//
// The left pane (operator, 2026-10-24: "chat history should be on the left
// pane"): the conversations Gwen's session logs hold, newest first, grouped by
// day, the live one first as "Now". Picking a past one shows it read-only in the
// middle; the live conversation is where questions go.

import { useEffect, useState } from "react";
import { fetchConversations, type ConversationSummary, type HistoryClientOptions } from "../api/history";
import { dayLabel } from "../api/kinds";

export interface HistoryPaneProps {
  secret?: string;
  clientOptions?: HistoryClientOptions;
  /** The past conversation shown in the middle, or null for the live one. */
  openId: string | null;
  /** Bumped by the page after each answer, so the list refreshes. */
  refreshKey?: number;
  onOpen: (conversation: ConversationSummary | null) => void;
  onClose?: () => void;
  /** End the live conversation and start a new one. */
  onNew?: () => void;
  /** Why the last New conversation was refused, shown under the button. */
  newError?: string | null;
}

function timeOf(ts: number | null): string {
  if (ts === null) return "";
  return new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function dayOf(ts: number | null): string {
  return ts === null ? "" : new Date(ts * 1000).toISOString().slice(0, 10);
}

export function HistoryPane({ secret = "", clientOptions, openId, refreshKey = 0, onOpen, onClose, onNew, newError }: HistoryPaneProps) {
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void fetchConversations(secret, clientOptions).then((r) => {
      if (!live) return;
      if (r.ok) {
        setItems(r.value);
        setError(null);
      } else setError(r.code);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secret, refreshKey]);

  const past = items.filter((c) => !c.current);
  const days = new Map<string, ConversationSummary[]>();
  for (const c of past) days.set(dayOf(c.last), [...(days.get(dayOf(c.last)) ?? []), c]);

  return (
    <nav className="pane history" aria-label="Conversations">
      <div className="pane-head">
        <h2>Conversations</h2>
        {onClose && (
          <button type="button" className="icon-btn" aria-label="Hide conversations" onClick={onClose}>
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path d="M15 5l-7 7 7 7" />
            </svg>
          </button>
        )}
      </div>
      <div className="rows">
        {onNew && (
          <button type="button" className="pill-btn conv-new" onClick={onNew}>
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
            New conversation
          </button>
        )}
        {onNew && newError && (
          <p className="library-error" role="alert">
            A new conversation could not be started ({newError}). Try again.
          </p>
        )}
        <button
          type="button"
          className="row conv conv--now"
          aria-current={openId === null ? "true" : undefined}
          onClick={() => onOpen(null)}
        >
          <span className="conv-dot" aria-hidden="true" />
          <span className="row-title">Now</span>
          <span className="row-side">current</span>
        </button>
        {error && (
          <p className="library-error" role="alert">
            Past conversations could not be loaded ({error}).
          </p>
        )}
        {!error && past.length === 0 && <p className="rows-note">Earlier conversations appear here.</p>}
        {[...days.entries()].map(([day, group]) => (
          <section key={day || "undated"} aria-label={dayLabel(day) || "Undated"}>
            <h3 className="day">{dayLabel(day) || "Undated"}</h3>
            {group.map((c) => (
              <button
                key={c.id}
                type="button"
                className={c.agent ? "row conv conv--agent" : "row conv"}
                aria-current={openId === c.id ? "true" : undefined}
                onClick={() => onOpen(c)}
              >
                {c.agent && <span className="nick">{c.agent}</span>}
                <span className="row-title" dir="auto">
                  {c.title || "Untitled"}
                </span>
                <span className="row-side">{timeOf(c.last)}</span>
              </button>
            ))}
          </section>
        ))}
      </div>
    </nav>
  );
}
