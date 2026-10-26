// StatsView.tsx
//
// Data and site usage, in culture-library's "Site state" pattern (tiles, a
// status list with icon + label, a chart grid where every chart has a Table
// toggle and a Source line). Numbers come from GET /api/library and GET
// /api/status only. The daemon keeps counters since its last start and no
// history, so usage is labelled "since Gwen started" and nothing is drawn over
// time that was not recorded.

import { useEffect, useState, type ReactNode } from "react";
import { fetchStatus } from "../api/control";
import type { LibraryItem } from "../api/library";
import { itemDay, kindInfo, kindsPresent, shortDate } from "../api/kinds";

type Rec = Record<string, unknown>;
const rec = (v: unknown): Rec => (v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {});
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export interface StatsViewProps {
  items: LibraryItem[];
  secret?: string;
  /** Test seam. */
  fetchStatusFn?: typeof fetchStatus;
  /** Rendered under the status list: the voice controls and the degradations log. */
  children?: ReactNode;
}

type Level = "good" | "warning" | "critical" | "info";

function Icon({ level }: { level: Level }) {
  const path =
    level === "good"
      ? "M7 12.5l3 3 7-7"
      : level === "warning"
        ? "M12 8v5M12 16.5v.5"
        : level === "critical"
          ? "M8 8l8 8M16 8l-8 8"
          : "M12 11v6M12 7.5v.5";
  return (
    <svg className={`st-icon st-${level}`} viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <path d={path} />
    </svg>
  );
}

interface Row {
  name: string;
  level: Level;
  label: string;
  detail: string;
}

function ago(seconds: number | null): string {
  if (seconds === null) return "never";
  const s = Math.max(0, Date.now() / 1000 - seconds);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

interface Bar {
  label: string;
  value: number;
  color?: string;
  note?: string;
}

function Chart({
  title,
  sub,
  source,
  bars,
  columns,
}: {
  title: string;
  sub: string;
  source: string;
  bars: Bar[];
  columns?: boolean;
}) {
  const [table, setTable] = useState(false);
  const max = Math.max(1, ...bars.map((b) => b.value));
  return (
    <figure className="chart">
      <div className="chart-head">
        <div>
          <h3>{title}</h3>
          <p>{sub}</p>
        </div>
        <button type="button" className="pill-btn" aria-pressed={table} onClick={() => setTable((t) => !t)}>
          {table ? "Chart" : "Table"}
        </button>
      </div>
      {table ? (
        <table className="chart-table">
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col">Value</th>
            </tr>
          </thead>
          <tbody>
            {bars.map((b) => (
              <tr key={b.label}>
                <td>{b.label}</td>
                <td>{b.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : columns ? (
        <div className="cols" role="img" aria-label={`${title}: ${bars.map((b) => `${b.label} ${b.value}`).join(", ")}`}>
          {bars.map((b) => (
            <div key={b.label} className="col" data-tip={`${b.label}: ${b.value}`}>
              <span className="col-v">{b.value || ""}</span>
              <span
                className="col-bar"
                style={{ blockSize: `${(b.value / max) * 100}%`, ["--kind" as string]: b.color ?? "var(--accent)" }}
              />
              <span className="col-l">{b.label}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="hbars">
          {bars.map((b) => (
            <div key={b.label} className="hbar" data-tip={`${b.label}: ${b.value}`}>
              <span className="hbar-l">{b.label}</span>
              <span className="hbar-track">
                <span
                  className="hbar-bar"
                  style={{
                    inlineSize: `calc((100% - 3.5rem) * ${b.value / max})`,
                    ["--kind" as string]: b.color ?? "var(--accent)",
                  }}
                />
                <span className="hbar-v">
                  {b.value}
                  {b.note ? ` ${b.note}` : ""}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
      <figcaption className="chart-src">Source: {source}</figcaption>
    </figure>
  );
}

export function StatsView({ items, secret = "", fetchStatusFn = fetchStatus, children }: StatsViewProps) {
  const [status, setStatus] = useState<Rec | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    const load = () =>
      fetchStatusFn(secret)
        .then((s) => {
          if (!live) return;
          setStatus(rec((s as unknown as Rec).daemon));
          setFailed(false);
        })
        .catch(() => live && setFailed(true));
    void load();
    const timer = setInterval(load, 15000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [secret, fetchStatusFn]);

  const lib = rec(status?.library);
  const recall = rec(status?.recall);
  const access = rec(status?.access);
  const ear = rec(status?.ear);
  const textTurns = rec(status?.text_turns);
  const turns = rec(status?.turns);
  const tools = rec(lib.tool_calls);

  const reading = rec(status?.reading);
  const catalogItems = num(lib.catalog_items);
  const retrieveCalls = num(lib.calls) ?? 0;
  const retrieveOk = num(lib.calls_ok) ?? 0;
  const readServed = num(reading.served) ?? 0;
  const readHeard = num(reading.heard) ?? 0;
  const readRefused = num(reading.refused) ?? 0;
  const typed = num(textTurns.run) ?? 0;
  const spoken = num(turns.completed) ?? 0;
  const searches = num(tools.search_library) ?? 0;
  const reads = num(tools.read_passage) ?? 0;
  const recalls = num(recall.calls) ?? 0;
  const recallHits = num(recall.hits_total) ?? 0;

  const rows: Row[] = [];
  if (status) {
    if (lib.configured === false) {
      rows.push({ name: "Library", level: "warning", label: "Not connected", detail: "No library URL is set for Gwen." });
    } else {
      const failures = Object.values(rec(lib.failures_by_code)).reduce<number>((n, v) => n + (num(v) ?? 0), 0);
      const lastLatency = num(lib.last_latency_s);
      rows.push({
        name: "Library catalog",
        level: lib.catalog_down ? "warning" : "good",
        label: lib.catalog_down ? "Not reachable" : "Listed",
        detail: `Last listed ${ago(num(lib.last_listed_at))}; ${catalogItems ?? 0} items in the catalog.`,
      });
      rows.push({
        name: "Library answers",
        level: failures > 0 ? "warning" : searches > 0 ? "good" : typed > 0 ? "warning" : "info",
        label: failures > 0 ? `${failures} failed` : searches > 0 ? `${searches} searches` : "Not used yet",
        detail: `Gwen searched the library ${searches} times and opened ${reads} passages over ${typed} typed questions. The library answered ${retrieveOk} of ${retrieveCalls} retrievals${lastLatency === null ? "" : `, the last in ${lastLatency.toFixed(2)} s`}.`,
      });
      if (reading.configured !== false) {
        rows.push({
          name: "Reading aloud",
          level: readServed === 0 ? "info" : readRefused > 0 ? "warning" : "good",
          label: readServed === 0 ? "Not used yet" : `${readHeard} of ${readServed} heard`,
          detail: `${readServed} paragraphs served, ${readHeard} heard to the end, ${readRefused} heard reports refused, ${num(reading.seeks) ?? 0} jumps.`,
        });
      }
    }
    rows.push({
      name: "Memory recall",
      level: recalls === 0 ? "info" : recallHits === 0 ? "warning" : "good",
      label: recalls === 0 ? "Not used yet" : recallHits === 0 ? "Finds nothing" : `${recallHits} hits`,
      detail: `${recalls} recalls in ${String(recall.mode ?? "unknown")} mode, ${num(recall.empty_total) ?? 0} empty.`,
    });
    const accessMode = String(access.mode ?? "off");
    rows.push({
      name: "Sign-in",
      level: accessMode === "off" ? "info" : "good",
      label: accessMode === "off" ? "Install secret" : "Cloudflare Access",
      detail:
        accessMode === "off"
          ? "This page uses the install secret; no public host is set."
          : `${num(access.admitted) ?? 0} requests admitted, ${num(access.refused) ?? 0} refused.`,
    });
    rows.push({
      name: "Room voice",
      level: ear.active ? (ear.muted ? "warning" : "good") : "info",
      label: ear.active ? (ear.muted ? "Muted" : "Listening") : "Off",
      detail: ear.active
        ? `Listening through the ${String(ear.active)} microphone.`
        : "Start live voice from the dock to talk with Gwen through the room microphone.",
    });
  }

  const kinds = kindsPresent(items);
  const byKind: Bar[] = kinds.map((k) => ({
    label: k.many,
    value: items.filter((i) => i.kind === k.id).length,
    color: k.color,
  }));
  const days = new Map<string, number>();
  for (const i of items) {
    if (i.kind !== "transcript") continue;
    const d = itemDay(i.createdAt, i.title);
    days.set(d, (days.get(d) ?? 0) + 1);
  }
  const byDay: Bar[] = [...days.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .slice(-14)
    .map(([d, v]) => ({ label: shortDate(d), value: v, color: kindInfo("transcript").color }));
  const usage: Bar[] = [
    { label: "Typed questions", value: typed },
    { label: "Spoken turns", value: spoken },
    { label: "Library searches", value: searches },
    { label: "Passages opened", value: reads },
    { label: "Memory recalls", value: recalls },
  ];

  return (
    <section className="stats" aria-label="Stats">
      <header className="stats-head">
        <h1>Stats</h1>
        <p>
          What Gwen can read, and how the page is used. Usage counts start when Gwen starts; no history is kept yet.
        </p>
      </header>
      {failed && (
        <p className="library-error" role="alert">
          Gwen's status could not be read. The numbers below may be stale.
        </p>
      )}
      <div className="tiles">
        <div className="tile">
          <b>{items.length}</b>
          <span>Items in the library</span>
          <small>
            {kinds
              .map((k) => {
                const n = items.filter((i) => i.kind === k.id).length;
                return `${n} ${(n === 1 ? k.one : k.many).toLowerCase()}`;
              })
              .join(", ")}
          </small>
        </div>
        <div className="tile">
          <b>{catalogItems ?? "–"}</b>
          <span>Items in the catalog</span>
          <small>
            {retrieveOk} of {retrieveCalls} retrievals answered
          </small>
        </div>
        <div className="tile">
          <b>{typed + spoken}</b>
          <span>Questions since Gwen started</span>
          <small>
            {typed} typed, {spoken} spoken
          </small>
        </div>
        <div className="tile">
          <b>{searches}</b>
          <span>Library searches</span>
          <small>{reads === 1 ? "1 passage opened" : `${reads} passages opened`}</small>
        </div>
      </div>
      {rows.length > 0 && (
        <div className="status-list">
          {rows.map((r) => (
            <div key={r.name} className="status-row">
              <span className="status-name">{r.name}</span>
              <span className="status-state">
                <Icon level={r.level} />
                {r.label}
              </span>
              <span className="status-detail">{r.detail}</span>
            </div>
          ))}
        </div>
      )}
      <div className="charts">
        <Chart
          title="What the library holds"
          sub="Items by kind"
          source="GET /api/library"
          bars={byKind}
        />
        <Chart
          title="Transcripts by recording day"
          sub="Last 14 days with a recording"
          source="GET /api/library, the date in each title"
          bars={byDay}
          columns
        />
        <Chart
          title="Use since Gwen started"
          sub="Counters reset when Gwen restarts"
          source="GET /api/status"
          bars={usage}
        />
      </div>
      {children && <div className="stats-extra">{children}</div>}
    </section>
  );
}
