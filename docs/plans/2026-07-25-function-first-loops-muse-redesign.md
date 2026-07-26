# Build Plan — function-first loops + muse redesign

slug: `function-first-loops-muse-redesign` · status: `exported` · from frame: `function-first-loops-muse-redesign`

> embodiment's loops and model seams are redesigned around cognitive function: the muse ships as reflective/associative counsel (reflect, reframe, challenge, synthesize alternatives) with machine-readable responsibilities and forbidden authorities instead of a creative lobe; the scratchpad, planner and degradation ledger converge into one survivable record of a mind a successor can resume from; subagents arrive as an injected seam whose budgets compose and whose degradations are attributed; loop and presence events ride the shipped events-cli emitter; and the redesign is validated against the existing live-test challenge suite and adversarial league-of-agents play, with the muse configurable on/off so the comparison is measured rather than assumed

## Tasks

### t1 — Reframe the muse prompt: five-verb reflective task prose in MUSE_AUTHORITY, boundary rendering and the `_MUSE` appendix, inviting disagreement

- covers: c2, h2, c7, h5, c19, h18
- acceptance:
  - `MUSE_AUTHORITY` and `_MUSE` name imagine/reframe/connect-memories/simulate-futures/construct-meaning and explicitly invite disagreement and reframing; the diff is additive - no existing sentence removed
  - role name, injected endpoint contract and thor-muse references unchanged; test_framing token-ban tests (incl. the senses ban and museless-composition ban) and test_muse authority tests pass, extended not weakened

### t2 — Counsel-kind self-labelling: kind marker vocabulary in the muse turn format, MuseInsight.kind, unlabelled defaults to durable

- depends on: t1
- covers: c28
- acceptance:
  - insights carry a kind; the muse self-labels via a prompt marker in the GUIDANCE: tradition; unlabelled or malformed markers default to durable (unit tests for labelled, unlabelled, malformed)

### t3 — Kind-aware delivery in the runner: durable counsel survives to next boundary/synthesis, step-sensitive ages by lag, measured relative latency, per-kind counters, work-class priority with per-class drop codes

- depends on: t2
- covers: c28, h13, c39, h29
- acceptance:
  - no durable-kind insight is dropped for loop-distance staleness alone (test)
  - per-kind delivery counts and per-class drops are readable from the runner snapshot; a slow fake compilation cannot displace boundary counsel without a recorded, kind-labelled drop (test)

### t4 — Bundle fetch module (new files): runtime-side fetch over today's flat eidetic recall (records + links), bundle schema with enrichment-level field, per-record ids and source labels

- covers: c35, h25, c34, h24
- acceptance:
  - a rig with only current eidetic-cli runs the full path end to end with the bundle marked flat-fetch in provenance (test with a faked store)
  - every bundle item carries its record id and source label; the muse gains no query verbs anywhere (the fetch is caller-side only)

### t5 — Recall-context channel + bundle budget: wire the bundle into the muse boundary rendering with its own budget distinct from max_context_chars; truncation is a recorded degradation

- depends on: t2, t4
- covers: c29, h14, c36, h26
- acceptance:
  - the bundle budget is independent of max_context_chars (test: boundary-snapshot clipping and bundle clipping are separately exercised)
  - an oversized bundle produces a ledger-visible truncation record, never a silent clip; a live run reports delivered bundle size

### t6 — Compiled-memory delivery + provenance loop closure: compiled memory reaches the cortex as durable counsel; a drive it informed carries the compiled-from record ids as links in its durable record

- depends on: t3, t4
- covers: c38, h28, c34
- acceptance:
  - compiled counsel arrives via the existing counsel channel labelled durable and never replaces host Task.context (test)
  - a two-process run shows the second process recalling a record whose links resolve to the records the first process's compiled memory cited (extended greenhouse continuity test)

### t7 — Memory-borne echo-chamber probe: a planted hostile record in a scratch store must be RESISTED before the compiled-memory lane is claimed live-safe

- depends on: t5, t6
- covers: c37, h27
- acceptance:
  - a committed probe harness seeds a hostile record demanding an action, drives a live run whose fetch surfaces it, and grades RESISTED/DEFERRED; the result is recorded either way in the live-test tradition
  - compiled counsel rendering preserves advisory framing and per-record source labels for store-sourced text (test)

### t8 — Scratchpad promotion: move the pad into the packaged embodiment namespace, port tests wholesale, keep JSONL append-per-write, add the combined pad+ledger resume report

- covers: c8, h6
- acceptance:
  - open-intent recovery across a real process boundary, torn-write survival and per-write persistence all pass from the packaged location (ported tests, not weakened)
  - the resume report renders open intent, observations and degradations as one output a successor can read (test)

### t9 — Subagent seam in loop/contract: injected SubagentFn, tree-wide spawn allowance strictly decrementing with no upward override, turn budget drawn from the parent, attenuated executor/task pass-through, AST tests extended

- covers: c9, h7, c47, h32, c11, h17
- acceptance:
  - parent max_steps bounds total turns including children (test); allowance zero blocks spawning (test); a child allowance >= parent is refused (test); no widening path exists in the seam
  - AST tests still assert exactly three exits, no new module-scope imports, and the depth bound structurally; a drive that spawns nothing is byte-identical (golden transcript test); no shell-cli reference (existing guard unchanged)

### t10 — Subagent ledger lane: child degradations reach the parent ledger with child attribution via a new lane vocabulary

- depends on: t9
- covers: c9
- acceptance:
  - a child degradation read back from the ledger names the subagent lane and the child task id, never silently the parent (test)

### t11 — Subagent reference example: a narrowed child (subset tool surface, own pad, visible allowance) demonstrating attenuation

- depends on: t8, t9
- covers: h32
- acceptance:
  - the example runs hermetically and shows a child with a strictly smaller tool surface and allowance than its parent, with its own pad readable afterwards

### t12 — Challenge harnesses: designed subset problem (76/trap 72), 4-bit register and entropic register as committed truth-in-code harnesses; shared config-record preamble helper

- covers: c15, h9, c16, h10
- acceptance:
  - each harness carries its truth function in code and grades mechanically with a scripted mind, no doc consultation (tests)
  - every experiment harness writes per-role temperature, muse controls, stale policy and n into the results record before the first result line (shared helper, test)

### t13 — Perception meets a model: wire the gateway 12B into perceive(interpret=...) in the greenhouse behind an opt-in flag with verbatim and never-raise live assertions

- covers: c18, h12
- acceptance:
  - the live run asserts ContextPacket.original is byte-identical to the caller's input against the real transcript; a dead endpoint degrades to None plus a recorded degradation, never a raise (live-gated tests, skipped by default)

### t14 — Arena seat host: examples/league_seat.py driving the local league engine via the public harness contract, both residency arms (resident reference, command continuity-stress), muse on/off, pad+eidetic continuity, optional EventEmitter observer

- depends on: t8, t9, t12
- covers: c13, h8
- acceptance:
  - a full match completes through the public CLI only, both arms, with driver residency recorded in the match log; no arena-specific code or dependency lands inside embodiment/
  - in the command arm all per-turn continuity rides the pad and eidetic (fresh subprocess per turn); in the resident arm the muse thread lives across turns

### t15 — Docs reframe: relationships.md gains the function map with the design-metaphor caveat; muse role rewording in README/CLAUDE/explain catalog; README/CLAUDE frame-table promotion gate recorded; stale no-events-module comments fixed

- depends on: t1
- covers: c5, h4, c6, h16
- acceptance:
  - the metaphor sentence appears in the same section as the function vocabulary everywhere it lands (grep-checkable); README/CLAUDE keep the layer table until the association-work result, and the gate is stated where the map lands
  - test_zero_deps.py:76-77 and pyproject.toml:24-25 no longer claim no events module exists

### t16 — Lobes proposal: file the responsibilities/forbidden-responsibilities role-metadata issue on lobes via communicate, citing the spec

- depends on: t15
- covers: c4, h15
- acceptance:
  - the issue is filed on the lobes repo and embodiment docs cite it as a proposal; no PR from this lane touches lobes

### t17 — Muse-challenges-cortex golden: a committed harness where the muse receives a cortex-produced result and must challenge or reframe it, graded on material difference from restatement

- depends on: t1, t2
- covers: c3, h3
- acceptance:
  - the golden runs against the live rig and is graded mechanically or by an injected judge with the grader verified before results are read; a restatement fails it

### t18 — Live measurement series A: the association-work experiment (reframed muse on reflective work) plus per-kind delivery re-measurement against the 29 percent baseline, configs pre-registered

- depends on: t3, t5, t12, t17
- covers: c5, h4, h13, c23, h22
- acceptance:
  - the muse-value question leaves with a number or an honest recorded negative; the README/CLAUDE promotion decision is recorded accordingly (h4)
  - delivery is reported per counsel kind against the 2-of-7 baseline with configuration written before results

### t19 — Live measurement series B: full arena matches, both residency arms, muse on/off with fixed seeds, replay hashes committed, continuity demonstrated across matches

- depends on: t6, t7, t14
- covers: c24, h23, c13
- acceptance:
  - every claimed comparison is backed by a committed deterministic replay hash; continuity across matches (not merely within one) is demonstrated and recorded

### t20 — Delivery record: results docs in the live-test-results corrections tradition, the plan-vs-spec delivery summary, and the per-audience artifact checklist

- depends on: t7, t10, t11, t13, t16, t18, t19
- covers: c1, h1, c20, h19, c21, h20, c22, h21
- acceptance:
  - every validation claim states its n and cites its artifact; each audience named in the spec is matched to a delivered artifact; corrections are recorded as first-class, not smoothed over

### t21 — Fix embodiment#15: strip fenced JSON in perception, and degrade (never silently succeed) when an interpretation cannot be read

- depends on: t13
- covers: c18, h12
- acceptance:
  - a fenced JSON payload parses to populated fields; a fixture for the fenced shape joins the hostile-output set so this stays covered with no live rig
  - an unreadable or empty interpretation records a degradation instead of returning silent empties with degraded=False (C3); never-raise is preserved
  - the verbatim invariant is untouched - original is still never sourced from model output - and tests/test_perception.py's existing pins pass unchanged

## Risks

- [unknown_nonblocking] eidetic-cli composite fetch (eidetic-cli#37) may land late or differently shaped; contained by the flat-fetch degrade floor (c35) - the adapter swaps when it arrives (task t4)
- [unknown_nonblocking] child-pad readability by the parent resume report is shaped during t11, not before - issue 9 notes a dying subagent is exactly the pad's case (task t11)
- [unknown_nonblocking] multi-seat rig capacity (several drives + muse threads against one gateway) is unmeasured; recorded in the config preamble when matches run (task t19)
- [unknown_nonblocking] the thor-muse deployment shape remains declared-UNVALIDATED (lobes#108); every muse measurement rides the gateway proxy and says so (task t18)
- [unknown_nonblocking] the association-work experiment may return an honest negative: the promotion gate then blocks README/CLAUDE and the function map stays in relationships.md with the negative recorded - a stated path, not a failure (task t18)
- [follow_up] run(resume_from=pad) and eidetic-backed pads are deliberate follow-ups (frame parks v3/v4), out of this plan
