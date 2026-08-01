# error-derived timeouts + bee-hive architecture

> The harness family's model-call and fan-out timeouts are derived from measured error evidence — never chosen per file, never retried on a clock that already censored — and the orchestrator harness gains a bee-hive architecture: a cortex queen holding final authority over a saturation-sized swarm of workers coordinating through a bounded shared surface
> instruction: ships as one cycle: the CI timeout-bound test and fan-out deadline derivation on main; B and P arms pre-registered then dialled with the width rung first; the drone skill opt-in and off. Verified when all three c30 signals have fired or honestly failed

## Audience

- the operator running the rig; the arch-series experiment itself; and harness consumers — colleague and future hosts — who inherit the timeout rule everywhere and the drone skill if the tier validates
  - instruction: the spec names the three audiences; colleague learns via the t19 findings post and lobes via lobes-cli#168 — no push into either repo

## Before → After

- Before: seven per-file timeout constants, one of which already censored a completion-length distribution while recording `truncated: false`; a flat-favouring verdict measured entirely on the counsel/sequential axis with the width rung never reached; and no reuse of authored intelligence — every recurring task re-pays a full cortex turn (5,000-14,265 tokens, 400-730s)
  - instruction: each figure cites its doc: the censoring record (CHANGELOG 0.10.0 / #41), the off-axis verdict (#44 section 0), the authoring cost (5000-14265 tokens, 400-730s)
- After: no timeout can censor evidence again — every client timeout and wait deadline is derived from its token budget and CI breaks on drift below the bound; the tier question has a measured dose-response curve (B0/B1/B2 sweep, P with its controls) instead of priors; and authored intelligence is reusable — an evoked drone repeats a recurring task for tens of worker tokens and zero cortex turns
  - instruction: each promise maps to a plan task at /spec-to-plan time: CI bound test, streaming client, width rung, B0/B1/B2 sweep, P controls, drone verbs

## Why it matters

- the rig is mis-loaded: the 27B cortex thinks at ~23 tok/s strictly sequentially while ~9x of measured worker concurrency and unbounded bot-code width sit unused; maximizing the models means putting each unit of work on the cheapest tier that holds quality — and the only axis where the unused capacity must win by construction (width) has no evidence either way
  - instruction: cite the measured numbers where used: 23 tok/s sequential cortex, ~9x effective worker concurrency, saturation near width 8 (worker-throughput.md); restate if remeasured

## Requirements

- main ships `REQUEST_TIMEOUT = 300.0` in examples/`worker_seam.py` while its own CHANGELOG 0.10.0 records the derived raise to 1200.0 (amendment 1, deviation d4) — the raise exists only on branch `owa/t12` (commit 25a65b7); the timeout work must reconcile main with the changelog claim it already carries
  - instruction: when owa/t12 merges, assert main's `REQUEST_TIMEOUT` equals the CHANGELOG value; add the constant to the c14 test's walked set so it can never drift silently again
  - honesty: when this frame's work ships, main's `REQUEST_TIMEOUT` satisfies the c13 bound and equals what the CHANGELOG claims — verified by the c14 CI test reading the constant, not by prose
- `DEFAULT_FANOUT_TIMEOUT = 60.0` (examples/`orchestrator_tools.py`, unchanged on main and `owa/t12`) sits against the worker's measured p95 latency of 40.78s at width 14 on 1200-token completions; at series budgets a dispatched unit outruns it and is declared `fanout-unit-absent` — the same evidence-censoring shape amendment 1 fixed at the transport layer, waiting one layer up before any manager/hybrid cell dials
  - instruction: raise `DEFAULT_FANOUT_TIMEOUT` to its derived bound (c16 formula) in the same PR that lands the CI test; grep committed fan-out results for fanout-unit-absent and publish the re-exam either way
  - honesty: before any manager/hybrid cell dials, the fan-out deadline is raised to its derived bound — or every `fanout-unit-absent` in committed results is re-examined as potential censoring and the outcome published
- timeout lengths are derived from measured evidence, not chosen per file: the amendment-1 rule (`max_tokens` / slowest measured tok/s, so the token budget binds instead of the clock) becomes the family-wide policy for the scattered constants — 60s fan-out, 300s worker seam and batch wait, 600s muse-arms and devague-legs, 900s league-commander, 1800s h2h
  - instruction: one module-level comment per harness naming budget, rate and derivation; the CI test recomputes and compares — #42's audit table enumerates the seven constants
  - honesty: each harness's timeout cites the `max_tokens` and the measured rate it derives from, in committed config or a constant the CI test recomputes — a value with no citable inputs fails the test
- the timeout rule is #42's: `REQUEST_TIMEOUT >= max_tokens / slowest_measured_generation_rate` — derived from the budget, never a latency percentile, because the timeout censors the tail a percentile would be sized from; above the bound an over-long turn self-reports as `finish_reason == "length"`, below it the identical turn vanishes into a transport retry. The rate is cited per rig (21.5 tok/s slowest, pre-registration section 18), never guessed
  - instruction: implement as a pure helper `derive_timeout`(`max_tokens`, `rate_tok_s`) in the test module (not embodiment/, per the q4 decision); rate cited from preregistration section 18 — 21.5 tok/s slowest
  - honesty: the bound is computed only from committed inputs (budget and cited rate) — never from an observed latency distribution, which the current timeout may already have censored
- the bound is enforced in CI: `test_timeout_bounds.py` walks the harnesses, recomputes `max_tokens` / RATE per file from committed inputs, and fails on any timeout below its bound — a timeout that drifts below the bound breaks CI rather than waiting for a series to notice (#42 proposed work 1)
  - instruction: tests/`test_timeout_bounds.py` walks the seven constants; include a test-of-the-test mutating one below bound and asserting red
  - honesty: `test_timeout_bounds.py` is proven able to fail: with any one constant mutated below its bound the test goes red — a test-of-the-test, per the M2/M3 discipline
- `league_commander`'s committed records are re-examined before its figures are re-cited: at 1.21x it is the narrowest passing margin in #42's audit, and it produced the 2.4-4.4x hierarchy-cost figure this repo cites; `retries > 0` per call decides whether cut turns inflated cost and understated correctness — if clean, say so and the margin stands (#42 proposed work 2)
  - instruction: read league-commander's committed jsonl: retries per call, and wall clocks matching the 4x timeout + 3x 20s retry arithmetic; publish the verdict in corrections.md whichever way it lands
  - honesty: the re-exam reads committed records — `retries` per call and wall-clock against tokens/rate — and publishes the outcome either way: clean stands recorded, dirty triggers the same treatment C1-E got
- \#42's audit covers six client timeouts but not the fan-out wait deadline: `DEFAULT_FANOUT_TIMEOUT = 60.0` bounds a whole unit drive (up to `max_steps` turns, each up to the unit's `max_tokens`), so its bound is the per-turn bound times the turn budget, not one turn's — below it, units land `fanout-unit-absent` and the fan-out layer censors exactly the way the transport layer did
  - instruction: fan-out bound = `derive_timeout`(unit `max_tokens`, rate) x the turn budget actually granted (`DEFAULT_FANOUT_MAX_STEPS` clamped); asserted by the same CI test
  - honesty: the fan-out bound multiplies the per-turn bound by the turn budget actually granted to the unit, and the derived value is asserted by the same CI test that guards client timeouts
- arm B — the Bee-Hive's core claim: the worker is a tool the cortex calls, never an agent — no loop, no turn, no goal, `transform(N things) -> N results`; the only arm that takes lobes' `worker.forbidden_responsibilities = [final_decision, security_decision]` literally, and it is measured against M and H before further investment in either
  - instruction: B's call sites declare literal JSON schemas in the harness; a hermetic test walks every declared call and asserts an enumerable answer space with no free-text goal field
  - honesty: arm B's worker calls carry no goal-shaped prompt: every call's schema is authored by the harness and its answer space enumerable — asserted structurally by schema inspection in hermetic tests, not by intent
- arm P — the cortex authors a unit-control policy as code once (one model call per episode), the policy plays at ~0ms per decision; graded by the machinery that already exists (`challenge_coding.py`'s workspace jail, adapted as k1.py was); three controls are mandatory — random policy, hand-written baseline, no-op — and escalation rate and call-acceptance are reported axes that never share a number with outcome
  - instruction: P adapts `challenge_coding.py`'s jail to grade policy source against league episodes; the three controls are registered in the pre-registration before any dial; escalation rate and call-acceptance are separate result columns
  - honesty: P's three controls (random policy, hand-written baseline, no-op) are pre-registered before any measured dial, and escalation rate and call-acceptance are reported as separate axes from outcome in the committed results
- the caste vocabulary and its structural claim: every delegation is to an artifact the cortex authored — a worker-harness (code borrowing bounded worker intelligence for scoped typed questions) or a code-drone (the mind's strategy compiled, frozen at authoring time); nothing is delegated to a raw mind. Falsifiable prediction: the #32/#33 interface-failure class disappears because the cortex authors the schema it delegates across, measured as call-acceptance
  - instruction: call-acceptance per arm is a first-class results column; a B refusal rate at #33's level (74%) is published as refuting this claim, by name
  - honesty: call-acceptance is measured per arm and the prediction is falsifiable both ways: a B arm showing #33-class refusal rates is published as refuting the delegate-to-authored-artifacts claim, not smoothed over
- the drone skill: /drone create / evoke / list — a drone is a named, committed, legible artifact (`.drones/<name>/` with manifest, drone.py, README), its one-line description is required at create, it is smoke-proven before save (well-shaped is not runnable — the K1 lesson), and list surfaces staleness by re-checking each drone's assumed surface; a drone whose assumptions fail refuses rather than reports
  - instruction: skill wrapper plus embodiment drone CLI verbs; `.drones/<name>/` holds manifest.json, drone.py, README.md; create refuses to save without a passing smoke run; list runs assumed-surface checks; explain/catalog.py entry per repo convention
  - honesty: `create` refuses to save a drone that has not passed a smoke invocation, and `list` runs each drone's assumed-surface check — both proven by tests before any drone is authored for real use
- the harness family adopts SSE streaming for model calls (the operator's direction, 2026-08-01): with a stream, the client timeout becomes an inter-chunk idle bound orthogonal to generation length — no amount of thinking can hit the clock, and reasoning is visible as it arrives; the derived total bound (c13) stays as the outer backstop for dead transports
  - instruction: adopt streaming first in one harness (`worker_seam`) behind a flag: SSE parse, inter-chunk idle bound (~60s), the c13 bound as outer backstop for dead transports; partial reasoning kept in the transcript record; discovery rides lobes-cli#168's advert when it lands
  - honesty: verified in lobes source that the gateway relays SSE chunk-by-chunk, and the retry-semantics change is designed for: a stream that dies mid-body is never blindly re-run whole — partial thinking is kept where the record allows, and only a dead transport triggers the outer bound

## Honesty conditions

- the shipped surface matches the announcement: budget-derived timeouts CI-enforced, and the bee-hive measured — B and P dialled against E/W/M/H — not merely designed; an unmeasured design does not count as shipped
- no commit on owa/t12 changes a timeout constant or arm set mid-series except as a dated, appended amendment plus a recorded deviation — verifiable from branch history
- tests/`test_governance.py` stays green throughout: no hive or drone work touches embodiment/ muse modules or promotes the worker role without a supporting verdict
- every hive/B/P execution path carries the same structural termination assertion the fan-out has (AST or step-count bound), not only behavioural tests
- the pre-registration names its outcome metric and proves it is not an optional field — a metric any arm can decline to populate is rejected at registration time
- no default-on drone path ships: a fresh checkout with no explicit opt-in evokes nothing, asserted by a test
- the spec names all three audiences, and colleague inherits through the t19 findings post — never by a push into their repo
- every before-state figure stays cited to its measurement doc and is corrected there if superseded, never restated from memory
- the mis-load claim rests on the measured numbers (23 tok/s sequential cortex, ~9x worker concurrency, saturation near 8); if remeasurement moves them the claim is restated, not defended
- each after-state promise maps to a plan task with acceptance criteria — no promise reaches the spec without a coverage target
- each signal is machine-checkable when it fires: a red CI run, a published verdict document, a drone evocation record showing zero cortex calls

## Success signals

- three signals, one per lane: `test_timeout_bounds.py` goes red on any timeout mutated below its derived bound; the width rung produces either measured separation or an honest CEILING/INCONCLUSIVE published either way; and a drone's second evocation completes its task with zero cortex turns, reporting call-acceptance as its own axis
  - instruction: measured targets: 0 clock-discarded completions in the next measured series; 7 of 7 timeout constants under the CI test; a drone's second evocation makes 0 cortex calls at <=5 percent of its authoring tokens; the width rung publishes a verdict — separation, CEILING or INCONCLUSIVE — either way

## Scope / boundaries

- the running t12 series (worktree .worktrees.embodiment/t12, ladder mid-C2 with C1 recorded CEILING) is pre-registered; no timeout value or architecture changes mid-series except through the pre-registration's own amendment protocol plus a recorded deviation — the path amendment 1 already walked
  - instruction: verify from owa/t12 branch history: any mid-series timeout or arm change appears only as a dated appended amendment plus a devague deviation record
- the hive lives in examples/ and changes nothing under embodiment/; the worker role enters no reference-rig table without a supporting verdict — tests/`test_governance.py` pins `_WORKER_ROLE_HAS_SUPPORTING_VERDICT = False`
  - instruction: run tests/`test_governance.py` in every hive/drone PR's CI; the worker-promotion flag flips only in the PR that lands a supporting verdict document
- bounded termination survives the swarm: every hive worker runs under a bounded grant, every wait is finite, and a hung worker cannot park the queen's drive — the fan-out's own stated construction, inherited rather than reinvented
  - instruction: reuse the fan-out's structural termination test shape (AST or step-count bound) for every B and P execution path; a behavioural pass alone does not close this
- the environment is the league lane — the grader is not ours (corrections.md section 2: four self-authored graders wrong in the flattering direction in one cycle), it is genuinely multi-unit so width is real, and the harnesses are committed; hard exclusion: no metric may be an optional field one arm happens to populate more often — the league-h2h lesson
  - instruction: the pre-registration names the outcome metric and asserts it is mandatorily populated for every arm — league outcome, never an optional team-message field
- drones ship opt-in and off until #44's experiment validates the tier — the standing rule's mirror image: an unmeasured behaviour does not ship as default either; and the execution-security model for model-written code is decided before a line is written — `shell-cli` owns the tool surface, and C2 means stating the threat model rather than letting a name imply a sandbox
  - instruction: a test proves a fresh checkout with no explicit opt-in evokes nothing; the manifest carries declared capabilities per the q5 decision

## Non-goals

- the muse seam and d15's muse-off reference rig are untouched — a hive is orchestration below the cortex, not counsel beside it; no muse module is edited (the governance guard already pins this)

## Assumptions

- retrying a clock-timeout on the same clock re-runs the whole generation and discards it — 4x300s + 3x20s = 1260.0s of work recorded as NO ANSWER while `truncated: false` was logged throughout; an error-informed retry escalates the allowance on a timeout error rather than repeating the clock that just censored
- hive width is sized from measured saturation — 8, where effective concurrency reads 77% of ideal — never the advertised x14, which lobes has relabelled a KV-pool ceiling; docs/live-test-results/worker-throughput.md is the sizing authority
- the flat-favouring verdict measured the axis where orchestration cannot win — counsel and sequential reasoning — while rung F (fan-out width), where separation is structural because a flat arm cannot dispatch concurrent units at all, was never reached; the width rung runs first as the cheapest decisive experiment (#44: 'run the width rung first')
- scope size is the independent variable, swept rather than toggled: B0 rigid (0 calls) / B1 scoped questions (typed, small answer space, code keeps control flow) / B2 agentic (= M, an open goal, worker holds control); pre-registered prediction — quality rises then plateaus while cost rises monotonically, so the knee is the finding, and no on/off design can locate a knee (every prior arm comparison here died tied at a ceiling)
- drone economics, stated before building: authoring costs one cortex turn (measured 5,000-14,265 completion tokens, 400-730s); a one-shot task is pure loss, 2-3 uses roughly break even, recurring tasks on stable surfaces win by a widening margin and ~9x again under fan-out — the failure mode is quiet waste, not a crash

## Scope exploration

- `s1` — `examples/worker_seam.py:272 + CHANGELOG.md 0.10.0 + branch owa/t12`: main's checked-in `REQUEST_TIMEOUT` is 300.0 while CHANGELOG 0.10.0 claims the derived raise to 1200.0; commit 25a65b7 carrying the raise is only on `owa/t12` — main documents a change it does not contain
  - seeds: `c2`
- `s2` — `examples/orchestrator_tools.py:197 + docs/live-test-results/worker-throughput.md`: fan-out deadline 60.0s against measured worker p95 40.78s at width 14 on 1200-token completions; series rungs budget 16000 tokens, so real units will outrun the deadline and land `fanout-unit-absent` degradations
  - seeds: `c3`
- `s3` — `grep REQUEST_TIMEOUT across examples/`: seven independent timeout constants, each chosen per harness: 60s fan-out, 300s worker seam and batch wait, 600s muse-arms and devague-legs, 900s league-commander, 1800s h2h — none derived from a shared rule
  - seeds: `c4`
- `s4` — `docs/live-test-results/corrections.md + CHANGELOG.md 0.10.0 Fixed`: the 300s clock censored the completion-length distribution while recording `truncated: false`; retry arithmetic 4x300 + 3x20 = 1260.0s matched to tenths of a second — the evidence base for error-informed retry escalation
  - seeds: `c5`
- `s5` — `.worktrees.embodiment/t12 (branch owa/t12, ladder-decisions.jsonl, C2-E.jsonl)`: the series is live: C1 verdict CEILING (arm E 6/6, 3544.9s wall), climb to C2, C2-E five records deep and uncommitted; manager/hybrid cells not yet dialled at C2 — any change here is mid-measurement
  - seeds: `c6`
- `s6` — `examples/arch_arms.py (ARMS) + examples/arch_league.py`: arms are data (`top_level_role`, `delegates`, `keeps_work`) and the league lane pins `FINAL_AUTHORITY_TOOLS = ('order',)` on the top-level actor whatever the architecture — the exact seams a fifth hive arm extends
  - seeds: `c7` (rejected)
- `s7` — `examples/orchestrator_tools.py (partition, MIN_UNIT_GRANT, fan-out construction)`: partitioned grant with a refusal floor and finite waits already exists as the dispatch substrate; the fan-out's stated construction — a hung unit cannot park the parent's drive — is the termination discipline the hive inherits
  - seeds: `c8` (rejected), `c11`
- `s8` — `docs/live-test-results/worker-throughput.md`: saturation near width 8: going 8 to 14 buys +5.5% aggregate for +75% concurrent load, and the series' only two truncations happened at width 14; lobes relabelled the 50 tok/s x14 figure a KV-pool ceiling
  - seeds: `c9`
- `s9` — `tests/test_governance.py`: hermetic guards pin the muse modules untouched and the worker role out of reference-rig tables until a supporting verdict exists — both bind a hive arm
  - seeds: `c10`, `c12`
- `s10` — `the user's pasted error text (13 lines)`: never reached the agent — the paste arrived as a collapsed placeholder; the concrete errors behind the timeout ask are unverified, and every timeout claim above rests on repo evidence instead
- `s11` — `issue #42 (opened by the operator, read in full)`: the general timeout rule already argued: budget-derived, never percentile (the timeout censors the tail a percentile is sized from); repo audit shows only the failed harness below bound, `league_commander` 1.21x the narrowest pass; proposes the CI test, the records re-exam, and defers the embodiment/-placement decision as a genuine open question
  - seeds: `c13`, `c14`, `c15`
- `s12` — `issue #44 + operator comment (read in full)`: the Bee-Hive named and argued: five-arm table with B as worker-as-tool, P as programmed policy, the B0/B1/B2 spectrum with scope size as a swept variable, the delegate-to-authored-artifacts structural claim, the width-rung-first order, and the league environment with the optional-field metric exclusion
  - seeds: `c17`, `c18`, `c19`, `c20`, `c21`, `c22`
- `s13` — `issue #45 (read in full)`: the worker-harness caste as an operator-facing skill: three verbs, committed legible artifacts, three named failure modes (staleness, the unscoped call, model-written code execution), break-even economics, and an honest status — the design it implements is unvalidated, so it ships opt-in and off
  - seeds: `c23`, `c24`, `c25`
- `s14` — `issue #42's audit table vs examples/orchestrator_tools.py`: the audit covers six client timeouts and omits the fan-out wait deadline — a 60.0s bound on a whole unit drive; this scope pass's addition to #42, not a repeat of it
  - seeds: `c16`

## Decisions

- B1's ~9-way concurrency is plain concurrent scoped calls from harness code — the `worker_seam` path the throughput probe already measured — not fan-out unit dispatch; the partitioned-grant fan-out remains the M/H seam, because only units that hold turns need turn budgets partitioned
- the timeout bound stays per-harness — each constant cites its inputs where it lives — with one shared CI test (`test_timeout_bounds.py`) enforcing the bound repo-wide; moving transport policy into embodiment/ is parked as a follow-up, respecting #42's own deferral. The operator's felt sense of the cortex clock (300s too small, maybe 600s) is answered by derivation, not suspicion: at 21.5 tok/s a 16000-token budget needs 744s, and a real 14,265-token turn (~663s) would have outrun 600 — the bound is recomputed, never intuited
- drone v1 executes in-process under the host's existing approval policy, with every capability declared in the manifest so review is possible; the workspace jail stays available per-drone — the threat model is stated per C2, never implied by a name
- whether the cortex's reasoning streams in deltas is a deployment question, answered by probe only after the series (never mid-series against the contended cortex); the ask to lobes — advertise streaming and reasoning-delta support in /capabilities so consumers discover it by name rather than probe — is filed via communicate

## Open parks

- [unknown_nonblocking] whether a runtime-adaptive timeout — one that adjusts during a run according to errors — can stay honest under pre-registration: a constant that moves mid-series is an instrument change mid-measurement; the likely resolution is derive-before-freeze-during, but no series has tested it
- [unknown_nonblocking] issue #45's remaining open questions ride with it: repo-scoped vs user-scoped .drones/, whether evoke may escalate to the cortex, whether create should prefer a pure code-drone when no judgement is needed, and skill vs CLI verb — decided at /think or build time, none blocking the frame
- [follow_up] whether the derived-timeout rule should eventually live in embodiment/ as a transport-policy seam all harnesses import — deferred per #42 and the q4 decision
