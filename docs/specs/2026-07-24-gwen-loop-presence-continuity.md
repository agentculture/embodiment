# Gwen: loop, presence, continuity

> embodiment ships colleague's bounded tool loop as a reusable stdlib-only package — one loop, usable as a library or CLI — where Qwen is the working cortex and an optional Gemma 4 31B muse injects guidance and critique as a subconsciousness; configured identity makes it Gwen per colleague#352 (byte-identical prompts when absent), and an eidetic/coherence subprocess adapter makes its presence continuous across sessions with every degradation observable
> instruction: verify with: the zero-deps guard test, the byte-identical-prompt golden test, the museless default fixture, the two-host demo, and the continuity demo across two separate interactions

## Audience

- app developers embedding an AI presence in their application — colleague first among them as the refactor consumer — plus the agents operating those apps through the embodiment CLI

## Before → After

- Before: the loop and presence machinery live only inside colleague 1.52.1 — working but unreusable; an app wanting an embodied presence must reimplement the pump, memory and coherence must be hand-wired per host, and no identity framing exists anywhere (Gwen is a contract, not code)
- After: an app imports embodiment (or installs its CLI), supplies a model seam, IO callbacks, and optionally an injected tool executor, and gains the bounded perceive-decide-act loop with presence between acts; configured with an identity it becomes Gwen — Qwen cortex worker with an optional Gemma 31B muse subconsciousness — and via the eidetic/coherence adapter its presence is continuous across sessions, with every degradation observable

## Why it matters

- the loop gets written once and imported, not reimplemented per host; presence claims stay honest because degradation is recorded, the operator's words survive verbatim, and continuity comes from authoritative subsystems (eidetic, coherence) rather than a larger context window

## Requirements

- extract colleague 1.52.1's injection-shaped seams: loop.py bounded tool loop (h3 guaranteed termination; task_start/pre_tool/post_tool/finish hooks with only pre_tool control-bearing; ToolCall/ModelResponse/WorkAborted/TaskResult) plus the presence stack (presence_engine.py PresenceIO pump — no TTY/thread/clock; presence.py pure policy; senses_loop.py tools-off coordination loop) — decoupling work, not redesign
  - honesty: the extracted loop preserves colleague's guarantees without importing any colleague module: guaranteed termination (finish / empty tool-call turn / max_steps), the four-event hook lifecycle with only pre_tool control-bearing, and the public shapes — proven by porting colleague's loop-behavior tests
- pure-stdlib core (C1): [project].dependencies stays exactly [] — verified empty at embodiment 0.6.2; optional capabilities live behind extras with lazy in-function imports, so pip install embodiment clears colleague's tests/test_zero_deps.py bar
  - honesty: embodiment ships its own zero-deps guard (runtime sys.modules diff, no allow-list needed); [project].dependencies is [] at every release; optional extras import lazily inside functions, never at module load
- observable degradation (C3): every degradation records a transition the host can see — cadence caps, clarify thresholds, and senses_loop's explicit loop→beats→off ladder; nothing degrades silently
  - honesty: a test enumerates every degradation path in the shipped code and asserts each appends a host-visible record (the recorded-ladder pattern); no silent except-pass on any presence path
- inherit both senses.py invariants verbatim: ContextPacket.original is set from the caller's input verbatim (never from model output), and every presence-path invocation never raises — dead port, request error, overflow, or lossy JSON degrades to None plus a degraded record
  - honesty: fault-injection tests (dead port, overflow, malformed JSON, hostile model output) show every public presence entry point returns a degraded value instead of raising, and the perceived original stays byte-identical to the caller's input
- state the software-presence boundary in explain output too: README.md already carries the boundary blockquote (verified on disk), but embodiment/explain/catalog.py has no boundary language (grep for presence/body/physical/software returns nothing) — C2 requires both surfaces
  - honesty: embodiment explain output renders the software-presence boundary statement, and a catalog test asserts it is present
- compose eidetic-cli (memory: recall, provenance, consolidation, relevance, ageing, forgetting) and coherence-cli (the relationship between memory and the present: quality, meaning, signal, investiture, frames) as runtime subsystems; embodiment owns only the lived sequence — when something is perceived, considered, acted on, remembered, or revisited
  - honesty: the lifecycle invokes eidetic and coherence through the adapter at the defined checkpoints; embodiment source contains no store, no scoring, and no embedding logic of its own
- coherence checks run at meaningful boundaries — before consequential action, before final completion, before durable memory; remember selectively; preserve provenance from perception through action into durable memory
  - honesty: the lifecycle exposes named checkpoints — before consequential action, before final completion, before durable memory — each covered by a test showing coherence consulted and memory writes gated there
- definition of done spans two hosts: continuity demonstrated across at least two separate interactions in a minimal non-colleague app, and the same composition inside colleague without colleague-specific assumptions leaking into embodiment
  - honesty: the repo contains a minimal non-colleague demo app demonstrating recall across two separate process runs, and embodiment's public API carries no colleague-specific types or imports
- Gwen is explicit configuration for the reference rig, reusing colleague's resolved identity (culture.yaml nick/suffix, then .colleague/identity.json) — no parallel persona system, and identity is never inferred by parsing model names
  - honesty: identity enters only through explicit configuration on the resolved-identity seam; a test changes model names and asserts the resolved identity is unchanged
- absent identity => byte-identical prompts: no configured identity means today's prompts survive unchanged, byte for byte — the acceptance criterion that keeps the feature honest; tests must cover unnamed, named single-model, and named dual-model prompt composition
  - honesty: a golden test renders every prompt surface with no identity configured and asserts byte-equality with the pre-identity output
- role-specific authority per prompt-bearing role: senses speaks externally as Gwen in the first person; cortex is framed only on the top-level acting loop (typed subagents are not cortex); muse receives its authority boundary via a system message on every deepthink path; transcription stays identity-neutral; a single-model run must not claim another mind exists
  - honesty: each prompt-bearing role's template carries its own authority framing; typed subagents never receive cortex framing; a muse-absent run mentions no second mind; tests cover unnamed, named-single-model, and named-dual-model composition
- inherit PresenceIO as the IO seam: 8 plain callables all defaulted to no-ops (dispatch_to_cortex, append_guidance, read_flight, render, poll_operator_input, feed_tail, task_state, narrate), with narrate exception-swallowed so TTS can never disturb the text path; carry over both AST guard tests — no-front-import independence and presence.py policy purity (no time/threading/datetime/subprocess)
  - honesty: the extracted PresenceIO keeps every callback defaulted to a no-op, and the ported AST guard tests (no-front-import for the engine; no time/threading/datetime/subprocess for policy) pass in embodiment CI
- the subprocess adapter must pin its environment: eidetic recall resolution depends on cwd (git-toplevel probe in backend.py _resolve_write_dir — public+in-repo writes to <git-toplevel>/.eidetic/memory, else HOME) so the adapter sets EIDETIC_DATA_DIR or pins cwd explicitly; and coherence assess exits 0 even when the embedding endpoint is unreachable, naming what could not run in the report's unavailable field — the adapter must inspect unavailable/diagnostics, never trust exit code alone
  - honesty: the adapter sets EIDETIC_DATA_DIR or an explicit cwd on every eidetic invocation, and parses coherence verdicts from the JSON payload including the unavailable field — a test proves an unreachable embedder (exit 0) still surfaces as a visible degradation
- the muse injects through the advisory channel only: its instructions, guidance, and critique arrive as text the cortex reads (guidance/message stream), never through the control-bearing pre_tool hook — the muse holds no deny/rewrite authority over tool calls, preserving colleague#352's proposes-never-decides boundary in the mechanism, not just the prose
  - honesty: the muse's output enters the loop only as advisory text in the guidance/message stream; a test asserts no muse-sourced path can deny or rewrite a tool call
- cortex-only is the primary tested path, not the degraded exception: the default test fixture runs the full loop with no muse configured; the muse is an optional seam whose arrival, absence, and mid-run failure are each recorded per C3 (grounded in thor-muse being declared-UNVALIDATED in lobes docs)
  - honesty: the default CI fixture runs the loop museless end-to-end; muse configured-but-dead and muse mid-run-failure each produce a recorded degradation, asserted by tests
- honest scope for the pump: presence.py policy (cadence, clarify, go-words) extracts as-is, but presence_engine's driver seam is redesigned around the cortex+muse pair because the senses loop stays behind in colleague — the spec names this as light redesign, not pure extraction
  - honesty: presence.py ports with its tests unchanged; the redesigned engine seam still passes the ported cadence-policy tests and the no-front-import AST guard; the spec and CHANGELOG describe the engine work as redesign, not extraction
- record the framing divergence before implementation lands: comment on colleague#352 via the communicate skill that embodiment implements the cortex+muse framing and leaves senses framing to colleague — a recorded divergence, never silent drift
  - honesty: the comment exists on colleague#352, signed '- embodiment (Claude)', before the identity-framing implementation merges

## Honesty conditions

- no embodiment-authored commit or PR lands directly on agentculture/colleague; the refactor exists only as a tracked colleague issue authored via the communicate skill
- review confirms no eidetic-equivalent or coherence-equivalent implementation exists in embodiment, and the colleague seam proposal names colleague's memory/coherence wiring as the code the refactor replaces
- the tool-approval callback and the coherence gate are separate injection points; a test proves a coherence verdict cannot alter an approval decision, and vice versa
- with identity on versus off, tool schemas, routing, and approval decisions are identical — only speaker-framing text differs; asserted by a test that diffs both compositions
- embodiment's own whoami/doctor output is unaffected by any Gwen configuration; Gwen resolves from the consuming rig's configuration, never from this repo's culture.yaml
- importing embodiment's base package introduces no websocket/audio/third-party module (covered by the zero-deps guard); any future realtime lane is extra-gated, lazily imported, and ears-only
- embodiment source never imports or shells out to shell-cli; the loop runs against any injected executor — proven by a no-shell test fixture driving the loop with an executor exposing zero shell tools
- both audiences are demonstrably served: colleague can adopt without gaining transitive deps (zero-deps guard), and a non-colleague app author can integrate from the README plus the demo app alone
- the end-state is exactly the sum of the confirmed requirements — loop import, optional muse, Gwen-when-configured, adapter continuity, observable degradation — each already carrying its own confirmed, testable honesty condition
- the before-state is survey-verified, not asserted: the loop/presence code exists only inside colleague 1.52.1 (scope entries s1, s13-s20), no Gwen literal exists anywhere (s19), and memory/coherence wiring is colleague-internal (s25)
- the value claim holds only if the extraction preserves the invariants (termination, verbatim, never-raise, recorded degradation) — all pinned by confirmed conditions h2, h5, h6 — and the demo proves reuse without reimplementation (h12)
- every signal is mechanically checkable: CI job status for the three test families, the demo app present and passing in-repo, and the two issue links (colleague seam proposal, #352 divergence comment) resolvable on GitHub
- every announcement clause is demonstrable: a stdlib-only wheel installs cleanly, a colleague-shaped host and a non-colleague demo drive the same imported loop, the museless run is the default tested path, Gwen framing appears only when configured, and continuity works over the adapter

## Success signals

- three observable signals: (1) embodiment's CI is green with the zero-deps guard, the byte-identical-prompt golden test, and the degradation-record tests; (2) a minimal non-colleague demo app in-repo shows continuity across two separate interactions; (3) the seam proposal is filed on agentculture/colleague and colleague#352 carries the recorded framing divergence

## Scope / boundaries

- colleague's refactor is colleague's to make: embodiment proposes the import seam via an issue on agentculture/colleague (communicate skill), never pushes there
- never reimplement eidetic's or coherence's responsibilities inside embodiment, and remove/avoid equivalent parallel implementations in embodiment and colleague
- permission stays separate from coherence: coherence asks whether an action makes sense; the capability layer decides whether it is permitted
- identity framing renames who is speaking, never what they may do: no change to tool authority, routing, or safety policy; traces still expose the actual contributing role, model, and machine
- two identities, never conflated: this repo's mesh identity is the agent 'embodiment' (culture.yaml: suffix embodiment, backend colleague, model Qwen3.6-27B — doctor reports healthy), while Gwen is the reference teammate the package configures for a consuming rig; Gwen resolves from the consuming deployment's identity, not from this repo's culture.yaml
- realtime/voice stays a perception seam behind an optional extra, never a base dependency: colleague's [voice] precedent — sounddevice/soundfile/websocket-client imported lazily, missing extra raises a clean CliError naming pip install colleague[voice], mid-session failure flips RealtimeSession.degraded with one stderr notice; ears-only is double-pinned by tests (static source scan + live wire assertion that response.create is never sent)
- shell-cli is part of colleague, not embodiment: not all embodiment apps have shell access, so embodiment never composes or depends on shell-cli — tools reach the loop only through the injected tool-executor protocol, and a host with no shell simply injects different (or no) tools
  - instruction: guard with the zero-deps runtime diff plus a source grep for shell_cli / shell-cli; ship the no-shell fixture as a first-class test

## Non-goals

- no physical-embodiment claim (C2): reachy-mini-cli owns the robot body, reachy-lobes its local brain, reachy_nova its AI brain — embodiment is software presence; driving hardware would require a stated goal agreed with the robot siblings
- embodiment does not own the presence event stream: reterminal and harmonics-cli are not consumers here; embodiment is loop-only on that axis

## Assumptions

- eidetic and coherence arrive over a subprocess seam or an injected port behind optional extras — never as base dependencies — reconciling issue #2's 'import and compose' with C1's dependencies = []; both are CLIs, consumable over a subprocess boundary, which is exactly how consumers stay dependency-free
- the decoupling cost (parked question 4) is bounded: loop.py's module-scope block is 13 stdlib / 0 third-party / 25 colleague-internal statements, and ~20 of 25 are colleague-policy to convert to injection points following the loop's own existing precedent — subagents, deepthink, senses, and the engine already arrive as injected callables, never imports; the loop-essential carry is context.py (365 L), media.py (111 L), a carved contract.py subset (1791 L, zero internal imports), and a ToolExecutor protocol; the highest-leverage cuts are config.py (3037 L dragged in by the single MAX_SUBAGENT_FANOUT constant) and the contract.py carve
- extraction snag: senses_loop.py imports three private symbols from senses.py (_fold_history, _TokenMeter, _window_text at senses_loop.py:58) — these must be promoted to public seams in the extracted package, not re-imported privately
- Gwen's identity resolution slots into colleague's existing seam: identity.py resolves culture.yaml top-level nick, then first agent suffix, then .colleague/identity.json 'as' (repo-level before user-home), stdlib line-scan, no PyYAML — and identity.py:22 carries an explicit TODO reserving a sub-identity insertion point between those two sources; no new persona system is needed
- the subprocess seam is forced, not merely preferred: eidetic-cli 0.12.1 depends on data-refinery-cli[store] (pulls neo4j+pymongo transitively) and coherence-cli 0.6.1 depends on numpy+httpx — neither can be a base dependency of a stdlib-only embodiment; both expose the byte-identical agent-first CLI contract (same exit codes 0/1/2/3+, same stdout-results/stderr-errors split, same argparse-errors-honour---json mechanism), so ONE shared subprocess adapter covers both, and embodiment owns its own drift test against eidetic's docs/contract.md as that document prescribes for consumers
- roles resolve by name from the lobes /capabilities contract: ROLES = (cortex, senses, muse, embedder, reranker, stt, tts) at lobes/roles.py:59, one canonical build_role_registry behind both the CLI and GET /capabilities; every role's endpoint is the single gateway origin (routing by model field, internal upstreams never leaked); ready is clamped structurally so an unloaded/unwired role can never report ready; the injected-port precedent is colleague resolving embedder and injecting EIDETIC_EMBED_URL / COHERENCE_EMBED_URL before shelling out
- the 31B muse leg of Gwen's reference rig is conditional: lobes init --shape thor-muse exists (drops cortex+senses, hosts nvidia/Gemma-4-31B-IT-NVFP4 at 256K context, measured KV budget on a live Thor boot) but docs/deployment-shapes.md:61 marks it declared-UNVALIDATED — no accept-shape transcript under docs/evidence/ (lobes#108); Gwen must degrade coherently when no muse is served, and the realtime_vad_session advert on stt is additive and appears only when the audio overlay is actually wired
- lobes-cli is the structural precedent embodiment should copy: dependencies = [] base wheel with heavy capability quarantined into extras only containers install, an import-isolation guard proving the stdlib path stays importable, and pure stdlib decision modules tested offline with a thin pragma-no-cover glue edge (lobes/realtime/_segmenter,_wire,_session,_pcm vs app.py)

## Scope exploration

- `s1` — `agentculture/embodiment#1 §2 (build brief: the seam already exists — surveyed live at colleague 4e27905)`: loop.py docstring already promises engine-agnosticism (the loop never knows the difference between mock and vLLM complete callables); presence_engine imports no front module, pinned by an import-graph test — the package boundary is already drawn
  - seeds: `c2`
- `s2` — `embodiment/pyproject.toml + agentculture/embodiment#1 §4 C1`: dependencies = [] is already true on disk at 0.6.2; colleague's zero-deps guard asserts its deps are exactly [agentfront>=…] and no third-party top-level import — a naive new base dep breaks colleague CI
  - seeds: `c3`
- `s3` — `agentculture/embodiment#1 §6.4 (first moves: propose to colleague, do not push)`: same rule shell-cli was given; cross-repo asks go through tracked issues
  - seeds: `c4`
- `s4` — `agentculture/embodiment#1 §4 C3 + §2b (senses_loop.py degradation ladder)`: a boundary whose turns all degrade transitions for the next boundary AND records the transition; an app that appears attentive and is not is the worst available outcome
  - seeds: `c5`
- `s5` — `agentculture/embodiment#1 §2c (the invariant to carry over verbatim)`: a presence layer that can lose the user's words, or fail loudly into an app's main path, is worse than no presence layer
  - seeds: `c6`
- `s6` — `embodiment/README.md (boundary blockquote) + embodiment/explain/catalog.py`: README states 'Software presence, not a robot body' explicitly; the explain catalog does not yet — the C2 obligation is half-done on disk
  - seeds: `c7`, `c8`
- `s7` — `agentculture/embodiment#2 (intended relationship + guiding principles)`: memory and coherence are runtime, not tools the model picks arbitrarily; the issue's work list explicitly requires importing both through their intended reusable package surfaces and removing equivalents
  - seeds: `c9`, `c10`
- `s8` — `agentculture/embodiment#2 (guiding principles: boundaries, selectivity, permission split)`: storing everything is not understanding what matters; coherence findings split into informative vs progress-pausing is one of the issue's open implementation questions
  - seeds: `c11`, `c12`
- `s9` — `agentculture/embodiment#2 (definition of done)`: a host must be able to observe how past experience affected the present interaction, where remembered information came from, whether contradictions were found, and what was carried forward
  - seeds: `c13`
- `s10` — `agentculture/embodiment#2 x #1-C1 dependency tension`: issue #2 says import both through their intended reusable package surfaces; C1 forbids new base deps; the intended reusable surface for both siblings is their CLI — pending verification of eidetic's own README argument
  - seeds: `c14`
- `s11` — `agentculture/colleague#352 (Gwen: design boundaries + both acceptance-criteria lists; open, 0 comments)`: the issue defines Colleague=runtime, Gwen=teammate identity, Qwen 3.6 27B=cortex, Gemma 4 31B=muse, Gemma 4 12B=senses; it is a contract to build against — no Gwen literal exists in colleague 1.52.1 yet
  - seeds: `c15`, `c16`, `c17`, `c18`
- `s12` — `embodiment/culture.yaml + AGENTS.colleague.md + 'uv run embodiment doctor' (healthy: prompt_file_present, skills_present)`: the repo's own agent identity machinery is live and green; nothing on disk mentions Gwen yet — the teammate identity is entirely future configuration surface
  - seeds: `c19`
- `s13` — `colleague/loop.py:32-91 import block @4e27905 (13 stdlib, 0 third-party, 25 internal) + docstring-only injected collaborators at loop.py:2489,2601,581,2860`: already-injected precedent exists in the file itself; per-import triage done: affectedtests/autosplit/backpressure/coherence/escalation/fillline/lint/memory/testintegrity/capacity/chain/incompletion/neighbours/policy/roles/selfknowledge/telemetry/tui-progress are policy; context+media+contract-subset+tools-protocol are loop-essential
  - seeds: `c20`
- `s14` — `colleague/presence_engine.py:59-99 (PresenceIO) + tests/test_presence_engine.py:245 + colleague/presence.py:10-14 + tests/test_presence.py:196`: presence_engine imports only 3 internal modules (contract, presence, senses_loop); presence.py confirmed pure — stdlib string/dataclasses/typing only; both properties are pinned by AST tests that must travel with the extraction
  - seeds: `c21`
- `s15` — `colleague/senses_loop.py:58 (private-symbol imports) + :85 DEFAULT_LOOP_CAP=2 + :256-273 _transition`: ladder transitions are recorded as SensesRecord facts (senses-ladder:old->new) plus an exception-swallowed on_rung_change hook — the C3 observable-degradation machinery already exists and travels with the extraction; the private-import snag is the one impurity
  - seeds: `c22`
- `s16` — `colleague/identity.py (resolve_identity :44-92; :22 reserved insertion TODO; identity_env :94)`: the resolved-identity mechanism colleague#352 says to reuse is 247 lines, stdlib-only, with a reserved insertion slot already commented in place
  - seeds: `c23`
- `s17` — `colleague/realtime.py (:493 open_session, :176 ears-only note, lazy imports :222/:564) + tests/test_realtime_client.py:299,:430 + colleague pyproject [voice] extra`: one sanctioned daemon pump thread, threading.Event stop, poll-wake read, bounded join; the module is the sanctioned third place allowed to import threading per tests/test_boundary.py
  - seeds: `c24`
- `s18` — `colleague/tests/test_zero_deps.py (runtime sys.modules diff; loop imported at :282; agentfront allow-listed at :65) + colleague/pyproject.toml:27 deps=[agentfront>=0.20.0]`: the guard is a runtime import diff, not a static scan — a stdlib-only embodiment needs no allow-list entry at all; colleague 1.52.1 verified at exactly one base dep
  - seeds: `c3`
- `s19` — `grep -rniI gwen over colleague @4e27905 + git log --all --grep=gwen`: zero matches, zero commits — colleague#352 is a contract to build against, not code to read; the only identity code is the name-agnostic identity.py
  - seeds: `c15`, `c16`
- `s20` — `colleague/senses.py:524 (original=text  # VERBATIM) + never-raise sites :542,:602,:669,:838,:961,:1002,:1132`: the verbatim invariant is a single assignment with the contract restated in seven docstrings; every public senses entry wraps its whole body and returns a degraded value, never propagates
  - seeds: `c6`
- `s21` — `eidetic-cli/pyproject.toml (deps=[data-refinery-cli[store]>=0.6,<0.7]) + coherence-cli/pyproject.toml (numpy>=1.26, httpx>=0.27) + eidetic-cli/README.md:7-10 + eidetic-cli/docs/contract.md par.4`: eidetic's README states consumers stay dependency-free because they call eidetic over a subprocess boundary; contract.md par.4 prescribes each subprocess consumer pins scope/visibility conventions with its OWN drift test (eidetic's own is tests/test_contract_drift.py)
  - seeds: `c14`, `c25`
- `s22` — `eidetic-cli/eidetic/memory/backend.py (_resolve_write_dir :186, _candidate_read_dirs :205, _git_toplevel :155 cwd-cached) + coherence-cli/coherence/cli/_commands/assess.py:12-21 (unreachable embedder exits 0)`: two silent-corruption traps for a naive shell-out: cwd-dependent store resolution, and a 0 exit that does not mean all domains ran; C3 observability must read the payload, not the exit code
  - seeds: `c26`
- `s23` — `lobes-cli/lobes/roles.py (:59 ROLES, :177 STT_REALTIME_RESPONSIBILITY, :537 build_role_registry, :718 _resolve_audio_role) + lobes/profiles/builtin_shapes/thor-muse.toml + docs/deployment-shapes.md:61 + lobes/realtime/ (app.py:279 /v1/realtime, protocol.py:15-25 pcm16@24kHz)`: seven-role contract confirmed in code; thor-muse provisions COMPOSE_PROFILES=muse + MUSE_BASE_URL; muse is honestly feasible:false on non-hosting boxes and model=muse 404s role_infeasible rather than falling back to cortex
  - seeds: `c27`, `c28`
- `s24` — `lobes-cli/pyproject.toml (deps=[], extras [realtime]/[chatterbox]) + lobes/realtime/__init__.py import-light guard note`: a shipping sibling already clears the exact C1 bar embodiment must clear, with the extras+guard pattern proven in production
  - seeds: `c29`
- `s25` — `coherence-cli/README.md:219-234 (consumer map) + colleague/loop.py existing wiring (memory recall/remember :2113/:2166, run_coherence_gate :3637)`: the issue-2 composition already half-exists in the wild: coherence's consumer map declares eidetic gating memory writes on subdimensions.consequence/future_constraint, and colleague already wires recall-before/remember-after plus a post-loop coherence gate inside loop.py — exactly the code the extraction moves and the parallel implementations issue 2 says to remove
  - seeds: `c9`, `c10`, `c11`
- `s26` — `coherence-cli/README.md:92-95 vs coherence-cli/pyproject.toml`: upstream doc drift, not embodiment's to fix: the README line 'the runtime package has no third-party dependencies' is stale against pyproject's numpy+httpx — flag to coherence-cli via communicate when the seam is proposed; do not cite that README line as evidence of stdlib purity

## Decisions

- one loop ships: the bounded tool loop extracted from colleague/loop.py; the senses coordination loop (senses_loop.py) does not ship in embodiment
- two seams reshaped as worker + subconscious: Qwen (cortex) is the worker/thinker — owns the loop, repo actions, final synthesis, final authority; Gemma 4 31B (muse) is the reviewer and commenter — injects instructions, guidance, and critique into the running loop as the cortex thinks, like a subconsciousness; the muse seam is advisory-only (proposes, never decides, per colleague#352's authority boundary) and optional — embodiment degrades coherently to cortex-only when no muse is served
- library + CLI ship: the app imports embodiment and supplies callbacks, and the embodiment console script is the second surface; wrapper mode (driving an app from outside) is dropped
- the Gwen framing lands in embodiment: Gwen is the identity embodied with embodiment — colleague adopts it by importing the package, and colleague#352's contract is implemented against embodiment's prompt/identity seam

## Resolved vagueness

- [unknown_blocking] C1b — the third-base-dependency question: does colleague allow-list three base deps (agentfront + shell-cli + embodiment), or does embodiment compose shell-cli so colleague gains one instead of two? Not settleable unilaterally — needs written agreement from colleague and shell-cli via the communicate skill before the dependency shape is chosen. — resolved: embodiment's half is decided: no shell-cli composition, tools arrive only via the injected executor protocol (c35). The residual — whether colleague allow-lists embodiment as a base dep alongside agentfront and shell-cli — is colleague's call, to be raised in the seam-proposal issue via communicate.
