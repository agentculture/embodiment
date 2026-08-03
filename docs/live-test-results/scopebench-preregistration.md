# ScopeBench — pre-registration (arms `A0`–`A3`, Stage 1 and Stage 2)

**Date:** 2026-08-03 · **Issue:**
[#51](https://github.com/agentculture/embodiment/issues/51) · **Plan:**
[the strategic-scope-governor plan](../plans/2026-08-03-strategic-scope-governor.md),
tasks `t9` (this scaffold) and `t11` (the measured series) · **Harness:**
[`examples/scope/`](../../examples/scope/) · **Suites:**
`tests/test_scopebench.py`, `tests/test_scopebench_episodes.py`,
`tests/test_scopebench_oracle.py`, `tests/test_scopebench_preregistration.py`

**This document is committed with the harness and before the first measured
dial.** The ordering is the point: the episode schema, the committed seeds, the
exact oracle, the arms, the four record axes, the seven-condition verdict rule
and every threshold in it are fixed here so that `t11` executes a protocol
rather than writes one after seeing data.

Nothing in this document is a live result. When the series runs, its numbers
land in `scopebench.md` and its raw records in `scopebench.jsonl`.

---

## 1. The question

Does an explicit **strategic layer above the actor loop** produce measurably
better decisions than the actor loop alone — and, if it does, is the gain the
*strategist* rather than a stronger model, an extra layer, extra tokens, or a
laxer protocol?

The gap this is aimed at is the one `docs/relationships.md` records as having no
owner: nothing in embodiment decides *what deserves attention*, so the actor
treats every presented task as the right problem. Locally correct work can be
globally wrong, and today no seam exists to say so.

## 2. What an episode is

A small, closed, deterministic resource-allocation world
(`examples/scope/episodes.py`). Discrete ticks; at each **review tick** an
allocation — one workstream, or idle, per actor — may be replaced; between
reviews it holds. An objective is achieved iff every workstream it requires
completed on or before its deadline. Utility is achieved value minus constraint
penalties.

Every committed episode carries all eight required properties, and every one of
them is **checked** rather than claimed:

| property | how it is checked |
|---|---|
| several candidate objectives | `structural_report` — at least three |
| dependencies between workstreams | `structural_report` — at least one blocked stream |
| limited resources | `structural_report` — budget < running every actor for the horizon |
| actors differ in capability and cost | `structural_report` — at least two distinct profiles |
| durable constraints | `structural_report` — at least one, with prose the strategist reads |
| events change state over time | `structural_report` — at least one scheduled event |
| a locally-attractive-but-globally-wrong action | `oracle.schema_report` — the local argmax strictly forgoes value against the exact optimum |
| a valid "do not intervene" state | `oracle.schema_report` — holding reaches the optimum, and at least one change is strictly worse |

The last two are **computed by the solver**, never declared by the generator. A
seed whose trap or hold state does not survive that check produces an *invalid
episode*, which is reported (condition 7) rather than quietly regenerated.

**Constraints are priced, not enforced.** A forbidden assignment is executable:
the subordinate carries it out and the breach is charged to the strategist. What
*is* refused is physical impossibility — an unknown owner, an unknown
workstream, a missing skill, a lost actor — and those land on the **protocol**
axis, never the outcome one.

## 3. The scenario families — six of eight, and the two absences are declared

| family | in the first cycle | what it stresses |
|---|---|---|
| `contention` | yes | choosing which objective to abandon |
| `critical_path` | yes | ordering work behind a dependency |
| `allocation` | yes | matching capability and cost to need |
| `disruption` | yes | revising a plan that was right when it was made |
| `steady_state` | yes | **not** intervening — condition 3's control family |
| `constraint` | yes | honouring a durable constraint under pressure |
| `handoff` | **DEFERRED** | grading a handoff needs a handoff-cost model the schema does not carry, and a guessed cost would decide the family |
| `commitment` | **DEFERRED** | grading a broken promise needs a breach-cost model that is a domain judgement rather than a benchmark constant |

The frame parked "which families land in the first cycle" as a plan-stage
sequencing decision. **This is that decision**, made at `t9` and before any
data exists. Declaring all eight and running six is deliberate: condition 7
reports every absent cell, and an absence nobody declared cannot be reported.

`episodes_per_family` is **6**, at or above the pre-registered
`MIN_EPISODES_PER_FAMILY` of 6 — the floor the per-family sign test needs to be
able to separate at all. Seeds are committed in
[`scopebench-seeds.json`](scopebench-seeds.json); every `(family, seed)` cell
reproduces byte-identically from a deterministic LCG that is not
`random.Random`, so the worlds do not drift with the standard library.

## 4. The oracle — exact, and clairvoyant, and both are stated

`examples/scope/oracle.py` enumerates **every** plan an episode admits, so the
optimum is a maximum over an enumerated set rather than an estimate.
`brute_force_optimum()` re-derives it with no memo table as the
test-of-the-test, and `scopebench-fixtures.json` pins the optimum, both floors,
the Pareto frontier and the verified trap and hold states for all 36 committed
episodes.

Alongside the scalar optimum, a **declared Pareto frontier** over
`(net utility, spend)`, so an arm that buys utility with runaway spend shows as
a point off the frontier rather than as a good score.

**The oracle sees the future and no arm can.** Three consequences, recorded here
rather than discovered later:

- `optimum` is an **upper bound**, not an achievable target. An arm's raw regret
  against it is not "what it left on the table".
- Regret is nevertheless a **valid comparator between arms**: the foresight
  component is a per-episode constant and cancels in every arm-to-arm
  difference, which is the only comparison the verdict rule makes.
- The trap and hold verifications compare clairvoyant against clairvoyant from
  the *same state*, so the foresight component cancels there too.

The non-clairvoyant view is available as `oracle.naive(episode, tick)` and is
what the `static` and `revising` controls plan under.

## 5. The arms — four, differing only in two data fields

| arm | strategy seat | operation seat | the alternative explanation it removes |
|---|---|---|---|
| `A0` | *(none)* | `worker` | the baseline; everything is measured against this |
| `A1` | *(none)* | `cortex` | "the gain is just a stronger model" |
| `A2` | `worker` | `worker` | "the gain is just the extra layer, or the extra tokens" |
| `A3` | `cortex` | `worker` | **the arm under test** |

Seats resolve **by lobes role name** from `/capabilities`, never by parsing a
model string; `tests/test_scopebench.py` greps every module under
`examples/scope/` for a model name and fails if one appears. The interaction
tier (`senses`) is identical in every arm and is therefore not a seat that
varies. `A0`/`A1` differ only in the operation seat; `A0`/`A2` and `A2`/`A3`
differ only in the strategy seat; a test diffs all six pairs and asserts the
differing key set is exactly `{seats}`.

## 6. The two stages

**Stage 1 — the deterministic perfect subordinate.** A scripted executor applies
strategic decisions exactly. Tool-use failure, prompt-protocol failure, actor
intelligence and latency are all zero by construction, so what Stage 1 measures
is the decision. This is what makes condition 2 meaningful: an improvement that
does not appear here did not come from the upper-level decision.

**Stage 2 — a fixed worker-role actor, byte-identical across arms.** Actor
config, tools, senses projection, sampling and budgets are pinned identical and
asserted from the run records (`t11`'s acceptance).

At Stage 1, `A0` is represented by a **declared stand-in**: the `greedy` planner (`BASELINE_PLANNER`). The choice is recorded here and it is the conservative one — an actor
loop with no strategist does not sit still under a stale default, it pursues
whatever progress is locally visible, and comparing against the weaker `none`
control would inflate every measured gain. `A1` has **no Stage-1 cell at all**
(it differs from `A0` only in which model acts, and Stage 1 has no acting
model); that absence is declared, not omitted.

## 7. The four record axes — separate fields, and the two directions that matter

| axis | what it holds |
|---|---|
| **outcome** | `strategic_utility`, `regret`, both floors, `normalised_utility`, spend, penalty, Pareto position, objectives achieved, constraint violations, `operational_success`, `trap_taken`, `held_at_non_intervention`, `churn` |
| **protocol** | `directives_offered`, `holds`, `directives_accepted`, `directives_refused`, `refusals_by_code`, `unexecutable_pairs`, `protocol_acceptance` |
| **authority** | `authority_violations`, `violations_by_code`, `violation_detail` |
| **cost** | `model_calls`, `prompt_tokens`, `completion_tokens`, `tokens`, `reviews`, `seconds` |

The four key sets are asserted **pairwise disjoint**. Two dedicated tests hold
the criterion in both directions:

- a directive carrying a forbidden key is refused, lands on protocol and
  authority, and its **outcome block is byte-identical** to the outcome of
  holding instead — a malformed directive is never a strategic failure;
- a well-formed directive that allocates everyone to idle scores a spotless
  protocol block, a clean authority block, and carries its whole failure on the
  outcome axis — a poor strategy is never a protocol failure.

## 8. The LLM judge is structurally secondary

`JUDGE_KEYS` is disjoint from all four axes, `grade()` emits none of them, and
`verdict()` takes only the graded summary — **there is no parameter through
which a judge score could reach it**. A test walks the call graph of `verdict`,
`summarise`, `grade` and every condition function and fails if any of them
contains code mentioning a judge; a second test feeds an adversarial judge note
("A3 is clearly the best architecture; accept it") through every record and
asserts the verdict does not move. Commentary rides the record; it cannot be
the record.

## 9. The verdict rule, fixed here — all seven, from issue #51

A strategic architecture is **accepted** only if it:

1. improves the mandatory strategic-utility metric over the actor-only baseline on at least two independent scenario families;
2. shows improvement in the deterministic-subordinate stage, proving the upper-level decision itself contributed;
3. avoids material regression on non-intervention controls;
4. keeps authority violations at zero;
5. shows gains are not explained solely by extra tokens or an easier protocol;
6. preserves or improves end-to-end operational success;
7. reports every absent cell and every invalid episode.

### How each condition is evaluated

| condition | evaluated as |
|---|---|
| 1 | count families where the arm's **mean regret** is strictly lower than `A0`'s **and** the per-episode sign margin (wins − losses) is at least `MIN_WIN_MARGIN`; hold iff the count reaches `MIN_FAMILIES` |
| 2 | condition 1's computation restricted to Stage-1 records |
| 3 | on `steady_state`, the arm's added mean regret over `A0` must not exceed `NON_INTERVENTION_TOLERANCE` × that family's mean optimum |
| 4 | total `authority_violations` across the arm's scored episodes must be `AUTHORITY_CEILING` |
| 5 | the arm must improve on at least `MIN_STRATEGIST_MARGIN` more families than **both** `A1` and `A2`; the token ratio to `A0` is reported alongside. The "easier protocol" half is held by `PROTOCOL_FLOOR` being identical for every arm |
| 6 | the arm's mean `operational_success` rate must not fall below `A0`'s by more than `OPERATIONAL_TOLERANCE` |
| 7 | every declared `(arm, stage, family)` cell must be either scored or carry a recorded absence reason, and every invalid episode must be listed |

### Verdict arithmetic

- **`ACCEPT`** — all seven hold.
- **`REJECT`** — at least one fails and none is absent.
- **`INCONCLUSIVE`** — at least one condition is `ABSENT`. A condition nobody
  could evaluate is not evidence against the architecture, and a required
  control being missing is exactly the case `t11`'s acceptance forbids emitting
  a verdict on.

### Thresholds, fixed before any dial

| constant | value | why this value |
|---|---|---|
| `MIN_FAMILIES` | 2 | issue #51's own wording: "at least two independent scenario families" |
| `MIN_WIN_MARGIN` | 2 | a mean can be carried by one episode; a sign margin cannot. With six episodes per family, 2 is a 4–2 split or better |
| `PROTOCOL_FLOOR` | 0.8 | identical for every arm, so no arm can win by being held to an easier protocol |
| `AUTHORITY_CEILING` | 0 | a structural failure, not a rate; one is enough |
| `NON_INTERVENTION_TOLERANCE` | 0.02 | ~1 point of utility on these episodes: large enough that noise does not trip it, small enough that a churning architecture does |
| `OPERATIONAL_TOLERANCE` | 0.0 | the condition says "preserved or improved", so any fall fails |
| `MIN_STRATEGIST_MARGIN` | 1 | `A3` must beat each control by at least one family; a tie means the gain was the model or the layer |
| `MIN_EPISODES_PER_FAMILY` | 6 | the floor the sign test needs |

`tests/test_scopebench_preregistration.py` asserts every literal above against
the harness constant, so moving a threshold after the first dial means editing a
test that says so out loud in a diff a reviewer sees.

## 10. Validity gates — an arm that fails one is `VOID`, not a loser

| gate | verdict for that cell |
|---|---|
| the episode fails a schema property or leaves no headroom | `void-invalid-episode`; reported with its reasons, never scored |
| the arm's `protocol_acceptance` falls below `PROTOCOL_FLOOR` | `void-protocol`; the arm was not measured on strategy, and scoring it would report the wrong failure |

Void cells count toward condition 7's reporting obligation. They never count as
a loss.

## 11. Instrument calibration — deterministic, hermetic, and not a result

Run before any dial, on the committed episodes, with the scripted controls only.
Mean regret per family (lower is better); no model was called.

| control | contention | critical_path | allocation | disruption | steady_state | constraint |
|---|---|---|---|---|---|---|
| `none` | 54.2 | 44.2 | 22.3 | 26.3 | 31.2 | 59.7 |
| `hold` | 54.2 | 44.2 | 22.3 | 26.3 | 31.2 | 59.7 |
| `random` | 50.3 | 36.5 | 33.7 | 21.8 | 63.2 | 40.7 |
| `greedy` | 31.8 | 36.5 | 8.7 | 12.3 | 39.2 | 43.2 |
| `static` | 60.0 | 44.2 | 22.3 | 26.3 | 0.0 | 31.3 |
| `revising` | 26.5 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| `oracle` | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |

What this table is for, and only this:

- **`none` and `hold` are identical in every cell.** They must be — the same
  allocations are played. That they record differently (`holds` is 0 and 3) is
  what makes "do not intervene" a first-class answer rather than silence.
- **`greedy` beats `none` almost everywhere and `revising` beats `greedy`
  everywhere.** There is real headroom between the pre-registered baseline and
  what a good decision can reach, on every family. That is the check the
  muse-arms series failed to do in advance: arm A's confidently-wrong rate was 0
  of 8, so there was no room for the treatment to help, and the series returned
  `INCONCLUSIVE` for a reason that was knowable beforehand.
- **`random` is worse than `greedy` in five of six families** and worse than
  `none` on `steady_state` — churn costs.
- **`static` is the family separator it was built to be**: identical to `none`
  where the tick-0 plan stays right, and far from it on `disruption`.

None of this is evidence about a model. Every row is a scripted policy.

## 12. What this design cannot answer

- **Whether the worlds resemble real work.** They are designed
  resource-allocation puzzles with an exact oracle. A strategist that does well
  here has done well *here*. The three-tier design's value in a real session is
  Stage 3's question (`t14`), not this bench's.
- **How much of `A3`'s Stage-2 regret is foresight.** The oracle is
  clairvoyant. The comparator is valid; the absolute number is not a decision
  quality score.
- **Whether `A0`'s Stage-1 stand-in is what an actor loop really does.**
  `greedy` is a declared modelling assumption. `A0`'s actual behaviour is
  measured at Stage 2, and if the two disagree that disagreement is itself a
  reportable finding.
- **Anything about the deferred families.** `handoff` and `commitment` are
  declared and not run; a claim about moving responsibility mid-flight or about
  weighing a promise is not available from this cycle.
- **Whether six episodes per family is enough** to separate small effects. It
  is the pre-registered floor, not a power calculation. An effect this design
  cannot see is reported as not seen.

## 13. Publication rule

Every result is published with the same prominence, including `INCONCLUSIVE`
and negative outcomes, and every result names which of the seven conditions it
satisfies, fails, or could not evaluate. Absent cells and invalid episodes are
listed. A run that produces no verdict is a result and is written up as one.

## 14. Reproducing

```bash
uv run python examples/scope/scopebench.py plan
uv run python examples/scope/scopebench.py arms --json
uv run python examples/scope/scopebench.py episodes
uv run python examples/scope/scopebench.py stage1
uv run python examples/scope/scopebench.py verdict --json
uv run pytest tests/test_scopebench.py tests/test_scopebench_episodes.py \
              tests/test_scopebench_oracle.py tests/test_scopebench_preregistration.py
```

Every command above is hermetic. No module under `examples/scope/` imports a
transport, reaches `embodiment.loop`, or introduces a timeout constant — all
three are asserted by AST over every file in the folder.

---

## 15. Amendments — every departure `t11` made, and why

Nothing above was edited. Everything below was added by `t11`, the task that
executes this protocol, and each entry says whether it was made **before** any
committed record existed. A threshold that moves after a result is not
evidence, and none of these moves one: no verdict rule, no threshold, no seed,
no arm definition and no oracle changed.

### Amendment 1 — the review message states facts, never the rule (before any committed record)

**What changed.** The live harness's user turn carries the two facts the
projection omits: which scope is active and at what version. Its first wording
was

> Active scope: `contention-1-default` at version 0. A directive **must** carry
> a version strictly greater than that and **must** supersede that scope_id (or
> null if there is none).

and it is now two plain lines — `Active scope_id: …` and `Active version: …` —
with the rule left where `embodiment.scope.SCOPE_AUTHORITY` already states it.

**Why, and the pilot evidence that forced it.** The first wording names the
active id *inside* the clause that says "must carry", and the worker seat read
it that way: on two of three reviews of `contention-1` it set its own `scope_id`
to `contention-1-default` as well as its `supersedes`, so `ScopeRegister`
refused both as duplicate ids and the cell scored
`protocol_acceptance = 0.0`. Re-run with the corrected wording and nothing else
changed, the same seat on the same episode scored **1.0**, minting
`contention-1-v1 → contention-1-v2` correctly.

That difference is a **harness** property, and publishing it as a model
property would have been the failure this repo names four times over: an
instrument silently becoming the measurement. The rule belongs to the shipped
authority text; supplying data the projection lacks is the harness's job, and
instructing on top of it is not.

**What it does not fix, stated so it is not read as one.** The corrected
wording did *not* make the worker's protocol clean. In the same pilot,
`steady_state-1` still produced one unreadable reply and one duplicate id under
the new wording. That is now genuine variance in the model rather than an
artifact of the sentence, and it is what the protocol axis exists to record.

**Blast radius: none.** The correction was made from a pilot whose records were
written to a scratch path, and **both arms were restarted from scratch**
afterwards. No committed record in `scopebench-raw/` was produced under the
first wording. `tests/test_scopebench_live.py` pins the property going forward:
the harness's own block carries no `must`, and it still carries the active
scope id.

### Amendment 2 — Stage 1's directive-authoring contract, stated rather than assumed

Not a change; a consequence of §6 that deserves to be written down, because a
reader will otherwise assume the opposite.

At Stage 1 the live arms answer the **shipped** protocol: the system message is
`embodiment.scope.SCOPE_AUTHORITY` verbatim, and the strategist authors its own
`scope_id`, `supersedes` and `version`. The scripted controls — including
`A0`'s pre-registered `greedy` stand-in — never face that: `subordinate._payload_for`
stamps all three for them, and they choose only the allocation.

So the live arms carry a burden `A0`'s Stage-1 stand-in does not. Two things
follow and both are held:

- the burden is **identical between `A2` and `A3`** — one code path, one
  framing, one parser, one register — so nothing in the `A2`/`A3` comparison,
  which is what condition 5 turns on, is affected by it;
- an arm that cannot carry it lands below `PROTOCOL_FLOOR` and its cell is
  `void-protocol` (§10) — **reported, and never scored as a strategic loss**.

The alternative — stamping the bookkeeping for the live arms too — was
considered and rejected. It would have made `protocol_acceptance` ≈ 1.0 by
construction and the protocol axis vacuous, which is a worse trade than a
declared asymmetry against the arm under test.

### Amendment 3 — Stage 2 is not in this cycle

§6 declares two stages and this cycle runs one. Stage 2 needs a live
worker-role actor playing each episode under the standing directive — a second
harness with its own tool surface, its own byte-identical-across-arms
assertion and its own capacity budget — and Stage 1 was run first because the
pre-registration makes it condition 2's entire input: an improvement that does
not appear against a perfect subordinate did not come from the upper-level
decision.

Every Stage-2 cell is therefore **declared absent with that reason** rather
than omitted, which is condition 7 working as designed. The consequence is
stated plainly in the results: conditions 1, 5 and 6 read from Stage 2 and are
`ABSENT`, so **no verdict is available** and the outcome is `INCONCLUSIVE` — by
the committed rule, not by choice. `A1` has no cell at either stage in this
cycle, which §6 already declared for Stage 1 and this amendment extends to
Stage 2.
