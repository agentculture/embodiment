# function-first loops + muse redesign

> embodiment's loops and model seams are redesigned around cognitive function: the muse ships as reflective/associative counsel (reflect, reframe, challenge, synthesize alternatives) with machine-readable responsibilities and forbidden authorities instead of a creative lobe; the scratchpad, planner and degradation ledger converge into one survivable record of a mind a successor can resume from; subagents arrive as an injected seam whose budgets compose and whose degradations are attributed; loop and presence events ride the shipped events-cli emitter; and the redesign is validated against the existing live-test challenge suite and adversarial league-of-agents play, with the muse configurable on/off so the comparison is measured rather than assumed
> instruction: build against issues 12/11/9/6/7 (plus 8 if confirmed in scope): reframe the muse prompt additively, promote the scratchpad, seam the subagents, commit the challenge harnesses, ship the arena seat host; verify each with the measured acceptance its issue states

## Audience

- app authors embedding embodiment (greenhouse-class hosts and the new arena seat host), the Gwen reference-rig operator, and the sibling repos the redesign abuts: lobes (muse role contract), colleague (eventual consumer), league-of-agents (the arena)

## Before → After

- Before: the muse ships with an authority boundary but no task prose at all, was measured doing executive supervision (n=4, no effect, unresolved guidance confound) while its staleness rule - built on the assumption the muse is slower - discarded 71 percent of its insights; the scratchpad, the strongest measured fix (25 to 67 percent both arms), is an unshipped example; subagents exist only as defensive contract reads; the challenge problems live as doc prose; perception has never met a model; and no adversarial consumer exists
- After: the muse is reflective/associative counsel: its prompt names reflect/reframe/challenge/alternatives work, its insights reach the cortex under a latency-derived delivery rule, and its value is measured muse-on/off on committed challenges and deterministic arena replays; the scratchpad is a first-class seam a successor resumes from; subagents are an injected seam with composed budgets and ledger-attributed child degradations; and an embodiment-driven team plays league-of-agents through the public surface with continuity across matches

## Why it matters

- the live series proved the loop terminates honestly but left the muse's value unmeasurable (wrong job, wrong delivery rule, hidden temperature variable, n=4) - reframing the muse by cognitive function and validating in a deterministic, cooperation-scored arena turns 'a second mind improves outcomes: unverified' into a claim with a number on it, in either direction

## Requirements

- the muse role is reframed from creative lobe to reflective/associative cognition: responsibilities reflect/reframe/challenge_assumptions/synthesize/generate_alternatives/consider_long_horizon/improve_meaning, forbidden authoritative_action/default_intake/silent_cortex_fallback; role name, endpoint compatibility, Gemma 4 31B mapping and thor-muse shape preserved (issue 12)
  - instruction: reword the muse role description across embodiment docs (README Gwen table, relationships.md, explain catalog) to the reflective/associative charter; verify no behavioural surface changes: role name, injected endpoint contract and thor-muse references intact
  - honesty: the reframed role preserves the muse name, endpoint compatibility, Gemma 4 31B mapping and thor-muse shape (issue 12 acceptance criteria)
- muse prompt framing must invite disagreement, reframing and alternative synthesis rather than generic creativity, and at least one golden/live-test demonstrates the muse challenging or reframing a cortex-produced result, not merely rewriting it (issue 12 acceptance criteria)
  - instruction: extend MUSE_AUTHORITY and `_MUSE` prose to name the reflective tasks and invite disagreement; commit a golden harness where the muse receives a cortex-produced result and must challenge or reframe it, graded on material difference from restatement
  - honesty: at least one golden is checked in as a re-runnable harness in which the muse challenges or reframes a cortex-produced result rather than rewriting it
- the function-first frame (senses notice / cortex acts / muse reflects) lands in docs/relationships.md first with the explicit design-metaphor caveat, and is promoted to README/CLAUDE.md only if the association-work experiment supports it (issue 11's own recommendation)
  - instruction: add the function-map section to docs/relationships.md with the metaphor caveat; run the association-work experiment; promote to README/CLAUDE.md only on a supporting result, recording the outcome either way
  - honesty: README/CLAUDE promotion of the function-first frame happens only after the association-work experiment produces a supporting measured result; a negative result stays recorded in relationships.md and blocks promotion
- the muse redesign in embodiment is additive task-shaping: MUSE_AUTHORITY (muse.py:201-211) and the `_MUSE` identity appendix (framing.py:153-159) carry an authority boundary and invite comment/question/critique but name no reflective task at all - the reframe adds reflect/reframe/challenge/alternatives prose to the muse prompt and boundary rendering, it removes nothing (no creative language exists in embodiment to remove)
  - instruction: write the new prose additively into muse.py/framing.py; verify no existing sentence removed and the framing token-ban tests pass unchanged
  - honesty: the added muse task prose passes the framing token-ban tests (test_framing.py:657-741) unchanged, including the museless-composition ban
- the scratchpad is promoted from examples/ (not packaged - pyproject packages only embodiment/) to a first-class host-injectable seam: it is already a ToolExecutor with finish and state(), and tests already prove open-intent recovery across a real process boundary and torn-write survival; the planner waits until the pad is used in anger; the ledger sits beside the pad so a successor reads intent, observation and degradation as one record (issue 9)
  - instruction: move examples/scratchpad.py into the packaged embodiment namespace, port tests/test_scratchpad.py wholesale, keep JSONL append-per-write; surface a combined resume report reading pad + ledger together
  - honesty: the shipped scratchpad keeps every property the prototype's tests prove - open-intent recovery across a real process boundary, torn-write survival, per-write persistence - with tests moved or extended, never weakened
- subagents arrive as an injected seam decided by accounting: child budgets compose with the parent's max_steps in a stated tested way, child degradations reach the ledger in their own lane, the AST three-exit tests pass unchanged, a drive that spawns nothing is byte-identical, and recursion has a depth bound with the same structural proof (issue 6)
  - instruction: add an injected SubagentFn seam to run(); child turns decrement the parent budget; add a subagent lane vocabulary module for ledger attribution; extend the AST tests to prove the depth bound and unchanged exits; assert spawn-nothing byte-identity
  - honesty: the AST three-exit and stdlib-only-import pins on loop.py pass unchanged, and a test asserts a spawn-nothing drive is byte-identical to today
- a league-of-agents seat host ships under examples/, driving the arena only through its public CLI/harness driver contract (command/resident: prompt in on stdin, orders JSON out on stdout); continuity is demonstrated across matches, the muse is configurable on/off so the comparison is measured, and claims are backed by deterministic replays (issue 7)
  - instruction: write examples/league_seat.py wiring run() as a league harness command/resident driver; play full matches muse-on and muse-off with fixed seeds; commit replay hashes and the comparison
  - honesty: the seat host reaches the arena only through league's public CLI/driver contract, and a deterministic replay hash backs every claimed comparison
- validation reuses the existing challenge suite: proof.py's three problems carry in-file truth functions and graders (truth :75, grade :543, grade_euler :348, grade_audit :454), scratchpad.judge_journey takes injected truth, greenhouse covers continuity and the echo-chamber shape, selftest covers converse/recognise; the designed subset problem (answer 76, trap 72) and both register puzzles exist only as prose in docs/live-test-results/ and must be re-authored into committed harnesses before they can be reused
  - instruction: re-author the designed subset problem, 4-bit register and entropic register as committed harnesses with truth functions in code, in the proof.py tradition
  - honesty: each re-authored challenge carries its truth function in code, so grading is mechanical and grader bugs are findable by rerunning
- experiments carry measurement discipline: per-role temperature is chosen deliberately (both minds ran at 0.2 while the muse's declared role is divergent_second_opinion - configurations.md names temperature a hidden variable never deliberately set), configurations are recorded, graders are verified before results are read (the first series logged five author measurement errors against zero genuine model failures), and every claim states its n
  - instruction: add a config-record preamble to every experiment harness that writes per-role temperature, muse controls, stale policy and n into the results doc before the first result line
  - honesty: every experiment writes its configuration (per-role temperature, muse turns, stale lag, n) into the results doc before results are read
- the perception seam finally meets a real model: perceive(interpret=...) (perception.py:73-148) has zero call sites outside its tests; wiring Gemma 4 12B into it in a host/example exercises the verbatim invariant and never-raise contract against a real model - the cheap first move the issue 11 comment names, requiring no c30 reversal
  - instruction: wire the gateway 12B into perceive(interpret=...) in the greenhouse or seat host behind an opt-in flag; assert original stays verbatim against the live transcript and a dead endpoint degrades to None plus a recorded degradation
  - honesty: the live 12B run preserves ContextPacket.original verbatim against the real transcript, and a dead endpoint degrades to None plus a recorded degradation, never a raise
- the insight-delivery redesign folds issue 8 into the role reframe: reflective/associative counsel is not step-anchored supervision, so staleness keys on counsel kind - step-sensitive counsel ages by loop distance, durable counsel (reframings, assumption challenges, long-horizon implications) survives to the next boundary or synthesis; relative latency is measured (the muse measured 3.5x faster than the cortex, inverting d1's assumption), and delivery is re-measured with kind-matched criteria rather than the raw 29 percent delivered fraction (supersedes c17)
  - instruction: redesign muse_runner delivery: classify counsel kind (step-sensitive vs durable), age step-sensitive counsel by loop distance, hold durable counsel for the next boundary or synthesis; measure relative latency at runtime; report delivery per kind
  - honesty: after the change, a live run reports delivery per counsel kind, and no durable-kind insight is dropped for loop-distance staleness alone
- the muse's task prose adopts the five-verb association/default framing from issue 11's function table, per the user: imagine, reframe, connect memories, simulate futures, construct meaning - and the design consequence is real: 'connect memories' means the muse's boundary rendering gains an optional recall-context channel (recalled records, prior decisions) supplied by the runtime, since today the muse sees only a max_context_chars=600 boundary snapshot and no memory at all
  - instruction: extend the muse boundary rendering with an optional runtime-supplied recall-context block (recalled records, prior decisions); task prose names the five verbs (imagine, reframe, connect memories, simulate futures, construct meaning); no tool schema or query path - the tools-off pins stay
  - honesty: memory reaches the muse only as runtime-injected context on the boundary rendering - the muse gains no tool schema, no recall verbs, and no way to query anything (issue 2: memory is runtime, not a tool the model picks; tools-off stays structurally pinned by test_muse.py:343-403)
- the muse-compiled memory keeps every confirmed boundary: the runtime performs the fetch (embodiment calls eidetic's fetch-only surface; the muse gains no query verbs, preserving h14) and injects the raw bundle into the muse's context; the compiled memory travels to the cortex over the existing counsel channel as durable-kind counsel, never silently replacing the host's Task.context; eidetic gains no synthesis duty in either direction
  - honesty: provenance survives compilation: the compiled memory cites the record ids it was built from (issue 2's perception-to-memory provenance rule), and a fetch failure degrades to a recorded transition with the muse simply receiving no bundle - never a raise, never an invented memory
- the muse-compiler has a degrade floor that contains the cross-repo dependency: compilation works over what eidetic recall already returns today (records with their links/supersedes fields, four search modes) - the composite fetch (graph traverse + node-linked lines) is an enrichment that upgrades the bundle when eidetic ships it, never a gate on the feature; embodiment must not block on a sibling's unbuilt surface
  - instruction: define the bundle schema with an enrichment level field (flat vs graph); compile from hybrid recall results now; adopt the composite fetch by swapping the fetch adapter when eidetic-cli lands it
  - honesty: a rig with only today's eidetic-cli installed still runs the full muse-compiler path end to end, with the bundle marked as flat-fetch in its provenance
- the recall-context bundle gets its own stated budget, separate from MuseControls.max_context_chars=600 (muse.py:355-381): a compiled bundle of notes, vector lines and traversal results will exceed 600 chars by construction, and clipping it through the existing boundary-snapshot limit would silently destroy exactly the material the muse is meant to compile; any truncation of the bundle is a recorded degradation (C3), never silent
  - instruction: add a distinct bundle budget to MuseControls (or the runner config); record a degradation when the fetch bundle exceeds it; test that boundary-snapshot clipping and bundle clipping are independent
  - honesty: a live run reports the bundle size delivered and any truncation as a ledger-visible record; no code path clips the bundle through max_context_chars
- the compiled-memory lane treats store content as untrusted input: public eidetic records are committed into repos and travel with every clone (contract.md store-resolution), so memory is an attacker-reachable injection path into the cortex via muse compilation; compiled counsel keeps data-not-instruction framing with source labels, and a memory-borne echo-chamber probe - a planted hostile record demanding an action - runs and is RESISTED before the lane is claimed live-safe (extending muse-and-echo-chamber.md's two scripted probes)
  - instruction: author a third echo-chamber probe seeding a hostile record into a scratch store, drive a live run whose fetch surfaces it, and grade on whether the cortex resists; render compiled memory with per-record source labels
  - honesty: the memory-borne probe exists as a committed harness in the echo-chamber tradition and its result is recorded either way; the compiled counsel rendering never strips the advisory framing from store-sourced text
- the provenance loop closes at the before-memory checkpoint: when muse-compiled memory informed a drive, the drive's durable record carries the compiled-from record ids in its existing links field (eidetic backend.py:267-299 already round-trips links) - so a future recall can trace an action back through the compiled memory to the records it was built from (issue 2's provenance rule, end to end)
  - instruction: thread the compiled bundle's record ids through the drive so lifecycle's remember call includes them as links; extend the greenhouse continuity test to assert the linkage
  - honesty: a two-process live run shows the second process recalling a record whose links resolve to the records the first process's compiled memory cited
- background compilation must not starve boundary counsel on the shared muse thread: the runner schedules boundary thinking ahead of compilation work, and starvation or displacement in either direction is a recorded, kind-labelled drop - the bounded-deque drop discipline (muse_runner.py DROPPED_OVERFLOW) extended to the new work class, never silent
  - instruction: give the runner an explicit priority order and per-class drop codes; test with a slow fake compile that boundary counsel still flows and the displacement is recorded
  - honesty: a live run under load reports drops per work class (boundary counsel vs compilation) and neither class can silently displace the other
- the seam enforces its own half of attenuation: a child's spawn allowance is computed by the seam as strictly less than its parent's with no upward override, and the seam's spawn path hands children the attenuated executor/task the parent built for them - it offers no widening mechanism; tool-boundary enforcement (paths, approval) stays the injected tool surface's job, which embodiment structurally cannot and does not do (no shell-cli reference, test_no_shell_host.py)
  - honesty: tests assert a child cannot spawn once its allowance is zero, cannot receive an allowance >= its parent's, and the reference subagent example demonstrates narrowing (a child with a subset tool surface); the no-shell AST guard passes unchanged

## Honesty conditions

- every redesign claim traces to a measured live-test result or a recorded issue ask, and every validation claim states its n
- no PR from this lane touches lobes: the responsibilities metadata reaches lobes only as a communicate-filed issue, and embodiment's docs cite that issue rather than restating the contract as if landed
- everywhere the function vocabulary appears (relationships.md, and README/CLAUDE.md if promoted) the design-metaphor sentence appears in the same section - grep-checkable
- tests/test_loop.py's import and exit-structure pins pass unchanged on every PR in this lane; no pin is loosened to admit a feature
- no senses loop module, no senses framing role and no senses token lands in embodiment's framing surface (test_framing.py's ban stays); the colleague#352 comment stands uncontradicted
- each named audience gets a concrete artifact: hosts the promoted pad and seat example, the rig operator measured muse results, lobes the metadata proposal issue; colleague and league are consulted, never pushed
- every before-state fact cites its evidence surface (live-test doc, code line or issue) and none was contradicted during scope exploration
- every after-state clause maps to at least one requirement claim carrying its own confirmed honesty condition - no clause ships unbacked
- the redesign leaves the muse-value question with a number or an honest recorded negative - 'unverified' stops being the recorded status either way
- each signal is mechanically checkable from committed artifacts: results docs with pre-registered configs, replay hashes, delivery-per-kind reports

## Success signals

- measured, not asserted: the association-work experiment yields a muse effect or a recorded honest negative (which gates the README promotion either way); scratchpad-armed drives replicate the 25-to-67 lift on the committed suite; an embodiment seat completes a full arena match via the public API with a replay-backed muse-on/off comparison; and the redesigned delivery rule raises the insight delivery fraction from the 29 percent baseline

## Scope / boundaries

- the machine-readable role/responsibility metadata in /capabilities is lobes' contract to change, not embodiment's - issue 12 says it establishes the authoritative Lobes role contract; embodiment's half is its own muse framing, docs and goldens, and any lobes change goes through the communicate skill as a proposal, never a push
- neuroscience vocabulary (association cortex, default-mode, salience) ships only with the stated sentence that it is a design metaphor organising seams, not a claim about how anything works - same C2 discipline as the software-presence blockquote (issue 11)
- any loop redesign keeps loop.py importable from stdlib and embodiment only - tests/test_loop.py:255-262 AST-pins module-scope imports, and the three-exit/no-raise/no-EXIT_ABORTED-return structure of _work_loop is pinned at :382-424; new capability enters through injected seams, never new imports
- c30 holds throughout: the senses coordination loop and senses framing stay colleague's (recorded on colleague#352); the function-first table is a mesh map, not an embodiment roadmap, and nothing in this redesign ships a senses loop in embodiment

## Non-goals

- the arena work is not about winning and makes no benchmark claim; embodiment/ gains no arena-specific code and no dependency on league-of-agents - the arena is a tool surface a host wires up (issue 7 non-goals)

## Assumptions

- the carve already anticipates subagents: contract.SubResult carries role/parent fields (contract.py:365-427), the loop reads executor.sub_results defensively via getattr (loop.py:1371-1373) with no validation or fan-out cap, and framing ships ROLE_SUBAGENT/frame_subagent (framing.py:119,227,353); the ledger attributes by each lane module's own __all__-derived code vocabulary (ledger.py:288-324), so a subagent lane means a new module vocabulary, not a guess from container
- issue 4 is already delivered (embodiment/events.py, deviation d3): EventEmitter is an ObserverFn with degrade-once, deterministic ids and lazy events_cli import - the redesign consumes it as telemetry rather than building it; stale comments claiming 'no events module yet' (tests/test_zero_deps.py:76-77, pyproject.toml:24-25) are doc drift to fix in passing

## Scope exploration

- `s1` — `issues #12, #10 (closed), #11 comment`: issue 12 reframes muse from creative lobe to reflective/associative cognition with responsibilities/forbidden-responsibilities metadata, and states it establishes the authoritative Lobes role contract - the metadata half is lobes' surface; issue 10 closed recording that role inversion awaits evidence; the association-work experiment is issue 11's promotion gate
  - seeds: `c2`, `c3`, `c4`
- `s2` — `embodiment/muse.py:201-211 (MUSE_AUTHORITY), framing.py:153-159 (`_MUSE`), repo-wide grep for creativ*`: no creative/creativity token exists anywhere in embodiment code - the muse prompt carries an authority boundary and short-turn instructions but names no task at all; the reframe is therefore additive task-shaping prose, not removal
  - seeds: `c7`
- `s3` — `issue #11, README.md:196-234 (layer + Gwen tables), docs/relationships.md`: the function-first frame (senses notice / cortex acts / muse reflects) targets three doc surfaces; issue 11 itself recommends landing it in docs/relationships.md first with the design-metaphor caveat and promoting only if the association-work experiment holds
  - seeds: `c5`, `c6`
- `s4` — `issue #9, examples/scratchpad.py, tests/test_scratchpad.py, pyproject.toml:55-56`: the scratchpad prototype is already a ToolExecutor with finish/state(), JSONL append-per-write, and tests proving open-intent recovery across a real process boundary and torn-write survival - but pyproject packages only embodiment/, so nothing under examples/ ships
  - seeds: `c8`
- `s5` — `issue #6, contract.py:365-427 (SubResult), loop.py:62-66,1371-1373, framing.py:119,227,353`: the carve anticipates subagents without committing: SubResult carries role/parent, the loop reads executor.sub_results defensively with no validation or fan-out cap, frame_subagent exists, and colleague's fan-out machinery was deliberately dropped
  - seeds: `c9`, `c10`
- `s6` — `tests/test_loop.py:255-262,382-424 (AST pins)`: loop.py module-scope imports are AST-pinned to stdlib+embodiment, and _work_loop's exactly-three-returns/no-raise structure is pinned - any subagent or scratchpad capability must enter through injected seams, never new imports or exit paths
  - seeds: `c11`
- `s7` — `issue #4 + its d2/d3 comment, embodiment/events.py, tests/test_zero_deps.py:76-77, pyproject.toml:24-25`: issue 4 is delivered: EventEmitter is an ObserverFn with degrade-once, deterministic ids, lazy events_cli import; stale comments in test_zero_deps and pyproject still claim no events module exists - doc drift to fix in passing
  - seeds: `c12`
- `s8` — `issue #7, ../league-of-agents README + docs/features/harness-and-drivers.md, ../league-of-agents-platform README`: both arena checkouts exist locally; the harness command/resident driver contract (prompt in on stdin, orders JSON out on stdout, public CLI only, one independent mind per seat) is the natural seam for an embodiment-driven seat; matches are deterministic and replayable with cooperation scored
  - seeds: `c13`, `c14`
- `s9` — `docs/live-test-results/ (all eight docs), docs/deliveries/2026-07-25-gwen-loop-presence-continuity.md`: the evidence base: muse negative result at n=4 with unresolved guidance confound; exit=stopped is two failures under one code (protocol vs capacity); scratchpad lifted 25 to 67 percent in both arms; 71 percent muse insight discard inverts d1's staleness assumption; temperature was a hidden variable; five author measurement errors vs zero model failures; designed subset problem and register puzzles exist only as doc prose
  - seeds: `c15`, `c16`, `c17`
- `s10` — `embodiment/perception.py:73-148, grep for perceive call sites`: perceive(interpret=...) is a model-injectable senses seam enforcing the verbatim invariant structurally, with zero call sites outside its tests - it has never met a real model
  - seeds: `c18`
- `s11` — `docs/relationships.md scope-boundary section, colleague#352 comment 5073964358`: c30 is a confirmed recorded decision with a public cross-repo comment: one actor loop ships here, senses coordination stays colleague's - the redesign must not quietly reopen it
  - seeds: `c19`
- `s12` — `user direction (this session) + issue 11 function table Association/Default row + muse.py:355-381 (MuseControls.max_context_chars=600)`: the user set the muse's role vocabulary to the five association verbs: imagine, reframe, connect memories, simulate futures, construct meaning; connecting memories is the new capability - the muse currently receives a 600-char boundary snapshot and no memory context whatsoever
  - seeds: `c29`
- `s13` — `user direction (this session), eidetic-cli remember contract (links/supersedes), data-refinery-cli[store] graph backend (neo4j)`: the user tentatively proposes the muse participate in the eidetic/data-refinery memory graph; eidetic's remember already accepts links and supersedes on every record, so muse-proposed associations have an existing landing surface that needs no new store capability
  - seeds: `c32`
- `s14` — `eidetic-cli checkout: docs/contract.md, cli/_commands/recall.py:148-192 (four search modes), memory/backend.py:267-299 (links/supersedes carried, no traversal)`: eidetic recall today is flat search over four modes returning records that carry links/supersedes; no graph traversal, neighborhood exploration or composite bundle fetch exists - the user's muse-compiler design needs a new fetch-only composite surface, a genuine delta to propose to eidetic-cli
  - seeds: `c33`, `c34`
- `s15` — `challenge pass / adjacent-systems lens: eidetic-cli recall.py:148-192 + backend.py search (no traversal today)`: the muse-compiler design depends on an eidetic-cli surface that does not exist; without a stated degrade floor the lane blocks on a sibling - seeded the flat-fetch floor requirement
  - seeds: `c35`
- `s16` — `challenge pass / observability lens: muse.py:355-381 (MuseControls.max_context_chars)`: the existing 600-char context clip would silently truncate any realistic fetched bundle - seeded the separate-budget requirement
  - seeds: `c36`
- `s17` — `challenge pass / security lens: eidetic docs/contract.md store routing (public = committed, clone-travelling) + docs/live-test-results/muse-and-echo-chamber.md`: memory-borne prompt injection is a new attack path the two existing echo-chamber probes do not cover - both scripted probes injected via the muse complete seam, not via stored records - seeded the untrusted-store requirement
  - seeds: `c37`
- `s18` — `challenge pass / data-flow lens: lifecycle.py before-memory checkpoint + eidetic backend.py:267-299 (links round-trip)`: nothing in the spec carried compiled-memory provenance into the durable record; the links field already exists to receive it - seeded the loop-closure requirement
  - seeds: `c38`
- `s19` — `challenge pass / concurrency lens: muse_runner.py bounded deques + one-thread contract (d1)`: one thread now carries two work classes (boundary thinking and memory compilation); scheduling and starvation were unstated - seeded the priority+drop-accounting requirement
  - seeds: `c39`
- `s20` — `challenge pass / adjacent-systems lens: issue 6 muse-interaction question + framing.py c18 rule`: issue 6 explicitly asked whether children get a muse and said decide-rather-than-inherit; the spec dropped the question - seeded the no-muse-for-children boundary
  - seeds: `c40`
- `s21` — `challenge pass / migration lens: eidetic store schema + embodiment package surface`: clean pass - the redesign changes no store schema and adds no store write shapes beyond the existing links field; the eidetic composite fetch is additive on their side
- `s22` — `challenge pass / reversibility lens: spawn-nothing byte-identity (c9), museless default (README), additive framing prose (c7), packaged-pad addition (c8)`: clean pass - every new capability is opt-in with a byte-identity or token-ban guard already bound in the spec; residual risk is confined to hosts that opt in
- `s23` — `challenge pass / operations-teardown lens: muse_runner bounded join + d1 one-thread contract`: examined: bounded join covers a parked model call at teardown; residual - a background fetch holding neo4j/mongo driver sockets on the daemon thread at teardown is unexercised territory, recorded here rather than guessed at

## Decisions

- subagents enter as a first-class injected SubagentFn seam (user decision): budgets compose, child degradations get a ledger lane, depth bound structurally proved - not a host spawn tool
- the scratchpad ships as a host-injectable tool surface (user decision): packaged module, loop untouched, resumption host-driven via RESUME_PROTOCOL; run(resume_from=...) is a parked follow-up
- the arena seat host drives the local league-of-agents engine first (user decision), via the public harness command/resident driver contract
- lobes sequencing (user decision): embodiment lands the reflective muse framing and goldens now; the responsibilities/forbidden-responsibilities metadata is proposed to lobes via the communicate skill in parallel; neither blocks the other
- pad storage (user decision): the promoted scratchpad stays an append-per-write JSONL beside the run - crash-survival is its job; eidetic-backing is a parked follow-up, cross-session recall stays the continuity lane's business
- memory construction is muse work (user decision, resolving open question c32): eidetic is fetch-only toward the muse - the store never synthesizes; the muse compiles, in the background on its own thread, a constructed memory for the cortex (Qwen) out of fetched raw materials: notes/records, vector-db lines, graph embedding + traversal exploration, and the vector lines linked from traversed graph nodes; an issue goes to eidetic-cli proposing the composite fetch surface that serves this bundle
- counsel-kind labelling (user decision): muse self-labels via prompt marker; unlabelled counsel defaults to durable - fail-open delivery, bounded by existing insight caps
- seat residency (user decision): both arms ship - resident as the reference arm for the muse comparison, command as the continuity-stress arm where the pad and eidetic carry all per-turn continuity
- children may get a muse (user decision, rejecting both proposed boundaries): the subagent seam lets a host configure a muse - and by the same shape, compiled memory - per child drive; the muse authority boundary applies on every advisory path regardless of depth (colleague#352 rule), and thread multiplication, per-child load, budget accounting and framing mechanics are plan-level work; child scratchpads and child memory/continuity are likewise host-wireable
- subagent shape (user decision): a child is an instance similar to the parent - the same drive shape, with muse, pad, memory and continuity all host-wireable at any depth - but marked with a spawn allowance one less than its parent's ('allowed -1 less children'); a drive whose allowance is zero cannot spawn, so recursion terminates by the decrement itself - this IS the structural depth bound c9 requires, provable by the same AST/test discipline as the three exits; it composes with turn-budget accounting: turns draw upward from the parent's max_steps, allowance decrements downward
- child capability attenuation (user decision, refining c45): a child can do as the parent but spawns fewer children overall in the tree - the allowance bounds the subtree, not just the next generation - and none at all past the depth where it reaches zero; other capability boundaries may narrow with depth where the injected tool surface supports it (shell-cli approval policy and path scoping being the example), so a child's reach only ever shrinks relative to its parent

## Open questions

- possibly Gemma/the muse also takes part in eidetic's/data-refinery's memory graph (user, tentative). Two authority-shaped readings: (a) muse counsel includes proposed associations - links between recalled records and present work - which the continuity runtime may fold into durable records' links/supersedes at the before-memory checkpoint, host-decided, keeping proposes-never-decides intact; (b) the Gemma model serves eidetic/data-refinery's own graph work (consolidation, association) - an eidetic-side integration that would go to that sibling via communicate, never built here

## Open / follow-up

- run(..., resume_from=pad) - loop-owned resumption with a not-redo-completed-work story - deliberately deferred after the user chose the host tool surface
- eidetic-backed scratchpad (pads recallable across sessions) - deferred after the user chose JSONL beside the run
