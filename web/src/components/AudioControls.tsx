// Provenance: the composer's speak / record / upload controls sit inside the form as in
// pocketsync/web/static/index.html (#composer) and app.js:790-808 (composer wiring).
// The recording and speech-to-speech logic is embodiment's own, not ported.
import type { ChatSource } from "../api/chat";
import { useRef, useState } from "react";
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
    <span className="chat__speak">
      <button type="button" onClick={speak} disabled={busy} aria-label="Speak this message">
        Speak
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
}

export function AudioControls({ secret, onReply, clientOptions, s2sRole }: AudioControlsProps) {
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

  return (
    <div className="chat__audio">
      {recording ? (
        <button type="button" onClick={stopRecording}>
          Stop recording
        </button>
      ) : (
        <button type="button" onClick={startRecording} disabled={busy}>
          Record
        </button>
      )}
      <label>
        Upload audio
        <input type="file" accept="audio/*" onChange={onFile} disabled={busy || recording} />
      </label>
      <button type="button" disabled aria-describedby="chat-s2s-reason">
        Speech-to-speech
      </button>
      <span id="chat-s2s-reason" className="chat__hint">
        {s2sRole === undefined ? (
          "no speech-to-speech role is advertised by the gateway"
        ) : (
          <>
            role <span dir="auto">{s2sRole}</span> advertised; not enabled in this release
          </>
        )}
      </span>
      {error && (
        <p className="chat__degraded" role="alert">
          audio unavailable: {error}
        </p>
      )}
    </div>
  );
}
