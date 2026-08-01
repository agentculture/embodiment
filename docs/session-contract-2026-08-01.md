# Session contract — 2026-08-01

**What we expect to have when this session is done.** Written at the start of
execution, before the results are in, so it can be *held against* us rather
than written to match whatever we happened to produce. That is the whole point:
a contract authored after the fact is a summary wearing a contract's clothes.

The lane is the `error-derived-timeouts-bee-hive-architecture` cycle —
[spec](specs/2026-08-01-error-derived-timeouts-bee-hive-architecture.md),
[plan](plans/2026-08-01-error-derived-timeouts-bee-hive-architecture.md), 15
tasks in 6 waves, seeded from operator issues
[#42](https://github.com/agentculture/embodiment/issues/42),
[#44](https://github.com/agentculture/embodiment/issues/44) and
[#45](https://github.com/agentculture/embodiment/issues/45).

## The five things this session owes

### 1. No timeout in this repository can censor evidence again

Not "timeouts are longer." **Derived, cited, and enforced by a test that goes
red when someone drifts below the bound.** The rule is
`timeout >= max_tokens / slowest_measured_rate`, the rate lives in a dated
committed config rather than a literal, and the fan-out *wait* deadline is
covered too — it was the one #42's own audit missed.

**Met when:** `test_timeout_bounds.py` walks every constant, recomputes from
committed inputs, and a test-of-the-test proves it can fail. **Not met if** the
constants are simply raised and nothing enforces them.

### 2. The Bee-Hive is measured, not just designed

Arm **B** (worker as a *tool* — no loop, no turn, no goal) and arm **P** (the
cortex compiles a policy once and the policy plays) exist as harnesses with
hermetic tests, and the width rung — the one the last ladder never reached, and
the only axis where orchestration must win by construction — is pre-registered
and dialled.

**Met when:** a verdict is published — separation, `CEILING`, or
`INCONCLUSIVE` — with its decision rule quoted beside it. **An `INCONCLUSIVE`
published honestly is a met contract; a verdict quietly not run is not.**

### 3. Authored intelligence becomes reusable

`/drone create | evoke | list`: a named, committed, legible artifact that
repeats a recurring task for tens of worker tokens and zero cortex turns —
smoke-proven before save, opt-in and off, staleness surfaced before it misleads,
every evocation leaving a content-hashed record.

**Met when:** a second evocation makes **0** cortex calls at **≤5%** of the
authoring token cost. **Not met if** `create` can save something that has never
run.

### 4. The live series is never damaged by this work

A measured, pre-registered series is climbing its ladder in a parallel worktree
throughout this session. Every interaction with it goes through the amendment
protocol — appended and dated, never edited into registered sections — plus a
recorded deviation.

**Met when:** the series' branch history shows no mid-series instrument change
outside that protocol, and no published cell is silently re-graded.

### 5. The findings travel

Colleague dials the same cortex through their own transport and would hit the
timeout defect identically. lobes owns the streaming advert that could retire
whole-request clocks altogether
([lobes-cli#168](https://github.com/agentculture/lobes-cli/issues/168)).
Neither learns by a push into their repo — both by a tracked issue with the
measured evidence attached.

**Met when:** the negatives travel too. A findings post that reports only what
worked is not this contract met.

## How we will know we kept it

The delivery summary at the end resolves **every** planned task to one of three
states, and only these three:

| State | What it means |
|---|---|
| **delivered** | an openable artifact path, and the acceptance criteria actually checked |
| **ABSENT** | honestly not done, with the reason named |
| **deviated** | changed mid-run, with a recorded `dN` and an issue documenting it |

There is no fourth state, and in particular there is no "substantially done."
The previous cycle's summary recorded **27 of 27** with three uncomfortable
facts stated in the opening rather than buried — that is the bar.

## The standing rules this session runs under

These are not new; they are the repo's, restated because a contract that omits
its own constraints is easy to keep.

- **The measured failure mode never ships as default behaviour** — and its
  mirror: an *unmeasured* one does not ship as default either. That is why the
  drone skill ships opt-in and off while #44's experiment is unrun.
- **Degradation is observable to the host** (C3). A thing that fails silently
  is worse than a thing that fails.
- **Verify a drift entry against current upstream before acting on it.** This
  repo has asserted an upstream bug that did not exist.
- **An environmental explanation blames nothing you own, which is exactly why
  it needs the same evidentiary bar as any other claim.**
- **Well-shaped is not runnable.** A saved artifact that has never executed is
  a trap with a friendly name.

## What we are explicitly not promising

- **Not** that the Bee-Hive wins. The experiment may return `INCONCLUSIVE` or
  refute the design, and either is a kept contract.
- **Not** that streaming lands fully — it depends on a sibling's advert and on
  what this rig's server actually emits, which cannot be probed while the series
  holds the cortex.
- **Not** that every wave completes if the running series' schedule forbids it.
  A task blocked by an honest scheduling constraint is reported `ABSENT` with
  the constraint named, never quietly dropped.
- **Not** that no unknown unknowns remain. The challenge pass raised the odds of
  finding blind spots and lowered the cost of the ones that survive; it did not
  eliminate them, and no pass can.

## Closing condition

The session is done when the delivery summary is written, the PR is open with
**zero SonarCloud issues** and **every Qodo comment triaged**, and this document
has been checked line by line against what actually shipped — with any clause we
failed to keep marked as failed, in place, rather than edited to match the
outcome.
