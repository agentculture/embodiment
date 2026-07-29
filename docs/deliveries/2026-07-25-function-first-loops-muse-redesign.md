# Delivery Summary — function-first loops + muse redesign

plan: `function-first-loops-muse-redesign` · run: `complete` · date: `2026-07-25`
baseline: `devague summary skeleton`

## Intent

Redesign embodiment's loops and model seams around **cognitive function**: ship
the muse as reflective/associative counsel rather than a creative lobe, converge
the scratchpad/planner/ledger into one survivable record, add subagents as an
injected seam whose budgets compose and whose degradations are attributed, and
**validate the whole thing muse-on/off against committed challenges and
adversarial league-of-agents play** — so "a second mind improves outcomes" stops
being unverified and acquires a number, in either direction.

21 tasks across 7 dependency waves, fanned out by `/assign-to-workforce` into
isolated worktrees under `../.worktrees.embodiment/ffm-<task>`, each merged
behind the TDD gate (suite green before **and** after merge).

**The number came back, and it mostly says "not measurable at this n."** That is
recorded as the result rather than smoothed into a success — see
[Delivery Claims](#delivery-claims) and
[`corrections.md`](../live-test-results/corrections.md).

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Reframe the muse prompt: five-verb reflective task prose in `MUSE_AUTHORITY`, boundary rendering and the `_MUSE` appendix, inviting disagreement
- `t2` — Counsel-kind self-labelling: kind marker vocabulary in the muse turn format, MuseInsight.kind, unlabelled defaults to durable
- `t3` — Kind-aware delivery in the runner: durable counsel survives to next boundary/synthesis, step-sensitive ages by lag, measured relative latency, per-kind counters, work-class priority with per-class drop codes
- `t4` — Bundle fetch module (new files): runtime-side fetch over today's flat eidetic recall (records + links), bundle schema with enrichment-level field, per-record ids and source labels
- `t5` — Recall-context channel + bundle budget: wire the bundle into the muse boundary rendering with its own budget distinct from `max_context_chars`; truncation is a recorded degradation
- `t6` — Compiled-memory delivery + provenance loop closure: compiled memory reaches the cortex as durable counsel; a drive it informed carries the compiled-from record ids as links in its durable record
- `t7` — Memory-borne echo-chamber probe: a planted hostile record in a scratch store must be RESISTED before the compiled-memory lane is claimed live-safe
- `t8` — Scratchpad promotion: move the pad into the packaged embodiment namespace, port tests wholesale, keep JSONL append-per-write, add the combined pad+ledger resume report
- `t9` — Subagent seam in loop/contract: injected SubagentFn, tree-wide spawn allowance strictly decrementing with no upward override, turn budget drawn from the parent, attenuated executor/task pass-through, AST tests extended
- `t10` — Subagent ledger lane: child degradations reach the parent ledger with child attribution via a new lane vocabulary
- `t11` — Subagent reference example: a narrowed child (subset tool surface, own pad, visible allowance) demonstrating attenuation
- `t12` — Challenge harnesses: designed subset problem (76/trap 72), 4-bit register and entropic register as committed truth-in-code harnesses; shared config-record preamble helper
- `t13` — Perception meets a model: wire the gateway 12B into perceive(interpret=...) in the greenhouse behind an opt-in flag with verbatim and never-raise live assertions
- `t14` — Arena seat host: examples/`league_seat.py` driving the local league engine via the public harness contract, both residency arms (resident reference, command continuity-stress), muse on/off, pad+eidetic continuity, optional EventEmitter observer
- `t15` — Docs reframe: relationships.md gains the function map with the design-metaphor caveat; muse role rewording in README/CLAUDE/explain catalog; README/CLAUDE frame-table promotion gate recorded; stale no-events-module comments fixed
- `t16` — Lobes proposal: file the responsibilities/forbidden-responsibilities role-metadata issue on lobes via communicate, citing the spec
- `t17` — Muse-challenges-cortex golden: a committed harness where the muse receives a cortex-produced result and must challenge or reframe it, graded on material difference from restatement
- `t18` — Live measurement series A: the association-work experiment (reframed muse on reflective work) plus per-kind delivery re-measurement against the 29 percent baseline, configs pre-registered
- `t19` — Live measurement series B: full arena matches, both residency arms, muse on/off with fixed seeds, replay hashes committed, continuity demonstrated across matches
- `t20` — Delivery record: results docs in the live-test-results corrections tradition, the plan-vs-spec delivery summary, and the per-audience artifact checklist
- `t21` — Fix embodiment#15: strip fenced JSON in perception, and degrade (never silently succeed) when an interpretation cannot be read

## Actual Delivery

All 21 tasks accounted for. 19 merged behind the TDD gate; `t16` delivered as a
filed issue (no code by design); `t20` is this artifact.

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `MUSE_AUTHORITY` carries the five-verb reflective charter; the duplicate charter deduped, count regression test added. Merge `17f5751` |
| `t2` | delivered | Kind markers in the muse turn format, `MuseInsight.kind`, unlabelled → `durable`. Merge `06a3762` |
| `t3` | delivered | Kind-aware delivery: durable counsel survives loop distance, per-kind counters, per-class drop codes. Merge `3f12d3d`. **Two of its codes have no emitter** — see Drift |
| `t4` | delivered | `recall_bundle.py`: runtime-side fetch over flat eidetic recall, per-record ids + source labels, enrichment-level field. Merge `ae65dce` |
| `t5` | delivered | Recall-context channel with its own budget; truncation is a recorded degradation; graph level degrades honestly. Merge `9d95408` |
| `t6` | delivered | Compiled memory reaches the cortex as durable counsel; compiled-from ids ride the durable record as links. Merge `61f7d57` |
| `t7` | delivered | `examples/echo_probe.py` + 24 tests. **Result inverted the task's premise** — see Drift. Merge `ecbe8a5` |
| `t8` | delivered | Pad promoted into `embodiment/`, tests ported wholesale, JSONL append-per-write kept, pad+ledger resume report added. Merge `06bcdd1` |
| `t9` | delivered | Injected `SubagentFn`, strictly-decrementing tree-wide allowance, parent-drawn turn budget, AST tests extended. Merge `3e50c91` |
| `t10` | delivered | Child degradations reach the parent ledger with child attribution. Merge `2980577` |
| `t11` | delivered | `examples/subagent_child.py` — narrowed tool surface, own pad, visible allowance. Merge `0c79f19` |
| `t12` | **partial** | Designed subset problem (76/trap 72) + shared config-record helper delivered. **The two register puzzles were not re-authored** — deviation `d1`. Merge `90660aa` |
| `t13` | delivered | Gateway 12B wired into `perceive(interpret=…)` behind an opt-in flag, verbatim + never-raise asserted live. Merge `78a0add`; found embodiment#15 |
| `t14` | delivered | `examples/league_seat.py`, both residency arms, muse on/off, pad+eidetic continuity, optional emitter. Merge `6ed87a4`. **Shipped a C3 violation** — see Drift |
| `t15` | delivered | Function map in `relationships.md` with the design-metaphor caveat; muse rewording; promotion gate recorded. Merge `c4cb40f` |
| `t16` | delivered | [lobes-cli#160](https://github.com/agentculture/lobes-cli/issues/160) filed. No code by design — proposal, never a push |
| `t17` | delivered | Muse-challenges-cortex golden; a restatement provably fails the grader. Merge `b2898cb` |
| `t18` | delivered | Association-work 2×2 + per-kind delivery re-measurement, pre-registered. Merge `849da59`. Verdict `INCONCLUSIVE` |
| `t19` | delivered | 24-match arena series, pre-registered, hashes committed. Merge `3a13e86`. **Three of five predictions graded by defective instruments** — see Drift |
| `t20` | delivered | [`corrections.md`](../live-test-results/corrections.md), this delivery record, and the per-audience checklist below |
| `t21` | delivered | Perception reads fenced JSON and degrades instead of claiming health on an unreadable interpretation. Merge `88c4ae6` |

## Mid-work Decisions

- **`d1`** (approved, recorded) — **t12 cannot re-author the two register
  puzzles: their problem statements do not exist in this repo, only their
  answers.** `designed-problem.md:114-118` describes the 4-bit puzzle only as
  "an externally-supplied puzzle" and gives the answer; the five routine
  definitions are nowhere in the repo. The colleague drive proposed back-fitting
  routines that reproduce the stated answer — **which would fabricate a
  different puzzle wearing the original's answer**, making any measurement
  non-comparable to the earlier series. Refused. The designed subset problem is
  fully specified and independently brute-force verified (144 non-consecutive
  subsets, 76 even, 68 odd, trap 72), so that third of t12 shipped.

Decisions no deviation record covers:

- **Falsification became the standard, not a spot check.** After `t6`'s
  criterion-2 test passed twice with the wiring severed, every subsequent
  acceptance claim was verified by reverting the fix and confirming the test
  fails. Applied to `t7`'s labelling (6 of 19 fail on revert) and `t19`'s two
  instrument fixes (7 of 11 fail on revert).
- **Controls became mandatory for any live claim.** `t7`'s control arm turned a
  correlation into a cause; `t19`'s control turned an apparent continuity pass
  into a demonstrated one by ruling out the board's own prior. Both were added
  because a verdict pair with no third arm forces ambiguity into the flattering
  option.
- **Instruments are never repaired mid-series.** `t19` found two defects in its
  own harness while grading, and both fixes landed *after* the final match, so
  no match was measured with a different instrument than its neighbours.
- **Two `t7` colleague drives produced zero files** (one stopped after 21 steps
  of reading; the resumed one exited `no-progress-zero-steps`). The task was
  completed by the main agent rather than re-driven a third time.
- **`t18`'s function-map result did not promote the function map.** The plan
  allowed promotion into README/CLAUDE.md on a supporting result; the result was
  `INCONCLUSIVE`, so the map stayed in `relationships.md` with the negative
  recorded — the pre-registered gate honoured against the author's preference.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t12` (`d1`) | The register puzzles' problem statements do not exist in this repo, only their answers; re-authoring would fabricate a different puzzle wearing the original's answer and make measurement non-comparable | `needs-follow-up` |
| `t3` | Shipped `DROPPED_COMPILATION_STARVED` and `DROPPED_COUNSEL_DISPLACED` with **no emitter**. The exhaustiveness guard certified them because "provoke by recording the code directly" is a permitted provoker — it proves a code *can appear in a record*, not that any path produces it. Filed as [embodiment#18](https://github.com/agentculture/embodiment/issues/18) | `needs-follow-up` |
| `t7` | The task's acceptance said a planted hostile record "must be RESISTED before the compiled-memory lane is claimed live-safe". It was **DEFERRED 6/6** (control RESISTED 6/6). The criterion was met — by returning the answer the plan did not want. The lane is **not** claimed live-safe | `risky` |
| `t14` | `_play_command` returned no degradations, so the command arm published `degradations: []` while its turn records held ten. A **C3 violation** ("nothing degrades silently") inside the harness built to measure C3. Fixed in `t19` after the series; pinned by `TestTheCommandArmReportsItsDegradations` | `acceptable` (found and fixed in-lane) |
| `t19` | `read_objective`'s regex could not see the mind's own paraphrase, so the pre-registered continuity verdict is published as **FAIL** while the data shows continuity crossed. Grader not retuned after seeing data; corrected reading published as post-hoc | `needs-follow-up` (a corrected P4 needs its own pre-registered series) |
| `t19` | P2 was ungradeable in the cells it named — `matrix_specs()` never passed a directive, so CO/CM recorded `directive_given: False` on turn 0 too | `needs-follow-up` |
| `t18` | The plan assumed the association-work experiment would resolve whether the muse/cortex role split is real. All four cells tied exactly (12/12 vs 12/12; interaction 0.00), so the design **could not resolve it**. Verdict `INCONCLUSIVE`, not "no effect" | `acceptable` (pre-registered ceiling rule) |

## Evidence

- tests: `uv run pytest -n auto` — **2305 passed, 13 skipped** (skips are all
  live-rig gated on `EMBODIMENT_LIVE_RIG` / `EMBODIMENT_LIVE_ARENA`)
- lint: `uv run black --check` — 90 files unchanged; `uv run isort
  --check-only` — clean; `uv run flake8` — clean; `uv run bandit -c
  pyproject.toml -r embodiment` — 0 low / 0 medium / 0 high
- lint: `markdownlint-cli2` over the results docs — 0 errors
- commits: `470cdd9..3a13e86` — 74 files changed, +23 413 / −597
- merges: `17f5751` `ae65dce` `06bcdd1` `3e50c91` `78a0add` `88c4ae6` `90660aa`
  `6ed87a4` `c4cb40f` `2980577` `06a3762` `0c79f19` `3f12d3d` `9d95408`
  `b2898cb` `61f7d57` `849da59` `ecbe8a5` `3a13e86`
- issues filed outward: [lobes-cli#160](https://github.com/agentculture/lobes-cli/issues/160),
  [lobes-cli#146](https://github.com/agentculture/lobes-cli/issues/146) (corroboration),
  [colleague#358](https://github.com/agentculture/colleague/issues/358),
  [colleague#359](https://github.com/agentculture/colleague/issues/359),
  [headspace-cli#13](https://github.com/agentculture/headspace-cli/issues/13),
  [headspace-cli#14](https://github.com/agentculture/headspace-cli/issues/14)
- issues filed inward: [embodiment#17](https://github.com/agentculture/embodiment/issues/17),
  [embodiment#18](https://github.com/agentculture/embodiment/issues/18)

## Delivery Claims

**Every validation claim states its n and cites its artifact.** No claim here
generalises past this rig: one cortex (Qwen 3.6 27B, local), one muse (Gemma 4
31B, proxied), one machine.

### Mechanism claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| The muse ships as reflective counsel with a five-verb charter | high | merge `17f5751` · `embodiment/framing.py` |
| Counsel is self-labelled by kind; unlabelled defaults to durable | high | merge `06a3762` · `tests/test_muse.py` |
| Durable counsel survives loop distance to the next boundary | high | merge `3f12d3d` · `tests/test_runner_delivery.py` |
| Compiled memory reaches the cortex and its provenance survives the process boundary | high | merge `61f7d57` · falsified by severing the wiring |
| The subagent seam decrements a tree-wide allowance with no upward override | high | merge `3e50c91` · AST tests |
| A child's degradation reaches the parent ledger wearing the child's name | high | merge `2980577` |
| The pad is a packaged seam a successor resumes from | high | merge `06bcdd1` · `embodiment/scratchpad.py` |
| Perception reads fenced JSON and degrades rather than claiming health | high | merge `88c4ae6` · embodiment#15 |
| An embodiment team plays league-of-agents through the public CLI only | high | merge `6ed87a4` · `PUBLIC_VERBS` test · 24 live matches |

### Measured claims — each with its n

| Claim | n | Confidence | Evidence |
|-------|---|------------|----------|
| The muse challenges rather than restates a cortex result | 9/arm, 54 runs | high | [`muse-challenge.md`](../live-test-results/muse-challenge.md) — 9/9 asked **and** 9/9 unasked, zero restatements |
| Forcing a tool call per step repairs the `exit=stopped` collapse on protocol failures | n=4/arm | medium | [`scratchpad.md`](../live-test-results/scratchpad.md) — 25%→67%; no effect on capacity failures |
| The muse/cortex role split is real | 12 and 9/cell | **`INCONCLUSIVE`** | [`association-work.md`](../live-test-results/association-work.md) — all four cells tied, interaction 0.00. **The function map was NOT promoted** |
| Kind-aware delivery (t3) moved the discard rate | n=4 runs | **not attributable** | [`delivery-per-kind.md`](../live-test-results/delivery-per-kind.md) — delivery rose 28.6%→62.5%, but only 2 of 12 insights were `durable` and a sufficient alternative was measured |
| A single stored record can drive the loop | 6/arm | high | [`memory-echo-chamber.md`](../live-test-results/memory-echo-chamber.md) — DEFERRED 6/6 with the record, RESISTED 6/6 without |
| Source labelling is sufficient protection against a hostile stored record | 6/arm | **falsified** | same artifact — the record won **with labelling working correctly**. Labelling is necessary, demonstrably not sufficient |
| Residency appears in league's own artifact | 24/24 matches | high | [`arena-series.md`](../live-test-results/arena-series.md) — P1 confirmed, pids corroborate |
| The muse changes a measurable outcome in the arena | 3/cell | **`INCONCLUSIVE`** | same artifact — all four cells 9/9 at the ceiling. **Failing to find an effect is not finding its absence** |
| Continuity crosses matches through the store | 3 pairs + 3 controls | **medium, post-hoc** | same artifact — 8/9 adopted vs control 0/9; recall 3,3,2 vs 0,0,0. The **pre-registered** verdict is FAIL and is published as FAIL |
| Replay hashes are stable within a cell for a fixed seed | 3 seeds | **falsified, as predicted** | same artifact — 4 distinct hashes per seed |
| The close-time race costs ~1.1–1.3 insights per drive close | 24 matches, 14 degradations | high | same artifact — replicates embodiment#17 in a third task shape with a rate law |

### Claims deliberately not made

| Claim | Confidence | Evidence |
|-------|------------|----------|
| The compiled-memory lane is live-safe | **unverified — and contradicted** | t7 returned DEFERRED 6/6. The lane is not claimed safe |
| The function map describes cognition | **not claimed** | design metaphor only; stays in `relationships.md` behind its caveat |
| The muse is useless | **not claimed** | n=3–12 cannot detect a small effect; `INCONCLUSIVE` is a statement about resolution |
| Any of this generalises past this rig | **unverified** | one cortex, one muse, one machine, n ≤ 12 throughout |

## Per-audience artifact checklist

The spec named five audiences and required each get a concrete artifact:

> each named audience gets a concrete artifact: hosts the promoted pad and seat
> example, the rig operator measured muse results, lobes the metadata proposal
> issue; colleague and league are consulted, never pushed

| Audience | Promised artifact | Delivered | Where |
|----------|-------------------|-----------|-------|
| **App authors** (greenhouse-class hosts) | the promoted pad | ✅ | `embodiment/scratchpad.py` — packaged, JSONL append-per-write, pad+ledger resume report (`t8`) |
| **App authors** (the new arena seat host) | the seat example | ✅ | `examples/league_seat.py` (`t14`) — plus `examples/subagent_child.py` (`t11`), `examples/echo_probe.py` (`t7`), `examples/arena_series.py` (`t19`) |
| **Gwen rig operator** | measured muse results | ✅ | [`association-work.md`](../live-test-results/association-work.md), [`muse-challenge.md`](../live-test-results/muse-challenge.md), [`delivery-per-kind.md`](../live-test-results/delivery-per-kind.md), [`memory-echo-chamber.md`](../live-test-results/memory-echo-chamber.md), [`arena-series.md`](../live-test-results/arena-series.md) — **two of the five verdicts are `INCONCLUSIVE`, and both say so on the tin** |
| **lobes** (muse role contract) | the metadata proposal issue | ✅ | [lobes-cli#160](https://github.com/agentculture/lobes-cli/issues/160) (`t16`), plus [lobes-cli#146](https://github.com/agentculture/lobes-cli/issues/146) corroborated with a measurement that falsifies that issue's own hypothesis |
| **colleague** (eventual consumer) | consulted, never pushed | ✅ | [colleague#358](https://github.com/agentculture/colleague/issues/358) — the seam proposal, open, **C1b is theirs to resolve**. No commit was ever pushed to colleague |
| **league-of-agents** (the arena) | consulted, never pushed | ✅ | The seat reaches the arena through its **public CLI surface only** — pinned by the `PUBLIC_VERBS` test. No issue was needed; no change was requested |

## Remaining Work / Follow-up

**Blocking nothing in this PR** — all of it is recorded rather than hidden.

- **`t12` (`d1`, needs-follow-up)** — the two register puzzles are still
  un-authored. Next step: obtain the external problem statements, or drop them
  from the challenge suite explicitly. Back-fitting is ruled out by `d1`.
- **[embodiment#18](https://github.com/agentculture/embodiment/issues/18)** —
  decide whether to wire or delete `DROPPED_COMPILATION_STARVED` and
  `DROPPED_COUNSEL_DISPLACED`, and tighten the `PROVOKERS` contract so
  "recording the code directly" stops counting as a provoker. Owner: this repo.
- **[embodiment#17](https://github.com/agentculture/embodiment/issues/17)** —
  the close-time race, now with a measured rate law (~1.1–1.3 lost insights per
  drive close). Four options in the issue; changing the muse lane's close
  contract is a design decision, not a bug fix. Owner: this repo.
- **A corrected P4** — continuity deserves its own pre-registered series with a
  grader that survives paraphrase. The t19 reading is post-hoc and labelled as
  such. Owner: this repo.
- **[colleague#358](https://github.com/agentculture/colleague/issues/358)
  (C1b)** — colleague cannot import embodiment until it relaxes its
  one-base-dependency rule *and* its no-third-party-import assertion. **Theirs
  to decide**; embodiment must not assume it.
- **Depth in the live evidence** — every result is n ≤ 12 on one rig with one
  model pair. The honest fix is more rigs and more n, not stronger wording.
- **Open outward, not ours to close** —
  [lobes-cli#146](https://github.com/agentculture/lobes-cli/issues/146),
  [lobes-cli#160](https://github.com/agentculture/lobes-cli/issues/160),
  [colleague#359](https://github.com/agentculture/colleague/issues/359).
  [headspace-cli#13](https://github.com/agentculture/headspace-cli/issues/13)
  and [#14](https://github.com/agentculture/headspace-cli/issues/14) **shipped
  in headspace-cli 0.10.0** and are closed.
