# Delivery Summary — Gwen: loop, presence, continuity

plan: `gwen-loop-presence-continuity` · run: `complete` · date: `2026-07-25`
baseline: `devague summary skeleton`

## Intent

Extract colleague's bounded tool loop and presence pump into a reusable
package an app can import — one loop, library or CLI — where Qwen is the
working cortex and an optional Gemma 4 31B muse advises without deciding;
configured identity makes it Gwen per colleague#352 with byte-identical prompts
when absent; and an eidetic/coherence seam makes presence continuous across
sessions with every degradation observable.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Carve the data contract into embodiment/contract.py
- `t2` — Zero-deps guard and extras discipline
- `t3` — Port context windowing and media handling
- `t4` — Extract the bounded tool loop with injection points
- `t5` — No-shell host fixture
- `t6` — Port the presence policy verbatim
- `t7` — Redesign the presence engine around cortex + muse
- `t8` — Verbatim perception and never-raise
- `t9` — Degradation ledger
- `t10` — Optional muse seam — the advisory subconsciousness
- `t11` — Resolved-identity seam
- `t12` — Role-framed prompt composition — Gwen per colleague#352
- `t13` — Shared subprocess adapter for eidetic and coherence
- `t14` — Continuity lifecycle checkpoints
- `t15` — Non-colleague demo app
- `t16` — Explain catalog and CLI surface — the software-presence boundary
- `t17` — Relationship docs for app authors
- `t18` — Record the framing divergence on colleague#352
- `t19` — Seam-proposal issue on agentculture/colleague
- `t20` — Integration verification — the announcement checklist

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `embodiment/contract.py` — carved shapes, no colleague import (`2607199`) |
| `t2` | delivered | zero-deps guard; **later reshaped by `d2`** into a human gate that pins the approved set (`b2c62b2`) |
| `t3` | delivered | `context.py` + `media.py` ported with their tests (`34d91c1`) |
| `t4` | delivered | `loop.py` — 4463 → 1615 lines, three exits proved by AST (`4ca649c`) |
| `t5` | delivered | no-shell host fixture; its guard later hardened from text scan to AST (`42c801c`) |
| `t6` | delivered | `presence.py` ported byte-faithfully; env prefix renamed (`8c37ad2`) |
| `t7` | delivered | presence engine redesigned around cortex + muse; clock injected, not stamped (`f750d83`) |
| `t8` | delivered | `perception.py` — verbatim invariant enforced structurally (`3b4f1af`) |
| `t9` | delivered | `ledger.py` — six shapes, seven lanes, enumeration exhaustive by construction (`96d86d3`) |
| `t10` | delivered | split under `d1` into `muse.py` (`71ee1d6`) and `muse_runner.py` (`1f07096`) |
| `t11` | delivered | `identity.py` — resolution order mirrors colleague's (`6ae7324`) |
| `t12` | delivered | `framing.py` — absent identity structurally cannot diverge (`19bc669`) |
| `t13` | delivered | `continuity.py`; **rewritten under `d2`** from subprocess to in-process (`8f575b2`) |
| `t14` | delivered | `lifecycle.py` — three checkpoints; restarted once against d2's API (`42ddec1`) |
| `t15` | delivered | `examples/greenhouse.py`, doubling as the live harness (`0ea7b8a`) |
| `t16` | delivered | explain catalog states the software-presence boundary (`8df8bdf`) |
| `t17` | delivered | `docs/relationships.md` (`f0686f0`) |
| `t18` | delivered | colleague#352 comment `5073964358`, posted before the framing work merged |
| `t19` | delivered | colleague#358 filed; **the decision it asks for is outstanding and colleague's** |
| `t20` | delivered | `tests/announcement_checklist.py` + 118 guarding tests (`ced8d0b`) |

20 of 20 accounted for; 20 delivered.

## Mid-work Decisions

- `d1` (risky) — the muse becomes a second, parallel thinking loop on
  embodiment's own thread, replacing t7's synchronous per-boundary seam.
  Reason: Gemma 4 31B is a thinking model needing iterative turns, and must run
  non-blocking alongside the actor. The operator chose embodiment owning the
  thread over a host-owned one, accepting that threading enters the library
  contract.
- `d2` (risky) — eidetic-cli and coherence-cli become base dependencies
  imported at module scope, replacing the subprocess adapter. Accepted cost:
  `pip install embodiment` now pulls neo4j, pymongo, numpy, httpx, paho-mqtt,
  and colleague's zero-deps test fails until colleague relaxes its rule.
- `d3` (acceptable) — event emission through events-cli lands as new scope,
  neither module nor dependency in the confirmed plan.
- `d4` (needs-follow-up) — live rig testing becomes an acceptance bar beyond
  the in-repo demo and CI checklist.
- `d5` (acceptable) — live self-testing: two instances converse; an instance
  sorts its own memories from another agent's.

Decisions no deviation record covers:

- **t10 was split into t10a/t10b** and **t14 was restarted** on a fresh branch
  after `d2` changed the API beneath it — execution mechanics within d1/d2's
  approved scope, not separate departures.
- **`run()` returns `LoopOutcome`, not `TaskResult`** — forced by t1's carve
  dropping `HookFiring`; those ledgers are the loop's record of its own conduct.
- **`run(continued_from=...)` was added** because the `before-memory` boundary
  fires *inside* `run()`, so a host setting it on the returned result is always
  too late. Found by the t15 demo, which is what a demo is for.
- **The package surface is lazily resolved** (PEP 562) rather than eager or
  bare; `__version__` is lazy because `importlib.metadata` was 18.9ms of a
  19.8ms import.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t10` (`d1`) | Gemma 4 31B is a thinking model needing iterative turns, and must run non-blocking alongside the actor | risky |
| `t13` (`d2`) | direct imports chosen over the subprocess workaround; base dependencies with module-scope imports | risky |
| `t9` (`d3`) | events-cli emission added as new scope once `d2` removed the dependency objection | acceptable |
| `t20` (`d4`) | live rig testing added as an acceptance bar; **the bar was subsequently met** | needs-follow-up |
| `t20` (`d5`) | live self-testing added as new scope | acceptable |
| `t2` | the zero-deps guard's *purpose* changed under `d2` from "prove there are none" to "none arrive without a human deciding" — no record covers this reshaping | acceptable |
| `t19` | filed as planned, but the C1b decision it requests is **outstanding and colleague's alone**; embodiment cannot resolve it | needs-follow-up |

## Evidence

- tests: `uv run pytest -n auto` — **1601 passed, 2 skipped** (live-rig tests
  skip without `EMBODIMENT_LIVE_RIG=1`)
- coverage: **99%** (3451 statements, 30 missed; gate `fail_under=60`)
- lint: `black --check`, `isort --check-only`, `flake8`, `bandit -c
  pyproject.toml` over `embodiment tests examples` — all clean
- markdown: `markdownlint-cli2 "**/*.md" …` (the exact CI invocation) — 0 errors
- rubric: `uv run teken cli doctor . --strict` — PASS
- commits: `main..HEAD` — 76 commits, 80 files, +34,917 / −175
- independent review: `ask-colleague review` on this branch — artifact
  `.colleague/5485cfd4a836.*.json`
- issues filed: embodiment #4, #5, #6, #7, #8, #9, #10, #11; colleague #357,
  #358; colleague#352 comment `5073964358`
- live results: `docs/live-test-results/` (7 documents, 16 recorded runs)

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| The bounded tool loop is extracted and driven only by injected seams | high | `embodiment/loop.py`, commit `4ca649c`; module-scope imports are 5 stdlib + 3 embodiment-internal |
| Termination is proved structurally, not merely tested | high | `tests/test_loop.py::TestTerminationMatrix` — AST tests over `_work_loop`'s returns and raises; verified by mutation (a smuggled fourth return fails) |
| Nothing muse-sourced can reach a tool decision | high | `tests/test_muse_runner.py::TestAuthorityBoundary` — a real drive with a muse demanding `deny-every-write`; the write happens anyway. Independently confirmed by the external review |
| Absent identity yields byte-identical prompts | high | `tests/test_framing.py::TestGoldenAgainstTheRealSeams`; verified by mutation (10 tests fail if `_compose` rebuilds the base) |
| Continuity holds across two real processes against a live model | high | `docs/live-test-results/continuity.md`; run 2 recalled a sensor and threshold named nowhere in its own utterance |
| Every degradation reaches one host-visible ledger | high | `embodiment/ledger.py`; `tests/test_ledger.py` derives expected codes at runtime — adding one fails the suite by name |
| A host with no shell can drive the loop | high | `tests/test_no_shell_host.py` — full drive on a domain-only tool surface; AST guard on the `shell_cli` identifier |
| Dependencies cannot change without a human deciding | high | `tests/test_zero_deps.py`; verified by adding `requests` and reading the failure message |
| The scratchpad repairs the `exit=stopped` protocol failure | medium | `docs/live-test-results/scratchpad.md` — 1/4 → 2/3 per arm, n=3; direction consistent, interval wide |
| A second mind improves outcomes | **unverified** | **Negative result.** 1/4 on both arms at n=4; both arms failed the register puzzles. See `designed-problem.md` |
| The muse improves *process* where it does not improve outcomes | low | one paired run: 824 vs 446 answer chars at half the internal-reasoning ratio; n=1, not replicated |
| colleague can import this loop | **unverified** | colleague#358 open, no response; blocked on the C1b decision, which is colleague's |
| The reference muse rig is validated | **unverified** | `thor-muse` remains declared-UNVALIDATED (lobes#108); the muse answers via gateway proxy, which is not the same claim |

## Remaining Work / Follow-up

- **`t19` / colleague#358** — C1b is unresolved and not embodiment's to resolve.
  After `d2`, colleague cannot import embodiment until it relaxes both its
  one-base-dependency assertion and its no-third-party-import test. Three
  answers are actionable (yes / no / yes-behind-an-extra); silence is not.
- **`d4` follow-up** — the live bar was met (museless baseline, two-mind run,
  echo-chamber probe both directions, self-recognition, non-spiralling
  conversation), but every result is n≤4 on one rig with one model pair.
- **embodiment#8** — `DEFAULT_STALE_LAG = 5` discards 71% of muse insights and
  was guessed under an assumption the measurements inverted.
- **embodiment#5, #6** — a Python execution seam and subagents. The register
  puzzles produced a model stating the correct 60×16 search and stalling for
  want of somewhere to run it, which is the sharpest argument for #5.
- **embodiment#9** — promote the scratchpad/planner/ledger prototypes into
  first-class seams, if the scratchpad's effect replicates.
- **embodiment#10, #11, #12** — the muse's role and embodiment's framing. #10
  and #12 both propose that the muse was measured doing the wrong job.
- **Four public-API rough edges** found by the demo: `record_id_for` unhoisted,
  `Task.repo_path` required and colleague-flavoured, `ThreadedMuseRunner`
  snapshot returning dataclasses where siblings return dicts.
- **Two upstream reports awaiting response** — colleague#357 (the forced
  synthesis budget overrun) and the eidetic `_bridge_env` leak, which is
  handled here but unfixed upstream.

### One methodological finding worth carrying forward

Across 16 recorded live runs, **five measurement errors were made by the
author** — the Euler trap tested recall rather than reasoning; the self-test
graded the retriever as if it were the mind; `steps` was misread as the model-turn
budget; a grader checked for the author's phrasing rather than for correctness;
and a re-verification with identical values was reported as a self-correction —
**against zero genuine model failures**. Each is recorded in
`docs/live-test-results/README.md`. On this evidence the instrument was less
reliable than the thing it measured, and the negative muse result should be read
with that in mind.
