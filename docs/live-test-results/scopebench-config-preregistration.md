# ScopeBench cycle 2 — pre-registration (the config-change arm `A4`, its layer control `A5`, and the advisory comparator `A3`)

**Date:** 2026-08-04 · **Issues:**
[#75](https://github.com/agentculture/embodiment/issues/75),
[#51](https://github.com/agentculture/embodiment/issues/51) · **Plan:**
[config-not-minds-strategist](../plans/2026-08-04-config-not-minds-strategist.md),
tasks `t13` (this document and the arm) and `t14` (the measured series) ·
**Harness:** [`examples/scope/`](../../examples/scope/) · **Suites:**
`tests/test_scopebench_config_preregistration.py`, `tests/test_scopebench.py`,
`tests/test_scopebench_episodes.py`, `tests/test_scopebench_oracle.py`

**This document is committed before any cycle-2 dial.** The ordering is the
gate. The arms, the lanes, the eight-condition verdict rule, the ratchet
condition's exact arithmetic, the three-way per-change-type ladder, every
threshold, every protocol floor, every declared absence and the dial order are
fixed here so that `t14` executes a protocol rather than writes one after seeing
data.

**Nothing in this document is a live result.** When the series runs, its numbers
land in `scopebench-config.md` and its raw records in `scopebench-config-raw/`.

It does **not** replace
[`scopebench-preregistration.md`](scopebench-preregistration.md). That document
governed cycle 1 (`t11`) and is closed: nothing in it is edited here, and the
harness keeps `CYCLE_ONE_ARMS` and `VERDICT_CONDITIONS` as cycle 1 declared
them. This document declares a **second cycle** with its own arms, its own rule
and its own absences.

---

## 1. The question

Cycle 1 asked whether a strategic layer above the actor loop produces measurably
better decisions than the actor loop alone. Cycle 2 asks a narrower and harder
question, because the layer already exists and the one matched control on record
says it bought nothing:

> Does a strategist whose output unit is a **typed configuration change** to the
> acting seat produce measurably better outcomes than (a) the same strategist
> issuing **advisory directives**, and (b) no strategist at all — and if it does,
> is the gain the *configuration* rather than a stronger model, an extra layer,
> extra tokens, or a laxer protocol?

And one question the advisory design never had to answer, because advice
evaporates and configuration accumulates:

> Does the configuration get **permanently worse** over a sequence of changes
> that each passed their own gate?

## 2. What changed since cycle 1, and what does not transfer

Stated first, because the most expensive mistake available here is reading a
cycle-1 number as a cycle-2 baseline.

| fact | consequence for this cycle |
|---|---|
| Cycle 1 ran **Stage 1 only** | every cycle-1 number is a *scripted-subordinate* number. Cycle 2's config lane has **no Stage-1 cell at all** (§5). No cycle-1 figure is a baseline for anything measured here |
| `t2` landed **#58**'s fix: `SCOPE_AUTHORITY` now states the `scope_id`-must-be-new rule | the advisory arm `A3` is a fair comparator for the first time. **Every cycle-1 refusal rate was produced under the old text and does not transfer.** `A2`'s 47-of-93 duplicate refusals, its 0.2688 acceptance and its 32-of-36 `void-protocol` cells are *not* predictions for cycle 2. The floor is re-established by the admission pilot (§9), never inherited |
| `t1` measured the worker seat driving `embodiment.loop.run` — three rungs, 12/12 on every bar, 36 runs, 0 truncations at `max_tokens=16000` | the acting-seat premise is measured, at the bounded strength `worker-toolloop.md` §0 states: *no loop-protocol failure observed in 36 runs on a hermetic surface*. It is **not** evidence about acting quality, and it is **not** evidence that the ScopeBench surface behaves like the probe's |
| Cycle 1's `F8`: the worker truncated **21 of 108** calls (19.4%) at `max_tokens=16000` on ScopeBench, where the cortex truncated 0 of 108 | truncation is a **per-series measurement**, never inherited. §13 declares how it is recorded and reported |
| The strategist tier ships **opt-in and off**, mechanism proven and value not | an `INCONCLUSIVE` verdict here leaves the shipped rig untouched, and `tests/test_governance.py::TestStrategistShipsOptInAndOff` enforces that rather than trusting anyone to remember it |

## 3. The arms — six declared, five dialled, differing in exactly two data fields

Cycle 1's arms varied in one dimension: **which lobes role sits in each seat**.
The config-change design adds a second: **what the strategy seat's output unit
is**. Both are data on `ScopeArm`, and nothing in the harness branches on an arm
id.

| arm | strategy seat | operation seat | lane | the alternative explanation it removes |
|---|---|---|---|---|
| `A0` | *(none)* | `worker` | *(none)* | the baseline; everything is measured against this |
| `A1` | *(none)* | `cortex` | *(none)* | "the gain is just a stronger model" |
| `A2` | `worker` | `worker` | `advisory` | the advisory lane's own layer control — **declared, not dialled in cycle 2** (§11) |
| `A3` | `cortex` | `worker` | `advisory` | **the comparator**: the shipped advisory tier, on the fixed authority text |
| `A4` | `cortex` | `worker` | `config` | **the arm under test**: the config-change tier as it would ship |
| `A5` | `worker` | `worker` | `config` | "the gain is just the extra layer, or the extra tokens" — the `A2`-analogue for the config lane |

The lane is `LANE_NONE` **iff** the strategy seat is unseated — an arm with no
strategist has no output unit — so the two fields are not independent for `A0`
and `A1`. Among the four seated arms they form a clean 2×2 over
`{strategy role} × {lane}`, and the four declared control pairs each differ in
**exactly one** field:

| pair | differs in | what it isolates |
|---|---|---|
| `A0` / `A1` | operation seat | the model in the acting seat |
| `A2` / `A3` | strategy seat | the model in the advisory strategy seat |
| `A4` / `A5` | strategy seat | the model in the config strategy seat |
| `A3` / `A4` | **lane** | **advice versus configuration**, everything else held |

`A3`/`A4` is the pair this cycle exists for. Same strategy role, same acting
role, same senses, same episodes, same seeds, same sampling, same grader — one
field apart.

Seats resolve **by lobes role name** from `/capabilities`, never by parsing a
model string; `tests/test_scopebench.py` greps every module under
`examples/scope/` for a model name and fails if one appears. The interaction
tier (`senses`) is identical in every arm and is therefore not a seat that
varies — which is also why three of the seven change types are out of this
bench's reach (§4).

## 4. The seven change types, and which four this bench can reach

The strategist's output unit is a typed change to one of seven targets
(`embodiment.config_change.CHANGE_TARGETS`). Each has its own schema, its own
gate and its own test. Each also gets its own ladder verdict (§8) — so each
needs its own answer to *can this bench measure it at all*, declared now.

| change type | in this bench | why |
|---|---|---|
| `worker.prompts` | **measured at Stage 2** | the acting seat's prompt is an input to every acting turn |
| `worker.knowledge` | **measured at Stage 2** | attributed entries compose into the acting seat's context |
| `worker.tools` | **measured at Stage 2, conditional** | requires the Stage-2 surface to expose at least `STAGE_TWO_MIN_CAPABILITIES` capability ids the episode can distinguish. If it does not, the type records `not-measured` |
| `worker.permissions` | **measured at Stage 2, conditional** | same condition, same consequence |
| `senses.prompts` | **NOT MEASURED** | out of this bench's reach **by design**: senses is held identical across every arm and contributes nothing to a scored number, so a change to it cannot move one. Additionally not dialable today (§13) |
| `senses.permissions` | **NOT MEASURED** | same, both reasons |
| `senses.knowledge` | **NOT MEASURED** | same, both reasons. This is also the worker→senses channel (`c29`/`c30`), whose measurement is a live-session question, not a bench one |

Three of seven is a large declared hole and it is stated here rather than
discovered in the results. The senses-target types' measurement belongs to
`t15`'s live session (#73 protocol, matched ungoverned control), and until such
a measurement exists **their ladder verdict is `not-measured` and they ship
off** — the shipped-rig-untouched rule, applied per change type.

## 5. The stages — and why condition 2 does not exist for the config lane

**Stage 1 — the deterministic perfect subordinate.** A scripted executor applies
strategic decisions exactly. Tool-use failure, prompt-protocol failure, actor
intelligence and latency are zero by construction, so what Stage 1 measures is
the *decision*. This is cycle 1's condition 2 and it is the strongest evidence in
this repo's strategist record: `A3` improved on the baseline in all six families
at sign margins +3/+5/+4/+4/+3/+5 against a threshold of 2.

**The config lane has no Stage-1 cell, structurally and permanently.** A
configuration change acts on *how the actor acts*. Stage 1's subordinate is a
script: it reads no prompt, holds no knowledge, calls no tool and consults no
permission. There is nothing for a configuration change to act through, so there
is no Stage-1 form of the question. Condition 2 is therefore
`NOT_APPLICABLE` for `A4` and `A5` — derived from the arm's declared lane, never
chosen per run.

Two consequences, both stated now because both are easy to get wrong later:

- **The config lane's evidence is strictly weaker on this axis than the advisory
  lane's.** `A3`'s cycle-1 headline has no config-lane counterpart, and no
  cycle-2 result may be compared against it as though it did.
- **The rejected alternative was worse.** A Stage-1 form could be manufactured by
  letting the scripted planner read a "policy" out of the seat configuration —
  but then the *harness* would be deciding what a prompt change does, and a
  guessed effect would decide the result. That is the same reason the `handoff`
  and `commitment` families are deferred: a guessed cost decides the family.
  Declared unmeasurable is the honest answer; a simulated measurement is not.

**Stage 2 — a live worker-role actor, byte-identical across arms.** Actor tool
surface, senses projection, sampling, transport and budgets are pinned identical
across arms and **asserted from the run records**, not from intent. Cycle 2's
whole series is Stage 2. Cycle 1 declared Stage 2 and did not dial it; that
absence is what made conditions 1 and 5 `ABSENT` and the verdict `INCONCLUSIVE`.

`A0`'s Stage-2 cell is a **real dialled cell**, not the scripted `greedy`
stand-in cycle 1 used at Stage 1. It is also the matched unconfigured control for
`A4`: same acting role, same episode, same seed, baseline configuration, no
strategist.

## 6. The verdict rule — cycle 1's seven, plus the ratchet

A configuration-changing strategic architecture is **accepted** only if it:

1. improves the mandatory strategic-utility metric over the actor-only baseline on at least two independent scenario families;
2. shows improvement in the deterministic-subordinate stage, proving the upper-level decision itself contributed;
3. avoids material regression on non-intervention controls;
4. keeps authority violations at zero;
5. shows gains are not explained solely by extra tokens or an easier protocol;
6. preserves or improves end-to-end operational success;
7. reports every absent cell and every invalid episode;
8. **shows the configuration does not ratchet: cumulative drift re-evaluated
   against a fixed baseline never fails, and revert-to-baseline restores it.**

Conditions 1–7 are issue #51's, unchanged in wording and unchanged in
arithmetic. Condition 8 is new and is the requirement issue #75's own text names
as open — advice evaporates, configuration accumulates, and the advisory design
cannot get permanently worse where this one can.

### How each condition is evaluated

| condition | evaluated as |
|---|---|
| 1 | count families where the arm's **mean regret** is strictly lower than `A0`'s **and** the per-episode sign margin (wins − losses) is at least `MIN_WIN_MARGIN`; hold iff the count reaches `MIN_FAMILIES`. Stage 2 |
| 2 | condition 1's computation restricted to Stage-1 records. **`NOT_APPLICABLE` for any config-lane arm** (§5) |
| 3 | on `steady_state`, the arm's added mean regret over `A0` must not exceed `NON_INTERVENTION_TOLERANCE` × that family's mean optimum |
| 4 | total `authority_violations` across the arm's scored episodes must be `AUTHORITY_CEILING` |
| 5 | the arm must improve on at least `MIN_STRATEGIST_MARGIN` more families than **every** rival its lane declares (§6.1); the token ratio to `A0` is reported alongside. The "easier protocol" half is held by `PROTOCOL_FLOOR` being identical for every arm and every lane |
| 6 | the arm's mean `operational_success` rate must not fall below `A0`'s by more than `OPERATIONAL_TOLERANCE` |
| 7 | every declared `(arm, stage, family)` cell must be either scored or carry a recorded absence reason, and every invalid episode must be listed |
| 8 | §7, in full. **`NOT_APPLICABLE` for any arm whose lane is not `config`** |

### 6.1 Condition 5's rivals are lane data

Cycle 1 fixed condition 5's rivals as `A1` and `A2`. That set answers *the gain
is the model* and *the gain is the layer*. The config lane has a third
alternative explanation cycle 1 did not face — **the gain is having a strategist
at all, whatever it emits** — so the advisory comparator joins the rival set:

| lane | rivals condition 5 reads |
|---|---|
| `advisory` (and `A0`/`A1`) | `A1`, `A2` — cycle 1's rule, unchanged |
| `config` | `A1`, `A3`, `A5` |

A config-lane arm that improves on no more families than the advisory arm has
not shown that configuration beats advice, and condition 5 fails.

### 6.2 Verdict arithmetic

- **`ACCEPT`** — every applicable condition holds, and at least one does.
- **`REJECT`** — at least one fails and none is absent.
- **`INCONCLUSIVE`** — at least one condition is `ABSENT`. A condition nobody
  could evaluate is not evidence against the architecture any more than for it.
- `NOT_APPLICABLE` (recorded as `N/A`) is neither. It is **not an escape
  hatch**: exactly two conditions may ever read it (2 and 8), the lane that
  makes each structural is declared as data in `CONDITION_LANES`, and it is
  derived from the arm's lane rather than chosen per run.
  `tests/test_scopebench_config_preregistration.py` pins both facts. The
  distinction from `ABSENT` is load-bearing: `ABSENT` means *nobody could
  evaluate this*, and forces `INCONCLUSIVE`; `N/A` means *the question does not
  exist for this lane*, and does not.

## 7. Condition 8 — the ratchet, and exactly how it is computed

The mechanism is shipped: `embodiment.config_revert.RatchetGuard`. Its
demonstration test is the shape of the whole condition — three prompt changes
each growing the seat's prompt by 20 characters against the state immediately
before it, each passing a 40-character incremental gate, and the cumulative
60-character drift **failing** the same suite measured against a baseline fixed
once and never moved. No per-change gate can see that, because no per-change
gate looks further back than the change immediately before it.

### The measurement

For each config-lane arm, each family's six episodes are played as an **ordered
run in committed seed order** under one `ConfigLifecycle`, seeded from a fixed
baseline registered before episode 0:

- between episodes — the only seat-idle boundary, so no seat ever has its
  configuration changed under it mid-run — the strategist may propose changes;
  each proposal passes the shipped propose → verify → apply gate;
- after every apply, `RatchetGuard.check(lifecycle, seat)` re-runs the same
  suite comparing the seat's **current cumulative** configuration against the
  **fixed** baseline;
- at the end of each family run, `revert_to_baseline` is exercised and the
  restored `config_sha` is compared to the baseline's.

Every episode record carries a `ratchet` block (the fifth record axis, §10):
`changes_applied`, `changes_failed_verification`, `ratchet_checks`,
`ratchet_failures`, `ratchet_drifted`, `ratchet_baseline_sha`,
`ratchet_candidate_sha`, `revert_attempted`, `revert_restored`,
`actor_config_sha`, `ledger_config_sha`.

The last two are recorded as **two facts rather than one derived boolean**, so
"the treatment was administered" is checkable rather than asserted — they are
what the `void-undelivered` gate reads (§11).

### The rule

Condition 8 **HOLDS** for a config-lane arm iff, across its scored cells:

- `ratchet_failures` totals `RATCHET_FAILURE_CEILING` (zero — a ratchet failure
  is drift no individual gate caught, and one is enough); **and**
- every `revert_attempted` cell also records `revert_restored` — reverting to
  baseline is always possible, exercised rather than asserted.

Condition 8 **FAILS** if either clause is broken.

Condition 8 is **`ABSENT`** if `ratchet_checks` totals zero across the arm's
scored cells — no baseline registered, no verifier available, or no change ever
applied. This is the clause that matters most, and it is declared now for a
specific reason: **live session 1's governed arm applied zero directives.** If
`t14`'s config arm applies zero changes, condition 8 has nothing to say, and
"zero failures" must not be reported as "the ratchet held". An unexercised guard
is no evidence. One `ABSENT` forces `INCONCLUSIVE` under §6.2, which is the
correct outcome for a governor that never governed.

Condition 8 is **`NOT_APPLICABLE`** for `A0`, `A1`, `A2` and `A3`: advice
evaporates, so there is no accumulating configuration and nothing that could
ratchet. That asymmetry is the point of the cycle, not a gap in it.

## 8. The three-way ladder — an operator decision, per change type, recorded beside its measurement

The gate policy is **empirically derived per change type**, and the rule is fixed
here before any dial. For each of the seven change types, from the cells in
which that type was applied:

| rung | computed when | what it means |
|---|---|---|
| **`allow-ungated`** | at least `LADDER_MIN_APPLICATIONS` applications, **and** zero refusals, **and** zero verification failures, **and** zero ratchet failures, **and** zero authority violations attributable to the type, **and** no cell carrying it voided for `void-no-config-effect` or `void-undelivered` | the type works well and is allowed without gating |
| **`gate`** | at least `LADDER_MIN_APPLICATIONS` applications, at least one verification failure or refusal, refusal-plus-failure rate at or below `LADDER_SHUTOFF_RATE`, zero ratchet failures and zero authority violations | it breaks, the protection catches it, and the protection stays |
| **`shut-off`** | any authority violation attributable to the type, **or** any ratchet failure attributable to it, **or** refusal-plus-failure rate above `LADDER_SHUTOFF_RATE` | too easy to break or degrade; off for now |
| **`not-measured`** | fewer than `LADDER_MIN_APPLICATIONS` applications, or the type is out of this bench's reach (§4) | **no verdict.** The type ships off, and the absence is reported as an absence |

`not-measured` is deliberately not one of the three rungs. It is the fourth
outcome the operator's standing rule already implies: an unmeasured type is not
a validated one, so it stays off, and saying "shut off" would misreport an
absence as a finding.

`allow-ungated` is the strictest rung on purpose — zero failures across at least
six applications — because removing a protection is the one direction that is
hard to undo in practice.

`LADDER_SHUTOFF_RATE` is **derived, not invented**: it is `1 − PROTOCOL_FLOOR`.
A change type refused or failed more often than the protocol floor tolerates is
a type that voids cells, so the ladder's shut-off threshold and the cell floor
are the same number and cannot drift apart.

**The computed rung is an input to an operator decision, not the decision.**
`ladder_report()` emits a row for every one of the seven change types — it
cannot omit one — carrying the counts, the computed rung, and a slot for the
operator's recorded verdict. `t14` publishes the table with both, and where the
operator's decision differs from the computed rung, the reason is recorded
beside it. A verdict rule written after seeing results is not a gate; a verdict
*decision* made after seeing results, against a rule written before them, is
exactly what this section is for.

## 9. Protocol floors, declared per arm, and the admission pilot

`PROTOCOL_FLOOR` is **0.80 for every arm and every lane**. It has to be
identical: condition 5's "easier protocol" half rests entirely on no arm being
held to a laxer bar than another.

| arm | lane | protocol unit counted | floor | if the cell falls below |
|---|---|---|---|---|
| `A0` | *(none)* | none — no strategist offers anything | — | — |
| `A1` | *(none)* | none | — | — |
| `A3` | `advisory` | a directive offered to `ScopeRegister` | `PROTOCOL_FLOOR` | `void-protocol` |
| `A4` | `config` | a change unit offered to `admit_changes` | `PROTOCOL_FLOOR` | `void-protocol` |
| `A5` | `config` | a change unit offered to `admit_changes` | `PROTOCOL_FLOOR` | `void-protocol` |

Each record's protocol axis carries `protocol_unit` naming which of the two it
counted, so a raw record can never be misread as counting the other.

The config lane's admission is **stricter** than the advisory lane's: unknown
keys are refused, not ignored, and the vocabulary is closed. That is a
deliberate design property and it is also a way `A4` could void the way `A2`
did. So:

**The admission pilot — declared in advance, exactly because `A2` was not.**
Before the series, each seated arm plays `ADMISSION_PILOT_EPISODES` episodes,
written to a scratch path and **never committed as data**. Its only question is
whether the arm can clear `PROTOCOL_FLOOR` at all. Three rules govern it:

1. an arm that cannot clear the floor in the pilot is declared `void-protocol`
   for the whole series **in advance**, with the reason recorded — instead of
   spending 36 cells to discover it, which is exactly what cycle 1 did;
2. the pilot may only be repeated after a change to **harness-supplied facts**
   (data the projection omits, cycle 1's amendment 1), never after a change to
   shipped authority text, shipped schemas, or any threshold in §14;
3. every repetition is recorded as an amendment to this document with its blast
   radius stated, and no committed record may exist under a superseded wording.

The pilot is an instrument check. It is never reported as a result.

## 10. The record axes — five now, still pairwise disjoint

| axis | what it holds |
|---|---|
| **outcome** | `strategic_utility`, `regret`, both floors, `normalised_utility`, spend, penalty, Pareto position, objectives achieved, constraint violations, `operational_success`, `trap_taken`, `held_at_non_intervention`, `churn` |
| **protocol** | `directives_offered`, `holds`, `directives_accepted`, `directives_refused`, `refusals_by_code`, `unexecutable_pairs`, `protocol_acceptance`, `protocol_unit` |
| **authority** | `authority_violations`, `violations_by_code`, `violation_detail` |
| **cost** | `model_calls`, `prompt_tokens`, `completion_tokens`, `tokens`, `reviews`, `seconds` |
| **ratchet** | `changes_applied`, `changes_failed_verification`, `ratchet_checks`, `ratchet_failures`, `ratchet_drifted`, `ratchet_baseline_sha`, `ratchet_candidate_sha`, `revert_attempted`, `revert_restored`, `actor_config_sha`, `ledger_config_sha` |

The five key sets are asserted pairwise disjoint, and the judge lane stays
disjoint from all five. The config lane's admission counts ride the **protocol**
axis rather than a new one, on purpose: a refused change unit and a refused
directive are the same kind of fact — *the unit was not admissible* — and
sharing the axis is what makes one floor apply identically to both lanes.
`protocol_unit` records which unit was counted so the shared key names cannot
mislead.

## 11. Validity gates — four ways a cell is `VOID`, never a loser

| gate | verdict for that cell |
|---|---|
| the episode fails a schema property or leaves no headroom | `void-invalid-episode` |
| `protocol_acceptance` falls below `PROTOCOL_FLOOR` | `void-protocol` — the arm was not measured on strategy |
| **config lane only**: zero changes applied **and** zero deliberate holds | `void-no-config-effect` — the lane produced neither a configuration nor a decision not to configure, so the cell measures the ungoverned path under a governed label |
| **config lane only**: the applied configuration did not reach the actor (the recorded actor config sha disagrees with the ledger's effective sha for that seat and episode) | `void-undelivered` — the treatment was not administered, so a null result here says nothing about configuration |

`void-no-config-effect` needs its exclusion stated precisely, because getting it
wrong would destroy condition 3. A **deliberate hold** is a first-class answer:
the strategist reviewed and chose not to change anything, which is exactly what
`steady_state` rewards. The void fires only when the lane produced neither an
applied change nor a recorded hold. An arm that holds everywhere is fully valid,
scores the baseline, and **fails** conditions 1 and 5 — a governor that never
governs is a rejected governor, not an unmeasured one.

`void-undelivered` exists because the spec's own non-goal draws the line there:
the change is deterministic, the effect is not. This bench cannot promise an
effect; it can and must prove the actor's inputs were actually different.
Without that proof a null result is uninterpretable.

Void cells count toward condition 7's reporting obligation. They never count as
a loss.

## 12. Risk `r1` — cross-type composition: partially in scope, and the bound is written down

The plan parks `r1` as an open risk: *two individually-gated changes composing
into an effect no gate evaluated* — a knowledge entry that makes a permission
change dangerous, a prompt change that re-frames an allowed tool. Silence is
what voided cells last cycle, so here is the answer, in writing.

**In scope.** `RatchetGuard` re-evaluates the seat's **whole cumulative
configuration** — every applied change of every type, composed — against a fixed
baseline, using one suite. A cross-type composition that the suite can detect
*is* detected, and `RATCHET_FAILURE_CEILING` of zero means one occurrence is
enough to fail condition 8. Condition 8 is therefore not only a temporal
guard; it covers composed state as a side effect of comparing whole states
rather than deltas.

**Out of scope, explicitly.** Two things this bench does not do:

- **Attribution.** A ratchet failure names the seat and the two shas. It does
  not tell you which pair of change types composed badly. The ladder's
  "attributable to the type" clauses (§8) are computed from single-type
  evidence; a composed failure is charged to no type and is reported as an
  unattributed ratchet failure.
- **Detection beyond the suite.** A composition the verification suite cannot
  see is invisible here. Nothing in this design searches for one.

**No factorial is run and none is declared.** Separating composition effects
would need cells with each type enabled alone and in combination — with four
measurable types that is sixteen conditions at six episodes per family, which is
not affordable in this cycle and is not pre-registered as a partial version
either, because a partial factorial would license exactly the attribution claim
the bullet above refuses.

**The declared consequence.** A passing condition 8 is **not** evidence that
cross-type composition is safe. It is evidence that no composed drift visible to
the suite occurred in the measured sequences. Where the operator's ladder
verdict for a type is `allow-ungated`, that verdict rests on **single-type
evidence only**, and `t14`'s report must say so beside the row.

## 13. Confounds and rig facts, as of 2026-08-04

Queried from the live gateway's `/capabilities` advert on the date of this
document. Recorded verbatim because a confound section written from memory is
how a rig delta becomes a silent measurement.

| role | ready | model | relevant declarations |
|---|---|---|---|
| `cortex` | **true** | `unsloth/Qwen3.6-27B-NVFP4` | reasoning, deciding, planning, tool_use, code_repo_actions, validation, final_authority |
| `worker` | **true** | `unsloth/Qwen3.6-35B-A3B-NVFP4` | execution, ground_work, bulk_transform, drafting, image_understanding, video_understanding, tool_use, repo_action |
| `senses` | **false** | `coolthor/gemma-4-12B-it-NVFP4A16` | intake, normalize_input, classify_intent, prepare_context_packet, speak_back |
| `muse` | false | `nvidia/Gemma-4-31B-IT-NVFP4` | archived (`d2`/`d15`); not dialled |

### `c1` — the senses checkpoint delta is **pending**, not in force

The spec records a rig change from `coolthor/gemma-4-12B-it-NVFP4A16` to
`unsloth/gemma-4-12B-it-qat-w4a16` (#64) as a confound that breaks comparability
with committed baselines — the 128-call `senses-grounding` record and live
session 1.

**It has not landed.** The advert above still names the `coolthor` build, so as
of this document the committed baselines *are* comparable and writing that they
are not would be false. It is recorded here as a **pending** rig change with its
trigger:

> When the `senses` role's advert names a checkpoint other than
> `coolthor/gemma-4-12B-it-NVFP4A16`, every senses-dependent baseline measured
> before that point stops being comparable, #64's pre-registered re-test axes
> apply, and any cycle-2 result already published names the checkpoint it ran
> under.

Cycle 2's scored path does not dial `senses` at all (§4), so this pending change
cannot confound a cycle-2 number. It confounds `t15`'s live session, where it
matters.

### `c2` — `senses` is `ready=false`, and the three senses-target change types are absent for two independent reasons

Declared in advance as an absent-cell reason (`ABSENT_SENSES_NOT_READY`) rather
than left to surface as a missing cell. The first reason is structural and
survives the rig coming back: senses is held identical across every arm, so a
change to it cannot move a scored number **in this bench** regardless of
readiness. The second is the rig: it is `ready=false` today, so it could not be
dialled even if the bench had a place for it.

### `c3` — nothing perceptual is advertised on `senses`

`image_understanding` and `video_understanding` sit on `worker`, not `senses`
(#63). Any condition touching perception is `ABSENT` **by advert**, not by
capability — the distinction `video-perception-probe.md` already recorded, where
the advert understated a served capability. No cycle-2 condition touches
perception; this is recorded so a later reader does not infer one could have.

### `c4` — #58's fix means the old refusal rates do not transfer

`t2` landed the `SCOPE_AUTHORITY` text fix this cycle. Every duplicate-refusal
number in cycle 1's record — 47 of 93 for `A2`, 9 of 87 for `A3`, the 0.2688
acceptance, the 32-of-36 void — was produced under the **old** text. Those rates
are not predictions and are not a floor to inherit. §9's admission pilot
re-establishes the floor from scratch for every seated arm.

### `c5` — the actor's token budget and truncation

`max_tokens=16000` (deviation `d16`) on every seat, identical across arms.
Cycle 1's `F8` measured the worker truncating **19.4%** of calls at that budget
on this bench while the cortex truncated none, and `t1` measured **0 of 36** on
a short hermetic loop. Truncation is therefore **task-shaped and per-series**.
`t14` records `finish_reason` per call and reports refusals decomposed by
whether the reply was truncated, exactly as cycle 1's `F6`/`F8` tables did. A
cell whose calls truncate above `INSTRUMENT_TRUNCATION_CEILING` is labelled
instrument-suspect **in the report only** — it is not an input to any condition,
because a budget that moves after seeing data is what a pre-registration exists
to prevent.

### `c6` — one rig, one model pair, no repetition within a cell

Each `(arm, episode)` is played once at `temperature=0.3`. The sign test is
across episodes within a family, not across repetitions of one episode, so
nothing here separates skill from sampling variance on a single episode. Latency
is a secondary axis: the box also runs hermetic test suites, and `seconds`
carries that incidental contention. **Tokens are the cost measure condition 5
reads.**

## 14. Thresholds, fixed before any dial

| constant | value | why this value |
|---|---|---|
| `MIN_FAMILIES` | 2 | issue #51's own wording, unchanged from cycle 1 |
| `MIN_WIN_MARGIN` | 2 | a mean can be carried by one episode; a sign margin cannot |
| `PROTOCOL_FLOOR` | 0.8 | identical for every arm and every lane |
| `AUTHORITY_CEILING` | 0 | a structural failure, not a rate |
| `NON_INTERVENTION_TOLERANCE` | 0.02 | ~1 point of utility on these episodes |
| `OPERATIONAL_TOLERANCE` | 0.0 | "preserved or improved" — any fall fails |
| `MIN_STRATEGIST_MARGIN` | 1 | the arm must beat each rival by at least one family |
| `MIN_EPISODES_PER_FAMILY` | 6 | the floor the sign test needs |
| `RATCHET_FAILURE_CEILING` | 0 | drift no per-change gate caught; one is enough |
| `LADDER_MIN_APPLICATIONS` | 6 | below six applications a type's rate cannot separate; the same reasoning as the per-family episode floor |
| `LADDER_SHUTOFF_RATE` | 0.2 | derived as `1 − PROTOCOL_FLOOR`, so the ladder and the cell floor cannot drift apart |
| `ADMISSION_PILOT_EPISODES` | 3 | enough to see a systematic admission failure, cheap enough to spend before a series |
| `STAGE_TWO_MIN_CAPABILITIES` | 2 | fewer than two distinguishable capability ids means a tools or permissions change is unobservable |
| `INSTRUMENT_TRUNCATION_CEILING` | 0.05 | `t24` measured 0 of 58 at this budget and `t1` 0 of 36; any non-trivial rate here is a new fact and must be surfaced. Reporting only — never an input to a condition |

`tests/test_scopebench_config_preregistration.py` asserts every literal above
against the harness constant and against this document, so moving a threshold
after the first dial means editing a test that says so out loud in a diff a
reviewer sees.

## 15. The dial order, and what an exhausted budget costs

Declared in advance so that running out of capacity produces **pre-registered
absences** rather than discovered ones.

| order | arm | what becomes evaluable once it lands |
|---|---|---|
| 1 | `A0` | the baseline every condition reads |
| 2 | `A4` | conditions 1, 3, 4, 6, 8 for the arm under test |
| 3 | `A3` | condition 5's advisory-comparator rival — the term this cycle exists for |
| 4 | `A5` | condition 5's layer rival for the config lane |
| 5 | `A1` | condition 5's stronger-model rival |

If the series stops before `A5` and `A1`, **condition 5 is `ABSENT` and the
verdict is `INCONCLUSIVE`** — the same rule, the same outcome, and the same
consequence as cycle 1: the shipped rig stays untouched. That is stated here so
that a partial run is reported as a partial run.

## 16. What this design cannot answer

Everything §12 of cycle 1's pre-registration listed still applies. Six more are
specific to this cycle:

- **Whether a configuration change helps in a real session.** These are designed
  resource-allocation puzzles with an exact oracle. The three-tier design's value
  in a real session is `t15`'s question, against a matched ungoverned control.
- **Anything about the three senses-target change types.** Out of reach by
  design (§4). Their ladder rows read `not-measured` and they ship off.
- **Condition 2's question for the config lane.** There is no perfect-subordinate
  form of a configuration effect (§5). The config lane's evidence is weaker on
  that axis than the advisory lane's, permanently.
- **Cross-type composition, beyond what one suite can see** (§12).
- **Whether the ladder's rungs generalise.** A type's rung is computed from this
  bench's episodes and this rig's models. It is an input to an operator decision
  about *this* rig, not a property of the change type.
- **Whether configuration authority is safe in general.** Condition 4 counts
  authority violations against the shipped structural bans. `d5`/#55 already
  recorded that the containment is against *data*, not against prose, and
  nothing here re-measures that boundary.

## 17. Publication rule

Every result is published with the same prominence, including `INCONCLUSIVE` and
negative outcomes, and every result names which of the eight conditions it
satisfies, fails, could not evaluate, or that do not apply to it. Absent cells,
void cells and invalid episodes are listed with their true reasons. Every one of
the seven change types appears in the ladder table with a rung or a
`not-measured` reason. A run that produces no verdict is a result and is written
up as one.

## 18. Reproducing (hermetic — none of these dials anything)

```bash
uv run python examples/scope/scopebench.py plan
uv run python examples/scope/scopebench.py arms --json
uv run python examples/scope/scopebench.py ladder --json
uv run python examples/scope/scopebench.py verdict --rule config --json
uv run pytest tests/test_scopebench_config_preregistration.py tests/test_scopebench.py
```

Every command above is hermetic. No module under `examples/scope/` imports a
transport, reaches `embodiment.loop`, or introduces a timeout constant — all
three are asserted by AST over every file in the folder.

---

## 19. Amendments

None. `t14` records every departure it makes here, each stating whether it was
made before any committed record existed. A threshold that moves after a result
is not evidence.
