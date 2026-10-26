// Reader.tsx
//
// Library reader: title, meta, passages, and a tape when passages are timed.
// Ported (cite-don't-import) from pocketsync, the reader's open/render/
// highlight/seek logic at pocketsync/web/static/app.js:461-626 and its markup
// at pocketsync/web/static/index.html:110-145. Differences: passages come from
// GET /api/library/{id} (anchor-addressed, times optional), summary/todos/
// re-enrich are dropped, and every string is a text node with dir="auto".
// Layout: side panel >= 861px, full-screen sheet below (styles/library.css).
// Listen mode (embodiment#107, t4): ReadAloud.tsx plays the item on this device;
// the passage being spoken is marked, heard ones recede, and the map turns into
// one span per section while a read is on.

import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { fetchItem, type LibraryClientOptions, type LibraryDocument, type Passage } from "../api/library";
import { dayLabel, kindInfo } from "../api/kinds";
import type { ReadingUnit, SavedPosition } from "../api/reading";
import { deviceKind, neighbour, playedOf, progressOf, ReadAloudDock, useReadAloud, type AudioFactory, type SectionChoice } from "./ReadAloud";
import "../styles/library.css";

export interface ReaderHandle {
  openAt: (anchor: string) => void;
  /** Fetch the item again (a failed open, or a re-click on the open item). */
  reload: () => void;
}

/** What a failed open says; "not found" mid re-index means "not ready yet". */
function failureText(code: string): string {
  if (code === "library-item-gone") return "This item is no longer in the library.";
  if (code === "library-item-not-found") {
    return "This item is not ready to read yet: Gwen is still indexing it. Try again in a moment.";
  }
  return `This item could not be opened (${code}).`;
}

export interface ReaderProps {
  id: string;
  secret?: string;
  /** Passage anchor to scroll to and highlight once loaded. */
  anchor?: string;
  /** "side" beside the conversation (default), "page" as the main pane of the library. */
  variant?: "side" | "page";
  clientOptions?: LibraryClientOptions;
  onClose: () => void;
  /** Start reading aloud as soon as the reader opens (a Read aloud card or a Continue chip). */
  listen?: boolean;
  /** This item's saved reading position, if any: the start button offers to continue there. */
  savedPosition?: SavedPosition | null;
  /** Tests inject a fake Audio. */
  audioFactory?: AudioFactory;
}

const norm = (t: string) => t.replace(/\s+/g, " ").trim();

/** A reader anchor (`s<section>.p<paragraph>`, the daemon's paragraph_anchor) as a reading
 * position, or null for any other anchor. */
export function positionOf(anchorOf: string): { section: number; paragraph: number } | null {
  const m = /^s(\d+)\.p(\d+)$/.exec(anchorOf);
  return m ? { section: Number(m[1]), paragraph: Number(m[2]) } : null;
}

/** The passage that holds the spoken text: equal, or containing it (a part of a long paragraph). */
function matchPassage(passages: Passage[], text: string, from: number): number {
  const want = norm(text);
  if (!want) return -1;
  const order = [...passages.keys()].slice(Math.max(0, from)).concat([...passages.keys()].slice(0, Math.max(0, from)));
  for (const i of order) if (norm(passages[i].text) === want) return i;
  for (const i of order) if (norm(passages[i].text).includes(want)) return i;
  return -1;
}

function calmMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function totalSeconds(passages: Passage[]): number {
  let total = 0;
  for (const p of passages) total = Math.max(total, p.endS ?? p.startS ?? 0);
  return total;
}

const isTimed = (p: Passage): boolean => p.startS !== null;

/** The passage a click at `at` seconds lands on: the last one starting at or before it, else the first. */
function passageAt(passages: Passage[], at: number): Passage | undefined {
  const timed = passages.filter(isTimed);
  let best: Passage | undefined;
  for (const p of timed) if ((p.startS as number) <= at) best = p;
  return best ?? timed[0];
}

export const Reader = forwardRef<ReaderHandle, ReaderProps>(function Reader(
  { id, secret = "", anchor, clientOptions, onClose, variant = "side", listen, savedPosition, audioFactory },
  ref,
) {
  const [doc, setDoc] = useState<LibraryDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cited, setCited] = useState<string | null>(anchor ?? null);
  const [attempt, setAttempt] = useState(0);
  const nodes = useRef(new Map<string, HTMLLIElement>());
  const docRef = useRef<LibraryDocument | null>(null);
  docRef.current = doc;
  // The audio made ahead (d6) comes from this reader's own copy of the item, never via
  // next?section=&paragraph= (under d1 that is a seek that re-pins the cursor). "After" is
  // what plays once this unit is heard (unknown mid-paragraph, when the next part follows);
  // "before" is what Previous plays. Both use the buttons' rule, so section edges are covered.
  const textAt = useCallback((at: { section: number; paragraph: number } | null): string | null => {
    const d = docRef.current;
    if (!d || !at) return null;
    const p = d.passages.find((x) => {
      const pos = positionOf(x.anchor);
      return pos !== null && pos.section === at.section && pos.paragraph === at.paragraph;
    });
    return p ? p.text : null;
  }, []);
  const peekFollowing = useCallback(
    (u: ReadingUnit): string | null => (u.part + 1 < u.partCount ? null : textAt(neighbour(u, 1))),
    [textAt],
  );
  const peekPreceding = useCallback(
    (u: ReadingUnit): string | null => {
      const at = neighbour(u, -1);
      // at the item's first paragraph Previous plays the same one: nothing to make ahead
      return at && (at.section !== u.section || at.paragraph !== u.paragraph) ? textAt(at) : null;
    },
    [textAt],
  );
  const [reading, controls] = useReadAloud({ itemId: id, secret, clientOptions, audioFactory, peekFollowing, peekPreceding });
  const device = deviceKind();
  // The dock's Choose section list, from this reader's own copy: each section in order,
  // named by its first heading, else by its number.
  const sections = useMemo((): SectionChoice[] => {
    const out: SectionChoice[] = [];
    for (const p of doc?.passages ?? []) {
      const pos = positionOf(p.anchor);
      if (!pos) continue;
      const last = out[out.length - 1];
      if (last?.section === pos.section) {
        if (!last.label && p.heading) last.label = p.heading;
      } else out.push({ section: pos.section, label: p.heading });
    }
    return out.map((s) => ({ ...s, label: s.label || `Section ${s.section + 1}` }));
  }, [doc]);

  useEffect(() => {
    let live = true;
    setDoc(null);
    setError(null);
    void fetchItem(id, secret, clientOptions).then((r) => {
      if (!live) return;
      if (r.ok) setDoc(r.value);
      else setError(r.code);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, secret, attempt]);

  useEffect(() => {
    if (anchor) setCited(anchor);
  }, [anchor]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (!cited || !doc) return;
    nodes.current.get(cited)?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [cited, doc]);

  // A Read aloud card or a Continue chip opened this reader: start at the cursor.
  useEffect(() => {
    if (listen) controls.start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, listen]);

  const lastMatch = useRef(-1);
  const unit = reading.unit;
  const listening = reading.status !== "idle" && reading.status !== "gone";
  const speakingIndex = doc && unit && listening ? matchPassage(doc.passages, unit.text, lastMatch.current) : -1;
  if (speakingIndex >= 0) lastMatch.current = speakingIndex;
  const live = reading.status === "playing" || reading.status === "loading";
  const prog = unit ? progressOf(unit, playedOf(reading)) : null;
  const finished = reading.status === "done";

  useEffect(() => {
    if (speakingIndex < 0 || !doc) return;
    const anchorOf = doc.passages[speakingIndex]?.anchor;
    const node = anchorOf ? nodes.current.get(anchorOf) : undefined;
    node?.scrollIntoView?.({ block: "center", behavior: calmMotion() ? "auto" : "smooth" });
  }, [speakingIndex, doc]);

  useImperativeHandle(
    ref,
    () => ({ openAt: (a: string) => setCited(a), reload: () => setAttempt((n) => n + 1) }),
    [],
  );

  const seek = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      if (!doc) return;
      const total = totalSeconds(doc.passages);
      const box = event.currentTarget.getBoundingClientRect();
      if (!total || !box.width) return;
      const target = passageAt(doc.passages, ((event.clientX - box.left) / box.width) * total);
      if (target) setCited(target.anchor);
    },
    [doc],
  );

  // d3 (live t9): tap a paragraph to hear it from there. A seek, so the cursor follows (d1).
  const listenFrom = useCallback(
    (anchorOf: string) => {
      const at = positionOf(anchorOf);
      if (!at) return;
      const picked = window.getSelection?.()?.toString() ?? "";
      if (picked.trim()) return; // selecting text is not a tap
      controls.start(at);
    },
    [controls],
  );

  const total = doc ? totalSeconds(doc.passages) : 0;
  const timed = doc ? doc.passages.filter(isTimed) : [];
  const kind = doc ? kindInfo(doc.item.kind) : null;
  const when = doc ? dayLabel(doc.item.createdAt) : "";
  const citedIndex = doc && cited ? doc.passages.findIndex((p) => p.anchor === cited) : -1;

  return (
    <aside className={`reader reader--${variant}`} aria-label="Reader">
      <div className="reader-head">
        <div className="reader-meta">
          {kind && (
            <span className="kind-badge" style={{ ["--kind" as string]: kind.color }}>
              {kind.one}
            </span>
          )}
          {when && <span>{when}</span>}
          {doc?.item.author && <span dir="auto">from {doc.item.author}</span>}
          {citedIndex >= 0 && doc && (
            <span>
              passage {citedIndex + 1} of {doc.passages.length}
            </span>
          )}
        </div>
        <button type="button" className="reader-close icon-btn" aria-label="Close" onClick={onClose}>
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
        <h2 dir="auto" className="reader-title">
          {doc ? doc.item.title : error ? "Not available" : "Opening"}
        </h2>
        {unit && prog && listening ? (
          <div className="reader-map reader-map--sections" aria-hidden="true">
            {Array.from({ length: unit.sectionCount }, (_, i) =>
              i < unit.section || finished ? (
                <span key={i} className="heard" data-share="1" />
              ) : i === unit.section ? (
                <span key={i} className="now" data-share={String(prog.section)} style={{ ["--share" as string]: prog.section }} />
              ) : (
                <span key={i} />
              ),
            )}
          </div>
        ) : doc && doc.passages.length > 1 && (
          <div className="reader-map" aria-hidden="true">
            {doc.passages.map((p) => (
              <span key={p.anchor} className={p.anchor === cited ? "hit" : undefined} />
            ))}
          </div>
        )}
        {timed.length > 0 && total > 0 && (
          <div className="tape" title="Jump to a moment" onClick={seek}>
            {timed.map((p) => (
              <div
                key={p.anchor}
                className={p.anchor === cited ? "turnmark hit" : "turnmark"}
                style={{
                  insetInlineStart: `${((p.startS as number) / total) * 100}%`,
                  inlineSize: `${Math.max((((p.endS ?? p.startS) as number) - (p.startS as number)) / total * 100, 0.25)}%`,
                }}
              />
            ))}
          </div>
        )}
        {doc && (reading.status === "idle" || reading.status === "stopped") && (
          <div className="listen-start">
            {reading.status === "stopped" || (savedPosition && !savedPosition.gone) ? (
              <>
                <button type="button" className="pill-btn pill-btn--go" onClick={() => controls.start()}>
                  {reading.status === "stopped" && unit
                    ? `Continue listening: section ${unit.section + 1}, paragraph ${unit.paragraph + 1}`
                    : `Continue listening: section ${(savedPosition?.section ?? 0) + 1}, paragraph ${(savedPosition?.paragraph ?? 0) + 1}`}
                </button>
                <button type="button" className="pill-btn" onClick={() => controls.start({ section: 0, paragraph: 0 })}>
                  From the start
                </button>
              </>
            ) : (
              <button type="button" className="pill-btn pill-btn--go" onClick={() => controls.start()}>
                Read aloud on this {device}
              </button>
            )}
            <span className="listen-note" dir="auto">Gwen reads the text as written, one paragraph at a time.</span>
          </div>
        )}
      </div>
      <div className="reader-body">
        {error && (
          <div className="library-error" role="alert">
            <p>{failureText(error)}</p>
            <button type="button" className="pill-btn" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </div>
        )}
        <ol className="transcript">
          {doc?.passages.map((p, i) => {
            // past the end, the last paragraph spoken is heard like the ones before it
            const speaking = i === speakingIndex && !finished;
            const heard =
              unit !== null && speakingIndex >= 0 && i <= speakingIndex && i >= speakingIndex - unit.paragraph && (i < speakingIndex || finished);
            const listenClass = speaking ? (live ? " speaking" : " stopped") : heard ? " heard" : "";
            return (
            <li
              key={p.anchor}
              className={(p.anchor === cited && !speaking ? "seg cited" : "seg") + listenClass}
              aria-current={speaking || p.anchor === cited ? "true" : undefined}
              tabIndex={positionOf(p.anchor) ? 0 : undefined}
              title={positionOf(p.anchor) ? "Listen from here" : undefined}
              onClick={() => listenFrom(p.anchor)}
              onKeyDown={(e) => {
                if (e.target !== e.currentTarget || (e.key !== "Enter" && e.key !== " ")) return;
                e.preventDefault();
                listenFrom(p.anchor);
              }}
              data-played={speaking && prog ? String(prog.paragraph) : undefined}
              style={speaking && prog ? { ["--played" as string]: prog.paragraph } : undefined}
              ref={(n) => {
                if (n) nodes.current.set(p.anchor, n);
                else nodes.current.delete(p.anchor);
              }}
            >
              {speaking && !live && (
                <span className="stop-flag" dir="auto">
                  <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                    <path d="M6 3h12v18l-6-4-6 4z" />
                  </svg>
                  {reading.status === "stopped" ? "You stopped here" : "Paused here"}
                </span>
              )}
              {p.speaker && <span dir="auto" className="seg-speaker">{p.speaker}</span>}
              {p.heading && <h3 dir="auto" className="seg-heading">{p.heading}</h3>}
              <p dir="auto" className="seg-text">{p.text}</p>
            </li>
            );
          })}
        </ol>
      </div>
      {reading.status !== "idle" && reading.status !== "stopped" && (
        <ReadAloudDock state={reading} controls={controls} device={device} sections={sections} />
      )}
    </aside>
  );
});
