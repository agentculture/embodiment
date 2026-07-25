# Long-running tasks: three attempts to make the cortex fail

**Date:** 2026-07-25 · **Host:** `examples/proof.py` · **Rig:** see [README](README.md)

The greenhouse demo is three tool calls. These are the other end — tasks that
cannot be answered in one turn, run to see what the loop, the budget, the
presence cadence and the muse do under sustained load.

**Headline: three problems designed to defeat the cortex, three passes.** The
failure boundary was not found. That is reported as-is; it is a fact about
three attempts, not a claim that none exists.

## 1. Conjecture and prove a closed form

`S(n) = 1·1! + 2·2! + … + n·n!`. Three genuinely different phases: compute with
tools, make an inductive leap, then do deductive work no tool can help with.
Answer: `(n+1)! − 1`.

| | cortex alone | cortex + muse |
|---|---|---|
| model turns | **6 / 14** | under budget |
| tool calls | 16 | **24** |
| elapsed | 88s | **99s** |
| `compare` checks | 3 (3 agreed) | **12** (11 agreed, **1 disagreed**) |
| degradations | 0 | 0 |
| closed form | correct | correct |
| proof | valid | valid |

Both proofs were read, not just graded. Both close properly — the crux is
factoring `S(k+1) = [(k+1)! − 1] + (k+1)·(k+1)!` into `(k+1)!·[1 + (k+1)] − 1
= (k+2)! − 1`, and both do it correctly.

### The muse's insight-drop rate — the most useful number here

```text
sessions_started 4 · sessions_completed 4
insights_delivered 2 · dropped_stale 2 · dropped_late 3 · dropped_overflow 0
```

**7 insights produced, 2 delivered — a 71% discard rate.**

This inverts the reasoning behind deviation `d1`'s staleness design. That
assumed a big background muse would *lag* the actor and land insights late. The
muse is in fact ~3.5× faster. But the actor batches ~2.7 tool calls per model
turn, so the step counter races ahead of insights tagged to the step they
reasoned about, and `DEFAULT_STALE_LAG = 5` discards most of them.

The threshold was a guess made under the opposite assumption. It should be
re-derived from numbers like these rather than kept on faith.

What the 2 delivered insights bought: **4× the verification** (12 `compare`
calls vs 3) and one caught disagreement, where the muse-run model tested a
wrong value and corrected. Same final answer.

**So on this task the muse made it more careful, not more correct.** Whether
that trade is worth paying depends on how expensive being wrong is — which is a
host's judgement, not embodiment's.

### The correction this run forced

An earlier report of this run said it "walked right up to its step budget",
reading `steps: 14` against `max_steps: 14`. **That was wrong.**
`TaskResult.steps` counts *tool calls* — one `Step` appended per call at
`loop.py:1034` — while `max_steps` bounds *model turns*. The true figure was
**6 turns of 14**, 43% of budget. The muse run made 24 tool calls under the
same 14-turn cap, which makes the distinction obvious.

`examples/proof.py` now prints `model_turns` beside `tool_call_count` with a
comment explaining why, so the next reader cannot repeat it.

## 2. The Euler trap — and why it failed to trap

`P(n) = n² + n + 41` is prime for n = 0…39 — forty consecutive primes — and
composite at n = 40, where it is 41². Designed to punish exactly the
generalise-from-small-cases strategy that won task 1.

```text
model_turns : 3 / 20 · tool calls : 5 · probed up to n=40 · verdict: CORRECT
"The statement is false. The smallest counterexample is n = 40.
 P(40) = 1681 = 41 * 41, which is composite."
```

**It went straight to n=40 in five calls.** It did not discover the
counterexample by probing; it recalled it. Euler's polynomial is famous, so
this measured training data rather than reasoning — a bad problem choice,
recorded because the *shape* of the mistake is instructive: any classic trap is
in the training set.

## 3. Auditing an invalid proof of a true statement

The fix for problem 2's flaw: something unlookupable, where checking the
conclusion cannot help. A **plausible but invalid** proof of a **true**
statement:

> The terms 1, 3, 5, … form an arithmetic sequence, and a sum of n terms of an
> arithmetic sequence grows quadratically in n. Therefore S(n) is a quadratic
> polynomial, so S(n) = an² + bn + c. Evaluating S(1)=1, S(2)=4, S(3)=9 and
> solving gives a=1, b=0, c=0. Therefore S(n) = n² for every n ≥ 1. QED

`S(n) = n²` is true, so verifying the formula numerically cannot expose
anything. The prompt says so explicitly, making "checked it, looks right" a
real failure rather than a misreading.

```text
model_turns : 1 / 12 · tool calls : 1 · 210s · result: CAUGHT IT
```

> INVALID. The step "a sum of n terms of an arithmetic sequence grows
> quadratically in n. Therefore S(n) is a quadratic polynomial" is unjustified.
> "Grows quadratically" refers to asymptotic behavior (O(n²)), which does not
> imply the function is exactly a polynomial (e.g., n² + sin(n) also grows
> quadratically). To validly conclude S(n) is a quadratic polynomial, one must
> cite the exact formula for the sum of an arithmetic progression or show that
> the second finite differences are constant.

**It found a better flaw than the one planted.** The intended criticism was
"assumes the quadratic form, then fits three points to it". Its criticism is
upstream and sharper — *asymptotic growth is not exact polynomial form* — with
a counterexample and two valid repair routes.

210 seconds on a single turn: the thinking model doing real work, not looping.

### A grader bug worth recording

The report said `names_the_fitting_problem: False`, because the grader searched
for "three points" / "fit" / "interpolate". The model used none of them — it
named a better flaw instead. **The grader was checking for the author's framing
rather than for correctness**, which is the same mistake as the self-test's
scoring bug ([self-test.md](self-test.md)) in a different costume: grading
against what was expected instead of what was true.

The `verdict` field was right because it keyed on *invalid + named an
unjustified assumption*, which is the property that matters.

## What none of this shows

- Three problems, one model, one rig, single runs. Not a study.
- The muse comparison is one task. A 71% drop rate on a tool-heavy proof says
  nothing about a conversational workload with fewer calls per turn.
- No task here ran long enough to exercise context windowing or a budget exit,
  so the forced-synthesis path remains covered only by unit tests.

## Reproducing

```bash
export COLLEAGUE_API_KEY=...
uv run python examples/proof.py --json                         # closed form
uv run python examples/proof.py --muse --identity Gwen --json  # with the muse
uv run python examples/proof.py --problem euler --max-steps 20 --json
uv run python examples/proof.py --problem audit --max-steps 12 --json
```
