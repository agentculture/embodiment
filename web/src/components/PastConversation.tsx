// PastConversation.tsx
//
// An earlier conversation in the middle of Ask, continued as if it never stopped
// (operator, 2026-10-24; deviation d5): its turns are shown first, and the input
// box sends new questions INTO it -- the daemon reopens it with those turns as
// Gwen's context and appends to its log. Its old answers' sources were not kept
// in the session log, so their citation numbers show as text; new answers cite
// as usual. The live conversation is one click away.

import { useEffect, useState } from "react";
import { fetchConversation, type ConversationSummary, type HistoryClientOptions, type HistoryTurn } from "../api/history";
import { dayLabel } from "../api/kinds";
import { Chat, type ChatProps } from "./Chat";

export interface PastConversationProps {
  conversation: ConversationSummary;
  secret?: string;
  clientOptions?: HistoryClientOptions;
  onBack: () => void;
  /** Passed to the conversation's chat (citations, answers, placeholder). */
  chat?: Pick<ChatProps, "onOpenCitation" | "onAnswer" | "placeholder" | "clientOptions" | "liveText">;
}

export function PastConversation({ conversation, secret = "", clientOptions, onBack, chat }: PastConversationProps) {
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
        <span className="past-note">{day ? `Continuing a conversation from ${day}` : "Continuing an earlier conversation"}</span>
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
      {turns !== null && (
        <Chat
          key={conversation.id}
          secret={secret}
          conversation={conversation.id}
          initialTurns={turns.map((t) => ({ role: t.role, text: t.text }))}
          {...chat}
        />
      )}
    </section>
  );
}
