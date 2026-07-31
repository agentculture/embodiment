# Build Plan — orchestrator-worker architectures

slug: `orchestrator-worker-architectures` · status: `exported` · from frame: `orchestrator-worker-architectures`

> Gwen gains an orchestrator: the Qwen 27B cortex delegates ground work to the Thor-hosted Qwen 3.6 35B A3B worker role, in two architectures — manager (cortex orchestrates, worker executes) and hybrid (cortex routes simple work to the worker, keeps complex work itself) — and a pre-registered escalating series measures both against the existing single-actor rig on the math, league and logic/coding ladders

## Tasks

### t1 — Serial delegate tool: host executor exposing delegate-to-worker via SpawnRequest(role='worker'), tool surface enumerated, no repo access, cortex keeps finish authority

- covers: c7, h4, c14, h9, c36, h28
- acceptance:
  - hermetic tests prove the child surface excludes parent-level finish and an un-enumerated tool raises UnknownToolError
  - zero diffs under embodiment/ — the tool lives in examples/, riding the existing subagent seam
  - per-child ledger attribution (`SOURCE_SUBAGENT`, `child_task_id`) is verified in tests

### t2 — Worker dial config and wiring smoke: explicit endpoint/model config plus a smoke lane running a bare completion and a bounded tool loop through the exact harness path

- covers: c3, h2, c19, h13, c34, h26
- acceptance:
  - worker endpoint and model come only from explicit flags/env; absent config yields a recorded ABSENT degradation, never a silent fallback to the spark gateway
  - the smoke lane completes one bare completion and one bounded tool loop (schema call, result fed back, clean finish) against the live worker and commits both transcripts
  - per-call records carry `finish_reason` and token counts

### t3 — Worker throughput and effective-concurrency pre-measurement at widths 1, 2, 8, 14

- depends on: t2
- acceptance:
  - measured tok/s and per-call latency at each width are committed to docs/live-test-results/ before any arm design cites concurrency
  - sustained-load stability (errors and timeouts per width) is reported, addressing the parked stability unknown
  - the report states whether the operator-supplied 50 tok/s x14 figure reproduced on this rig

### t4 — Parallel fan-out tool: one tool call dispatching N workers with bounded total charge and recorded partial-failure degradation

- depends on: t1
- covers: c31, h23
- acceptance:
  - hermetic tests assert the bounded total charge per the pre-registered accounting rule, and that a failed child lands a recorded degradation plus partial results, never an abort
  - the loop's termination guarantee is preserved, asserted structurally (AST or step-count bound), not just behaviourally

### t5 — Architecture arms harness (existing / worker-solo / manager / hybrid) for the challenge rungs, with metered per-call records and the pinned sampling table

- depends on: t1, t2
- covers: c9, h5, c11, h7, c13, h8, c33
- acceptance:
  - every model call lands one record with `finish_reason`, prompt, completion and reasoning tokens, role, model and arm; budgets 16000 per d16
  - the analyse verb refuses to emit a verdict when either flat arm's cell is missing, and reports missing cells ABSENT
  - senses configuration is byte-identical across arms, asserted by config hash
  - the sampling table (temperature, thinking mode, `max_tokens` per role per arm) is read from committed config, not code defaults

### t6 — League lane arms: all four architectures drive cmatch decision points, with hybrid routing exercised and logged

- depends on: t1
- covers: c32, h24
- acceptance:
  - all four arms complete a scripted, no-network cmatch end-to-end in hermetic tests
  - the hybrid arm's routing decision is exercised per decision point and logged with its stated reason

### t7 — Coding rung: problems with brute-force-verified answers in docs/challenge-problems.md, an M2-kit grader, and workspace-jailed execution

- covers: c18, h12, c35, h27
- acceptance:
  - problem statements and verified answers land in docs/challenge-problems.md before any measured run
  - the grader ships adversarial fixtures, a paraphrase case, a vacuity assertion, and commits raw responses
  - a hermetic test proves model-written code cannot execute outside the network-less workspace

### t8 — Vision rung: image tasks, the senses-description path for the 27B arm, native image parts for worker arms

- depends on: t5
- covers: c17, h11
- acceptance:
  - both perception paths run live and are logged per call; no video path exists anywhere in the rung
  - the 27B arm's senses-described lane is dialled, not scored structurally absent

### t9 — Team-scoped map renderer: PNG from the fogged briefing alone, adversarial fog-leak fixtures, goldens, committed maps

- covers: c21, h15, c24, h17, c37, h29
- acceptance:
  - the renderer consumes only the fogged briefing JSON; no vision-radius constant exists anywhere in harness code
  - a fog-leak fixture (an entity outside visibility must not be drawn) fails the suite if violated; goldens are committed
  - every measured turn's rendered map is committed to the raw results dir

### t10 — Map-image league cells with enforced text/image twins

- depends on: t6, t9
- covers: c22, h16
- acceptance:
  - the harness refuses to run a cell whose image lacks a committed text twin from the same fog snapshot (same turn, seat, snapshot hash)
  - twin snapshot hashes ride the per-call records

### t11 — Pre-registration: ladder, arms, decision and stop rules, sampling table, tool-surface enumeration, fan-out accounting rule, heterogeneous-rung declarations — pinned by test

- depends on: t3
- covers: c10, h6, h24, h25
- acceptance:
  - committed before the first measured dial, provable from git history; a pin test recomputes its constants rather than asserting literals
  - it names, for every hybrid-graded rung, why that rung is heterogeneous, and excludes degenerate cells from the verdict by rule

### t12 — Run the series up the ladder with the stop rule, committing artifacts as they land

- depends on: t4, t5, t6, t7, t8, t10, t11
- covers: c27, h19
- acceptance:
  - each climbed rung lands its raw jsonl and per-call records beside the results doc; a skipped rung is recorded ABSENT citing the stop rule
  - smoke transcripts and throughput pre-measurements are committed with the series artifacts

### t13 — File the sibling proposals: the lobes-cli worker role, and the league-of-agents fog-scoped render verb plus per-unit visibility surface

- covers: c5, h3, c26
- acceptance:
  - both issues exist, signed per convention, carrying the Thor advert JSON and the briefing-schema probe as evidence
  - no commit touches either sibling repo

### t14 — Results and verdicts: decision rules quoted beside every verdict, wall-clock and token tables per arm per rung, corrections section, README reproduce lines

- depends on: t12, t13
- covers: c1, h1, c28, h20, c29, h21, c30, h22, h18
- acceptance:
  - every verdict quotes its pre-registered rule; INCONCLUSIVE is reported as INCONCLUSIVE, never softened
  - wall-clock and token tables (content and reasoning separately) per arm per climbed rung
  - the before-state is cited from d15, next-cycle M1 and the live Thor advert; both sibling issues are linked
  - the announcement's claims are verified by re-running the README reproduce commands

### t15 — Governance guard: muse modules untouched and the promotion gate honored in the landing PRs

- covers: c16, h10, c20, h14
- acceptance:
  - the cycle's PRs delete no muse module, test or harness, verified by diff review
  - reference-rig tables change only with a supporting verdict; INCONCLUSIVE leaves d15's rig untouched

## Risks

- [unknown_nonblocking] the served worker build's stability under sustained x14 load is unknown until t3 reports; a sibling variant crash-looped on GB10 hardware (task t3)
- [unknown_nonblocking] hybrid routing quality has no precedent grader; if a non-defective instrument cannot be designed, the hybrid verdict degrades to cost-plus-outcome only, and the pre-registration must say so (task t11)
- [unknown_nonblocking] the subagent seam's parent-minus-one attenuation may be the wrong bound for a 14-wide fan-out; if a width budget is needed it is a proposed embodiment change, never a harness hack (task t4)
- [unknown_nonblocking] the ladder may separate at the first rung, leaving coding and vision rungs ABSENT by rule — their harnesses still land hermetically tested, only the live cells go unmeasured (task t12)
