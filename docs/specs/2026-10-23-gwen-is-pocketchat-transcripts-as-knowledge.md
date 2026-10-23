# Gwen is pocketchat, transcripts as knowledge

> Gwen's page is pocketchat: you open gwen.culture.dev and get a chat-first library of your recordings — ask, read, cite, jump to the moment — with Gwen's voice as one button inside it, and she answers from the transcripts themselves, not only from distilled facts

## Audience

- The operator, on gwen.culture.dev from a phone or a laptop (Cloudflare Access, his email only), and on the LAN dashboard - one person reading and asking about his own library
  - instruction: check README 'Remote access' and the Access allow-list (`EMBODIMENT_ACCESS_EMAILS`) name exactly the operator

## Before → After

- Before: Gwen's page is a voice console: the waveform is the hero (App.tsx:127, obligation o9) and chat is the fourth panel; she knows only distilled facts sent through 'embodiment memory ingest', not documents; pocketchat is a separate site over pocketsync, and xteink's library (newspapers, documents, books, and transcripts per culture-rules) is readable by no agent
- After: Opening gwen.culture.dev shows pocketchat's experience as Gwen's page: an Ask view with a pinned composer (mic, speak, record/upload and the speech-to-speech button inside it), a Library view of the library's items (culture-library; xteink's instance today) grouped by day with kind chips, and a side reader; Gwen answers typed questions from the library with \[n\] citations that open the reader at the cited passage, alongside her own remembered facts

## Why it matters

- The operator wants one place to talk with what he has read and recorded; pocketchat already proved the experience (ask, read, cite, jump to the passage), and Gwen adds memory and a voice - but only if the page is chat-first and answers from the documents themselves, cited, rather than from summaries alone

## Requirements

- The ported page keeps pocketchat's anatomy (pocketsync/web/static/index.html:13-145, app.js): an Ask view (thread, empty state with memory bars + 'recently recorded' + suggestions, composer with scope toggles), a Recordings view grouped by day with label chips, a To-dos view, and a side reader (sheet under 860px) that opens at the cited span when a \[n\] citation is clicked (openCitation -> openReader, app.js:364-432, 461, 594), with its tokens, prefers-color-scheme dark mode, reduced-motion and a11y conventions
  - honesty: Each pocketchat region the port keeps (Ask, Library, To-dos or its replacement, reader) is rendered by a component with a test, and the cited source file:line in pocketsync is named in a provenance header in the ported file (cite-don't-import)
- The port adds what pocketchat lacks for Gwen: Hebrew/RTL (pocketchat has no dir=auto and no logical CSS properties; text-align:left, border-left throughout app.css) - dir=auto on question, answer, transcript segments and quotes, and logical properties so the layout mirrors
  - honesty: A Hebrew question, answer, transcript passage and quote each render with dir=auto, and the layout uses logical CSS properties (no physical left/right in the ported CSS), pinned by a test that scans the stylesheet
- Realtime voice becomes a feature inside the Ask view (mic / speak-this / record-or-upload / speech-to-speech buttons at the composer), not the page's hero; pocketchat has no voice UI at all, so these controls are Gwen's additions to the cited page
  - honesty: The mic, speak, record/upload and speech-to-speech controls live in the Ask view's composer, the page's first view is Ask, and the waveform is not the largest element - pinned by an App test on render order
- Gwen's dashboard becomes chat-first: App.tsx (web/src/App.tsx:122-201) today puts the Waveform first as 'the centrepiece (operator decision, obligation o9)' and Chat fourth; the port makes the Ask view primary with a pinned composer, the waveform a compact strip or drawer, the install-secret form shown only on an unauthorized state, and the separate Transcript panel merged into the thread
  - honesty: The install-secret form is shown only when the stream reports unauthorized, and on the public Access host the page works end to end with no secret ever entered
- New read routes on Gwen's HTTP surface (library list, document/transcript read, search) are GET routes under /api/ that inherit guard.py's rules unchanged (secret or Access on the public Host, Host/Origin allow-list) and run on the text pool with their own derived timeout, not the 5 s control default; server.py has no path/query parser today, so the port adds one
  - honesty: Every new /api read route passes guard.check unchanged (a test hits each without a secret on loopback and gets 401, and with Access on the public Host gets 200), runs on the text pool, and has a timeout derived from the library client's own bound
- Library knowledge reaches Gwen as READ tools beside remember/forget (tools.py ToolSpec; daemon binds at app.py:1465-1498; typed and voice turns share the tools, `max_steps` 8), whose results are wrapped in the same `KNOWLEDGE_ATTRIBUTION` fence as recall (tool output is plain str today and bypasses `render_recalled`) and whose citations get a new path into the chat's sources (`chat_source` is fed only from recall records today, app.py:4737-4768)
  - honesty: A library tool result reaches the model only inside the `KNOWLEDGE_ATTRIBUTION` fence with fence marks neutralised (a test plants an instruction and a fence mark in a document and asserts both arrive quoted), and every cited passage appears in the reply's sources with item id, title, date and passage anchor
- Gwen reads item text herself: fetches GET /api/library/{id}/file and extracts EPUB text with the stdlib (zipfile + html.parser - no new dependency, `test_zero_deps.py`), chunks it, and embeds chunks with gemma2-embed into her own 0600 document index; the library's q stays a metadata filter, never the content search
  - honesty: Gwen's document index is a separate 0600 file under her state dir (never the eidetic room store), built with stdlib EPUB extraction (`test_zero_deps.py` unchanged), and a re-fetched item whose sha256 is unchanged is not re-embedded
- Gwen's library client is GET-only by construction (an AST/test pin that it never sends POST/DELETE/PUT to the library), because xteink keys have no scopes: any `xtk_` key is a full operator key (docs/api.md:17-37 - upload, delete, mint keys, revoke devices); the key is held in grant and injected, never written to disk by Gwen
  - honesty: No code path in Gwen's library client can issue POST, PUT, PATCH or DELETE (AST test over the client module), and the key is read only from the injected environment, never from a file Gwen writes

## Honesty conditions

- Every user-visible claim in the announcement is true on gwen.culture.dev at ship time: chat-first page, library reading, cited answers from document text, voice as a button - nothing on the page is a placeholder for a library that is not wired
- The Access allow-list for gwen.culture.dev names only the operator's email, and the dashboard is the same build on the LAN and the public host
- The before state is checked against main at the start of the cycle: App.tsx waveform-first, chat fourth, no library client, tools bound = remember + forget
- The why is the operator's stated aim (one place to talk with what he read and recorded); no value is claimed beyond it until measured
- On the deployed page each named element exists and works: Ask first, composer with voice controls, Library view listing real library items, side reader opening at a citation
- A grep/AST check over embodiment finds no write call to the library and no change to sibling repos in the cycle's diff; any sibling ask is a drafted issue, posted only after the operator's go-ahead
- The live check of >= 5 questions is recorded with which item each citation opened; a citation that opens the wrong item or no passage counts as a failure
- The AST/test pin runs in CI, and the live run's request log is read and its non-GET count reported as a number
- The questions are fixed before the run, the hit@5 and p90 are published as measured including if bad, and no real document text enters the repo

## Success signals

- On gwen.culture.dev, 100% of answers that use a library item carry at least one \[n\] citation whose click opens the reader on that item at the cited passage (pinned by test and checked live on >= 5 questions)
- Gwen's library client sends 0 non-GET requests to xteink - pinned by an AST/test check and confirmed in a live run's request log
- A measurement over >= 20 model-written reworded questions against the indexed library is published (hit@5 and the per-recall p90 latency at the indexed size) in docs/live-test-results/, aggregates only, whatever the numbers are

## Scope / boundaries

- Gwen never writes to the library: no POST/PUT/DELETE, no key minting, no device or queue calls; culture-library, xteink, ebooks-cli, culture-rules, pocketsync and the firmware are not changed by this cycle - asks to them are drafted for the operator's go-ahead

## Non-goals

- No change to pocketsync in this cycle: no new endpoint, no service-token policy, no second --no-access-check instance, and pocketchat.culture.dev is not retired here (no retirement plan exists in pocketsync's docs)
- No reading progress, bookmarks or highlights as knowledge this cycle: the firmware never sends them back (xteink-firmware docs/xteink/sync.md) and no repo stores them

## Assumptions

- Gwen must serve pocketchat's backend contract or an equivalent: GET recordings list/detail/transcript/actions, a health endpoint, and a chat answer with \[n\]-numbered citations {n,id,title,`recording_at`,start,end,text} plus retrieval.mode (app.js:54 api(); docs/web-api.md) - the client is built around those shapes
- Today's library instance is xteink's, live on this host (python -m xteink.server serve on 127.0.0.1:8780, Docker container xteink-api-1; MCP on 8782) from the local-only branch feat/issue-1; its contract is docs/api.md + api/openapi.json there. The library's home becomes culture-library at library.culture.dev (c30), so Gwen binds through one small client seam whose base URL and contract a move re-points, not rewrites
- Transcripts are NOT in the library yet: the live catalog (2026-10-24, 17 items) holds 13 culture-rules 'Daily newspaper' issues (2026-10-11..23, sections such as Jetson news), 3 standalone documents and 1 O'Reilly book, and no Pocket transcript; xteink#8 describes culture-rules pushing transcripts as EPUBs, so that rule is planned or not yet running. Gwen's library index therefore starts with newspapers and documents, and transcripts arrive when the rule runs

## Scope exploration

- `s1` — `pocketsync/web/static/{index.html,app.js,app.css}`: Vanilla ES-module page, ~1600 lines, no framework/build/CDN, all text via textContent (app.js:2,16). Three hash views (Ask/Recordings/To-dos), side reader with a speaker-tinted tape; citations are \[n\] markers in a non-streamed answer, revealed word by word client-side (revealWords app.js:189). History is client-only, last 6 messages
  - seeds: `c3`
- `s2` — `pocketchat RTL handling (app.css, index.html lang=en)`: grep for dir=auto / logical properties returns 0; layout is physically left-anchored; Gwen listens and speaks Hebrew in v1, so the port must add bidi handling
  - seeds: `c4`
- `s3` — `pocketchat voice surface`: No voice UI, no streaming endpoint, no WebSocket in pocketchat (app.js fetch calls are all JSON request/response); the pending/revealWords UI is the hook for a spoken or streamed answer
  - seeds: `c5`
- `s4` — `pocketchat client-backend contract (app.js api() calls)`: Endpoints: GET /api/health, /api/recordings?limit=500, /api/recordings/{id}, /api/recordings/{id}/transcript, /api/actions?`open_only`=, POST /api/chat, POST /api/enrich. Auth: no bearer; same-origin Access cookie, server verifies Cf-Access-Jwt-Assertion and Origin on POST
  - seeds: `c6`
- `s5` — `pocketsync web API + MCP + recall (server.py:303-325, access.py, mcp/server.py, recall/search.py)`: Readable surface exists (GET recordings/transcript/actions, POST search with chunks {start,end,text,score}, POST chat with \[n\] citations; MCP is stdio-only), but every HTTP call needs a Cloudflare Access JWT (no loopback/service path), and the operator chose xteink's library instead (c7), so pocketsync is not Gwen's read path
  - seeds: `c7`
- `s6` — `pocketsync deployment + contracts (deploy/systemd/pocketsync-web.service, docs/operations/pocketchat-culture-dev.md, tests/test_surface_parity.py)`: pocketchat runs on 127.0.0.1:8788 behind its own tunnel/Access app; its web API and verb registry are contract-pinned by `test_surface_parity.py`; no stated plan to retire the site; cross-repo asks go through communicate
  - seeds: `c8`
- `s7` — `embodiment web/src (App.tsx, components/, api/, hooks/useEventStream.ts)`: React 18 + Vite, deps react/react-dom/two fontsource fonts only, build fails on any external origin (scripts/check-no-external-origin.mjs); Chat/SpeakButton/AudioControls/api/\*/useEventStream survive as-is; App layout, app.css (one breakpoint), App.test.tsx order assertions and Chat.tsx:98 typedSerials filter need rework; waveform-first is a recorded obligation o9 the port supersedes
  - seeds: `c9`
- `s8` — `embodiment/http/server.py _route (664-695) + guard.py requires_guard (448-469)`: Every /api/\* path is guarded incl. GET; unknown /api paths 404; long reads must not use the 5 s control pool; query strings are stripped before matching (server.py:665)
  - seeds: `c10`
- `s9` — `embodiment/tools.py + daemon tool binding + memory.render_recalled + chat_source`: Tool results are str(value) fed back as the tool message, bypassing the untrusted-data fence; sources come only from turn.records; tests/`test_release_docs.py` pins `NOT_YET` 'other tools' and `TOOLS_BOUND`=(remember,forget) - a third tool needs README/explain/CLAUDE.md edits and a reviewed test change
  - seeds: `c11`
- `s10` — `xteink feat/issue-1 (docs/api.md, xteink/server/routes_library.py:51-115, core/models.py)`: GET /api/library?q=&kind= (q matches title/author only), GET /api/library/{id}, GET /api/library/{id}/file (FileResponse: the stored EPUB/TXT/BMP), POST upload, DELETE. Item = {id, sha256, kind book|article, title, author, format, size, `created_at`}; sqlite + files dir; no full text, no search over content, no embeddings, no highlights or progress
  - seeds: `c12`
- `s11` — `xteink search capability`: The only search is q over title/author (docs/api.md:114-120); Gwen cannot ask the library 'what did I say about X' - content search must be built on Gwen's side or asked of ebooks-cli (ebooks-cli#1 design mentions no search/embeddings)
  - seeds: `c13`
- `s12` — `xteink#8 + culture-rules transcript push`: Per xteink#8 a transcript corpus already flows through the library as kind=article EPUBs; whether those EPUBs carry segment timestamps or speakers (pocketchat's tape/jump-to-moment needs start/end) is unread
  - seeds: `c14`
- `s13` — `xteink auth (docs/api.md:17-60, xteink/server/auth.py:131 require_operator)`: Every library route depends on `require_operator`; keys are `xtk_` API keys (no scope/role field on the key object) or `xtd_` device keys (device app only); optional Cloudflare Access SSO on the main app; MCP needs a per-caller `xtk_` key
  - seeds: `c15`
- `s14` — `ebooks-cli (scaffold b7214f6, issue #1)`: Empty template CLI; issue #1 is the build brief to extract xteink's library/API/MCP, with 'bearer keys with scopes' and OPDS-first; nothing built - the natural place to ask for read-scoped keys and a content search endpoint later
- `s15` — `xteink-firmware (docs/xteink/sync.md, SCOPE.md:35)`: Pull-only device sync; status POST carries no reading position, bookmarks or highlights (they stay on the SD card); SCOPE.md rules out new connectors - reading progress is not available as knowledge this cycle
- `s16` — `live xteink library catalog (GET /api/library with Gwen's key, 2026-10-24)`: 17 EPUB items: ids 5,8-10,12-20 culture-rules daily newspapers; 1,2,4 standalone docs (one LaTeXML paper); 11 an O'Reilly book (7.4 MB). No item is a Pocket transcript; newspaper headings are topic sections (Jetson optimization/jobs/news, Isaac ROS), no recordings. EPUB markup: p/section/h2/li, no data-\* timing attributes, no timestamp or speaker markup
  - seeds: `c14`

## Decisions

- UI: port pocketchat's page (pocketsync/web/static: index.html, app.js, app.css) into Gwen's dashboard, citing it (cite-don't-import, with provenance), not importing pocketsync; chat + library first, realtime voice is a feature inside it, not the main view. Reverses the last cycle's q3 ('chat only, no pocketchat parity')
- Knowledge source: Gwen does NOT read pocketsync's API, MCP or Mongo. Transcripts already reach xteink's library through a (culture-rules) rule, so xteink's library is the one knowledge source Gwen reads - transcripts, and the library's books
- Library access: one dedicated `xtk_` key for Gwen, held in grant (injected, never written by Gwen); her library client is GET-only by construction and pinned by test; an ask for read-scoped keys goes to ebooks-cli (communicate, operator go-ahead)
- Index scope: Gwen indexes transcript and article items first; books come later or opt-in per item, after recall cost at that size is measured
- Timestamps: inspect one transcript EPUB's structure (markup only, no content shown) before deciding whether the tape/jump-to-moment survives, needs a culture-rules ask, or is dropped for library items
- Keep the distilled-facts ingest beside the library: facts in Gwen's memory serve fast spoken recall (0.25 s head start); the library is the deep, cited source
- Assume culture-rules pushes Pocket transcripts into xteink's library as EPUB items (operator, 2026-10-24). The spec builds for transcripts arriving through the library; it does not claim any are there today - the 2026-10-24 catalog held none (s16)
- The knowledge home is culture-library (operator, 2026-10-24), served at library.culture.dev; it holds all our knowledge. Gwen binds to 'the library' through one client seam; xteink's live library (127.0.0.1:8780, feat/issue-1) is today's instance until culture-library serves it

## Open parks

- [unknown_nonblocking] The library contract lives on xteink's local-only branch feat/issue-1 and xteink#3 moves it to ebooks-cli; the shape Gwen binds to may change before or after this cycle ships
- [unknown_nonblocking] The transcript EPUB format is unseen (none in the library on 2026-10-24): whether it carries segment start/end times and speakers decides if pocketchat's tape and jump-to-moment survive for transcripts or degrade to opening the reader at the cited passage
- [unknown_nonblocking] culture-library (library.culture.dev) was not found on 2026-10-24 - not in ~/git, not under agentculture on GitHub, no DNS - so its API contract, auth (Access? scoped keys?) and whether it keeps xteink's /api/library shape are unknown

## Resolved vagueness

- [unknown_nonblocking] The library contract lives on xteink's local-only branch feat/issue-1 and xteink#3 moves it to ebooks-cli; the shape Gwen binds to may change before or after this cycle ships — resolved: duplicate of v1 (recorded twice by an errored retry)
