# strategic scope governor

> embodiment gains an opt-in strategic scope layer above the bounded actor loop: a strategist seam with typed, versioned, supersedable directives that owns objectives, priorities and responsibility allocation — structurally without actor-tool, presence or policy authority (its only tools a host-wired, role-scoped bench per c22) — while run() stays actor-only and unchanged, and no strategic claim ships without ScopeBench measurement
> instruction: ship scope.py (pure shapes + bounded ScopeLoop), `scope_runner.py` (background mechanics), `scoped_run.py` (composition over loop.run()); prove actor-only unchanged with the existing suite and an AST import test on the actor path

## Audience

- hosts that embed embodiment — colleague, the reachy family, greenhouse-style example apps — plus this repo own examples/ harnesses; the operator configures the strategist seat explicitly, and end operators keep experiencing one coherent teammate

## Before → After

- Before: today nothing owns salience: docs/relationships.md function map records "no owner" for deciding what deserves attention, so the actor treats every presented task as the right problem — locally correct work can be globally wrong and no seam exists to say so
- After: a host can opt into a strategist seat issuing typed, versioned, supersedable directives that own objectives, priorities and responsibility allocation; the actor runs under the active directive and reports material changes upward; every strategic decision, degradation, staleness and superseded result is host-observable; actor-only hosts see byte-identical behaviour

## Requirements

- the strategist seam resolves by role name from the lobes /capabilities contract like every other seam; per the updated issue #51 and the q1 seat-to-role decision the tiers align with existing lobes roles — strategy rides `cortex` (dense Qwen3.6-27B, local), operation rides `worker` (Qwen3.6-35B-A3B, proxied), interaction rides `senses` — so no new lobes role and no cross-repo proposal is needed; embodiment still never infers a seat from model names
  - honesty: no code path inspects a model name string to infer a seat: the strategist seat comes only from explicit host configuration naming a lobes role, and a grep-level test can pin the absence of model-name parsing in scope modules
- Stage 0 authority tests must cover surrender as well as seizure: the recorded echo-chamber gap (memory embodiment-muse-echo-chamber-gap, live probe 2026-07-25) is that structural guards stop an advisor deciding but nothing stops the actor deferring — with an authority-bearing strategist the new failure mode is the cortex treating directive prose as operational instruction, so tests must pin both what a directive cannot carry and how the cortex may consume it
  - honesty: a surrender-direction test exists: the actor is handed a directive whose prose embeds an operational instruction (for example a shell command inside an objective string) and the loop provably never executes it as a tool call — the mirror of the seizure-direction sentinel the muse already has
- `run_scoped()` composes the existing `embodiment.loop.run()` through seams run() already exposes — injected observer, presence, continuity, hooks and controls (loop.py:2021-2040) — so the actor loop stays byte-unchanged and actor-only remains the default supported path
  - honesty: `run_scoped`() calls loop.run() unmodified: no new run() parameter, no copied loop body — the diff to loop.py from this work is zero lines
- Stage 0 authority tests reuse the three structural techniques already proven in this repo: the AST return-constant walk for `ScopeLoop` termination (tests/`test_loop.py`:417-548), the `dataclasses.fields()` vocabulary check so no scope shape carries tool/arguments/deny/rewrite/approve names (tests/`test_muse.py`:645-662), and the `_imported_modules()` AST import ban so `embodiment.loop` and presence acting names are not even in scope inside scope.py (tests/`test_muse.py`:224-231, tests/`test_presence_engine.py`:972-1011)
  - honesty: all three techniques run in CI against the new modules: dataclasses.fields() decision-vocabulary ban over every Scope\* shape, AST import ban over scope.py, and the AST exit-returning-function walk over ScopeLoop
- `scope_runner` mirrors `ThreadedMuseRunner` mechanics exactly: non-blocking consider/drain, one in-flight with a single replaceable pending slot, bounded deque with recorded overflow drops, lag-based staleness, bounded join on close, pull-only degradations (`muse_runner.py`:800-811, 922-1004, 746-748, 415-433, 1316-1358) — with one deliberate delta: its output is authority-bearing within a typed vocabulary, so every drop or staleness record is an authority event, never silently lost counsel
  - honesty: consider() and drain() never block or raise (tested), at most one review is in flight with a single replaceable pending slot, and every displaced, dropped, stale or superseded result lands in the ledger — zero silent losses under an authority-bearing seam
- scope degradations join the existing ledger by the harvest pattern: a `SOURCE_SCOPE` lane with a `from_scope` reader — ledger.`known_codes`() auto-harvests `DEGRADED_*`/`DROPPED_*` constants from a module `__all__` (ledger.py:211-219, 689-735), so the new lane needs no ledger rewrite
  - honesty: the ledger gains `SOURCE_SCOPE` by exactly one `_MODULES` row plus a `from_scope` reader — ledger.`known_codes`() harvests the new `DEGRADED_`/`DROPPED_` constants with no other ledger.py change
- scope.\* observability rides the existing observer seam: loop.py never imports events.py — hosts wire `EventEmitter` (mapping kind to embodiment.{kind}, events.py:306-313) as the ObserverFn — so scope events are emitted the same way and embodiment still does not own the external event fabric
  - honesty: scope.py and `scope_runner.py` import no event fabric; a host wiring the existing EventEmitter as observer receives every scope.\* event carrying actual model, role, directive and snapshot ids
- every timeout constant a strategist seam introduces needs a dated rate entry in docs/live-test-results/timeout-rate-measurements.json and a Clock in tests/`test_timeout_bounds.py` CLOCKS — the AST guard fails any examples timeout constant outside the walk, and the senses role shows the honest `unmeasured_roles` path when a rate is not yet measured; the committed rate config already names the live cortex id unsloth/Qwen3.6-27B-NVFP4 (measured 2026-08-01)
  - honesty: every timeout constant the strategist seam introduces appears as a Clock in CLOCKS derived from a dated rate entry in the committed rate config, and the AST guard (with its test-of-the-test) fails on any constant outside the walk
- the threat model documents the injection chain end-to-end: hostile text arriving through the scope projector (snapshot fields sourced from repo content or conversation) can steer the strategist toward a bad directive, and the directive is the surrender path into an actor holding tool authority — README, explain output and the bench docs state it (the drone.py precedent: the threat model is stated on every surface, never left to inference), Stage 0 includes a snapshot-embedded-instruction case, and every strategist bench call (e.g. an eidetic recall query) is a recorded event
  - honesty: a Stage 0 test feeds a snapshot whose `material_outcomes` embed an operational instruction and proves it cannot reach the executor: the strategist may echo it into a directive, and the surrender tests then prove the actor never executes it — the chain is covered at both hops, and the threat-model prose appears in README and explain output
- the active directive enters the actor only through framing composition on the top-level acting loop: framing.py already carries the exact discipline (`frame_cortex` targets the top-level acting loop only, `ROLE_SUBAGENT` for typed subagents, and its charter line — who is speaking, never what they may do); directive rendering follows it, and the absent-identity byte-identical acceptance from colleague#352 extends to the scope lane
  - honesty: directives are rendered into actor context exclusively via framing composition (an AST or call-graph test pins that `scoped_run` has no other prompt-bearing path), and with no configured identity the scope lane leaves prompts byte-identical
- directive persistence is layered (operator, 2026-08-03): durable directives survive across drives AND session-scoped temporary persistence exists within the same process — a session holds its own scope state without touching the durable lane, and sessions are the future seam for per-subagent scoping
  - honesty: the two lanes are structurally distinct and observable: a session-scoped directive never outlives its session and never writes the durable lane; a durable directive survives a process restart through a host-visible persistence seam; every scope record names which lane it belongs to

## Honesty conditions

- opt-in is real: with no strategist configured, run() and every existing test pass byte-unmodified, and the actor path imports no scope module — provable by the existing suite plus an import-graph test
- directive shapes structurally exclude the decision vocabulary (fields() test) and an adversarial sentinel proves nothing strategist-sourced reaches the executor or the `pre_tool` hook registry — the same mechanism that already guards the muse
- embodiment ships no strategic storage, retrieval or scoring implementation: the scope projector is a host-supplied callable whose inputs may include continuity and coherence outputs, and scope modules import neither eidetic nor coherence beyond the existing continuity seam
- every scope.\* record carries the actual contributing model and role, and a run configured with a single model emits no record naming a strategist — the absent-identity byte-identical rule extends to the new tier
- tests/`announcement_checklist.py` caveat cli1 still passes after the work: no CLI verb imports embodiment.loop, embodiment.scope or the scoped composition
- the strategist bench is empty by default and host-wired: with no bench configured the strategist is byte-identical to a tools-off mind, and the bench type structurally cannot hold an actor ToolExecutor — plus the #32/#33 lesson stands: bench tools ship only with the protocol-shape failure modes measured, not assumed away
- the strategist thinking budget is set from measured completion-length data (the t24 method) and the streaming bounds come from the committed rate config — no strategist clock or budget constant is guessed
- this work adds no code to `arch_hive.py`, `worker_seam.py` or `worker_scoped_overhead.py` beyond keeping CI green; the bee-hive neither grows nor shrinks under this frame
- the definition of done from issue #2 holds here too: a non-colleague host (the greenhouse example or a sibling) demonstrates the strategist seat end-to-end, proving the audience is hosts-in-general and not colleague alone
- the claimed gap is real in code, not just prose: no module under embodiment/ implements attention or priority allocation today, and the relationships.md salience row stays honest until a measured strategist ships — the row is only rewritten when the measurement exists
- every after-state property maps to a shipped, tested surface: directive versioning and supersession under structural tests, observability through the ledger and observer events, and byte-identical actor-only behaviour under the existing suite plus the import test
- the verdict rule and benchmark seeds are committed before any live model call, and every ScopeBench result names which of the seven conditions it satisfies or fails — absent cells and invalid episodes are reported, never silently dropped

## Success signals

- the pre-committed ScopeBench verdict rule from issue #51 holds: strategic-utility improvement over the actor-only baseline on at least two scenario families, improvement present in the deterministic-subordinate stage, no material regression on non-intervention controls, zero authority violations, gains not explained by extra tokens alone, end-to-end operational success preserved — and negative or INCONCLUSIVE outcomes are publishable first-class results

## Scope / boundaries

- host authority stays untouched by the authority-split table: permission and safety remain with the injected ToolExecutor (run() takes executor with no default, loop.py:2025-2048) and the control-bearing hook lane; operator-facing wording and timing remain with host senses/presence — a directive structurally cannot carry tool calls, tool arguments, shell commands, file edits, approval decisions, or operator speech
- eidetic and coherence remain the memory owners: the host-supplied scope projector may consume continuity and coherence outputs through the existing continuity.py seam, but must not reproduce their storage, retrieval, or scoring logic (issue #2 compose-dont-reimplement; relationships.md Remember / Relate rows)
- a single-model run must not claim a strategist exists, and every scope record exposes the actual contributing model and role — the Gwen rule from colleague#352 extends unchanged to the new tier; roles are configured explicitly, never inferred from model names
- the CLI stays introspection-only: no scope verb drives the loop — tests/`announcement_checklist.py` caveat cli1 (line 839) already checks both the verb list and that nothing under cli/`_commands`/ imports embodiment.loop, and the scope layer must not change that
- the bee-hive orchestrator-worker architecture is out of scope for this rescope: embodiment architecture story is the three-authority-level design. The bee-hive footprint is entirely examples/ (`arch_hive.py`, `worker_seam.py`, `worker_scoped_overhead.py`), tests/ and docs/ — no package module is hive-specific (the embodiment/ grep hits are the word "archived"), so this boundary costs the package nothing
  - instruction: treat any bee-hive relocation (parked v2) as its own future frame; this frame neither extends nor removes the hive, and CI keeps its tests passing untouched

## Non-goals

- the senses coordination loop stays in the host: no senses-loop module exists in embodiment/ (package listing, 2026-08-03) and docs/relationships.md pins the c30 decision — embodiment ships one actor loop, colleague keeps the senses coordination loop; issue #51 restates this as a non-goal
- the muse is neither replaced nor renamed: muse.py / `muse_runner.py` stay optional and advisory with `MUSE_AUTHORITY` as the single charter source (docs/relationships.md "not the muse charter" note); the strategist is a separate seam with typed scope-limited authority, not an upgraded muse
- no generic difficult-task escalation: loop.py:99 records deepthink escalation as deliberately left at the product layer, and the strategist runs at review boundaries for scope — never merely because a prompt is locally hard
- League stays downstream integration evidence, never the primary strategic verdict: league-h2h.md itself records `outcome.total` tied 0-0 in all six matches and concludes the series separated the arms on interface compliance, not on play

## Assumptions

- `scope_runner` cadence and staleness constants must be derived from measured strategist latency, never guessed: the dense 27B thinking model spends ~1000 reasoning tokens before acting (measured 2026-07-30), and muse.py's `DEFAULT_STALE_LAG`=5 was already a guess made under an inverted latency assumption
- the Operation-tier model seam already exists live: the lobes gateway /capabilities (probed 2026-08-03) carries a `worker` role — unsloth/Qwen3.6-35B-A3B-NVFP4, ready, proxied like senses — so arms A0/A2/A3 are provisionable today. The seat mapping is decided (q1 + the updated ticket): strategy maps to the cortex role, operation to the worker role; the advertised `final_authority`/`tool_use` on the cortex role describe the live role envelope, and embodiment enforces the strategist seat authority structurally on its own side
- ScopeBench lands in examples/ organized per architecture — a subfolder per architecture (operator decision 2026-08-03), e.g. an examples/scope/ folder for the ScopeBench harnesses, rather than more flat `arch_`\* files at the top level; the arch precedent carries over (deterministic controls, pre-registration, machine grading — `arch_hive.py`, `arch_policy.py`, `league_h2h.py`); no new package module or sibling repo is needed for the bench
- the worker model has measured seam rates and smoke (worker-seam-smoke.json, worker-throughput-summary.json, both naming unsloth/Qwen3.6-35B-A3B-NVFP4) but has NEVER driven embodiment.loop.run() as the acting seat — the bee-hive arm B deliberately gave it no loop, no turn, no goal — so arms A0/A2 rest on unmeasured loop-driving capability and the live series needs a loop-driving smoke (the colleague#360 failure shape: tool-call XML emitted as text under budget pressure) before any Stage 2 dial

## Scope exploration

- `s1` — `embodiment/loop.py:2021-2040 run() signature`: run() already exposes observer, presence, continuity, hooks and controls as injected seams — `run_scoped`() can compose the actor loop without touching it
  - seeds: `c5`
- `s2` — `embodiment/loop.py:99 module docstring`: deepthink escalation is named in the extraction notes as deliberately left at the product layer — the strategist must not become it
  - seeds: `c14`
- `s3` — `tests/test_loop.py:417-548, tests/test_muse.py:224-231 + 645-662, tests/test_presence_engine.py:972-1011`: the three structural techniques are all live: AST exit-returning-function walk for termination, dataclasses.fields() decision-vocabulary ban, AST imported-modules ban — reusable verbatim for Stage 0 authority tests in both directions
  - seeds: `c6`, `c13`
- `s4` — `embodiment/muse_runner.py:800-815 + 1316-1358`: single replaceable pending slot (a newer boundary replaces a waiting one, displaced work recorded), `_degrade` and `_drop_late` record every loss — the mechanics `scope_runner` mirrors, with drops becoming authority events
  - seeds: `c7`
- `s5` — `embodiment/ledger.py:211-219 + 689-735`: `_MODULES` harvest table auto-collects `DEGRADED_`/`DROPPED_` constants per source lane and read() folds lanes into one stream — a `SOURCE_SCOPE` lane slots in without a ledger rewrite
  - seeds: `c8`
- `s6` — `embodiment/events.py:300-316`: EventEmitter is an ObserverFn that never raises (C3) and loop.py never imports events.py — scope.\* events ride the same host-wired observer seam
  - seeds: `c9`
- `s7` — `tests/test_timeout_bounds.py CLOCKS + docs/live-test-results/timeout-rate-measurements.json`: every clock is parametrized over CLOCKS and derived from the dated committed rate config (`read_by` tests/`rate_config.py`); the config already names the live cortex id — a strategist seam clock joins the same walk
  - seeds: `c10`
- `s8` — `lobes gateway /capabilities, live probe 2026-08-03`: no strategist-named role exists; cortex = unsloth/Qwen3.6-27B-NVFP4 local with `final_authority` + `tool_use` responsibilities; a `worker` role ALREADY exists — unsloth/Qwen3.6-35B-A3B-NVFP4, ready, proxied like senses; muse ready=false (d15 muse-off holds)
  - seeds: `c2`, `c19`
- `s9` — `docs/relationships.md:275-310 function map`: the salience row reads "no owner — nothing in this repo implements it", the gap issue #51 fills; the c30 note keeps the senses coordination loop in colleague; the muse charter has one source (`MUSE_AUTHORITY`) and this table must not become a second
  - seeds: `c11`, `c12`, `c16`
- `s10` — `docs/live-test-results/league-h2h.md:73-142 + 202`: outcome.total tied 0-0 in all six matches (P2 HELD 6/6) and the doc itself concludes the series separated interface compliance, not play — League cannot be the primary strategic verdict
  - seeds: `c15`
- `s11` — `embodiment/ package listing + presence_engine.py:19 + drone.py worker grep`: no senses-loop module exists in the package; "worker" is already overloaded in-repo — `presence_engine.py`:19 calls the cortex "the worker that owns the bounded tool loop" and drone.py uses "worker" for scoped drone intelligence — so issue #51 tier naming collides three ways
  - seeds: `c11`
- `s12` — `tests/announcement_checklist.py:311 + 839 (cli1)`: the shipped console script carries introspection only, and cli1 checks both the verb list and the embodiment.loop import — the boundary the scope layer inherits
  - seeds: `c18`
- `s13` — `eidetic memories embodiment-muse-echo-chamber-gap + embodiment-muse-faster-than-cortex`: recalled 2026-08-03: structural guards stop an advisor seizing authority but nothing stops the actor surrendering it (live probe 2026-07-25), and the 27B thinking model inverted the d1 staleness assumption — both directly shape Stage 0 tests and `scope_runner` constants
  - seeds: `c3`, `c4`
- `s14` — `examples/ harness family (arch_hive.py, arch_policy.py, league_h2h.py)`: the repo already builds graded multi-arm harnesses with deterministic controls and pre-registration in examples/ — the proven landing surface for ScopeBench
  - seeds: `c20`
- `s15` — `repo-wide hive grep + embodiment/ package check`: the bee-hive footprint is examples/ (`arch_hive.py`, `worker_seam.py`, `worker_scoped_overhead.py`), tests/ (`test_arch_hive.py`, bee-hive preregistration, hive CLOCKS) and docs/ only — the embodiment/ package hits are the word "archived", so relocating the bee-hive touches no package module
  - seeds: `c24`
- `s16` — `challenge pass / adjacent-systems lens: colleague#358`: probed 2026-08-03: the seam proposal was last updated 2026-07-29, before issue #51 existed — its loop-import content survives the rescope (zero loop.py diff) but its architecture story is behind; seeded the update-timing question
- `s17` — `challenge pass / counter-evidence lens: docs/live-test-results worker measurements`: REFUTED my own draft risk — the rate config carries a worker entry (35B-A3B, C1-E cell, width-dependent 76.4-29.8 tok/s) and seam smoke exists; what remains unmeasured is loop-DRIVING, seeded as the c31 assumption; plan risk r1 amended to the corrected text
  - seeds: `c31`
- `s18` — `challenge pass / distributed-state lens: /capabilities worker.hosted_by + the 0.11.0 timeout lessons`: the worker is proxied from thor:8000, so every bee-hive measurement already crossed the proxy hop; the strategist seat is local; long-turn discipline is owned by the existing c10 clock walk — clean beyond the width-dependence note folded into r1
- `s19` — `challenge pass / security lens: scope projector inputs + drone.py threat-model precedent`: the injection chain snapshot-to-directive-to-actor was unexamined: the surrender tests covered the second hop only; seeded c29 with the both-hops honesty condition
  - seeds: `c29`
- `s20` — `challenge pass / data-flow lens: embodiment/framing.py`: framing.py:1-129 already carries the needed discipline (`frame_cortex` on the top-level acting loop only, who-is-speaking-never-what-they-may-do); directive rendering routes through it rather than inventing a second prompt path; seeded c30
  - seeds: `c30`
- `s21` — `challenge pass / lifecycle lens: directive persistence across drives`: the spec versions directives within a drive but never says what happens at process exit — no owner among host, continuity, or scratchpad; seeded the persistence question
- `s22` — `challenge pass / concurrency lens: muse_runner + scope_runner coexistence`: `scope_runner` internal concurrency is covered by the t2 muse-runner mirror; what was unexamined is DUAL background minds attached to one actor — seeded the simultaneous-wiring question
- `s23` — `challenge pass / containment lens: mid-drive strategist disable`: degradation covers strategist FAILURE and runner close stops new directives, but no move drops an already-active directive mid-drive; seeded the containment question
- `s24` — `challenge pass / overlooked-actors lens: operator_inbox + presence pump`: presence passes through `run_scoped` untouched (c5 already pins the seam list) — clean; operator intent as a review trigger parked as a t4 design detail

## Decisions

- q2 resolved (operator, 2026-08-03): the new tier is named \*\*strategist\*\* in embodiment code and docs — issue #51 uses "Cortex" for it, but that label is not adopted into this repo vocabulary; per the q1 seat-to-role mapping the strategist seat rides the lobes cortex role and the acting seat rides the worker role, which sidesteps the three-way naming collision recorded in s11
- the strategist MAY use tools — but only a host-allowed, role-scoped bench serving strategic work (memory recall via eidetic, coherence checks if exposed as a tool, and the like), never the actor operational surface: no repo actions, no shell, no file edits. Follows the MuseToolBench precedent — the host wires the bench explicitly, nothing is on by default — and the Stage 0 structural techniques (fields() vocabulary ban, AST import ban) pin that the bench cannot reach actor tools. This deliberately amends issue #51 flat no-tools stance for the strategic tier
- strategist work is accepted as long and slow: latency is handled by streaming transport (the `worker_seam.py` derived first-chunk and inter-chunk bounds) plus background execution in `scope_runner` — never by shrinking the strategist thinking budget. The t24/d16 lesson applies with more force here: a truncated turn arrives indistinguishable from a deliberate one, and for an authority-bearing seam that silent loss is worse than latency
- v1 composition is strategist-only (operator, 2026-08-03): the muse is not part of the three-tier reference architecture — senses (host), Worker actor (lobes worker role, Qwen3.6-35B-A3B), strategist (lobes cortex role, dense Qwen3.6-27B). Dual muse+strategist wiring is unsupported in v1; muse code ships unchanged per c12 and d15 continues to hold
- containment is at drive boundaries in v1 (operator, 2026-08-03): closing `scope_runner` stops new directives and the active directive governs until the drive ends — no mid-drive directive-drop move ships, and the docs state this boundary explicitly
- directive delivery is event-shaped (operator, 2026-08-03): a strategist decision raises events that are inserted into the Worker context at safe boundaries — insertion into the turn stream is the transport, the rendering is framing-composed per c30, and the system prompt is never rewritten mid-drive
- Stage 3 live testing runs context-clear (operator, 2026-08-03): each live session is executed by a fresh-context operator agent following self-contained instructions from a follow-up issue — one filed at plan time for session 1, a second authored during t14 with session 1 findings folded in — so live evidence is never contaminated by builder context

## Open parks

- [unknown_nonblocking] ScopeBench full breadth — eight scenario families each with an exact oracle or declared Pareto frontier — is a larger build than any prior harness in examples/; which families land in the first cycle is a plan-stage sequencing decision, not a scope decision
- [unknown_nonblocking] moving the bee-hive to a different repo altogether is under operator consideration (2026-08-03) — whether and when is unresolved. Footprint if decided: examples + tests + docs only, no package modules; the hive-specific CLOCKS entries in tests/`test_timeout_bounds.py` would travel with it while the rate-config mechanism stays
- [unknown_nonblocking] whether `operator_inbox` (a seam run() already exposes) should feed strategic review triggers — operator intent arriving mid-drive is a natural review boundary but the wiring is a v1 design detail inside t4
- [unknown_nonblocking] directive schema versioning across releases (as opposed to per-instance `scope_id` supersession, which is specified) — the contract.py `schema_version` precedent applies when a directive ever persists or crosses a process boundary
- [unknown_nonblocking] which component stores the durable directive lane — host state round-tripped through the scope projector, or a continuity record — is a t1/t4 design decision inside the confirmed layered-persistence capability (c33)
