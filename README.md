# embodiment

**Gwen, a background realtime voice app** — a daemon that listens through a
microphone array, answers in Hebrew through a speaker model on the
[lobes](https://github.com/agentculture/lobes-cli) gateway, remembers what it
is explicitly asked to, and shows what it is doing on a dashboard. Underneath
it is a small, tested core for giving an app an embodied AI presence.

## Status

**The daemon is built and runs, on one rig.** `embodiment start` launches it;
it has run live Hebrew turns with the operator through a reSpeaker XVF3800,
stored and recalled memory across a restart, been interrupted mid-sentence,
and been watched from a phone. Every module was reviewed and probed on the
device or in a browser before it was merged.

**Its usefulness is unmeasured.** Nothing here claims that Gwen is worth
talking to. Acceptance is on **one rig only** — one array, one gateway, one
operator, one language — and the acceptance run (`t21`, a human at the
microphone) publishes latency as measured, including if it is bad. See
[`docs/live-test-results/2026-09-22-t21-acceptance.md`](docs/live-test-results/2026-09-22-t21-acceptance.md)
when t21's result file lands; until it does, no number in this repo describes
the daemon's turn latency, its echo cancellation or its speakability, and
this README quotes none.

Known from the integration runs, not yet measured:

- The first word after a silence can be lost by the gateway's segmenter
  (observed 3 times in about 8 synthetic utterances; the operator's own ~30
  live turns did not show it). Pause, then speak.
- Hardware echo cancellation (`aec_mode=aec`) is declared and relied on; t21
  checks that Gwen never transcribes her own voice.
- Recall is semantic by default (the `gemma2-embed` role, `google/embeddinggemma-2`,
  over a private vector cache) with a one-flag rollback to keyword recall
  (`EMBODIMENT_RECALL_MODE=keyword`); eidetic's keyword tokeniser is ASCII-only,
  so on that rollback Hebrew recall runs on an exact-substring fallback
  (deviation `d5`). The dashboard shows the recall mode actually in effect.

Measured once, on this rig, in
[`docs/live-test-results/2026-10-23-recall-gemma2.md`](docs/live-test-results/2026-10-23-recall-gemma2.md)
(aggregates only; one person's store of 24 records; queries written by a model,
not spoken by a person — none of it is a measurement of real use):

- **Recall, keyword versus semantic.** Close queries hit@5 18/24 keyword versus
  24/24 semantic; reworded queries 1/24 keyword versus 15/24 semantic at the
  shipped threshold (n = 24 each).
- **The similarity threshold is `0.715`, and provisional.** It was chosen on the
  data it was scored on, for the highest hit@5 with at most 5% false positives:
  hit@5 39/48, false positives 2/44 (n = 48 labelled, 44 unrelated). One
  held-out fold reached 2/22 (9%), and the unrelated queries were easy
  negatives, so that rate is a floor. Precision against a realistic distractor
  pool (newspaper text) is not measured.
- **The recall bound is derived from the store it scores**: the query embed's
  p90 (161 ms, n = 30) plus 0.13 ms per active record plus 0.15 s, capped at
  3 s. A spoken turn still waits at most 0.25 s for memory before answering.
  Status warns past about 400 active records (the largest store measured with
  no recall over 0.25 s) and past about 600 (where p90 crossed it).
- **Clips are capped at 60 s**, and the 46.8 s `stt` clock is derived from the
  measured transcription speed: at most 0.614 s per audio second (n = 28
  synthetic clips of 1 to 8.8 s; speed only, not accuracy, so a 60 s clip is an
  extrapolation). A `senses` completion took 4.0 s median and 5.1 s at most
  (n = 30), well inside the 30 s completion bound.

**The library is queried, not copied (0.23.0, embodiment#107); its value is unmeasured.**
The live acceptance (t9) ran on the phone
(`docs/live-test-results/2026-10-26-library-queried-and-heard.md`): tap to first sound
was warm "immediate" and cold about 6 s, over the 5 s bar, both the operator's estimates by
ear (n = 1), not timed. Before 0.23.0 Gwen
kept her own copy (`read_item` was capped at 40 passages and 16,000 characters,
`library/reading.py:36-37`; one spoken reply at 4,000, `voice.py:333`): a sync
(`embodiment/library/sync.py`) fetched every item, and
a 0600 document index (`embodiment/library/index.py`, version 3, a 0.715
threshold and a 60-character floor) answered search. That retrieval was
**measured once**, against that retired index, in
`docs/live-test-results/2026-10-24-library.md` (aggregates only, model-written
questions, 50 indexed items): hit@5 was 19 of 32 and 17 of 28 on two question
sets, with 0 of 10 off-topic false positives in each run and a search p90 of
0.21 s. That number describes the index that no longer exists. Search now asks
the library's own `POST /api/rag/retrieve` (hybrid mode), and the only things
measured about it are the probes of 2026-10-26, on this rig, n = 5 retrieve
latencies (hybrid 0.74-1.12 s, against the old warm p90 of 0.21 s; culture-library#25
tracks a faster path), the shape of the live catalog (68 items) and of an item's
`/text`, and n = 2 text-to-speech runs per language (warm 0.46-0.83 s for 7.7-10.7 s
of audio, but the first, cold call took 6.59 s). Hit rate against the library's
retrieval is not measured, no similarity floor is applied (hybrid scores are
0.011-0.033, not comparable to memory's `0.715`), and transcripts reach the
library through culture-rules (decision c21): 33 Pocket transcripts were there
on 2026-10-24.

Still not measured:

- The follow-up window for a late recall is off by default and unmeasured.
- Whether the `stt` lane accepts the browser's MediaRecorder webm/mp4 output
  (and the duration cap for those containers), and the load on the
  `gemma2-embed` host (orin).

The rig was re-probed on 2026-10-23: `senses`, `worker` and `gemma2-embed` are
ready but proxied; `stt` and `tts` are ready and local. Roles resolve by name;
re-probe before relying on this.

Everything that used to live here — the strategist tier, the configuration
lane, the muse, drones, the example hosts and the old live-test results — is
archived in git history, not deleted from the record. The tag
[`archive/pre-realtime-0.14.0`](https://github.com/agentculture/embodiment/tree/archive/pre-realtime-0.14.0)
is the last commit that contains it; its own published verdicts (mechanism
proven, value not) are quoted in `CLAUDE.md`, and the redesign inherits that
honesty: it makes no value claim until it has a measurement.

## Software presence, not a body

"Embodiment" is an overloaded word in this mesh. This package is **software
presence, not a body**: it gives an *application* ears, a voice, memory and a
loop. It does not drive hardware and nothing here claims a body.

- `reachy-mini-cli` owns the physical robot, and has its own **`agent embody`**
  layer — a name collision with this package, and a different thing. Nothing
  here is that.
- `reachy-lobes` owns the robot's local brain.
- A later stage lets a Reachy Mini act as a relay for Gwen's ears and voice.
  That is a planned, stated seam, never an implication drawn from the name.

## What runs

- **A daemon** — `embodiment start` / `status` / `stop`. It works with no
  browser open; the dashboard is one client of it. `status` reports process
  liveness, degradation counts and the recall mode in effect — and says
  plainly that a live process is not evidence that anything was heard.
- **Live voice is opt-in** (d6, 2026-10-24): the daemon starts with no ear; the
  page's Live button opens the room mic and closes it again (`embodiment start
  --listen` keeps the old hot mic on start). The mic is the reSpeaker XVF3800 (`pw-record` /
  `pw-play` over PipeWire, `arecord` / `aplay` as fallback). The mic is always
  visible and always mutable; mute is enforced in the capture path, before
  encode, never in the UI.
- **An ears-only session** on lobes' `/v1/realtime`, dialled at the array's
  native 16 kHz. **The daemon runs the turn**: it never sends
  `response.create`. It takes the transcript, recalls, calls the `senses` role
  (a Gemma) for every spoken reply, and speaks through `POST /v1/audio/speech`
  into the same array.
- **Barge-in is the daemon's.** On speech during playback it stops its own
  speaker; the ears-only session never receives `response.interrupted`.
- **Memory in a private store** under the daemon's state directory, never the
  committed pool. Two things are remembered: an explicit spoken ask, and one
  summary at session end. Recalled memory enters the prompt at exactly one
  place, quoted, attributed and fenced — it is untrusted data, never
  instructions.
- **An HTTP surface** on loopback (or a tailnet address with `--bind-public`
  and `--allowed-host`): the dashboard, a control API, and a server-sent event
  stream. The install secret is required on every guarded request and on the
  stream, because the stream carries the transcript.
- **The dashboard** (React / Vite / TypeScript, shipped inside the wheel) is
  chat-first, the library with the chat inside it: culture-library's look and shell around
  pocketchat's thread (both cited, not imported). **Ask** is a three-pane
  workspace: your conversations on the left (the live one and the earlier ones,
  read from Gwen's own session logs through `GET /api/conversations`; an
  earlier one can be continued as if it never stopped (and **New conversation**
  ends the live one and starts afresh): its turns are Gwen's
  context and new turns are added to its log, while the room's voice session is
  untouched; once a conversation has been quiet for 30 minutes Gwen indexes it
  herself and can search it later, every turn tagged with how it arrived:
  typed, transcribed from audio, or generated by her; a cited past
  conversation opens here, ready to continue), the conversation in the middle
  with numbered
  citations and a Speak button per answer (markers are never read aloud), and
  on the right the **Library** list and the answers' **Sources**, which open
  the cited passage in place. Both side panes toggle, leave a rail when closed,
  and become drawers on a phone. Below the thread, the box holds the mic
  (record and send a question) and the text; beside it sit **Live** (the room
  microphone's
  realtime voice) and Upload. **Library** is the shelf and the items as cards,
  **To-dos** comes from the library only: the `kind=todo` items culture-rules files
  (one Markdown checklist per recording), re-read every 30 s, line for line. It is
  checkable: your mark (`POST /api/todos/{id}`, the checklist id such as
  `pocket.<uuid>.<n>`) is Gwen's **pending** mark, shown at once and re-sent to
  culture-rules at each 30 s check, for up to an hour, until the library's own line
  agrees, compared to the second; then it counts **confirmed**. A line the library no
  longer holds drops its mark. Each change is published as a `todo` event carrying the
  checklist id and the `recording_id`. `status.todos` counts confirmed, pending,
  unconfirmed and dropped marks; with the library down or never loaded the list
  answers 503 `library_unavailable`, with none configured `library-not-configured`.
  Gwen answers about them with `list_todos` and marks one with `set_todo_done` only
  when your own words ask (d16). The first real flip, a mark confirmed by the live
  library, is not yet on record: that is an open obligation the operator files.
  **Stats** shows the library and the
  usage counters since Gwen started, and a status drawer keeps the room voice
  controls and the degradations. Before 0.18.0 the page was waveform-first
  with chat fourth. It streams events with `fetch` and sends the secret in the
  `Authorization` header, so it works from a phone over TLS as well as on
  localhost; the install-secret form shows only when the stream is
  unauthorized, and on the Access-protected public host no secret is entered.
- **The library read path** (optional). The library is **culture-library**
  (library.culture.dev); **its instance on 127.0.0.1:8796 is today's** (d15, 2026-10-24).
  Gwen keeps no copy of it any more (0.23.0, embodiment#107). Two clients talk to
  it, both configured by `EMBODIMENT_LIBRARY_URL` and `EMBODIMENT_LIBRARY_KEY`:
  `embodiment/library/client.py` is **GET-only by construction** (an AST test pins
  that it never sends POST, PUT, PATCH or DELETE; its `gets` counter in `status` is
  the live evidence) and lists the catalog (every 60 s) and fetches an item's
  `/text`; `embodiment/library/retrieve.py` makes **one retrieve POST**, to the
  literal path `/api/rag/retrieve` in hybrid mode, and nothing else (an AST test
  pins that too). `search_library` and `read_passage` ask it live on typed turns;
  a date argument lists that day's items from the catalog and passes their ids as
  `item_ids`. Retrieve answers with the library's best passages and no score floor is
  applied, so the result says "best passages only". The first start on this release
  **deletes the retired index file once** (`<state>/library/index.json`, recorded as
  `app-library-index-retired`) and there is **no switch back**: the rollback is installing
  the previous release, whose sync rebuilds it. Gwen's own conversations keep a separate
  index (`search_conversations`). Library tools run on typed turns only; their results
  reach the model inside the same untrusted-data fence as recalled memory, and every
  cited passage appears in the reply's sources with item id, title, date and anchor, so
  a `[n]` citation opens the reader at that passage. Routes: `GET /api/library`,
  `/api/library/{id}`, `/api/library/search` and `/api/todos`, all behind the guard,
  answering through retrieve and `/text`. Unset config means no library tool and a
  visible "library not configured".
- **Read aloud.** Gwen can read a library document to you, in order, and remember
  where she got to. `embodiment/daemon/reading.py` splits an item's `/text` into
  units (a paragraph, or sentence-bounded parts of at most 4,000 characters) and keeps
  one cursor per item in `reading-positions.json` (0600, in the state dir).
  `GET /api/reading` lists the saved positions; `GET /api/reading/{id}/next` serves the
  next unit with an opaque position token (with `section` and/or `paragraph` it is a
  seek that re-pins the cursor, forward or back: deviation d1); `POST
  /api/reading/{id}/heard` moves the cursor past a unit only when the token names exactly
  the cursor's unit, and `POST /api/reading/{id}/clear` drops it. The page speaks each
  unit through `/api/speak` (the Reader dock, Continue listening on the Library, and a
  Read aloud card in chat); `status.reading` counts reads started, units served and
  heard, refused reports, continues and seeks. The dock's Choose section (0.23.2) lists the
  item's sections by heading and reads the chosen one from its first paragraph, a seek
  like previous, next and a tap on a paragraph. The live acceptance (t9) ran on a
  phone, with tap to first sound judged by ear: warm at once, cold about 6 s, over the 5 s
  bar.
- **The write guard.** An xteink key is a full operator key, so the client never
  writes; and in a turn whose context holds library text, `remember` and
  `forget` are refused unless the operator's own words in that turn name the
  write. Each refusal is counted (`library_guarded_writes`) and recorded
  (`app-library-guarded-write`). The audio subprocesses are spawned with a
  scrubbed environment that drops the gateway and library keys.
- **Text and clips**: a chat view on the dashboard (`/api/chat`) with every
  recalled fact cited by source, a speak button (`/api/speak`), and
  record/upload of a clip transcribed by `stt` (`/api/clip`).
- **`embodiment tunnel`** prints the `cultureflare` / `cloudflared` commands
  for remote access and never runs them.
- **Degrade, never raise.** A dead gateway, no audio device, a mid-session
  drop, a recall that missed its deadline: each is a recorded degradation,
  visible in `status`, on the dashboard and in a crash-durable ledger, and the
  daemon keeps running. Nothing degrades silently.

Retention: recordings are kept. Every clip the page sends (recorded on its mic or
uploaded) is kept as it arrived, before it is transcribed (d10), and so is every
utterance the room microphone hears, from speech onset to its end, as a WAV
(d11); the silence between utterances is not written. They live under
`recordings/` in the state directory: 0600 files named by their sha256, in a 0700
directory, capped at 2 GiB. A recording past the cap is refused and recorded, and
nothing is deleted to make room. Archiving to S3 is tracked in #99. Transcripts go
to a private, size-bounded, per-session log (0600) under the state directory
(0700). Each turn says how it arrived (d7), links its recording, and carries typed
timestamps (d12): `created`, `recorded`, `witnessed`, `received`, `transcribed`,
`generated`, with the page's own times marked `by: client`.

## How to run

```bash
uv sync                    # creates .venv with the approved dependencies
uv run embodiment doctor   # identity invariants; nothing is started
```

The gateway key comes from the `grant` CLI and is injected into the daemon's
environment for that one process. There is no env-file fallback: the daemon
reads `EMBODIMENT_GATEWAY_KEY` (or the mesh-wide `CULTURE_VLLM_API_KEY`) from
its environment and nothing else, and the key never reaches a browser, an
asset, an event, a log line or a `status` field.

```bash
grant run --inject EMBODIMENT_GATEWAY_KEY=LOBES_GATEWAY_API_KEY -- uv run embodiment start
uv run embodiment status
uv run embodiment stop
```

`start` returns once the daemon has reported *itself* running, not merely
once a process exists; a second `start` finds the first and starts nothing.
`EMBODIMENT_GATEWAY_URL` selects the gateway (default `http://localhost:8001`);
its `stt` lane must be local to that gateway, because the realtime session
never crosses machines (the gateway refuses to tunnel a WebSocket to a proxied
peer).

**The library** is optional. Set `EMBODIMENT_LIBRARY_URL` and
`EMBODIMENT_LIBRARY_KEY`; the key is injected, never written by Gwen:

```bash
grant run --inject EMBODIMENT_GATEWAY_KEY=LOBES_GATEWAY_API_KEY \
  --inject EMBODIMENT_LIBRARY_KEY=XTEINK_GWEN_API_KEY -- uv run embodiment start
```

**The dashboard** is at `http://127.0.0.1:8823`. It asks for the install
secret; paste the contents of `<state dir>/install-secret`, where the
state directory is `$EMBODIMENT_STATE_DIR`, else `$XDG_STATE_HOME/embodiment`,
else `.local/state/embodiment` under your home directory (the XDG default). The
daemon creates that file (0600) on first start.

**From another device on your tailnet**, bind off loopback and name the host
a browser will send:

```bash
grant run --inject EMBODIMENT_GATEWAY_KEY=LOBES_GATEWAY_API_KEY -- \
  uv run embodiment start --http-bind 100.x.y.z --bind-public --allowed-host 100.x.y.z:8823
```

`--bind-public` is required for any non-loopback bind — the event stream
carries the transcript — and `--allowed-host` is matched exactly as the
browser sends it. Put TLS in front (`tailscale serve` pointing at
`127.0.0.1:8823` is the cheapest way): over plain http to a non-localhost
address the browser withholds what the guard needs to vouch for a cookie, and
`status` reports `http.secure_context_required` when that applies.

## Personas, model choice and spark-gwen

Unreleased (0.24.0, branch `feat/gwen-personas-models-mesh`); measured only where
stated.

- **Personas.** The Personas tab (`/api/personas`) holds personas the operator
  writes: a private 0600 store, a language (`he`, `en` or `none`), a length and
  free text. The built-in Gwen is the default; her **spoken** prompt is
  byte-identical to before, and her **typed** prompt drops the speech-only lines
  (deviation d1: short sentences fit speech, not text). The tool registry, the
  write guard and the identity framing are the same with and without a persona.
- **Model choice per conversation.** The page lists the roles the gateway
  reports on `/capabilities` (`/api/models`, polled every 60 s) and a
  conversation can pick one. A chosen role that is unavailable is its own
  degradation (`app-text-model-unavailable`, distinct from the gateway being
  down) and there is **no fallback** to another model; the trace carries
  `chat_role`, `chat_model` and `chat_host`. **Voice is gated:** `senses` is
  exempt; any other role speaks only after its voice latency has been measured
  by the background probe. `stt`, `tts`, the realtime session and the embedder
  do not change with the choice.
- **The translator.** A persona's `translate_via` role is the Gemma translator
  (`embodiment/translate.py`): the chosen role sees English, and `remember` and
  `search_*` use the operator's own words, not the translation.
- **English is text-only.** lobes has no English voice (its `tts` role is
  Hebrew-only), so an English persona answers in text and is never spoken.
- **spark-gwen.** Gwen's daemon holds its own agentirc connection as
  `spark-gwen` (`EMBODIMENT_MESH`, on by default; `EMBODIMENT_MESH_NICK`,
  `EMBODIMENT_MESH_SERVER`, `EMBODIMENT_MESH_CHANNELS`); `embodiment mesh` prints
  the setup and runs nothing. The `ask_culture` tool posts **only the question
  she composed** as its own thread to a listed channel; answers arrive later,
  also after expiry, marked as late. Any agent on the mesh may talk to her and
  gets a normal turn, **memory included**: a local agent is allowed to know
  local memory, so **a newly linked server can read Gwen's memory**. The nick is
  not authenticated by agentirc; link only a local or trusted server. Her
  listing as a resident agent awaits agentculture/culture#506. A mesh answer
  that asks her to remember, forget or mark a to-do causes none of those writes.
- **Unmeasured:** Hebrew quality of the non-Gemma roles, the English
  `remember`/`forget` call rate, voice latency per role and for the translator,
  and mesh answer latency.

## Boundaries

- **Hebrew is the only voice.** Listening and speaking are Hebrew, matching the deployed audio
  lane on the rig (`stt`: `ivrit-ai/whisper-large-v3-turbo`; `tts`:
  `notmax123/BlueTTS2.5-onnx`). Roles resolve by name on the gateway; the
  models behind them drift.
- **One active ear at a time.** lobes has not validated concurrent realtime
  sessions, so a second ear pre-empts or is refused, and either way the
  handover is published as one event and one ledger record.
- **The session never crosses machines.** The daemon needs a gateway whose
  `stt` lane is local. The session is audio-only.
- **Loopback is not authentication.** Every state-changing request and the
  event stream need the install secret; off loopback they also need a
  Host/Origin allow-list, and behind a public hostname a Cloudflare Access
  assertion (see below for what that verifier does today).
- **Identity is explicit.** Gwen is configured, never inferred from a model
  name; absent identity produces byte-identical prompts; identity framing
  renames who is speaking, never what they may do.

## Not yet

None of the following is in this release. Each is a stated seam or a stated
absence, not an implication.

- **Other tools.** The daemon binds `remember` (d7) and `forget` (d8, which
  archives a record in place and never deletes a byte, and drops its vector; it
  does not redact past conversation transcript logs, which are private, 0600 and
  size-bounded and may have quoted the memory — a follow-up), and, on **typed
  turns only** (chat and clips) and only when a library is configured, two
  read-only library tools: `search_library` and `read_passage`. Also on typed
  turns, `search_conversations` searches Gwen's own earlier conversations (d8),
  and `list_todos` / `set_todo_done` read and mark the to-dos (d16).
  They are indexed in her private conversation index once each has been quiet for 30
  minutes, every turn labelled with who said it and how it arrived, her own replies
  marked as not a source of fact; the live conversation is never searched.
  To summarize a whole item (the day's newspaper), `read_item` reads it in
  order; to follow an article's source, `fetch_source` reads one web page —
  **only a link that library item lists** (the operator's choice), over http or
  https to a public address (private, loopback and link-local refused, the
  connection pinned to the checked address, every redirect re-checked), GET
  only, at most 2 MB and 15 s, the text fenced as untrusted like library text.
  This is Gwen's first reach outside the rig. Spoken turns keep today's path.
  `ask_culture` posts one question to a listed mesh channel and the answer
  comes back later. Nothing else — no shell, no files, no Qwen Code, and no
  tool that writes to the library. Gwen cannot act on anything outside her own memory, her own
  conversations and her read of the library and the pages it links.
- **Vision.** The session is audio-only; `senses` does not advertise image
  understanding on this rig.
- **A face.** The waveform is a compact strip beside the chat; a
  speech-driven face is later.
- **Live realtime voice and speech-to-speech.** The phone is control, text, and
  uploaded or recorded clips (transcribed by `stt`); there is no live voice from
  the browser. The dashboard's **Live** button turns on the rig's room
  microphone, not the phone's, and no speech-to-speech role is used. The `BrowserEar` client and the inbound `/v1/realtime`
  endpoint (`audio/remote.py`) exist and are tested, and are **not wired** into
  the daemon.
- **An English voice.** lobes `tts` is Hebrew-only; English is text-only.
- **A robot relay.** Same seam, same status: a Reachy Mini speaking the lobes
  wire into that endpoint is a future, separate implementation.
- **Reconnection.** A dropped realtime session is recorded and the daemon
  keeps running deaf until restarted.
- **A measured value claim.** See Status.

## Remote access

`embodiment tunnel` prints the `cultureflare`/`cloudflared` commands for
exposing the daemon beyond loopback — it is **dry-run only**: unlike `lobes
tunnel` there is no `--apply` flag here at all, because provisioning a
Cloudflare Tunnel and Access app is the operator's act, run by hand from the
printed command, never something this verb does itself.

```bash
embodiment tunnel
embodiment tunnel --json
embodiment tunnel --hostname gwen.example.org --allow me@example.com
embodiment tunnel --with-service-token
```

The default hostname is `gwen.culture.dev`. It prints two commands and runs
neither, then the Access environment variable names the daemon needs (names
only, never values):

1. `cultureflare remote-login setup --hostname <h> --service
   http://127.0.0.1:<port> [--allow <email>]... [--with-service-token]
   [--tunnel-name <name>]` — the one-time provisioning of the tunnel, the DNS
   record and the Cloudflare Access app/policy. `--allow <email>` (repeatable)
   is who the Access policy admits by browser login.
2. `cloudflared tunnel run <name>` — what the operator runs afterwards, once
   step 1 has actually been applied with `--apply`. `cloudflared` refuses a
   bare `cloudflared tunnel run` (it needs the name, or a `--token`), and
   `cultureflare` derives that name itself unless `--tunnel-name` overrides
   it — a rule `embodiment tunnel` does not know and will not guess. Pass
   `--tunnel-name <name>` and both commands carry that exact name; omit it and
   step 2 prints `<tunnel-name-from-step-1>` with a line saying to read the
   real name off step 1's own output before running it.

**A printed command is only honest if pasting it does what it says.**
`--hostname` (RFC-1123 labels), `--allow` (exactly one `@`, no whitespace) and
`--tunnel-name` (`[A-Za-z0-9._-]` only) are refused at parse time — a
structured `error:`/`hint:` and exit `1` — if they fall outside that charset,
and every token of a printed command line is `shlex.quote`-d regardless. So
`embodiment tunnel --tunnel-name 'a; touch pwned'` is refused outright, and
even a value that somehow bypassed that check would still print as one inert
quoted token, never a second shell command.

**What Cloudflare Access protects, and what it does not.** Access sits in
front of exactly one thing: requests arriving for the **public hostname**
named by `--hostname`. It never gates loopback names (`localhost`,
`127.0.0.1`, `::1`) — those never cross Cloudflare's edge at all, so there is
nothing for Access to intercept. Loopback is not authentication, so the
daemon does not rely on Access alone either (see below).

**How the daemon validates the assertion.** `embodiment/http/access.py`
verifies the `Cf-Access-Jwt-Assertion` JWT with the standard library (RS256 is
one modular exponentiation, so no new dependency): the `kid` must name a key in
the team JWKS, the signature must verify, `iss` must be the team domain, `aud`
must contain the application's AUD tag, the token must be inside `nbf`..`exp`,
and the person must be on the operator's email allow-list. The guard applies it
to every guarded request whose `Host` is the configured public hostname, and
only those. Configure it with three environment variables, set **together or
not at all** (names only here; the values come from the Access app):

- `EMBODIMENT_ACCESS_TEAM_DOMAIN`
- `EMBODIMENT_ACCESS_AUD`
- `EMBODIMENT_ACCESS_EMAILS`

and name the public Host with `embodiment start --public-hostname <name>` (or
`EMBODIMENT_PUBLIC_HOSTNAME`); a daemon started without it treats no Host as
public, and `status()["http"]["public_hostname_configured"]` says which. With
the Access env unset the guard is unchanged and refuses every assertion
(`http-access-verifier-missing`); a partial set refuses every assertion and
records it (`http-access-config-incomplete`). An unreachable JWKS is
infrastructure, not a bad token: the request is refused with 503 and the
outage is recorded once. Every refusal has a fixed reason that carries no token
or email.

**On the public Host a valid assertion replaces the install secret**, so the
operator reaches the dashboard and chat at `gwen.culture.dev` by signing in with
Cloudflare. Every other Host (loopback, a tailnet name) still needs the install
secret, whatever assertion it carries — an assertion off the public Host did not
come through Cloudflare. Because Cloudflare attaches the assertion to any request
carrying the `CF_Authorization` cookie, an Access-admitted request must also carry
an allow-listed `Origin` or `Sec-Fetch-Site: same-origin` (a CSRF rule); the event
stream is closed when the assertion expires. This is verified against synthetic
tokens in tests; a live pass through Cloudflare is the operator's, and is not
claimed here.

**Service tokens are refused.** Gwen's public surface is for a person: a valid
assertion that names a service token (no person) is refused
(`http-access-service-token`), as is a person who is not on the allow-list
(`http-access-email-not-allowed`). `--with-service-token` still prints the
`cultureflare` flag, but it does not admit a non-browser client here; a robot
relay is a future, separate seam.

**Memory ingest.** Producers (Pocket recordings, culture-rules' daily newspaper)
enqueue facts with `embodiment memory ingest --file <ndjson> [--apply]` and
`embodiment memory forget`. The verbs validate and enqueue only, and are a dry run
unless `--apply`; the daemon is the single writer of the store. A newspaper issue
is archived together 30 days after its issue date. Recalled facts are cited by
their source in the chat view.

## Inbound realtime endpoint — a browser-shaped, robot-shaped seam

`embodiment/audio/remote.py` is a `websockets` server that speaks the lobes
`/v1/realtime` wire **inbound**, implementing the same `AudioEndpoint`
protocol `embodiment/audio/host.py` implements for the machine's own
microphone and speaker. A client dials in, the WebSocket handshake completes
carrying no credential at all, and the FIRST message on the socket must be
`{"type": "auth", "secret": "..."}` — checked against an install secret (or a
per-endpoint secret) in constant time before any other message, audio
included, is ever processed. A wrong, missing, malformed or late first message
closes the socket (code 1008) and is recorded. The secret deliberately never
rides the connect URL — a URL lands in proxy access logs, browser history and
`Referer` headers, all of which this repo's privacy rules forbid for a
credential.

**v1 ships no robot support and wires no client to it.** The endpoint and the
browser-side `BrowserEar` are built and tested; the daemon composes only the
host endpoint today. What
ships is the *seam* — one `AudioEndpoint` implementation among several the
daemon can compose — so a browser ear or a robot relay speaking the same wire
is a future, separate step, arrived at without touching the daemon that
composes endpoints.

## The core underneath

| Module | What it is |
|--------|------------|
| `loop.py` | The bounded perceive → decide → act tool loop the turn runs on. Termination is proved *structurally* by AST tests |
| `turn.py`, `tools.py` | One spoken turn through `loop.run`, never silent, never raises; the tool registry (empty by default; the daemon binds `remember` and `forget`, plus `search_library` and `read_passage` on typed turns when a library is configured) |
| `session.py`, `memory.py` | The conversation (explicit-ask detector, turn queue, supersede on barge-in, summary on close) and `RoomMemory`: private, pinned, deadline-bounded |
| `realtime/`, `voice.py` | The typed lobes wire and the ears-only client; sentence-by-sentence TTS with paced waveform features |
| `audio/` | `AudioEndpoint` protocol, the host endpoint, the inbound endpoint, the feature extractor |
| `library/` | The GET-only catalog client, the one retrieve POST, `/text` documents, the to-do checklist source, the conversation index, and the read tools; no library copy (the sync is gone) |
| `daemon/reading.py` | The read-aloud cursor (`reading-positions.json`, 0600) and its next/heard machine |
| `http/`, `bus.py` | Guard (secret + allow-list + Access seam), server (static dashboard, SSE, control API), the event bus with redaction at publish |
| `daemon/` | State dir, bounded logs, crash-durable ledger; lifecycle by `(pid, start time)`; the app that wires it all |
| `perception.py`, `identity.py`, `framing.py`, `senses_text.py` | The verbatim invariant; explicit identity; absent identity ⇒ byte-identical prompts; measured grounding text |
| `continuity.py`, `events.py`, `presence*.py`, `subagent.py`, `context.py`, `media.py`, `contract.py` | The eidetic/coherence seam, the MQTT observer, the presence pump, and what the loop needs |

## Dependencies — what installing this costs you

Dependencies are human-gated: `tests/test_zero_deps.py` pins the exact approved
set and fails on any change in either direction.

| Dependency | Pulls in |
|------------|----------|
| `eidetic-cli>=0.12` | `data-refinery-cli[store]` → neo4j + pymongo (installed, never imported at module scope) |
| `coherence-cli>=0.6` | numpy + httpx |
| `events-cli>=0.10` | paho-mqtt (imported lazily) |
| `websockets>=15` | the realtime client and the inbound endpoint |

Host audio runs through `pw-record`/`pw-play` (or `arecord`/`aplay`) as
subprocesses — no in-process audio library (deviation `d4`). `lobes-cli` is
reached over the network only and is forbidden as a dependency.

## CLI

```bash
embodiment start         # the daemon, detached; idempotent
embodiment status        # the daemon's state, truthfully; never starts anything
embodiment stop          # bounded stop; a SIGKILL is reported and recorded
embodiment tunnel        # print the remote-access commands; runs nothing
embodiment whoami        # identity from culture.yaml
embodiment learn         # structured self-teaching prompt
embodiment explain       # markdown docs; `explain <path>` for any verb
embodiment overview      # descriptive snapshot
embodiment doctor        # agent-identity invariants
embodiment cli overview  # the CLI surface
```

Every verb takes `--json`. Results go to stdout, errors and diagnostics to
stderr, never mixed. Exit codes: `0` success, `1` user error, `2` environment
error. No Python traceback ever reaches stderr.

## Development

```bash
uv sync
uv run pytest -n auto
uv run pytest --cov=embodiment --cov-report=term   # gate: 60%
uv run teken cli doctor . --strict                 # the agent-first rubric CI enforces
```

Lint stack (line length 100): `black`, `isort`, `flake8`, `bandit`,
`markdownlint-cli2`. Every PR bumps the version. The dashboard has its own
`vitest` suite under `web/`.

## Where embodiment sits

| Layer | Package | Owns |
|-------|---------|------|
| Surface | `agentfront` | CLI / MCP / HTTP fronts |
| Tools | `shell-cli` | The file-and-shell tool surface |
| **Loop + presence** | **`embodiment`** | **Perceive → decide → act, and the presence between acts** |
| Models, ears, voice | `lobes-cli` | The gateway, roles, `/v1/realtime`, STT and TTS |
| Memory / trust | `eidetic-cli`, `coherence-cli` | Durable recall; consistency signals |
| Product | `colleague` | The coder-agent harness |

## License

See [LICENSE](LICENSE).
