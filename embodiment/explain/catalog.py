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
- **Two traps in the configuration lane** (embodiment#79, the embodiment#62
  shape — a correct-looking wiring produces a tier that proposes nothing, with
  no error anywhere). **T1:** a review boundary is a *tool-step* boundary, so a
  drive whose actor answers in one turn without calling a tool reviews nothing
  while `governor.armed` reads True; read `counts["boundaries_projected"]`, not
  `outcome.applied`, when asking whether the tier is alive. (T2, a cadence
  memory that outlived the per-drive step index, was the second — it is FIXED
  and off the list.) All seven remaining gaps:
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

## The four safeguards — all on

1. **Opt-in, and off.** `evoke` runs nothing unless
   `EMBODIMENT_DRONES_ENABLED=1` is set (or a host passes `opt_in=` through the
   library). The design is unvalidated — issue #44's experiment has not run —
   and an unmeasured behaviour does not ship on by default, the same rule that
   keeps a *measured* failure mode out of the defaults.
2. **Staleness.** `list` re-checks each drone's declared `assumed_surface`, so
   a stale drone is visible **without being executed** — you learn it while
   choosing a drone, not after it has reported. `evoke` **refuses to run** a
   stale drone (`--stale-ok` overrides). The failure mode is not a crash: a
   drone whose assumptions expired keeps passing, authoritatively, on a check
   that no longer means anything.
3. **The audit trail.** Every evocation — answers, "I cannot", refusals and
   harness failures alike — appends the drone name, the **sha256 of the bytes
   that ran**, the declared capability set and per-call acceptance to
   `<drones dir>/.evocations.jsonl`. There is no sandbox, so the record is the
   containment story: it is traceability, not tamper-proofing.
4. **No escalation.** An undecidable case returns **"I cannot"**. There is no
   escalate-to-cortex path in v1 — that is the whole point of the cost model,
   and it is what keeps *a drone's second evocation makes zero cortex calls*
   exact, with no exception clause.

## Statuses `list` can report

| status | meaning |
|--------|---------|
| `ok` | every declared assumption was re-checked and holds |
| `stale` | an assumption failed; `evoke` refuses to run it |
| `unverifiable` | nothing found wrong and nothing confirmed right: a kind this
  build cannot check, or a drone declaring no surface at all |
| `unchecked` | no check ran, reported as no check ran |
| `broken` | the manifest could not be read or did not validate |

`unverifiable` is deliberately not `ok`: absence of a check never renders as a
pass.

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

`schema`, `name`, `author.date`, `source_sha256` and the smoke result are
filled in by `create`.

## `assumed_surface` is what makes the drone honest later

It is the *only* thing a staleness check has to re-check, so declare what the
drone actually depends on. Three kinds are re-checkable today:

| kind | holds when |
|------|------------|
| `path` | `<root>/<value>` still exists |
| `glob` | at least one file still matches `<value>` |
| `contains` | the file at `<value>` still contains `text` |

`contains` is the one that catches a **convention** moving:

    {"kind": "contains", "value": "embodiment/cli/__init__.py",
     "text": "def register(", "note": "verbs register through this"}

The file still existing proves nothing there — which is exactly the failure a
`path` assumption would sail straight past. Any other `kind` is reported
`unverifiable` rather than `ok`; a drone that declares nothing checkable cannot
detect its own obsolescence, and `list` says so.

`create` does **not** run these checks: authoring is where the surface is
declared, and re-checking a claim against the moment it was made proves nothing
about later.
"""

_DRONE_EVOKE = """\
# embodiment drone evoke

Run a saved drone. This is the cheap half: code plus tens of tokens, no
authoring turn, and no cortex call.

## Usage

    EMBODIMENT_DRONES_ENABLED=1 embodiment drone evoke <name>
    EMBODIMENT_DRONES_ENABLED=1 embodiment drone evoke <name> --arg path=embodiment/cli --json
    EMBODIMENT_DRONES_ENABLED=1 embodiment drone evoke <name> --answers answers.json

## Two refusals before anything runs

- **Drones are opt-in and OFF.** Without `EMBODIMENT_DRONES_ENABLED=1` (or a
  host passing `opt_in=` through the library) this exits `1` having executed
  nothing. The design is unvalidated, and an unmeasured behaviour does not ship
  on by default.
- **A stale drone refuses rather than reports.** If the drone's declared
  `assumed_surface` no longer holds, `evoke` exits `1` naming the assumption
  that broke. Re-author it, or pass `--stale-ok` and read its answer knowing an
  assumption it depends on is false.

Both refusals still write an evocation record.

## Flags

- `--arg KEY=VALUE` — repeatable; reaches the drone as `request.args`.
- `--answers` — JSON map of question id to answer, for a scripted or CI run
  with no live worker.
- `--stale-ok` — run despite a failed assumed-surface check.
- `--drones-dir`, `--json` — as elsewhere.

## The evocation record

Every run appends one JSON line to `<drones dir>/.evocations.jsonl` — including
refusals and failures. Each line carries the drone `name`, the `source_sha256`
of the bytes that actually ran, `source_matches_manifest` (did the code change
since it was reviewed?), the declared `capabilities`, every scoped call with
its acceptance, the `outcome`, and `opt_in` — who authorised the run. `ran`
says whether model-written code executed at all; both refusals report `false`.

A run that leaves no record is a bug, and a test asserts it for every outcome.
If the ledger cannot be written the run still completes and says so on stderr —
point `EMBODIMENT_DRONE_LEDGER` at a writable path.

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
    import-graph      maps imports for a package, flags cycles    12d ago    ok
    find-callers      lists call sites for a symbol              41d ago    stale
                      └ assumed surface no longer holds —
                        path 'embodiment/senses.py': no longer exists

- **name / does** — discovery: the one-line description is required at create.
- **authored** — provenance: a drone is model-written code that will run on
  someone else's checkout, so its age is part of reading its output honestly.
- **status** — the re-checked verdict: `ok`, `stale`, `unverifiable`,
  `unchecked` or `broken`. A stale or broken row still renders, with the reason
  beneath it and on stderr — a drone you cannot see is one you re-author.

## Staleness is checked here, and this is deliberate

`list` re-runs each drone's declared `assumed_surface` against the repo. That
puts the verdict at the moment you are **choosing** a drone rather than after
one has run and reported confidently on a surface that moved — and it means a
stale drone is visible **without being executed**. Nothing here runs any
drone's code; `list` only reads manifests.

A drone marked `stale` is refused by `evoke` (`--stale-ok` overrides).
`unverifiable` means nothing was found wrong and nothing was confirmed right
either — an assumption kind this build cannot re-check, or a drone that
declares no surface at all and therefore cannot detect its own obsolescence.
It never renders as `ok`, and it does not block `evoke`.

The check is a pluggable seam, so a host can supply its own:
`catalog(drones_dir, status_fn=surface_status_fn(root))` is what this verb
does; with no `status_fn` every row reads `unchecked`, because no check ran.
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
