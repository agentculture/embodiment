"""Markdown catalog for ``embodiment explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("embodiment",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

_ROOT = """\
# embodiment

A small, tested core for giving an app an embodied AI presence — a bounded
perceive → decide → act **loop**, a **presence** pump, a verbatim **perception**
seam, configured **identity**, and **continuity** through eidetic — that is being
rebuilt into a background realtime voice app. The experiments that used to live
here are archived in git history (tag `archive/pre-realtime-0.14.0`).

## Status, stated plainly

**The realtime app is planned, not built.** Nothing in this package listens,
speaks, runs as a daemon or serves a dashboard yet. What ships today is the core
listed below and this CLI's introspection verbs. The spec and the plan are in
`docs/specs/` and `docs/plans/` (2026-09-21, "realtime embodiment app").

The archived tiers (strategist, configuration lane, muse, drones) were each
proven as mechanisms and never as value; their own published verdicts said so.
The redesign makes no value claim either, until it has a measurement.

## Software presence, not a robot body

"Embodiment" is an overloaded word in this mesh: `reachy-mini-cli` owns the
physical robot (and has its own `agent embody` layer), `reachy-lobes` its local
brain. This package gives an *application* a loop and a presence — it does not
drive hardware, and nothing here claims a body. A later stage lets a robot act
as a relay for the voice app's ears and voice; that is a stated, planned seam,
never an implication drawn from the name.

## Key concepts

- **Loop** (`embodiment.loop`) — The bounded perceive → decide → act cycle.
  Handed a `complete` callable that performs one model turn, it drives until
  the model finishes, stops requesting tools, or the step budget is reached.
  Termination is proved structurally by AST tests, not only behaviourally.
  Delegation rides the `embodiment.subagent` seam, bounded by arithmetic.
- **Presence** (`embodiment.presence_engine`, `embodiment.presence`) — The pump
  that keeps an app feeling attended to between acts, and its pure policy half.
  No TTY, no thread, no clock: all IO rides injected callbacks.
- **Perception** (`embodiment.perception`) — The verbatim invariant: the user's
  words are taken from the caller's input, never from model output, and intake
  never raises.
- **Identity and Gwen** (`embodiment.identity`, `embodiment.framing`) — Explicit
  configuration that frames who is speaking. Gwen is the teammate this repo
  ships. Absent identity means byte-identical prompts. Role names are design
  metaphors for allocating responsibility across model seams, not claims about
  cognition, and a single-model run never claims a second mind exists.
- **Continuity** (`embodiment.continuity`) — Memory and coherence as runtime
  subsystems through eidetic-cli and coherence-cli, imported in-process. Every
  degradation is recorded; nothing degrades silently.
- **Events** (`embodiment.events`) — An optional observer that publishes loop
  and presence events onto events-cli's MQTT fabric; free when unused.

## Verbs

- `embodiment whoami` — identity probe from `culture.yaml`.
- `embodiment learn` — structured self-teaching prompt.
- `embodiment explain <path>` — markdown docs for any noun/verb.
- `embodiment overview` — descriptive snapshot of the agent.
- `embodiment doctor` — check the agent-identity invariants.
- `embodiment cli overview` — describe the CLI surface.

No verb drives the loop or starts anything. `start` / `stop` / `status` arrive
with the daemon.

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
