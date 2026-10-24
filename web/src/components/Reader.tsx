// Reader.tsx
//
// Library reader: title, meta, passages, and a tape when passages are timed.
// Ported (cite-don't-import) from pocketsync, the reader's open/render/
// highlight/seek logic at pocketsync/web/static/app.js:461-626 and its markup
// at pocketsync/web/static/index.html:110-145. Differences: passages come from
// GET /api/library/{id} (anchor-addressed, times optional), summary/todos/
// re-enrich are dropped, and every string is a text node with dir="auto".
// Layout: side panel >= 861px, full-screen sheet below (styles/library.css).

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { fetchItem, type LibraryClientOptions, type LibraryDocument, type Passage } from "../api/library";
import { dayLabel, kindInfo } from "../api/kinds";
import "../styles/library.css";

export interface ReaderHandle {
  openAt: (anchor: string) => void;
  /** Fetch the item again (a failed open, or a re-click on the open item). */
  reload: () => void;
}

/** What a failed open says; "not found" mid re-index means "not ready yet". */
function failureText(code: string): string {
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
  { id, secret = "", anchor, clientOptions, onClose, variant = "side" },
  ref,
) {
  const [doc, setDoc] = useState<LibraryDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cited, setCited] = useState<string | null>(anchor ?? null);
  const [attempt, setAttempt] = useState(0);
  const nodes = useRef(new Map<string, HTMLLIElement>());

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
        {doc && doc.passages.length > 1 && (
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
          {doc?.passages.map((p) => (
            <li
              key={p.anchor}
              className={p.anchor === cited ? "seg cited" : "seg"}
              aria-current={p.anchor === cited ? "true" : undefined}
              ref={(n) => {
                if (n) nodes.current.set(p.anchor, n);
                else nodes.current.delete(p.anchor);
              }}
            >
              {p.speaker && <span dir="auto" className="seg-speaker">{p.speaker}</span>}
              {p.heading && <h3 dir="auto" className="seg-heading">{p.heading}</h3>}
              <p dir="auto" className="seg-text">{p.text}</p>
            </li>
          ))}
        </ol>
      </div>
    </aside>
  );
});
