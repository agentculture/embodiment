# The scratchpad: one fix, and a failure it could not fix

**Date:** 2026-07-25 · **Instrument:** `examples/scratchpad.py` · **Rig:** see [README](README.md)

Built to attack the dominant failure of the series — drives ending
`exit=stopped` at `turns=2` having called **no tools at all**. The thinking
telemetry had diagnosed it as runaway reasoning: ~15,000 characters of internal
deliberation per turn, then prose instead of a structured call.

The counter-measure is a tool surface rather than a prompt tweak: a working
memory the model writes **prospectively**, one sentence per call, with the
intent recorded *before* the act it describes.

## Result 1 — it repairs the protocol failure

Designed subset problem (`docs/live-test-results/designed-problem.md`), same
rig, same temperature, three runs per arm on the committed instrument:

| | without scratchpad | with scratchpad |
|---|---|---|
| cortex alone | **1 / 4** | **2 / 3** |
| cortex + muse | **1 / 4** | **2 / 3** |

25% → 67%, and **the same on both arms**. The failing runs still fail the same
way (`exit=stopped`), so the pad does not make the model infallible; it gives
the reasoning somewhere to go often enough to change the rate substantially.

Every successful run wrote the same shape: `intend → observe → conclude`.

## Result 2 — it does NOT solve the hard problems, and shows why

The register puzzles, with the scratchpad:

| run | exit | entries | answer |
|---|---|---|---|
| 4-bit, solo | `stopped` | 2 | none |
| 4-bit, muse | `stopped` | 1 | none |
| Entropic + constraint, solo | `stopped` | 0 | none |

The muse arm's single entry is the most informative line produced in this whole
series:

```text
n1 [intend] I will systematically search through all 60 possible orders (where A
            comes before D) and all 16 initial values to find which combination
            produces values in the correct sets after 1, 3, and 5 operations.
    ! n1 has no observation — you were interrupted here.
```

**The strategy is exactly right.** 60 orders — 5! halved by the A-before-D
constraint — against 16 initial values. That is precisely the search this
author ran to verify the puzzle, and precisely the hand method used to solve it.
The model knew the algorithm and had no way to execute 960 traces of five
bitwise operations in-head.

### `exit=stopped` is two different failures wearing one exit code

This is the correction the hard runs forced, and it had been muddled all series:

- On the **subset** problem it is a **protocol** failure. The model can do the
  work but reasons past the tool call. The scratchpad fixes it.
- On the **register** problems it is a **capacity** failure. The mind has the
  right plan and cannot run it. No scratchpad repairs that, and none should.

That is a direct and sharper argument for [#5](https://github.com/agentculture/embodiment/issues/5)
than the one the issue was filed with: not "no hand-written tool surface reaches
this", but *here is a model stating the correct algorithm and stalling for want
of somewhere to run it.*

The Entropic run wrote **nothing at all** in two turns. Given it needs 120 × 256
traces under a Hamming-distance-2 error model, stopping is arguably correct
behaviour — it simply failed to say so, which is its own kind of failure.

## Result 3 — the muse still shows no effect, and the self-correction was not one

Six runs on the committed instrument produced **zero revisions**, in either arm.

An earlier run on the *journal* version of the pad appeared to show the muse arm
self-correcting (`n6 revises n5`), and this author reported it as "the first
self-correction observed anywhere in this series". **That was wrong twice over:**

1. **It was not a correction.** The two entries carry *identical values* — n6
   re-states n5's table with the base case added, filed under the `revise` tool.
   It is a re-verification. The claim was made from the tool's *label* without
   reading its *text*.
2. **It does not reproduce.** Zero revisions across six runs.

The operator's challenge at the time — that a revision "could have happened
without Gemma, theoretically" — was right in the strongest available sense: it
does not happen *with* Gemma either.

This is the fifth measurement error by this author in the series, against zero
genuine model failures. See the Corrections section of [README](README.md).

## What the pad demonstrated about itself

The reset-survival mechanism proved out **in the wild rather than in a fixture**.
The 4-bit muse pad ends on an unanswered intent and says so. A successor process
reading only that file would know the plan, and know it had not been run — which
is the entire design claim, met on a real failure rather than a synthetic one.

One weakness observed: the 4-bit solo arm's `observe` did not observe anything —
*"I began systematically analyzing the problem…"* restates the intent rather
than recording a result. The pad can be used as narration instead of as memory,
and nothing currently prevents that. The muse arm did not do this.

## Limits

- Three runs per arm on one problem; the register results are single runs.
- One rig, one model pair, one temperature (0.2 for both minds).
- The 67% figure is 4 of 6 pooled; the direction is consistent but the interval
  is wide.
- No experiment isolated *which* property of the pad helps — forcing a tool call
  per step, externalising the reasoning, or the ordering discipline. All three
  changed at once.
