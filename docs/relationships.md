# Relationships: embodiment, eidetic, coherence, and Gwen

This is for someone deciding whether to build on `embodiment`: what each
package in the continuity story owns, what you supply, what you get back, and
what it costs. It assumes you have read the [README](../README.md) for the
install/quickstart basics; this doc is about the *shape* of the relationship
between the pieces, not how to run the test suite.

Two relationships get confused easily and this doc keeps them apart
throughout:

1. **embodiment / eidetic-cli / coherence-cli** — the continuity stack: how a
   drive's memory and coherence work.
2. **Colleague / Gwen / Qwen / Gemma** — the reference rig: who the operator
   is talking to, and which model plays which role.

## 1. The ownership split (issue #2)

> "An embodiment without memory is a sequence of awakenings. An embodiment
> without coherence is a sequence of plausible but potentially different
> selves."

That line, from
[agentculture/embodiment#2](https://github.com/agentculture/embodiment/issues/2),
is the whole ownership split in one sentence. Spelled out:

| Package | Owns | Answers |
|---|---|---|
| `eidetic-cli` | **Memory.** Recall, provenance, consolidation, relevance, ageing, forgetting. | *What was true, and what happened?* |
| `coherence-cli` | **The relationship between memory and the present.** Quality, meaning, signal, investiture, frames. | *Does what's remembered still make sense against what's happening now?* |
| `embodiment` | **The lived sequence.** *When* something is perceived, considered, acted on, remembered, or revisited. | *What is the app doing with all of that, and on what cadence?* |

`embodiment` is deliberately the thinnest of the three. It contains no store,
no scoring, and no embedding logic of its own — that split isn't just prose,
it is enforced by AST guard tests that parse `embodiment/continuity.py` and
`embodiment/lifecycle.py` and fail if either module ever defines its own
storage-path logic, scoring function, or embedding call
(`tests/test_continuity.py::TestNoStoreScoringEmbedding` and the equivalent
class in `tests/test_lifecycle.py`, both literally titled *"Guard the 'no
store, no scoring, no embedding logic' criterion"*). If you're evaluating
whether embodiment reimplements eidetic or coherence: it structurally cannot,
and CI checks that on every change.

**Compose, don't reimplement** is the rule this split exists to protect.
`embodiment.continuity` is the seam — a call in, a sibling library call out,
an honestly-labelled result back (`probe`, `remember`, `recall`, `assess`).
`embodiment.lifecycle` is the policy layered on that seam: it decides *when*
the seam fires, using three checkpoints the loop already exposes —
`BOUNDARY_ACTION`, `BOUNDARY_COMPLETION`, `BOUNDARY_MEMORY` — and it never
computes a score or a path itself, only reads what `continuity` already
returned.

Permission and coherence stay separate on purpose: coherence asks whether an
action *makes sense*; whether it is *permitted* is the capability layer's
call (your executor, your hooks), never embodiment's. This isn't just
documented — `ContinuityLifecycle.__call__` always returns `None` regardless
of what coherence said, and the loop's own `Boundary` type carries no
approval field to read one from even if a lifecycle wanted to. Both
directions are pinned by an AST guard plus a behavioral test driving the real
loop.

### What you actually inject

```python
from embodiment.lifecycle import LifecycleConfig, build_continuity_fn
from embodiment.loop import run

lifecycle = build_continuity_fn(
    LifecycleConfig(data_dir="/var/lib/myapp/memory", scope="myapp")
)
outcome = run(complete, task, executor=executor, max_steps=20,
              continuity=lifecycle)
for event in lifecycle.events:      # the observability ledger — see below
    log(event.to_dict())
```

`ContinuityLifecycle` *is* a `ContinuityFn` (`Callable[[Boundary], Any]`) —
that's the whole seam. `LifecycleConfig` is where you tell it what matters to
*your* app, because embodiment structurally cannot guess:

- `data_dir` — required for any eidetic write or read. With it unset,
  `continuity.remember`/`recall` refuse before touching the store at all
  (a recorded degradation, `CODE_NO_STORAGE_ANCHOR`) rather than resolving
  against whatever git repo the host process happens to be sitting in.
  Coherence-only operation (no memory) is a supported configuration.
- `scope` — eidetic's scope name (defaults to your resolved identity's
  suffix via `embodiment.identity.resolve_identity`, never inferred any
  other way).
- `consequential` — a predicate over `Boundary` that says which tool calls
  are worth a coherence check. Only you know that your kiosk's
  `send_message` matters and its `get_weather` doesn't; embodiment refuses
  to guess from a tool name. Leave it unset and embodiment defaults to
  checking the first action of each work item.
- `added_by`, `max_links` — provenance knobs threaded onto every durable
  record.

Every checkpoint call, skip, write, and degradation is appended to
`lifecycle.events` unconditionally — that ledger is a bounded local record
for your own inspection (`DEFAULT_MAX_EVENTS = 1000`, oldest dropped first,
the drop count stays readable), not a claim that embodiment owns an event
*stream*. If you want these on a bus, wire `on_event=` to your own sink.

### What composing them costs

This is the part that changed under an approved deviation, so it is worth
stating plainly rather than leaving to the README alone (see its
"Dependencies — what installing this costs you" section for the full
breakdown). `eidetic-cli` and `coherence-cli` are **base dependencies** of
embodiment — direct, module-scope imports, not a subprocess adapter. That
means `pip install embodiment` pulls a graph driver and a Mongo driver (via
`eidetic-cli` → `data-refinery-cli[store]`) and numpy + httpx (via
`coherence-cli`), whether or not you ever call `continuity.remember`.

The older story — that embodiment shells out to `eidetic`/`coherence` as
subprocesses to stay pure-stdlib — is dead. It was the original design (task
t13's shared subprocess adapter existed and worked), and it was deliberately
replaced by deviation `d2`, recorded and approved, because in-process calls
buy real Python objects, an injectable `embed_fn` for offline coherence
testing, no per-boundary process-spawn cost, and type-checkable coupling
instead of an argv contract that drifts silently.

What survives from the old zero-dependency posture is not the zero — it's
the discipline: `tests/test_zero_deps.py` pins the exact approved dependency
set and the exact third-party modules importing embodiment introduces, and
fails on *any* delta, addition or removal, with a message naming what
approval is being requested. No dependency enters this package by accident.
`import embodiment` alone still costs nothing (the package root resolves
every public name lazily, PEP 562) — only a host that reaches
`embodiment.continuity` or `embodiment.lifecycle` pays.

The dependency tension issue #2 itself raised — both eidetic and coherence
are CLIs with real transitive weight, and issue #2 asked to "import and
compose" while the original constraint C1 forbade new base dependencies —
was real, not hypothetical, and `d2` is how it was resolved: by paying the
cost and recording it, not by finding a way around it. If your app is
weight-sensitive, that is the number to look at before adopting embodiment's
continuity seam; the loop and presence pump alone (no `continuity=` injected)
do not require any of it.

## 2. Colleague, Gwen, and the reference rig

The continuity split above is about memory and coherence. This section is
about a different question entirely: *who is the operator talking to?*

| Concept | Value | Authority |
|---|---|---|
| Runtime | `colleague` | the harness |
| Loop + presence | `embodiment` | the pump |
| **Teammate identity** | **Gwen** | who the operator addresses |
| **Cortex** | Qwen 3.6 27B (`sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP`) | the worker: bounded tool loop, repo actions, final synthesis — **final authority** |
| **Muse** | Gemma 4 31B (`nvidia/Gemma-4-31B-IT-NVFP4`) | reflective counsel: reframes the problem, challenges assumptions, offers materially different alternatives. Advisory only — **proposes, never decides**; optional |

These role names are design metaphors for splitting responsibility across model
seams, not claims about cognition — [§3](#3-the-function-map--what-each-part-is-for)
states that in full and maps each role to the job it actually does.

Gwen is one prompt-visible teammate produced by cooperating cognitive roles
across two model families — **G**\ from Gemma, **wen** from Qwen. Colleague
stays the runtime and agent type; `embodiment` is the loop that drives the
roles; Gwen is who the operator actually addresses. The full design contract
lives at
[colleague#352](https://github.com/agentculture/colleague/issues/352); as of
this writing it is open with no `Gwen` literal anywhere in colleague's own
code — it is a contract embodiment builds *against*, not code embodiment
reads.

### The scope boundary — read this twice

colleague#352 also names a third role: **senses** (Gemma 4 12B) — intake,
perception, conversational speak-back. **Senses does not ship in embodiment.**
This repo ships exactly one actor loop (cortex, with an optional muse);
colleague's senses coordination loop, and the framing that goes with it,
stays in colleague. This is not an omission to fill in later — it is a
confirmed decision (`c30` in the converged frame:
*"one loop ships: the bounded tool loop extracted from colleague/loop.py; the
senses coordination loop (senses_loop.py) does not ship in embodiment"*),
and the divergence from colleague#352's full three-role picture was recorded
publicly as a comment on that issue
([colleague#352, comment 5073964358](https://github.com/agentculture/colleague/issues/352#issuecomment-5073964358))
before the identity-framing implementation landed.

Two other documents in this repo — the `explain` catalog and an earlier draft
of the README's Gwen table — stated the three-role picture without this
boundary and had to be corrected. If you take one thing from this section: a
table that lists senses alongside cortex and muse as something *this package*
frames is wrong. embodiment frames cortex and an optional muse. Senses, when
it exists, is colleague's concern.

### The muse: a parallel thread, not a per-boundary consult (deviation `d1`)

The muse was originally scoped as a synchronous, per-boundary advisory call —
ask the muse a question at a checkpoint, block, read the answer. That design
was replaced under approved deviation `d1`: the muse is now **a second,
parallel thinking loop that embodiment runs on its own dedicated thread**,
alongside the actor loop rather than interrupting it.

Concretely: `embodiment.muse.MuseLoop` is the bounded, tools-off reasoning —
no thread, no clock, deterministic. `embodiment.muse_runner.ThreadedMuseRunner`
is the thread mechanics around it — one daemon thread, a
`threading.Event` stop signal with a poll-wake wait, and a bounded join so
teardown never hangs even if the muse is parked inside a model call nothing
can interrupt. The actor loop never waits on it: `ThreadedMuseRunner.consider`
hands over a boundary and returns immediately; `ThreadedMuseRunner.drain`
returns whatever finished, instantly, with an empty drain being the normal
case. Insights that arrive stale, late, superseded, or that overflow the
runner's bounded buffer are each recorded, never silently dropped.

The muse's advisory-only boundary is enforced structurally, not just by
convention: `ThreadedMuseRunner` never imports `embodiment.loop`, has no
executor and no decision vocabulary in scope, and there is no code path from
anything the muse produces to a tool-call decision. Its output enters the
running loop only as text on the guidance/message stream that the cortex
reads — the mechanism colleague#352 calls "proposes, never decides" is real
in code, not asserted in a docstring.

What the muse is *for* is carried by the prompt, not only by this document:
`MUSE_AUTHORITY` names reflective, associative work and asks the muse to
disagree when it sees a better path, rather than to be generically creative.
That is task shaping and nothing more — the authority boundary sits in the same
system message and is unchanged by it.

A museless run — no muse configured at all — is the default, primarily
tested path, not a degraded fallback: no muse means no runner, and a runner
that's never asked to consider anything starts no thread at all.

### Events: embodiment produces, it does not own the vocabulary (deviation `d3`)

A fourth piece worth knowing about if you're wiring observability: embodiment
added `embodiment/events.py` and a fourth base dependency, `events-cli`, to
publish the loop's own activity (`LoopEvent`) onto `events-cli`'s fabric via
an optional `EventEmitter`:

```python
from embodiment.events import EventEmitter
from embodiment.loop import run

emitter = EventEmitter(repo_path=my_repo_root)
outcome = run(complete, task, executor=executor, max_steps=20, observer=emitter)
emitter.close()
```

`EventEmitter` is only ever a *producer* — it maps the loop's own `kind`
vocabulary (`turn` / `step` / `hook` / `phase` / `degradation` / `operator` /
`exit`) onto a dotted event type and routes every publish through
`events_cli.core.type_to_topic`. It never invents a topic of its own, and
this was scoped explicitly as a non-goal in the converged frame (`c33`):
embodiment does not own a presence *event stream*. `reterminal-cli` and
`harmonics-cli` — which render or sonify presence state — are **not**
embodiment's consumers; nothing here knows either of them exists. With no
`observer=` supplied, none of this runs and a host never touches
`events-cli` in any form.

## 3. The function map — what each part is *for*

Sections 1 and 2 answer *where the pieces sit* and *who is speaking*. Neither
answers a third question that turns out to explain more of the design than
either: **what job is each part doing?** That frame comes from
[embodiment#11](https://github.com/agentculture/embodiment/issues/11) and
compresses to six words — *senses notice, cortex acts, muse reflects*.

> **These names are design metaphors, not claims about cognition.** "Cortex",
> "muse", "senses", "association", "salience", "reflect" are here to allocate
> responsibility across model seams and modules: which endpoint gets which job,
> which module owns which decision. They are not claims about neuroscience, not
> descriptions of what any model does internally, and not evidence that anything
> here notices, reflects or remembers the way a person does. A model prompted to
> reflect is a model prompted to reflect. This is the same discipline the
> README's *software presence, not a robot body* note applies to the package
> name (constraint **C2**): say what the word is doing, before someone reads it
> as an architecture claim.

| Function | What it does | Where it lives today |
|---|---|---|
| **Notice** | take the operator's words in, keep them verbatim, interpret without deciding | `embodiment/perception.py` — the *seam* only (`perceive(interpret=…)`); the senses coordination loop stays colleague's (`c30`, above) |
| **Act** | plan, choose, call tools, finish — under a step budget, with termination proved structurally | `embodiment/loop.py` |
| **Reflect / associate** | imagine alternatives, reframe the problem, connect memories, simulate futures, construct meaning — counsel only | `embodiment/muse.py` (the bounded thinking loop) + `embodiment/muse_runner.py` (its thread) |
| **Hold working state across a reset** | intent written *before* the act, observation after, so a successor resumes instead of restarting | `embodiment/scratchpad.py` |
| **Remember** | recall, provenance, consolidation, ageing, forgetting | `eidetic-cli`, reached through `embodiment/continuity.py` |
| **Relate memory to the present** | quality, meaning, signal, investiture, frames | `coherence-cli`, through the same seam |
| **Sequence all of it** | when something is perceived, considered, acted on, remembered, revisited — and what is worth keeping | `embodiment/lifecycle.py` |
| **Stay present between acts** | keep the host attended-to while the acting loop is not producing output | `embodiment/presence_engine.py` + `embodiment/presence.py` |
| **Report what went wrong** | fold six lanes' degradation vocabularies into one host-visible stream (**C3**) | `embodiment/ledger.py` |
| **Decide what deserves attention** (salience) | — | **no owner.** Nothing in this repo implements it |

Four things this table is *not*:

- **It is not an embodiment roadmap.** A row naming colleague or a sibling CLI
  says where that function lives *in the mesh*, not what this package is going
  to grow. In particular it does not reopen `c30`: embodiment ships one actor
  loop, and colleague keeps the senses coordination loop and its framing.
- **It is not the muse's charter.** The muse's task prose lives in exactly one
  place — `MUSE_AUTHORITY` in `embodiment/muse.py`, prepended to the system
  message of every thinking turn, so no host configuration can drop it. The row
  above *describes* it; restating it here would create a second source of truth
  that drifts.
- **"Connect memories" is half-built, and the half that exists is the fetch.**
  `embodiment/recall_bundle.py` fetches raw records out of the store on the
  runtime's side — never the muse's, which has no query verb and no tool schema
  — and marks each bundle flat or graph in its provenance. The muse itself still
  sees only a boundary snapshot capped by `MuseControls.max_context_chars` (600
  characters) with no memory in it. Wiring the bundle into the muse's boundary
  rendering, under its own budget, is **(planned)** — plan task `t5`.
- **Salience really has no owner.** The nearest things in this package answer
  different questions: `lifecycle.select_for_memory` asks what is worth
  *remembering* by fixed policy, and `LifecycleConfig.consequential` asks the
  *host* which actions matter (because embodiment refuses to guess that from a
  tool name). Neither decides what deserves attention in the first place.

### Why this map lives here and not in README.md or CLAUDE.md

`README.md` and `CLAUDE.md` keep the **layer table** — the where-it-sits map —
and this document keeps the function map. That is not a half-finished
migration; the promotion is gated on a measurement.

The frame's own strongest argument is a prediction. The muse's measured
non-result (`docs/live-test-results/designed-problem.md`: 1 of 4 in both arms,
n=4 per arm) is what you would expect if the muse had been asked to *supervise a
tool loop* — executive work — rather than to reflect. That is testable, and
issue #11 recommends testing it before promoting the frame rather than promoting
it because it reads well.

So the gate, stated plainly so a later reader knows this map is parked
deliberately:

- **Promotion** — the function map moves into `README.md` and `CLAUDE.md` only
  after the association-work experiment (plan task `t18`) returns a supporting
  measured result, with its n stated.
- **An honest negative keeps the map here.** If the experiment finds no effect,
  the map stays in this document, the negative is recorded in this section
  beside it, and the layer table stays exactly where it is. A negative is a
  result, not a prompt to re-run until it passes.
- **Neither outcome is pre-empted.** As of this writing the association-work
  experiment has **not been run**, and nothing in this document reports one.

## 4. Before → after — grounded in what was actually surveyed

The claim that embodiment is worth adopting rests on a specific "before"
state that was verified against colleague's code, not assumed. From the
converged spec
(`docs/specs/2026-07-24-gwen-loop-presence-continuity.md`):

> **Before:** the loop and presence machinery live only inside colleague
> 1.52.1 — working but unreusable; an app wanting an embodied presence must
> reimplement the pump, memory and coherence must be hand-wired per host, and
> no identity framing exists anywhere (Gwen is a contract, not code).
>
> **After:** an app imports embodiment (or installs its CLI), supplies a
> model seam, IO callbacks, and optionally an injected tool executor, and
> gains the bounded perceive-decide-act loop with presence between acts;
> configured with an identity it becomes Gwen — Qwen cortex worker with an
> optional Gemma 31B muse subconsciousness — and its presence is continuous
> across sessions, with every degradation observable.

(The exported spec's "after" still describes a subprocess adapter for
continuity — that detail is superseded by `d2`, above; the composition
itself, and everything else in that paragraph, held.)

That "before" is not asserted for effect — it is what the frame's scope
exploration actually found, each finding tied to a file and line read at the
time:

- **The eidetic/coherence composition already half-existed, informally, in
  colleague itself** — scope entry `s25`: *"coherence's consumer map declares
  eidetic gating memory writes on subdimensions.consequence/future_constraint,
  and colleague already wires recall-before/remember-after plus a post-loop
  coherence gate inside loop.py — exactly the code the extraction moves and
  the parallel implementations issue 2 says to remove."* Composing them as a
  reusable seam wasn't inventing new behavior; it was pulling out code that
  colleague had already written once, ad hoc, and would otherwise keep
  rewriting per host.
- **No identity framing existed anywhere to reuse** — scope entry `s19`:
  a `grep -rniI gwen` over colleague's history and a `git log --all --grep=gwen`
  both returned zero matches. `s11` confirms why: colleague#352, which defines
  Colleague=runtime / Gwen=teammate / Qwen=cortex / Gemma-31B=muse /
  Gemma-12B=senses, was open with zero comments at the time of the survey —
  "a contract to build against, not code to read."
- **The dependency tension in issue #2 was real, not a false alarm** — scope
  entry `s10`: issue #2 says to import both eidetic and coherence "through
  their intended reusable package surfaces," while the original constraint
  (C1) forbade new base dependencies; `s21` confirmed both packages carry real
  transitive weight (`data-refinery-cli[store]` → neo4j + pymongo;
  numpy + httpx). That tension is what `d2` eventually resolved by paying the
  cost explicitly rather than routing around it with a subprocess seam
  indefinitely.
- **The guiding principles for memory selection came from the issue itself,
  not invention** — scope entries `s7`–`s9`: memory and coherence are
  *runtime*, not tools a model picks arbitrarily; "storing everything is not
  understanding what matters"; and a host must be able to see how past
  experience affected the present interaction, where a remembered fact came
  from, and what was carried forward. `embodiment.lifecycle.select_for_memory`
  is the code that answers exactly those three questions, in that order.

None of this is presented as embodiment's own opinion — it's what a
concrete read of colleague's code, the two build-brief issues, and
colleague#352 turned up. If you want to verify any single claim above rather
than take this document's word for it, the scope entry IDs (`s7`, `s9`,
`s10`, `s19`, `s21`, `s25`) are the citations, resolvable with
`devague show` against this repo's frame.

## 5. Summary for an app author

- **You supply:** a model seam (a `complete` callable), IO callbacks, and
  (optionally) a tool executor, a `LifecycleConfig` naming your `data_dir` and
  what counts as consequential, an `EventEmitter` if you want loop activity on
  a bus, and — if you want the Gwen framing — an explicit resolved identity.
  Nothing is ever inferred from a model name.
- **You get:** `embodiment.loop.run(...)` — the bounded perceive-decide-act
  loop with guaranteed termination; the presence pump between acts
  (`PresenceEngine`); continuity across sessions through
  `build_continuity_fn`, if you inject it; an optional parallel muse via
  `ThreadedMuseRunner`, if you configure one; and an observable event feed via
  `EventEmitter`, if you wire one in. Every one of those is optional except
  the loop itself — an unconfigured host gets today's behavior, byte for
  byte.
- **It costs:** four base dependencies (`eidetic-cli`, `coherence-cli`,
  `events-cli`, transitively neo4j + pymongo + numpy + httpx + paho-mqtt),
  approved and pinned under deviation `d2`/`d3`, human-gated on every future
  change by `tests/test_zero_deps.py`. `import embodiment` alone still costs
  nothing; the cost is paid only by the modules you actually reach.
- **Library only, for now:** embodiment ships as something you import and
  supply callbacks to; the alternative "wrapper mode" (embodiment driving your
  app from outside) was considered and dropped as a decision, not left open.

## See also

- [`README.md`](../README.md) — install, quickstart, the full dependency
  cost table, and the CLI surface.
- [`CLAUDE.md`](../CLAUDE.md) — contributor-facing status, the module map,
  and the full deviation ledger.
- [agentculture/embodiment#1](https://github.com/agentculture/embodiment/issues/1) —
  the build brief.
- [agentculture/embodiment#2](https://github.com/agentculture/embodiment/issues/2) —
  the continuity issue this document's ownership split is drawn from.
- [colleague#352](https://github.com/agentculture/colleague/issues/352) —
  the Gwen identity contract, including the comment recording the
  cortex+muse-only scope of this repo.
- `docs/specs/2026-07-24-gwen-loop-presence-continuity.md` — the converged
  spec this document cites scope entries and decisions from.
