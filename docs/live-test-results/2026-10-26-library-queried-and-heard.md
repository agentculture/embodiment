# Library queried and heard - 2026-10-26

Plan task `t9` of `gwen-queries-and-reads-the-library` (embodiment#107).
Aggregates only: no library text, no query text, no item titles. The probe
scripts printed structure, counts and seconds.

## Setup

- Code: branch `feat/gwen-queries-the-library` (0.23.0). The pre-flight ran
  `0.23.0` before the review fixes; production ran `2a58a65` for the probes
  and the phone check, and the merged `e19be55` (0.23.0, #110) for the retests.
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

The operator's own words, on an iPhone (the browser was not named), against production:

| Check | Result |
| --- | --- |
| A Hebrew transcript read aloud, stopped, reloaded, Continue listening | pass ("I checked on transcript Hebrew and confirmed") |
| An English newspaper read aloud | **fail**, then pass after d2: it played audio that was not English. BlueTTS runs with `TTS_LANGUAGE=he` and passes a span with no Hebrew letters to the model as raw letters. Gwen now tags Latin-script text `<en>` (d2, `24ba287`); the operator heard tagged and mixed clips ("Tagged and mixed are perfect"), then the newspaper ("I can listen to it correctly now") |
| Pause reachable mid-article | **fail**: the dock sat after the last paragraph of a long article. Fixed by d3 (`c4d3f8b`): on a phone the dock is fixed to the screen; checked at 390 px in a browser against the real page, mid-article |
| Jump to a paragraph | missing; added by d3: a tap on a paragraph starts there (checked in the browser: a tap on paragraph 10 played paragraph 10) |
| Progress bar | **fail**: a finished paragraph's bar emptied before the next one began, and past the end the last paragraph read "Paused here" with its segment unfilled. Fixed by d3 |
| The item's title as its first paragraph | as designed: the library's text opens with the title as its own block; the operator heard it and kept it |
| A long one-sentence paragraph | **fail**: the voice stopped after "perception" and the reader went on. Every sentence's audio was capped at 10 s; the same paragraph measured 10.00 s cut on the pre-flight daemon, then 13.93 s whole after the fix (`23d0b01`). Retested below |
| After a refresh, the header and the dock | **fail**: the header was scrolled away and the dock floated above empty space; fixed by d5 (`910cbcc`). Retested on the merged ref: pass ("I don't see the issue with bottom place and missing header") |
| Pause then play, relisten | slow, then pass: the audio was made again on every play; d6 keeps the current, previous and next paragraphs' audio. The operator: "Caching works well, and no ring when relisten" (the ring waits 300 ms, d4) |
| Previous and Next, section edges included | pass: made ahead while a paragraph plays (d6, `91b486e`). The operator: "Previous and next cache works good!" |
| Loading feedback on a jump | added by d4: a turning ring on the play button, no words (the operator: "Loading ring on play is clear") |
| A long one-sentence paragraph, retested | pass ("Reads well to the end") |
| Transitions between sentences | **fail**: "transitions between sentences aren't great". Measured: sentences joined with 0-10 ms of silence and starting at full amplitude. Fixed by d7; measured after on the pre-flight daemon: 0.27 s quiet at each join, every clip starts and ends at sample 0. Retested: pass ("Transitions are far better. Very natural in the paragraph"), except a colon ("still passes fast"): a colon and a space now end a piece of speech with a pause. Retested: "Colon pause was good, maybe extra 50-100ms" and "100-200 ms" more after a period; then "Colon is perfect" at 0.30 s, and "a period [needs] a bit more weight. Maybe 100ms": 0.53 s after a full stop; retested on the merged ref: pass ("pause is right") |
| One paragraph per sentence (A) or in one tts call (B) | A kept. The operator, by ear on one synthetic paragraph: "Per sentence is better! The model didn't parse well the transition to the question. Otherwise, whole sentence was better." Made in 1.06 s (A) and 0.70 s (B) for about 20 s of audio; B is the alternative if the voice handles questions mid-paragraph |
| Tap-to-sound time, cold | a long paragraph: "maybe 6" seconds (the operator's estimate by ear, n = 1, not timed) |
| Choose section (0.23.2, `682ac74`, production) | pass ("confirmed choose section works") |
| Tap-to-sound time, warm | the same paragraph re-tapped: "immediate and smooth" (by ear, n = 1) |

c22's 5 s bar, judged on the warm figure as the plan says: **pass**. The cold
figure beside it, about 6 s, **misses** the bar; it matches the cold tts call
probed on the rig (6.59 s, c32). Both figures are estimates by ear, not timed.
The stop, reload and continue check was made on a Hebrew transcript, not on a
chapter of an EPUB; the EPUB was matched against `/text` by the probe only.

Server side, production's reading positions moved one paragraph per heard
report on both items read (paragraph 0 heard, cursor at paragraph 1).

## Not measured

- Hit rate of the library's retrieval: no question set was run against it.
  Its hybrid scores (about 0.011-0.033) do not compare with memory's 0.715.
- Title-only chunks (about 40 characters) come back as passages; Gwen's old
  index skipped fragments under 60 characters. Citations no longer anchor on
  them (`2a58a65`), but they still take a passage slot.
- Two devices playing the same item at once (follow-up v5).
