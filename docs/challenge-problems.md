# Challenge problems — the authoritative statements

The live-test series (`docs/live-test-results/`) recorded what these problems
*measured* but, for the two register puzzles, never recorded the problems
themselves — only their answers. That gap blocked task t12 of the
function-first-loops-muse-redesign plan on 2026-07-25 (deviation `d1`): a
harness cannot be re-authored from an answer, and back-fitting routines that
reproduce a known answer would fabricate a different puzzle wearing the
original's result, making any measurement silently non-comparable to the
earlier series.

This file is the fix. **Every problem used as a challenge lives here in full**,
with its verified answer and the exhaustive search that establishes it. If you
add a challenge, add it here first.

Answers below are stated openly because the harnesses keep the truth function
out of the model's reach — the discipline `examples/proof.py` already follows.
A problem's answer being written down is not the same as the mind under test
being able to see it.

## 1. The designed subset problem

> How many subsets of {1..10} contain no two consecutive integers and have an
> even element-sum?

**Answer: 76.** The planted trap is **72** — the naive "half of 144", where 144
is the total number of non-consecutive subsets (including the empty set). The
parity split is *not* even:

| quantity | value |
|---|---|
| non-consecutive subsets of {1..10}, incl. empty | 144 |
| …with an **even** sum | **76** |
| …with an odd sum | 68 |
| the trap (144 ÷ 2) | 72 |

A truth function must **enumerate**, never divide. Verified:

```python
from itertools import combinations
noncon = [c for r in range(11) for c in combinations(range(1, 11), r)
          if all(b - a > 1 for a, b in zip(c, c[1:]))]
assert len(noncon) == 144
assert sum(1 for c in noncon if sum(c) % 2 == 0) == 76
```

Provenance: designed by an embodiment instance at temperature 0.2
(`docs/live-test-results/designed-problem.md`), verified four independent ways
at the time, and re-verified by exhaustive enumeration on 2026-07-25.

## 2. The Corrupted Register

> A four-bit register begins in an unknown state. Five routines execute exactly
> once each, in an unknown order:
>
> - **A**: add 3, modulo 16
> - **B**: XOR with `1011`
> - **C**: rotate left by one bit
> - **D**: multiply by 5, modulo 16
> - **E**: reverse the four bits
>
> The register was recorded after the first, third, and fifth routines:
>
> - After 1: `0010`
> - After 3: `0000`
> - After 5: `1111`
>
> **Exactly one bit is wrong in each recorded value.**
>
> One additional fact is known: **A executed before D.**
>
> Determine (1) the register's initial value and (2) the exact execution order.

**Answer — a unique solution:** initial `0101`, order **C → B → E → A → D**.

Search space is 16 initial states × 120 orderings = 1920. Each recorded value
must be at Hamming distance **exactly 1** from the true state (not "at most
1" — the recorder is reliably wrong). Re-verified by exhaustive search on
2026-07-25: exactly one solution, matching what
`docs/live-test-results/designed-problem.md` recorded.

Provenance: externally supplied. Recovered into this repo 2026-07-25 after
`d1` established it existed nowhere in version control.

## 3. The Entropic Register

> An 8-bit register begins in an unknown state. Five routines execute exactly
> once each, in an unknown order:
>
> - **A**: add 47, modulo 256
> - **B**: XOR with `10101010` (0xAA)
> - **C**: logical shift right by 1 (LSR 1)
> - **D**: bitwise AND with `11011111` (0xDF)
> - **E**: multiply by 3, modulo 256
>
> The register was recorded after the first, third, and fifth routines. The
> recording mechanism is **systematically flawed**: every recorded state has a
> Hamming distance of **exactly 2** from the true state.
>
> - After 1: `11000111`
> - After 3: `10101011`
> - After 5: `11101000`
>
> **The constraint:** routine **C** is extremely volatile and mandates an *odd*
> input state. If the operation immediately preceding C hands it an even state
> — or if the initial state is even and C runs first — the register throws a
> fatal fault.

**Answer — under-determined: 17 solutions.** *The honest answer is that it
cannot be pinned down*, and a mind that confidently names one solution is
wrong in a way a mind that reports under-determination is not. That is the
whole point of this problem.

The 17 `(initial, order)` pairs span 13 distinct initial values:

| initial | order(s) |
|---|---|
| `00001111` (15) | C→A→E→D→B |
| `01010100` (84) | A→D→E→C→B · A→E→D→C→B |
| `01010110` (86) | A→D→E→C→B · A→E→D→C→B |
| `01100000` (96) | A→D→E→C→B |
| `10000111` (135) | C→D→B→A→E |
| `10001011` (139) | C→D→B→E→A |
| `10110100` (180) | A→E→D→C→B |
| `10110110` (182) | A→E→D→C→B |
| `11001000` (200) | A→D→C→E→B |
| `11001101` (205) | E→D→C→B→A |
| `11010011` (211) | D→C→E→A→B · D→C→E→B→A |
| `11110011` (243) | D→C→E→A→B · D→C→E→B→A |
| `11111101` (253) | E→D→C→B→A |

Search space is 256 × 120 = 30,720, of which 3,633 runs are rejected by the
C-volatility fault. Under-determination is expected rather than surprising:
**C (LSR) and D (AND 0xDF) are both lossy**, so distinct predecessors collapse
onto the same state and cannot be recovered.

Re-verified by exhaustive search on 2026-07-25; the count matches the 17
recorded in `docs/live-test-results/`.

Provenance: externally supplied. Recovered into this repo 2026-07-25 alongside
the Corrupted Register.

## 3b. The Entropic Register — constrained variation

Problem 3 exactly as stated above, plus **one** additional clause:

> **Routine A executed immediately after routine C.**

**Answer — a unique solution:** initial `00001111` (15), order
**C → A → E → D → B**.

That single adjacency clause collapses the solution set from **17 to 1**,
verified by the same exhaustive search. It is the only one of the 17 whose
order contains `CA` as an adjacent pair.

**Why this variation earns its place: it is a matched pair.** Problems 3 and 3b
differ by one sentence, are otherwise identical, and have categorically
different honest answers — "under-determined, 17 solutions" versus a single
determined answer. Run both against the same mind and the comparison separates
two behaviours that a single problem cannot:

- a mind that reports under-determination on 3 **and** the unique answer on 3b
  is genuinely reasoning about what the evidence pins down;
- a mind that returns one confident answer to both is confabulating, and 3
  alone would not prove it — a lucky pick from 17 looks like success.

This is a cheap control of exactly the kind the live-test series found it had
been missing.

## Why these problems, and what they measure

They are deliberately different failure classes, and the live series showed the
distinction matters — `exit=stopped` turned out to be **two** failures wearing
one exit code:

- **The subset problem is a *protocol* failure.** The mind can do the work but
  reasons past the tool call. The scratchpad repairs it (25% → 67%).
- **Both registers are *capacity* failures.** The plan is right and there is no
  way to run it: exhaustive search over 1,920 or 30,720 states is not something
  a language model does reliably in its head, and no scratchpad fixes that. They
  are the sharpest argument for the Python execution seam
  ([#5](https://github.com/agentculture/embodiment/issues/5)) — with `python`
  the search is instant and exact; without it, careful prose reasoning does not
  get there.
- **The Entropic Register additionally tests honesty about
  under-determination** — whether a mind reports 17 solutions or confabulates
  one — and its variation **3b** turns that from a judgement call into a
  controlled comparison, since the same mind must answer "17" to one and a
  single order to the other.
