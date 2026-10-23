// t12 (plan gwen-is-pocketchat-transcripts-as-knowledge): this shell SUPERSEDES
// obligation o9 ("the waveform is the centrepiece") per decision c2 -- the page
// is chat-first now. Layout ported, cited not imported, from pocketchat's shell
// (pocketsync/web/static/index.html:13-35 top bar + hash views, :36-108 the
// three views, :110-145 the side reader): a top bar with brand, health pill and
// Ask/Library/To-dos hash nav; the Ask view first, its composer carrying the
// voice-as-a-feature buttons (Chat + AudioControls); the waveform reduced to a
// compact strip under the bar; voice controls, recall mode and degradations in
// a collapsible status drawer; the install-secret form only while the stream
// is refused (on the public Host, Access carries the credential instead).
import { useCallback, useEffect, useRef, useState } from "react";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { VoiceControls } from "./components/VoiceControls";
import { Transcript } from "./components/Transcript";
import { Chat } from "./components/Chat";
import { LibraryView } from "./components/LibraryView";
import { Reader, type ReaderHandle } from "./components/Reader";
import { TodosView } from "./components/TodosView";
import { DegradationsLog } from "./components/DegradationsLog";
import { RecallModeIndicator } from "./components/RecallModeIndicator";
import { RemoteViewers } from "./components/RemoteViewers";
import { Waveform, type WaveformProps } from "./components/Waveform";
import { useEventStream, type UseEventStreamOptions } from "./hooks/useEventStream";
import { parseControlOutcome, setMicMute, startVoice, stopVoice } from "./api/control";
import { loadInstallSecretFromSession, saveInstallSecretToSession } from "./api/secret";

export interface AppProps {
  /** Test seam: override the SSE endpoint's URL and connector. */
  eventsUrl?: string;
  eventStreamOptions?: UseEventStreamOptions;
  /** Test seam (task t18): override the oscilloscope's canvas context
   *  factory / animation-frame scheduler / clock / listener-analyser
   *  source -- jsdom has no canvas, so an end-to-end App test that needs to
   *  observe the waveform go live has to inject these, the same way
   *  `eventStreamOptions` already lets a test inject the SSE seam. Left
   *  undefined in production, where `Waveform`'s own defaults
   *  (`canvas.getContext("2d")`, `window.requestAnimationFrame`,
   *  `Date.now`, no analyser) apply. */
  waveformOptions?: Pick<
    WaveformProps,
    | "contextFactory"
    | "raf"
    | "nowFn"
    | "listenerAnalyser"
    | "measureSize"
    | "resizeObserverFactory"
    | "devicePixelRatioFn"
    | "styleReader"
  >;
}

const DEFAULT_EVENTS_URL = "/api/events";

type View = "ask" | "library" | "todos";

const VIEWS: { id: View; label: string }[] = [
  { id: "ask", label: "Ask" },
  { id: "library", label: "Library" },
  { id: "todos", label: "To-dos" },
];

/** The view a location hash names; anything else (or none) is Ask. */
function viewFromHash(hash: string): View {
  const id = hash.replace(/^#/, "");
  return VIEWS.some((v) => v.id === id) ? (id as View) : "ask";
}

function currentHash(): string {
  return typeof window === "undefined" ? "" : window.location.hash;
}

/** A citation's anchor is opaque on the wire; the reader addresses passages by string. */
function anchorString(anchor: unknown): string | undefined {
  if (typeof anchor === "string") return anchor;
  if (typeof anchor === "number" && Number.isFinite(anchor)) return String(anchor);
  return undefined;
}

interface Citation {
  itemId: string;
  anchor?: string;
}

type ControlAction = "start" | "stop" | "mute";

const ACTION_LABEL: Record<ControlAction, string> = {
  start: "start voice",
  stop: "stop voice",
  mute: "mic mute",
};

export default function App({
  eventsUrl = DEFAULT_EVENTS_URL,
  eventStreamOptions,
  waveformOptions,
}: AppProps) {
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

  const [view, setView] = useState<View>(() => viewFromHash(currentHash()));
  useEffect(() => {
    const onHash = () => setView(viewFromHash(currentHash()));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const [drawerOpen, setDrawerOpen] = useState(false);

  // The reader a citation opened (pocketchat openCitation -> openReader,
  // app.js:364-432, 461). A second citation into the item already open moves
  // it in place through the reader's own openAt, without a refetch.
  const [citation, setCitation] = useState<Citation | null>(null);
  const readerRef = useRef<ReaderHandle>(null);
  const openCitation = useCallback(
    (itemId: string, rawAnchor: unknown) => {
      const anchor = anchorString(rawAnchor);
      if (citation && citation.itemId === itemId && anchor) readerRef.current?.openAt(anchor);
      setCitation({ itemId, anchor });
    },
    [citation],
  );
  const closeReader = useCallback(() => setCitation(null), []);

  const unauthorized = stream.status === "unauthorized";

  return (
    <div className="app">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="bar">
        <a className="brand" href="#ask" onClick={() => setView("ask")}>
          <svg className="brand-mark" viewBox="0 0 24 24" aria-hidden="true">
            <rect x="2" y="10" width="20" height="4" rx="2" />
            <rect x="9" y="10" width="6" height="4" className="brand-mark-hit" />
          </svg>
          <span>Gwen</span>
        </a>
        <nav className="views" aria-label="Views">
          {VIEWS.map((v) => (
            <a
              key={v.id}
              href={`#${v.id}`}
              aria-current={view === v.id ? "page" : undefined}
              onClick={() => setView(v.id)}
            >
              {v.label}
            </a>
          ))}
        </nav>
        <span className="health">
          <ConnectionStatus status={stream.status} />
        </span>
        <button
          type="button"
          className="drawer-toggle"
          aria-expanded={drawerOpen}
          aria-controls="status-drawer"
          onClick={() => setDrawerOpen((o) => !o)}
        >
          Status
        </button>
      </header>

      {/* Decision c2 supersedes obligation o9: the waveform is a compact
          strip under the bar -- still live, never the largest element. */}
      <section className="strip" aria-label="Waveform">
        <h2 className="sr-only">Waveform</h2>
        <Waveform features={stream.features} {...waveformOptions} />
      </section>

      {unauthorized && (
        <form
          className="secret-form"
          onSubmit={(event) => {
            event.preventDefault();
            applySecret(secret);
          }}
        >
          <p className="secret-form__lead">
            The daemon refused this page. Enter the install secret to connect.
          </p>
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
      )}

      <main id="main" className="main" tabIndex={-1}>
        <section className="view view--ask" data-view="ask" aria-label="Ask" hidden={view !== "ask"}>
          <section className="thread thread--voice" aria-labelledby="voice-thread-title">
            <h2 className="sr-only" id="voice-thread-title">
              Transcript
            </h2>
            <Transcript entries={stream.transcript} />
          </section>
          <Chat
            secret={appliedSecret}
            replyEvents={stream.transcript}
            s2sRole={stream.s2sRole ?? undefined}
            onOpenCitation={openCitation}
          />
        </section>
        {view === "library" && (
          <section className="view" data-view="library">
            <h1 className="view__title">Library</h1>
            <LibraryView secret={appliedSecret} />
          </section>
        )}
        {view === "todos" && (
          <section className="view" data-view="todos">
            <h1 className="view__title">To-dos</h1>
            <TodosView secret={appliedSecret} />
          </section>
        )}
      </main>

      {citation && (
        <Reader
          ref={readerRef}
          id={citation.itemId}
          anchor={citation.anchor}
          secret={appliedSecret}
          onClose={closeReader}
        />
      )}

      <section id="status-drawer" className="drawer" aria-label="Status" hidden={!drawerOpen}>
        <section className="drawer__section">
          <h2 className="drawer__title">Voice</h2>
          <VoiceControls
            voiceOn={voiceOn}
            micHot={micHot}
            onStart={() => void runControl("start", () => startVoice(appliedSecret))}
            onStop={() => void runControl("stop", () => stopVoice(appliedSecret))}
            // Round 6 fix: `hot` means "capturing, NOT muted" (daemon's
            // `_publish_mic`: `hot = ear is not None and not self._muted()`).
            // To TOGGLE, the new `muted` value must equal the CURRENT `hot`
            // value -- hot=true (unmuted) -> mute it (muted=true); hot=false
            // (muted) -> unmute it (muted=false). The button is disabled
            // when `micHot` is null (see VoiceControls.tsx), so that case
            // never reaches here -- `Boolean(micHot)` is safe without an `??`.
            onToggleMute={() => void runControl("mute", () => setMicMute(appliedSecret, Boolean(micHot)))}
          />
          {controlRefusal && (
            <p className="control-refusal" role="alert">
              refused ({ACTION_LABEL[controlRefusal.action]}): {controlRefusal.code}
            </p>
          )}
        </section>
        <section className="drawer__section">
          <h2 className="drawer__title">Recall</h2>
          <div className="indicator-row">
            <RemoteViewers clients={stream.clients} />
            <RecallModeIndicator
              degradations={stream.degradations}
              seededMode={stream.recallStatusMode}
              configuredMode={stream.recallConfiguredMode}
            />
          </div>
        </section>
        <section className="drawer__section">
          <h2 className="drawer__title">Degradations</h2>
          <DegradationsLog entries={stream.degradations} />
        </section>
      </section>
    </div>
  );
}
