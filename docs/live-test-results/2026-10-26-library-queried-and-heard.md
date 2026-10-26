# Library queried and heard - 2026-10-26

Plan task `t9` of `gwen-queries-and-reads-the-library` (embodiment#107).
Aggregates only: no library text, no query text, no item titles. The probe
scripts printed structure, counts and seconds.

## Setup

- Code: branch `feat/gwen-queries-the-library` (0.23.0). The pre-flight ran
  `0.23.0` before the review fixes; production now runs `2a58a65`.
- Pre-flight daemon: the same code on port 8824 with a scratch state
  directory, so the live store was never touched by a probe.
- Production: `embodiment-gwen.service` on gwen.culture.dev, state in the
  default `~/.local/state/embodiment`.
- Library: culture-library on 127.0.0.1:8796, 68 items at the start (36
  documents, 15 newspapers, 11 books, 3 transcripts, 2 to-do items, 1 Discord
  message).

## Results

| Check | Result | Measured |
| --- | --- | --- |
| No indexing (h1) | pass | a 600 s window: 30 library GETs = 10 catalog listings + 20 to-do checks; 0 other GETs, 0 `/file` GETs |
| Old index retired once (q5) | pass | production's first start wrote one `app-library-index-retired` entry; `index.json` absent; the conversations index kept |
| Search without a kind drops to-dos | pass | 5 searches, 0 to-do or Discord passages |
| Search latency (hybrid, through the daemon) | measured | n = 5: min 0.78 s, median 1.08 s, max 1.46 s |
| The reader matches `/text`, EPUB | pass | item of kind book: 3943 of 3943 paragraphs, equal in order, same sha256 |
| The reader matches `/text`, Markdown | pass | transcript: 4 of 4 paragraphs, equal in order |
| Next unit, then speak | measured | n = 5 units of 39-698 characters: next under 0.01 s, the speak call 0.19-2.00 s, for about 1.8-31 s of audio |
| Heard moves the cursor; a stale heard does not | pass | `moved=True`, then `moved=False` |
| A seek re-pins the cursor (d1) | pass | seek to section 2 paragraph 0, then continue serves section 2 paragraph 0 |
| An unauthenticated heard is refused | pass | 401 |
| A new library item reaches search with no Gwen-side action (h13) | pass | one newspaper: created 03:01:55Z, in the catalog by 03:02:03Z (10 s polling), found by Gwen's search 0.7 s after that: about 9 s end to end, n = 1 |
| Document cache (pre-flight finding) | fixed | 4 documents caused 20 refetches of a 752 KB book while searching mid-read; raised to 12 (`93f4152`) |

## On the phone (the operator)

Pending: read a chapter aloud on gwen.culture.dev, stop mid-paragraph,
reload, tap Continue listening; whether the same paragraph replays from its
start, and the tap-to-sound time cold and warm; whether iOS Safari plays it.

## Not measured

- Hit rate of the library's retrieval: no question set was run against it.
  Its hybrid scores (about 0.011-0.033) do not compare with memory's 0.715.
- Title-only chunks (about 40 characters) come back as passages; Gwen's old
  index skipped fragments under 60 characters. Citations no longer anchor on
  them (`2a58a65`), but they still take a passage slot.
- Two devices playing the same item at once (follow-up v5).
