# Delivery Summary — Gwen queries and reads the library

plan: `gwen-queries-and-reads-the-library` · run: `partial` · date: `2026-10-25`
baseline: `devague summary skeleton`

## Intent

> Gwen asks culture-library for passages instead of keeping her own copy of it, and reads any library document aloud, by page or by paragraph, remembering where she stopped so the operator can say continue and hear the rest

After: `search_library` and its siblings answer from culture-library's passages; Gwen holds no library index and runs no library sync; on the page the operator says or taps read for an item, hears it paragraph by paragraph on that device's speakers, stops at any point, and later (from any device) continues at the first paragraph not fully heard

The run is `partial` only because `t9`'s phone check is still waiting on the
operator; every other task merged.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — library/retrieve.py: the one POST to /api/rag/retrieve, bounded, pinned, counted
- `t2` — library/document.py: an item's text as sections and paragraphs, from GET /api/library/{id}/text
- `t3` — daemon/reading.py: reading positions (0600) and the next/heard state machine
- `t4` — web: read aloud on this device (reader listen mode, dock, paragraph-synced progress, continue)
- `t5` — tools: `search_library` on retrieve, `read_item` and `fetch_source` on /text
- `t6` — daemon: retire the library sync and index; catalog, search and the reader routes go live
- `t7` — daemon: the reading routes behind the guard, and status.reading
- `t8` — docs and release 0.23.0: the library is queried, not copied; documents can be heard
- `t9` — live acceptance on the rig, published as measured

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `embodiment/library/retrieve.py` (`c3ed2a2`, `kind` sent in the body after a send-back `3ce6a37`); merged `6a9fdb3` |
| `t2` | delivered | `embodiment/library/document.py` + `LibraryClient.fetch_text` (`1b3f32b`); merged `09a0c9d` |
| `t3` | delivered | `embodiment/daemon/reading.py` (`4026432`), seek re-pin per `d1` (`acf169c`); merged `972bd50` |
| `t4` | delivered, one control short | Reader dock, Continue listening, Read aloud card, paragraph-synced progress (`fe22ad2`); the mockup's Choose section control was not built; merged `705ccb9` |
| `t5` | delivered | retrieve-backed `search_library`/`read_passage`, `/text`-backed `read_item`/`fetch_source` (`12cddcf`); merged `c30a149` |
| `t6` | delivered | `library/sync.py` deleted, `retire_library_index`, catalog/search/reader live (`6874d7e`); merged `dd23476` |
| `t7` | delivered | `/api/reading*` behind the guard, `status.reading`, Stats (`9cbed7d`); merged `653c37f` |
| `t8` | delivered | README, CLAUDE.md, `explain` catalog, CHANGELOG 0.23.0 (`a65a8e6`); merged `a9c5235` |
| `t9` | partial | pre-flight and production probes measured and written up in `docs/live-test-results/2026-10-26-library-queried-and-heard.md`; the operator's phone check (obligation `o9`) is not yet done |

## Mid-work Decisions

- `d1` — an explicit seek (GET /api/reading/{id}/next with section/paragraph: the dock's previous/next paragraph and Choose section) re-pins the cursor to the sought unit, so heard reports for it advance the position; stale reports for any other position are still refused — as built (criterion 2, quoted verbatim), a seek only previews: every heard report after a seek is refused and continue returns to the old place, which breaks the approved mockup's previous/next/choose-section controls; decider 0.68 for re-pin (choice), 0.62 that it amends the exact-next rule
- The document cache went from 4 to 12 items (`93f4152`): the pre-flight saw 20 refetches of a 752 KB book when a search ran during a read. No deviation record covers this; it changes no contract.
- Review fixes after the colleague review (`2a58a65`): the retrieve AST pin is alias-proof; a passage under 40 characters no longer anchors a citation (it opens the item at its start); resume continues at the saved cursor instead of replaying a heard paragraph. No deviation record covers these; they tighten behavior already promised.
- Two review points were left as they are: surfacing a reset when an item's sha256 changes mid-read, and refusing a symlink when the old index is removed (`unlink` removes only the link).
- The production daemon moved to systemd units (`embodiment-gwen.service`, `cloudflared-gwen.service`) after gwen.culture.dev was found down after a reboot; Docker is embodiment#108. This is operations, not code in this branch.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t3` (`d1`) | as built (criterion 2, quoted verbatim), a seek only previews: every heard report after a seek is refused and continue returns to the old place, which breaks the approved mockup's previous/next/choose-section controls; decider 0.68 for re-pin (choice), 0.62 that it amends the exact-next rule | `acceptable` |
| `t4` | the Choose section control from the approved mockup was not built; seeking by section exists only as the route's `section`/`paragraph` parameters (delta `b3`) | `needs-follow-up` |
| `t9` | the operator's phone check (playback on the device, Continue listening after a reload, tap-to-sound time, iOS Safari) is not yet done | `needs-follow-up` |

## Evidence

- tests: the full suite at `2a58a65` — 4478 passed, 1 skipped; web (vitest) 503 passed; `tsc` and the build clean
- tests: the obligation set at `2a58a65` (`validate107-tests.sh`, 2026-10-26T03:05:31Z) — 128 passed, including:
  - `tests/test_library_retrieve.py::test_module_issues_exactly_one_post_to_the_literal_path` — pass
  - `tests/test_library_retrieve.py::test_todo_and_discord_message_dropped` — pass
  - `tests/test_library_document.py::test_epub_kept_in_order_empty_paragraphs_skipped_bytes_exact` — pass
  - `tests/test_reading.py::test_heard_must_be_exact_next_and_never_backwards` — pass
  - `tests/test_reading.py::test_a_seek_repins_the_cursor_to_the_sought_unit` — pass
  - `tests/test_daemon_library.py::TestNoIndexNoSync::test_the_worker_lists_the_catalog_and_never_starts_a_sync` — pass
  - `tests/test_daemon_library.py::TestTheRetiredIndexIsRemoved::test_first_start_deletes_it_once_and_later_starts_do_nothing` — pass
  - `tests/test_daemon_reading.py::TestRefusals::test_next_and_heard_need_the_secret_and_a_refused_heard_leaves_the_file` — pass
- live: `docs/live-test-results/2026-10-26-library-queried-and-heard.md` — every probe row passed or is reported as measured
- lint: `black --check`, `isort --check-only`, `flake8`, `bandit -c pyproject.toml`, `markdownlint-cli2` — clean
- validate-delivery records: obligations `o1`-`o9`, evidence `e1`-`e12` (all pass), deltas `b1`-`b3`; all `llm`-origin and proposed
- commits: `74f51f1..2a58a65` on `feat/gwen-queries-the-library`, stacked on `feat/gwen-reads-library-todos` (`8425c06`)
- PRs / issues: embodiment#107; follow-ups embodiment#108, culture-rules#99

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| search is one POST to `/api/rag/retrieve`; Gwen keeps no library index and runs no sync | high | `e1`, `e2` (tests at `2a58a65`); `e12` (600 s live window, 0 indexing GETs) |
| to-do and Discord passages never come back from search | high | `e3`; 5 live searches, 0 such passages |
| a read uses the library's `/text` paragraphs in order | high | `e4`; `e9` (live: 3943 of 3943, same sha256) |
| the cursor moves only on a heard report for the next unit, or on a seek (`d1`) | high | `e5`, `e6` |
| the reading routes are guarded | high | `e7`; live unauthenticated heard refused 401 |
| the old index is deleted once and never again | high | `e8`; `e10` (production ledger entry, file absent) |
| a new library item is searchable with no Gwen action | medium | `e11` (n = 1, about 9 s, bounded by 10 s polling) |
| the page's TS reading client is tested | medium | `web/src/api/reading.test.ts`; lapse `l1` (proposed): its tests were not seen failing first; mutation check after the fact (break `/heard`: 1 of 7 fails) |
| a read plays on the phone and Continue listening resumes the stopped paragraph | unverified | obligation `o9` has no evidence — not claimed done |
| library retrieval hit rate | unverified | not measured; hybrid scores 0.011-0.033 do not compare with memory's 0.715 |

Lapse ledger evidence:

pending approval (not yet evidence): `l1`

## Remaining Work / Follow-up

- `t9` / `o9` — the operator's phone check on gwen.culture.dev; then fill the "On the phone" section of the live-test results and refresh this summary. Blocking for the PR.
- Choose section control (`b3`) — build it on the page, or confirm the route-only seek is enough. Operator's call.
- Proposed records to adjudicate: obligations `o1`-`o9`, evidence `e1`-`e12`, deltas `b1`-`b3`, lapse `l1`.
- A culture-library issue for title-only chunks (about 40 characters) returned as passages — to draft and ask before posting.
- Follow-ups already named: voice commands for reading (v4, with realtime Live); two devices reading one item (v5); embodiment#106; Docker (embodiment#108); transcript segment times (culture-rules#99); deleting the dead library code in `library/index.py` and `library/extract.py`.
- The `feat/gwen-reads-library-todos` PR (embodiment#105) this branch stacks on still awaits the operator's go-ahead.
