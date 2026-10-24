// PastConversation.tsx
//
// One earlier conversation, read-only in the middle of Ask, drawn like the live
// thread. Its sources were not kept in the session log, so citation numbers in
// old answers show as text; the live conversation is one click away.

import { useEffect, useState } from "react";
import { fetchConversation, type ConversationSummary, type HistoryClientOptions, type HistoryTurn } from "../api/history";
import { dayLabel } from "../api/kinds";

export interface PastConversationProps {
  conversation: ConversationSummary;
  secret?: string;
  clientOptions?: HistoryClientOptions;
  onBack: () => void;
}

export function PastConversation({ conversation, secret = "", clientOptions, onBack }: PastConversationProps) {
  const [turns, setTurns] = useState<HistoryTurn[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setTurns(null);
    setError(null);
    void fetchConversation(conversation.id, secret, clientOptions).then((r) => {
      if (!live) return;
      if (r.ok) setTurns(r.value.turns);
      else setError(r.code);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversation.id, secret]);

  const day = conversation.last ? dayLabel(new Date(conversation.last * 1000).toISOString().slice(0, 10)) : "";

  return (
    <section className="past" aria-label="Earlier conversation">
      <div className="past-bar">
        <span className="past-note">{day ? `Earlier conversation, ${day}` : "Earlier conversation"}</span>
        <button type="button" className="pill-btn" onClick={onBack}>
          Back to now
        </button>
      </div>
      {error && (
        <p className="library-error" role="alert">
          This conversation could not be opened ({error}).
        </p>
      )}
      {turns === null && !error && <p className="rows-note">Opening</p>}
      <ol className="thread past-thread" aria-label="Earlier conversation turns">
        {turns?.map((t, i) =>
          t.role === "user" ? (
            <li key={i} className="turn turn--q">
              <span className="sr-only">user: </span>
              <p className="question" dir="auto">
                {t.text}
              </p>
            </li>
          ) : (
            <li key={i} className="turn turn--a">
              <span className="sr-only">assistant: </span>
              <p className="answer" dir="auto">
                {t.text}
              </p>
            </li>
          ),
        )}
      </ol>
    </section>
  );
}
