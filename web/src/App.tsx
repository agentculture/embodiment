import { useCallback, useState } from "react";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { VoiceControls } from "./components/VoiceControls";
import { Transcript } from "./components/Transcript";
import { DegradationsLog } from "./components/DegradationsLog";
import { RecallModeIndicator } from "./components/RecallModeIndicator";
import { RemoteViewers } from "./components/RemoteViewers";
import { Waveform } from "./components/Waveform";
import { useEventStream, type UseEventStreamOptions } from "./hooks/useEventStream";
import { setMicMute, startVoice, stopVoice } from "./api/control";
import { loadInstallSecretFromSession, saveInstallSecretToSession } from "./api/secret";

export interface AppProps {
  /** Test seam: override the SSE endpoint's URL and connector. */
  eventsUrl?: string;
  eventStreamOptions?: UseEventStreamOptions;
}

const DEFAULT_EVENTS_URL = "/api/events";

export default function App({ eventsUrl = DEFAULT_EVENTS_URL, eventStreamOptions }: AppProps) {
  // `secret` is the live-typed value, bound to the input and used
  // immediately for every control-API POST (Start/Stop/Mute) -- those have
  // always sent whatever is currently typed, with no separate "apply" step.
  const [secret, setSecret] = useState(() => loadInstallSecretFromSession());
  // `appliedSecret` is what the STREAM actually authenticates with. It is
  // deliberately NOT the same as `secret`: reconnecting the stream on every
  // keystroke would be wasteful and would spam t16's guard. It changes only
  // via `applySecret` (the "Apply" button), and initializes from
  // sessionStorage directly -- so the FIRST `useEventStream` call already
  // carries a restored secret, with no separate mount-effect / extra
  // reconnect cycle needed (round 3 needed one, because the credential
  // travelled by cookie and had to be written as a side effect before the
  // stream could see it; round 4's Authorization header is just a value
  // passed straight into the hook).
  const [appliedSecret, setAppliedSecret] = useState(() => loadInstallSecretFromSession());
  // An explicit escape hatch alongside `appliedSecret` itself already being
  // a reconnect trigger -- re-applying the IDENTICAL secret value still
  // forces a reconnect (e.g. after the guard was reconfigured server-side).
  const [reconnectVersion, setReconnectVersion] = useState(0);

  const applySecret = useCallback((value: string) => {
    saveInstallSecretToSession(value);
    setAppliedSecret(value);
    setReconnectVersion((v) => v + 1);
  }, []);

  const stream = useEventStream(eventsUrl, appliedSecret, {
    ...eventStreamOptions,
    reconnectKey: reconnectVersion,
  });

  const voiceOn = stream.state?.data.status === "voice-on";
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
          onStart={() => void startVoice(secret)}
          onStop={() => void stopVoice(secret)}
          onToggleMute={() => void setMicMute(secret, !(micHot ?? false))}
        />
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
          <RecallModeIndicator degradations={stream.degradations} />
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
