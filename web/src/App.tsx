import { useState } from "react";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { VoiceControls } from "./components/VoiceControls";
import { Transcript } from "./components/Transcript";
import { DegradationsLog } from "./components/DegradationsLog";
import { RecallModeIndicator } from "./components/RecallModeIndicator";
import { RemoteViewers } from "./components/RemoteViewers";
import { Waveform } from "./components/Waveform";
import { useEventStream, type UseEventStreamOptions } from "./hooks/useEventStream";
import { setMicMute, startVoice, stopVoice } from "./api/control";

export interface AppProps {
  /** Test seam: override the SSE endpoint's URL and factory. */
  eventsUrl?: string;
  eventStreamOptions?: UseEventStreamOptions;
}

const DEFAULT_EVENTS_URL = "/api/events";

export default function App({ eventsUrl = DEFAULT_EVENTS_URL, eventStreamOptions }: AppProps) {
  const stream = useEventStream(eventsUrl, eventStreamOptions);
  const [secret, setSecret] = useState("");

  const voiceOn = stream.state?.data.status === "voice-on";
  const micHot = stream.mic ? Boolean(stream.mic.data.hot) : null;

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">Gwen</h1>
        <ConnectionStatus status={stream.status} />
      </header>

      <section className="panel">
        <h2 className="panel__title">Voice</h2>
        <VoiceControls
          voiceOn={voiceOn}
          micHot={micHot}
          onStart={() => void startVoice(secret)}
          onStop={() => void stopVoice(secret)}
          onToggleMute={() => void setMicMute(secret, !(micHot ?? false))}
        />
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
        <div className="indicator-row">
          <RemoteViewers clients={stream.clients} />
          <RecallModeIndicator degradations={stream.degradations} />
        </div>
      </section>

      <section className="panel">
        <h2 className="panel__title">Waveform</h2>
        <Waveform features={stream.features} />
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
