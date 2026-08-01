# Orchestrator-worker architectures — pre-registration

**Written:** 2026-07-31 · **Task:** t11 · **Plan:**
[orchestrator-worker-architectures](../plans/2026-07-31-orchestrator-worker-architectures.md)
· **Spec:** [`2026-07-31-orchestrator-worker-architectures.md`](../specs/2026-07-31-orchestrator-worker-architectures.md)
· **Pin:** `tests/test_orchestrator_worker_preregistration.py` · **Rig:**
[README](README.md)

Committed **before the first measured dial** of the architecture series, which
is what makes it evidence rather than a story told about a number. Provable
from git history: this file and its pin land in one commit on `owa/t11`, and no
`arch-arms*.jsonl` or `arch-league*.jsonl` artifact exists in the repository at
that commit.

**Every number below is recomputed by the pin test from a committed input, not
asserted as a literal.** That is `M3`'s machine-checkable requirement
([next-cycle-candidates.md](../plans/next-cycle-candidates.md)) and it is the
difference between a pre-registration and a wish: if
`worker-throughput-summary.json` says something else tomorrow, the fan-out
width this file pins changes with it and the test fails until this document is
rewritten to match. Four **bars** are choices rather than measurements — they
are named as choices in [§7](#7-the-bars-that-are-choices-not-measurements) and
they are fixed here, before the dial.

Only two things ran before this file was committed, and both are already
published: the four capability probes in
[§1](#1-the-rig-and-where-each-of-its-facts-comes-from) and the offline
hermetic suites of the arm harnesses. Neither is a measured arm.

## Contents

| § | what it fixes | covers |
|---|---|---|
| [1](#1-the-rig-and-where-each-of-its-facts-comes-from) | capability facts, cited from probes and never from `/capabilities` | `c43`, `h32` |
| [2](#2-the-arms) | E / W / M / H, and the refusal without both flat controls | `c10` |
| [3](#3-the-ladder-re-checked-against-the-new-cortex) | the ladder, the ceiling rule, the escalation reserve | `c46`, `h34` |
| [4](#4-the-decision-rule) | separation, `CEILING`, `FLOOR`, `INCONCLUSIVE`, numerically | `c10`, `h6` |
| [5](#5-the-stop-rule) | where the climb stops and what is reported `ABSENT` | `h6` |
| [6](#6-cell-sizing-against-measured-latency) | the `n` a ~57 s cortex turn actually buys | `h6` |
| [7](#7-the-bars-that-are-choices-not-measurements) | the four chosen bars, declared before the dial | `h6` |
| [8](#8-fan-out-width-derived-from-saturation-and-not-from-the-advert) | `FANOUT_WIDTH`, and why not 14 | `c48`, `h35` |
| [9](#9-the-fan-out-accounting-rule-verbatim) | the charge bound, quoted verbatim | `c31` |
| [10](#10-the-sampling-table) | temperature / thinking / `max_tokens` per role per arm | `c33` |
| [11](#11-heterogeneous-rung-declarations) | why each hybrid-graded rung is heterogeneous | `h24`, `h25` |
| [12](#12-the-perception-routing-rung-and-its-per-rung-exemption) | the routing screen and what the exemption relaxes | `c40`, `h31` |
| [13](#13-tool-surfaces-per-arm) | exactly what goes on the wire, per arm | `h28` |
| [14](#14-tool-call-stability-a-validity-gate-never-an-outcome-metric) | protocol stability against its documented baseline | `c45`, `h33` |
| [15](#15-predictions-as-falsifiable-statements) | six predictions with their falsifiers | — |
| [16](#16-rules-fixed-in-advance) | the run discipline | — |
| [17](#17-what-this-series-cannot-show) | the honest limits | — |

## 1. The rig, and where each of its facts comes from

**The gateway advert is not a capability source for this series while it
disagrees with a measurement** (`c43`/`h32`). It has now been wrong twice in
the same direction — `/capabilities` declares no vision for the cortex and the
cortex sees; it declares no video for the cortex and both minds read video —
so the [video probe](video-perception-probe.md)'s summary stands: *the advert
is 0 for 2 as a guide to what a route can do on this rig*.

| fact the series relies on | value | source of record |
|---|---|---|
| cortex accepts image parts and indexes into them | 4 bands, second = purple, guess-proof palette | [cortex-vision-probe.md](cortex-vision-probe.md) |
| cortex holds the tool protocol on an easy task | 10 / 10, `finish_reason: tool_calls` | [cortex-toolcall-probe.md](cortex-toolcall-probe.md) |
| cortex turn latency | median **57.2 s**, tail **204.3 s**, fastest 25.3 s, n=10 | [cortex-toolcall-probe.md](cortex-toolcall-probe.md) |
| worker saturation and effective concurrency | 6.14 at width 8; 8.99 at width 14 | [worker-throughput.md](worker-throughput.md) |
| worker single-stream rate and call latency | 76.4 tok/s; median 10.869 s at width 1 | [worker-throughput-summary.json](worker-throughput-summary.json) |
| the operator's "50 tok/s × 14" | **did not reproduce** — 29.8 tok/s per stream, 268 tok/s aggregate | [worker-throughput.md](worker-throughput.md) |
| both minds read motion from a GIF as `video_url` | cortex and worker, 86 prompt tokens | [video-perception-probe.md](video-perception-probe.md) |
| worker reachability through the exact harness dial path | bare completion + bounded tool loop | [worker-seam-smoke.json](worker-seam-smoke.json) |
| `/capabilities` on the cortex role | reasoning, deciding, planning, `tool_use`, `code_repo_actions`, validation, `final_authority` — **no image, no video** | recorded, **not used** |

The cortex the probes measured is `unsloth/Qwen3.6-27B-NVFP4`. The sampling
table pins the cortex model as `null` on purpose (it is resolved from
`EMBODIMENT_CORTEX_MODEL` or `--cortex-model`), so **every run's preamble must
record the id it dialled, and a run whose recorded cortex id is not the probed
id does not inherit this section's capability facts.** The worker is
`unsloth/Qwen3.6-35B-A3B-NVFP4` on Thor, pinned in the sampling table because
Thor's own advert verifies it.

Prior results in this directory — `arena-budget.md`, `league-h2h.md`,
`league-commander.md` — describe the *previous* cortex. They are used below
only for **turn counts and drive shapes**, never as a quality baseline, and
every such use is named where it happens.

## 2. The arms

Four architectures, differing only in which role drives the top-level loop and
whether the top-level mind keeps the problem's own tool bench.

| arm | label | shape | top-level mind | delegates | keeps bench | role in the design |
|---|---|---|---|---|---|---|
| **E** | existing | flat | cortex | no | yes | today's single-actor rig (`d15`), **re-measured** against the upgraded cortex |
| **W** | worker-solo | flat | worker | no | yes | the control without which no orchestration claim is attributable |
| **M** | manager | orchestrated | cortex | yes | **no** | the cortex orchestrates; the worker executes all ground work |
| **H** | hybrid | orchestrated | cortex | yes | yes | the cortex routes what it judges simple and keeps the rest |

`ARMS` is data in `examples/arch_arms.py`; nothing in the harness branches on
an arm id. `M` and `H` differ by exactly one field (`keeps_work`).

**No verdict issues without both flat controls.** `arch_arms.analyse` refuses —
it returns no verdict at all for a rung missing a *gradeable* cell for either
`E` or `W`, rather than a hedged one computed from the arms that did run. Arm E
without arm W cannot tell orchestration from the worker simply being
sufficient; arm W without arm E cannot tell either from the rig it would
replace. That refusal is machinery, not a promise, and the pin test exercises
it in both directions.

**The cortex keeps final authority in every arm except W, where the worker *is*
the top-level mind and is framed as such.** `finish` lives on the orchestrator
surfaces and on no other; the worker's terminal verb is `report`, which ends
the child's drive and can never end the task ([§13](#13-tool-surfaces-per-arm)).

## 3. The ladder, re-checked against the new cortex

**The models changed under this design, so the ladder's difficulty is a
question, not an inheritance** (`c46`/`h34`). What is already known about where
the new cortex sits:

- it closed **10 of 10** runs through a `finish` tool on a two-step arithmetic
  task — *a ceiling with no headroom*, which the probe itself says measures
  nothing;
- the *older, weaker* cortex already scored **3 of 3** on the subset problem
  (`C1`) at a 16000-token budget ([live-suite-and-challenges.md](live-suite-and-challenges.md));
- four prior arm-comparisons in this repo — `association-work`,
  `arena-series`, `muse-arms`, `league-commander` — died tied at the top of
  their range.

So `C1` is pre-declared a **ceiling risk on the evidence available**, and the
ladder is built to escalate past it rather than to report it as a tie.

### Ladder A — the correctness climb

| # | rung | lane | problems | heterogeneous | why it is harder than the rung below |
|---|---|---|---|---|---|
| 1 | `C1` | challenge | `subset` | no | 144 candidate subsets, a planted trap at 72; **protocol**-class failure. The ceiling probe of this ladder |
| 2 | `C2` | challenge | `register` | no | 1,920 states; **capacity**-class — an exhaustive search no mind does reliably in its head |
| 3 | `C3` | challenge | `entropic`, `entropic_constrained` | no | 30,720 states, and a matched pair: `3` is under-determined at 17 solutions while `3b` is unique, so a confabulating mind cannot pass both |
| 4 | `C4` | challenge | `subset`, `register`, `entropic` | **yes** | the mixed battery — three problems in one cell, and the only challenge rung where routing has room to vary |
| 5 | `K1` | coding | `parity_subsets`, `preimage_count`, `register_recover` | **yes** | the mind must *write executable code*, graded by running it against 9 / 14 / 7 committed cases inside the network-less workspace. `register_recover` is `C2`'s combinatorial family, programmed rather than solved in the head |

Rungs `C1`–`C4` are `examples/arch_arms.py`'s committed `LADDER`, cited by id
rather than restated. `K1`'s three problems, their statements and their
brute-force-verified answers are already committed in
`docs/challenge-problems.md` and `examples/challenge_coding.py`; the run task
registers them into `arch_arms.PROBLEMS` with the difficulty mapping declared
in [§11](#11-heterogeneous-rung-declarations) and **changes no grader**.

### Rung F — the parallel fan-out rung (not on ladder A)

`L1` — league scenario `c-frontier-1`, three roles per side (`defender`,
`harvester`, `scout`), seeds from the committed `league_commander.SEEDS`. It is
**deliberately not on the correctness ladder**: its primary axes are wall clock
and tokens, where separation is structural rather than hopeful — a flat arm
cannot dispatch concurrent units at all. Correctness is reported beside them
and is not the verdict.

### Rung P — the perception-routing screen (a separate factor)

Two stages, its own rule, and its own exemption — see
[§12](#12-the-perception-routing-rung-and-its-per-rung-exemption).

### What happens when a rung ceilings — and what happens on a fifth tie

1. **A rung where arm E scores at ceiling is `CEILING`, never a tie.** The rule
   is numeric and lives in [§4](#4-the-decision-rule). A `CEILING` rung
   produces **no verdict**, is reported by name with arm E's cell committed,
   and **the climb continues to the next rung.** The other three arms are not
   dialled at that rung ([§5](#5-the-stop-rule)), so a ceiling costs one cell
   rather than four.
2. **If every rung of ladder A ceilings**, the ladder verdict is
   `INCONCLUSIVE` with the ceiling documented at each rung, and the
   pre-registered next step is **a new problem set authored under the `M2`
   grader kit — not a larger `n` on these rungs.** `M4` is explicit that
   continuing to run a ceiling design at larger `n` measures nothing, and this
   would be the fifth such tie. That sentence is written here, before the data,
   precisely so it cannot be softened into "needs more samples" afterwards.
3. **The escalation reserve is named in advance**, so escalation is not
   invented after seeing the data. In order: `K1` at `hard` only
   (`register_recover` alone, the hardest committed coding problem), then `L1`
   at its reserve seeds. Both are committed material. **No problem authored
   after the first measured dial may enter this series** — it would enter a new
   one, with its own pre-registration.

## 4. The decision rule

Applied to whatever comes back, without amendment. It reads committed cell
records (`kind: "cell"`), and it reads exactly these fields: `arm`, `rung`,
`route`, `attempted`, `correct`, `truncated_calls`, `calls`,
`senses_config_hash`.

### Per cell

`score(arm) = correct / attempted`. Two exclusions are applied **first**, and
both are rules fixed before the data arrives rather than judgements made after
it:

- **`truncated-calls`** — any cell with `truncated_calls > 0` is excluded from
  the verdict. It stays in the artifact and in the cost fold. A truncated turn
  returns empty content, which the loop reads as a mind with nothing to say
  (embodiment#37); scoring it would be scoring the instrument. This is
  *stricter* than `league-commander`'s 10% length-fraction gate, deliberately.
- **`degenerate-rung`** — the hybrid arm's cell is excluded on any rung whose
  problems all carry one difficulty. See
  [§11](#11-heterogeneous-rung-declarations).

A cell with fewer than `MIN_ATTEMPTS_GRADEABLE` = 2 attempts cannot separate at
any margin and is reported `ABSENT` rather than graded. That is not a chosen
number: it is the smallest `a` for which `a ≥ margin_required(a)`. A rung whose
flat-arm cell is absent for that reason refuses in the ordinary way — an
under-sized control is a missing control.

### Per rung

Evaluated in this order. The first that fires decides.

1. **`ABSENT`** — no cells recorded for the rung.
2. **`CEILING`** — `attempted(E) − correct(E) < margin_required(attempted(E))`.
   The control sits close enough to the top that the required margin does not
   *fit* above it, so no arm could out-score it by the required amount even in
   principle. At `ATTEMPTS_PER_CELL` = 6 this fires when arm E scores 5 or 6 of
   6. **Reported as `CEILING`, never as a tie, and never as `INCONCLUSIVE`.**
3. **`REFUSED`** — a gradeable cell is missing for `E` or `W`. No verdict, and
   the refusal names which control is missing and why.
4. **`FLOOR`** — every gradeable arm scored `correct == 0`. The rung is above
   the whole field; it too measures nothing, and unlike a ceiling it means the
   ladder **overshot**.
5. **`SEPARATED`** — the leading gradeable arm's `correct` exceeds the
   runner-up's by at least `margin_required(attempted)`, where

   ```text
   margin_required(a) = max(MARGIN_MIN, ceil(MARGIN_FRACTION * a))
   ```

   with `MARGIN_MIN` = 2 and `MARGIN_FRACTION` = 0.25, and where `a` is the
   **smallest** attempt count among the gradeable arms — so a cell that
   happened to run longer cannot lower the bar the rung is judged at. At
   `ATTEMPTS_PER_CELL` = 6 that is a margin of **2**. The rung reports the
   implied arm ordering; a cycle is reported as cyclic, never smoothed into a
   ranking that does not exist.
6. **`INCONCLUSIVE`** — graded, both controls present, not at ceiling or floor,
   margin below the requirement.

**`arch_arms.analyse`'s own `separation_margin` is 1, and it is a *lower*
bound, not the rule.** The harness ships that value as provisional and names
task `t11` as the authority (`arch-arms-sampling.json`, `decision.why`). This
pre-registration is stricter at every `n`: the pin test asserts
`margin_required(a) ≥ 1` for every `a`, so the harness can never report
`SEPARATED` where this document would not. A row `analyse` calls `SEPARATED` at
a margin below `margin_required` is published as **`INCONCLUSIVE`, with both
numbers shown.** The sampling table is not edited to say so — an amendment to
another task's committed input would be a quieter change than this paragraph.

### Rung F — wall clock and tokens, not correctness

Wall clock is noisy on this rig: the cortex probe measured an **8× spread**
between the fastest and slowest run of an *identical* prompt (25.3 s to
204.3 s). A percentage threshold on a median would be smaller than the
instrument's own noise, so rung F uses a **direction rule on paired matches**
instead:

- **`SEPARATED-COST`** — an orchestrated arm's per-match wall clock is lower
  than *both* flat arms' on at least `direction_required(n)` of the `n` matches
  played at the same seed, where `direction_required` is
  `examples/league_commander.py`'s committed function
  (`DIRECTION_FRACTION` = 0.8, reused rather than re-derived).
- **`INCONCLUSIVE-COST`** otherwise.
- Completion, prompt, reasoning and content tokens are reported per arm per
  match either way, with the direction stated.

At `LEAGUE_MATCHES` = 2 this requires **both** matches to point the same way.
That is a tight bar on a very small `n`, and it is stated as small.

### Cost decides what quality does not

**Cost is a result, not overhead.** If ladder A returns no `SEPARATED` rung,
the publishable answer is the cost comparison with its direction — the
precedent `league-h2h` set, and the axis `league-commander` actually resolved
(2.4–4.4× for identical results). Wall clock, all four token counts,
truncation counts, delegation counts, `model_turns` and `child_model_turns` are
reported per arm at every rung regardless of the correctness verdict.

## 5. The stop rule

- Ladder A climbs `C1 → C2 → C3 → C4 → K1`, in that order.
- **Arm E is dialled first at every rung.** If its cell meets the `CEILING`
  rule, the remaining three arms are **not dialled** at that rung: the rung is
  recorded `CEILING`, its three unrun cells are `ABSENT` by name, and the climb
  moves up. A ceiling rung therefore costs one cell, not four.
- `CEILING`, `REFUSED` and `INCONCLUSIVE` **continue** the climb.
- **`SEPARATED` stops it.** Every rung above the separating rung is reported
  `ABSENT`, prominently and by name — never implied, never padded to look
  complete.
- **`FLOOR` also stops it**, upward. Every rung above a floored rung is
  strictly harder, so climbing further cannot resolve anything; those rungs are
  `ABSENT` and the ladder verdict is `INCONCLUSIVE` with the overshoot recorded.
- Rungs `F` and `P` are separate experiments with their own budgets. The
  cycle's live-budget priority order is **ladder A, then F, then P**, and
  anything the ladder cap does not reach is `ABSENT` by name.
- `RUNG_CAP_SECONDS` = 10800 and `LADDER_CAP_SECONDS` = 28800 — reused verbatim
  from `league-h2h`'s amendment 2 rather than re-invented, and imported from
  `examples/league_h2h.py` by the pin test so they cannot drift. A rung that
  would run past its cap stops; every rung above it is `ABSENT`.

## 6. Cell sizing against measured latency

The cortex probe's unplanned finding is the binding constraint on this whole
series: **~57 s median per cortex turn, with a 204 s tail.** The probe asked
that `t11` size cells against that rather than against optimism. So `n` is
derived here, not asserted.

### The model, and every input it takes

```text
seconds(one attempt of one arm) = turns_per_attempt(arm) × seconds_per_turn(role)
```

| input | value | recomputed from |
|---|---|---|
| `CORTEX_TURN_MEDIAN_SECONDS` | 57.2 | [cortex-toolcall-probe.md](cortex-toolcall-probe.md), parsed |
| `CORTEX_TURN_TAIL_SECONDS` | 204.3 | same |
| `WORKER_CALL_MEDIAN_SECONDS` | 10.869 | width-1 `latency_seconds_median`, [worker-throughput-summary.json](worker-throughput-summary.json) |
| `TURNS_PER_DRIVE` | 5 | `ceil(mean(model_turns))` over the nine 16000-budget drives in [`arena-budget-raw/graded.json`](arena-budget-raw/graded.json) |
| `ORCH_TURNS_PER_DRIVE` | 9 | `ceil(TURNS_PER_DRIVE × 24 / 14)` — the committed `M`/`E` `max_steps` ratio |
| `RUNG_CAP_SECONDS` | 10800 | `examples/league_h2h.py` |
| `LADDER_CAP_SECONDS` | 28800 | same |

One **attempt-set** is one attempt of each of the four arms on one problem:
`TURNS_PER_DRIVE` cortex turns for E, the same count of worker turns for W, and
`ORCH_TURNS_PER_DRIVE` for each of M and H — 23 cortex turns and 5 worker turns
in total. At the median clock that is **1369.9 s**; at the tail clock,
**4753.2 s**.

```text
ATTEMPTS_PER_CELL = min( floor(RUNG_CAP_SECONDS   / median_attempt_set),
                         floor(LADDER_CAP_SECONDS / tail_attempt_set) )
                  = min(7, 6) = 6
```

**Two constraints, because one is not enough.** Sizing on the median alone
ignores a tail measured at 3.6× the median — an unlucky rung would eat the
whole night. Sizing on the tail alone would refuse a series the rig can plainly
afford most of the time. So the median must fit the **rung** cap, and the tail
must fit the **ladder** cap: even at its worst, one rung may not consume more
wall clock than the entire ladder was budgeted.

**`ATTEMPTS_PER_CELL` = 6.** The repetition count falls out of the rung's width
rather than being chosen: `repetitions = ATTEMPTS_PER_CELL // problems`.

| rung | problems | repetitions | attempts per cell | `margin_required` |
|---|---|---|---|---|
| `C1` | 1 | 6 | 6 | 2 |
| `C2` | 1 | 6 | 6 | 2 |
| `C3` | 2 | 3 | 6 | 2 |
| `C4` | 3 | 2 | 6 | 2 |
| `K1` | 3 | 2 | 6 | 2 |

`LEAGUE_MATCHES` = 2 comes out of the identical model fed the arena's own
measured turn count — 13 cortex completions per three-turn match
(`CORTEX_TURNS_PER_MATCH` = 13, `ORCH_TURNS_PER_MATCH` = 23) — giving 3516 s
per match-set at the median and 12195 s at the tail, so
`min(3, 2) = 2`. `LEAGUE_MATCHES_RESERVE` = 5 is the number of committed seeds;
matches 3–5 are dialled **only** if the rung is still inside its cap, and any
seed not played is `ABSENT` by seed.

### What this estimate assumes, said out loud

- **`TURNS_PER_DRIVE` is measured on the previous cortex, in the arena, at
  `max_steps=16`.** It is used here as a *drive-shape* number, not a quality
  number. A harder rung will take more turns and longer turns, so **6 is
  optimistic and the caps are the real guard.**
- **The orchestrated arms are modelled as if every charged turn were a cortex
  turn.** Child turns are charged to the parent's budget but execute on the
  faster worker, so this is deliberately conservative — the orchestrated arms
  should come in under their estimate, not over.
- **The worker's 10.869 s is a floor.** It was measured at `max_tokens=1200` on
  two-sentence prompts; this series runs at 16000 on real problems. Arm W's
  clock is therefore reported as measured and is not claimed to be predicted.
- **The coding rung adds container time** (workspace provisioning and
  execution) that no committed measurement covers. `K1` is expected to overrun
  its estimate and its cap will bite first.
- **If every drive instead exhausted its budget** (14 / 24 / 24 cortex turns),
  the same arithmetic gives `ATTEMPTS_FLOOR` = 2. **The honest range is 2 to 6
  attempts per cell**; the series targets 6 and reports what it realised. A
  cell is never padded to look like its target.
- **At the median clock the full five-rung ladder does not fit the ladder cap**
  — 5 × 6 attempt-sets is **11.4 h** against an 8 h `LADDER_CAP_SECONDS`. The
  series is affordable only if it ceilings cheaply (one cell per ceiling rung)
  or separates early. Stated now rather than discovered at hour eight.

**`n` is small and is stated as small. No claim in the results document may be
stated without its `n`.**

## 7. The bars that are choices, not measurements

Four numbers below are not derived from anything — they are judgements, and the
whole value of a pre-registration is that they are fixed *before* the data.
They are collected here so nobody has to hunt for which is which.

| bar | value | what it decides | why this value |
|---|---|---|---|
| `MARGIN_MIN` | 2 | the smallest correct-count gap that counts as separation | one attempt's difference is a coin flip at any `n` this rig affords |
| `MARGIN_FRACTION` | 0.25 | how the margin grows with `n` | keeps the bar meaningful if a cheap rung ever affords a large `n` |
| `PARALLEL_EFFICIENCY_BAR` | 0.75 | the fan-out width | a width that returns under three-quarters of its nominal parallelism is buying queueing |
| `MARGINAL_GAIN_BAR` | 0.25 | the fan-out width, independently | a width whose marginal stream adds under a quarter of the best marginal stream is past saturation |

`DIRECTION_FRACTION` = 0.8 is **not** in this table: it is reused from
`league-commander`'s committed pre-registration and imported from its harness,
so it is a citation rather than a fresh choice.

## 8. Fan-out width, derived from saturation and not from the advert

**`MAX_FANOUT_WIDTH = 14` in `examples/orchestrator_tools.py` was set to the
operator-supplied stream count, and the measurement does not support it**
(`c48`/`h35`). t3 measured the number and the operator's figure did not
reproduce in either reading.

`FANOUT_WIDTH` = **8**, derived from
[worker-throughput-summary.json](worker-throughput-summary.json) by two
independent rules that must agree:

| width | effective concurrency | parallel efficiency | marginal aggregate tok/s per added stream |
|---|---|---|---|
| 1 | 0.996 | 0.996 | — |
| 2 | 1.599 | 0.800 | 26.23 |
| 8 | 6.140 | **0.768** | 25.30 |
| 14 | 8.993 | 0.642 | **2.33** |

1. **Parallel efficiency** (`effective_concurrency / width`) must be at least
   `PARALLEL_EFFICIENCY_BAR` = 0.75. The widest measured width that clears it
   is **8**; width 14 returns 0.642.
2. **Marginal aggregate gain** per added stream must be at least
   `MARGINAL_GAIN_BAR` = 0.25 of the best marginal gain observed (26.23 tok/s,
   at width 2 — a bar of 6.56). Width 8 returns 25.30; width 14 returns
   **2.33**, which is 9% of the best. The widest width that clears it is **8**.

Both rules select 8, from the same committed data, without agreeing on a
method. **No width above measured saturation is pre-registered, so no
above-saturation justification is owed** — which is the honest outcome of the
requirement, not an evasion of it.

Two further facts, both computed from committed constants:

- **The committed grant cannot fund a width-14 fan-out.** The fan-out asks the
  loop for `DEFAULT_FANOUT_MAX_STEPS` = 12 turns and partitions that one grant
  across its units, with `MIN_UNIT_GRANT` = 1. So
  `MAX_FUNDABLE_WIDTH` = 12 // 1 = **12**: at width 14, `partition(12, 14)`
  produces two zero slices and two units are refused `unfunded` before
  dispatch. At width 8, `partition(12, 8) = (2, 2, 2, 2, 1, 1, 1, 1)` — every
  unit funded, and the slices sum to exactly the grant. This is a second,
  independent reason 14 is not an operating width.
- **`MAX_FANOUT_WIDTH` = 14 stays in the harness and is not edited by this
  task.** It is a structural *refusal ceiling* — it declines units beyond it —
  and it never *sets* a width. The series dials `FANOUT_WIDTH` = 8 and never
  more. The gap between the two numbers is named here so that it reads as a
  deliberate belt-and-braces bound rather than as drift.

**And the series will not reach 8 either.** The only league rung that fans out
is `L1`, whose scenario `c-frontier-1` fields **three** roles per side, so
`REALISED_FANOUT_WIDTH_L1` = 3. A width-8 fan-out needs a scenario with at
least eight simultaneously-idle units and league's committed scenarios do not
provide one. This is recorded as an honest limit of the experiment rather than
left for a reader to infer from a ceiling that never bites: **this series
measures a three-wide fan-out, and any claim it makes about width is a claim
about three.**

## 9. The fan-out accounting rule, verbatim

Quoted from `examples/orchestrator_tools.py`'s `FANOUT_ACCOUNTING_RULE`, which
the spec requires to appear here verbatim. The pin test compares this block to
that string character for character.

<!-- FANOUT_ACCOUNTING_RULE:BEGIN -->
```text
A fan-out spends what ONE serial spawn may spend, and never more.

The loop hands the seam a single SubagentCall carrying a single grant,
`call.max_steps`, already narrowed to what the parent has left. That one grant
is PARTITIONED across the units before any of them starts — never shared,
never re-checked under contention.

1. `partition(grant, width)` splits the grant into per-unit slices whose sum is
   exactly the grant. It is a pure function of two integers, it runs on the
   calling thread before a single unit is dispatched, and it is the only
   producer of a unit's budget.
2. A unit whose slice is zero is NOT DISPATCHED. `embodiment.loop.run` takes
   `max(1, max_steps)` turns, so a zero-slice unit would still spend one turn
   nothing budgeted. An unfunded unit is refused before dispatch and the
   refusal is reported. That is the bound working, not a failure, so it mints
   no degradation — the reading `SPAWN_REFUSED_BUDGET` already gets.
3. Each dispatched unit is driven by `embodiment.loop.run(max_steps=slice)`,
   which bounds its own turns at its slice. Its actual spend is charged
   verbatim; a unit that somehow exceeds its slice is charged the overspend AND
   records a degradation, exactly as `loop._charge_child` does.
4. A unit that has not returned by the deadline is charged its WHOLE slice, not
   the zero turns it never reported. It may still be running, and a bound that
   assumes an absent worker spent nothing is not a bound.
5. No unit may borrow another's unspent remainder. Reclaim would need a shared
   counter read under contention, and a bound that depends on how threads
   interleave is not a bound. Unspent turns go back to the parent unspent.

Therefore charged = the sum over dispatched units of (what it actually spent,
or its whole slice when it is absent) <= the sum of the slices <= the grant =
call.max_steps <= what the parent had left. Concurrency changes WHEN turns are
spent, never HOW MANY.

Width is bounded independently by MAX_FANOUT_WIDTH, and every unit is a leaf
(allowance NO_SPAWNS), so a fan-out adds exactly one level to the tree and no
branching the grant does not already pay for.
```
<!-- FANOUT_ACCOUNTING_RULE:END -->

**A child failure degrades to a recorded partial result, never an aborted
drive** (C3). `fanout-partial` is the exit recorded when any unit is refused,
absent or failed; `fanout-unit-timeout` and `fanout-unit-overspend` are
degradations; `unfunded` and `over-width` are the bound working and mint no
degradation, but are reported on the plan and read back to the acting model as
text.

## 10. The sampling table

The table lives in
[`arch-arms-sampling.json`](arch-arms-sampling.json) — an **input** read by
`examples/arch_arms.py`, not a run artifact. That file stays the single source;
what follows is a reader's copy, and the pin test loads the JSON through
`arch_arms.load_config` and checks every cell below against it, so the copy
cannot quietly become a second set of values. `configurations.md` records
temperature as a hidden variable that confounded an entire prior series, and
`arch_arms` defines **no fallback**: a missing cell raises rather than quietly
becoming 0.3.

| arm | role | temperature | thinking | `max_tokens` |
|---|---|---|---|---|
| E | cortex | 0.3 | on | 16000 |
| E | senses | 0.3 | off | 16000 |
| W | worker | 0.3 | on | 16000 |
| W | senses | 0.3 | off | 16000 |
| M | cortex / worker | 0.3 / 0.3 | on / on | 16000 / 16000 |
| M | senses | 0.3 | off | 16000 |
| H | cortex / worker | 0.3 / 0.3 | on / on | 16000 / 16000 |
| H | senses | 0.3 | off | 16000 |

| arm | `max_steps` | `worker_max_steps` | `spawn_allowance` |
|---|---|---|---|
| E | 14 | 0 | 0 |
| W | 14 | 0 | 0 |
| M | 24 | 6 | 3 |
| H | 24 | 6 | 3 |

- **Temperature 0.3** for every dialled role in every arm — the operator's
  stated 0.1–0.3 heuristic for correctness work, and every rung here is a
  correctness task.
- **`max_tokens` 16000** is `d16`: t24 measured the shipped 2048 default
  truncating **6.0%** of completions with **zero** degradations recorded,
  because `ModelResponse` carries no `finish_reason` (embodiment#37). The
  budget is *sufficient* rather than *equal*, and cost is read off tokens
  actually consumed.
- **`thinking: on`** puts `{"chat_template_kwargs": {"enable_thinking": true}}`
  on the wire; the mapping is data in the same file, so a mode with no entry is
  a config error rather than a silent no-op.
- **Senses is configured in every arm and dialled by none of the text rungs.**
  Its configuration is *hashed*, and `arch_arms` refuses to run when two arms'
  senses configuration differs — so the constant is checkable from a committed
  artifact rather than trusted. The hash starts biting on real traffic at
  rung P.
- **Budgets are sufficient, not equal.** The loop charges every child turn to
  the parent's `max_steps`, so capping M and H at the flat arms' 14 would
  measure the budget rather than the architecture. Fairness in cost terms is
  recovered by reporting turns and tokens actually consumed.

### Reasoning tokens are reported separately — and this endpoint reports none

Every cell reports reasoning tokens separately from content tokens. **The
committed measurement is that the split does not exist on this rig:** all 52
raw records in the throughput series carry `reasoning_token_source: "absent"`,
meaning neither `usage.completion_tokens_details.reasoning_tokens` nor the flat
`usage.reasoning_tokens` ever appeared. Pre-registered consequence: an absent
count is recorded as **`None`, never folded into a `0`**, and the
character-level split (`content_chars` vs `reasoning_chars`) is reported beside
it and labelled a proxy. The throughput series measured reasoning text at ~90%
of every completion, so this is not a rounding matter.

## 11. Heterogeneous-rung declarations

**The hybrid arm's defining behaviour is per-subtask routing, and on a
single-difficulty rung it has nothing to route.** There the hybrid is
structurally identical to the manager or to a flat arm and its cell measures
nothing. So:

**The exclusion rule, stated in advance:** a rung whose problems all share one
`difficulty` value is *degenerate for the hybrid*. The hybrid cell is still
run, still recorded, and still folded into cost — and it is **excluded from the
verdict** with `excluded: "degenerate-rung"`. Heterogeneity is **computed** by
`Rung.heterogeneous()` from the problem registry, never declared by a flag a
human sets, and the pin test recomputes it for every rung on the ladder.

| rung | difficulties present | hybrid graded? | why |
|---|---|---|---|
| `C1` | `simple` | **no — degenerate** | one problem, one difficulty; nothing to route |
| `C2` | `complex` | **no — degenerate** | one problem, one difficulty |
| `C3` | `complex` only | **no — degenerate** | two problems, both capacity searches — the pair is matched on difficulty by design |
| `C4` | `simple`, `complex` | **yes** | `subset` is a **protocol**-class problem tractable in the head — the routable-simple end — beside `register` and `entropic`, two **capacity** searches. The routing decision has two genuinely different kinds of subtask to choose between |
| `K1` | `simple`, `complex` | **yes** | `challenge_coding` declares its own rungs `easy` / `medium` / `hard`. Mapping, fixed here: `parity_subsets` (easy) → `simple`; `preimage_count` (medium) → `complex`; `register_recover` (hard) → `complex`. Nine cases against fourteen and seven, a closed-form recurrence against a list-valued reconstruction |
| `L1` | mixed by construction | **yes** | a round holds routine per-unit moves *and* a commander-level fusion decision at the same decision point. `arch_league`'s own rung note says the three-role scenario is "mixed enough that the hybrid can both route and keep"; a two-role scenario would collapse the hybrid into the manager |
| `P2` | inherits its rung | inherits | stage 2 rides whichever rung it is run on and inherits that rung's heterogeneity verbatim |

**A second, behavioural degeneracy is declared now so it cannot be discovered
later.** A hybrid cell whose routing log shows **zero** delegations, or
delegations on **every** subtask, has collapsed into arm E or into arm M
respectively. Such a cell is **not** excluded from the correctness verdict —
excluding it would be judging after the data, and a hybrid that chose never to
delegate *is* the hybrid's measured behaviour. What is forbidden in advance is
the *claim*: **no statement of the form "routing helped" or "routing hurt" may
be made from a collapsed cell**, and every hybrid cell publishes its delegation
count beside its score so a reader can see which it was.

## 12. The perception-routing rung and its per-rung exemption

Perception routing is a factor **orthogonal** to the architecture factor, and
the two are screened rather than crossed: twelve cells at any `n` this rig
affords cannot resolve an interaction, which is how t18 and t19 died.

### Stage 1 — four cells, arm E only

| route | what the deciding mind receives | what it does not |
|---|---|---|
| `text` *(control)* | the fogged team-scoped briefing as prose | no image part anywhere |
| `native` | the same fogged snapshot rendered to PNG, as an `image_url` content part | no senses call |
| `described` | a senses (Gemma 4 12B) description of that PNG, as text | no image part reaches the deciding mind |
| `both` | the senses description **and** the PNG | — |

The spec's decision said "~3 cells". The fourth is the already-committed `text`
route, kept as the **control**: without it a route comparison can only say
which vision route is least bad, never whether vision helps at all. Adding a
control is the one widening this pre-registration makes to the frame's cell
count, and it is named here rather than folded in.

The winner is the route with the highest correct fraction; ties break on
prompt-token cost, then on fewest moving parts (`text` before `described`
before `both`). **Stage 2 dials only the route stage 1 selected**, across all
four arms — never a post-hoc pick after seeing stage 2 data.

### The exemption, and exactly what it relaxes

**Rung P is exempt from the information-matching rule (`c22`/`h16`)**
(`c40`/`h31`). That rule exists so an image cell cannot smuggle state past its
text twin. Routing arms deliberately vary information content — a senses
description is lossy compression of the image *by design* — so matching would
forbid the very contrast the rung measures.

The exemption is **per rung and it is this rung only**:

- **It relaxes information matching. It does not touch fog scoping.** `h15`
  binds every route unchanged: no route may show a mind a cell its visibility
  excludes, and the renderer's adversarial fog-leak fixtures run against every
  route.
- **It does not extend to the league map-image cells.** Those cells stay fully
  information-matched: every image briefing has a committed text twin derived
  from the same fog snapshot (same turn, seat, snapshot hash), and the harness
  refuses to run a cell whose pairing check fails.
- v1 renders the **team-scoped** view for every mind. No vision-radius constant
  exists anywhere in embodiment's harness code, and none may be added — a
  harness-duplicated fog computation would desync into exactly the leak `h15`
  forbids.

**The asymmetry, stated rather than implied — and corrected.** The spec's
assumption list says *"the commander in manager/hybrid arms is the text-only
27B: it cannot consume map images"*. **That assumption was written before the
vision probe and is superseded by it**: the cortex demonstrably accepts image
parts (§1), so both flat arms put an image in front of their top-level mind on
the image routes. What remains true is a *design* asymmetry rather than a
capability one: in arms M and H the commander reads the team-knowledge brief
while units read what their route gives them, so `native` and `both` at those
arms measure unit-level perception plus the commander's fusion of
visually-informed unit reports — not the commander's own sight. The correction
is recorded here rather than folded in, because the superseded assumption is
still readable in the spec.

## 13. Tool surfaces per arm

The harness passes **exactly** this surface. A tool absent from the enumeration
raising `UnknownToolError` is the desired behaviour, not a bug.

### Challenge and coding lanes

| arm | top-level surface | worker child surface |
|---|---|---|
| E | the problem's own bench **+ `finish`** | — |
| W | the problem's own bench **+ `finish`** (driven by the worker as the top-level mind) | — |
| M | `delegate`, `note`, `finish` — **no ground-work verb at all** | bench minus `finish`, **+ `report`** |
| H | the problem's own bench **+ `delegate`, `note`, `finish`** | bench minus `finish`, **+ `report`** |

Per problem, the bench is: `subset` — `check_subset`,
`count_nonconsecutive`; `register` — `apply_routine`, `hamming_distance`,
`check_order`; `entropic` and `entropic_constrained` — `apply_routine`,
`hamming_distance`, `check_c_volatility`.

- **`finish` appears on the orchestrator surfaces and on no other.** That
  sentence *is* "the cortex keeps final authority", at the wire level.
- **The worker's terminal verb is `report`.** It ends the child's own drive and
  hands text back; it can never end the task.
- **No arm hands the worker repo access**, regardless of the advert's
  `repo_action` allowance. Containment is the arm design's, not the advert's.
- The coding rung's model-written code executes **only** inside the bounded,
  network-less workspace container. No harness path executes it on the host.

### League lane

| arm | seat surface | unit surface |
|---|---|---|
| E, W | `order`, `note` | — |
| M, H | `delegate_units`, `order`, `note` | `report` |

`order` is the league lane's final-authority verb and lives only on the seat.

## 14. Tool-call stability: a validity gate, never an outcome metric

The operator's stated expectation of the upgrade was *"probably more stable
tool calling — real win here"* (`c44`). This repo has a documented baseline to
beat, so it was measured before being cited (`c45`/`h33`):

| the documented baseline | the new cortex |
|---|---|
| the `exit=stopped` collapse — a mind reasoning *past* its tool call and ending on prose, repaired **25% → 67%** by forcing a tool call per step ([scratchpad.md](scratchpad.md)) | **10 / 10** closed through the `finish` tool |
| [#32](https://github.com/agentculture/embodiment/issues/32) — handed a pad, a mind wrote tool calls and **no prose**, 8 turns, zero content | **0 / 10** ended on prose |
| [#33](https://github.com/agentculture/embodiment/issues/33) — `command` sent as a string rather than an array, refusing **17 of 23** calls | **10 / 10** well-formed arguments |

**Pre-registered consequence: protocol stability cannot be an outcome metric in
this series.** With the control at 10/10 there is no headroom, so no arm can
separate on it — the probe says this about itself. It is used as a **validity
gate** instead:

- A cell whose protocol-failure fraction (turns ending with neither content nor
  a tool call, plus malformed-argument refusals) exceeds 0.10 is reported
  `VOID` for that arm at that rung and excluded from the verdict, with its
  count published. The 0.10 is `league-commander`'s committed
  `LENGTH_FRACTION_MAX`, reused for the same "the instrument ate the turn"
  family rather than invented here.
- The honest claim available today, quoted from the probe, is *"no protocol
  failure observed at n=10 on an easy task", and nothing wider.* The series may
  not upgrade that to "the new cortex has stable tool calling".

**One operational consequence of the same probe.** `WorkerSeam.REQUEST_TIMEOUT`
is 300 s and the measured cortex tail is 204.3 s — a margin of only **1.47×**,
measured on an *easy* task. A harder rung's tail could cross it and arrive as a
`transport_failure` rather than as a result. Pre-registered: a transport
timeout is **contention, not a result** — retried up to
`MAX_TRANSPORT_RETRIES` = 3 with a 20 s wait, with the retry count riding into
the artifact. If timeouts appear at all, the timeout is raised and **the
write-up says so**, exactly as `league-h2h` raised its token budget rather than
scoring the truncated turn.

## 15. Predictions, as falsifiable statements

**P1 — no truncation at 16000.** Zero calls in the whole series report
`finish_reason == "length"`. *Falsified by any truncated call*, in which case
the affected cells are excluded by the rule in §4 and the budget question is
reopened in the write-up. Basis: t24 measured 0 of 58 at 16000.

**P2 — `C1` comes back `CEILING`.** Arm E scores 5 or 6 of 6 on the subset
rung. *Falsified by arm E scoring 4 or fewer.* Basis: the older cortex scored
3/3 on this problem and the new one held tool protocol 10/10 on an easier task.

**P3 — ladder A returns no `SEPARATED` rung.** *Falsified by any.* Stated
because it is what four prior arm-comparisons in this repo found on their
closest questions, and a prediction that costs nothing to make is worth nothing.

**P4 — the orchestrated arms cost more completion tokens than arm E at every
graded rung.** *Falsified if any orchestrated arm spends fewer than E at any
rung.* Basis: `league-commander` measured hierarchy at 2.4–4.4× flat for
identical results, with ~95% prompt tokens.

**P5 — reasoning tokens are reported `absent` on every call.** *Falsified by
any call carrying a real reasoning-token count*, in which case the character
proxy is retired in favour of the number and the write-up says so. Basis: 52 of
52 in the throughput series.

**P6 — the pre-registered fan-out width is never exercised.** No dispatched
batch exceeds three units, because `c-frontier-1` fields three roles.
*Falsified by any batch wider than 3.*

I expect **P3 and P6 to hold**, which would make the honest answer *"correctness
did not separate at this `n`; here is the cost and its direction"* — with the
`n` stated loudly enough that nobody reads it as a strong claim.

## 16. Rules fixed in advance

1. **A degraded or failed call is data.** A degradation, an aborted drive or an
   exhausted transport is counted and reported with its record. It is not
   discarded and not re-run for a better number.
2. **A transport timeout or connection error is contention, not a result** —
   retried up to 3 times with a 20 s wait, retry count in the artifact. Nothing
   is ever retried because the answer was disliked.
3. **Nothing is re-run to get a better number.** Each cell runs once at the
   pre-registered attempt count. An interrupted series resumes; it never
   replays a completed cell.
4. **Repetitions write separate artifacts and are folded before analysis.**
   `arch_arms.analyse` keys cells by `(rung, route) → arm`, so a later cell
   with the same key **overwrites** an earlier one. Concatenating repetition
   logs and analysing the result would silently grade only the last
   repetition. The fold sums `attempted`, `correct`, `truncated_calls` and
   `calls` per `(rung, route, arm)` before `analyse` sees them, and the folded
   record is committed beside the per-repetition ones.
5. **The result is published either way**, including `INCONCLUSIVE`, and
   **every rung and cell that did not run is reported `ABSENT` by name.** An
   honest partial is the required outcome; a padded complete-looking one is a
   failure.
6. **Raw per-call records are committed**, not only verdicts: every call's
   `finish_reason`, both token counts, content and reasoning character counts,
   tool-call names, role, model, latency and retry count.
7. **A verdict is only quoted with its pre-registered decision rule beside it**,
   and `INCONCLUSIVE` is reported as `INCONCLUSIVE` — never softened into a win.
8. **The store is scratch.** Every run's eidetic store lives under a temp home
   outside any git work tree. `.eidetic/memory/embodiment__public.jsonl` is
   tracked in this repo and a run's memories written there would be committed
   and shared with mesh peers.
9. **The worker enters no reference-rig table on anything but a supporting
   verdict.** `d15`'s muse-off rig stays the shipped default while the series
   runs, and an `INCONCLUSIVE` leaves it untouched.
10. **Software presence, not a robot body** (C2). Every rung is text, an
    image, or a grid in someone else's simulation. Nothing here drives hardware.

## 17. What this series cannot show

- **Anything about general model quality.** Two Qwen builds acting, one Gemma
  senses model configured beside them, one rig, one night, `n` between 2 and 6
  per cell. A mind that routes a grid skirmish well is not thereby a better
  coder.
- **Whether width helps.** The realised fan-out is three units wide. Every
  claim about parallelism here is a claim about three, and the width-8 bound is
  never reached.
- **Anything about sustained load.** t3 ruled out an immediate crash-loop
  signature over 51 calls on one night. It certifies nothing about hours.
- **That the arms are equivalent if P3 holds.** A six-attempt cell cannot
  detect a small effect, and failing to find one is not finding its absence.
  The results document must say so in those words.
- **Generalisation past this rig.** One local cortex, one Thor worker, one
  gateway, one day.
