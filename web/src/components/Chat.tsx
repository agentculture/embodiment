import { useEffect, useRef, useState } from "react";
import type { EventEnvelope } from "../api/events";
import {
  formatMmSs,
  isPublicHost,
  parseSources,
  sendChat,
  type ChatClientOptions,
  type ChatSource,
} from "../api/chat";

export interface ChatProps {
  /** Install secret the page already holds; ignored on the public Host. */
  secret?: string;
  /** Reply events from the event stream (kind "reply"). */
  replyEvents?: EventEnvelope<"transcript" | "reply">[];
  /** Test seam: override the location hostname. */
  hostname?: string;
  clientOptions?: ChatClientOptions;
}

interface Message {
  key: string;
  role: "user" | "assistant";
  text: string;
  sources: ChatSource[];
  followUp?: boolean;
}

/** Provenance as plain text: recording (title, date, mm:ss) or issue (date, section, link). */
function provenance(s: ChatSource): string {
  const parts: string[] = [];
  if (s.start !== null) {
    if (s.title) parts.push(s.title);
    if (s.date) parts.push(s.date);
    const range = [formatMmSs(s.start), formatMmSs(s.end)].filter(Boolean).join("-");
    if (range) parts.push(range);
  } else {
    if (s.date) parts.push(s.date);
    if (s.section) parts.push(s.section);
    if (s.link) parts.push(s.link);
    if (parts.length === 0 && s.title) parts.push(s.title);
  }
  return parts.join(" | ");
}

function Sources({ sources }: { sources: ChatSource[] }) {
  if (sources.length === 0) return null;
  return (
    <ul className="chat__sources" aria-label="sources (untrusted)">
      {sources.map((s, i) => (
        <li key={i} className="chat__source" data-untrusted="true">
          <span className="chat__source-label" dir="auto">
            [untrusted source] {provenance(s)}
          </span>
          <blockquote className="chat__source-text" dir="auto">
            {s.text}
          </blockquote>
        </li>
      ))}
    </ul>
  );
}

export function Chat({ secret = "", replyEvents = [], hostname, clientOptions }: ChatProps) {
  const host = hostname ?? (typeof window !== "undefined" ? window.location.hostname : "");
  const publicHost = isPublicHost(host);
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
    const outcome = await sendChat(text, publicHost ? "" : secret, clientOptions);
    if (outcome.ok) {
      setDegraded(null);
      if (outcome.value.turnSerial !== null) typedSerials.current.add(outcome.value.turnSerial);
      setMessages((m) => [
        ...m,
        { key: `a-${m.length}`, role: "assistant", text: outcome.value.reply, sources: outcome.value.sources },
      ]);
    } else {
      // Recorded, visible degradation (C3): a fixed code, never the response body.
      setDegraded(outcome.code);
    }
    setBusy(false);
  }

  return (
    <div className="chat" data-public-host={publicHost ? "true" : "false"}>
      <div className="chat__log">
        {messages.map((m) => (
          <div key={m.key} className="chat__message" data-role={m.role} data-follow-up={m.followUp ? "true" : undefined}>
            <p className="chat__text" dir="auto">
              <strong>{m.role}: </strong>
              {m.text}
            </p>
            <Sources sources={m.sources} />
          </div>
        ))}
      </div>
      {degraded && (
        <p className="chat__degraded" role="alert">
          chat unavailable: {degraded}
        </p>
      )}
      <form className="chat__form" onSubmit={submit}>
        <input
          type="text"
          dir="auto"
          aria-label="message"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" disabled={busy}>
          Send
        </button>
      </form>
    </div>
  );
}
