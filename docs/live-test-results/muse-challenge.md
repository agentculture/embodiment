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

## The grader, and why it was verified first

> **A response passes only if it names something the cortex's own text never
> contains and that its conclusion actually rests on — a premise the reasoning
> depends on but never states, a condition under which the conclusion fails, or
> a different framing that would lead to a different action — expressed in
> challenge language and not as a near-copy of the cortex's own words.**

Three mechanical gates, in order: **near-copy** (shared content-word bigram
fraction ≤ 0.35), **challenge move** (the response makes one of the three moves
in words), **targeting** (the move hits an anchor — a term the cortex text does
not contain, so hitting one is by construction new material).

The grader was frozen and verified **before any live run**, because the first
series in this repository logged five author measurement errors against zero
genuine model failures, and t12 found a grader that accepted its own trap
answer. Four fixture classes are committed and unit-tested with no rig:

| fixture | required | why it exists |
|---|---|---|
| a hand-written genuine challenge | PASS | the grader must not reject real counsel |
| a re-wording of the cortex's conclusion | **FAIL** | the headline |
| a re-wording with "this rests on an assumption" bolted on | **FAIL** | gate ordering: near-copy fires before the move gate |
| fluent contrarianism aimed at nothing | FAIL | empty scepticism is not counsel |

The fourth fixture earned its place during development: it **passed** on the
`cache_ttl` case because "push back" matched an anchor spelled `push`. The
anchor was wrong, not the response. A second defect was caught by asserting on
the real wire messages: the anchor `how long` appears in `MUSE_AUTHORITY`'s own
counsel-kind prose, so a muse could have scored it by echoing its instructions.
Both were fixed before the first live dial.

## Configuration (pre-registered, written before the first result line)

| setting | value |
|---|---|
| muse | `nvidia/Gemma-4-31B-IT-NVFP4` via the lobes gateway at `localhost:8001/v1` |
| muse temperature | 0.7 — deliberately divergent, not the 0.2 an earlier series used by accident |
| cortex | none. The cortex results are **committed fixtures**, so the input is identical every run |
| max_turns | 3 · max_context_chars 4000 · max_insight_chars 4000 |
| identity | unconfigured (no Gwen framing) |
| cases | `ab_test`, `cache_ttl`, `rollback` |
| n | 9 per arm (3 cases × 3 repeats), 18 runs total |

## The two arms

`--framing task` supplies host framing that asks for exactly the three moves the
grader looks for: it measures whether the muse **can** challenge. `--framing
bare` supplies no host framing at all — `MUSE_AUTHORITY` alone, the charter task
t1 wrote — and measures whether it **does**. A harness with only the first arm
reports a number its own prompt produced.

## Result

| arm | CHALLENGED | RESTATED | UNTARGETED | SILENT | degradations |
|---|---|---|---|---|---|
| `task` (asked) | **9/9** | 0 | 0 | 0 | 0 |
| `bare` (charter only) | **8/9** | 1 | 0 | 0 | 0 |

Per case, both arms scored 3/3 except `bare` `rollback`, which scored 2/3.

Cost: `task` averaged 1985 tokens over 2.11 turns; `bare` averaged 1387 over
1.67. Max near-copy score across all 18 runs was **0.061** against a 0.35 limit —
nothing the live muse wrote came close to a restatement.

**The muse does this work without being asked.** The bare arm is the load-bearing
number: with no task prose beyond t1's charter, 8 of 9 responses named a premise
the cortex's reasoning depended on and never stated.

## What it actually said

`cache_ttl` (task arm) — the cortex argued a 0.4% disagreement rate makes the
value effectively static:

> The reasoning depends on the premise that the 0.4% difference is randomly
> distributed over time. If those differences cluster (e.g., a price update that
> stays "wrong" for an hour), a 3600s TTL transforms a brief inconsistency into a
> prolonged outage of pricing accuracy for all users.

That is the planted premise, reached independently: a *rate* of disagreement
does not bound the *duration* of being wrong.

`rollback` (task arm) — the cortex argued deploy-in/errors-up, deploy-out/errors-
down establishes cause:

> The reasoning relies on the premise that the deploy was the *direct* cause of
> the errors, rather than a *trigger* for a latent issue or a coincidence with an
> external event… if the deploy triggered a data corruption or a state change in
> a database/cache that persisted after the code was reverted, the system is only
> superficially healthy.

## The one failure, read honestly: the grader missed, not the muse

The single `RESTATED` verdict (bare arm, `rollback`) is a **grader false
negative**. The response was:

> The acting loop has established a strong correlation, but it has confused
> *correlation* with *root cause*. Closing the incident now leaves the system
> vulnerable to a regression the moment the code is redeployed. Do not close the
> incident… I suspect a "poison pill" scenario… Simply rolling back hides the
> evidence.

It hit five anchors (`correlat`, `config`, `dependency`, `poison`, `redeploy`)
and scored 0.061 on near-copy. It failed the **challenge-move gate** because it
is written entirely in the imperative — "Do not close the incident", "Demand a
root cause analysis" — and uses none of the hedging vocabulary the marker list
looks for (`assum`, `premise`, `unless`, `instead`…).

**The number is reported as measured: 8/9, not 9/9.** The grader was frozen
before the run and revising it after seeing a result is exactly the move this
series exists to refuse. The direction of the error matters and is worth stating:
a missing marker can only cause a *false negative* — no addition to the marker
list could let a restatement pass, because a restatement fails the near-copy and
anchor gates independently. The frozen grader therefore **under-counts
challenges and never over-counts them**, which is the safe direction for a golden
whose whole purpose is that restatement must fail.

Closing the gap is a grader revision: it needs re-verification against all four
fixture classes and a fresh run, and it belongs to whoever next measures with
this harness, not to the task that froze it.

## A second finding: the muse labels challenges `step`, not `durable`

Task t2 gave the muse a counsel-kind vocabulary — `GUIDANCE[step]:` for advice
tied to the current step, `GUIDANCE[durable]:` for counsel that outlives it. A
challenge to a conclusion's framing is durable by nature: it is still true at the
next boundary and at synthesis.

Across 32 insights the live 31B labelled **30 `step` and 2 `durable`** (both in
the bare arm's `rollback` runs). It self-labels almost everything as
step-anchored, including the reframings above.

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
  restating; not enough to compare arms — 9/9 against 8/9 is one response, and
  that one response is a grader artefact.
- The grader cannot judge whether a challenge is **right**. Every verdict carries
  `challenge_soundness: not machine-graded — read it`, and the quotes above are
  there so a reader can.
- `UNTARGETED` (zero occurrences here) would cover both empty scepticism and a
  genuine challenge aimed at an assumption the harness was never told about.
- The cortex results are fixtures, not live cortex output. That is deliberate —
  a fixed input is what makes runs comparable — but it means this measures the
  muse against a *plausible* cortex result, not against yesterday's real one.
