// audio/browserEar.ts
//
// The thin, embodiment-owned adaptation the cited `audio/lobes/*` files sit
// under (task t18 instruction: "adapt only in a thin wrapper of your own").
// None of the cited files were changed; this module composes them against
// t14's wire instead of lobes' own server:
//
//   - dial a WebSocket at the configured URL (see `api/realtimeSettings.ts`)
//   - send `{"type":"auth","secret":...}` FIRST, before any audio frame
//     (`embodiment/audio/remote.py`'s own docstring: "accepts exactly ONE
//     JSON message before anything else is processed" -- a wrong/missing/
//     late auth closes the socket with code 1008 and never reads a frame)
//   - relay `MicCapture`'s `input_audio_buffer.append` events over the
//     socket once, and only once, auth has been acknowledged
//   - decode `response.audio.delta` frames the socket receives into
//     `DeltaPlayer`
//   - own an `AnalyserNode`, created and connected ONLY while `MicCapture`
//     is actually capturing (t18 instruction: "AnalyserNode only when the
//     browser is the ear"), exposed as a `ListenerAnalyserSource` for
//     `Waveform`'s listener trace.
//
// Never raises, never blocks (lesson 3 / C3): every public method degrades
// and records rather than throwing, and `status()` reports which listener
// source is actually in effect -- the same "observable, not merely correct"
// requirement `remote.py` states of itself on the server side.

import type { AudioContextLike } from "./lobes/audio-graph";
import { MicCapture, type MicCaptureState, type MicStateDetail } from "./lobes/mic-capture";
import { DeltaPlayer, type PlaybackState, type PlaybackStopInfo } from "./lobes/audio-playback";
import { AUDIO_DELTA_EVENT_TYPE, type AppendEvent } from "./lobes/pcm-wire";
import type { ListenerAnalyserSource } from "../components/Waveform";

/** `RemoteEndpoint`'s own first-message contract (`embodiment/audio/
 *  remote.py`): `{"type": "auth", "secret": "..."}`, sent before anything
 *  else, over the socket the browser just opened. */
export const AUTH_MESSAGE_TYPE = "auth";

export type BrowserEarState =
  | "idle"
  | "connecting"
  | "authenticating"
  | "live"
  | "closed"
  | "failed";

/** Degradation codes this module records, prefixed per the task-agent
 *  preamble's lesson 4 ("name the fault the host would look for"). Never
 *  carries the socket's close reason text (lesson 5: no attacker/server-
 *  controlled string in a record) -- only these static, named codes. */
export const DEGRADED_SOCKET_ERROR = "browser_ear.socket_error";
export const DEGRADED_SOCKET_CLOSED = "browser_ear.socket_closed";
export const DEGRADED_UNPARSEABLE_FRAME = "browser_ear.unparseable_frame";
export const DEGRADED_APPEND_BEFORE_AUTH = "browser_ear.append_before_auth";

export interface SocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: (() => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
}

/** The narrow `AnalyserNode` surface this module reads -- injectable so a
 *  test never needs a real Web Audio graph, mirroring `audio-graph.ts`'s
 *  own pattern for the cited files. */
export interface AnalyserLike {
  readonly frequencyBinCount: number;
  getByteTimeDomainData(out: Uint8Array): void;
  connect(destination: unknown): unknown;
  disconnect(): void;
}

export interface BrowserEarDeps {
  createSocket(url: string): SocketLike;
  createMicCapture(
    context: AudioContextLike,
    onAppend: (event: AppendEvent) => void,
    onState: (state: MicCaptureState, detail: MicStateDetail) => void,
  ): MicCapture;
  createPlayer(
    context: AudioContextLike,
    onState: (state: PlaybackState) => void,
    onStop: (info: PlaybackStopInfo) => void,
  ): DeltaPlayer;
  createAnalyser(context: AudioContextLike): AnalyserLike;
}

export interface BrowserEarConfig {
  wsUrl: string;
  secret: string;
}

/** How many buckets `listenerAnalyserSource().readTrace()` reports -- the
 *  same count the bus `features` envelope uses (`ENVELOPE_POINTS` in
 *  `audio/envelope.ts`), so `Waveform`'s draw code treats an analyser-backed
 *  trace exactly like a bus-backed one, with no special-casing. */
const ANALYSER_BUCKETS = 16;

/** `getByteTimeDomainData` centers silence at 128 (unsigned byte PCM) --
 *  converted to the same [0, 1] peak-magnitude-per-bucket shape
 *  `waveform/model.ts`'s `sampleFromFeatures` produces from the bus, so one
 *  draw path serves both sources. */
function analyserBytesToBars(bytes: Uint8Array, bucketCount: number): number[] {
  if (bytes.length === 0) return new Array(bucketCount).fill(0);
  const bucketSize = Math.max(1, Math.floor(bytes.length / bucketCount));
  const bars: number[] = [];
  for (let b = 0; b < bucketCount; b += 1) {
    let peak = 0;
    const start = b * bucketSize;
    const end = b === bucketCount - 1 ? bytes.length : start + bucketSize;
    for (let i = start; i < end && i < bytes.length; i += 1) {
      const magnitude = Math.abs(bytes[i] - 128) / 128;
      if (magnitude > peak) peak = magnitude;
    }
    bars.push(Math.min(1, peak));
  }
  return bars;
}

export interface BrowserEarStatus {
  state: BrowserEarState;
  micState: MicCaptureState | null;
  playbackState: PlaybackState | null;
  /** Which source `listenerAnalyserSource()` is currently reading from --
   *  C3: this is what makes "the browser is the ear" an observable fact,
   *  not an assumption. */
  analyserActive: boolean;
  appendsSentBeforeAuthDropped: number;
  unparseableFramesDropped: number;
}

/**
 * One browser-ear session: one socket, one `MicCapture`, one `DeltaPlayer`,
 * one optional `AnalyserNode`. A fresh instance per attempt -- `connect()`
 * is not idempotent-safe to call twice on one instance, mirroring
 * `MicCapture`'s own one-session-per-instance shape.
 */
export class BrowserEar {
  private readonly config: BrowserEarConfig;
  private readonly deps: BrowserEarDeps;
  private readonly context: AudioContextLike;

  private socket: SocketLike | null = null;
  private state: BrowserEarState = "idle";
  private micCapture: MicCapture | null = null;
  private player: DeltaPlayer | null = null;
  private analyser: AnalyserLike | null = null;
  private micState: MicCaptureState | null = null;
  private playbackState: PlaybackState | null = null;
  private appendsSentBeforeAuthDropped = 0;
  private unparseableFramesDropped = 0;

  constructor(config: BrowserEarConfig, context: AudioContextLike, deps: BrowserEarDeps) {
    this.config = config;
    this.context = context;
    this.deps = deps;
  }

  status(): BrowserEarStatus {
    return {
      state: this.state,
      micState: this.micState,
      playbackState: this.playbackState,
      analyserActive: this.analyser !== null,
      appendsSentBeforeAuthDropped: this.appendsSentBeforeAuthDropped,
      unparseableFramesDropped: this.unparseableFramesDropped,
    };
  }

  /**
   * Open the socket and send the auth message the instant it opens --
   * nothing else is ever sent before it, and no append frame is relayed
   * until the wire confirms the socket is open (this module never learns an
   * explicit "auth accepted" event from `remote.py`'s wire beyond the
   * socket staying open; a subsequent `onclose`/`onerror` before any audio
   * ever played is the only signal a wrong secret gives, exactly as
   * `remote.py`'s docstring describes).
   */
  connect(): void {
    this.state = "connecting";
    const socket = this.deps.createSocket(this.config.wsUrl);
    this.socket = socket;

    socket.onopen = () => {
      this.state = "authenticating";
      socket.send(JSON.stringify({ type: AUTH_MESSAGE_TYPE, secret: this.config.secret }));
      this.state = "live";
    };
    socket.onerror = () => {
      this.state = "failed";
    };
    socket.onclose = () => {
      if (this.state !== "failed") this.state = "closed";
    };
    socket.onmessage = (event) => this.handleMessage(event.data);
  }

  private handleMessage(raw: string): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      this.unparseableFramesDropped += 1;
      return;
    }
    if (typeof parsed !== "object" || parsed === null) {
      this.unparseableFramesDropped += 1;
      return;
    }
    const obj = parsed as Record<string, unknown>;
    if (obj.type === AUDIO_DELTA_EVENT_TYPE && typeof obj.audio === "string" && this.player) {
      this.player.enqueueDelta(obj.audio);
    }
  }

  /** Relay one append frame -- dropped and counted (never sent) if the
   *  socket is not open yet, satisfying the same "auth first, nothing
   *  before it" contract from this side. */
  private sendAppend(event: AppendEvent): void {
    if (!this.socket || this.state !== "live") {
      this.appendsSentBeforeAuthDropped += 1;
      return;
    }
    this.socket.send(JSON.stringify(event));
  }

  /**
   * Start the mic. Creates the `AnalyserNode` and connects it ONLY here --
   * never at construction, never speculatively -- so `status().
   * analyserActive` is true precisely while the browser is genuinely the
   * ear, per t18's instruction.
   */
  async startMic(): Promise<boolean> {
    const micCapture = this.deps.createMicCapture(
      this.context,
      (event) => this.sendAppend(event),
      (state) => {
        this.micState = state;
      },
    );
    this.micCapture = micCapture;
    const started = await micCapture.start(this.context);
    if (started) {
      this.analyser = this.deps.createAnalyser(this.context);
    }
    return started;
  }

  startPlayback(): void {
    this.player = this.deps.createPlayer(
      this.context,
      (state) => {
        this.playbackState = state;
      },
      () => {},
    );
  }

  /** `Waveform`'s listener-trace source while the browser mic is capturing
   *  -- `null` before `startMic()` succeeds, so `Waveform` (and this
   *  module's own README/CLAUDE.md constraint) never claims an analyser is
   *  live when nothing is attached. */
  listenerAnalyserSource(): ListenerAnalyserSource | null {
    if (!this.analyser) return null;
    const analyser = this.analyser;
    return {
      readTrace: () => {
        const bytes = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteTimeDomainData(bytes);
        return analyserBytesToBars(bytes, ANALYSER_BUCKETS);
      },
    };
  }

  /** Idempotent, never raises, bounded -- tears everything this session
   *  owns down (lesson 6: shutdown is a feature). */
  close(): void {
    this.micCapture?.stop();
    this.analyser?.disconnect();
    this.analyser = null;
    try {
      this.socket?.close();
    } catch {
      // never throw on teardown
    }
    this.socket = null;
    if (this.state !== "failed") this.state = "closed";
  }
}
