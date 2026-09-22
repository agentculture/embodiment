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
  // Round 5 correction: embodiment/daemon/app.py's `_publish_mic` sends
  // `ear: self._ear_name`, which is `None` (JSON `null`) when no ear is
  // attached -- read directly from the daemon source, not assumed. The
  // committed schema.json fixture's example (a non-null ear name) doesn't
  // exercise this case, which is why it went unnoticed until a real
  // connect-after-detach was observed.
  ear: string | null;
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

/** Why a raw SSE frame body was rejected by :func:`parseEnvelopeFrame`. */
export type EnvelopeRejectionReason =
  | "unparseable-json"
  | "not-an-object"
  | "unsupported-version"
  | "data-not-object";

export interface EnvelopeParseResult {
  /** The parsed, wire-shaped envelope, or null if rejected. */
  envelope: EventEnvelope | null;
  /** Present only when `envelope` is null. */
  rejectionReason?: EnvelopeRejectionReason;
}

/**
 * Parse one SSE frame's `data:` body as a WHOLE envelope — round 2's fix:
 * the wire carries `{v, kind, ts, seq, source, data: {...}}` (exactly what
 * tests/fixtures/events/*.json commits), never the inner `data` object on
 * its own. A probe server serving the committed fixtures verbatim caught
 * the previous version of this hook treating the parsed JSON as if it
 * *were* `data` — every pane rendered the envelope's outer shape (`.text`,
 * `.code`, `.env` all `undefined`) instead of the inner one.
 *
 * Validates only what the brief calls out as load-bearing: `v === 1` (the
 * one schema version this dashboard understands — schema.json's own
 * `schemaVersion`) and that `data` is a plain object (never null, never an
 * array — every kind's fields live directly on it). Every other envelope
 * field (`kind`, `ts`, `seq`, `source`) is passed through as the server
 * sent it, unvalidated here — `kind` routing already comes from which
 * `addEventListener` fired, so a spoofed/missing `kind` field on the body
 * cannot mis-route an event; `ts`/`seq`/`source` are display-only.
 *
 * Never throws (lesson 3): a malformed frame is reported as a rejection,
 * not an exception — the caller counts it rather than losing it silently.
 */
export function parseEnvelopeFrame(raw: string): EnvelopeParseResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { envelope: null, rejectionReason: "unparseable-json" };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { envelope: null, rejectionReason: "not-an-object" };
  }
  const obj = parsed as Record<string, unknown>;
  if (obj.v !== 1) {
    return { envelope: null, rejectionReason: "unsupported-version" };
  }
  if (typeof obj.data !== "object" || obj.data === null || Array.isArray(obj.data)) {
    return { envelope: null, rejectionReason: "data-not-object" };
  }
  return { envelope: obj as unknown as EventEnvelope };
}
