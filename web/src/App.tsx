import { useCallback, useState } from "react";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { VoiceControls } from "./components/VoiceControls";
import { Transcript } from "./components/Transcript";
import { DegradationsLog } from "./components/DegradationsLog";
import { RecallModeIndicator } from "./components/RecallModeIndicator";
import { RemoteViewers } from "./components/RemoteViewers";
import { Waveform } from "./components/Waveform";
import { useEventStream, type UseEventStreamOptions } from "./hooks/useEventStream";
import { parseControlOutcome, setMicMute, startVoice, stopVoice } from "./api/control";
import { loadInstallSecretFromSession, saveInstallSecretToSession } from "./api/secret";

export interface AppProps {
  /** Test seam: override the SSE endpoint's URL and connector. */
  eventsUrl?: string;
  eventStreamOptions?: UseEventStreamOptions;
}

const DEFAULT_EVENTS_URL = "/api/events";

type ControlAction = "start" | "stop" | "mute";

const ACTION_LABEL: Record<ControlAction, string> = {
  start: "start voice",
  stop: "stop voice",
  mute: "mic mute",
};

export default function App({ eventsUrl = DEFAULT_EVENTS_URL, eventStreamOptions }: AppProps) {
  // `secret` is the live-typed value, bound to the input. It is NEVER used
  // as a credential directly (round 5 fix) -- only `applySecret` reads it,
  // at the moment "Apply" is submitted.
  const [secret, setSecret] = useState(() => loadInstallSecretFromSession());
  // `appliedSecret` is the ONE credential this dashboard ever sends,
  // whether to the stream or to a control POST. It is deliberately NOT the
  // same as `secret`: reconnecting the stream (or authenticating a click)
  // on every keystroke would be wasteful and would spam t16's guard. It
  // changes only via `applySecret` (the "Apply" button), and initializes
  // from sessionStorage directly -- so the FIRST `useEventStream` call
  // already carries a restored secret, with no separate mount-effect /
  // extra reconnect cycle needed.
  const [appliedSecret, setAppliedSecret] = useState(() => loadInstallSecretFromSession());
  // An explicit escape hatch alongside `appliedSecret` itself already being
  // a reconnect trigger -- re-applying the IDENTICAL secret value still
  // forces a reconnect (e.g. after the guard was reconfigured server-side).
  const [reconnectVersion, setReconnectVersion] = useState(0);
  // The most recent control POST's refusal, if any -- cleared on the next
  // successful one. Never carries anything but the refusal CODE (never the
  // secret, never the daemon's free-text `message`) -- api/control.ts's
  // parseControlOutcome already enforces that boundary.
  const [controlRefusal, setControlRefusal] = useState<{ action: ControlAction; code: string } | null>(
    null,
  );

  const applySecret = useCallback((value: string) => {
    saveInstallSecretToSession(value);
    setAppliedSecret(value);
    setReconnectVersion((v) => v + 1);
  }, []);

  const stream = useEventStream(eventsUrl, appliedSecret, {
    ...eventStreamOptions,
    reconnectKey: reconnectVersion,
  });

  const runControl = useCallback(
    async (action: ControlAction, call: () => Promise<Response>) => {
      const response = await call();
      const outcome = await parseControlOutcome(response);
      if (outcome.ok) {
        setControlRefusal(null);
        // 200 -> refresh from a fresh GET /api/status (brief's own ask):
        // the daemon also publishes a live mic/state event for the same
        // change, but this gives the operator an immediate, definitive
        // read rather than depending on that arriving too.
        await stream.refreshStatus();
      } else {
        setControlRefusal({ action, code: outcome.code });
      }
    },
    [stream],
  );

  // Round 5 correction: the daemon never publishes a "voice-on" status
  // token (verified against embodiment/daemon/app.py's real `_publish`
  // calls) -- voiceOn/micHot come from the `mic` event's real shape
  // (`{hot, ear}`, embodiment/daemon/app.py's `_publish_mic`), seeded on
  // connect from GET /api/status and kept current by every live `mic`
  // event the daemon actually sends on every ear attach/detach/mute.
  // `ear` is a NAME string ("host"/"browser"/"null") when attached, JSON
  // `null` when not -- `!== null` is the right test, not string truthiness
  // (the ear literally named "null" is still attached).
  const voiceOn = stream.mic ? stream.mic.data.ear !== null : false;
  const micHot = stream.mic ? Boolean(stream.mic.data.hot) : null;

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">Gwen</h1>
        <ConnectionStatus status={stream.status} />
      </header>

      {/* The live assistant waveform is the centrepiece (operator decision,
          obligation o9): the first and largest element after the header,
          full card width -- never a decorative animation. The heading is
          visually hidden (not omitted): it stays in the accessibility tree
          without competing with the waveform itself for visual weight. */}
      <section className="panel panel--waveform">
        <h2 className="panel__title panel__title--sr-only">Waveform</h2>
        <Waveform features={stream.features} />
      </section>

      <section className="panel">
        <h2 className="panel__title">Voice</h2>
        <VoiceControls
          voiceOn={voiceOn}
          micHot={micHot}
          onStart={() => void runControl("start", () => startVoice(appliedSecret))}
          onStop={() => void runControl("stop", () => stopVoice(appliedSecret))}
          onToggleMute={() =>
            void runControl("mute", () => setMicMute(appliedSecret, !(micHot ?? false)))
          }
        />
        {controlRefusal && (
          <p className="control-refusal" role="alert">
            refused ({ACTION_LABEL[controlRefusal.action]}): {controlRefusal.code}
          </p>
        )}
        <form
          className="secret-form"
          onSubmit={(event) => {
            event.preventDefault();
            applySecret(secret);
          }}
        >
          <label>
            install secret{" "}
            <input
              type="password"
              value={secret}
              onChange={(event) => setSecret(event.target.value)}
              aria-label="install secret"
              autoComplete="off"
            />
          </label>
          <button type="submit">Apply</button>
        </form>
        <div className="indicator-row">
          <RemoteViewers clients={stream.clients} />
          <RecallModeIndicator degradations={stream.degradations} seededMode={stream.recallStatusMode} />
        </div>
      </section>

      <section className="panel">
        <h2 className="panel__title">Transcript</h2>
        <Transcript entries={stream.transcript} />
      </section>

      <section className="panel">
        <h2 className="panel__title">Degradations</h2>
        <DegradationsLog entries={stream.degradations} />
      </section>
    </div>
  );
}
