# orchestrator-worker architectures

> Gwen gains an orchestrator: the Qwen 27B cortex delegates ground work to the Thor-hosted Qwen 3.6 35B A3B worker role, in two architectures — manager (cortex orchestrates, worker executes) and hybrid (cortex routes simple work to the worker, keeps complex work itself) — and a pre-registered escalating series measures both against the existing single-actor rig on the math, league and logic/coding ladders
> instruction: verify by re-running the series commands recorded in docs/live-test-results/README.md and comparing the results doc's numbers to the raw jsonl

## Audience

- the rig operator (who owns architecture promotion decisions), embodiment's maintainers (whose subagent seam the arms exercise), and the sibling repos: lobes-cli and league-of-agents receive proposals, colleague consumes the measured architecture story

## Before → After

- Before: the rig acts through one serial mind: the 27B cortex performs every act (d15), the 35B worker sits ready and idle on Thor with no harness dialling it, no coding or vision rung exists, and parallel execution is structurally absent — M1 recorded it blocked on a second mind
- After: the repo carries pre-registered, live-measured verdicts comparing today's flat-27B rig against worker-solo, manager and hybrid architectures up an escalating ladder — committed math/logic problems, the league arena including the new fog-scoped map-image lane, a new coding rung and a vision rung — with cost, wall-clock, truncation and correctness reported per arm; the worker enters the reference rig only if the decision rule supports it

## Why it matters

- the cortex is slow and serial while the worker is fast, concurrent and idle: if orchestration wins, Gwen gets faster and gains vision and parallel lanes without surrendering final authority; if it loses, a measured negative stops a tempting architecture from shipping on vibes — either way the next architecture decision is made on evidence, the way d15 was

## Requirements

- the spark gateway (localhost:8001) must expose the worker role by name before any live arm dials it: embodiment resolves roles from ONE gateway's /capabilities, spark's advert set today is cortex/senses/muse/embedder/reranker/stt/tts with no worker, and its muse entry is stale (`ready: false`) — either spark's lobes config proxies worker the way it already proxies senses, or the harness takes an explicit worker-endpoint override
  - instruction: add explicit --worker-url/--worker-model (env-overridable) harness config; run a wiring-smoke lane first, as league-h2h did
  - honesty: before the first measured match, a smoke completion through the exact harness dial path returns from unsloth/Qwen3.6-35B-A3B-NVFP4 with its `finish_reason` recorded — reachability is measured, not assumed from the advert
- the delegation architectures ride the existing subagent seam unchanged: the host executor exposes a delegate tool returning `ToolOutcome(spawn=SpawnRequest(role='worker', model=...))`, the loop's spawn machinery (attenuation, step charging, `SOURCE_SUBAGENT` ledger attribution) already handles the rest, and `examples/league_commander.py` is the live-tested reference pattern — no `embodiment/` source change is required for the manager arm
  - instruction: assert via PR diff review; existing framing tests stay green unmodified
  - honesty: the delegation mechanics require zero embodiment/ source change — arms live in examples/ and tests/; if a worker framing block is added to framing.py it is additive, follows colleague#352's authority rules, and changes no existing framing byte
- every arm's every model call records `finish_reason` via the harness-local parallel record (the #37 workaround every existing harness carries), the cortex and worker token budgets are 16000 per d16, and truncation counts are a reported outcome column — a second model whose truncation is invisible would reintroduce the exact silent-lost-turn failure t24 measured
  - instruction: reuse `league_h2h`'s MeteredSeam pattern; budgets 16000 for both minds per d16
  - honesty: every model call in every arm lands one per-call record carrying `finish_reason` and token counts, truncation is a first-class results column, and a cell with missing records is reported ABSENT rather than summarized
- the series reuses the committed ladders and escalates until separation per #35: math/logic from docs/challenge-problems.md (subset-76 with its planted trap, corrupted register 1920-state, entropic register 17-solution under-determination plus its 3b matched pair), the league lane from the `league_h2h` escalating ladder and `league_seat`/arena instruments — and the pre-registration commits the ladder and decision rule before the first measured dial, as every prior series did
  - instruction: follow league-h2h-preregistration.md's structure; pin its constants with a test as prior series did
  - honesty: the pre-registration (arms, rungs, decision rule, stop rule) is committed before the first measured dial — provable from git history — and the climb stops at the first separating rung
- the arm set includes a flat-35B-solo control (the worker alone, no orchestrator) alongside flat-27B (today's rig), the manager arm (27B orchestrates, 35B executes all ground work) and the hybrid arm (27B routes simple work to 35B, keeps complex work) — every prior series taught that the control is where the result lives, and without worker-solo a hybrid win cannot be attributed to routing rather than to the 35B simply being sufficient
  - instruction: the analyse verb refuses to emit a verdict when either flat arm's cell is missing
  - honesty: arm W (flat 35B) runs at every rung flat-27B runs, and no orchestration claim is stated except relative to BOTH flat arms
- the worker enters the reference rig's identity table only on a supporting measured result from this series — until then it is experiment-only, the same promotion gate the function map lives under; d15's muse-off reference rig stays the shipped default while the series runs
  - instruction: check rig tables in CLAUDE.md/README against the verdict in the PR that lands results
  - honesty: no reference-rig table or identity doc changes this cycle unless the pre-registered decision rule returned a supporting verdict; an INCONCLUSIVE leaves d15's rig untouched
- a vision rung enters the ladder: image tasks ride media.py's existing image parts; the flat-27B arm sees images only as senses-described text (the cortex model is text-only), worker arms see them natively — the one rung where the existing architecture cannot tie at a ceiling, and the arm design must state that asymmetry rather than hide it
  - instruction: 27B arm: senses describes the image into the context packet; worker arms: image parts via media.py; both paths logged per call
  - honesty: the vision rung's images derive from the same fog snapshot as the text control via the committed renderer, and the flat-27B arm's senses-described path actually runs live — it is dialled, not scored structurally absent without a run
- a coding rung is authored this cycle: a committed coding task set + grader under the M2 grader-kit discipline, with the problem statements and verified answers landing in docs/challenge-problems.md first, per that file's own rule
  - instruction: follow M2's four grader-kit requirements verbatim; grader tests are hermetic
  - honesty: the coding grader ships with adversarial fixtures, a paraphrase case, a vacuity assertion and committed raw responses, and the problems plus verified answers land in docs/challenge-problems.md before the first measured run
- the live series dials the worker directly at Thor's gateway via an explicit harness endpoint + model id; a worker-role proposal goes to lobes-cli as an issue (communicate skill), and nothing in the series blocks on lobes accepting it
  - instruction: config precedence documented in the harness; degradation recorded through the ledger, not a log line
  - honesty: the worker endpoint and model id come only from explicit flags/env with no silent fallback to the spark gateway; missing worker config degrades to a recorded ABSENT arm, never a silent substitution
- the league lane gains a map-image experiment: each mind receives a rendered map of the board scoped to its own visibility — every unit its own view, the orchestrator the team-knowledge view — alongside the existing text briefing lane
  - instruction: unit view from `visible_cells` (or its harness-side equivalent) at the decision turn; commander view from `latest_knowledge`; both committed per measured turn
  - honesty: each mind's map is scoped to exactly its own visibility at that decision point — a unit's map never contains a cell its fog computation excludes, asserted by adversarial renderer fixtures — and the orchestrator's map is the team-knowledge view, never ground truth
- the map-image arm must be information-matched to a text control: the same fog-scoped state rendered once as prose briefing and once as an image, so any separation attributes to presentation modality and not to information content — an image carrying more (or staler) state than the text arm would measure the leak, not vision
  - instruction: pairing asserted in the harness before dialling; snapshot hashes ride the per-call record
  - honesty: every image briefing has a committed text twin derived from the same fog snapshot (same turn, seat, snapshot hash), and the harness refuses to run a cell whose pairing check fails
- rendering is the new instrument: no fog-aware image renderer exists in league (raster primitives in replay/video.py draw ground-truth GIF only; the fogged views are markdown/JSON and ANSI TUI) — the series renders seat-view PNGs harness-side from the fogged briefing JSON first (unblocked, mirrors the direct-Thor precedent), and a `league match render --team/--unit --fog --turn` verb is proposed upstream to league-of-agents in parallel, propose-never-push; the renderer is an instrument and falls under the M2 grader-kit discipline (a wrong map fed to a mind is a defective instrument)
  - instruction: renderer tests hermetic under tests/ with goldens; rendered maps land in the raw results dir
  - honesty: the renderer carries its own adversarial test kit — including a fog-leak fixture where an entity outside visibility must not be drawn, and golden images — and every measured turn's rendered map is committed so any dial can be re-inspected

## Honesty conditions

- every architecture named in the announcement exists as committed, runnable harness code, and every verdict quoted traces to a pre-registered decision rule applied to committed raw records
- no commit in this cycle touches lobes-cli; the worker-role ask exists as a lobes-cli issue filed via the communicate skill with the Thor advert JSON as evidence
- senses' code path and prompts are byte-identical across arms — a senses change would confound every arm and is out of scope this cycle
- in every arm except worker-solo the finish authority is the cortex's alone (in worker-solo the worker IS the top-level mind and is framed as such), and traces expose the actual role, model and machine behind every contribution
- the cycle's diffs delete no muse module, test or harness; d15 stands verbatim
- the sibling proposals (lobes-cli worker role, league-of-agents fog-scoped render verb) are filed as issues on those repos, and the results doc links both
- the after-state is demonstrated by artifacts, not description: each climbed rung has its preregistration and results doc pair in docs/live-test-results/ with raw jsonl beside it
- the before-state is cited from the repo's own records — d15, next-cycle M1, and the live Thor advert — never asserted from memory
- the speed claim is measured, not narrated: wall-clock and tokens are reported per arm at every rung, so 'faster' is always a number with a direction
- a verdict is only quoted with its pre-registered decision rule beside it, and INCONCLUSIVE is reported as INCONCLUSIVE — never softened into a win

## Success signals

- the series returns a verdict at some rung of the pre-registered ladder — SEPARATED with a direction, or INCONCLUSIVE with the ceiling documented — with the pre-registration committed before the first measured dial, raw per-call records including `finish_reason` committed beside the results doc, and a corrections section recording every belief the runs contradicted

## Scope / boundaries

- lobes-cli's role contract is closed and does not know `worker`: `ROLES` is a seven-name tuple (`lobes/roles.py:59`) validated in ~5 places, no `worker` exists in code, docs or git history, and the served Thor gateway is running ahead of the checkout — adding the role to lobes is sibling work embodiment proposes via issue, never pushes; this series must not depend on it and dials Thor's gateway directly (or via env override) instead
  - instruction: file the issue before results publish; link it from the results doc
- senses (Gemma 4 12B) stays intake, perception and speak-back in every arm, unchanged — it never acts on the repo and never orchestrates; the architecture change is entirely on the acting side (cortex/worker), and `framing.py` explicitly keeps senses out of the framing surface
  - instruction: hold the senses prompt constant in harness config; assert its hash is identical across arm configs
- the cortex keeps final authority in every arm: the worker's advert already forbids `final_decision` and `security_decision`, the worker is framed through the existing subagent framing (or a new worker framing that follows colleague#352's rule that identity framing never modifies tool authority), and no arm makes the worker a second final authority — a single-model run must not claim another mind exists, and a delegating run must expose which role actually produced each contribution in traces
  - instruction: the delegate tool's child surface excludes parent-level finish; per-call trace records carry role+model
- no muse deletion this cycle: d15 stands verbatim — the muse modules, tests and harnesses ship unchanged as opt-in code, off by default
  - instruction: assert via PR diff review

## Non-goals

- video understanding is out of scope for this series: `media.py` supports images and audio only, no video content-part exists anywhere in the repo, and the named ladders are text-only — the worker's video capability is a later cycle's question, recorded here so the capability is not silently forgotten

## Assumptions

- Thor's lobes gateway already advertises the worker role live: `unsloth/Qwen3.6-35B-A3B-NVFP4`, context 262144, `tools: true`, `ready: true`, `loaded: true`, responsibilities `execution, ground_work, bulk_transform, drafting, image_understanding, video_understanding, tool_use, repo_action`, forbidden `final_decision, security_decision` — the Opus-to-Sonnet authority split is already encoded server-side
- the muse is now un-hosted on this rig — Thor's advert shows `ready: false, loaded: false` — so muse-dependent experiments cannot run regardless of embodiment's code; d15's ruling stands that the muse seam ships as opt-in code, off by default, not deleted
- the worker's operational figures are operator-supplied, not advertised: /capabilities has no concurrency, throughput or modality fields, and lobes' own 50-tok/s-class numbers were measured on a different model variant and box class — the series must measure worker throughput and effective concurrency itself before any arm's design leans on x14 fan-out
- league-commander (t28) is the direct prior and its economics invert here: hierarchy cost 2.4-4.4x a flat mind for identical results because the commander AND units were expensive minds contending for the outcome at a ceiling — the worker is a faster-per-token MoE on a box that does not contend with the local cortex, so orchestration can plausibly reduce wall-clock instead of only adding cost; that is the hypothesis the series must actually test rather than assume
- the worker's x14 concurrency on Thor is the first genuinely parallel lane this rig has had — next-cycle M1 recorded parallel cells as blocked on a second cortex, and the worker is that second-plus mind on a non-contended box — so the ladder includes a parallel fan-out rung (manager dispatches N workers concurrently) that no flat arm can structurally match; wall-clock and tokens are primary outcome axes there, since cost/latency separation is guaranteed even where correctness ties
- fog substrate exists and is reusable as-is at team scope: the continuous lane's briefing already applies team-union fog when a match config sets fog true (ground truth is the default), and the grid lane exposes `team_view` and `latest_knowledge` — but every mind-facing payload folds to TEAM scope; true per-unit cones (`visible_cells` per unit) exist only as an unconsumed engine primitive, so per-unit map scoping is new surface work in league or a harness-side derivation
- the commander in manager/hybrid arms is the text-only 27B: it cannot consume map images, so in those arms units (35B) see images while the commander reads text knowledge briefs — the image lane tests unit-level perception and the commander's fusion of visually-informed unit reports; only worker-solo (and any all-35B arm) can put an image in front of the top-level mind, and the arm table must state this asymmetry

## Scope exploration

- `s1` — `Thor lobes gateway /capabilities (live dial, 2026-07-31)`: worker role is live on thor.tail0be7e0.ts.net:8000 with the model, context, tool support and authority boundary verified from the advert itself, not trusted from the operator's description
  - seeds: `c2`
- `s2` — `spark lobes gateway /capabilities (live dial, localhost:8001, 2026-07-31)`: no worker advert locally; senses and muse show the proxied-role pattern (`hosted_by` orin/thor) that a worker proxy entry would follow; muse shows ready:false loaded:false confirming its removal from Thor
  - seeds: `c3`
- `s3` — `deviation ledger (devague deviate --list): d15, d16, d12-d14`: d15 consolidates the rig on Qwen as only actor with the muse seam shipped-but-off; d16 raised cortex token defaults to 16000 after t24 measured 6.0% silent truncation at 2048; d12-d14 record the prior coordinator-delegates-to-developer experiment lineage (t28)
  - seeds: `c4`
- `s4` — `lobes-cli repo (/home/spark/git/lobes-cli): roles.py, profiles/schema.py, profiles/shapes.py, catalog.py, gateway/_config.py`: role set closed at cortex/senses/muse/embedder/reranker/stt/tts; worker absent from all branches and history; RoleInfo schema carries no concurrency/throughput/modality fields; catalog's only 35B A3B entry is mmangkad text-only `role_hint`=candidate with a flaky GB10 history — a different variant from the served unsloth model
  - seeds: `c5`
- `s5` — `lobes-cli docs/qwen3.6-35b-a3b-nvfp4.md + RoleInfo dataclass (lobes/roles.py:207-297)`: no structured channel exists for tok/s, concurrency, vision or video in the capabilities contract; those claims ride prose only, and the cataloged benchmark numbers belong to mmangkad/nvidia variants, not the served unsloth build
  - seeds: `c6`
- `s6` — `embodiment/subagent.py + embodiment/loop.py (ToolExecutor protocol, spawn machinery) + examples/delegation.py + examples/league_commander.py`: SubagentFn is an injected seam; SpawnRequest.role and .model are free strings resolved by the host, not a registry; `league_commander` already ran a commander-delegates-to-units architecture live today with zero embodiment source changes; delegation.py is the hermetic structural proof (never live-tested)
  - seeds: `c7`
- `s7` — `docs/live-test-results/league-commander.md + league-commander-preregistration.md`: INCONCLUSIVE at a ceiling twice (12/12 at 19-0, 6/6 at 10-0); hierarchy 2.4-4.4x cost, ~95% prompt tokens; 1 override in 82 consultations — the commander was an expensive relay; every validity gate passed so the tie is the arena's, and the next design must escalate difficulty until separation (#35)
  - seeds: `c8`
- `s8` — `embodiment/contract.py ModelResponse + issue #37 + examples/league_seat.py:1880, league_commander.py:427-490`: ModelResponse still carries no `finish_reason`; every live harness builds its own record from the raw choice; d16 raised example-host defaults to 16000 after 6.0% silent truncation at 2048
  - seeds: `c9`
- `s9` — `docs/challenge-problems.md + examples/league_h2h.py + examples/arena_series.py + issue #35`: the authoritative problem statements, verified answers and escalation-ladder discipline all exist; league is the repo's own resource-skirmish arena (not Riot's game); four prior experiments tied at a ceiling, so difficulty escalation is the pre-registered fix, not larger n
  - seeds: `c10`
- `s10` — `docs/plans/next-cycle-candidates.md (M4, control lessons) + docs/live-test-results/corrections.md pattern`: M4: ceiling designs measure nothing — the next experiment needs tasks where the flat mind visibly fails; the recurring correction is a missing control arm or defective grader, so arms and graders are designed under the M2 grader-kit discipline
  - seeds: `c11`
- `s11` — `docs/plans/next-cycle-candidates.md M1 + rig topology (cortex local on spark, worker on thor)`: M1's parallelism blocker was the single local cortex; the worker role removes it for delegated work while the cortex stays serial; latency figures stay honest because the two boxes do not contend
  - seeds: `c12`
- `s12` — `embodiment/framing.py (ROLE_CORTEX/ROLE_SUBAGENT/ROLE_MUSE, senses exclusion) + colleague#352 design rules + Thor worker advert forbidden_responsibilities`: framing has exactly three roles and deliberately no senses role; worker framing either rides `ROLE_SUBAGENT` or adds a fourth block under the same authority rules; the advert's forbidden set already encodes the Opus-Sonnet split
  - seeds: `c13`, `c14`
- `s13` — `embodiment/media.py (_MEDIA_TYPES, build_part)`: png/jpg/jpeg/gif/webp images and wav/mp3/ogg/flac audio only; a video lane needs a new media type, part builder and flatten placeholder — new work, not configuration
  - seeds: `c15`
- `s14` — `operator decisions 2026-07-31 (AskUserQuestion, four answers)`: muse code kept per d15; worker dialled direct at Thor; coding rung authored under M2; vision rung added with the senses-described vs native-vision asymmetry stated
  - seeds: `c17`, `c18`, `c19`, `c20`
- `s15` — `league-of-agents repo: engine/vision.py, engine/knowledge.py, charness.py (briefing + fog filters), replay/video.py, replay/tui.py, faces/brief.py, match.py CLI, exported fog frame`: per-team fog is engine-computed and shipped (briefing fog:true, `team_view`, `latest_knowledge`, TUI overlay, brief face); per-unit visibility exists only as the unconsumed `visible_cells` primitive; no image renderer is fog-aware and the only raster path emits whole-match GIF from ground truth; a seat-view PNG verb is assemblable from `_Canvas` draw primitives plus the fogged snapshot shapes but is new league work
  - seeds: `c21`, `c22`, `c23`, `c24`, `c25`

## Open parks

- [unknown_nonblocking] whether the served unsloth/Qwen3.6-35B-A3B-NVFP4 build stays stable under sustained x14 concurrent load on Thor — lobes' catalog records a sibling variant (mmangkad) crash-looping on GB10 hardware, but that was a different build on a different box; unknown until the throughput pre-measurement runs
- [unknown_nonblocking] whether hybrid routing (27B deciding per-task simple-vs-complex) can be graded without a defective instrument — routing quality is a new grader class with no precedent in the repo
