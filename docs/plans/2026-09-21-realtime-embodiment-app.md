# Build Plan — realtime embodiment app

slug: `realtime-embodiment-app` · status: `exported` · from frame: `realtime-embodiment-app`

> embodiment is a background realtime app: a voice-first embodied presence built on the lobes /v1/realtime API, switched on and off from the CLI or a web dashboard, with context and memory on eidetic-cli; the prior loop/strategist/muse experiments are archived in git history

## Tasks

### t1 — Archive the experiments: tag, remove, re-pin CI

- instruction: Tag the parent commit `archive/pre-realtime-0.14.0` first. Keep: `embodiment/cli/`, `explain/`, `identity.py`, `framing.py`, `perception.py`, `presence.py`, `events.py`, `continuity.py` (eidetic half), `loop.py`, `contract.py` and whatever `loop.py` imports, plus their tests and the AST termination tests. Remove the strategist, config lane, muse, drone, `examples/`, `docs/live-test-results/` and their tests. Drop `headspace-cli`. Re-point bandit and keep the coverage gate honest. This is its own PR with a version bump; no daemon code rides in it.
- covers: c10, h13, h1
- acceptance:
  - `git tag` lists the archive tag on the parent of the archive commit and the full suite, lint stack and `teken cli doctor . --strict` are green on the archive commit
  - `tests/test_zero_deps.py` pins the reduced approved set exactly and still fails on any delta in either direction
  - the PR body names every deleted structural test (`test_governance.py`, `test_package_surface.py`, announcement and muse/scope AST tests) with one line on why it no longer applies
- obligation: `o1` (criterion 1) [git history / CI] the repo is green at the archive commit with no daemon code present, so old-gone-and-nothing-runs never exists
- obligation: `o2` (criterion 2) [tests/`test_zero_deps.py`] removing or adding a dependency without editing the approved set fails the suite

### t2 — Rewrite the docs for the archive and handle external pointers

- instruction: Same PR as t1. Rewrite README, `CLAUDE.md`, `AGENTS.colleague.md` and the `explain` catalog to describe the small core and the redesign direction; take the before-state from the repo's own 0.12.0/0.13.0 verdicts. Remove the first-party `drone` skill and its `docs/skill-sources.md` row. Draft, do NOT post, a comment for colleague#358 and a closing note for the moot issues; posting needs the operator's go-ahead.
- depends on: t1
- covers: c41, h31, c25, h4
- acceptance:
  - README and `CLAUDE.md` describe only modules that exist after t1, quote the old tiers' published verdicts for the before-state, and carry the corrected live rig table (senses = Gemma 4 26B A4B)
  - the PR body lists each external pointer (colleague#358, the ~45 moot issues, the `drone` skill, CI paths) and what was done about it, with drafts committed under `docs/archive/` and nothing posted to a sibling repo
  - `markdownlint-cli2` and `tests/test_cli_introspection.py` pass

### t3 — Approve the new dependency set

- instruction: Human gate, deliberately its own task. Propose: `websockets` (realtime client AND the inbound endpoint server) as a base dependency; `sounddevice` behind an `audio` extra; stdlib `http.server` for HTTP and SSE, so no web framework. State each one's transitive cost in the pin's comments. `lobes-cli` must never appear.
- depends on: t1
- covers: c11, h14
- acceptance:
  - `tests/test_zero_deps.py` lists each new dependency with a cost comment, pins the `audio` extra separately, and asserts `lobes` is absent from dependencies and from the required runtime imports
  - the PR description names every new dependency and its transitive footprint
- obligation: `o3` (criterion 1) [pyproject.toml dependencies] no dependency enters or leaves without the pinned approved set changing in the same diff

### t4 — State directory, bounded log, persisted degradation ledger

- instruction: New `embodiment/daemon/state.py`. XDG state dir outside any repo. Size-bounded rotating log; the degradation ledger appends JSONL and survives a crash. The operational log takes no transcript text.
- depends on: t1
- covers: c42, h32
- acceptance:
  - a crash leaves a ledger `status` can read, and the log never exceeds its configured size
  - a test writes a transcript through the session API and asserts the text is absent from the operational log
  - the state dir resolves outside the repo regardless of cwd
- obligation: `o4` (criterion 2) [operational log] transcript text never appears in the operational log under default retention

### t5 — Lifecycle verbs: start, stop, status

- instruction: New `embodiment/daemon/lifecycle.py` and `cli/_commands/{start,stop,status}.py` via `register(sub)`, with `explain` catalog entries. Pidfile + liveness check in the state dir. Every failure is a `CliError`; every verb takes `--json`.
- depends on: t4
- covers: c5, h11
- acceptance:
  - double `start` is idempotent, `stop` returns within a bounded time with a reader thread parked in a blocking call, and `status` reports `dead (unclean)` with the last ledger entry against a stale pidfile
  - `stop` then `start` each complete in under 5 s in a test with fake components
  - `teken cli doctor . --strict` and `tests/test_cli_introspection.py` pass with the three verbs registered
- obligation: `o5` (criterion 1) [embodiment stop] stop terminates within its bound even when a thread is parked on a blocking read
- obligation: `o6` (criterion 1) [embodiment status] status tells the truth after an unclean death instead of reporting stopped

### t6 — Realtime ears: the lobes client

- instruction: New `embodiment/realtime/{wire,client}.py`. Discover through keyless `GET /capabilities` (stt carrying `realtime_vad_session`), dial `/v1/realtime` with the Bearer key, pcm16 24 kHz base64. EARS ONLY: never send `response.create`. Declare `aec_mode=aec` and `language=he`. Build against event fixtures taken from lobes `docs/realtime-pipeline.md`. Never raises into the daemon.
- depends on: t3
- covers: c2, h8, c3, h9
- acceptance:
  - fixture-driven tests cover session.created, `speech_started`/stopped, transcription.completed and each named error code
  - an absent advert, a 404 `role_infeasible` (peer origin named) and a mid-session drop each yield exactly one degradation record and a still-running caller; an AST test proves `response.create` is never constructed
  - a live dial test, skipped when the gateway is down, reaches `session.created`
- obligation: `o7` (criterion 2) [lobes /v1/realtime] the client never arms the server-run turn and never raises into its caller

### t7 — Audio endpoint interface and the host endpoint

- instruction: New `embodiment/audio/{endpoint,host}.py`. One `AudioEndpoint` protocol (frames in, frames out, mute, attach/detach). Host implementation on `sounddevice`, imported lazily from the `audio` extra, resampling device rate to 24 kHz. Mute drops frames BEFORE encode.
- depends on: t3
- covers: c18, h17, c36, h27
- acceptance:
  - with the audio import forced to fail, and with device enumeration empty, construction returns a recorded no-voice state and never raises
  - a muted endpoint delivers zero frames downstream, proved by counting frames at the encoder boundary, and the mute change is one event
  - the turn and daemon modules import the protocol only; an import-graph test forbids importing `audio.host` from them
- obligation: `o8` (criterion 2) [capture path] mute is enforced before encode, so a muted daemon sends no audio to the gateway

### t8 — Audio feature extractor

- instruction: New `embodiment/audio/features.py`, pure and stdlib-or-numpy only (numpy already arrives with coherence-cli). Per ~33 ms block: RMS level, a decimated waveform slice, noise floor; pitch and spectral centroid optional. No IO.
- depends on: t1
- covers: h21
- acceptance:
  - a 440 Hz test tone yields a slice whose zero-crossing rate matches 440 Hz within 2% and a level within 1 dB of expected
  - silence yields a flat slice and a level at the noise floor, never a synthetic wiggle
  - one second of audio produces under 8 kB of feature payload
- obligation: `o9` (criterion 1) [feature stream] the published waveform is derived from real samples and can never be a decorative animation

### t9 — Turn engine on the bounded tool loop

- instruction: New `embodiment/turn.py` and `embodiment/tools.py`. Drive ONE spoken turn through `loop.run` with an empty tool registry, the `senses` role as the model seam, Gwen framing from `framing.py`, Hebrew system prompt, generous `max_tokens`. The user's words enter verbatim via `perception.py`. `loop.py` stays zero-diff.
- depends on: t1
- covers: c32, h25
- acceptance:
  - a turn with an empty registry terminates in one model call; registering a fake tool makes the same code path execute it, with no edit to `turn.py`
  - `loop.py` is byte-identical to the archived-core version and its AST termination tests pass
  - a truncated or empty completion yields a degradation record and a spoken fallback, never silence
- obligation: `o10` (criterion 1) [embodiment.tools registry] adding a tool is an addition to the registry, never a rewrite of the turn
- obligation: `o11` (criterion 3) [model seam] a lost turn is always recorded

### t10 — Memory: private, pinned, deadline-bounded, attributed

- instruction: New `embodiment/memory.py` over `continuity.py`. Default `visibility=private`; pin `EIDETIC_DATA_DIR` at start. Recall runs in an executor under a deadline (keyword mode on the fast path). Report the recall mode the last call actually used. Recalled text enters the prompt at ONE point, wrapped with `KNOWLEDGE_ATTRIBUTION`.
- depends on: t1
- covers: c6, h12, c33, h23, c35, h26, c38, h28
- acceptance:
  - a remember with no explicit visibility writes under the pinned private dir and leaves the repo's `.eidetic/memory/` byte-identical, from any cwd
  - a backend that sleeps past the deadline lets the turn complete without memory and leaves one degradation record
  - a recalled record containing an imperative is rendered inside the attributed data block, and the reported recall mode flips to lexical when the embedder call fails
- obligation: `o12` (criterion 1) [eidetic store] nothing said in the room reaches the committed public store by default
- obligation: `o13` (criterion 2) [recall fast path] recall can never stall a spoken turn
- obligation: `o14` (criterion 3) [prompt assembly] recalled memory is data, never instructions

### t11 — Session: context window, transcript log, remembering

- instruction: New `embodiment/session.py`. Rolling turn window with a token budget; private size-bounded per-session transcript log in the state dir; raw audio is never written. Remembering is an explicit spoken ask plus one attributed end-of-session summary, both through `memory.py`.
- depends on: t4, t10
- acceptance:
  - the window never exceeds its budget and drops oldest turns first, keeping the user's words verbatim
  - no code path writes audio bytes to disk, asserted by a test that scans the state dir after a fake session
  - an explicit ask writes one private record; ending a session writes one attributed summary; no other write occurs

### t12 — Voice out and barge-in

- instruction: New `embodiment/voice.py`. Sentence-split the reply, synthesize through `POST /v1/audio/speech`, play through the active endpoint. On `speech_started` during playback stop output and drop the undelivered remainder, recording it.
- depends on: t6, t7, t8
- covers: c39
- acceptance:
  - with a fake player, `speech_started` mid-playback stops output in under 200 ms and the dropped remainder is recorded
  - a TTS failure yields one degradation record and the reply text is still published as an event
  - played audio is fed to the feature extractor so the assistant trace reflects what was actually spoken
- obligation: `o15` (criterion 1) [playback] Gwen stops speaking when the listener starts, within the stated bound

### t13 — Event bus and the SSE projection

- instruction: New `embodiment/bus.py`. MQTT through `events-cli` is the internal substrate, with an in-process fallback recorded as a degradation when no broker answers. One versioned event schema (state, mic, turn, transcript, reply, degradation, features, clients, heartbeat), committed as JSON fixtures the web app tests against. SSE is a projection of the bus.
- depends on: t3
- covers: h16
- acceptance:
  - every event validates against the committed schema fixtures and carries a schema version
  - a heartbeat is emitted at a fixed interval asserted in a test, and a missing broker degrades visibly to in-process
  - no event payload ever contains the gateway key or the install secret
- obligation: `o16` (criterion 3) [event stream] secrets never enter an event payload

### t14 — Inbound realtime endpoint: the browser ear, robot-shaped

- instruction: New `embodiment/audio/remote.py`. A `websockets` server speaking the lobes `/v1/realtime` wire inbound, implementing `AudioEndpoint`. Authenticated by the install secret or a per-endpoint secret. v1 consumer is the browser; no robot support ships.
- depends on: t7, t6
- covers: c50, h33
- acceptance:
  - a recorded lobes-wire fixture drives the endpoint end to end and the frames reach the same interface the host uses
  - a connection without a valid secret is refused before any audio is accepted
  - README states that v1 ships no robot support and that the seam is what ships
- obligation: `o17` (criterion 1) [inbound audio wire] a later endpoint needs a URL and a secret, not a new protocol

### t15 — The daemon: wiring, one ear, zero clients

- instruction: New `embodiment/daemon/app.py`. Ears -> perception -> memory -> turn -> voice, publishing to the bus. One active ear with explicit, recorded handover. Client count is never an input to the turn loop. Features from captured AND played audio go to the bus.
- depends on: t5, t6, t9, t11, t12, t13, t14
- covers: c12, h15, c40, h30, c31, h20
- acceptance:
  - with zero clients attached a full fake turn completes, and attaching then detaching a client changes no daemon state
  - a second ear attaching pre-empts or is refused visibly, with one handover event, and never yields two sessions
  - each injected failure (dead gateway, STT error, TTS error, no device, recall timeout) leaves a degradation record and a running daemon
- obligation: `o18` (criterion 1) [turn loop] the daemon is the app: no browser is needed for a spoken turn
- obligation: `o19` (criterion 2) [ear handover] two realtime sessions never exist at once
- obligation: `o20` (criterion 3) [degradation ledger] no turn fails silently

### t16 — HTTP surface: static, SSE, control API, the guard

- instruction: New `embodiment/http/{server,guard}.py` on stdlib `http.server`. Serve `web/dist`, the SSE stream and the control API (start/stop voice, mute, status). Loopback bind unless `--bind-public`. Guard every state-changing request AND the stream: install secret, Host + Origin allow-list, and a valid `Cf-Access-Jwt-Assertion` when the Host is the public hostname. The gateway key stays server-side.
- depends on: t13, t5
- covers: c34, h24, c22, h19, h10
- acceptance:
  - POST with a foreign Origin, with a rebinding Host, and with the public Host but no Access assertion are each refused; the same three are refused on the SSE route
  - a routable bind address without the explicit flag is a `CliError` with a hint
  - a test greps every served asset and a captured stream for the gateway key and the install secret and finds neither; a missing `web/dist` yields a recorded no-dashboard state
- obligation: `o21` (criterion 1) [control API + SSE] arriving on loopback is never proof of being local
- obligation: `o22` (criterion 3) [served assets] the gateway key never reaches any browser

### t17 — Web app scaffold

- instruction: New `web/` like culture-nodes: Vite + React + TypeScript + Vitest. `web/src/culture-design/` copied from `org` at a pinned commit with `scripts/check-culture-design.py`. An `EventSource` hook against t13's schema fixtures; the switch, status, mic/mute, transcript, degradations, recall mode and remote-viewer indicator. Fonts bundled, no CDN.
- depends on: t13
- covers: c21, c4, c13
- acceptance:
  - Vitest component tests render every state from the committed event fixtures, including a disconnected state when the heartbeat stops
  - the design-token check script passes against the pinned `org` commit and fails on a one-byte edit
  - the built `web/dist` references no external origin
- obligation: `o23` (criterion 1) [dashboard] a lost event link shows as disconnected, never as a quiet room

### t18 — Waveform centrepiece and browser audio

- instruction: In `web/`: a canvas oscilloscope drawing the assistant trace and the listener trace from the feature events at animation-frame rate; browser mic capture and playback cited from lobes `site/src/scripts/` (`mic-capture.ts`, `pcm-wire.ts`, `audio-playback.ts`), talking to t14. `AnalyserNode` only when the browser is the ear.
- depends on: t17, t8, t14
- covers: c29
- acceptance:
  - with no microphone permission and no audio element, the assistant trace moves from fixture feature events and goes flat, not frozen, when the stream stops
  - pcm16 encode/decode round-trips a known buffer exactly
  - pitch, resonance and noise-floor readouts render when present and are absent, not zero, when not

### t19 — Packaging and CI for the web build

- instruction: `pyproject.toml` hatch config to ship `web/dist` inside the package as a build artifact; `tests.yml` builds and tests `web/`; `publish.yml` builds it before the wheel on both TestPyPI and PyPI paths.
- depends on: t16, t17
- covers: h22, h18
- acceptance:
  - CI fails a PR whose `web/` build or Vitest run fails
  - a test builds the wheel and asserts `web/dist/index.html` and the hashed assets are inside it
  - the node build adds under 3 minutes to CI, measured and written in the PR
- obligation: `o24` (criterion 2) [published wheel] pip install yields a working dashboard with no node toolchain

### t20 — Remote access through cultureflare

- instruction: New `embodiment tunnel` verb, dry-run by default like `lobes tunnel`, printing the `cultureflare remote-login setup --hostname agent.culture.dev --service ...` and `cloudflared` commands, and docs for Access `--allow` and a service token for non-browser endpoints. NEVER runs `--apply` itself: provisioning is the operator's act.
- depends on: t16
- acceptance:
  - `embodiment tunnel` prints the exact commands and mutates nothing, with `--json` and an `explain` entry
  - README documents that Cloudflare Access protects only the public hostname and how the daemon validates the assertion
  - a test confirms the verb never invokes `cultureflare` with `--apply`

### t21 — Live acceptance on the rig

- instruction: A scripted live session on this box, committed under `docs/live-test-results/` with raw per-turn numbers, n, rig and model pair. Publish latency as measured. A turn that fails with no degradation record fails the run.
- depends on: t15, t18, t19, t20
- covers: c27, h6, c24, h3, h29, c1
- acceptance:
  - 10 consecutive Hebrew spoken turns with 0 silent failures; median and p90 end-of-speech to first reply audio published with raw data
  - a fact stated before a daemon restart is recalled aloud after it, with the recalled record id in the prompt trace; all with zero browser clients attached
  - one live barge-in stops playback, Gwen's own speech produces no transcript during playback, and `stop`/`start` from the CLI and from the dashboard each take effect in under 5 s
- obligation: `o25` (criterion 1) [the running rig] presence is demonstrated live, and bad latency is published rather than hidden
- obligation: `o26` (criterion 3) [host audio] the operator's echo-cancellation claim is measured, not assumed

### t22 — Release docs: status, boundary, the not-yet list

- instruction: README, `CLAUDE.md`, `explain` root text, CHANGELOG and the version bump for the first daemon release. State: one rig only; software presence, not a body; value unmeasured; no tools, vision, face, phone voice or robot yet; the `reachy-mini-cli` `agent embody` name collision.
- depends on: t21
- covers: c23, h2, c26, h5, c28, h7
- acceptance:
  - a test asserts the `explain` root text and the README contain the software-presence boundary and the not-yet list
  - README's status section says the daemon's usefulness is unmeasured and that acceptance was on one rig, citing t21's result file
  - `markdownlint-cli2`, the rubric gate and `version-check` pass

## Risks

- [unknown_nonblocking] Cloudflare may buffer a long-lived SSE response through the tunnel; unprobed. Mitigation if so: padding comments and a short heartbeat, or long-poll fallback.
- [unknown_nonblocking] host audio device selection and resampling on this box are unexamined; the mic's hardware echo cancellation is accepted on the operator's word until t21 measures it. (task t7)
- [unknown_nonblocking] BlueTTS per-sentence latency is unmeasured and may dominate the turn; t21 publishes it either way. (task t12)
- [unknown_nonblocking] whether an MQTT broker already runs on this box for events-cli is unchecked; the in-process fallback keeps the base working without one. (task t13)
- [unknown_nonblocking] hatch must be told to include git-ignored `web/dist` as an artifact, for the sdist as well as the wheel; culture-nodes solved the same problem in Go, not Python. (task t19)
- [unknown_nonblocking] semantic recall is down on this rig today (embedder not ready), so t21's memory check exercises the lexical path only. (task t10)
- [follow_up] supervision: nothing restarts the daemon after a crash or reboot, and nothing runs cloudflared; a systemd user unit is the likely next step.
- [follow_up] cross-repo posts (colleague#358, reachy-mini-cli relay ask, closing ~45 moot issues) and `cultureflare remote-login --apply` each need the operator's explicit go-ahead; the plan drafts, it does not post or provision.
- [out_of_scope] vision, actions and agent triggers, the face, phone voice and the Reachy relay are later stages; the base ships their seams only.
