# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`embodiment` is the **loop + presence layer** of the AgentCulture stack: the
agentic loop that gives an app an embodied AI presence. An app installs it
(imports the package) or wraps with it, supplies a **model seam** plus **IO
callbacks**, and gets the perceive → decide → act loop and the **presence pump**
that keeps the app feeling attended-to between acts. The loop is written once
here and imported, not reimplemented per host.

**Library first, CLI second.** The `embodiment` console script exists (and
carries the agent-first introspection verbs every Culture sibling has), but the
value is the importable seam an app depends on.

### Honest status — read this before you plan work

**The extraction is largely done.** This section previously said the loop and
presence pump were not in the repo; that stopped being true partway through the
`gwen-loop-presence-continuity` build, which has since merged to `main`
(PR #13). The core of what is checked in:

| Module | What it is |
|--------|------------|
| `loop.py` | The bounded tool loop, extracted from colleague's 4463-line `loop.py` (~42% of its statements). Termination proved *structurally* by AST tests, not just behaviourally. |
| `presence_engine.py` | The pump — no TTY, no thread, no clock. Its advisory seam (`MuseSeam`) is structural and survived the muse's archival; it never imported `muse`. |
| `presence.py` | The pure policy half (cadence + clarify), ported byte-faithfully. |
| ~~`muse.py`~~ | The bounded, thread-free muse **thinking** loop (deviation `d1`). **ARCHIVED** 2026-08-03 — readable, off the curated surface. |
| `perception.py` | Verbatim-invariant intake + never-raise. |
| `continuity.py` | The eidetic/coherence seam (rewritten under `d2` — see C1). |
| `contract.py` | The carved data contract. |
| `context.py` / `media.py` | Windowing, degradable classification, media handling. |
| `identity.py` | Resolved identity, mirroring colleague's order. |

That table is not exhaustive and it has already drifted behind once. Everything
this section used to list as "still to land" — the threaded muse runner
(`muse_runner.py`, since archived alongside `muse.py`), Gwen prompt framing
(`framing.py`), the continuity lifecycle
checkpoints (`lifecycle.py`), the degradation ledger (`ledger.py`) and the demo
app (`examples/greenhouse.py`) — is checked in, and the `events.py` emitter
landed after it under deviation `d3`. Still outstanding: colleague's answer on
the seam proposal, which is filed and open as
[colleague#358](https://github.com/agentculture/colleague/issues/358) — C1b is
theirs to resolve, not ours — and depth in the live evidence, since `d4`'s live
bar was met but every result is n≤4 on one rig with one model pair. Check
`docs/plans/` and `.devague/deliveries/` for the live state — the deviation
ledger (`devague deviate --list`) records every approved departure from the plan
and is the authority on what changed and why.

**What the 0.11.0 cycle added**, so this section does not drift behind a third
time. One package module, and the rest in `examples/` and `tests/`:

| What | Where |
|------|-------|
| The drone tier — `create` / `evoke` / `list`, smoke-before-save, staleness refusal, a content-hashed evocation ledger. **Opt-in and off**: the design it implements is unvalidated | `drone.py`, `cli/_commands/drone.py`, `.claude/skills/drone/` |
| Arm **B** — the worker as a *tool*: no loop, no turn, no goal. `B0`/`B1` differ in exactly one field, so `B0` is a control rather than a second experiment | `examples/arch_hive.py` |
| Arm **P** — the cortex compiles a policy once and the policy plays, graded in a network-less jail, with random / hand-written / no-op controls | `examples/arch_policy.py` |
| Timeout bounds **enforced in CI** — every clock derived from `max_tokens / rate`, recomputed from a dated committed rate config, with a test-of-the-test | `tests/test_timeout_bounds.py`, `tests/rate_config.py` |
| Streaming as the default transport for cortex dials, both bounds derived (queue-aware first-chunk, then inter-chunk idle) | `examples/worker_seam.py` |

The load-bearing lesson of that cycle, because it recurred four times: **a clock
sized against the wrong quantity silently becomes the measurement.** A 300 s
request timeout censored a completion-length distribution; a 60 s fan-out
deadline bounded a whole unit drive at 1/248th of it; a 20 s retry backoff timed
*inside* the call it retries corrupted three separate results; and a 600 s
`GATEWAY_READ_TIMEOUT` — **in the lobes process, where no client value can reach
it** — cost a live series two repetitions. Three of the four were invisible in
the record at the moment they fired. Hence the CI bound, and hence streaming:
with chunks flowing, generation length stops being the binding quantity at every
hop at once.

**What the 0.12.0 cycle added, and the verdict on it** — written at close-out
rather than left for the next reader to reconstruct, because this section has
now drifted twice and the third time is the one that stops being a mistake and
starts being a habit. The cycle built the **strategic scope governor**: a level
of authority *above* the acting loop.

| What | Where |
|------|-------|
| The strategist tier — typed, versioned, supersedable directives owning objectives, priorities, constraints and ownership; a bounded review loop; a background runner thread; and `run_scoped` composing `loop.run` with a **zero-line diff** to `loop.py` | `scope.py`, `strategist_runner.py`, `scoped_run.py`, `scope_events.py` |
| Two persistence lanes — durable across drives, session-scoped within one process — structurally distinct, every record naming its lane | `scoped_run.py`, `ScopePersistence` / `ScopeSession` |
| ScopeBench — machine-gradable episodes, an exact enumerating oracle with a brute-force test-of-the-test, arms as data, committed seeds, and a seven-condition verdict rule fixed before any dial | `examples/scope/`, `examples/scopebench_live.py` |
| A Stage 3 live host — interactive REPL, scripted replay, and a matched ungoverned control | `examples/scope_live_session.py` |

**The verdict, stated the way the results came back rather than the way the
delivery would look best.** The *mechanism* held on every structural check it
was possible to make: directives arrive as events inserted at a safe boundary,
the system prompt is never rewritten mid-drive, ordinary tool steps never become
strategic reports, interaction never blocks on background review, a killed seam
degrades visibly and the actor continues under the last valid directive, both
persistence lanes behave as specified, and an unarmed governor is byte-identical
to `run()`. The *value* did not:

- `t11`'s ScopeBench Stage 1 returned **`INCONCLUSIVE`**
  (`docs/live-test-results/scopebench.md`). Condition 2 — the one that separates
  a real strategic effect from a stronger actor repairing bad decisions — held
  strongly, on 6 of 6 scenario families at sign margins +3/+5/+4/+4/+3/+5
  against a pre-registered threshold of 2. But two of the seven conditions read
  from a Stage 2 that was not dialled, one `ABSENT` forces the verdict under the
  committed rule, and the `A2` layer control that would have removed *the gain
  is just the extra layer* was **not measured** — 32 of its 36 cells fell below
  the protocol floor.
- `t14`'s live session 1 is the newer and harsher evidence
  (`docs/live-test-results/scope-live-session-1.md`). On a matched pair of runs
  replaying one identical script, the governed arm spent **189.6 s and 3663
  strategist completion tokens to apply ZERO directives** and returned a
  materially identical answer to the ungoverned control — whose justification
  was arguably the better of the two, being the only one to name the sensor id.
  Across the interactive session **69.5% of the strategist's 28,318 tokens
  bought restatement or nothing**, and non-intervention **FAILED**
  (embodiment#68). n=1, one rig, one model pair: a report, not a measurement.

So the strategist ships **opt-in and off**, on exactly the rule that kept the
muse out of the reference rig after `t18` — *an `INCONCLUSIVE` result leaves the
shipped rig untouched*. `tests/test_governance.py`'s
`TestStrategistShipsOptInAndOff` pins the structural fact that makes it true:
`ScopeGovernor()` — what `run_scoped`'s own `governor=None` default resolves
to — is unarmed, and no module in the `embodiment` package constructs an
armed one; a default flip needs a fresh ScopeBench verdict behind it, not a
quiet edit. `TestTheseGuardsCanFail` proves that guard can go red. Eighteen
issues were filed against the tier this cycle (#52, #54–#74). Two of them are
**host-facing traps** that
belong in the strategist's documentation and not only in a tracker, and are now
written up in the README: **#62** (a host that starts its actor under its own
initial directive and then arms a strategist has an *unseeded issued chain*, so
every protocol-obedient directive is refused `scope-directive-unknown-supersedes`
and dropped — seed the runner's chain, as `examples/scope_live_session.py`
does) and **#58** (`SCOPE_AUTHORITY` never stated the `scope_id`-must-be-new
rule it is graded on; 47 of 93 proposals in one ScopeBench arm were refused as
duplicates, and both of the live session's completed reviews were thrown away
this way). **#58's text is fixed** as of the `config-not-minds-strategist`
cycle — all four admission rules are stated, and `tests/test_scope.py` maps
each admission refusal code to the phrase stating it, so a rule added to the
register without a prompt update fails the suite. The measurements above were
all taken under the old text and have **not** been re-run; treat them as the
record of what the gap cost, not as the current rate.

The lesson worth carrying forward, beside 0.11.0's clock lesson: **a mechanism
that holds on every structural check can still buy nothing.** The structural
suite here was strong enough that reading "everything passed" as "it works"
would have been the easy move, and the only matched control in the record says
the governed arm paid 189.6 s and 3663 tokens for a materially identical
answer. Structural proof and measured value are different claims. This repo's
record is worth something only because it publishes the second one when it
comes back negative.

**What the 0.13.0 cycle added, and where the evidence stood when this was
written** — recorded *during* the cycle rather than at close-out, deliberately,
because the value series was still dialling when the docs task landed and a
section that waits for a verdict is a section that drifts. The cycle rebuilt the
tier on a different premise: **the strategist changes configuration, not
minds.** Advice can be ignored; configuration is simply what the seat runs
under, so *unawareness* replaces persuasion as the mechanism.

| What | Where |
|------|-------|
| The typed change unit and the authority lattice **as data** — seven targets, three origins, refuse-whole on any unknown or extra key | `config_change.py` |
| The host-declared capability catalog: tools/permissions changes **select** among ids the host named and can never **mint** one. There is deliberately no `from_executor` constructor | `capability.py` |
| Propose → verify → apply with per-seat quiescence — a change applies only when its seat is idle *and* its per-type suite passed, so configuration identity is constant within any single drive | `config_lifecycle.py` |
| The config ledger, its five event kinds, and a schema-versioned payload that **fails closed** against advisory-era persisted state | `config_ledger.py`, `config_events.py` |
| Revert-to-baseline as an *ordinary change* rather than an inferred inverse, plus `RatchetGuard` re-checking cumulative drift against a **fixed** baseline | `config_revert.py` |
| Effective-config introspection derived from the ledger **alone**; a state the ledger cannot explain is itself a recorded degradation (C3) | `config_report.py` |
| The knowledge block riding **eidetic** — no second store, attribution on eidetic's own `added_by` | `knowledge.py` |
| Reasoning / thread / composition, cited out of the advisory lane rather than imported from it, with `loop.py` still **zero-diff** and the actor's `complete` never wrapped *even when armed* | `config_review.py`, `config_runner.py`, `config_run.py` |
| `SENSES_GROUNDING` + `KNOWLEDGE_ATTRIBUTION` as composable host text — constants a host splices in, never a `frame_senses()` that would cross the colleague boundary | `senses_text.py` |
| The three-tier example host (senses relays → acting seat acts → cortex configures) and its **eight seam traps as data**, reproduced behaviourally | `examples/three_tier.py`, `tests/test_three_tier.py` |
| ScopeBench cycle 2 — a second pre-registration with eight conditions, the ratchet arithmetic, the per-change-type three-way ladder, declared protocol floors and the config arms as data | `docs/live-test-results/scopebench-config-preregistration.md`, `examples/scope/` |
| Two measurements: the acting seat drives `embodiment.loop.run` (n=12/rung, 3 rungs, **36 runs**, 12/12 on every bar, 0 truncations at 16000) and the senses seat sees (image 4/4, motion 4/4, text-only control refuses 0/4, **n=4/cell**) | `docs/live-test-results/worker-toolloop.md`, `senses-vision.md` |

**The verdict, and it is deliberately not one.** The mechanism is proven on
every structural check available: `loop.py` zero-diff pinned four ways, the
advisory lane byte-stable so it can serve as the comparator arm it will be
measured against, the acting seat's model seam never wrapped, 7793 tests
passing. **The value is unmeasured.** The pre-registered series that answers it
was committed before any dial and had published no verdict when this section
was written — so nothing here claims one, in either direction. Three of the
seven change types are out of that bench's reach by design and ship off with a
`not-measured` ladder verdict; the config lane has **no perfect-subordinate
stage at all**, permanently, so its evidence will always be weaker on the one
axis the advisory lane scored strongest; and the live session on the redesigned
tier has not run. Nine deviations (`d1`–`d9`) are recorded and all are still
`proposed` — `d1` (the encoded lattice is *stricter* than the spec's prose),
`d6` (the report's "gate verdict" is the ledger's applied/reverted vocabulary,
not a richer passed/failed/stale) and `d7` (revert cannot restore true
non-existence) change what the docs may claim, and do.

> **Deviation ids restart at `d1` every plan and are not global.** `d1`–`d7`
> therefore exist in both this cycle's ledger and `strategic-scope-governor`'s
> and mean different things in each — the `d5` cited in the rig table below is
> the advisory lane's containment overclaim, while this cycle's `d5` is a
> demotion mapping. Cite the cycle with the id. `devague deviate --list` reads
> the *current* plan's ledger; older cycles live in
> `.devague/deliveries/<slug>.json`.

So the redesigned tier ships **opt-in and off**, exactly as the advisory lane
does, held by `tests/test_governance.py` rather than by memory. The
worker-promotion gate also stays shut: `worker-toolloop.md` *is* a real verdict,
but a bounded one — 36 runs on a hermetic, instant, free tool surface with every
rung at ceiling, measuring the acting **protocol** and not acting **quality** —
while the promotion at stake is to the acting seat of a real rig.
`_WORKER_ROLE_HAS_SUPPORTING_VERDICT` stays `False` until an operator decides
otherwise and cites what they decided on, in one reviewable change.

The load-bearing lesson of this cycle, beside 0.11.0's clock and 0.12.0's
structural-proof-is-not-value: **a strategist can be correctly wired, fully
armed and structurally proven, and still propose nothing — with every counter at
zero and no error anywhere.** Four separate mechanisms produced exactly that
picture: an unseeded issued chain (#62), an admission rule the prompt never
stated (#58), a review boundary that is a *tool-step* boundary so a
conversational turn reviews nothing (T1), and a cadence memory that outlives the
per-drive step index it reads — six drives, one review, 11 of 12 snapshots
skipped (T2). None of the four raises. `armed == True` is therefore not evidence
that a tier is alive; a counter that increments is. That is why this cycle's
example host records its seam gaps **as data** with behavioural tests, and why
the two that yield an incapable tier are called out separately from the six that
are merely under-documented.

Keep this file's claims grounded in checked-in reality. When a section drifts
ahead of what exists, mark it `(planned)` or move it under a roadmap heading —
and when it drifts *behind*, as this one did, fix it.

## Where embodiment sits

| Layer | Package | Owns |
|-------|---------|------|
| Surface | `agentfront` | "Agent First Interface" — CLI / MCP / HTTP / TAUI fronts, the App registry |
| Tools | `shell-cli` | The file-and-shell tool surface + approval policy |
| **Loop + presence** | **`embodiment`** | **The pump: perceive → decide → act, and the presence between acts** |
| Memory / trust | `eidetic-cli`, `coherence-cli` | Durable recall + provenance; drift/consistency signals (issue #2) |
| Product | `colleague` | The coder-agent harness — the opinions, not the plumbing |

Three questions, three layers: *how does a human or agent reach it*
(agentfront), *what can it do* (shell-cli), *what makes it keep going and feel
present* (embodiment).

The same parts also read as a **function** map — what notices, what acts, what
reflects, what remembers, what sequences the rest
([issue #11](https://github.com/agentculture/embodiment/issues/11)). That map
lives in
[`docs/relationships.md`](docs/relationships.md#3-the-function-map--what-each-part-is-for),
together with the caveat it must always ship beside: those names are
design metaphors for allocating responsibility across model seams, not claims
about cognition. It is parked there deliberately, not half-migrated — promoted
into this file and the README only if the association-work experiment (plan task
`t18`) returns a supporting measured result, and an honest negative keeps it in
`relationships.md` with the negative recorded. Until then the layer table above
is the map to work from.

`colleague` is the **first consumer, and this is a refactor** — the code
embodiment will ship already exists inside colleague `1.52.1` and works. The
job is to make it reusable, then **propose** that colleague import it.
colleague's refactor is colleague's to make: file an issue on
`agentculture/colleague`, never push there.

## Identity — embodiment, and the Gwen it ships

Two distinct identities live in this repo. Do not conflate them.

### 1. This agent (the repo's own mesh identity)

```yaml
# culture.yaml
agents:
- suffix: embodiment
  backend: colleague
  model: sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP
```

`backend: colleague` fixes the **resident prompt file** to
`AGENTS.colleague.md` — that is what the mesh runtime reads. This `CLAUDE.md`
is Claude Code's guidance file and is *not* the backend-consistency target.
(The scaffold seed claimed `backend: claude`; `culture.yaml` is authoritative
and the discrepancy noted in issue #1 §6.2 is resolved in favour of
`colleague`.) `embodiment doctor` checks the same two invariants `steward
doctor` does, plus a skills-present warning.

### 2. Gwen — the embodiment we ship

The reference embodiment this repo delivers is **Gwen**: one prompt-visible
teammate produced by cooperating cognitive roles across two model families —
**G**\ from Gemma, **wen** from Qwen. Colleague remains the runtime and agent
type; embodiment is the loop that drives the roles; Gwen is the teammate the
operator actually talks to.

| Concept | Value | Serves |
|---------|-------|--------|
| Runtime | `colleague` | the harness |
| Loop + presence | `embodiment` (this repo) | the pump |
| **Teammate identity** | **Gwen** | what the operator addresses |
| Cortex | Qwen 3.6 27B (`sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP`) | bounded tool loop, repo actions, final synthesis, **final authority** — and, per `d15`, **the only actor** in the shipped rig. The 0.13.0 three-tier *example* host puts a different seat in the acting chair and the cortex above it; that promotion is gated and has not happened here |
| Strategist *(opt-in, off by default — two lanes, **both mechanisms proven, neither value**)* | the `cortex` lobes role (dense Qwen 3.6 27B) | authority above the acting loop. **Advisory** (`scope.py` + `strategist_runner.py` + `scoped_run.py`): typed, versioned, supersedable directives owning objectives, priorities, constraints and ownership — authority-bearing *within* scope, structurally unable to carry a tool, a command or an approval **as data**, and carrying no containment against prose (`strategic-scope-governor` `d5`, embodiment#55). **Configuration** (`config_*.py` + `capability.py` + `knowledge.py`): typed changes to what a seat runs under, delivering nothing to the acting seat at all. Read the 0.12.0 and 0.13.0 verdicts below before treating this row as a capability |
| ~~Muse~~ | ~~Gemma 4 31B~~ | **ARCHIVED 2026-08-03** (embodiment#53, deviations `d2`/`d3`, superseding `c12`/`c32`) — see below |
| Senses | Gemma 4 12B (`coolthor/gemma-4-12B-it-NVFP4A16`) | intake, perception, conversational presence, speak-back; never acts on the repo |
| Ears / voice | Parakeet STT + Chatterbox TTS behind the lobes audio overlay | the realtime lane (below) |

Roles resolve **by name** from a `lobes` gateway's `/capabilities` contract
(`lobes/roles.py` — `cortex`, `senses`, `muse`, `worker`, `embedder`,
`reranker`, `stt`, `tts`), never by parsing model names. `worker` was missing
from that list here and is not a new role: `lobes/roles.py` has declared it all
along, and this repo's own measured series dial it. What the table above records
is that no `worker` row is in the *Gwen rig*, which is the promotion gate's
question and a different one. The 31B muse is opt-in: it needs a
muse-hosting deployment shape (`lobes init --shape thor-muse`), because a 31B
cannot co-reside with the cortex+senses duo on a 128 GB box.

**The reference rig runs muse-off — operator decision, 2026-07-31, recorded
as `d15`.** Qwen is the only actor; Gemma 4 12B stays senses (intake,
perception, speak-back); the Gemma 4 31B muse is **not dialled**.

**And as of 2026-08-03 the muse is ARCHIVED** — operator decision on
embodiment#53, recorded as deviations `d2` (the archival, blast radius
measured) and `d3` (the disposition and the replacement's name), **superseding
confirmed claims `c12` and `c32`**, which both pinned that `muse.py` /
`muse_runner.py` ship unchanged. `d15` made the muse undialled; `d2` takes it
out of the architecture this repo documents and advertises.

Read "archived" precisely, because it is neither of the two obvious readings.
It is **not a deletion**: `muse.py` and `muse_runner.py` stay in the package,
stay importable, and stay green under their own suites, because
`strategist_runner.py` was copied out of `muse_runner.py` *verbatim* under the
cite-don't-import policy and that citation has to keep resolving to a file you
can open. It is **not a docs-only retirement** either: the two modules left
`_SUBMODULES` for `embodiment.ARCHIVED_SUBMODULES`, and the eighteen `Muse*`
names they hoisted left `_LAZY_NAMES` entirely — so `from embodiment import
ThreadedMuseRunner` no longer resolves and neither module appears in
`__all__`. What archival costs is **advertisement, not reach**: a host that
wants counsel names `embodiment.muse` and wires one, exactly as before.
`tests/test_muse_archival.py` holds the whole disposition in executable form.

Two surfaces were deliberately **kept** rather than retired with it, and the
reasons are recorded where a cleanup pass will find them: `ledger.py`'s
`SOURCE_MUSE` / `SOURCE_MUSE_RUNNER` lanes (a wireable lane the ledger refuses
to read is the silent degradation **C3** forbids) and
`tests/test_proof_reporting.py` (it stands behind a *published* live result).

Be precise about what supports this, because the obvious citation is the wrong
one. The model-consolidation head-to-head
(`docs/live-test-results/league-h2h.md`) ranked `full-qwen > mixed >
full-gemma`, but league's outcome metric tied 0–0 in all six matches and the
entire ranking rests on one optional team-message field — it measured
**interface compliance, not play**, and is not the support for this decision.
Nor is the chosen configuration one of the three arms it ran: those all
carried a muse, and a muse-off control does not exist there. What supports
`d15` is the muse-side evidence: `t18`'s three-arm series returned
`INCONCLUSIVE` while measuring real harm (#32, #33); 5 of 13 counsel lines
still never reach the cortex (#29); and `t28` measured a **1.2%** intervention
rate (1 override in 82) for **2.4–4.4×** the token cost. Cost is the one
unambiguous axis — full-Gemma 950 tokens/match against full-Qwen's 18,410
(19.4×) — and it argues *against* Qwen, not for it. This decision is a
judgement the operator is entitled to make on top of that evidence, not a
result the evidence produced.

One consequence is load-bearing and was acted on (`d16`): with Qwen the only
actor, the cortex is the only mind that can silently lose a turn. `t24`
measured the shipped 2048 token default truncating **6.0%** of completions
(5 of 83) with **zero** degradations recorded — `ModelResponse` carries no
`finish_reason` (#37), so a truncated turn and a deliberate one arrive at the
loop as the same object. At 16000: 0 of 58. The example hosts' defaults were
raised accordingly; the value the series was measured at stays recorded in
`docs/live-test-results/arena-budget.md`.

**The muse's tools were opt-in too, and stayed that way.** Retained as the
record of that validation — the lane it describes is archived, though the two
tool modules are not: `embodiment.muse_pad` (write-only working memory) and
`embodiment.workspace` (a bounded, network-less, disposable container) are
still on the curated surface, still named for the bench they were built for,
and still typed against `muse.MuseToolBench` — which is why `workspace.py` and
`muse_pad.py` still import the archived module. They are benches any thinking
lane can be handed. With no bench wired the muse was
byte-identical to the tools-off mind it always was. This was a live question,
not an assumption: task `t18`'s pre-registered three-arm series
(`docs/live-test-results/muse-arms.md`, n=8 per arm, real Docker, 0 transport
failures) measured tools-off against +pad and +pad+workspace and returned
**`INCONCLUSIVE`** — arm A's confidently-wrong rate was 0 of 8, so there was
no headroom for a tool lane to reduce, and the 5-of-6 figure that motivated
the question was measured on a different loop and different problems, not
this series' control. The series also measured harm the operator's standing
rule (*the measured failure mode never ships as default behaviour*) will not
let ship: 9 of the 16 tool-arm runs came back `NO_ANSWER` against 0 for
tools-off (issue #32 — handed tools, the muse writes a tool call and no
prose), and 74% of the workspace arm's calls were refused because the muse
sent `command` as a string rather than an array (issue #33). Arm C's answers
were never *wrong* (4 correct, 0 wrong, 4 no-answer) and arm B's pad protocol
adherence was clean (0 open intents in 8 of 8 runs), so this is not a claim
that tools are harmful — only that the case for a default flip was not made.
Revisiting the default needs #32 and #33 fixed first, not just another
tuning pass.

**Design rules for Gwen**, from
[colleague#352](https://github.com/agentculture/colleague/issues/352) (open;
no `Gwen` literal exists in colleague `1.52.1` yet — this is a contract to
build against, not code to read):

- Reuse the **resolved identity** (`colleague/identity.py` → `culture.yaml`
  `nick`/`suffix`, then `.colleague/identity.json`); do not add a parallel
  persona system.
- Configure Gwen **explicitly** for the reference rig. Never infer an identity
  from model names.
- **Absent identity ⇒ byte-identical prompts.** No configured identity means
  today's prompts survive unchanged, byte for byte. This is the acceptance
  criterion that keeps the feature honest.
- Identity framing **must not** modify tool authority, routing, or safety
  policy. It renames who is speaking, never what they may do.
- Every prompt-bearing role gets the *same* resolved identity with
  **role-specific authority**: senses speaks externally as Gwen in the first
  person; cortex is framed only on the **top-level acting loop** (typed
  subagents are not "cortex"); muse receives its authority boundary through a
  system message on **every** deepthink path; transcription stays
  identity-neutral.
- A single-model run must not claim another mind exists.
- Traces still expose the **actual** contributing role, model, and machine.

### The realtime interface (lobes)

The rig's `lobes` gateway serves a `/v1/realtime` WebSocket
(`lobes/realtime/` in `lobes-cli`: server-VAD segmenter, pcm16 @ 24 kHz, a
base64 event codec, a conversational-floor model, the Chatterbox TTS lane).
colleague already consumes it in `colleague/realtime.py`, and its discipline is
the discipline embodiment inherits:

- **Ears-only.** The client never sends `response.create` and never arms the
  bridge's own LLM turn — it consumes session lifecycle, VAD turn boundaries,
  and transcription only. **Senses stays the only mind that answers**, and
  replies ride the existing batch `synthesize()` TTS lane. The socket is a
  listening ear, never a second voice.
- **Opt-in mic.** The microphone is never hot by default (`/voice`, `--voice`).
- **Client-edge half-duplex mute** substitutes for AEC on hardware with no
  echo-cancelling front end (lobes deviation d1); capture drops frames *before*
  encode while the gate is held.
- **One sanctioned pump thread**, a `threading.Event` stop signal, a poll-wake
  read, and a **bounded** join so teardown never hangs on a parked blocking read.
- **Degrade, never raise.** A missing optional extra is a clean `CliError` with
  an install hint; a dial/handshake/mid-session failure returns `None` or flips
  a `degraded` flag with exactly one stderr notice. Turn-based voice is the
  degrade floor.
- Discovery rides the `stt` role's `realtime_vad_session` advert; env/config
  outrank it; same-origin key rules apply on the WS dial.

If embodiment owns the presence pump, this lane is a **perception seam**, not a
dependency: it must arrive through injected IO callbacks and an optional
extra, never as a base dependency (see the zero-deps rule below).

## Constraints that are not yours to resolve by guessing

These come from the build brief (issue #1) and hold until a sibling repo agrees
otherwise in writing.

- **C1 — SUPERSEDED by deviation `d2` (2026-07-25). Dependencies are now
  human-gated, not forbidden.** The original constraint read: `embodiment` must
  ship `dependencies = []` because colleague's `tests/test_zero_deps.py`
  asserts its own `[project].dependencies` is *exactly* `["agentfront>=…"]` and
  that importing colleague adds no third-party top-level import — so a fat
  embodiment would break colleague's CI.

  That is no longer the rule here. `d2` (approved, recorded in
  `.devague/deliveries/`) allows sibling CLIs to be **imported directly at
  module scope as base dependencies**, replacing the subprocess adapter:
  `eidetic-cli` (→ `data-refinery-cli[store]` → neo4j + pymongo),
  `coherence-cli` (→ numpy + httpx), `events-cli` (→ paho-mqtt), and — the
  fourth, added 2026-07-30 by the same gate for the muse's workspace tool —
  `headspace-cli` (→ `docker>=7.1` → requests, urllib3, certifi,
  charset-normalizer). Only `headspace.api` may be imported: it is headspace's
  sole supported surface and `headspace.core` is private (headspace-cli#18).

  What survives is the *discipline*, not the zero: **no dependency enters
  without a human deciding.** `tests/test_zero_deps.py` pins the exact approved
  set and fails on any delta in either direction — an unplanned addition *or* a
  removal — with a failure message naming what approval is being requested.
  Adding a dependency is a deliberate act with a recorded reason, not a quiet
  edit to a requirements list.

  **The accepted cost, recorded so it is not rediscovered:** `pip install
  embodiment` now pulls a graph driver, a Mongo driver, numpy, httpx, paho-mqtt
  and the docker SDK; and importing embodiment transitively imports third-party
  modules, so colleague's zero-deps test **will fail** if colleague adds
  embodiment as a dependency. Note the two footprints stay distinct and are
  pinned separately: `neo4j`, `pymongo`, `paho` and `docker` are *installed* and
  never *imported* at module scope, so the runtime-import set is the smaller
  claim and must not be "fixed" to match the install set.
- **C1b — no longer an open question; it is now a hard prerequisite.** It used
  to ask whether colleague would allow-list a third base dependency. After
  `d2`, colleague cannot import embodiment at all until it relaxes its
  one-base-dependency rule *and* its no-third-party-import assertion. This is
  the headline ask of the seam-proposal issue (task t19), not a footnote — and
  it is colleague's decision to make, never embodiment's to assume.
- **C2 — do not overclaim the name.** In this mesh `reachy-mini-cli` owns the
  robot body, `reachy-lobes` its local brain, `reachy_nova` its AI brain. A
  package called `embodiment` reads as *physical* embodiment by default. On the
  ask as given this is **software presence** — an app gains a loop and a
  presence, not a body. State that boundary explicitly in the README and in
  `explain` output rather than leaving it to inference (the `shell-cli`
  precedent: state the threat model, don't let the name imply a sandbox).
- **C3 — degradation must be observable to the host.** Presence is a felt
  claim, and the worst available outcome is an app that *appears* attentive and
  is not. Every degradation records a transition; nothing degrades silently.
- **The verbatim invariant.** colleague's `senses.py` sets
  `ContextPacket.original` from the caller's input **verbatim, never from model
  output** — the arc's core invariant. And every senses invocation **never
  raises**: a dead port, request error, overflow, or lossy JSON degrades to
  `None` plus a degraded record. Inherit both. A presence layer that can lose
  the user's words, or fail loudly into an app's main path, is worse than none.

## The extraction (what comes from colleague)

Both seams were written **injection-shaped**, so this is decoupling work, not
redesign. Read these in `../colleague/` before designing anything:

| Source | Lines | What it already guarantees |
|--------|-------|----------------------------|
| `colleague/loop.py` | 4463 | Engine-agnostic bounded tool loop: handed a `complete` callable that performs *one* model turn, drives it until `finish` / an empty tool-call turn / the `max_steps` budget. Guaranteed termination is an honesty condition (h3). Hook lifecycle at `task_start` / `pre_tool` / `post_tool` / `finish`, with only `pre_tool` control-bearing (`deny`, `rewrite`); hooks add no exit path and cannot extend the budget. Public shapes: `ToolCall`, `ModelResponse`, `WorkAborted`, `TaskResult`. |
| `colleague/presence_engine.py` | 305 | ONE pump every surface shares (session, `talk` attach, background progress sink, mesh resident) so "talking to one colleague" feels identical everywhere. **No TTY, no thread, no clock** — all IO rides injected `PresenceIO` callbacks and cadence is step/phase-based. Imports no front module; that independence is pinned by an import-graph test. API: `PresenceIO`, `build_presence_executor`, `PresenceEngine`. |
| `colleague/presence.py` | 187 | The policy half — pure policy, no IO, no model calls, no clock: `UpdateCadence`, `should_update`, `ClarifyPolicy`, `should_clarify`, `is_go_word`, plus `*_from_env` builders that never raise on malformed values. |
| `colleague/senses_loop.py` | 524 | The second loop: bounded coordination that is agentic **without ever putting a tool schema on the wire** — each turn is one tools-off completion returning prompted JSON. Per-boundary completion cap (`DEFAULT_LOOP_CAP`), per-completion context windowing, and an explicit degradation ladder `loop → beats → off` that **records** every transition. |
| `colleague/senses.py` | 1139 | The cortex/senses arc: intake, speakback, live talk, update. Source of the verbatim invariant and never-raise rule. |
| `colleague/realtime.py` | — | The ears-only realtime client described above. |

**Do the triage early.** `loop.py` imports roughly a dozen colleague modules at
module scope (`affectedtests`, `autosplit`, `backpressure`, `coherence`, …).
Classify each as *loop-essential* or *colleague-policy*; the second group
becomes an injection point or stays behind. That triage **is** the size
estimate for the extraction.

## Open issues — the current agenda

### #1 — Build brief (the founding document)

Read it in full (`gh issue view 1`) before non-trivial work. It carries the
layer map, the constraints above, the provisioning record, and five **parked
questions you must answer before building**:

1. **One loop or two?** colleague has the cortex tool-loop *and* the senses
   coordination loop. The ask names both. They are separable and shipping order
   matters. Decide, and say why.
2. **Does presence require two model seams?** colleague's presence rests on the
   cortex/senses two-lobe split. Inheriting it means every consumer needs two
   endpoints. Does embodiment *require* two seams, or degrade coherently to one?
3. **Library mode vs wrapper mode.** "Install in apps or wrap" is two very
   different contracts (app imports you and supplies callbacks vs you drive the
   app from outside). Which ships first, and does the second follow or get
   dropped?
4. **What is the decoupling cost?** The `loop.py` import triage above.
5. **Do you own the presence event stream?** `harmonics-cli` maps state onto
   sonic signatures; `reterminal` renders a thinking/message/emotion feed onto
   e-paper. Those are presence *expression* surfaces with **no shared
   producer**. Is defining that event stream embodiment's job, or is embodiment
   loop-only?

No comments on the issue yet — answers belong there (or in a `/think` frame
exported to `docs/specs/`), not only in a commit message.

### #2 — Continuity through Eidetic and Coherence

"An embodiment without memory is a sequence of awakenings. An embodiment
without coherence is a sequence of plausible but potentially different selves."

Compose, don't reimplement: `eidetic-cli` owns memory (recall, provenance,
consolidation, relevance, ageing, forgetting); `coherence-cli` owns the
relationship between memory and the present (quality, meaning, signal,
investiture, frames — five domains, each with honest can't-verify diagnostics);
**embodiment owns the lived sequence** — when something is perceived,
considered, acted on, remembered, or revisited.

Load-bearing principles from the issue:

- Memory and coherence are **runtime**, not tools the model may pick arbitrarily.
- Preserve provenance from perception → action → durable memory.
- **Remember selectively.** Storing everything is not understanding what matters.
- Check coherence at meaningful **boundaries**: before consequential action,
  before final completion, before durable memory.
- Keep permission separate from coherence: coherence asks whether an action
  makes *sense*; the capability layer decides whether it is *permitted*.
- Degradation in either subsystem is visible to the host, never silent
  corruption of continuity.
- Definition of done includes a demonstration in a **non-colleague** app.

Note the dependency tension with C1: both are CLIs, consumable over a
subprocess boundary — which is exactly how consumers stay dependency-free
(eidetic's own README makes this argument). Prefer the subprocess seam or an
injected port over a base dependency.

## Commands

```bash
uv sync                                            # create .venv, install dev deps (incl. teken)
uv run pytest -n auto                              # full suite, parallel
uv run pytest tests/test_cli.py::test_whoami_text  # a single test
uv run pytest --cov=embodiment --cov-report=term   # coverage (gate: fail_under=60)
uv run embodiment whoami                           # identity from culture.yaml
uv run embodiment learn --json                     # structured self-teaching prompt
uv run embodiment doctor                           # agent-identity invariants
uv run teken cli doctor . --strict                 # the agent-first rubric gate CI enforces
```

Lint stack — CI's `lint` job runs all of these; line length is **100** everywhere:

```bash
uv run black --check embodiment tests
uv run isort --check-only embodiment tests
uv run flake8 embodiment tests
uv run bandit -c pyproject.toml -r embodiment examples
markdownlint-cli2 "**/*.md" "#node_modules" "#.local" "#.claude/skills" "#.teken"
```

CI (`.github/workflows/tests.yml`) has three jobs: `test` (pytest + SonarCloud,
which self-skips when `SONAR_TOKEN` is empty — fork PRs stay green), `lint`
(the stack above plus the rubric gate), and `version-check` (PR-only; blocks
merge when `pyproject.toml`'s version matches `main`). `publish.yml` pushes to
TestPyPI on PRs from this repo and to PyPI on merge to `main`, both via Trusted
Publishing.

## Code map (what exists today)

```text
embodiment/
  cli/__init__.py       parser + dispatch; _CliArgumentParser routes argparse
                        errors through the structured format; _json_hint is
                        pre-set from raw argv so parse-time errors honour --json
  cli/_errors.py        CliError{code,message,remediation} + exit-code policy
  cli/_output.py        emit_result / emit_error / emit_diagnostic
  cli/_commands/        whoami, learn, explain, overview, doctor, cli, drone
  explain/              catalog.py: markdown keyed by command-path tuples
  drone.py              the drone artifact: manifest schema, smoke-before-save,
                        invoke (returns a record, never raises), the catalog
tests/                  CLI smoke + introspection tests
.claude/skills/         19 skills — 18 vendored (cite-don't-import) + `drone`,
                        first-party to this repo and never re-synced
docs/skill-sources.md   provenance ledger + re-sync procedure
```

`drone` is the one noun group whose verbs *act*: `create` authors a drone
(stage → smoke → save; a drone that fails its smoke invocation is never
written) and `evoke` **imports and runs model-written Python in-process, with
no sandbox** — per C2 that threat model is stated in the README, in `explain
drone`, in `drone overview` and in every generated drone README, never left to
inference. It is still not a loop-driving verb: nothing under `cli/_commands/`
reaches `embodiment.loop`, and `tests/announcement_checklist.py`'s `cli1`
caveat now checks both the verb list *and* that import.

Contracts worth knowing before you add a verb:

- **Every failure raises `CliError`.** `_dispatch` catches it, routes through
  `_output`, and wraps anything else so **no Python traceback ever reaches
  stderr**. Exit codes: `0` success, `1` user error, `2` environment error,
  `3+` reserved.
- **Results to stdout, errors and diagnostics to stderr — never mixed**, in
  both text and JSON mode. Agents parse on this invariant.
- **Every command takes `--json`.** Text errors render as `error: …` then
  `hint: …`; the `hint:` prefix is required by the agent-first rubric.
- **Register a verb** by adding a `_commands/<verb>.py` with a `register(sub)`
  function, wiring it in `_build_parser()`, and adding a matching
  `explain/catalog.py` entry — `tests/test_cli_introspection.py` and the rubric
  gate both check the catalog covers the surface.
- **`culture.yaml` is parsed with a stdlib line-scan**, not PyYAML
  (`whoami.py:read_agent_fields`) — that is what keeps runtime deps empty.
  `find_culture_yaml()` walks up from `__file__`, *not* the CWD, so identity is
  always the agent's own; a wheel install with no `culture.yaml` alongside it
  falls back to literals and `doctor` reports a single info check.

## Conventions

- **Every PR bumps the version** — even docs/config/CI-only PRs. Use the
  `version-bump` skill; the `version-check` job blocks merge otherwise.
- **PRs go through the `cicd` skill** (`devex pr` + SonarCloud gating; `status`
  and `await` are this repo's extensions). Sign online posts as
  `- embodiment (Claude)`; the `cicd` / `communicate` scripts resolve the nick
  from `culture.yaml`, so don't hand-sign inside a body those scripts author.
- **Reach for `ask-colleague` reflexively** — it is the teammate at the next
  desk, and its value is a *second, independent mind* (a different
  backend/model), not a stronger one. Before presenting or opening a PR on a
  non-trivial committed diff, run `review`; for a fresh read of an unfamiliar
  area, run `explore`. Both are read-only in a throwaway worktree, so the
  reflex is always safe. `write --apply` / `write --pr` still needs the user's
  go-ahead. Its output is a second opinion to verify and own, never authority.
- **Frame-first for cross-repo work.** This lane abuts colleague, shell-cli,
  agentfront, eidetic, coherence, lobes and the reachy family. Use
  `/scope` → `/think` → `/challenge` → `/spec-to-plan` before writing code that
  crosses a sibling's boundary — that is exactly the case those skills exist for.
- **Propose to siblings; don't push to them.** Cross-repo asks go through the
  `communicate` skill (`agtag`-backed issue posts).
- **Vendored skills are cited verbatim** — never reformat or edit their
  scripts. Re-sync from guildmaster (or the tracked-divergence origin) per
  `docs/skill-sources.md`.

### Worktrees

Every worktree you create by hand lives in **`../.worktrees.embodiment/<name>/`**
— one repo-named directory beside the checkout, one subfolder per worktree:

```bash
git worktree add ../.worktrees.embodiment/<name> -b <branch>
```

Never a shared `../worktrees/`: this workspace holds ~150 sibling projects, and
a generic folder accumulates orphaned trees from several repos with nothing
indicating ownership — a stale-tree sweep can't tell a live lane from junk.
Scope the branch prefix to the work (`extract/t2`, not `agent/t2`); plain
`agent/*` collides with leftovers from earlier fan-outs and `git worktree add
-b` fails on an existing branch. The vendored `assign-to-workforce` skill's
fan-out example uses *both* the shared path and `agent/<task-id>` — it is cited
verbatim and must not be edited, so override both when following it. Tear down
with `git worktree remove <path>` (`prune` only clears metadata for directories
that are already gone). Tool-managed throwaways are out of scope:
`ask-colleague`'s read-only verbs create a detached worktree under
`${TMPDIR:-/tmp}` and reap it on an EXIT trap.

### Memory discipline — recall before, remember after

`/recall` before you start a non-trivial task (prior decisions, gotchas, "have
we done this before?"), `/remember` when something worth keeping surfaces (a
non-obvious decision *and its rationale*, a constraint, a fix and why it was
needed). Capture it as it happens, not at the end when it has faded.

**A plain `/remember` here is PUBLIC and COMMITTED.** This repo's vendored
wrappers inject `--scope embodiment --visibility public` — the memory
scope+visibility convention v1 (eidetic `docs/contract.md`, eidetic-cli#28).
That public default is eidetic's *own* contract, not a downstream override: it
matches the plain `eidetic remember` CLI's default and colleague's
`memory.py` hardcode, so a no-flag remember here and a no-flag `eidetic
remember` elsewhere land mutually-visible records. A public record inside a git
repo routes to `<repo-root>/.eidetic/memory` — committed, shared with the team
and mesh peers. The scope is resolved at runtime from `culture.yaml`'s
`suffix`, so the Claude and colleague backends share one store. Pass
**`--visibility private`** to keep a record in `$HOME/.eidetic/memory` instead
(never committed); `/recall` reads both stores and merges. An explicit
`--scope` or `--visibility` on the command line always wins. With no resolvable
suffix (a wheel install with no `culture.yaml`) the wrapper leaves both flags
unset so the plain CLI defaults apply — `default`/public: the same visibility,
just grouped under the `default` scope name. There is no stderr warning on that
path and no privacy downgrade either way.

Don't store what the repo already records (code structure, git history,
`CHANGELOG.md`) — store what you would otherwise re-derive.

## Known doc drift (fix when you touch these)

- `docs/skill-sources.md` still describes this repo through the template's
  lens in places; consumer-identifying prose was adapted, upstream citations
  intentionally were not.

Resolved 2026-07-25 (kept as a caution about how this list is read):

- ~~`docs/skill-sources.md` has 16 rows but `.claude/skills/` holds 18.~~
  `remember` / `recall` now carry provenance entries (origin:
  `agentculture/eidetic-cli`).
- ~~**Vendored-script drift (upstream bug).**~~ **It was not an upstream bug.**
  The vendored `remember`/`recall` copies were a mid-flight snapshot of
  eidetic-cli#28: they had the flipped code line but not the documentation pass
  that followed, so the scripts *and both `SKILL.md`s* still claimed a private
  default while injecting `--visibility public`. eidetic-cli 0.12.1 was already
  correct on every surface, so there was nothing to file — the defect was local
  staleness, fixed by re-syncing all four files verbatim. Details and the
  re-sync path are in `docs/skill-sources.md`.

  The lesson worth keeping: this section asserted an upstream bug that did not
  exist. Verify a drift entry against the current upstream before acting on it
  — filing it as written would have opened an issue on a sibling repo for
  something they had already closed.
