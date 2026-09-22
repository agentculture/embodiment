// hooks/useEventStream.ts
//
// The event-stream hook against t13's bus-event schema (task t17). Opens
// one SSE connection (t16's projection of embodiment/bus.py) via the
// `SSEConnect` seam (sseConnection.ts) and routes each frame by its `kind`
// — an unknown future kind is simply not seen, never mis-rendered.
//
// Round 4: this used to be EventSource-based. A LIVE finding (the operator
// opening the dashboard over Tailscale, off-loopback and off-https) showed
// EventSource's cookie-credentialed stream gets refused by t16's guard
// there (`http-refused-cookie-without-origin`) — see
// api/sseFetchReader.ts's module docstring for the full story. The default
// connector is now `api/sseFetchReader.ts`'s fetch-based reader, and the
// credential travels as `Authorization: Bearer <secret>`, a header WE set
// on the fetch call — never a cookie, so the guard's Origin/Sec-Fetch-Site
// rule for cookies is never consulted for this request at all.
//
// Liveness is judged ONLY by the `heartbeat` kind (embodiment/bus.py emits
// it on a fixed cadence via Bus.tick(), independent of other traffic): the
// stream is "disconnected" once DISCONNECTED_AFTER_MS has passed since the
// later of (a) the connection opening or (b) the last heartbeat — "no
// heartbeat for 2 intervals" (the brief's own wording). Any other kind of
// traffic does NOT reset this timer; a chatty features/transcript stream
// with a stalled heartbeat is still reported as disconnected (lesson 3:
// never silent — a status field that looked "connected" because *something*
// arrived would hide a dead daemon whose heartbeat loop crashed).

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  type ClientsData,
  type DegradationData,
  type EventEnvelope,
  type EventKind,
  EVENT_KINDS,
  type FeaturesData,
  type MicData,
  type ReplyData,
  type StateData,
  type TranscriptData,
  type TurnData,
  DISCONNECTED_AFTER_MS,
  isEventEnvelope,
  parseEnvelopeFrame,
} from "../api/events";
import { connectSSE } from "../api/sseFetchReader";
import type { SSEConnect } from "./sseConnection";
import { fetchStatus, type StatusEnvelopeResponse } from "../api/control";

export type ConnectionStatus = "connecting" | "connected" | "disconnected" | "unauthorized";

/** How many speech-log / degradation-log entries to keep. Chosen, not
 *  measured — a round number generous enough for a single sitting, bounded
 *  so a long-running daemon cannot grow the tab's memory without limit
 *  (lesson 3: a bounded buffer, never an unbounded one). */
export const TRANSCRIPT_LOG_LIMIT = 500;
export const DEGRADATION_LOG_LIMIT = 200;

export interface EventStreamSnapshot {
  status: ConnectionStatus;
  state: EventEnvelope<"state"> | null;
  mic: EventEnvelope<"mic"> | null;
  turn: EventEnvelope<"turn"> | null;
  features: EventEnvelope<"features"> | null;
  clients: EventEnvelope<"clients"> | null;
  /** transcript + reply, merged into one speech log in arrival order. */
  transcript: EventEnvelope<"transcript" | "reply">[];
  degradations: EventEnvelope<"degradation">[];
  /** Wall-clock ms of the last heartbeat this hook has seen, or null before
   *  the first one. Exposed for callers that want to render "last seen Ns
   *  ago" rather than only the coarse connected/disconnected status. */
  lastHeartbeatAtMs: number | null;
  /** Count of SSE frames dropped for failing parseEnvelopeFrame's
   *  validation (unparseable JSON, not an object, wrong schema version, or
   *  `data` not itself an object) — lesson 3: a bounded buffer counts what
   *  it drops, a dropped frame is never silently invisible. */
  droppedFrames: number;
  /** `status()["recall"]["mode"]`, seeded from `GET /api/status` (round
   *  5). `null` before the first successful seed, or if the daemon itself
   *  has never completed a recall call. A REAL daemon-reported value, not
   *  inferred — see RecallModeIndicator.tsx. */
  recallStatusMode: string | null;
  /** `status()["recall"]["configured_mode"]` (round 6) -- what recall is
   *  CONFIGURED to do (`AppConfig.recall_mode`, default `"keyword"`),
   *  always present once seeded, unlike `recallStatusMode` which stays
   *  null until the first completed recall call. Lets the indicator show
   *  what WILL happen rather than a bare "unknown" in that gap. */
  recallConfiguredMode: string | null;
  /**
   * Re-fetch `GET /api/status` and reseed `mic`/`clients`/`recallStatusMode`
   * from it. Called automatically on every successful (re)connect; the
   * caller (App.tsx) also calls it after a control POST resolves 200, so
   * Start/Stop/Mute's outcome is visible immediately from a fresh read
   * rather than waiting on (or duplicating trust in) the live event the
   * daemon separately publishes for the same change. Never throws: a
   * failed refresh leaves the existing state alone.
   */
  refreshStatus: () => Promise<void>;
}

interface DataState {
  state: EventEnvelope<"state"> | null;
  mic: EventEnvelope<"mic"> | null;
  turn: EventEnvelope<"turn"> | null;
  features: EventEnvelope<"features"> | null;
  clients: EventEnvelope<"clients"> | null;
  transcript: EventEnvelope<"transcript" | "reply">[];
  degradations: EventEnvelope<"degradation">[];
  droppedFrames: number;
  recallStatusMode: string | null;
  recallConfiguredMode: string | null;
}

const INITIAL_DATA: DataState = {
  state: null,
  mic: null,
  turn: null,
  features: null,
  clients: null,
  transcript: [],
  degradations: [],
  droppedFrames: 0,
  recallStatusMode: null,
  recallConfiguredMode: null,
};

type Action =
  | { type: "state"; envelope: EventEnvelope<"state">; data: StateData }
  | { type: "mic"; envelope: EventEnvelope<"mic">; data: MicData }
  | { type: "turn"; envelope: EventEnvelope<"turn">; data: TurnData }
  | { type: "features"; envelope: EventEnvelope<"features">; data: FeaturesData }
  | { type: "clients"; envelope: EventEnvelope<"clients">; data: ClientsData }
  | { type: "speech"; envelope: EventEnvelope<"transcript" | "reply">; data: TranscriptData | ReplyData }
  | { type: "degradation"; envelope: EventEnvelope<"degradation">; data: DegradationData }
  | { type: "dropped" }
  | { type: "recallStatusMode"; mode: string | null; configuredMode: string | null };

function reducer(prev: DataState, action: Action): DataState {
  switch (action.type) {
    case "state":
      return { ...prev, state: action.envelope };
    case "mic":
      return { ...prev, mic: action.envelope };
    case "turn":
      return { ...prev, turn: action.envelope };
    case "features":
      return { ...prev, features: action.envelope };
    case "clients":
      return { ...prev, clients: action.envelope };
    case "speech": {
      const next = [...prev.transcript, action.envelope];
      const overflow = next.length - TRANSCRIPT_LOG_LIMIT;
      return { ...prev, transcript: overflow > 0 ? next.slice(overflow) : next };
    }
    case "degradation": {
      const next = [...prev.degradations, action.envelope];
      const overflow = next.length - DEGRADATION_LOG_LIMIT;
      return { ...prev, degradations: overflow > 0 ? next.slice(overflow) : next };
    }
    case "dropped":
      return { ...prev, droppedFrames: prev.droppedFrames + 1 };
    case "recallStatusMode":
      return { ...prev, recallStatusMode: action.mode, recallConfiguredMode: action.configuredMode };
    default:
      return prev;
  }
}

/** `GET /api/status`'s fetcher, as an injectable seam so tests never need a
 *  real network call. Defaults to `api/control.ts`'s `fetchStatus`. */
export type FetchStatusFn = (
  secret: string,
  options?: { basePath?: string; fetchFn?: typeof fetch },
) => Promise<StatusEnvelopeResponse>;

export interface UseEventStreamOptions {
  /** Test/DI seam: defaults to the fetch-based `connectSSE`. */
  connect?: SSEConnect;
  /** Test seam: defaults to Date.now. */
  nowFn?: () => number;
  /** How often to re-check the heartbeat deadline. Chosen, not measured —
   *  1s is fine granularity for a UI status pill. */
  checkIntervalMs?: number;
  /**
   * Bump this (any value that changes by `!==`) to force the current
   * connection closed and a fresh one opened, without changing `url` or
   * `secret`. Kept as an explicit escape hatch alongside `secret` itself
   * already being a reconnect trigger (see below) — e.g. the operator
   * re-applying the identical secret value still forces a reconnect.
   */
  reconnectKey?: unknown;
  /** Test/DI seam: defaults to `api/control.ts`'s `fetchStatus`. */
  fetchStatusFn?: FetchStatusFn;
}

export function useEventStream(
  url: string,
  secret: string,
  options: UseEventStreamOptions = {},
): EventStreamSnapshot {
  const {
    connect = connectSSE,
    nowFn = Date.now,
    checkIntervalMs = 1000,
    reconnectKey,
    fetchStatusFn = fetchStatus,
  } = options;

  const [data, dispatch] = useReducer(reducer, INITIAL_DATA);
  const [opened, setOpened] = useState(false);
  const [lastHeartbeatAtMs, setLastHeartbeatAtMs] = useState<number | null>(null);
  const [openedAtMs, setOpenedAtMs] = useState<number | null>(null);
  const [now, setNow] = useState<number>(() => nowFn());
  const [erroredClosed, setErroredClosed] = useState(false);
  const [unauthorized, setUnauthorized] = useState(false);

  const urlRef = useRef(url);
  urlRef.current = url;

  // The latest connection attempt's own status-seeding function, so
  // `refreshStatus` (below, stable across renders) always calls the seed
  // for the CURRENT secret/connection rather than a stale one captured at
  // an earlier render.
  const seedFromStatusRef = useRef<() => Promise<void>>(async () => {});

  const refreshStatus = useCallback(async () => {
    await seedFromStatusRef.current();
  }, []);

  useEffect(() => {
    setOpened(false);
    setOpenedAtMs(null);
    setErroredClosed(false);
    setUnauthorized(false);
    // Reset per connection attempt: whether THIS connection has ever fired
    // onOpen. An error before the first successful open is the best signal
    // available for "the guard refused the credential" (t16's guard.py
    // returns 401 for a missing/bad secret; neither EventSource nor a plain
    // fetch() response status distinguishes "refused" from "network drop"
    // without inspecting the body, which this hook deliberately does not
    // do — the status code plus this heuristic is enough for the pill).
    let everOpened = false;

    const headers: Record<string, string> = secret ? { Authorization: `Bearer ${secret}` } : {};

    // Round 5 [LIVE, MAJOR]: a viewer who connects after the ear was
    // already attached/muted/etc. receives no snapshot over SSE, only
    // FUTURE changes — mic/clients/recall stayed "unknown" forever. This
    // reads embodiment/daemon/app.py's real `status()` (via `GET
    // /api/status`) and reseeds mic/clients/recallStatusMode from it,
    // exactly once per call, on every successful (re)connect and again
    // whenever the caller invokes `refreshStatus()` (App.tsx does, after a
    // control POST resolves 200).
    const seedFromStatus = async () => {
      try {
        const response = await fetchStatusFn(secret);
        const daemon = response?.daemon;
        if (!daemon) return;
        const nowIso = new Date(nowFn()).toISOString();
        if (daemon.ear) {
          const ear = daemon.ear.active ?? null;
          const hot = ear !== null && !daemon.ear.muted;
          const micData: MicData = { hot, ear };
          dispatch({
            type: "mic",
            envelope: {
              v: 1,
              kind: "mic",
              ts: nowIso,
              seq: -1,
              source: "http-status-seed",
              data: micData,
            },
            data: micData,
          });
        }
        if (daemon.clients) {
          const clientsData: ClientsData = {
            count: daemon.clients.count,
            remote: daemon.clients.remote,
          };
          dispatch({
            type: "clients",
            envelope: {
              v: 1,
              kind: "clients",
              ts: nowIso,
              seq: -1,
              source: "http-status-seed",
              data: clientsData,
            },
            data: clientsData,
          });
        }
        dispatch({
          type: "recallStatusMode",
          mode: daemon.recall?.mode ?? null,
          configuredMode: daemon.recall?.configured_mode ?? null,
        });
      } catch {
        // GET /api/status failing is not fatal to an already-open stream —
        // the seed simply doesn't happen this time; existing state (and
        // whatever live events keep arriving) is left alone.
      }
    };
    seedFromStatusRef.current = seedFromStatus;

    const handleFrame = (frame: { kind: string; data: string }) => {
      // Round 2 fix, still true under the new transport: `frame.data` is
      // the WHOLE envelope on the wire ({v, kind, ts, seq, source, data:
      // {...}}), never the inner `data` object on its own.
      const result = parseEnvelopeFrame(frame.data);
      if (!result.envelope) {
        dispatch({ type: "dropped" });
        return;
      }
      const kind = frame.kind as EventKind;
      if (!(EVENT_KINDS as readonly string[]).includes(kind)) {
        return; // an unknown kind is simply not seen, never mis-rendered
      }
      const envelope = result.envelope as EventEnvelope<EventKind>;
      if (kind === "heartbeat") {
        setLastHeartbeatAtMs(nowFn());
      }
      switch (kind) {
        case "state":
          dispatch({ type: "state", envelope: envelope as EventEnvelope<"state">, data: envelope.data as StateData });
          break;
        case "mic":
          dispatch({ type: "mic", envelope: envelope as EventEnvelope<"mic">, data: envelope.data as MicData });
          break;
        case "turn":
          dispatch({ type: "turn", envelope: envelope as EventEnvelope<"turn">, data: envelope.data as TurnData });
          break;
        case "features":
          dispatch({
            type: "features",
            envelope: envelope as EventEnvelope<"features">,
            data: envelope.data as FeaturesData,
          });
          break;
        case "clients":
          dispatch({
            type: "clients",
            envelope: envelope as EventEnvelope<"clients">,
            data: envelope.data as ClientsData,
          });
          break;
        case "transcript":
        case "reply":
          dispatch({
            type: "speech",
            envelope: envelope as EventEnvelope<"transcript" | "reply">,
            data: envelope.data as TranscriptData | ReplyData,
          });
          break;
        case "degradation":
          dispatch({
            type: "degradation",
            envelope: envelope as EventEnvelope<"degradation">,
            data: envelope.data as DegradationData,
          });
          break;
        case "heartbeat":
          // no data state to update beyond lastHeartbeatAtMs, set above
          break;
        default:
          break;
      }
    };

    const handle = connect(url, headers, {
      onOpen: () => {
        everOpened = true;
        setOpened(true);
        setOpenedAtMs(nowFn());
        setErroredClosed(false);
        setUnauthorized(false);
        void seedFromStatus();
      },
      onError: () => {
        if (everOpened) {
          setErroredClosed(true);
        } else {
          setUnauthorized(true);
        }
      },
      onFrame: handleFrame,
    });

    return () => {
      handle.close();
    };
    // `url`, `secret` and `reconnectKey` are the only things that should
    // reopen the connection; the other options are DI seams a caller passes
    // once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, secret, reconnectKey]);

  useEffect(() => {
    const id = setInterval(() => setNow(nowFn()), checkIntervalMs);
    return () => clearInterval(id);
  }, [nowFn, checkIntervalMs]);

  const status: ConnectionStatus = useMemo(() => {
    if (unauthorized) return "unauthorized";
    if (erroredClosed) return "disconnected";
    if (!opened) return "connecting";
    const baseline = lastHeartbeatAtMs ?? openedAtMs;
    if (baseline === null) return "connected";
    if (now - baseline >= DISCONNECTED_AFTER_MS) return "disconnected";
    return "connected";
  }, [unauthorized, erroredClosed, opened, lastHeartbeatAtMs, openedAtMs, now]);

  return {
    status,
    state: data.state,
    mic: data.mic,
    turn: data.turn,
    features: data.features,
    clients: data.clients,
    transcript: data.transcript,
    degradations: data.degradations,
    lastHeartbeatAtMs,
    droppedFrames: data.droppedFrames,
    recallStatusMode: data.recallStatusMode,
    recallConfiguredMode: data.recallConfiguredMode,
    refreshStatus,
  };
}

export { isEventEnvelope };
