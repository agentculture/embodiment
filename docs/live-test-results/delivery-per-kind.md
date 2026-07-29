# Delivery, re-measured per counsel kind

**Date:** 2026-07-29 · **Rig:** see [README](README.md) · **Harness:**
`examples/proof.py` via `examples/delivery_series.py` · **Raw records:**
[`delivery-per-kind.jsonl`](delivery-per-kind.jsonl) (4 runs) ·
**Pre-registration:**
[`association-work-preregistration.md`](association-work-preregistration.md),
committed before the first dial

## The baseline being re-measured

[`proof.md`](proof.md), one run, 2026-07-25:

```text
insights_delivered 2 · dropped_stale 2 · dropped_late 3 · dropped_overflow 0
```

**7 produced, 2 delivered — 28.6%.** Measured *before* t2 (counsel kinds) and
t3 (kind-aware delivery). The question this series asks is whether t3 moved it.

## The result

Four runs, same harness, same problem (`factorial`), same `--identity Gwen`,
`DEFAULT_STALE_LAG = 5`:

```text
sessions_started 16 · sessions_completed 16
insights_delivered 10 · dropped_stale 2 · dropped_late 4 · dropped_overflow 0
boundaries_superseded 0
```

**16 produced, 10 delivered — 62.5%**, against the baseline's 28.6%.

### Per counsel kind

| kind | delivered | dropped stale | attributable total |
|---|---|---|---|
| `step` | 8 | 2 | 10 |
| `durable` | **2** | **0** | 2 |
| *unattributable* | — | 4 late, 0 overflow | 4 |

The unattributable row is not an omission and it was **declared before the run**:
`ThreadedMuseRunner` updates its per-kind counters only inside `drain()`, so
deliveries and *stale* drops carry a kind and *late* and *overflow* drops
cannot. Reporting them as a total is the honest shape; assigning them a kind
would be invention.

### The two t3 codes

| code | fired |
|---|---|
| `DROPPED_COMPILATION_STARVED` | **never — no code path in `embodiment` emits it** |
| `DROPPED_COUNSEL_DISPLACED` | **never — no code path in `embodiment` emits it** |

Both are declared, exported and listed in `RUNNER_CODES`; neither is passed to
`_record` or `_degrade` anywhere in the package. This is reported as *absent*
rather than as `0`, because a zero would read as "this run did not trip it"
when the truth is that nothing can.
`tests/test_proof_reporting.py::TestUnproducedCodes` walks every module and
fails if a producer ever appears, so wiring one forces this section to be
corrected rather than quietly going stale.

## The number moved. It cannot be credited to kind-aware delivery

This is the part that matters, and the honest answer is not the flattering one.

**t3's rule can only change an outcome for a `durable` insight that would
otherwise have been dropped for loop distance.** In this series:

- Only **2 of 12** kind-attributable insights were labelled `durable` (16.7%).
- Both appeared in runs 2 and 3 — and **those runs recorded zero stale drops of
  any kind**, so nothing in them was near the threshold.
- **The record does not carry per-insight lag**, so whether either `durable`
  insight *would* have aged out cannot be determined from this artifact.

So: no measurement in this series shows the durable-sparing rule changing a
single delivery. The mechanism is present, correct, and — on this workload —
**never load-bearing**. That is the outcome the pre-registration named as most
likely, and it is what the data shows.

**A sufficient alternative explanation is present and measured.** These runs
were *shorter* than the baseline run:

| | baseline (n=1) | this series (n=4) |
|---|---|---|
| model turns | 6 of 14 | **4 of 14**, all four runs |
| tool calls | 24 | **14–16** |
| insights produced | 7 | 4 per run |
| delivered | 2 (28.6%) | 10 of 16 (62.5%) |

Staleness is *loop distance*: `DEFAULT_STALE_LAG = 5` discards step-sensitive
counsel more than five steps behind. A loop that takes 14–16 tool calls ages
less counsel than one that takes 24. The one run that did drop counsel as stale
is consistent with exactly that — both drops were `lag 6 > 5`, at steps 0 and 6.

**And the baseline is a single run.** Comparing one run against four, with
different loop lengths, is a weak comparison. The 28.6% → 62.5% movement is
recorded as *observed*, and explicitly **not** attributed to t3.

A mechanism that works but is never triggered is a different finding from a
mechanism that does not work. This is the first.

## The dominant loss is no longer staleness. It is a close-time race

Four of the six undelivered insights were `muse-insight-late`, and the ledger
is unambiguous:

```text
run 0   muse-insight-late   step=13   undrained when the runner closed
run 1   muse-insight-stale  step=0    insight about step 0 read at step 6 (lag 6 > 5)
run 1   muse-insight-stale  step=6    insight about step 6 read at step 12 (lag 6 > 5)
run 1   muse-insight-late   step=15   undrained when the runner closed
run 2   muse-insight-late   step=13   undrained when the runner closed
run 3   muse-insight-late   step=13   undrained when the runner closed
```

**Exactly one late drop in every one of the four runs**, at step 13–15, always
"undrained when the runner closed". That is deterministic, not stochastic: the
muse thinks about the final boundary, the actor finishes, the runner closes,
and the insight has no remaining drain to be collected at. It is **25% of all
counsel produced in this series**, and it is invisible to kind-aware delivery
because the late path never consults a kind.

This is not a bug in the sense of a broken invariant — `close()` records every
stranded insight rather than dropping it silently, which is exactly what C3
asks. But it does mean the largest single source of undelivered counsel on this
workload is a lifecycle race, not the staleness threshold that
[`proof.md`](proof.md) proposed re-deriving. **Anyone tuning
`DEFAULT_STALE_LAG` from these numbers would be tuning the smaller of the two
losses.**

## A cross-check from the other half of the series

[`association-work.md`](association-work.md) ran the *same* muse prompt and loop
through both models on the reflective cases and found the `step`/`durable`
split is a **model** property, not a role or prompt property:

| mind | `step` | `durable` |
|---|---|---|
| Gemma 4 31B (muse) | 21 | 2 |
| Qwen 3.6 27B (cortex) | 4 | 15 |

So "the muse hardly ever emits `durable`" — the finding t3 has to design around,
recorded in [`muse-challenge.md`](muse-challenge.md) as 29 of 31 — is a
statement about *this* muse model on *this* rig. Swapping the muse model would
change the input to kind-aware delivery with no change to embodiment at all.

## Configuration — recorded for the first time

`proof.py` produced the published baseline while **writing no configuration
preamble at all**, and its temperature was a literal inside `gateway()`. Both
are fixed; the values on the wire this time:

| setting | value |
|---|---|
| cortex | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP`, temperature **0.3**, max tokens 6000 |
| muse | `nvidia/Gemma-4-31B-IT-NVFP4`, temperature **0.3**, max tokens 1200 |
| muse controls | `max_turns = 2`, bare framing, identity `Gwen` |
| staleness | `DEFAULT_STALE_LAG = 5`, default policy |
| loop budget | `max_steps = 14` |
| n | 4 runs |

The muse's **0.3** is unchanged from what the baseline actually sent — the
hardcoded literal — so the comparison is like-for-like. What changed is that
the number is now visible in `results/proof_config.json` instead of being a
hidden variable in a published result.

## Degradations

Zero loop degradations across all four runs; zero harness errors; zero
overflow drops; zero superseded boundaries. Every muse session started and
completed (16/16). The six recorded transitions are the two stale drops and the
four late drops above — all of them delivered to the host's ledger, none silent.

## What could not be measured

- **Per-insight lag is not in the record**, so whether either `durable` insight
  would have been dropped without t3's rule cannot be determined. This is the
  single measurement that would have let the improvement be attributed, and it
  is absent.
- **The kind of the four late-dropped insights.** The late path does not touch
  the per-kind counters, by construction.
- **`relative_latency` is `None` in all four runs** — `proof.py` never calls
  `note_loop_step`, so the runner has no loop-side timing to compare against.
  The muse-versus-actor speed ratio that [`proof.md`](proof.md) reasoned about
  is therefore still unmeasured by this harness.
- **Whether a longer workload reproduces the baseline's drop rate.** All four
  runs took 4 model turns; none exercised a long loop. The 28.6% figure came
  from a 6-turn, 24-call run, and this series does not contain one.

## Limitations

- n = 4 runs, 16 insights, one problem, one rig, one model pair.
- The baseline is n = 1, so the "before" side of the comparison is a single
  observation.
- All four runs had identical loop lengths (4 turns), which is good for
  internal consistency and bad for coverage: the workload was never varied.

## Reproducing

```bash
export COLLEAGUE_API_KEY=...

uv run python examples/delivery_series.py --n 4 \
    --out docs/live-test-results/delivery-per-kind.jsonl

# one run on its own, with the per-kind counters printed
uv run python examples/proof.py --muse --identity Gwen --json

# the reporting, no rig required
uv run pytest tests/test_proof_reporting.py -q
```
