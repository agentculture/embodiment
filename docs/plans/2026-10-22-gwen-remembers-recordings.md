# Build Plan — Gwen remembers recordings

slug: `gwen-remembers-recordings` · status: `exported` · from frame: `gwen-remembers-recordings`

> The operator talks with Gwen — by voice on the rig and by text at gwen.culture.dev behind Cloudflare Access — and she remembers their Pocket recordings and culture-rules' daily newspaper: culture-rules enqueues distilled facts through an embodiment-owned memory ingest verb, the daemon is their single writer, recall finds them semantically through gemma2-embed, every recalled fact is cited by its source, and pocketchat can retire

## Tasks

### t1 — Ingest contract: source registry, record validation, caps and charset-safe stored ids

- instruction: Pure module: no IO, no model call, no clock (pass dates in). Map wire ids with a fixed scheme such as replacing ':' with '.', and reject inputs that would collide after mapping. Keep the wire id in metadata. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- covers: c33, h30, c36, h32, c6, h13
- acceptance:
  - owns embodiment/ingest/`__init__.py`, embodiment/ingest/contract.py and tests/`test_ingest_contract.py` only
  - a registry holds exactly two sources, pocket and newspaper, each with its allowlisted types, id prefix, required provenance fields and per-record and per-batch caps; any other source is refused with a CliError-shaped error naming the registry
  - `validate_batch`(lines) validates every record before returning anything and returns either the whole stamped batch or one refusal with the first failing line number
  - stamping forces visibility private, the shared ingest scope and `added_by` culture-rules whatever the input says
  - wire ids such as `pocket:<rec>:<kind>:<n>` map deterministically to a stored id inside `HEADER_LABEL_CHARSET`, and the mapping is stable across runs

### t2 — Ingest spool: atomic enqueue, ordered inbox, receipts-only done/, purged failed/

- instruction: Reuse daemon/state.py's helpers by importing them (`resolve_state_dir`, the private dir/file modes, the atomic writer); if one is private, add a public alias there in this task and nothing else in state.py. Forget requests are batches of their own kind in the same queue, so ordering holds across ingest and forget. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- covers: c2, h9, c50, h57
- acceptance:
  - owns embodiment/ingest/spool.py and tests/`test_ingest_spool.py` only
  - enqueue writes a temp file and renames it into a 0700 inbox as a 0600 file with a sortable name; a refused batch leaves the inbox directory byte-identical
  - batches list in enqueue order; `mark_done` keeps only a receipt (batch id, record ids, counts, hashes), never record text; `mark_failed` keeps the reason and failed/ entries older than 7 days are purged
  - status() reports pending, done and failed counts and the oldest pending age, and works with no daemon running

### t3 — Cited stdlib Cloudflare Access verifier with an operator email allow-list

- instruction: Copy pocketsync/pocketsync/web/access.py (326 lines) and adapt; standard library only, urllib not httpx. Its tests (pocketsync tests/`test_web_access.py`, 26 tests) are the model for yours. Return a result the guard's AssertionVerifier seam can take (guard.py:296-324); never raise out of verify. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- covers: c19, h22, c48, h40
- acceptance:
  - owns embodiment/http/access.py and tests/`test_http_access.py` only
  - the module header cites pocketsync/web/access.py, culture-rules `culture_rules`/auth/access.py and culture-nodes internal/auth/verifier.go, and says this copy is owned by embodiment
  - a token signed with a locally generated RSA key and served by a fake JWKS fetch verifies; bad signature, wrong iss, wrong aud, expired, not-yet-valid, unknown kid and malformed each fail with their own safe reason
  - only an sso identity whose email is in the configured allow-list is admitted; a service token or another email is refused even when the signature is valid
  - an unreachable JWKS yields an unavailable result distinct from a refusal; config is the all-or-nothing pair `EMBODIMENT_ACCESS_TEAM_DOMAIN` + `EMBODIMENT_ACCESS_AUD` plus `EMBODIMENT_ACCESS_EMAILS`

### t4 — Vector cache and gemma2-embed client

- instruction: Probe findings: one call through the proxy to orin costs 158-210 ms; eidetic's no-prompt payload is accepted (cos 0.901 related vs 0.715 unrelated, n=1); the lane accepts Retrieval-query / Retrieval-document prompt names and refuses 'Query'. Key via `EMBODIMENT_GATEWAY_KEY` as the daemon already does; tests use a fake transport. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- covers: c38, h33, c51, h42
- acceptance:
  - owns embodiment/vectors.py and tests/`test_vectors.py` only
  - the cache is a 0600 file under a given dir, keyed by text hash + embedding model; an entry for another model is never returned; drop(`record_id`) removes an entry
  - the client embeds through the gateway's /v1/embeddings with model gemma2-embed named by role, and online state comes from the last real call plus a bounded background re-probe; a recall-path call makes exactly one request
  - an unreachable embedder is reported as offline with a reason, never raised

### t5 — RoomMemory write side: two scopes, supersede flip, forget by source, forget-tool isolation

- instruction: continuity.`_build_record` already accepts supersedes/lifecycle/scope (continuity.py:690-734); RoomMemory.`_build` does not emit them (memory.py:1549-1572). The files backend drops same-hash records on a new id (`data_refinery` files.py:53-58): detect it before the write. Keep every write behind the existing executor and `MAX_INFLIGHT` gate. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t1
- covers: c5, h12, c7, h14, c9, h15, c39, h34, h36
- acceptance:
  - owns embodiment/memory.py write-side methods and tests/`test_memory_ingest.py` only
  - recall reads the gwen scope and the ingest scope inside one deadline, and no other scope
  - `ingest_records` writes one record per lock hold, and a record carrying supersedes flips the superseded record's lifecycle before returning; a recall after it never returns the superseded record
  - `forget_source`(source, `source_id`) archives every ingest record with that metadata.`recording_id` or issue id by enumerating the scope, and no other; `forget_ids` archives exact ids
  - a record whose text hash collides with an existing record is reported as collapsed, never silently dropped; the forget tool's id path cannot archive an ingest-scope record

### t6 — CLI memory noun: ingest, ingest --status, forget, overview

- instruction: Follow the nested-noun precedent in cli/`_commands`/cli.py:34-54 (`parser_class`=type(p)); add memory overview because the rubric's `overview_cli_noun_exists` check needs it. The verbs only enqueue; they never touch the store. Report daemon liveness from lifecycle.status() separately from the enqueue result. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t1, t2
- covers: c17, h20, c14, h18
- acceptance:
  - owns embodiment/cli/`_commands`/memory.py, its registration in embodiment/cli/`__init__.py`, the memory entries in embodiment/explain/catalog.py, overview.py's verb list and tests/`test_cli_memory.py` only
  - memory ingest --file is a dry run unless --apply, takes --json and --state-dir, and exits 0 / 1 with error + hint on refusal; forget takes --source + --source-id or --id
  - the verbs succeed under env {PATH, LANG}, stdin closed, a temp cwd, an absolute --file and --state-dir
  - teken cli doctor . --strict and tests/`test_cli_introspection.py` pass

### t7 — Guard: Access JWT replaces the secret on the public Host, with CSRF and 503

- instruction: Today `_check` runs Host, Origin, secret, then the assertion (guard.py:541-551) and `_check_assertion` hard-codes 401. Add a status to the refusal path for unavailable. Extend the cookie CSRF rule (guard.py:577-611) to the assertion path: Cloudflare adds the assertion to cross-site requests too. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t3
- covers: c26, h26, c47, h39, c20, h8, c46, h7
- acceptance:
  - owns embodiment/http/guard.py and tests/`test_http_guard.py` only
  - with the Access env unset, every existing guard test passes unchanged and the public Host is refused as today
  - on the public Host a valid assertion admits a request without the install secret; loopback and LAN still require the secret
  - an Access-admitted request without an allow-listed Origin or Sec-Fetch-Site: same-origin is refused; an unavailable JWKS answers 503 with its own code

### t8 — Dashboard chat view with cited sources

- instruction: Contract: POST /api/chat {message} -> {reply, sources:\[{source, title, date, start, end, section, link, text}\]}; follow-ups arrive as SSE kind reply with `turn_serial`. Reuse Transcript.tsx's dir=auto pattern (Transcript.tsx:28). React 18 only; no new npm dependency. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- covers: c21, h23, c64, h52
- acceptance:
  - owns web/src/components/Chat.tsx, Chat.test.tsx, web/src/api/chat.ts, chat.test.ts and the Chat mount in web/src/App.tsx only
  - a message posts to /api/chat and the reply and every source render as text nodes with dir=auto; a static test forbids innerHTML, dangerouslySetInnerHTML and insertAdjacentHTML under web/src
  - a source shows its provenance (recording title, date, mm:ss; or issue date, section, link as plain text) and is fenced as untrusted
  - a later memory follow-up for a typed turn arrives on the event stream and shows as a second message; on the public Host the page does not ask for the install secret

### t9 — Render provenance through the single recall funnel

- instruction: Every new field goes through `_label`/`_neutralise` with a charset or is quoted as body text (memory.py:2021-2134); extend `HEADER_PATTERN` if a header changes. The fuzz class is TestTheFenceHoldsUnderFuzzing (tests/`test_memory.py`:1233). No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t5
- covers: c10, h16, c34, h31, c11, h17
- acceptance:
  - owns `render_recalled` and its helpers in embodiment/memory.py and tests/`test_memory.py` only
  - an ingest record renders its provenance (title, date, mm:ss; issue date, section, link) as neutralised quoted data inside the fence
  - the fuzz generator produces hostile values for every new rendered field and `test_no_generated_record_escapes_the_fence` passes; the one-sanitiser AST test passes
  - a scripted turn whose recalled record says to call remember or forget executes no tool call

### t10 — HTTP routes: chat, speak, clip upload, and SSE close at token expiry

- instruction: Add Controls callables beside `start_voice`/`stop_voice`/`set_mute`/status (server.py:354-371) and routes in `_CONTROL_ROUTES` (server.py:275); controls run on the bounded pool via `call_control` (server.py:1130) - give chat its own longer bound. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t7
- covers: c18, h21, c49, h41
- acceptance:
  - owns embodiment/http/server.py and tests/`test_http_server.py` only
  - POST /api/chat, /api/speak and /api/clip are guarded control routes with bounded bodies; a clip over its byte cap is 413 before any callable runs
  - a slow callable answers 503 with a reason inside its own bound, never a hung request
  - an event stream admitted by an assertion closes within one heartbeat after the token's exp

### t11 — The drain: daemon-side single writer for the spool, with newspaper retention

- instruction: Callable from DaemonApp.pump (app.py:1404-1418) with a time budget; never block the 0.5 s heartbeat. Per-record failures use new app-ingest-\* codes through the daemon's `_record` path (app.py:1268-1302), passed in as a callback. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t2, t5
- covers: c3, h10, c15, h19, c40, h35
- acceptance:
  - owns embodiment/daemon/drain.py and tests/`test_daemon_drain.py` only
  - `drain_once`(budget) applies whole batches in enqueue order, one record write per lock hold, and moves each to done/ or failed/ exactly once; killing it between records and re-running completes the batch idempotently
  - applying the same batch twice leaves the scope file identical apart from store-owned timestamps
  - a newspaper item is recallable 29 days after its issue date and archived by 31, with an injected clock

### t12 — Semantic recall over cached vectors, with a threshold, backfill and keyword rollback

- instruction: Today `_work` calls `_probe`() before every semantic recall (memory.py:1799,1822-1838) and eidetic re-embeds every candidate (eidetic scoring.py:354-366): score cached vectors in embodiment instead of eidetic's approximate mode. Measured: probe ~170 ms + batch 291 ms = 469 ms at N=24. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t9, t4
- covers: c52, h43, c53, h44, c54, h45, c29, h27
- acceptance:
  - owns the recall-mode code in embodiment/memory.py and tests/`test_memory.py` recall cases only
  - a semantic recall with every candidate cached makes exactly one embed request and no probe
  - a query below the similarity threshold returns zero records; the threshold is a config value
  - a bounded background backfill embeds every uncached record at start and the recall path never embeds a candidate
  - recall mode keyword restores today's behaviour on every existing memory test; an unreachable embedder demotes to keyword with a recorded degradation and status() names the embedder role

### t13 — Background recall: arrival events, remainder regeneration, follow-up, restart and the unprompted-speech seam

- instruction: Pure orchestration with injected seams (recall, complete, speak, `stop_playback`, `floor_held`, clock) so it is fully testable without a daemon; app.py wiring is t16. Generation is one non-streaming completion (app.py:960-1010) and voice.py speaks sentence by sentence. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- covers: c59, h47, c60, h48, c61, h49, c62, h50, c63, h51
- acceptance:
  - owns embodiment/daemon/`recall_events.py` and tests/`test_recall_events.py` only
  - a recall that lands after sentence 1 of 3 makes sentences 2..n come from a second completion that saw the memory; the first completion's unspoken sentences are dropped
  - a recall landing after the reply produces at most one follow-up, only above the threshold, only while nobody holds the floor and inside its window; otherwise it is dropped and counted
  - a restart is recorded with a safe reason and uses the `stop_playback` path; every arrival increments exactly one of before-first-sentence, during-reply, follow-up, dropped
  - the follow-up seam has one entry point a non-recall caller can use, and the never-silent rule does not fire on a dropped follow-up

### t14 — Text turns, speak-a-message and clip transcription handlers

- instruction: Injected seams: `run_text_turn`, tts, stt. The typed turn joins the current session and transcript log (operator decision c58) - the daemon passes its session in. Raw audio never touches disk (CLAUDE.md privacy rule). No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- covers: c67, h53, c68, h54
- acceptance:
  - owns embodiment/daemon/`text_turns.py` and tests/`test_text_turns.py` only
  - a typed turn returns its reply as text and makes no call to the host endpoint's play()
  - `speak_message` returns synthesised audio bytes from the tts lane for the caller and writes nothing to disk
  - a clip is transcribed in memory through /v1/audio/transcriptions and run as a typed turn; over its byte or duration cap it is refused before transcription; no audio byte reaches the state dir

### t15 — Dashboard audio controls: speak button, record/upload, speech-to-speech placeholder

- instruction: MediaRecorder output differs by browser (webm/opus vs mp4/aac); send it as recorded and surface the server's refusal - transcoding is risk r2. No new npm dependency. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t8
- covers: c69, h55
- acceptance:
  - owns web/src/components/AudioControls.tsx, AudioControls.test.tsx, web/src/api/audio.ts, audio.test.ts and the AudioControls mount inside Chat.tsx only
  - each Gwen message has a speak button that plays the returned audio in the browser
  - the page records a clip or uploads a file and posts it to /api/clip; the reply arrives as a chat message
  - the speech-to-speech button is disabled with its reason shown while no such role is advertised

### t16 — Daemon integration: wire drain, background recall, text turns and Access into the app

- instruction: Replace the synchronous `_recall` in `_handle_turn` (app.py:~2203, 2630-2660) with `recall_events`; Guard is built in main() at app.py:~4047 with no `assertion_verifier` today. Keep the d9 close ordering (summary first). No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t6, t10, t11, t12, t13, t14
- covers: c44, h5
- acceptance:
  - owns embodiment/daemon/app.py and tests/`test_daemon_app.py` only
  - pump() drains within a budget, close(deadline) has a drain share before memory, and status() reports the inbox, the recall-arrival counters, the embedder in effect and the Access mode
  - the voice turn no longer waits on recall; text, speak and clip controls are bound; main() reads the Access env and email allow-list and injects the verifier
  - the full suite passes: uv run pytest -n auto

### t17 — Release docs, tunnel default, version bump

- instruction: Bump with the version-bump skill (run it alone with </dev/null). Say what is still unmeasured; no value claim. Nothing in this task runs cultureflare, cloudflared or a sibling-repo post. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t16, t15, t6
- covers: c22, h24, c32, h29, c70, h56, c24, h37, c41, h2, c28, h38
- acceptance:
  - owns README.md, CLAUDE.md, CHANGELOG.md, pyproject.toml version, the explain root in embodiment/explain/catalog.py, embodiment/cli/`_commands`/tunnel.py, tests/`test_release_docs.py` and tests/`test_cli_tunnel.py` only
  - the not-yet list drops Cloudflare Access and semantic recall, narrows phone voice to live realtime voice and speech-to-speech, and tests/`test_release_docs.py` passes
  - the tunnel verb defaults to gwen.culture.dev, prints the Access env names, and runs nothing
  - the CLAUDE.md rig table and code map match the shipped state; markdownlint passes

### t18 — Measure recall: keyword vs gemma2-embed, threshold, volume ceiling, arrival distribution

- instruction: Start from the scratchpad recall94/ scripts (`gen_queries.py`, `measure_recall.py`); run on a copy of the store with reinforce=False. Needs a ready chat role for question generation (risk r1). No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t16
- covers: c45, h6, c25, h25, c30, h28, c43, h4
- acceptance:
  - owns scripts/measure-recall.py and `docs/live-test-results/<date>-recall-gemma2.md` only
  - publishes hit@5 and hit@1 for close and reworded queries, latency median/p90, the calibrated threshold's false-positive rate, and the recall-arrival distribution, each with its n, whatever the numbers say
  - states the ceiling of ingested records the derived deadline carries, measured over stores of at least 3 sizes
  - only aggregates are committed: no record text, query text or labelled set from Gwen's real store enters the repo

### t19 — Synthetic end-to-end acceptance

- instruction: Drive the daemon with fake gateway seams (no network) and a real files backend in a tmp dir. No new dependency (tests/`test_zero_deps.py` must pass unchanged). Synthetic data only - the repo is public; never copy a record from Gwen's real store. Degrade, never raise; every degradation is recorded (C3).
- depends on: t16
- covers: c1, h1, c42, h3, c55, h46
- acceptance:
  - owns tests/`test_acceptance_ingest.py` only
  - a synthetic pocket batch and a synthetic newspaper batch enqueued through the CLI are applied by a running daemon within 2 drain intervals and recalled and cited in a text turn
  - 100 interleaved ingest and remember writes lose 0 records
  - after ingest then forget of a recording, no file under the state dir except the archived store record contains its sentinel text or vector
  - every announcement clause maps to a passing test named in the test module's docstring

## Risks

- [unknown_nonblocking] senses (the speaker) is not served on this rig (frame park v4, served from thor once deployed); t18's question generation and any live check wait until a chat role reports ready (task t18)
- [unknown_nonblocking] the stt lane's accepted formats are unverified against browser recordings (webm/opus, mp4/aac); a transcode step would need a dependency approval (tests/`test_zero_deps.py`) (task t14)
- [unknown_nonblocking] per-turn embeds add load on orin next to pocketsync's own embeds; unmeasured (task t18)
- [unknown_nonblocking] the second completion for an unspoken remainder adds latency nobody has measured; the arrival counters and t18 publish it (task t13)
- [follow_up] sibling work, proposed only through the communicate skill with the operator's go-ahead: pocketsync's landed/deleted/merged events and its refused `prompt_name` 'Query'; culture-rules' distil step, newspaper rule and file-writing action
- [unknown_nonblocking] memory.py is touched by t5, t9 and t12 in sequence; the chain is deliberate (one file) and lengthens the critical path (task t12)
