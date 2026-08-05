# Delivery Summary — config-not-minds strategist

plan: `config-not-minds-strategist` · run: `partial` · date: `2026-08-04`
baseline: `devague summary skeleton`

## Intent

Rebuild the strategist tier on a different premise, from issue
[#75](https://github.com/agentculture/embodiment/issues/75): **the strategist
changes configuration, not minds.** Advice can be ignored; configuration is
simply what a seat runs under, so *unawareness* replaces persuasion as the
mechanism. The cycle also adopted a three-tier architecture — senses relays,
the worker acts, the cortex configures — and committed to measuring the new
lane against the advisory lane and an ungoverned control before any default
changes.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Probe: the worker drives a bounded tool loop live (cortex-toolcall-probe pattern pointed at the worker role, 2-3 steps)
- `t2` — Fix #58: `SCOPE_AUTHORITY` states the `scope_id`-must-be-new rule
- `t3` — Change-unit schemas: one typed dataclass per target (worker tools/prompts/knowledge/permissions; senses prompts/permissions/knowledge)
- `t4` — Propose->verify->apply lifecycle with per-seat quiescence
- `t5` — Config ledger, events and fail-closed persistence
- `t6` — Revert-to-baseline and ratchet mechanics
- `t7` — Effective-config introspection, derived from the ledger alone
- `t8` — Knowledge block on eidetic: designated scope, attributed, strategist-maintained
- `t9` — Config-lane runner composed from kept plumbing, advisory lane untouched
- `t10` — Governance guard + CLAUDE.md drift fix
- `t11` — Host-composable senses grounding text (embodiment-side only)
- `t12` — Three-tier example host: senses-worker-cortex/strategist, wired from documented seams alone
- `t13` — ScopeBench pre-registration + config-change arm as data
- `t14` — Run the bench; publish whichever way it comes back
- `t15` — Live session 2 (#73 protocol) on the redesigned tier
- `t16` — Docs: README + explain carry the tier honestly

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `worker-toolloop-probe.py` + a pre-registered 3-rung ladder + `worker-toolloop.md`. 36 live runs, 12/12 on every bar, every rung **at ceiling** so the claim is bounded to acting protocol |
| `t2` | delivered | `SCOPE_AUTHORITY` states all four admission rules; `TestScopeAuthorityStatesTheAdmissionRules` maps each refusal code to its stating phrase |
| `t3` | delivered | `config_change.py` (7 targets, 3 origins, the lattice as data, refuse-whole) + `capability.py` (select-never-mint, no `from_executor`), 128 tests at 100 % coverage |
| `t4` | delivered | `config_lifecycle.py` — propose/verify/apply, seat-idle **and** suite-pass gate, per-seat config constancy proved with a per-step sha probe |
| `t5` | delivered | `config_ledger.py` + `config_events.py`; fail-closed proven against the **real** advisory-era `--state` payload from live session 1 |
| `t6` | delivered | `config_revert.py` — revert as an ordinary change, `RatchetGuard` against a fixed baseline; the headline test shows 3 increments each passing a 40-char gate while the cumulative 60-char drift fails |
| `t7` | delivered | `config_report.py` — effective config per seat with provenance, derived from the ledger alone; unexplainable state is a recorded degradation |
| `t8` | delivered | `knowledge.py` — eidetic-backed, attribution on eidetic's own `added_by`, enforced on write **and** read, no second store (import-proved) |
| `t9` | delivered | `config_review.py` / `config_runner.py` / `config_run.py`; four pins mutation-proved across 19 deliberate breaks; the actor's `complete` never wrapped even when armed |
| `t10` | delivered | `TestStrategistShipsOptInAndOff` + a `TestTheseGuardsCanFail` case, proved red against **real** mutations of committed files; CLAUDE.md drift corrected |
| `t11` | delivered | `senses_text.py` — `SENSES_GROUNDING` verified verbatim against the measured probe by AST-parsing the artifact rather than hand-copying |
| `t12` | delivered | `examples/three_tier.py` + matched and layer controls + **eight seam traps as data**, reproduced behaviourally (#79) |
| `t13` | delivered | `scopebench-config-preregistration.md` committed **before any dial**; arms as data with a second `lane` field; 8 conditions incl. the ratchet; the three-way ladder; declared floors and absences |
| `t14` | **partial** | The harness extension landed (the arms were undialable — `d8`) and the series is **dialling**. Admission pilots returned; **no verdict has been published** |
| `t15` | **partial** | The hermetic three-arm comparison ran and the agent-experience arm was recorded; the **live session did not run** |
| `t16` | delivered | README, `CLAUDE.md` (0.13.0 entry) and `explain` carry the lane with its value stated as unmeasured, plus both trap classes at the seam |

## Mid-work Decisions

Ten deviations were recorded via `/deviate` during the run. All are `proposed`
— the operator has not yet ruled on any of them.

- `d1` — the encoded authority lattice is **stricter** than the spec's prose: the worker owns *exactly* `senses.knowledge` and nothing else — *a seat that can widen its own tool surface is the escalation `h11` exists to prevent*
- `d2` — a third `host` origin was added, not in the spec's two-origin model — *without it, revert must be a privileged back door instead of an ordinary recorded change*
- `d3` — `t5` re-declared a structurally identical `ConfigPersistence` rather than importing `scoped_run.ScopePersistence` — *an import edge is a reason to edit the advisory lane, which must stay byte-stable as the comparator*
- `d4` — `ConfigDeferral` deliberately does **not** subclass `ConfigDegradation` — *a busy seat is the gate working; folding it in would make a correct session report hundreds of degradations*
- `d5` — a `verified → proposed` demotion exists that the five-kind vocabulary has no kind for; `t9` resolved it as a **documented mapping** (a re-entry marked by the existing `code` field) rather than a sixth kind
- `d6` — `t7`'s report derives "gate verdict" as the ledger's `applied`/`reverted`, not a richer `passed`/`failed`/`stale` — *the honest fix is for the ledger to record the verdict at write time; today it does not, so the report makes the weaker claim*
- `d7` — revert cannot restore a prompt section or knowledge entry to true non-existence (the apply primitives are additive) — *made observable via `residual_*` fields and `matches_baseline=False`, never a false claim of exact restoration*
- `d8` — the cycle-2 arms were **undialable**: `scopebench_live.py`'s `LIVE_ARMS` was still cycle 1's pair, and `t13` was correctly scoped out of the live harness
- `d9` — **the pre-registration was NOT amended** although §19 permitted it. Condition 8 will read FAILED on a digest rather than on drift (`d7`). *Moving a threshold to fit a known outcome is what makes a pre-registration worthless* — the series runs under the committed rule and the report states the artifact beside the verdict
- `d10` — A3's admission pilot has no verdict file: it completed 2 of 3 episodes (both acceptance 1.0) and was killed by **my own 1500 s timeout**, sized before I knew the advisory arm costs ~660 s/episode. *The 0.11.0 clock lesson recurring, on the operator this time.*

Not covered by any deviation record:

- The eight `#62`-class seam traps `t12` found were **filed as an issue** (#79) rather than only documented at the seam, because two of them are defaults rather than documentation gaps.
- The senses vision question was **settled by measurement** (`senses-vision.md`) after the gateway advert was found to disagree with the Orin actually serving the seat.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t3` (`d1`) | the encoded lattice is stricter than the spec states | acceptable |
| `t3` (`d2`) | a third `host` origin, not in the spec's model | acceptable |
| `t5` (`d3`) | a re-declared port rather than the named import | acceptable |
| `t4` (`d4`) | deferrals ride a separate stream from degradations | acceptable |
| `t4` (`d5`) | a demotion transition outside the declared vocabulary | needs-follow-up |
| `t7` (`d6`) | "gate verdict" is the weaker ledger vocabulary | needs-follow-up |
| `t6` (`d7`) | revert cannot delete; exact restoration is not claimable | acceptable |
| `t14` (`d8`) | the arms were undialable; the harness was extended inside `t14` | needs-follow-up |
| `t14` (`d9`) | condition 8 runs under the committed rule and is expected to FAIL on a digest | needs-follow-up |
| `t14` (`d10`) | A3's pilot was cut off by an operator-side timeout; §9 rule 1 was not enforced for A3 | needs-follow-up |
| `t14` | **the series has not finished and no verdict is published** — the pre-registered dial order means the incomplete arms are *declared* absences, not discovered ones | needs-follow-up |
| `t15` | the live session did not run; only the hermetic and agent-experience arms exist | needs-follow-up |

## Evidence

- tests: full suite `uv run pytest -n auto -q` — **7794 passed, 30 skipped**
- lint: `black --check`, `isort --check-only`, `flake8`, `bandit -c pyproject.toml -r embodiment examples` — all clean
- lint: `markdownlint-cli2` over 87 files — 0 errors
- gate: `uv run teken cli doctor . --strict` — PASS
- coverage: 97 % total; the config lane 87–100 % per module (gate is 60 %)
- zero-diff pin: `git diff` on `embodiment/loop.py`, `scoped_run.py`, `strategist_runner.py`, `scope_events.py` against the pre-cycle baseline — **empty**
- commits: 41 on `spec/config-not-minds-strategist`
- live records: `docs/live-test-results/worker-toolloop.md` (36 runs), `senses-vision.md` (12 calls), `scopebench-config-raw/` (series in flight)
- issues: [#78](https://github.com/agentculture/embodiment/issues/78), [#79](https://github.com/agentculture/embodiment/issues/79), comment on [#63](https://github.com/agentculture/embodiment/issues/63)

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| The configuration lane ships, opt-in and off by default | high | `tests/test_governance.py::TestStrategistShipsOptInAndOff` · `embodiment/config_*.py` |
| `loop.py` is byte-identical and the advisory lane survives as a comparator | high | empty `git diff` on the four files · `tests/test_config_run.py` pins, 19 mutations proved red |
| A change unit cannot carry an action, and capability changes cannot mint | high | `tests/test_config_change.py` · `tests/test_capability.py` (100 % coverage both) |
| The worker may write senses' knowledge and structurally cannot reach its prompts | high | `tests/test_config_change.py::TestTheAuthorityLattice` |
| Configuration identity is constant within any single drive | high | `tests/test_config_lifecycle.py::TestPerSeatConstancy` (+ a probe that fires on a miswired seat) |
| Cumulative drift is detectable against a fixed baseline | high | `tests/test_config_revert.py::TestRatchetCatchesWhatIncrementalGatesMiss` |
| Revert restores configuration **exactly** | **unverified** | `d7` — additive primitives leave residual declared sections; `matches_baseline` reports `False` rather than claiming otherwise |
| The acting seat can drive `embodiment.loop.run` | medium | `worker-toolloop.md` — 36 runs, 0 failures, **but all rungs at ceiling and the surface hermetic**; bounded to acting protocol |
| The senses seat can read images and motion | medium | `senses-vision.md` — image 4/4, motion 4/4, text-only control refuses 0/4, n=4/cell, one rig |
| **The configuration lane is more valuable than the advisory lane** | **unverified** | the series is dialling; **no verdict published** — not claimed in any direction |
| **The tier earns its cost in a live session** | **unverified** | `t15`'s live session did not run |

## Remaining Work / Follow-up

- **`t14` — finish the series and publish the verdict.** It is dialling in the
  pre-registered order (A0 → A4 → A3 → A5 → A1), writing per-episode so a stop
  loses nothing. Whatever has not run is a *declared* absence and forces
  `INCONCLUSIVE` under the committed rule. Next step: let it complete, then
  `scopebench_live.py report --rule config`.
- **`t15` — run live session 2** under #73's protocol with a matched ungoverned
  control. Blocked on rig availability; `senses` was `ready=false` throughout
  this run, which the operator accepted with senses degrading visibly.
- **`d10` — re-run A3's admission pilot** with a timeout sized from its measured
  ~660 s/episode, so §9 rule 1 is enforced for the comparator arm.
- **`d9` — decide condition 8's future.** Either accept the digest-based FAILED
  or amend the rule for cycle 3, *with the evidence in hand*.
- **`d5`/`d6` — close the two `needs-follow-up` deviations**: the demotion
  mapping is documented but the vocabulary is still five kinds, and the ledger
  still does not record a gate verdict at write time.
- **#79 — fix or document the two default-shaped seam traps.** T2 in particular
  reproduces the advisory tier's zero-directive outcome by default (six drives,
  one review).
- **The worker-promotion gate stays shut.** `_WORKER_ROLE_HAS_SUPPORTING_VERDICT`
  is `False`; `worker-toolloop.md` is a bounded verdict about protocol, and the
  promotion at stake is to the acting seat of a real rig. An operator decision.
- **Ten deviations await a ruling** — `devague deviate --confirm d1 … d10`.
