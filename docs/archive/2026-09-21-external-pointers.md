# External pointers to the archived package — drafts

**Nothing in this file has been posted.** These are drafts for the operator.
Plan task `t2` of the realtime redesign requires that everything pointing at the
old package is accounted for, and that nothing reaches a sibling repo — or
closes an issue — without an explicit go-ahead.

The archive point is the tag `archive/pre-realtime-0.14.0`.

## 1. colleague#358 — draft comment

[colleague#358](https://github.com/agentculture/colleague/issues/358) is open
and proposes that colleague import embodiment's extracted loop. Draft, to be
posted through the `communicate` skill (which signs it) only if the operator
says so:

> **Status change on the embodiment side — please read before acting on this.**
>
> embodiment has changed direction. The strategist, configuration-lane and muse
> experiments are archived in git history (tag `archive/pre-realtime-0.14.0`),
> and the package is being rebuilt into a background realtime voice app on the
> lobes `/v1/realtime` API. Spec and plan:
> `docs/specs/2026-09-21-realtime-embodiment-app.md` and
> `docs/plans/2026-09-21-realtime-embodiment-app.md`.
>
> What this means for the proposal here:
>
> - **The loop you were asked to consider importing still exists and is
>   unchanged.** `embodiment/loop.py` is byte-identical across the archive, with
>   its AST termination tests, and the voice turn is being built on it. So is
>   `presence_engine.py`, `presence.py`, `perception.py`, `identity.py` and
>   `framing.py`.
> - **The dependency ask (C1b) is unchanged in kind and smaller in size.**
>   `headspace-cli` (and the docker SDK) is gone. The base set is now
>   `eidetic-cli`, `coherence-cli`, `events-cli`, and the redesign will add a
>   WebSocket client and an optional audio extra. Importing embodiment would
>   still break colleague's one-base-dependency and no-third-party-import
>   assertions.
> - **`framing.muse_system_message` and the `MUSE_AUTHORITY` re-export are
>   gone** (they needed the archived muse module). `frame_muse` remains.
> - **embodiment is no longer primarily a library waiting for a first
>   consumer.** Its first consumer is now its own daemon. We are not asking
>   colleague to do anything; if this proposal no longer fits your roadmap,
>   closing it is a fine outcome and we will not read it as a rejection of the
>   loop.
>
> The decision remains yours.

## 2. Open issues — proposed disposition

65 issues were open on 2026-09-21. Proposed: **keep 11**,
**look at 4**, **close 50 as moot**.

### Keep open — they bear on kept code or on the redesign

| Issue | Title | Why |
|-------|-------|-----|
| #1 | Build brief: the agentic loop that gives an app an embodied AI presence | the founding brief; the redesign answers its parked questions differently, so it needs an update, not a close |
| #2 | Give Embodiment continuity through Eidetic and Coherence | continuity through eidetic and coherence is still the design; `continuity.py` is kept |
| #4 | Emit loop and presence events through events-cli (resolves #1's parked question 5) | `events.py` is kept and MQTT is now the daemon's internal event substrate |
| #5 | A host-providable Python execution seam, so a drive can actually compute | a host-providable execution seam is what Gwen's later tools need |
| #6 | Subagents: decomposition as an injected seam, without a fourth exit path | `subagent.py` is kept, and is the natural seam for triggering agents |
| #37 | ModelResponse carries no finish_reason, so a truncated turn and a stopped turn are indistinguishable — a C3 violation | `ModelResponse` has no `finish_reason`; in a voice turn a truncated completion is silence, so this is now more urgent (plan task t9) |
| #40 | Coherence is wired but unused by this cycle's harnesses — and 'change the ceiling' is a gate that doesn't exist yet: a four-rung ladder that measures the signal before building on it | coherence stays as a dependency for measurements and is still unused |
| #47 | Configured cortex model id 404s: culture.yaml names one model, the gateway serves another | `culture.yaml` still names a model the gateway does not serve |
| #63 | Explore: what the senses seat should be — one clause, not the model, decides whether it invents readings (0/16 vs 16/16) | the senses seat is now the speaker; the grounding clause result is kept evidence |
| #64 | Re-test senses after the move to gemma-4-12B-it-qat-w4a16 on Jetson AGX Orin — baseline committed, instrument committed | the re-test is against a model that is no longer deployed here, but the instrument is kept |
| #72 | question: asked point-blank, senses asserts 'I am one mind. You are talking to me alone.' — is that the intended answer? | `senses` asserting 'I am one mind' bears directly on a Gwen who speaks through `senses` |

### Needs a human look before deciding

| Issue | Title | Why |
|-------|-------|-----|
| #11 | Frame embodiment by cognitive function, not by where it sits in the stack | the function map lived in `docs/relationships.md`, which the archive removed |
| #27 | Two observations from an independent review: bounded-join teardown latency and _read's blanket exception swallow | bounded-join teardown and a blanket exception swallow: check whether the code it names survived |
| #60 | SonarCloud's analysis surface is narrower than the code surface: examples/ is unanalysed and no branch is ever scanned | `examples/` is gone, so half of this is moot; the no-branch-scan half may not be |
| #74 | Flaky: a class-scoped fixture re-runs per xdist worker, so the suite used as a merge gate can fail on a match its siblings never saw | a flaky class-scoped fixture: check whether the test it names survived |

### Close as moot — the code or experiment they describe is archived

Draft closing note, identical on each:

> Closing as moot under the realtime redesign: the code or experiment this issue
> describes was archived in git history and is no longer in the tree. The last
> commit containing it is the tag `archive/pre-realtime-0.14.0`; the finding
> itself stays on the record here and in the archived `docs/live-test-results/`.
> If it turns out to bear on the new daemon, reopen it with a pointer to the
> plan task it affects (`docs/plans/2026-09-21-realtime-embodiment-app.md`).

| Issue | Title |
|-------|-------|
| #7 | Play league-of-agents: an adversarial, long-horizon, non-colleague consumer |
| #8 | DEFAULT_STALE_LAG=5 discards 71% of muse insights — re-derive it from measured lag |
| #9 | Scratchpad, planner and ledger: a mind's record of itself, survivable across a reset |
| #12 | Reframe muse: from creative lobe to reflective/associative cognition |
| #17 | The last counsel of every drive is structurally undeliverable — 25% of counsel, one per run, invisible to kind-aware delivery |
| #20 | Run the devague legs across both minds: a muse experiment with a grader we did not write |
| #23 | d3: the #17 terminal drain fires only on forced-synthesis exits, so clean-finish drives get none |
| #24 | d1: a live-evidence gate (rig tests, challenge harnesses, league/arena benchmark) now precedes the delivery summary |
| #25 | d2: headspace is imported via headspace.api (public, semver), never headspace.core — resolved by headspace-cli 0.11.0 |
| #26 | MuseLoop.think can raise on hostile controls, contradicting the never-raise guarantee its callers cite |
| #28 | The devague-legs deviate pools are separable by input length alone — 11/12 is not evidence of ability |
| #29 | Counsel delivered at the terminal drain reaches no cortex turn on a clean finish — 5 of 13 lines, larger than the loss #17 fixed |
| #30 | ThreadedMuseRunner cannot carry a tool bench, so the muse tool seam is unreachable in a live drive |
| #31 | d5: t14 (no-secrets boundary) was absorbed into t13 and shipped there |
| #32 | Handed a pad, the muse writes tool calls and no prose — 8 turns, 6 pad writes, zero content |
| #33 | The muse cannot call the workspace tool: command passed as a string, 17 of 23 calls refused |
| #34 | Experiment: full-Gemma vs mixed vs full-Qwen, round-robin head-to-head up an escalating League of Agents ladder |
| #35 | Escalate until separation: four experiments tied at a ceiling, and the ladder is the general fix |
| #36 | Experiment: Gemma coordinator + Qwen developer — testable in embodiment today, no colleague needed |
| #39 | Experiment: watch a playthrough video, explain it, and test whether it transfers to play |
| #41 | REQUEST_TIMEOUT of 300s silently censored the cortex's output distribution — two turns lost and scored as wrong answers (was: a contention claim, now corrected) |
| #42 | Derive client timeouts from the token budget, not a latency percentile — and enforce the bound in CI |
| #44 | Design: the Bee-Hive — cortex agents with worker-harnesses or code-drones, against M and H |
| #45 | Skill: `drone` — create / evoke / list named units that mix code with scoped worker intelligence |
| #46 | Cycle: error-derived timeouts + the Bee-Hive — spec, plan, and the deviation ledger |
| #48 | Redundancy for quality: spend the measured width on N answers to one question, not N distinct questions |
| #50 | A standing benchmark base set: quality, performance, intelligence and achievement — coding, league, math and algorithms, with graded metrics that do not saturate |
| #51 | Architecture: add a strategic scope governor above the acting cortex |
| #52 | Stage 3 live-test session 1 — context-clear instructions (t14) |
| #54 | ScopeRegister.active can name a directive the actor never received |
| #55 | The scope layer adds no containment in the surrender direction — state it, don't imply it |
| #56 | A refused directive's identity does not survive the relay to the host |
| #57 | Strategist staleness/cadence defaults are sized for a token budget the reference rig does not use |
| #58 | SCOPE_AUTHORITY never states the scope_id-must-be-new rule it is graded on |
| #59 | d16's 16000-token budget does not transfer: 19.4% of worker calls truncate where the cortex truncates 0% |
| #61 | embodiment.scope_events is off the curated public surface — its three lane siblings are on it |
| #62 | An unseeded issued-chain makes a protocol-obedient strategist look incapable: every review refused as superseding something never issued |
| #65 | scope: the superseded directive is re-inserted into the actor's context ahead of its replacement |
| #66 | scope: a one-off task instruction became a durable persisted constraint, and a durable objective decayed into a task |
| #67 | scope: a stale directive silently substituted its objective for the operator's request |
| #68 | scope: non-intervention fails live — 4 of 5 directives restated one ordering, 69.5% of strategist tokens bought nothing |
| #69 | scope: directives render 'Responsibilities: - :' — owner empty on 4 of 5 live directives |
| #70 | scope: degradation records are triplicated and split across two incompatible shapes (confirms #56 live) |
| #71 | scope host: transcript output fails the repo lint gate (590 errors), and scope.directive.proposed omits previous_version |
| #73 | Stage 3 live-test session 2 — context-clear instructions (successor to #52, session 1 findings folded in) |
| #75 | Design: the strategist changes configuration, not minds — gated prompt/example/verification edits instead of advisory directives |
| #77 | The timeout AST guard walks examples/ and embodiment/ — but not docs/, where a model-dialling probe now lives |
| #78 | t1 measured: the worker seat drives the bounded tool loop — 36 runs, 3 rungs, all clean and all at ceiling |
| #79 | Eight seam traps in the config lane — two of them reproduce the advisory tier's zero-directive failure by default |
| #80 | The cycle-2 harness overwrites cycle 1's committed capabilities.json |

## 3. Inside this repo — done in the archive PR

- The first-party `drone` skill left with `drone.py`; its row in
  `docs/skill-sources.md` is gone.
- CI no longer names `examples/` in black, isort, flake8 or bandit.
- The coverage gate (60%) is unchanged and the smaller package measures 97%.
- `README.md`, `CLAUDE.md` and the `explain` catalog's root entry were rewritten
  to describe only what exists. `AGENTS.colleague.md` is generic and needed no
  change.
- Removed as describing only archived work: `docs/announcement-checklist.md`,
  `docs/challenge-problems.md`, `docs/relationships.md`,
  `docs/session-contract-2026-08-01.md`, `docs/sonar-dispositions.md`.
- Kept as history: the earlier `docs/specs/`, `docs/plans/`, `docs/deliveries/`
  and `.devague/` records.

## 4. Later, and also not posted

A proposal to `agentculture/reachy-mini-cli` that its realtime client accept a
Gwen URL plus a secret, and a note in both READMEs about its `agent embody`
layer sharing the word "embodiment" with this package. That belongs to the
robot-relay stage, not to this PR.
