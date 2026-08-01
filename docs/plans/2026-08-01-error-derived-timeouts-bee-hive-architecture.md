# Build Plan — error-derived timeouts + bee-hive architecture

slug: `error-derived-timeouts-bee-hive-architecture` · status: `exported` · from frame: `error-derived-timeouts-bee-hive-architecture`

> The harness family's model-call and fan-out timeouts are derived from measured error evidence — never chosen per file, never retried on a clock that already censored — and the orchestrator harness gains a bee-hive architecture: a cortex queen holding final authority over a saturation-sized swarm of workers coordinating through a bounded shared surface

## Tasks

### t1 — Rate measurement config: the dated, committed rate input every bound derives from

- covers: c39, h27
- acceptance:
  - a committed config in docs/live-test-results/ carries rate, date, n, model and concurrency condition; no rate literal appears in test code
  - deleting the config fails the bound test with a message naming the missing measurement, never a silent default
  - the re-derivation procedure (how to re-measure and update) is documented beside the config

### t2 — Bound test and constant raises, atomic: `test_timeout_bounds.py` plus main's below-bound constants raised in the same change

- depends on: t1
- covers: c2, h2, c4, h4, c13, h5, c14, h6, c16, h8, c36, h24
- acceptance:
  - pure helper `derive_timeout`(`max_tokens`, `rate_tok_s`) lives in the test module; the test walks all seven constants recomputing need-vs-timeout from committed inputs
  - a test-of-the-test proves red: any one constant mutated below its bound fails
  - the fan-out deadline is asserted against per-turn bound x granted turn budget, and main's `REQUEST_TIMEOUT` and `DEFAULT_FANOUT_TIMEOUT` are at or above bound in the same commit — main is never red at any commit
  - each harness constant carries a module-level comment naming budget, rate and derivation

### t3 — Running-series interlock: the fan-out deadline reaches owa/t12 via amendment 2 plus a recorded deviation before any manager/hybrid cell dials — or the censoring re-exam publishes

- covers: c3, h3, c6, h13
- acceptance:
  - if M/H cells remain undialled on owa/t12, the deadline raise lands there as a dated appended amendment plus a devague deviation record, never an edit to registered sections
  - if any M/H cell already dialled at 60s, every fanout-unit-absent in its committed results is re-examined as potential censoring and the outcome published either way
  - branch history shows no mid-series constant change outside the amendment protocol

### t4 — `league_commander` records re-exam: does the 1.21x margin hold, or were its published figures censored

- covers: c15, h7
- acceptance:
  - committed jsonl read for retries > 0 per call and wall clocks matching the 4x timeout + 3x 20s retry arithmetic
  - the verdict lands in corrections.md whichever way: clean stands recorded, dirty triggers the C1-E treatment for the 2.4-4.4x figures

### t5 — Streaming pilot in `worker_seam`, flag-gated: SSE client with two-phase derived bounds, metering parity, and died-stream records

- depends on: t2
- covers: c34, h23, c37, h25, c38, h26, c41, h28
- acceptance:
  - the client requests `stream_options` `include_usage` and a streamed call's committed record is field-identical to a non-streamed one (`finish_reason` plus all four token counts), asserted by a record-shape test
  - bounds are two-phase and derived: queue-aware time-to-first-chunk, inter-chunk idle only after the first chunk; a queued-request test proves queue wait is never charged as idle
  - a provoked mid-stream death yields a record distinguishable by explicit field from a completed turn, with partial content and reasoning retained
  - streaming is off by default and scoped to the cortex lane; non-streamed behaviour byte-identical with the flag off

### t6 — Arm B — the worker as a tool: harness-authored schemas, no goal-shaped prompts, structural termination

- covers: c17, h9, c11, h15
- acceptance:
  - a hermetic test walks every declared worker call and asserts an enumerable answer space with no free-text goal field
  - arm B's execution paths carry the same structural termination assertion the fan-out has (AST or step-count bound)
  - the worker never holds a turn: no loop, no finish authority, verified against the dispatch surface

### t7 — Arm P — the compiled policy: authoring lane, workspace-jail grading, and the three controls as dialable arms

- depends on: t6
- covers: c18, h10
- acceptance:
  - model-written policy source executes only inside the network-less workspace jail, proven hermetically
  - random policy, hand-written baseline and no-op exist as dialable control arms before any measured dial
  - escalation rate and call-acceptance are separate reported columns from outcome in the record shape

### t8 — Worker tiny-call overhead pre-measurement: what a scoped call actually costs at realistic context sizes

- covers: c43, h29
- acceptance:
  - N-call probe at widths 1 and 8 on Thor with realistic league-unit prompts and tens-of-token completions, committed like worker-throughput.md
  - prompt-token accounting per call is reported so the B1 sweep's granularity choice can cite it
  - runs only post-series or in a declared idle window, never contending with a live cell

### t9 — The new series pre-registration: width rung first, the B0/B1/B2 sweep, and its stated relationship to the still-climbing ladder

- depends on: t6, t7, t8
- covers: c20, h11, c22, h16, c44, h30
- acceptance:
  - registered before any measured dial, provable from git history; constants recomputed by a pin test from committed inputs
  - the outcome metric is proven mandatory-populated for every arm — an optional field any arm can decline is rejected at registration
  - call-acceptance is a per-arm axis and a B refusal rate at the #33 level is pre-declared as refuting the delegate-to-authored-artifacts claim
  - the opening section quotes the old series' t14 results doc by section, or states it registered first and why that is sound
  - the sweep's call granularity cites t8's overhead numbers

### t10 — Run the width rung and the sweep under the stop rule, artifacts committed as they land

- depends on: t2, t9
- covers: c28, h20
- acceptance:
  - the width rung runs first; every cell lands per-call records with `finish_reason` and token counts under the raised bounds
  - the mis-load numbers (rate, concurrency, saturation) are restated from this run's measurements wherever they moved
  - the stop rule is applied by code, not judgement, matching the old ladder's discipline

### t11 — Drone core: create / evoke / list verbs, the committed artifact shape, and the catalog surface

- covers: c23, h12
- acceptance:
  - create refuses to save a drone that has not passed a smoke invocation, proven by test before any real drone is authored
  - a drone is a committed, legible artifact — manifest with required one-line description, drone source, README — and list renders name, description, age and status
  - every verb takes --json, results to stdout, diagnostics to stderr, and explain/catalog.py covers the surface (the rubric gate passes)

### t12 — Drone safeguards: opt-in-off, staleness refusal, the content-hashed audit trail, and no escalation

- depends on: t11
- covers: c25, h17, c45, h31
- acceptance:
  - a fresh checkout with no explicit opt-in evokes nothing, asserted by test
  - list re-checks each drone's assumed surface and a drone whose assumptions fail refuses rather than reports
  - every evocation — including refusals and failures — records drone name, drone source content hash, capability set and call-acceptance; a run leaving no record is a test failure
  - an undecidable case returns 'I cannot' and the record shows the refusal; no escalation path exists in v1

### t13 — Governance continuity: the guards hold through the cycle

- covers: c10, h14
- acceptance:
  - tests/`test_governance.py` stays green on every PR: muse modules untouched, worker role in no reference-rig table without a supporting verdict
  - the drones-default-off guard joins the governance suite so the standing rule is enforced, not remembered

### t14 — Results, verdicts and the accountability map: every promise resolves to an artifact or an honest ABSENT

- depends on: t10
- covers: c1, h1, c27, h19, c29, h21, c30, h22
- acceptance:
  - decision rules quoted beside every verdict; separation, CEILING or INCONCLUSIVE published either way
  - the c30 targets are checked by value: clock-discarded completions in the series (target 0), constants under the CI test (target 7 of 7), drone second-evocation cortex calls (target 0) and token fraction (target at most 5 percent)
  - every before-state figure cites its measurement doc, and every after-state promise maps to a shipped artifact path or is marked ABSENT
  - wall-clock and token tables per arm per rung, corrections section, README reproduce lines

### t15 — Sibling closure: the findings travel — colleague, lobes-cli#168, and the deferred reasoning-delta probe

- depends on: t14
- covers: c26, h18
- acceptance:
  - a communicate post to colleague carries the timeout rule and streaming adoption with measured evidence — they dial the same cortex; no push into their repo
  - lobes-cli#168 is followed up with the pilot's live findings, and the reasoning-delta question is answered by probe only after the series releases the cortex
  - negatives travel too: any refuted prediction is reported alongside the wins

## Risks

- [unknown_nonblocking] the running series' timing gates t3 (amendment window), t8 (Thor idle) and t10 (ladder relationship); its remaining rungs and verdicts are not this plan's to schedule (task t8)
- [unknown_nonblocking] lobes-cli#168 is a sibling decision; streaming discovery falls back to explicit env/config if the advert does not land (task t5)
- [unknown_nonblocking] whether this rig's vLLM emits the SSE terminal usage chunk and reasoning deltas is unverifiable until the pilot's first live dial (park v4 carried plan-side) (task t5)
- [unknown_nonblocking] `arch_arms`' ARMS table is a shared surface: t6 and t7 are serialized by dependency; any third arm task must re-check file disjointness before joining a wave (task t7)
