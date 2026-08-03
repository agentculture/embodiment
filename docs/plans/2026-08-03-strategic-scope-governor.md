# Build Plan — strategic scope governor

slug: `strategic-scope-governor` · status: `exported` · from frame: `strategic-scope-governor`

> embodiment gains an opt-in strategic scope layer above the bounded actor loop: a strategist seam with typed, versioned, supersedable directives that owns objectives, priorities and responsibility allocation — structurally without actor-tool, presence or policy authority (its only tools a host-wired, role-scoped bench per c22) — while run() stays actor-only and unchanged, and no strategic claim ships without ScopeBench measurement

## Tasks

### t1 — scope.py — the pure strategic protocol: Scope shapes (Snapshot, Directive, Report, Controls, Outcome, Degradation), bounded ScopeLoop, directive validation / versioning / supersession, and the role-scoped strategist bench type (empty by default)

- covers: c6, h5, c13
- acceptance:
  - every Scope dataclass passes a fields() decision-vocabulary ban: no tool, arguments, deny, rewrite, allow, approve names anywhere in the shapes
  - AST import test: scope.py imports no embodiment.loop, no presence acting names, no event fabric, no threading
  - the AST exit-returning-function walk proves ScopeLoop termination structurally, mirroring tests/`test_loop.py`
  - directive validation refuses a version moving backward and an unknown supersedes id; a rejected directive is recorded, never silently dropped
  - the bench type is empty by default and structurally cannot hold an actor ToolExecutor (fields test)

### t2 — `scope_runner.py` — background mechanics mirroring ThreadedMuseRunner: non-blocking consider and drain, one in-flight review with a single replaceable pending slot, bounded buffers, lag-based staleness, bounded join, pull-only degradations — every loss recorded as an authority event

- covers: c7, h6
- acceptance:
  - consider() and drain() never block or raise under test, including with a wedged strategist seam
  - at most one review is in flight; a newer snapshot replaces the waiting one and the displaced snapshot is recorded
  - every displaced, dropped, stale or superseded result lands in the degradation record set — a counted test proves zero silent losses
  - close() joins with a bound and never hangs on a parked seam call

### t3 — the ledger lane: `SOURCE_SCOPE` added by exactly one `_MODULES` row plus a `from_scope` reader; `known_codes`() harvests the new `DEGRADED_`/`DROPPED_` constants from the scope module `__all__`

- depends on: t1, t2
- covers: c8, h7
- acceptance:
  - ledger.`known_codes`() lists every scope `DEGRADED_`/`DROPPED_` constant with no ledger change beyond the one row and the reader
  - a `scope_runner` drop surfaces through ledger.read(scope=...) as a first-class LedgerRecord
  - the ledger diff outside the `_MODULES` row and `from_scope` reader is zero lines

### t4 — `scoped_run.py` — `run_scoped`() composing loop.run() unchanged: explicit host-derived default scope until the first directive, application only at safe actor boundaries, continue-under-last-valid on strategist failure, no silent restore of superseded scope

- depends on: t1, t2
- covers: c1, h1, c5, h4, c30, h22
- acceptance:
  - the diff to loop.py is zero lines; `run_scoped`() passes only arguments run() already accepts
  - with no strategist configured, `run_scoped`() output is byte-identical to run() on the same scripted seam, and the actor path imports no scope module (AST test)
  - a directive arriving mid-step is applied only at the next boundary; a stale or superseded directive is recorded and never applied
  - a failing strategist degrades to the last valid directive (or the host default scope), records the degradation, and the drive completes
  - directives render into actor context exclusively through framing composition (`frame_cortex`, top-level acting loop only); an AST/call-graph test pins no other prompt-bearing path, and with no configured identity the scope lane leaves prompts byte-identical
  - directive application inserts a framing-composed event into the Worker context at the safe boundary — the system prompt is never rewritten mid-drive (c35)

### t5 — scope observability: the scope.\* event kinds ride the host-wired ObserverFn; every record carries actual model, role, directive and snapshot ids, triggering boundary, and previous/resulting scope versions

- depends on: t4
- covers: c9, h8, c17, h12
- acceptance:
  - scope.py and `scope_runner.py` import no event fabric (AST test); a host wiring EventEmitter as observer receives every scope.\* kind
  - every emitted scope record names the actual contributing model and role — asserted for accepted, rejected, stale and degraded paths
  - a run configured with a single model emits no record naming a strategist — the absent-identity byte-identical rule holds

### t6 — Stage 0 authority suite — seizure AND surrender: the adversarial sentinel proves nothing strategist-sourced reaches the executor or `pre_tool` registry; the surrender test proves directive prose embedding an operational instruction is never executed; the memory-owner and CLI boundaries are pinned

- depends on: t1, t4
- covers: c3, h3, h10, c16, h11, c18, h13, c29, h21
- acceptance:
  - sentinel test: a hostile strategist seam attempting tool injection leaves the executor and `pre_tool` hook registry provably untouched
  - surrender test: a directive whose objective prose embeds a shell command drives a scripted actor that never emits it as a tool call — the prose is consumed as scope, not instruction
  - AST test: scope modules import neither eidetic nor coherence beyond the existing continuity seam, and the scope projector is typed as a host-supplied callable
  - tests/`announcement_checklist.py` caveat cli1 still passes: no CLI verb imports embodiment.loop, embodiment.scope or the scoped composition
  - snapshot-injection case: a snapshot whose `material_outcomes` embed an operational instruction cannot reach the executor — covered at both hops: the strategist may echo it into a directive, and the actor provably never executes it

### t7 — seat wiring under examples/scope/: explicit seat-to-role config mapping strategy to the lobes cortex role, operation to the worker role, interaction to senses — resolved by role name from /capabilities, never from model names

- depends on: t4
- covers: c2, h2
- acceptance:
  - the example config names lobes roles only; an AST/grep test pins that no scope module or example parses a model name string to infer a seat
  - a missing role on the gateway degrades to actor-only with a recorded degradation, never a raise
  - the wiring lives under examples/scope/ per the per-architecture layout decision

### t8 — the non-colleague demo: a greenhouse-style host under examples/scope/ opts into the strategist seat end-to-end — projector built from host state, directives applied across boundaries, ledger and events visible — while the plain greenhouse stays actor-only and untouched

- depends on: t4, t7
- covers: c25, h17, c27, h19
- acceptance:
  - the demo drives `run_scoped`() with a live or scripted strategist and shows a directive changing actor behaviour at a boundary
  - examples/greenhouse.py is unchanged — the actor-only path remains the demonstrated default
  - every after-state property is visible in the demo output: versioned directives, supersession, host-observable degradations, upward reports

### t9 — ScopeBench scaffold under examples/scope/: machine-gradable episode schema, the deterministic perfect subordinate (Stage 1), arms A0-A3 as data not code paths, committed seeds, and the preregistration doc with the seven-condition verdict rule — all before any live dial

- depends on: t1
- covers: h20
- acceptance:
  - episode outcomes are machine-graded against an exact oracle or declared Pareto frontier; an LLM judge is structurally secondary
  - the four arms differ only in data (seat config), asserted by a test that diffs their configurations
  - the preregistration doc and generated seeds are committed before any live result, and the verdict rule names all seven conditions including the non-intervention and token-cost guards
  - outcome, protocol acceptance, authority compliance and cost are separate record fields — a malformed directive is never counted as a strategic failure

### t10 — clocks and rates for the scope lane: a dated rate entry for the worker role (and strategist cadence derived from measured latency, not guessed), a Clock in CLOCKS for every timeout constant the scope examples introduce, under the existing AST guard and its test-of-the-test

- depends on: t7, t9
- covers: c10, h9
- acceptance:
  - every timeout constant under examples/scope/ appears as a Clock derived from a dated entry in timeout-rate-measurements.json; the AST guard fails any constant outside the walk
  - the worker role has a measured, dated rate entry (or an honest `unmeasured_roles` entry blocking its live dial)
  - `scope_runner` staleness and cadence defaults cite the measured strategist latency in their derivation comment

### t11 — the pre-registered series: Stage 1 (deterministic subordinate) then Stage 2 (fixed worker-role actor, byte-identical across arms) over the four arms; results published as first-class outcomes — including INCONCLUSIVE or negative — with every absent cell reported

- depends on: t9, t10
- covers: c28
- acceptance:
  - Stage 1 runs before any Stage 2 dial and its verdict is recorded per the committed rule; no verdict is emitted with a required control absent
  - Stage 2 pins actor config, tools, senses projection, sampling and budgets byte-identical across arms, asserted from the run records
  - the results doc reports every condition of the verdict rule separately, and a no-benefit outcome is published with the same prominence as a win

### t13 — directive persistence lanes: the durable lane surviving across drives and the session-scoped temporary lane within one process — structurally distinct, every record naming its lane, sessions as the future per-subagent scoping seam

- depends on: t1, t4, t5
- covers: c33, h23
- acceptance:
  - a session-scoped directive never outlives its session and never writes the durable lane (tested)
  - a durable directive survives a process restart through the host-visible persistence seam (round-trip test)
  - every scope record, ledger entry and event names the lane it belongs to

### t14 — Stage 3 live sessions (t14): a context-clear operator agent talks, works and brainstorms with the three-tier embodiment through a real host — directives visibly reach the Worker as inserted events mid-conversation, senses presents one coherent teammate, background review never blocks interaction, failures degrade visibly, and both persistence lanes are exercised live

- depends on: t8, t11
- covers: c28, h19
- acceptance:
  - session 1 follows issue #52 (the pre-filed context-clear instructions) verbatim; findings land in docs/live-test-results/scope-live-session-1.md with failures and INCONCLUSIVE first-class
  - a second follow-up issue for session 2 is authored during this task with session 1 findings folded in, and session 2 runs context-clear as well
  - the live record shows a strategist decision raised as an event and inserted into the Worker context at a safe boundary, and the operator experiences one coherent teammate throughout
  - a mid-session strategist kill degrades to the last valid directive visibly, with senses and presence unaffected; the non-intervention check is recorded

### t12 — docs close-out: README / CLAUDE.md / relationships.md present the three-authority-level design as opt-in, the salience row is rewritten only if the measured result supports it (otherwise the gap stays recorded), the League demotion is stated, and the bee-hive files are verifiably untouched

- depends on: t11, t14
- covers: c24, h16, c26, h18
- acceptance:
  - git diff over the whole plan shows zero changes to `arch_hive.py`, `worker_seam.py` and `worker_scoped_overhead.py`
  - the relationships.md salience row is updated only if t11 returns a supporting verdict; an honest negative keeps the no-owner row with the negative recorded
  - README and explain output state the strategist is opt-in, software-presence only (C2), and that a single-model run claims no strategist

## Risks

- [unknown_nonblocking] the worker role HAS a dated rate entry (C1-E cell, thinking on) but its rate is width-dependent — 76.4 falling to 29.8 tok/s between widths — and it was measured as a scoped seam on an easy cell, through the proxy; t10 must pick the conservative bound for the scoped-run calling pattern rather than reusing the width-1 figure (task t10)
- [unknown_nonblocking] which of the eight ScopeBench scenario families form the first-cycle subset is an operator decision at t9 preregistration time — the frame parked this (v1) and the plan inherits it (task t9)
- [unknown_nonblocking] strategist cadence and staleness constants depend on latency measured under the new seat load, not the historical muse numbers — `DEFAULT_STALE_LAG`=5 was a guess under an inverted assumption and must not be copied forward (task t10)
- [follow_up] the bee-hive relocation to another repo (frame park v2) is its own future frame — nothing in this plan moves or extends the hive
- [unknown_nonblocking] the durable-lane storage owner — host state round-tripped through the scope projector versus a continuity record — is a design decision inside t13; the frame parks it as v5 (task t13)
