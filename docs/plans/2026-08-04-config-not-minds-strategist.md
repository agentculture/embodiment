# Build Plan — config-not-minds strategist

slug: `config-not-minds-strategist` · status: `exported` · from frame: `config-not-minds-strategist`

> the strategist changes configuration, not minds: typed, gated, tested and revertible prompt/example/verification changes replace advisory directives; the worker is unaware of the strategist; senses relays the world to the embodiment and the inner state to the world; no default changes until the config-change arm is measured against the advisory arm and the ungoverned control

## Tasks

### t1 — Probe: the worker drives a bounded tool loop live (cortex-toolcall-probe pattern pointed at the worker role, 2-3 steps)

- instruction: Do not build on the three-tier shape before this returns. Copy docs/live-test-results/cortex-toolcall-probe.md's pattern, point it at the worker role via the lobes gateway, and script it (never inline one-liners). Derive every clock from `max_tokens`/rate per tests/`rate_config.py` - a clock sized against the wrong quantity becomes the measurement. Coordinate rig time with the operator before dialling.
- covers: c27, h12
- acceptance:
  - a committed probe script and result doc measure: loop completion over >=2 tool steps, tool-call format compliance, the #33-class malformed-args rate, and truncation behaviour under the loop
  - the result is published whichever way it comes back; a failed probe is a named trigger for /deviate on the three-tier decision, not a quiet retry

### t2 — Fix #58: `SCOPE_AUTHORITY` states the `scope_id`-must-be-new rule

- instruction: Text-only change to `SCOPE_AUTHORITY` in embodiment/scope.py plus a test. Do not touch the register's logic - the rule already exists in code (scope-directive-duplicate-id), it is the prompt that never states it.
- covers: c16, h4
- acceptance:
  - the authority text names all four admission rules; a test asserts each rule appears; the advisory arm is graded against the updated text

### t3 — Change-unit schemas: one typed dataclass per target (worker tools/prompts/knowledge/permissions; senses prompts/permissions/knowledge)

- instruction: New module(s) only. Do not edit scope.py or `scoped_run.py`: the advisory lane must stay dialable as t13's comparator. Reuse the refuse-whole pattern from scope.py:1288-1314 by citation, not by import-coupling. Resolve plan risk r2 (capability-id enumeration) here and record what you decided.
- covers: c2, h6, c15, h3, c29, h13
- acceptance:
  - an unknown or extra-keyed unit is refused whole and recorded (FORBIDDEN-keys pattern reused)
  - origin is a required field; a worker-originated unit aimed at a prompt-shaped target is refused whole, proven by test
  - tools/permissions units reference host-declared capability ids only; a free-form tool definition is a refused shape
  - new module(s), no edits to scope.py/`scoped_run.py` - the advisory lane is untouched

### t4 — Propose->verify->apply lifecycle with per-seat quiescence

- instruction: The apply gate is seat-idle AND suite-pass; the drone stage-smoke-save flow (embodiment/drone.py:1795-1885) is the pattern, moved to runtime. Prove the per-seat invariant the way live session 1 proved constant system-prompt sha.
- depends on: t3
- covers: c34, h23
- acceptance:
  - propose/verified/applied are distinct recorded states; apply is gated on seat-idle plus suite-pass
  - a mid-run seat never observes a config change: config identity is constant within any single run, proven by test (the per-seat constant-sha analogue)
  - a proposal whose verification suite fails is never applied and the failure is recorded, like a failed drone smoke

### t5 — Config ledger, events and fail-closed persistence

- instruction: Follow `scope_events.py`'s translation pattern (pure functions, defensive reads, one envelope builder). Build the advisory-era fixture from a real examples/`scope_live_session.py` --state payload, not a hand-written stub.
- depends on: t3
- covers: c8, h8, c31, h21
- acceptance:
  - proposed/verified/applied/rejected/reverted events emitted through the `scope_events` translation pattern; the applied-change ledger persists through the ScopePersistence port
  - the persisted payload carries a schema version; an advisory-era or unknown-version payload is refused with one recorded degradation naming the fix, proven against a real advisory-era fixture - never a silent reinterpretation, never a crash

### t6 — Revert-to-baseline and ratchet mechanics

- instruction: Revert must restore configuration state exactly; it does not unsay what was already said - t7's ledger is what keeps that honest. The ratchet check re-evaluates against a FIXED baseline, not against the previous change.
- depends on: t5
- covers: c7, h7
- acceptance:
  - reverting to baseline is always possible and exercised by test
  - successive gate-passing changes can be re-evaluated against the fixed baseline, so drift each individual gate misses is detectable

### t7 — Effective-config introspection, derived from the ledger alone

- instruction: Derive from the applied-change ledger alone. A parallel bookkeeping structure here would be the exact drift this repo keeps catching - if the ledger cannot explain a config state, that is a recorded degradation, not a gap to fill from elsewhere.
- depends on: t5
- covers: c32, h22
- acceptance:
  - a host-callable report renders each seat's current effective configuration with provenance (which change, when, which gate verdict), derived from the applied-change ledger and nothing else
  - a config state the ledger cannot explain is itself a recorded degradation (C3)

### t8 — Knowledge block on eidetic: designated scope, attributed, strategist-maintained

- instruction: Compose eidetic, do not reimplement memory (issue #2). Use the scope+visibility convention v1 (eidetic docs/contract.md). Note eidetic-cli is already an approved base dependency per d2 - tests/`test_zero_deps.py` pins the exact set and fails on any delta in either direction.
- depends on: t3
- covers: c35, h24, c30, h20
- acceptance:
  - knowledge units are eidetic records in a designated scope per the scope+visibility convention; worker writes via remember, senses reads via recall, strategist maintenance uses eidetic's consolidation/supersession verbs
  - attribution rides eidetic's native provenance; an unattributed write is refused whole, proven by test
  - the knowledge path imports no store of its own, proven by import test; the knowledge-as-attributed-claims clause ships as composable senses text

### t9 — Config-lane runner composed from kept plumbing, advisory lane untouched

- instruction: The zero-diff pins are the deliverable, not a side effect: existing suite green unmodified, AST import-closure test green, unarmed governor forwards by identity. Reuse `strategist_runner.py`'s lifecycle by citation (cite-don't-import), the way `strategist_runner.py` itself was copied from `muse_runner.py`.
- depends on: t4, t5
- covers: c1, c3, h14
- acceptance:
  - the config reviewer reuses the runner thread lifecycle, bounded four-exit review loop, and degrade-never-raise ledger discipline; the advisory lane's own suites stay green unmodified
  - loop.py stays zero-diff: the existing suite plus the AST import-closure test pass unmodified, and an unarmed governor forwards the host's objects by identity, proven by identity test

### t10 — Governance guard + CLAUDE.md drift fix

- instruction: Copy TestDronesShipOptInAndOff's shape including its TestTheseGuardsCanFail case - a guard nobody proved can fail is a guard nobody has. The CLAUDE.md sentence to fix is in the 0.12.0 verdict section: it claims `test_governance.py` enforces the strategist rule; it does not.
- covers: c12, h10, h1
- acceptance:
  - TestStrategistShipsOptInAndOff lands in tests/`test_governance.py` on the drone-guard template (opt-in pinned False, env fails closed, AST single-bypass) with a TestTheseGuardsCanFail case proving it can go red
  - the CLAUDE.md sentence claiming `test_governance.py` already enforces the strategist rule is corrected in the same change; any default flip is gated on the ScopeBench re-run verdict

### t11 — Host-composable senses grounding text (embodiment-side only)

- instruction: embodiment-side only. The clause text is measured verbatim in docs/live-test-results/senses-grounding.md (0/16 vs 16/16) - ship it as measured, do not reword it. Anything needing colleague's cooperation is a filed issue via the communicate skill, never a push.
- covers: c4, h15
- acceptance:
  - the measured grounding clause ships as a composable constant documented as required (the #63 recommendation), beside the knowledge-attribution clause's home
  - no change of this frame touches colleague: the delivery record shows embodiment-only changes; senses-half asks on colleague remain filed issues

### t12 — Three-tier example host: senses-worker-cortex/strategist, wired from documented seams alone

- instruction: Wire it the way a stranger would - if you need to read package source to get it working, that is a #62-class trap and documenting it at the seam is part of this task. Streaming is the default transport with both bounds derived (queue-aware first-chunk, then inter-chunk idle); every clock must pass tests/`test_timeout_bounds.py`.
- depends on: t8, t9
- covers: c22, c23, h17
- acceptance:
  - the host wires worker-as-actor under config governance, cortex as strategist, senses as relay, using documented seams only - any wiring step that required reading package source is recorded as a new #62-class trap and documented at the seam in the same change
  - the proxied acting path uses streaming with both derived bounds and every clock passes the CI-enforced timeout tests
  - a matched ungoverned control mode replays the same script with no strategist armed

### t13 — ScopeBench pre-registration + config-change arm as data

- instruction: Pre-registration is committed BEFORE any dial - that ordering is the gate. Arms are data (a new ScopeArm entry), controls stay in their separate namespace. Address plan risk r1 (cross-type composition) explicitly or declare it out of this bench's scope in writing; silence is what voided cells last time.
- depends on: t1, t2, t9
- covers: c9, h9, c16, c14, h2, c24
- acceptance:
  - the config-change arm is a new ScopeArm entry; the pre-registration commits the seven conditions plus a ratchet condition and a new absent-cell reason BEFORE any dial
  - the per-change-type three-way verdict ladder (allow ungated / gate / shut off) is written into the pre-registration, and the senses checkpoint delta (coolthor -> unsloth QAT w4a16) is named as a rig confound
  - worker-as-actor cells and protocol floors for the layer control are declared, so the A2-analogue cannot silently void again

### t14 — Run the bench; publish whichever way it comes back

- instruction: Publish whichever way it comes back, defects included. One ABSENT condition forces INCONCLUSIVE and INCONCLUSIVE leaves the shipped rig untouched - that rule is what keeps this repo's record worth anything. Watch for truncation: no `finish_reason` reaches the loop (#37), so a truncated turn and a deliberate one look identical.
- depends on: t6, t13
- covers: c25, h19, c27, h12, h18
- acceptance:
  - the result lands in docs/live-test-results/ defects included; one ABSENT condition forces INCONCLUSIVE and the shipped rig stays untouched
  - each change type's verdict is recorded beside the measurement that produced it; the worker-promotion flag flips only in the same change that cites this verdict, and the motivating session-1 numbers stay cited as n=1 reports

### t15 — Live session 2 (#73 protocol) on the redesigned tier

- instruction: Three arms, all recorded: our benchmarks, the main agent's own working experience reflected back to the operator in the first person, and the operator's own live test. Follow #73's protocol with a matched ungoverned control replaying an identical script. The agent-experience arm is experience, stated as n=1 impression - it never outweighs the pre-registered verdict.
- depends on: t7, t11, t14
- covers: c17, h5, h16
- acceptance:
  - a matched ungoverned control replays the identical script; the session is scored on measurably-better versus materially-identical, and the effective-config introspection report audits every applied change in the session record
  - run on a non-colleague host (issue #2's definition-of-done); C2-honest wording throughout the report
  - the live test has three arms, all recorded: (1) our benchmarks - the ScopeBench verdict from t14; (2) the main agent's own working experience driving the redesigned tier, reflected back to the operator as a first-person report naming what helped, what got in the way, and what it cost; (3) the operator's own live test
  - the agent-experience arm is reported as experience with its provenance stated - a single agent's n=1 impression, never presented as a measurement, and never allowed to outweigh arm (1)'s pre-registered verdict

### t16 — Docs: README + explain carry the tier honestly

- instruction: Every number cited names its n and its rig. Negative and INCONCLUSIVE results are stated as such. Do not let 'deterministic' back into the wording: the change is deterministic, the effect is not.
- depends on: t14, t15
- covers: h18, c23, h17
- acceptance:
  - README and explain document the tier as opt-in and off, state the no-determinism wording (the change is deterministic, the effect is not) and the C2 'diverse mind entity' discipline, and document both known trap classes at the seam
  - every measured number cited in docs names its n and rig; negative and INCONCLUSIVE results are stated as such

## Risks

- [unknown_nonblocking] cross-type composition: two individually-gated changes composing into an effect no gate evaluated - the pre-registration must address it or declare it out of its scope explicitly (task t13)
- [unknown_nonblocking] capability-id enumeration: no enumerable capability catalog seam exists in the loop contract; how ids are enumerated and kept stable across hosts and restarts must be resolved during schema design (task t3)
- [unknown_nonblocking] worker acting feasibility: if the t1 probe fails, the acting-seat promotion and the three-tier decision c26 are invalidated - the named response is /deviate, never a quiet retry or model swap (task t1)
- [unknown_nonblocking] rig contention: bench and live-session tasks dial the operator's rig (local cortex, proxied worker and senses) - scheduling is the operator's call, and the t24/`finish_reason` truncation trap applies to every dialled seat (task t14)
