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

- instruction: Owns pyproject.toml, tests/`test_zero_deps.py`, tests/`test_package_surface.py`, CHANGELOG.md, and a new workspace-tool module + its test file. ONE change: the pin and the first import together — a pin without an importer fails `test_runtime_imports_match_the_approved_set` (vanished set non-empty, tests/`test_zero_deps.py`:303) and violates h2. RESOLVED 2026-07-30 (deviation d2, headspace-cli#18 answered by 0.11.0): import `headspace.api` ONLY — it declares exactly create/run/put/export/destroy in `__all__` under semver; `headspace.core` is private and must never be imported. Pin headspace-cli>=0.11. MEASURED: `import headspace.api` adds only stdlib — docker and requests are NOT imported — so a module-scope import adds exactly one third-party top-level module (`headspace`) to `_REQUIRED_RUNTIME_IMPORTS`, and docker stays install-only like neo4j/pymongo; consider extending `test_install_footprint_is_wider_than_import_footprint` to name docker. Use provider="fake" (in-memory, no Docker daemon) for the CI-safe tests, and gate the real-docker no-reach probe behind an env flag like the other live tests. Port the executed probe: create a workspace, assert policy network=disabled, connect to the docker-bridge gateway from inside (discover it with `ip route`, never hardcode 172.17.0.1), assert unreachable. Missing docker degrades with a CliError-style hint, never raises.
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

### t21 — File the sibling-repo asks as issues through the communicate skill

- instruction: No repo files. Use the communicate skill (agtag-backed) to post on colleague#358 and any lobes/headspace ask. Sign as embodiment (Claude) per the scripts. Never push to a sibling repo.
- covers: c26, h20
- acceptance:
  - colleague#358 is updated with the widened C1b consequence (embodiment now carries four base dependencies); any lobes or headspace ask is filed on their repos, never assumed here

### t25 — Widen the terminal drain to fire at drive end on every exit reason, and wire `append_guidance` in an in-repo host

- instruction: Owns embodiment/loop.py, embodiment/`presence_engine.py`, tests/`test_presence_engine.py`, and one host in examples/. The gap t4 measured: `_maybe_force_synthesis` returns early on a non-empty summary, so clean-finish drives fire no terminal boundary at all. Fire the terminal beat at drive end for EVERY exit reason — the natural point is after `_work_loop` returns and before/around `_boundary`(ctx, `BOUNDARY_COMPLETION`) at loop.py:2103, which is on all exit paths. Keep c22 intact: no new exit path, no new consumer of `turn_budget`/`reading_budget`, tests/`test_loop.py` untouched. Keep the duck-typed getattr probe so a three-member sink stays byte-compatible. Then wire `append_guidance` in examples/proof.py (the baseline harness) so the live re-run can actually show counsel reaching the cortex.
- depends on: t4
- acceptance:
  - a drive that exits via finish, stopped or budget all fire exactly one terminal drain; a test proves the clean-finish path (which produced zero terminal boundaries before) now delivers; at least one checked-in host wires `append_guidance` so delivered counsel reaches the cortex messages; the termination matrix and hook suites still pass unchanged and no new exit path or budget consumer appears

### t26 — Wire a tool bench through ThreadedMuseRunner so tools are reachable inside a live drive

- instruction: Owns embodiment/`muse_runner.py` and tests/`test_muse_runner.py`. `muse_runner.py`:596-602 builds MuseLoop(complete, controls=, system=, sink=, clock=) with no tools= argument — that omission is the whole gap. Thread a bench through the constructor to that call. Keep muse.`_bench_for` as the single depth gate; do not add a second one. Preserve the single-daemon-thread discipline, the bounded join, deviation d1 (the actor never waits), and the stdlib allow-list. Tools-off must stay byte-identical — t11 pins that at MuseLoop level and it must keep passing.
- depends on: t13
- acceptance:
  - ThreadedMuseRunner accepts a tools bench and passes it to its MuseLoop; depth gating still withholds below top level and records `DEGRADED_TOOLS_WITHHELD`; with no bench wired the constructed loop and its prompts stay byte-identical to today; a test drives a live-shaped run with a bench and observes the tool round

### t14 — No-secrets boundary on the workspace tool

- instruction: Owns the workspace-tool test file (after t15 has finished editing the module). Assert the constructed create/run invocations carry no --env or --env-file and that no secrets parameter exists on the seam at all.
- depends on: t13, t15
- covers: c34, h28
- acceptance:
  - the constructed create/run invocations carry no --env or --env-file; a test asserts the seam exposes no secrets parameter, so the leak path cannot be opened by configuration

### t20 — Flip muse tools default-on after the validation tuning pass

- instruction: RESCOPED by deviation d8 (operator-confirmed 2026-07-30): muse tools do NOT flip default-on. t18 returned INCONCLUSIVE at an arm-A ceiling and measured harm — tools-off 8/8 correct with 0 `NO_ANSWER` vs 9 `NO_ANSWER` across 16 tool-arm runs, 74% workspace-call refusal, 17 open intents in arm C. This task is now the RECORD of that decision, not a flip. Owns README.md, CLAUDE.md and CHANGELOG. State plainly that tools are opt-in, cite the series, name issues #32 and #33 as what would have to change to revisit, and keep the tools-off path documented as the default rather than as a rollback. README.md:235 still calls the muse "a tools-off mind" — correct it to say tools exist and are opt-in, since that sentence is now wrong in a different way.
- depends on: t18, t26
- acceptance:
  - the default flips only after t18 publishes; the tuned pad protocol is what ships; the tools-off path remains available as the documented rollback

### t23 — Run the live-rig test suite and the assigned challenge harnesses, recording results

- instruction: Owns docs/live-test-results/ (new live-evidence records). Run with `EMBODIMENT_LIVE_RIG`=1 for the rig-gated tests (`test_demo_greenhouse`, `test_echo_probe`) and drive examples/`challenge_subset.py` against the real cortex. Only the cortex is local — muse and senses are proxied, so budget wall-clock accordingly. Record degraded runs as data; never re-run for a better number.
- depends on: t20, t25
- acceptance:
  - the `EMBODIMENT_LIVE_RIG`-gated tests run against the real rig and their results are committed; the challenge harnesses (`challenge_subset` and any sibling assigned challenge) run live with raw transcripts committed; a lane that could not run is reported absent, never implied

### t24 — Run the league/arena benchmark and record the series

- instruction: Owns docs/live-test-results/ (new arena series record). Drive examples/`league_seat.py` live; the arena-series-preregistration.md discipline applies — rules fixed in advance, a degraded match is data, nothing re-run for a better number. Fold muse-runner degradations into the match report via ledger.read(..., `muse_runner`=runner).
- depends on: t23
- acceptance:
  - examples/`league_seat.py` runs with `EMBODIMENT_LIVE_ARENA`=1 against the real arena; the series is recorded in docs/live-test-results/ with n, rig and model pair stated; results are published either way including INCONCLUSIVE

### t27 — Round-robin head-to-head: full-Gemma vs mixed vs full-Qwen across an escalating League of Agents difficulty ladder

- instruction: Owns a NEW head-to-head harness under examples/ plus docs/live-test-results/. Do NOT edit examples/`league_seat.py` — t24 uses it; import from it or drive the league CLI directly.

THE HEAD-TO-HEAD REQUIREMENT (deviation d10). `league_seat.py` drives the rival with `rival_orders`(), a deterministic scripted policy (lines 1449 and 1592), NOT a model. Round-robin means BOTH teams are model seats. The cli.act(`match_id`, team, orders) seam is symmetric, so run the seat logic twice per turn, once per team, with different model configs. `league_seat.py` exposes --cortex-model and --muse-model, so an arm is a flag pair, never a code change.

THE THREE PAIRINGS: gemma-vs-mixed, mixed-vs-qwen, qwen-vs-gemma. Arms: full-Gemma = Gemma-4-31B as BOTH cortex and muse; full-Qwen = Qwen3.6-27B as BOTH; mixed = today shipped pairing, Qwen cortex + Gemma muse. Senses stays Gemma-4-12B in all three — the operator was explicit.

THE LADDER. The operator asked for "+5 in size"; board size is NOT a knob — league arena list ships exactly three fixed scenarios (skirmish-1 12x10 `turn_limit` 30, recon-1 14x12, skirmish-2 14x12 fogged `turn_limit` 16). Say that plainly in the write-up and name what you substituted. Escalate with what exists, in rising order: scenario skirmish-1 -> recon-1 -> skirmish-2, plus --max-actions TEAM:N tightening, shorter turn budgets, and --map-read fog / --unit-comms off as the hardest rungs. Run rungs in order and STOP climbing when the arms separate — separation is the result, exhausting the ladder is the fallback.

FAIRNESS, and it is load-bearing: both seats in a match get identical --max-steps and --max-tokens, and BOTH SIDES PLAY BOTH COLOURS at every rung (swap which arm is blue) or map/seat asymmetry confounds the whole result. Fix seeds and record them. Record that equal token budgets are NOT equal useful output: the Qwen cortex spends about 1000 tokens reasoning before emitting anything (measured — at `max_tokens` 300 it returns empty content with `finish_reason` length), while Gemma answers directly. Budget 3000+ for any Qwen seat.

RIG: gateway <http://localhost:8001/v1>, auth REQUIRED (Authorization: Bearer $`COLLEAGUE_API_KEY`, 401 without). cortex sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP, muse nvidia/Gemma-4-31B-IT-NVFP4. Only the cortex is local; a full-Qwen arm therefore puts BOTH roles on the one local GPU while full-Gemma puts both on the proxy — say so, because wall-clock is not a clean quality signal across arms.

PRE-REGISTER the ladder, the pairings, the fairness rules and the decision rule BEFORE the first dial, house style per docs/live-test-results/\*-preregistration.md, thresholds asserted by value in a test.

COST IS A RESULT, not overhead: report completion tokens and wall clock per arm beside outcome. t9 measured the muse reaching identical verdicts to the cortex for about 1/25 the tokens; if quality ties here, cost decides, and that is a publishable answer to the operator question.

- depends on: t24
- acceptance:
  - three arms differing only in which model serves cortex and muse (senses stays Gemma 12B in all three); an escalating ladder run in order until the arms separate or it is exhausted; every rung records its scenario, turn limit, actions cap, max-steps, seed and both model ids; results published either way including INCONCLUSIVE, with any rung that did not run reported absent; a per-arm cost column (completion tokens and wall clock) reported beside outcome, because equal quality at 25x the cost is itself the answer
  - the three pairings gemma-vs-mixed, mixed-vs-qwen and qwen-vs-gemma each play the ladder, with both teams driven by model seats rather than the scripted rival policy; the two seats in a match receive identical budgets and that equality is recorded in tokens, noting it is not equality of useful output since the Qwen cortex spends about 1000 tokens reasoning before emitting

### t28 — League of Agents commander experiment: Gemma commands Qwen unit agents on the continuous lane, against a mirror and flat baselines

- instruction: Owns a NEW harness under examples/ plus docs/live-test-results/. NO embodiment source change is needed or permitted.

THE ARCHITECTURE. loop.run(complete=..., subagent=..., `spawn_allowance`=N, lineage=...) already lets the host pick a different model per level — verified. Commander = the top-level run(). Unit agent = the child your SubagentFn drives. subagent.py states the protocol verbatim: pass SubagentCall.allowance through as `spawn_allowance`, .lineage as lineage, .`max_steps` as `max_steps`. `spawn_allowance` defaults to `NO_SPAWNS` and MUST be strictly positive or no child can exist. framing.py already types the two levels (`ROLE_CORTEX` top-level, `ROLE_SUBAGENT` children) per colleague 352.

THE ARENA — use the CONTINUOUS lane, this is why it fits. `league cmatch` asks for exactly ONE unit action at a decision point (the instant that unit goes idle), where `league match` asks for a whole-team turn order. So the commander/unit split is native. Verified working: scenario c-skirmish-1 exists and requires roster roles defender and harvester; `league team register <id> --agent ID:MODEL:ROLE --apply` declares the model per unit, so the arena keeps its own fairness record; cmatch verbs are new/show/act/tick/run, all DRY-RUN BY DEFAULT and needing --apply. `league explain cmatch` and docs/continuous-contract.md are the mind-facing contract — read them first. State is a pure fold of the log, so a killed harness resumes from the same working directory.

THE ARMS (fixed house-bot opponent, same seeds across arms):
 B — Gemma-4-31B commands, Qwen3.6-27B unit agents  \[the proposal\]
 C — Qwen commands, Gemma unit agents               \[the mirror — MANDATORY\]
 A — flat: one model decides every unit, no commander \[baseline; run for both models if budget allows, else Gemma since it is cheaper\]
The mirror separates architecture from model assignment; the flat baseline is what says whether hierarchy helps at all. Without both, a B win is unattributable.

BUDGETS: 16000 tokens for any Qwen level. Record `finish_reason` on every call — a `length` finish is TRUNCATION, an instrument event, and must never be scored as a bad decision or a lost match. At 6000 this cortex returns `finish_reason`=length with empty content and 12857 chars of reasoning; that already caused one published misreading.

REPORT TOKENS PER LEVEL (commander vs units, separately) — the entire question is whether paying for a large commander is worth it.

RIG: gateway <http://localhost:8001/v1>, auth REQUIRED (Authorization: Bearer $`COLLEAGUE_API_KEY`, 401 without). Only the cortex model is local; Gemma is proxied — so wall-clock is not a clean quality signal across arms. `tool_choice` is broken on this rig. Tasks t23 and t27 may be using the rig: a >600s timeout is CONTENTION, not a result — wait, retry, record the retry.

PRE-REGISTER before the first dial (house style: docs/live-test-results/\*-preregistration.md, thresholds asserted by value in a test). State the CEILING RISK and an ESCALATION PATH — four experiments in this cycle died at a ceiling (see issue 35); if the arms tie, climb rather than publish INCONCLUSIVE and stop.

Publish either way. Any arm that did not run is reported ABSENT, prominently.

- depends on: t27
- acceptance:
  - three arms driven entirely by host wiring with no embodiment source change — A: today, Qwen actor with final authority plus a tools-off Gemma muse that proposes and never decides; B: Gemma top-level coordinator delegating to a Qwen subagent developer; C: the mirror, Qwen coordinator delegating to a Gemma subagent developer; the mirror arm is mandatory because B changes both who coordinates and which model coordinates, and without C neither can be attributed; every arm records both model ids per level, the spawn allowance, and tokens consumed per level
  - the continuous lane is driven through league cmatch so each decision is one unit acting; the team roster declares the model per unit via --agent ID:MODEL:ROLE so the arena carries its own record of who played what; arms B (Gemma commands, Qwen units) and C (the mirror) run against the same fixed house-bot opponent with the same seeds, plus at least one flat single-model baseline; tokens are reported per level, not just per match

### t22 — Demonstrate the full flow end to end and publish the delivery summary

- instruction: Owns docs/deliveries/ + the delivery summary. Use the summarize-delivery skill; run devague summary for the skeleton. Report planned vs actual honestly — a lane that did not run is reported absent, never implied. Map every done-condition to an openable artifact path.
- depends on: t18, t9, t20, t24
- covers: c1, h1, c25, h5, c28, h22
- acceptance:
  - one scope-think-challenge-spec-to-plan-fan-out-summary cycle completes across the two minds with the human confirm gate intact; frame, plan and delivery summary are committed; every done-condition maps to an openable artifact path and any lane that did not run is reported absent

## Risks

- [unknown_nonblocking] docker workspace contention with local cortex inference on the shared 128 GB box is unprofiled: workspaces default to 0.5 GB and 1 CPU, but N concurrent muse sessions each holding a workspace is an unmeasured load beside a 27B model (task t13)
- [unknown_nonblocking] the storage ceiling is measured, not enforced, under the default local volume driver (probe warning) — a runaway workspace write is reported, not capped (task t15)
- [unknown_nonblocking] the devague grader surface may move mid-cycle: 0.22.0 just landed and further work is in flight, so pooling runs across versions would confound the gate DVs (task t9)
- [unknown_nonblocking] a tool-wielding muse session may never complete within the drive tail, leaving the terminal drain with nothing to deliver and the zero-late-drop target unreachable in practice (task t16)
