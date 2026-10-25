// Provenance: ported from pocketsync/web/static/index.html (#thread, #composer) and
// pocketsync/web/static/app.js:336-432 (ask, renderAnswer, sourceList, [n] cite markers,
// revealWords at :189-212) and :790-808 (composer: Enter sends, Shift+Enter newline).
// The composer is the voice dock of UI option C (2026-10-24): the mic is its one loud
// control. Citation markers go through api/citations.ts, so "[2, 3]" is two links and
// speech never reads a marker. All text is JSX text nodes; there is no HTML sink.
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { EventEnvelope } from "../api/events";
import {
  formatMmSs,
  parseSources,
  sendChat,
  type ChatClientOptions,
  type ChatSource,
} from "../api/chat";
import { splitCitations, stripCitations } from "../api/citations";
import { kindInfo } from "../api/kinds";
import { AudioControls, SpeakButton, type ClipReply } from "./AudioControls";
import { deviceKind } from "./ReadAloud";

export interface ChatProps {
  /** Install secret the page already holds; sent whenever present (Access replaces it on the public Host). */
  secret?: string;
  /** Reply events from the event stream (kind "reply"). */
  replyEvents?: EventEnvelope<"transcript" | "reply">[];
  clientOptions?: ChatClientOptions;
  /** Speech-to-speech role the gateway advertises, if any; the page's Live control reports it. */
  s2sRole?: string;
  /** Called with a library citation's item id and anchor; never called for a source without item_id. */
  onOpenCitation?: (itemId: string, anchor: unknown) => void;
  /** Shown while the thread is empty; `ask` sends a question as if typed. */
  emptyState?: (ask: (question: string) => void) => ReactNode;
  /** The realtime voice control, drawn beside the dock (not inside the input box). */
  live?: ReactNode;
  /** The text box's placeholder; a phone passes a shorter one. */
  placeholder?: string;
  /** The earlier conversation this chat continues (d5); questions go into it. */
  conversation?: string;
  /** That conversation's earlier turns, shown first (their sources were not kept). */
  initialTurns?: { role: "user" | "assistant"; text: string }[];
  /** Called once per answer with the question it answered and its sources. */
  onAnswer?: (question: string, sources: ChatSource[]) => void;
  /** Start reading a cited library item aloud on this device (embodiment#107). */
  onReadAloud?: (itemId: string) => void;
}

/** The library item an answer can offer to read aloud: its first cited library source. */
export function readableSource(sources: ChatSource[]): ChatSource | null {
  return sources.find((s) => s.source === "library" && !!s.item_id && s.kind !== "todo") ?? null;
}

/** The card under an answer that starts reading its cited item aloud here (mockup107, board 2). */
function ReadCard({ source, onRead }: { source: ChatSource; onRead: (itemId: string) => void }) {
  const kind = sourceKind(source);
  const device = deviceKind();
  return (
    <div className="read-card" role="group" aria-label="Read aloud">
      <span className="cover" aria-hidden="true" style={{ ["--kind" as string]: kind.color }} />
      <b dir="auto">{source.title || "This item"}</b>
      <span dir="auto">{source.section || kind.label}</span>
      <button type="button" className="pill-btn pill-btn--go" onClick={() => onRead(source.item_id as string)}>
        Read aloud on this {device}
      </button>
    </div>
  );
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
 * Gwen's own memory, "library" an item of the library; anything else gets a
 * neutral label.
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
    case "library":
    case "conversation":
      // The kind badge and the date sit beside it; mixing a date into a Hebrew title garbles the bidi.
      parts.push(s.title || "library");
      break;
    case "web":
      // A page an item links to, read by fetch_source: its title, then where it is.
      parts.push(s.title || "web page");
      if (webLink(s)) parts.push(hostOf(webLink(s) as string));
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

function sourceKind(s: ChatSource): { label: string; color: string } {
  if (s.kind) {
    const k = kindInfo(s.kind);
    return { label: k.one, color: k.color };
  }
  if (s.source === "room") return { label: "Memory", color: "var(--k-diary)" };
  if (s.source === "newspaper") return { label: "Newspaper", color: "var(--k-newspaper)" };
  if (s.source === "pocket") return { label: "Recording", color: "var(--k-transcript)" };
  return { label: "Source", color: "var(--k-other)" };
}

type OpenCitation = (itemId: string, anchor: unknown) => void;

/** A URL's host, or "" when it does not parse -- never a throw during render. */
function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
}

/** A fetched source's own address (http/https only, and parseable), or null. */
function webLink(s: ChatSource): string | null {
  return s.source === "web" && /^https?:\/\//i.test(s.link || "") && hostOf(s.link) ? s.link : null;
}

function openSource(s: ChatSource, onOpen?: OpenCitation) {
  const link = webLink(s);
  if (link) {
    window.open(link, "_blank", "noopener,noreferrer");
    return;
  }
  if (s.item_id && onOpen) onOpen(s.item_id, s.anchor);
}

function Sources({ sources, onOpen }: { sources: ChatSource[]; onOpen?: OpenCitation }) {
  if (sources.length === 0) return null;
  return (
    <ol className="sources" aria-label="sources (untrusted)">
      {sources.map((s, i) => {
        const kind = sourceKind(s);
        const body = (
          <>
            <span className="source-n" aria-hidden="true">
              {i + 1}
            </span>
            <span className="source-head">
              <span className="kind-badge" style={{ ["--kind" as string]: kind.color }}>
                {kind.label}
              </span>
              <span className="sr-only">[untrusted source] </span>
              <span className="source-label" dir="auto">
                {provenance(s)}
              </span>
            </span>
            <blockquote className="source-quote" dir="auto">
              {s.text}
            </blockquote>
          </>
        );
        return (
          <li key={i} className="source" data-untrusted="true">
            {webLink(s) ? (
              <a className="source-open" href={webLink(s) as string} target="_blank" rel="noopener noreferrer">
                {body}
              </a>
            ) : s.item_id ? (
              <button type="button" className="source-open" onClick={() => openSource(s, onOpen)}>
                {body}
              </button>
            ) : (
              <div className="source-open">{body}</div>
            )}
          </li>
        );
      })}
    </ol>
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

/** An answer with its citation markers as cite buttons; a marker naming no source stays text. */
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
      {splitCitations(text, sources.length).map((part, i) => {
        if (part.kind === "cite") {
          const src = sources[part.n - 1];
          if (!src) return `[${part.n}]`;
          return (
            <button
              key={i}
              type="button"
              className="cite"
              aria-label={`Open source ${part.n}: ${src.title || "source"}`}
              onClick={() => openSource(src, onOpen)}
            >
              {part.n}
            </button>
          );
        }
        if (!reveal) return part.text;
        const node = <Words key={i} text={part.text} base={words} />;
        words += part.text.split(/\s+/).filter(Boolean).length;
        return node;
      })}
    </>
  );
}

export function Chat({
  secret = "",
  replyEvents = [],
  clientOptions,
  onOpenCitation,
  emptyState,
  live,
  onAnswer,
  placeholder = "Ask about your library",
  conversation,
  initialTurns,
  onReadAloud,
}: ChatProps) {
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<Message[]>(() =>
    (initialTurns ?? []).map((t, i) => ({ key: `h-${i}`, role: t.role, text: t.text, sources: [] })),
  );
  const [degraded, setDegraded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const baselineSeq = useRef<number | null>(null);
  const typedSerials = useRef<Set<number>>(new Set());
  const seen = useRef<Set<number>>(new Set());
  const end = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end", behavior: calmMotion() ? "auto" : "smooth" });
  }, [messages.length, pending]);

  async function ask(raw: string) {
    const text = raw.trim();
    if (!text || busy) return;
    setDraft("");
    setBusy(true);
    setPending(text);
    baselineSeq.current = replyEvents.reduce((mx, e) => Math.max(mx, e.seq), 0);
    setMessages((m) => [...m, { key: `u-${m.length}`, role: "user", text, sources: [] }]);
    const outcome = await sendChat(text, secret, clientOptions, conversation);
    if (outcome.ok) {
      setDegraded(null);
      if (outcome.value.turnSerial !== null) typedSerials.current.add(outcome.value.turnSerial);
      onAnswer?.(text, outcome.value.sources);
      setMessages((m) => [
        ...m,
        {
          key: `a-${m.length}`,
          role: "assistant",
          text: outcome.value.reply,
          sources: outcome.value.sources,
          reveal: !calmMotion(),
        },
      ]);
    } else {
      // Recorded, visible degradation (C3): a fixed code, never the response body.
      setDegraded(outcome.code);
    }
    setPending(null);
    setBusy(false);
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    void ask(draft);
  }

  function onClipReply({ transcript, reply, turnSerial, sources }: ClipReply) {
    setDegraded(null);
    if (baselineSeq.current === null) {
      baselineSeq.current = replyEvents.reduce((mx, e) => Math.max(mx, e.seq), 0);
    }
    if (turnSerial != null) typedSerials.current.add(turnSerial);
    onAnswer?.(transcript, sources ?? []);
    setMessages((m) => [
      ...m,
      { key: `u-${m.length}`, role: "user", text: transcript, sources: [] },
      { key: `a-${m.length + 1}`, role: "assistant", text: reply, sources: sources ?? [] },
    ]);
  }

  const empty = messages.length === 0;

  return (
    <div className="chat" data-empty={empty ? "true" : undefined}>
      {empty && emptyState ? <div className="chat__empty">{emptyState((q) => void ask(q))}</div> : null}
      <ol className="thread" aria-label="Conversation" aria-live="polite">
        {messages.map((m) =>
          m.role === "user" ? (
            <li key={m.key} className="turn turn--q" data-role="user">
              <span className="sr-only">user: </span>
              <p className="question" dir="auto">
                {m.text}
              </p>
            </li>
          ) : (
            <li
              key={m.key}
              className="turn turn--a"
              data-role="assistant"
              data-follow-up={m.followUp ? "true" : undefined}
            >
              <span className="sr-only">assistant: </span>
              <p className="answer" dir="auto">
                <Answer text={m.text} sources={m.sources} reveal={!!m.reveal} onOpen={onOpenCitation} />
              </p>
              <div className="answer-tools">
                <SpeakButton text={stripCitations(m.text, m.sources.length)} secret={secret} clientOptions={clientOptions} />
                {m.sources.length > 0 && (
                  <span className="answer-count">
                    {m.sources.length === 1 ? "1 source" : `${m.sources.length} sources`}
                  </span>
                )}
              </div>
              <Sources sources={m.sources} onOpen={onOpenCitation} />
              {onReadAloud && readableSource(m.sources) && (
                <ReadCard source={readableSource(m.sources) as ChatSource} onRead={onReadAloud} />
              )}
            </li>
          ),
        )}
        {pending && (
          <li className="turn turn--a turn--pending" aria-label="Gwen is answering">
            <p className="answer answer--pending">Reading your library</p>
          </li>
        )}
      </ol>
      <div ref={end} />
      <div className="dock-wrap">
        {degraded && (
          <p className="chat__degraded" role="alert">
            Gwen could not answer ({degraded}). Try again in a moment.
          </p>
        )}
        <form className="dock-line" aria-label="composer" onSubmit={submit}>
          <AudioControls
            secret={secret}
            onReply={onClipReply}
            clientOptions={clientOptions}
            live={live}
            conversation={conversation}
          >
            <label className="sr-only" htmlFor="ask-box">
              Question
            </label>
            <textarea
              id="ask-box"
              className="dock__input"
              dir="auto"
              rows={1}
              aria-label="message"
              placeholder={placeholder}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  e.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <button type="submit" className="dock__send" disabled={busy || !draft.trim()} aria-label="Send">
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <path d="M5 12h13M13 6l6 6-6 6" />
              </svg>
            </button>
          </AudioControls>
        </form>
      </div>
    </div>
  );
}
