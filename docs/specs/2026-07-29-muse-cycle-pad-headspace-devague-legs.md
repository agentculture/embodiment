# muse cycle: pad, headspace, devague legs

> The next muse cycle shipped: a pre-registered three-arm pad+headspace validation graded by the verified subset oracle, the devague legs split across both minds behind the human confirm gate, and the muse lane's close-time counsel loss and dead degradation codes resolved by explicit decision
> instruction: verify against the live-test-results ledger and the delivery summary: each headline number resolves to a raw jsonl or transcript in the repo

## Audience

- the audience is the operator running Gwen on the reference rig and any host embedding the embodiment package; secondarily the sibling repos this cycle touches — colleague as first consumer, headspace-cli and devague as newly-approved dependencies, lobes as the role gateway
  - instruction: file sibling-repo asks through the communicate skill as issues on their repos rather than assuming their changes in this plan

## Before → After

- Before: today the muse is a tools-off counsel lane that goes confidently wrong on arithmetic (five of six failures state a definite wrong answer), loses its last counsel at every drive close (25 percent of counsel, one per run), certifies two dead degradation codes through a guard escape hatch, and the devague method legs run in one mind only
  - instruction: before the fixes land, snapshot the baseline numbers — late-drop rate, confidently-wrong rate, dead-code count — into the pre-registration for comparison
- After: the end state is the method running end to end — scope, think, challenge, then spec-to-plan, fan-out to subagents, and a delivery summary — as the flow this embodiment can carry; achieving that flow is the point of the cycle (operator statement, 2026-07-29)
  - instruction: demonstrate one full scope-think-challenge-spec-to-plan-fan-out-summary cycle across the two minds and commit the artifacts (frame, plan, delivery summary) as the evidence

## Requirements

- arm B reuses the existing actor-side pad protocol in embodiment/scratchpad.py — KINDS intend/observe/conclude/revise (scratchpad.py:63), `SCRATCHPAD_TOOLS` schemas (239-282), disk persistence (104-221) — rather than inventing a second pad; nothing in the muse lane references it today
  - instruction: wire the muse pad against scratchpad.py KINDS and schemas; any protocol divergence lands as a recorded design note in the seam PR
  - honesty: if the muse pad diverges from the actor pad protocol, the divergence is recorded with a reason — a silent second protocol is the failure this claim forbids
- the experiment reuses the verified oracle: truth() enumerates 76 of 144 non-consecutive subsets and grade() flags trap 72 (examples/`challenge_subset.py`:61-85, pinned by tests/`test_challenge_harnesses.py`, cross-validated four ways per designed-problem.md:46-63); `run_once`() is cortex-only — the muse flag only fills the recorded config preamble — so all three arm harnesses are new plumbing
  - instruction: import truth() and grade() from examples/`challenge_subset.py` into the arm harness unchanged; pin their behaviour in the pre-registration by asserting 76 and trap-72 rejection
  - honesty: the oracle is reused unmodified — truth() and grade() stay byte-stable during the experiment; any change to either invalidates prior arm results and restarts the series
- arm C sandbox boundary is the load-bearing control (operator comment on issue 21): no repo, no eidetic store, no network — asserted by test, not intent; headspace-cli 0.10.0 verified installed 2026-07-29 with run env forwarding by name never argv, input/put under a disabled network, digest-verified export, and destroy that refuses on unexported artifacts (CHANGELOG 0.10.0 closes headspace-cli 13 and 14)
  - instruction: write the sandbox boundary test first: create a workspace, attempt repo, eidetic and network reach from inside, assert refusal; make it part of the arm C harness gate
  - honesty: the sandbox boundary test fails the suite if the workspace can reach the repo, the eidetic store or the network — asserted before the first measured arm C run, not after
- arm C measures what the workspace was used for, not only answer correctness: classify each execution as arithmetic-offloaded vs reasoning-displaced, and score counsel quality independently of correctness (operator reframing on issue 21: headspace lets the muse stop pretending to be a calculator; the counter-prediction is execution substituting for reasoning)
  - instruction: define the execution classifier (arithmetic-offloaded vs reasoning-displaced) and the counsel-quality rubric in the pre-registration doc; classify every arm C execution in the committed transcripts
  - honesty: every arm C execution is classified before verdicts are drawn, and the classification rule is fixed in the pre-registration — not invented after seeing the data
- pre-registration follows the practiced discipline, not a template: committed before the first dial, thresholds as literal constants asserted by value in tests, rules fixed in advance so INCONCLUSIVE is reportable (association-work-preregistration.md, arena-series-preregistration.md; no standalone standing-discipline doc exists — grep confirms)
  - instruction: commit the pre-registration doc with literal thresholds and a test asserting them by value before the first dial; cite association-work-preregistration.md as the template
  - honesty: the pre-registration commit predates the first measured run in git history; thresholds changed after data invalidate the run
- the issue 20 pipeline is Gemma proposes, human confirms, Qwen plans — never Gemma confirms; the existing origin-llm lands-proposed gate is the mitigation the echo-chamber result demands (next-cycle-candidates.md:192-214)
  - instruction: run the legs so muse-authored claims are captured --origin llm; the harness asserts zero confirms not attributable to the human; include the confirm log in the run artifacts
  - honesty: no frame claim authored by the muse reaches confirmed without a recorded human confirm; the experiment logs prove the gate was exercised
- the issue 20 grader is version-pinned at run time: devague 0.21.0 installed; plan converge and waves verified present and deterministic (json output, metadata only); an assign-to-workforce fan-out on devague issue 13 is in flight across four agent worktrees, so the pre-registration records the devague version rather than assuming a stable gate
  - instruction: record the devague version in each run config; the analysis refuses to pool configs with differing versions
  - honesty: each issue 20 run records the devague version in its committed config; results across differing versions are not pooled
- headspace-cli becomes a base dependency of embodiment — the operator decision of 2026-07-29 exercising the d2 human gate; measured transitive cost: docker>=7.1 alone, which pulls requests, urllib3, certifi, charset-normalizer and idna; the installed package is importable (`headspace` module with core/ and cli/ layers), so the d2 precedent of module-scope import over a subprocess seam is available
  - instruction: add headspace-cli>=0.10 to `_APPROVED_DEPENDENCIES` and pyproject with the docker>=7.1 cost note in the same PR that first imports `headspace`; run the zero-deps suite and watch the gate pass deliberately
  - honesty: the gate is exercised for real: the tests/`test_zero_deps.py` pinned sets update in the same change that adds the import, transitive cost recorded; the dependency is never added speculatively ahead of the code that needs it
- the muse gains tool use: a tool-wiring seam on the muse lane so hosts wire tools to it — the scratchpad and a headspace workspace first — superseding the structurally tools-off seam as design intent (operator decision, 2026-07-29); issue 21 measured Gemma emitting valid tool calls over five fed-back turns, so the model side is established
  - instruction: design the MuseCompleteFn successor that carries a tool schema; revise the TestToolsOff pins deliberately in the same change; add counter-bounded-while and exit-constant AST tests mirroring `test_muse.py`:318-325, plus a sandbox no-reach test for the headspace tool
  - honesty: the seam change is proved structurally, not asserted: the muse tool loop carries its own AST termination pins and a no-reach authority test before any live run claims safety
- the dependency enters through the gate, not around it: update `_APPROVED_DEPENDENCIES` (tests/`test_zero_deps.py`:68-79) and `_REQUIRED_RUNTIME_IMPORTS` (90-99) deliberately with the recorded transitive cost, plus pyproject and CHANGELOG rationale per the gate banner — updating the pinned set IS the approval; the C1b consequence (colleague cannot import embodiment) was already accepted under d2 and widens by one more dependency
  - instruction: one PR: pyproject, `_APPROVED_DEPENDENCIES`, `_REQUIRED_RUNTIME_IMPORTS`, CHANGELOG rationale and the first import together; the zero-deps suite is green in that PR and red if any piece is dropped
  - honesty: the pinned-set update, pyproject change and CHANGELOG rationale land in the same commit as the first import — the gate is never satisfied retroactively
- the muse tool loop carries the same structural guarantee shape its siblings prove: import-posture pins, an exit-constant AST walk, a counter-bounded while (`test_muse.py`:318-325 is the precedent), and a no-reach test for the authority boundary; the TestToolsOff pins (`test_muse.py`:349-409) are revised deliberately as part of the seam redesign, never silently deleted; new public modules join the package-surface lazy map and are auto-measured by `test_zero_deps`
  - instruction: author the muse tool loop with its AST test file in the same change: import-posture, exit-constant walk, counter-bounded while, no-reach test; revise the TestToolsOff pins with replacement pins in the same diff
  - honesty: the muse tool loop AST tests exist and fail on an unbounded loop before any live run; deleting a TestToolsOff pin without a replacement pin is a violation, not a cleanup
- the issue 18 fix tightens the guard either way: an AST check banning provokers from reaching private attributes — only `test_ledger.py`:556-581 call `runner._record` directly, with nothing but prose marking them — following the structural-pin precedents (`test_loop.py`:281-287, `test_muse.py`:311-316); and the two vocabulary-only tests at `test_muse_runner.py`:1148-1164 are rewritten to assert behaviour or renamed to claim less
  - instruction: add the AST provoker check to `test_ledger.py` banning private-attribute access in provoker bodies; rewrite the two vocabulary-only tests to drive the new producing paths and assert the drop records
  - honesty: after the change, a provoker reaching a private attribute fails the suite — demonstrated by a deliberate red test during development, then removed
- any issue 17 change preserves the structural pins: `_work_loop` returns only the three EXIT constants with no new raise (`test_loop.py`:480-522), hooks add no exit path or budget (`test_loop.py`:565-617), and no fourth consumer of the turn or reading budgets appears; delivery-to-memory stays C3-observable — a new LifecycleEvent kind beside `CHECKPOINT_REMEMBERED` (lifecycle.py:808-818) needs no events.py change, while LoopEvent.kind is a closed vocabulary (loop.py:525-526)
  - instruction: run the termination matrix and hook suites unchanged against the drain change; add the delivery-to-memory observability record and a test that a live run emits it
  - honesty: the termination matrix and hook tests pass unchanged after the drain change; delivery-to-memory emits an observable record in the same run that exercises it
- devague is approved as a base dependency if implementation needs it (operator decision, 2026-07-29): measured transitive cost is zero — devague 0.21.0 declares no dependencies and its installed venv holds only the `devague` package itself; the import is optional per need — the goal is the methodology (scope, think, challenge, spec-to-plan, fan-out, summary), not the package
  - instruction: decide at spec-to-plan time whether any task imports devague; if one does, record the reason in the dependency gate alongside the pin
  - honesty: if the methodology is achievable without the import, the dependency stays out; an import that lands must name the code that needs it
- option 1 must pair with the skip-final-consider variant: the synthesis boundary drains WITHOUT starting a new muse session — otherwise the consider() fired on that same boundary (`presence_engine.py`:709-710) starts one more session that structurally strands, and the c29 zero-late-drop target stays unreachable by design
  - instruction: implement the terminal-boundary drain as drain-without-consider in `presence_engine`; assert by test that no session starts on the synthesis boundary
  - honesty: a live series after the change shows zero muse sessions started on the terminal boundary and zero undrained-at-close drops attributable to the synthesis-boundary session
- the headspace import stays lazy: bare `import embodiment` pulls no docker or requests — the continuity precedent, pinned by `test_bare_package_import_still_costs_nothing` and `test_continuity_is_what_costs` in tests/`test_zero_deps.py` (337-363); the muse tool module joins `_HEAVY` in `test_package_surface.py`
  - instruction: import headspace inside the muse tool module lazily (function scope or lazy submodule); extend `_HEAVY` in `test_package_surface.py`
  - honesty: `test_bare_package_import_still_costs_nothing` stays green after the dependency lands; docker and requests appear in no bare-import measurement
- the no-reach test asserts the recorded network=disabled policy AND a failed bridge connect from inside the workspace: measured today (workspace hs-6fd6fa930508) — interfaces are loopback only and connects to the docker bridge gateway fail Errno 101 on 8001 (lobes), 7687 (neo4j) and 27017 (mongo); the bridge IP is the documented host-escape path when network is enabled, so the test must pin the disabled posture
  - instruction: port the executed probe into the suite: create a workspace, assert policy shows network=disabled, attempt the bridge connect from inside, assert unreachable
  - honesty: the no-reach test asserts both the recorded network=disabled policy and a failed bridge connect from inside the workspace; a green test with network enabled is impossible by construction
- workspace lifecycle is owned and bounded: workspaces are destroyed at runner close with a bounded teardown mirroring the bounded thread join; a drive ending with a live workspace records a degradation naming the workspace id; the destroy --force policy is explicit; resource budgets are advisory on this host — the storage cap is measured, not enforced (probe warning, default local volume driver)
  - instruction: destroy workspaces in runner close under a bounded timeout; on failure record the degradation with the workspace id; document the --force policy
  - honesty: a drive that ends with a live workspace records a degradation naming the workspace id; teardown joins are bounded like the runner thread join
- the muse tool lane ships its degradation codes with emitters and real-path provokers from day one — the issue 18 lesson applied forward; once declared, the exhaustive-by-construction PROVOKERS table forces coverage, and the tightened contract (no private-attribute provokers) makes vacuous coverage impossible
  - instruction: declare each new code with its emitter in the same commit; add real-path provokers; the exhaustiveness guard enforces the rest
  - honesty: each new code has an emit site in production logic and a provoker that drives it through the public seam before the PR merges — verified by the tightened PROVOKERS contract
- the tools-off muse path remains available and byte-identical when no tools are wired — the degrade floor and the rollback path for default-on; it also keeps the issue 20 todays-seams design reproducible after the seam lands (the colleague 352 acceptance criterion — absent config means byte-identical prompts — applied to tools)
  - instruction: keep the tools-off constructor path; add a byte-identity test comparing prompts with and without tools wired
  - honesty: with no tools wired, prompts and behaviour are byte-identical to the pre-seam release
- the terminal drain records what it delivered to the synthesis turn — count and insight ids, zero included — as an observable ledger record; a silent delivery path would violate C3 exactly where the cycle claims to fix delivery
  - instruction: add the synthesis-drain ledger record and a test that a live-shaped run emits it with an accurate count
  - honesty: a live run ledger shows the synthesis-drain record with its count; zero-delivered runs still show the record
- the echo-probe re-run covers workspace results as well as pad entries: an execution result returns wearing measured-result authority — structurally the record shape that beat the cortex 6/6 — so the probe needs an arm where a workspace result carries a planted wrong number
  - instruction: extend the echo-probe harness with the workspace-result arm before enabling pad recall or claiming the lane safe
  - honesty: the re-run echo probe includes an arm where a workspace result carries a planted wrong number, and the verdict is recorded either way
- the cycle targets devague 0.22.0 (PR 101, reasoning-degradation ledger, merging imminently): experiments file `devague lapse` records mid-flight instead of reconstructing corrections from memory at the end — the six starting codes name exactly the failure classes this repos last cycle hit (grader-unverified, control-absent, n-below-claim, instrument-changed-mid-series) — and the `SCHEMA_VERSION` 4-to-5 bump is hard, so the operator upgrades once before plan work begins and never mixes binaries on one frame mid-cycle
  - instruction: upgrade the installed devague once PR 101 merges, before plan work begins; wire lapse filing into the experiment run protocol with llm-origin lapses adjudicated by the human
  - honesty: the pre-registration records devague 0.22.0 or later with the lapse ledger available; an instrument change mid-series is itself filed as a lapse, not silently absorbed

## Honesty conditions

- every measured claim in the shipped cycle cites a committed raw artifact; a lane that did not run is reported absent, never implied
- no recall path surfaces muse pad entries until an echo probe re-run on the pad lane is committed with a clean result
- the issue 20 runs execute against a commit predating the muse tool seam, and the run config records that commit hash
- the no-reach test is part of the suite, and every trace shows the actual role and model — a run where the muse acted on the repo is a contract breach to report, never to smooth over
- the flow counts as achieved only when run end to end with the human confirm gate intact — a partial demo of individual legs does not satisfy it
- sibling-repo impact is proposed to those repos through their own issues, never assumed on their behalf
- every number in the before-state cites its committed measurement doc; if a re-measurement moves a number, the claim is amended, not defended
- each done-condition maps to a committed artifact — a test, transcript or summary a reviewer can open; a condition without an artifact is not done
- the targets are compared against runs recorded with the same config discipline as the baselines; a series shorter than four drives or an arm under n=6 reports INCONCLUSIVE, never a pass
- the zero-late-drop target is evaluated against runs using the shipped muse configuration — tools on if default-on ships — never against the faster tools-off latency profile
- no muse workspace invocation passes --env or --env-file; the seam exposes no secrets parameter at all, so the leak path cannot be opened by configuration
- the analysis reports the same-mind control alongside every cross-mind number; a cross-mind result without its control is not reported

## Success signals

- the cycle is done when: both dead codes have real producing paths and the tightened provoker contract rejects direct-record provokers; a live run shows formerly-stranded counsel reaching the synthesis turn; the three-arm validation publishes a pre-registered result either way with raw transcripts committed; and one full scope-think-challenge-spec-to-plan-fan-out-summary flow completes across the two minds with the human confirm gate intact
  - instruction: map each done-condition to its artifact path in the delivery summary; a condition with no artifact link is reported not-done
- measurable targets: the close-time late drop goes from exactly one per run (4 of 4 runs, 25 percent of counsel) to zero per run across a live series of at least four drives; both dead codes reach 2-of-2 emitter coverage with real-path provokers; the three-arm validation reports the confidently-wrong rate against the 5-of-6 baseline with n at least 6 per arm — published either way
  - instruction: evaluate every target against committed raw series artifacts; fix the late-drop target only after the park v3 latency probe measures tool-session completion odds, per c31

## Scope / boundaries

- the muse pad stays non-recallable until the echo probe re-runs clean: a hostile record surfaced via recall beat the cortex DEFERRED 6/6 vs control RESISTED 6/6 with labelling working correctly (memory-echo-chamber.md, n=6 per arm)
  - instruction: assert no recall surface includes pad entries (a test on the recall bundle); schedule the echo probe re-run as its own task before any pad-recall feature
- the devague-legs experiment runs on today's seams — no muse tool surface inside it (issue 20's own not-in-scope: it would collapse the distinction the experiment measures); the pad question is issue 21's separate lane
  - instruction: record the base commit hash in each issue 20 run config and assert it predates the tool-seam merge commit
- the authority contract survives the seam change: muse tools are thinking tools — a pad and a bounded workspace — never acting tools; the workspace reaches no repo, no eidetic store, no network, and that reach is the load-bearing control (issue 21 operator comment); `MUSE_AUTHORITY` stays prepended on every path and traces keep exposing the actual role, model and machine (colleague 352)
  - instruction: add the no-reach suite for the muse toolset — no repo write, no eidetic access, no network from the workspace tool; keep the `MUSE_AUTHORITY` prepend assertions green; assert trace role and model fields in the runner tests
- muse workspaces receive no secrets: the env-forwarding channel (run --env / --env-file) is operator tooling and the muse tool seam exposes no secrets parameter at all — a job that prints its env leaks it into captured output (headspace documented edge), and captured output flows into counsel text, which can reach the committed public eidetic store
  - instruction: construct create/run calls with no env parameters; add a test asserting the constructed invocation carries none

## Assumptions

- the headroom premise rests on association-work.md:112-144 — five of the muse six failures state a definite wrong final answer (n=9 per cell) — and that finding is itself a correction of a defective classifier whose zero-reasoning-failures was a property of the rule, so the baseline arm re-measures it
- decision context for issue 18: the two dead codes belong to a never-built background-compilation feature — the `WORK_BOUNDARY`/`WORK_COMPILATION`/`WORK_CLASSES` vocabulary (`muse_runner.py`:227-230) is likewise declared and never branched on; `DROPPED_COUNSEL_DISPLACED` overlaps the live `DROPPED_OVERFLOW` (`muse_runner.py`:711-713); tests/`test_proof_reporting.py`:64-100 pins non-emission by AST and breaks the moment a producer lands
- issue 17 refined by exploration: the synthesis-phase boundary already fires a consider-plus-drain pair (loop.py:825 and 1789 via `presence_engine.py`:709-710) — option 1 is partially in place, and that final consider starts the session which strands; skipping consider on the terminal boundary while keeping drain is a cheap option 2 variant; option 4 has a precedent seam — `_gather_compiled_from` (lifecycle.py:874-898) duck-types the runner at the before-memory boundary (loop.py:2109), which fires before the host-side close (`league_seat.py`:1239-1256, greenhouse.py:739-757) — but needs a new accessor, since reusing drain would double-consume against presence
- the deviation ledger constrains the cycle: d1 — the muse on its own clock — is the structural cause of the close-time race and a standing confound for every muse experiment (proof.md:42-49 measured the muse about 3.5x faster than the staleness design assumed); d2 is the human-gate precedent the headspace decision exercises; d3 the ObserverFn instrumentation precedent; d4 the live bar — results validate on the rig, not only in CI
- the issue 17 fix and the tool-use decision interact: pad-wielding muse sessions grew 5.5s to 30.4s across five turns in the issue 21 probe (about 6x tools-off), so with tools on, the final in-flight session rarely completes before synthesis and the drain recovers nothing — the zero-late-drop target must be evaluated under the shipped tool-use latency profile, not the tools-off one
- the devague gate measures structural convergence, not plan quality: a vacuous plan can converge, so the gate DVs stay meaningful only next to the same-mind control arm the issue 20 acceptance already requires — the control is where the result lives, as t7 and t19 both taught

## Scope exploration

- `s1` — `embodiment/muse.py`: the entire muse seam is one tools-off completion — `MuseCompleteFn` at muse.py:513-516 with no executor or tool-schema parameter anywhere in MuseLoop or the runner; `MUSE_AUTHORITY` is unconditionally prepended on every deepthink path (muse.py:829-834, framing.py:419-423); the absence of an acting seam is test-pinned (`test_muse.py`:352-397), so tool use means a new seam signature, not wiring
  - seeds: `c2` (rejected), `c15`
- `s2` — `embodiment/scratchpad.py`: a full intend/observe/conclude/revise pad already exists for the actor loop — tool schemas at scratchpad.py:239-282, an execute dispatcher returning ToolOutcome (166-213), disk persistence (104-221); nothing in muse.py or `muse_runner.py` references it, so wiring it to the muse is new seam work, not reuse of existing wiring
  - seeds: `c3`, `c15`
- `s3` — `embodiment/muse_runner.py`: seven of the nine `RUNNER_CODES` have emit sites; `DROPPED_COMPILATION_STARVED` and `DROPPED_COUNSEL_DISPLACED` have none — definitions at `muse_runner.py`:186 and 188, with only `__all__` and `RUNNER_CODES` referencing them; the reserved `WORK_*` vocabulary (227-230) is never branched on; the stranded-insight drop lives in close() at 562-576
  - seeds: `c20`
- `s4` — `tests/test_ledger.py`: the PROVOKERS exhaustiveness set is derived from source via ledger.`known_codes`() scanning module `__all__` prefixes, so coverage is by construction — but exactly two provokers (`test_ledger.py`:556-581) satisfy it by calling `runner._record` under the private lock, and nothing machine-readable distinguishes them from real-path provokers
  - seeds: `c19`
- `s5` — `tests/test_muse_runner.py:1105-1164`: the two TestWorkClassPriority docstrings claim conditions are recorded with the dead codes while the assertions check only vocabulary membership and the muse- prefix (1148-1164); the behavioural sibling at 1128-1146 stages a real displacement race but never asserts a drop record
  - seeds: `c19`
- `s6` — `tests/test_zero_deps.py`: the pinned set is exactly eidetic-cli, coherence-cli and events-cli (68-79), failing on any delta in either direction with a human-approval banner that says updating the pinned set IS the approval; headspace appears nowhere in package code or pinned sets today — docs-only, per repo-wide grep
  - seeds: `c6` (rejected), `c16`
- `s7` — `embodiment/loop.py + embodiment/presence_engine.py (drain path)`: drain has exactly one call site — `presence_engine.py`:710, always paired with consider at 709 — reached per tool step (loop.py:1396, 1459) and per phase announcement including forced synthesis (loop.py:825, 1789); close is never called inside the package — it is host-side, after run returns
  - seeds: `c21`
- `s8` — `embodiment/lifecycle.py + embodiment/continuity.py`: the before-memory boundary (loop.py:2109) is the last act of run and fires before any host close (`league_seat.py`:1239-1256, greenhouse.py:739-757); `_gather_compiled_from` (lifecycle.py:874-898) already duck-types the runner there, bypassing drain; continuity.remember accepts free-form mappings, so a counsel field costs nothing on the eidetic side
  - seeds: `c21`
- `s9` — `embodiment/events.py + structural termination tests`: LoopEvent.kind is a closed seven-kind vocabulary with no muse or counsel kind (loop.py:525-526); muse degradations reach hosts only through an explicit ledger fold; the termination matrix (`test_loop.py`:417-617) and the muse-loop AST pins (`test_muse.py`:201-345) are the invariants any drain-timing or seam change must keep
  - seeds: `c22`
- `s10` — `examples/challenge_subset.py + tests/test_challenge_harnesses.py`: truth() computes 76 by brute force and grade() flags trap 72, both test-pinned and four-way cross-validated (designed-problem.md:46-63); `run_once`() drives a cortex-only loop — the muse flag only fills the recorded config preamble — and no pad or headspace wiring exists in the file
  - seeds: `c4`
- `s11` — `docs/live-test-results/association-work.md`: five of the muse six failures state a definite wrong final answer (112-144), a correction that falsified the classifier which had reported zero reasoning failures; n=9 per cell — the headroom premise for the three-arm design
  - seeds: `c9`
- `s12` — `docs/live-test-results/memory-echo-chamber.md`: hostile record surfaced via recall: DEFERRED 6/6; withheld control: RESISTED 6/6; labelling and provenance framing were working correctly when the record won (n=6 per arm) — the result behind the pad-recallability boundary
  - seeds: `c10`
- `s13` — `docs/live-test-results pre-registration docs`: the standing discipline is practice, not a template: both precedents commit before the first dial, hold thresholds as literal constants asserted by value in tests, and fix rules in advance so INCONCLUSIVE is reportable; no standalone discipline doc exists
  - seeds: `c8`
- `s14` — `docs/plans/next-cycle-candidates.md`: Tier 2b carries the full devague-legs argument including the four-failed-graders record (79-91) and the echo-chamber objection with its Gemma-proposes-human-confirms mitigation (192-214); Tier 2d frames headspace as an operator-run workspace with the sandbox boundary asserted by test (319-324) — the operator decision now moves headspace beyond that framing
  - seeds: `c11`, `c12`
- `s15` — `headspace-cli 0.10.0 (installed CLI, venv metadata, CHANGELOG)`: verified on PATH 2026-07-29: env forwarding by name never argv, input/put under a disabled network, digest-verified export, destroy refusing on unexported artifacts (CHANGELOG closes headspace-cli 13 and 14); the venv shows the package is importable — `headspace` with core/ and cli/ — and its sole declared dependency is docker>=7.1, pulling requests, urllib3, certifi, charset-normalizer, idna
  - seeds: `c5`, `c14`
- `s16` — `devague 0.21.0 + /home/spark/git/devague`: devague 0.21.0 on PATH declares zero dependencies; plan converge and waves are thin deterministic json moves; main is clean while four agent worktrees carry one task-commit each of an assign-to-workforce fan-out on devague issue 13 — the running improvement — so the grader surface may move mid-cycle
  - seeds: `c13`, `c24`
- `s17` — `.devague/deliveries deviation ledger`: d1 made the muse a second thread on its own clock — the structural cause of the close race, measured about 3.5x faster than the staleness design assumed (proof.md:42-49); d2 is the human-gated dependency precedent; d3 the ObserverFn instrumentation precedent; d4 the live acceptance bar; a further d5 covers live self-testing
  - seeds: `c23`
- `s18` — `package-surface + import-graph tests`: `test_contract.py`:62-64 sweeps every package module for colleague imports automatically; `test_package_surface.py` requires lazy-map and `TYPE_CHECKING` entries for any new public module; `test_zero_deps` auto-discovers new modules into the runtime-import measurement — obligations a muse tool module inherits on arrival
  - seeds: `c17`
- `s19` — `challenge pass / adjacent-systems lens: presence_engine drain path, rig topology, docker daemon`: the issue 17 fix and tool-use interact through session latency (probe: 5.5s to 30.4s over five turns); the docker daemon is a new runtime adjacency — absent docker must degrade observably, never raise; only the cortex is local on the rig, so workspace execution is local while the muse model is proxied
  - seeds: `c30`, `c31`
- `s20` — `challenge pass / assumptions-and-counter-evidence lens: c15 vs the measured pad probe, zero-deps laziness precedent, devague gate depth`: c15 establishes tool-call mechanics only — the same probe is counter-evidence on protocol adherence (five intents, zero observations), which fed q6 on release ordering; the lazy-import discipline is the pinned precedent a heavy dependency must follow; the devague gate proves structure, not quality — the same-mind control carries the meaning
  - seeds: `c32`, `c40`
- `s21` — `challenge pass / overlooked-actors-and-lifecycle lens: subagent depth, workspace lifecycle, secrets flow`: subagent.py wires muse and scratchpad at any depth — tools at depth is q7, a decision not a default; workspace teardown ownership was undesigned — now c35; the env-forwarding channel plus captured-output leak plus committed public eidetic store compose into a secrets exfiltration path — now the c34 boundary
  - seeds: `c34`, `c35`
- `s22` — `challenge pass / security-concurrency-reversibility lens: bridge escape, terminal boundary, rollback floor`: measured in workspace hs-6fd6fa930508: network=disabled leaves loopback only, bridge connects fail Errno 101 on 8001/7687/27017 — the no-reach premise holds by construction and the test must pin the posture; the terminal-boundary session is the concurrency finding behind c30; byte-identical tools-off is the rollback path for a default-on, PyPI-published seam change
  - seeds: `c33`, `c37`
- `s23` — `challenge pass / observability-containment lens: synthesis-drain record, day-one codes, echo extension`: the new delivery path needs its own observable record (c38); the muse tool lane must not repeat issue 18 — codes ship with emitters (c36); workspace results wear measured-result authority, the 6/6 echo shape, so the probe re-run gains a workspace-result arm (c39)
  - seeds: `c36`, `c38`, `c39`
- `s24` — `challenge pass / cheap-probes lens: executed bridge probe + deferred latency probe`: one probe executed (workspace hs-6fd6fa930508, created network-disabled, probed from inside, destroyed clean; storage cap reported measured-not-enforced); the latency-distribution probe is deliberately deferred into the pre-registration (park v3) so the c29 target is fixed against measured tool-session latency
  - seeds: `c33`, `c31`
- `s25` — `challenge pass / adjacent-systems lens: ../devague reasoning-degradation-ledger branch (PR 101, 0.22.0)`: read the 0.22.0 changelog in the checkout: devague lapse is an append-only reasoning-degradation ledger with six codes naming this repos own failure classes, origin-driven status, never gating, absent from spec exports; `SCHEMA_VERSION` 4-to-5 is a hard bump — mixing binaries on one frame mid-cycle loses data
  - seeds: `c41`

## Open parks

- [unknown_nonblocking] whether muse pad writes survive the drive close that strands the last counsel (issue 21 risk 4 as a candidate issue 17 mitigation) — the actor pad is disk-persisted (scratchpad.py:104-221) but the muse pad wiring does not exist yet to assert it on
- [unknown_nonblocking] docker workspace contention with local cortex inference on the shared 128 GB box is unmeasured — workspaces default to 0.5 GB memory and 1 CPU, but N concurrent muse sessions each holding a workspace is an unprofiled load beside a 27B model
- [unknown_nonblocking] whether a tool-wielding muse session ever completes within a drives tail such that the terminal drain has counsel ready — probe candidate for the pre-registration: measure the tool-session latency distribution through the actual lobes proxy before fixing the c29 target
