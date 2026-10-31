// components/MeshCards.tsx
//
// The mesh cards of mockup v2 (gwen-pm t17): the ask Gwen posted on Culture chat
// (waiting / answered / expired), the answer that came back, and an agent that
// contacted her. Another agent's words are UNTRUSTED: they render as plain text in a
// blockquote with dir="auto" (never HTML), the labels stay outside the dir=auto span.
import { useEffect, useState } from "react";
import type { EventEnvelope } from "../api/events";
import { meshCardsFor, type AnswerCard, type AskCard, type ContactCard } from "../api/mesh";

export interface MeshCardsProps {
  events: EventEnvelope<"mesh">[];
  /** The reopened conversation, or null for the live thread. */
  conversationId: string | null;
  /** Epoch seconds, for the waiting clock; defaults to the real clock, ticking. */
  now?: number;
}

function clock(s: number): string {
  const t = Math.max(0, Math.floor(s));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

function later(s: number): string {
  const t = Math.max(0, Math.round(s));
  return t < 60 ? `${t} s later` : `${Math.floor(t / 60)} min ${t % 60} s later`;
}

function Where({ channel, direct }: { channel: string | null; direct?: boolean }) {
  return channel ? <span className="nick">{channel}</span> : direct ? <span>direct message</span> : null;
}

function Ask({ card, now }: { card: AskCard; now: number }) {
  return (
    <div className="mesh" aria-label="Question asked on Culture chat" data-state={card.state}>
      <div className="mesh__head">
        <b>Asked on Culture chat</b>
        <Where channel={card.channel} />
        {card.state === "waiting" && <span className="pending">waiting · {clock(now - card.at)}</span>}
        {card.state === "answered" && <span className="arrived">answered</span>}
        {card.state === "expired" && <span className="expired">expired, no answer</span>}
      </div>
      <blockquote dir="auto">{card.question}</blockquote>
      <small>Only this question went out: no recording, transcript, memory or library text.</small>
    </div>
  );
}

function Answer({ card }: { card: AnswerCard }) {
  return (
    <div className="mesh" aria-label="Answer from Culture chat">
      <div className="mesh__head">
        <b>Answer from Culture chat</b>
        <span className="nick">{card.nick}</span>
        {card.afterS !== null && <span className="arrived">{later(card.afterS)}</span>}
        {card.late && <span className="mesh__mark">late</span>}
        {card.afterExpiry && <span className="mesh__mark">after expiry</span>}
        {card.ambiguous && <span className="mesh__mark">ambiguous: several asks were open</span>}
      </div>
      <blockquote dir="auto">{card.text}</blockquote>
      <small>Another agent&apos;s words, quoted as data.</small>
    </div>
  );
}

function Contact({ card }: { card: ContactCard }) {
  return (
    <div className="mesh" aria-label="Agent contacted Gwen">
      <div className="mesh__head">
        <b>Agent contacted Gwen</b>
        <span className="nick">{card.nick}</span>
        <Where channel={card.channel} direct={card.direct} />
      </div>
      <blockquote dir="auto">{card.text}</blockquote>
      <small>Another agent&apos;s words, quoted as data.</small>
    </div>
  );
}

export function MeshCards({ events, conversationId, now }: MeshCardsProps) {
  const cards = meshCardsFor(events, conversationId);
  const waiting = cards.some((c) => c.type === "ask" && c.state === "waiting");
  const [tick, setTick] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!waiting || now !== undefined) return;
    const id = window.setInterval(() => setTick(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, [waiting, now]);
  if (cards.length === 0) return null;
  const clockNow = now ?? tick;
  return (
    <div className="mesh-cards">
      {cards.map((c) =>
        c.type === "ask" ? <Ask key={c.key} card={c} now={clockNow} /> :
        c.type === "answer" ? <Answer key={c.key} card={c} /> :
        <Contact key={c.key} card={c} />,
      )}
    </div>
  );
}
