# Tool-session latency against the drive tail — the c29 target is reachable

**Date:** 2026-07-30 · **Plan:**
[the muse-cycle plan](../plans/2026-07-29-muse-cycle-pad-headspace-devague-legs.md),
task t16 · **Pre-registration:**
[`muse-latency-preregistration.md`](muse-latency-preregistration.md), committed
before the first measured dial · **Probe:** `examples/muse_latency.py` ·
**Raw:** [`muse-latency-sessions.jsonl`](muse-latency-sessions.jsonl) (32 rows),
[`muse-latency-drives.jsonl`](muse-latency-drives.jsonl) (4 rows)

## Verdict: `KEEP`

Claim `c29`'s zero-late-drop target stands **unchanged**. It is reachable under
the measured tool-use latency profile, and it is not close:

| | measured |
|---|---|
| worst tool-wielding session, time to first insight | **22.09 s** (n=32 sessions, 0 degraded) |
| shortest drive tail | **41.71 s** (n=4 drives) |
| margin at the worst case against the tightest tail | **19.62 s — a factor of 1.89** |
| pre-registered estimator `P_all` / `P_late`, governing arm | **1.00 / 1.00** |
| `KEEP_THRESHOLD` | 0.95 |
| directly observed late drops, 4 live drives | **0, 0, 0, 0** |

The lapse this task exists to close — `l1`, *"the c29 target predates any
measurement of tool-session completion odds"* — is closed with data, and the
data says the original target was not aspirational. **Claim `c31`'s premise
does not reproduce on this rig**, and the reason is structural rather than
lucky; see "Why the race is not close" below.

Two things that verdict does **not** say, both stated here rather than in a
footnote:

1. **The tools-on profile was measured out of drive, not in one.** There is no
   seam to run a tool-wielding muse inside a live drive (below). The odds are an
   estimate combining two measured distributions, never a directly observed
   rate. `c31`'s honesty condition asks for evaluation *"against runs using the
   shipped muse configuration — tools on if default-on ships"*, and this
   measurement does not satisfy that. It bounds the risk; it does not retire it.
2. **Zero late drops is not zero loss.** Every one of the 4 drives delivered
   counsel at the terminal drain that never reached the cortex — 5 of 13 counsel
   lines. That loss is real, is larger than the one `c29` names, and `c29` does
   not cover it. See "The residue c29 does not cover".

## The absent lane, named first

**There is no tools-on-in-drive measurement here, and there cannot be one
today.** `ThreadedMuseRunner.__init__` constructs its `MuseLoop` with no
`tools=` argument, and its own docstring calls the injected seam *"the
**tools-off** thinking seam"*. `MuseToolBench` reaches a session only through
`MuseLoop(tools=…)`, which the runner never passes. So no supported path puts a
tool bench inside a live drive: the muse lane of every drive in this repo is
tools-off by construction, whatever `muse_pad.py` is wired to elsewhere.

Task t16 may not change `embodiment/`, so the lane is reported absent rather
than approximated by reaching into a private attribute. It is also a
prerequisite nobody has scheduled: **task t20 ("flip muse tools default-on")
has nothing to flip until the runner grows that parameter.**

## Lane 1 — the session-latency distribution

n = 8 sessions per arm, 32 total. **Zero degraded sessions, zero failed model
calls, zero harness errors.** Each arm ran 2 simulated drives × 4 boundary
positions (steps 3, 7, 11, 15) cut from one committed transcript, so the same
boundary text reached every arm. Muse `nvidia/Gemma-4-31B-IT-NVFP4` through the
lobes proxy at `localhost:8001`, `temperature` 0.3, `max_tokens` 1200 — the
same wire settings `proof.py` used for the baseline series.

**Time to first insight (`TTFI`)**, seconds — the dependent variable that
decides delivery, because an insight reaches the runner's buffer through the
sink the moment its turn is folded:

| arm | tools used | min | p25 | median | p75 | max | mean |
|---|---|---|---|---|---|---|---|
| `off-2` — tools off, `max_turns` 2 (the baseline series' config) | 0 / 8 | 6.40 | 8.54 | 8.68 | 9.69 | **19.05** | 9.78 |
| `off-4` — tools off, `max_turns` 4 (package default) | 0 / 8 | 7.03 | 8.21 | 8.60 | 9.04 | 10.14 | 8.63 |
| `pad-4` — pad wired, the shipped tools-on config | 3 / 8 | 6.49 | 8.52 | 10.57 | 13.47 | 16.72 | 11.14 |
| `primed-4` — pad wired + priming, **an upper bound** | 8 / 8 | 15.99 | 17.87 | 18.63 | 19.27 | **22.09** | 18.79 |

Total session wall clock, for cost rather than delivery:

| arm | min | median | max | median turns | median completion tokens |
|---|---|---|---|---|---|
| `off-2` | 6.95 | 9.99 | **26.46** | 1 | 129.5 |
| `off-4` | 8.21 | 9.17 | 13.34 | 1 | 121 |
| `pad-4` | 6.49 | 10.57 | 16.72 | 1 | 136 |
| `primed-4` | 16.62 | 18.63 | 22.09 | 4 | 227.5 |

**The slowest single session in the entire series was tools-off**: `off-2`,
sequence 1, boundary step 7 — 26.46 s wall, 19.05 s to first insight, two turns,
no tools anywhere near it. Proxy variance on this rig is larger than the cost of
wiring the pad.

### The tools-on penalty, against what `c31` predicted

`c31` states pad-wielding sessions grew *"5.5s to 30.4s across five turns … about
6x tools-off"*. Measured against the tools-off arm at the same budget (`off-4`):

| comparison | ratio |
|---|---|
| `pad-4` median ÷ `off-4` median | **1.23×** |
| `primed-4` median ÷ `off-4` median | **2.17×** |
| `primed-4` max ÷ `off-4` max | **2.18×** |
| `c31`'s stated figure | ~6× |

No arm approached 6×, and the arm that always used its pad — which is the
condition `c31` describes — cost 2.2×.

### Context growth: it does not happen

Mean `TTFI` by boundary step. The premise under `c31` is that a session gets
slower as context accumulates. It does not, in either direction of tooling:

| arm | step 3 | step 7 | step 11 | step 15 |
|---|---|---|---|---|
| `off-2` | 7.57 | 14.37 | 8.62 | 8.57 |
| `off-4` | 7.57 | 9.08 | 9.59 | 8.27 |
| `pad-4` | 15.09 | 13.27 | 7.29 | 8.92 |
| `primed-4` | 16.57 | 20.39 | 18.91 | 19.29 |

The boundary at step 15 carries four times the transcript of the boundary at
step 3 and is not slower at it.

### The shipped tools-on arm barely uses its pad

`pad-4` — the pad wired exactly as `muse_pad.py` documents, with
`MUSE_PAD_PROTOCOL` as the host framing and no other prompting — put tool calls
on the wire in **3 of 8 sessions**, 6 tool rounds in total. Five of eight
sessions were one turn, zero rounds, indistinguishable from tools-off.

That failed the pre-registered validity gate V3
(`MIN_TOOL_EXERCISE_FRACTION` = 0.5; measured 0.375), so the pre-registered
fallback took over and `primed-4` became the governing arm. **The fallback makes
the verdict stricter, not weaker**: `KEEP` is declared on the arm that always
used its pad and was the slowest of the four, so it holds a fortiori for the
arm that would actually ship.

This is also a finding in its own right, and it is a different failure from the
one `muse_pad.py`'s docstring records (*"five intents, zero observations, zero
conclusions"* — the muse using the pad badly). Here it did not reach for the pad
at all. When it did reach for it, in either arm, it used the protocol correctly.
Pad counts read off the measured sessions (cumulative per sequence, since the
pad persists across a sequence's four boundaries, which is how it behaves in a
drive):

| arm | sequence | final `intend` / `observe` / `conclude` | open intents |
|---|---|---|---|
| `pad-4` | 0 | 2 / 1 / 0 | **1** |
| `pad-4` | 1 | 1 / 1 / 1 | 0 |
| `primed-4` | 0 | 4 / 4 / 4 | 0 |
| `primed-4` | 1 | 4 / 4 / 4 | 0 |

Zero rejected calls and zero off-protocol calls across all 16 tools-on sessions
— `finish` was never hallucinated at the boundary that refuses it. Every
`primed-4` sequence wrote strict `intend → observe → conclude` triples, so the
stacked-intent failure appeared exactly once in this series (`pad-4` sequence 0,
one intent left open). These are `t18`'s dependent variables and are reported
here only because the sessions produced them; this task grades latency, not
counsel.

`tool_choice` was not used to force the issue, and could not have been: checked
against this rig before the pre-registration was written,
`tool_choice: "required"` returned `finish_reason: "tool_calls"` with a `null`
tool-call list, and a named-function `tool_choice` returned ordinary content.
Recorded so it is not rediscovered.

## Lane 2 — the drive tail

n = 4 live drives of the `proof.py` factorial harness, `--identity Gwen`,
`max_steps` 14, `MuseControls(max_turns=2)`, cortex
`sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` local, muse proxied — the baseline
series' own instrument, imported unchanged. All four exited `finished`.

| drive | model turns | tool calls | elapsed | muse sessions | **tail** | last session | landed in tail | late drops |
|---|---|---|---|---|---|---|---|---|
| 0 | 4 | 16 | 194.8 s | 4 | **146.98 s** | 8.65 s | yes | 0 |
| 1 | 4 | 16 | 152.6 s | 4 | **107.57 s** | 11.21 s | yes | 0 |
| 2 | 3 | 9 | 123.8 s | 3 | **61.13 s** | 16.15 s | yes | 0 |
| 3 | 4 | 14 | 88.7 s | 4 | **41.71 s** | 11.05 s | yes | 0 |

Tail distribution: min 41.71, p25 61.13, median 84.35, p75 107.57, max 146.98,
mean 89.35 seconds.

The **tail** is the interval from the last muse session's start to the terminal
beat firing — the window that session actually had. It was timestamped through
public seams only: the muse endpoint is wrapped (a session's opening call is the
one carrying exactly two messages, which is all `_build_messages` ever returns),
and the presence sink is a forwarding proxy, which works because
`loop._presence_terminal` probes `on_terminal_boundary` by name.

### Delivery accounting, against the baseline

The same four-run shape the baseline froze, at the same problem and budget:

| | baseline (`delivery-per-kind.md`, n=4) | this series (n=4) |
|---|---|---|
| insights produced | 16 | 16 |
| delivered | 10 (62.5%) | **13 (81.25%)** |
| dropped stale | 2 | 3 |
| **dropped late** | **4 — one per run, 4 of 4 runs** | **0 — none, 4 of 4 runs** |
| dropped overflow | 0 | 0 |
| terminal drains | — (the beat did not exist) | 4 of 4, carrying 1 / 1 / 2 / 1 |

This is a **directly observed** result, not an estimate, and it is not this
task's work: tasks `t4` (the terminal boundary drains without considering) and
`t25` (the terminal beat fires at drive end on every exit reason) are what moved
it. Lane 2 measures it because the tail cannot be measured without running the
drives, and reporting the late-drop count it fell out of would be perverse.

## The odds

The pre-registered estimator, over every (session, tail) pair:

| arm | `P_all` (8 × 4 = 32 pairs) | `P_late` (4 × 4 = 16 pairs) |
|---|---|---|
| `off-2` | 1.00 | 1.00 |
| `off-4` | 1.00 | 1.00 |
| `pad-4` | 1.00 | 1.00 |
| `primed-4` (governing) | 1.00 | 1.00 |

Every validity gate held for the governing arm: V1 (4 usable drives ≥ 4),
V2 (8 sessions with an insight ≥ 6), V3 (tool-exercise fraction 1.00 ≥ 0.50),
V4 (degraded fraction 0.00 ≤ 0.33).

**`DV-CHECK` — the estimator against an observation.** Applied to `off-2`, the
arm matching lane 2's own configuration, the estimator predicts **0.0** late
drops across the four-drive series. The drives observed **0**. The estimator and
the ground truth agree. That is a weak check — both numbers are at a floor, so
it can only fail loudly, never pass informatively — and it is reported as such.

## Why the race is not close — the structural reason

A 1.89× worst-case margin invites the question *"what would make it tighter?"*,
and the honest answer is that the shipped seam bounds both axes the growth
premise needs:

- **The prompt is capped.** `_render_boundary` renders at most
  `_MAX_HISTORY_ENTRIES = 6` history entries, each clipped to
  `max_context_chars` (600), with every other boundary field capped the same
  way. A boundary late in a drive costs what an early one costs — which is
  exactly what the by-step table above shows, and why "context accumulates"
  does not describe this lane.
- **The turn budget is a hard ceiling that tool rounds spend from, not beside.**
  `_tool_ceiling` draws `min(ctx.budget, ctx.turns + max_tool_rounds)`, so a
  round can only ever make a turn end sooner. At the package default
  `max_turns = 4` a session is at most four model calls **including** its tool
  rounds. The 5.5 → 30.4 s growth `c31` cites is growth *across five turns of one
  session*; under the shipped controls there is no fifth turn.
- **The two minds are an order of magnitude apart in cost.** These drives spent
  88.7–194.8 s on 3–4 cortex turns — roughly 30–50 s of actor time per turn,
  tools included — against 4.5–16.1 s for a whole muse session. The tail is
  measured in actor turns; the session is measured in muse turns; the actor's
  unit is the expensive one. That asymmetry, not tuning, is what makes the
  window generous.

Anything that removes one of those bounds — a larger `max_turns`, an uncapped
history, a muse model as slow as the cortex, or a tool whose result is a
network call rather than a list append — puts the target back in play. The
margin belongs to the configuration, not to the design.

## The residue `c29` does not cover

`c29` counts *late drops*. It does not count counsel that was drained on time and
still never reached the acting mind, and this series measured that loss
directly:

| drive | counsel lines appended | reached the cortex | never reached it |
|---|---|---|---|
| 0 | 2 | 1 | 1 |
| 1 | 3 | 2 | 1 |
| 2 | 4 | 2 | 2 |
| 3 | 4 | 3 | 1 |
| **total** | **13** | **8** | **5** |

The five undelivered lines are **exactly** the terminal drains' output
(1 + 1 + 2 + 1 = 5). Every drive exited via a clean `finish`, and a clean finish
has no later completion for buffered guidance to ride —
`proof.py`'s `GuidanceRelay` docstring anticipates this in prose; here it is a
number. So the terminal drain, which is what took the late-drop rate to zero,
delivers to the operator's transcript and the run's records and **not to the
cortex**, on every clean-finish drive.

That is 5 of 13 counsel lines — a larger loss than the 4 of 16 the cycle set out
to fix — and no code in `c29`'s vocabulary names it, because from the runner's
point of view nothing went wrong. It is reported here as an open finding, not
folded into the verdict.

## Limitations

- **n = 4 drives, one problem, one rig, one model pair.** All four drives exited
  `finished` on the same `factorial` task; a longer or messier workload is
  unmeasured, exactly as `muse-cycle-baseline.md` warned about its own four runs.
- **n = 8 sessions per arm, and they are a sequence, not replicates.** Each arm's
  eight sessions are 2 simulated drives × 4 boundary positions against one fixed
  transcript. The boundary axis is deliberate; independence across the eight is
  not claimed.
- **The tools-on odds are an estimate.** They rest on the assumption the
  pre-registration names and this document repeats: the tail is actor-determined
  (deviation `d1` — the actor never waits; only the cortex is local on this rig),
  so a tools-off drive measures the tail a tools-on drive would have had. Under
  that assumption the arithmetic is sound; the assumption itself is untested.
- **`primed-4` is not a shipping configuration.** It appends a pre-registered
  sentence instructing the muse to use the pad. It is an upper bound on the cost
  of tool use, declared before the dial, and must never be quoted as the shipped
  profile's latency.
- **One harness defect, fixed before any row was committed.** The first lane-2
  attempt crashed after drive 0 while serialising `snapshot()["deliveries"]`,
  which is already plain dicts. No rows were written, the bug was fixed, and the
  four-drive series ran from scratch. It is recorded because a series that only
  reports what worked is not evidence.
- **The instrument checks are not measured cells.** The `tool_choice` probe and
  four smoke sessions run while building the probe are declared under the
  pre-registration's rule 6 and are excluded from every n above.
- **Nothing here says the counsel was any good.** This measures when counsel
  arrives, never whether it was worth arriving. That is tasks `t17` / `t18`.

## What this changes

- **`c29`: no change.** The late-drop target stays *zero per run across a live
  series of at least four drives*. It was fixed from tools-off baselines and it
  survives the tool-use profile with a 1.89× margin at the worst measured case.
- **`c31`: its premise is contradicted on this rig, and its honesty condition is
  still unmet.** The ~6× figure did not reproduce (measured 1.2× shipped,
  2.2× primed), and the growth-with-context mechanism it rests on does not occur
  under the shipped caps. But its honesty condition — evaluate the target against
  runs using the shipped muse configuration, tools on — cannot be met until a
  tool bench can reach a runner.
- **`l1` is closed by measurement, and a narrower gap replaces it:** the target
  no longer predates a latency measurement; it now predates an *in-drive* one.

## Reproducing

```bash
export COLLEAGUE_API_KEY=...

uv run python examples/muse_latency.py --lane sessions \
    --out docs/live-test-results/muse-latency-sessions.jsonl
uv run python examples/muse_latency.py --lane drives \
    --out docs/live-test-results/muse-latency-drives.jsonl
uv run python examples/muse_latency.py --analyse \
    --sessions docs/live-test-results/muse-latency-sessions.jsonl \
    --drives docs/live-test-results/muse-latency-drives.jsonl
```
