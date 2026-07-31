# Delivery Summary — muse cycle: pad, headspace, devague legs

plan: `muse-cycle-pad-headspace-devague-legs` · run: `partial` · date: `2026-07-31`
baseline: `devague summary skeleton`

## Intent

Ship the next muse cycle: a pre-registered three-arm pad+headspace validation
graded by the verified subset oracle, the devague method legs split across both
minds behind the human confirm gate, and the muse lane's close-time counsel loss
and two dead degradation codes resolved by explicit decision.

The plan's *After* clause is the one that decides whether this cycle succeeded:

> the end state is the method running end to end — scope, think, challenge, then
> spec-to-plan, fan-out to subagents, and a delivery summary — as the flow this
> embodiment can carry; achieving that flow is the point of the cycle (operator
> statement, 2026-07-29)

27 tasks (t1–t28 with `t8` rejected during planning), fanned out into isolated
worktrees under `../.worktrees.embodiment/`, each merged behind the TDD gate.
**74 commits, 131 files, +36,698 lines**, of which the great majority is
evidence rather than code.

**Read the headline numbers with their qualifier attached, because in three
places the qualifier is larger than the number.** The `#17` late-drop loss went
to **zero** — and the counsel that no longer drops late still **does not reach
the cortex 5 times in 13** (`#29`). The muse tool seam, the pad and the workspace
all **shipped** — and `tool_rounds` is **0** in every checked-in host. Five
verdicts came back `INCONCLUSIVE`, and one of the two that separated did so on a
single optional field. That is the state, not a framing of it.

## Planned Work

Quoted verbatim from the `devague summary` skeleton. `t8` was rejected during
planning and never entered the run; `t14` entered the plan and was absorbed into
`t13` mid-run (`d5`).

- `t1` — Snapshot the baseline numbers before any fix lands
- `t2` — Wire the two dead degradation codes to real background-compilation paths in `muse_runner`
- `t3` — Tighten the PROVOKERS contract and rewrite the two vocabulary-only tests
- `t4` — Terminal-boundary drain-without-consider: the synthesis boundary drains and never starts a muse session
- `t5` — Synthesis-drain observability record
- `t6` — Pre-register the devague-legs experiment under devague 0.22.0 with the lapse protocol wired
- `t7` — Record the devague-import decision: the experiment harness shells out to the devague CLI rather than importing it
- `t9` — Run the devague-legs experiment across both minds on a pre-seam commit, starting with the /deviate leg
- `t10` — The muse tool seam: a MuseCompleteFn successor carrying a tool schema, with structural pins
- `t11` — Tools-off byte-identity: the degrade floor and the rollback path for default-on
- `t12` — Wire the scratchpad as the muse first tool, top-level only
- `t13` — Land headspace-cli as a lazily-imported base dependency AND the workspace tool with its no-reach suite, in one change
- `t14` — No-secrets boundary on the workspace tool
- `t15` — Workspace lifecycle: bounded teardown and day-one degradation codes
- `t16` — Measure the tool-session latency distribution through the lobes proxy, then fix the c29 targets
- `t17` — Build the three-arm harness on the verified oracle
- `t18` — Run the three arms and classify every execution
- `t19` — Re-run the echo probe with a workspace-result arm
- `t20` — Flip muse tools default-on after the validation tuning pass
- `t21` — File the sibling-repo asks as issues through the communicate skill
- `t22` — Demonstrate the full flow end to end and publish the delivery summary
- `t23` — Run the live-rig test suite and the assigned challenge harnesses, recording results
- `t24` — Run the league/arena benchmark and record the series
- `t25` — Widen the terminal drain to fire at drive end on every exit reason, and wire `append_guidance` in an in-repo host
- `t26` — Wire a tool bench through ThreadedMuseRunner so tools are reachable inside a live drive
- `t27` — Round-robin head-to-head: full-Gemma vs mixed vs full-Qwen across an escalating League of Agents difficulty ladder
- `t28` — League of Agents commander experiment: Gemma commands Qwen unit agents on the continuous lane, against a mirror and flat baselines

## Actual Delivery

**26 of 27 delivered · 1 blocked.** Every task is accounted for. "Delivered"
means the task's acceptance criteria are met by a committed artifact — for the
experiment tasks that means *the series ran and published*, which is a different
claim from *the series found something*. Several found nothing resolvable, and
say so on the tin.

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | [`muse-cycle-baseline.md`](../live-test-results/muse-cycle-baseline.md) — late drops 1/run in 4 of 4 (4 of 16 insights, 25%), confidently-wrong 5 of 6 failures, dead codes 2 of 9. Merged `bb883fd` |
| `t2` | delivered | `ThreadedMuseRunner.compile()` gives `DROPPED_COMPILATION_STARVED` and `DROPPED_COUNSEL_DISPLACED` real emit sites; `TestUnproducedCodes` became `TestEveryRunnerCodeHasAProducer`. Merged `d4d9618` (`#18`) |
| `t3` | delivered | An AST check bans provokers reaching private attributes — the audit found **five** shortcuts, not the two `#18` named; three `TestWorkClassPriority` tests rewritten to drive real paths. Merged `867da00` |
| `t4` | delivered | `PresenceEngine.on_terminal_boundary()` drains without `consider()`. **Superseded in scope by `t25` under `d3`** — as merged it fired on no clean-finish drive. Merged `3d76bb5` (`#17`) |
| `t5` | delivered | `drain_terminal()` records delivered count + insight ids on `DELIVERY_POINTS`, a stream separate from the degradation ledger, zero included. Merged `ffaaf48` |
| `t6` | delivered | [`devague-legs-preregistration.md`](../live-test-results/devague-legs-preregistration.md) — committed `98386f6`, before the first dial; devague 0.22.0 pinned; thresholds asserted by value in `tests/test_devague_legs_preregistration.py` |
| `t7` | delivered | devague recorded as approved-but-deliberately-not-imported in `pyproject.toml` + `CHANGELOG.md`; the harness subprocesses the CLI. Merged `80e5fe3` |
| `t9` | **partial** | [`devague-legs.md`](../live-test-results/devague-legs.md). Experiment 2 ran (12 cases × 2 minds, 24/24, 0 transport errors). **Experiment 1 is ABSENT — 0 of 4 pipelines ran** (`l3`, `l4`); reported absent rather than `INCONCLUSIVE`. Merged `2a808d2` |
| `t10` | delivered | The muse tool seam: a tool-carrying successor to `MuseCompleteFn`, with `tests/test_muse_tool_loop_ast.py` proving one counter-bounded while and exits drawn only from declared constants. Merged `4136e04` |
| `t11` | delivered | `tests/test_muse_tool_identity.py` — prompts byte-identical to the pre-seam release with no tools wired; `MUSE_AUTHORITY` still first on every path. Merged `a8681e6` |
| `t12` | delivered | `embodiment/muse_pad.py` reuses `scratchpad.py` KINDS and schemas unchanged; subagent-depth muses get no tools; no recall surface includes pad entries. Merged `fe08478` |
| `t13` | delivered | `embodiment/workspace.py` + `headspace-cli>=0.11` as the fourth base dependency, in one commit with `_APPROVED_DEPENDENCIES`, `_REQUIRED_RUNTIME_IMPORTS` and the CHANGELOG rationale. `tests/test_workspace.py` (1,769 lines). Merged `5a5c04f` |
| `t14` | delivered (absorbed) | Shipped inside `t13` under `d5`; no separate task ran. `TestNoSecretsParameterExists` + an AST call-site allow-list + `test_a_full_session_sends_no_environment_and_no_inputs` in `tests/test_workspace.py` |
| `t15` | delivered | Bounded teardown (`DEFAULT_JOIN_TIMEOUT` 1.0s + `DEFAULT_DESTROY_TIMEOUT` 2.0s = 3.0s), an injected `closers` seam, and four codes each with an emitter and a real-path provoker. Merged `434ead0` |
| `t16` | delivered | [`muse-latency.md`](../live-test-results/muse-latency.md) — **`KEEP`**: worst tool session 22.09s against a shortest tail of 41.71s (margin 19.62s, 1.89×); `c31`'s ~6× penalty did not reproduce (1.23× shipped, 2.17× primed). Merged `6ff9bee` |
| `t17` | delivered | `examples/muse_arms.py` on the verified oracle (truth 76, trap 72, imported unchanged) + [`muse-arms-preregistration.md`](../live-test-results/muse-arms-preregistration.md), committed together and before the first dial. Merged `0cc0b73` |
| `t18` | delivered | [`muse-arms.md`](../live-test-results/muse-arms.md) — **`INCONCLUSIVE`**, n=8 per arm, 24 runs, 0 transport failures. Published against its own interest. Merged `eb53b50` |
| `t19` | delivered | [`workspace-echo-chamber.md`](../live-test-results/workspace-echo-chamber.md) — **`INCONCLUSIVE`**, 12/12 RESISTED, `c10` stays. Ran n=3 not the pre-registered n=5: a >600s gateway timeout killed replicate 4 (`l12`). Merged `0f81dce` |
| `t20` | delivered (rescoped) | Under `d8` this became the **record of a flip that did not happen**. README, CLAUDE.md and CHANGELOG state tools are opt-in and cite the series. Merged `8e1f792` |
| `t21` | delivered | [colleague#358](https://github.com/agentculture/colleague/issues/358) commented 2026-07-29 with the widened C1b consequence (a fourth base dependency); [headspace-cli#18](https://github.com/agentculture/headspace-cli/issues/18) filed 2026-07-29 (**answered by their 0.11.0, closed**) and [headspace-cli#22](https://github.com/agentculture/headspace-cli/issues/22) filed 2026-07-30 (open). No commit pushed to any sibling repo |
| `t22` | delivered | This document |
| `t23` | delivered | [`live-suite-and-challenges.md`](../live-test-results/live-suite-and-challenges.md) — **13 of 13** `EMBODIMENT_LIVE_RIG` tests passed; `challenge_subset` 3/3 CORRECT; `challenge_register` and `challenge_entropic` 3b relabelled **`INCONCLUSIVE`** (the token cap, not the model). Merged `45aed76` |
| `t24` | **blocked** | **ABSENT.** The pre-registration and the instrument landed on branch `muse-cycle/t24` (`a223ab4`, unmerged): `docs/live-test-results/arena-budget-preregistration.md` and a `--trace-out` on `examples/league_seat.py` carrying the `finish_reason` `ModelResponse` cannot (`#37`). **No measured match was recorded by the time this summary was written.** See [Remaining Work](#remaining-work--follow-up) |
| `t25` | delivered | `loop._presence_terminal()` fires exactly once per drive on **every** exit reason (finish, stopped, budget); `examples/proof.py` wires `append_guidance`. Merged `37bbe9f` (`#17`, `#23`) |
| `t26` | delivered, **unexercised** | `ThreadedMuseRunner(complete, tools=bench, depth=0)` reaches its `MuseLoop`; `counts["tool_rounds"]` is a readable number. **No checked-in host wires it** — `tool_rounds: 0` in the live gate (`l21`). Merged `9a51cc0` (`#30`) |
| `t27` | delivered | [`league-h2h.md`](../live-test-results/league-h2h.md) — **`SEPARATED` at L1**, full-qwen > mixed > full-gemma, 3/3 pairings, no cycle. L2–L4 **ABSENT** (the climb stops at the first separating rung, as pre-registered). Merged `aaddc9d` (`#34`) |
| `t28` | delivered | [`league-commander.md`](../live-test-results/league-commander.md) — **`INCONCLUSIVE` at a ceiling twice**: 12 of 12 matches at 19–0, then 6 of 6 at 10–0 on a 5.6× larger decision surface. Escalation E2 ABSENT (no between-match variance to double into). Merged `5e33dcc` (`#36`) |

## Mid-work Decisions

Fourteen deviations are recorded, thirteen approved and one still `proposed`.
Each is quoted from the `devague deviate` record; the record is the decision,
consumed here rather than re-litigated. Read them in full with
`devague deviate --list`.

- `d1` — the plan gains a **live-evidence gate before the delivery summary**:
  live-rig testing, assigned challenge harnesses and league/arena benchmarking
  must all run and be recorded before any lane is declared done. Operator
  instruction 2026-07-30. Added tasks `t23`, `t24`, `t25`.
- `d2` — headspace-cli is imported at module scope **only if its Python API is
  declared public**; otherwise the workspace tool subprocesses the CLI. Measured
  2026-07-30: headspace 0.10.0 advertised only `__version__` in `__all__`.
  Resolved by headspace-cli#18 → their 0.11.0 declares `headspace.api`;
  `headspace.core` is private and is never imported.
- `d3` — the terminal drain must fire at drive end **on every exit reason**, and
  at least one in-repo host must wire `append_guidance`. Measured by `t4`:
  `_maybe_force_synthesis` returns early on a clean non-empty summary, so a
  clean-finish drive produced **zero** terminal boundaries and the `t4` fix never
  fired. This is the most consequential mid-run finding in the cycle — without
  it, honesty condition `c22` would have passed **vacuously**.
- `d4` — `ThreadedMuseRunner` cannot carry a tool bench, so tools are unreachable
  inside a live drive and `t20` has nothing to flip. Added `t26`.
- `d5` — `t14`'s scope was absorbed into `t13` and shipped there; no separate
  task ran. Spawning it would duplicate shipped tests.
- `d6` — `t17` authored the three-arm pre-registration itself, **because none
  existed**: `t18`'s instruction pointed at `t6`'s devague-legs pre-registration,
  a different experiment. A plan defect in my own task instruction, found and
  repaired before any measured dial.
- `d7` — the arm harness adds a **closing turn**: one tools-off constant appended
  to the muse's own history, put to every arm alike. Handed a pad, the muse
  emitted `finish_reason: tool_calls` with **empty content on all 8 turns** (6
  pad writes, zero prose), so arms B and C returned `NO_ANSWER` every run.
- `d8` — **muse tools do NOT flip default-on**; `t20` becomes the record of why.
  Operator-confirmed. The standing `q6` rule was "the measured failure mode never
  ships as default behaviour", and `t18` measured one.
- `d9` — add the full-Gemma vs full-Qwen homogeneous-arm experiment across an
  escalating ladder, to decide whether to consolidate to one model. Operator
  request 2026-07-31.
- `d10` — that experiment is a **round-robin head-to-head**, not three solo runs
  against the house bot: both teams driven by model seats, since
  `league_seat.py`'s rival is a deterministic scripted policy.
- `d11` — **still `proposed`, not approved**: `t19` added a fourth, post-hoc
  `instruction` arm beyond the workspace arm and control, its acceptance named.
  Recorded here as an open item, not as a decision.
- `d12` — add the architecture experiment: Gemma as top-level coordinator with
  final authority delegating to a Qwen subagent developer, plus a **mandatory
  mirror arm**, because the proposal changes two variables at once.
- `d13` — `t28` rescoped to the concrete League instantiation on the **continuous
  lane**, where `cmatch` asks for exactly one unit's action at a decision point.
- `d14` — design decisions taken under delegated authority for `t28`: continuous
  lane, four arms with two flat baselines, a fixed house-bot opponent, and
  per-**level** token accounting.

Decisions no deviation record covers, captured here directly:

- **The `t28` ceiling clause was a real bug, fixed in the open** (`841c90c`):
  `compare()` compared sorted lists rather than sets, which would have
  mislabelled identical-value / unequal-n arms as `NO_EFFECT`. The correction
  only ever moves a verdict *toward* `INCONCLUSIVE` — the unflattering direction.
- **`t27`'s `rejections` counter was left wrong on purpose.** It reported 0 while
  league's own log held 2. Pinned by a test and recorded in
  [`corrections.md`](../live-test-results/corrections.md) rather than retuned
  after the data (`l15`). It never reached a verdict — `rejections` is the third
  tie-break.
- **`t27`'s token budget was amended 3,000 → 16,000 before the first dial**
  (amendment 1, with amendment 2 sizing the wall-clock caps to match). See
  [The instrument that decided an outcome](#the-instrument-that-decided-an-outcome).
- **`t23` did not re-run or re-score `memory-echo-chamber.md`** despite
  discovering that its published DEFERRED 6/6 was measured at a 700-token cap.
  The result stands with the caveat attached rather than being quietly restated.

## Drift From Plan

Exhaustive relative to the plan. Entries covered by an approved deviation cite it
and inherit its classification verbatim; entries no record covers are worked out
here.

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t22` (`d1`) | operator instruction 2026-07-30: live-testing, assigned challenges and league benchmarking are steps before declaring done; the confirmed plan had no arena/league benchmark task and no explicit pre-summary evidence gate | `needs-follow-up` |
| `t13` (`d2`) | headspace 0.10.0 advertised only `__version__` in `__all__`, so `t13` as written would import an undeclared API a minor release could reshape; the ask is filed as headspace-cli#18 and is theirs to answer | `risky` |
| `t4` (`d3`) | `_maybe_force_synthesis` returns early on a clean non-empty summary, so a clean-finish drive produces zero terminal boundaries and the `t4` fix never fires; and no checked-in host wired `append_guidance`, so counsel landed on `_noop_guidance` | `needs-follow-up` |
| `t20` (`d4`) | `muse_runner.py`:596-602 constructed `MuseLoop` with no `tools=`, so no supported path put a bench inside a live drive and the default-on flip `q6` authorised was not executable as specified | `needs-follow-up` |
| `t14` (`d5`) | `t14` acceptance in full was delivered inside `t13`, because the seam and its no-secrets proof are the same change | `acceptable` |
| `t17` (`d6`) | plan defect in my own task instruction: no pre-registration for the three-arm experiment existed; `t17` wrote one and fixed rule R-C1 before any measured dial | `acceptable` |
| `t17` (`d7`) | smoke-driven, named nowhere in the plan: the muse emitted tool calls with empty content on all 8 turns, so DV1 was measuring the turn budget rather than correctness | `needs-follow-up` |
| `t20` (`d8`) | `t18` returned `INCONCLUSIVE` at an arm-A ceiling **and** measured harm; by the operator's own stated rule that must not ship as the default | `needs-follow-up` |
| `t24` (`d9`) | operator request 2026-07-31: every run in the cycle used the mixed pairing, so nothing measured whether a single model would do as well; escalating difficulty is also the direct fix for the ceilings | `needs-follow-up` |
| `t27` (`d10`) | head-to-head answers "which is better" directly; it required harness work, because `league_seat.py`'s rival is a scripted policy rather than a model | `needs-follow-up` |
| `t27` (`d12`) | operator proposal 2026-07-31; verified expressible today with no embodiment change via `loop.run`'s `subagent` seam, and the mirror arm is required because two variables move at once | `needs-follow-up` |
| `t28` (`d13`) | the arena supports the commander/unit split natively on the continuous lane, so rescoping beats adding a `t29` | `acceptable` |
| `t28` (`d14`) | the operator delegated further design decisions rather than being asked for each | `needs-follow-up` |
| `t9` | **No record covers this.** Experiment 1 — the `/think` and `/spec-to-plan` legs across both minds — did not run: 0 of 4 pipelines. It is structurally blocked: the operator forbade state-mutating `devague` commands, *and* the pre-registration requires a human `--confirm` per claim across all four pipelines. Reported ABSENT rather than `INCONCLUSIVE`, because a mechanical `INCONCLUSIVE` would imply an instrument that ran | `needs-follow-up` |
| `t24` | **No record covers this.** The task did not complete within the cycle. Its pre-registration and instrument are committed on `muse-cycle/t24` (`a223ab4`); no measured match exists. The plan made `t22` depend on `t24`, so this summary ships with one of its four dependencies unmet, and says so | `needs-follow-up` |
| `t16` | **No record covers this.** The task's own acceptance is met — the probe reports session latencies against the drive tail and `c29` is `KEEP` — but the **tools-on-in-drive lane is absent, and the `KEEP` odds are an estimate compounding two separately-measured distributions**, never an observed in-drive rate. `d4` and `t26` grew the seam; `l7` and `l21` record that the measurement is still absent | `needs-follow-up` |
| `t19` | **No record covers this.** The pre-registered n was 5 per arm; 3 ran. A >600s gateway read timeout raised `LoopAborted` and `probe()` let it through, ending the series at 9 of 15 cells (`l12`). The stopping point was chosen by a socket, not by the data | `risky` |
| `t27` | **No record covers this.** L2–L4 did not run. This is *not* drift — the pre-registration says stop climbing once the arms separate, and they separated at L1. Recorded here so three absent rungs are never read as an oversight | `acceptable` |
| `t28` | **No record covers this.** `N_MATCHES` deviated from the pre-registered 5 down to 3, because sibling task `t23` was holding the rig at 94% GPU load. Recorded in the results doc rather than smoothed | `acceptable` |

## Evidence

Every check below was run read-only against merge commit `f6c423f` on branch
`cycle/muse-pad-headspace`.

- tests: `uv run pytest -n auto -q` — **3168 passed, 17 skipped** in 28.39s. This
  task is docs-only; the count is unchanged from the pre-`t22` baseline.
- tests (skips, all opt-in live gates): 13 × `EMBODIMENT_LIVE_RIG`, 4 ×
  `EMBODIMENT_LIVE_ARENA`. The 13 rig-gated tests **were run and passed** under
  `t23`; the 4 arena-gated tests belong to `t24` and were **not run**.
- lint: `markdownlint-cli2 "**/*.md" "#node_modules" "#.local" "#.claude/skills" "#.teken"` — pass.
- lint: `uv run black --check`, `uv run isort --check-only`, `uv run flake8`,
  `uv run bandit -c pyproject.toml -r embodiment examples` — unchanged by this
  docs-only task.
- commits: `9e88b29..f6c423f` — 74 commits, 131 files, +36,698 / −293 lines; 23
  of them are `merge(tN)` commits, one per task worktree.
- deviation ledger: `devague deviate --list` — 14 records `d1`–`d14`; `d11` is
  `proposed`, the other thirteen `approved`.
- lapse ledger: `devague lapse --list` — **24 records `l1`–`l24`**, all
  `proposed`. (This task reported 21, correctly: at `f6c423f` the *committed*
  ledger held 21 while `l22`–`l24` sat in uncommitted working state. Resolved
  at `c3e6065`, which committed them. The discrepancy is itself an instance of
  `l22` — a record that exists only in working state is not a record anyone
  else can read — and it had by then happened twice.)
- raw data, all committed under `docs/live-test-results/`: `*.jsonl`,
  `*-config.json`, `*-trace.json`, `league-commander-logs/`,
  `league-commander-frontier-logs/`, `live-suite-raw/`.
- issues opened on this repo during the cycle: `#23`–`#37`.
- issues touched on sibling repos: colleague#358 (comment), headspace-cli#18
  (closed by their 0.11.0), headspace-cli#22 (open).

## Delivery Claims

Each claim carries a confidence and a resolvable pointer, or an explicit
`unverified` marker. **A claim with no artifact is reported not-done.**

### Mechanism claims — the code exists and is pinned

| Claim | Confidence | Evidence |
|-------|------------|----------|
| The two dead degradation codes now have real emit sites, and a code can never again pass by declaration | high | `embodiment/muse_runner.py` `compile()`; `TestEveryRunnerCodeHasAProducer` walks call *arguments* to `_record`/`_degrade`; commit `d4d9618` |
| A provoker can no longer satisfy the exhaustiveness guard through the private door | high | AST check in `tests/test_ledger.py`; demonstrated red once; **five** shortcuts found, not the two `#18` named; commit `867da00` |
| The terminal drain fires exactly once per drive on finish, stopped **and** budget | high | `loop._presence_terminal()`; `tests/test_presence_engine.py`; commits `3d76bb5`, `37bbe9f` |
| The terminal drain's delivery is observable, with zero distinguishable from never-ran | high | `drain_terminal()` on `DELIVERY_POINTS`, separate from `RUNNER_CODES`; commit `ffaaf48` |
| The muse tool seam terminates, proved structurally rather than behaviourally | high | `tests/test_muse_tool_loop_ast.py` — one counter-bounded while, exits drawn only from declared constants; commit `4136e04` |
| With no tools wired, the muse's prompts are byte-identical to the pre-seam release | high | `tests/test_muse_tool_identity.py`; commit `a8681e6` |
| The muse pad reuses the actor pad protocol rather than inventing a second one | high | `embodiment/muse_pad.py` imports `scratchpad.py` KINDS and schemas unchanged; commit `fe08478` |
| The workspace cannot reach the repo, the eidetic store or the network — held by construction, not by intent | high | `embodiment/workspace.py` builds no `policy` and never calls `put`/`export`; under `EMBODIMENT_LIVE_RIG` only `lo` exists inside and bridge connects fail `[Errno 101]`; `tests/test_workspace.py` |
| No secrets parameter exists on the workspace seam, so the leak path cannot be opened by configuration | high | `TestNoSecretsParameterExists` + an AST call-site allow-list + `test_a_full_session_sends_no_environment_and_no_inputs` (`t14` via `d5`) |
| Workspace teardown is bounded at a stated 3.0s, and a survivor is reported rather than hidden | high | `DEFAULT_JOIN_TIMEOUT` 1.0s + `DEFAULT_DESTROY_TIMEOUT` 2.0s; `workspace-left-live` carries the id and the two reaping commands; commit `434ead0` |
| `headspace-cli` entered as a base dependency **through** the gate, not around it | high | `pyproject.toml`, `_APPROVED_DEPENDENCIES`, `_REQUIRED_RUNTIME_IMPORTS` and the CHANGELOG rationale in the same commit as the first import (`5a5c04f`); `import embodiment` still costs nothing |
| `ThreadedMuseRunner` can carry a tool bench into a live drive | high | `ThreadedMuseRunner(complete, tools=bench, depth=0)`; a differential test against the pre-`t26` argument list; commit `9a51cc0` (`#30`) |

### Measured claims — each with its n, and each with its qualifier

| Claim | Confidence | Evidence |
|-------|------------|----------|
| **Late drops went to zero.** Baseline was 1 per run in 4 of 4 (4 of 16 insights, 25%); the post-fix series measured 0 in 4 of 4, delivery 62.5% → 81.25% | medium — n=4, one problem, one rig | [`muse-cycle-baseline.md`](../live-test-results/muse-cycle-baseline.md), [`muse-latency.md`](../live-test-results/muse-latency.md), `muse-latency-drives.jsonl` |
| **`c29`'s zero-late-drop target is `KEEP`** — worst tool session 22.09s against a shortest tail of 41.71s; margin 19.62s, factor 1.89 | medium — n=32 sessions, n=4 drives | [`muse-latency.md`](../live-test-results/muse-latency.md), `muse-latency-sessions.jsonl` |
| `c31`'s predicted ~6× tools-on penalty **did not reproduce**: 1.23× in the shipping shape, 2.17× primed | medium | same |
| **5 of 13 counsel lines never reach the cortex.** Terminal-drain counsel lands on *presence*; on a clean finish there is no later completion for it to ride into | high (mechanism), medium (rate, n=4) | [`muse-latency.md`](../live-test-results/muse-latency.md) residue table — 13 appended / 8 reached / 5 never; [`live-suite-and-challenges.md`](../live-test-results/live-suite-and-challenges.md) — 4 appended / 3 reached / 1 undelivered. Filed as [`#29`](https://github.com/agentculture/embodiment/issues/29) |
| **Muse tools did not earn default-on.** n=8 per arm, 24 runs, 0 transport failures: arm A 8 correct / 0 wrong / 0 no-answer; arm B 2 / 1 / 5; arm C 4 / 0 / 4 | high | [`muse-arms.md`](../live-test-results/muse-arms.md), `muse-arms.jsonl`; decision recorded as `d8` and in `CHANGELOG` 0.9.0 |
| The muse **cannot reliably call the workspace tool**: 17 of 23 calls refused for a string `command`; 3 of 8 arm-C runs landed zero accepted commands | high | same. Filed as [`#33`](https://github.com/agentculture/embodiment/issues/33) |
| Handed a pad on a tools-off closing turn, the muse **emits a tool call instead of prose** and the gateway drops it — 8 of 9 `NO_ANSWER` runs reproduced on replay | high | [`muse-arms.md`](../live-test-results/muse-arms.md), `muse-arms-closing-turn-diagnostic.json`. Filed as [`#32`](https://github.com/agentculture/embodiment/issues/32) |
| **Both minds returned identical verdicts on all 12 deviate cases**, 11/12 against ground truth each; the muse used ~1/25 the completion tokens (557 vs 13,852) and ~1/10 the wall clock (64.8s vs 637.7s) | high for the numbers, **low for what they mean** | [`devague-legs.md`](../live-test-results/devague-legs.md), `devague-legs-deviate.jsonl`. **The pools are separable by input length alone** — positives 506–1228 chars, negatives 123–221 — so "long implies deviation" scores 11/12 without reading a word (`l5`, [`#28`](https://github.com/agentculture/embodiment/issues/28)) |
| **The muse's cost advantage is real and large**, and reproduces in the arena: full-gemma 950 completion tokens per match vs mixed 12,819 (13.5×) vs full-qwen 18,410 (19.4×) | high | [`league-h2h.md`](../live-test-results/league-h2h.md), `league-h2h.jsonl` |
| **`SEPARATED` at L1** — full-qwen > mixed > full-gemma, 3 of 3 pairings, `cyclic: false` | medium — n=4 matches per arm | [`league-h2h.md`](../live-test-results/league-h2h.md) |
| **…and that ranking rests on one optional field.** The game tied **0–0 in all six matches**; three of the four cooperation signals sit at a ceiling for every arm; the whole ordering is `message_utility`, on which the Gemma cortex sent a team message on **0 of 12** seat-turns against full-Qwen's 9 | high | same, and `l16`. The doc says so in its own verdict section |
| Identical minds **never separate** on the same ladder — the offline control, 24 matches, 4 of 4 rungs `INCONCLUSIVE`; the colour bias is real (90 vs 45 under fog between byte-identical minds) and the paired `net_margin` cancels it to exactly 0.0 | high | `league-h2h-scripted-control.jsonl` — the instrument check that had to pass before the series meant anything |
| **Hierarchy costs 2.4–4.4× a flat mind for identical results**, and its bill is ~95% prompt (commander 39,543 prompt vs 1,955 completion tokens) | medium — n=3, at a ceiling | [`league-commander.md`](../live-test-results/league-commander.md), `league-commander.jsonl` |
| The commander **overrode 1 of 82 proposals** while consulting at 100% of decision points | medium | same, `league-commander-transcripts.jsonl`. The doc is explicit that this is a property of *problems that produced no disagreement*, not of the model |
| **13 of 13 live-rig tests passed**, and `challenge_subset` answered 76 correctly 3 of 3 | high | [`live-suite-and-challenges.md`](../live-test-results/live-suite-and-challenges.md), `challenge-subset.json`, `challenge-subset-trace.json` |
| Three-way rig contention is real and measurable: a trivial prompt cost the Qwen cortex 99.7s and 147.8s against a 9.3s uncontended baseline | medium | [`league-h2h.md`](../live-test-results/league-h2h.md) contention probe |

### The instrument that decided an outcome

This deserves its own section, because it is the cycle's clearest methodological
finding and because it is easy to state one notch too strongly.

`t27`'s pre-registration was **amended before the first dial** (amendment 1) to
raise the cortex token budget from the originally-briefed **3,000 to 16,000**.
Measured spend was ~4,000 completion tokens per seat-turn for the Qwen cortex. At
3,000 the Qwen seats would have truncated on essentially every turn, returned
empty content, staged no orders and lost every match — and
[`league-h2h.md`](../live-test-results/league-h2h.md) would have reported
**full-Gemma as the better model**. The measured ranking is the reverse.

**The precise claim:** the amendment landed *pre-dial*, so this is arithmetic off
the measured spend, **not an observed flip of a published result**. No ranking
was reversed by re-running anything. That distinction matters, and stating it the
stronger way would be exactly the overclaim this document exists to prevent. What
*is* established: at a plausible, briefed instrument setting this experiment would
have returned the opposite answer, and nothing in its own output would have looked
wrong.

The same instrument class caught `t23`: `challenge_register` and
`challenge_entropic` 3b graded **WRONG**, then were relabelled **`INCONCLUSIVE`**
once the traces showed turns burning a full 16000/16000 with empty content. The
general form of the defect is filed as
[`#37`](https://github.com/agentculture/embodiment/issues/37) — **`ModelResponse`
carries no `finish_reason`, so a truncated turn and a stopped turn are
indistinguishable to the loop.** That is a C3 violation in embodiment's own
contract, found by this cycle and **not fixed by it**; `l20` records it. `t24`'s
unmerged `--trace-out` is a harness-level workaround, not the contract fix.

### The five `INCONCLUSIVE` verdicts — what they are, and are not

Five verdicts came back `INCONCLUSIVE`. They are **results**: each is a
pre-registered design refusing to license a conclusion its data cannot carry.
But they are not all the same shape, and
[`#35`](https://github.com/agentculture/embodiment/issues/35) — which classifies
the pattern and proposes *escalate until separation* as the general fix — covers
the ceiling cases only.

| Verdict | Cause | At a ceiling? |
|---|---|---|
| [`muse-arms.md`](../live-test-results/muse-arms.md) (`t18`) | arm A is 8/8 correct with 0 confidently wrong — no headroom for a tool lane to improve | **yes** |
| [`league-commander.md`](../live-test-results/league-commander.md), primary lane (`t28`) | all four arms won 12 of 12 at 19–0 | **yes** |
| [`league-commander.md`](../live-test-results/league-commander.md), escalation E1 (`t28`) | all arms won at 10–0 on a 5.6× larger surface; every validity gate passed, so the tie is the arena's and not the instrument's | **yes** |
| [`workspace-echo-chamber.md`](../live-test-results/workspace-echo-chamber.md) (`t19`) | **not a ceiling — an insensitive instrument.** Its own positive control (the costume that beat the cortex 6/6) also moved nothing, so 12/12 RESISTED cannot distinguish a safe channel from a blind probe (`l13`) | no |
| `challenge_register` + `challenge_entropic` 3b (`t23`) | **not a ceiling — the token cap.** Graded WRONG, relabelled `INCONCLUSIVE` once traces showed 16000/16000 truncation | no |

`t28` is the one that **climbed** rather than publishing `INCONCLUSIVE` and
stopping: the E1 escalation onto `c-frontier-1` is `#35`'s own recommendation,
executed the same week it was written. Two further experiments named in `#35`
(`association-work`, `arena-series`) are from the previous cycle and remain
un-escalated.

### Claims deliberately not made

Each of these is something a reader could reasonably infer from the numbers
above. None of them is supported.

| Non-claim | Status | Why |
|-----------|--------|-----|
| **"The `#17` counsel loss is fixed."** | **not claimed** | Late drops are 0. Counsel *reaching the cortex* did not follow: 5 of 13 lines never arrive — a **larger** loss than the 4 of 16 the cycle set out to fix. `#29`, open |
| **"The muse tool lane works in a live drive."** | **unverified** | `t26` shipped the seam; `tool_rounds` is 0 in the live gate and **no checked-in host wires a bench** — `proof.py`:732, `greenhouse.py`:714, `league_seat.py`:1113 all no (`l21`). The only in-drive tool round ever observed is scripted, in a test file |
| **"The tools-on completion odds are measured."** | **unverified** | They are an estimate compounding two separately-measured distributions. `c31`'s honesty condition is unsatisfied until a tools-on drive is dialled live (`l7`) |
| **"Either mind can judge whether a record warrants a deviation."** | **not claimed** | 11/12 each, identical — but the pools are length-separable, so the number is not evidence of judgment (`l5`, `#28`) |
| **"The human confirm gate fires."** | **partially verified** | *No confirms happened* is proven by digest — 24/24 judgments `origin=llm`, `status=proposed`, `confirmed_by=null`, ledger sha256 unchanged before and after. *The gate fires* is proven only by **reading `devague/delivery.py`**; no live `--origin llm` capture was issued, because it would have written to the real ledger (`l6`) |
| **"full-Qwen plays league better."** | **not claimed** | The game tied 0–0 in all six matches. The separation is on interface compliance, not play (`l16`) |
| **"The muse mattered in the arena."** | **not claimed** | `t27` has no muse-off cell, so the mixed-vs-full-Qwen difference cannot be attributed (`l17`) |
| **"Gemma is, or is not, the better commander."** | **not claimed** | At a ceiling, twice. `league-commander.md` says so in its own words |
| **"A ~0 override rate is a property of the model."** | **not claimed** | It is a property of problems that produced no disagreement to resolve |
| **"The workspace echo channel is safe."** | **not claimed** | 12/12 RESISTED from an instrument with no measured sensitivity is an absence of evidence, not evidence of absence. `c10`'s pad-recall boundary **stays shut** (`l13`) |
| **"The cycle's second-mind review happened."** | **unverified** | Two `ask-colleague review` drives on the cycle diff ended incomplete with **zero tool steps**. No independent review of the merged code was obtained (`l2`) |
| **"The `--muse` flag on the challenge harnesses does anything."** | **false, and left in place** | It is dead: zero muse machinery in those files. Its only effect is writing `muse_model` into the config preamble — a config record naming a muse for a run where none was dialled, in the artifact that exists to prevent exactly that (`l19`) |
| **"Any of this generalises past this rig."** | **unverified** | One cortex, one muse, one gateway, one box. n ≤ 8 per arm everywhere except `t28`'s 12-match primary lane |

### The lapse ledger

`devague lapse --list` holds **24 records** (`l1`–`l24`), every one `proposed`,
every one filed by the task that made the substitution rather than reconstructed
afterwards. They are the cycle's own record of where a check was replaced by an
assumption. Grouped by code:

- **`control-absent`** — `l3` (Experiment 1's control and cross arms did not run
  at all), `l13` (the echo probe's positive control moved nothing), `l17` (`t27`
  has no muse-off cell).
- **`n-below-claim`** — `l4` (0 of 4 pipelines, so validity gate V1 is unmet and
  no gate DV has a value), `l12` (the echo series stopped at 9 of 15 cells on a
  socket timeout), `l18` (`t27` ran n=4 per arm, below what a ranking claim
  needs).
- **`grader-unverified`** — `l6` (the confirm gate is evidenced statically), `l8`
  (`ArmTools._ran` counted an exit-127 job as an execution), `l10` (R-C1's token
  match turns on a **trailing space**; the hand audit reversed two
  classifications), `l11` (the degradation gate does not name which codes count),
  `l15` (`t27`'s rejections counter reported 0 against league's 2), `l20`
  (`ModelResponse` has no `finish_reason`).
- **`assumption-for-measurement`** — `l1` and `l7` (the `c29` target predates an
  in-drive measurement), `l5` (the deviate pools are length-separable), `l9` (the
  `d7` mitigation was validated at n=1 and fails 9 of 16 at n=8), `l16` (`t27`'s
  tie-break was chosen to measure play and measured interface compliance), `l21`
  (`t26`'s in-drive tool lane is shipped and unexercised).
- **`provenance-missing`** — `l2` (no second-mind review), `l14` (one degraded
  call not retried — HTTP 503 recovering the cortex reasoning text), `l19` (the
  dead `--muse` flag writing a false config record).

### The flow acceptance — every done-condition to an openable path

`t22`'s acceptance criterion is the contract this section discharges.

| Done-condition | Artifact | Status |
|---|---|---|
| a **scope** leg ran | `.devague/frames/muse-cycle-pad-headspace-devague-legs.json` → `devague scope --list` — 20 entries `s1`–`s20`, each citing the file and line range actually explored | ✅ |
| a **think** leg ran and converged | [`docs/specs/2026-07-29-muse-cycle-pad-headspace-devague-legs.md`](../specs/2026-07-29-muse-cycle-pad-headspace-devague-legs.md) — 41 claims, `convergence: PASSED`, three parked unknowns | ✅ |
| a **challenge** leg ran | Recorded **as scope entries**, not as a separate document: `s19` (adjacent-systems lens — drain path, rig topology, docker daemon) and `s20` (assumptions-and-counter-evidence lens), seeding claims `c30`, `c31`, `c32`, `c40`. Named here because a reader looking for a `challenge` artifact will not find one | ✅ located, not co-located |
| a **spec-to-plan** leg ran | [`docs/plans/2026-07-29-muse-cycle-pad-headspace-devague-legs.md`](../plans/2026-07-29-muse-cycle-pad-headspace-devague-legs.md) — 27 tasks, 1 rejected, 4 risks parked | ✅ |
| a **fan-out** happened | 74 commits `9e88b29..f6c423f`, 23 of them `merge(tN)` commits, one per task worktree, each gated on a green suite | ✅ |
| a **delivery summary** is committed | this file | ✅ |
| the cycle ran **across the two minds** | **PARTIAL.** One leg — `/deviate` — was measured on both minds: [`devague-legs.md`](../live-test-results/devague-legs.md), 12 cases × 2 minds, 24/24 completions. The `/think` and `/spec-to-plan` legs across both minds are **Experiment 1, ABSENT** (`l3`, `l4`). The *authoring* of this cycle's own frame and plan was single-mind | ⚠️ |
| the **human confirm gate** is intact | `devague-legs-deviate.jsonl` + `devague-legs-deviate-config.json` — 24/24 `origin=llm`, `status=proposed`, `confirmed_by=null`; ledger sha256 digests unchanged before and after; `devague deviate --list --json` unchanged. `d11` is still `proposed` and is not treated here as a decision | ✅ intact; ⚠️ *fired* is static-only (`l6`) |
| **frame, plan and delivery summary are committed** | all three above, in-tree | ✅ |
| **any lane that did not run is reported absent** | `t24` (the whole task), Experiment 1 (`t9`), rungs L2–L4 (`t27`), escalation E2 (`t28`), the 4 `EMBODIMENT_LIVE_ARENA` tests and `challenge_entropic` variant 3 and the no-muse `proof.py` control (`t23`), the tools-on-in-drive lane (`t16` / `t26`) | ✅ |

## Remaining Work / Follow-up

**Blocking nothing in this PR.** Everything here is recorded rather than hidden.

### The one blocked task

- **`t24` — the league/arena series, ABSENT.** Its pre-registration
  (`docs/live-test-results/arena-budget-preregistration.md`) and its instrument
  (`examples/league_seat.py --trace-out`, appending one JSON object per live
  completion carrying the `finish_reason` `ModelResponse` cannot) are committed
  on branch `muse-cycle/t24` at `a223ab4` and **not merged**. No measured match
  exists. What it would have covered: a 2×2 of arm × cortex `max_tokens`, n=3,
  seeds 4242/4243/4244, muse off, the seat playing blue against the house bot —
  i.e. **the token cap as an independent variable**, which is precisely the
  hidden variable `t23` and `t27` both tripped over. It also carries the only
  measurement that would retire a known preamble defect: the `t19` arena series
  ran its whole 24-match matrix at the 2048 default and its config record did not
  say so. Next step: finish the series, then merge. Owner: this repo.
  **This summary ships with one of `t22`'s four declared dependencies unmet.**

### Filed, not fixed

- **[`#29`](https://github.com/agentculture/embodiment/issues/29)** — counsel
  delivered at the terminal drain reaches no cortex turn on a clean finish, 5 of
  13 lines. Larger than the loss `#17` fixed. Owner: this repo.
- **[`#37`](https://github.com/agentculture/embodiment/issues/37)** —
  `ModelResponse` carries no `finish_reason`. A **C3 violation in embodiment's
  own contract**, found by this cycle, filed 2026-07-31, not resolved: two
  challenge harnesses reported `exit=stopped` with an empty degradation list
  while truncating at a full 16000 budget (`l20`). Owner: this repo.
- **[`#32`](https://github.com/agentculture/embodiment/issues/32)** and
  **[`#33`](https://github.com/agentculture/embodiment/issues/33)** — the muse
  writes tool calls instead of prose, and cannot form a workspace `command`
  array. Both must be fixed before a default-on flip is revisitable at all.
- **[`#30`](https://github.com/agentculture/embodiment/issues/30)** — closed by
  `t26`, but **nothing uses the seam**. The next in-drive tool measurement needs
  a host that wires a bench (`l21`).
- **[`#35`](https://github.com/agentculture/embodiment/issues/35)** — the
  escalation methodology. `t28` executed it once; `association-work` and
  `arena-series` are named there as the remaining candidates.
- **[`#26`](https://github.com/agentculture/embodiment/issues/26)**,
  **[`#27`](https://github.com/agentculture/embodiment/issues/27)** and
  **[`#28`](https://github.com/agentculture/embodiment/issues/28)** — a
  never-raise contradiction in `MuseLoop.think`, two independent-review
  observations, and the length-separable deviate pools.

### Method debt this cycle created

- **All 24 lapses are `proposed`.** None has been adjudicated. They are the
  honest record of substituted checks, and they stay open until someone decides
  each one is acceptable or must be redone.
- **`d11` is still `proposed`** — `t19`'s post-hoc `instruction` arm. It is
  labelled post-hoc in code and excluded from `--arm all`, so nothing depends on
  approving it, but the deviation record is not closed.
- **No second-mind review of the cycle diff exists** (`l2`). Two `ask-colleague
  review` drives ended with zero tool steps. The review reflex this repo's
  `CLAUDE.md` calls for did not complete on 36,698 lines.
- **The `--muse` flag on all three challenge harnesses is dead and writes a false
  config record** (`l19`), and is deliberately left in place for now.

### Not ours to close

- **[colleague#358](https://github.com/agentculture/colleague/issues/358)
  (C1b)** — now a **four**-base-dependency ask after `headspace-cli`. colleague
  cannot import embodiment until it relaxes its one-base-dependency rule *and*
  its no-third-party-import assertion. Updated 2026-07-29 under `t21`. **Theirs
  to decide.**
- **[headspace-cli#22](https://github.com/agentculture/headspace-cli/issues/22)**
  — `headspace.api` cannot reap a busy workspace. embodiment ships a
  `workspace-left-live` record and a rig-gated test that *executes the recorded
  remedy*, rather than assuming a relaxation. Open, theirs.
- **Depth in the live evidence.** Every result in this cycle is n ≤ 8 per arm
  (n ≤ 12 matches on `t28`'s primary lane) on **one rig with one model pair**.
  The honest fix is more rigs and more n, not stronger wording — the same
  sentence the previous two delivery summaries ended on, and still true.
