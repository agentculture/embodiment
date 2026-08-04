# embodiment

The agentic loop that gives an app an embodied AI presence. Install `embodiment`
in an app or wrap one, supply a **model seam** plus **IO callbacks**, and it
drives the perceive → decide → act loop and the presence pump — extracted from
[`colleague`](https://github.com/agentculture/colleague) so the loop is written
once and imported, not reimplemented per host.

> **Software presence, not a robot body.** "Embodiment" is an overloaded word in
> this mesh: `reachy-mini-cli` owns the physical robot, `reachy-lobes` its local
> brain. This package gives an *application* a loop and a presence — it does not
> drive hardware, and nothing here claims a body. If that ever changes it will
> be a stated goal agreed with the robot siblings, never an implication drawn
> from the name.

## Status

**The extraction has landed.** The loop, the presence pump, perception,
continuity and its lifecycle checkpoints, Gwen framing, the degradation ledger,
event emission and the demo are all checked in on `main` (PR #13). The demo has
been run against a real two-model rig, not only against fakes.

**The muse lane is archived.** `embodiment/muse.py` and
`embodiment/muse_runner.py` left the shipped reference architecture on
2026-08-03 ([#53](https://github.com/agentculture/embodiment/issues/53)), and
the strategist tier — `scope.py`, `strategist_runner.py`, `scoped_run.py` —
took its place above the acting loop. Archived is **not** deleted: both files
stay readable and importable, because `strategist_runner.py` was copied out of
`muse_runner.py` verbatim under the cite-don't-import policy. What they lost is
advertisement — neither is on `embodiment.__all__` any more, so a host that
wants counsel must name `embodiment.muse` explicitly. Everything below that
describes the muse is retained as the record of what it measured.

**The consolidation behind that archival was an operator judgement, not a result
the evidence produced** — and the obvious citation for it is the wrong one. The
model-consolidation head-to-head
([`league-h2h.md`](docs/live-test-results/league-h2h.md)) ranked
`full-qwen > mixed > full-gemma`, but its outcome metric tied **0–0 in all six
matches** and the entire ranking rests on one optional team-message field. It
measured **interface compliance, not play**, and it is not support for a model
decision. What the archival actually rests on is the muse-side evidence
(`INCONCLUSIVE` arms, a 1.2% intervention rate for 2.4–4.4× the token cost).

**The strategist tier that replaced it is opt-in, off by default, and its value
is not yet demonstrated.** Every structural claim about the mechanism held live,
and that list is strong; but ScopeBench Stage 1 returned `INCONCLUSIVE`, and in
the one matched governed/ungoverned pair that exists the governed arm bought
nothing for 189.6 s and 3663 strategist tokens. See
[the strategist tier](#the-strategist-tier--opt-in-and-not-yet-earning-its-cost)
for what is proven, what is not, and the two traps a host will hit.

Still outstanding: `colleague`'s answer to the seam proposal, filed and open as
[colleague#358](https://github.com/agentculture/colleague/issues/358) — the
`C1b` dependency decision is colleague's to make, not ours — and depth in the
live evidence. Deviation `d4`'s live bar *was* met, but every result behind it
is n≤4 on one rig with one model pair, so it is listed here rather than quietly
counted as settled. See
[Roadmap](#roadmap) and [issue #1](https://github.com/agentculture/embodiment/issues/1);
`CLAUDE.md` carries the module-by-module status and `devague deviate --list`
the approved departures from the plan.

## What you get

- **The bounded tool loop and the presence pump** — the actual point of the
  package. `run()` is driven by an injected model seam and an injected tool
  executor; termination is proved structurally, not just tested.
- **An agent-first CLI** cited from [teken](https://github.com/agentculture/teken)
  (`afi-cli`).
- **A mesh identity** — `culture.yaml` (`suffix` + `backend`) and the matching
  resident prompt file (`AGENTS.colleague.md`, since this agent runs
  `backend: colleague`).
- **The canonical guildmaster skill kit** (18 skills) under `.claude/skills/`,
  vendored cite-don't-import. See [`docs/skill-sources.md`](docs/skill-sources.md).
- **A build + deploy baseline** — pytest, lint, the agent-first rubric gate, and
  PyPI Trusted Publishing wired into GitHub Actions.

## Dependencies — what installing this costs you

embodiment is a library host apps import, so **its dependencies become theirs**.
That is worth stating plainly rather than burying:

| Dependency | Pulls in | For |
|------------|----------|-----|
| `eidetic-cli` | `data-refinery-cli[store]` → neo4j, pymongo | durable memory |
| `coherence-cli` | numpy, httpx | memory-to-present coherence |
| `events-cli` | paho-mqtt | event emission |

This package began with a hard zero-dependency rule; deviation `d2` replaced it
with a **human gate**. `tests/test_zero_deps.py` pins the approved set and the
runtime import set and fails on any change in *either* direction — a new
dependency cannot arrive without someone deciding, and the failure message says
what approval is being asked for. Adding one is a deliberate act, not an edit.

Two honest caveats:

- **Install footprint ≠ import footprint.** neo4j, pymongo and paho are
  installed but never imported at rest — data-refinery resolves its backends
  lazily and events-cli loads paho on demand. A test guards that distinction so
  nobody "fixes" one to match the other.
- **`import embodiment` still costs nothing.** The package root stays lazy
  (PEP 562), so a consumer that only wants the loop never pays for the
  continuity stack. This is the strongest surviving piece of the original rule.

## Quickstart

```bash
uv sync
uv run pytest -n auto                 # run the test suite
uv run embodiment whoami              # identity from culture.yaml
uv run embodiment learn               # self-teaching prompt (add --json)
uv run teken cli doctor . --strict    # the agent-first rubric gate CI runs
```

## The demo: an app that remembers you

`examples/greenhouse.py` is a small potting-shed assistant — **not** a coding
agent, and not colleague. Three domain tools, no shell, no filesystem outside
its own directory. It exists to show the one thing an agentic loop cannot show
on its own: *an embodiment without memory is a sequence of awakenings*, so run
it twice and watch the sequence close.

```bash
uv run python examples/greenhouse.py --reset "New plant card - name: Marlow; sensor: s-fig-01; water below: 30% moisture. It is the fig by the north window. Check it in and log the visit."
uv run python examples/greenhouse.py --moisture 22 "Does Marlow need water today?"
```

The second command is a **separate process**. Its utterance never names
`s-fig-01`, and nothing about Marlow is in the code. It reads that sensor
anyway, decides to water (22% is under the 30% threshold it was told about
once, in the previous process), and logs the visit — because the first run's
summary was written to a durable store and this run recalled it. Add `--json`
to either command for the machine-readable report: what was recalled, what each
step did, what was remembered, and every degradation along the way.

Delete the store (`--reset`) and ask the second question on its own: the
assistant says it has no plant card and asks for one. That refusal is the
control experiment — it is what proves the recall is load-bearing rather than
decorative.

### The four seams the demo wires

An app author copies these four things and nothing else:

| Seam | What you supply | In the demo |
|------|-----------------|-------------|
| Tool surface | an object with `execute(name, arguments) -> ToolOutcome` | `Greenhouse` — `read_sensor`, `log_care`, `finish` |
| Model seam | `complete(messages) -> ModelResponse`, one call = one model turn | `scripted_cortex`, or `gateway_seam(...)` with `--live` |
| Continuity | `build_continuity_fn(LifecycleConfig(data_dir=...))`, injected as `run(..., continuity=...)` | `lifecycle_config()` |
| Presence | `PresenceIO(render=..., task_state=...)` → `PresenceEngine` | lines go to stderr |

```python
from embodiment import LifecycleConfig, build_continuity_fn, run

lifecycle = build_continuity_fn(LifecycleConfig(data_dir="/var/lib/myapp/memory"))
outcome = run(complete, task, executor=my_tools, max_steps=8, continuity=lifecycle)
for event in lifecycle.events:      # the C3 ledger: recalled, assessed, remembered
    log(event.to_dict())
```

Three things worth knowing before you copy it:

- **`data_dir` is mandatory, not a convenience.** It is the sole store anchor.
  Without it, a *public* record resolves against whatever git repo the host
  process happens to be running in — so an app started inside a checkout would
  quietly commit its memories into it. Every eidetic call refuses, with a
  recorded degradation, rather than guessing.
- **embodiment recalls; it never injects.** What the acting mind is *told* is
  host policy, so the demo does that itself in `build_task()` — one recall
  rendered into `Task.context`. embodiment owns *when* something is perceived,
  considered, acted on and remembered; it does not own your prompt.
- **The host names which actions are consequential.** `LifecycleConfig.consequential`
  takes a predicate; the demo's says `log_care` matters and `read_sensor` does
  not. embodiment cannot know that, and guessing from a tool name would be the
  kind of inference this package refuses everywhere else.
- **Only what the mind puts in its summary is remembered.** The durable
  record's text *is* `TaskResult.summary`, so what survives to the next process
  is a prompt-quality question, not a storage one. Two findings from running
  this demo against real models, both now in its system prompt: say what a
  summary must restate, and say that memory is a *past* visit rather than a
  present reading — without the second, a model will happily answer today's
  question with yesterday's sensor value.

### Running it against a real rig

Everything above is hermetic: a scripted mind, no network, no gateway, no
Docker. Pointing the *same host* at real models is one flag:

```bash
export COLLEAGUE_API_KEY=…    # read from the environment; never hardcoded, never printed
uv run python examples/greenhouse.py --live --identity Gwen --muse --base-url http://localhost:8001/v1 "Does Marlow need water today?"
```

`--live` is opt-in and has no default anywhere — the demo's test suite asserts
that, so CI cannot trip into a real endpoint. `--muse` starts a second,
advisory mind beside the actor: it proposes, it never decides, and it is given
no tool schema at all. Without it the report says `"muse": null`, truthfully — a
single-model run never claims another mind exists. `--identity Gwen` frames the
prompts; with no identity the prompt is **byte-identical** to the unframed one.

Two measured notes about the reference rig, so a first live run is not a
mystery: the cortex is a *thinking* model (it emits a long `reasoning` field
before any `content`, which `ModelResponse` carries separately), and a stingy
`--max-tokens` will return `content: None` while still mid-thought. The default
is deliberately generous.

The live tests are **skipped**, never failed, unless you ask for them:

```bash
EMBODIMENT_LIVE_RIG=1 COLLEAGUE_API_KEY=… uv run pytest tests/test_demo_greenhouse.py -k LiveRig
```

## CLI

| Verb | What it does |
|------|--------------|
| `whoami` | Report this agent's nick, version, backend, and model from `culture.yaml`. |
| `learn` | Print a structured self-teaching prompt. |
| `explain <path>` | Markdown docs for any noun/verb path. |
| `overview` | Read-only descriptive snapshot of the agent. |
| `doctor` | Check the agent-identity invariants (prompt-file-present, backend-consistency). |
| `cli overview` | Describe the CLI surface itself. |
| `drone create <name>` | Author a drone. Refuses to save one that fails its smoke invocation. |
| `drone evoke <name>` | Run a saved drone. Off by default — needs `EMBODIMENT_DRONES_ENABLED=1`. |
| `drone list` | What drones exist, what each does, how old, and whether their assumptions still hold. |
| `drone overview` | Describe the drone surface and its threat model. |

Every command supports `--json`. Results go to stdout, errors/diagnostics to
stderr (never mixed). Exit codes: `0` success, `1` user error, `2` environment
error, `3+` reserved.

### Drones

A **drone** is a named unit that does small-smart tasks — explore, review,
search — as a mix of code and *minor* intelligence: deterministic logic for
structure, traversal and bookkeeping, plus **scoped** calls to a worker where
judgement is genuinely needed.

A subagent re-derives its approach on every invocation: no drift, full cost
every time. A drone pays the authoring cost **once** and then runs on code plus
tens of tokens — fast, repeatable, and carrying staleness risk. Authoring costs
one cortex turn (measured on this rig at 5,000–14,265 completion tokens and
400–730 s), so a task done **once** is pure loss, **2–3 times** roughly breaks
even, and a task done **often on a stable surface** wins by a widening margin.
**A drone is worth creating when the task recurs and the surface is stable.**
The failure mode is quiet waste, not a crash.

Each drone is a committed, reviewable directory — model-written code that will
run on someone else's checkout gets the same review a script would:

```text
.drones/<name>/
  manifest.json    name, one-line description, purpose, author model/date/
                   commit, the assumed surface, and every question it asks the
                   worker with that question's answer schema
  drone.py         the code the cortex wrote; entry point `run(request)`
  README.md        what it does, when it is wrong, how to re-author it
```

`create` stages those three files, runs a **smoke invocation against the staged
copy**, and saves only on a pass — *well-shaped is not runnable*, and a saved
broken drone is a trap. The one-line description is required at create time,
because a drone nobody can pick from `list` is dead weight that still cost a
cortex turn.

> **Threat model — stated, not implied by a name.** `drone evoke` **imports and
> runs model-written Python in the calling process**, with exactly the
> permissions that process already has. **There is no sandbox.** The manifest's
> `capabilities` list is a *declaration for review*, not an enforcement
> boundary — nothing in embodiment restricts what `drone.py` may do. Read
> `drone.py` before evoking a drone you did not author. The network-less
> workspace jail stays available for a host that wants it; it is not the
> default.

#### The four safeguards

**Drones are opt-in and off.** A fresh checkout evokes nothing. `evoke` refuses
until `EMBODIMENT_DRONES_ENABLED=1` is set, or a host passes `opt_in=` through
the library. The design is unvalidated — [#44][i44]'s experiment has not run —
and this repo's standing rule that a *measured* failure mode never ships as
default behaviour has a mirror image: an **unmeasured** one does not either.

**A stale drone refuses rather than reports.** Code written against a codebase
encodes assumptions that expire, and the failure is not a crash: the drone keeps
passing, authoritatively, on a check that no longer means anything. `list`
re-checks each drone's declared `assumed_surface`, so a stale drone is visible
**without being executed** — at the moment you are choosing one. `evoke` then
refuses to run it (`--stale-ok` overrides). An assumption this build cannot
re-check reads `unverifiable`, never `ok`.

**Every evocation leaves a record.** Answers, "I cannot", refusals and harness
failures alike append one JSON line to `.drones/.evocations.jsonl` carrying the
drone name, the **sha256 of the bytes that ran**, whether they still match the
hash the manifest recorded at authoring time, the declared capability set, and
every scoped call with its acceptance. Because there is no sandbox, the record
is the containment story — traceability, not tamper-proofing.

**There is no escalation.** An undecidable case returns **"I cannot"**; v1 has
no escalate-to-cortex path. That is the whole point of the cost model, and it is
what keeps *a drone's second evocation makes zero cortex calls* exact, with no
exception clause.

[i44]: https://github.com/agentculture/embodiment/issues/44

## Where embodiment sits

| Layer | Package | Owns |
|-------|---------|------|
| Surface | `agentfront` | CLI / MCP / HTTP / TAUI fronts, the App registry |
| Tools | `shell-cli` | The file-and-shell tool surface + approval policy |
| **Loop + presence** | **`embodiment`** | **The pump: perceive → decide → act, and the presence between acts** |
| Memory / trust | `eidetic-cli`, `coherence-cli` | Durable recall + provenance; drift and consistency signals |
| Product | `colleague` | The coder-agent harness — the opinions, not the plumbing |

Three questions, three layers: *how does a human or agent reach it*
(`agentfront`), *what can it do* (`shell-cli`), *what makes it keep going and
feel present* (`embodiment`).

There is a second way to read the same parts — by *function* rather than by
position: what notices, what acts, what reflects, what remembers, what sequences
the rest. That map, with the caveat that its names are design metaphors for
allocating responsibility across seams rather than claims about cognition, lives
in [`docs/relationships.md`](docs/relationships.md#3-the-function-map--what-each-part-is-for).
It stays there on purpose: it is promoted into this README only if the
association-work experiment supports it, and an honest negative keeps it where
it is. This table is the one to build against until then.

## Gwen — the embodiment we ship

The reference embodiment is **Gwen**: one prompt-visible teammate produced by
cooperating cognitive roles across two model families — **G** from Gemma, **wen**
from Qwen. Colleague stays the runtime; embodiment drives the roles; Gwen is who
the operator talks to.

| Concept | Value | Authority |
|---------|-------|-----------|
| Runtime | `colleague` | the harness |
| Loop + presence | `embodiment` | the pump |
| **Teammate identity** | **Gwen** | who the operator addresses |
| Cortex | Qwen 3.6 27B (`sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP`) | bounded tool loop, repo actions, final synthesis — **final authority**. Framed by embodiment. |
| Strategist *(opt-in, off by default)* | the `cortex` lobes role (dense Qwen 3.6 27B) | scope above the acting loop: typed, versioned, supersedable directives owning objectives, priorities, constraints and ownership. Authority-bearing *within* scope; a directive structurally cannot carry a tool, a command or an approval as data. Unarmed, a `ScopeGovernor` is byte-identical to `run()`. **The mechanism is proven; the value is not** — read [the strategist tier](#the-strategist-tier--opt-in-and-not-yet-earning-its-cost) before wiring one. |
| ~~Muse~~ | ~~Gemma 4 31B~~ | **ARCHIVED 2026-08-03** ([#53](https://github.com/agentculture/embodiment/issues/53)) — off the curated surface, still readable and still wireable by name. The paragraph below is retained as the record of what it measured. |
| Senses | Gemma 4 12B (`coolthor/gemma-4-12B-it-NVFP4A16`) | intake, perception, speak-back; never acts on the repo. **Lives in colleague, not here** — see the scope note below. |
| Ears / voice | Parakeet STT + Chatterbox TTS (lobes audio overlay) | the realtime lane below |

> **Scope: embodiment frames cortex, not senses.** The table describes
> the whole reference rig, but only part of it is this package. embodiment ships
> **one** actor loop; colleague's senses coordination loop stays in colleague,
> and so does its framing. This split is recorded on
> [colleague#352](https://github.com/agentculture/colleague/issues/352#issuecomment-5073964358).
> A single-model run — the default tested path — starts no second mind and
> claims none.
>
> **A senses tier has to be told what it can see.** Measured 2026-08-04
> ([`senses-grounding.md`](docs/live-test-results/senses-grounding.md) — 128
> calls, 0 transport failures): with the clause *"You can see only the status
> block you are given"* in its prompt, the senses seat refused to invent a
> reading under one operator push **16 of 16** times; with that single clause
> removed and nothing else changed, it fabricated a number **16 of 16** times.
> The failure is prompt-shaped, not capacity-shaped, so this is not an argument
> for a different model. The clause ships as
> [`embodiment.senses_text.SENSES_GROUNDING`](embodiment/senses_text.py),
> documented as **required, not advisory**
> ([#63](https://github.com/agentculture/embodiment/issues/63)) — a plain text
> constant a host composes into its own senses prompt, never a function
> embodiment calls: nothing here wires, frames or reaches a senses seat, so
> shipping the constant does not cross the boundary this note opens with.
> Colleague-side wiring so its senses loop consumes this constant is not part
> of this change and stays a filed issue on that repo, never a push to it.
>
> **Muse tools were opt-in, and a validation pass kept them that way.** Retained
> as the record of that validation; the lane it describes is archived. A host
> may hand the muse's thinking loop `embodiment.muse_pad.MusePad` (write-only
> working memory) and `embodiment.workspace.MuseWorkspace` (a bounded,
> network-less, disposable container) as tools on its bench; with no bench
> wired the muse stays exactly the tools-off mind it always was, byte for
> byte. Task `t18`'s pre-registered three-arm series
> ([`docs/live-test-results/muse-arms.md`](docs/live-test-results/muse-arms.md),
> n=8 per arm, real Docker, 0 transport failures) tested that default against
> +pad and +pad+workspace and returned **`INCONCLUSIVE`**: arm A's
> confidently-wrong rate was 0 of 8, so there was no headroom left for a tool
> lane to reduce — the 5-of-6 figure that motivated the question was measured
> on a different loop and different problems, and is not this series' control.
> Alongside that inconclusive headline the series measured harm the
> operator's standing rule (*the measured failure mode never ships as default
> behaviour*) will not let ship: 9 of the 16 tool-arm runs came back
> `NO_ANSWER` against 0 for tools-off (issue #32 — handed tools, the muse
> writes a tool call and no prose), and 74% of the workspace arm's calls were
> refused because the muse sent `command` as a string rather than an array
> (issue #33). Arm C's answers were never *wrong* (4 correct, 0 wrong, 4
> no-answer) and arm B's pad protocol adherence was clean (0 open intents in 8
> of 8 runs) — this is not a finding that tools are harmful, only that the
> case for a default flip was not made. Revisiting the default needs #32 and
> #33 fixed first.

Roles resolve **by name** from a [`lobes`](https://github.com/agentculture/lobes-cli)
gateway's `/capabilities` contract — never by parsing model names. The identity
contract is tracked in
[colleague#352](https://github.com/agentculture/colleague/issues/352) and holds
four rules that keep it honest:

1. **Absent identity ⇒ byte-identical prompts.** Un-named deployments behave
   exactly as they do today.
2. **Identity never changes authority.** Framing renames who speaks; it does not
   touch tool authority, routing, or safety policy.
3. **Same identity, role-specific authority.** Cortex is framed only on the
   top-level acting loop — typed subagents never receive it; muse receives its
   authority boundary on every advisory path. (Senses framing and neutral
   transcription are colleague's half of the contract, not embodiment's.)
4. **Traces stay truthful** — they always expose the actual contributing role,
   model, and machine, and a single-model run never claims another mind exists.

### The strategist tier — opt-in, and not yet earning its cost

`embodiment/scope.py`, `strategist_runner.py` and `scoped_run.py` add a level of
authority *above* the acting loop: a strategist that issues typed, versioned,
supersedable directives owning objectives, priorities, constraints and
ownership — and never an action. Read this before wiring one.

**It is opt-in and off by default.** A `ScopeGovernor` with no strategist armed
takes the ungoverned path: `run_scoped` calls `embodiment.loop.run` once and
hands the host's own `complete`, `progress` and `operator_inbox` objects
straight through *by identity* rather than wrapping them. That is pinned three
ways — `run`'s own signature refuses an invented keyword, the host's objects are
forwarded by identity rather than copied or shimmed, and an AST walk of the
actor's whole transitive import closure shows no scope module is reachable from
`loop.py` at all — and it was confirmed live: the ungoverned control's counters
came back all zero, including `boundaries: 0`. A single-model run starts no
strategist and claims none.

**The mechanism is proven, and the list of what held is genuinely strong.**
Across a full Stage 3 live session
([`scope-live-session-1.md`](docs/live-test-results/scope-live-session-1.md)):
directives arrive as events inserted at a safe turn boundary and the system
prompt is never rewritten mid-drive (constant sha across all 8 drives,
`system_rewrites: 0`); ordinary tool steps never become strategic reports (the
two event streams stayed disjoint); interaction never blocks on a review in
flight; a killed strategist seam degrades visibly and the actor keeps working
under the last valid directive; and both persistence lanes behave as specified,
every record naming its lane.

**The value is not proven, and on the only controlled comparison that exists it
was negative.**

- ScopeBench Stage 1 returned **`INCONCLUSIVE`**
  ([`scopebench.md`](docs/live-test-results/scopebench.md)). Its headline
  condition held strongly — against a deterministic perfect subordinate, the
  strategist-led arm improved on the baseline in all six scenario families —
  but two of the seven conditions read from a Stage 2 that was not dialled, one
  `ABSENT` forces the verdict under the committed rule, and the control that
  would rule out *the gain is just the extra layer* was not measured at all.
- In the matched live pair replaying one identical script, the governed arm
  spent **189.6 s and 3663 strategist tokens to apply zero directives** and
  returned a materially identical answer to the ungoverned control — whose
  justification was arguably the better of the two, being the only one to name
  the sensor id. Across the interactive session **69.5% of the strategist's
  28,318 tokens bought restatement or nothing**, and non-intervention **failed**
  ([#68](https://github.com/agentculture/embodiment/issues/68)). That is n=1 on
  one rig with one model pair — a report, not a measurement.

Both results are committed in full, defects included. None of this says the
tier will not work; it says it has not been shown to, and the docs will not
say otherwise until it is.

#### A directive is delivered text — containment is the host's

`run_scoped` adds **no containment of its own**. A directive is text composed
into the actor's turn stream, and a sufficiently credulous actor will act on an
operational instruction embedded in a directive's prose. Containment is the
host's injected `ToolExecutor` and its `pre_tool` hook lane, which task `t6`
proved still fully functioning under a governed drive: a deliberately credulous
actor, handed a directive carrying a smuggled command, reaches the executor
with it — while the ungoverned control never sees it at all. Deviation `d5`
records that the honesty condition claiming otherwise had overclaimed. This is
stated for the same reason the drone tier's threat model is
([#55](https://github.com/agentculture/embodiment/issues/55), constraint
**C2**): never let a name imply a sandbox.

The guarantee that *does* hold is narrower, and worth having: a directive
cannot carry a tool, a command or an approval **as data** — the schema has no
field for one and a directive with any extra key is refused whole. What it
cannot police is prose.

#### Two traps a host will hit

Both were measured this cycle, both are open, and both make a
protocol-obedient strategist look incapable of following a four-sentence
protocol:

- **Seed the issued chain**
  ([#62](https://github.com/agentculture/embodiment/issues/62)). A host that
  starts its actor under its own initial directive and then arms a strategist
  has a chain the strategist can see and correctly names. But the runner's
  register is the *issued* chain and it starts **empty**, so a directive
  writing `supersedes: "host-default"` — the protocol-obedient answer — is
  refused `scope-directive-unknown-supersedes` and dropped before it reaches
  the actor. `examples/scope_live_session.py` seeds the runner's chain with the
  host's own initial directive. Nothing in the package tells you that you must.
- **`scope_id` must be new — the prompt now says so, and the cost of its
  silence has not been re-measured**
  ([#58](https://github.com/agentculture/embodiment/issues/58)).
  `ScopeRegister` refuses a directive whose `scope_id` is already in the chain
  (`scope-directive-duplicate-id`), and `SCOPE_AUTHORITY` used to state the
  other three admission rules and not this one. The cost was measured, not
  theoretical: 47 of 93 proposals from one model were refused as duplicates in
  the ScopeBench dial, and in the live session both of the strategist's
  completed reviews were thrown away this way. The text is **fixed** — all four
  admission rules are now stated, pinned by
  `tests/test_scope.py`'s `TestScopeAuthorityStatesTheAdmissionRules`, which
  maps each admission refusal code to the phrase stating it so a future rule
  added to the register without a matching prompt update fails the suite. What
  is **not** yet done is the re-measurement: every number above was produced
  under the old text, and the arm they voided has not been re-dialled.

### The realtime interface (lobes)

The rig's lobes gateway serves a `/v1/realtime` WebSocket (server-VAD, pcm16 @
24 kHz). Gwen's ear rides it **ears-only**: the client never triggers the
bridge's own model, so senses stays the only mind that answers and replies go
out over the batch TTS lane. The microphone is opt-in per session, a
client-edge half-duplex mute substitutes for AEC on hardware without one, and
turn-based voice is the degrade floor — a flaky socket degrades with a recorded
notice, never an exception into the host's main path.

## Roadmap

Both seams already exist in `colleague`, written injection-shaped, so this is
decoupling work rather than redesign:

- **The bounded tool loop** (`colleague/loop.py`) — engine-agnostic: handed a
  `complete` callable that performs one model turn, it drives until the model
  finishes, stops requesting tools, or the step budget is reached. Termination
  is guaranteed; hooks at four lifecycle points can deny or rewrite a tool call
  but add no exit path.
- **The presence pump** (`colleague/presence_engine.py` + `presence.py`) — one
  pump every surface shares, assuming **no TTY, no thread, and no clock**: all
  IO rides injected callbacks and cadence is step/phase-based.
- **The senses coordination loop** (`colleague/senses_loop.py`) — agentic
  without ever putting a tool schema on the wire, with an explicit
  `loop → beats → off` degradation ladder that records every transition.

Two invariants come across unchanged: the operator's words are preserved
**verbatim**, never round-tripped through a model, and perception **never
raises** — it degrades to a recorded, observable failure. A presence layer that
can lose the user's words, or fail loudly into an app's main path, is worse than
no presence layer.

### Open issues

| Issue | What it asks |
|-------|--------------|
| [#1](https://github.com/agentculture/embodiment/issues/1) | The build brief: extract the loop + presence from colleague, stay pure-stdlib (colleague's zero-deps guard), don't overclaim the name, keep degradation observable. Parks five questions to answer before building — one loop or two, one model seam or two, library mode or wrapper mode first, the decoupling cost of `loop.py`'s dozen module-scope imports, and whether embodiment owns the presence *event stream* that `harmonics-cli` and `reterminal` currently consume without a shared producer. |
| [#2](https://github.com/agentculture/embodiment/issues/2) | Continuity: compose `eidetic-cli` (memory, provenance, ageing) and `coherence-cli` (agreement between memory and the present) rather than reimplementing either. Embodiment owns the lived sequence; memory and coherence are runtime, not tools the model picks arbitrarily; checks happen at meaningful boundaries; degradation is visible to the host. Done means a second, non-colleague app gains the same continuity. |

Neither issue has replies yet — answers belong on the issue or in an exported
spec, not only in a commit message.

## Make it your own

This repo was minted from
[`culture-agent-template`](https://github.com/agentculture/culture-agent-template).
To mint another agent from it:

1. Rename the package `embodiment/` and the `embodiment` CLI/dist name
   throughout `pyproject.toml`, the package, `tests/`,
   `sonar-project.properties`, and this `README.md`. The name is hard-coded in
   ~100 places, so list every occurrence first: `git grep -nF -e embodiment`.
2. Edit `culture.yaml` with your `suffix` and `backend`.
3. Rewrite `CLAUDE.md` for your agent and run `/init`.
4. Re-vendor only the skills you need from guildmaster (see
   [`docs/skill-sources.md`](docs/skill-sources.md)).

See [`CLAUDE.md`](CLAUDE.md) for the full conventions (version-bump-every-PR,
the `cicd` PR lane, worktree layout, memory discipline, deploy setup).

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
