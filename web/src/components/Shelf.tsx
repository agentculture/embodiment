// Shelf.tsx
//
// One spine per library item, coloured by kind, oldest to newest, with a
// legend that names every kind and its count (identity is never colour
// alone). The shape is culture-library's home shelf (styles.css .shelf,
// .spine); heights vary by a stable hash of the id so the row reads as books.

import type { LibraryItem } from "../api/library";
import { itemDay, kindInfo, kindsPresent } from "../api/kinds";

export interface ShelfProps {
  items: LibraryItem[];
  onOpen?: (itemId: string) => void;
  /** At most this many spines; the newest win. */
  max?: number;
}

function spineHeight(id: string): number {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) % 997;
  return 58 + (h % 42);
}

export function Shelf({ items, onOpen, max = 90 }: ShelfProps) {
  const day = (i: LibraryItem) => itemDay(i.createdAt, i.title);
  const sorted = [...items].sort((a, b) => (day(a) < day(b) ? -1 : 1)).slice(-max);
  const kinds = kindsPresent(items);
  return (
    <figure className="shelf" aria-label="The shelf: one spine per item">
      <div className="shelf-row">
        {sorted.map((item) => {
          const k = kindInfo(item.kind);
          return (
            <button
              key={item.id}
              type="button"
              className="spine"
              style={{ ["--kind" as string]: k.color, blockSize: `${spineHeight(item.id)}%` }}
              aria-label={`${k.one}: ${item.title}`}
              title={item.title}
              onClick={() => onOpen?.(item.id)}
            />
          );
        })}
      </div>
      <figcaption className="shelf-cap">
        <span className="legend">
          {kinds.map((k) => (
            <span key={k.id} className="legend-item">
              <span className="dot" style={{ ["--kind" as string]: k.color }} />
              {k.many} {items.filter((i) => i.kind === k.id).length}
            </span>
          ))}
        </span>
        <span className="shelf-count">{items.length} on the shelf</span>
      </figcaption>
    </figure>
  );
}
