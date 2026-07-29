# Is the role split real? The association-work 2×2

**Date:** 2026-07-29 · **Rig:** see [README](README.md) · **Harness:**
`examples/association_work.py` · **Raw responses:**
[`association-work.jsonl`](association-work.jsonl) (42 records) ·
**Pre-registration:**
[`association-work-preregistration.md`](association-work-preregistration.md),
committed before the first dial

## The decision, first

> **`INCONCLUSIVE`. The function map is NOT promoted. It stays in
> `docs/relationships.md` §3, and `README.md` / `CLAUDE.md` are unchanged.**

Judged against the rule committed in
`examples/association_work.py` before any model was dialled:

| id | condition | result | |
|---|---|---|---|
| V1 | every cell has data | 4 of 4 cells, n ≥ 9 | **pass** |
| V2 | no cell over ⅓ transport errors | 0 transport errors anywhere | **pass** |
| V3 | reflective axis can discriminate | muse 1.000 vs cortex 1.000 | **FAIL** |
| V4 | executive axis can discriminate | muse 0.333 vs cortex 0.333 | pass |
| P1 | interaction ≥ 0.40 | **0.00** | **FAIL** |
| P2 | reflective gap ≥ 0.25 | **0.00** | **FAIL** |

A validity failure makes the verdict `INCONCLUSIVE` rather than `NEGATIVE`.
**The distinction does not matter for the decision and it should not be used to
soften the finding:** both outcomes block promotion, and here P1 and P2 fail
too. *There is no reading of this data under the pre-registered rule that
promotes the map.*

## The numbers

Every cell, n stated:

| cell | model | n | passed | rate |
|---|---|---|---|---|
| reflective · muse | `nvidia/Gemma-4-31B-IT-NVFP4` | **12** | 12 | **1.000** |
| reflective · cortex | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | **12** | 12 | **1.000** |
| executive · muse | `nvidia/Gemma-4-31B-IT-NVFP4` | **9** | 3 | **0.333** |
| executive · cortex | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | **9** | 3 | **0.333** |

```text
D_reflective = 1.000 − 1.000 =  0.00
D_executive  = 0.333 − 0.333 =  0.00
interaction  = 0.00 − 0.00   =  0.00
```

Per problem, both minds landed on **the same cell in every case**:

| axis | problem | muse | cortex |
|---|---|---|---|
| reflective | `ab_test` | 4/4 | 4/4 |
| reflective | `cache_ttl` | 4/4 | 4/4 |
| reflective | `rollback` | 4/4 | 4/4 |
| executive | `subset` | 3/3 | 3/3 |
| executive | `register` | 0/3 | 0/3 |
| executive | `entropic3b` | 0/3 | 0/3 |

**Zero degradations and zero transport errors across all 42 runs.** Total
measured model time 10,774.9 s (≈ 3.0 h); the per-cell totals below are each
rounded, so they sum to 10,774.

## What this actually shows

**The muse golden's headline is not a muse-specific property.** The 2026-07-26
series measured the 31B muse challenging a committed cortex result 9/9 and
9/9. Put the *cortex* model through the same muse loop, the same three cases,
the same v4 grader and the same framing, and it also scores 12/12 — with a
maximum near-copy of 0.041 against a 0.35 limit, versus the muse's 0.062. Both
minds name a premise the cortex text never states; neither restates.

That is the load-bearing result and it points the opposite way from the map.
The map's claim is that reflection is the muse's *function*. On the only
reflective instrument this repo owns, it is not distinguishable from the
cortex's.

**The executive axis measured a real null, and a shallow one.** V4 passed on
the aggregate (0.333 ≠ 0.000 and ≠ 1.000), so the rule read the axis as able to
discriminate. Looking per problem, it mostly could not: `subset` was a ceiling
for both (3/3, 3/3) and `register` and `entropic3b` were floors for both (0/3,
0/3). **The aggregate rate had resolution that no individual problem had**, and
a rule that checks walls per axis rather than per problem gives the executive
null more credit than it has earned. Recorded as a defect in the pre-registered
rule, not repaired after the fact.

### The failure modes are not the same, even though the rates are

Both executive cells are 3 passed / 6 protocol / 0 reasoning — **neither model
ever submitted a wrong answer to `register` or `entropic3b`.** But they failed
in completely different ways:

| | muse (Gemma 4 31B) | cortex (Qwen 3.6 27B) |
|---|---|---|
| exit reason on the 6 failures | `budget`, 6/6 | `stopped`, 6/6 |
| model turns used | 14 of 14, every time | 2–7 of 14 |
| tool calls made | 43–123 | 0–8 |
| summary produced | 1,906–12,538 chars of working | `__COLLEAGUE_NO_RESULT_PRODUCED__`, 6/6 |

The cortex's sentinel is emitted only when a drive produced **no finish
summary, no forced-synthesis output, and no substantive prose at all**
(`loop.py::_resolve_terminal_summary`). This is the `exit=stopped` collapse
already recorded in [`scratchpad.md`](scratchpad.md), reproducing on a second
problem family.

So "3/9 versus 3/9" must not be read as *equally good at executive reasoning*.
It reads as: **both minds failed to complete two of three problems inside a
14-turn budget, one by grinding through it and one by giving up silently.**

### A lenient check that looked like a finding and was not

The muse's budget-exhausted `register` prose contains `0101` and `CBEAD` — the
correct initial state and order — in all three runs. That looks like "it found
the answer and only failed to submit it". **It is a false positive, and reading
the text is what caught it.** The muse enumerates candidates exhaustively, so
all 16 four-bit strings appear; its actual conclusion is stated at the end and
is wrong:

```text
initial=1011 order=BCAED
finish(answer='initial=1011 order=BCAED')
```

A substring check for the truth inside an exhaustive search is worthless,
because an exhaustive search prints the truth by construction. Recorded because
it is the exact shape of the overclaims this series of documents keeps having
to retract.

That trailing `finish(...)` is also *literal text where a tool call belonged*.
The loop has a `finish_recovered: "literal-markup"` path for that case; **this
series did not capture `TaskResult.finish_recovered`, so whether the recovery
was attempted here cannot be said from the record.** Listed under what could
not be measured, not resolved by inference.

## A second finding: counsel-kind labelling is a model property

Task t2 gave the muse a counsel-kind vocabulary, and
[`muse-challenge.md`](muse-challenge.md) recorded the live 31B labelling **29 of
31 insights `step`** — the finding t3's kind-aware delivery has to live with.

This series ran the *same prompt, same loop, same cases* through both models,
and the labels diverge sharply:

| mind | `step` | `durable` | n insights |
|---|---|---|---|
| muse (Gemma 4 31B) | **21** | 2 | 23 |
| cortex (Qwen 3.6 27B) | 4 | **15** | 19 |

The `step`-heavy self-labelling that t3 has to design around is therefore a
property of **this muse model**, not of the muse role, the charter, or the
framing — because holding all three fixed and changing only the model flips the
distribution. Replicated at n = 12 per cell, and consistent with t17's number
for the muse arm (21/23 here versus 29/31 there).

That matters for t3: "the muse hardly ever emits `durable`" is a statement
about Gemma 4 31B on this rig. A different muse model would change the input to
kind-aware delivery without any change to embodiment.

## Cost and latency — contended, and not a speed comparison

The local GPU sat at 95–96% utilisation throughout, with a concurrent drive on
the cortex. The cortex is local; the muse is proxied from a peer. These are
wall-clock numbers under contention and must not be read as model throughput.

| cell | median | min | max | cell total |
|---|---|---|---|---|
| reflective · muse | 19.4 s | 10.7 s | 24.5 s | 224 s |
| reflective · cortex | 159.1 s | 69.5 s | 463.0 s | 2,457 s |
| executive · muse | 237.5 s | 85.6 s | 686.9 s | 2,501 s |
| executive · cortex | 470.0 s | 185.6 s | 1,202.7 s | 5,592 s |

On the reflective axis the muse used 2.17 turns and 2,010 tokens on average;
the cortex 1.75 turns and 5,130 tokens. The cortex is a thinking model, so most
of that is a reasoning field the grader never sees (below).

## What could not be measured

- **The cortex's `reasoning` field is never graded, and was not kept.**
  `MuseLoop` reads `ModelResponse.content` and nothing else
  (`embodiment/muse.py::_content`). The comparison is symmetric — both minds
  run the same loop and are graded on what that loop would hand a host — but
  this series cannot say whether reflective content also appeared in a field
  the loop discards. The JSONL does not carry it either, so it cannot be
  recovered from this artifact.
- **Whether the loop's literal-markup finish recovery fired** on the muse's
  `register` runs. `finish_recovered` was not captured.
- **Unprompted reflection.** Only the `task` framing was run, on purpose (the
  executive axis states its problem outright, so the reflective axis had to as
  well). Whether either mind reflects with no host framing is not measured
  here.
- **Whether a challenge is correct.** The grader judges the shape of a
  challenge, never its soundness; every verdict carries
  `challenge_soundness: not machine-graded — read it`.
- **Anything beyond one rig, one model pair, three reflective cases and three
  executive problems**, all authored by the same hand as the graders.

## Limitations

- **The reflective axis had no headroom.** 12/12 against 12/12 says the two
  minds are indistinguishable *on this instrument*, not that they are
  indistinguishable. A harder reflective set could separate them; this one
  cannot. Condition V3 exists because that risk was foreseen, and it fired.
- **The executive axis was mostly at a wall per problem**, as above.
- **n = 12 and n = 9 per cell, single rig, single pair.** A zero interaction at
  this n rules out a large concentrated effect. It does not rule out a small
  one.
- The two models differ in more than role: 27B thinking versus 31B instruct,
  local versus proxied. The 2×2 controls for "uniformly better or worse", which
  is the claim under test, but it cannot attribute a difference to *role* as
  opposed to any other property. It found no difference to attribute.

## Corrections and repairs this series forced

The executive axis had **never been run**. Preparing it found six defects, all
fixed before the pre-registration commit and all recorded there:

1. All three executive harnesses built `Task(system=…, tools=…)` and called
   `run(task=…, complete=…, bench=…)`. The contract has none of those four
   names, so every invocation raised `TypeError` before its first model call.
   The committed tests graded `truth()` and `grade()` — both correct — and
   never drove the harness.
2. `results/` was never created, so the config preamble raised
   `FileNotFoundError` even earlier.
3. `--cortex-temperature` was accepted, written into the config preamble, and
   then ignored: `gateway()` sent a hardcoded 0.3. A recorded value that is not
   the value on the wire is the hidden variable the preamble exists to prevent.
4. One bench was shared across `--n` runs, so run 2's tool log carried run 1's
   calls.
5. `muse_challenge.py` had no `--max-tokens`, so a thinking model could not be
   given a budget it could finish a thought in.
6. `proof.py` wrote no config preamble at all (see
   [`delivery-per-kind.md`](delivery-per-kind.md)).

Direction: 1, 2 and 5 blocked measurement outright; 3 and 4 corrupted the
record rather than the result; 6 left the published delivery baseline unable to
answer the question its successor was built to ask.

**One predicate was settled after the pre-registration commit**:
`failure_modes()`'s exact protocol-versus-reasoning test. It was finalised
before any executive run existed, it is a disclosure rather than a gate, and
`decide()` never reads it. Stated here rather than left in the history.

## Reproducing

```bash
export COLLEAGUE_API_KEY=...

uv run python examples/association_work.py --live \
    --n-reflective 4 --n-executive 3 \
    --out docs/live-test-results/association-work.jsonl

# re-apply the committed rule to the committed records; dials nothing
uv run python examples/association_work.py --analyse \
    --out docs/live-test-results/association-work.jsonl

# the rule and the harnesses, no rig required
uv run pytest tests/test_association_work.py -q
```
