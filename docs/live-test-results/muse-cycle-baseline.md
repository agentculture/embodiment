# Muse-cycle baseline — the numbers frozen before any fix lands

**Date:** 2026-07-30 · **Plan:**
[the muse-cycle plan](../plans/2026-07-29-muse-cycle-pad-headspace-devague-legs.md),
task t1 · **Purpose:** freeze the three numbers task t2 (dead-code wiring) and
task t6 (the devague-legs pre-registration) are built against, so a later
comparison cannot quietly move the goalposts.

This document makes **no new measurement**. Every number below is copied,
verbatim, from an existing committed measurement doc. Where a source states a
caveat, that caveat is carried forward here rather than dropped for brevity.
If a number is not in the sources, this document says so rather than
estimating one.

## 1. The late-drop rate — one `muse-insight-late` drop per run, 4 of 4

**Value:** in every one of 4 runs, exactly one insight was dropped with code
`muse-insight-late`, always at or near the run's final step, always because it
was "undrained when the runner closed". Across the series that is **4 late
drops out of 16 insights produced — 25% of all counsel produced in this
series**.

**n:** 4 runs (`examples/delivery_series.py --n 4`), same harness, same
problem (`factorial`), same `--identity Gwen`, `DEFAULT_STALE_LAG = 5`, loop
budget `max_steps = 14`. 16 insights produced in total (4 per run).

**The raw ledger**, quoted in full because the "one per run" claim depends on
seeing all four runs side by side:

```text
run 0   muse-insight-late   step=13   undrained when the runner closed
run 1   muse-insight-stale  step=0    insight about step 0 read at step 6 (lag 6 > 5)
run 1   muse-insight-stale  step=6    insight about step 6 read at step 12 (lag 6 > 5)
run 1   muse-insight-late   step=15   undrained when the runner closed
run 2   muse-insight-late   step=13   undrained when the runner closed
run 3   muse-insight-late   step=13   undrained when the runner closed
```

**Caveats, carried forward from the source:**

- This is reported as **deterministic, not stochastic**: "the muse thinks
  about the final boundary, the actor finishes, the runner closes, and the
  insight has no remaining drain to be collected at." It is a close-time race,
  not the staleness threshold (`DEFAULT_STALE_LAG`) that `proof.md` proposed
  re-deriving — "anyone tuning `DEFAULT_STALE_LAG` from these numbers would be
  tuning the smaller of the two losses."
- The late path **does not carry a counsel kind** — `ThreadedMuseRunner`
  updates its per-kind counters only inside `drain()`, so late (and overflow)
  drops are unattributable to `step` versus `durable` by construction. This
  was declared before the run, not discovered after it.
- **n = 4 runs, one problem, one rig, one model pair.** All four runs had
  identical loop lengths (4 model turns), which is "good for internal
  consistency and bad for coverage: the workload was never varied."
- **Whether a longer workload reproduces this rate is explicitly unmeasured.**
  The original 2-of-7 (28.6%) baseline in `proof.md` came from a 6-turn,
  24-tool-call run; none of these four runs exercised a loop that long, so
  this series cannot say whether the late-drop rate holds under a longer
  workload.
- This is reported as a transition the host's ledger records, not a silent
  loss: "`close()` records every stranded insight rather than dropping it
  silently, which is exactly what C3 asks."
- **Not attributable to task t3** (kind-aware delivery): the 28.6% → 62.5%
  overall delivery movement in the same series is recorded as *observed* and
  explicitly **not credited** to t3's `durable`-sparing rule, because only 2 of
  16 insights that run were labelled `durable`, both in runs that recorded
  zero stale drops of any kind (so nothing in them was near the staleness
  threshold to begin with), and per-insight lag is not in the record — so
  whether either `durable` insight would have aged out cannot be determined
  from this artifact. A sufficient alternative explanation is present and
  measured instead: these runs were shorter (4 of 14 turns) than the single-run
  baseline (6 of 14 turns).

**Citation:**
[`docs/live-test-results/delivery-per-kind.md`](delivery-per-kind.md), section
"The dominant loss is no longer staleness. It is a close-time race" (lines
103–130, ledger at 108–115), cross-referenced with "The number moved. It
cannot be credited to kind-aware delivery" (lines 63–101), "What could not be
measured" (lines 175–189) and "Limitations" (lines 191–197).

## 2. The confidently-wrong rate — 5 of the muse's 6 failures state a wrong answer

**Value:** on the executive axis of the association-work 2×2, the muse
(`nvidia/Gemma-4-31B-IT-NVFP4`) failed 6 of its 9 runs (rate 0.333 passed). Of
those **6 failures, 5 state a definite final answer, and every one of the five
is wrong**. The sixth ran out of budget mid-verification, with no final answer
stated.

**n:** 9 runs on the executive axis for the muse cell (3 problems ×
3 repeats — `subset`, `register`, `entropic` variant 3b), of which 6 were
failures; 5 of those 6 failures are the ones scored here.

**The five wrong answers, quoted in full:**

| problem | muse's stated answer | truth |
|---|---|---|
| `register` | `initial=1011 order=BCAED` | `initial=0101 order=CBEAD` |
| `register` | `initial=1000 order=BCAED` | `initial=0101 order=CBEAD` |
| `entropic3b` | `Initial value 143, Order E-B-D-C-A` | `00001111`, `C-A-E-D-B` |
| `entropic3b` | `Initial value 191, Order B, E, D, C, A` | `00001111`, `C-A-E-D-B` |
| `entropic3b` | `Initial Value: 167, Order: B, E, D, C, A` | `00001111`, `C-A-E-D-B` |

(A sixth `register` run is truncated mid-verification with no final answer —
that is the one non-"confidently wrong" failure of the six.)

**Caveats, carried forward from the source — this number is itself a
correction of a defective classifier:**

- The document's own `failure_modes()` classifier **originally reported "0
  reasoning failures"** for both cells (3 passed / 6 protocol / 0 reasoning on
  each executive cell). That reading was **published, then falsified** by
  reading the six muse transcripts directly: "`failure_modes()` therefore
  under-counts reasoning failures, and its `0` is a property of the rule
  rather than a finding about the models." The classifier's predicate charges
  any non-`finished` exit as *protocol*, so a budget exit that ends on a
  stated, wrong answer is still bucketed as protocol, not reasoning.
- **The corrected reading, stated by the source in these terms:** "muse — 5 of
  6 are *reasoning* failures that also missed the submission protocol: it did
  the work, reached a definite answer, got it wrong, and never called
  `finish`. The sixth ran out of budget mid-verification." By contrast the
  cortex's 6 of 6 executive failures are "pure collapses: no finish, no
  synthesis, no substantive prose at all" — a different failure profile at
  the same aggregate rate (0.333 for both), which the source explicitly warns
  must not be read as "equally good at executive reasoning."
- The classifier itself **was deliberately left uncorrected** ("the counter is
  left as-is and documented as defective rather than retuned after the fact")
  — the 5-of-6 figure lives only in the prose correction, not in
  `failure_modes()`'s own output.
- A related false positive is recorded beside this number: a substring check
  for the correct `register` answer inside the muse's exhaustive-search prose
  would have produced a false "found it" — "a substring check for the truth
  inside an exhaustive search is worthless, because an exhaustive search
  prints the truth by construction." The 5-of-6 figure was reached by reading
  the transcripts' *stated final answers*, not by any substring match.
- **n = 9 per cell, one rig, one model pair, three executive problems**, all
  authored by the same hand as the graders. The executive axis was "mostly at
  a wall per problem" (`subset` a ceiling 3/3 for both minds, `register` and
  `entropic3b` a floor 0/3 for both), so the 0.333 aggregate has "resolution
  that no individual problem had."
- Whether the loop's literal-markup `finish` recovery fired on these runs is
  explicitly listed as unmeasured (`finish_recovered` was not captured).

**Citation:**
[`docs/live-test-results/association-work.md`](association-work.md), section
"Correction: the classifier said '0 reasoning failures'. It was wrong" (lines
112–144, the five-answer table at 119–126), cross-referenced with "The
failure modes are not the same, even though the rates are" (lines 88–110),
"The numbers" (lines 33–42, executive · muse cell) and "Limitations" (lines
233–246). Also recorded in
[`docs/live-test-results/README.md`](README.md), Corrections item 6
(lines 136–143).

## 3. The dead-code count — 2 declared runner codes with no emit site

**Value:** `embodiment/muse_runner.py` declares, exports and lists **9** codes
in `RUNNER_CODES`. Of those, **2** — `DROPPED_COMPILATION_STARVED`
(`"muse-compilation-starved"`) and `DROPPED_COUNSEL_DISPLACED`
(`"muse-counsel-displaced"`) — are never passed to `_record` or `_degrade`
anywhere in the `embodiment` package. They are declared and reachable as
constants; nothing in the shipped code ever emits them.

**Confirmed directly against the source in this worktree:**

- `embodiment/muse_runner.py:186` — `DROPPED_COMPILATION_STARVED =
  "muse-compilation-starved"`
- `embodiment/muse_runner.py:188` — `DROPPED_COUNSEL_DISPLACED =
  "muse-counsel-displaced"`
- `embodiment/muse_runner.py:193-202` — both constants are members of the
  `RUNNER_CODES` tuple (9 codes total: `DEGRADED_THREAD`, `DEGRADED_WORKER`,
  `DEGRADED_ENDPOINT`, `DROPPED_STALE`, `DROPPED_LATE`, `DROPPED_OVERFLOW`,
  `DROPPED_BOUNDARY`, `DROPPED_COMPILATION_STARVED`,
  `DROPPED_COUNSEL_DISPLACED`).
- A repo-wide search for both constants outside their own declaration,
  `__all__` listing and the `RUNNER_CODES` tuple in `muse_runner.py` finds
  **no other production module** referencing them — only test modules
  (`tests/test_ledger.py`, `tests/test_proof_reporting.py`,
  `tests/test_muse_runner.py`), which assert the constants' membership in
  `RUNNER_CODES` and, in `test_ledger.py`, provoke the two ledger-fold
  predicates by calling `_record` directly from the test rather than through
  any real runner code path.

**Caveats, carried forward from the source:**

- This is reported as *absence*, not as a measured `0`: "reporting them as a
  total is the honest shape; assigning them a kind would be invention" (for
  the sibling late/overflow attribution question), and specifically for these
  two codes — "this is reported as absent rather than as `0`, because a zero
  would read as 'this run did not trip it' when the truth is that nothing
  can."
- A structural test already pins this so it cannot go stale silently:
  `tests/test_proof_reporting.py::TestUnproducedCodes` "walks every module and
  fails if a producer ever appears" for either code — so wiring one (task t2's
  job) is expected to force this document's number down to 1, then 0, rather
  than drift unnoticed.
- The dead-code finding is about **emission**, not about test coverage or
  intent: both codes have descriptive docstrings in `muse_runner.py`
  (`DROPPED_COMPILATION_STARVED`: "Background compilation was starved because
  boundary counsel took priority"; `DROPPED_COUNSEL_DISPLACED`: "Boundary
  counsel was displaced by background compilation filling the buffer") — the
  vocabulary is designed, just not yet wired to a real code path.

**Citation:** `embodiment/muse_runner.py` (lines 137–139 `__all__`, 186–188
constant definitions, 193–202 `RUNNER_CODES` and its comment), cross-checked
against
[`docs/live-test-results/delivery-per-kind.md`](delivery-per-kind.md), section
"The two t3 codes" (lines 48–61), and
`tests/test_proof_reporting.py::TestUnproducedCodes` (class at line 63,
docstring at lines 64–70, the parametrized non-emission assertion around
line 79 (`test_no_module_in_the_package_records_it`)).

## Context, not a fourth baseline number: the muse/cortex latency relationship

`docs/live-test-results/proof.md` and `docs/live-test-results/README.md` both
measure the muse as *faster* than the cortex, not slower — the opposite of the
assumption `DEFAULT_STALE_LAG`'s design started from. `README.md`'s rig table
(measured on a trivial prompt, 2026-07-25) records cortex latency 9.3s versus
muse latency 2.6s — "the muse is ~3.5x faster and ~30x cheaper per answer."
`proof.md`'s task-1 run states the same ratio in context: "This inverts the
reasoning behind deviation `d1`'s staleness design. That assumed a big
background muse would *lag* the actor and land insights late. The muse is in
fact ~3.5× faster. But the actor batches ~2.7 tool calls per model turn, so the
step counter races ahead of insights tagged to the step they reasoned about."
This is carried here only as background for reading baseline number 1 above —
it is not itself one of the three numbers this document is required to freeze,
and `association-work.md` separately caveats that its own latency numbers were
taken "under contention" (a concurrent drive sharing the local GPU) and "must
not be read as model throughput." **Citation:**
[`docs/live-test-results/proof.md`](proof.md) (lines 33–46) and
[`docs/live-test-results/README.md`](README.md) (lines 26–37).

## What this document is not

- Not a new measurement. No rig was dialled to produce this document; every
  figure is a citation of an existing committed artifact.
- Not a re-analysis. Where a source document itself corrected an earlier
  reading (the association-work classifier correction, in particular), this
  document reports the corrected reading and cites the correction, rather than
  re-deriving anything.
- Not exhaustive over every number in the three source documents. It records
  only the three the plan's task t1 acceptance criterion names, plus the
  latency context immediately above that a later reader needs to interpret
  baseline number 1.

## Reproducing (the sources' own commands, not new ones)

```bash
export COLLEAGUE_API_KEY=...

# late-drop series (baseline number 1)
uv run python examples/delivery_series.py --n 4 \
    --out docs/live-test-results/delivery-per-kind.jsonl

# association-work 2x2, executive axis (baseline number 2)
uv run python examples/association_work.py --live \
    --n-reflective 4 --n-executive 3 \
    --out docs/live-test-results/association-work.jsonl

# the dead-code pin (baseline number 3), no rig required
uv run pytest tests/test_proof_reporting.py::TestUnproducedCodes -q
```
