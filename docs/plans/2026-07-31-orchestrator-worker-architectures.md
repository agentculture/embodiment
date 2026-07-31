# Build Plan — orchestrator-worker architectures

slug: `orchestrator-worker-architectures` · status: `exported` · from frame: `orchestrator-worker-architectures`

> Gwen gains an orchestrator: the Qwen 27B cortex delegates ground work to the Thor-hosted Qwen 3.6 35B A3B worker role, in two architectures — manager (cortex orchestrates, worker executes) and hybrid (cortex routes simple work to the worker, keeps complex work itself) — and a pre-registered escalating series measures both against the existing single-actor rig on the math, league and logic/coding ladders

## Tasks

### t1 — Serial delegate tool: host executor exposing delegate-to-worker via SpawnRequest(role='worker'), tool surface enumerated, no repo access, cortex keeps finish authority

- instruction: merged: examples/`orchestrator_tools.py` — the delegate tool rides the subagent seam with the worker surface enumerated as the dispatch condition
- covers: c7, h4, c14, h9, c36, h28
- acceptance:
  - hermetic tests prove the child surface excludes parent-level finish and an un-enumerated tool raises UnknownToolError
  - zero diffs under embodiment/ — the tool lives in examples/, riding the existing subagent seam
  - per-child ledger attribution (`SOURCE_SUBAGENT`, `child_task_id`) is verified in tests

### t2 — Worker dial config and wiring smoke: explicit endpoint/model config plus a smoke lane running a bare completion and a bounded tool loop through the exact harness path

- instruction: merged: examples/`worker_seam.py` — explicit config, no silent fallback, live tool-loop smoke committed
- covers: c3, h2, c19, h13, c34, h26
- acceptance:
  - worker endpoint and model come only from explicit flags/env; absent config yields a recorded ABSENT degradation, never a silent fallback to the spark gateway
  - the smoke lane completes one bare completion and one bounded tool loop (schema call, result fed back, clean finish) against the live worker and commits both transcripts
  - per-call records carry `finish_reason` and token counts

### t3 — Worker throughput and effective-concurrency pre-measurement at widths 1, 2, 8, 14

- instruction: merged: worker saturates near width 9; the operator's 50 tok/s x14 did not reproduce — see docs/live-test-results/worker-throughput.md
- depends on: t2
- acceptance:
  - measured tok/s and per-call latency at each width are committed to docs/live-test-results/ before any arm design cites concurrency
  - sustained-load stability (errors and timeouts per width) is reported, addressing the parked stability unknown
  - the report states whether the operator-supplied 50 tok/s x14 figure reproduced on this rig

### t4 — Parallel fan-out tool: one tool call dispatching N workers with bounded total charge and recorded partial-failure degradation

- instruction: merged: the fan-out partitions one grant before dispatch; `FANOUT_ACCOUNTING_RULE` is quotable verbatim for t11
- depends on: t1
- covers: c31, h23
- acceptance:
  - hermetic tests assert the bounded total charge per the pre-registered accounting rule, and that a failed child lands a recorded degradation plus partial results, never an abort
  - the loop's termination guarantee is preserved, asserted structurally (AST or step-count bound), not just behaviourally

### t5 — Architecture arms harness (existing / worker-solo / manager / hybrid) for the challenge rungs, with metered per-call records and the pinned sampling table

- instruction: merged: arms are data not code branches; the cortex model id stays null in committed config until dialled by env
- depends on: t1, t2
- covers: c9, h5, c11, h7, c13, h8, c33
- acceptance:
  - every model call lands one record with `finish_reason`, prompt, completion and reasoning tokens, role, model and arm; budgets 16000 per d16
  - the analyse verb refuses to emit a verdict when either flat arm's cell is missing, and reports missing cells ABSENT
  - senses configuration is byte-identical across arms, asserted by config hash
  - the sampling table (temperature, thinking mode, `max_tokens` per role per arm) is read from committed config, not code defaults

### t6 — League lane arms: all four architectures drive cmatch decision points, with hybrid routing exercised and logged

- instruction: merged: one drive per decision ROUND so the fan-out has units to dispatch; routing basis distinguishes architecture from judgement
- depends on: t1
- covers: c32, h24
- acceptance:
  - all four arms complete a scripted, no-network cmatch end-to-end in hermetic tests
  - the hybrid arm's routing decision is exercised per decision point and logged with its stated reason

### t7 — Coding rung: problems with brute-force-verified answers in docs/challenge-problems.md, an M2-kit grader, and workspace-jailed execution

- instruction: merged: three problems verified two independent ways; the M2 kit caught a shared-seed defect between truth function and reference before shipping
- covers: c18, h12, c35, h27
- acceptance:
  - problem statements and verified answers land in docs/challenge-problems.md before any measured run
  - the grader ships adversarial fixtures, a paraphrase case, a vacuity assertion, and commits raw responses
  - a hermetic test proves model-written code cannot execute outside the network-less workspace

### t8 — Vision rung: image tasks, the senses-description path for the 27B arm, native image parts for worker arms

- instruction: RESCOPED by deviation d1: no longer a two-path asymmetry (the cortex now sees). Build the three-route perception screen — native at the cortex, senses description as text, both — stage 1 on the flat arm only, per c41
- depends on: t5
- covers: c17, h11, c39, h30
- acceptance:
  - both perception paths run live and are logged per call; no video path exists anywhere in the rung
  - the 27B arm's senses-described lane is dialled, not scored structurally absent

### t9 — Team-scoped map renderer: PNG from the fogged briefing alone, adversarial fog-leak fixtures, goldens, committed maps

- instruction: merged: renders only what the fogged briefing places, so unobserved ground is a strict lower bound on visibility and can never overstate it
- covers: c21, h15, c24, h17, c37, h29
- acceptance:
  - the renderer consumes only the fogged briefing JSON; no vision-radius constant exists anywhere in harness code
  - a fog-leak fixture (an entity outside visibility must not be drawn) fails the suite if violated; goldens are committed
  - every measured turn's rendered map is committed to the raw results dir

### t10 — Map-image league cells with enforced text/image twins

- instruction: register an image route against t6's PerceptionRoute seam using `map_render`'s `fog_snapshot`/`write_turn_map`; `snapshot_hash` is the twin key
- depends on: t6, t9
- covers: c22, h16
- acceptance:
  - the harness refuses to run a cell whose image lacks a committed text twin from the same fog snapshot (same turn, seat, snapshot hash)
  - twin snapshot hashes ride the per-call records

### t11 — Pre-registration: ladder, arms, decision and stop rules, sampling table, tool-surface enumeration, fan-out accounting rule, heterogeneous-rung declarations — pinned by test

- instruction: the pre-registration must state: the ladder re-checked against the NEW cortex (c46), fan-out width derived from the measured saturation not the advert (c48), capability facts cited from the committed probes not /capabilities (c43), the routing rung's per-rung exemption from information matching (c40), and tool-call stability against its documented baseline (c45)
- depends on: t3
- covers: c10, h6, h24, h25, c40, h31, c43, h32, c45, h33, c46, h34, c48, h35
- acceptance:
  - committed before the first measured dial, provable from git history; a pin test recomputes its constants rather than asserting literals
  - it names, for every hybrid-graded rung, why that rung is heterogeneous, and excludes degenerate cells from the verdict by rule

### t12 — Run the series up the ladder with the stop rule, committing artifacts as they land

- instruction: the live series; do not start without the operator's go-ahead. Cell sizing must respect the measured ~57s median cortex turn with a 204s tail
- depends on: t4, t5, t6, t7, t8, t10, t11
- covers: c27, h19
- acceptance:
  - each climbed rung lands its raw jsonl and per-call records beside the results doc; a skipped rung is recorded ABSENT citing the stop rule
  - smoke transcripts and throughput pre-measurements are committed with the series artifacts

### t13 — File the sibling proposals: the lobes-cli worker role, and the league-of-agents fog-scoped render verb plus per-unit visibility surface

- instruction: DONE 2026-07-31 — filed with measured evidence: lobes-cli#166 (worker role + modality; carries the cortex-vision advert defect) and league-of-agents#43 (fog-scoped render + per-unit visibility; carries the `video_url` finding about their own replay GIFs). Both signed by agtag; no commit touched either sibling repo.
- covers: c5, h3, c26
- acceptance:
  - both issues exist, signed per convention, carrying the Thor advert JSON and the briefing-schema probe as evidence
  - no commit touches either sibling repo

### t14 — Results and verdicts: decision rules quoted beside every verdict, wall-clock and token tables per arm per rung, corrections section, README reproduce lines

- instruction: record what the runs contradicted, not only what they confirmed — including that the video probe's first conclusion was wrong and how it was caught
- depends on: t12, t13
- covers: c1, h1, c28, h20, c29, h21, c30, h22, h18
- acceptance:
  - every verdict quotes its pre-registered rule; INCONCLUSIVE is reported as INCONCLUSIVE, never softened
  - wall-clock and token tables (content and reasoning separately) per arm per climbed rung
  - the before-state is cited from d15, next-cycle M1 and the live Thor advert; both sibling issues are linked
  - the announcement's claims are verified by re-running the README reproduce commands

### t15 — Governance guard: muse modules untouched and the promotion gate honored in the landing PRs

- instruction: merged: guards verified to fail when provoked, not merely to pass
- covers: c16, h10, c20, h14
- acceptance:
  - the cycle's PRs delete no muse module, test or harness, verified by diff review
  - reference-rig tables change only with a supporting verdict; INCONCLUSIVE leaves d15's rig untouched

### t17 — Video-perception lane: league's existing GIFs delivered as `video_url` parts, plus the media.py builder that makes them reachable — wired to embodiment#39

- instruction: land the capability only: a media.py `video_url` builder plus a flatten placeholder. league's GIFs are used unchanged — the probe rules out both `image_url` and frame sampling
- depends on: t6
- acceptance:
  - media.py gains a video content-part builder and a `flatten_parts` placeholder; a hermetic test proves a text-only surface degrades cleanly rather than dropping the part silently
  - a league match GIF is delivered unchanged as a `video_url` part — no frame sampler, no re-encode — and a hermetic test pins that the `image_url` path is NOT used for video
  - the committed probe (docs/live-test-results/video-perception-probe.md) is cited for why `video_url` is used over `image_url` and over frame sequences, so neither dead end is re-explored
  - the unknown the probe leaves open is measured before any claim about playthroughs: how many frames of a long match actually survive the server's internal sampling

### t18 — Wire Perception.parts to the wire: image and video routes must produce real multimodal payloads, with a vacuity assertion that fails when parts are dropped

- instruction: found by grep after t10 merged: zero consumers of Perception.parts and zero media references in any harness. t17 already landed the media builder, so this is wiring plus the assertion that proves it fired.
- depends on: t10
- covers: c51, h36
- acceptance:
  - a hermetic test asserts an image route's dial produces an outgoing message carrying an image part built through embodiment.media, and the same test FAILS when the parts are dropped — asserted on the payload, never on the route's own record
  - the text route's payload is byte-identical to today's, so wiring multimodal support changes no existing cell
  - an image route whose parts cannot be built degrades to a recorded ABSENT cell, never to a silent text dial

### t19 — Report the series' findings to colleague: a second communicate post once results exist, including the negatives

- instruction: operator asked for two posts: the rig facts (colleague#361, DONE 2026-07-31) and this one after the series, so colleague can learn from the studies rather than only the configuration
- depends on: t14
- acceptance:
  - the post carries what the series measured including INCONCLUSIVE and CEILING verdicts, never only the favourable ones
  - it links colleague#361 (the rig-facts post) and states which of its numbers the series revised
  - no commit touches the colleague repo

## Risks

- [unknown_nonblocking] the served worker build's stability under sustained x14 load is unknown until t3 reports; a sibling variant crash-looped on GB10 hardware (task t3)
- [unknown_nonblocking] hybrid routing quality has no precedent grader; if a non-defective instrument cannot be designed, the hybrid verdict degrades to cost-plus-outcome only, and the pre-registration must say so (task t11)
- [unknown_nonblocking] the subagent seam's parent-minus-one attenuation may be the wrong bound for a 14-wide fan-out; if a width budget is needed it is a proposed embodiment change, never a harness hack (task t4)
- [unknown_nonblocking] the ladder may separate at the first rung, leaving coding and vision rungs ABSENT by rule — their harnesses still land hermetically tested, only the live cells go unmeasured (task t12)
- [unknown_nonblocking] the frame-sequence probe is n=1 on a moving square, not a tactical board; whether a mind reads a fogged league map in sequence is untested and is the real question the lane rests on (task t16)
- [unknown_nonblocking] the server's internal frame sampling is unmeasured — 86 prompt tokens for a 6-frame clip implies aggressive downsampling, so a 40-turn match may reach the model as a handful of frames; this bounds what any playthrough experiment can claim (task t17)
