// LibraryView.tsx
//
// The library list, grouped by day with kind chips; clicking an item opens
// the Reader. Ported from the recording list of the pocketsync web app,
// cited not imported (cite-don't-import): see Reader.tsx for the pinned
// provenance of the reader half. All text is rendered as text nodes with
// dir="auto"; nothing is parsed as markup.

import { useEffect, useMemo, useState } from "react";
import { fetchLibrary, type LibraryClientOptions, type LibraryItem } from "../api/library";
import { Reader } from "./Reader";
import "../styles/library.css";

export interface LibraryViewProps {
  secret?: string;
  clientOptions?: LibraryClientOptions;
}

const dayOf = (createdAt: string): string => createdAt.slice(0, 10) || "undated";

function groupByDay(items: LibraryItem[]): [string, LibraryItem[]][] {
  const days = new Map<string, LibraryItem[]>();
  for (const item of items) {
    const day = dayOf(item.createdAt);
    days.set(day, [...(days.get(day) ?? []), item]);
  }
  return [...days.entries()].sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0));
}

export function LibraryView({ secret = "", clientOptions }: LibraryViewProps) {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [kinds, setKinds] = useState<Set<string>>(new Set());
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void fetchLibrary(secret, clientOptions).then((r) => {
      if (!live) return;
      if (r.ok) setItems(r.value);
      else setError(r.code);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secret]);

  const allKinds = useMemo(() => [...new Set(items.map((i) => i.kind))].sort(), [items]);
  const shown = items.filter((i) => kinds.size === 0 || kinds.has(i.kind));

  const toggle = (kind: string) =>
    setKinds((prev) => {
      const next = new Set(prev);
      if (!next.delete(kind)) next.add(kind);
      return next;
    });

  return (
    <section className="library" aria-label="Library">
      {error && <p className="library-error" role="alert">library unavailable: {error}</p>}
      <div className="chips">
        {allKinds.map((k) => (
          <button key={k} type="button" className="chip" aria-pressed={kinds.has(k)} onClick={() => toggle(k)}>
            {k}
          </button>
        ))}
      </div>
      {groupByDay(shown).map(([day, group]) => (
        <div key={day} className="library-day">
          <h3>{day}</h3>
          <ul>
            {group.map((item) => (
              <li key={item.id}>
                <button type="button" className="library-item" onClick={() => setOpenId(item.id)}>
                  <span dir="auto">{item.title}</span>
                  <span className="library-kind">{item.kind}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {openId && (
        <Reader id={openId} secret={secret} clientOptions={clientOptions} onClose={() => setOpenId(null)} />
      )}
    </section>
  );
}
