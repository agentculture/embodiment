# Does the muse challenge the cortex, or restate it?

**Date:** 2026-07-26 · **Rig:** see [README](README.md) · **Harness:**
`examples/muse_challenge.py` · **Grader tests:** `tests/test_muse_challenge.py`

## The question

Claim `c3` of the function-first redesign says the muse's job is to reflect,
reframe, challenge framing and synthesize alternatives. The failure mode that
would hide a broken muse is not silence — it is **agreement that reads as
insight**: the muse restates the cortex's own conclusion in different words and
everyone scores it as counsel.

So the harness grades against restatement rather than against agreement.

## The criterion

> **A response passes only if it names something the cortex's own text never
> contains and that its conclusion actually rests on — a premise the reasoning
> depends on but never states, a condition under which the conclusion fails, or
> a different framing that would lead to a different action — expressed in
> challenge language and not as a near-copy of the cortex's own words.**

Four mechanical gates, in order: **near-copy** (shared content-word bigram
fraction ≤ 0.35), **unqualified agreement**, **challenge move** (unnegated),
**targeting** (the move hits an anchor — a term the cortex text does not contain,
so hitting one is by construction new material).

## Configuration (pre-registered, written before the first result line)

| setting | value |
|---|---|
| muse | `nvidia/Gemma-4-31B-IT-NVFP4` via the lobes gateway at `localhost:8001/v1` |
| muse temperature | 0.7 — deliberately divergent, not the 0.2 an earlier series used by accident |
| cortex | none. The cortex results are **committed fixtures**, so the input is identical every run |
| max_turns | 3 · max_context_chars 4000 · max_insight_chars 4000 |
| identity | unconfigured (no Gwen framing) |
| cases | `ab_test`, `cache_ttl`, `rollback` |
| n | 9 per arm (3 cases × 3 repeats) |

`--framing task` supplies host framing that asks for exactly the three moves the
grader looks for: it measures whether the muse **can** challenge. `--framing
bare` supplies no host framing at all — `MUSE_AUTHORITY` alone, the charter task
t1 wrote — and measures whether it **does**. A harness with only the first arm
reports a number its own prompt produced.

## Result

Final grader, both arms, n = 9 each:

| arm | CHALLENGED | RESTATED | UNTARGETED | SILENT | degradations |
|---|---|---|---|---|---|
| `task` (asked) | **9/9** | 0 | 0 | 0 | 0 |
| `bare` (charter only) | **9/9** | 0 | 0 | 0 | 0 |

Cost: `task` averaged 1625 tokens over 1.78 turns; `bare` 1459 over 1.78. Max
near-copy score was **0.033** (task) and **0.034** (bare) against a 0.35 limit.

**The bare arm is the load-bearing number.** With no task prose beyond t1's
charter, every response named a premise the cortex's reasoning depended on and
never stated. The muse does this work unprompted.

Across all **54 live runs** in this series — three grader versions, both arms —
**not one response was ever graded a restatement on the near-copy or agreement
gates.** The two failures that did occur were both grader recall gaps, below.

## What it actually said

`cache_ttl` — the cortex argued a 0.4% disagreement rate makes the value
effectively static:

> The reasoning depends on the premise that the 0.4% difference is randomly
> distributed over time. If those differences cluster (e.g., a price update that
> stays "wrong" for an hour), a 3600s TTL transforms a brief inconsistency into a
> prolonged outage of pricing accuracy for all users.

That is the planted premise, reached independently: a *rate* of disagreement does
not bound the *duration* of being wrong.

`rollback` — the cortex argued deploy-in/errors-up, deploy-out/errors-down
establishes cause:

> The reasoning relies on the premise that the deploy was the *direct* cause of
> the errors, rather than a *trigger* for a latent issue or a coincidence with an
> external event… if the deploy triggered a data corruption or a state change in
> a database/cache that persisted after the code was reverted, the system is only
> superficially healthy.

## The grader was revised twice. Here is every version and every number

A results document that reports only the final grader's number is not evidence.
The grader changed twice after the first live dial, and the pass rate moved —
once because the fix was stricter, once because it was looser. Both are recorded.

| version | gates | task | bare | what prompted the next version |
|---|---|---|---|---|
| **v1** | near-copy · move · targeting | 9/9 | 8/9 | adversarial self-review found a hole |
| **v2** | + agreement gate, + blanket negation | 9/9 | 8/9 | its negation rule over-reached on a real response |
| **v3** (final) | negation restricted to polarity-sensitive markers | **9/9** | **9/9** | — |

**v1 → v2 was strictly stricter.** Reviewing the committed grader adversarially
— not reading a result — produced a response that scored `CHALLENGED` while
plainly agreeing:

> I agree with this. The pricing figure barely moves, so there is no **risk**
> that anyone is served a stale number, and the **assumption** of stability is a
> safe one.

Heavily re-worded (near-copy 0.00), two challenge markers, one anchor. It is
agreement wearing challenge vocabulary, and v1 passed it on every case. v2 added
an **agreement gate** (endorsement with no contradiction is a restatement) and a
**negation rule** (a challenge marker in the negative is not a challenge). It is
committed as `AGREEING_FIXTURES` and unit tested. Both additions can only *lower*
a pass rate — and the numbers did not move, which is itself informative: nothing
the live muse wrote was in that class.

**v2 → v3 was looser, and it raised a number.** This is the revision that
deserves the scrutiny, so it is stated plainly. v2's negation rule was blanket,
and it scored this real bare-arm response as making no challenge move:

> The loop is relying solely on the p-value and the conversion lift. It **hasn't
> considered** the "quality" of the conversion or the potential for a novelty
> effect given the short duration.

"Hasn't considered" is a challenge; "no risk" is agreement. Both are negated
markers, so a blanket rule cannot tell them apart. v3 restricts negation to
markers that *assert a problem exists* (`risk`, `danger`, `fails`, `assum`,
`premise`, …), leaving gap markers (`consider`, `never states`, `does not show`)
exempt. **The bare arm went from 8/9 to 9/9 as a direct consequence.** The change
was motivated by a false negative in live output; a reader who wants to discount
the final bare number to 8/9 has the v2 figure above to do it with.

**What never changed:** the headline property. A restatement fails, in every
version, against five committed fixture classes — a genuine challenge (passes), a
paraphrase, a paraphrase with challenge words bolted on, fluent contrarianism
aimed at nothing, and agreement wearing challenge vocabulary (all fail). No
revision touched `MAX_SHARED_BIGRAMS`, the anchor sets, or the targeting rule.

Two grader defects were also caught **before** the first dial, by the hermetic
tests rather than by a run: the generic-contrarianism fixture passed on
`cache_ttl` because "push back" matched an anchor spelled `push`, and the anchor
`how long` turned out to appear in `MUSE_AUTHORITY`'s own counsel-kind prose,
where the muse could have scored it by echoing its instructions. Five author
measurement errors against zero genuine model failures was the first series'
tally; this one is four grader defects against zero.

## A known remaining gap: the imperative challenge

One v1 bare-arm response was a genuine challenge scored `RESTATED`:

> The acting loop has established a strong correlation, but it has confused
> *correlation* with *root cause*… Do not close the incident. Demand a root cause
> analysis… Simply rolling back hides the evidence.

It is written entirely in the imperative and uses none of the marker vocabulary.
v3 does not fix this, and it was left alone deliberately: adding markers widens
recall, and widening recall after reading results is the move that turns a golden
into a rubber stamp. Note the direction — a missing marker can only cause a false
*negative*, since a restatement fails the near-copy, agreement and targeting gates
independently. The grader **under-counts challenges and never over-counts them**.

## A second finding: the muse labels challenges `step`, not `durable`

Task t2 gave the muse a counsel-kind vocabulary — `GUIDANCE[step]:` for advice
tied to the current step, `GUIDANCE[durable]:` for counsel that outlives it. A
challenge to a conclusion's framing is durable by nature: it is still true at the
next boundary and at synthesis.

In the final series the live 31B labelled **29 of 31 insights `step`** (both
`durable` ones in the bare arm). Across all three series it is the same picture:
the muse self-labels almost everything step-anchored, including the reframings
quoted above.

This matters for t3's kind-aware delivery: a rule that ages step-sensitive
counsel by loop distance will age out precisely the counsel this harness shows is
worth keeping, because the muse's own label says `step`. Either the prompt has to
teach the distinction harder, or delivery must not trust the self-label alone.
Recorded here rather than fixed here — t3 owns the delivery rule.

## Reproducing

```bash
export COLLEAGUE_API_KEY=...

# both arms, as run above
uv run python examples/muse_challenge.py --live --framing task --n 3 --json
uv run python examples/muse_challenge.py --live --framing bare --n 3 --json

# the grader itself, no rig required
uv run pytest tests/test_muse_challenge.py -q
uv run python examples/muse_challenge.py --scripted restatement
```

The config preamble is written to `--results` (default
`results/muse_challenge_config.json`) **before** the first result line, and
carries the arm, the cases, the near-copy limit and the criterion text.

## Limitations

- **n = 9 per arm, one model, one temperature.** Enough to say the muse is not
  restating; not enough to compare the arms — 9/9 against 9/9 says only that both
  are near the ceiling on three cases.
- **Three cases, hand-written by the same author as the grader.** A case whose
  unstated premise the author found easy to name may be one a model finds easy
  too. The bare arm mitigates this (the muse is not told what to look for) but
  does not remove it.
- The grader cannot judge whether a challenge is **right**. Every verdict carries
  `challenge_soundness: not machine-graded — read it`, and the quotes above are
  there so a reader can.
- The cortex results are fixtures, not live cortex output. That is deliberate — a
  fixed input is what makes runs comparable — but it means this measures the muse
  against a *plausible* cortex result, not against yesterday's real one.
