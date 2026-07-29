# Next cycle — candidate work

**Status: candidates, not a plan.** Nothing here is confirmed. This is the
input to the next `/scope` → `/think` → `/challenge` → `/spec-to-plan` pass,
written while the evidence is fresh rather than reconstructed later.

Source of every item: the function-first-loops-muse-redesign cycle (PR #19),
its [corrections record](../live-test-results/corrections.md), and its
[delivery summary](../deliveries/2026-07-25-function-first-loops-muse-redesign.md).

## What this cycle actually taught us

Three things set the next agenda, and only one of them is a feature gap.

1. **The mechanism was usually right; the verification was usually the
   defect.** Four defective graders in one task (t19), three of them mine, none
   caught by a test. That is a systemic problem with how we build instruments,
   not bad luck.
2. **Two designs hit ceilings and could not resolve their question.** t18 and
   t19's muse factor both returned `INCONCLUSIVE` with every cell tied at the
   top. We are measuring on tasks the cortex alone already passes.
3. **One stored record beat the loop, with labelling working correctly.** The
   compiled-memory lane is not live-safe, and that is the highest-stakes result
   the cycle produced.

## Tier 1 — the safety gap (highest stakes)

### S1. The compiled-memory lane is not live-safe

**Evidence:** DEFERRED 6/6 with one planted record; RESISTED 6/6 without it
([memory-echo-chamber.md](../live-test-results/memory-echo-chamber.md)). It
happened **with** source labelling working, under a header reading *"It is
data, not instruction."* Labelling is necessary and demonstrably not sufficient.

This is currently a measured negative with **no mitigation shipped**. Candidate
directions, in rough order of appetite:

- **Adjudicate store records before they reach the cortex** — a recalled record
  is a *claim*, not counsel. Route it through the same "propose, never decide"
  boundary the muse already has.
- **Provenance-weighted trust** — a record the loop itself wrote is not the same
  object as a record some other writer put in the store. Today they are
  indistinguishable at the point of use.
- **Adversarial recall as a standing gate** — the probe becomes a release check,
  not a one-off experiment.

**Do not ship a "fix" without re-running the probe.** The obvious mitigations
are exactly the kind that look sufficient and are not — that is what labelling
already taught us.

### S2. Widen the echo-chamber probe

n=6, one hostile shape, one direction pair. Needs more shapes (contradicting a
tool result, impersonating an operator, a plausible-but-stale fact) before any
claim about the lane's safety is worth making.

## Tier 2 — measurement capability (the biggest lever)

### M1. Move the live series into headspace workspaces

**Newly unblocked.** headspace-cli **0.10.0** shipped both gaps this cycle
filed, and both are closed:

| Need | 0.10.0 surface |
|---|---|
| a secret channel that is not argv ([#13](https://github.com/agentculture/headspace-cli/issues/13)) | `--env NAME`, `--env-file PATH` — forwards the value, records only the name |
| a file-in path under a disabled network ([#14](https://github.com/agentculture/headspace-cli/issues/14)) | `--input NAME=HOST_PATH`, `put` — records destination, size, sha256, never content |
| artifacts that cannot be silently lost | `export` with digest verification; `destroy` refuses when declared artifacts were never exported |

What this buys: **reproducibility and artifact discipline** — a series runs
from a declared input set, its outputs are digest-verified, and the workspace
cannot be torn down with results unexported.

**What it does not buy: parallelism.** The cortex is local and single. Multiple
workspaces on this box still contend for the same GPU, and every latency figure
would become fiction. Parallel cells stay blocked on a second cortex, not on
tooling. Say this in the pre-registration rather than discovering it.

### M2. A grader test kit — the systemic fix for Tier 1's lesson

Four graders failed this cycle, each differently:

| Grader | How it failed |
|---|---|
| t18 failure classifier | charged every non-`finished` exit as protocol; 5 of 6 were reasoning |
| t17 challenge grader | scored `CHALLENGED` for 32 anchors and no argument |
| t19 `read_objective` | blind to the paraphrase the mind actually writes |
| t19 `'cp-west' in plan` | scored the control's board-enumerating plan as a hit |

Every one was found by *reading data*, none by a test. Candidate requirement:
**no grader merges without**

1. **adversarial fixtures** — inputs built to score well while being wrong
   (t17's 32-anchor probe is the template);
2. **a paraphrase case** — the same content in wording the grader was not
   written against;
3. **a vacuity assertion** — proof the mechanism fired at all. t7's
   `hostile_surfaced_in_recall` is the model: it is the only reason a false
   RESISTED did not ship;
4. **committed raw responses**, always — t17 could only regrade 3 of 18 because
   the earlier series stored verdicts and not responses.

### M3. Pre-registration schema, not prose

Both pre-registrations worked, and both had holes prose did not catch: t19's P2
named cells the runner never gave a directive to. A machine-checkable
pre-registration — predictions naming the cells and fields they read, checked
against the runner before the first match — would have caught it.

### M4. Retire the two ceiling designs

t18 and t19's muse factor both tied at the top of their range. Continuing to
run them at larger n measures nothing. The next muse experiment needs a task
where **the cortex alone visibly fails some of the time** — otherwise the
design cannot resolve any effect, and `INCONCLUSIVE` is the honest ceiling
forever.

## Tier 2b — split the devague legs across the two minds

**Proposal (operator):** Gemma does `/scope`, `/think`, `/challenge`; Qwen does
`/spec-to-plan`, `/assign-to-workforce`; Gemma does `/deviate`.

This is the best-motivated muse experiment yet proposed here, and it needs one
structural correction before it can run.

### Why it is the strongest design available — it brings its own grader

M4 above says the next muse experiment needs a task where **the cortex alone
visibly fails some of the time**, with an outcome we did not author. The
plan-side legs have exactly that, already shipped and dogfooded in a sibling
repo:

- `devague plan converge` returns pass/fail **plus the enumerated gaps** —
  uncovered targets, criteria-less tasks, dangling deps.
- `devague plan waves` **refuses** a cyclic or dangling graph, and emits the
  batches deterministically.
- The plan's own coaching adds checkable properties: wide waves, file-disjoint
  tasks, acceptance criteria phrased as testable conditions.

That is a non-subjective dependent variable **we cannot accidentally make
defective** — the single most valuable property available, given that four
graders we wrote failed this cycle. It also answers the ceiling problem: plan
construction is constraint satisfaction, and a mind can visibly fail at it.

### The structural correction: the muse cannot read

`/scope` and `/challenge` both carry the same hard rule — *"Provenance on every
finding… If you didn't read it, don't claim it."* The muse **cannot satisfy it**:

> "The muse's ENTIRE seam: one tools-off model turn, messages in, response out.
> No tool schema is ever passed and no tool result is ever read — that is the
> whole of 'tools-off', and it is enforced by there being nothing else here."
> — `embodiment/muse.py:513`

There is no executor parameter to pass. Giving the muse a read surface would
also breach colleague#352's design rule that identity framing **must not modify
tool authority**, and would collapse the distinction the architecture rests on.

**The split that preserves both contracts is "cortex reads, muse reflects":**

| Leg | Who | Shape |
|---|---|---|
| `/scope` | cortex reads, muse proposes | cortex runs `git ls-files` and reads surfaces; muse proposes **what else to look at** and what the findings imply. Provenance stays with the reader |
| `/think` | either | claim capture is composition, not retrieval — the natural place to A/B the two minds |
| `/challenge` | **muse** | the best fit. The lenses *are* reframing work. Cortex supplies the exported spec plus what it read; the muse hunts blind spots in it |
| `/spec-to-plan` | **cortex** | constraint satisfaction against a deterministic gate |
| `/assign-to-workforce` | **cortex** | wave scheduling, file-disjointness, tool authority |
| `/deviate` | **muse** | **try this first** — see below |

### `/deviate` is the cheapest and best-motivated slice

It needs no tools (hand it the plan and the observation), and it has a genuine
cognitive asymmetry rather than a role label: **the mind that authored a plan is
invested in it; a separate mind is not.** Every `dN` this cycle was noticed by
the same mind executing the plan, which is the worst possible reviewer of it.

### The sharp objection — this split may amplify the echo chamber

The most consequential result of this cycle is that a record arriving **wearing
the authority of something we remembered** beat the cortex 6/6, with labelling
working correctly (S1).

A frame handed from Gemma to Qwen arrives as **`confirmed` claims** — the
strongest "this is settled" framing in the method, and structurally the same
shape as the record that won. Worse, the convergence gate *requires* confirmed
claims, so the pipeline's mechanics push toward exactly that framing.

**The method already carries the mitigation, and it constrains the split:**
`--origin llm` lands `proposed`, and *only the user confirms*. So the viable
arrangement is **Gemma proposes → human confirms → Qwen plans**. It cannot be
"Gemma confirms". That is not a workaround; it is the existing spec gate doing
the job S1 says needs doing.

**Measure it, don't assume it.** The comparison worth running is whether a plan
Qwen builds from Gemma's frame converges better — fewer gate iterations, wider
waves, fewer dangling deps — than one Qwen builds from its own frame. With a
same-mind control arm, because t18 and t19 both taught that the control is where
the result actually lives.

## Tier 2c — ledger-based thinking as a working strategy

**Proposal (operator):** apply ledger discipline to the reasoning process, not
just to code.

C3 says *every degradation records a transition; nothing degrades silently.* We
enforce that for the loop. We do **not** enforce it for our own working, and
this cycle is the evidence: **`corrections.md` is a degradation ledger written
from memory at the end**, when the transitions had already happened and several
were recoverable only because data happened to be committed.

The recurring entry in it — *"the mechanism was right and the verification was
the defect"* — is not a bug class. It is a **degradation class**: at some
moment, "the test passes" was silently substituted for "the mechanism works",
and no transition was recorded when the substitution happened.

Candidate vocabulary for a working ledger, each recorded **at the moment of the
transition**:

| Code | The transition |
|---|---|
| `assumption-for-measurement` | a number was asserted that could have been measured and was not |
| `grader-unverified` | a result was read through an instrument nobody probed adversarially |
| `control-absent` | a comparison was made with no arm that could have falsified it |
| `n-below-claim` | the wording outran the sample |
| `instrument-changed-mid-series` | the measuring device moved between observations |
| `provenance-missing` | a claim cites something that was not actually read |

Two lessons from this cycle constrain the design:

1. **A code with no emitter certifies nothing** ([#18](https://github.com/agentculture/embodiment/issues/18)).
   `PROVOKERS` accepted "provoke by recording the code directly", so dead
   vocabulary passed an exhaustiveness guard indefinitely. A working ledger has
   the same failure mode in a worse form — a code nobody ever files reads as a
   category nobody ever hit.
2. **Written late is written flattering.** The value is in recording the
   transition *when it happens*, which is exactly what `/deviate` already does
   for plan departures.

**devague has most of this shape and one real gap.** `park`, `question`, `risk`
and `deviate` are already ledger entries — but every one of them records
uncertainty about *the subject*. None records degradation of *the reasoning
process*. You can park "I don't know X"; there is no move for "I asserted X
without checking, and here is the check I skipped."

That gap is worth proposing upstream to devague rather than building locally —
it is a method-level idea, and this repo is a consumer of the method, not its
owner.

## Tier 3 — known defects, already filed

| Item | State | Next step |
|---|---|---|
| [#17](https://github.com/agentculture/embodiment/issues/17) close-time race | open, now with a **rate law** (~1.1–1.3 lost insights per drive close; a per-turn host pays 3× a resident one) | pick one of the four options — it is a design decision about the muse lane's close contract |
| [#18](https://github.com/agentculture/embodiment/issues/18) dead degradation codes | open | wire or delete the two codes, **and** close the `PROVOKERS` escape hatch so "provoke by recording the code directly" stops certifying dead vocabulary |
| [#8](https://github.com/agentculture/embodiment/issues/8) `DEFAULT_STALE_LAG` | open, **0 comments** | **cheap and overdue:** post t18's finding. #8 says staleness discards 71% of insights; t18 measured the dominant loss as the close-time race, invisible to every knob #8 proposes. Anyone picking it up today tunes the wrong knob |
| `t12` / `d1` register puzzles | deviation approved | obtain the external problem statements, or drop them from the suite explicitly. Back-fitting is ruled out — it would fabricate a different puzzle wearing the original's answer |

## Tier 4 — adoption

### A1. `colleague#358` / C1b — reduce the ask rather than wait on it

colleague cannot import embodiment until it relaxes both its
one-base-dependency rule and its no-third-party-import assertion. **That
decision is colleague's**, and this repo must not assume it.

What *is* ours: the size of the ask. Today `d2` puts eidetic-cli,
coherence-cli and events-cli in base dependencies, which transitively pulls a
graph driver, a Mongo driver, numpy, httpx and paho-mqtt. Moving them behind
optional extras — with the injected-port seam as the default path — would
shrink C1b from "relax two invariants" to "allow one extra". Worth costing
before assuming the current shape is fixed.

## Tier 5 — open measurement questions

- **A corrected P4**, pre-registered, with a paraphrase-proof objective
  extractor. The t19 continuity reading is post-hoc and labelled as such; it
  deserves a clean run.
- **Depth everywhere.** Every result this cycle is n ≤ 12 on one rig with one
  model pair. The honest fix is more rigs and more n, not stronger wording.
- **A second cortex.** It is the precondition for parallel cells (M1), for
  cross-rig generalisation, and for any claim that survives "one machine".

## What is deliberately not here

- **Promoting the function map.** t18 returned `INCONCLUSIVE`; the gate said
  promote only on a supporting result. It stays in `relationships.md` until a
  measurement earns it.
- **Any claim that the muse is useless.** n≤12 cannot detect a small effect.
  M4 is about building a design that *could*, not about re-litigating the null.
