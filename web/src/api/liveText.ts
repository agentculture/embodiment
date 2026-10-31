// api/liveText.ts
//
// A typed turn's text as it streams (deviation d5; operator, 2026-10-31: "stream the
// tokens for now, so we can see reasoning and no timeout"). The daemon publishes
// `stream` events, each carrying one channel's text SO FAR in one model step; this
// keeps the newest per request id. Text only: rendered as text nodes, never HTML.

export interface LiveView {
  /** The model step the text belongs to, from 0. */
  step: number;
  reasoning: string;
  content: string;
  /** Reasoning of the earlier steps of this turn, oldest first. */
  earlier: string[];
}

export type LiveMap = Record<string, LiveView>;

/** How many requests' live text the page keeps. */
export const LIVE_KEPT = 16;

/** Fold one `stream` event's data into *map*; the same object back when it is not usable. */
export function applyStream(map: LiveMap, data: Record<string, unknown>): LiveMap {
  const { request_id: id, channel, text } = data;
  const step = typeof data.step === "number" && Number.isInteger(data.step) ? data.step : 0;
  if (typeof id !== "string" || !id || typeof text !== "string") return map;
  if (channel !== "reasoning" && channel !== "content") return map;
  const prev = map[id];
  if (prev && step < prev.step) return map; // a late frame of an earlier step
  let view: LiveView = prev ?? { step, reasoning: "", content: "", earlier: [] };
  if (prev && step > prev.step) {
    view = { step, reasoning: "", content: "", earlier: prev.reasoning ? [...prev.earlier, prev.reasoning] : prev.earlier };
  }
  view = { ...view, [channel]: text };
  const next: LiveMap = { ...map };
  delete next[id]; // re-insert last: insertion order is age
  next[id] = view;
  const ids = Object.keys(next);
  for (const old of ids.slice(0, Math.max(0, ids.length - LIVE_KEPT))) delete next[old];
  return next;
}

/** All of a turn's reasoning so far, steps separated by a blank line. */
export function liveReasoning(view: LiveView | undefined): string {
  if (!view) return "";
  return [...view.earlier, view.reasoning].filter(Boolean).join("\n\n");
}
