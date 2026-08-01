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
  and holds final authority) plus an optional Gemma muse — reflective counsel
  that reframes the problem, challenges the cortex's assumptions and offers
  materially different alternatives, with no tools, no decisions and no actions
  of its own. A run with no muse configured says so rather than implying a
  second mind. Absent identity means byte-identical prompts to today's behavior.
  "Cortex" and "muse" name seams — which model gets which job. They are
  design metaphors for allocating responsibility, not claims about cognition.
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
- `embodiment drone create|evoke|list` — author, run and discover drones.

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


_DRONE = """\
# embodiment drone

A **drone** is a named unit that does small-smart tasks — explore, review,
search — as a mix of code and *minor* intelligence: deterministic logic for
structure, traversal and bookkeeping, plus **scoped** calls to a worker where
judgement is genuinely needed.

## Drone or subagent?

A subagent re-derives its approach on every invocation: no drift, full cost
every time. A drone pays the authoring cost **once** and then runs on code plus
tens of tokens: fast, repeatable, and carrying staleness risk.

Authoring costs one cortex turn — measured on this rig at 5,000–14,265
completion tokens and 400–730 s. So a task done **once** is pure loss, **2–3
times** is roughly break-even, and a task done **often on a stable surface**
wins by a widening margin. **A drone is worth creating when the task recurs and
the surface is stable.** The failure mode is quiet waste, not a crash.

## The artifact — committed, not hidden

    .drones/<name>/
      manifest.json    name, one-line description, purpose, author model/date/
                       commit, the assumed surface, and every question it asks
                       the worker with that question's answer schema
      drone.py         the code the cortex wrote; entry point `run(request)`
      README.md        what it does, when it is wrong, how to re-author it

Model-written code that will run on someone else's checkout gets the same
review a script would, so the artifact is legible and diffable where it is used.

## Threat model — stated, never implied by a name

`evoke` **imports and runs model-written Python in the calling process**, with
exactly the permissions that process already has. There is **no sandbox**. The
manifest's `capabilities` list is a *declaration for review*, not an enforcement
boundary — nothing in embodiment restricts what `drone.py` may do. Read
`drone.py` before evoking a drone you did not author. The network-less
workspace jail stays available for a host that wants it; it is not the default.

## Limits of v1

- An undecidable case returns **"I cannot"**. There is no escalate-to-cortex
  path — that is the whole point of the cost model.
- `list`'s `status` column reads `unchecked` until a host wires an
  assumed-surface check. No check ran is reported as no check ran.

## Verbs

    embodiment drone overview
    embodiment drone create <name> --source <drone.py> --description '<one line>'
    embodiment drone evoke <name>
    embodiment drone list

## See also

- `embodiment explain drone create`
- `embodiment explain drone evoke`
- `embodiment explain drone list`
"""

_DRONE_CREATE = """\
# embodiment drone create

Author a drone: stage the three artifacts, run a **smoke invocation against the
staged copy**, and save only on a pass. A drone that fails smoke is never
written to `.drones/` — an unsaved failure is cheap, a saved broken drone is a
trap. (*Well-shaped is not runnable*: a prior task shipped problems with
`statement=""` and `grade=lambda _raw: {}` that reviewed fine for a whole task
while the rung could not be dialled.)

## Usage

    embodiment drone create <name> --source drone.py --description '<one line>'
    embodiment drone create <name> --source drone.py --manifest draft.json --json

## Flags

- `--source` (required) — the `drone.py` the cortex wrote. It must define
  `run(request) -> DroneAnswer`.
- `--description` — the **required** one-line description `list` prints. May
  instead come from the draft manifest; create refuses if neither supplies one.
  A drone nobody can pick from `list` is dead weight that still cost a cortex
  turn.
- `--manifest` — draft manifest JSON supplying `purpose`, `assumed_surface`,
  `capabilities`, `questions` and the `smoke` block.
- `--notes` — markdown appended to the generated README.
- `--author-model` / `--commit` — provenance overrides; both default to this
  agent's model and `git rev-parse HEAD`.
- `--force` — replace an existing drone of the same name.
- `--drones-dir` — where drones live (default `<repo root>/.drones`, or
  `$EMBODIMENT_DRONES_DIR`).

## The smoke invocation

Hermetic — no worker is dialled. Every declared question carries a canned
answer in `smoke.answers`, validated against that question's own declared
schema. `create` refuses when:

- `drone.py` fails to import, or defines no callable `run`;
- `run()` raises;
- `run()` returns neither an answer nor an "I cannot";
- the drone asks a question its manifest never declared;
- a declared question has no canned answer, or one that violates its own schema.

## Manifest shape

    {
      "purpose": "the longer story",
      "assumed_surface": [
        {"kind": "path", "value": "embodiment/cli/_commands/", "note": "…"}
      ],
      "capabilities": ["read_repo"],
      "questions": [
        {"id": "is_cycle_intentional", "prompt": "…",
         "schema": {"type": "string", "enum": ["yes", "no"]}}
      ],
      "smoke": {"args": {}, "answers": {"is_cycle_intentional": "yes"}},
      "when_wrong": "…",
      "reauthor": "…"
    }

`schema`, `name`, `author.date` and the smoke result are filled in by `create`.
"""

_DRONE_EVOKE = """\
# embodiment drone evoke

Run a saved drone. This is the cheap half: code plus tens of tokens, no
authoring turn, and no cortex call.

## Usage

    embodiment drone evoke <name>
    embodiment drone evoke <name> --arg path=embodiment/cli --json
    embodiment drone evoke <name> --answers answers.json

## Flags

- `--arg KEY=VALUE` — repeatable; reaches the drone as `request.args`.
- `--answers` — JSON map of question id to answer, for a scripted or CI run
  with no live worker.
- `--drones-dir`, `--json` — as elsewhere.

## The worker seam

`evoke` does not dial a worker by itself. With no seam wired every scoped
question goes unanswered (recorded as a refused call, with one note on stderr)
and a well-written drone returns **"I cannot"** rather than guessing. A host
injects a real seam through the library:

    from embodiment import drone
    record = drone.invoke(drone.load("find-callers", d), root=root, ask=my_ask)

## What it reports

The answer or the refusal, the drone's provenance (age, authoring model,
commit), and **call acceptance as its own axis** — how many scoped calls were
asked and how many were accepted. Interface failure and task failure must never
share a number.

## Safety

This **imports and runs model-written Python in this process**. There is no
sandbox. See `embodiment explain drone`.
"""

_DRONE_LIST = """\
# embodiment drone list

Not a directory listing. Its job is **discovery** — answering "is there already
a drone for this?" with one cheap command instead of an authoring turn. Nothing
gets reused that nobody can find, and the whole economics assume reuse.

## Usage

    embodiment drone list
    embodiment drone list --json

## Output

    name              does                                       authored   status
    import-graph      maps imports for a package, flags cycles    12d ago    unchecked

- **name / does** — discovery: the one-line description is required at create.
- **authored** — provenance: a drone is model-written code that will run on
  someone else's checkout, so its age is part of reading its output honestly.
- **status** — `unchecked` until a host wires an assumed-surface check;
  `broken` when a drone's manifest cannot be read or does not validate (the row
  still renders, with the reason on stderr — a drone you cannot see is one you
  re-author).

The staleness re-check that turns `unchecked` into `ok` / `STALE` is a
pluggable seam (`catalog(..., status_fn=...)`), not yet wired by the CLI.
"""

_DRONE_OVERVIEW = """\
# embodiment drone overview

Describes the drone surface: the verbs, the break-even economics, the on-disk
artifact, the threat model, and v1's limits. Read-only.

## Usage

    embodiment drone overview
    embodiment drone overview --json

See `embodiment explain drone` for the same ground in prose.
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
    ("drone",): _DRONE,
    ("drone", "overview"): _DRONE_OVERVIEW,
    ("drone", "create"): _DRONE_CREATE,
    ("drone", "evoke"): _DRONE_EVOKE,
    ("drone", "list"): _DRONE_LIST,
}
