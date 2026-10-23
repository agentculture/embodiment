// Provenance: ported from pocketsync/web/static/index.html (#thread, #composer) and
// pocketsync/web/static/app.js:336-432 (ask, renderAnswer, sourceList, [n] cite markers,
// revealWords at :189-212) and :790-808 (composer: Enter sends, Shift+Enter newline).
// All text is JSX text nodes; there is no HTML sink.
import { useEffect, useRef, useState } from "react";
import type { EventEnvelope } from "../api/events";
import {
  formatMmSs,
  parseSources,
  sendChat,
  type ChatClientOptions,
  type ChatSource,
} from "../api/chat";
import { AudioControls, SpeakButton, type ClipReply } from "./AudioControls";

export interface ChatProps {
  /** Install secret the page already holds; sent whenever present (Access replaces it on the public Host). */
  secret?: string;
  /** Reply events from the event stream (kind "reply"). */
  replyEvents?: EventEnvelope<"transcript" | "reply">[];
  clientOptions?: ChatClientOptions;
  /** Speech-to-speech role the gateway advertises, if any (daemon must expose it). */
  s2sRole?: string;
  /** Called with a library citation's item id and anchor; never called for a source without item_id. */
  onOpenCitation?: (itemId: string, anchor: unknown) => void;
}

interface Message {
  key: string;
  role: "user" | "assistant";
  text: string;
  sources: ChatSource[];
  followUp?: boolean;
  /** Reveal word by word (a fresh answer, motion allowed). */
  reveal?: boolean;
}

/**
 * Provenance as plain text, branching on the daemon's `source` (never on the
 * shape of `start`): "pocket" is a recording, "newspaper" an issue, "room"
 * Gwen's own memory; anything else gets a neutral label.
 */
function provenance(s: ChatSource): string {
  const parts: string[] = [];
  switch (s.source) {
    case "pocket": {
      if (s.title) parts.push(s.title);
      if (s.date) parts.push(s.date);
      const range = [formatMmSs(s.start), formatMmSs(s.end)].filter(Boolean).join("-");
      if (range) parts.push(range);
      break;
    }
    case "newspaper":
      if (s.date) parts.push(s.date);
      if (s.section) parts.push(s.section);
      if (s.link) parts.push(s.link);
      if (parts.length === 0 && s.title) parts.push(s.title);
      break;
    case "room":
      parts.push("Gwen's memory");
      if (s.date) parts.push(s.date);
      break;
    default:
      parts.push("source");
      if (s.title) parts.push(s.title);
      if (s.date) parts.push(s.date);
  }
  return parts.join(" | ");
}

type OpenCitation = (itemId: string, anchor: unknown) => void;

function openSource(s: ChatSource, onOpen?: OpenCitation) {
  if (s.item_id && onOpen) onOpen(s.item_id, s.anchor);
}

function Sources({ sources, onOpen }: { sources: ChatSource[]; onOpen?: OpenCitation }) {
  if (sources.length === 0) return null;
  return (
    <ul className="chat__sources" aria-label="sources (untrusted)">
      {sources.map((s, i) => {
        const body = (
          <>
            <span className="chat__source-n">{i + 1}</span>
            <span className="chat__source-label" dir="auto">
              [untrusted source] {provenance(s)}
            </span>
            <blockquote className="chat__source-text" dir="auto">
              {s.text}
            </blockquote>
          </>
        );
        return (
          <li key={i} className="chat__source" data-untrusted="true">
            {s.item_id ? (
              <button type="button" className="chat__source-open" onClick={() => openSource(s, onOpen)}>
                {body}
              </button>
            ) : (
              body
            )}
          </li>
        );
      })}
    </ul>
  );
}

function calmMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Words as spans with a staggered delay (pocketchat revealWords); whitespace stays plain text. */
function Words({ text, base }: { text: string; base: number }) {
  const step = Math.min(28, 1400 / Math.max(1, text.split(/\s+/).length));
  let n = base;
  return (
    <>
      {text.split(/(\s+)/).map((part, i) => {
        if (!part) return null;
        if (/^\s+$/.test(part)) return part;
        const delay = Math.round(n * step);
        n += 1;
        return (
          <span key={i} className="chat__word" style={{ animationDelay: `${delay}ms` }}>
            {part}
          </span>
        );
      })}
    </>
  );
}

/** An answer with [n] markers turned into cite buttons; an unknown marker stays text. */
function Answer({
  text,
  sources,
  reveal,
  onOpen,
}: {
  text: string;
  sources: ChatSource[];
  reveal: boolean;
  onOpen?: OpenCitation;
}) {
  let words = 0;
  return (
    <>
      {text.split(/(\[\d+\])/g).map((part, i) => {
        if (!part) return null;
        const m = /^\[(\d+)\]$/.exec(part);
        const src = m ? sources[Number(m[1]) - 1] : undefined;
        if (m && src) {
          return (
            <button
              key={i}
              type="button"
              className="chat__cite"
              aria-label={`Open source ${m[1]}: ${src.title || "source"}`}
              onClick={() => openSource(src, onOpen)}
            >
              {m[1]}
            </button>
          );
        }
        if (!reveal) return part;
        const node = <Words key={i} text={part} base={words} />;
        words += part.split(/\s+/).filter(Boolean).length;
        return node;
      })}
    </>
  );
}

export function Chat({ secret = "", replyEvents = [], clientOptions, s2sRole, onOpenCitation }: ChatProps) {
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [degraded, setDegraded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const baselineSeq = useRef<number | null>(null);
  const typedSerials = useRef<Set<number>>(new Set());
  const seen = useRef<Set<number>>(new Set());

  useEffect(() => {
    if (baselineSeq.current === null) return;
    const base = baselineSeq.current;
    const fresh: Message[] = [];
    for (const ev of replyEvents) {
      if (ev.kind !== "reply" || seen.current.has(ev.seq) || ev.seq <= base) continue;
      const serial = ev.data.turn_serial;
      if (typeof serial !== "number") continue;
      if (typedSerials.current.size > 0 && !typedSerials.current.has(serial)) continue;
      seen.current.add(ev.seq);
      fresh.push({
        key: `ev-${ev.seq}`,
        role: "assistant",
        text: String(ev.data.text ?? ""),
        sources: parseSources(ev.data.sources),
        followUp: true,
      });
    }
    if (fresh.length) setMessages((m) => [...m, ...fresh]);
  }, [replyEvents]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    setBusy(true);
    baselineSeq.current = replyEvents.reduce((mx, e) => Math.max(mx, e.seq), 0);
    setMessages((m) => [...m, { key: `u-${m.length}`, role: "user", text, sources: [] }]);
    const outcome = await sendChat(text, secret, clientOptions);
    if (outcome.ok) {
      setDegraded(null);
      if (outcome.value.turnSerial !== null) typedSerials.current.add(outcome.value.turnSerial);
      setMessages((m) => [
        ...m,
        { key: `a-${m.length}`, role: "assistant", text: outcome.value.reply, sources: outcome.value.sources, reveal: !calmMotion() },
      ]);
    } else {
      // Recorded, visible degradation (C3): a fixed code, never the response body.
      setDegraded(outcome.code);
    }
    setBusy(false);
  }

  function onClipReply({ transcript, reply, turnSerial }: ClipReply) {
    setDegraded(null);
    if (baselineSeq.current === null) {
      baselineSeq.current = replyEvents.reduce((mx, e) => Math.max(mx, e.seq), 0);
    }
    if (turnSerial != null) typedSerials.current.add(turnSerial);
    setMessages((m) => [
      ...m,
      { key: `u-${m.length}`, role: "user", text: transcript, sources: [] },
      { key: `a-${m.length + 1}`, role: "assistant", text: reply, sources: [] },
    ]);
  }

  return (
    <div className="chat">
      <div className="chat__log">
        {messages.map((m) => (
          <div key={m.key} className="chat__message" data-role={m.role} data-follow-up={m.followUp ? "true" : undefined}>
            <p className="chat__text" dir="auto">
              <strong>{m.role}: </strong>
              {m.role === "assistant" ? (
                <Answer text={m.text} sources={m.sources} reveal={!!m.reveal} onOpen={onOpenCitation} />
              ) : (
                m.text
              )}
            </p>
            {m.role === "assistant" && (
              <SpeakButton text={m.text} secret={secret} clientOptions={clientOptions} />
            )}
            <Sources sources={m.sources} onOpen={onOpenCitation} />
          </div>
        ))}
      </div>
      {degraded && (
        <p className="chat__degraded" role="alert">
          chat unavailable: {degraded}
        </p>
      )}
      <form className="chat__form" aria-label="composer" onSubmit={submit}>
        <textarea
          dir="auto"
          rows={1}
          aria-label="message"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <button type="submit" disabled={busy}>
          Send
        </button>
        <AudioControls secret={secret} onReply={onClipReply} clientOptions={clientOptions} s2sRole={s2sRole} />
      </form>
    </div>
  );
}
