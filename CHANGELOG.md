# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0] - 2026-07-30

### Added

- Background compilation as a muse work class: `ThreadedMuseRunner.compile()` offers tools-off compilation of the host's recalled material, admitted behind boundary counsel on the runner's single slot. This gives the two long-dead degradation codes real producing paths — `DROPPED_COMPILATION_STARVED` when a compilation item never reaches the thread, `DROPPED_COUNSEL_DISPLACED` when compilation evicts boundary counsel from a full drain buffer (a priority inversion, and a directional refinement of `DROPPED_OVERFLOW` rather than a separate mechanism). Closes the emitter half of #18 (t2)
- A terminal drain at drive end: `PresenceEngine.on_terminal_boundary()` drains without calling `consider()`, so the last beat no longer starts the session that used to strand, and `loop._presence_terminal()` fires it exactly once per drive on every exit reason — finish, stopped and budget. Placed before the completion boundary and summary resolution, so counsel delivered there still reaches a forced synthesis turn's messages. The aborted path deliberately fires none. Addresses #17 (t4, t25)
- A delivery record stream separate from the degradation ledger: `ThreadedMuseRunner.drain_terminal()` records the terminal beat's delivered count and insight ids — zero included, so "delivered nothing" and "never ran" stay distinguishable. `DELIVERY_POINTS` answers "did the last beat arrive?" while `RUNNER_CODES` keeps answering "what went wrong?"; recording a healthy delivery as a degradation would make every successful run cry wolf (t5)
- `examples/proof.py` wires `append_guidance`, buffering counsel and flushing it at the top of the next completion rather than mid-turn, and reports guidance arrival separately from handover so undelivered counsel stays visible (t25)
- The muse's tool bench reaches a live drive: `ThreadedMuseRunner(complete, tools=bench, depth=0)` hands both straight to its `MuseLoop`, so the t10 tool seam, the t12 pad and the t13 workspace are reachable from a running drive instead of only from a harness that built a `MuseLoop` by hand. Found by t16, which tried to measure in-drive tool behaviour and found none to measure (#30, t26). Three things the wire deliberately does not do: it adds **no second depth gate** — `muse._bench_for` still decides, the runner compares `depth` to nothing and mints no code, so a bench below the top level is withheld and `muse-tools-withheld` reaches a host's ledger the way every other session-level code does; it **owns nothing** — no attribute here holds a bench, so `close()` cannot tear down host state whose lifetime belongs to whoever wired it, and could not do so safely anyway, since the teardown join is bounded by design and a parked session may still be using it; and it **inspects nothing** — no schema, tool name or result is read here. With no bench wired the constructed loop and every prompt it sends are unchanged, held by a differential test against a `MuseLoop` built with the pre-t26 argument list. `counts["tool_rounds"]` is the new measurement, so "the muse used its tools in this drive" is a number a host can read (t26)
- A pre-registration for the devague-legs experiment (#20), committed before any dial: the leg split across both minds, a mandatory same-mind control arm, dependent variables read off `devague plan converge/waves` rather than a grader we wrote, literal thresholds asserted by value, an explicit INCONCLUSIVE condition, devague 0.22.0 pinned as the graded instrument, and the new `devague lapse` protocol wired into the run rules (t6)
- A baseline snapshot of the three numbers this cycle moves — the close-time late-drop rate, the confidently-wrong rate and the dead-code count — each cited to its measurement doc with its n and caveats carried forward (t1)
- `embodiment/workspace.py` — the muse's second thinking tool: `MuseWorkspace` offers exactly one verb, `workspace_run(command)`, executing an argv in a bounded, disposable headspace workspace and folding the compact result back as text. The reach is the point and is held by construction, not by intent: no `policy` is ever built or passed (so headspace's closed default — network disabled, no host paths, 512 MiB / 1 CPU / 128 pids / 300 s — is the only posture reachable from here), the two host-path verbs `put`/`export` are never called, and the schema's single parameter is an argv. Under `EMBODIMENT_LIVE_RIG=1` a real workspace is opened and the reach is *attempted*: only `lo` exists inside it, and connects to the docker-bridge and default gateways on 8001/7687/27017 fail with `[Errno 101] Network is unreachable`. Missing Docker degrades with an install hint and a recorded transition, never a raise (t13)
- **`headspace-cli>=0.11` is a base dependency** — the fourth, and the operator decision of 2026-07-29 exercising d2's human gate. It lands in the same commit as the first `headspace` import, never ahead of it (honesty condition h2): `pyproject.toml`, `_APPROVED_DEPENDENCIES`, `_REQUIRED_RUNTIME_IMPORTS` and this entry moved together, and the gate is red if any piece is dropped. Transitive cost, measured: `docker>=7.1` -> `requests`, `urllib3`, `certifi`, `charset-normalizer`, `idna`. Import cost, also measured: `import headspace.api` pulls **stdlib only**, so `headspace` is the single new entry in the runtime-import set and `docker`/`requests` stay install-only like `neo4j`/`pymongo`/`paho` — `test_install_footprint_is_wider_than_import_footprint` now names them. `import embodiment` still costs nothing. Only `headspace.api` is imported: it is the sole supported surface, declaring create/run/put/export/destroy under headspace's own semver, and `headspace.core` is private (headspace-cli#18, answered by 0.11.0) (t13)
- Workspace lifecycle, bounded: `MuseWorkspace.close()` — and `destroy()`, and the `with` block — tears the workspace down on a daemon thread joined under a bound, mirroring `muse_runner._bounded_join`'s discipline rather than importing it. The whole default close budget for a host wiring both lanes is stated rather than implied: `DEFAULT_JOIN_TIMEOUT` (1.0s) plus `DEFAULT_DESTROY_TIMEOUT` (2.0s) = **3.0s**, paid once at drive end and never on the actor's hot path (`d1`). The teardown bound is deliberately the larger of the two — the join covers a local thread hand-off, this covers a round trip to a container engine sharing a box with a 27B cortex (t15)
- `ThreadedMuseRunner(complete, closers=(...))` — host-owned teardowns tied to the muse lane's close, run once, after the bounded join and after the late-drop accounting. Each entry is an opaque zero-argument callable: the runner never learns what it closed and imports nothing to support it, so **ownership stays with whoever constructed the thing and only the *timing* is delegated**. The host owns the destroy call because the host is the only party that constructs a workspace and wires its bench; the runner has no tool surface at all. The default `()` leaves every existing host byte-identical (t15)
- Four degradation codes, each with a production emit site and a provoker driving the public seam: `workspace-destroy-timeout` (teardown outran its bound — distinct from a refusal, which is an answer rather than the absence of one), `workspace-left-live` (the lane closed and a workspace survived; the record carries the id, the provider and the two commands that reap it, remedy written before explanation so a truncation cannot eat the actionable half), `workspace-lane-closed` (a tool call reached a closed lane) and `muse-closer-failed` (a host-wired teardown raised at close) (t15)
- A closed lane provisions nothing. The muse's thread is joined with a *bound*, so a thinking session can outlive the close that tore its workspace down; without the latch every such session would mint a second container after the drive ended — a leak created by the teardown meant to prevent one. The same latch resolves the `create`-in-flight race: a workspace that arrives into a closed lane is torn down again instead of being abandoned (t15)
- The workspace lane's codes are deliberately **not** folded into `embodiment.ledger` — the deferred decision t13 left open, now made. The ledger gathers the lanes embodiment drives; a workspace is constructed and closed by the host, so it is read off the object the host owns, as `MusePadCounts` and `BundleDegradation` already are (t15)

### Changed

- The `PROVOKERS` exhaustiveness guard no longer accepts a provoker that reaches a private attribute of the object it constructs. An audit found five such shortcuts, not the two #18 named. The guard's own gap is now reproduced as a test: with the old self-recording provoker reinstated, `TestEveryCodeIsCovered` stays green and only the new structural check fails (t3)
- `TestUnproducedCodes` became `TestEveryRunnerCodeHasAProducer`: an AST walk over *call arguments* to `_record`/`_degrade`, generalised from the two known offenders to every code in `RUNNER_CODES`, so a declaration, an `__all__` entry or the vocabulary tuple can never again be mistaken for a producer (t2)
- Three tests in `TestWorkClassPriority` that asserted only vocabulary membership while their docstrings promised a recorded drop now drive real paths and read the record back, including the step index that locates the loss in the run (t3)
- `devague` is approved as a base dependency but deliberately not imported — the experiment harness subprocesses its CLI so the externally-authored grader stays external and every run can pin the version it was graded by. Recorded in `pyproject.toml` and here so it is not quietly reversed (t7)

### Fixed

- Three committed docs asserted the two runner codes had no emitter, which stopped being true when t2 wired them. Each carries a superseded note rather than a rewrite; `muse-cycle-baseline.md` records that its own prediction — that a structural test would force the correction rather than let it drift — is what happened
- A latent false-STALE in `tests/announcement_checklist.py`: a caveat probe asked whether `to_dict` appeared anywhere in `snapshot()`, so an unrelated new key would have retired a caveat that still stands. Scoped to the value it is actually about (t5)

### Known limitations

- **A drive that ends mid-command leaves a workspace behind, and this release reports that rather than fixing it.** Measured on headspace 0.11.0: `destroy` refuses a workspace whose job is still running (`illegal lifecycle transition: 'running' -> 'destroyed'`), and the `stop` that clears it is deliberately outside `headspace.api`'s supported surface (headspace-cli#18) because it must run in a different process from the `run` it interrupts. Since the actor never waits on the muse (`d1`), a drive ending mid-command is the *ordinary* case, not an edge one. Two alternatives were weighed and rejected: waiting for the job (headspace's default wall clock is 300s, so a bounded wait mostly expires — and one long enough to succeed would stall every drive end) and subprocessing the CLI's `stop` (mixing an import and a subprocess for one lifecycle, having taken the dependency under `d2` specifically to stop subprocessing). What ships instead is a `workspace-left-live` record naming the id and the exact commands that reap it, and an `EMBODIMENT_LIVE_RIG`-gated test that *runs that recorded remedy* against a real leaked container and asserts it works — a remedy nobody has executed is a guess. Running it is what caught two things a help page does not tell you: `headspace stop --apply` **exits 5 on success** (so the first draft's `stop && destroy` stopped dead at the successful first command and never reaped anything), and `stop` returns once the job is *signalled* rather than ended, so the following `destroy` can still be refused and needs repeating. Both are now in the record the operator reads. The relaxation is asked for in headspace-cli#22; this is built for today's surface (t15)
- **Muse tools stay opt-in — the validation pass that could have flipped the default returned `INCONCLUSIVE`, not a green light.** Task `t18`'s pre-registered three-arm series (`docs/live-test-results/muse-arms.md`, contract in `muse-arms-preregistration.md`; n=8 per arm, real Docker, 0 transport failures, 0 retries) compared tools-off (arm A: 8 correct, 0 wrong, 0 no-answer) against +pad (arm B: 2 correct, 1 wrong, 5 no-answer) and +pad+workspace (arm C: 4 correct, 0 wrong, 4 no-answer). The verdict is `INCONCLUSIVE` because the decision rule's first gate fires: arm A's confidently-wrong rate is 0 of 8 — all eight reach 76 by an explicit two-parity recurrence cross-checked against 144 — so there is no headroom for a tool lane to improve. The 5-of-6 confidently-wrong figure that motivated the experiment was measured on a different setup entirely (the acting loop, different problems) and is not this series' control. Alongside the inconclusive headline, the series measured harm the operator's standing rule (decision `q6`: the measured failure mode never ships as default behaviour) will not let ship: 9 `NO_ANSWER` outcomes across the 16 tool-arm runs against 0 for tools-off, and 74% of arm C's workspace calls refused because the muse sent `command` as a string rather than an array (17 of 23, issue #33) — both traced to a single mechanism (issue #32): asked to stop and commit to an answer on a request carrying no `tools`, the muse emits a pad tool call in its native token syntax instead of prose, which the gateway's tool parser silently discards. Arm C's answers were never *wrong* (4 correct, 0 wrong, 4 no-answer) and arm B's pad protocol adherence was clean (0 open intents in 8 of 8 runs, 22 intends paired with 22 observes) — so this is not a finding that tools are harmful, only that the case for a default flip was not made. Tools-off remains the default it always was, not a rollback: `embodiment.muse_pad.MusePad` and `embodiment.workspace.MuseWorkspace` are tools a host wires onto `muse.MuseToolBench` explicitly, and revisiting the default needs #32 and #33 fixed first (t18, t20)

## [0.8.0] - 2026-07-29

### Added

- The muse ships as reflective counsel: a five-verb charter in MUSE_AUTHORITY, counsel self-labelled by kind, and kind-aware delivery so durable counsel survives loop distance (t1, t2, t3)
- Recall bundles: runtime-side fetch over flat eidetic recall with per-record ids and source labels, its own context budget, and truncation recorded as a degradation (t4, t5)
- Compiled memory reaches the cortex as durable counsel, and a drive it informed carries the compiled-from record ids as links (t6)
- Subagents as an injected seam: SubagentFn, a strictly-decrementing tree-wide spawn allowance with no upward override, parent-drawn turn budgets, and child degradations attributed in the parent ledger (t9, t10, t11)
- The scratchpad is promoted into the package with a combined pad+ledger resume report a successor resumes from (t8)
- examples/league_seat.py — an embodiment team plays league-of-agents through the arena's public CLI only, in both residency arms, with pad+eidetic continuity across matches (t14)
- examples/echo_probe.py and examples/arena_series.py — the memory echo-chamber probe and the pre-registered arena series runner (t7, t19)
- docs/live-test-results/corrections.md — every belief this lane held that the work contradicted, recorded as first-class (t20)
- docs/deliveries/ — the plan-vs-actual delivery record with per-audience artifact checklist (t20)

### Changed

- Perception meets a real model: the gateway 12B is wired into perceive(interpret=...) behind an opt-in flag, with the verbatim invariant and never-raise asserted live (t13)
- docs/relationships.md gains the function map with its design-metaphor caveat. The map was NOT promoted into README or CLAUDE.md: the pre-registered experiment returned INCONCLUSIVE (all four cells tied, interaction 0.00), and the promotion gate was honoured (t15, t18)

### Fixed

- embodiment#15: perception reads fenced JSON, and degrades rather than claiming health on an interpretation it cannot read (t21)
- A newline inside a stored record stripped its source label, placing unlabelled store-sourced text into the model's context (t7)
- `examples/league_seat.py` reported the command arm's degradations as zero: `_play_command` returned nothing, so ten muse-insight-late entries published as [] — a C3 violation inside the harness built to measure C3 (t19)
- `examples/league_seat.py`'s `read_objective` could not see the mind's own paraphrase, which made the t19 continuity prediction grade FAIL on six matches whose stores held the objective (t19)

## [0.7.1] - 2026-07-25

### Added

- Spec + build plan for the function-first loops + muse redesign (frame function-first-loops-muse-redesign): reflective/associative muse with five-verb charter and counsel-kind delivery, muse-compiled memory over a fetch-only eidetic bundle (eidetic-cli#37 proposed), scratchpad promotion, attenuating subagent seam, committed challenge harnesses, and a league-of-agents seat host with two residency arms

## [0.7.0] - 2026-07-25

### Added

- The bounded tool loop, extracted from colleague (4463 lines -> 1615) and driven only by an injected `complete` callable and an injected tool-executor protocol. Termination is proved structurally: AST tests assert every return in `_work_loop` is one of three exit constants and that it raises nothing of its own.
- The presence pump, redesigned around cortex + muse. No TTY, no thread, no clock; all IO rides injected `PresenceIO` callbacks and cadence is step/phase-based.
- `muse.py` and `muse_runner.py` — the muse as a bounded, tools-off thinking loop on one daemon thread with a `threading.Event` stop signal and a bounded join (deviation d1). Advisory only: nothing muse-sourced can reach a tool decision, proved by an adversarial sentinel test.
- `perception.py` — verbatim intake. `ContextPacket.original` comes from the caller's input and never from model output, enforced structurally by an allowlist rather than by convention.
- `continuity.py` and `lifecycle.py` — eidetic/coherence composed behind three named checkpoints (before-action, before-completion, before-memory), with provenance threaded from perception to durable record.
- `framing.py` — Gwen role-framed prompt composition per colleague#352. Absent identity yields byte-identical prompts, proved by goldens against the real seams plus an AST guard.
- `ledger.py` — one host-visible degradation stream folding six record shapes across seven lanes, with an enumeration test that is exhaustive by construction.
- `events.py` — optional event emission through events-cli, satisfying the loop's existing `ObserverFn` seam.
- A curated, lazily-resolved public API (PEP 562): `import embodiment` loads no submodule and costs 9.1ms, down from 19.8ms.
- `examples/` — a greenhouse demo proving continuity across two real processes, a live self-test (two instances conversing; recognising own memories), long-running proof tasks, thinking telemetry, and a reset-survivable scratchpad.

### Changed

- BREAKING: `[project].dependencies` is no longer empty. eidetic-cli, coherence-cli and events-cli are now base dependencies imported in-process (deviation d2), which transitively installs neo4j, pymongo, numpy, httpx and paho-mqtt. What survives of the old zero-dependency rule is the discipline: `tests/test_zero_deps.py` is now a human gate that pins the approved set and fails on any change in either direction.
- BREAKING for colleague specifically: importing embodiment introduces third-party top-level modules, so colleague cannot adopt it until it relaxes its own zero-deps assertions. Tracked as the C1b decision in colleague#358.
- `run()` returns a `LoopOutcome` (result, exit reason, hook firings, degradations) rather than a bare `TaskResult`, because those ledgers are the loop's record of its own conduct.
- `run()` accepts `continued_from`, making the `supersedes` provenance edge reachable through the public API — the before-memory boundary fires inside `run()`, so setting it on the returned result is always too late.

### Fixed

- An aborted drive reported `exit_reason="budget"`. `outcome` defaulted to EXIT_BUDGET before the try, so a seam raising three steps into a twenty-step drive claimed to have exhausted a budget it had barely touched. Now EXIT_ABORTED, which is not a loop exit and cannot become one. Found by an independent review.
- The continuity store anchor checked only `data_dir is None`. An empty string passed, and an empty EIDETIC_DATA_DIR reads as unset — dropping eidetic back to the git-toplevel probe, the exact leak the anchor exists to prevent. Found by the same review.
- The forced synthesis turn could exceed `max_steps`, inherited from colleague where `synthesis_reserve` defaults to 0. It is now reserved out of the budget. Reported upstream as colleague#357.
- The markdownlint CI job had been red since dependencies landed, because `.venv` was never in the ignore list and numpy ships LICENSE.md files that fail six rules.
- Vendored remember/recall skills were a mid-flight snapshot of eidetic-cli#28 whose prose still claimed a private default while the code injected public — including in the SKILL.md files loaded into an agent's context.

## [Unreleased]

### Changed

- **BREAKING (install footprint) — deviation d2: `eidetic-cli` and
  `coherence-cli` are now base dependencies, imported directly at module
  scope.** The t13 subprocess adapter in `embodiment/continuity.py` is gone;
  the seam calls the sibling libraries as ordinary Python. `events-cli` is
  declared alongside them for a sibling task (no events module ships yet).
  `pip install embodiment` therefore now pulls **neo4j + pymongo** (via
  `eidetic-cli` → `data-refinery-cli[store]`), **numpy + httpx** (via
  `coherence-cli`) and **paho-mqtt** (via `events-cli`). This deliberately
  reverses constraint C1 (pure-stdlib core); the deviation was recorded and
  approved with the cost stated up front.
  - **Consequence for colleague, stated rather than discovered later:**
    importing embodiment now transitively imports third-party modules, so
    colleague's own `tests/test_zero_deps.py` — which asserts its dependencies
    are *exactly* `["agentfront>=…"]` and that importing colleague adds no
    third-party top-level import — **will fail if colleague adds embodiment**.
    C1b (whether colleague relaxes its one-base-dependency rule) is now a hard
    prerequisite for the seam proposal, not an open question.
  - `import embodiment` on its own still costs nothing: the package root stays
    lazy (PEP 562), so only a host that actually reaches
    `embodiment.continuity` pays. Pinned by a test.
- **Both silent-corruption traps re-solved for in-process, not inherited
  blindly.** *Trap #1* — eidetic resolves an unpinned public write against
  `os.getcwd()`, which in-process is the **host's** cwd, so a host started
  inside a git checkout would commit its memories into that repo. With no
  subprocess `cwd` left to pin, `data_dir` becomes the **sole, mandatory**
  anchor, applied through a `_pinned_store` context manager that sets
  eidetic's `EIDETIC_DATA_DIR` override (which short-circuits the git-toplevel
  probe entirely) and restores the host's environment in a `finally` —
  including `DR_DATA_DIR`, which eidetic itself writes and never restores, a
  leak that only becomes visible once the call is in-process. Supplying no
  anchor still degrades *before* any work. *Trap #2* — `coherence.assess` now
  reports partial availability by **returning normally** with a non-empty
  `unavailable` map rather than by exiting 0 with one; `assess()` reads the
  returned structure and records a degradation regardless, so success is never
  inferred from "it didn't raise".
- **`tests/test_zero_deps.py` is now a human gate, not a zero-deps assertion.**
  It pins the approved dependency set *and* the exact set of third-party
  top-level modules an import introduces, and fails on any delta **in either
  direction** — a removal is as reviewable as an addition. Its failure message
  names what changed, what it costs, and that updating the pin *is* the
  approval. Module discovery stays dynamic, so future modules are covered
  automatically.

### Removed

- **`continuity.repo_path`, `Degradation.exit_code`, and the call timeout.**
  All three were subprocess artefacts with no honest in-process meaning:
  `repo_path` set a child `cwd`; `exit_code` reported a process result;
  `timeout` bounded a hung child. `Degradation.exception` (the exception class
  name) replaces `exit_code`. There is deliberately **no** replacement timeout
  — an in-process call cannot be bounded without a watchdog thread, and the
  module will not pretend otherwise; a host needing a hard bound imposes it at
  its own boundary.
- **The `CODE_*` tokens the subprocess model implied.** `cli-not-found` →
  `import-failed`; `nonzero-exit`/`launch-error` → `subsystem-error`;
  `malformed-json` → `malformed-result`; `timeout` removed. Added
  `artifact-unreadable` (coherence's engine lets file I/O errors propagate —
  in-process embodiment is the boundary that converts them) and
  `reinforce-failed` (a recall whose passive write-back failed still returns
  its records).

### Added

- **`embodiment/perception.py` — the verbatim-invariant perception intake
  (task t8).** ``perceive(original, interpret=...)`` is the ONE entry point:
  it builds every returned ``ContextPacket`` from a single
  ``ContextPacket(original=text, **fields)`` call, where ``fields`` comes from
  an allowlist (``_extract_fields``) that reads exactly the five
  non-``original`` packet fields and never a ``data.get("original")`` —
  structural enforcement of colleague ``senses.py``'s core invariant
  (``ContextPacket.original`` is set from the caller's input verbatim, never
  from model output), proven with hostile-completion tests (a spoofed
  ``"original"`` key, a prompt-injection payload, a near-miss paraphrase,
  empty output, non-JSON garbage, multiline unicode, a JSON list instead of an
  object) asserting byte-identity, not equivalence. Never raises: the four
  fault classes named in the build brief (C3) — a dead port, a request error,
  an overflow, and lossy/malformed JSON — all fold through one blanket
  ``except Exception`` into a degraded ``(ContextPacket, SensesRecord)``
  return, never an exception, so the caller's verbatim text is never lost.
  With no ``interpret`` seam configured, ``perceive`` still returns a clean,
  non-degraded packet at zero model calls — the same "museless is the primary
  path, not a fault" stance ``presence_engine.py`` (t7) already takes.
- **`embodiment/loop.py` — the bounded tool loop, extracted (task t4).** The
  pump itself: handed a `complete` callable that performs *one* model turn, it
  drives that until the model finishes, stops asking for tools, or exhausts
  `max_steps`. Extracted from colleague `1.52.1`'s 4463-line `loop.py`, whose
  seams were already injection-shaped, so this is decoupling work rather than
  redesign. Public API: `run()` → `LoopOutcome`, plus the `ToolExecutor` /
  `PresenceSink` protocols, the `HookFn` / `ObserverFn` / `ProgressFn` /
  `ContinuityFn` / `OperatorInboxFn` seams, and `LoopControls`.
- **Termination is an honesty condition, and now provable.** `_work_loop` has
  exactly three exits — `EXIT_FINISHED`, `EXIT_STOPPED`, `EXIT_BUDGET` — proved
  both behaviourally and *structurally*: `tests/test_loop.py` parses the module
  and asserts the function returns nothing but those three constants and
  contains no `raise` of its own, so the only other way out is the injected
  seam's own exception (preserved as a partial and re-raised as `LoopAborted`).
  colleague's two extra exits are gone with the features that justified them:
  the pilot stop left with flight control, and the unknown-tool streak guard's
  `tool_protocol` exit left because a broken tool channel is now one
  self-correcting step bounded by the ordinary budget.
- **Nothing extends the budget.** Not a hook, not a finish nudge, and — unlike
  upstream — not the forced synthesis turn, which is now *reserved out of*
  `max_steps` rather than added to it, so the number of `complete` calls a drive
  makes never exceeds the budget it was given.
- **The four-event hook lifecycle** (`task_start` / `pre_tool` / `post_tool` /
  `finish`) with only `pre_tool` control-bearing (`deny` skips execution and
  feeds the reason back; `rewrite` swaps the arguments; the first decisive
  verdict wins and short-circuits the chain). Hook *discovery* is injected as a
  single `HookFn`, which is what keeps process spawning, config-directory
  resolution and command quoting out of the package entirely — nothing in
  `embodiment` may assume a shell (C6), because not every host that wants a
  presence has one. A hook that raises fails closed to a `deny` firing plus a
  recorded degradation; it can never abort the work item.
- **An optional `PresenceSink` binding** conforming to the protocol t7 defined:
  `acknowledge` once before the first step, `on_progress_boundary` once per
  step, and any operator message routed through `on_operator_message`. The
  protocol is deliberately re-declared rather than imported, so presence
  consumes the loop and never the reverse; with no sink — or an inactive one —
  the drive is byte-identical to a loop with no presence at all.
- **C3 throughout:** every `contextlib.suppress(Exception)` upstream became an
  explicit handler recording a `LoopDegradation` on a host-visible ledger — the
  context-overflow shrink-and-retry ladder and its exhaustion, the
  media-rejection flatten, an unreadable attachment, and every raising hook,
  progress sink, observer, presence sink or continuity seam. A raising observer
  is recorded once and then disabled rather than re-provoked. `hook_firings` and
  `degradations` ride `LoopOutcome`, not `TaskResult`: they are the loop's record
  of its own conduct, which is why task t1's contract carve deliberately left
  `HookFiring` behind.
- **Clean injection points for task t14** — `ContinuityFn` and the three
  `Boundary` points issue #2 names (`before-action`, `before-completion`,
  `before-memory`). The loop owns *when*; it owns no checkpoint policy, ignores
  whatever the seam returns (coherence asks whether an action makes sense; the
  capability layer decides whether it is permitted), and degrades rather than
  letting a downed memory subsystem take the work item with it.
- **`embodiment/presence_engine.py` — the presence pump, redesigned around
  cortex + muse (task t7).** This is a **redesign, not an extraction**, and the
  spec (claim c43) names it as such. colleague `1.52.1`'s `presence_engine.py`
  hands every boundary to a `SensesLoopDriver` — the senses *coordination* loop,
  a second agentic loop answering each boundary with a tools-off JSON "move".
  embodiment ships exactly ONE loop (decision c30: the bounded tool loop;
  `senses_loop.py` stays in colleague), so the driver seam is rebuilt around the
  pair embodiment actually ships: the **cortex** loop, reached only through the
  acting callbacks on `PresenceExecutor` and calling back into the pump at each
  progress boundary (`PresenceSink`), and an **optional muse** that comments on a
  boundary and *proposes, never decides* — its narration is rendered, its
  guidance rides the same advisory `append_guidance` channel an operator relay
  does, and no muse-sourced value can reach a tool-call decision because the
  acting surface has no `deny`/`rewrite` field to bind (c41, held by the
  mechanism rather than the prose).
- The **museless run is the primary path**, not a degraded exception (c42): with
  no muse configured the pump still acknowledges (from the intake packet's own
  `ContextPacket.ack`) and still narrates progress (from the loop's own reported
  state), spending zero model calls. Three lanes, one ladder — `muse` →
  `cortex-only` → `off` — where only a *transition* into `cortex-only` is a
  degradation; starting there is normal. A muse that fails records the failed
  invocation, the transition, its reason and a rendered notice, then unbinds so a
  dead endpoint is not re-dialled every step (C3: nothing degrades silently).
- `PresenceIO` carries the same **eight plain callables, all defaulted to no-ops**
  (c21), with `narrate` the ONE whose exceptions are swallowed so a voice hook
  can never disturb the text path; every other callback's failure stays visible
  to the host. Recorded lines carry the **contributing role** (`packet` /
  `operator` / `muse` / `cortex`), so a single-model run never looks like two
  minds.

### Fixed

- **Fault-injection hardening across every public presence entry point**
  (task t8). ``PresenceEngine.acknowledge`` / ``on_operator_message`` /
  ``on_progress_boundary`` are now proven, per the four C3 fault classes (dead
  port, request error, overflow, lossy JSON), to degrade visibly and never
  raise — reusing t7's existing ``_degrade_muse`` mechanism (the
  ``muse:<boundary>`` / ``muse:degraded-off`` record pair, the rendered
  notice, the permanent transition to cortex-only) rather than a second one,
  so a later degradation-ledger task has one consistent shape to build over.
- **The clock the "no TTY, no thread, no clock" contract denied.** Upstream's
  `presence_engine.py` imports `time` and stamps `time.time()` onto its
  capped-update record, contradicting its own docstring. embodiment resolves it
  by injection: an optional `clock` callable is the only source of a timestamp,
  and its default (`None`) **omits the `at` key entirely** rather than fabricating
  a zero — a fabricated timestamp is exactly the silent dishonesty C3 forbids. An
  AST test pins that `time` / `threading` / `datetime` / `subprocess` are never
  imported by the engine, alongside the ported no-front-import graph guard and a
  test that the engine exposes **no presence event stream** (non-goal c33 —
  presence stays loop-internal; `snapshot()` is a pull-only artifact fold).

## [0.6.2] - 2026-07-24

### Added

- **Known doc drift** section in `CLAUDE.md` recording that `docs/skill-sources.md` carries 16 provenance rows while `.claude/skills/` holds 18 — the eidetic-origin `remember` / `recall` skills have no ledger entry, to be added (origin: `agentculture/eidetic-cli`) on the next skills PR.

### Changed

- **`CLAUDE.md` re-initialized** from the self-init seed into a full runtime prompt (the `/init` deliverable). It records what is actually on disk today — the renamed `culture-agent-template` scaffold, with the loop and presence pump still in `colleague 1.52.1` awaiting extraction — so a future session does not go hunting for code that does not exist yet. Adds: the layer map (agentfront / shell-cli / **embodiment** / eidetic + coherence / colleague); the two identities this repo holds (its own mesh identity, where `culture.yaml`'s `backend: colleague` is authoritative over the seed's `claude` claim per issue #1 §6.2, and **Gwen**, the shipped embodiment); the extraction source table with per-module guarantees from `colleague/loop.py`, `presence_engine.py`, `presence.py`, `senses_loop.py`, `senses.py` and `realtime.py`; the four hard constraints (pure-stdlib core against colleague's `test_zero_deps.py` guard, the open third-base-dependency question, the software-presence-not-a-body name boundary, observable degradation) plus the verbatim and never-raise invariants; both open issues' agendas including issue #1's five parked questions; the CLI contracts (`CliError` and the exit-code policy, the stdout/stderr split, how to register a verb, why `culture.yaml` is line-scanned rather than parsed with PyYAML); and the conventions the template documented before `guild create` reset the seed — worktrees at `../.worktrees.embodiment/<name>/`, version-bump-every-PR, the `cicd` lane, the `ask-colleague` reflex, and memory discipline as this repo's vendored wrappers actually behave (a plain `/remember` is **public and committed** to `<repo-root>/.eidetic/memory`; `--visibility private` is what keeps a record in `$HOME`).
- **`README.md` rewritten** around the real project rather than the template it was cloned from. Adds the software-presence-not-a-robot-body boundary callout that issue #1's constraint C2 explicitly requires be stated rather than left to inference; a Status section marking scaffold stage; the layer table; a **Gwen** section naming the reference rig (Qwen 3.6 27B cortex / Gemma 4 31B muse / Gemma 4 12B senses, model ids as catalogued in `lobes/catalog.py`, roles resolved by name from the lobes `/capabilities` contract) and the four honesty rules from [colleague#352](https://github.com/agentculture/colleague/issues/352); the ears-only `/v1/realtime` lane over the lobes gateway; a roadmap describing the three colleague seams; and an open-issues table summarizing issues #1 and #2 (both currently without replies, stated as such).
- Corrected the README's vendored-skill count from 11 to **18** (the number actually under `.claude/skills/`).

### Fixed

- **Memory-visibility default documented backwards** (PR #3 review). `CLAUDE.md` claimed the vendored `/remember` / `/recall` wrappers default to `--visibility private` (`$HOME/.eidetic/memory`, uncommitted); the wrappers actually inject `--visibility public`, so a plain `/remember` lands in `<repo-root>/.eidetic/memory` — **committed** and shared with the team and mesh peers. The section now leads with that, names `--visibility private` as the opt-out, and records the underlying **upstream bug** under Known doc drift: `remember.sh` / `recall.sh` still document a private default in their header comments and `--help` text, contradicting their own flag-injection blocks (`remember.sh:148`, `recall.sh:144`). The scripts are cited verbatim (cite-don't-import), so the fix belongs upstream in `agentculture/eidetic-cli`, not in this repo.

## [0.6.1] - 2026-07-20

### Added

- **Worktree location convention** in `CLAUDE.md` — every worktree you create
  by hand (workforce fan-out lanes, scratch checkouts) lives in
  `../.worktrees.embodiment/<name>/`, one
  repo-named directory beside the checkout, replacing a shared `../worktrees/`
  folder. This workspace holds many sibling projects, so a generic shared
  folder accumulates orphaned trees from several repos at once with nothing
  indicating ownership — a stale-tree sweep can't tell a live lane from junk.
  Matches the convention already documented in sibling repo `reachy-mini-cli`.
  Adds branch-prefix guidance (scope the prefix to the work; plain `agent/*`
  collides with leftovers from earlier fan-outs and fails `git worktree add
  -b`), and notes that the vendored `assign-to-workforce` skill uses both the
  shared path *and* `agent/<task-id>` branches in its fan-out example — it is
  cited verbatim and must not be edited, so both are overridden when following
  it. Teardown guidance names `git worktree remove <path>` as the verb that
  actually deletes a worktree; `git worktree prune` only clears metadata for
  directories that are already gone. Tool-managed throwaways are explicitly
  out of scope: `ask-colleague`'s read-only verbs create a detached worktree
  under `${TMPDIR:-/tmp}` and reap it on an EXIT trap, so they never persist
  to need an owner.

## [0.6.0] - 2026-07-18

### Added

- **Four devague-origin skills re-vendored into `.claude/skills/`**
  (cite-don't-import), synced to the fixed devague source
  (devague#74/#75/#76):
  - `challenge` — a risk-scaled blind-spot discovery pass that runs between
    `/think` and `/spec-to-plan`, routing findings back through the existing
    deterministic moves as human-adjudicated proposals.
  - `scope` — the idea→scope leg that surveys the surfaces an idea touches
    before framing, seeding the Announcement Frame with provenance-backed
    boundary/non-goal/assumption claims.
  - `deviate` — stops an in-flight `assign-to-workforce` run when execution
    must diverge from the confirmed plan and records the divergence as a
    first-class, append-only deviation record.
  - `summarize-delivery` — closes the loop after an `assign-to-workforce`
    run with a planned-vs-actual accountability artifact.

  These four originate in `devague` and are re-broadcast via guildmaster; see
  `docs/skill-sources.md` for provenance.

## [0.5.0] - 2026-06-24

### Added

- **Memory-discipline "Conventions and workflow" section in `CLAUDE.md`** — a
  per-task *recall-before / remember-after* convention (scope localized to this
  repo's nick) so the vendored `remember` / `recall` skills are actually used,
  not just present: `/recall` before non-trivial work to build on prior
  decisions instead of re-deriving them, and `/remember` when a non-obvious
  decision, constraint, fix-and-why, or hard-won gotcha surfaces. The section
  documents this repo's memory as **in-repo and public** — records resolve to
  `<repo-root>/.eidetic/memory` (committed, team- and mesh-shared). Inserted
  idempotently (skipped if already present), slotted under an existing
  "Conventions and workflow" heading when one exists, else appended.

### Changed

- **Refreshed the `remember` + `recall` wrappers from eidetic-cli 0.10.0**
  (cite-don't-import) — picks up eidetic's **project-local store default**: the
  files backend now resolves per record by visibility — PUBLIC records inside a
  git repo go to `<repo-root>/.eidetic/memory` (committed, team-shared), PRIVATE
  records (or any record outside a repo) go to `$HOME/.eidetic/memory` (never
  committed), an explicit `EIDETIC_DATA_DIR` still wins, and recall reads both
  stores and merges. Also carries the 0.9.3 hardening (interactive-stdin guard,
  `help` as a search term, SIGPIPE-safe suffix parsing). **Recipe policy
  override (the wrappers here are NOT byte-verbatim):** the injected default
  visibility is flipped from eidetic's `private` to **`public`**, so a plain
  `/remember` lands the note in `./.eidetic/memory` in this repo, kept as part
  of the repo — pass `--visibility private` to route a record to `$HOME`
  instead. `remember` drives `eidetic remember` (idempotent upsert of one JSON
  record or an NDJSON batch on stdin); `recall` drives `eidetic recall` with
  four search modes (exact / approximate / keyword / hybrid). Each `SKILL.md` is
  localized only in the illustrative `--scope <nick>` examples (Provenance keeps
  "First-party to eidetic-cli"). Runtime dep: the `eidetic` CLI on PATH (else a
  local eidetic-cli checkout with `uv`) — **`eidetic >= 0.10.0`** for the
  in-repo routing; on an older CLI the public records still work but are stored
  in `$HOME/.eidetic/memory` instead of in-repo. Propagated by rollout-cli's
  `eidetic-memory` recipe.

## [0.4.0] - 2026-06-23

### Added

- **Vendored the `remember` + `recall` memory skills from eidetic-cli**
  (cite-don't-import) — the write/read halves of eidetic's shared
  `$HOME/.eidetic/memory` surface, so this agent (Claude and its colleague
  backend) can persist facts across sessions and recall them later, sharing
  one store.
  `remember` drives `eidetic remember` (idempotent upsert of one JSON record or
  an NDJSON batch on stdin, dedup by id + content hash); `recall` drives
  `eidetic recall` with four search modes — exact / approximate / keyword /
  hybrid — each hit carrying text, full provenance metadata, a relevance score,
  and a freshness signal. The `.sh` wrappers are byte-verbatim from eidetic-cli
  (their first-party origin); each `SKILL.md` is localized only in the
  illustrative `--scope <nick>` examples (Provenance keeps "First-party to
  eidetic-cli"). Both default to this agent's PRIVATE scope, reading the suffix
  from `culture.yaml`. Runtime dep: the `eidetic` CLI on PATH (else a local
  eidetic-cli checkout with `uv`). Propagated by rollout-cli's `eidetic-memory`
  recipe.

## [0.3.4] - 2026-06-20

### Fixed

- Identity docs and self-description strings still claimed `backend: claude`
  (prompt file `CLAUDE.md`), but this template was promoted to a colleague
  resident in #14/#15: `culture.yaml` declares `backend: colleague` (Qwen) with
  `AGENTS.colleague.md` as the resident prompt. Corrected the stale claim in
  `CLAUDE.md` (Identity section), `README.md`, `docs/skill-sources.md`, and the
  two CLI description strings (`overview` artifacts and `explain doctor`). The
  `doctor` backend→prompt-file mapping and the tests were already on
  `colleague`; this aligns the prose and self-description with them.

## [0.3.3] - 2026-06-20

### Fixed

- pyproject.toml: correct the `license` field and PyPI classifier from MIT to
  Apache-2.0 to match the `LICENSE` file. The README License section was already
  corrected in 0.3.2, but the package metadata was missed; the built wheel now
  reports `License-Expression: Apache-2.0`.

## [0.3.2] - 2026-06-18

### Added

- ask-colleague skill: `monitor`/`guide`/`stop` pilot verbs plus a `--watch`
  flag to dispatch, watch the live feed of, send mid-flight guidance to, and
  cooperatively stop a running colleague flight (re-vendored from colleague).

### Changed

- README: correct the License section from MIT to Apache 2.0 to match the
  `LICENSE` file.

## [0.3.1] - 2026-06-13

### Changed

- CLAUDE.md: add a convention to reach for the `ask-colleague` skill reflexively
  for explore/review/write/grade — read-only `review`/`explore` are always safe;
  side-effecting `write` needs the user's go-ahead.

## [0.3.0] - 2026-06-13

### Added

- AGENTS.colleague.md resident prompt file (backend colleague <-> AGENTS.colleague.md)

### Changed

- Promote agent identity to a colleague resident: culture.yaml backend
  claude -> colleague with a pinned model. The `doctor` backend-consistency
  map gains `colleague` -> AGENTS.colleague.md.

## [0.2.1] - 2026-06-12

### Changed

- **Re-vendored the `ask-colleague` skill from colleague (now 1.7.0, up from the
  0.39.2 sync)** — the wrapper had drifted multiple releases behind origin. Picks
  up the `clean` verb (reap stale/corrupt `colleague/*` branches + orphaned
  `.colleague/` artifacts a crashed run left behind), the `--json` flag on every
  verb (result JSON on stdout, diagnostics/digest on stderr), the
  `_colleague_via_uv` local-dev resolution that honors `--repo`, and the
  tri-state (0/1/2) exit-code contract. `scripts/ask-colleague.sh` + `prompts/`
  are byte-identical to the origin; `SKILL.md` diverges only in the one
  consumer-identifying Provenance clause (`embodiment vendors from
  guildmaster`). `docs/skill-sources.md` sync row updated to
  `2026-06-12 (colleague 1.7.0, direct)`. Refs: colleague#183, #186.

## [0.2.0] - 2026-06-06

### Added

- **`ask-colleague` skill** (`.claude/skills/ask-colleague/`) — the first-party front door to the `colleague` CLI (the renamed `convertible`). On top of `explore` / `review` / `write` it adds a `feedback` verb (grade a finished work item — the ROI loop), and `write` now **previews by default** in a throwaway worktree (no side effects) unless `--apply` / `--pr` is given. Reach for it reflexively — `review` for a diverse second opinion on a committed diff before opening a PR, `explore` for a fresh read of an unfamiliar area.

### Changed

- **Replaced the `outsource` skill with `ask-colleague`.** `outsource` was renamed to `ask-colleague` upstream ([colleague#148](https://github.com/agentculture/colleague/pull/148)). Because guildmaster has not re-broadcast the rename yet (its kit still ships the old `outsource`), `ask-colleague` is vendored **directly from the sibling `colleague` checkout** rather than from guildmaster — a tracked local divergence recorded in `docs/skill-sources.md`, parallel to the `agex` → `devex` one. Vendored verbatim except one consumer-identifying clause in the Provenance paragraph.
- **Ledger + CLAUDE.md + `.gitignore`:** point `docs/skill-sources.md` and the CLAUDE.md Skills section at `colleague` / `ask-colleague`, swap the *optional* runtime prerequisite `convertible` → `colleague` (env prefix `CONVERTIBLE_*` → `COLLEAGUE_*`, with the legacy names kept as a deprecated fallback), and gitignore the `.colleague/` run-artifact dir the skill writes (plus the stale `.agex/`).

## [0.1.4] - 2026-05-31

### Added

- **Vendor the `outsource` skill** (`.claude/skills/outsource/`) from
  guildmaster's canonical copy (origin
  [`agentculture/convertible`](https://github.com/agentculture/convertible),
  re-broadcast via guildmaster — guildmaster
  [#51](https://github.com/agentculture/guildmaster/pull/51)). Every agent
  cloned from this template now inherits the ability to hand a scoped task to a
  *different* engine/mind: `explore` (read-only investigation), `review` (a
  diverse second opinion on the committed diff), and `write` (delegate a small
  implementation). `explore`/`review` run isolated in a throwaway `git worktree`;
  `write` refuses a dirty tree. Fulfils
  [#8](https://github.com/agentculture/embodiment/issues/8).
- **Ledger + CLAUDE.md:** record `outsource` in `docs/skill-sources.md`
  (origin = convertible, re-broadcast via guildmaster; vendored verbatim — it
  already carries `type: command`) and document its *optional* runtime
  dependency on the `convertible` CLI (the skill exits with an install hint if
  absent, so a clone that never uses it is unaffected).

### Changed

### Fixed

## [0.1.3] - 2026-05-31

### Changed

- Expanded the clone-and-rename instructions in `CLAUDE.md`: added `README.md` to
  the rename targets and a portable `git grep` discovery command so a cloner can
  find every occurrence of the template name (hard-coded in ~100 places across the
  package, including the CLI command files and `_ISSUES_URL` in
  `embodiment/cli/__init__.py`) rather than renaming by hand.
- Synced `README.md`'s "Make it your own" checklist with `CLAUDE.md`: it now lists
  `README.md` itself as a rename target and points to `CLAUDE.md`'s discovery
  command as the authoritative procedure, so the two onboarding checklists no
  longer drift.

## [0.1.2] - 2026-05-30

### Changed

- Renamed the PR-lifecycle CLI references `agex` / `agex-cli` to `devex` (same
  tool, new name) across `CLAUDE.md`, `docs/skill-sources.md`, `.gitignore`, and
  the vendored `cicd`, `assign-to-workforce`, and `communicate` skills — the
  `cicd` scripts now invoke `devex pr`.
- Logged the vendored-skill in-place patch as a local divergence in
  `docs/skill-sources.md`; the matching canonical rename is tracked upstream for
  guildmaster in
  [agentculture/guildmaster#48](https://github.com/agentculture/guildmaster/issues/48)
  so a future re-sync reconciles cleanly.
- Aligned the documented `devex` version floor to `>=0.21` across the vendored
  `cicd` `SKILL.md` and `workflow.sh` install hint (were `>=0.1`), matching
  `docs/skill-sources.md` and the `await`-era feature set; flagged upstream on
  guildmaster#48.

### Fixed

- SonarCloud now reports code coverage — added `relative_files = true` to
  `[tool.coverage.run]` so `coverage.xml` emits repo-relative paths that map to
  `sonar.sources=embodiment` (absolute / `.venv` paths were dropped
  as unmappable). Mirrors the sibling `convertible` setup.

## [0.1.1] - 2026-05-26

### Changed

- **CI gates on the SonarCloud quality gate**
  ([issue #3](https://github.com/agentculture/embodiment/issues/3)) —
  added `sonar.qualitygate.wait=true` to `sonar-project.properties` so a failing
  gate fails the `test` job when `SONAR_TOKEN` is set. Token-less repos and fork
  PRs remain green (the scan step is guarded by `if: env.SONAR_TOKEN != ''`).

## [0.1.0] - 2026-05-26

### Added

- **Onboarded into the AgentCulture mesh** ([issue #1](https://github.com/agentculture/embodiment/issues/1)).
- **Agent-first CLI** cited from teken's (`afi-cli`) `python-cli` reference
  (`teken cli cite`) — verbs `whoami`, `learn`, `explain`, `overview`, `doctor`,
  and the `cli` noun group. Runtime is self-contained (`dependencies = []`);
  `teken>=0.8` is a dev dependency only. Passes the seven-bundle agent-first
  rubric (`teken cli doctor . --strict`). `doctor` checks the agent-identity
  invariants (prompt-file-present, backend-consistency, skills-present).
- **Mesh identity**: `culture.yaml` (`suffix: embodiment`,
  `backend: claude`) and the matching `CLAUDE.md` prompt file.
- **Canonical guildmaster skill kit** (11 skills) vendored under
  `.claude/skills/` (cite-don't-import): `agent-config`, `assign-to-workforce`,
  `cicd`, `communicate`, `doc-test-alignment`, `pypi-maintainer`, `run-tests`,
  `sonarclaude`, `spec-to-plan`, `think`, `version-bump`. Every `SKILL.md`
  carries `type: command` (load-bearing for the culture/claude backend);
  `cicd` / `communicate` consumer-identifying prose adapted, all script bodies
  verbatim. Provenance in `docs/skill-sources.md`. Three skills (`think`,
  `spec-to-plan`, `assign-to-workforce`) originate in `devague`, re-broadcast
  via guildmaster.
- **Build + deploy baseline**: `pyproject.toml` (hatchling), `tests/` (pytest,
  xdist, coverage), `.github/workflows/{tests,publish}.yml` (CI rubric/lint gate,
  PyPI Trusted Publishing), `.flake8`, `.markdownlint-cli2.yaml`,
  `sonar-project.properties`, and `.claude/skills.local.yaml.example`.

### Changed

### Fixed
