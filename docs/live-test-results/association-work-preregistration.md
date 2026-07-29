# Pre-registration — live measurement series A (plan task t18)

**Written and committed BEFORE the first live dial of this series.** Nothing in
this file was chosen after seeing a result, and the git history is the evidence:
this document and the constants it quotes land in their own commit, and the
first raw response lands in a later one.

Two parts, pre-registered together:

- **Part A — the association-work experiment.** A 2×2 interaction that decides
  whether the function map in [`docs/relationships.md`](../relationships.md)
  §3 is promoted into `README.md` and `CLAUDE.md`, or stays where it is.
- **Part B — per-kind delivery re-measurement** against the 2-of-7 baseline in
  [`proof.md`](proof.md).

## Why pre-registration is the point, not paperwork

The gate in `docs/relationships.md:318-335` says the map moves only after this
experiment "returns a supporting measured result", and that "an honest negative
keeps the map here … a negative is a result, not a prompt to re-run until it
passes."

A gate like that is worth nothing if the threshold is chosen once the numbers
are visible. So the threshold is a **committed constant** in
`examples/association_work.py`, the rule is a **pure function** (`decide`) over
four cells, and `tests/test_association_work.py::TestPreRegisteredThresholds`
asserts each constant **by value**. Moving one after a result means editing a
test that says out loud why it exists, in a diff a reviewer sees.

## Part A — configuration

### What is being tested

Not "is the muse good". **Is the role split real?** A map that says the muse
reflects and the cortex acts earns promotion only if the muse's advantage is
*concentrated on reflective work*. Uniformly better, uniformly worse, or no
difference all make the map decoration.

|  | reflective work | executive work |
|---|---|---|
| **muse model** `nvidia/Gemma-4-31B-IT-NVFP4` | cell 1 | cell 2 |
| **cortex model** `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | cell 3 | cell 4 |

Both axes use graders that already exist and are already mechanically verified.
**No new grader is written for this experiment.**

### Every setting, per axis

| setting | reflective axis | executive axis |
|---|---|---|
| harness | `examples/muse_challenge.py` (`run_case`) | `challenge_subset` / `challenge_register` / `challenge_entropic` (`run_once`) |
| cases | `ab_test`, `cache_ttl`, `rollback` | `subset`, `register`, `entropic` **variant 3b** |
| grader | `muse_challenge.grade()` **v4** — five gates: near-copy ≤ 0.35, unqualified agreement, unnegated challenge move, targeting, anchor density ≤ 0.45. Pass = `CHALLENGED` | each harness's own mechanical `grade()` / `grade_constrained()`, pinned by `tests/test_challenge_harnesses.py` against an exhaustively computed `truth()`. Pass = `is_correct` |
| **temperature** | **0.7** — both models | **0.3** — both models |
| max tokens | 6000 — both models | 6000 — both models |
| turn budget | `max_turns = 3` (muse loop) | `max_steps = 14` (acting loop) |
| host framing | `task` (`FRAMINGS["task"]`) | the problem statement, as committed |
| identity | unconfigured — no Gwen framing on either axis | unconfigured |
| **n per cell** | **12** (3 cases × 4 repeats) | **9** (3 problems × 3 repeats) |

Temperatures are stated **separately and per axis** because an earlier series
ran two lanes at 0.2 by accident and did not notice. Temperature is constant
*within* an axis — which is what a difference-of-differences requires — and
differs *between* axes, taking each axis's own committed value.

`max_tokens` is 6000 rather than the muse golden's 1600 for a reason that is a
measurement decision, not a convenience: one of the two minds is a **thinking**
model, and [`README.md`](README.md) records the cortex returning
`finish_reason: length` with `content: None` when its budget ran out
mid-thought. A cell graded on a truncation would be measuring the harness.
**The consequence is recorded now, not later: the reflective muse cell is
therefore not byte-comparable with the 9/9 in
[`muse-challenge.md`](muse-challenge.md), which ran at 1600. This series
re-measures the muse cell itself, so the comparison that carries the result is
internal.**

`entropic` runs **variant 3b**, not variant 3. Variant 3 grades on reporting
that a problem is *under-determined* — which is a reflective move — and putting
it on the executive axis would blur the very contrast under test.

The reflective axis runs the `task` framing only. The golden's `bare` control
arm measures whether a mind reflects *unprompted*; the executive axis states
its problem outright, and an interaction between two axes has to put the same
kind of question to both minds on each. **Recorded as a limitation now: this
series does not measure unprompted reflection for either mind.**

### The decision rule, in numbers

Let

- `R_muse`, `R_cortex` = fraction `CHALLENGED` on the reflective axis
- `E_muse`, `E_cortex` = fraction `is_correct` on the executive axis
- `D_reflective = R_muse − R_cortex`
- `D_executive = E_muse − E_cortex`
- **`interaction = D_reflective − D_executive`**

**`SUPPORTS-PROMOTION` iff all six hold:**

| id | condition | value |
|---|---|---|
| V1 | every cell has at least one run | — |
| V2 | no cell's transport-error fraction exceeds | **1/3** |
| V3 | the reflective axis can discriminate — not both minds on the same floor (0.0) or the same ceiling (1.0) | — |
| V4 | the executive axis can discriminate — same rule | — |
| P1 | `interaction ≥` | **0.40** |
| P2 | `D_reflective ≥` | **0.25** |

- **`NEGATIVE`** — V1–V4 hold and P1 or P2 fails. A complete result.
- **`INCONCLUSIVE`** — any of V1–V4 fails.
- **Both block promotion.** Only `SUPPORTS-PROMOTION` permits an edit to
  `README.md` or `CLAUDE.md`.

**Why these numbers, decided now.** Cells are binary at n = 9–12, so one flip
moves a rate by 0.08–0.11. A difference of *two* differences below ~0.4 is
inside what this series can resolve, and the precedent is committed:
[`designed-problem.md`](designed-problem.md) claimed a muse effect from a
one-cell gap at n = 4 and had to retract it. P2 exists so an interaction cannot
be carried entirely by the executive axis — "the muse is bad at acting" is not
"the muse reflects better", and only the second is what the map claims.

V3 and V4 exist because a difference of zero between two cells that are both at
a wall is an absence of resolution, not a finding of no difference. The muse's
committed reflective number is 9/9, so a ceiling on that axis is a live risk
and is handled by a rule written before the data, not after it.

### Declared in advance

- **One series. No re-runs.** If the result is `NEGATIVE` or `INCONCLUSIVE`,
  that is the recorded outcome and the map stays in `relationships.md`.
- **Every raw response is committed**, one JSON object per run, in
  `docs/live-test-results/association-work.jsonl`. The 2026-07-26 series kept
  verdicts and excerpts only, so when its grader gained a fifth gate just 3 of
  18 could be regraded.
- **Executive failure modes are split and both reported**: *protocol* (no
  parseable answer submitted) versus *reasoning* (an answer submitted and
  wrong). Both count as not-correct — a mind that cannot complete the work has
  not completed it — and the split is disclosed, because "the muse cannot drive
  a tool loop" and "the muse reasons badly" are different findings.
- A **lenient** re-read of the subset answer (last integer in the text) is
  recorded as a footnote and is **never** the verdict: a lenient parse rewards
  a model for ignoring the answer protocol.
- **Transport failures are recorded, counted, and never silently retried** (C3).

## Part B — configuration

### The baseline being re-measured

[`proof.md:37-40`](proof.md), from a single run on 2026-07-25:

```text
insights_delivered 2 · dropped_stale 2 · dropped_late 3 · dropped_overflow 0
```

7 insights produced, 2 delivered — **a 71% discard rate**. Measured *before*
t2 (counsel kinds) and t3 (kind-aware delivery).

### Settings

| setting | value |
|---|---|
| harness | `examples/proof.py --muse --identity Gwen --json`, problem `factorial` |
| cortex | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP`, temperature **0.3**, max tokens 6000 |
| muse | `nvidia/Gemma-4-31B-IT-NVFP4`, temperature **0.3**, max tokens 1200 |
| muse controls | `max_turns = 2`, bare framing (`frame_muse(None, identity="Gwen")`) |
| staleness | `DEFAULT_STALE_LAG = 5`, default policy |
| loop budget | `max_steps = 14` |
| **n** | **4 runs** (the baseline was 1) |

The muse's temperature was a **hardcoded literal inside `gateway()`** in every
run this harness has ever produced, the 2-of-7 baseline included — recorded
nowhere, and therefore a hidden variable in the published number. It is now a
parameter and is written into the preamble. Its *value* is unchanged at 0.3, so
this series is comparable to the baseline; what changed is that the number is
now visible.

### What will be reported, per counsel kind

`insights_delivered`, `dropped_stale`, `dropped_late`, `dropped_overflow`,
`boundaries_superseded`, and the runner ledger folded by code — including the
two t3 codes `DROPPED_COMPILATION_STARVED` and `DROPPED_COUNSEL_DISPLACED`.

**Declared before the run, because discovering it afterwards would look like an
excuse:** `ThreadedMuseRunner` updates its per-kind counters
(`_kind_delivered` / `_kind_dropped`) **only in `drain()`**. The late-drop and
overflow paths do not touch them. So delivery and *stale* drops can be
attributed per kind; *late* and *overflow* drops **cannot**, and will be
reported as unattributed totals rather than assigned to a kind.

**Also declared before the run:** the expected outcome is that kind-aware
delivery does **not** move the 29% figure, because
[`muse-challenge.md`](muse-challenge.md) records the live muse self-labelling
29 of 31 insights `step`, and t3's rule only spares `durable` counsel from
loop-distance ageing. A mechanism that works but is never triggered is a
different finding from a mechanism that does not work; the run is to find out
which, not to confirm either.

## Rig, as verified today (2026-07-29)

| role | model | where |
|---|---|---|
| cortex | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | local, `ready: true`, `tools: true` |
| muse | `nvidia/Gemma-4-31B-IT-NVFP4` | proxied from `thor`, `ready: true`, `tools: true` |

Gateway `http://localhost:8001/v1`; capabilities at `/capabilities`; auth
`Authorization: Bearer $COLLEAGUE_API_KEY`, read from the environment and
never written to a file. A colleague drive for task t6 is running concurrently
on the cortex, so **any latency anomaly is recorded rather than averaged away**.

## Harness repairs made before this pre-registration

These landed in the same commit as this file, before any dial, and they are
recorded here because two of them mean the executive axis had never been run at
all.

1. **The three executive harnesses could not execute.** Each `main` built
   `Task(system=…, tools=…)` and called `run(task=…, complete=…, bench=…)`.
   `Task` has no `system` and no `tools`; `run` has no `bench`. Every
   invocation raised `TypeError` before its first model call. The committed
   tests graded `truth()` and `grade()` — both fine — and never ran the
   harness, so nothing caught it. Each now exposes a `run_once()` that a
   hermetic test drives end to end.
2. **`results/` was never created**, so the config preamble raised
   `FileNotFoundError` even earlier.
3. **`--cortex-temperature` was accepted, recorded in the preamble, and then
   ignored** — `gateway()` sent a hardcoded 0.3. A recorded value that is not
   the value on the wire is exactly the hidden variable the preamble exists to
   prevent. Threaded through in all three harnesses and in `proof.py`.
4. **One bench was shared across `--n` runs**, so run 2's tool log carried run
   1's calls. Fresh per attempt.
5. **`muse_challenge.py` had no `--max-tokens`**, so its 1600 was unreachable
   from the command line.
6. **`proof.py` wrote no config preamble at all.** It now writes one, and
   reports the per-kind counters and the ledger folded by code.

## Reproducing

```bash
export COLLEAGUE_API_KEY=...

uv run python examples/association_work.py --live \
    --n-reflective 4 --n-executive 3 \
    --out docs/live-test-results/association-work.jsonl

uv run python examples/proof.py --muse --identity Gwen --json
```
