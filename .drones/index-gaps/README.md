# drone: index-gaps

finds results docs missing from the live-test-results index

## What it does

The index is the entry point to docs/live-test-results/, so an unlinked document is a measurement nobody re-reads. This drone walks the directory, parses which files README.md already links, and asks the worker only whether each unlinked file is substantive enough to index — the judgement a set difference cannot make.

## Provenance

- authored by: `unsloth/Qwen3.6-27B-NVFP4`
- authored on: 2026-08-01T12:32:05.875562+00:00
- against commit: `b65016867f01fbc1315e0bfc0e23cfff0f2624af`

## The surface it assumes

- path: `docs/live-test-results/README.md` — the index it diffs against
- contains: `docs/live-test-results/README.md` — the table the index rows live under; a restructure invalidates the parse

## Capabilities it declares

- `read-repo`

## Questions it asks the worker

- `substantive` — A results directory contains this document. Answer with exactly one word: index if it is a substantive results document worth listing in the index, skip if it is a generated sub-artifact or fragment, unclear if you cannot tell.

## When it is wrong

Code written against a codebase encodes assumptions that expire. If the surface above has moved, this drone will keep reporting confidently on a check that no longer means anything — weigh its age against how fast that surface changes.

## How to re-author it

Re-run the authoring turn and `embodiment drone create index-gaps --force`. Authoring costs one cortex turn; re-authoring costs the same, so it is worth doing when the assumed surface moves, not on a schedule.

## Threat model — read this before evoking

`embodiment drone evoke` **imports and runs this model-written Python in the
calling process**, with exactly the permissions that process already has. There
is **no sandbox**. The `capabilities` list in `manifest.json` is a *declaration
for review*, not an enforcement boundary — nothing in embodiment restricts what
`drone.py` may do.

Read `drone.py` before evoking a drone you did not author, exactly as you would
any committed script. The network-less workspace jail
(`embodiment/workspace.py`) is available for a host that wants to run a drone
under it; it is not the default.

Because there is no sandbox, **the audit trail is the containment story**:
every evocation — answers, refusals and failures alike — appends a record
naming this drone, the sha256 of the bytes that actually ran, the declared
capabilities above and the acceptance of every scoped call, to
`.drones/.evocations.jsonl`.

## Turning drones on

Drones are **opt-in and off**. `evoke` refuses to run this drone unless
`EMBODIMENT_DRONES_ENABLED=1` is set (or a host passes `opt_in=` through the
library). The design is unvalidated, and an unmeasured behaviour does not ship
on by default.
