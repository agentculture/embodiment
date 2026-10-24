// LibraryPane.tsx
//
// The library list beside the conversation: kind filters with counts, the
// items grouped by day (newest first), and passage hits when the header
// search runs. Ported from culture-library's reader pane (culture_library/
// web/static/index.html #pane, styles.css .pane/.rows), cited not imported.
// Every string is a text node with dir="auto"; nothing is parsed as markup.

import { useMemo, useState } from "react";
import type { LibraryItem, SearchHit } from "../api/library";
import { dayLabel, itemDay, kindInfo, kindsPresent, shortTitle } from "../api/kinds";

export interface LibraryPaneProps {
  items: LibraryItem[];
  error?: string | null;
  /** The item open in the reader, highlighted in the list. */
  openId?: string | null;
  /** Items cited in this conversation, marked in the list. */
  citedIds?: ReadonlySet<string>;
  /** Text filter from the header search (titles). */
  query?: string;
  /** Passage hits for `query`, when a search ran. */
  hits?: SearchHit[] | null;
  searching?: boolean;
  onOpen: (itemId: string, anchor?: string) => void;
  onClose?: () => void;
  /** When given, the pane carries its own search box (phones hide the header's). */
  onQuery?: (q: string) => void;
  onSearch?: () => void;
}

export function groupByDay(items: LibraryItem[]): [string, LibraryItem[]][] {
  const days = new Map<string, LibraryItem[]>();
  for (const item of items) {
    const day = itemDay(item.createdAt, item.title) || "undated";
    days.set(day, [...(days.get(day) ?? []), item]);
  }
  return [...days.entries()].sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0));
}

export function LibraryPane({
  items,
  error,
  openId,
  citedIds,
  query = "",
  hits,
  searching,
  onOpen,
  onClose,
  onQuery,
  onSearch,
}: LibraryPaneProps) {
  const [kind, setKind] = useState<string | null>(null);
  const kinds = useMemo(() => kindsPresent(items), [items]);
  const q = query.trim().toLowerCase();
  const shown = items.filter(
    (i) => (kind === null || i.kind === kind) && (!q || i.title.toLowerCase().includes(q)),
  );

  return (
    <nav className="pane" aria-label="Library list">
      <div className="pane-head">
        <h2>Library</h2>
        {onClose && (
          <button type="button" className="icon-btn" aria-label="Hide the library list" onClick={onClose}>
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path d="M15 5l-7 7 7 7" />
            </svg>
          </button>
        )}
      </div>
      {onQuery && (
        <form
          className="pane-search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            onSearch?.();
          }}
        >
          <label className="sr-only" htmlFor="pane-q">
            Search the library
          </label>
          <input
            id="pane-q"
            type="search"
            dir="auto"
            autoComplete="off"
            placeholder="Titles, or Enter for passages"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
          />
        </form>
      )}
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
      {error && (
        <p className="library-error" role="alert">
          The library is not reachable right now ({error}).
        </p>
      )}
      <div className="rows">
        {q && (
          <section className="hits" aria-label="Passages">
            <h3 className="day">{searching ? "Searching passages" : "Passages"}</h3>
            {!searching && hits && hits.length === 0 && <p className="rows-note">No passage matched.</p>}
            {hits?.map((h, i) => (
              <button
                key={`${h.itemId}-${h.anchor}-${i}`}
                type="button"
                className="hit"
                onClick={() => onOpen(h.itemId, h.anchor || undefined)}
              >
                <span className="hit-title">
                  <span className="dot" style={{ ["--kind" as string]: kindInfo(h.kind).color }} />
                  <span dir="auto">{h.title}</span>
                </span>
                <span className="hit-text" dir="auto">
                  {h.text}
                </span>
              </button>
            ))}
            <h3 className="day">Titles</h3>
          </section>
        )}
        {shown.length === 0 && !error && <p className="rows-note">Nothing here yet.</p>}
        {groupByDay(shown).map(([day, group]) => (
          <section key={day} aria-label={dayLabel(day) || day}>
            <h3 className="day">{dayLabel(day) || day}</h3>
            {group.map((item) => (
              <button
                key={item.id}
                type="button"
                className="row"
                aria-current={openId === item.id ? "true" : undefined}
                onClick={() => onOpen(item.id)}
              >
                <span className="dot" style={{ ["--kind" as string]: kindInfo(item.kind).color }} />
                <span className="row-title" dir="auto" title={item.title}>
                  {shortTitle(item.title)}
                </span>
                <span className="row-side">
                  {citedIds?.has(item.id) ? "cited" : kindInfo(item.kind).one.toLowerCase()}
                </span>
              </button>
            ))}
          </section>
        ))}
      </div>
    </nav>
  );
}
