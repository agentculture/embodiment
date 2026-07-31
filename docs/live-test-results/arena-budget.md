# Arena series C — the token budget in the arena — results

**Task:** t24 · **Run:** 2026-07-31, 04:15–05:26 +03:00 · **Rig:** [`arena-budget-raw/capabilities.json`](arena-budget-raw/capabilities.json)
**Pre-registration:** [arena-budget-preregistration.md](arena-budget-preregistration.md)
(committed before the first measured match)

**12 matches, 12 completed, 0 aborted, 0 degraded, every match played its full
3 turns.** 4,242 seconds of serial wall clock. Raw records:
[`arena-budget-raw/`](arena-budget-raw/) — every match's report, its
per-completion trace, its league match log, its config preamble, the runner and
the runner's log.

`n = 3` per cell. **Every number below is n=3 on one rig with one model pair, on
one day.** No claim here is stated without its n, and none of them survives
generalisation past this rig.

## The rig and the model pair

Read from `GET /capabilities` before the first match and committed verbatim.

| Role | Model | Where | Context |
|---|---|---|---|
| cortex | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | **local** | 262144 |
| muse | `nvidia/Gemma-4-31B-IT-NVFP4` | proxied — `thor.tail0be7e0.ts.net:8000` | 262144 |

**The muse was OFF in all 12 matches.** Every config preamble in
`arena-budget-raw/` reads `"muse_model": null`, and no muse completion appears
in any trace. The muse row is here because it is the rig's declared pair, not
because one was dialled. `league_seat.py`'s `--muse` wiring **is** real —
`build_muse` constructs a `ThreadedMuseRunner` that `PresenceEngine(muse=…)`
consumes, unlike the challenge harnesses' dead flag — and it is verified live in
the [muse wiring check](#the-muse-wiring-check-verified-live-after-the-series)
below, run *after* the series so it could not touch it.

One OpenAI-compatible gateway at `localhost:8001`, bearer auth from
`COLLEAGUE_API_KEY` (a keyless request returns `401`).

### Colour, stated because the map is biased

**The seat played blue in all 12 matches**; league's own `bot` driver played
red. **No colour swap was performed and no blue-versus-red claim is made
anywhere in this document.** This cycle's identical-mind control measured
**90 vs 45** under fog between byte-identical policies, so the map's colour bias
is larger than anything this series could detect.

Every comparison below is *within* blue, at the same three seeds, against the
same deterministic house policy, so the bias is constant across cells and cannot
produce a cell-to-cell difference.

### Contention

None observed. The gateway returned `200` on all 141 completions; there were no
`503`s, no timeouts, and no retries. The operator confirms t23, t27 and t28 had
finished before this window. **No run in this series was discarded or repeated.**

## The design

| | `--max-tokens 2048` | `--max-tokens 16000` |
|---|---|---|
| **`--arm command`** | **C2** | **C16** |
| **`--arm resident`** | **R2** | **R16** |

2048 is `league_seat.DEFAULT_MAX_TOKENS`, the shipped default — **and the budget
the published [arena series B](arena-series.md) was actually measured at**,
because `arena_series.seat_argv` passes no `--max-tokens` and its config
preamble recorded temperature but not the cap.

## The headline

**The one drive in this series that exited `stopped` did so because it was cut
off twice, not because it stopped — and nothing in the loop's own record says
so.**

`C2-r1`, turn 0, at the shipped 2048 default:

| Surface | What it says |
|---|---|
| `drive.exit_reason` | `stopped` |
| `drive.summary` | `__COLLEAGUE_NO_RESULT_PRODUCED__` |
| `drive.tools` | `["note"]` |
| `orders.actions` | `[]` — **blue played its opening league turn with no orders** |
| `degradations` | `[]` — match level *and* turn level |
| league's cooperation score for the match | **100**, the joint-highest in the series |

Read from those six rows alone, this is a mind that thought about its opening
move and declined to make one. The `--trace-out` record says otherwise — 2 of
that drive's 4 completions came back like this:

```json
{"role": "cortex", "finish_reason": "length", "completion_tokens": 2048,
 "prompt_tokens": 1198, "content_chars": 0, "reasoning_chars": 4157,
 "tool_calls": [], "pid": 1947383}
```

Two turns, each burning the entire budget, each returning **zero content
characters and zero tool calls**, 4,157 and 4,626 characters of reasoning
discarded mid-thought. `LoopControls.max_continue_nudges` is 1, so the first was
nudged and the second ended the drive.

This is [embodiment#37](https://github.com/agentculture/embodiment/issues/37)
with a consequence attached. `ModelResponse` carries `content`, `reasoning`,
`tool_calls` and both token counts — **but not `finish_reason`** — so a
truncated turn and a deliberate one arrive at the loop as the same object. The
loop did the only thing it could. **No host on the documented seam could have
told these apart**, and this task could only do it by instrumenting the seam's
own HTTP layer.

**And league's own metric is blind to it too.** The lost turn cost blue nothing
in the cooperation score: `C2-r1` scored 100 while playing a turn with no
orders, because `discipline` is `1 − rejected/declared` and a turn that declares
nothing rejects nothing.

## The predictions, graded

| | prediction | verdict |
|---|---|---|
| **P1** | at 2048 every match truncates; at 16000 none does | **FALSIFIED** — 3 of 6 matches at 2048 truncated zero times |
| **P2** | a truncated turn is empty and no degradation names it | **CONFIRMED 5/5** |
| **P3** | the second truncation in a drive ends it | **CONFIRMED 24/24 drives** |
| **P4** | `turns_played` cannot see any of it | **CONFIRMED 12/12** |
| **P5** | the optional `message` is omitted | **FALSIFIED** — 20 of 36 seat-turns sent one |

### The whole series in one table

| cell | arm | budget | league turns | completions | **truncated** | drives | `stopped` | msg turns | empty-order turns | mean wall clock | completion tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **C2** | command | 2048 | 9 | 40 | **4** | 9 | **1** | 6/9 | **1** | 402.0s | 29,945 |
| **C16** | command | 16000 | 9 | 37 | 0 | 9 | 0 | 2/9 | 0 | 329.3s | 24,570 |
| **R2** | resident | 2048 | 9 | 43 | **1** | 3 | 0 | 3/9 | 0 | 391.0s | 28,734 |
| **R16** | resident | 16000 | 9 | 21 | 0 | 3 | 0 | 9/9 | 0 | 291.7s | 21,798 |

## P1 — FALSIFIED, and the falsification is the useful half

P1 was two claims joined by an "and". Only one survived.

| budget | completions | truncated | rate |
|---|---|---|---|
| 2048 | 83 | **5** | 6.0% |
| 16000 | 58 | **0** | 0.0% |

- **The 16000 half held**: zero truncations in 58 completions.
- **The 2048 half failed as written.** P1 said *at least one model turn per
  match*. Three of the six 2048 matches — `C2-r2`, `R2-r0`, `R2-r2` — truncated
  **zero** times. The decision rule says a single falsifying match falsifies the
  claim, so P1 is FALSIFIED and no amendment is applied.

The honest replacement, stated as the weaker claim it is: **truncation at 2048
is real but intermittent — 6.0% of completions, in 3 of 6 matches, n=3 per
cell.** The largest completion in the whole series was 3,191 tokens (`C16-r0`),
so 2048 sits inside the distribution rather than below it; whether a given turn
truncates depends on how much the model happens to think.

That is a worse property than "always truncates", not a better one. A cap that
fails half the time produces a record where most turns look fine and a few are
silently empty, which is exactly the shape that gets read as behaviour.

## P2 — CONFIRMED 5/5: a truncated turn is invisible

Every one of the five `length` completions carried **0 content characters and 0
tool calls**. Not one produced a partial answer the loop could have used.

**Total degradations recorded across the entire series: 0** — at the match level
and in the turn records alike. Nothing in either surface names truncation,
because nothing in the contract can.

This is worth stating against **C3** ("degradation must be observable to the
host; nothing degrades silently"). It is not a bug in the loop: the loop is
never told. It is a gap in `ModelResponse`, and t23 filed it. This series is the
second independent shape it has been measured in, and the first where it cost a
turn of play rather than a puzzle answer.

Note the match-level `degradations` field is trustworthy here: t19's defect
(`_play_command` returning no degradations) has since been fixed, and the
per-turn records agree with the match field at zero.

## P3 — CONFIRMED 24/24 drives

`max_continue_nudges = 1`, and `nudges` is cumulative across a drive rather than
consecutive, so the prediction was that the *second* no-tool turn ends it.

| | drives | exited `stopped` |
|---|---|---|
| drives with ≥2 truncated turns | **1** | **1** |
| drives with ≤1 truncated turn | **23** | **0** |

`C2-r0` is the case that makes this a real test rather than a tautology: it
truncated **twice in the same match**, but in two *different* drives (the
command arm gives every league turn its own process), one each. Both drives
finished normally. Same match, same budget, same number of truncations as
`C2-r1` — and no lost turn, because they did not land in the same drive.

**So the cost of a truncation is not per match, it is per drive**, and it lands
on the second one. A resident host, whose whole match is one drive, accumulates
nudges across every turn; a command host resets the count at each turn boundary.
`R2-r1` truncated once inside a single 11-completion match drive and finished; a
second truncation anywhere in that match would have ended the whole match, not
one turn.

That asymmetry was not pre-registered and is stated as a mechanism reading of
n=1 falsifying-drive, not as a measured effect.

## P4 — CONFIRMED 12/12, and this is what it means for series B

**Every match played all 3 turns**, including `C2-r1`, whose opening turn was
filed with an empty order set. league accepts `{"actions": []}` as a turn and
resolves it.

So `turns_played` — and any metric built on it — **cannot see this failure at
all.**

[Series B](arena-series.md) graded its muse factor on exactly that metric:

> | cell | RO | RM | CO | CM |
> |---|---|---|---|---|
> | turns completed | 9/9 | 9/9 | 9/9 | 9/9 |

It reported `INCONCLUSIVE` at a ceiling, which was the right call for the reason
it gave. This series adds a second reason: **the metric was structurally
incapable of registering the failure mode its own budget was producing.** A cell
that lost an opening turn to truncation and a cell that played perfectly both
score 9/9.

**This is not a re-scoring of series B.** That series is not re-run here and no
claim is made about which of its findings would change. What is established is
that its instrument was set to 2048, that 2048 truncates this cortex about 6% of
the time in this task shape, and that its headline completion metric could not
have detected it.

## P5 — FALSIFIED, and I should have known before predicting

P5 predicted blue's `communication` signal would be 0.0 in the majority of
matches and the `message` field absent from the majority of turns.

| | measured |
|---|---|
| seat-turns that sent a team message | **20 / 36** |
| matches with `communication == 0.0` | **3 / 12** |

Both halves fail. The Qwen cortex uses the optional field more often than not.

league scores it as `communication = min(1, 2 × message_turns / turns_played)`
at weight **0.20** of the cooperation score, so it is worth real points, and the
per-cell rates vary enormously at this n:

| cell | msg turns | `communication` per match |
|---|---|---|
| C2 | 6/9 | 0.6667, 1.0, 1.0 |
| C16 | 2/9 | 0.6667, 0.6667, 0.0 |
| R2 | 3/9 | 0.0, 0.0, 1.0 |
| R16 | 9/9 | 1.0, 1.0, 1.0 |

**No claim is made about that spread.** It was not pre-registered as a
comparison, n=3 per cell, and 9/9 against 2/9 on three matches is exactly the
size of swing three samples produce.

### The lapse, recorded because this directory exists for it

**[league-h2h.md](league-h2h.md) already answered P5, and I pre-registered it
without reading that document.** It measured the same cortex sending a team
message on **9 of 12** seat-turns, and it made the message rate the entire basis
of its L1 separation. My prediction contradicted a committed result in the same
directory, written by the same cycle, four documents up the README table.

Writing a prediction down first is only worth something if you first read what
is already known. I got the discipline right and the homework wrong. The
pre-registration is left unamended and P5 is published FALSIFIED.

### One number that is genuinely new — and is not the control h2h wanted

`league-h2h.md` closes its message-rate section by noting that its `mixed` and
`full-qwen` arms share a cortex and differ only in the muse, that they sent
messages at 4/12 and 9/12, and that **a muse-off control does not exist there**.

This series is muse-off, same cortex: **20 of 36 seat-turns (56%)**.

That is *not* that control, and must not be read as one. Different harness
(`league_seat` vs `league_h2h`), different opponent (a deterministic house bot
vs a model-driven seat), different colour assignment, different turn budget. It
is one more muse-off datum on the same cortex, recorded so the next person
asking has it, and nothing more.

## Post-hoc observations, labelled as post-hoc

None of these were pre-registered. None is graded. They are recorded because the
data is committed and someone will otherwise recompute them.

### The smaller budget was slower and cost more tokens

| budget | mean wall clock | total completion tokens | completions |
|---|---|---|---|
| 2048 | 396.5s | 58,679 | 83 |
| 16000 | 310.5s | 46,368 | 58 |

The direction agrees in both arms (C2 > C16, R2 > R16), but the per-match ranges
overlap heavily (C2 260–492s, C16 268–437s) and n is 3. **No effect is claimed.**

The token gap has an arithmetic mechanism, which is why it is worth writing
down: **5 truncated turns × 2048 tokens = 10,240 completion tokens that were
generated, paid for and discarded — 83% of the 12,311-token gap between the two
budgets.** A truncated turn is not a cheap turn; it is a full-price turn whose
output is thrown away, and the nudge turn that follows re-reasons from the top.

### The outcome axis carries no information at 3 turns

Every match ended `0–0` with `winner: null`. On `skirmish-1` the units start far
from every control point and 3 turns is not enough to reach one. **Do not read
12 draws as balance** — the outcome score was never in play. Only the
cooperation axis moved (77–100).

### Fewer, larger calls

`R16-r1` played a complete 3-turn match in **3** model completions; `R2-r0`
needed 17. The 16000 cells used 58 completions to the 2048 cells' 83 for the
same 36 league turns.

## The 4 `EMBODIMENT_LIVE_ARENA` tests — 4 of 4 PASSED

These are the tests task t23 reported **ABSENT** and left to this task. Run
2026-07-31 04:58 with `EMBODIMENT_LIVE_ARENA=1` against the **real `league`
CLI** (`/home/spark/.local/bin/league`). Raw pytest output:
[`arena-budget-raw/live-arena-tests.txt`](arena-budget-raw/live-arena-tests.txt).

| Test | Result | Duration |
|---|---|---|
| `test_league_seat.py::TestLiveArena::test_both_arms_play_the_real_arena_and_it_records_the_residency` | PASSED | 4.45s |
| `test_league_seat.py::TestLiveArena::test_the_real_arena_carries_the_directive_across_processes` | PASSED | 2.63s |
| `test_league_seat.py::TestLiveArena::test_league_can_drive_the_seat_as_its_own_command_driver` | PASSED | 0.60s |
| `test_arena_series.py::TestLiveArenaSeries::test_the_runner_drives_the_real_seat` | PASSED | 3.82s |

4 passed in 11.64s. No test was retried and no run discarded.

**Read what they assert, not what the green implies.** These play the real arena
with a **scripted mind** — no model, no network. They prove league's own CLI
answers this host's calls, that league's match-log header records the residency
the arm claims, and that league can drive the seat as its own `command` driver.
They are silent on anything a model does, which is why the series above exists.

With the rig off, the full suite reports **3072 passed, 17 skipped**; these 4
are the `EMBODIMENT_LIVE_ARENA` skips, and the other 13 are
`EMBODIMENT_LIVE_RIG`, which t23 ran.

## The muse wiring check, verified live after the series

Run **after** every measured match, so it could not affect one. One command-arm
match, 1 turn, `--muse --max-tokens 16000`, 2m24s. Report and trace:
[`arena-budget-raw/muse-wiring-smoke.json`](arena-budget-raw/muse-wiring-smoke.json),
[`arena-budget-raw/trace-muse-wiring-smoke.jsonl`](arena-budget-raw/trace-muse-wiring-smoke.jsonl).

The check exists because t23 found `--muse` on all three challenge harnesses to
be a **dead flag** that wrote a muse model into the config preamble for a run in
which no muse was ever dialled. Asserting from the source that `league_seat.py`
is different is not the same as watching it dial.

**It dials.** The trace records **4 completions against
`nvidia/Gemma-4-31B-IT-NVFP4`**, interleaved with the cortex's 5, each
`finish_reason: stop` at 49–81 completion tokens against a 512 cap:

| | measured |
|---|---|
| muse completions on the wire | **4**, all to the Gemma muse |
| `sessions_started` / `sessions_completed` | 4 / 4 |
| `insights_delivered` | **4**, all kind `step` |
| dropped stale / late / overflow / superseded | **0 / 0 / 0 / 0** |
| `terminal_drains` / `insights_delivered_terminal` | 1 / 1 |
| `tool_rounds` | **0** |
| degradations | **0** |

Two things follow, both narrow.

- **`--muse` on `league_seat.py` is not the challenge harnesses' dead flag.**
  A muse recorded in one of this host's config preambles is a muse that ran.
- **`tool_rounds: 0` replicates t23's finding** that t26's muse tool bench is
  unwired in every checked-in host, `league_seat.py` included.

The **zero late drops** are worth one careful sentence. Series B recorded 14
`muse-insight-late` degradations — "undrained when the runner closed" — at
roughly **1.11 per drive close** in the command arm. This drive closed once and
dropped none, with the terminal drain firing and delivering. That is consistent
with t25's terminal drain having closed the race, and **one drive is not a
measurement of a rate**. It is a wiring check that happens not to have
reproduced the defect, not evidence the defect is gone.

**No measured match in this series carried a muse**, so nothing here changes a
single number above.

## Everything that did not run, and why

Listed so absence is never inferred from silence.

| Lane | Status | Why |
|---|---|---|
| The muse factor as a measured arm | **ABSENT — deliberately not attempted** | Wall clock, and [series B](arena-series.md) already ran the muse 2×2 to an `INCONCLUSIVE` ceiling. Recording a muse for a run where none was dialled is the defect t23 found; every preamble here reads `"muse_model": null`. |
| A colour swap / red arm | **ABSENT — not attempted** | Single-colour by design. The map's bias (90 vs 45 between identical minds) is larger than this series' resolution, so **no cross-colour claim is made**. |
| Budgets between 2048 and 16000 | **ABSENT — not attempted** | Two points, not a curve. Nothing here identifies a threshold; the largest successful completion observed was 3,191 tokens, which is a lower bound on "enough", not a recommendation. |
| Budgets above 16000 | **ABSENT — not attempted** | t23 exhausted 16000 twice on puzzle problems. Zero truncation at 16000 **in this task shape** is not evidence 16000 is sufficient in general. |
| Re-running series B at 16000 | **ABSENT — deliberately not attempted** | This series measures what the budget does. It does not re-score series B, and makes no claim about which of that document's findings would change. |
| More than 3 league turns | **ABSENT — not attempted** | Wall clock. It also means the outcome axis never engaged; see the post-hoc note. |
| `arena_series.py` as the runner | **ABSENT — could not be used** | It passes neither `--max-tokens` nor `--trace-out`, so it cannot vary or observe the variable this series is about. Driven directly through `league_seat.py`'s CLI instead, by the committed [`run-series.sh`](arena-budget-raw/run-series.sh). |

## A guard from another lane that this change tripped, and what I did about it

Stated in its own section because **I modified a test belonging to task t27's
lane**, and a results document that buries that has no business being read.

`tests/test_league_h2h.py::TestBothTeamsAreModelSeats::test_league_seat_py_is_not_modified_by_this_lane`
asserted its claim by running `git diff --name-only main...HEAD` over the
**whole branch** and requiring `examples/league_seat.py` to appear nowhere in
it. Adding `--trace-out` to the seat turned the suite red: **3071 passed, 1
failed**.

The guard's name says *"by this lane"*. Its implementation says *"by anybody, on
any branch"* — and on this repo those are wildly different statements, because
**the entire muse cycle lives on one unmerged branch**, so `main...HEAD` is every
file every task in the cycle has touched. Any lane with a legitimate reason to
edit the seat fails a test about the head-to-head lane. It failed here while
`examples/league_h2h.py` had not been touched at all.

**What I changed:** the guard now walks the commits that modified
`examples/league_h2h.py` and asserts none of them also modified
`examples/league_seat.py`. Deviation `d10`'s guarantee — the h2h lane imports
the seat rather than forking or bending it — is unchanged, and is now enforced
against the only lane that could actually break it. The two structural tests
beside it (`imported-not-copied`, and the scripted rival never reached) are
untouched.

It is **not vacuous on this branch**: it inspects 6 real h2h commits and finds
none of them touch the seat.

**It cannot have affected any number in this document.** It is a test about a
different lane's commit hygiene, it was discovered *after* the last measured
match, and it reads git history rather than anything the series produced. The
suite is back to its **3072 passed, 17 skipped** baseline.

## Defects found in the record, not in the code

Both are about artifacts this directory maintains so a later reader cannot be
misled about what was configured.

1. **The published series B has no token budget in its record.**
   `arena_series.seat_argv` passes no `--max-tokens`, so all 24 matches ran at
   2048, and [`arena-series-config.json`](arena-series-config.json) records
   `cortex_temperature` but no cap. Nothing in that document says what budget
   its numbers were measured through. Fixed forward only: `league_seat.py`'s
   preamble now writes `cortex_max_tokens`, `muse_max_tokens`, `max_steps` and
   `trace_out`, so every config in `arena-budget-raw/` states its own budget.

2. **`max_turns` means two different things in two committed config artifacts.**
   `challenge_config.write_config_preamble` documents `max_turns` as a *muse*
   control. `league_seat.write_preamble` passes `args.muse_max_turns` into it
   (so this series' configs read `"max_turns": 2`), while `arena_series.py`
   passes the *match* turn limit (so `arena-series-config.json` reads
   `"max_turns": 3`, meaning league turns). Same key, same directory, two
   meanings. Reported, not changed — renaming a canonical config field would
   rewrite what an already-committed artifact claims.

## What this series cannot show

Restated from the pre-registration, unchanged:

- **Nothing about model quality, and nothing about winning.** One task shape,
  one deterministic house rival, one scenario family, one colour, n=3. Every
  match drew 0–0.
- **No generalisation past this rig.** One cortex, one machine, one day.
- **Not that 16000 is sufficient in general.** Zero truncation in 58
  completions here; t23 exhausted the same budget twice elsewhere.
- **Not a re-scoring of series B.**
