---
name: drone
type: command
description: Author, run and discover drones — named units that do small-smart tasks (explore, review, search) as deterministic code plus scoped worker calls. Authoring is prompt-shaped and costs one cortex turn; `evoke` is mechanical and runs on code plus tens of tokens. Use when the user says "create a drone", "make a drone for this", "evoke <name>", "run the <name> drone", "what drones do we have", "is there a drone for this", or when a task you are about to do by hand is one you have now done several times on a surface that is not moving. First-party to agentculture/embodiment.
---

# drone

A **drone** is a named unit that does small-smart tasks — explore, review,
search — as a **mix of code and minor intelligence**: deterministic logic for
structure, traversal and bookkeeping, with **scoped** calls to a worker where
judgement is genuinely needed.

This skill is a thin wrapper over the CLI verbs. The verbs are the substrate on
purpose: authoring is genuinely prompt-shaped (which is why this skill exists),
but `evoke` is mechanical and must be callable by a script or CI **without a
mind in the loop**.

```bash
embodiment drone create <name> --source <drone.py> --description '<one line>' [--manifest draft.json]
EMBODIMENT_DRONES_ENABLED=1 embodiment drone evoke <name> [--arg k=v] [--answers answers.json] [--stale-ok]
embodiment drone list
embodiment drone overview
```

Every verb takes `--json`. Results go to stdout, diagnostics to stderr. See
`embodiment explain drone` (and `drone create` / `drone evoke` / `drone list`)
for the full reference.

**`evoke` is off by default.** Drones ship opt-in: without
`EMBODIMENT_DRONES_ENABLED=1` (or a host passing `opt_in=` through the library)
`evoke` exits `1` having executed nothing. The design is unvalidated —
[#44](https://github.com/agentculture/embodiment/issues/44)'s experiment has not
run — and an unmeasured behaviour does not ship on by default, the same rule
that keeps a *measured* failure mode out of the defaults. `create` needs no
switch: it proves its own candidate runs against a copy staged in a temporary
directory, from source you handed it this second.

## Read this before authoring one

Authoring costs **one cortex turn** — measured on this rig at **5,000–14,265
completion tokens** and **400–730 s**. So:

- a task done **once** → a drone is **pure loss**;
- a task done **2–3 times** → roughly break-even;
- a task done **often, on a stable surface** → the drone wins by a widening
  margin, and by ~9× again if it can fan out.

**A drone is worth creating when the task recurs and the surface is stable.**

The failure mode here is not a crash — it is quietly spending a cortex turn to
save nothing. So before authoring:

1. **Run `embodiment drone list` first.** It is one cheap command, and it is the
   difference between reusing a drone and re-authoring one at a cortex turn a
   time. Nothing gets reused that nobody can find. It also re-checks each
   drone's assumed surface, so you see a drone is **stale** while choosing it —
   `stale` means re-author, not reuse.
2. **Ask whether the surface is stable.** Code written against a codebase
   encodes assumptions that expire. A drone authored against a convention that
   changes next month will keep passing, authoritatively, on a check that no
   longer means anything.
3. **Prefer a pure code-drone.** If the task turns out to need no judgement at
   all, declare zero questions. That is the cheapest possible outcome: zero
   worker tokens, and nothing to drift.

### Drone or subagent?

|              | authoring        | each use                  | drifts?                  |
|--------------|------------------|---------------------------|--------------------------|
| **subagent** | none             | a full mind's turns, every time | no — re-reasons each run |
| **drone**    | one cortex turn  | code + tens of tokens     | **yes — this is the risk** |

A subagent re-derives its approach on every invocation. A drone pays that once
and then runs. The trade is the honest one: you buy speed and repeatability with
staleness risk.

## What `create` actually does

1. Take the name and the task description.
2. **Explore the surface** the task touches — this is the expensive part, and
   the reason a drone is worth more than a script.
3. Decide the split: what compiles into code, what stays a scoped question.
4. Write `drone.py`, and declare every worker question and its answer schema in
   the draft manifest.
5. Hand both to `embodiment drone create`, which **proves it runs** before
   saving.
6. Record the assumed surface, so a staleness check has something to re-check.

Step 5 is not optional and is not yours to skip: `create` stages the artifacts
in a temporary directory, runs the smoke invocation **against the staged copy**,
and moves it into `.drones/` only on a pass. A drone that raises, defines no
`run`, returns neither an answer nor an "I cannot", or asks a question its
manifest never declared is **never saved**. *Well-shaped is not runnable*, and a
saved broken drone is a trap.

## Writing `drone.py`

The entry point is `run(request)`:

```python
def run(request):
    # request.args  — the --arg KEY=VALUE map
    # request.root  — the repo root
    # request.home  — this drone's own directory
    # request.ask   — the scoped worker call
    verdict = request.ask("is_cycle_intentional", {"module": "a"})
    if verdict is None:
        # No worker seam wired, or the answer failed its declared schema.
        # v1 has NO escalation path: refuse cleanly.
        return {"cannot": "no worker answered is_cycle_intentional"}
    return {"answer": f"cycles intentional: {verdict}", "detail": {"asked": 1}}
```

Rules the harness enforces, so you may as well design for them:

- **Every question must be declared** in the manifest with an answer schema.
  An undeclared question fails the run. The efficiency claim rests on the worker
  being asked *narrow typed questions*; a drone that asks "review this file" is
  a slow subagent with extra steps.
- **`request.ask` returns `None`** when there is no usable answer — unavailable
  and schema-refused collapse into one case so you have one branch to write.
  Handle it by returning `cannot`.
- **Return an answer or a refusal.** Returning neither is treated as not having
  run at all.
- **No escalation.** An undecidable case returns "I cannot". There is no
  escalate-to-cortex path in v1 — that is the whole point of the cost model,
  and it is what keeps *a drone's second evocation makes zero cortex calls*
  exact, with no exception clause. `request.ask`, bounded by the manifest's
  declared questions, is a drone's only outward seam; there is nothing else to
  reach for. Whether escalation should exist is #44's question, to be answered
  by a measurement rather than a patch.

## The draft manifest

```json
{
  "purpose": "the longer story: what this is for",
  "assumed_surface": [
    {"kind": "path", "value": "embodiment/cli/_commands/", "note": "verb modules live here"},
    {"kind": "contains", "value": "embodiment/cli/__init__.py", "text": "def register("}
  ],
  "capabilities": ["read_repo"],
  "questions": [
    {"id": "is_cycle_intentional",
     "prompt": "Is this import cycle intentional?",
     "schema": {"type": "string", "enum": ["yes", "no"]}}
  ],
  "smoke": {"args": {}, "answers": {"is_cycle_intentional": "no"}},
  "when_wrong": "optional: when this drone misleads",
  "reauthor": "optional: how to rebuild it"
}
```

`smoke.answers` needs **one canned answer per declared question**, each valid
against that question's own schema. That is what makes the smoke run hermetic —
no worker is dialled — and it proves the schema and the drone agree before a
worker is ever involved.

`schema`, `name`, `author.*`, `source_sha256` and the smoke result are filled in
by `create`.

### `assumed_surface` is the drone's own expiry check — write it properly

It is the only thing the staleness check has a chance of re-checking, and
`list` / `evoke` act on the verdict. Three kinds are checkable:

| kind | holds when |
|------|------------|
| `path` | `<repo root>/<value>` still exists |
| `glob` | at least one file still matches `<value>` |
| `contains` | the file at `<value>` still contains `text` |

**Reach for `contains` when what you depend on is a convention, not a file.**
The failure this whole safeguard exists for is a review drone checking a rule
that changed next month — and the file will still be sitting there, so a `path`
assumption sails straight past it.

Any other `kind` reports `unverifiable`, never `ok`. A drone that declares
nothing checkable cannot detect its own obsolescence, and `list` will say so
every time someone reads it.

## Safety — say this out loud, do not let the name imply a sandbox

`evoke` **imports and runs model-written Python in the calling process**, with
exactly the permissions that process already has. **There is no sandbox.** The
`capabilities` list is a *declaration for review*, not an enforcement boundary.

So: **commit the drone, and review it like a script.** Read `drone.py` before
evoking one you did not author. This is the recorded v1 decision — in-process
under the host's existing approval policy, with every capability declared so
review is possible. The network-less workspace jail stays available for a host
that wants to run a drone under it; it is not the default.

Because there is no sandbox, **the audit trail is the containment story**. Every
evocation — answers, "I cannot", refusals and failures alike — appends one JSON
line to `.drones/.evocations.jsonl`:

- `name`, and `outcome` (`answered` / `cannot` / `failed` / `refused-opt-in` /
  `refused-stale`) — refusal and failure never share a number;
- `source_sha256`, the hash of **the bytes that actually ran**, plus
  `source_matches_manifest` — has the code changed since it was reviewed?
- `ran` — did model-written code execute at all (both refusals: `false`);
- `capabilities`, and `opt_in` — who authorised this run;
- every scoped `call` with its acceptance, and `call_acceptance` as its own
  number. Interface failure and task failure must never share one: #33 measured
  17 of 23 worker calls refused on a *shape* error, and a drone whose calls are
  mostly refused runs to completion and reports confidently on nothing.

This is traceability, not tamper-proofing: anyone who can edit `drone.py` can
edit `manifest.json` beside it. Git history and review are the integrity
boundary. Point `EMBODIMENT_DRONE_LEDGER` elsewhere to keep records outside the
repo.

## Provenance

First-party to [`agentculture/embodiment`](https://github.com/agentculture/embodiment)
— authored here, not vendored from guildmaster. The design is issue
[#45](https://github.com/agentculture/embodiment/issues/45); the library lives
in `embodiment/drone.py` and the verbs in
`embodiment/cli/_commands/drone.py`. Requires `embodiment` on PATH.
