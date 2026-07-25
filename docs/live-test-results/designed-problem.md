# The designed problem, and a negative result about the muse

**Date:** 2026-07-25 · **Rig:** see [README](README.md)

Two experiments, run in sequence:

1. **Inversion** — the embodiment *designs* a hard problem instead of solving one.
2. **Replication** — a fresh instance attempts it, with and without the muse,
   four runs per arm.

The headline is the second one, and it is negative: **no measured muse effect.**

## 1. Designing a problem — and the temperature result

Same prompt, same rig, same budget, same muse settings. One variable.

| arm | cortex/muse temperature | outcome |
|---|---|---|
| high | **0.8 / 0.8** | `exit=stopped`, 2 turns, **nothing submitted** |
| low | **0.2 / 0.2** | `exit=finished`, well-posed problem, correct answer key |

The operator's heuristic — *0.1–0.3 for brilliance, 0.6–0.8 for creative
writing* — predicted the 0.8 arm would produce "creative nonsense". What
happened was sharper: it produced **nothing**. The model stopped emitting tool
calls and the loop exited through its empty-tool-call path. The failure was not
degraded content but an abandoned protocol.

The original 0.8 setting came from assuming "designing is creative". That
conflated the *genre* of a task with its *demands*: designing a hard problem is
a correctness task in creative clothing, because the answer must be right and
the trap must actually bite.

### What the 0.2 arm produced

> Let **S** be the collection of all subsets of {1, …, 10} that contain no two
> consecutive integers. For each A ∈ S, let σ(A) denote the sum of the elements
> of A. Determine the number of subsets A ∈ S for which σ(A) is even.

Its own answer key: **76**. Its stated trap: *"a solver might incorrectly assume
that exactly half of the valid subsets have an even sum, leading to 144/2 = 72."*
Its unlookupability argument: *"requires deriving a parity-dependent recurrence
rather than recalling a known result."*

All three hold up. This is a genuinely good problem.

### Four independent confirmations of 76

| source | method |
|---|---|
| the designer's own key | its worked solution |
| this author, by hand | four-state DP over (include/exclude × parity) |
| brute force | enumerate all 144 valid subsets |
| ChatGPT (external) | two-term recurrence, parity-coupled |

ChatGPT's derivation is cleaner than the author's: instead of tracking
include/exclude separately, it couples `(Eₙ, Oₙ)` directly, with the parity of
`n` deciding whether the recurrence stays diagonal (`Eₙ = Eₙ₋₁ + Eₙ₋₂`, n even)
or flips (`Eₙ = Eₙ₋₁ + Oₙ₋₂`, n odd). Its table matches at every n, not just
the endpoint.

**On difficulty, corroborated rather than self-assessed:** ChatGPT called it
*"moderate difficulty, but straightforward once you choose the right
recurrence"*; this author independently judged it "real work, but not hard".
A good problem, not a defeating one.

## 2. The replication — no muse effect

A fresh instance, no memory of designing it, no tools but `submit`. Four runs
per arm at temperature 0.2.

| arm | correct | stopped without answering | rate |
|---|---|---|---|
| cortex alone | 1 | 3 | **1 / 4** |
| cortex + muse | 1 | 3 | **1 / 4** |

**Identical.** Every failure was `exit=stopped` at `turns=2` — the model
abandoning tool calls, not answering wrongly. Notably, **no run ever answered
72**: the designed trap never fired, in either arm.

### The claim this author nearly published, twice

The first pair ran solo-fails / muse-succeeds, which reads as the
diverse-mind thesis vindicated. It was noise.

- After **one** run per arm, the write-up would have been "the muse rescued a
  failure".
- After **two of three** solo replicates (both failures), this author wrote
  *"3 of 3 reproducible failure means the first pair wasn't luck"* — before the
  third replicate had reported. The third succeeded, falsifying it.
- With n=4 per arm, the rates are the same.

Both premature claims pointed in the direction the author was hoping for. That
is recorded because it is the most transferable thing in this document.

### What the failure actually is

`exit=stopped` at `turns=2` is remarkably consistent — across solo runs, muse
runs, and the 0.8 design run. The model gets two turns in, then emits prose
instead of a tool call, and the loop exits through its empty-tool-call path.

This is the signature colleague already tracks in
[#353](https://github.com/agentculture/colleague/issues/353),
[#346](https://github.com/agentculture/colleague/issues/346) and
[#323](https://github.com/agentculture/colleague/issues/323) — drives collapsing
because Qwen 3.6 27B emits tool calls as text. Those are filed against
low-temperature drives; the 0.8 result here suggests temperature aggravates it,
which nobody appears to have isolated.

**The loop handled it correctly every time**: no crash, no hang, no fabricated
answer — an honest `exit_reason`, an empty payload, and a harness that printed
`(nothing submitted)` rather than inventing one. That is what the three-exit
termination proof is for.

## 3. The 4-bit register puzzle — both arms failed

An externally-supplied puzzle (harder: a Hamming-distance-1 error model over
five non-commuting 4-bit routines, plus an ordering constraint). Verified by
exhaustive search to have exactly one solution — initial `0101`, order
`C→B→E→A→D` — which this author also derived by hand.

| arm | outcome |
|---|---|
| cortex alone | `exit=stopped`, 2 turns, nothing submitted |
| cortex + muse | `exit=stopped`, 2 turns, nothing submitted |

This killed the last surviving hypothesis. After the subset problem the story
was "muse guidance keeps the model in tool-calling mode". Here guidance was
delivered and the muse arm stalled identically. The collapse is
**prompt-shape dependent, not muse-dependent**.

## What this series actually measured

It set out to test whether a second, different mind improves an embodiment's
work. On this evidence it does not — at least not on tool-driven reasoning
tasks at n=4.

What it reliably surfaced instead is **a tool-calling reliability problem in the
cortex under this prompt shape**, which a second mind does not fix, and which
temperature appears to worsen.

Earlier tasks did show the muse making the cortex *more careful* — 4× the
verification on the factorial proof, and materially fuller summaries in the
greenhouse demo (which matters, because the durable record **is** the summary).
Neither changed an outcome. "More careful, not more correct" is the fairest
summary of every muse result in this repository.

## Limits

- n=4 per arm, one problem, one rig, one model pair. Under-powered for anything
  but a large effect.
- Both arms ran at 0.2 for **both** minds. A muse whose role is
  `divergent_second_opinion` may want a *higher* temperature than the actor —
  running it at 0.2 may have suppressed the divergence it exists to supply.
  Untested. See [configurations.md](configurations.md).
- The guidance confound is unresolved: muse guidance adds *any* structured text
  mid-conversation. The clean follow-up injects inert filler and checks whether
  that alone changes the completion rate.
