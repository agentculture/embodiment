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
  Gwen is the reference embodiment: a Qwen cortex (the worker that owns the loop
  and holds final authority) plus an optional Gemma muse (an advisory
  subconsciousness that comments and critiques, but never decides and never
  acts). A run with no muse configured says so rather than implying a second
  mind. Absent identity means byte-identical prompts to today's behavior.
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
