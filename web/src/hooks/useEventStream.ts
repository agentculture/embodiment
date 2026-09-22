// hooks/useEventStream.ts
//
// The EventSource hook against t13's bus-event schema (task t17). Opens one
// SSE connection (t16's projection of embodiment/bus.py) and registers an
// `addEventListener` for every kind in EVENT_KINDS — EventSource has no
// wildcard listener, so an unknown future kind is simply not seen, never
// mis-rendered, mirroring the pattern culture-nodes' useSharedEvents.tsx
// uses against its own fixed vocabulary.
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

import { useEffect, useMemo, useReducer, useRef, useState } from "react";
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
};

type Action =
  | { type: "state"; envelope: EventEnvelope<"state">; data: StateData }
  | { type: "mic"; envelope: EventEnvelope<"mic">; data: MicData }
  | { type: "turn"; envelope: EventEnvelope<"turn">; data: TurnData }
  | { type: "features"; envelope: EventEnvelope<"features">; data: FeaturesData }
  | { type: "clients"; envelope: EventEnvelope<"clients">; data: ClientsData }
  | { type: "speech"; envelope: EventEnvelope<"transcript" | "reply">; data: TranscriptData | ReplyData }
  | { type: "degradation"; envelope: EventEnvelope<"degradation">; data: DegradationData }
  | { type: "dropped" };

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
    default:
      return prev;
  }
}

export interface UseEventStreamOptions {
  /** Test/DI seam: defaults to `(url) => new EventSource(url)`. */
  eventSourceFactory?: (url: string) => EventSource;
  /** Test seam: defaults to Date.now. */
  nowFn?: () => number;
  /** How often to re-check the heartbeat deadline. Chosen, not measured —
   *  1s is fine granularity for a UI status pill. */
  checkIntervalMs?: number;
  /**
   * Round 3: bump this (any value that changes by `!==`) to force the
   * current EventSource closed and a fresh one opened, without changing
   * `url`. EventSource has no "reconnect now" method of its own, and t16's
   * guard (embodiment/http/guard.py) refuses `GET /api/events` until the
   * `embodiment_secret` cookie is set — which happens strictly after this
   * hook's connect effect already fired once on mount. The caller (App.tsx)
   * bumps this after writing the cookie so the stream actually reconnects
   * with the credential now present.
   */
  reconnectKey?: unknown;
}

function defaultFactory(url: string): EventSource {
  return new EventSource(url);
}

export function useEventStream(
  url: string,
  options: UseEventStreamOptions = {},
): EventStreamSnapshot {
  const {
    eventSourceFactory = defaultFactory,
    nowFn = Date.now,
    checkIntervalMs = 1000,
    reconnectKey,
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

  useEffect(() => {
    const source = eventSourceFactory(url);
    setOpened(false);
    setOpenedAtMs(null);
    setErroredClosed(false);
    setUnauthorized(false);
    // Reset per connection attempt: whether THIS EventSource has ever
    // fired onopen. An error before the first successful open is the best
    // signal this API gives for "the guard refused the credential" (t16's
    // guard.py returns 401 for a missing/bad secret; EventSource exposes no
    // HTTP status to JS at all — see round 3's report).
    let everOpened = false;

    source.onopen = () => {
      everOpened = true;
      setOpened(true);
      setOpenedAtMs(nowFn());
      setErroredClosed(false);
      setUnauthorized(false);
    };
    source.onerror = () => {
      // readyState 2 (CLOSED) means the browser gave up retrying; anything
      // else means it is already reconnecting on its own — either way,
      // report the fault rather than staying silently "connected".
      if (source.readyState === 2 /* CLOSED */) {
        if (everOpened) {
          setErroredClosed(true);
        } else {
          setUnauthorized(true);
        }
      }
    };

    const makeHandler =
      <K extends EventKind>(kind: K) =>
      (raw: MessageEvent<string>) => {
        // Round 2 fix: raw.data is the WHOLE envelope on the wire
        // ({v, kind, ts, seq, source, data: {...}}), never the inner
        // `data` object on its own — a real SSE server serving the
        // committed fixtures verbatim caught the previous version of this
        // handler fabricating an envelope around what it wrongly assumed
        // was already `data`, so every pane read `.text`/`.code`/`.env` off
        // the OUTER envelope and got `undefined`.
        const result = parseEnvelopeFrame(raw.data);
        if (!result.envelope) {
          dispatch({ type: "dropped" });
          return;
        }
        const envelope = result.envelope as EventEnvelope<K>;
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

    const handlers = EVENT_KINDS.map((kind) => [kind, makeHandler(kind)] as const);
    for (const [kind, handler] of handlers) {
      source.addEventListener(kind, handler as EventListener);
    }

    return () => {
      for (const [kind, handler] of handlers) {
        source.removeEventListener(kind, handler as EventListener);
      }
      source.close();
    };
    // `url` and `reconnectKey` are the only things that should reopen the
    // connection; the other options are DI seams a caller passes once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, reconnectKey]);

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
  };
}

export { isEventEnvelope };
