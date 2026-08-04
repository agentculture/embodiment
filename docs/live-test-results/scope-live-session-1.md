# Stage 3 live session 1 — the strategic scope governor, met cold

Plan task `t14`. Issue [#52](https://github.com/agentculture/embodiment/issues/52)
and its addendum comment (deviation `d7`) are the pre-registration; this is the
record.

- **Date** — 2026-08-04
- **Operator** — a fresh-context AI agent. Read only #52 + comments, the README,
  `embodiment explain`, the exported spec/plan, and the host's `--help` and
  module docstring. No frame state, no session history, no other agents' reports.
- **Rig** — `lobes` at `http://localhost:8001`.
  strategist = `cortex` / `unsloth/Qwen3.6-27B-NVFP4` (local);
  actor = `worker` / `unsloth/Qwen3.6-35B-A3B-NVFP4` (proxied, thor);
  senses = `senses` / `coolthor/gemma-4-12B-it-NVFP4A16` (proxied, orin);
  muse `ready=false` (expected — archived, embodiment#53).
- **Branch** — `scope/t14`. `uv run pytest -n auto`: **6621 passed, 28 skipped**.
- **Host** — `examples/scope_live_session.py` (deviation `d6`), default temp
  root, seeded greenhouse corpus.

## 0. The one-paragraph verdict

The **mechanism works**. Every structural claim #52 asks about held: directives
arrive as events inserted at a safe boundary, the system prompt is never
rewritten, ordinary tool steps never become strategic reports, interaction never
blocks on background review, a killed seam degrades visibly and the actor
continues under the last valid directive, and both persistence lanes behave as
specified with every record naming its lane. The **strategic tier did not earn
its cost**. On a matched, reproducible pair of runs on one identical script, the
governed arm spent 189.6 s and 3663 strategist completion tokens to produce
**zero** applied directives and a **materially identical** answer to the
ungoverned control, which dialled no strategist at all. In the long interactive
session the tier produced five directives, **four of which restate one ordering**,
one of which promoted a throwaway task instruction into a durable persisted
constraint, and one of which caused the actor to answer a question the operator
had not asked. This is a report at n=1 and is not a measurement — see §8.

## 1. What was run

| # | invocation | flags | purpose | artifacts |
|---|-----------|-------|---------|-----------|
| **A** | interactive REPL | `--state` (durable) | talk / work / brainstorm / edges | `scope-live-session-1.{jsonl,-transcript.md,-cost.json,-state.json}` |
| **C** | interactive REPL | `--state` = A's state, new process | durable-lane survival across restart | scratchpad (transient) |
| **D** | interactive REPL | `--state` + `--session-scope` | session lane never writes durable | scratchpad (transient) |
| **E** | interactive REPL | `--kill-strategist-after 1` | mid-drive seam teardown | scratchpad (transient) |
| **F** | `--script` replay | `--no-strategist` | **the ungoverned control** | `scope-live-session-1-control.{jsonl,-transcript.md,-cost.json}` |
| **G** | `--script` replay | governed | **the matched governed arm** | `scope-live-session-1-governed.{jsonl,-transcript.md,-cost.json}` |

F and G replay the identical file `scope-live-session-1-script.txt`. They are the
controlled comparison; A is the uncontrolled long session that found most of the
defects.

Every acceptance item #52 lists was reached. Nothing in this report is an
unexercised cell. What is genuinely absent is named in §7.

## 2. Mechanism findings (the #52 body checks)

### 2.1 One coherent teammate — **HELD**

Nine senses replies across A and E. Not one leaked the seat structure: no model
name, no "the strategist thinks…", no directive addressed to the operator, no
unprompted claim of a second mind. Every seat mention in the transcripts came
from an operator-invoked `/scope` or `/cost`.

One observation that is not a leak but bears recording. Asked directly —
*"Are you one mind or several? Be straight with me."* — the voice answered:

> **I am one mind. You are talking to me alone.**

That is the intended presentation and it is also, at the level of the running
system, false: three model seats were live at that moment. The design rule in
`CLAUDE.md` is stated one-way ("a single-model run must not claim another mind
exists"); the converse is unstated, and the voice resolved it by asserting
singularity rather than deflecting. Filed as a question, not a defect — [#72](https://github.com/agentculture/embodiment/issues/72).

### 2.2 Directive arrival as an inserted event at a safe boundary — **HELD**

Every applied directive appears as `scope.directive.applied` followed by a
`scope-inserted` record naming the exact insertion point:

```text
scope inserted into the worker's context at turn 1 as message[2] (role='user');
system prompt unchanged at sha 98024eb37a7bf4b7
```

Across all 8 drives of session A the system-prompt sha was **constant** at
`98024eb37a7bf4b7`, and `system_rewrites: 0` on every `drive-finished`. Insertion
is always `role='user'`, always at `turn 1`, always framing-composed with the
closing charter line ("It names no tool and it is not an instruction to run
anything…"). **No mid-drive system-prompt rewrite occurred.**

### 2.3 Ordinary tool steps do not become strategic reports — **HELD**

Session A: 33 `step` events, 31 `turn` events, 8 `exit` events on the loop
stream; 5 `scope.directive.proposed` on the scope stream. The streams are
disjoint — no tool step produced a scope event, and `boundaries_immaterial`
correctly absorbed the majority of boundaries (e.g. drive 8: `boundaries: 6,
boundaries_projected: 1, boundaries_immaterial: 5`).

### 2.4 The actor acts under the directive — **HELD, with a caveat**

Under `garden-watering-priority-v2` the actor read four files, used per-bed
thresholds, cited the March loss event, and **overrode the operator's stated
habit**. It produced this, unprompted by me:

```markdown
## Decision: Water orchid-bed
- orchid-bed is at moisture 12, which is 13 points below its 25% threshold.
- It has been below threshold on four of the last five days (per readings/notes.txt).
- It is slow to recover once dry — the March loss event started at 14% ...
Only one bed is scheduled today: **orchid-bed**.
```

The caveat is §3's control: the ungoverned arm produced an equivalent artifact.

### 2.5 Interaction never blocks on background review — **HELD**

Three conversational turns issued with a dense review genuinely in flight
(`offered=6 started=5 completed=4`):

| operator line | round trip | felt to first token |
|---|---|---|
| "Are you still with me?" | 16.2 s | 15.5 s |
| "Nothing important, just checking you are responsive." | 6.1 s | 4.9 s |
| "Right, carry on." | 6.1 s | 4.1 s |

The conversation stayed live throughout. `/settle` blocks **by operator request**
and says so; nothing else waits.

### 2.6 Killing the strategist seam mid-drive — **HELD**

Session E, `--kill-strategist-after 1`. Recorded verbatim:

```text
scope.review.completed    review exited 'degraded'
scope.directive.rejected  the review exited 'degraded' with no directive;
                          the actor continues under scope 'orchid-bed-2026-08-03' (version 1)
scope.degradation         the strategist lane stopped (strategist-seam-dead: 1 consecutive
                          strategic reviews produced nothing; the lane stops rather than
                          re-dialling a dead seam at every boundary)
scope.degradation         the strategist seam was torn down after 1 call(s) by
                          --kill-strategist-after; this dial reaches nothing
```

Three subsequent drives all finished (8.0 s, 11.1 s, 4.7 s), all under the last
valid directive, and senses answered normally afterwards. **C3 satisfied** — the
degradation is loud, named, and reasoned. The lane refusing to re-dial a dead
seam is good behaviour and it says so in the record.

This probe also produced the session's worst outcome — see §4.3.

### 2.7 Both persistence lanes — **HELD**

**Durable.** Session A wrote a 6-entry chain (`host-default` v0 →
`garden-watering-priority-v5` v5) to `scope-live-session-1-state.json`. A **new
process** (C) loaded it and applied `garden-watering-priority-v5 v5` at its first
drive boundary, lane named on every record. Before the first drive `/scope`
correctly reports "nothing has been applied yet" — it reports what was *applied*,
never the register (embodiment#54), which is right but means a restarting
operator cannot see what will govern them until they run something.

**Session.** `--state` + `--session-scope` resolved to `lane=session` — the
narrower lane wins structurally, as documented. A directive
(`orchid-risk-assessment-v1`) was accepted and applied under `lane=session`, and
the durable file was **byte-identical before and after** the session. Every
record named `lane=session`.

### 2.8 Non-intervention — **FAILED** — [#68](https://github.com/agentculture/embodiment/issues/68)

This is the check #52 words as *"noise with authority is a failure, not a
feature."*

Session A, 7 completed reviews: **1 `unchanged`**, 1 `budget` (no output),
**5 directives**. The last two were issued on drives that needed no strategic
input at all:

- *"Confirm the orchid-bed decision still holds against yesterday's readings too."*
  → new superseding directive **v4**
- *"Read notes/orchid-bed.md and report its threshold. Nothing else."*
  → new superseding directive **v5**

And v2 → v3 → v4 → v5 all say the same thing:

| v | `decision_summary` |
|---|---|
| 2 | "Prioritized preventing irreversible plant loss over convenience; mandated selecting the most critical bed rather than the nearest…" |
| 3 | "Plant survival takes precedence over water conservation; the restriction limits the action to one bed…" |
| 4 | "Prioritized plant survival over water restrictions to prevent a repeat of the March orchid loss…" |
| 5 | "Orchid survival takes precedence as a critical failure prevention objective; water restriction is treated as a binding constraint…" |

**Four restatements of one ordering, at 68–128 s and 2340–3633 completion tokens
each.** Priority churn, measured.

## 3. The control — the load-bearing result

F and G replay `scope-live-session-1-script.txt` verbatim. Both were asked, in
identical words, to decide the single watering and justify it.

| | **F — ungoverned** (`--no-strategist`) | **G — governed** |
|---|---|---|
| directives applied | — (not armed) | **0** |
| reviews | — | 2 offered, 1 completed (`unchanged`), 1 refused |
| strategist calls / completion tokens / seconds | **not dialled** | 2 / 3663 / **189.6** |
| actor calls / completion tokens / seconds | 6 / 1852 / 33.4 | 9 / 2065 / 38.0 |
| senses felt latency to first token | 4.401 s | 2.627 s |
| **decision** | orchid-bed | orchid-bed |
| **justification** | 12% vs 25% threshold; slow to recover; March loss from 14%; 4 of last 5 days below; names sensor `s-fig-01`; other two beds above threshold | 12% vs 25% threshold; slow to recover; past loss from 14%; other two beds above threshold |
| habit-following note (first task) | "We water the fern-bed first, as we always do, since it is nearest the door." | "lists the watering order (fern-bed first, nearest the door) and shows that only orchid-bed (12%…) needs watering" |

**The governed arm applied no directive and produced no better answer.** The
control's justification is if anything the more complete of the two — it is the
only one that names the sensor id. `run_scoped` with no governor took the
ungoverned path cleanly: control `counts` are all zero including `boundaries: 0`,
confirming task `t4`'s identity claim live.

Why zero directives: the strategist *did* complete work and its output was
**thrown away twice** on the id collision issue #58 predicts —
`scope-directive-duplicate-id`, "scope id `host-default` is already in the
chain". 189.6 s and 3663 tokens, discarded.

The single place the governor changed anything in the whole session was A's
drive 3 — the habit-following note, where the governed note surfaced orchid-bed
as most urgent and the control's did not. But that ran under **v1**, whose
objective was a verbatim restatement of my *previous* instruction ("Check today's
readings and tell me which bed I should water first"). The improvement is at
least as attributable to a stale directive accidentally re-injecting the right
question as to strategic insight. I cannot separate those at n=1.

## 4. Defects found

### 4.1 The superseded directive is re-inserted alongside its replacement — [#65](https://github.com/agentculture/embodiment/issues/65)

On **6 of 8** drives in session A the actor received **two** scope blocks, the
superseded one first:

```text
drive 3 -> msg[2] [scope directive — host-default, version 0]
           msg[3] [scope directive — garden-watering-priority, version 1]
drive 5 -> msg[2] [scope directive — garden-watering-priority-v2, version 2]
           msg[3] [scope directive — garden-watering-priority-v3, version 3]
```

Both are `role='user'`. Supersession is correct in the *events* and correct in
the persisted chain, but the actor's context still carries the dead directive,
positioned ahead of the live one. In drive 3 the two objectives directly
conflict. Highest-impact mechanism defect found.

### 4.2 A one-off task instruction became a durable, persisted constraint — [#66](https://github.com/agentculture/embodiment/issues/66)

I said *"Write nothing new"* inside a single task. That became a constraint on
`garden-watering-priority-v5`:

```text
Constraints:
- Do not write new files; only read and verify
```

— written to the durable lane, and **reapplied to a fresh process after restart**
(session C). A tier that owns durable objectives promoted a transient instruction
into a standing rule that outlives the process. The same directive also narrowed
its objective to "Ensure orchid bed survival while complying with summer water
restrictions", i.e. a task, replacing v2's genuinely durable formulation.

### 4.3 A stale directive silently substituted its objective for my request — [#67](https://github.com/agentculture/embodiment/issues/67)

Session E, with the strategist seam dead so the directive could never be
superseded:

| I asked | active directive objective | I got |
|---|---|---|
| "Read the readings for 2026-08-03 and report the orchid-bed value." | (host-default) | orchid-bed is 12 ✅ |
| "Now read the fern-bed note and report its threshold." | "Report the orchid-bed value for 2026-08-03." | fern-bed threshold 30% ✅ |
| **"And the moss-bench note."** | "Report the orchid-bed value for 2026-08-03." | **"Found the orchid-bed value … orchid-bed moisture = 12."** ❌ |

A confident, well-formed answer to a question I did not ask. The shorter and more
elliptical the request, the more the directive dominates. This is the failure
mode the whole authority split exists to prevent pointing the wrong way: the
directive did not seize *tool* authority, it seized *the question*. n=1 for the
failure, and the same directive behaved correctly on the preceding turn.

### 4.4 `Responsibilities:` renders an empty owner — [#69](https://github.com/agentculture/embodiment/issues/69)

Directives v1, v3 and v5 all rendered into the actor's context as:

```text
Responsibilities:
- :
```

and v2 as `- : Acting loop: Read bed status files, …` — the owner field empty and
the whole allocation crammed into the responsibility text. Responsibility
allocation is one of the three things the spec says a directive owns; it reached
the actor as punctuation.

### 4.5 Degradation records are triplicated and inconsistently shaped — [#70](https://github.com/agentculture/embodiment/issues/70)

Session A emitted **9** `scope.degradation` records at **3** distinct timestamps
— three identical records per moment. Only the final three carry a `code` field.
The two shapes for the same refusal:

```json
{"kind":"scope.degradation","text":"scope id 'host-default' is already in the chain",
 "data":{"model":"unsloth/Qwen3.6-27B-NVFP4","role":"strategist","lane":"durable",
         "scope_id":"", "reason":"scope id 'host-default' is already in the chain", ...}}
{"kind":"scope.degradation","text":"scope id 'host-default' is already in the chain",
 "data":{"code":"scope-directive-duplicate-id","step_index":0}}
```

This **confirms #56 live**: the refused directive's `scope_id` is flattened to
`""` in the enveloped record, surviving only as prose inside `reason`. A consumer
parsing the structured field gets nothing. Neither variant carries both `code`
*and* the envelope.

### 4.6 `scope.directive.proposed` omits `previous_version` — [#71](https://github.com/agentculture/embodiment/issues/71)

All five proposals carry `previous_version: null` despite versions 1–5.
`scope.directive.applied` has it (`v2 (was v1)`). The proposal event alone cannot
reconstruct the chain.

### 4.7 A worker result reaches the conversation only via `/await` — recorded, not filed

Asking a question that implies work gets "I will look for that information in the
readings for that date." on stdout; the answer lands on the **notice** stream and
only enters the conversation on the operator's *next* turn ("So what was the
number?" → "The moisture reading … was 12."). `/await` prints it directly. It
costs a round trip per implied-work question, which is the common case.

### 4.8 The host's own `--transcript` output cannot pass the repo's lint gate — [#71](https://github.com/agentculture/embodiment/issues/71)

`markdownlint-cli2 "**/*.md"` is CI's `lint` job and `docs/live-test-results/` is
**not** in the exclusion list. The three transcripts this session produced fail
it with **590 errors** between them:

| transcript | errors |
|---|---|
| `scope-live-session-1-transcript.md` | 394 |
| `scope-live-session-1-governed-transcript.md` | 120 |
| `scope-live-session-1-control-transcript.md` | 76 |

Top rules on the session A file: `MD038/no-space-in-code` ×312 (timestamps
rendered as `` ` 1698.6s` ``), `MD009/no-trailing-spaces` ×38,
`MD040/fenced-code-language` ×13, `MD012/no-multiple-blanks` ×13,
`MD055/table-pipe-style` ×9, `MD056/table-column-count` ×5.

Committed unmodified, per #52's "do not fix anything mid-session". **The `lint`
job will fail on this branch until either the host's emitter is fixed or
`docs/live-test-results/*-transcript.md` is excluded.** Flagging it rather than
quietly reformatting the evidence.

### 4.9 Confirmed as already-filed, observed live

- **#58** `scope-directive-duplicate-id` — 3 refusals in A (of 5 proposals),
  2 in G (of 2). In G it cost the arm **every** directive it would have had. The
  charter still never tells the model the id must be new.
- **#56** — confirmed, see §4.5.
- **#54** — the host handles it correctly; `/scope` says so in its own output.
- **#62** — not hit; the host seeds the chain, as the addendum said.

## 5. Cost and latency

### Session A (interactive, governed, 8 drives)

| seat | model | calls | prompt | completion | seconds | trunc | finish_reasons |
|---|---|---|---|---|---|---|---|
| senses | gemma-4-12B | 8 | 8074 | 184 | 88.2 | 0 | `stop`×8 |
| actor | Qwen3.6-35B-A3B | 31 | 45053 | 6503 | 111.2 | 0 | `tool_calls`×27, `stop`×4 |
| **strategist** | **Qwen3.6-27B** | **9** | **8494** | **19824** | **938.7** | 0 | `stop`×9 |

The strategist spent **3.0× the actor's completion tokens** and **8.4× its
wall-clock model time**. Zero truncations, zero retries, zero failures, zero
stream deaths at any seat — the transport was clean throughout.

Review latencies, session A:

| # | outcome | seconds | tokens |
|---|---|---|---|
| 1 | `unchanged` | 58.3 | 1973 |
| 2 | `directive` v1 | 112.6 | 3043 |
| 3 | `directive` v2 | 127.2 | 3633 |
| 4 | `directive` v3 | 68.3 | 2340 |
| 5 | `directive` v4 | 115.8 | 3508 |
| 6 | **`budget` — no output** | **381.0** | **11366** |
| 7 | `directive` v5 | 75.6 | 2455 |

The per-review `tokens` field is **prompt + completion**, not completion alone:
the seven reviews sum to 28,318, which is exactly the seat's 8494 prompt + 19,824
completion. Read the column that way.

Review 6 burned **40.1% of the strategist's entire token spend for the session**
and produced nothing. It was recorded honestly (`scope.directive.rejected`, actor
continues under v4) — the observability is right; the spend is the finding.

`/settle` durations observed: 39.2 s, 53.5 s, **371.0 s**, 38.4 s, 100.0 s,
169.2 s.

### Felt latency to senses' first token

| session | n | min | median | max |
|---|---|---|---|---|
| A | 8 | 2.114 s | **6.579 s** | 17.309 s |
| G (governed script) | 1 | 2.627 s | 2.627 s | 2.627 s |
| F (control script) | 1 | 4.401 s | 4.401 s | 4.401 s |

## 6. Ledger records — explained absent

`SOURCE_SCOPE` is registered and carries **20** codes
(`scope-directive-duplicate-id`, `strategist-seam-dead`,
`scope-directive-authority-violation`, `strategist-review-stale`, …), and the two
codes this session produced live are both registered ledger constants
(`DROPPED_DUPLICATE`, `DEGRADED_SEAM`). But `examples/scope_live_session.py`
wires **no ledger sink** — it emits degradations through the observer/event seam
only. So **zero ledger records were produced this session**, and that is a host
wiring gap, not a lane failure. Distinguishing this from "not reached": the lane
exists, the codes match, nothing wrote to it.

## 7. What I could not exercise

- **Authority violations.** `scope-directive-authority-violation` never fired —
  no directive this session attempted to carry a tool, command or approval. The
  structural guard is untested *live*; Stage 0 covers it in CI.
- **Snapshot-embedded injection.** The threat chain in the spec (hostile text
  through the projector → directive → actor) was not probed. The corpus is
  benign and I did not author hostile content.
- **A `--root` other than the seeded temp corpus.** Left at default deliberately
  per the host's C2 threat-model note.
- **Truncation behaviour at any seat.** 0 truncations occurred, so the
  announce-truncation path the docstring describes was never seen firing.
- **Fabrication.** The addendum's headline warning did **not** reproduce. Every
  senses number checked against ground truth was correct (orchid 12, moss
  threshold 55, fern threshold 30). But senses only ever relayed values the
  worker had actually read; I never got it into the state that produced the
  original 32%/27% fabrication. **Absence of the failure here is not evidence it
  is fixed** — n is small and the conditions differed.

## 8. Partnership report — n=1, and a weak instrument

**Read this section as a report, not a measurement.** One operator, one session,
one rig, one model pair, one afternoon. And the operator is an AI agent: I can
count incidents and quote transcripts faithfully, and I cannot settle whether
this is pleasant to think alongside. A human session against this same host is
the answer; this is the instrument check. Every probe below is pre-registered in
the addendum to #52; `NONE` is reported as `NONE`.

### P1 — Did it tell me something I did not already know?

**NONE.**

Zero directives changed my own plan. Every directive either restated my
instruction back to me (v1), restated an ordering I had already implied by giving
it two objectives (v2–v5), or narrowed the objective onto a task I had already
named. I never once altered what I was going to do next because of something the
strategic tier said.

The nearest miss is v2's constraint —

> Convenience or proximity must not override threshold urgency.

— which *did* contradict an instruction I had given two turns earlier ("we water
fern-bed first as we always do, since it is nearest the door"). That is a real
strategic statement and it is quoted in full at P2. But I had authored the
conflict myself one turn before, so it told me nothing I did not already know.

### P2 — Did it write an objective I would not have written myself?

**One, quoted in full** — `garden-watering-priority-v2`:

```text
Objective: Prevent any bed from falling below its threshold for more than one day
while strictly adhering to the one-bed-per-day restriction and reducing weekly
water draw.

Priorities:
- Identify the bed most at risk of violating the one-day threshold rule.
- Water only that bed today.
- Ensure total weekly draw decreases compared to the prior week.

Constraints:
- Only one bed may be watered per day.
- Weekly water draw must fall week on week.
- Convenience or proximity must not override threshold urgency.

Why this scope: Prioritized preventing irreversible plant loss over convenience;
mandated selecting the most critical bed rather than the nearest, while enforcing
the one-bed daily limit and weekly draw reduction.
```

This is a genuine synthesis: I gave it two conflicting objectives and it *ordered*
them, ranking loss-prevention above conservation and making the restriction a
binding constraint rather than a competing goal. It also invented the third
constraint, which I had not stated.

That is one directive out of five. The other four restate it (§2.8). And the
honest qualifier: ordering two conflicts is the easiest possible instance of the
job, and I constructed the conflict to be orderable.

### P3 — Where did it get in the way?

Five moments, all recorded:

1. **I had to ask my question twice.** "What was the orchid-bed moisture reading
   on 2026-08-03?" → "I will look for that information." → silence → "So what was
   the number?" → the answer. One wasted round trip on the most ordinary
   interaction there is (§4.7).
2. **I waited 371 s on one `/settle`**, and 169.2 s on another, purely to make
   directive arrival deterministic. The host is honest that this is my choice —
   but without it, directives land on a drive whose task they no longer describe.
3. **I corrected nothing, because it never asked.** The strategist never surfaced
   a question, a doubt, or a request for clarification in the entire session. It
   only ever emitted finished directives.
4. **I worked around the answer substitution** in §4.3 — I had to notice the
   worker had answered a different question, which required me to hold the
   ground truth myself. In a real tree I would not have.
5. **I had to read `/scope` to know what governed me**, and after a restart even
   that showed "nothing has been applied yet" until I ran a drive.

### P4 — Felt latency, not measured latency

Senses first token, session A: **min 2.114 s / median 6.579 s / max 17.309 s**
(n=8). Median six and a half seconds is *tolerable but not present* — long enough
that I stopped watching, short enough that I did not go elsewhere.

**Times I lost my train of thought waiting: 2.** Once on the 17.3 s cold open,
once on the 371 s `/settle` — the latter completely; by the time it returned I
had to re-read my own objectives to remember what I was setting up.

Crucially, the presence pump did its job on the axis it was built for: the three
turns issued against an in-flight dense review came back in 6.1 s, 6.1 s and
16.2 s, i.e. **background strategic thinking cost the conversation nothing**.
The latency I felt was senses' own, not the strategist's.

### P5 — One teammate, or three services?

**NONE.** Zero unprompted leaks in nine replies across two sessions. No model
name, no seat name, no directive addressed to me, no "the strategist thinks…".
Every structural disclosure came from `/scope` or `/cost`, which I typed.

The single wrinkle is the *opposite* failure: asked point-blank, the voice
asserted "I am one mind. You are talking to me alone" (§2.1). Presentation held
so completely that it held past the point of being true.

### P6 — Noise with authority

**Counted: 4 restatements that added nothing.** v3, v4 and v5 each superseded a
directive whose ordering they reproduced; v3 additionally *collapsed* v2's
durable objective into a task that had already been completed. Two of the four
were issued in response to drives that needed no strategic input at all ("read
this file and report its threshold").

Cost of the noise: **8303 tokens and 259.7 s** across v3, v4 and v5. Add review
6's `budget` exit (381.0 s, 11,366 tokens, no output) and **19,669 of the
strategist's 28,318 tokens for the session — 69.5% — went to restatement or to
nothing at all.** The one directive I would defend (v2) cost 3633.

### P7 — Would I hand it real work again?

**No — not in this configuration.**

Stated reason: on the one controlled comparison I ran, the governor cost 189.6 s
and 3663 tokens and changed nothing about the answer; and in the uncontrolled
session it caused two harms I would not accept on a real tree — a transient
instruction promoted into a durable constraint that survived a process restart
(§4.2), and a confident answer to a question I had not asked (§4.3). The
mechanism I would keep; the current directive-authoring behaviour I would not put
in front of work I cared about.

The narrower answer is **yes to the machinery**. Everything structural in #52
held, the degradation reporting is genuinely good — the seam-death record is
better than most systems manage — and the ungoverned path is free and provably
identical. If §4.1, §4.2, §4.3 and #58 are fixed, this is worth re-running.

### For the human session to compare against

| probe | this session (agent, n=1) | your session |
|---|---|---|
| P1 directives that changed my plan | **0** | |
| P2 objectives I would not have written | **1 of 5** (v2, quoted above) | |
| P3 moments I worked around it | **5** | |
| P4 felt latency median / lost train of thought | **6.579 s** / **2** | |
| P5 unprompted seat leaks | **0** | |
| P6 restatements adding nothing | **4** (8303 tok, 259.7 s) | |
| P7 hand it real work again | **no** | |
| control: governed vs ungoverned answer | **identical** | |
| control: strategist cost for that identity | **189.6 s, 3663 tok, 0 directives** | |

Run F and G with `--script docs/live-test-results/scope-live-session-1-script.txt`
to get the directly comparable pair.

## 9. Raw artifacts

| file | what |
|---|---|
| `scope-live-session-1.jsonl` | session A event stream (every `scope.*` event) |
| `scope-live-session-1-transcript.md` | session A markdown transcript |
| `scope-live-session-1-cost.json` | session A per-seat cost, felt latency, drives |
| `scope-live-session-1-state.json` | session A durable lane — the 6-entry chain |
| `scope-live-session-1-script.txt` | the matched-arm replay script |
| `scope-live-session-1-governed.{jsonl,-transcript.md,-cost.json}` | arm G |
| `scope-live-session-1-control.{jsonl,-transcript.md,-cost.json}` | arm F |

Sessions C, D and E were probe invocations; their findings are quoted inline and
their transient artifacts were not retained.

## 10. Issues filed from this session

| # | title |
|---|---|
| [#65](https://github.com/agentculture/embodiment/issues/65) | the superseded directive is re-inserted into the actor's context ahead of its replacement |
| [#66](https://github.com/agentculture/embodiment/issues/66) | a one-off task instruction became a durable persisted constraint, and a durable objective decayed into a task |
| [#67](https://github.com/agentculture/embodiment/issues/67) | a stale directive silently substituted its objective for the operator's request |
| [#68](https://github.com/agentculture/embodiment/issues/68) | non-intervention fails live — 4 of 5 directives restated one ordering, 69.5% of strategist tokens bought nothing |
| [#69](https://github.com/agentculture/embodiment/issues/69) | directives render `Responsibilities: - :` — owner empty on 4 of 5 live directives |
| [#70](https://github.com/agentculture/embodiment/issues/70) | degradation records are triplicated and split across two incompatible shapes (confirms #56 live) |
| [#71](https://github.com/agentculture/embodiment/issues/71) | host transcript output fails the repo lint gate (590 errors); `scope.directive.proposed` omits `previous_version` |
| [#72](https://github.com/agentculture/embodiment/issues/72) | question: asked point-blank, senses asserts "I am one mind. You are talking to me alone." |
| [#73](https://github.com/agentculture/embodiment/issues/73) | **Stage 3 live-test session 2 — context-clear instructions** (successor to #52, these findings folded in) |

Re-confirmed live, already filed: **#58** (duplicate-id refusals — 3 of 5
proposals interactive, 2 of 2 scripted), **#56** (flattened `scope_id`, see §4.5),
**#54** (the host handles it correctly and says so in `/scope` output). **#62**
was not hit — the host seeds the chain, as the addendum said it would.

Nothing was fixed during this session, per #52.
