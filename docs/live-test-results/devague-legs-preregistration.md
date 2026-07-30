# Pre-registration — the devague legs across two minds (plan task t6, issue #20)

**Written and committed BEFORE the first live dial of this experiment.**
Nothing in this file was chosen after seeing a result, and the git history is
the evidence: this document and the literal constants it quotes land in their
own commit
(`tests/test_devague_legs_preregistration.py`), and the first devague command
this experiment issues lands in a later one, under task t9.

This document does not build the experiment harness. Task t9 owns
`examples/` and runs the legs; this document is the contract t9 executes
against, and the threshold test is the pin that makes moving a number after
data comes back a visible, explained diff.

## Why pre-registration is the point, not paperwork

The last cycle shipped four graders, and found all four defective only by
reading the underlying data by hand, never by a test
(`docs/plans/next-cycle-candidates.md:79-101`, the four-failure table at
lines 84-89):

| Grader | How it failed |
|---|---|
| t18 failure classifier | charged every non-`finished` exit as protocol; 5 of 6 were reasoning |
| t17 challenge grader | scored `CHALLENGED` for 32 anchors and no argument |
| t19 `read_objective` | blind to the paraphrase the mind actually writes |
| t19 `'cp-west' in plan` | scored the control's board-enumerating plan as a hit |

That is the whole argument for this experiment's shape
(`next-cycle-candidates.md:126-143`): `devague plan converge` and
`devague plan waves` are graders **we did not write**. Convergence is
pass/fail plus enumerated gaps; `waves` refuses a cyclic or dangling
dependency graph outright. Neither can be made to look better by us reading
the data more charitably, because we do not own the code that produces the
verdict.

`next-cycle-candidates.md:103-108` (M3) names the second failure mode this
document exists to close: "both pre-registrations worked, and both had holes
prose did not catch." This document states dependent variables as field
names a runner reads off `devague`'s own `--json` output, not as prose a
runner has to interpret, for exactly that reason.

## The baseline this incorporates (plan task t1)

`docs/live-test-results/muse-cycle-baseline.md` freezes three numbers before
any fix in this cycle lands. Only one bears directly on this experiment's
design, and it is why `/spec-to-plan` and `/assign-to-workforce` stay with
the cortex rather than becoming an open question re-litigated here:

> On the executive axis of the association-work 2×2, the muse
> (`nvidia/Gemma-4-31B-IT-NVFP4`) failed 6 of its 9 runs. Of those 6
> failures, **5 state a definite final answer, and every one of the five is
> wrong.** … n = 9 per cell, one rig, one model pair, three executive
> problems, "mostly at a wall per problem."
> — `muse-cycle-baseline.md`, "The confidently-wrong rate"

Constraint satisfaction — enumerating coverage targets, checking a
dependency graph for cycles, deciding whether an acceptance criterion is
testable — is executive-axis work in that same sense. A mind that is
confidently wrong 5 of 6 times on the executive axis is not the mind to make
final on the one leg with a hard pass/fail gate, independent of how well it
reflects. That is the headroom premise for keeping `/spec-to-plan` and
`/assign-to-workforce` on the cortex in the leg split below, not an
assumption reached for convenience.

The baseline's other two numbers — the close-time late-drop rate and the
dead-degradation-code count — are muse-counsel-delivery mechanics (task t2's
and t4's territory) and are not re-cited here: neither is evidence about
constraint-satisfaction quality, and citing them would pad this document
without changing its design.

## The leg split (requirement 1)

`next-cycle-candidates.md:118-215` (Tier 2b) proposes the split and records
the one structural correction it needs: the muse's seam is tools-off by
design (`embodiment/muse.py:513`, "one tools-off model turn, messages in,
response out. No tool schema is ever passed and no tool result is ever
read"), so a leg that requires reading a repo cannot be pure-muse. The split
below is "cortex reads, muse reflects," not "muse acts":

| Leg | Mind | Shape |
|---|---|---|
| `/scope` | cortex reads, muse proposes | cortex runs the mechanical reads (`git ls-files`, file opens); muse proposes what else to look at and what the findings imply. Provenance stays with the reader. |
| `/think` | either | claim capture is composition, not retrieval — the natural place to A/B the two minds. Both arms run it below. |
| `/challenge` | **muse** | the lenses *are* reframing work. Cortex supplies the exported spec plus what it read; the muse hunts blind spots in it. |
| `/spec-to-plan` | **cortex** | constraint satisfaction against a deterministic gate — the leg the baseline above keeps off the muse. |
| `/assign-to-workforce` | **cortex** | wave scheduling, file-disjointness, tool authority. |
| `/deviate` | **muse** | **the cheapest first slice** — see Experiment 2. No tools needed, and it has a genuine cognitive asymmetry: the mind that authored a plan is invested in it; a separate mind is not. |

`CHEAPEST_FIRST_LEG = "deviate"` and the full table above are pinned by
value in `tests/test_devague_legs_preregistration.py::TestPreRegisteredThresholds`.

## Experiment 1 — the same-mind control on `/think` + `/spec-to-plan`

This is the mandatory comparison
(`next-cycle-candidates.md:210-214`): "the comparison worth running is
whether a plan Qwen builds from Gemma's frame converges better … than one
Qwen builds from its own frame. With a same-mind control arm, because t18
and t19 both taught that the control is where the result actually lives."

### The arms

- **`ARM_CROSS`** — Gemma runs `/scope` (proposing; cortex reads),
  `/think`, and `/challenge`. Every claim it proposes is captured
  `--origin llm` and lands `proposed`; the human confirms each one before
  the frame converges (see the Gemma-proposes-human-confirms section
  below). Qwen then runs `/spec-to-plan` and `/assign-to-workforce`
  against that human-confirmed frame.
- **`ARM_CONTROL`** — Qwen runs the entire pipeline solo, end to end:
  its own `/scope`, `/think`, `/challenge`, `/spec-to-plan`, and
  `/assign-to-workforce`. The human still confirms every claim before the
  frame converges, so the confirm-gate discipline is identical across arms
  and the only variable is which mind authored the frame's content.

Both arms run on the same devague version, the same base commit, and the
same topic. Nothing about tool authority, model routing, or safety policy
changes between arms — only which mind's claims populate the frame.

### Topics, fixed now

Two already-filed, currently open GitHub issues on this repo, chosen by a
rule applied before any dial:

1. **Not already delivered.** A repo-wide screen against
   `docs/deliveries/*.md` found that several open-looking issues are
   already shipped: issue #4 (events-cli emission) landed under deviation
   `d3` as `embodiment/events.py`
   (`docs/specs/2026-07-25-function-first-loops-muse-redesign.md:108`), and
   issue #6 (subagent seam) landed as tasks t9–t11 of the
   `function-first-loops-muse-redesign` plan (`docs/deliveries/2026-07-25-function-first-loops-muse-redesign.md:66-68`,
   all three rows `delivered`). Both stayed open on GitHub despite being
   done — thinking through an already-answered question would test
   retrieval, not reasoning, so both are excluded.
2. **Not already scheduled in this cycle's own plan** (excludes the
   pad/headspace and drain-related issues #17, #18, #20, #21).
3. **Not foundational scope-setting** (excludes #1, #2 as oversized for a
   bounded pipeline run).

What remains, screened the same way: issue #9 ("Scratchpad, planner and
ledger: a mind's record of itself, survivable across a reset" — the
scratchpad component shipped under task t8, but the issue's own body says
the planner and pad-plus-ledger joining "is the least developed of the
three," and resumption (`run(..., resume_from=pad)`) is stated as an open
question, not a shipped one) and issue #5 ("A host-providable Python
execution seam, so a drive can actually compute" — distinct from this
cycle's muse-scoped headspace tool, since #5 is about the **actor** loop,
not the muse lane).

```text
TOPIC_ISSUES = (5, 9)
N_TOPICS = 2
```

**Substitution rule, stated now so a substitution is a pre-committed act, not
a discretionary one:** if either issue closes before the run, the next
lowest-numbered open issue passing the same three-part screen is substituted,
and the substitution is recorded in the run config and filed as a lapse
(`provenance-missing` if the closure changes what "not yet answered" means
for that topic, `assumption-for-measurement` otherwise).

**A deliberate side effect, named rather than discovered:** unlike a
scratch-store rig probe, devague frames and plans are committed, repo-native
state. The frames and plans this experiment produces for issues #5 and #9 are
real exploratory work product on those issues, independent of this
experiment's verdict on the leg split. That is accepted, not incidental —
this pre-registration does not require the byproduct to be discarded.

### Settings

| setting | value |
|---|---|
| devague version | `0.22.0`, recorded in every run config (`PINNED_DEVAGUE_VERSION`) |
| topics | issues #5, #9 (`TOPIC_ISSUES`), paired across both arms |
| cortex | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` |
| muse | `nvidia/Gemma-4-31B-IT-NVFP4` |
| identity | unconfigured — no Gwen framing in either arm, matching the association-work precedent's choice to isolate the variable under test |
| confirm gate | identical in both arms: every claim needs a recorded human `--confirm` before the frame converges |
| base commit | recorded per run config; asserted to predate the muse tool-seam merge, per this cycle's own scope boundary |
| plan/frame slugs | `devague-legs-<arm>-issue<N>`, e.g. `devague-legs-cross-issue9` |

### The gate DVs (requirement 3)

Read directly off `devague plan converge --json` and
`devague plan waves --json` — never re-derived or re-interpreted:

- **`DV-ITER` — gate iterations to convergence.** The count of
  `devague plan converge` calls, from the first call after `devague plan
  new` to the first call reporting overall pass. A plan that has not
  converged after `MAX_GATE_ITERATIONS` calls records
  `MAX_GATE_ITERATIONS + 1` and is treated as non-convergent for that arm.
- **`DV-GAPS` — gaps at first converge.** The total count of gap entries
  `devague plan converge --json` enumerates on the **first** call, summed
  exactly as the tool reports them (uncovered coverage targets +
  criteria-less tasks + dangling dependency edges), before any repair.
- **`DV-WAVE` — wave width.** The maximum task count in any single wave
  from the first successful `devague plan waves --json` call against the
  exported plan. Reported, not thresholded: a property of the plan's
  parallelism, not a claim about its quality.
- **`DV-REFUSE` — dangling/cyclic refusal.** A one-time grader self-check,
  not a per-arm measurement: before trusting `devague plan waves`'s
  silence on the four real plans, build one synthetic plan with a
  deliberate 2-task mutual dependency (or a dependency on a nonexistent
  task id) and confirm `devague plan waves` refuses it under the pinned
  version. This is the vacuity assertion the M2 lesson asks for — proof
  the refusal mechanism fires at all, run once per devague version in use,
  before either arm's real plans are trusted.
- **`DV-CRITERIALESS` — criteria-less tasks at first converge.** The
  criteria-less-task subcomponent of `DV-GAPS`, reported on its own
  because it is the sharpest, most literal signal available: did this
  mind write testable acceptance criteria, yes or no, per task.

`DV-ITER` and `DV-GAPS` are the headline, threshold-bearing comparisons.
`DV-WAVE`, `DV-REFUSE`, and `DV-CRITERIALESS` are always published as
diagnostics; they do not gate the verdict.

### Literal thresholds (requirement 4)

All asserted by value in
`tests/test_devague_legs_preregistration.py::TestPreRegisteredThresholds`:

| constant | value | reasoning |
|---|---|---|
| `MAX_GATE_ITERATIONS` | **5** | a real plan of the size this cycle's own tasks run (10-20 tasks) converges within a handful of amend/cover/accept rounds in practice (this repo's own three delivered plans did); five rounds is generous headroom before calling an arm non-convergent, not a tight pass bar |
| `MIN_GATE_ITERATION_MARGIN` | **1** | one full converge round is the smallest unit the gate itself produces — not a fractional score. The real bar is that both topics must agree in direction (below), not the margin's size |
| `MIN_GAP_MARGIN` | **2** | a 1-gap difference is within plausible wording noise (a target marked covered by one phrase and not by a near-synonym); 2 is the smallest difference this design treats as a signal rather than noise |
| `MAX_ERROR_FRACTION` | **1/3** | reused verbatim from `association-work-preregistration.md`'s `V2` — the same reasoning applies regardless of domain: no more than a third of the `converge`/`waves` invocations in an arm's pipeline may be transport or tooling errors (subprocess crash, non-JSON output) before that arm's result is untrustworthy |

### The decision rule and the INCONCLUSIVE condition (requirement 5)

Let, per topic:

- `G_cross`, `G_control` = `DV-ITER` for each arm (capped at
  `MAX_GATE_ITERATIONS + 1` if non-convergent)
- `K_cross`, `K_control` = `DV-GAPS` for each arm
- `ΔG = G_control − G_cross` (positive ⇒ CROSS converged in fewer rounds)
- `ΔK = K_control − K_cross` (positive ⇒ CROSS had fewer gaps)

**Validity gates — ALL must hold, or the verdict is `INCONCLUSIVE`:**

| id | condition |
|---|---|
| V1 | all 4 pipelines (2 arms × 2 topics) produce a completed plan artifact, or a substitution per the topic rule above stands in for one that does not |
| V2 | every one of the 4 run configs records `devague --version` exactly `0.22.0`; a mid-series version change is filed as `instrument-changed-mid-series` and the series is not pooled |
| V3 | not every one of the 4 runs converges in exactly 1 iteration with 0 gaps (a floor — the gate had nothing to discriminate), and not every one fails to converge within `MAX_GATE_ITERATIONS` (a ceiling) |
| V4 | across all `converge`/`waves` invocations issued during the whole experiment, the transport/tooling-error fraction does not exceed `MAX_ERROR_FRACTION` |

**Effect thresholds, applied only once V1–V4 hold:**

- **`SUPPORTS-CROSS`** — on **both** topics, `ΔG ≥ MIN_GATE_ITERATION_MARGIN`
  **and** `ΔK ≥ MIN_GAP_MARGIN` (both headline DVs favour CROSS, both
  topics agree).
- **`SUPPORTS-CONTROL`** — the symmetric case: both topics show
  `ΔG ≤ −MIN_GATE_ITERATION_MARGIN` and `ΔK ≤ −MIN_GAP_MARGIN`.
- **`NEGATIVE`** — V1–V4 hold, and neither of the above: the two DVs
  disagree in direction, the topics disagree with each other, or a margin
  is not met. A complete, reportable result.
- **`INCONCLUSIVE`** — any of V1–V4 fails.

`DECISIONS = ("SUPPORTS-CROSS", "SUPPORTS-CONTROL", "NEGATIVE",
"INCONCLUSIVE")`, pinned by value. Requiring both headline DVs to agree, on
both of only two topics, is a deliberately strict bar for `n = 2` — the
same discipline arena-series-preregistration.md states directly: "an honest
small n beats an aspirational large one that never finishes," and a strict
bar keeps a 2-topic design from manufacturing a supported verdict out of
noise.

### Predictions, as falsifiable statements — not results

**Predicted direction:** `ARM_CROSS` shows fewer gaps at first converge and
fewer gate iterations than `ARM_CONTROL`, because a `/challenge` pass from a
separate, uninvested mind should catch blind spots a solo self-review
misses — the same asymmetry Tier 2b names for `/deviate` ("the mind that
authored a plan is invested in it; a separate mind is not").

**The live counter-consideration, stated because it is real, not because it
is comfortable:** `docs/live-test-results/memory-echo-chamber.md` found that
a confidently-authored record beat a live cortex 6 of 6 times, in both
directions, with correct labelling. A frame handed from Gemma to Qwen
carries exactly that risk in a milder form — a confidently-authored frame
could be trusted rather than scrutinized, which could show up as `ARM_CROSS`
converging *fast* while the gate still catches gaps the human-confirm step
did not. If `ARM_CROSS` converges in fewer iterations *and* has *more* gaps
at first converge than `ARM_CONTROL`, that specific split is the echo-chamber
risk showing up in this design, and is named as such in the results, not
folded into either supports-verdict.

## Experiment 2 — the `/deviate` cheapest-first slice

This is what task t9 runs first: no tools, no repo write, and the ledger
material already exists. Its job is narrower than Experiment 1's — validate
the human-confirm pipeline mechanics live, at low cost, before spending the
larger budget on the four `/think`+`/spec-to-plan` pipelines above.

### The case pool — grounded in already-committed history

Rather than invent scenarios, this leg draws on real, already-approved
deviation records in `.devague/deliveries/`, plus a matched set of tasks
those same deviations never touched — so the ground truth ("was a deviation
in fact warranted here") cannot be adjusted after the fact; it is already
git history.

**Positive cases — a deviation was filed and approved (`status: approved`
in the committed ledger):**

| case | what deviated |
|---|---|
| `gwen-loop-presence-continuity:d1` | the muse becomes a second, parallel thinking loop on its own thread |
| `gwen-loop-presence-continuity:d2` | eidetic-cli and coherence-cli become base dependencies |
| `gwen-loop-presence-continuity:d3` | event emission through events-cli lands as new scope |
| `gwen-loop-presence-continuity:d4` | live rig testing becomes an acceptance bar |
| `gwen-loop-presence-continuity:d5` | live self-testing lands as new scope |
| `function-first-loops-muse-redesign:d1` | the two register puzzles cannot be re-authored; only the designed subset problem is buildable now |

**Negative cases — delivered tasks no deviation's `task_ref` or `affects`
list names, selected by a fixed rule (the lowest-numbered untouched task
from each plan, alternating, until six are collected):**

| case | task |
|---|---|
| `gwen-loop-presence-continuity:t1` | Carve the data contract into `embodiment/contract.py` |
| `function-first-loops-muse-redesign:t1` | Reframe the muse prompt |
| `gwen-loop-presence-continuity:t3` | Port context windowing and media handling |
| `function-first-loops-muse-redesign:t2` | Counsel-kind self-labelling |
| `gwen-loop-presence-continuity:t5` | No-shell host fixture |
| `function-first-loops-muse-redesign:t3` | Kind-aware delivery in the runner |

Both pools, and that every positive case has committed `status: approved`
and every negative case is genuinely untouched by any recorded deviation,
are asserted directly against the committed JSON ledgers in
`tests/test_devague_legs_preregistration.py::TestDeviateCasePoolsAreGroundedInCommittedHistory` —
not merely pinned as literal strings that happen to look right.

### Protocol

For each of the 12 cases, both minds independently review the same
input — for a positive case, the deviation's own `reason` text with the
`id`/`status`/`classification` fields redacted; for a negative case, that
task's "Actual Delivery" row from the plan's `docs/deliveries/*.md` — and
answer: does this warrant filing a `devague deviate`, and if so, under which
classification? Neither mind is told which pool a case is drawn from.

**Structural claim (pass/fail, mirrors t9's own acceptance criterion):**
every Gemma-authored deviation judgment in this leg is captured
`--origin llm` and lands `proposed`; zero become `confirmed` without a
recorded human `--confirm`. Any single violation falsifies this claim.

**Exploratory note, not a verdict:** report the per-mind concordance table
(hits, misses, false alarms across the 12 cases) for both Qwen and Gemma,
naming the self-invested-vs-uninvested asymmetry as the variable of
interest. This is **explicitly not powered for a threshold verdict** — 12
judgments from one run each cannot support one — and no `SUPPORTS`/
`NEGATIVE`/`INCONCLUSIVE` label is assigned to it. It is published as data,
not read as a finding.

## Version pinning (requirement 6)

`devague 0.22.0` is installed and is the graded instrument for this
experiment (`PINNED_DEVAGUE_VERSION = "0.22.0"`, verified installed on this
rig 2026-07-30 via `devague --version`). Every run — both experiments —
records its own `devague --version` output in its committed run config.
Runs recorded against a different version are **not pooled** with runs
recorded against `0.22.0`; a version change mid-series is itself filed as a
lapse (`instrument-changed-mid-series`), per honesty condition h35, not
silently absorbed into the result.

## The lapse protocol (requirement 7)

devague `0.22.0` ships an append-only reasoning-degradation ledger:

```text
devague lapse "<what>" --code <code> --skipped "<check>" --ref <ref> --origin llm|user
```

with six codes, pinned by value in
`tests/test_devague_legs_preregistration.py`:

`assumption-for-measurement`, `grader-unverified`, `control-absent`,
`n-below-claim`, `instrument-changed-mid-series`, `provenance-missing`.

**Wired into this experiment's run protocol, concretely — filed the moment
the substitution happens, never reconstructed afterward:**

| trigger during the run | code |
|---|---|
| a topic issue closes and is substituted (see the substitution rule above) | `assumption-for-measurement` or `provenance-missing`, per the doc's own rule |
| the `DV-REFUSE` grader self-check is skipped and the real plans' convergence is trusted anyway | `grader-unverified` |
| Experiment 1's control arm is dropped for cost reasons | `control-absent` |
| a pipeline aborts and is not replaced, so an arm's completed-run count falls below what V1 requires | `n-below-claim` |
| `devague --version` changes between any two of the four pipelines | `instrument-changed-mid-series` |
| a claim in the eventual results document cites something not actually read | `provenance-missing` |

This distinguishes a **lapse** (a degradation in *how this measurement was
taken*) from a **deviation** (a divergence from *the confirmed plan* itself,
filed via `devague deviate`): the topic-substitution rule above is a lapse
about the measurement's own integrity, not a plan deviation, and the two
ledgers are not interchangeable.

`--origin llm` lands the lapse as `proposed`; only a recorded human
`--confirm` moves it further. The pre-registration describes this protocol.
It does not file a lapse — there is no run yet to have degraded.

## The Gemma-proposes-human-confirms pipeline (requirement 8)

`next-cycle-candidates.md:204-208`: "the method already carries the
mitigation, and it constrains the split: `--origin llm` lands `proposed`,
and *only the user confirms*. So the viable arrangement is Gemma proposes →
human confirms → Qwen plans. It cannot be 'Gemma confirms.'" That is the
existing spec gate doing the job
`docs/live-test-results/memory-echo-chamber.md`'s 6/6 result demands — a
record (or a claim) wearing the authority of "something confirmed" beat a
live cortex 6 of 6 times, in both directions, with correct labelling. This
experiment does not invent a new gate; it exercises the one already in the
method and asserts the exercise is real:

- every claim Gemma proposes across both experiments is captured
  `--origin llm` and starts `proposed`;
- the confirm log for every run shows **zero** confirms not attributable to
  a recorded human action;
- no frame claim authored by the muse reaches `confirmed` status any other
  way.

`LLM_ORIGIN_STATUS = "proposed"` and `HUMAN_CONFIRM_STATUS = "confirmed"`
are pinned by value.

## Rules fixed in advance

1. **A degraded pipeline is data.** A pipeline that records a degradation,
   or that aborts, is counted and reported with its degradation — not
   discarded, not re-run for a cleaner number.
2. **Nothing is re-run to get a better number.** Each of the 4 Experiment-1
   pipelines and the 12 Experiment-2 cases runs once, at the pre-registered
   configuration.
3. **The result is recorded either way** — `SUPPORTS-CROSS`,
   `SUPPORTS-CONTROL`, `NEGATIVE`, and `INCONCLUSIVE` are all publishable
   outcomes, none is a prompt to re-run.
4. **Every raw devague `--json` response is committed** alongside the
   results document, one artifact per call, not verdicts-and-excerpts only —
   the same discipline `association-work-preregistration.md` adopted after
   its own predecessor series could only re-grade 3 of 18 responses.
5. **The `DV-REFUSE` grader self-check runs once, before the four real
   pipelines, and is not itself a measured cell** — the same role a warm-up
   match plays in `arena-series-preregistration.md`: establishing trust in
   the instrument, not contributing a data point.
6. **This experiment does not touch the shared eidetic store.** `/scope`,
   `/think`, `/challenge`, `/spec-to-plan`, and `/assign-to-workforce` are
   devague-CLI moves against `.devague/` state, not memory-store calls; if
   either mind's session tool surface exposes `recall`/`remember` during a
   run, any invocation is logged and is explicitly out of scope for this
   experiment's claims.

## What this pre-registration cannot show

- **Anything about model quality in general.** Two topics, one rig, one
  cortex/muse pair. A result here is about this leg split, on this rig,
  not a claim about Gemma or Qwen as such.
- **A powered verdict on the `/deviate` leg's concordance.** 12 judgments
  from a single pass is exploratory data, stated as such above, not a
  claim this design can support with a threshold.
- **Generalisation past devague 0.22.0.** The gate's own shape (what
  `converge --json` enumerates as a gap, what `waves --json` reports) is
  part of what is being measured; a later devague version that changes
  that shape is a different instrument, and results do not pool across the
  boundary.

## Reproducing

No harness exists yet — task t9 builds one that subprocesses these exact
devague invocations (task t7's decision: the grader stays external and
per-run version-pinnable, never imported). The protocol above is the
contract t9's harness executes; until then, the moves are:

```bash
devague --version   # must print 0.22.0 before any run begins

# Experiment 1, arm CROSS, topic issue #9 (repeat per arm x topic)
devague scope --frame devague-legs-cross-issue9 ...      # cortex reads, muse proposes
devague think --frame devague-legs-cross-issue9 ...      # Gemma
devague challenge --frame devague-legs-cross-issue9 ...  # Gemma; human confirms every claim
devague plan new --frame devague-legs-cross-issue9 --title "..."
devague plan converge --plan devague-legs-cross-issue9 --json   # DV-ITER, DV-GAPS
devague plan waves --plan devague-legs-cross-issue9 --json      # DV-WAVE

# Experiment 2, per case in DEVIATE_POSITIVE_CASES / DEVIATE_NEGATIVE_CASES
devague deviate "<judgment>" --origin llm --classification <...>  # Gemma's proposal
devague deviate --confirm <id>                                     # human only
```
