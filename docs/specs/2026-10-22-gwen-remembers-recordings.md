# Gwen remembers recordings

> The operator talks with Gwen — by voice on the rig and by text at gwen.culture.dev behind Cloudflare Access — and she remembers their Pocket recordings and culture-rules' daily newspaper: culture-rules enqueues distilled facts through an embodiment-owned memory ingest verb, the daemon is their single writer, recall finds them semantically through gemma2-embed, every recalled fact is cited by its source, and pocketchat can retire

## Audience

- the operator, talking with Gwen by voice on the rig and by text from a phone or laptop at gwen.culture.dev; and culture-rules, the producer that enqueues distilled facts from Pocket recordings and the daily newspaper

## Before → After

- Before: today the guard checks the install secret before the Access assertion (guard.py:541-551) and the default verifier refuses every token, so the public Host is unreachable and an Access-authenticated browser would still need the install secret
- After: Gwen answers a question about a Pocket recording or a newspaper item — spoken or typed — from her own memory, cites where the fact came from, and finds it by meaning in Hebrew rather than by an exact word; culture-rules feeds her through one verb; pocketsync can redirect pocketchat to gwen.culture.dev

## Why it matters

- today the operator's recordings are reachable only through pocketchat, a second chat front with its own index, and Gwen's recall is keyword-only and blind to Hebrew morphology (#92 measured 0 of 24 recalls rendered in one session); one front with one memory is the point, and C3 says a recall that silently finds nothing is the worst outcome

## Requirements

- `embodiment memory ingest --file <ndjson>` and `memory forget --source pocket --source-id <id>` validate the whole batch, then enqueue it atomically (temp + rename) into a 0700 inbox under the daemon's state dir; they never write the store, are dry-run unless `--apply`, and take `--json`
  - honesty: a refused batch writes nothing to the inbox: validation completes over every record before the temp file is renamed
    - instruction: test: a batch with one bad record leaves the inbox directory unchanged and exits 1 with a hint
- the running daemon is the only consumer of the inbox: it drains batches in filename order, one record write at a time so no write holds the store lock across a batch, and moves each batch to done/ or failed/ with a safe reason; batches queued while the daemon is down apply on the next start
  - honesty: the drain applies batches in enqueue order and moves each to done/ or failed/ exactly once, across a daemon restart mid-batch
    - instruction: test: kill the drain between records; on restart the batch completes idempotently
- ingested records land in the one shared private ingest scope of c39, which recall reads alongside `gwen`; visibility private, the scope and `added_by: culture-rules` are stamped by the verb and never taken from the file
  - honesty: a record written to the ingest scope is returned by the daemon's recall, and a record in any other scope is not
    - instruction: test against RoomMemory with two scopes in one pinned dir
- record ids rendered in a prompt or forgotten by the tool use a charset the funnel accepts; either the wire id `pocket:<rec>:<kind>:<n>` is mapped to a charset-safe stored id or the header charset is widened, with the fuzz test re-run either way
  - honesty: every stored ingest id passes `HEADER_LABEL_CHARSET` and the fuzz test is re-run over ingest-shaped ids
    - instruction: uv run pytest tests/`test_memory.py` -k Fuzz
- a record carrying `supersedes` hides the record it replaces before the batch is reported done — the daemon flips the old record's lifecycle itself; nothing relies on a later `eidetic sweep`
  - honesty: after a batch with supersedes is reported done, the superseded record is never returned by recall
    - instruction: test: ingest B, then A supersedes B; recall the shared text; only A comes back
- `memory forget --source pocket --source-id <rid>` archives every record whose metadata.`recording_id` matches by enumerating the scope (not by ranked recall); a per-id forget exists for the surplus ids a re-distil leaves behind
  - honesty: forget by source id archives every record of that source id and no other, enumerating the scope rather than ranking a recall
    - instruction: test with 30 records over 3 recording ids; forget one; 10 archived, 20 untouched
- a recalled recording fact renders through the single `render_recalled` funnel with its provenance (recording title, date, mm:ss) as neutralised, quoted data; the fuzz generator and the one-sanitiser AST test are extended to every newly rendered field
  - honesty: every newly rendered provenance field goes through `_neutralise` and a charset, the fuzz generator produces hostile values for it, and the one-sanitiser AST test still passes
    - instruction: uv run pytest tests/`test_memory.py` -k 'Fuzz or sanitiser'
- the ingest verbs run correctly under culture-rules' runner: an absolute argv, no stdin, a throwaway cwd and an env of only PATH and LANG — so the state dir is given by flag (`--state-dir`) and `--file` takes an absolute path; exit 0 means success
  - honesty: the verbs succeed with env {PATH, LANG}, stdin closed, a temp cwd, an absolute --file and --state-dir
    - instruction: test: subprocess.run with that exact env and cwd
- the verbs are genuinely idempotent: re-enqueuing the same file twice changes nothing in the store, because `machine.command` has no idempotency key and the engine retries only steps declared idempotent
  - honesty: enqueueing and draining the same file twice leaves the store byte-identical apart from timestamps the store itself owns
    - instruction: test: hash the scope file after one and two applies
- `memory` is a CLI noun with `ingest`, `forget` and `overview` leaves on the nested-subparser precedent, each with an explain-catalog entry, so the introspection tests and `teken cli doctor --strict` stay green
  - honesty: teken cli doctor --strict and the introspection tests pass with the memory noun registered
    - instruction: uv run teken cli doctor . --strict && uv run pytest tests/`test_cli_introspection.py`
- a text chat with Gwen: a new guarded POST route and Controls callable that runs a text turn through the daemon's existing turn path (`run_turn`), bounded so a slow model call is not a control-timeout 503, and a chat view in the dashboard
  - honesty: a text turn over HTTP returns within its own bounded deadline or a 503 with a reason, never a hung request
    - instruction: test with a slow fake model: the route answers 503 inside the bound
- a cited, owned copy of pocketsync's stdlib Access verifier backs the guard's `assertion_verifier` seam, configured by an all-or-nothing `EMBODIMENT_ACCESS_TEAM_DOMAIN` + `EMBODIMENT_ACCESS_AUD` pair; an unavailable JWKS refuses with 503, every other failure with 401/403; with the pair unset behaviour is byte-identical to today
  - honesty: the verifier imports only the standard library and `test_zero_deps.py` passes unchanged
    - instruction: uv run pytest tests/`test_zero_deps.py`
- chat answers that use recording memory show provenance (title, date, timestamp) rendered with textContent only and dir=auto, reusing pocketsync's post-retrieval pipeline (source fencing, \[k\] citation validation) over Gwen's lexical recall
  - honesty: the chat view inserts no model or memory text as HTML and sets dir=auto on every message and source
    - instruction: a static test over web/src forbidding innerHTML/dangerouslySetInnerHTML, plus a vitest asserting dir=auto
- the README not-yet entry for Cloudflare Access, the explain root, CLAUDE.md and the tunnel verb's default hostname move to the shipped state for gwen.culture.dev, with `tests/test_release_docs.py` updated
  - honesty: the release-docs test passes with the Access and semantic-recall entries moved out of Not yet
    - instruction: uv run pytest tests/`test_release_docs.py`
- the volume ceiling is measured: recall latency against store size (records per recording x recordings) under the 0.25 s deadline, published with its n, and the deadline re-derived from that distribution if it censors hits (#92 item 4)
  - honesty: the volume ceiling is stated as records the recall deadline carries at p90, measured over stores of at least 3 sizes
    - instruction: extend `measure_recall.py` with synthetic store sizes
- on the public Host, a valid Access JWT for the operator's identity admits the request without the install secret; loopback and LAN requests still require the install secret; with the Access env pair unset, the public Host is refused exactly as today (operator decision q1)
  - honesty: with the Access env pair unset, the guard's behaviour is byte-identical to today's on every existing guard test
    - instruction: uv run pytest tests/`test_http_guard.py`
- Gwen's semantic recall embeds with the gateway's `gemma2-embed` lane (google/embeddinggemma-2, 768-d), named explicitly by role, never inferred from a model name; it replaces eidetic's default Qwen/Qwen3-Embedding-0.6B and the unreachable `embedder` role; the recall mode actually in effect is reported (C3) (operator, 2026-10-23)
  - honesty: the embedder in effect is reported in status() by role name, and an unreachable gemma2-embed demotes recall to keyword with a recorded degradation
    - instruction: test with an embed probe that fails
- semantic recall latency is measured against store size, and the recall deadline is re-derived from that distribution rather than kept at 0.25 s by default (agent proposal from the scope survey: eidetic re-embeds every candidate, scoring.py:354-366)
  - honesty: the re-derived deadline is computed from the measured distribution and recorded with its n, not chosen
    - instruction: the delivery cites the distribution and the derivation
- the docs that say semantic recall is down move to the shipped state: the CLAUDE.md rig table (embedder not ready), deviation d5's framing, and the README/explain not-yet 'semantic recall' entry pinned by tests/`test_release_docs.py` `NOT_YET` (agent proposal)
  - honesty: every doc statement that semantic recall is down is changed in the same PR that turns it on
    - instruction: grep -rn 'lexical-only\|embedder is down\|not ready' README.md CLAUDE.md embodiment/explain
- the ingest contract is source-generic: a fixed registry of sources, today `pocket` (recordings) and `newspaper` (culture-rules' daily issue), each with its own allowlisted types, id prefix, required provenance fields and size caps; a source outside the registry is refused at enqueue (operator, 2026-10-23)
  - honesty: a record whose metadata.source is not in the registry is refused at enqueue with exit 1 and a hint naming the registry
    - instruction: test with source=unknown
- a newspaper item is recallable with its provenance — issue date, section and source link — rendered through the same single funnel as quoted, neutralised data; a link is shown on the dashboard and never spoken as an instruction (agent proposal)
  - honesty: a newspaper link is rendered as quoted data in the prompt and as text (not an anchor with script) on the dashboard
    - instruction: test the render and a vitest over the chat view
- ingested volume is bounded per source and over time: a per-batch cap per source, plus the retention rule of c40, so the store a recall searches stays inside the measured deadline (agent proposal)
  - honesty: a batch over its source's cap is refused whole at enqueue
    - instruction: test with cap+1 records
- record vectors are cached beside the pinned store, keyed by text hash + embedding model, filled when the drain writes and when remember writes; a recall embeds only the query (operator decision q5)
  - honesty: a recall embeds exactly one text (the query) when every candidate's vector is cached, and a cache entry for a changed model is never reused
    - instruction: test with a counting fake embedder
- every external source lands in one shared private ingest scope, metadata.source telling pocket from newspaper; recall reads the gwen scope plus that one (operator decision q6)
  - honesty: recall reads exactly two scopes, gwen and the ingest scope, inside one deadline
    - instruction: test the scope list RoomMemory queries
- a newspaper issue's items are archived together 30 days after the issue date (operator decision q7)
  - honesty: a newspaper item is returned by recall 29 days after its issue date and not 31 days after
    - instruction: test with an injected clock

## Honesty conditions

- every clause of the announcement is backed by a confirmed requirement and by an acceptance check in the plan
  - instruction: trace each clause to a claim id in the exported spec before /spec-to-plan
- no drained write holds the store lock across more than one record, so a recall issued mid-drain meets its deadline
  - instruction: extend TestTheStoreLockCannotStallATurn with a drain in flight
- an ingested record whose text is an imperative naming remember or forget produces zero tool calls in a scripted turn
  - instruction: test with a fake model that echoes its system prompt; assert no `tool_calls` executed
- recalled ingest records are unreachable by the forget tool's id path
  - instruction: test: forget tool with an ingest id is refused
- the before-state is confirmed by an existing test that the public Host is refused today
  - instruction: uv run pytest tests/`test_http_guard.py` -k public
- no step of the plan runs cultureflare, cloudflared or a gh post to a sibling repo
  - instruction: review the plan's task commands
- one spec and one plan cover both parts, and the plan's waves can still land A's tasks before B's
  - instruction: devague plan waves
- both audiences have a surface: the operator a voice/text turn and the chat page, culture-rules the memory verbs with a documented record contract
  - instruction: explain catalog entries for memory ingest/forget + the chat route documented in README
- the after-state is demonstrated with synthetic records only — no real recording or transcript enters the repo or a test
  - instruction: grep the diff and fixtures for anything not generated by a test helper
- the before numbers quoted are #92's, re-measured on this store by the `success_signal` measurement rather than restated
  - instruction: cite results.json from the measurement script in the delivery
- the drain is exercised by a test that interleaves CLI enqueues with daemon remember calls against a real files backend and counts every record afterwards
  - instruction: uv run pytest tests/`test_memory_ingest.py` -k interleaved
- the measurement runs on a copy of the store, with reinforce=False, a labelled query set and the daemon's own recall path, and states n
  - instruction: scratchpad recall94/`measure_recall.py`, then publish under docs/live-test-results/
- the verifier's tests sign tokens with a locally generated RSA key and a fake JWKS fetch — no network in the suite
  - instruction: uv run pytest tests/`test_http_access.py`

## Success signals

- a synthetic pocket batch and a synthetic newspaper batch enqueued with `memory ingest --apply` are applied by a running daemon within 2 drain intervals, recalled in a text turn and cited with their source; 0 records lost across 100 interleaved ingest and remember writes
- on Gwen's store, recall hit@5 and hit@1 and latency median/p90 are published for keyword+d5 versus gemma2-embed with cached vectors, with n stated and published whatever the numbers say
- on the public Host: 100% of requests without a valid Access JWT are refused (401/403), a valid operator JWT is admitted without the install secret, and an unreachable JWKS answers 503 — all under test with a locally signed RS256 key

## Scope / boundaries

- recalled recording facts are data only: no code path turns recalled text into a tool call, recordings are unreachable by the `forget` tool, and a hostile imperative inside an ingested record provably causes no tool call (test)
- nothing in this cycle provisions Cloudflare (Access app, tunnel route) or posts to a sibling repo; the tunnel verb prints commands and the operator runs them; 'B is live' goes to pocketsync through the communicate skill with the operator's go-ahead
- A and B ship as one cycle: one spec, one plan (operator decision q2)

## Non-goals

- embodiment makes no model calls and no content decisions on ingest — distillation, id minting, `supersedes`, and what to forget are culture-rules' (projection rule, #94 comment 6351646938)
- ingested records are never pushed into an active session's prompt; they become recallable memory only (#94 refinement 6351576099; #90)
- pocketchat parity beyond chat — a recordings list with filters, a transcript reader with the cited passage highlighted, an action-items list — and Gwen's own full-transcript lookup (pocketsync MCP / read verbs, a first tool beyond memory under #89) are not part of this cycle
- the since-we-last-talked digest is not part of this cycle (operator decision q4)

## Assumptions

- a concurrent ingest write competes with turn recall for `_STORE_LOCK`; recall's 0.25 s deadline (memory.py:305, app.py:697) returns no records on a miss, so the drain must yield between records or it silently costs turns their memory
- the files backend deletes an older record in the same scope file whose text hash matches a new id (files.py:53-58), so two recordings that distil to identical text collapse; ingest must make text distinct (e.g. provenance in the text) or accept and report the collapse
- pocketsync will emit `pocketsync.recording.landed`, `.deleted` and `.merged` as outbound events; today it emits only `.enriched` and `.reclassified`, and deletes/merges are inbound webhooks that emit nothing — so until it does, forget-on-delete has no trigger
- embeddinggemma-2 takes task prompt names (Retrieval-query / Retrieval-document) and refuses unknown ones with 400; eidetic sends none, so the retrieval quality of the default prompt is unmeasured (agent proposal from the gateway probe)
- newspaper text is less trusted than a recording: it is a model's summary of arbitrary web pages, so anyone who can publish a page can place text in Gwen's memory; the fence, the no-tool-authority test and the fuzz property cover newspaper records exactly as they cover recordings (agent proposal)
- culture-rules adds the newspaper's producer side: a rule or a workflow step after render that writes the issue's items as NDJSON to an absolute path and runs the ingest verb on Gwen's host through a runner actor; culture-rules has no file-writing action today (agent proposal)

## Scope exploration

- `s1` — `embodiment/daemon/state.py`: `resolve_state_dir` (state.py:489), the 0700 helper (state.py:550), the atomic temp+rename writer (state.py:698) and the stale-temp sweep (state.py:875) all exist and are reusable; no inbox, spool or ingest code exists anywhere in embodiment/ (grep)
  - seeds: `c2`
- `s2` — `eidetic files backend (data_refinery store/backends/files.py:43-60, 266-290)`: upsert is load, mutate, atomic replace with no cross-process lock and one shared .tmp name; continuity.`_STORE_LOCK` (continuity.py:352) is an in-process RLock held for every eidetic call, so a second writing process would lose updates
  - seeds: `c3`
- `s3` — `embodiment/daemon/app.py pump + close`: the only periodic tick is pump() every `poll_interval_s`=0.5 (app.py:1404-1418); close(deadline) hands out cumulative shares (summary first, memory 0.98, app.py:1461-1479), so a drain needs its own bounded step and a close share
  - seeds: `c3`
- `s4` — `tests/test_memory.py TestTheStoreLockCannotStallATurn (:328)`: an existing test class already guards the store lock against stalling a turn; the drain must extend it rather than bypass it
  - seeds: `c4`
- `s5` — `embodiment/memory.py RoomMemory.recall (memory.py:1807-1810) + eidetic scope.can_serve (scope.py:25-31)`: recall is hard-bound to the one scope fixed at construction, and eidetic serves a private record only to the same scope name and visibility, so a gwen-recordings record is invisible to Gwen today without a memory.py change
  - seeds: `c5`
- `s6` — `embodiment/memory.py HEADER_LABEL_CHARSET (memory.py:402)`: the header label charset is \[A-Za-z0-9.`_`-\]; the proposed id `pocket:<rec>:<kind>:<n>` contains ':' and would render as placeholder characters, and `_safe_record_id` (app.py:3686) would refuse it
  - seeds: `c6`
- `s7` — `eidetic lifecycle.py:12-16,159-178 + continuity.py:795-811`: recall only hides records whose stored lifecycle is shadowed/archived; writing A with supersedes=B does not hide B until a sweep runs, and nothing in embodiment calls sweep; the default sweep also archives records older than 365 days or with signal below 0.25
  - seeds: `c7`
- `s8` — `embodiment/memory.py RoomMemory._build (memory.py:1549-1572)`: RoomMemory.remember emits only id, text, type, created, metadata, `added_by` — no supersedes, links, lifecycle or scope — although continuity.`_build_record` (continuity.py:690-734) would accept them
  - seeds: `c7`
- `s9` — `data_refinery store/backends/files.py:53-58`: for a new id, upsert drops every existing record in the scope file with the same text hash before appending — the older id is silently deleted; hash is SHA-256 of text, not metadata.`content_hash`
  - seeds: `c8`
- `s10` — `eidetic-cli commands + backend.search filters (backend.py:~395-420)`: eidetic has no archive/forget verb; facet filters on metadata exist but recall is ranked and query-bound, so backend.all() is the only true enumeration; archive is embodiment's continuity.archive, by id, one scope
  - seeds: `c9`
- `s11` — `culture-rules#73 comments 3 and 5`: culture-rules forgets on recording.deleted by --source-id, forgets source then re-distils target on merged, and when a re-distil yields fewer facts it forgets the surplus ids — by id, which #94 does not specify
  - seeds: `c9`
- `s12` — `embodiment/memory.py render_recalled (memory.py:2096-2134) + tests/test_memory.py TestTheFenceHoldsUnderFuzzing (:1233)`: `render_recalled` renders only id, `added_by`, created and quoted text; metadata and type are never rendered, and the fuzzer never generates metadata, so title/date/timestamp provenance is a new rendered surface the fence test does not cover
  - seeds: `c10`
- `s13` — `embodiment/tools.py:232-290 + daemon/app.py:1118-1136, 3494-3527`: tools dispatch only from model `tool_calls`; the daemon binds exactly remember and forget; recall enters at one point, the end of the system prompt — the residual risk is prompt-level persuasion, which #89 (a future do tool) makes worse
  - seeds: `c11`
- `s14` — `embodiment#94 comment 6351646938`: pocketsync is the source of truth; Gwen's records are a distilled projection, content changes arrive only through the queue, Gwen never edits or enriches them herself
  - seeds: `c12`
- `s15` — `embodiment#94 comment 6351576099`: ingest only enqueues; records are stored for recall, never pushed into a live prompt; an optional operator-toggled metadata-only digest is offered
  - seeds: `c13`
- `s16` — `culture-rules culture_rules/actors/code.py:65,148-182,282-300`: machine.command runs a local Popen with `_MINIMAL_ENV` {PATH, LANG}, stdin=DEVNULL, a fresh temp cwd, fixed argv templates with {param} substitution; non-zero exit = failed (retryable), stdout is not parsed, 60 s default timeout
  - seeds: `c14`
- `s17` — `embodiment/cli/_commands/status.py:114-121 + daemon/lifecycle.py:1027`: --state-dir is the established override flag; lifecycle.status() reports running/stopped and the `state_dir` actually in use, including a fallback dir
  - seeds: `c14`
- `s18` — `culture-rules culture_rules/node/actions/machine.py:58`: `supports_idempotency_key` = False; #73 comment 3 says runner steps should be declared idempotent so `unsafe_retry` does not block retries
  - seeds: `c15`
- `s19` — `pocketsync/events.py:92-93 + serve.py:39-40,296-310`: the only outbound event types are recording.enriched and recording.reclassified; recording.deleted/merged are inbound Pocket webhooks handled by tombstoning in the sinks with no event emitted
  - seeds: `c16`
- `s20` — `embodiment/cli/_commands/cli.py:34-54 + explain/catalog.py + tests/test_cli_introspection.py:16-53`: one nested-noun precedent exists (cli overview, `parser_class`=type(p)); the introspection test walks the parser recursively and fails on any uncatalogued path; no verb has --apply today (tunnel is dry-run only)
  - seeds: `c17`
- `s21` — `embodiment/http/server.py:275,354-371,518-546 + web/src`: routes are only events, status, voice start/stop and mic mute; Controls has four callables; the daemon has `run_turn` (app.py:2143) and `submit_transcript` (app.py:2074) but no HTTP route reaches them; the dashboard has no text input
  - seeds: `c18`
- `s22` — `embodiment/http/guard.py:296-324,457,541-625 + daemon/app.py:~4047`: AssertionVerifier = Callable\[\[str\], AssertionResult\]; the default refuses every token; the refusal status is hard-coded 401 so a 503 needs a status field or code; main() builds Guard with no `assertion_verifier` — the single wiring point
  - seeds: `c19`
- `s23` — `pocketsync/pocketsync/web/access.py (326 lines) + tests/test_web_access.py (26 tests)`: stdlib-only RS256 via pow, kid-keyed JWKS cache with rate-limited refetch, negative cache and single-flight; reasons include unavailable (server maps to 503); itself cited from culture-rules `culture_rules`/auth/access.py, which mirrors culture-nodes internal/auth/verifier.go
  - seeds: `c19`
- `s24` — `tests/test_zero_deps.py`: a stdlib-only verifier adds no third-party import and needs no pin change; it must use urllib, not the httpx that coherence-cli brings in
  - seeds: `c19`
- `s25` — `embodiment/http/guard.py _check order`: Host, Origin, then the install secret, then the assertion when Host is public — a verified Access identity does not relax the secret check
  - seeds: `c20`
- `s26` — `pocketsync/pocketsync/web/chat.py:56-309 + docs/web-api.md`: retrieval is Atlas $vectorSearch (not portable); everything after it — budgeting, the quoted sources block, the system prompt, cite() marker validation, the zero-hit short-circuit — is portable; pocketsync has no dir=auto, Gwen's Transcript.tsx:28 does; the dashboard uses no innerHTML
  - seeds: `c21`
- `s27` — `README.md:184-191,239-258 + explain/catalog.py:62 + tests/test_release_docs.py:40-103 + cli/_commands/tunnel.py:74`: `NOT_YET` pins the phrase 'cloudflare access' in both the README and explain root; the tunnel default is agent.culture.dev; the progress file's recorded remote decision names agent.culture.dev too
  - seeds: `c22`
- `s28` — `pocketsync docs/web-api.md:63-98 + embodiment#89`: the reader, list and actions routes are plain Mongo reads in pocketsync; a pocketsync tool would be Gwen's first tool beyond memory, which #89 says is blocked on the operator's tool-boundary decisions
  - seeds: `c23`
- `s29` — `embodiment/cli/_commands/tunnel.py:1-48,183-252 + CLAUDE.md constraints`: tunnel prints cultureflare/cloudflared commands and never runs them; provisioning and cross-repo posts are the operator's acts
  - seeds: `c24`
- `s30` — `embodiment#92 items 3-4`: 24 records today; recall that lands costs about 1 s end-to-end; the 0.25 s deadline was never sized against a growing store; Hebrew recall depends on the d5 exact-substring fallback (app.py:2700-2735), hit-or-miss on morphology
  - seeds: `c25`
- `s31` — `gateway GET /capabilities (2026-10-23)`: role gemma2-embed: model google/embeddinggemma-2, sentence-transformers, 768-d (MRL 128/256/512/768), context 8192, ready=true, loaded=false, feasible=false, proxied=true, hosted on member orin
  - seeds: `c29`
- `s32` — `scratchpad probe_gemma2_embed.py via grant run (n=1 Hebrew triple)`: reachable through the gateway proxy despite lobes-cli#284; 158-189 ms per call; cos(query, related fact with different morphology)=0.891 vs cos(query, unrelated)=0.663 — one pair, not a measurement
  - seeds: `c29`
- `s33` — `eidetic/memory/embed.py:40,173-177,279`: eidetic picks the model from `EIDETIC_EMBED_MODEL` (default Qwen/Qwen3-Embedding-0.6B) and the URL from `EIDETIC_EMBED_URL`; its payload is {model, input} with no `prompt_name`
  - seeds: `c29`
- `s34` — `eidetic/memory/scoring.py:354-374`: approximate recall embeds the query plus every candidate record's text fresh at query time (one batch); nothing is persisted, so recall cost grows with the store
  - seeds: `c30`
- `s35` — `embodiment/memory.py:305 + daemon/app.py:697-699`: recall deadline 0.25 s, `recall_mode` hard-coded 'keyword', `top_k` 5; a ~160 ms query embed leaves ~90 ms for scoring before the deadline censors hits
  - seeds: `c30`
- `s36` — `gateway 400 on prompt_name 'Query' + pocketsync/enrich/gateway.py:185-191`: the lane lists BitextMining, Classification, Clustering, CodeRetrieval, Document, FactChecking, ..., Retrieval, Retrieval-document, Retrieval-query...; pocketsync's embed() documents Document or Query
  - seeds: `c31`
- `s37` — `CLAUDE.md rig table + tests/test_release_docs.py:40-49`: CLAUDE.md lists embedder Qwen3-Embedding-0.6B 'not ready' and recall lexical-only; `NOT_YET` pins 'semantic recall' in README and explain root
  - seeds: `c32`
- `s38` — `operator message 2026-10-23`: Gwen should also remember the newsletter culture-rules generates, sent to her by a rule on culture-rules — not only the pocketsync source
  - seeds: `c33`
- `s39` — `culture-rules docs/operations/newspaper.md:1-80 + docs/rules/newspaper/workflows/daily-newspaper.json`: daily at 05:00 Asia/Jerusalem: Brave web search per section, newspaper.filter, then llm.complete on cortex writes one bullet per result, each linking its source; render builds HTML + EPUB; delivered by email and to the xteink reader; no step writes a file or emits a Gwen-facing event today
  - seeds: `c34`
- `s40` — `culture-rules newspaper.py sections/filter docstring`: results are filtered only by must/`must_not` terms and site include/exclude lists; content comes from any https page the search returns
  - seeds: `c35`
- `s41` — `culture-rules culture_rules/node/actions/newspaper.py:109-110`: `DEFAULT_COUNT` 5 results per section, up to `MAX_SECTIONS` 20 sections and count 20 each — up to 400 items a day; at defaults with 5 sections, 25 a day, ~9,000 a year, against a 24-record store today
  - seeds: `c36`
- `s42` — `culture-rules culture_rules/actors/code.py:172-182,282-295`: machine.command runs with stdin=DEVNULL in a throwaway cwd; args are scalar strings bound into a fixed argv — a batch must already exist as a file at an absolute path
  - seeds: `c37`

## Open parks

- [unknown_nonblocking] whether Hebrew recall over distilled recording facts is useful at all is unmeasured: eidetic's \[a-z0-9\]+ tokeniser drops Hebrew, recall falls to the d5 exact-substring fallback, and #92 measured hit rates from 0 of 24 to 31 of 51 on the same store; semantic recall waits on lobes-cli#284
- [unknown_nonblocking] the text chat runs through the daemon's turn path, so it answers with senses (Gemma 26B, the speaker) where pocketchat answered with the 27B cortex; whether that is the right model for cited text answers is untested

## Resolved vagueness

- [unknown_blocking] pocketsync emits no recording.landed/deleted/merged outbound events today (pocketsync/events.py:92-93); until it does, culture-rules has no trigger to distil or forget, so end-to-end 'she remembers my recordings' is blocked on pocketsync and culture-rules#73 regardless of what embodiment ships — resolved: embodiment's acceptance uses synthetic batches; pocketsync's lifecycle events and culture-rules' producer side are sibling work, tracked as a follow-up (operator, 2026-10-23)
- [unknown_blocking] on 2026-10-23 the gateway reports senses (Gemma-4-26B, Gwen's speaker) ready=false feasible=false — 'not feasible on this machine', `role_infeasible` 404 — and cortex not ready; only worker (Qwen 35B, proxied to thor) answers chat. Until senses is served again Gwen cannot answer a turn on this rig, by voice or by the new text chat — resolved: senses is served from thor through the gateway proxy (the operator is deploying it, 2026-10-23); chat completions cross machines, only the realtime WebSocket must stay local and stt is local; the plan's live acceptance tasks wait until senses reports ready
