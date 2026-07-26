# Live test results

What happened when embodiment was run against real models rather than fakes.

The test suite (1567 tests) proves embodiment cannot lie, hang, or degrade
silently. It cannot tell you whether the result is any good. These are the runs
that address the second question, recorded as deviations
[`d4`](#deviations) (live testing as an acceptance bar) and `d5` (live
self-testing).

Every number here came from an actual run on the date given. Where a run
contradicted something we believed, the contradiction is recorded rather than
the belief.

## The rig

| Role | Model | Where |
|------|-------|-------|
| cortex | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | local, served on the gateway |
| muse | `nvidia/Gemma-4-31B-IT-NVFP4` | proxied through the same gateway from a peer |

One OpenAI-compatible gateway at `localhost:8001` fronts every role, so
embodiment addresses roles **by name** and never parses a model id. Auth is a
bearer key from `COLLEAGUE_API_KEY`.

Measured on a trivial prompt ("reply with exactly: X"), 2026-07-25:

| | latency | completion tokens |
|---|---|---|
| cortex | 9.3s | 209 |
| muse | 2.6s | 50 |

The cortex is a **thinking model**: it emits a long `reasoning` field before
`content`, which is why a three-word answer costs 209 tokens. The muse answers
directly with `reasoning: null`. So the muse is ~3.5× faster and ~30× cheaper
per answer — the opposite of the assumption the staleness design was built on
(see [proof.md](proof.md)).

**A trap worth knowing:** at `max_tokens=64` the cortex returned
`finish_reason: length` with `content: None`, still mid-thought. A caller can
easily misread that as an empty turn rather than a truncated one. Budget
generously.

## The experiments

| Document | What it tested | Outcome |
|---|---|---|
| [continuity.md](continuity.md) | does run 2 recall run 1 across separate processes | held |
| [muse-and-echo-chamber.md](muse-and-echo-chamber.md) | does an advisory mind help, and can it mislead | helped; did not mislead |
| [self-test.md](self-test.md) | two instances conversing; recognising own memories | converged; recognised |
| [proof.md](proof.md) | a long multi-phase task, with and without the muse | correct; muse more careful, not more correct |
| [designed-problem.md](designed-problem.md) | the embodiment designs a problem; a fresh instance solves it, n=4 per arm | **no measured muse effect** (1/4 both arms) |
| [configurations.md](configurations.md) | every run's full settings, including confounded ones | temperature was a hidden variable throughout |
| [scratchpad.md](scratchpad.md) | does forcing a tool call per step repair the `exit=stopped` collapse | yes on protocol failures (25%→67%), no on capacity ones |
| [muse-challenge.md](muse-challenge.md) | does the muse challenge a cortex result or restate it, n=9 per arm | challenged 9/9 asked **and** 9/9 unasked; zero restatements in 54 runs |

## Reproducing

```bash
export COLLEAGUE_API_KEY=...

# continuity across two processes
uv run python examples/greenhouse.py --home /tmp/gh --reset --live "<plant card>"
uv run python examples/greenhouse.py --home /tmp/gh --live --moisture 22 "Does Marlow need water today?"

# the same, with the advisory lane and Gwen framing
uv run python examples/greenhouse.py --home /tmp/gh2 --reset --live --muse --identity Gwen "<plant card>"

# self-test: conversation, then self-recognition
uv run python examples/selftest.py

# does the muse challenge a cortex result, or restate it (both arms)
uv run python examples/muse_challenge.py --live --framing task --n 3 --json
uv run python examples/muse_challenge.py --live --framing bare --n 3 --json

# long-running proof, with and without the muse
uv run python examples/proof.py --json
uv run python examples/proof.py --muse --identity Gwen --json
uv run python examples/proof.py --problem euler --max-steps 20 --json
```

The hermetic suite never touches any of this: live paths are opt-in and skip
cleanly when the rig or the key is absent.

## Corrections

Two claims made during these runs were wrong and are corrected in place rather
than quietly dropped. Both are recorded because a results document that only
contains successes is not evidence of anything.

1. **"The proof run walked right up to its step budget."** It did not.
   `TaskResult.steps` counts *tool calls* — one `Step` per call — while
   `max_steps` bounds *model turns*. A run showing `steps: 14` against
   `max_steps: 14` had used **6 turns of 14**. See [proof.md](proof.md).
2. **"The muse rescued a failure the solo arm could not complete."** It did
   not. With four runs per arm both scored 1/4 — the original pair was noise.
   Claimed once from n=1, then again from two of three replicates *before the
   third reported*; the third falsified it. See
   [designed-problem.md](designed-problem.md).
3. **"The muse arm self-corrected — the first in the series."** It did not.
   The two entries carry *identical values*; it was a re-verification filed
   under the `revise` tool, and the claim was made from the label without
   reading the text. It also does not reproduce — zero revisions across six
   runs. See [scratchpad.md](scratchpad.md).
4. **"The instance failed to recognise its own memories."** It did not fail;
   the scoring did. The first self-test graded against records that were
   *seeded* rather than records that were *recalled*, so a retriever that
   surfaced 3 of 4 was counted against the mind. See [self-test.md](self-test.md).

## Deviations

- **`d4`** — live rig testing becomes an acceptance bar beyond the in-repo demo
  and the CI checklist. Recorded `needs-follow-up`; the bar is now met.
- **`d5`** — live self-testing: two instances converse, and an instance sorts
  its own memories from another agent's.

Read them with `devague deviate --list`.
