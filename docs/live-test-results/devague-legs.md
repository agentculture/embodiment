# The devague legs across two minds — results (plan task t9, issue #20)

Contract: [`devague-legs-preregistration.md`](devague-legs-preregistration.md),
committed before the first dial. This document reports what the pre-registered
protocol actually produced. Nothing here was chosen after seeing a number.

**Run date:** 2026-07-30. **Raw transcript:**
[`devague-legs-deviate.jsonl`](devague-legs-deviate.jsonl) — every prompt and
every response, 24 completions, nothing summarised away. **Run config:**
[`devague-legs-deviate-config.json`](devague-legs-deviate-config.json).

## Read this first — one of the two experiments did not run

The pre-registration defines **two** experiments. Only one of them was executed.

| Experiment | Status |
|---|---|
| **Experiment 2** — the `/deviate` cheapest-first slice, 12 cases × 2 minds | **RAN.** 24 of 24 completions, 0 degraded |
| **Experiment 1** — the same-mind control on `/think` + `/spec-to-plan`, 4 pipelines | **ABSENT. Did not run at all.** |

**Experiment 1 has no result.** Not a partial one, not a weak one — none. No
arm ran, no pipeline was built, no `DV-ITER`, `DV-GAPS`, `DV-WAVE`,
`DV-CRITERIALESS` or `DV-REFUSE` value exists, and neither `ARM_CROSS` nor
`ARM_CONTROL` was attempted. Applying the pre-registered decision rule
mechanically would return `INCONCLUSIVE` (validity gate V1 fails: 0 of 4
pipelines produced a plan artifact), but printing that label here would dress
an unrun experiment as a measured outcome. It is reported **ABSENT**.

**Why it did not run — a structural reason, not a budget one.** Two
constraints made it un-runnable in this session, and the second is the deeper
of the two:

1. The operator instruction for this run was **no state-mutating `devague`
   command**. Experiment 1 is nothing *but* state-mutating moves — `devague
   new`, `capture`, `confirm`, `plan new`, `plan task`, `plan accept`, `plan
   depend`, `plan cover`, `plan confirm`. Only `plan converge` and `plan
   waves` are read-only, and both require a plan that the forbidden moves
   would have had to create first.
2. More fundamentally, **the pre-registration's own requirement 8 puts a human
   in the loop of every one of the four pipelines**: "every claim needs a
   recorded human `--confirm` before the frame converges", identically in both
   arms. `--origin llm` lands `proposed`, and *only the user confirms*. An
   agent cannot supply those confirms without falsifying the exact structural
   claim the experiment exists to protect. Experiment 1 is therefore not a
   long agent task — it is a **human-gated** one, and it needs an operator
   present for each claim across four pipelines.

That second point is worth carrying forward: the mitigation that makes the
cross-mind pipeline safe is also what makes it un-automatable. It is not a
flaw in the design; it is the design working. But it means "run Experiment 1"
is a scheduling ask on a person, not a compute ask on a rig.

## Run configuration, as recorded

Both facts below are read from the machine at run time and written into the
committed config, never hardcoded from a brief.

| field | value |
|---|---|
| `devague --version` output | `devague 0.22.0` |
| matches `PINNED_DEVAGUE_VERSION` (`0.22.0`) | **yes** |
| base commit | `ffaaf4819e8f15b35ed454dd9a6bf5f897465048` (`ffaaf48`) |
| base commit subject | `merge(t5): terminal-drain delivery record on its own stream, not the degradation ledger (#17)` |
| predates the muse tool seam | **yes**, asserted — see below |
| cortex | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP`, temperature 0.3, `max_tokens` 4000 |
| muse | `nvidia/Gemma-4-31B-IT-NVFP4`, temperature 0.3, `max_tokens` 2000 |
| identity | unconfigured — no Gwen framing in either arm |
| state-mutating `devague` commands issued | **0** |

**The pre-seam assertion is checked, not claimed.** The muse tool seam is this
cycle's plan task `t13` (headspace-cli as a dependency plus the workspace
tool). The harness records two facts that jointly establish the base commit
predates it, and refuses to run if either is false:

- `embodiment/headspace.py` does not exist — `False`
- `embodiment/muse.py` still declares the tools-off invariant ("No tool schema
  is ever passed") — `True`

**One temperature for both minds.** 0.3 each, recorded separately. An A/B
whose arms differ in temperature measures the temperature — the hidden
variable [`configurations.md`](configurations.md) found running through an
earlier series.

## The confirm-log assertion — the non-negotiable one

The pre-registration's requirement 8 states the structural claim as pass/fail:
every model-authored judgment is captured `--origin llm`, lands `proposed`,
and **zero** become confirmed without a recorded human action. This is the
mitigation for [`memory-echo-chamber.md`](memory-echo-chamber.md)'s 6/6 result,
where a record wearing remembered authority beat a live cortex in both
directions.

**Result: PASSES. Zero confirms not attributable to a human.**

Three independent checks, because one assertion about our own bookkeeping
would only prove our own bookkeeping:

| check | result |
|---|---|
| all 24 captured judgments are `origin=llm`, `status=proposed`, `confirmed_by=null` | pass, 0 violations |
| both committed deviation ledgers are byte-identical before and after (sha256) | pass — unchanged |
| `devague deviate --list --json` confirm log identical before and after, both plans | pass — unchanged |

The ledger digests are in the transcript's `summary` record. `git status`
after the run shows no modification to `.devague/` at all.

One `origin=llm` record does sit `approved` in the ledger —
`function-first-loops-muse-redesign:d1` — and it **predates this run
entirely**, carrying a recorded human `--confirm` in git history. This run
added none.

### Where this assertion is weaker than pre-registered — stated, not buried

The pre-registration's "Reproducing" section sketches the capture as `devague
deviate "<judgment>" --origin llm`, i.e. exercising devague's own gate live.
**That step was not executed.** The operator instruction for this run
prohibited state-mutating `devague` commands, and independently, writing
twelve synthetic judgments into this repo's real deviation ledger would
corrupt the very ground truth the case pool is drawn from — the positive pool
*is* that ledger. The judgments were captured into the committed JSONL
artifact with the same `origin`/`status`/`confirmed_by` fields instead.

The honest consequence: this run proves **no confirm happened** (by digest,
which is the strongest available form of that claim), but it does **not**
prove devague's confirm gate *fires*. Absence of confirms in a run that issued
no capture is close to vacuous on the second question, and the M2 lesson the
pre-registration cites is precisely about not trusting an untested refusal
mechanism.

What is offered instead is **static** evidence, labelled as such and never as
a live exercise — the installed instrument's own source at
`devague/delivery.py`:

```python
status = "proposed" if origin == "llm" else "approved"
```

and `deviate --confirm` mapping `proposed → approved`, documented in
`devague/cli/_commands/deviate.py` as "the anti-fabrication rule that LLM
proposals never self-confirm."

**A vocabulary mismatch in the pre-registration, found here.** It pins
`HUMAN_CONFIRM_STATUS = "confirmed"`. devague's *deviation* ledger has no
`confirmed` status — its three are `proposed`, `approved`, `rejected`, and a
human `--confirm` produces `approved`. `"confirmed"` is the *frame-claim*
vocabulary, not the deviation ledger's. The constant is left as pinned rather
than edited after the fact; the mismatch is recorded here.

## Experiment 2 — the `/deviate` slice

12 cases, both minds judging each blind and independently, one completion
each. Positive cases are the deviation's `what` + `reason` from the committed
ledger; negative cases are the task's Actual Delivery row from the committed
delivery document. Neither mind was told which pool a case came from.

**Redaction, stated exactly.** The protocol names three fields to redact
(`id`, `status`, `classification`). The same paragraph also requires blinding.
Those interact: the positive pool carries `origin` and `affects` fields the
negative pool has no counterpart for, so leaving them in would be a structural
tell. The harness therefore redacts a **superset** — id, status,
classification, origin and affects — presenting only free prose plus the plan
slug and item ref, which both pools have. Nothing was added.

### Per-case results

| case | pool | truth | cortex | muse | chars |
|---|---|---|---|---|---|
| `gwen-loop-presence-continuity:d1` | positive | True | True | True | 506 |
| `gwen-loop-presence-continuity:d2` | positive | True | True | True | 702 |
| `gwen-loop-presence-continuity:d3` | positive | True | True | True | 921 |
| `gwen-loop-presence-continuity:d4` | positive | True | True | True | 1227 |
| `gwen-loop-presence-continuity:d5` | positive | True | True | True | 1017 |
| `function-first-loops-muse-redesign:d1` | positive | True | True | True | 1228 |
| `gwen-loop-presence-continuity:t1` | negative | False | False | False | 147 |
| `function-first-loops-muse-redesign:t1` | negative | False | False | False | 178 |
| `gwen-loop-presence-continuity:t3` | negative | False | False | False | 123 |
| `function-first-loops-muse-redesign:t2` | negative | False | False | False | 145 |
| `gwen-loop-presence-continuity:t5` | negative | False | False | False | 123 |
| `function-first-loops-muse-redesign:t3` | negative | False | **True** | **True** | 221 |

### The concordance table

Published as data, **not read as a finding** — the pre-registration states
directly that 12 judgments from a single pass cannot support a threshold
verdict, and no `SUPPORTS` / `NEGATIVE` / `INCONCLUSIVE` label is assigned to
this leg.

| mind | hits /6 | misses | false alarms | correct rejections | accuracy |
|---|---|---|---|---|---|
| cortex | 6 | 0 | 1 | 5 | 0.917 |
| muse | 6 | 0 | 1 | 5 | 0.917 |

- cortex: 12 of 12 scored, 0 degraded, mean latency **53.1s**
- muse: 12 of 12 scored, 0 degraded, mean latency **5.4s**
- transport/tooling errors: **0 of 24**

### The two minds returned identical verdicts on all 12 cases

Not merely equal scores — the **same** answer on every case, including the
same single false alarm. The variable of interest that Tier 2b names for this
leg ("the mind that authored a plan is invested in it; a separate mind is
not") produced **zero observable difference here**.

That is a null observation, not a finding, and the design cannot carry it
further (see the limits below). But it is the third time in this series that
an ability framed as muse-shaped has failed to distinguish from the cortex:

| run | claim | what the control showed |
|---|---|---|
| [muse-challenge.md](muse-challenge.md) | the muse challenges rather than restates, 9/9 | no cortex arm was run |
| [association-work.md](association-work.md) | challenging is muse-shaped | the cortex challenged 12/12 too |
| this run | an uninvested mind judges deviations differently | identical on 12/12 |

### The cost asymmetry is the sharpest thing in this data

Same twelve answers, very different bills:

| mind | completion tokens (12 cases) | total latency | mean per case |
|---|---|---|---|
| cortex | 13,852 | 637.7s | 53.1s |
| muse | 557 | 64.8s | 5.4s |

The cortex spent **~25× the completion tokens and ~10× the wall-clock** to
reach the same twelve verdicts. On a leg where the two minds are
indistinguishable in quality, that ratio is the whole argument for routing it
to the muse — and it is an argument about cost, which this run *can* measure,
not about judgment, which at n=12 it cannot.

### The one shared false alarm is defensible

Both minds flagged `function-first-loops-muse-redesign:t3`. Its delivery row
reads: *"Kind-aware delivery: durable counsel survives loop distance, per-kind
counters, per-class drop codes. Merge `3f12d3d`. **Two of its codes have no
emitter** — see Drift"*.

- cortex: "identifies a specific defect (missing emitters for two codes) that
  diverges from the expected plan and should be formally tracked as a
  deviation rather than folded into drift"
- muse: "The delivery is incomplete as two codes lack emitters, representing a
  divergence from the expected functional delivery of the item"

Ground truth says `False` because no deviation record names `t3` — and that is
correct under devague's method: a shipped defect is drift, filed as
[embodiment#18](https://github.com/agentculture/embodiment/issues/18), not a
divergence from the confirmed plan. Both minds drew the deviation/drift line
in the same place and in the same wrong spot. It is the hardest case in the
pool and the only one either mind missed.

### Post-hoc, not pre-registered — classification agreement

Noticed mid-run and computed from the committed transcript with no extra dial.
Labelled post-hoc wherever it appears; the pre-registration asks only for
hits/misses/false alarms.

Against the human-recorded `classification` on the six positive cases: cortex
**5/6**, muse **5/6** — and they miss *different* cases only once, both on
`gwen-loop-presence-continuity:d4` (ledger `needs-follow-up`; cortex said
`risky`, muse said `acceptable`). Five of six exact matches to a
human-authored risk label is the most surprising number in the run, and at
n=6 it is an observation, not a claim.

## What this run does not show

- **Nothing about Experiment 1.** See the top of this document.
- **No verdict on the `/deviate` leg's concordance.** Pre-registered as
  exploratory; 12 judgments from one pass cannot support a threshold, and the
  identical-verdicts observation above is subject to the same limit.
- **The pools are separable by input length alone**, and this is the single
  biggest threat to reading anything into the concordance numbers:

  | pool | min chars | max chars | mean |
  |---|---|---|---|
  | positive | 506 | 1228 | 934 |
  | negative | 123 | 221 | 156 |

  Every positive case is longer than every negative case. A mind that answered
  "long ⇒ deviation" scores 11/12 on this pool without reading a word. The
  pre-registration fixed both inputs before the first dial and this harness did
  not change them — measuring the confound is honest, repairing it after seeing
  the data would not be. But it means 11/12 is **not** evidence that either
  mind can tell a deviation from a delivery; it is consistent with that and
  equally consistent with a length heuristic, and this design cannot separate
  the two.
- **The negative pool may be too easy in a second way.** A one-line "Status:
  delivered" row does not describe a divergence in any reading, so five of the
  six negatives ask an easy question. Only `t3` — the shared false alarm —
  poses a genuine deviation-versus-drift judgment.
- **Generalisation past devague 0.22.0**, per the pre-registration's own
  boundary.
- **Anything about model quality in general.** One rig, one cortex/muse pair,
  one pass.

## Deviations from the pre-registered protocol

Recorded here so they are visible in a diff rather than inferred from what is
missing:

1. **Experiment 1 not run at all** (reasons above). Pre-registered as the
   larger of the two experiments; contributes nothing to this document.
2. **The `devague deviate --origin llm` capture step was not executed**;
   judgments were captured to a committed JSONL artifact carrying the same
   fields, and the gate's contract is evidenced statically instead.
3. **`DV-REFUSE`, the grader self-check**, was not run — it requires building a
   synthetic plan with a deliberate dependency cycle, which is a
   state-mutating `plan` sequence. It is a component of Experiment 1's
   instrument trust; with Experiment 1 absent, no plan's convergence is being
   trusted on the strength of an unverified refusal.
4. **The redaction is a superset of the three named fields** (see above).

## Reproducing

```bash
export COLLEAGUE_API_KEY=...

# Experiment 2 — the /deviate slice, 12 cases x 2 minds
uv run python examples/devague_legs.py \
    --out docs/live-test-results/devague-legs-deviate.jsonl

# build the case pool and print the length confound, dialling nothing
uv run python examples/devague_legs.py --dry-run

# re-render every table in this document from the committed transcript
uv run python examples/devague_legs.py --analyse \
    --out docs/live-test-results/devague-legs-deviate.jsonl
```

Every table above is `--analyse` output, pasted. Three of this repo's
committed corrections were errors made *between* a run and its write-up; a
number in this document is a number the transcript contains.

Experiment 1 has no reproducing recipe here, because it was not run and a
recipe would imply otherwise. The pre-registration's own "Reproducing" section
carries the intended move sequence — and note that executing it requires a
human at the `--confirm` step of all four pipelines.
