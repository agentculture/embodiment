# The live-suite gate — every `EMBODIMENT_LIVE_RIG` test and the challenge harnesses

Task **t23** of the muse cycle: the live-evidence gate the operator added
mid-cycle (deviation `d1`). Nothing in this cycle is declared done until the
standing live surfaces have actually been *run*. Its whole value is that it was
run rather than assumed.

Run **2026-07-31**, 00:27–03:30 +03:00, on the reference rig, with task **t27**
using the same rig concurrently for part of that window.

Everything below is a real run on that date. Where a lane could not run it is
marked **ABSENT** in its own row, never implied by omission.

## The rig, as the gateway reported it

Read from `GET /capabilities` at the start of the session and committed
verbatim as [`live-suite-raw/capabilities.json`](live-suite-raw/capabilities.json).

| Role | Model | Where | Context |
|---|---|---|---|
| cortex | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | **local** | 262144 |
| senses | `coolthor/gemma-4-12B-it-NVFP4A16` | proxied — `orin.tail0be7e0.ts.net:8000` | 32768 |
| muse | `nvidia/Gemma-4-31B-IT-NVFP4` | proxied — `thor.tail0be7e0.ts.net:8000` | 262144 |

One OpenAI-compatible gateway at `localhost:8001`, bearer auth from
`COLLEAGUE_API_KEY`. Only the cortex is local, so only cortex work contends for
this box.

## Lane 1 — the `EMBODIMENT_LIVE_RIG` tests: 13 of 13 ran, 13 passed

The gate names them by their skip reason. With the rig off, `uv run pytest -n
auto -q` reports **2972 passed, 17 skipped**; 13 of those 17 skips are
`EMBODIMENT_LIVE_RIG` and 4 are `EMBODIMENT_LIVE_ARENA`.

| Test | Result | Batch wall-clock |
|---|---|---|
| `test_workspace.py::TestTheLiveSandboxProbe::test_a_live_workspace_reaches_neither_the_network_nor_the_repo` | PASSED | 18.5s (with 17 hermetic siblings) |
| `test_workspace.py::TestTheLiveTeardown::test_a_live_close_removes_the_container` | PASSED | ″ |
| `test_workspace.py::TestTheLiveTeardown::test_a_live_job_in_flight_leaves_a_workspace_the_record_can_reap` | PASSED | ″ |
| `test_muse_challenge.py::TestLiveMuseChallenge::test_the_live_muse_answers_this_boundary_at_all` | PASSED | 37.6s (2 tests) |
| `test_muse_challenge.py::TestLiveMuseChallenge::test_the_live_verdict_is_produced_by_the_verified_grader` | PASSED | ″ |
| `test_association_work.py::TestLiveAssociationWork::test_the_reflective_axis_reaches_a_real_endpoint` | PASSED | 311.1s (2 tests) |
| `test_association_work.py::TestLiveAssociationWork::test_the_executive_axis_reaches_a_real_endpoint` | PASSED | ″ |
| `test_demo_greenhouse.py::TestLiveRig::test_two_live_runs_carry_the_card_across` | PASSED | 387.8s (6 tests) |
| `test_demo_greenhouse.py::TestLiveRig::test_the_muse_is_a_second_mind_only_when_asked_for` | PASSED | ″ |
| `test_demo_greenhouse.py::TestLivePerception::test_live_perception_preserves_verbatim_original` | PASSED | ″ |
| `test_demo_greenhouse.py::TestLivePerception::test_dead_endpoint_degrades_not_raises` | PASSED | ″ |
| `test_echo_probe.py::TestLiveMemoryEchoChamber::test_a_live_run_surfaces_the_record_and_produces_a_legible_verdict` | PASSED | ″ |
| `test_echo_probe_workspace.py::TestLiveTheWorkspaceReallyComputesTheWrongNumber::test_a_real_docker_workspace_prints_the_planted_number` | PASSED | ″ |

No test was retried, and no run was discarded. Raw pytest output is committed
under [`live-suite-raw/`](live-suite-raw/).

Worth stating plainly, because a green column invites the wrong reading: these
tests were written to assert **what the harness controls** — that a boundary
reached a real endpoint, that the session terminated, that the committed grader
produced the verdict — and deliberately *not* that a model answered well. Their
passing means the seams are live and honest. It is not a quality measurement.

### ABSENT from this lane

- **`EMBODIMENT_LIVE_ARENA` (4 tests)** — `test_league_seat.py` (3) and
  `test_arena_series.py` (1). Not run: they belong to task **t24**, not t23.
  Deliberately skipped, reported here so the 17-skip total reconciles.

## Lane 2 — the challenge harnesses

### `challenge_subset.py` — 3 of 3 CORRECT

n=3, temperature 0.3, `max_steps=14`, `max_tokens=16000`. Config preamble
written before the first dial:
[`challenge-subset-config.json`](challenge-subset-config.json). Raw
per-completion transcript:
[`challenge-subset-trace.json`](challenge-subset-trace.json). Graded results:
[`challenge-subset.json`](challenge-subset.json).

| Run | Verdict | Answer | Exit | Model turns | `finish_reason` | Degradations |
|---|---|---|---|---|---|---|
| 1 | CORRECT | 76 | `finished` | 1 | `tool_calls` | none |
| 2 | CORRECT | 76 | `finished` | 1 | `tool_calls` | none |
| 3 | CORRECT | 76 | `finished` | 1 | `tool_calls` | none |

The planted trap (72) was not taken in any run. No run touched
`check_subset` or `count_nonconsecutive` — the cortex solved the problem inside
one reasoning block and called `finish` directly, so the bench's arithmetic
surface went entirely unused. 12m22s for three runs, ≈4m07s per single turn.

### `challenge_register.py` — first attempt ABORTED by the gateway

n=2, `max_tokens=16000`. **The run died after 41m43s on `HTTP 503 Service
Unavailable`** from the gateway, mid-way through the *first* run. The gateway
answered `200` again two minutes later, and task **t27** was using the rig
concurrently, so this is recorded as an **infrastructure event — contention,
not a measurement**, exactly as the rig guidance requires.

What makes it worth its own section is what it cost. `run()` correctly refused
to swallow the failure: `classify_degradable` returns `None` for a 503, so
`_classify_complete_error` re-raised and `run()` wrapped it in `LoopAborted`.
That is the designed abort path and the loop behaved correctly. The **harness**
did not: it wrote its results and its transcript only after the last run
returned, so an uncaught exception discarded **every turn already paid for**.
41 minutes of live cortex work produced a 0-byte results file and no transcript
at all.

### `challenge_register.py` — the retry: WRONG, and the reason is the instrument

Retried at **n=1** (reduced from 2 to bound wall-clock after the 41-minute
loss), same `max_tokens=16000`. It completed in 25m57s. The retry is recorded
as a retry, per the rig guidance. Transcript:
[`challenge-register-trace.json`](challenge-register-trace.json).

| | Value |
|---|---|
| Verdict | **WRONG** |
| Answer | `__COLLEAGUE_NO_RESULT_PRODUCED__` (the loop's sentinel — no answer was ever submitted) |
| Expected | `initial=0101 order=CBEAD` |
| Exit reason | `stopped` |
| Model turns | 4 |
| `finish_reason` per turn | `tool_calls`, **`length`**, **`length`**, `tool_calls` |
| Degradations recorded | **none** |

**Read the `finish_reason` column before reading the verdict.** Turns 2 and 3
each consumed the *entire* budget and returned nothing:

| Turn | `finish_reason` | completion tokens | reasoning chars | content | tool calls |
|---|---|---|---|---|---|
| 1 | `tool_calls` | 1514 | 3753 | — | 5 |
| 2 | **`length`** | **16000 / 16000** | 33491 | empty | none |
| 3 | **`length`** | **16000 / 16000** | 29994 | empty | none |
| 4 | `tool_calls` | 8185 | 14869 | empty | 1 |

So **`max_tokens=16000` is still not enough for this model on this problem.**
The corrected budget fixed the subset problem — where the worst run needed 7375
— and the register problem blows straight through twice, mid-thought, with over
30,000 characters of reasoning and nothing to show for it.

This run is *not* evidence that the cortex cannot solve the Corrupted Register.
It is evidence that it was **cut off twice before it could answer**. Written up
without the `finish_reason` column, this would have read as "the cortex
abandoned the protocol and produced no result" — a behavioural claim, and a
false one. That is the same error [designed-problem.md](designed-problem.md)
made and had to correct, reproduced here under controlled observation.

### The gap this exposes: a truncated turn is invisible to the host

`degradations: []`. Two turns burned their whole budget and produced nothing,
and the loop recorded **no degradation at all**.

That is not a bug in the loop — it is a **contract gap**, and it is worth
naming against `C3` ("degradation must be observable to the host; nothing
degrades silently"). `embodiment.contract.ModelResponse` carries `content`,
`reasoning`, `tool_calls`, `prompt_tokens` and `completion_tokens` — **but not
`finish_reason`**. A truncated turn and a turn where the model chose to say
nothing arrive at the loop as the same object: empty content, no tool calls.
The loop cannot distinguish them because the seam does not carry the
distinction, so it does the only thing it can and treats the turn as empty,
which is what produces `exit=stopped`.

The consequence for every host built on the documented seam: **`exit=stopped`
is ambiguous, and no host can disambiguate it without bypassing
`ModelResponse`.** This task could only see it by instrumenting the harness's
own HTTP layer. [workspace-echo-chamber.md](workspace-echo-chamber.md) already
suspected as much — it noted `exit=stopped` on its problem "was partly the
token cap" — and this is that suspicion measured.

Filed for the operator to adjudicate; it is a contract change, not a
harness fix, and therefore not this task's to make.

## Lane 3 — this cycle's changes, running together for the first time

`examples/proof.py --muse --identity Gwen --max-tokens 16000`, one drive,
4m27s. It is the only checked-in host that wires `append_guidance`, so it is
the only place the cycle's terminal drain, delivery record and guidance relay
can be observed against a real model at once. Report:
[`proof-muse.json`](proof-muse.json).

The drive itself: `exit_reason: finished`, 4 model turns, 14 tool calls, zero
degradations, and a **valid induction proof** of `Σ k·k! = (n+1)! − 1` with
`compare` agreeing 3 of 3.

| Claim under test | Live result |
|---|---|
| **t4 / t25** — the terminal drain fires at drive end on *every* exit reason, including a clean `finish` | **CONFIRMED.** `terminal_drains: 1`, `insights_delivered_terminal: 1` on an `exit_reason: finished` drive. Before t25 this path produced no terminal boundary at all. |
| **t25** — a checked-in host wires `append_guidance` so counsel reaches the cortex | **CONFIRMED, with the predicted residue measured.** 4 lines appended, **3 reached the cortex**, 1 undelivered. |
| **t26** — a tools bench is reachable through `ThreadedMuseRunner` inside a live drive | **ABSENT — the seam is unwired in every checked-in host.** `tool_rounds: 0`. |
| **t2** — the two formerly-dead degradation codes have real emit sites | **Not provoked.** `compilation_starved: 0`, `counsel_displaced: 0`, `muse_degradation_codes: {}`. No contradiction; this drive simply did not trip them. |
| **t12 / t13** — the muse pad and the workspace tool | **Not exercised by this drive** (they sit behind the unwired bench). The workspace *was* exercised live in Lane 1 — real containers, 4 of 4 tests. |

Muse lane, for the record: 4 sessions started, 4 completed, 4 insights
delivered, all of kind `step`; **zero** dropped stale, late, overflow or
superseded; zero degradations.

### The one that deserves more than a table row

**The terminal drain delivers to *presence*, and on a clean finish its counsel
structurally cannot reach the *cortex*.**

`guidance_undelivered: 1` is not noise — it is precisely the terminal drain's
own insight. A clean `finish` has no later completion by construction, so
counsel drained at drive end has nowhere to ride. `GuidanceRelay`'s docstring
predicts exactly this and reports it as pending rather than counting it
delivered, which is the honest accounting working as designed.

The consequence is that **"the terminal drain delivered 1 insight" and "the
cortex saw it" are different claims, and on a clean-finish drive only the first
is true.** t4/t25's acceptance criteria are met — the drain fires, the record
carries the count, delivered counsel reaches the synthesis turn's messages —
and a reader should still not conclude that drive-end counsel influenced the
result. On this drive it could not have.

### `tool_rounds: 0` is a wiring gap, not a model choice

t26 grew `ThreadedMuseRunner` a `tools=` seam and proved it with a live-shaped
test. No host in `examples/` passes one:

| Host | `ThreadedMuseRunner(...)` | bench wired? |
|---|---|---|
| `proof.py` | line 732 | no |
| `greenhouse.py` | line 714 | no |
| `league_seat.py` | line 1113 | no |
| `muse_latency.py` | line 461 | no — its `tools=TOOLS` is on the **cortex** gateway |

This is the same shape as the gap t25 existed to close: a seam that is correct,
tested, and reaches nothing, so every number published about it measures the
seam rather than the system. [muse-latency.md](muse-latency.md) already flagged
the tools-on-in-drive lane as absent; this run confirms it is **still** absent
in a live drive, after t26 landed.

## Instrument changes, declared

Three changes were made to harnesses **before** the runs whose results they
would affect. Stating them here because a results document that quietly edits
its own instruments is not evidence.

1. **`--max-tokens` on `challenge_subset.py`, `challenge_register.py`,
   `challenge_entropic.py` and `proof.py`.** All four hardcoded a 6000-token
   cortex budget with no way to raise it. The flag **defaults to 6000**, so the
   shipped behaviour is unchanged; the runs here pass `16000` explicitly and
   the value is recorded in each config preamble (as `max_tokens` /
   `cortex_max_tokens`) rather than left as a hidden variable.
2. **`trace=` on the three challenge harnesses' `gateway()`, plus `--trace-out`.**
   The seam built a `ModelResponse` and discarded the raw payload, so
   `finish_reason` was never visible. A turn cut off by the token cap and a
   model that simply stopped are **identical from the outside** — both arrive as
   empty content — and `designed-problem.md` records this repo reading one as
   the other. Every run now carries its turns' `finish_reason`, token counts and
   raw reasoning.
3. **Crash-safety, added after the 503 above and before the retry.** The
   transcript is now flushed after **every run** instead of once at the end, and
   a gateway failure inside a run is caught and recorded as an `ABORTED` run
   rather than killing the series. No verdict changes; this only stops evidence
   being discarded. It was made after seeing a *failure*, never after seeing a
   result it would change — the 3/3 subset series was already complete and is
   unaffected.

### `challenge_entropic.py` (variant 3b) — WRONG, and it replicates the truncation

n=1, `max_tokens=16000`, 52m54s, one run. Transcript:
[`challenge-entropic-trace.json`](challenge-entropic-trace.json).

Graded **WRONG**, `exit_reason: stopped`, `__COLLEAGUE_NO_RESULT_PRODUCED__`,
8 model turns, 9 tool calls, `degradations: []`. Expected `initial 00001111,
order C→A→E→D→B`.

And again, the `finish_reason` column carries the story — **3 of 8 turns
truncated at a full 16000/16000**:

| Turn | `finish_reason` | completion tokens | reasoning chars | tool calls |
|---|---|---|---|---|
| 1 | `tool_calls` | 11483 | 21448 | 3 |
| 2 | `tool_calls` | 3235 | 7839 | 1 |
| 3 | `tool_calls` | 6100 | 12355 | 1 |
| 4 | **`length`** | **16000 / 16000** | 30701 | 0 |
| 5 | `tool_calls` | 5978 | 11243 | 3 |
| 6 | `tool_calls` | 12384 | 21434 | 1 |
| 7 | **`length`** | **16000 / 16000** | 24203 | 0 |
| 8 | **`length`** | **16000 / 16000** | 26663 | 0 |

87,180 completion tokens for one run. This is an **independent replication** of
the register result on a different problem: same `exit=stopped`, same empty
sentinel, same empty degradation list, same underlying cause. Note also that its
*first* turn — a perfectly successful one — spent **11,483** tokens, nearly
twice the 6000 default, so on this problem the shipped budget would not have
survived turn one.

Recorded as **INCONCLUSIVE** for the same reason as the register run: the model
was cut off three times before it could answer.

### Left alone deliberately

Two things were found and **not** changed, because changing them is a decision
for the repo rather than a fix this task needed to make its runs happen.

1. **`--muse` on all three challenge harnesses is a dead flag that writes a
   false config record.** Its help text reads *"run the advisory lane too"*.
   None of the three files contains a single reference to `ThreadedMuseRunner`,
   `PresenceEngine`, `MuseLoop`, `frame_muse` or `presence=` — there is no
   advisory lane to run. The flag's only effect is
   `muse_model=args.muse_model if args.muse else None` in the config preamble.

   That makes it worse than a no-op: passing `--muse` writes a configuration
   record naming a muse model for a run in which **no muse was ever dialled**,
   into the one artifact this directory maintains specifically so that a later
   reader cannot be misled about what was configured. No run in this document
   passed `--muse`; every config preamble here correctly reads
   `"muse_model": null`.

2. **The flush granularity is per-run, not per-turn.** The crash-safety change
   above protects a multi-run series, but an `n=1` series that dies mid-run
   still loses its transcript — which is exactly the shape of the 41-minute
   loss. Fixing it properly means pushing the flush into the `trace` callback.

## The token budget is the finding, and it is measured

The rig guidance for this task was corrected mid-run: the cortex is
token-hungry by design and had been under-budgeted across the board. The subset
transcript measures it directly.

| Run | completion tokens | reasoning chars | would 6000 have truncated it? |
|---|---|---|---|
| 1 | 5242 | 10262 | no, with 758 to spare |
| 2 | **7375** | 12425 | **yes** |
| 3 | 5856 | 10035 | no, with 144 to spare |

At the harnesses' own shipped default of `max_tokens=6000`, **one of these
three correct answers would have come back as `finish_reason: length` with no
answer at all** — and the other two clear the cap by 2% and 13%. Every one of
these runs called `finish` on its *first* turn, so a truncated turn would not
have been retried; it would have been the whole result.

And 16000 is not a ceiling either. It was exhausted on **two independent
problems**:

| Problem | turns | turns truncated at 16000/16000 | largest successful turn |
|---|---|---|---|
| subset | 1 per run | 0 of 3 | 7375 |
| register | 4 | **2** | 8185 |
| entropic 3b | 8 | **3** | 12384 |

The honest statement is not "16000 is enough" but: **6000 is demonstrably too
small; 16000 suffices for the subset problem and does not for either register
problem; and no budget has been established as sufficient in general.** Both
harder problems produced a successful turn above 8000 tokens and a truncated
turn at 16000, so the distribution has a long tail rather than a clean cutoff.

This is not a hypothetical. These budgets are checked in today:

| Harness | cortex `max_tokens` | Verdict |
|---|---|---|
| `echo_probe.py` | **700** | far below one turn of this model's reasoning |
| `greenhouse.py` | **2048** | below one turn |
| `proof.py` | 6000 (before this task) | truncates ~1 turn in 3 on the subset problem |
| `challenge_*.py` | 6000 (before this task) | ″ |
| `association_work.py` | 6000 | ″ |
| `echo_probe_workspace.py` | 16000 | the only one already correct |

> **Superseded 2026-07-31 — the table above is the audit as it stood, not the
> current tree.** Task `t24` went on to *measure* what this audit inferred:
> 5 of 83 completions truncated at 2048 against 0 of 58 at 16000, one of them
> costing an entire league turn that the outcome metric scored as played
> ([arena-budget.md](arena-budget.md)). Under the standing rule that a
> measured failure mode does not ship as default behaviour, deviation `d16`
> raised **every** cortex budget in this table to `16000`: `echo_probe.py`
> 700, `greenhouse.py` 2048, `league_seat.py` 2048, `association_work.py`
> 6000, and `proof.py` / `challenge_*.py` 6000. **No result in this directory
> was re-run or re-graded** — this task's decision not to re-run stands, and
> each affected doc names the `--max-tokens` value that reproduces its
> published run.

`echo_probe.py` at **700** is the one worth flagging hardest: this model spends
~10,000 characters of reasoning before it emits anything, and 700 tokens does
not cover the thinking, let alone the answer. The published
[memory-echo-chamber.md](memory-echo-chamber.md) headline — DEFERRED 6/6 with
the record, RESISTED 6/6 without — was measured through that cap. **This task
did not re-run that series and makes no claim about whether the result would
change.** What it establishes is that the instrument was set below one turn of
the model's reasoning, which is a fact about the instrument, not about the
finding.

The live-gated tests in Lane 1 ran at these shipped budgets, because the tests
are the contract and running them at a budget they do not specify would be
measuring something else. They all passed regardless — but they assert seam
behaviour, not answer quality, which is exactly why the cap did not surface
there.

## Everything that did not run, and why

Listed so the reader never has to infer absence from silence.

| Lane | Status | Why |
|---|---|---|
| `EMBODIMENT_LIVE_ARENA` — `test_league_seat.py` (3), `test_arena_series.py` (1) | **ABSENT — not attempted** | They belong to task **t24**. Out of scope here by assignment, not by failure. |
| `challenge_register.py` at n=2 | **ABSENT — attempted, destroyed** | `HTTP 503` at 41m43s. Retried at n=1; that retry is reported above. The n=2 series has no data because the harness discarded it. |
| `challenge_entropic.py` variant `3` (the under-determined, 17-solution form) | **ABSENT — not attempted** | Only variant `3b` was run. Wall-clock: one 3b run cost 52m54s. |
| A no-muse control arm for `proof.py` | **ABSENT — not attempted** | Wall-clock. The muse arm is reported on its own terms and **no muse-versus-solo comparison is claimed** from it. |
| Re-running `echo_probe.py` / `greenhouse.py` at a corrected budget | **ABSENT — deliberately not attempted** | Their published results were measured at 700 and 2048 tokens. This task reports the budget and makes **no claim** about whether the findings would change. |

## Verdicts, honestly stated

**Lane 1 — the live-gated suite: PASS, 13 of 13.** Every seam the rig-gated
tests cover is live and behaves as its test asserts. This is a statement about
seams, not about answer quality; the tests were deliberately written not to
assert the latter.

**Lane 2 — `challenge_subset`: 3/3 CORRECT**, a clean pass on a small n. One
problem, one model, one rig, one temperature, on one day — the same n≤4 caveat
every other document in this directory carries. All three runs answered in a
single turn, so the bench's tool surface went unused and this says nothing about
tool-assisted reasoning.

**Lane 2 — `challenge_register` and `challenge_entropic` 3b: INCONCLUSIVE, not
WRONG.** Both are graded WRONG and both scores are committed unchanged. But 2 of
4 and 3 of 8 turns respectively were cut off at a full 16000/16000 completion,
so neither run ever had the chance to produce an answer. **The verdicts measure
the token cap, not the model.** They are recorded as INCONCLUSIVE with the
graded WRONG left visible beside them, rather than quietly rescored. Two
problems failing the same way is a replication, not a coincidence.

**Lane 3 — the cycle's changes: t4/t25 CONFIRMED live; t26 ABSENT in every
host; t2 not provoked.** The single most useful sentence this task can hand
forward: *the terminal drain fires and delivers, and on a clean-finish drive its
counsel provably cannot reach the cortex.*

**The cross-cutting finding: the token budget is a hidden variable across this
whole directory**, at 700, 2048 and 6000 in five checked-in harnesses, and
`ModelResponse` cannot carry the `finish_reason` that would make its effects
visible. Nothing here re-scores an earlier result on that basis. It says where
the instrument was set.
