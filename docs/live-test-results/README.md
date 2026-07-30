# Live test results

What happened when embodiment was run against real models rather than fakes.

The test suite (1567 tests) proves embodiment cannot lie, hang, or degrade
silently. It cannot tell you whether the result is any good. These are the runs
that address the second question, recorded as deviations
[`d4`](#deviations) (live testing as an acceptance bar) and `d5` (live
self-testing).

Every number here came from an actual run on the date given. Where a run
contradicted something we believed, the contradiction is recorded rather than
the belief.

## The rig

| Role | Model | Where |
|------|-------|-------|
| cortex | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | local, served on the gateway |
| muse | `nvidia/Gemma-4-31B-IT-NVFP4` | proxied through the same gateway from a peer |

One OpenAI-compatible gateway at `localhost:8001` fronts every role, so
embodiment addresses roles **by name** and never parses a model id. Auth is a
bearer key from `COLLEAGUE_API_KEY`.

Measured on a trivial prompt ("reply with exactly: X"), 2026-07-25:

| | latency | completion tokens |
|---|---|---|
| cortex | 9.3s | 209 |
| muse | 2.6s | 50 |

The cortex is a **thinking model**: it emits a long `reasoning` field before
`content`, which is why a three-word answer costs 209 tokens. The muse answers
directly with `reasoning: null`. So the muse is ~3.5× faster and ~30× cheaper
per answer — the opposite of the assumption the staleness design was built on
(see [proof.md](proof.md)).

**A trap worth knowing:** at `max_tokens=64` the cortex returned
`finish_reason: length` with `content: None`, still mid-thought. A caller can
easily misread that as an empty turn rather than a truncated one. Budget
generously.

## The experiments

| Document | What it tested | Outcome |
|---|---|---|
| [continuity.md](continuity.md) | does run 2 recall run 1 across separate processes | held |
| [muse-and-echo-chamber.md](muse-and-echo-chamber.md) | does an advisory mind help, and can it mislead | helped; did not mislead |
| [self-test.md](self-test.md) | two instances conversing; recognising own memories | converged; recognised |
| [proof.md](proof.md) | a long multi-phase task, with and without the muse | correct; muse more careful, not more correct |
| [designed-problem.md](designed-problem.md) | the embodiment designs a problem; a fresh instance solves it, n=4 per arm | **no measured muse effect** (1/4 both arms) |
| [configurations.md](configurations.md) | every run's full settings, including confounded ones | temperature was a hidden variable throughout |
| [scratchpad.md](scratchpad.md) | does forcing a tool call per step repair the `exit=stopped` collapse | yes on protocol failures (25%→67%), no on capacity ones |
| [muse-challenge.md](muse-challenge.md) | does the muse challenge a cortex result or restate it, n=9 per arm | challenged 9/9 asked **and** 9/9 unasked; zero restatements in 54 runs |
| [association-work.md](association-work.md) | is the muse/cortex role split real — a pre-registered 2×2, n=12 and n=9 per cell | **`INCONCLUSIVE`, interaction 0.00 — the function map is NOT promoted.** The cortex challenges 12/12, same as the muse |
| [delivery-per-kind.md](delivery-per-kind.md) | did kind-aware delivery (t3) move the 2-of-7 discard rate, n=4 runs | delivery 28.6% → 62.5%, but **not attributable to t3**: only 2 of 12 insights were `durable`, and the dominant loss is a close-time race |
| [memory-echo-chamber.md](memory-echo-chamber.md) | can a record written into the memory store drive the loop — hostile arm vs control, n=6 each | **DEFERRED 6/6 with the record, RESISTED 6/6 without it.** One stored record flips the action in both directions |
| [arena-series.md](arena-series.md) | league-of-agents as a 2×2 (resident/command × muse on/off) plus a continuity pair and its control, n=3 per cell, 24 matches | residency **CONFIRMED 24/24**; muse **`INCONCLUSIVE`** at a 9/9 ceiling; continuity crossed **8/9 vs 0/9** against its control — but **three of the five predictions were graded by defective instruments**, and the pre-registered P4 verdict is a FAIL the data contradicts |
| [arena-series-preregistration.md](arena-series-preregistration.md) | the 2×2, the decision rule, and the five rules fixed in advance | committed **before** the first measured match |
| [corrections.md](corrections.md) | every belief this fan-out held that the work contradicted | deliberately unflattering; the recurring pattern is that the mechanism was right and the verification was the defect |
| [association-work-preregistration.md](association-work-preregistration.md) | the configuration and numeric decision rule for both of the above | committed **before** the first dial; the ordering is the point |
| [devague-legs.md](devague-legs.md) | can either mind judge whether a record warrants a `devague deviate` — 12 blind cases, both minds, pre-registered | **Experiment 1 ABSENT (did not run).** Experiment 2: the two minds returned **identical verdicts on all 12 cases**, 11/12 against ground truth each — but the pools are separable by input length alone. The muse reached the same answers for ~1/25 the completion tokens |
| [devague-legs-preregistration.md](devague-legs-preregistration.md) | the leg split, both experiments' decision rules, the lapse protocol, and the Gemma-proposes-human-confirms gate | committed **before** the first dial |

## Reproducing

```bash
export COLLEAGUE_API_KEY=...

# continuity across two processes
uv run python examples/greenhouse.py --home /tmp/gh --reset --live "<plant card>"
uv run python examples/greenhouse.py --home /tmp/gh --live --moisture 22 "Does Marlow need water today?"

# the same, with the advisory lane and Gwen framing
uv run python examples/greenhouse.py --home /tmp/gh2 --reset --live --muse --identity Gwen "<plant card>"

# self-test: conversation, then self-recognition
uv run python examples/selftest.py

# does the muse challenge a cortex result, or restate it (both arms)
uv run python examples/muse_challenge.py --live --framing task --n 3 --json
uv run python examples/muse_challenge.py --live --framing bare --n 3 --json

# can a stored record drive the loop — hostile arm, then the control
uv run python examples/echo_probe.py --store /tmp/echo/live --direction both --live
uv run python examples/echo_probe.py --store /tmp/echo/ctl --direction both --live --control

# is the role split real — the pre-registered 2x2, then the same rule re-applied
uv run python examples/association_work.py --live --n-reflective 4 --n-executive 3 \
    --out docs/live-test-results/association-work.jsonl
uv run python examples/association_work.py --analyse \
    --out docs/live-test-results/association-work.jsonl

# per-kind delivery, against the 2-of-7 baseline
uv run python examples/delivery_series.py --n 4 \
    --out docs/live-test-results/delivery-per-kind.jsonl

# the /deviate leg across both minds — 12 blind cases, pre-registered
uv run python examples/devague_legs.py \
    --out docs/live-test-results/devague-legs-deviate.jsonl
uv run python examples/devague_legs.py --analyse \
    --out docs/live-test-results/devague-legs-deviate.jsonl

# long-running proof, with and without the muse
uv run python examples/proof.py --json
uv run python examples/proof.py --muse --identity Gwen --json
uv run python examples/proof.py --problem euler --max-steps 20 --json
```

The hermetic suite never touches any of this: live paths are opt-in and skip
cleanly when the rig or the key is absent.

## Corrections

Two claims made during these runs were wrong and are corrected in place rather
than quietly dropped. Both are recorded because a results document that only
contains successes is not evidence of anything.

1. **"The proof run walked right up to its step budget."** It did not.
   `TaskResult.steps` counts *tool calls* — one `Step` per call — while
   `max_steps` bounds *model turns*. A run showing `steps: 14` against
   `max_steps: 14` had used **6 turns of 14**. See [proof.md](proof.md).
2. **"The muse rescued a failure the solo arm could not complete."** It did
   not. With four runs per arm both scored 1/4 — the original pair was noise.
   Claimed once from n=1, then again from two of three replicates *before the
   third reported*; the third falsified it. See
   [designed-problem.md](designed-problem.md).
3. **"The muse arm self-corrected — the first in the series."** It did not.
   The two entries carry *identical values*; it was a re-verification filed
   under the `revise` tool, and the claim was made from the label without
   reading the text. It also does not reproduce — zero revisions across six
   runs. See [scratchpad.md](scratchpad.md).
4. **"The instance failed to recognise its own memories."** It did not fail;
   the scoring did. The first self-test graded against records that were
   *seeded* rather than records that were *recalled*, so a retriever that
   surfaced 3 of 4 was counted against the mind. See [self-test.md](self-test.md).
5. **"Challenging a conclusion is what the muse is for."** Not established.
   [muse-challenge.md](muse-challenge.md) measured the muse at 9/9 and 9/9 but
   never ran the *cortex* through the same loop. When
   [association-work.md](association-work.md) did, it also scored 12/12. The
   original numbers stand; the inference drawn beside them — that this is a
   muse-shaped ability — does not.
6. **"Neither model ever submitted a wrong answer on the executive problems."**
   Written from a classifier's output, and false. `failure_modes()` charges any
   non-`finished` exit as a *protocol* failure, so it reported `0` reasoning
   failures — while **5 of the muse's 6 failures end on a definite, wrong final
   answer** that simply never went through the `finish` tool. Caught by reading
   the transcripts the series had committed. The counter is left as-is and
   documented as defective rather than retuned after the fact. See
   [association-work.md](association-work.md).
7. **"The three constrained-problem harnesses are committed and verified."**
   Their *graders* were. The harnesses themselves had never executed: each
   built `Task(system=…, tools=…)` and called `run(task=…, bench=…)`, and the
   contract has none of those names, so every invocation raised `TypeError`
   before its first model call. Found by trying to run one. See
   [association-work.md](association-work.md).

## Deviations

- **`d4`** — live rig testing becomes an acceptance bar beyond the in-repo demo
  and the CI checklist. Recorded `needs-follow-up`; the bar is now met.
- **`d5`** — live self-testing: two instances converse, and an instance sorts
  its own memories from another agent's.

Read them with `devague deviate --list`.
