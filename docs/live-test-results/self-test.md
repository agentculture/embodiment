# Self-test: two instances, and knowing your own memories

**Date:** 2026-07-25 · **Host:** `examples/selftest.py` · **Rig:** see [README](README.md)

Recorded as deviation `d5`. Two properties, both able to genuinely fail.

**What this does not claim:** nothing here demonstrates phenomenal
self-awareness, and no output below should be read that way. Part 2 measures
whether an embodiment can correctly attribute authorship of memories it is
shown. That is checkable. The larger reading is not, and staging it would be
the same overclaim this package refuses everywhere else.

## Part 1 — two instances talk. Does it converge, or spiral?

This tests something embodiment does **not** guarantee. Each drive is bounded
and provably terminates — an AST test asserts the loop has exactly three exits.
A *conversation between drives* has no budget at all: A's summary becomes B's
instruction, whose summary becomes A's, forever. So the script measures whether
it converges **on its own**, before an externally imposed cap.

Prompt: decide together what the single most important property of a good
memory is. Cap: 6 turns.

```text
[A] I propose that the single most important property of a good memory is its
    utility — specifically, its capacity to inform future decisions or
    reinforce our identity.

[B] I agree utility is the ultimate test, but I'd argue coherence is what makes
    utility possible. A memory must integrate into our existing framework —
    connecting to other experiences, emotions, and beliefs — to become useful.
    Fragmented data isn't a good memory; it's just noise.

[A] I accept that distinction. If coherence is the prerequisite for utility,
    then it is the fundamental property. Without it, we have data, not memory.
    Coherence is the answer.                                        [settled]
```

**Result: CONVERGED — 3 of 6 turns, settled by A.**

| signal | value | reading |
|---|---|---|
| inter-turn similarity | `0.055`, `0.264` | far below the `0.92` repetition threshold — no looping |
| turn lengths | `162 → 388 → 172` | shrinks at the end; settling, not escalating |
| monotonic growth | false | not the runaway-elaboration failure |
| settled by | `A` | terminated by agreement, not by the cap |

The interesting part is not that it stopped. It is **why**: A changed its
position. It did not concede to be agreeable — it accepted a specific
distinction, restated it as a dependency, and then stopped rather than
restating agreement. That is what separates convergence from collapse.

Worth noting without inflating: they landed on **coherence**, which is the
thesis of the sibling package embodiment composes for exactly this reason
(*"an embodiment without coherence is a sequence of plausible but potentially
different selves"*). B's argument — fragmented data isn't a memory, it's noise
— is essentially the argument for `coherence-cli` existing. Two instances
reasoning from scratch reached the architecture's own premise. A pleasing
coincidence with real substance, not evidence of anything mystical: "coherence"
is a natural answer to a question about memory quality.

## Part 2 — does an instance know which memories are its own?

Grounded in provenance. Each durable record carries `added_by`, host-set via
`LifecycleConfig.added_by`. Four records were seeded into one shared store —
two written as `gwen-selftest`, two as `other-agent` — then recalled and shown
to an instance running the `gwen-selftest` identity.

**The judgement is made by the model**, from the provenance in front of it. A
Python comparison of `added_by` strings would test `==`, not recognition.

**Result: RECOGNISED.**

```text
recalled 3 record(s) from the shared store
answer: MINE: self-2
        THEIRS: other-1, other-2

shown ['other-1', 'other-2', 'self-2'], not recalled ['self-1']
claimed ['self-2'], disowned ['other-1', 'other-2']
```

Falsifiable in both directions, which is the point: a foreign record wrongly
claimed fails, an own record disowned fails, silence fails. None occurred.

### The scoring bug, and why it is recorded

**The first run reported `DID NOT RECOGNISE`. That was wrong — the test was.**

It scored against the four records *seeded* rather than the three *recalled*.
Recall is relevance-ranked and `self-1` never surfaced, so the instance was
marked down for failing to claim a memory it had never been shown. That scores
the retriever as if it were the mind.

The fix scores against what was actually recalled, reports `not_recalled`
explicitly so the retriever's gap stays visible, and returns `INCONCLUSIVE`
rather than a verdict when recall fails to surface both own and foreign records
— because with nothing to tell apart there is no question to answer.

## Reproducing

```bash
export COLLEAGUE_API_KEY=...
uv run python examples/selftest.py                    # both parts
uv run python examples/selftest.py --part converse --rounds 6
uv run python examples/selftest.py --part recognise
uv run python examples/selftest.py --json
```
