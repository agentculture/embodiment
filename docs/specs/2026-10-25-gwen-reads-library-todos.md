# gwen reads library todos

> Gwen reads her to-dos from culture-library's todo items and marks one by sending its checklist id on the bus; culture-rules flips the line in the library, and the first real flip is filed as evidence

## Audience

- The operator, who reads and checks to-dos on gwen.culture.dev and asks Gwen about them; and culture-rules, which acts on the todo event
  - instruction: check the README's To-dos paragraph names both readers

## Before → After

- Before: Gwen's To-dos come only from ingested `action_item` memory records, culture-rules never ingests them, and today's Markdown transcripts and to-do lists are refused as `bad_epub` (6 on 2026-10-25), so the list is empty of today's recordings
  - instruction: status library.`failures_by_code`.`bad_epub` > 0 on a tree without the Markdown reader
- After: Gwen reads every Markdown library item; her To-dos list is the library's todo items, line for line; checking one sends its checklist id, and within a minute the library item shows it done and Gwen counts the mark as confirmed
  - instruction: live: mark one line on the page, then read the item file and status.todos

## Why it matters

- One source: what the library holds is what Gwen knows (d17), and the operator can check a to-do once, anywhere Gwen is, and have the record follow
  - instruction: d17 in devague deviate --list for the pocketchat plan

## Requirements

- Gwen reads Markdown library items: a format=md item (culture-rules' new transcripts and to-do lists) is extracted and indexed, not refused as `bad_epub`
  - honesty: a format=md item yields passages (headings and paragraphs, links kept) and is searchable; an EPUB still extracts as before
    - instruction: tests/`test_library_markdown.py` (new) + the existing EPUB tests; live: items 57, 58, 60 searchable
- The To-dos list comes from culture-library kind=todo items: each line '- \[ \] text <!-- todo:<id> -->' or '- \[x\] text <!-- todo:<id> `done_at`=<ISO> by=<by> -->' becomes a to-do with that checklist id, its text and its library done state, grouped per item (title and date from the item's title)
  - honesty: every `- [ ]` / `- [x]` line with a `todo:<id>` comment becomes exactly one to-do; a line without an id is not checkable; text outside lines is ignored
- The Pocket recording id of a to-do is taken from its checklist id (`pocket.<recording id>.<n>`), so the todo event carries `data.id` = the checklist id and `data.recording_id` = the Pocket recording id, as culture-rules' flip rule expects
  - honesty: the published event's `data.id` is the line's checklist id and `data.recording_id` is the middle part of `pocket.<recording id>.<n>`
- A mark shows at once and is kept as Gwen's pending mark until the library item shows that line flipped at least as recently; then the library's state is the one shown, and status counts marks sent and marks the library confirmed
  - honesty: a mark stays shown until the library line agrees with it (`done_at` >= the mark's at); then the library's state shows, and status.todos counts marked and confirmed
    - instruction: daemon tests: pending then confirmed; library flips the other way wins when newer
- Docs and pins follow the source: README.md:156-157, CLAUDE.md:76-79, explain/catalog.py:77 say To-dos come from the library; tests/`test_daemon_library.py`:680/:714/TestCheckableTodos and tests/fixtures/events/todo.json + schema.json note move from memory-record ids to checklist ids
  - honesty: README, CLAUDE.md and the explain catalog say To-dos come from the library; the todo fixture and schema note use a checklist id
    - instruction: grep 'fed from Gwen.s memory' README.md returns nothing; tests/`test_release_docs.py` passes
- The first real flip on gwen.culture.dev is an obligation with a live seam: mark a library to-do on the page and see the culture-library item (same id) show the line \[x\] with `done_at` and by within 60 s; the operator files its evidence
  - honesty: the obligation is filed with seam 'live library to-do flip' and its evidence is filed by the operator from the first real flip
    - instruction: devague oblige + evidence after the plan exists
- Gwen checks the library's to-do items every 30 s (listing only kind=todo, re-reading the changed ones) besides the 300 s catalog check, so a mark's confirmation and a new recording's list reach her within ~30 s; following /api/feed is a follow-up (q1)
  - honesty: a todo item replaced in the library is re-read within one 30 s todo check, without waiting for the 300 s catalog check
    - instruction: a sync test with a fake clock: replace at t, re-read by t+30 s
- Done marks stored under the old memory-record ids are dropped: they match no checklist line and are pruned from todos-done.json (q2)
  - honesty: after the first library-sourced load, todos-done.json holds no key that is not a library checklist id
    - instruction: a daemon test seeded with an old memory-record id
- A mark the library has not confirmed is published again on each 30 s to-do check until the line agrees, the line disappears, or 1 hour passes (then status shows it unconfirmed); re-sending the same at is safe because culture-rules applies a flip only when its at is newer
  - honesty: with the broker down at mark time, the mark is confirmed after the broker returns, without a second click
- status.todos reports items and lines read from the library, marks sent, marks confirmed, marks pending with the oldest pending age, re-sends, and a library-unavailable degradation; a failed library read shows a stated code on the To-dos page, never an empty list
  - honesty: stopping the library makes /api/todos answer a declared code and status.todos record the degradation
- kind=todo items are read by the checklist parser only, not chunked or embedded into the document index; transcript and other Markdown items go through the document index like EPUB items (passages with anchors that `read_passage`, `read_item` and the page's reader use)
  - honesty: after a sync, the index holds passages for items 57, 58, 60 and none for 59, 61
- When a to-do item is archived or deleted, or a line leaves it, its to-dos disappear from the list and any pending mark for them is dropped and counted; a re-extraction that adds lines shows them at the next to-do check
  - honesty: removing a line in a fake library drops it and its pending mark; adding one shows it within one check
- Library Markdown is read as Markdown, not rendered: HTML tags are stripped (culture-rules wraps Hebrew transcripts in a dir=rtl div), entities are unescaped (derived text is HTML-escaped), and lines, characters per line and items per list are capped, so a malformed item is bounded
  - honesty: an item holding a dir=rtl div, an entity and a 10 000-character line yields plain, bounded text

## Honesty conditions

- the delivered tree reads library to-dos and sends checklist ids, and the first real flip is on record as evidence (not claimed before it happens)
- bus.py has no diff in this change
  - instruction: git diff --stat main -- embodiment/bus.py is empty
- web/src has no diff and the /api/todos response keeps its keys
  - instruction: git diff --stat main -- web/src; tests/`test_http_server.py` todo tests unchanged
- `valid_todo_id` accepts pocket.70b523d5-42ca-5978-a92b-b9139c2e4bf3.1
  - instruction: a unit test with that literal id
- the GET-only AST pin still passes and no write method is added
  - instruction: tests/`test_library_client.py`::`test_module_is_get_only`
- the ingest contract still accepts pocket `action_item` and the drain is unchanged
  - instruction: git diff --stat main -- embodiment/ingest embodiment/daemon/drain.py is empty
- `DEFAULT_KINDS` and `SEARCH_KINDS` do not include todo
  - instruction: tests/`test_library_index.py` default-kinds test unchanged
- the operator's page and Gwen's `list_todos` show the same lines the library item holds
  - instruction: compare /api/todos with the item's file for one live item
- the live daemon on 2026-10-25 showed `bad_epub` 6 with items 57-61 listed but not searchable
  - instruction: scratchpad live0/`appstatus_s`.txt
- a mark round-trips: page -> todo event -> library item replaced under the same id -> Gwen's confirmed counter +1
  - instruction: the first real flip (c13), filed by the operator
- no to-do Gwen shows is missing from the library, and none in the library is missing from Gwen after a 30 s check
  - instruction: compare counts after one todo tick
- both times are read from records (feed event at, status), not from a stopwatch
  - instruction: `GET /api/feed?item_id=<id>` on culture-library and `GET /api/status`
- the evidence flip is one Gwen published: its line's `done_at` equals the at of a todo event in Gwen's log, and by matches; a line already \[x\] before Gwen sent anything is not evidence
- for the first flip, `done_at` parsed to epoch equals the event's at to the second

## Success signals

- The first real flip: the culture-library item shows the line \[x\] with `done_at` and by <= 60 s after the mark, and Gwen's status counts 1 mark sent and 1 confirmed within 60 s; `bad_epub` is 0 for Markdown items
  - instruction: time the mark on the page against the item's updated feed event and status.todos

## Scope / boundaries

- embodiment/bus.py is unchanged: the todo event already publishes as type embodiment.bus.todo from app://embodiment, with the to-do fields under data.data, which is where culture-rules reads them
- The /api/todos JSON shape {groups:\[{`recording_id`,title,date,items:\[{id,text,done,`done_at`,`done_by`}\]}\]} and POST /api/todos/{id} stay as they are, so the page (web/src/api/todos.ts, TodosView.tsx) needs no change
- Gwen still never writes the library: the client stays GET-only (tests/`test_library_client.py`:269 AST pin); the flip travels only as the bus event

## Non-goals

- Retiring the ingest mailbox or dropping `action_item` from the pocket contract is not part of this change; only the To-dos stop reading it (pocket facts and summaries and the newspaper source still use it)
- To-do items are not added to `search_library`'s default kinds; `list_todos` stays the way Gwen answers about to-dos

## Assumptions

- `valid_todo_id` (daemon/todos.py:36, `^[A-Za-z0-9._:-]{1,160}$`) already accepts `pocket.<uuid>.<n>` ids; no regex change
- A line's `done_at` is rendered from the at of the event that set it (checklist.set records the event's at; checklist.render prints it as ISO), so Gwen compares her mark's at with `done_at` exactly, on one clock

## Scope exploration

- `s1` — `embodiment/library/epub.py + daemon/app.py:1715 (extract)`: the only extractor is `epub_extract` (zipfile); live on 2026-10-25 the daemon counts `bad_epub` 6 and items 57, 58, 60 (transcripts) and 59, 61 (to-dos) are listed but not searchable
  - seeds: `c2`
- `s2` — `embodiment/daemon/app.py _todos/_todo_key/todo_groups (:4094, :6230-6275)`: to-dos are built from memory records of type `action_item` via memory.`list_ingest`; the item id is the memory record id, `recording_id` comes from record metadata
  - seeds: `c3`
- `s3` — `culture-rules docs/operations/library-items.md (main e936acf) + embodiment#105`: ids are `<prefix>.<list key>.<n>` with the recording id as list key; the flip rule keys on `trigger.data.data.id` and takes `list_key`, id, done, at, by from the event; the flow config is live and never committed
  - seeds: `c4`
- `s4` — `embodiment/bus.py:1317-1323, :373`: Envelope type is f'embodiment.bus.{kind}', source `DEFAULT_SOURCE` app://embodiment, data is the whole bus event; todo is not `LOCAL_ONLY`
  - seeds: `c5`
- `s5` — `web/src/api/todos.ts:104-216, components/TodosView.tsx:36-87`: the page treats ids as opaque, refetches on stream.todo.seq, and needs only text per item
  - seeds: `c6`
- `s6` — `embodiment/daemon/todos.py:36-39`: the id charset admits letters, digits, dots, hyphens; pocket.70b523d5-42ca-5978-a92b-b9139c2e4bf3.1 passes
  - seeds: `c7`
- `s7` — `embodiment/library/sync.py:131-158 + daemon/app.py:1072-1084`: a replaced item is re-read only on a catalog sha256 diff, every 300 s (5 s catch-up while behind), so the library's flip reaches Gwen up to ~5 min after the mark
  - seeds: `c8`
- `s8` — `embodiment/library/client.py:152-197`: all reads go through `_get`, method GET; `list_items`, `get_item`, `fetch_file`; no /api/feed support today
  - seeds: `c9`
- `s9` — `embodiment/ingest/contract.py:52-71, daemon/drain.py, cli/_commands/memory.py`: `action_item` is read only by the to-dos path; drain and the memory CLI are generic over types; no recall or prompt depends on `action_item`
  - seeds: `c10`
- `s10` — `embodiment/library/index.py:74, tools.py:128`: `DEFAULT_KINDS` and `SEARCH_KINDS` exclude todo; `item_kind` passes todo through
  - seeds: `c11`
- `s11` — `README.md, CLAUDE.md, explain/catalog.py, tests/test_daemon_library.py, tests/fixtures/events/`: README says To-dos is fed from Gwen's memory; schema.json says id is the memory record id; daemon-library tests build to-dos from `action_item` records
  - seeds: `c12`
- `s12` — `embodiment#105 acceptance 3`: culture-rules measured 2 s from run start to the replaced item; the 60 s bound is the issue's
  - seeds: `c13`
- `s13` — `challenge pass / failure-mode lens: embodiment/bus.py publish + culture-rules checklist.set`: the bus publishes a todo event once; a broker outage or a dropped message would leave the mark pending forever; checklist.set ignores a flip that is not newer, so a re-send is idempotent
  - seeds: `c21`
- `s14` — `challenge pass / observability lens: daemon/app.py _todos (MEMORY_UNAVAILABLE path) + CLAUDE.md C3`: today a memory failure returns groups=\[\] with `MEMORY_UNAVAILABLE`; the library path needs the same, plus counters that move (lesson 3)
  - seeds: `c22`
- `s15` — `challenge pass / security lens: culture-rules docs/operations/library-items.md (transcript.render, checklist.render) + daemon/todos.py:221`: derived text is HTML-escaped and Hebrew transcripts wrapped in a div; `list_todos` already fences to-do text as untrusted
  - seeds: `c23` (rejected)
- `s16` — `challenge pass / adjacent-systems lens: library/reading.py:70-90 + library/index.py + library/epub.py:60-75`: `read_item` and the reader read indexed passages (anchor, heading, text, links); a Markdown extractor yielding the same Passage shape serves them; refused items are remembered in memory only, so a restart retries them without an index version bump
  - seeds: `c24`
- `s17` — `challenge pass / lifecycle lens: culture-library core/library.py replace + checklist.merge (culture-rules doc)`: replace keeps the item id and changes sha256; merge keeps ids and appends new ones; archive and purge are feed event types
  - seeds: `c25`
- `s18` — `challenge pass / counter-evidence lens: probe md_shape.py on culture-library items 59 and 61`: item 59 already holds a done line with `done_at` and by before Gwen sent any flip, and was re-fetched after a replace (`bad_epub` counted 6 for 5 Markdown items); so an \[x\] line alone is not evidence of Gwen's round trip
  - seeds: `c20`
- `s19` — `challenge pass / unstated-assumptions lens: culture-rules docs/operations/library-items.md checklist.set + checklist.render`: the doc says set records at (epoch seconds) and render prints `done_at=<ISO>`; nothing says it is the event's at verbatim
  - seeds: `c26`
- `s20` — `challenge pass / unexamined surface: Hebrew Markdown transcripts`: only non-wrapped Markdown seen live (probe of 57-61); a Hebrew item is the first real test of tag stripping
- `s21` — `challenge pass / adjacent-systems lens: transcripts vs culture-rules#92`: live transcript items carry no recording id or recording time in their body, which #92 asked for; Gwen does not need them for to-dos (the id gives the recording), so this is noted, not built
- `s22` — `challenge pass / concurrency lens: library/sync.py worker + the new 30 s to-do check`: two schedules on one worker or two threads could both write Gwen's state; the to-do check touches only the to-do list and its marks, never the document index; ordering of marks is by at (newer wins) both at Gwen and at culture-rules
- `s23` — `challenge pass / migration + reversibility lens: todos-done.json`: old keys are pruned (c15); the real store has no to-do file; rollback is the previous release, since d17 removes the memory path for to-dos and there is no fallback by design

## Decisions

- The library is trusted: each item is classified for its trust level and its source (operator, 2026-10-25); Gwen does not treat library items as untrusted by default
- \#105 keeps today's handling of library text (fenced as data, the write guard on library turns); using each item's trust level and source is a follow-up once culture-library serves the classification (culture-rules#90)

## Open parks

- [unknown_nonblocking] No Hebrew Markdown transcript has been seen yet (items 57, 58, 60 carry no div wrapper); the dir=rtl handling is built from culture-rules' doc, not from a live item
