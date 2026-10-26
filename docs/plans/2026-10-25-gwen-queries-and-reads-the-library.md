# Build Plan — Gwen queries and reads the library

slug: `gwen-queries-and-reads-the-library` · status: `exported` · from frame: `gwen-queries-and-reads-the-library`

> Gwen asks culture-library for passages instead of keeping her own copy of it, and reads any library document aloud, by page or by paragraph, remembering where she stopped so the operator can say continue and hear the rest

## Tasks

### t1 — library/retrieve.py: the one POST to /api/rag/retrieve, bounded, pinned, counted

- instruction: Write tests/`test_library_retrieve.py` first against a local http.server fake (see tests/`test_library_client.py` for the fake-server and AST-guard helpers; reuse its `_violations` idea but allow the one POST). Copy client.py's bounded-read and opener (no redirects) patterns; do not edit client.py. Probed live 2026-10-26: modes are only semantic|hybrid, `item_ids` filters, the response is {query, mode, reranked, passages:\[...\]} and hybrid scores are 0.01-0.03 (do not apply any floor here).
- covers: c13, h10, c7, h5, c6
- acceptance:
  - embodiment/library/retrieve.py exposes RetrieveClient(`base_url`, key, deadline).retrieve(query, k, kind=None, `item_ids`=None) -> list\[Passage(`item_id`, title, kind, `chunk_no`, `chunk_sha`, text, score)\] with mode hybrid and rerank false, reading `EMBODIMENT_LIBRARY_URL` / `EMBODIMENT_LIBRARY_KEY` like client.py
  - a new AST test pins that retrieve.py issues exactly one non-GET request, a POST to the literal path /api/rag/retrieve, and no other method, path or body; tests/`test_library_client.py`'s GET-only pin on client.py is unchanged and still passes
  - passages of kind todo or discord-message are dropped before they are returned (test with a fake server answering both)
  - a connection failure, a 503 `rag_unavailable`, any other non-200, an oversize or malformed body and a hang past the deadline each raise one LibraryError with a declared code (`library_unavailable`, `rag_unavailable`, `library_timeout`, `library_bad_response`); redirects are refused; nothing else escapes
  - counters: calls, `calls_ok`, `failures_by_code` and `last_latency_s`, readable via status() and moved by the tests above

### t2 — library/document.py: an item's text as sections and paragraphs, from GET /api/library/{id}/text

- instruction: Probed live 2026-10-26: EPUB /text has sections \[{heading, paragraphs:\[str\], links:\[{text, href, start}\]}\] (one book: 431 sections, 3943 paragraphs, median 68 chars, max 1217); Markdown transcripts return text with sections null. Tests in tests/`test_library_document.py` with synthetic fixtures only (public repo). Touch only client.py (one new GET method), document.py and their tests.
- covers: c9, h7
- acceptance:
  - LibraryClient gains `fetch_text`(`item_id`) (a GET, so client.py's GET-only pin still passes) returning the parsed JSON {`item_id`, kind, format, title, text, sections, reason}
  - embodiment/library/document.py: `to_document`(meta, `text_json`) -> Document(`item_id`, sha256, title, kind, sections: \[Section(heading, paragraphs: \[str\], links: \[Link(text, href)\])\]); an EPUB's sections and paragraphs are kept in order and count exactly as /text gives them, empty paragraphs skipped
  - a Markdown item (sections null) is split by embodiment/library/markdown.py into paragraphs, a heading starting a new section, text before the first heading in a section with an empty heading; each paragraph equals one passage of markdown.extract over the same text
  - paragraph text is returned byte for byte (no cleaning beyond what markdown.extract already does for Markdown); a test pins one EPUB fixture and one Markdown fixture (synthetic) end to end

### t3 — daemon/reading.py: reading positions (0600) and the next/heard state machine

- instruction: Pure module: the document comes from an injected provider (`item_id` -> Document from t2's shape, or raises gone/unavailable), the clock and the state dir are injected. Follow embodiment/daemon/todos.py (TodoStates) for the file discipline. Route contract shared by t3, t4 and t7 (JSON): GET /api/reading lists saved positions \[{`item_id`, sha256, section, paragraph, part, `updated_at`, gone}\]; GET /api/reading/{`item_id`}/next?section=&paragraph= (both optional: absent = continue at the cursor) answers {`item_id`, sha256, title, section, `section_count`, heading, paragraph, `paragraph_count`, part, `part_count`, text, position} where position is an opaque string naming exactly that unit, or {done: true} past the end; POST /api/reading/{`item_id`}/heard {position} answers {moved: bool, cursor: {...}}; POST /api/reading/{`item_id`}/clear drops the cursor. Errors: 404 library-item-not-found, 410 library-item-gone, 503 `library_unavailable`. This task implements the logic behind those routes, not the routes. Tests in tests/`test_reading.py`.
- covers: c11, h9, c23, h16, c10, h8, c31, h24
- acceptance:
  - ReadingPositions keeps one cursor per item (`item_id`, sha256, section, paragraph, part, `updated_at`) in `<state>/reading-positions.json`, 0600, atomic replace under one lock, lazy load, an unreadable file renamed aside with .unreadable and counted, never raises; survives a reload from disk (restart)
  - Reader.next(`item_id`, section=None, paragraph=None) returns the unit at the cursor (or the one asked for) with a position token; Reader.heard(`item_id`, position) moves the cursor past that unit only when position is exactly the cursor's next unit, else returns moved False and counts a refused report; the cursor never runs past the end (done)
  - a paragraph over 4000 characters is served as several parts split at sentence boundaries (none over 4000), each part its own position; the text of every unit is the source text verbatim (concatenated parts equal the paragraph)
  - an item whose sha256 changed resumes at the start of the saved section, or the item's start if that section is gone; an item no longer in the catalog keeps its cursor, is listed with gone true, and next answers gone
  - two interleaved readers in a test (A at paragraph 3, B at paragraph 7) leave the cursor at the furthest exact next position, never moved backwards, file never torn
  - counters: `reads_started`, `units_served`, `units_heard`, `heard_refused`, continues, `last_item`, `last_position`

### t4 — web: read aloud on this device (reader listen mode, dock, paragraph-synced progress, continue)

- instruction: Match the approved mockup (scratchpad mockup107/index.html, screenshots sent 2026-10-26): page tokens only, highlight with --mark, edge line and segments in --cobalt, the dock sticky at the reader's bottom, 'Playing on this phone/laptop'. Route contract shared by t3, t4 and t7 (JSON): GET /api/reading lists saved positions \[{`item_id`, sha256, section, paragraph, part, `updated_at`, gone}\]; GET /api/reading/{`item_id`}/next?section=&paragraph= (both optional: absent = continue at the cursor) answers {`item_id`, sha256, title, section, `section_count`, heading, paragraph, `paragraph_count`, part, `part_count`, text, position} where position is an opaque string naming exactly that unit, or {done: true} past the end; POST /api/reading/{`item_id`}/heard {position} answers {moved: bool, cursor: {...}}; POST /api/reading/{`item_id`}/clear drops the cursor. Errors: 404 library-item-not-found, 410 library-item-gone, 503 `library_unavailable`. Mock fetch and Audio in tests; do not touch the Python side.
- covers: c18, c23, h16
- acceptance:
  - web/src/api/reading.ts implements the route contract with the same auth headers as api/library.ts; web/src/components/ReadAloud.tsx (or a listen mode in Reader.tsx) plays each unit by POSTing its text to /api/speak and playing the WAV, reports heard only on the audio ended event for that unit's position, and prefetches the next unit while the current one plays
  - pause, an audio error, a reload, Stop reading or moving to another paragraph before ended never report heard (vitest with a fake Audio)
  - the dock has one segment per paragraph of the section; heard paragraphs full, the playing one filling with its audio's currentTime/duration, the same value driving the paragraph's edge line and the section's share of the map (tested on the computed style or a data attribute)
  - the library card shows Continue listening for an item with a saved position, the chat answer can carry a Read aloud card, and a gone item says it is no longer in the library; all text nodes dir=auto, keyboard reachable, reduced motion respected
  - npm run build, the existing web tests and check-no-external-origin pass

### t5 — tools: `search_library` on retrieve, `read_item` and `fetch_source` on /text

- instruction: Files: embodiment/library/tools.py, embodiment/library/reading.py and their tests (tests/`test_library_tools.py`, tests/`test_library_reading.py`). The tools take a retrieve client, a catalog callable and a document provider instead of an index; keep `build_`\* signatures readable for t6 to wire. Keep the AST pin that `render_recalled` is the only formatter.
- depends on: t1, t2
- covers: c5, h3, c26, h19, c27, h20
- acceptance:
  - `search_library` calls RetrieveClient.retrieve with k = limit and the kind when given, applies no similarity floor, renders through the existing `_render`/`render_recalled` fencing, and its fixed host text says these are the library's best passages for the query, not confirmed matches
  - a citation carries `item_id`, `chunk_no` and `chunk_sha` (CitationCollector dedupes on `item_id` + `chunk_sha`); `read_passage` takes `item_id` + `chunk_sha` and returns the passage text from the item's /text paragraphs that contain it, or the fixed not-found text
  - date: the day's items are listed from the catalog (`created_at`) and passed as `item_ids`; a day with no items makes no retrieve call and answers `NO_MATCHES` (test sees the exact ids)
  - `read_item`(`item_id`, offset=0) returns paragraphs from document.py from offset, capped as today (40 / 16000), with a fixed more-follows line naming the next offset; offset past the end answers the fixed end text; `fetch_source` allows only hrefs listed in the item's /text sections (a URL not listed is refused, test with a fake library)
  - a RetrieveClient LibraryError maps to the tool's fixed fault text and its code; nothing raises into the turn

### t6 — daemon: retire the library sync and index; catalog, search and the reader routes go live

- instruction: Files: embodiment/daemon/app.py, embodiment/http/server.py (only the library routes' backing), embodiment/library/index.py only if the library-only parts must go (DocumentIndex stays for conversations), delete sync.py, and tests/`test_daemon_library.py` / `test_http_server.py`. The todo source (TodoSource, embodiment#105) keeps listing kind=todo as today. The index path is `<state>/library/index.json` (app.py:1076-1078).
- depends on: t5, t3
- covers: c2, h2, c8, h6, c25, h18, c6, h4
- acceptance:
  - embodiment/library/sync.py and its tests are deleted; daemon/app.py builds no DocumentIndex and starts no sync thread for the library lane; the conversations index (conversations.py, conversations-index.json) is untouched and tests/`test_library_conversations.py` passes unchanged
  - /api/library lists the catalog from a live `list_items` refreshed on a bounded interval (last good list kept on failure, status says when it was listed); /api/library/{id} serves the reader from document.py; /api/library/search answers through retrieve; each answers 503 `library_unavailable` with the library down
  - with the library stopped, with retrieve answering 503 and with retrieve hanging past the deadline, a typed search turn still replies with the fixed fault, status.library.`failures_by_code` counts it and one degradation is recorded
  - on the first start the daemon deletes `<state>/library/index.json` if present, records one ledger entry saying it did, never touches conversations-index.json, and does nothing on later starts
  - status.library shows configured, catalog items, `last_listed_at`, retrieve calls / `calls_ok` / `failures_by_code` / `last_latency_s`; no `items_indexed`, chunks or sync worker fields remain

### t7 — daemon: the reading routes behind the guard, and status.reading

- instruction: Route contract shared by t3, t4 and t7 (JSON): GET /api/reading lists saved positions \[{`item_id`, sha256, section, paragraph, part, `updated_at`, gone}\]; GET /api/reading/{`item_id`}/next?section=&paragraph= (both optional: absent = continue at the cursor) answers {`item_id`, sha256, title, section, `section_count`, heading, paragraph, `paragraph_count`, part, `part_count`, text, position} where position is an opaque string naming exactly that unit, or {done: true} past the end; POST /api/reading/{`item_id`}/heard {position} answers {moved: bool, cursor: {...}}; POST /api/reading/{`item_id`}/clear drops the cursor. Errors: 404 library-item-not-found, 410 library-item-gone, 503 `library_unavailable`. Files: embodiment/daemon/app.py (wiring only), embodiment/http/server.py (routes, refusals like `_TODOS_READ_REFUSALS`), tests/`test_daemon_reading.py`. Reuse the guard exactly as /api/todos and /api/speak use it.
- depends on: t6
- covers: c28, h21, c30, h23, c13, h10, c10, h8
- acceptance:
  - the routes of the shared contract are served by daemon/reading.py through http/server.py; next and heard are refused without the install secret on a non-public Host and without a valid Access assertion on the public Host (401/403), and a refused heard leaves the cursor file unchanged
  - a whole read in a test (three paragraphs, one stale heard report) shows status.reading served 3, heard 3, refused 1, and no senses / completion call happens during it (a senses fake that fails on any call)
  - reading text served to the page is the source text verbatim; a turn that holds read text keeps the remember/forget guard (`library_text_in_context`)

### t8 — docs and release 0.23.0: the library is queried, not copied; documents can be heard

- instruction: Every before-state fact cites code or a measurement in this repo (index.py, sync.py, reading.py:31-32, voice.py:333, docs/live-test-results/2026-10-24-library.md). Do not claim the live acceptance (t9) passed.
- depends on: t4, t7
- covers: c19, h12, c33, h26
- acceptance:
  - README.md, CLAUDE.md (status + code map), embodiment/explain/catalog.py and CHANGELOG 0.23.0 describe retrieve-backed search, the retired index and sync, the read-aloud routes and the cursor file, citing code and the 2026-10-26 probes; tests/`test_release_docs.py` passes with its pinned terms rewritten truthfully (get-only now says the one retrieve POST)
  - the release notes say this release removes the local library index, deletes its file once, and has no switch back: rollback is installing the previous release
  - version bumped with the version-bump script; uv.lock updated; markdownlint, black, isort, flake8, bandit and teken pass

### t9 — live acceptance on the rig, published as measured

- instruction: Owned by the main agent with the operator (the phone part is the operator's hands). Aggregates and structure only in the committed file: no library text, no real ids beyond item numbers.
- depends on: t8
- covers: c1, h1, c20, h13, c21, h14, c22, h15, c7, h5, c9, h7, c18, h11
- acceptance:
  - the gwen-live worktree moves to the merged ref and embodiment-gwen restarts on it; docs/live-test-results/2026-10-26-library-queried-and-heard.md records, from status and logs: retrieve calls >= 1 and 0 failures, 0 GET /file calls for indexing over 10 minutes, 5 kind-less live searches with 0 todo passages, a live EPUB and a live transcript matching /text in the reader
  - a chapter read on the phone over gwen.culture.dev with Access is stopped mid-paragraph, the page reloaded, and continue replays that paragraph; time from tap to first audio is recorded cold and warm separately (c32), and c22's 5 s bar is judged on the warm figure with the cold one beside it, including if it fails
  - a library item created during the run is returned by `search_library` within 60 s of the library indexing it, with no Gwen-side action

## Risks

- [unknown_nonblocking] t6 and t7 both edit daemon/app.py and http/server.py: they are serialized (t7 depends on t6), and t6 is the largest task; if it grows past one reviewable diff, split the reader routes from the sync removal (task t6)
- [unknown_nonblocking] culture-library's /text and /api/rag/retrieve shapes are unversioned; Gwen parses them defensively, but a library release can change them under her (culture-library#25 is already changing retrieve) (task t1)
- [unknown_nonblocking] deleting `<state>/library/index.json` on the live store is irreversible; the copy is derived from the library, so losing it costs only a rebuild by installing the previous release (task t6)
- [unknown_nonblocking] the deploy is a worktree move plus a web build under systemd (embodiment-gwen); a failed build or a bad ref takes gwen.culture.dev down until rolled back; Docker (embodiment#108) is the later home (task t9)
