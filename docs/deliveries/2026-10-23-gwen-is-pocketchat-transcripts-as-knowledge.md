# Delivery Summary — Gwen is pocketchat, transcripts as knowledge

plan: `gwen-is-pocketchat-transcripts-as-knowledge` · run: `partial` · date: `2026-10-23`
baseline: `devague summary skeleton`

## Intent

> Gwen's page is pocketchat: you open gwen.culture.dev and get a chat-first library of your recordings — ask, read, cite, jump to the moment — with Gwen's voice as one button inside it, and she answers from the transcripts themselves, not only from distilled facts

After: Opening gwen.culture.dev shows pocketchat's experience as Gwen's page: an Ask view with a pinned composer (mic, speak, record/upload and the speech-to-speech button inside it), a Library view of the library's items (culture-library; xteink's instance today) grouped by day with kind chips, and a side reader; Gwen answers typed questions from the library with \[n\] citations that open the reader at the cited passage, alongside her own remembered facts

The run executed the 15-task plan through `/assign-to-workforce`, then took twelve
operator-directed deviations (d1–d12) found live or asked for during review. It
shipped as one squash-merged PR, #98 (`83e0d60`, 0.18.0), 63 commits from `2e3111e`
to `d25e64a`. The run is **partial** only because t15's public-page checks by the
operator were never made; every other task merged.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Library client: GET-only HTTP client for the library (catalog, item, file)
- `t2` — EPUB extraction: bounded stdlib text + passage anchors
- `t3` — Document index: 0600 chunk store with gemma2-embed vectors, reconcile and search
- `t4` — Scrub keys from audio subprocess environments
- `t5` — Access admit/refuse counters and the stale --public-hostname help
- `t6` — HTTP read routes for the library and to-dos
- `t7` — Ask view: pocketchat's thread and composer, ported and cited
- `t8` — Library view and side reader, ported and cited
- `t9` — To-dos view from Gwen's remembered action items
- `t10` — Library sync: bounded background ticks that keep the index in step with the catalog
- `t11` — Library tools: `search_library` and `read_passage`, fenced as untrusted, returning citations
- `t12` — Chat-first app shell: pocketchat layout, views, voice as a feature
- `t13` — Daemon integration: library wired, typed-turn tools, write guard, citations, status
- `t14` — Release docs: three-plus tools, the library, the new page
- `t15` — Live acceptance: library measurement and the public-page checks

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `embodiment/library/client.py`: GET-only by construction (AST pin), a `gets` counter, deadline, redirects refused, key from the env only |
| `t2` | delivered | `embodiment/library/epub.py`: stdlib extraction in spine order with stable anchors, bounded by members, member size, total size and characters |
| `t3` | delivered | `embodiment/library/index.py`: 0600 index reconciled by (id, sha256); amended to version 3 by `d3` |
| `t4` | delivered | audio subprocesses spawn with a scrubbed env (`audio/host.py`) |
| `t5` | delivered | `status.access` admitted/refused counters; the `--public-hostname` help corrected |
| `t6` | delivered | `/api/library`, `/api/library/{id}`, `/api/library/search`, `/api/todos`, guarded GETs on the text pool |
| `t7` | delivered, then redesigned | the Ask thread and composer; redesigned by `d2` (voice dock, panes) and extended by `d4`/`d5`/`d6` |
| `t8` | delivered, then redesigned | Library view and side reader; redesigned by `d2` (library home, reader in the right pane) |
| `t9` | delivered | To-dos view from memory's `action_item` records, grouped by recording, read-only |
| `t10` | delivered | `embodiment/library/sync.py`: bounded ticks that yield to live turns; 5 s catch-up added by `d3` |
| `t11` | delivered | `search_library` and `read_passage` (`embodiment/library/tools.py`), fenced through `render_recalled`, citing per turn |
| `t12` | delivered, then redesigned | chat-first shell; redesigned by `d2`, history pane by `d4`, Live opt-in by `d6` |
| `t13` | delivered | library lane, typed-turn tools, write guard (c31), citations, status; extended by `d1`, `d5`, `d7`–`d12` |
| `t14` | delivered | README, CLAUDE.md, CHANGELOG 0.18.0 and the `explain` catalog; pinned by `tests/test_release_docs.py` |
| `t15` | partial | the library measurement is published (`docs/live-test-results/2026-10-24-library.md`); the operator's public-page checks (>= 5 questions on gwen.culture.dev) were never made |

## Mid-work Decisions

Each approved deviation, quoted from its record (`devague deviate --list`); all twelve
are approved, and each has an approved delta record (`b1`–`b12`):

- `d1` — one system-prompt line on typed turns only when a library is configured, a sharper `search_library` description, and status counters of library tool calls — measured live 2026-10-24: 0/6 typed turns used the library; direct senses probe 2/2 tool calls
- `d2` — the page redesigned chat-first inside culture-library's look: toggleable panes, a voice dock (mic in the box; Live and Upload beside it), drawers on a phone, a Library home, a Stats view; the disabled speech-to-speech button replaced by Live; citation groups split and markers stripped from speech — operator: the options artifact was not good; wanted the library and pocketchat experience, closer to option C
- `d3` — document index version 3 (content and title-context vectors, better of the two; fragments under 60 chars not searched), the next typed turn told the previous answer's sources by page number, a 5 s catch-up tick, and a reader retry — live: a question naming today's paper got five mastheads; 'Item 1' was not understood; a re-index took about 15 minutes
- `d4` — conversation history: `GET /api/conversations` and `/api/conversations/{id}` (guarded, read-only, bounded) and a left history pane; library and sources moved to a right pane with two tabs — operator: "What about chat history? It should be on the left pane, and library and resources on the right."
- `d5` — an earlier conversation can be continued in place (its turns are the window; new turns go to its log), and a read-only `embodiment history export --json` verb — operator: "I need to be able to continue them, not just read them"
- `d6` — live voice is off by default; the Live button opts in and out; `start --listen` keeps the hot mic on start — operator: "live conversation is off by default and I can click the button to opt in"
- `d7` — every turn tagged with how it arrived (`via`) and what it is worth as evidence; a conversation is announced once quiet for 30 minutes — operator: "we tag each content and its value … A recording is truth (that it happened, not its content)"
- `d8` — conversations are Gwen's own: indexed in her private conversation index and searched with `search_conversations`; nothing goes through culture-rules — operator: "culture rules intended for between processes, and this internal to embodiment/gwen"
- `d9` — transcripts, replies and audio levels still reach the MQTT broker; only the `conversation` event stays in-process; marking content crossing processes is asked upstream instead — operator: "I don't have privacy problem because it's all internal. But we need a way to mark content"
- `d10` — every page clip is kept as it arrived, before transcription, in a private, capped, refuse-never-delete store, linked from its turn — operator: "we also save the recording as well for now. Open issue for archive to s3"
- `d11` — every room-mic utterance is kept like a sent recording, cut by the wire's stream time — operator: "room microphone - confirmed. We keep that as well as if sent as recording."
- `d12` — typed timestamps on every turn and recording (created, recorded, witnessed, received, transcribed, generated; the page's times `by: client`) — operator: "everything must be timestamp, and timestamp needs type"

Decisions no deviation record covers:

- Bus envelope ids gained a per-run token: they were source plus `seq`, which restarts every run, and culture-rules keeps one envelope per id, so a new run's events could be dropped as duplicates. Found live while checking d7 (the first ten `conversation` events were never stored).
- The previous cycle's deviation record (PR #95's d6) was committed in this branch by a `git commit -a`; kept and noted in the PR.
- A placeholder-skip rule for empty transcripts was reverted after it broke 34 tests; the cause was raised upstream as culture-rules#88 instead.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t15` (`d1`) | measured live 2026-10-24: 0/6 typed turns used the library; direct senses probe 2/2 tool calls | needs-follow-up |
| `t12` (`d2`) | operator, 2026-10-24: the options artifact was not good; wanted the library and pocketchat experience, closer to option C, within the library; toggleable panes; speech and realtime speech buttons; mobile and wide | acceptable |
| `t11` (`d3`) | live 2026-10-24: a question naming today's paper got five mastheads and no article; 'Item 1' was not understood; a re-index took about 15 minutes. Version 2 (title vector only) measured worse and was replaced by version 3, which matches version 1 on both t15 sets | needs-follow-up |
| `t12` (`d4`) | operator, 2026-10-24: 'What about chat history? It should be on the left pane, and library and resources on the right.' | acceptable |
| `t13` (`d5`) | operator, 2026-10-24: 'past conversations - I need to be able to continue them, not just read them. and they should be indexed via culture-rules to library'; chose per-message typed continuation and an embodiment export that culture-rules pulls | acceptable |
| `t12` (`d6`) | Operator, 2026-10-24: 'live conversation is off by default and I can click the button to opt in and start it. (Or close and turn it off and reenable later)' | acceptable |
| `t13` (`d7`) | Operator, 2026-10-24: accepted export-when-quiet over a schedule; 'we tag each content and its value … A recording is truth (that it happened, not its content). A transcript is less so, as AI was doing the translation' | acceptable |
| `t13` (`d8`) | Operator, 2026-10-24: 'It's not right to work via culture rules - culture rules intended for between processes, and this internal to embodiment/gwen' | acceptable |
| `t13` (`d9`) | Operator, 2026-10-24: 'I don't have privacy problem because it's all internal. But we need a way to mark content on culture-rules (private, public, human input, code input, AI input, etc.)' | needs-follow-up |
| `t13` (`d10`) | Operator, 2026-10-24: 'we also save the recording as well for now. Open issue for archive to s3' | needs-follow-up |
| `t13` (`d11`) | Operator, 2026-10-24: 'room microphone - confirmed. We keep that as well as if sent as recording.' | needs-follow-up |
| `t13` (`d12`) | Operator, 2026-10-24: 'everything must be timestamp, and timestamp needs type (Creation, record, witness, etc.)' | acceptable |
| `t15` | the operator's public-page checks (>= 5 questions, which passage each citation opened, each announcement element observed) were never made; no deviation covers it | needs-follow-up |
| `t5`, `t6` | no plan drift: delivered as planned | acceptable |

`d5`'s second half (culture-rules exporting conversations into the library) was
superseded by `d7` and then `d8` before anything was posted; nothing was built for it
beyond the read-only `history export` verb.

## Evidence

- tests (validation run): 57 named tests on `main` at `83e0d60`, all pass, filed as
  `e1`–`e48` against obligations `o1`–`o19` (run by scratchpad `validate98/run.py`;
  the repo has no behavioral marker or folder, so each record names its test)
- documents: `docs/live-test-results/2026-10-24-library.md`, filed as `e49` (c28,
  coverage) and `e50` (c29, coverage)
- full suite on the PR head `d25e64a`: 4245 passed, 1 skipped (Python); 464 passed (web)
- CI on PR #98: `test` (two runs), `lint`, `version-check`, SonarCloud (Quality Gate
  OK, 0 open issues, 0 hotspots), GitGuardian — all pass
- lint: black, isort, flake8, bandit, markdownlint, `teken cli doctor . --strict` —
  clean; complexity: nothing new above 15 (`context.py:_build_droppable_segments` is 18,
  the same on `main` before this run)
- live, on gwen.culture.dev's scratch daemon: today's paper answered from 10-24
  articles; "Item 1" answered from source 1 (3 of 3); a past conversation continued
  (its log grew 16 to 18 turns); 10 conversations indexed by d8 and a question about
  the tomatoes answered from that conversation, cited; typed turns logged with
  `received` / `generated` timestamps
- commits: `2e3111e..d25e64a`, squash-merged as `83e0d60`
- PRs / issues: #98 (merged), #99 (S3 archive), culture-rules#88, culture-rules#90,
  events-cli#15

## Delivery Claims

Confidence is capped by an approved lapse where one touches the claim.

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `c4` — Hebrew answers and sources lay out by their own direction | high | `e1`, `e2`: `web:src/components/Chat.test.tsx > posts a message; reply and sources render as dir=auto …`, `web:src/App.test.tsx > renders a Hebrew transcript event with dir="auto" …` |
| `c5` — realtime voice is a feature of the Ask view, beside the composer (Live opt-in, d6) | high | `web:src/components/AudioControls.test.tsx > puts the Live slot and Upload beside the input box …`; `tests/test_daemon_app.py::TestLiveIsOptIn::test_live_on_off_and_on_again` |
| `c9` — the page is chat-first | medium (`l8`: layout proved by a CSS pin under jsdom, not a measured layout) | `web:src/App.test.tsx > pins the chat-first order …`; the page was reviewed by eye on iPhone 13 mini and wide sizes, not recorded as evidence |
| `c10` — library routes are guarded GETs | medium (`l4`: t6 tests never seen failing first) | `tests/test_http_server.py::TestLibraryRoutes::test_each_route_without_a_secret_on_loopback_is_401`, `…_with_access_on_the_public_host_is_200`, `…::test_post_is_405` |
| `c11` — the library reaches Gwen as read tools on typed turns | medium (`l7`: t11 tests seen failing only at collection) | `tests/test_daemon_library.py::TestTypedTurnsOnly::test_a_clip_turn_sees_the_library_tools`; live: with d1, Gwen used the library in 3 of 6 typed turns (not 6 of 6), every citation resolving (15 of 15) |
| `c13` — Gwen extracts EPUB text herself with the stdlib | medium (`l5`, `l11`: tests never run red; the real extractor was not exercised through the sync until live) | `tests/test_library_epub.py::test_spine_order_and_shape`, `::test_anchors_stable_and_unique` |
| `c15` — the library client is GET-only by construction | high | `tests/test_library_client.py::test_module_is_get_only`, `::test_ast_guard_fails_when_a_write_method_is_planted` |
| `c26` — Gwen never writes to the library; her conversations live in her own index | high | `tests/test_daemon_library.py::TestPastConversations::test_a_settled_conversation_is_indexed_and_found_but_never_the_live_one`; `tests/test_library_conversations.py::test_the_conversation_the_turn_is_in_is_never_searched` |
| `c27` — a citation's click opens the reader at the cited passage | medium (`l3`: t8 tests never seen failing first) | `web:src/App.test.tsx > loads, chats and opens the reader at the cited passage …`; `web:src/api/citations.test.ts > splits a merged group into one cite per source` |
| `c27` — 100% of library answers on gwen.culture.dev carry a working citation, checked live on >= 5 questions | unverified | the operator's public-page check was never made; t15 measured 0 of 6 replies with an inline `[n]` (citations showed in the sources list only) |
| `c28` — 0 non-GET requests: pinned by the AST check | high | `tests/test_library_client.py::test_every_request_is_counted_as_a_get` |
| `c28` — 0 non-GET requests: confirmed in a live run's request log | low | `e49`: the live counter counts all requests (18), not non-GET ones; the zero rests on the AST pin |
| `c29` — a retrieval measurement over >= 20 questions is published | high | `docs/live-test-results/2026-10-24-library.md` (32 and 28 questions, hit@5 19/32 and 17/28, p90 0.21 s, one rig, threshold uncalibrated for library text) |
| `c31` — library text cannot trigger remember/forget | high | `tests/test_daemon_library.py::TestTheWriteGuard::test_a_planted_instruction_in_a_full_typed_turn_writes_nothing`, `::test_the_operators_own_ask_still_writes` |
| `c32` — keys never reach child processes | high | `tests/test_audio_host.py::test_every_spawn_gets_an_env_without_keys_but_with_the_runtime_dir`, `::test_child_env_drops_by_suffix_and_keeps_the_rest` |
| `c33` — the index reconciles by (id, sha256) | medium (`l2`: tests seen failing only at collection) | `tests/test_library_index.py::test_reconcile_reports_added_changed_removed` and three more (`e20`–`e23`) |
| `c34` — EPUB extraction is bounded | medium (`l5`) | `tests/test_library_epub.py::test_too_many_members`, `::test_oversized_member`, `::test_zip_bomb_total`, `::test_char_cap` |
| `c35` — the library is optional and observable | high | `tests/test_daemon_library.py::TestUnsetIsToday::test_no_lane_binds_no_library_tool_and_routes_are_unbound`, `TestStatusAndRoutes::test_a_down_library_is_recorded_and_chat_goes_on` |
| `c36` — status.access counts admitted and refused requests | high | `tests/test_http_guard.py::TestAccessCounters::test_an_admitted_public_host_request_increments_admitted`; `tests/test_http_server.py::test_server_status_carries_the_guards_access_counters` |
| `c37` — background indexing yields to a live turn | medium (`l6`, `l11`) | `tests/test_daemon_library.py::TestTheSyncThread::test_a_chunk_embed_waits_for_the_live_turn`; `tests/test_library_sync.py::test_gate_held_means_no_embed_until_released_and_recall_first` |
| `c39` — library tools on typed turns only | high | `tests/test_daemon_library.py::TestTypedTurnsOnly::test_a_voice_turn_never_sees_a_library_tool_and_a_typed_turn_does` |
| `c40` — To-dos from memory's action items, read-only | medium (`l1`: t9 tests never seen failing first) | `tests/test_daemon_library.py::TestStatusAndRoutes::test_todos_group_action_items_by_recording`, `::test_todos_from_a_real_store` |
| `d8` — Gwen searches her own settled conversations | high | `b8`; `tests/test_daemon_library.py::TestPastConversations::test_the_tool_and_its_line_come_with_the_lane_only`; live: the tomato question answered correctly from that conversation |
| `d10` — every page clip is kept before transcription | high | `b10`; `tests/test_text_turns.py::TestClip::test_the_clip_is_kept_before_stt_and_linked_from_its_turn` |
| `d11` — every room-mic utterance is kept | medium (tested with synthetic wire events and PCM only; not yet exercised with real speech on the rig) | `b11`; `tests/test_daemon_app.py::TestTextTurns::test_room_speech_is_kept_like_a_recording_and_linked_with_its_times` |
| `d12` — every turn and recording carries typed timestamps | high | `b12`; `tests/test_daemon_app.py::TestTextTurns::test_every_turn_carries_typed_timestamps`; live typed turns logged `received` / `generated` |
| the value of the cycle to the operator | unverified | not measured; accepted on one rig |

Lapse ledger evidence (all approved by the operator, 2026-10-24):

| Lapse | Code | What |
|-------|------|------|
| `l1` | `grader-unverified` | t9 (To-dos view) tests were written in the same step as the implementation and never seen failing first; the acceptance tests pass but were not proven able to fail |
| `l2` | `grader-unverified` | t3 (document index) tests were only seen failing at collection (module absent), never individually against a broken rule; no mutation check that each test can fail |
| `l3` | `grader-unverified` | t8 (Library view + reader) tests were run only after the implementation was written in the same step; never seen failing first |
| `l4` | `grader-unverified` | t6 (library/to-dos routes) implementation was written before its tests; tests never seen failing first (they did catch one real defect, %FF -> 500) |
| `l5` | `grader-unverified` | t2 (EPUB extraction) tests written first but never run red; first run already green |
| `l6` | `grader-unverified` | t10 (library sync) tests only seen failing at collection (module absent), not per test |
| `l7` | `grader-unverified` | t11 (library tools) tests seen failing only at collection (module absent), not per test |
| `l8` | `assumption-for-measurement` | t12 (app shell) proves 'the waveform is not the largest element' (h4) by a CSS height pin under jsdom, not by measured layout; no real-browser check |
| `l9` | `grader-unverified` | t13: two tests (gate held while the speaker plays; unusable URL builds no lane) were written after the implementation and never seen red |
| `l10` | `assumption-for-measurement` | t13: c38 newspaper dedupe identifies an issue by kind 'newspaper' or a title containing newspaper/עיתון and takes the date from the title, unverified against the live catalog |
| `l11` | `control-absent` | Wave tasks t2/t10/t13 tested the extract seam only with fakes; the daemon's real default extractor was never called as the sync calls it, so a TypeError on every item reached the live run |
| `l12` | `assumption-for-measurement` | Put document index version 2 live and reported the search fix as working from one question, before re-running the t15 question sets; the re-measurement then showed hit@5 down (19 to 16 of 32, 17 to 11 of 28). Replaced by version 3, measured equal to version 1. |

## Remaining Work / Follow-up

- `t15` / `c27` — the operator's public-page check on gwen.culture.dev: >= 5 questions,
  recording which passage each citation opened and each announcement element observed —
  operator
- `d1` — Gwen used the library in 3 of 6 typed turns, and 0 of 6 replies carried an inline
  `[n]` at measurement time; re-measure on the current build — embodiment
- `c28` — no live counter of non-GET requests exists; the zero rests on the AST pin — accept,
  or add a counter if a live proof is wanted
- `d3` — library similarity threshold 0.715 is memory's, uncalibrated for library text
  (risk r6), and now also for conversations (d8) — calibrate on library and conversation
  questions
- `d9` — content marks across processes: events-cli#15 (envelope), then culture-rules#90;
  then embodiment marks its own envelopes with them — events-cli, culture-rules, embodiment
- `d10`/`d11` — S3 archive of the recordings (#99); the 2 GiB store cap is a judgement, not a
  measurement; room-mic recording not yet exercised with real speech — embodiment
- voice turns are not armed by the write guard from library-backed replies in their window —
  embodiment
- "today's newspaper" does not put today's date in the search query, so an older issue can
  rank first — embodiment
- an earlier conversation's citations show as plain numbers (the session logs keep text,
  not sources) — embodiment
- `l10` — the newspaper dedupe assumption is unverified against the live catalog — embodiment
- usage stats have no history (counters reset on restart); the phone keyboard behaviour is
  checked only at an emulated iPhone 13 mini size; the ask-colleague review was not run —
  embodiment
- transcripts carry no timing (0 timestamp tokens in the sampled EPUBs), so jump-to-moment
  cannot work for them — a culture-rules ask, not yet drafted
- the library's move to culture-library (library.culture.dev) — culture-library, then
  re-point `EMBODIMENT_LIBRARY_URL`
