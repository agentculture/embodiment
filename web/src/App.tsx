// The page, redesigned 2026-10-24 on the operator's word ("the library
// experience plus the chat experience, closer to option C, within the library
// experience"; wide and phone; a toggleable left pane and a toggleable right
// pane of sources; a speech button and a realtime speech button).
//
// Shell: culture-library's top bar (brand, pill tabs, search, actions;
// culture_library/web/static/index.html:19-45). Ask: a three-pane workspace,
// the library list (left, toggleable), pocketchat's thread with option C's
// voice dock (centre; pocketsync/web/static/index.html:36-108), and the
// conversation's sources with the reader in place (right, toggleable;
// index.html:110-145). On phones both panes are drawers over the thread.
// Library: culture-library's home (intro + shelf + cards) and its reader view.
// Stats: culture-library's "Site state". The status drawer keeps the room
// voice controls, recall mode and degradations; the install-secret form shows
// only while the stream is refused (on the public Host, Access carries it).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { VoiceControls } from "./components/VoiceControls";
import { Transcript } from "./components/Transcript";
import { Chat } from "./components/Chat";
import { LibraryHome } from "./components/LibraryHome";
import { LibraryPane } from "./components/LibraryPane";
import { Reader, type ReaderHandle } from "./components/Reader";
import { SourcesPane, type AnswerSources, type SideTab } from "./components/SourcesPane";
import { HistoryPane } from "./components/HistoryPane";
import { PastConversation } from "./components/PastConversation";
import type { ConversationSummary } from "./api/history";
import { StatsView } from "./components/StatsView";
import { Shelf } from "./components/Shelf";
import { TodosView } from "./components/TodosView";
import { DegradationsLog } from "./components/DegradationsLog";
import { RecallModeIndicator } from "./components/RecallModeIndicator";
import { RemoteViewers } from "./components/RemoteViewers";
import { Waveform, type WaveformProps } from "./components/Waveform";
import { useEventStream, type UseEventStreamOptions } from "./hooks/useEventStream";
import { newConversation, parseControlOutcome, setMicMute, startVoice, stopVoice } from "./api/control";
import { loadInstallSecretFromSession, saveInstallSecretToSession } from "./api/secret";
import { fetchLibrary, searchLibrary, type LibraryItem, type SearchHit } from "./api/library";
import type { ChatSource } from "./api/chat";
import { itemDay, kindInfo, shortTitle } from "./api/kinds";

export interface AppProps {
  /** Test seam: override the SSE endpoint's URL and connector. */
  eventsUrl?: string;
  eventStreamOptions?: UseEventStreamOptions;
  /** Test seam (task t18): override the oscilloscope's canvas context
   *  factory / animation-frame scheduler / clock / listener-analyser
   *  source -- jsdom has no canvas. Left undefined in production. */
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
/** Milliseconds before asking for an empty or failed library again. */
const LIBRARY_RETRY_MS = 10_000;

type View = "ask" | "library" | "todos" | "stats";

const VIEWS: { id: View; label: string }[] = [
  { id: "ask", label: "Ask" },
  { id: "library", label: "Library" },
  { id: "todos", label: "To-dos" },
  { id: "stats", label: "Stats" },
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

function wide(min: number): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia(`(min-width: ${min}px)`).matches;
}

type Theme = "light" | "dark" | null;

function loadTheme(): Theme {
  try {
    const t = window.localStorage.getItem("gwen-theme");
    return t === "light" || t === "dark" ? t : null;
  } catch {
    return null;
  }
}

interface Citation {
  itemId: string;
  anchor?: string;
}

type ControlAction = "start" | "stop" | "mute" | "new";

const ACTION_LABEL: Record<ControlAction, string> = {
  start: "start voice",
  stop: "stop voice",
  mute: "mic mute",
  new: "new conversation",
};

function Icon({ d, size = 18 }: { d: string; size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

// The left pane is the conversations; the right pane is the library and the sources.
const ICON_HISTORY = "M12 7v5l3 2M3.5 12a8.5 8.5 0 1 0 2.5-6M3 4v4h4";
const ICON_SIDE = "M4 5h16v14H4zM14.5 5v14";

export default function App({
  eventsUrl = DEFAULT_EVENTS_URL,
  eventStreamOptions,
  waveformOptions,
}: AppProps) {
  // `secret` is the live-typed value; only `applySecret` makes it the
  // credential (`appliedSecret`), so typing never reconnects the stream.
  const [secret, setSecret] = useState(() => loadInstallSecretFromSession());
  const [appliedSecret, setAppliedSecret] = useState(() => loadInstallSecretFromSession());
  const [reconnectVersion, setReconnectVersion] = useState(0);
  // The most recent control POST's refusal CODE, cleared on the next success.
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
    async (action: ControlAction, call: () => Promise<Response>): Promise<boolean> => {
      const response = await call();
      const outcome = await parseControlOutcome(response);
      if (outcome.ok) {
        setControlRefusal(null);
        await stream.refreshStatus();
      } else {
        setControlRefusal({ action, code: outcome.code });
      }
      return outcome.ok;
    },
    [stream],
  );

  // voiceOn/micHot come from the `mic` event ({hot, ear}); `ear` is a name
  // string when attached and JSON null when not.
  const voiceOn = stream.mic ? stream.mic.data.ear !== null : false;
  const micHot = stream.mic ? Boolean(stream.mic.data.hot) : null;

  const [view, setView] = useState<View>(() => viewFromHash(currentHash()));
  useEffect(() => {
    const onHash = () => setView(viewFromHash(currentHash()));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // iOS Safari keeps the layout viewport when the keyboard opens and shrinks only
  // the visual one, so a 100dvh page slides up and the dock hides behind the
  // keyboard (operator's phone, 2026-10-24). The app follows the visual viewport:
  // its height and top come from it, so the dock rides above the keyboard.
  useEffect(() => {
    const vv = typeof window !== "undefined" ? window.visualViewport : null;
    if (!vv) return;
    const root = document.documentElement;
    const sync = () => {
      root.style.setProperty("--app-h", `${Math.round(vv.height)}px`);
      root.style.setProperty("--app-top", `${Math.round(vv.offsetTop)}px`);
    };
    sync();
    vv.addEventListener("resize", sync);
    vv.addEventListener("scroll", sync);
    return () => {
      vv.removeEventListener("resize", sync);
      vv.removeEventListener("scroll", sync);
    };
  }, []);

  // A phone gets the short placeholder, so it fits on one line.
  const [narrow, setNarrow] = useState(() => !wide(520));
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(min-width: 520px)");
    const on = () => setNarrow(!mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);

  const [theme, setTheme] = useState<Theme>(loadTheme);
  useEffect(() => {
    const root = document.documentElement;
    if (theme) root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
  }, [theme]);
  const toggleTheme = () => {
    const dark = theme ? theme === "dark" : window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    const next: Theme = dark ? "light" : "dark";
    setTheme(next);
    try {
      window.localStorage.setItem("gwen-theme", next);
    } catch {
      // a blocked store only loses the remembered theme
    }
  };

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [leftOpen, setLeftOpen] = useState(() => wide(1280));
  const [rightOpen, setRightOpen] = useState(() => wide(1280));
  const [sideTab, setSideTab] = useState<SideTab>("library");
  // An earlier conversation shown read-only in the middle; null is the live one.
  const [past, setPast] = useState<ConversationSummary | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  // "New conversation" (operator, 2026-10-24): a fresh thread, and the voice
  // transcript from this point on; the ended one is in the history pane.
  const [liveKey, setLiveKey] = useState(0);
  // The last voice-transcript entry before "New conversation", or null. An entry, not
  // an index: the log is capped and drops from the front, so a fixed index would point
  // past the end once it fills (review of PR #101). If the entry has been dropped, every
  // entry left came after it.
  const [transcriptMark, setTranscriptMark] = useState<object | null>(null);
  const transcriptNow = useMemo(() => {
    if (transcriptMark === null) return stream.transcript;
    const at = stream.transcript.indexOf(transcriptMark as (typeof stream.transcript)[number]);
    return at < 0 ? stream.transcript : stream.transcript.slice(at + 1);
  }, [stream.transcript, transcriptMark]);
  // On a phone the panes are drawers over the thread: opening one closes the other.
  useEffect(() => {
    if (leftOpen && rightOpen && !wide(861)) setRightOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leftOpen]);
  useEffect(() => {
    if (leftOpen && rightOpen && !wide(861)) setLeftOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rightOpen]);

  // The library, shared by the list, the shelf, the cards and Stats. Asked again
  // while it comes back empty or fails (review of PR #98: after a daemon restart
  // the first answer can be empty, and a page that asked once stayed empty).
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [libraryTry, setLibraryTry] = useState(0);
  useEffect(() => {
    let live = true;
    void fetchLibrary(appliedSecret).then((r) => {
      if (!live) return;
      if (r.ok) {
        setItems(r.value);
        setLibraryError(null);
      } else setLibraryError(r.code);
    });
    return () => {
      live = false;
    };
  }, [appliedSecret, reconnectVersion, libraryTry]);
  useEffect(() => {
    if (items.length > 0 && !libraryError) return;
    const timer = setTimeout(() => setLibraryTry((n) => n + 1), LIBRARY_RETRY_MS);
    return () => clearTimeout(timer);
  }, [items.length, libraryError, libraryTry]);

  // Header search: titles filter as you type; Enter searches passages by meaning.
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const runSearch = async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    const r = await searchLibrary(q, appliedSecret);
    setHits(r.ok ? r.value : []);
    setSearching(false);
  };

  // The conversation's sources (right pane) and the reader a citation opened.
  const [answers, setAnswers] = useState<AnswerSources[]>([]);
  const onAnswer = useCallback((question: string, sources: ChatSource[]) => {
    setAnswers((a) => [...a, { key: `s-${a.length}`, question, sources }]);
    setHistoryKey((k) => k + 1);
    if (sources.length > 0) {
      setSideTab("sources");
      if (wide(1100)) setRightOpen(true);
    }
  }, []);
  const citedIds = useMemo(
    () => new Set(answers.flatMap((a) => a.sources.map((s) => s.item_id ?? "")).filter(Boolean)),
    [answers],
  );

  const [citation, setCitation] = useState<Citation | null>(null);
  const readerRef = useRef<ReaderHandle>(null);
  const openCitation = useCallback(
    (itemId: string, rawAnchor: unknown) => {
      // A past conversation Gwen cited (d8) opens in the middle, where it can be
      // continued; everything else opens the library reader on the right.
      const cited = answers.flatMap((a) => a.sources).find((s) => s.item_id === itemId);
      if (cited?.source === "conversation") {
        setPast({ id: itemId, title: cited.title, started: null, last: null, turns: 0, current: false });
        if (!wide(1280)) setRightOpen(false);
        return;
      }
      const anchor = anchorString(rawAnchor);
      if (citation && citation.itemId === itemId) {
        // The same item again: move to the passage, and fetch again in case the
        // first open failed (an item not yet re-indexed, live 2026-10-24).
        if (anchor) readerRef.current?.openAt(anchor);
        readerRef.current?.reload();
      }
      setCitation({ itemId, anchor });
      setRightOpen(true);
      if (!wide(1280)) setLeftOpen(false);
    },
    [citation, answers],
  );
  const closeRight = useCallback(() => {
    setRightOpen(false);
    setCitation(null);
  }, []);

  // The Library tab's own reader (culture-library's reader view).
  const [libOpen, setLibOpen] = useState<string | null>(null);
  const openInLibrary = (id: string) => {
    setLibOpen(id);
    window.location.hash = "#library";
    setView("library");
  };

  const go = (v: View) => {
    setView(v);
    if (v !== "library") setLibOpen(null);
  };

  const unauthorized = stream.status === "unauthorized";
  const sourceCount = answers.reduce((n, a) => n + a.sources.length, 0);

  // Realtime voice sits beside the input box, not in it. The level strip stays
  // mounted (the waveform keeps its live/idle state) but is drawn as the Live
  // button's breathing ring, not as a trace in the box.
  const live = (
    <div className="live-wrap" data-on={voiceOn ? "true" : undefined}>
      <button
        type="button"
        className="live"
        aria-pressed={voiceOn}
        data-hot={micHot ? "true" : undefined}
        aria-label={voiceOn ? "End live voice" : "Live voice"}
        aria-describedby="live-note"
        title={voiceOn ? "End live voice with Gwen" : "Talk with Gwen live through the room microphone"}
        onClick={() =>
          void (voiceOn
            ? runControl("stop", () => stopVoice(appliedSecret))
            : runControl("start", () => startVoice(appliedSecret)))
        }
      >
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          <path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4" />
        </svg>
      </button>
      <span id="live-note" className="sr-only">
        {voiceOn ? (micHot ? "Listening through the room microphone." : "Muted.") : "Off."}{" "}
        {stream.s2sRole
          ? `The gateway advertises a speech-to-speech role (${stream.s2sRole}); this page does not use it yet.`
          : "No speech-to-speech role is advertised by the gateway."}
      </span>
      <section className="strip" aria-label="Waveform" data-on={voiceOn ? "true" : undefined}>
        <h2 className="sr-only">Waveform</h2>
        <Waveform features={stream.features} {...waveformOptions} />
      </section>
    </div>
  );

  const recentTranscripts = [...items]
    .filter((i) => i.kind === "transcript")
    .sort((a, b) => (itemDay(a.createdAt, a.title) < itemDay(b.createdAt, b.title) ? 1 : -1))
    .slice(0, 3);

  const emptyState = (ask: (q: string) => void) => (
    <div className="welcome">
      <h1>What would you like to know?</h1>
      <p>
        Gwen answers from your recordings, the daily paper and your documents, and shows the passage behind every
        claim.
      </p>
      <div className="suggestions">
        {[
          "What did I commit to this week?",
          "What was in today's newspaper?",
          ...recentTranscripts.slice(0, 2).map((t) => `Summarize the ${shortTitle(t.title)} recording`),
        ].map((q) => (
          <button key={q} type="button" className="suggestion" dir="auto" onClick={() => ask(q)}>
            {q}
          </button>
        ))}
      </div>
      {items.length > 0 && <Shelf items={items} onOpen={(id) => openCitation(id, undefined)} max={60} />}
    </div>
  );

  return (
    <div className="app" data-view={view}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="top">
        <a className="brand" href="#ask" onClick={() => go("ask")} aria-label="Gwen home">
          <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true" className="brand-mark">
            <rect x="3" y="7" width="5" height="17" rx="2" fill="var(--k-transcript)" />
            <rect x="10" y="3" width="5" height="21" rx="2" fill="var(--accent)" />
            <rect x="17" y="9" width="5" height="15" rx="2" fill="var(--k-newspaper)" transform="rotate(-8 19.5 16)" />
          </svg>
          <span>Gwen</span>
        </a>
        <nav className="tabs" aria-label="Views">
          {VIEWS.map((v) => (
            <a key={v.id} href={`#${v.id}`} aria-current={view === v.id ? "page" : undefined} onClick={() => go(v.id)}>
              {v.label}
            </a>
          ))}
        </nav>
        <form
          className="search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            go("ask");
            window.location.hash = "#ask";
            setSideTab("library");
            setRightOpen(true);
            void runSearch();
          }}
        >
          <Icon d="M10.5 17a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13zM15.5 15.5 20 20" size={16} />
          <label className="sr-only" htmlFor="lib-search">
            Search your library
          </label>
          <input
            id="lib-search"
            type="search"
            dir="auto"
            autoComplete="off"
            placeholder="Search your library: titles, or press Enter to search passages"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setHits(null);
              if (e.target.value && view === "ask") {
                setSideTab("library");
                setRightOpen(true);
              }
            }}
          />
        </form>
        <div className="actions">
          <span className="health">
            <ConnectionStatus status={stream.status} />
          </span>
          <button
            type="button"
            className="pill-btn drawer-toggle"
            aria-label="Status"
            aria-expanded={drawerOpen}
            aria-controls="status-drawer"
            onClick={() => setDrawerOpen((o) => !o)}
          >
            <span className="mic-state" data-hot={micHot ? "true" : undefined} aria-hidden="true" />
            <span className="lbl">Status</span>
          </button>
          <button type="button" className="icon-btn" aria-label="Switch colour theme" onClick={toggleTheme}>
            <Icon d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z" />
          </button>
        </div>
      </header>

      {unauthorized && (
        <form
          className="secret-form"
          onSubmit={(event) => {
            event.preventDefault();
            applySecret(secret);
          }}
        >
          <p className="secret-form__lead">This page is not signed in. Enter the install secret to connect.</p>
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
          <button type="submit" className="btn primary">
            Apply
          </button>
        </form>
      )}

      <main id="main" className="main" tabIndex={-1}>
        <section
          className="view view--ask workspace"
          data-view="ask"
          aria-label="Ask"
          hidden={view !== "ask"}
          data-left={leftOpen ? "open" : undefined}
          data-right={rightOpen ? "open" : undefined}
        >
          {!leftOpen && (
            <button
              type="button"
              className="rail rail--left"
              aria-label="Show conversations"
              title="Show conversations"
              onClick={() => setLeftOpen(true)}
            >
              <Icon d={ICON_HISTORY} size={18} />
              <span className="rail-label">Conversations</span>
            </button>
          )}
          <div className="ws-left" aria-hidden={!leftOpen || undefined}>
            <HistoryPane
              secret={appliedSecret}
              openId={past ? past.id : null}
              refreshKey={historyKey}
              onOpen={(c) => {
                setPast(c && !c.current ? c : null);
                if (!wide(861)) setLeftOpen(false);
              }}
              onClose={() => setLeftOpen(false)}
              newError={controlRefusal?.action === "new" ? controlRefusal.code : null}
              onNew={() =>
                void runControl("new", () => newConversation(appliedSecret)).then((ok) => {
                  if (!ok) return;
                  setLiveKey((k) => k + 1);
                  setTranscriptMark(stream.transcript[stream.transcript.length - 1] ?? null);
                  setAnswers([]);
                  setPast(null);
                  setHistoryKey((k) => k + 1);
                })
              }
            />
          </div>
          <button
            type="button"
            className="scrim"
            aria-label="Close panels"
            tabIndex={-1}
            onClick={() => {
              setLeftOpen(false);
              closeRight();
            }}
          />
          <div className="ws-center">
            <div className="ws-bar">
              <button
                type="button"
                className="pill-btn"
                aria-pressed={leftOpen}
                aria-label={leftOpen ? "Hide conversations" : "Show conversations"}
                onClick={() => setLeftOpen((o) => !o)}
              >
                <Icon d={ICON_HISTORY} size={16} />
                <span className="lbl">Conversations</span>
              </button>
              <span className="ws-sp" />
              <button
                type="button"
                className="pill-btn"
                aria-pressed={rightOpen}
                aria-label={rightOpen ? "Hide library and sources" : "Show library and sources"}
                onClick={() => (rightOpen ? closeRight() : setRightOpen(true))}
              >
                <span className="lbl">Library and sources</span>
                {sourceCount > 0 && <span className="count">{sourceCount}</span>}
                <Icon d={ICON_SIDE} size={16} />
              </button>
            </div>
            {past && (
              <PastConversation
                conversation={past}
                secret={appliedSecret}
                onBack={() => setPast(null)}
                chat={{
                  onOpenCitation: openCitation,
                  onAnswer,
                  placeholder: narrow ? "Ask Gwen" : "Continue this conversation",
                }}
              />
            )}
            <div className="live-conversation" hidden={past !== null}>
            <section className="thread thread--voice" aria-labelledby="voice-thread-title">
              <h2 className="sr-only" id="voice-thread-title">
                Transcript
              </h2>
              <Transcript entries={transcriptNow} />
            </section>
            <Chat
              key={liveKey}
              secret={appliedSecret}
              replyEvents={transcriptNow}
              s2sRole={stream.s2sRole ?? undefined}
              onOpenCitation={openCitation}
              onAnswer={onAnswer}
              emptyState={emptyState}
              live={live}
              placeholder={narrow ? "Ask Gwen" : undefined}
            />
            </div>
          </div>
          {!rightOpen && (
            <button
              type="button"
              className="rail rail--right"
              aria-label="Show library and sources"
              title="Show library and sources"
              onClick={() => setRightOpen(true)}
            >
              <Icon d={ICON_SIDE} size={18} />
              <span className="rail-label">Library and sources</span>
              {sourceCount > 0 && <span className="rail-count rail-count--on">{sourceCount}</span>}
            </button>
          )}
          <div className="ws-right" aria-hidden={!rightOpen || undefined}>
            {rightOpen && (
              <SourcesPane
                answers={answers}
                open={citation}
                tab={sideTab}
                onTab={setSideTab}
                secret={appliedSecret}
                readerRef={readerRef}
                onOpen={openCitation}
                onBack={() => setCitation(null)}
                onClose={closeRight}
                library={
                  <LibraryPane
                    items={items}
                    error={libraryError}
                    openId={citation?.itemId ?? null}
                    citedIds={citedIds}
                    query={query}
                    hits={hits}
                    searching={searching}
                    onOpen={(id, anchor) => openCitation(id, anchor)}
                    onQuery={(q) => {
                      setQuery(q);
                      setHits(null);
                    }}
                    onSearch={() => void runSearch()}
                  />
                }
              />
            )}
          </div>
        </section>

        {view === "library" &&
          (libOpen ? (
            <section className="view view--read" data-view="library" aria-label="Library">
              <div className="read-split">
                <div className="read-pane">
                  <LibraryPane items={items} error={libraryError} openId={libOpen} onOpen={(id) => setLibOpen(id)} />
                </div>
                <div className="read-main">
                  <div className="read-tools">
                    <button type="button" className="pill-btn" onClick={() => setLibOpen(null)}>
                      <Icon d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" size={16} />
                      All cards
                    </button>
                    <span className="ws-sp" />
                    <span className="read-kind">
                      {kindInfo(items.find((i) => i.id === libOpen)?.kind ?? "").one}
                    </span>
                  </div>
                  <Reader id={libOpen} secret={appliedSecret} variant="page" onClose={() => setLibOpen(null)} />
                </div>
              </div>
            </section>
          ) : (
            <section className="view view--home" data-view="library" aria-label="Library">
              <LibraryHome
                items={items}
                error={libraryError}
                onOpen={openInLibrary}
                onAsk={() => {
                  window.location.hash = "#ask";
                  go("ask");
                }}
              />
            </section>
          ))}
        {view === "todos" && (
          <section className="view view--page" data-view="todos">
            <h1 className="view__title">To-dos</h1>
            <p className="view__lede">Action items Gwen found in your recordings.</p>
            <TodosView secret={appliedSecret} />
          </section>
        )}
        {view === "stats" && (
          <section className="view view--page" data-view="stats">
            <StatsView items={items} secret={appliedSecret} />
          </section>
        )}
      </main>

      <div className="sheet-scrim" hidden={!drawerOpen} onClick={() => setDrawerOpen(false)} />
      <section id="status-drawer" className="drawer" aria-label="Status" hidden={!drawerOpen}>
        <div className="drawer__head">
          <h2>Status</h2>
          <button type="button" className="icon-btn" aria-label="Close status" onClick={() => setDrawerOpen(false)}>
            <Icon d="M6 6l12 12M18 6 6 18" />
          </button>
        </div>
        <section className="drawer__section">
          <h3 className="drawer__title">Room voice</h3>
          <p className="drawer__note">Gwen listens through the room microphone on the rig, not this device.</p>
          <VoiceControls
            voiceOn={voiceOn}
            micHot={micHot}
            onStart={() => void runControl("start", () => startVoice(appliedSecret))}
            onStop={() => void runControl("stop", () => stopVoice(appliedSecret))}
            // `hot` means capturing and NOT muted, so the new `muted` value is the current `hot`.
            onToggleMute={() => void runControl("mute", () => setMicMute(appliedSecret, Boolean(micHot)))}
          />
          {controlRefusal && (
            <p className="control-refusal" role="alert">
              refused ({ACTION_LABEL[controlRefusal.action]}): {controlRefusal.code}
            </p>
          )}
        </section>
        <section className="drawer__section">
          <h3 className="drawer__title">Recall</h3>
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
          <h3 className="drawer__title">Degradations</h3>
          <DegradationsLog entries={stream.degradations} />
        </section>
      </section>
    </div>
  );
}
