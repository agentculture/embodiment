// LibraryHome.tsx
//
// The Library tab: culture-library's home (intro beside the shelf, then the
// newest items as cards with kind filters), and its reader view (list pane +
// wide reader) once an item opens. Ported from culture_library/web/static/
// index.html (#view-home, #view-read), cited not imported.

import { useMemo, useState } from "react";
import type { LibraryItem } from "../api/library";
import { itemDay, kindInfo, kindsPresent, shortDate, shortTitle } from "../api/kinds";
import { Shelf } from "./Shelf";

export interface LibraryHomeProps {
  items: LibraryItem[];
  error?: string | null;
  onOpen: (itemId: string) => void;
  onAsk: (question: string) => void;
}

export function LibraryHome({ items, error, onOpen, onAsk }: LibraryHomeProps) {
  const [kind, setKind] = useState<string | null>(null);
  const kinds = useMemo(() => kindsPresent(items), [items]);
  const day = (i: LibraryItem) => itemDay(i.createdAt, i.title);
  const newest = [...items].sort((a, b) => (day(a) < day(b) ? 1 : day(a) > day(b) ? -1 : a.createdAt < b.createdAt ? 1 : -1));
  const shown = newest.filter((i) => kind === null || i.kind === kind).slice(0, 24);
  const transcripts = items.filter((i) => i.kind === "transcript").length;

  return (
    <div className="home">
      <div className="intro">
        <div>
          <h1>Everything you read and record, on one shelf</h1>
          <p>
            Recording transcripts, daily newspapers, documents and books. Culture-rules brings them in; Gwen reads them
            and answers from them, and you can open any passage she cites.
          </p>
          <div className="intro-actions">
            <button type="button" className="btn primary" onClick={() => onAsk("")}>
              Ask Gwen
            </button>
            {transcripts > 0 && (
              <span className="intro-note">
                {transcripts === 1 ? "1 transcript" : `${transcripts} transcripts`} ready to ask about
              </span>
            )}
          </div>
        </div>
        <Shelf items={items} onOpen={onOpen} />
      </div>
      {error && (
        <p className="library-error" role="alert">
          The library is not reachable right now ({error}).
        </p>
      )}
      <div className="sect-head">
        <h2>Latest</h2>
        <div className="chips" role="group" aria-label="Kind">
          <button type="button" className="chip" aria-pressed={kind === null} onClick={() => setKind(null)}>
            All {items.length}
          </button>
          {kinds.map((k) => (
            <button
              key={k.id}
              type="button"
              className="chip"
              aria-pressed={kind === k.id}
              onClick={() => setKind(kind === k.id ? null : k.id)}
            >
              {k.many} {items.filter((i) => i.kind === k.id).length}
            </button>
          ))}
        </div>
      </div>
      <div className="cards">
        {shown.map((item) => {
          const k = kindInfo(item.kind);
          return (
            <button key={item.id} type="button" className="card" onClick={() => onOpen(item.id)}>
              <span className="kind-badge" style={{ ["--kind" as string]: k.color }}>
                {k.one}
              </span>
              <span className="card-title" dir="auto">
                {shortTitle(item.title)}
              </span>
              <span className="card-foot">
                {shortDate(day(item))}
                {!item.indexed && <span className="card-flag">not searchable yet</span>}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
