# The announcement checklist

The announcement embodiment ships on:

> embodiment ships colleague's bounded tool loop as a reusable package — one
> loop, usable as a library or CLI — where Qwen is the working cortex and an
> optional Gemma 4 31B muse injects guidance and critique as a subconsciousness;
> configured identity makes it Gwen per colleague#352 (byte-identical prompts
> when absent), and an eidetic/coherence adapter makes its presence continuous
> across sessions with every degradation observable.

Every clause of it is tied to a test that backs it, so "is that true today?" is
answered by **running something** rather than by reading prose.

## Run it

```bash
uv run python -m tests.announcement_checklist            # resolve only — hermetic, ~0.1s
uv run python -m tests.announcement_checklist --run      # execute every cited test
uv run python -m tests.announcement_checklist --online   # also probe GitHub through `gh`
uv run python -m tests.announcement_checklist --json     # the same report, machine-readable
```

Exit codes: `0` everything cited resolves (and passed, under `--run`); `1`
something is missing, failed, or has drifted; `2` the checklist could not run
at all (wrong `--root`).

The manifest lives in `tests/announcement_checklist.py`; `tests/test_announcement.py`
guards it, so **CI already enforces this file** through the ordinary suite — no
extra workflow step is needed. The `--run` mode exists for a human before a
release, when you want the backing tests executed *as this checklist groups
them* rather than as one undifferentiated suite.

## The clauses

| ID | Clause | Backed by |
|----|--------|-----------|
| `a1` | ships colleague's bounded tool loop as a reusable package | `test_loop.py` termination matrix + import posture, `test_no_shell_host.py`, `test_zero_deps.py` |
| `a2` | one loop, usable as a library or CLI | `test_package_surface.py`, `test_demo_greenhouse.py` (a host CLI), `test_cli.py` |
| `a3` | Qwen is the working cortex | `test_muse_runner.py` explicit configuration, `test_framing.py` cortex framing |
| `a4` | an optional Gemma 4 31B muse … as a subconsciousness | `test_muse_runner.py` museless default + authority boundary + degradation, `test_presence_engine.py` museless beats |
| `a5` | configured identity makes it Gwen (byte-identical when absent) | `test_framing.py` golden against the real seams + the structural pass-through guards |
| `a6` | an eidetic/coherence adapter makes presence continuous across sessions | `test_demo_greenhouse.py` two-process continuity + the empty-store control, `test_lifecycle.py` checkpoints |
| `a7` | with every degradation observable | `test_ledger.py` exhaustive-by-construction enumeration, `test_no_silent_degradation.py` |

The clause quotes **partition** the announcement: consecutive, gap-free apart
from punctuation. A sentence added to the announcement with no clause behind it
fails the build.

## How it fails

Citations are pytest node ids (`tests/test_loop.py::TestTerminationMatrix::test_…`)
resolved against the real test files by an AST walk before anything runs.

- **Rename or delete a backing test** → that clause reports `MISSING`, the
  report names the citation that rotted, and the exit code is `1`. It is never
  quietly reported as still-checked.
- **Reword the announcement** → the partition check fails.
- **Reword the frame's confirmed claim** (`c45` in
  `.devague/frames/gwen-loop-presence-continuity.json`) → the provenance line
  reports `DRIFTED`.
- **Fix something a caveat warns about** → that caveat's probe reports `[STALE]`
  and fails, because a report that keeps warning about a fixed problem stops
  being read.

Under `--run`, a clause whose group fails is re-run one node at a time, so the
report names the failing test rather than only the failing clause. If any
citation fails to resolve, **nothing is executed** — running a manifest that has
already drifted would report a comforting half-truth.

## What it does not verify

Three things in the announcement's success signals live outside a hermetic
checkout. They print as **external** on every run, with the exact `gh` command
that would settle them, and they never affect the exit code:

| Signal | Why external |
|--------|--------------|
| `s1` CI is green for the three test families | the families run here, but the CI **job status** lives on GitHub Actions |
| `s3` the seam-proposal issue ([colleague#358](https://github.com/agentculture/colleague/issues/358)) | nothing in this repo can prove an issue exists, let alone that colleague answered it |
| `s3` the [colleague#352](https://github.com/agentculture/colleague/issues/352) framing-divergence comment | same: a link on github.com, checked with `gh` or not at all |

`s2` — the demo app passing in-repo — is the one signal that *is* internal, and
it resolves and runs like any clause.

**`--online` never fakes a verdict.** No `gh`, no auth, or a network failure
reports `unavailable` with the reason. A search that finds nothing reports
`outstanding`. A search that finds candidates reports `reported` — a human still
reads it.

## Caveats it always prints

These are outstanding, and the checklist surfaces them rather than omitting
them. Each one carries a probe that fails if it quietly stops being true.

| ID | What |
|----|------|
| `d4` | **Live testing is a recorded acceptance bar that is NOT met.** The demo has run against the real rig, but the full bar — a museless baseline, a two-mind run with the Gemma 31B muse, and an echo-chamber probe feeding the muse confidently wrong advice — is outstanding. Today's guards stop the muse *seizing* authority, not the cortex *surrendering* it. `TestLiveRig` skips without `EMBODIMENT_LIVE_RIG=1` and `COLLEAGUE_API_KEY`. |
| `api1` | `lifecycle.record_id_for` is not hoisted to the package root. |
| `api2` | `Task.repo_path` is required and colleague-flavoured for a host with no repository. |
| `api3` | `ThreadedMuseRunner.snapshot()["degradations"]` returns dataclasses where sibling ledgers return dicts. |
| `cli1` | The shipped console script carries **no loop-driving verb** — "usable as a library or CLI" means an app builds its CLI on the library (`examples/greenhouse.py` is that host). |
| `t19` | The seam proposal is **filed** (colleague#358) and the decision it asks for is **not made**: colleague cannot adopt embodiment until it answers C1b — three base dependencies, or compose them? That answer is colleague's alone. |

## Provenance — the shipped text is not the frame's text

The frame confirmed claim `c45` with two words the shipped announcement no
longer carries, both because of deviation `d2`:

- *"reusable **stdlib-only** package"* → `eidetic-cli`, `coherence-cli` and
  `events-cli` are base dependencies now; `tests/test_zero_deps.py` became the
  human gate on that set instead of a purity guard.
- *"eidetic/coherence **subprocess** adapter"* → both CLIs are imported
  directly; the planned subprocess boundary is gone.

The checklist reads `c45` out of the frame on every run and reports `DRIFTED` if
it has been reworded since — so this section cannot silently fall behind the
frame it describes.
