import { useCallback, useEffect, useState } from "react";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { VoiceControls } from "./components/VoiceControls";
import { Transcript } from "./components/Transcript";
import { DegradationsLog } from "./components/DegradationsLog";
import { RecallModeIndicator } from "./components/RecallModeIndicator";
import { RemoteViewers } from "./components/RemoteViewers";
import { Waveform } from "./components/Waveform";
import { useEventStream, type UseEventStreamOptions } from "./hooks/useEventStream";
import { setMicMute, startVoice, stopVoice } from "./api/control";
import {
  type InstallSecretCookieOptions,
  loadInstallSecretFromSession,
  saveInstallSecretToSession,
  setInstallSecretCookie,
} from "./api/secret";

export interface AppProps {
  /** Test seam: override the SSE endpoint's URL and factory. */
  eventsUrl?: string;
  eventStreamOptions?: UseEventStreamOptions;
  /** Test seam: override how the install-secret cookie gets written. */
  installSecretOptions?: InstallSecretCookieOptions;
}

const DEFAULT_EVENTS_URL = "/api/events";

export default function App({
  eventsUrl = DEFAULT_EVENTS_URL,
  eventStreamOptions,
  installSecretOptions,
}: AppProps) {
  const [secret, setSecret] = useState(() => loadInstallSecretFromSession());
  // Bumped every time the operator applies a secret, so useEventStream
  // closes the current EventSource and opens a fresh one carrying the
  // now-set embodiment_secret cookie (round 3 item #1) -- EventSource has
  // no "reconnect now" of its own, and t16's guard refuses GET /api/events
  // until the cookie is present, which is strictly after the FIRST connect
  // attempt this hook already made on mount.
  const [reconnectVersion, setReconnectVersion] = useState(0);

  const applySecret = useCallback(
    (value: string) => {
      setInstallSecretCookie(value, installSecretOptions);
      saveInstallSecretToSession(value);
      setReconnectVersion((v) => v + 1);
    },
    [installSecretOptions],
  );

  // A secret restored from a previous tab session is applied once, on
  // mount, so a reload doesn't force the operator to retype it. Effects run
  // in the order they were REGISTERED during render, and this useEffect
  // call happens (in source order) before `useEventStream` is called
  // below -- so this mount effect's synchronous cookie write completes
  // before useEventStream's own connect effect ever runs, and the very
  // first EventSource this hook opens already carries the restored cookie
  // (no failed "unauthorized" attempt first). The reconnect bump this also
  // triggers then closes and reopens that already-good connection once
  // more; a harmless extra cycle, kept so mount-restore and the explicit
  // "Apply" button share one code path (applySecret) rather than two.
  useEffect(() => {
    if (secret) {
      applySecret(secret);
    }
    // Deliberately runs once, on mount, only -- see comment above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stream = useEventStream(eventsUrl, {
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
