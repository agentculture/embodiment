# Live test results

What happened when embodiment was run against real models rather than fakes.

The test suite (2806 tests) proves embodiment cannot lie, hang, or degrade
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
| [muse-latency.md](muse-latency.md) | is the c29 zero-late-drop target reachable once the muse holds tools — session latency (n=8 per arm, 4 arms) against the drive tail (n=4) | **`KEEP`**: worst tool-session 22.1s against a shortest tail of 41.7s, and **0 late drops in 4 of 4 drives** against the baseline's 1-per-run. `c31`'s ~6x tool penalty did not reproduce (1.2x shipped, 2.2x primed). But the tools-on-in-drive lane is **absent** — `ThreadedMuseRunner` had no `tools=` seam (grown since, by `t26`/#30; the measurement is still absent) — and 5 of 13 counsel lines still never reach the cortex |
| [muse-latency-preregistration.md](muse-latency-preregistration.md) | the four arms, the estimator, the derived `KEEP_THRESHOLD` and the `INCONCLUSIVE` condition | committed **before** the first dial; `KEEP_THRESHOLD` is recomputed from its inputs in the pin test, not asserted as a literal |
| [muse-arms-preregistration.md](muse-arms-preregistration.md) | the three muse arms (tools-off / +pad / +pad+workspace), the R-C1 execution rule, the counsel scorer and the decision rule | committed **with the harness** (`examples/muse_arms.py`) and **before** the first measured dial; the series itself is task t18's |
| [muse-arms.md](muse-arms.md) | does working memory, then somewhere to execute, cut the muse's confidently-wrong rate — n=8 per arm, 24 runs, real containers | **`INCONCLUSIVE`**: arm A is **8/8 correct, 0 confidently wrong**, so the control sits on a ceiling with nothing to move. **1 confidently-wrong answer in 24 runs.** 17 of arm C's 23 workspace calls were refused for a string argv; after the mandated hand-audit **every one of the 5 real executions was a whole-problem solver, none offloaded arithmetic**. Nine runs report `NO_ANSWER` because the muse emitted a **tool call on the tools-off closing turn** and the gateway dropped it |
| [workspace-echo-chamber.md](workspace-echo-chamber.md) | does a wrong number wearing *measured-result* authority drive the loop — a genuine workspace execution against a remembered-fact arm and a control, n=3 each | **`INCONCLUSIVE`, 12/12 RESISTED — the pad-recall boundary stays.** Nothing deferred, including a post-hoc arm carrying the exact costume that won 6/6, so the probe has **no measured sensitivity** to the effect it re-tests. It also measured that `exit=stopped` on this problem was partly the token cap |
| [league-h2h.md](league-h2h.md) | full-Gemma vs mixed vs full-Qwen, round-robin head-to-head, both teams driven by model seats — n=4 matches per arm | **`SEPARATED` at L1**, the easiest rung, 3/3 pairings and no cycle: **full-qwen > mixed > full-gemma**. But three of the four cooperation signals sit on a ceiling and **the whole ranking is one optional field** — the Gemma cortex sent a team message on **0 of 12** seat-turns against full-Qwen's 9. Cost: **950 vs 12,819 vs 18,410** completion tokens per match, **zero truncation in 196 model calls**. L2-L4 **`ABSENT`** (the climb stops at the first separating rung) |
| [league-h2h-preregistration.md](league-h2h-preregistration.md) | full-Gemma vs mixed vs full-Qwen, round-robin head-to-head up an escalating arena ladder: the three arms, the four rungs, the fairness rules and the decision rule | committed **before the first dial**, with the harness (`examples/league_h2h.py`) and its pin in the same change. Carries two **pre-dial amendments**: the token budget 3000 -> 16000 (an identical cap that truncates one arm measures truncation, not skill) and the wall-clock caps sized to match |
| [league-h2h-scripted-control.jsonl](league-h2h-scripted-control.jsonl) | the same ladder with the SAME hermetic mind in all three arms — map and seed bias with the models removed, 24 matches, offline | identical minds **never separate** (4/4 rungs `INCONCLUSIVE`), the colour bias is real and under fog is **90 vs 45** between byte-identical minds, and the paired `net_margin` cancels it **exactly** (0.0 everywhere) |
| [live-suite-and-challenges.md](live-suite-and-challenges.md) | the live-evidence gate (`d1`): every `EMBODIMENT_LIVE_RIG`-gated test plus the challenge harnesses, run rather than assumed | **13 of 13 live-gated tests PASSED.** `challenge_subset` **3/3 CORRECT**; `challenge_register` graded WRONG but **INCONCLUSIVE** — two of four turns hit `finish_reason: length` at a full **16000/16000**. The terminal drain (t4/t25) fires on a clean finish and delivers, and its counsel **provably cannot reach the cortex** there; t26's muse tool bench is **unwired in every checked-in host** (`tool_rounds: 0`). Cross-cutting: the cortex token budget is a hidden variable at **700 / 2048 / 6000** across five harnesses, and `ModelResponse` carries no `finish_reason` to make truncation visible |

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

# the same question with a *computed* wrong number: three pre-registered arms,
# then the post-hoc sensitivity arm (never in --arm all; it must be named)
uv run python examples/echo_probe_workspace.py --store /tmp/wecho/live \
    --arm all --n 3 --live \
    --out docs/live-test-results/workspace-echo-chamber.jsonl \
    --config-out docs/live-test-results/workspace-echo-chamber-config.json
uv run python examples/echo_probe_workspace.py --store /tmp/wecho/instr \
    --arm instruction --n 3 --live \
    --out docs/live-test-results/workspace-echo-chamber-instruction.jsonl

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

# tool-session latency against the drive tail, then the pre-registered fold
uv run python examples/muse_latency.py --lane sessions \
    --out docs/live-test-results/muse-latency-sessions.jsonl
uv run python examples/muse_latency.py --lane drives \
    --out docs/live-test-results/muse-latency-drives.jsonl
uv run python examples/muse_latency.py --analyse \
    --sessions docs/live-test-results/muse-latency-sessions.jsonl \
    --drives docs/live-test-results/muse-latency-drives.jsonl

# the three muse arms on the verified oracle — tools-off / +pad / +pad+workspace
uv run python examples/muse_arms.py --dry-run
uv run python examples/muse_arms.py --n 8 --provider docker \
    --out docs/live-test-results/muse-arms.jsonl
uv run python examples/muse_arms.py --analyse \
    --out docs/live-test-results/muse-arms.jsonl

# three model arms, round-robin, up the escalating league ladder (task t27).
# The ladder with NO --live is the identical-mind control: same scripted mind
# in all three arms, so every number it returns is map bias, not model.
uv run python examples/league_h2h.py plan
uv run python examples/league_h2h.py ladder --home /tmp/h2h-ctl \
    --log /tmp/h2h-ctl/scripted-control.jsonl
uv run python examples/league_h2h.py ladder --live --rungs L1 --home /tmp/h2h \
    --log docs/live-test-results/league-h2h.jsonl

# long-running proof, with and without the muse
uv run python examples/proof.py --json
uv run python examples/proof.py --muse --identity Gwen --json
uv run python examples/proof.py --problem euler --max-steps 20 --json

# the live-evidence gate: every rig-gated test, then the challenge harnesses.
# --max-tokens 16000 is NOT optional on this rig — the harness default of 6000
# truncates roughly one turn in three on the subset problem, and a truncated
# turn is indistinguishable from a refusal without --trace-out.
EMBODIMENT_LIVE_RIG=1 uv run pytest -p no:randomly -v \
    tests/test_workspace.py tests/test_muse_challenge.py \
    tests/test_association_work.py tests/test_demo_greenhouse.py \
    tests/test_echo_probe.py tests/test_echo_probe_workspace.py

uv run python examples/challenge_subset.py --n 3 --json --max-tokens 16000 \
    --results docs/live-test-results/challenge-subset-config.json \
    --trace-out docs/live-test-results/challenge-subset-trace.json
uv run python examples/challenge_register.py --n 1 --json --max-tokens 16000 \
    --results docs/live-test-results/challenge-register-config.json \
    --trace-out docs/live-test-results/challenge-register-trace.json
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
