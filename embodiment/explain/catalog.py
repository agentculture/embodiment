"""Markdown catalog for ``embodiment explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("embodiment",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

_ROOT = """\
# embodiment

The agentic loop that gives an app an embodied AI presence. Install `embodiment`
in an app or wrap one, supply a **model seam** plus **IO callbacks**, and it
drives the perceive → decide → act loop and the presence pump — extracted from
colleague so the loop is written once and imported, not reimplemented per host.

## Software presence, not a robot body

"Embodiment" is an overloaded word in this mesh: `reachy-mini-cli` owns the
physical robot, `reachy-lobes` its local brain. This package gives an
*application* a loop and a presence — it does not drive hardware, and nothing
here claims a body. If that ever changes it will be a stated goal agreed with
the robot siblings, never an implication drawn from the name.

## Key concepts

- **Loop** — The bounded perceive → decide → act cycle. Handed a `complete`
  callable that performs one model turn, it drives until the model finishes,
  stops requesting tools, or the step budget is reached. Termination is
  guaranteed.
- **Presence** — The pump that keeps the app feeling attended to between acts.
  No TTY, no thread, no clock — all IO rides injected callbacks and cadence is
  step/phase-based.
- **Identity and Gwen** — Configuration that frames who is speaking in prompts.
  Gwen is the reference embodiment: a Qwen cortex holding the acting seat and
  final authority, with an optional strategist above it (see below). Absent
  identity means byte-identical prompts to today's behavior. "Cortex" and
  "strategist" name seams — which model gets which job. They are design
  metaphors for allocating responsibility, not claims about cognition. Where a
  rig runs several roles across model families the phrase for it is a "diverse
  mind entity", and constraint C2 fixes what that may claim: the diversity is
  an *architectural fact* the trace always exposes (actual role, model,
  machine), the prompt-visible identity stays *one* teammate, a single-model
  run never claims a second mind exists, and any inner state relayed outward is
  a report of system state — degradations, budget, work in flight — never an
  affective claim. (The Gemma **muse** held the advisory seat
  until 2026-08-03, when it was archived off the shipped architecture —
  embodiment#53. Its modules stay readable and a host may still wire one.)
- **Strategist** — A tier of authority above the acting loop, **opt-in and off
  by default**, in either of two lanes. **Advisory** (`scope.py`,
  `scoped_run.py`): typed, versioned, supersedable directives owning
  objectives, priorities, constraints and ownership, structurally unable to
  carry a tool, a command or an approval *as data*. **Configuration**
  (`config_*.py`): typed changes to the configuration a seat runs under, with
  nothing delivered to the acting seat at all — the shape it wires is three
  tiers, senses relaying the world in and the inner state out, an acting seat
  driving the loop and unaware of the tier above it, and the cortex configuring
  both and addressing nobody (`examples/three_tier.py`, an example host and not
  a rig default). A run with no strategist
  configured says so rather than implying a second mind, and an unarmed
  governor is byte-identical to an ungoverned run. `tests/test_governance.py`
  holds the default shut. **Both mechanisms are proven; neither lane's value
  is.** Advisory: ScopeBench Stage 1 returned INCONCLUSIVE (216 live calls
  across two arms, 36 episodes each, one rig, one model pair), and in the only
  matched
  governed/ungoverned pair yet run — n=1, one rig, one model pair, a report
  rather than a measurement — the governed arm spent 189.6 s and 3663
  strategist tokens to apply zero directives and returned a materially
  identical answer. Configuration: its value series is pre-registered
  (docs/live-test-results/scopebench-config-preregistration.md, committed
  before any dial) and **no verdict has been published**. See
  docs/live-test-results/scopebench.md and scope-live-session-1.md.
- **The change is deterministic; the effect is not** — the one claim the
  configuration lane must never make. A configuration change is exact: prompt
  bytes, knowledge entries, the set of capability ids a seat may select from.
  What the seat does with it is not — a rewritten prompt still routes through a
  model and the response is still a sample. This is a committed non-goal of the
  design, guarded because "provably never executes" already hardened into an
  overclaim once (deviation `d5`, embodiment#55); a test fails if the word
  appears in the shipped authority text.
- **The authority lattice** — seven targets and three origins, held as data in
  `embodiment.config_change.CHANGE_AUTHORITY`. The strategist may change the
  acting seat's tools, prompts, knowledge and permissions and the senses seat's
  prompts, permissions and knowledge; the acting seat may write exactly one
  target, `senses.knowledge`, and never a prompt — prompt authority over senses
  is the strategist's alone, and a prompt-shaped write from another origin is a
  refused shape, recorded. Knowledge entries carry required origin attribution;
  an unattributed write is refused whole. Tools and permissions changes
  **select among host-declared capability ids and can never mint one**: a
  capability catalog is a host declaration, never a discovery from an executor.
- **Two traps in the configuration lane, both now FIXED** (embodiment#79, the
  embodiment#62 shape — a correct-looking wiring produces a tier that proposes
  nothing, with no error anywhere). **T1:** a review boundary used to be a
  *tool-step* boundary only, so a drive whose actor answered in one turn without
  calling a tool reviewed nothing while `governor.armed` read True; the drive's
  end is now a boundary too, so a conversational host is configured. **T2:** a
  cadence memory outlived the per-drive step index. Both are off the list, and
  `counts["boundaries_projected"]` — at least 1 for any drive that ran with both
  a reviewer and a projector wired — is still the honest liveness check. Note
  the precondition: `armed` never requires a projector, so an armed lane without
  one legitimately projects nothing. All six remaining gaps:
  `python examples/three_tier.py traps`.
- **A directive is delivered text** — `run_scoped` adds no containment of its
  own. A sufficiently credulous actor will act on an operational instruction
  embedded in a directive's prose. Containment is the host's injected
  `ToolExecutor` and its `pre_tool` hook lane, which task t6 proved still
  fully functioning under a governed drive. This is stated rather than left to
  inference, for the same reason the drone tier's threat model is: the package
  does not sandbox anything, and no name here should imply that it does
  (embodiment#55).
- **Continuity** — Memory and coherence wired as runtime subsystems through
  eidetic-cli (recall, provenance, ageing) and coherence-cli (agreement between
  memory and the present). Embodiment owns the lived sequence — when something
  is perceived, considered, acted on, remembered, or revisited.

## Verbs

- `embodiment whoami` — identity probe from `culture.yaml`.
- `embodiment learn` — structured self-teaching prompt.
- `embodiment explain <path>` — markdown docs for any noun/verb.
- `embodiment overview` — descriptive snapshot of the agent.
- `embodiment doctor` — check the agent-identity invariants.
- `embodiment cli overview` — describe the CLI surface.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `embodiment explain whoami`
- `embodiment explain doctor`
"""

_WHOAMI = """\
# embodiment whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    embodiment whoami
    embodiment whoami --json
"""

_LEARN = """\
# embodiment learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    embodiment learn
    embodiment learn --json
"""

_EXPLAIN = """\
# embodiment explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    embodiment explain embodiment
    embodiment explain whoami
    embodiment explain --json <path>
"""

_OVERVIEW = """\
# embodiment overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the template carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    embodiment overview
    embodiment overview --json
"""

_DOCTOR = """\
# embodiment doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`colleague` → `AGENTS.colleague.md`), plus a
skills-present check. Exits 1 when unhealthy.

## Usage

    embodiment doctor
    embodiment doctor --json
"""

_CLI = """\
# embodiment cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    embodiment cli overview
    embodiment cli overview --json
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("embodiment",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
}
