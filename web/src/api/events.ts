// api/events.ts
//
// The bus event contract this dashboard reads: mirrors
// tests/fixtures/events/schema.json (embodiment/bus.py, task t13, spec
// target h16). That file — and the one fixture JSON per kind beside it —
// is a CONTRACT between embodiment/bus.py and this app: both sides
// validate against the identical committed files, so the two cannot drift
// apart silently. This module names the same kinds and the same required
// fields; it never invents a field the schema does not list.

/** Every event kind the bus emits (schema.json's "kinds" object, in the
 *  same order tests/fixtures/events/ lists the one fixture per kind). */
export const EVENT_KINDS = [
  "state",
  "mic",
  "turn",
  "transcript",
  "reply",
  "degradation",
  "features",
  "clients",
  "heartbeat",
] as const;

export type EventKind = (typeof EVENT_KINDS)[number];

/** kind -> whether it can carry speech (schema.json's own note on
 *  transcript/reply; mirrors embodiment.bus.SPEECH_KINDS). Never rendered
 *  outside the transcript pane. */
export const SPEECH_KINDS: ReadonlySet<EventKind> = new Set(["transcript", "reply"]);

/**
 * embodiment/bus.py's HEARTBEAT_INTERVAL_S (embodiment/bus.py, module
 * constant): the fixed interval Bus.tick() emits a `heartbeat` event at
 * most once per. Duplicated here, in ONE place, as a shared constant with
 * this comment naming the Python source of truth — the integrator note for
 * this task requires exactly that, rather than a magic number reappearing
 * wherever the dashboard needs to reason about liveness.
 */
export const HEARTBEAT_INTERVAL_S = 15.0;

/** The dashboard calls itself "disconnected" once this many heartbeat
 *  intervals have passed with no heartbeat event (brief's own wording:
 *  "no heartbeat for 2 intervals"). */
export const DISCONNECTED_AFTER_MISSED_HEARTBEATS = 2;

export const DISCONNECTED_AFTER_MS =
  HEARTBEAT_INTERVAL_S * DISCONNECTED_AFTER_MISSED_HEARTBEATS * 1000;

export interface EventEnvelopeData {
  [key: string]: unknown;
}

/** The envelope shape every event kind shares (schema.json's "envelope"). */
export interface EventEnvelope<K extends EventKind = EventKind> {
  v: number;
  kind: K;
  ts: string;
  seq: number;
  source: string;
  data: EventEnvelopeData;
  /** present only when > 0: count of events this subscriber dropped. */
  gap?: number;
}

export interface StateData extends EventEnvelopeData {
  component: string;
  status: string;
}

export interface MicData extends EventEnvelopeData {
  hot: boolean;
  ear: string;
}

export interface TurnData extends EventEnvelopeData {
  phase: string;
  step_count: number;
  degradation_codes?: string[];
}

export interface TranscriptData extends EventEnvelopeData {
  role: "user" | "assistant" | string;
  text: string;
}

export interface ReplyData extends EventEnvelopeData {
  text: string;
}

export interface DegradationData extends EventEnvelopeData {
  source: string;
  code: string;
  reason: string;
}

export interface FeaturesData extends EventEnvelopeData {
  direction: "in" | "out" | string;
  env: string;
  level_db: number;
  noise_floor_db: number;
  zero_crossing_hz: number | null;
}

export interface ClientsData extends EventEnvelopeData {
  count: number;
  remote: number;
}

// heartbeat carries no required fields (schema.json: "required": []).
export type HeartbeatData = EventEnvelopeData;

/** Narrow required top-level envelope fields present and well-typed. Does
 *  NOT validate per-kind `data` shape (that is schema.json's job on the
 *  Python side); a malformed/unknown envelope is rejected here so a bad
 *  frame from the wire is dropped rather than crashing a render. */
export function isEventEnvelope(value: unknown): value is EventEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.v === "number" &&
    typeof v.kind === "string" &&
    (EVENT_KINDS as readonly string[]).includes(v.kind) &&
    typeof v.ts === "string" &&
    typeof v.seq === "number" &&
    typeof v.source === "string" &&
    typeof v.data === "object" &&
    v.data !== null
  );
}
