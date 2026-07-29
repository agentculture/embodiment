# Build Plan — muse cycle: pad, headspace, devague legs

slug: `muse-cycle-pad-headspace-devague-legs` · status: `exported` · from frame: `muse-cycle-pad-headspace-devague-legs`

> The next muse cycle shipped: a pre-registered three-arm pad+headspace validation graded by the verified subset oracle, the devague legs split across both minds behind the human confirm gate, and the muse lane's close-time counsel loss and dead degradation codes resolved by explicit decision

## Tasks

### t1 — Snapshot the baseline numbers before any fix lands

- instruction: Owns docs/live-test-results/ (new baseline doc only). Read delivery-per-kind.md, association-work.md and proof.md; transcribe the three baseline numbers with their n and caveats. No code changes.
- covers: c27, h21
- acceptance:
  - a committed baseline doc records the late-drop rate (one per run, 4 of 4), the confidently-wrong rate (5 of 6) and the dead-code count (2), each citing its measurement doc

### t2 — Wire the two dead degradation codes to real background-compilation paths in `muse_runner`

- instruction: Owns embodiment/`muse_runner.py`, tests/`test_muse_runner.py`, tests/`test_proof_reporting.py`. Decide what compilation-starvation and counsel-displacement actually mean against the `WORK_`\* vocabulary at `muse_runner.py`:227-230; keep `DROPPED_OVERFLOW` distinct. TestUnproducedCodes at `test_proof_reporting.py`:64-100 must be updated, not deleted.
- depends on: t1
- covers: c29
- acceptance:
  - both codes have production emit sites driven through the public seam by tests; the `test_proof_reporting` non-emission pin is updated to assert the emission paths instead

### t3 — Tighten the PROVOKERS contract and rewrite the two vocabulary-only tests

- instruction: Owns tests/`test_ledger.py`, tests/`test_muse_runner.py`. Add the AST provoker check following `test_loop.py`:281-287 as the structural-pin precedent; the two provokers at `test_ledger.py`:556-581 must be rewritten to drive t2 real paths. Demonstrate the check red once, then remove the deliberate violation.
- depends on: t2
- covers: c19, h18
- acceptance:
  - an AST check fails the suite when a provoker reaches a private attribute, demonstrated red once during development; the two TestWorkClassPriority tests drive real paths and assert drop records

### t4 — Terminal-boundary drain-without-consider: the synthesis boundary drains and never starts a muse session

- instruction: Owns embodiment/`presence_engine.py`, tests/`test_presence_engine.py`. The change is in `_muse_turns_for` (`presence_engine.py`:697-723): on the synthesis boundary, drain without calling consider. Do not touch loop.py budgets — no fourth consumer of `turn_budget`/`reading_budget`.
- depends on: t1
- covers: c22, c30, h19, h24
- acceptance:
  - a test asserts no session starts on the synthesis boundary; the termination matrix and hook suites pass unchanged; drained counsel reaches the synthesis turn messages

### t5 — Synthesis-drain observability record

- instruction: Owns embodiment/`muse_runner.py` + embodiment/ledger.py surfaces and their tests. Record the terminal-drain delivery (count + insight ids, zero included) with a code that has an emit site and a real-path provoker from the start.
- depends on: t4, t3
- covers: c38, h32
- acceptance:
  - a ledger record carries the delivered count and insight ids, zero included; a live-shaped test emits it with an accurate count

### t6 — Pre-register the devague-legs experiment under devague 0.22.0 with the lapse protocol wired

- instruction: Owns docs/live-test-results/ (new prereg doc). Follow association-work-preregistration.md verbatim as the template; incorporate t1 baseline numbers; hold thresholds as literal constants and assert them by value in a test.
- depends on: t1
- covers: c8, c11, c13, c41, h10, h12, h14, h35
- acceptance:
  - the prereg commits before the first dial with literal thresholds asserted by value in tests; it names the same-mind control, the gate DVs and the Gemma-proposes-human-confirms pipeline, pins devague 0.22.0, and wires llm-origin lapse filing into the run protocol

### t7 — Record the devague-import decision: the experiment harness shells out to the devague CLI rather than importing it

- instruction: Owns pyproject.toml + CHANGELOG.md (after t8 has landed its pin). Record that devague is approved but deliberately not imported: the harness subprocesses `devague plan converge --json` to keep the grader external and per-run version-pinnable.
- covers: c24, h4
- acceptance:
  - the harness invokes devague plan converge/waves as a subprocess and parses --json; no embodiment or examples module imports devague; the reason (keeping the grader external and version-pinnable per c13) is recorded in pyproject and CHANGELOG beside the unused approval

### t9 — Run the devague-legs experiment across both minds on a pre-seam commit, starting with the /deviate leg

- instruction: Owns examples/ (new devague-legs harness) + docs/live-test-results/. Start with the /deviate leg — cheapest slice, no tools needed. Subprocess the devague CLI; record its version and the base commit hash in every run config. Gemma proposes with --origin llm; only the human confirms.
- depends on: t6, t3, t5
- covers: c12, h13
- acceptance:
  - each run config records the devague version and a base commit hash asserted to predate the tool-seam merge; muse-authored claims are captured --origin llm and the confirm log shows zero confirms not attributable to the human; gate DVs and the same-mind control are reported together

### t10 — The muse tool seam: a MuseCompleteFn successor carrying a tool schema, with structural pins

- instruction: Owns embodiment/muse.py, tests/`test_muse.py`, plus a new AST test file. Design the MuseCompleteFn successor at muse.py:513-516 to carry a tool schema and read tool results. Mirror `test_muse.py`:318-325 for the counter-bounded while proof. Every TestToolsOff pin you revise needs a named replacement in the same diff — deleting one without a replacement is the violation.
- depends on: t9
- covers: c15, h3, c17, h16
- acceptance:
  - the new seam type carries a tool schema and reads tool results; its loop is proved by AST to be one counter-bounded while with exits drawn only from declared constants; import-posture pins hold; every revised TestToolsOff pin has a named replacement pin in the same diff

### t11 — Tools-off byte-identity: the degrade floor and the rollback path for default-on

- instruction: Owns tests/`test_muse_tool_identity.py` ONLY (new file, deliberately not `test_muse.py`, which t10 owns). Assert byte-identical prompts with no tools wired, and `MUSE_AUTHORITY` still first on every path.
- depends on: t10
- covers: c37, h31
- acceptance:
  - with no tools wired, prompts are byte-identical to the pre-seam release, asserted in tests/`test_muse_tool_identity.py` (a new file, keeping it disjoint from `test_muse.py`); `MUSE_AUTHORITY` stays prepended on every path

### t12 — Wire the scratchpad as the muse first tool, top-level only

- instruction: Owns a new pad-tool module + its test file. Reuse scratchpad.py KINDS (line 63) and `SCRATCHPAD_TOOLS` (239-282) unchanged; if the muse pad must diverge, record why in the PR. Top-level muse only — assert subagent-depth muses get no tools. Assert the pad is not in any recall surface.
- depends on: t10
- covers: c3, h6
- acceptance:
  - the muse pad uses scratchpad.py KINDS and schemas unchanged, or records any divergence with a reason in the PR; a test asserts subagent-depth muses receive no tools; a test asserts no recall surface includes pad entries

### t13 — Land headspace-cli as a lazily-imported base dependency AND the workspace tool with its no-reach suite, in one change

- instruction: Owns pyproject.toml, tests/`test_zero_deps.py`, tests/`test_package_surface.py`, CHANGELOG.md, and a new workspace-tool module + its test file. ONE change: the pin and the first import together — a pin without an importer fails `test_runtime_imports_match_the_approved_set` (vanished set non-empty, tests/`test_zero_deps.py`:303) and violates h2. Import headspace lazily (function scope) so bare `import embodiment` stays clean. Read the `_gate_message` banner and follow its numbered remedy. Port the executed probe into the suite: create a workspace, assert policy network=disabled, connect to the docker-bridge gateway from inside (discover it with `ip route`, never hardcode 172.17.0.1), assert unreachable. Missing docker degrades with a CliError-style hint, never raises.
- depends on: t10, t12
- covers: c5, h8, c33, h27, c18, h17, h26, c14, h2, c16, h15, c32
- acceptance:
  - the no-reach suite asserts the recorded network=disabled policy AND a failed docker-bridge connect from inside a live workspace (the executed probe, ported); no repo path and no eidetic store are reachable; a missing docker daemon degrades observably and never raises
  - pyproject, `_APPROVED_DEPENDENCIES`, `_REQUIRED_RUNTIME_IMPORTS` and the CHANGELOG rationale land in the same commit as the first headspace import — never ahead of it; `test_bare_package_import_still_costs_nothing` stays green so docker and requests are absent from a bare import; the new module joins `_HEAVY` in `test_package_surface.py`

### t15 — Workspace lifecycle: bounded teardown and day-one degradation codes

- instruction: Owns the workspace-tool module + runner close path. Bound the teardown like `_bounded_join` in `muse_runner.py`; a live workspace at close records a degradation naming the workspace id. Declare each new code WITH its emitter and a real-path provoker in the same commit.
- depends on: t13
- covers: c35, h29, c36, h30
- acceptance:
  - workspaces are destroyed at runner close under a bounded timeout mirroring the bounded thread join; a drive ending with a live workspace records a degradation naming the workspace id; every new code has a production emit site and a real-path provoker

### t16 — Measure the tool-session latency distribution through the lobes proxy, then fix the c29 targets

- instruction: Owns examples/ (new latency probe). Measure tool-session completion latency through the real lobes proxy against the drive tail; the issue 21 probe saw 5.5s to 30.4s over five turns. Report the distribution, then fix the c29 late-drop target against it.
- depends on: t12
- covers: h23
- acceptance:
  - the committed probe reports tool-session completion latencies against the drive tail; the c29 late-drop target is fixed against that measurement before the series runs; a series under four drives or an arm under n=6 reports INCONCLUSIVE

### t17 — Build the three-arm harness on the verified oracle

- instruction: Owns examples/ (new three-arm harness). Import truth() and grade() from examples/`challenge_subset.py` UNCHANGED — byte-stable for the whole series. One runner, three wired-tool configurations; commit raw transcripts per run, not just verdicts.
- depends on: t12, t13
- covers: c4, h7
- acceptance:
  - truth() and grade() are imported unchanged from examples/`challenge_subset.py` and pinned by asserting 76 and trap-72 rejection; arms A (tools-off), B (+scratchpad) and C (+scratchpad +headspace) share one runner differing only in wired tools; raw transcripts are committed per run

### t18 — Run the three arms and classify every execution

- instruction: Owns docs/live-test-results/ (results + verdict). Classify every arm C execution by the rule already fixed in t6 prereg — never invented after seeing data. Report counsel quality separately from correctness. File a devague lapse mid-flight for any substitution of assumption for check; publish either way, INCONCLUSIVE included.
- depends on: t17, t16
- covers: c7, h9
- acceptance:
  - each arm C execution is classified arithmetic-offloaded vs reasoning-displaced by the rule fixed in the pre-registration; counsel quality is scored independently of answer correctness; the confidently-wrong rate is reported against the 5-of-6 baseline, published either way

### t19 — Re-run the echo probe with a workspace-result arm

- instruction: Owns examples/ (echo-probe extension). Add the arm where a workspace execution result carries a planted wrong number, so it arrives wearing measured-result authority — the shape that beat the cortex 6/6. Assert the hostile result actually surfaced, as memory-echo-chamber.md learned to.
- depends on: t13
- covers: c10, h11, c39, h33
- acceptance:
  - one arm plants a wrong number in a workspace execution result so it arrives wearing measured-result authority; the pad-recall boundary stays asserted until this probe returns clean; the verdict is recorded either way

### t20 — Flip muse tools default-on after the validation tuning pass

- instruction: Owns the default-config surface + CHANGELOG. Flip only after t18 has published. Ship the tuned protocol, not the measured failure mode; keep tools-off documented as the rollback.
- depends on: t18
- acceptance:
  - the default flips only after t18 publishes; the tuned pad protocol is what ships; the tools-off path remains available as the documented rollback

### t21 — File the sibling-repo asks as issues through the communicate skill

- instruction: No repo files. Use the communicate skill (agtag-backed) to post on colleague#358 and any lobes/headspace ask. Sign as embodiment (Claude) per the scripts. Never push to a sibling repo.
- covers: c26, h20
- acceptance:
  - colleague#358 is updated with the widened C1b consequence (embodiment now carries four base dependencies); any lobes or headspace ask is filed on their repos, never assumed here

### t22 — Demonstrate the full flow end to end and publish the delivery summary

- instruction: Owns docs/deliveries/ + the delivery summary. Use the summarize-delivery skill; run devague summary for the skeleton. Report planned vs actual honestly — a lane that did not run is reported absent, never implied. Map every done-condition to an openable artifact path.
- depends on: t18, t9, t20
- covers: c1, h1, c25, h5, c28, h22
- acceptance:
  - one scope-think-challenge-spec-to-plan-fan-out-summary cycle completes across the two minds with the human confirm gate intact; frame, plan and delivery summary are committed; every done-condition maps to an openable artifact path and any lane that did not run is reported absent

### t14 — No-secrets boundary on the workspace tool

- instruction: Owns the workspace-tool test file (after t15 has finished editing the module). Assert the constructed create/run invocations carry no --env or --env-file and that no secrets parameter exists on the seam at all.
- depends on: t13, t15
- covers: c34, h28
- acceptance:
  - the constructed create/run invocations carry no --env or --env-file; a test asserts the seam exposes no secrets parameter, so the leak path cannot be opened by configuration

## Risks

- [unknown_nonblocking] docker workspace contention with local cortex inference on the shared 128 GB box is unprofiled: workspaces default to 0.5 GB and 1 CPU, but N concurrent muse sessions each holding a workspace is an unmeasured load beside a 27B model (task t13)
- [unknown_nonblocking] the storage ceiling is measured, not enforced, under the default local volume driver (probe warning) — a runaway workspace write is reported, not capped (task t15)
- [unknown_nonblocking] the devague grader surface may move mid-cycle: 0.22.0 just landed and further work is in flight, so pooling runs across versions would confound the gate DVs (task t9)
- [unknown_nonblocking] a tool-wielding muse session may never complete within the drive tail, leaving the terminal drain with nothing to deliver and the zero-late-drop target unreachable in practice (task t16)
