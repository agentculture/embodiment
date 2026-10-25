// ReadAloud.tsx
//
// Read an item aloud on this device (embodiment#107, plan t4): the page asks
// the daemon for the next unit (api/reading.ts), synthesises its text with
// POST /api/speak, plays the WAV here, and reports the unit heard ONLY from the
// audio's `ended` event for that unit's exact position. A pause, an audio
// error, Stop reading, moving to another paragraph, or the page going away
// (reload, unmount) bumps a session token, so a late `ended` can never report a
// unit that was not heard to its end. While one unit plays, the following one's
// audio is synthesised (prefetch), so speech does not stall between paragraphs.
// The prefetch never asks the daemon for that unit: next?section=&paragraph= is
// a seek that re-pins the cursor (deviation d1), and seeking ahead while this
// unit plays would make a reload skip it. The following text comes from the
// reader's own copy of the item (`peekFollowing`); once this unit is heard, the
// page asks next at the cursor (a small JSON call) and plays the prefetched
// audio when its text is the unit's text, else synthesises it then.
// Previous / Next paragraph are seeks: their unit is reported heard on ended
// like any other. The daemon owns the cursor (h16).
//
// The dock's look is the approved mockup (scratchpad mockup107, 2026-10-26):
// one segment per paragraph of the section, the playing one filling with its
// audio's currentTime/duration; the same value drives the paragraph's edge line
// in the reader and the section's share of the map (see `progressOf`).

import { useCallback, useEffect, useRef, useState } from "react";
import { speakMessage } from "../api/audio";
import {
  fetchNext,
  reportHeard,
  type At,
  type ReadingClientOptions,
  type ReadingUnit,
} from "../api/reading";

export type ListenStatus =
  | "idle"
  | "loading"
  | "playing"
  | "paused"
  | "stopped"
  | "blocked"
  | "done"
  | "gone"
  | "error";

export interface ListenState {
  status: ListenStatus;
  /** The unit playing, paused or last stopped; null before the first one arrives. */
  unit: ReadingUnit | null;
  /** How far this unit's audio has played, 0..1 (currentTime / duration). */
  played: number;
  /** A fixed failure code (error), never a response body. */
  code: string | null;
  /** A heard report that did not reach the daemon: listening goes on, the position may lag. */
  note: string | null;
}

export interface ListenControls {
  /** Start or continue: at the cursor when `at` is absent. Call from a tap. */
  start: (at?: At) => void;
  pause: () => void;
  /** Continue after a pause: the paragraph plays again from its start. */
  resume: () => void;
  stop: () => void;
  /** The previous (-1) or next (+1) paragraph; nothing is reported heard. */
  move: (delta: -1 | 1) => void;
}

export type AudioFactory = () => HTMLAudioElement;

export interface UseReadAloudOptions {
  itemId: string;
  secret: string;
  clientOptions?: ReadingClientOptions;
  audioFactory?: AudioFactory;
  /** The text of the unit after `unit`, from the reader's copy of the item; null when unknown. */
  peekFollowing?: (unit: ReadingUnit) => string | null;
}

const IDLE: ListenState = { status: "idle", unit: null, played: 0, code: null, note: null };

/** One paragraph's progress (parts folded in) and its section's share. Pure. */
export function progressOf(unit: ReadingUnit, played: number): { paragraph: number; section: number } {
  const p = Math.min(1, Math.max(0, Number.isFinite(played) ? played : 0));
  const paragraph = (unit.part + p) / unit.partCount;
  return { paragraph, section: (unit.paragraph + paragraph) / unit.paragraphCount };
}

/** "phone" or "laptop": which device the audio comes out of. */
export function deviceKind(): "phone" | "laptop" {
  try {
    if (/iPhone|iPod|Android.*Mobile|Mobi/i.test(navigator.userAgent)) return "phone";
    if (typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse) and (max-width: 860px)").matches) {
      return "phone";
    }
  } catch {
    /* fall through */
  }
  return "laptop";
}

/** A 12 ms silent WAV, played inside the tap so a later play() after a fetch is allowed. */
function silentWav(): Blob {
  const n = 100;
  const buf = new ArrayBuffer(44 + n);
  const v = new DataView(buf);
  const w = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF");
  v.setUint32(4, 36 + n, true);
  w(8, "WAVEfmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, 8000, true);
  v.setUint32(28, 8000, true);
  v.setUint16(32, 1, true);
  v.setUint16(34, 8, true);
  w(36, "data");
  v.setUint32(40, n, true);
  for (let i = 0; i < n; i++) v.setUint8(44 + i, 128);
  return new Blob([buf], { type: "audio/wav" });
}

function makeUrl(blob: Blob): string {
  try {
    return URL.createObjectURL(blob);
  } catch {
    return "";
  }
}

function dropUrl(url: string) {
  if (!url) return;
  try {
    URL.revokeObjectURL(url);
  } catch {
    /* nothing held */
  }
}

type Ready = { kind: "unit"; unit: ReadingUnit; blob: Blob } | { kind: "done" } | { kind: "failed"; code: string };

interface Prefetch {
  token: number;
  text: string;
  audio: Promise<Blob | null>;
}

const norm = (t: string) => t.replace(/\s+/g, " ").trim();

export function useReadAloud({ itemId, secret, clientOptions, audioFactory, peekFollowing }: UseReadAloudOptions): [ListenState, ListenControls] {
  const [state, setState] = useState<ListenState>(IDLE);
  const stateRef = useRef<ListenState>(IDLE);
  const token = useRef(0);
  const audio = useRef<HTMLAudioElement | null>(null);
  const url = useRef("");
  const prefetch = useRef<Prefetch | null>(null);
  const opts = useRef({ itemId, secret, clientOptions, audioFactory, peekFollowing });
  opts.current = { itemId, secret, clientOptions, audioFactory, peekFollowing };

  const set = useCallback((patch: Partial<ListenState>) => {
    stateRef.current = { ...stateRef.current, ...patch };
    setState(stateRef.current);
  }, []);

  const element = useCallback((): HTMLAudioElement | null => {
    if (!audio.current) {
      try {
        audio.current = (opts.current.audioFactory ?? (() => new Audio()))();
      } catch {
        audio.current = null;
      }
    }
    return audio.current;
  }, []);

  /** Silence the element and detach every handler: nothing it does now is a report. */
  const halt = useCallback(() => {
    const a = audio.current;
    if (!a) return;
    a.onended = null;
    a.onerror = null;
    a.ontimeupdate = null;
    try {
      a.pause();
    } catch {
      /* already stopped */
    }
  }, []);

  const ready = useCallback(async (at: At | undefined): Promise<Ready> => {
    const { itemId: id, secret: key, clientOptions: co } = opts.current;
    const next = await fetchNext(id, key, co, at);
    if (!next.ok) return { kind: "failed", code: next.code };
    if (next.value.done) return { kind: "done" };
    const pre = prefetch.current;
    if (!at && pre && norm(pre.text) === norm(next.value.unit.text)) {
      const blob = await pre.audio;
      if (blob) return { kind: "unit", unit: next.value.unit, blob };
    }
    const spoken = await speakMessage(next.value.unit.text, key, co);
    if (!spoken.ok) return { kind: "failed", code: spoken.code };
    return { kind: "unit", unit: next.value.unit, blob: spoken.blob };
  }, []);

  // Declared before use through refs, so the handlers always see the current ones.
  const playRef = useRef<(t: number, unit: ReadingUnit, blob: Blob) => void>(() => {});
  const loadRef = useRef<(t: number, at: At | undefined) => void>(() => {});

  const settle = useCallback(
    (t: number, r: Ready) => {
      if (t !== token.current) return;
      if (r.kind === "unit") playRef.current(t, r.unit, r.blob);
      else if (r.kind === "done") set({ status: "done", played: 0 });
      else if (r.code === "library-item-gone") set({ status: "gone", code: r.code });
      else set({ status: "error", code: r.code });
    },
    [set],
  );

  loadRef.current = (t: number, at: At | undefined) => {
    if (t !== token.current) return;
    set({ status: "loading", code: null, played: 0 });
    void ready(at).then((r) => settle(t, r));
  };

  const onEnded = useCallback(
    (t: number, unit: ReadingUnit) => {
      if (t !== token.current || stateRef.current.status !== "playing" || stateRef.current.unit !== unit) return;
      set({ played: 1 });
      const { itemId: id, secret: key, clientOptions: co } = opts.current;
      void reportHeard(id, unit.position, key, co).then((heard) => {
        if (t !== token.current) return;
        set({ note: heard.ok ? null : heard.code });
        // Continue at the cursor just moved; the prefetched audio is used when it matches.
        loadRef.current(t, undefined);
      });
    },
    [set, settle],
  );

  playRef.current = (t: number, unit: ReadingUnit, blob: Blob) => {
    if (t !== token.current) return;
    const a = element();
    if (!a) {
      set({ status: "error", code: "no-audio", unit });
      return;
    }
    halt();
    dropUrl(url.current);
    url.current = makeUrl(blob);
    a.src = url.current;
    try {
      a.currentTime = 0;
    } catch {
      /* not seekable yet */
    }
    a.ontimeupdate = () => {
      if (t !== token.current) return;
      const d = a.duration;
      if (Number.isFinite(d) && d > 0) set({ played: Math.min(1, a.currentTime / d) });
    };
    a.onerror = () => {
      if (t !== token.current) return;
      halt();
      set({ status: "error", code: "playback-failed" });
    };
    a.onended = () => onEnded(t, unit);
    set({ status: "playing", unit, played: 0, code: null });
    let started: Promise<void> | undefined;
    try {
      started = a.play();
    } catch {
      started = Promise.reject(new Error("play"));
    }
    void Promise.resolve(started).catch((e: unknown) => {
      if (t !== token.current) return;
      halt();
      const blocked = e instanceof Error && e.name === "NotAllowedError";
      set({ status: blocked ? "blocked" : "error", code: blocked ? null : "playback-failed" });
    });
    let text: string | null = null;
    try {
      text = opts.current.peekFollowing?.(unit) ?? null;
    } catch {
      text = null;
    }
    if (text && norm(text)) {
      const { secret: key, clientOptions: co } = opts.current;
      const audioReady = speakMessage(text, key, co).then((r) => (r.ok ? r.blob : null));
      prefetch.current = { token: t, text, audio: audioReady };
    } else {
      prefetch.current = null;
    }
  };

  const begin = useCallback(
    (at: At | undefined) => {
      token.current += 1;
      halt();
      prefetch.current = null;
      loadRef.current(token.current, at);
    },
    [halt],
  );

  const start = useCallback(
    (at?: At) => {
      // Inside the tap: let this element play later, after the fetch resolves.
      const a = element();
      if (a && !a.src) {
        const u = makeUrl(silentWav());
        if (u) {
          a.src = u;
          try {
            void Promise.resolve(a.play()).catch(() => {});
          } catch {
            /* the tap still counts on most browsers */
          }
        }
      }
      begin(at);
    },
    [begin, element],
  );

  const pause = useCallback(() => {
    const s = stateRef.current.status;
    if (s !== "playing" && s !== "loading") return;
    token.current += 1;
    halt();
    set({ status: "paused", played: 0 });
  }, [halt, set]);

  const resume = useCallback(() => {
    const unit = stateRef.current.unit;
    begin(unit ? { section: unit.section, paragraph: unit.paragraph } : undefined);
  }, [begin]);

  const stop = useCallback(() => {
    token.current += 1;
    halt();
    prefetch.current = null;
    set({ status: "stopped", played: 0 });
  }, [halt, set]);

  const move = useCallback(
    (delta: -1 | 1) => {
      const u = stateRef.current.unit;
      if (!u) return;
      let at: At | null;
      if (delta < 0) {
        at = u.paragraph > 0 ? { section: u.section, paragraph: u.paragraph - 1 } : u.section > 0 ? { section: u.section - 1, paragraph: 0 } : { section: 0, paragraph: 0 };
      } else {
        at = u.paragraph + 1 < u.paragraphCount ? { section: u.section, paragraph: u.paragraph + 1 } : u.section + 1 < u.sectionCount ? { section: u.section + 1, paragraph: 0 } : null;
      }
      if (!at) return;
      begin(at);
    },
    [begin],
  );

  // A new item, or the page going away: nothing playing now is reported heard.
  useEffect(() => {
    set(IDLE);
    return () => {
      token.current += 1;
      halt();
      dropUrl(url.current);
      url.current = "";
      prefetch.current = null;
      stateRef.current = IDLE;
    };
  }, [itemId, halt, set]);

  return [state, { start, pause, resume, stop, move }];
}

export interface ReadAloudDockProps {
  state: ListenState;
  controls: ListenControls;
  device?: "phone" | "laptop";
}

const IconPrev = () => (
  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path d="M18 6 9 12l9 6zM6 6v12" />
  </svg>
);
const IconNext = () => (
  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <path d="m6 6 9 6-9 6zM18 6v12" />
  </svg>
);

/** The listening dock: sticky at the reader's bottom (styles/library.css .listen-dock). */
export function ReadAloudDock({ state, controls, device = deviceKind() }: ReadAloudDockProps) {
  const { status, unit } = state;
  const going = status === "playing" || status === "loading";
  const prog = unit ? progressOf(unit, status === "playing" ? state.played : 0) : null;
  const where = (() => {
    if (status === "done") return "You reached the end of this item.";
    if (status === "gone") return "This item is no longer in the library.";
    if (!unit) return "Getting the first paragraph ready.";
    const para = `paragraph ${unit.paragraph + 1} of ${unit.paragraphCount}`;
    const part = unit.partCount > 1 ? ` Part ${unit.part + 1} of ${unit.partCount}.` : "";
    if (status === "paused" || status === "error" || status === "blocked") {
      return `Paused at ${para}. This paragraph plays again from its start.${part}`;
    }
    if (status === "loading") return `Getting the next paragraph ready. Section ${unit.section + 1} of ${unit.sectionCount}.`;
    return `Paragraph ${unit.paragraph + 1} of ${unit.paragraphCount} in this section. Section ${unit.section + 1} of ${unit.sectionCount}.${part}`;
  })();
  const playLabel = going ? "Pause" : status === "blocked" ? "Play" : "Continue";
  return (
    <div className="listen-dock" role="group" aria-label="Reading aloud" data-status={status}>
      <div className="transport">
        <button type="button" className="icon-btn" aria-label="Previous paragraph" disabled={!unit} onClick={() => controls.move(-1)}>
          <IconPrev />
        </button>
        <button
          type="button"
          className="play"
          aria-label={playLabel}
          disabled={status === "done" || status === "gone"}
          onClick={() => (going ? controls.pause() : controls.resume())}
        >
          {going ? (
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <rect x="6" y="5" width="4" height="14" rx="1" />
              <rect x="14" y="5" width="4" height="14" rx="1" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
              <path d="M8 5v14l11-7z" />
            </svg>
          )}
        </button>
        <button type="button" className="icon-btn" aria-label="Next paragraph" disabled={!unit} onClick={() => controls.move(1)}>
          <IconNext />
        </button>
      </div>
      <div className="where">
        <div className="where-section" dir="auto">
          {unit ? unit.heading || `Section ${unit.section + 1}` : "Reading aloud"}
        </div>
        <div className="where-pos" dir="auto" aria-live="polite">
          {where}
        </div>
        {unit && prog && (
          <div className="where-bar" aria-hidden="true">
            {Array.from({ length: unit.paragraphCount }, (_, i) =>
              i < unit.paragraph ? (
                <span key={i} className="heard" data-played="1" />
              ) : i === unit.paragraph ? (
                <span key={i} className="now" data-played={String(prog.paragraph)} style={{ ["--played" as string]: prog.paragraph }} />
              ) : (
                <span key={i} />
              ),
            )}
          </div>
        )}
        {status === "error" && (
          <p className="listen-note" role="alert" dir="auto">
            Reading stopped: this paragraph could not be played ({state.code}). Nothing was marked heard.
          </p>
        )}
        {status === "blocked" && (
          <p className="listen-note" role="alert" dir="auto">
            This browser waits for a tap before it plays sound. Tap play to start.
          </p>
        )}
        {state.note && (
          <p className="listen-note" role="status" dir="auto">
            Where you are was not saved ({state.note}); it is saved again after the next paragraph.
          </p>
        )}
      </div>
      <div className="dock-end">
        <span className="device" dir="auto">
          {device === "phone" ? (
            <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
              <rect x="7" y="2" width="10" height="20" rx="2" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
              <rect x="3" y="5" width="18" height="12" rx="2" />
              <path d="M8 21h8" />
            </svg>
          )}
          {going ? `Playing on this ${device}` : `Plays on this ${device}`}
        </span>
        <button type="button" className="pill-btn" onClick={controls.stop}>
          Stop reading
        </button>
      </div>
    </div>
  );
}
