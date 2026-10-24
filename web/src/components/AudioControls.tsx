// Provenance: the composer's speak / record / upload controls sit inside the form as in
// pocketsync/web/static/index.html (#composer) and app.js:790-808 (composer wiring).
// The recording and speech-to-speech logic is embodiment's own, not ported.
import type { ChatSource } from "../api/chat";
import { useRef, useState, type ReactNode } from "react";
import { postClip, speakMessage, type AudioClientOptions } from "../api/audio";

/**
 * The longest clip the daemon accepts, in seconds. Mirrors
 * `embodiment.daemon.text_turns.MAX_CLIP_SECONDS` (a Python test pins the two
 * equal): the stt clock behind it is derived from the measured transcription
 * speed (docs/live-test-results/2026-10-23-recall-gemma2.md).
 */
export const MAX_CLIP_SECONDS = 60;

/**
 * When a recording stops by itself. One second under the cap, so the
 * recorder's own stop latency cannot push the declared duration past the
 * daemon's refusal and lose a forgotten recording.
 */
export const RECORD_LIMIT_MS = (MAX_CLIP_SECONDS - 1) * 1000;

export interface ClipReply {
  transcript: string;
  reply: string;
  /** Serial of the turn the clip started, when the daemon reports one. */
  turnSerial?: number | null;
  /** The clip answer's cited sources, as chat's (library citations open the reader). */
  sources?: ChatSource[];
}

/** Plays /api/speak audio for one message. Failures show as role=alert. */
export function SpeakButton({
  text,
  secret,
  clientOptions,
}: {
  text: string;
  secret: string;
  clientOptions?: AudioClientOptions;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function speak() {
    if (busy) return;
    setBusy(true);
    setError(null);
    const out = await speakMessage(text, secret, clientOptions);
    if (!out.ok) {
      setError(out.code);
      setBusy(false);
      return;
    }
    let url = "";
    try {
      url = URL.createObjectURL(out.blob);
      const audio = new Audio(url);
      const release = () => URL.revokeObjectURL(url);
      audio.onended = release;
      await audio.play();
    } catch {
      if (url) URL.revokeObjectURL(url);
      setError("playback-failed");
    }
    setBusy(false);
  }

  return (
    <span className="speak">
      <button type="button" className="pill-btn" onClick={speak} disabled={busy} aria-label="Speak this message">
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <path d="M11 5 6 9H3v6h3l5 4z" />
          <path d="M15.5 8.5a5 5 0 0 1 0 7" />
          <path d="M18.5 5.5a9 9 0 0 1 0 13" />
        </svg>
        {busy ? "Speaking" : "Speak"}
      </button>
      {error && (
        <span className="chat__degraded" role="alert">
          speak unavailable: {error}
        </span>
      )}
    </span>
  );
}

export interface AudioControlsProps {
  secret: string;
  /** Called with the transcript and Gwen's reply after /api/clip succeeds. */
  onReply: (r: ClipReply) => void;
  clientOptions?: AudioClientOptions;
  /** Name of the speech-to-speech role the gateway advertises; undefined => none. */
  s2sRole?: string;
  /** The realtime voice control, shown beside the input box before Upload. */
  live?: ReactNode;
  /** The typed half of the box (text and Send), placed after the mic. */
  children?: ReactNode;
}

export function AudioControls({ secret, onReply, clientOptions, live, children }: AudioControlsProps) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const startedAt = useRef(0);
  const limit = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function send(clip: Blob, durationS?: number) {
    setBusy(true);
    const out = await postClip(clip, secret, clientOptions, durationS);
    if (out.ok) {
      setError(null);
      onReply({ transcript: out.transcript, reply: out.reply, turnSerial: out.turnSerial, sources: out.sources });
    } else {
      setError(out.code);
    }
    setBusy(false);
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setError(null);
    await send(file);
  }

  async function startRecording() {
    setError(null);
    if (typeof MediaRecorder === "undefined" || !MediaRecorder) {
      setError("recording-unsupported");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("microphone-unavailable");
      return;
    }
    const chunks: Blob[] = [];
    try {
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) chunks.push(ev.data);
      };
      rec.onstop = () => {
        if (limit.current !== null) {
          clearTimeout(limit.current);
          limit.current = null;
        }
        stream.getTracks().forEach((t) => t.stop());
        recorder.current = null;
        setRecording(false);
        const seconds = (Date.now() - startedAt.current) / 1000;
        void send(new Blob(chunks, { type: rec.mimeType || chunks[0]?.type || "" }), seconds);
      };
      recorder.current = rec;
      startedAt.current = Date.now();
      rec.start();
      limit.current = setTimeout(stopRecording, RECORD_LIMIT_MS);
      setRecording(true);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      setError("recording-unsupported");
    }
  }

  function stopRecording() {
    try {
      recorder.current?.stop();
    } catch {
      setError("recording-failed");
      setRecording(false);
    }
  }

  // Layout (operator, 2026-10-24): realtime voice and upload sit beside the input
  // box; the box holds only the mic, the text and Send. The disabled
  // speech-to-speech button is gone: the Live button (the sound-bars icon) is the
  // speech path now, through the room microphone.
  return (
    <>
      <div className="dock-side">
        {live}
        <label className="side-btn" title="Upload a recording">
          <span className="sr-only">Upload audio</span>
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
          </svg>
          <input
            className="sr-only"
            type="file"
            accept="audio/*"
            onChange={onFile}
            disabled={busy || recording}
          />
        </label>
      </div>
      <div className="dock">
        <div className="dock__row" data-recording={recording ? "true" : undefined}>
          {recording ? (
            <button type="button" className="mic mic--on" onClick={stopRecording} aria-label="Stop recording">
              <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
                <rect x="7" y="7" width="10" height="10" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              type="button"
              className="mic"
              onClick={startRecording}
              disabled={busy}
              aria-label="Record"
              title="Record a question (up to a minute)"
            >
              <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
                <rect x="9" y="2.5" width="6" height="12" rx="3" />
                <path d="M5 10.5a7 7 0 0 0 14 0M12 17.5V21" />
              </svg>
            </button>
          )}
          {recording && <span className="dock__rec">Recording. Tap the square to send.</span>}
          {busy && !recording && <span className="dock__rec">Transcribing</span>}
          {children}
          {error && (
            <p className="chat__degraded dock__error" role="alert">
              Audio did not go through ({error}).
            </p>
          )}
        </div>
      </div>
    </>
  );
}
