# Bee-Hive architecture — pre-registration, width rung first

**Written:** 2026-08-01 · **Task:** t9 · **Plan:**
[error-derived-timeouts-bee-hive-architecture](../plans/2026-08-01-error-derived-timeouts-bee-hive-architecture.md)
· **Spec:** [`2026-08-01-error-derived-timeouts-bee-hive-architecture.md`](../specs/2026-08-01-error-derived-timeouts-bee-hive-architecture.md)
· **Pin:** `tests/test_bee_hive_width_preregistration.py` · **Rig:**
[README](README.md)

Committed **before the first measured dial** of the Bee-Hive series. Provable
from git history: this file and its pin land in one commit on `tbh/t9`, and no
`arch-hive-*.jsonl` or `arch-policy-*.jsonl` run artifact exists in the
repository at that commit — only the two sampling tables and the policy fixture
file, which are *inputs*. The pin asserts that emptiness rather than trusting it.

**Every number below is recomputed by the pin test from a committed input.**
That is the standard
[`orchestrator-worker-preregistration.md`](orchestrator-worker-preregistration.md)
set and this document inherits it: change `worker-scoped-overhead-summary.json`
or `arch-hive-sampling.json` and the constants here change with them, or the pin
fails. **Two** numbers are choices rather than measurements; they are named as
choices in [§16](#16-the-bars-that-are-choices-not-measurements) and fixed here,
before the dial.

## Contents

| § | what it fixes | covers |
|---|---|---|
| [1](#1-relationship-to-the-still-climbing-ladder) | extend / supersede / wait, and the ladder's state at registration | `c44`, `h30` |
| [2](#2-the-instrument-this-series-runs-on-streaming) | `d3`, the two-phase bounds, and what is no longer comparable | `d3` |
| [3](#3-why-the-width-rung-goes-first) | the axis four experiments never reached | `c19` |
| [4](#4-the-outcome-metric-and-the-proof-it-is-mandatory) | the metric, the rejected candidates, the no-tie-break rule | `c22`, `h16` |
| [5](#5-rung-w1--the-scoped-lane-width-sweep) | arms, cells, the independent variables | `c21`, `c28`, `h20` |
| [6](#6-call-granularity-derived-from-t8s-overhead-numbers) | the grain ladder, and the figure it must never cite | `c43`, `h29` |
| [7](#7-the-sampling-table) | temperature / thinking / `max_tokens` per role per tier per grain | — |
| [8](#8-cell-sizing-derived-from-measured-throughput) | `ITEMS_PER_CELL`, `REPETITIONS_PER_CELL`, and what they cost | — |
| [9](#9-the-decision-rule) | `SEPARATED-WIDTH`, `CEILING-WIDTH`, `VOID`, `INCONCLUSIVE`, numerically | — |
| [10](#10-the-stop-rule) | where the climb stops and what is reported `ABSENT` | — |
| [11](#11-rung-w2--the-amortisation-cell-computed-before-it-is-dialled) | what width buys a whole hive drive | `c28`, `h20` |
| [12](#12-call-acceptance-as-a-per-arm-axis-and-the-33-refutation) | the falsifier for delegate-to-authored-artifacts | `c20`, `h11` |
| [13](#13-no-security-shaped-question-enters-this-sweep) | the three declared questions, and why no fourth | `c17`, `h9` |
| [14](#14-rung-h0--the-headroom-screen-the-sweep-is-gated-on) | league's outcome metric, and the screen it must pass | `c22`, `h16` |
| [15](#15-rung-p1--the-compiled-policy-its-three-controls-and-both-extremes) | escalation rate at 1.0 and at 0.0 | `c18`, `h10` |
| [16](#16-the-bars-that-are-choices-not-measurements) | the two chosen bars, declared before the dial | — |
| [17](#17-predictions-as-falsifiable-statements) | seven predictions with their falsifiers | — |
| [18](#18-rules-fixed-in-advance) | the run discipline | — |
| [19](#19-what-this-series-cannot-show) | the honest limits | — |
| [20](#20-every-registered-constant-at-the-value-the-pin-recomputes) | one table a reader can check every number against | — |

---

## 1. Relationship to the still-climbing ladder

`c44` requires this document to state, before any dial, whether the new series
extends, supersedes, or waits for the orchestrator-worker ladder — and `h30`
requires it to quote that series' results document by section, **or** to state
that it was registered before that document existed and why that is sound.

**Both, because the situation is split.**

### The t14 results document does not exist

The orchestrator-worker plan's task `t14` — *"Results and verdicts: decision
rules quoted beside every verdict, wall-clock and token tables per arm per rung,
corrections section, README reproduce lines"* — has not run. No file it would
produce exists on `main`, on this branch, or on the series branch. Registering
against a document that does not exist is not possible, and **waiting for it is
not sound**: the width rung this document registers does not dial the cortex at
all ([§5](#5-rung-w1--the-scoped-lane-width-sweep)), so it neither contends with
the running series nor depends on its verdicts. It can be registered and run
while that ladder is parked.

### The in-progress series document does exist, and is quoted

`docs/live-test-results/orchestrator-worker-series.md` (task `t12`, headed
**"IN PROGRESS"**) is committed on branch `owa/t12` at `ebf3c5e`, which is
**not merged to `main`** and is therefore *not* on this branch. The facts below
are read from that branch at that commit and are cited, **not pinned** — the pin
test cannot read a file that is not in its own tree, and a test that reached
across branches would pass or fail on which branches a clone happened to fetch.

Its `### The classifier applied` section reads, verbatim:

> - `C1` — **CEILING** · E 6/6 · {}
> - `C2` — **ABSENT** (no cells recorded)
> - `C3` — **ABSENT** (no cells recorded)
> - `C4` — **ABSENT** (no cells recorded)
> - `K1` — **ABSENT** (no cells recorded)

and its `### ABSENT — every cell that was not dialled, by name` section records
`L1` — **the fan-out rung, the only rung on that ladder where width could have
been measured** — as `ABSENT` for all four arms, because *"the cycle's
live-budget priority order is ladder A, then F, then P, and anything the ladder
cap does not reach is ABSENT by name."*

Two further facts from the same branch, read from the raw records rather than
from the prose, because the prose tables were generated before them:

- `orchestrator-worker-series-raw/ladder-decisions.jsonl` holds **exactly one**
  rung decision: `C1` → `CEILING`, clause `arm-e-first-ceiling`, with `C1-W`,
  `C1-M` and `C1-H` recorded absent and never dialled.
- `orchestrator-worker-series-raw/C2-E.jsonl` holds **two** attempts of a
  pre-registered six, both `exit_reason: aborted`, both `is_correct: false`, at
  **9084.6 s** and **5367.2 s**. That is **14451.7 s** on the two attempts'
  own clocks against a `RUNG_CAP_SECONDS` of **10800.0 s** — at least
  **3651.7 s past cap**, before any inter-attempt overhead. No `C2` rung
  decision has been written.

**The relationship, stated: this series does not extend that ladder and does not
supersede it. It runs beside it on a different axis, and it reconciles rather
than duplicates.** If the orchestrator-worker ladder ever reaches its own `L1`
fan-out rung, that rung measures *fan-out unit dispatch* — a partitioned turn
grant across units that hold turns — while [§5](#5-rung-w1--the-scoped-lane-width-sweep)
measures *plain concurrent scoped calls*, decision `c42`'s deliberately
different seam. The two are not the same measurement and neither can be quoted
for the other. This document says so now so that nobody quotes one for the other
later.

**One number from that branch is load-bearing here and is used as corroboration
only.** Its `### Turn latency` table measures the cortex at a **346.2 s** median
turn and **573.0 s** slowest against the 57.2 s the previous pre-registration
sized cells on — a 6.1× miss, recorded there as *"The cell-sizing model is wrong
by roughly an order of magnitude."* The cortex-turn range this document uses
(400–730 s) comes from `worker-scoped-overhead-summary.json`, which **is** on
this branch and **is** pinned. The `owa/t12` figures bracket it from below: the
slowest turn (573.0 s) sits inside the range and the median (346.2 s) sits just
under it. That direction is checked rather than waved at, because
[§11](#11-rung-w2--the-amortisation-cell-computed-before-it-is-dialled)'s whole
argument depends on the drive being *long*: at a 346.2 s turn the drive is
1731 s, the scoped lane is **0.73%** of it and width moves **0.33%** — still
under the 1% falsifier, so the conclusion survives the faster reading.

---

## 2. The instrument: this series runs on STREAMING

Deviation **`d3`** (approved, `.devague/deliveries/`) promotes streaming from a
flag-gated pilot to the default transport before this series dials, rather than
after it. The instrument is named here, in the opening, because a
pre-registration that does not state its transport is registering an experiment
it cannot reproduce.

### What was measured, and what it settles

[`streaming-probe.md`](streaming-probe.md), dialled 2026-08-01 against an idle
cortex:

| | thinking on | thinking off |
|---|---:|---:|
| total seconds | 43.45 | 18.16 |
| chunks | 390 | 164 |
| time to first chunk (s) | **0.263** | 0.248 |
| **max inter-chunk gap (s)** | **0.124** | 0.116 |
| first content delta at (s) | **43.221** | 0.248 |
| usage in terminal chunk | **yes** | **yes** |

The gateway's `GATEWAY_READ_TIMEOUT` is a **per-read** socket timeout, so under
streaming it stops being a total-request deadline — which is the only reason a
600 s bound in another process, that no client value can raise, is survivable at
all. And with thinking on the first *content* delta does not arrive until
43.2 s: non-streaming that is a wall of silence, which is the exact shape that
cost the running series two repetitions.

### The two-phase bound, and its derivation

A single idle clock recreates the censoring shape on a queued request (`c38`),
so the bound is two-phase and each phase is derived rather than chosen:

```text
phase 1 — time to first chunk, armed from dial until the first chunk arrives
  TTFC_BOUND  >=  MAX_NUM_SEQS x (max_tokens / slowest measured rate)

phase 2 — inter-chunk idle, armed ONLY after the first chunk has arrived
  IDLE_BOUND  >=  IDLE_MARGIN x MAX_INTER_CHUNK_GAP_SECONDS
              =   100 x 0.124
              =   12.4 s
```

`MAX_NUM_SEQS` = 2 is the cortex server's admitted concurrent sequences, stated
in the probe. **It applies to the cortex lane and not to the worker lane**: Thor
returned an effective concurrency of **7.48** on eight concurrent scoped calls
in `worker-scoped-overhead.md`, so the queue term is the local cortex's and not
the worker's. `IDLE_MARGIN` = 100 is a chosen bar
([§16](#16-the-bars-that-are-choices-not-measurements)); against a measured
maximum gap of 0.124 s it leaves two orders of magnitude, and the probe's own
margin against the 600 s gateway bound was ~4,800×.

**Phase 1 is derived from the queue model, not measured**, and `d3` says so:
both probe dials ran against an idle cortex, so a ~0.25 s time-to-first-chunk
says nothing about a *queued* request. That is the part the probe explicitly
could not discharge, and the first live dial validates the derivation rather
than gating it.

**The implementing constants are task `t5`'s, not this document's.** What is
pre-registered here is the *derivation* and one obligation: **every run's
preamble records the transport it dialled and the two bound values it dialled
them at**, and a run whose preamble does not carry them is not a cell of this
series.

### Where streaming is dialled — and the one place `d3`'s scope is contested

`d3` reads *"the DEFAULT transport for every cortex/worker dial in the harness
family"*. The spec's own decision list reads *"streaming targets the
long-completion cortex lane; B1's scoped worker calls (tens of tokens) stay
non-streaming — there is nothing to stream, and the concurrent fan-out path
keeps the simple blocking client instead of inheriting SSE-read thread teardown
discipline it does not need"* (`c42`). **These disagree, and a series may not
dial an instrument that is two things.**

Registered resolution, with the reason so it can be overruled on the record:

- **Cortex lane: streaming.** `d3`, unmodified. Every reason `d3` gives — a
  censoring per-request clock, a 43 s silent think, reasoning visible as it
  arrives — is a fact about long completions, and the cortex lane is where they
  live.
- **Scoped worker lane: non-streaming.** Not a rejection of `d3` but a reading
  of its scope. The scoped lane's *clock is this rung's outcome metric*
  ([§4](#4-the-outcome-metric-and-the-proof-it-is-mandatory)), and every
  baseline it is sized and predicted against — `1.8931` and `3.4593` calls per
  second, `0.2891` s per call — was measured non-streaming on this exact path.
  Changing the transport on the measured lane would make the one committed
  measurement this rung derives from non-comparable, which is the single thing
  a width rung must not do. `c42`'s teardown-discipline reason stands beside it.

**If `t5` lands streaming on the scoped worker lane as well, this rung's sizing
inputs are no longer its baseline and [§8](#8-cell-sizing-derived-from-measured-throughput)
must be re-derived under an appended, dated amendment — never an edit to this
section.**

### What is no longer comparable, said plainly

**Every latency figure this document cites from a prior series was measured
non-streaming**, including `worker-throughput.md`, `worker-scoped-overhead.md`,
`cortex-toolcall-probe.md`, `league-h2h.md` and `league-commander.md`. Under
streaming a client's stopwatch starts and stops at different events, so **no
latency in this series may be compared to a latency in those documents**, and
none is. **Token accounting is unaffected**: the terminal usage chunk arrives
when `stream_options.include_usage` is set and carries `finish_reason` and all
four token counts, measured `yes` on both probe dials — so every token figure
quoted from a prior document remains comparable, and every token figure this
series produces remains comparable with them.

---

## 3. Why the width rung goes first

Four experiments in this repository have compared an orchestrated arm against a
flat one. Every one of them agreed that orchestration costs a multiple and buys
nothing measurable:

| series | what it compared | outcome |
|---|---|---|
| `association-work` | muse/cortex role split, 2×2 | all four cells tied exactly; `interaction = 0.00` |
| `muse-arms` | tools-off vs +pad vs +pad+workspace, n=8 per arm | `INCONCLUSIVE`; 9 of 16 tool-arm runs `NO_ANSWER` |
| `league-commander` | hierarchy vs flat, 384 calls | identical outcomes on both arenas; **2.4–4.4×** the cost |
| `league-h2h` | full-Gemma / mixed / full-Qwen | outcome tied **0–0 in 6 of 6**; ranking on one optional field |

**And every one of them measured a second mind advising or deciding. None
measured a second mind doing bulk work in parallel.** The one rung that would
have tested the actual claim — where separation is *structural*, because a flat
arm cannot dispatch concurrent units at all — is rung `L1` on the
orchestrator-worker ladder, and [§1](#1-relationship-to-the-still-climbing-ladder)
records it `ABSENT`, never reached. `C1` ceilinged, `C2` ran 3651.7 s past its
cap on two aborted attempts of six, and `C3`, `C4` and `K1` were never dialled.

Said as issue [#44](https://github.com/agentculture/embodiment/issues/44) says
it: *we measured the axis where orchestration cannot win, and skipped the one
where it wins by construction.* So the width rung runs first, and it is the
cheapest decisive experiment available — it dials no cortex, it costs tens of
minutes rather than a night, and its null is structural rather than hopeful.

**If width does not separate, the whole M/H/B question is moot** and the flat
default stands on evidence rather than on priors. That is written into the stop
rule ([§10](#10-the-stop-rule)) rather than left as a sentiment.

---

## 4. The outcome metric, and the proof it is mandatory

`c22`'s hard exclusion: **no metric in this experiment may be an optional field
that one arm happens to populate more often.** `h16` requires the metric to be
*proved* mandatory rather than asserted, and requires a metric that fails the
test to be **rejected at registration time** rather than discovered afterwards.

### The lesson being obeyed, quoted from the series that taught it

[`league-h2h.md`](league-h2h.md), *The mechanism — three of four signals are on a
ceiling*:

> `outcome.total` (missions + control + resources) was **0–0 in all six
> matches** … So every match was decided by the first tie-break, league's own
> content-aware `cooperation_v1`. … The separation is `message_utility`, and it
> is binary at the source … **Said plainly: this series separated the arms on
> interface compliance, not on play.**

`full-gemma` sent a team message on **0 of 12** seat-turns, `mixed` on **4 of
12**, `full-qwen` on **9 of 12**. The tool schema calls that field *"Optional
team message."* An entire three-way ranking, and 5750 s of rig, rested on
whether an arm filled in a field it was told was optional.

### The registered outcome metric

**Rung `W1`'s outcome metric is `items_per_second`:**

```text
items_per_second = ITEMS_PER_CELL / cell_wall_seconds
```

where `ITEMS_PER_CELL` = **320** is a constant registered in
[§8](#8-cell-sizing-derived-from-measured-throughput) and fixed before any call
object exists, and `cell_wall_seconds` is the sum of the cell's
`batch_elapsed_seconds`, each measured by a monotonic clock around
`arch_hive.dispatch`.

`calls_per_second` = `items_per_second / items_per_call(grain)` is reported
beside it and is the figure comparable **within** a grain;
`items_per_second` is the figure comparable **across** grains, because it is the
same work priced in the same currency.

### The proof, in three legs

**Leg 1 — neither term can be read from a model response.** `ITEMS_PER_CELL` is
a registered integer. `cell_wall_seconds` comes from `time.monotonic()`. There
is no path by which a model's output changes either. The pin asserts the
structural fact that makes this hold under failure: **`arch_hive.dispatch`
returns exactly one `ScopedResult` per planned call, always** — a call that
times out gets `absent-timeout`, one whose transport dies gets
`absent-transport`, one that answers nothing gets `refused-empty`. There is no
code path on which a planned call produces no record.

**Leg 2 — the declining-arm test.** The pin drives the real dispatch path with a
worker double that returns empty content on every call
(`arch_hive.scripted_worker(silent=True)`), one that answers off-space, one that
answers correctly, and one whose transport raises. In every case it asserts that
`items_per_second` is still finite and populated while `acceptance_rate` moves
between `0.0` and `1.0`. That is the property `message_utility` did not have:
**an arm that declines everything still produces this number, and produces it at
full information.** Severing the guarantee — making `dispatch` drop
non-accepted results — was checked to turn the test red, so the proof is not a
test that has never been observed failing.

**Leg 3 — the rejected candidates, rejected here rather than later.** A metric
is rejected at registration by being named, with its defect:

| rejected candidate | why it is rejected |
|---|---|
| `cooperation_v1.message_utility` | **optional field.** The exact defect `c22` names. |
| `cooperation_v1` (composite) | three of its four signals sat at or within 3% of a ceiling for all three arms, so the composite *is* `message_utility` in disguise. |
| league `outcome.total` | mandatory, but **zero measured headroom in both committed configurations**: 0–0 in 6 of 6 model-vs-model (`league-h2h`), and four architectures scoring identically on both arenas vs the house bot (`league-commander`). Admitted to this series **only** behind the screen in [§14](#14-rung-h0--the-headroom-screen-the-sweep-is-gated-on). |
| `effective_concurrency` | mandatory, and **actively deceptive here**. See [§6](#6-call-granularity-derived-from-t8s-overhead-numbers). |
| `acceptance_rate` / `refusal_rate` | mandatory, but folding acceptance into outcome reports a *broken* arm as a *losing* arm — issue [#33](https://github.com/agentculture/embodiment/issues/33)'s shape. Registered as a separate axis and a validity gate ([§12](#12-call-acceptance-as-a-per-arm-axis-and-the-33-refutation)). |
| `truncated` / `absent-truncated` | an instrument event ([#37](https://github.com/agentculture/embodiment/issues/37)), never a result. |

### The rule that would have caught `league-h2h`, registered

**No tie-break may promote a secondary metric into a verdict.** A rung whose
outcome metric does not separate is reported `INCONCLUSIVE` on the outcome
metric. It is **not** decided by a secondary signal, a cost figure, an
acceptance figure, or a composite. Every one of those is reported beside the
verdict, none of them is the verdict, and no ordering of arms is published that
the outcome metric did not produce.

### And the headroom the metric has, measured

A mandatory metric with no headroom is the *other* half of the `league-h2h`
failure, so it is stated with a number. `worker-scoped-overhead.md` measured the
width-1 → width-8 call-throughput speedup at **3.44×** with lean context and
**1.83×** with realistic context, on this exact dispatch path. Between 1.0×
(the structural null) and 3.44× there is real room, and it is measured room
rather than hoped-for room.

---

## 5. Rung `W1` — the scoped-lane width sweep

### The arms, and what actually differs

| tier | label | answerer | play-time worker calls | role in this rung |
|---|---|---|---|---|
| **B0** | `hive-rigid` | code (`first`) | 0 | the zero-model-call floor: it prices the harness, not the model |
| **B1** | `hive-scoped` | worker | N, concurrent | **the arm under test** |
| **B2** | `hive-agentic` | worker holding control | a bounded drive | arm `M` in `arch_arms.py`; **not dialled at `W1`** |

`B0` and `B1` differ by exactly one field in `arch_hive.HIVE_ARMS` —
`HiveArm.answerer` — and nothing in the harness branches on an arm id. `B2` has
no entry in `HIVE_ARMS` on purpose: it is arm `M`, already built and already
dialable, and a second implementation of a shipped arm is not a tier.

**`W1` does not compare tiers. It varies width and grain inside `B1`.** That is
the whole point: the control for a width claim is *the same arm with concurrency
removed*, not a different arm. `B1` at width 1 is a flat arm in the only sense
that matters here — it cannot dispatch concurrent units — and it is
byte-identical to `B1` at width 8 in prompts, questions, item set, sampling and
model. **A width can therefore only move the clock**, and that is registered as a
checkable gate rather than assumed: prompt tokens are deterministic given the
plan and must agree **exactly** across widths within a grain
([§9](#9-the-decision-rule), `prompt-drift`). Completion tokens are *not* gated —
decode at temperature 0.3 is stochastic and a difference there is a fact about
sampling, not about width — they are reported per cell with their spread.

### The independent variables

```text
WIDTH_LADDER = (1, 2, 4, 8)      # powers of two up to MAX_HIVE_WIDTH = 8
GRAIN_LADDER = ('unit', 'pair', 'batch4', 'batch8')   # items_per_call 1, 2, 4, 8
```

`MAX_HIVE_WIDTH` = **8** is `arch_hive.py`'s own constant, itself measured
saturation from [`worker-throughput.md`](worker-throughput.md) — 8 → 14 buys
+5.5% aggregate throughput for +75% concurrent load, and
[`corrections.md` §10](corrections.md) later found that **32% of width-14 calls
were retried** while widths 1, 2 and 8 carried zero. The width ladder is derived
from that constant, not chosen: every power of two up to it.

The grain ladder is the committed `grains` table in
[`arch-hive-sampling.json`](arch-hive-sampling.json), read in ascending
`items_per_call` order. **No grain outside that table may be dialled**, and no
grain may be added after this commit.

`4 widths × 4 grains = 16 cells` per repetition block.

**`W1` runs no drive, and that is why it is not bound by `max_scoped_calls`.**
It calls `arch_hive.plan_calls` directly with a budget of
`ITEMS_PER_CELL / items_per_call(grain)`. The committed `max_scoped_calls` = 24
is `HiveOrchestrator`'s **drive-level** clamp — how many scoped calls one
*cortex drive* may dispatch in total — and a rung with no cortex in it has no
drive to clamp. That distinction is not a loophole; it is precisely the quantity
[§11](#11-rung-w2--the-amortisation-cell-computed-before-it-is-dialled) measures,
and the gap between 320 and 24 is the finding.

### What `W1` measures about the mis-loaded rig — and what it does not

`c28` says the rig is mis-loaded: *"the 27B cortex thinks at ~23 tok/s strictly
sequentially while ~9× of measured worker concurrency and unbounded bot-code
width sit unused."* `h20` binds that claim to its numbers and requires it to be
**restated, not defended**, if remeasurement moves them. Two of the three have
already moved, and this document restates them rather than repeating the frame:

| `c28`'s figure | as restated by a later measurement |
|---|---|
| cortex ~23 tok/s sequential | **holds** — the 21.5–25.4 tok/s band, corroborated at 25.43 tok/s by a second harness on a different scenario ([`corrections.md` §9](corrections.md), which is on this branch; the band's own measurement is `orchestrator-worker-preregistration.md` §18 on `owa/t12`) |
| "~9× of measured worker concurrency" | **does not transfer to scoped calls.** 8.99 was measured at width 14 on 1200-token completions and is retry-contaminated (7.25 corrected). On tens-of-token calls the currency that matters transferred at **3.44×** / **1.83×** ([§6](#6-call-granularity-derived-from-t8s-overhead-numbers)) |
| saturation near width 8 | **holds, and is strengthened** — 77% efficiency at width 8 against 52% (not 64%) at width 14 once the retry artifact is removed |

---

## 6. Call granularity, derived from t8's overhead numbers

`h29` requires this section to exist: *"the sweep's pre-registration cites the
overhead measurement's numbers for its chosen granularity; no arm design cites
the 9× figure for small calls without it."*

### The figure this sweep must never cite, and the one it must

[`worker-scoped-overhead.md`](worker-scoped-overhead.md) measured 140 scoped
calls at widths 1 and 8, with lean and realistic context:

| reading | 1200-token reference | measured on tens-of-token calls | transfers? |
|---|---|---|---|
| **calls answered per second, width 8 vs width 1** | 6.14× (width 8) | **3.44×** lean, **1.83×** realistic | **NO** — 43% / 23% of ideal |
| `effective_concurrency` | 6.14 | **7.48** / 7.56 | reads *higher*, and is **misleading here** |

`effective_concurrency` is a ratio of *token* throughputs. On a fifteen-token
completion the per-stream figure is dominated by overhead rather than decode,
which collapses the denominator and inflates the ratio: recomputed on this data
it says the concurrency figure transferred **better** than at 1200 tokens
(transfer ratio 1.22×). It did not.

**Registered: `calls_per_second` and `items_per_second` are this series' only
throughput metrics. `effective_concurrency` may be reported as a diagnostic and
may never appear in a verdict, a headline, or a comparison of arms — under any
spelling.** The pin asserts the document states both the trap figure (7.48) and
the true transfer (3.44× / 1.83×) so the two cannot drift apart.

### Prefill binds, not completion tokens

Adding ~768 prompt tokens of realistic repeated context costs:

- **+0.1176 s at width 1** — an implied prefill rate of ~6,509 tok/s;
- **+1.289 s at width 8** — an implied ~596 tok/s, **eleven times worse**.

Concurrency multiplies decode; it does not multiply prefill. Realistic context
takes width-8 answer throughput from 8.383 calls/s down to 3.459 — a
**0.4127×** throughput ratio. So for a scoped call the binding budget is
**prompt** tokens, not completion tokens, and grain is the variable that prices
it: one call carrying 8 items pays the shared preamble once instead of eight
times.

**That is why grain is swept rather than chosen.** `c21` predicts quality rises
then plateaus while cost rises monotonically, so the knee is the finding; on the
throughput axis the same argument runs the other way — coarser grain should be
strictly faster, and the rung measures where that stops being true. No on/off
design can locate a knee.

### The thinking toggle is pinned per role per tier, and it is not optional

Also measured by t8, at the same 48-token scoped cap:

| | `enable_thinking: false` | `enable_thinking: true` |
|---|---|---|
| answered | **10/10** (and 106 of 106 across every thinking-off cell) | **0 of 6** |
| truncated | 0 | **6 of 6** |
| completion tokens | 13.7 | 48.0 (the cap) |
| given a 2000-token budget | 15.3 tokens, 6/6 answered | **1,574.5** tokens, 2 of 4 still truncated |

A thinking model handed a tens-of-token budget reproduces
[#32](https://github.com/agentculture/embodiment/issues/32)'s shape exactly: it
consumes its budget and returns nothing usable. **Every scoped worker call in
this series puts `{"chat_template_kwargs": {"enable_thinking": false}}` on the
wire, and the run asserts it on the wire rather than assuming it.** The mapping
is data in `arch-hive-sampling.json`'s `thinking_wire.modes`, so a mode with no
entry is a config error and never a silent no-op.

### The token budget, checked against the coarsest grain

```text
needed(batch8) = MAX_ITEMS_PER_CALL x completion_tokens_per_scoped_call
               = 8 x 15.675
               = 125.4 tokens
committed worker max_tokens = 256      ->  margin 2.04x
```

The committed 256 covers the coarsest grain at **2.04×** the measured per-item
completion length. That margin is recomputed by the pin, and it is the number
[§12](#12-call-acceptance-as-a-per-arm-axis-and-the-33-refutation) leans on: a
budget that truncated at coarse grain would publish arm B as refuted by its own
`max_tokens`.

---

## 7. The sampling table

The table lives in [`arch-hive-sampling.json`](arch-hive-sampling.json) — an
**input** read by `examples/arch_hive.py` through `load_hive_config`, not a run
artifact. What follows is a reader's copy; the pin loads the JSON through the
harness's own loader and checks every cell below against it, so the copy cannot
quietly become a second set of values. `arch_hive` defines **no fallback** for
any cell: a missing one raises `ConfigError` naming the arm and the role, and
that absence of defaults is asserted structurally over the module's own AST.

| tier | role | temperature | thinking | `max_tokens` |
|---|---|---|---|---|
| B0 | cortex | 0.3 | on | 16000 |
| B0 | senses | 0.3 | off | 16000 |
| B1 | cortex | 0.3 | on | 16000 |
| B1 | **worker** | 0.3 | **off** | **256** |
| B1 | senses | 0.3 | off | 16000 |

| tier | `max_steps` | `worker_max_steps` | `spawn_allowance` | `max_scoped_calls` | `max_concurrency` |
|---|---|---|---|---|---|
| B0 | 14 | 0 | 0 | 24 | 1 |
| B1 | 14 | 0 | 0 | 24 | 8 |

- **`worker_max_steps` = 0 and `spawn_allowance` = 0 in both tiers, and that is
  the arm's whole claim.** A hive worker never drives, so there is no turn
  budget to grant it; the hive passes no subagent seam to `embodiment.run` at
  all, so nothing can be spawned even by accident.
- **The cortex cell matches `arch-arms-sampling.json` exactly** so the hive
  tiers stay comparable with E/W/M/H rather than confounded by sampling.
- **The worker cell deliberately does not.** A scoped answer is tens of tokens,
  thinking is off ([§6](#6-call-granularity-derived-from-t8s-overhead-numbers)),
  and 256 is a budget with a measured 2.04× margin — a hive call that needed
  16000 tokens would not be a scoped question.
- **Senses is configured in both tiers and dialled by neither.** Its
  configuration is hashed and `arch_hive.assert_senses_identical` refuses to run
  when the tiers disagree, so the constant is checkable from a committed
  artifact rather than trusted.
- **`W1` dials the worker only.** No cortex call is made at any `W1` cell; the
  cortex rows above are configured because the tier declares them and because
  [§11](#11-rung-w2--the-amortisation-cell-computed-before-it-is-dialled) uses
  them.

`dispatch.batch_timeout_seconds` = **300.0**, derived rather than chosen: a hive
batch holds *one turn per call* and never a drive, so the bound is the per-turn
bound with no turn-budget multiplier — 256 tokens / 21.5 tok/s = 11.9 s, with a
wide margin for queueing rather than a value sitting on its bound.

---

## 8. Cell sizing, derived from measured throughput

Two floors, and the cell takes the larger. Both are computed from committed
inputs; neither is chosen.

### Floor 1 — the retry-drop remedy must leave a cell

`worker-scoped-overhead.md`'s own correction: one transport retry in 141 calls
cost 20 s against a 0.98 s median healthy call — a **20×** ratio — and the pilot
run would have published a lean speedup of **0.25×**, a number saying
concurrency makes scoped calls *slower*, entirely from one sleep constant. The
committed remedy is to drop the contaminated batch **whole**, because a retried
call's neighbours shared its batch clock.

A cell must therefore have enough batches that dropping one still leaves a cell:

```text
RESOLUTION_FLOOR_ITEMS = MIN_BATCHES_PER_CELL x MAX_HIVE_WIDTH x MAX_ITEMS_PER_CALL
                       = 5 x 8 x 8
                       = 320
```

`MIN_BATCHES_PER_CELL` = **5** is not a choice: it is `len(batch_elapsed_seconds)`
of t8's committed `rich-off-w8` cell, reused rather than re-invented.

### Floor 2 — the cell must price something #44 actually claims

Issue #44's economic claim is that N scoped calls cost less wall clock than one
cortex turn. A cell too small to be a visible fraction of a cortex turn prices
nothing:

```text
AMORTISATION_FLOOR_ITEMS = ceil(SCOPED_SHARE_TARGET x CORTEX_TURN_SECONDS_LOW
                                x CALLS_PER_SECOND_W1)
                         = ceil(0.10 x 400.0 x 1.8931)
                         = 76
```

`SCOPED_SHARE_TARGET` = 0.10 is a chosen bar
([§16](#16-the-bars-that-are-choices-not-measurements)).
`CORTEX_TURN_SECONDS_LOW` = 400.0 and `CALLS_PER_SECOND_W1` = 1.8931 are both
recomputed from `worker-scoped-overhead-summary.json`.

```text
ITEMS_PER_CELL = max(320, 76) = 320
```

**`ITEMS_PER_CELL` = 320.**

### Repetitions, against the caps

Costed **conservatively**: every cell priced at the *finest* grain (the most
calls) and at the *width-1* rate (the slowest measured), so no cell can cost more
than its estimate.

```text
CELL_SECONDS_CEILING   = ITEMS_PER_CELL / CALLS_PER_SECOND_W1 = 320 / 1.8931 = 169.0 s
CELLS_PER_BLOCK        = 4 widths x 4 grains                                 = 16
BLOCK_SECONDS_CEILING  = 16 x 169.0                                          = 2704.6 s
REPETITIONS_PER_CELL   = floor(RUNG_CAP_SECONDS / BLOCK_SECONDS_CEILING)
                       = floor(10800.0 / 2704.6)                             = 3
```

**`REPETITIONS_PER_CELL` = 3**, and `direction_required(3)` = **3** — all three
repetitions must point the same way ([§9](#9-the-decision-rule)). That is a tight
bar on a very small `n`, and it is stated as small.

### The reserve, and how much the conservatism costs

Costing each cell at its *own* call count instead of the finest grain's:

```text
BLOCK_SECONDS_MODELLED = 4 widths x (320 + 160 + 80 + 40) calls / 1.8931
                       = 1267.8 s
REPETITIONS_RESERVE    = floor(10800.0 / 1267.8) = 8
```

**`REPETITIONS_RESERVE` = 8.** Repetitions 4–8 are dialled **only** while the
rung is still inside `RUNG_CAP_SECONDS`, and any repetition not played is
`ABSENT` by number. `direction_required(8)` = **7**.

**The analysis runs once, after the rung stops.** The verdict is evaluated at
whatever `n` was realised, and the stop condition is a clock rather than a
result — so stopping is not peeking. No verdict is computed mid-rung, and no
repetition is added after a verdict has been seen.

### What this estimate assumes, said out loud

- **The sizing rates were measured non-streaming** and on one seven-minute
  window on one rig ([§2](#2-the-instrument-this-series-runs-on-streaming)).
  They size the rung; they are not its result.
- **The width-1 rate is used for every width.** t8's *lean* width-8 cell read
  0.63× as measured — slower than width 1 — entirely because of one retry. A
  width that genuinely under-performs is a result this rung is built to report,
  and the sizing must not assume it away.
- **Coarse grains are costed as if they were fine ones**, which overstates them
  by roughly the factor between `BLOCK_SECONDS_CEILING` and
  `BLOCK_SECONDS_MODELLED` (2.13×). The reserve exists so that conservatism does
  not silently throw away affordable `n`.
- **Prefix caching is not controlled for.** The shared preamble is byte-identical
  across calls by design, so a server-side prefix cache could absorb part of the
  prefill cost — making every figure here a **lower** bound on what a sweep with
  per-call varying context would pay, never an upper one.
- **The whole block is ~2,400 calls per repetition** (7,200 at `n` = 3). At t8's
  measured 11× prompt-to-completion ratio the dominant spend is prompt tokens on
  Thor, and it is reported as measured rather than predicted.

---

## 9. The decision rule

Applied to whatever comes back, without amendment. It reads committed cell
records and exactly these fields: `width`, `grain`, `repetition`,
`cell_wall_seconds`, `batch_elapsed_seconds`, `retries`, `acceptance.counts`,
`acceptance.dispatched`, `acceptance.prompt_tokens`,
`acceptance.completion_tokens`, `senses_config_hash`.

### Per cell — the validity gates, applied first

Every gate is a rule fixed before the data, not a judgement made after it. A
gated cell **stays in the artifact and in the cost fold**; it is excluded from
the verdict only.

1. **`retry-contaminated`** — any batch whose calls carry `retries > 0` is
   dropped **whole**, and both readings are published (t8's committed remedy). A
   cell left with fewer than `MIN_BATCHES_PER_CELL − 1` = **4** clean batches is
   `VOID`.
2. **`truncated`** — any cell with `absent-truncated > 0` is `VOID`. A truncated
   turn is an instrument event ([#37](https://github.com/agentculture/embodiment/issues/37)),
   and at a 256-token budget it is a *budget* event specifically; scoring it
   would be scoring the instrument.
3. **`refusal-gate`** — a cell whose refusal fraction
   (`refused-off-space + refused-empty`, over dispatched calls that were **not**
   `absent-*`) exceeds `PROTOCOL_GATE` = **0.10** is `VOID` for the outcome axis.
   `0.10` is `league_commander.LENGTH_FRACTION_MAX`, reused for the same
   "the instrument ate the turn" family rather than invented here. **The
   acceptance figure is still published** — see
   [§12](#12-call-acceptance-as-a-per-arm-axis-and-the-33-refutation).
4. **`plan-clamped`** — a cell where
   `acceptance.dispatched × items_per_call ≠ ITEMS_PER_CELL` is `VOID`: the plan
   was clamped, so the registered numerator no longer describes the work done.
5. **`prompt-drift`** — within a grain, across widths, **total prompt tokens must
   agree exactly**. The prompts are composed from harness-authored parts and the
   plan fixes the call set, so prompt tokens are deterministic; width changes
   *when* calls run, never *what* they send. A difference means the two cells did
   not do the same work, so every cell in that (grain) row is `VOID`. Completion
   tokens are reported with their per-cell spread and are **not** gated: decode
   at temperature 0.3 is stochastic, and gating on it would void cells for
   sampling noise. A width that changed *what came back* is instead visible in
   `acceptance.counts`, which is published per cell.

### Per (grain, width-pair) — the direction rule

Wall clock is noisy and a percentage threshold on a median can be smaller than
the instrument's own spread, so the rule is a **direction rule on paired
repetitions**, reusing `league_commander.direction_required`
(`DIRECTION_FRACTION` = 0.8) rather than re-deriving one:

- **`SEPARATED-WIDTH`** — for a grain, `items_per_second` at width `w` exceeds
  width 1's on at least `direction_required(n)` of the `n` paired repetitions,
  for the widest `w` that so qualifies. The reported result is the
  **width curve** per grain, not a single number.
- **`INCONCLUSIVE-WIDTH`** — the cell is graded (gates passed, control present)
  and the direction rule does not fire. **Published as `INCONCLUSIVE`, never
  softened into "a trend", never rescued by a secondary metric.**
- **`CEILING-WIDTH`** — the control's own clock is too short for the instrument
  to resolve anything above it:

  ```text
  MIN_CELL_SECONDS = MIN_BATCHES_PER_CELL x RESOLUTION_SECONDS = 5 x 0.0899 = 0.4495 s
  ```

  `RESOLUTION_SECONDS` = 0.0899 is the largest batch-to-batch half-range among
  t8's committed retry-free thinking-off cells (`rich-off-w1`). **`CEILING` is a
  verdict, not a tie**: a rung whose control sits close enough to the top that
  the required margin cannot fit above it is reported `CEILING` and the climb
  escalates past it rather than recording a draw.

  **At the registered cell size this rule cannot fire, and the arithmetic saying
  so is here rather than hoped for**: the shortest control cell is `batch8` at
  width 1, **21.1 s**, which is **47.0×** `MIN_CELL_SECONDS`. It is registered
  anyway, because the ladder discipline requires a ceiling rule and because a
  rig ten times faster would need one. That it provably cannot fire *is* the
  contrast with the correctness ladder, where `CEILING` fired on rung 1.
- **`VOID`** — one or more validity gates fired. No verdict; the gate is named.
- **`ABSENT`** — the cell was not dialled. Reported by name, never implied.

### `FLOOR`, and why it has no analogue here

The correctness ladder's `FLOOR` — every arm scored zero, the rung overshot —
has no counterpart on a throughput axis: `items_per_second` cannot be zero for a
cell that produced batches, and a cell that produced none is `ABSENT`. Stated so
that its absence reads as a considered omission rather than a gap.

### The margin rule, for the rungs that grade correctness

`W1` does not grade correctness, and neither does `H0` (which uses a direction
rule on a game score — see [§14](#14-rung-h0--the-headroom-screen-the-sweep-is-gated-on)).
The sweep and `P1` do, and they reuse the previous pre-registration's rule
verbatim rather than inventing a second one:

```text
margin_required(a) = max(MARGIN_MIN, ceil(MARGIN_FRACTION * a))
```

with `MARGIN_MIN` = 2 and `MARGIN_FRACTION` = 0.25, and `a` the **smallest**
attempt count among the graded arms, so a cell that happened to run longer
cannot lower the bar the rung is judged at. Both harnesses ship
`separation_margin` = 1 as **provisional**, naming task `t9` as the authority;
this rule is stricter at every `n`, so neither harness can report `SEPARATED`
where this document would not.

---

## 10. The stop rule

1. **`W1` runs first, always.** Grains ascend `unit → pair → batch4 → batch8`;
   within each grain, widths ascend `1 → 2 → 4 → 8`. Width 1 is the control and
   is dialled first at every grain.
2. **`W1` `INCONCLUSIVE-WIDTH` or `VOID` at every grain stops the whole
   ladder.** `W2`, `H0`, the sweep and everything above are `ABSENT` by name,
   and the published answer is *"width did not separate on the axis where
   separation is structural; the flat default stands on evidence."* This is the
   sentence issue #44 asked for, and it is registered before the data so it
   cannot be argued into something softer afterwards.
3. **`W1` `SEPARATED-WIDTH` at any grain continues** to `W2`, then `H0`, then
   the sweep.
4. **`H0` failing does not stop the ladder**; it makes the sweep's **quality
   axis** `ABSENT` by name. The sweep may still run on cost and acceptance, and
   **no secondary metric is promoted into a quality verdict**
   ([§4](#4-the-outcome-metric-and-the-proof-it-is-mandatory)).
5. **Rung `P1` is a separate experiment with its own budget**, in the priority
   order `W1 → W2 → H0 → sweep → P1`. Anything the ladder cap does not reach is
   `ABSENT` by name.
6. `RUNG_CAP_SECONDS` = **10800** and `LADDER_CAP_SECONDS` = **28800**, imported
   by the pin from `examples/league_h2h.py` so they cannot drift. A rung that
   would run past its cap stops; every rung above it is `ABSENT`.
7. **A rung that stops on a cap is reported as stopped on a cap**, with the
   elapsed clock beside it — the failure mode `C2` demonstrated, where a rung ran
   3651.7 s past cap and no decision was written at all.

---

## 11. Rung `W2` — the amortisation cell, computed before it is dialled

`W1` measures the scoped lane. `W2` asks the question that decides whether the
answer matters: **how much of a whole hive drive is the scoped lane?**

The arithmetic is available now, from committed inputs, and is registered as a
result rather than left to be discovered:

```text
TURNS_PER_DRIVE          = 5                       # t24's nine 16000-budget drives
DRIVE_SECONDS            = 5 x [400.0 .. 730.0]    = [2000 .. 3650] s
max_scoped_calls         = 24                      # arch-hive-sampling.json, both tiers
DRIVE_SCOPED_SECONDS_W1  = 24 / 1.8931             = 12.68 s
DRIVE_SCOPED_SECONDS_W8  = 24 / 3.4593             =  6.94 s
```

| quantity | at the slow end of the turn range | at the fast end |
|---|---:|---:|
| scoped lane as a share of one drive | **0.35%** | **0.63%** |
| what width 8 removes from a whole drive | **0.16%** | **0.29%** |

**At the committed budget, width cannot be detected in a hive drive's wall clock
— by arithmetic, before any dial.** A 0.16–0.29% effect sits far below the
cortex's own measured spread; the toolcall probe measured an **8×** spread
between the fastest and slowest run of an *identical* prompt.

The number the Bee-Hive's economic claim actually needs:

```text
CALLS_FOR_VISIBLE_WIDTH = ceil(SCOPED_SHARE_TARGET x DRIVE_SECONDS x CALLS_PER_SECOND_W1)
                        = 379  (fast end)   ..   691  (slow end)
committed max_scoped_calls = 24    ->    15.8x to 28.8x short
```

**`W2` is therefore one paired cell, `n` = 1, and its purpose is to measure a
ratio rather than to compare arms.** One `B1` drive at width 1 and one at width
8, same items, same seed, reporting `scoped_seconds / drive_seconds`. It
confirms or falsifies a figure computed here; a single observation is meaningful
against an arithmetic prediction in a way it would not be against a comparison.
It is labelled `n=1` everywhere it appears.

**This is a finding for the plan, not only for the rung.** `max_scoped_calls`
= 24 is a committed input, and nothing in the spec or in #44 notices that it is
16–29× below the count at which the tier's own economic argument becomes visible.
Raising it is **not** done here: an input edited after a pre-registration cites
it is the drift this discipline exists to prevent. It is reported, and the
decision belongs to whoever reads this before `t10` dials.

---

## 12. Call-acceptance as a per-arm axis, and the #33 refutation

`c20`'s falsifiable prediction: the #32/#33 interface-failure class **disappears**
because the cortex authors the schema it delegates across. `h11` requires
call-acceptance to be measured **per arm** and the prediction to be falsifiable
**both ways**.

### The axis

`arch_hive.AcceptanceLedger` is a separate ledger with a key namespace disjoint
from the outcome record's — `ACCEPTANCE_KEYS` against `OUTCOME_KEYS`, asserted
disjoint by `tests/test_arch_hive.py` rather than trusted. Per arm, per cell, it
publishes `dispatched`, per-outcome `counts`, `acceptance_rate` and
`refusal_rate` over the closed vocabulary:

| outcome | class | what it means |
|---|---|---|
| `accepted` | — | an in-space answer for every item |
| `refused-off-space` | **refusal** | an answer outside the declared enum, or an item unanswered |
| `refused-empty` | **refusal** | no parseable `<id> = <answer>` line at all |
| `absent-timeout` | **absence** | the call missed the batch deadline |
| `absent-transport` | **absence** | the transport failed |
| `absent-truncated` | **absence** | the turn ran out of budget before answering |

### `absent-truncated` is read apart from refusal, and this is load-bearing

`ModelResponse` carries no `finish_reason`
([#37](https://github.com/agentculture/embodiment/issues/37)), so a truncated
worker turn and a schema refusal arrive at the harness as the same object.
`arch_hive` reclassifies a refusal-class result to `absent-truncated` when the
seam's transcript reports `finish_reason == "length"` — and if that reclassification
did not exist, **a `max_tokens` value could publish arm B as refuted by its own
budget.**

Registered, therefore:

- **The refusal rate is computed over the truncation-free denominator**, and
  **both denominators are published** for every cell.
- **A cell with `absent-truncated > 0` is `VOID`** and its budget is reported
  ([§9](#9-the-decision-rule)). A refutation may not be read off a `VOID` cell.
- The registered budget carries a **2.04×** margin at the coarsest grain
  ([§6](#6-call-granularity-derived-from-t8s-overhead-numbers)), so a truncation
  at any grain is a *finding about the budget*, published as one.

### The refutation, pre-declared by name

```text
CALL_ACCEPTANCE_REFUTES_AT = 0.74
```

recomputed by the pin from `arch-hive-sampling.json`'s
`decision.call_acceptance_refutes_at_rate`, itself issue #33's measured figure —
**74% of workspace-arm calls refused on a shape error**, `command` sent as a
string rather than an array.

**A B-tier cell whose refusal rate reaches 0.74 is published as REFUTING
`c20`'s delegate-to-authored-artifacts claim, by name, in the results
document's headline — not smoothed over, not explained away as a tuning
problem.**

**And the asymmetry is registered too, because it is real.** A refusal rate
*below* 0.74 does **not** confirm `c20`. #33's 74% was measured on a free-form
`command` array on a different harness with a different mind; a
harness-authored enum that is re-checked after the call is a different wire, and
observing that the different wire behaves differently is weak evidence for a
claim about *why*. The falsifier is strong and is registered; the confirmation
is weak and is registered as weak.

---

## 13. No security-shaped question enters this sweep

`c17` makes arm B *"the only arm that takes lobes' `worker.forbidden_responsibilities`
literally"* — the two entries being `final_decision` and `security_decision` — and
`h9` requires every call's answer space to be enumerable with no free-text goal
field, asserted structurally.

**Issue #44's own worked example — "is this safe?" — names a
`security_decision`, which that contract forbids.** Re-introducing it would break
the exact claim arm B exists to test. `examples/arch_hive.py` ships an
**observation** instead:

| question | kind | answer space | authority |
|---|---|---|---|
| `pick_option` | slots | `A`..`H`, truncated to the item's option count | `advisory` |
| `classify_load` | literal | `simple`, `complex` | `advisory` |
| `looks_risky` | literal | `clear`, `risky`, **`unclear`** | `advisory` |

`looks_risky` asks *"Does this item look risky to act on as described?"* — a
report about appearance, never a ruling. **`unclear` is first-class in the
enum**, so a worker with no view has an in-space way to say so; that closes the
no-answer shape of [#32](https://github.com/agentculture/embodiment/issues/32)
through the schema rather than through a guess. Every
question is `AUTHORITY_ADVISORY` and nothing in the harness can promote one: the
only place an answer becomes the attempt's output is the cortex's own `finish`
call.

**Registered: exactly these three questions, and no fourth.** No question may be
added to this series after this commit; one that arrived would belong to a new
series with its own pre-registration. The pin asserts that every declared
question's authority is `advisory`, that the harness's
`FORBIDDEN_RESPONSIBILITIES` still reads `("final_decision", "security_decision")`,
and that no declared question's id or ask text names a security decision.

---

## 14. Rung `H0` — the headroom screen the sweep is gated on

The B0/B1/B2 quality sweep needs a grader that is **not ours** — `c22`, and
[`corrections.md` §2](corrections.md)'s four self-authored graders wrong in the
flattering direction in one cycle. The league lane is that grader, and league's
own `outcome.total` is mandatory-populated by league's engine, not by any arm.

**But this repository has measured that metric at no headroom in every committed
configuration:**

| configuration | measured |
|---|---|
| model vs model, three turns (`league-h2h`) | `outcome.total` **0–0 in all six matches** |
| model vs house bot, both arenas (`league-commander`) | **all four architectures produced identical outcomes**; 19/19/19/19, and 10–0 in 6 of 6 |

A mandatory metric with no headroom is the second half of the `league-h2h`
failure: it is what pushed that series onto a tie-break, and the tie-break is
where the optional field lived. **So the sweep does not dial until the metric is
shown to move.**

### The screen, and it costs zero model calls

`H0` plays `HEADROOM_MATCHES` = **5** matches of the sweep's candidate scenario
at its candidate length, **bot against bot**, with one side under league's
engine-enforced `--max-actions` handicap — the same handicap `league_h2h.py`
already applies (`LeagueRung.max_actions`, `--max-actions TEAM:N`). Five is not a
choice: it is `len(league_commander.SEEDS)`, the committed seed count, imported
by the pin. `direction_required(5)` = **4**.

- **PASS** — on at least `direction_required(HEADROOM_MATCHES)` of the matches,
  some side's `outcome.total` is non-zero **and** the handicapped side's is
  strictly the lower of the two. A direction rule rather than a margin, because
  `outcome.total` is a game score and `margin_required` counts attempts — the two
  are different quantities and one must not be applied to the other.
- **FAIL** — otherwise. The sweep's **quality axis is `ABSENT` by name**, the
  sweep publishes cost and acceptance only, and no secondary metric is promoted.

**What a pass does and does not license, stated now.** `H0` tests whether
`outcome.total` can distinguish better play from worse play **at all**. Passing
it does **not** predict that the arms will separate — arms tying at a ceiling is
what `CEILING` is for. Failing it **proves** they cannot, because a metric that
cannot see a mind playing with one hand tied cannot see an architecture
difference. It is a necessary condition, registered as necessary and not as
sufficient.

### The sweep itself

If `H0` passes: B0 (rigid, zero play-time worker calls), B1 (scoped, at the
width and grain `W1` selected) and B2 (= arm `M` in `examples/arch_arms.py`, an
open goal with the worker holding control). Outcome metric: league's
`outcome.total`. Decision rule: [§9](#9-the-decision-rule)'s margin rule.
Cost and call-acceptance reported per arm, never folded into outcome.

**B2 is arm `M` and is not re-implemented.** Its sampling comes from
`arch-arms-sampling.json` and its budget from there too, so a B0/B1/B2 comparison
is a comparison across two committed tables — which is stated here because it is
the one place in this series where two configuration files must agree, and the
run must record both hashes.

---

## 15. Rung `P1` — the compiled policy, its three controls, and both extremes

`c18` and `h10`: the three controls are **pre-registered before any measured
dial**, and escalation rate and call-acceptance are **separate reported axes
that never share a number with outcome**.

### The four arms

| arm | label | source | origin | authoring calls |
|---|---|---|---|---|
| **P** | `policy-compiled` | written by the cortex, once per episode | model | **1** |
| **PR** | `policy-random` | `rng.choice(actions)`, seeded | committed | 0 |
| **PH** | `policy-baseline` | the hand-written rule | committed | 0 |
| **PN** | `policy-noop` | `return None` | committed | 0 |

They differ in **exactly one dataclass field** (`PolicyArm.source`); every other
per-arm property is derived from it, so a control cannot silently diverge on a
second field. `authoring_calls` is cross-checked by the loader against the arm's
structure, so a table granting a control an authoring call raises rather than
quietly measuring a different arm.

The episode is deterministic and harness-authored: **12 situations, seed
20260801, `occlusion_every` = 4 → 3 undecidable situations** (`u4`, `u8`, `u12`),
where *"I don't know"* is the correct return. The harness knows host-side which
those are, which is what makes escalation *quality* gradeable at all.

### Both extremes of escalation rate, pre-declared as informative

Neither extreme changes the outcome verdict. Both are published beside it.

**Collapsed — escalation rate at the top.** `escalation_rate >=
ESCALATION_COLLAPSES_AT` = **1.0** sets the harness's own `collapsed` boolean.
A policy that escalates every decision **has collapsed into the worker arm with
extra latency**, and is reported as collapsed rather than as a compiled policy
that happened to do well. One caveat registered with it: `max_escalation_calls`
= **8** against **12** situations, so a collapsed policy's checkpoint calls are
**clamped at 8** and its cost is under-reported by the clamp. The
`not_dispatched` count is published beside the rate.

**Blind — escalation rate at the bottom.** The harness has **no named flag for
this extreme**, so it is defined here and read from fields the harness does
record:

```text
BLIND  ==  escalation["escalated"] == 0
       and escalation["unescalated_undecidable"] > 0
       and escalation["escalation_recall"] == 0.0
```

A policy that never escalates while undecidable situations went past it **may be
unable to detect its own ignorance**, which is a different failure from deciding
badly and is reported as a different one. Naming it here rather than in the
harness is deliberate: the harness ships a threshold for the extreme it can
threshold, and this document supplies the reading for the extreme it cannot.

### The other two axes, kept apart from outcome

- **`policy_acceptance`** — the compiled artifact's own protocol compliance:
  did every return land in `ACTIONS` or on the escalate sentinel?
  `POLICY_ACCEPTANCE_FLOOR` = **1.0**; below it the arm is `VOID`. This is the
  axis that tells a *strategically inert but valid* policy from one that never
  returned a legal move.
- **`escalation_acceptance`** — the checkpoint call's own acceptance, on the
  identical ledger and identical wire as B1's scoped calls, so the two arms'
  acceptance figures are comparable. `CALL_ACCEPTANCE_REFUTES_AT` = 0.74 applies
  here exactly as in [§12](#12-call-acceptance-as-a-per-arm-axis-and-the-33-refutation).

### `PROVIDER_FAKE` provisions but executes nothing

The model-written policy executes **only** inside the network-less workspace
container, through `run_in_jail`, the harness's only execution path. Under
`--provider fake` headspace **provisions a workspace and executes nothing**:
every arm lands `NO_RESULT`, the vacuity flag never fires, and a green run
proves the plumbing and nothing else.

**Registered: the hermetic pre-flight for `P1` runs against a scripted headspace
double** (headspace's own `script_command`), never against `--provider fake`,
and the live rung runs `--provider docker`. A `P1` cell whose verdict is
`NO_RESULT` or `NO_WORKSPACE` is `ABSENT`, never `WRONG` — no container, no run,
and never a fallback to running model-written code on the host.

---

## 16. The bars that are choices, not measurements

Two numbers below are not derived from anything. They are judgements, and the
whole value of a pre-registration is that they are fixed *before* the data.

| bar | value | what it decides | why this value |
|---|---|---|---|
| `SCOPED_SHARE_TARGET` | 0.10 | `AMORTISATION_FLOOR_ITEMS`, and the call count at which width becomes visible in a drive | a lane under a tenth of a turn cannot be argued to have changed the turn |
| `IDLE_MARGIN` | 100 | the streaming phase-2 idle bound | two orders of magnitude over a measured 0.124 s maximum gap, against a probe margin of ~4,800× on the bound it replaces |

Six further numbers look like bars and are **citations**, so they are named here
to keep the distinction legible:

| number | value | cited from |
|---|---|---|
| `MIN_BATCHES_PER_CELL` | 5 | t8's committed `rich-off-w8` batch count |
| `HEADROOM_MATCHES` | 5 | `len(league_commander.SEEDS)` |
| `DIRECTION_FRACTION` | 0.8 | `examples/league_commander.py`, imported by the pin |
| `PROTOCOL_GATE` | 0.1 | `league_commander.LENGTH_FRACTION_MAX` |
| `MARGIN_MIN` | 2 | `orchestrator-worker-preregistration.md` §7 |
| `MARGIN_FRACTION` | 0.25 | same |

---

## 17. Predictions, as falsifiable statements

**P1 — width separates in the scoped lane.** `items_per_second` at width 8
exceeds width 1's on at least `direction_required(n)` of the paired repetitions,
at every grain. *Falsified by any grain where it does not.* Basis: t8 measured
**1.83×** with realistic context and **3.44×** with lean context on this exact
dispatch path.

**P2 — width is invisible in a hive drive's wall clock.** The measured
`scoped_seconds / drive_seconds` at the committed `max_scoped_calls` = 24 lands
inside **[0.35%, 0.63%]**. *Falsified by a measured share above 1%.* Basis: the
arithmetic in [§11](#11-rung-w2--the-amortisation-cell-computed-before-it-is-dialled),
computed here from committed inputs.

**P3 — coarser grain is strictly faster.** `items_per_second` rises monotonically
across `unit → pair → batch4 → batch8` at every width. *Falsified by any width
where a coarser grain is slower.* Basis: prefill is paid once per call and does
not parallelise — +768 prompt tokens costs +1.289 s at width 8.

**P4 — no truncation at the registered worker budget.** Zero `absent-truncated`
in the whole rung. *Falsified by any.* Basis: 106 of 106 answered thinking-off,
and a 2.04× token margin at the coarsest grain.

**P5 — call-acceptance does not reach #33's level.** No B-tier cell's refusal
rate reaches 0.74. *Falsified by any cell that does — which is published as
refuting `c20` by name.* Basis: the answer space is an enum the harness authored
and re-checks after the call.

**P6 — `H0` fails on the committed league configuration.** *Falsified by `H0`
passing.* Basis: `league-commander` measured four architectures at identical
outcomes on both arenas; `league-h2h` measured 0–0 in 6 of 6.

**P7 — arm P's escalation rate is neither extreme.** `0 < escalation_rate < 1.0`
for the compiled arm. *Falsified either way, and both falsifications are
informative*: 1.0 means it collapsed into the worker arm, and 0.0 with
`unescalated_undecidable > 0` means `BLIND`.

I expect **P2 and P6 to hold**, which would make the honest headline
*"concurrency is real in the scoped lane, invisible in the drive that uses it,
and the sweep's grader has no headroom to measure quality with"* — three
negatives, all of them decisive, and none of them requiring a night of cortex
time to establish.

---

## 18. Rules fixed in advance

1. **A degraded or failed call is data.** A degradation, a refusal, an absence or
   an exhausted transport is counted and reported with its record. It is not
   discarded and not re-run for a better number.
2. **A transport retry contaminates a batch and the batch is dropped whole** —
   t8's committed remedy, because a retried call's neighbours shared its clock.
   **Both readings are published**; the as-measured one is never deleted.
3. **Nothing is re-run to get a better number.** Each cell runs once at the
   registered repetition count. An interrupted rung resumes; it never replays a
   completed cell.
4. **The analysis runs once, after the rung stops** ([§8](#8-cell-sizing-derived-from-measured-throughput)).
   No verdict is computed mid-rung.
5. **The result is published either way**, including `INCONCLUSIVE`, and **every
   cell that did not run is reported `ABSENT` by name.** An honest partial is the
   required outcome; a padded complete-looking one is a failure.
6. **Raw per-call records are committed**, not only verdicts: every call's
   acceptance outcome, `finish_reason` where the seam reports one, both token
   counts, latency, retry count, question id, item ids, width, grain and
   repetition.
7. **A verdict is only quoted with its pre-registered decision rule beside it**,
   and `INCONCLUSIVE` is reported as `INCONCLUSIVE`.
8. **No metric may be added after the first dial**, and no question, grain or
   width outside [§5](#5-rung-w1--the-scoped-lane-width-sweep) and
   [§13](#13-no-security-shaped-question-enters-this-sweep) may be dialled. A new
   one belongs to a new series with its own pre-registration.
9. **Amendments are appended and dated, never edited into a registered
   section** — the convention `league-h2h` established and
   `orchestrator-worker-preregistration.md` §18/§19 followed (those two sections
   live on `owa/t12`, not on this branch). A pre-registration whose prose is
   rewritten after the dial is not a pre-registration; §19's own ledger
   correction, which found a deviation id asserted in prose that no record
   existed for, is the reason that rule is worth repeating.
10. **Every run's preamble records the transport, both streaming bounds, the
    resolved cortex and worker model ids, and both sampling-table hashes.** A
    run without them is not a cell of this series.
11. **The store is scratch.** Every run's eidetic store lives under a temp home
    outside any git work tree.
12. **The worker enters no reference-rig table on anything but a supporting
    verdict.** `d15`'s muse-off rig stays the shipped default while this series
    runs, and an `INCONCLUSIVE` leaves it untouched.
13. **Software presence, not a robot body** (C2). Every cell here is text over a
    socket. Nothing drives hardware.

---

## 19. What this series cannot show

- **Whether width helps a real agent.** `W1` measures a dispatch lane in
  isolation, and [§11](#11-rung-w2--the-amortisation-cell-computed-before-it-is-dialled)
  computes that at the committed budget the lane is under 1% of a drive. A
  `SEPARATED-WIDTH` verdict is a claim about the lane, and the results document
  must say so in those words.
- **Anything about model quality.** One worker build on one box, tens-of-token
  answers over an enum of at most eight values.
- **That the arms are equivalent if `W1` returns `INCONCLUSIVE`.** Three
  repetitions cannot detect a small effect, and failing to find one is not
  finding its absence.
- **Anything about sustained load.** `W1`'s whole block is tens of minutes.
  t8 ruled out nothing beyond a seven-minute window, and t3's own stability
  section had to be superseded once already.
- **What a prefix cache is absorbing.** The shared preamble is byte-identical by
  design and prefix caching is not controlled for, so every prefill figure is a
  lower bound on what varying context would cost.
- **Generalisation past this rig.** One local cortex, one Thor worker, one
  gateway, one day — and, for the first time in this repository, one transport
  that no prior result in this directory shares.

---

## 20. Every registered constant, at the value the pin recomputes

One table, so a reader can check every number in this document against one
place, and so a moved input breaks the pin rather than quietly invalidating the
prose. Nothing here is typed twice: `tests/test_bee_hive_width_preregistration.py`
recomputes each row from the committed input in its `from` column and asserts
the document states it at that value.

| constant | value | from |
|---|---|---|
| `MAX_HIVE_WIDTH` | 8 | `examples/arch_hive.py` |
| `WIDTH_LADDER` | 1, 2, 4, 8 | powers of two up to `MAX_HIVE_WIDTH` |
| `GRAIN_ITEMS` | 1, 2, 4, 8 | `arch-hive-sampling.json`, `grains` |
| `MIN_BATCHES_PER_CELL` | 5 | t8's `rich-off-w8` batch count |
| `RESOLUTION_FLOOR_ITEMS` | 320 | 5 × 8 × 8 |
| `AMORTISATION_FLOOR_ITEMS` | 76 | ceil(0.10 × 400.0 × 1.8931) |
| `ITEMS_PER_CELL` | 320 | max of the two floors |
| `CELLS_PER_BLOCK` | 16 | 4 widths × 4 grains |
| `CELL_SECONDS_CEILING` | 169.0 | 320 / 1.8931 |
| `BLOCK_SECONDS_CEILING` | 2704.6 | 16 × 169.0 |
| `BLOCK_SECONDS_MODELLED` | 1267.8 | per-grain call counts at the width-1 rate |
| `REPETITIONS_PER_CELL` | 3 | floor(10800 / 2704.6) |
| `REPETITIONS_RESERVE` | 8 | floor(10800 / 1267.8) |
| `CALLS_PER_SECOND_W1` | 1.8931 | `worker-scoped-overhead-summary.json`, rich |
| `CALLS_PER_SECOND_W8` | 3.4593 | same |
| `RESOLUTION_SECONDS` | 0.0899 | largest retry-free thinking-off batch half-range |
| `MIN_CELL_SECONDS` | 0.4495 | 5 × 0.0899 |
| `TURNS_PER_DRIVE` | 5 | `arena-budget-raw/graded.json`, cell `C16` |
| `CORTEX_TURN_SECONDS_LOW` | 400.0 | `worker-scoped-overhead-summary.json` |
| `DRIVE_SCOPED_SECONDS_W1` | 12.68 | 24 / 1.8931 |
| `DRIVE_SCOPED_SECONDS_W8` | 6.94 | 24 / 3.4593 |
| `CALLS_FOR_VISIBLE_WIDTH_LOW` | 379 | ceil(0.10 × 2000 × 1.8931) |
| `CALLS_FOR_VISIBLE_WIDTH_HIGH` | 691 | ceil(0.10 × 3650 × 1.8931) |
| `HEADROOM_MATCHES` | 5 | `len(league_commander.SEEDS)` |
| `TOKEN_MARGIN_COARSEST` | 2.04 | 256 / (8 × 15.675) |
| `EPISODE_UNDECIDABLE` | 3 | `arch_policy.demo_episode`, 12 situations, `occlusion_every` 4 |
| `MAX_ESCALATION_CALLS` | 8 | `arch-policy-sampling.json`, `budgets` |
| `MAX_INTER_CHUNK_GAP_SECONDS` | 0.124 | `streaming-probe.md` |
| `MAX_NUM_SEQS` | 2 | `streaming-probe.md` (cortex server only) |
| `IDLE_MARGIN` | 100 | **chosen bar** ([§16](#16-the-bars-that-are-choices-not-measurements)) |
| `SCOPED_SHARE_TARGET` | 0.10 | **chosen bar** (same) |
| `CALL_ACCEPTANCE_REFUTES_AT` | 0.74 | `arch-hive-sampling.json`, `decision` |
| `ESCALATION_COLLAPSES_AT` | 1.0 | `arch-policy-sampling.json`, `decision` |
| `POLICY_ACCEPTANCE_FLOOR` | 1.0 | same |
| `PROTOCOL_GATE` | 0.1 | `league_commander.LENGTH_FRACTION_MAX` |
| `DIRECTION_FRACTION` | 0.8 | `league_commander.DIRECTION_FRACTION` |
| `MARGIN_MIN` | 2 | `orchestrator-worker-preregistration.md` §7 |
| `MARGIN_FRACTION` | 0.25 | same |
| `RUNG_CAP_SECONDS` | 10800 | `examples/league_h2h.py` |
| `LADDER_CAP_SECONDS` | 28800 | same |
