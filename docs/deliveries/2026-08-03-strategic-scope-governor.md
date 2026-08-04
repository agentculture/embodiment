# Delivery Summary — strategic scope governor

plan: `strategic-scope-governor` · run: `partial` · date: `2026-08-03`
baseline: `devague summary skeleton`

## Intent

embodiment gains an **opt-in strategic scope layer above the bounded actor
loop**: a strategist seam issuing typed, versioned, supersedable directives that
own objectives, priorities, constraints and responsibility allocation —
structurally without actor-tool, presence or policy authority — while `run()`
stays actor-only and byte-identical, and **no strategic claim ships without
ScopeBench measurement**.

Executed from issue [#51](https://github.com/agentculture/embodiment/issues/51)
through the full devague flow (`/scope` → `/think` → `/challenge` →
`/spec-to-plan` → `/assign-to-workforce` → `/deviate` → this artifact), as 16
tasks in 8 waves on branch `spec/strategic-scope-governor`.

The run is marked **`partial`**, and the reason is not a failure to build: every
task merged and the suite is green. Two tasks did not meet their *full*
acceptance contract — `t11`'s Stage 2 was never dialled and `t14`'s session 2
never ran — and both are recorded as `partial` rather than rounded up.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — scope.py — the pure strategic protocol: Scope shapes (Snapshot, Directive, Report, Controls, Outcome, Degradation), bounded ScopeLoop, directive validation / versioning / supersession, and the role-scoped strategist bench type (empty by default)
- `t2` — `strategist_runner.py` — the strategist background lane, CITED from `muse_runner.py` (copied verbatim into a clearly-named new module, then owned and freely renamed/adjusted — never imported from the archived muse). Keeps the proven invariants: non-blocking consider/drain, one in-flight review with a single replaceable pending slot, bounded buffers, bounded join, pull-only degradations where every loss is an authority event. Cadence and staleness are DESIGNED for a deep thinker, not inherited from a fast advisor
- `t3` — the ledger lane: `SOURCE_SCOPE` added by exactly one `_MODULES` row plus a `from_scope` reader; `known_codes`() harvests the new `DEGRADED_`/`DROPPED_` constants from the scope module `__all__`
- `t4` — `scoped_run.py` — `run_scoped`() composing loop.run() unchanged: explicit host-derived default scope until the first directive, application only at safe actor boundaries, continue-under-last-valid on strategist failure, no silent restore of superseded scope
- `t5` — scope observability: the scope.\* event kinds ride the host-wired ObserverFn; every record carries actual model, role, directive and snapshot ids, triggering boundary, and previous/resulting scope versions
- `t6` — Stage 0 authority suite — seizure AND surrender: the adversarial sentinel proves nothing strategist-sourced reaches the executor or `pre_tool` registry; the surrender test proves directive prose embedding an operational instruction is never executed; the memory-owner and CLI boundaries are pinned
- `t7` — seat wiring under examples/scope/: explicit seat-to-role config mapping strategy to the lobes cortex role, operation to the worker role, interaction to senses — resolved by role name from /capabilities, never from model names
- `t8` — the non-colleague demo: a greenhouse-style host under examples/scope/ opts into the strategist seat end-to-end — projector built from host state, directives applied across boundaries, ledger and events visible — while the plain greenhouse stays actor-only and untouched
- `t9` — ScopeBench scaffold under examples/scope/: machine-gradable episode schema, the deterministic perfect subordinate (Stage 1), arms A0-A3 as data not code paths, committed seeds, and the preregistration doc with the seven-condition verdict rule — all before any live dial
- `t10` — clocks and rates for the scope lane: a dated rate entry for the worker role (and strategist cadence derived from measured latency, not guessed), a Clock in CLOCKS for every timeout constant the scope examples introduce, under the existing AST guard and its test-of-the-test
- `t11` — the pre-registered series: Stage 1 (deterministic subordinate) then Stage 2 (fixed worker-role actor, byte-identical across arms) over the four arms; results published as first-class outcomes — including INCONCLUSIVE or negative — with every absent cell reported
- `t12` — docs close-out: README / CLAUDE.md / relationships.md present the three-authority-level design as opt-in, the salience row is rewritten only if the measured result supports it (otherwise the gap stays recorded), the League demotion is stated, and the bee-hive files are verifiably untouched
- `t13` — directive persistence lanes: the durable lane surviving across drives and the session-scoped temporary lane within one process — structurally distinct, every record naming its lane, sessions as the future per-subagent scoping seam
- `t14` — Stage 3 live sessions (t14): a context-clear operator agent talks, works and brainstorms with the three-tier embodiment through a real host — directives visibly reach the Worker as inserted events mid-conversation, senses presents one coherent teammate, background review never blocks interaction, failures degrade visibly, and both persistence lanes are exercised live
- `t15` — archive the muse lane per d2/d3 and issue #53 (resolved: ARCHIVED BUT CITABLE): muse.py and `muse_runner.py` leave the shipped reference architecture while remaining readable as the cited reference for `strategist_runner.py`. Keep CI green and the ledger harvest honest
- `t16` — SonarCloud sweep: triage every open issue against the post-implementation tree and clear the S9073 composite-assertion debt (44 pre-existing instances across 8+ test files, plus any the new suites added) — fix where the rule earns its keep, disposition in docs/sonar-dispositions.md where it does not

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `embodiment/scope.py` (2334 lines) + 186 tests. All Scope shapes, `ScopeLoop`, validation/versioning/supersession, `ScopeToolBench` empty by default. Coverage 97% |
| `t2` | delivered | `embodiment/strategist_runner.py` (1262 lines) + 109 tests, **100%** coverage. Cited verbatim from `muse_runner.py` per `d3`. Every timing constant re-derived from the committed rate config and stated: `DEFAULT_MAX_LAG=12` (not the muse's 5), review gap 2, max_pending 4. `__init__` at 12 params (S107 limit 13) via `StrategistLimits` |
| `t3` | delivered | `SOURCE_SCOPE` ledger lane in **32 pure-insertion lines, 0 deletions**, + 45 tests (21 real provokers through public seams). One `_MODULES` row harvests all 21 codes |
| `t4` | delivered | `embodiment/scoped_run.py` (1566 lines) + 85 tests, **100%** coverage. `loop.py` diff **0 lines**, proven 3 ways; actor-only path byte-identical, proven 3 ways including seam identity. Directive rides a `user` message — `system_prompt` never appears as code |
| `t5` | delivered | `embodiment/scope_events.py` (550 lines) + 126 tests, **100%** coverage. Ten `scope.*` kinds, `ScopeEvent` field-identical to `LoopEvent` |
| `t6` | delivered | `tests/test_scope_authority.py`, 49 tests, no private access. Seizure **and** surrender both built — the surrender half used a real `Credulous` actor rather than the scripted one, which is what caught `h3`'s overclaim (`d5`, issue #55) |
| `t7` | delivered | `examples/scope/seats.py` — seat resolution by role name from `/capabilities`, with a behavioural proof that swapping model fields does not move a seat |
| `t8` | delivered | `examples/scope/greenhouse_scope.py` + `tests/test_demo_scope_greenhouse.py` (54 tests). Hermetic by hard rule; the plain greenhouse stays untouched |
| `t9` | delivered | ScopeBench: 7798 lines across `examples/scope/` (4 modules), committed seeds/fixtures/preregistration, 239 tests. Exact enumerating oracle with a no-memo brute-force test-of-the-test. 6 of 8 scenario families run; `handoff` + `commitment` declared and reported as ABSENT |
| `t10` | delivered | Per-role `calling_patterns` in the rate config; `CLOCKS` extended; the AST guard's walk extended to `embodiment/` and given `TestTheWalkReachesEverySubfolder` — a test-of-the-test the operator's own `rglob` fix had shipped without |
| `t11` | **partial** | Stage 1 ran live: 216 model calls, 36 episodes/arm, 0 dropped episodes, 0 transport retries, 0 stream deaths. Verdict **`INCONCLUSIVE`** with all 7 conditions reported separately and 4 pre-registration amendments recorded. **Stage 2 was never dialled** (amendment 3), so its byte-identical-across-arms acceptance criterion is unmet |
| `t12` | delivered | README / CLAUDE.md / `relationships.md` / `explain` catalog / `scoped_run` docstring. Salience row records the negative; #55's containment statement on all three surfaces; League demotion stated; bee-hive byte-identity verified |
| `t13` | delivered | `LANE_DURABLE` / `LANE_SESSION`, `ScopePersistence`, `ScopeSession`, `to_dict`/`from_dict`, 143 tests. Both lanes structurally distinct, every record naming its lane |
| `t14` | **partial** | Session 1 ran context-clear against a live three-tier host and is written up in full (`scope-live-session-1.md`, 621 lines) with mechanism findings, the ungoverned control, 8 partnership probes and 5 explained-absent items. Session 2's brief authored as #73. **Session 2 has not run** |
| `t15` | delivered | Muse archived per `d2`/`d3`: 27 files, `ARCHIVED_SUBMODULES`, 18 `Muse*` names off `_LAZY_NAMES`, `muse.py`/`muse_runner.py` byte-untouched so the citation resolves. Retired all three sibling workarounds and put the scope lane on the curated surface |
| `t16` | delivered | 49 SonarCloud issues fixed (45× S9073, 4× S9083), 3 dispositioned with rationale in `docs/sonar-dispositions.md` and matching server transitions. Zero files under `embodiment/` or `examples/` touched |

## Mid-work Decisions

- `d1` (**proposed**) — t2 was scheduled parallel to t1 but genuinely depends on it: `strategist_runner` must import the Scope shapes from `scope.py`, exactly as `muse_runner.py` imports 12 names from `embodiment.muse`. Waves resequenced to t1 alone, then t2+t9.
- `d2` (approved) — the muse lane is ARCHIVED (operator, 2026-08-03), contradicting confirmed claims `c12` and `c32`. Blast radius measured before acting (45× in `test_ledger.py`, 25× in `test_proof_reporting.py`, 20× in `ledger.py`, …), so this is a **surface retirement, not a file deletion**, and it was sequenced as its own task after t2 rather than executed inline.
- `d3` (approved) — archived **but citable**; t2 ships `embodiment/strategist_runner.py`, not `scope_runner.py`, diverging from confirmed claim `c7`.
- `d4` (approved) — `c7`'s exact-mirror requirement lifted. The runner keeps the proven *invariants* but designs its cadence and staleness for a deep thinker. The muse's `DEFAULT_STALE_LAG=5` encoded an assumption measured **false** on this rig and was explicitly not copied forward.
- `d5` (**proposed**) — confirmed honesty condition `h3` overclaims. `t6` measured that a governed drive **does** reach the executor with a smuggled command against a deliberately `Credulous` actor, while the ungoverned control never sees it. `run_scoped` adds no containment of its own; the host's `ToolExecutor` and `pre_tool` lane are the containment.
- `d6` (**proposed**) — t14 assumed a host that did not exist. `greenhouse_scope.py` is hermetic by hard rule and `scopebench_live.py` runs episodes, not conversation, so `examples/scope_live_session.py` was built as part of t14. The *session* stayed context-clear; only its harness was built with context.
- `d7` (approved, origin `user`) — t14 gained a pre-registered **partnership** dimension, an ungoverned `--no-strategist` control, and a human-operator session, because #52 as filed was a mechanism audit and would have defaulted to reporting what was countable.

Decisions no deviation record covers:

- **The `examples/scope/` placement of the live host was overridden by the building agent, correctly.** The ScopeBench pre-registration §14 asserts by AST over every file in that folder that none imports a transport, reaches `embodiment.loop`, or introduces a timeout constant. A live host is all three, so it landed at `examples/scope_live_session.py` — same placement and reason as `scopebench_live.py`.
- **`SENSES_MAX_TOKENS` was set to 1024, against the operator's own initial reasoning.** "A ceiling is not a target" was refuted by a live run in which the senses seat generated for **22 minutes** on one conversational turn. The two failure modes are asymmetric on the presence tier and the constant records why.
- **Generated session transcripts were excluded from markdownlint** rather than reformatted. 590 errors, almost all inside quoted model output; reformatting a transcript alters the record it exists to preserve. The exclusion is narrow — authored reports stay linted.
- **The `t1` agent died before committing.** 3800 uncommitted lines were salvaged from its worktree, linted, verified against all 5 acceptance criteria, and committed on its behalf rather than restarting the task.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t2` (`d1`) | t2 genuinely depends on t1 — `scope_runner.py` must import the Scope shapes from `scope.py` | `acceptable` |
| `t2` (`d2`) | archiving cannot precede t2: t2's confirmed acceptance criteria are to MIRROR ThreadedMuseRunner mechanics, so the reference implementation must survive until t2 lands | `needs-follow-up` |
| `t2` (`d3`) | operator instruction: the muse is archived and the strategist replaces it. Naming follows confirmed `c21`, which fixes "strategist" as this repo's vocabulary | `acceptable` |
| `t2` (`d4`) | the muse's mechanics encode an assumption measured FALSE on this rig; copying its staleness model would import a constant wrong in a third direction | `acceptable` |
| `t6` (`d5`) | a scripted-actor-only surrender test would have passed **vacuously** and shipped a guarantee the package does not make | `needs-follow-up` |
| `t14` (`d6`) | the plan derived t14's host from t8, whose demo is hermetic by design and by CI; the gap was invisible until #52 was read against the actual tree | `acceptable` |
| `t14` (`d7`) | league already ranked three architectures on interface compliance rather than play; an unpre-registered partnership read would have repeated that one layer up | `needs-follow-up` |
| `t11` | **Stage 2 was never dialled.** The acceptance criterion "Stage 2 pins actor config, tools, senses projection, sampling and budgets byte-identical across arms, asserted from the run records" is unmet. Recorded in the results doc as pre-registration **amendment 3**, declared rather than omitted — but it is drift against the plan, and it is why conditions 1 and 5 are `ABSENT` and the verdict is forced to `INCONCLUSIVE` | `needs-follow-up` |
| `t14` | **Session 2 has not run.** Its acceptance requires "session 2 runs context-clear as well". The brief exists (#73, authored during t14 with session 1's numbers in a comparison column); the session does not | `needs-follow-up` |

## Evidence

- tests: full suite `uv run pytest -n auto` — **6621 passed, 28 skipped**
- tests: coverage `uv run pytest --cov=embodiment` — **TOTAL 97%**; `scope.py` 97%, `scoped_run.py` **100%**, `strategist_runner.py` **100%**, `scope_events.py` **100%**
- lint: `black --check` / `isort --check-only` / `flake8` / `bandit -c pyproject.toml -r embodiment examples` — all clean, 185 files
- lint: `markdownlint-cli2 "**/*.md" …` — **0 errors**, 80 files
- lint: `uv run teken cli doctor . --strict` — **26/26, exit 0**
- SonarCloud: gate **PASSED**, `new_coverage` 97.2%, 0 hotspots; 48 open (all fixed locally, invisible to the server until the PR analyses), 5 dispositioned
- commits: `be357ce..HEAD` — 59 commits, **103 files changed, +39,464 / −200**
- live: `docs/live-test-results/scopebench.md` (216 calls), `scope-live-session-1.md` (8 drives + a matched control pair), `senses-grounding.md` (192 calls)
- issues: #52, #54–#75 filed this cycle; #53 resolved; colleague#358 open

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| the opt-in strategist tier ships: typed, versioned, supersedable directives owning objectives, priorities, constraints and ownership | high | `embodiment/scope.py`, `strategist_runner.py`, `scoped_run.py` · 6621-test suite |
| an unarmed governor is byte-identical to `run()` | high | proven 3 ways in `t4`; confirmed live — the ungoverned control's counters all zero including `boundaries: 0` (`scope-live-session-1.md` §3) |
| `loop.py` was not modified | high | `git diff main...HEAD -- embodiment/loop.py` — **0 lines** |
| a directive structurally cannot carry a tool, a command or an approval | high | `tests/test_scope_authority.py` · **0 authority violations across 28 episodes / 87 directives** (`scopebench.md` F5) |
| directives arrive as events inserted at a safe boundary, never a mid-drive system-prompt rewrite | high | `scope-live-session-1.md` §2.2 — `message[3]`, `role='user'`, constant sha across 8 drives, `system_rewrites: 0` |
| every degradation is host-observable through one ledger lane | medium | `t3`'s 32-line `SOURCE_SCOPE` insertion + 45 tests. **No live ledger record exists** — session 1 found the host wires no sink (`scope-live-session-1.md` §6) |
| both persistence lanes work and every record names its lane | high | `t13`'s 143 tests · `scope-live-session-1.md` §2.7 (durable survived a process restart; session-scoped died with its session) |
| the strategist tier improves outcomes | **unverified** | `t11` **`INCONCLUSIVE`**; `t14` session 1's only matched control applied **zero** directives for 189.6 s / 3663 tokens and returned a materially identical answer. **Not claimed** |
| the mechanism is proven | high | every structural check in #52 held (`scope-live-session-1.md` §2.1–§2.7) |
| non-intervention holds — the tier stays quiet when direction is correct | **unverified** | **FAILED live**: 4 of 5 directives restated one ordering; 69.5% of 28,318 strategist tokens bought nothing (#68) |
| Stage 2 pins config byte-identical across arms | **unverified** | Stage 2 never dialled — `t11` amendment 3 |
| the muse is archived without breaking the citation | high | `t15` · `muse.py`/`muse_runner.py` byte-untouched (+34/+29, 0 removed) · `tests/test_muse_archival.py` |
| the bee-hive files are untouched across the whole plan | high | `git diff --stat main...HEAD -- examples/arch_hive.py examples/worker_seam.py examples/worker_scoped_overhead.py` — **empty**, all three present in both trees |
| every timeout is derived from a dated committed rate config | high | `tests/test_timeout_bounds.py` + `TestTheWalkReachesEverySubfolder` |
| SonarCloud is fully triaged | medium | 49 fixed, 3 dispositioned. **The new ~15k lines have not been analysed** — CI never scans a branch (#60), so the local audit is a faithful reproduction of known rule families, not a scan |

## Remaining Work / Follow-up

- **`t11` Stage 2** — dial the fixed worker-role actor byte-identical across arms. Until then conditions 1 and 5 stay `ABSENT` and no strategic-value claim is available. Blocking for the plan's headline claim.
- **`t14` session 2** — run #73 context-clear. Its Priority 1 list is session 1's five unexercised items: authority violations never fired live, the injection chain was unprobed, the fabrication warning did not reproduce (but the state that produced it was never reached), truncation never fired, and **no ledger records exist because the host wires no sink**.
- **`d1`, `d5`, `d6` are `proposed`** and need the operator's confirm or reject. They are not mine to confirm.
- **Nine defects found live and filed, none fixed** (`/deviate` rule: a session that repairs what it finds has measured the repair) — #65 superseded directive re-inserted ahead of its replacement (6 of 8 drives), #66 a one-off instruction became a durable persisted constraint, #67 a stale directive silently substituted its objective for the operator's request, #68 non-intervention failed, #69 empty owner on 4 of 5 directives, #70 triplicated degradation records, #71 transcript lint + missing `previous_version`, #72 senses' one-mind assertion, #74 a flaky class-scoped fixture that can revert good work through the merge gate.
- **Two protocol defects that made a whole arm unmeasurable** — #58 (`SCOPE_AUTHORITY` never states the `scope_id`-must-be-new rule it enforces; 47 of `A2`'s 93 offers died on it) and #59 (`d16`'s 16000-token budget does not transfer: 19.4% worker truncation vs 0% cortex). Both want a fix **and** a re-run of `A2`.
- **#62** — an unseeded issued chain makes a protocol-obedient strategist look incapable. The live host works around it; nothing in the package warns you.
- **#61** — `embodiment.scope_events` is off the curated public surface while its three lane siblings are on it; the fourth task this cycle to hit that wall.
- **#75** — the operator's proposal that the strategist change **configuration** rather than issue advice. It is a direct response to this cycle's central negative result and would delete #54/#55/#57/#62/#65 by construction, at the cost of a strictly stronger authority model.
- **#63 / #64** — the senses seat: one clause decides whether it invents readings (0/16 vs 16/16), the model stays Gemma 4 12B, and the quantisation moves to QAT w4a16 with a committed baseline and a pre-registered ceiling warning.
- **colleague#358** — the seam proposal. `C1b` is colleague's decision, not this repo's.
