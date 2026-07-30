# The League commander experiment — Gemma commands, Qwen drives the units

**Run:** 2026-07-31 · **Task:** t28 · **Issue:**
[#36](https://github.com/agentculture/embodiment/issues/36) · **Deviations:**
`d12`, `d13`, `d14` · **Rule:**
[league-commander-preregistration.md](league-commander-preregistration.md),
committed before the first measured match

> Gemma 4 31B as coordinator / architect / final judge, with Qwen 3.6 27B as
> the primary developer — for example, Gemma playing League of Agents with Qwen
> as unit agents.

Is that better than what we run today — and if it is, is it better because
**Gemma is the better commander** or because **hierarchy beats a flat mind**?

## The short answer

**On the primary arena the data does not say, and it says so at a perfect
ceiling: all four arms won 19–0 in all twelve matches.** That is the
pre-registered `INCONCLUSIVE`, not "no effect", and the escalation ladder was
climbed rather than published around.

But two things the ceiling did **not** hide, because they are measured per
level rather than per match:

1. **Hierarchy cost 2.4× a flat Qwen and 4.4× a flat Gemma, for the identical
   result.** 25,459 tokens per match in arm B against 10,703 flat-Qwen and
   5,827 flat-Gemma.
2. **The commander never once overrode a unit — 0 of 30 opportunities, in
   BOTH directions.** Gemma-on-top accepted every Qwen proposal, and
   Qwen-on-top accepted every Gemma proposal. Pre-registered prediction **P2
   is FALSIFIED at the 0.0 endpoint**: on this problem the commander is a
   relay, not a judge, and arms B and C are the *unit* model playing with an
   expensive rubber stamp on top.

That second finding is the one worth carrying out of this experiment. It is
not a claim that a commander can never add value; it is a measurement that at
this decision granularity, on this problem, with these prompts, it did not.

## What ran, and what did not

| lane | arm | n | status |
|---|---|---|---|
| primary — `c-skirmish-1` | B, C, A-qwen, A-gemma | 3 each | **complete** (12 matches) |
| escalation E1 — `c-frontier-1` | A-gemma | 3 | **complete** |
| escalation E1 — `c-frontier-1` | B, C, A-qwen | 1 each | see [E1](#escalation-e1--the-bigger-decision-surface) |
| escalation E2 — n doubled | — | — | **ABSENT** — not reached; E1's decision surface is 5.6× larger and was the higher-value rung |
| escalation E3 — the finer metric | applied | — | applied to the primary series; also `INCONCLUSIVE` |

**Two departures from the pre-registration, both reported rather than
absorbed:**

- **`N_MATCHES` was 5; the series ran at n=3.** The rig is shared — task t23
  was running `challenge_register.py` against the same gateway throughout, at
  95% GPU — and 3 arms × 5 matches at a measured 5–12 minutes each did not fit
  beside it. The direction bar is derived (`ceil(0.8 × n)`), so n=3 requires
  3 of 3 rather than 4 of 5 and no threshold was chosen after the fact.
- **The ceiling clause compared multisets, not sets.** Found while folding the
  first arms and fixed in the open (commit `841c90c`): `compare()` decided
  "the instrument had variance to detect with" by comparing *sorted lists*, so
  two arms scoring an identical value at unequal n compared unequal and a flat
  ceiling would have been published as `NO_EFFECT`. The pre-registered prose
  says "the two arms' margin **sets**". The code was a buggy expression of the
  rule rather than a different rule, and the correction only ever moves a
  verdict **toward** `INCONCLUSIVE` — the unflattering direction.

## How the commander and the units are wired

**No `embodiment/` source was changed, and none needed to be.** The whole
experiment is host wiring on a seam that was already model-agnostic; this run
is the first time it has been exercised *asymmetrically*.

```text
run(complete=<COMMANDER model>, executor=CommanderExecutor,
    max_steps=6, subagent=unit_seam, spawn_allowance=1)      ← ROLE_CORTEX
      └─ tool `consult_unit` returns a SpawnRequest
            └─ unit_seam(call):
                 run(complete=<UNIT model>, executor=UnitExecutor,
                     max_steps=call.max_steps,               ← ROLE_SUBAGENT
                     spawn_allowance=call.allowance,
                     lineage=call.lineage, subagent=None)
      └─ tool `order`  ← the ONLY call that reaches the arena
```

- **Commander = the top-level `run()`.** It already holds final authority in
  the strongest available sense: `order` is the only tool whose result the
  harness submits to `league cmatch act`. The unit agent has no such tool and
  no path to one.
- **Unit agent = the child the injected `SubagentFn` drives**, with its own
  `complete`. Its one tool is `propose`.
- **The protocol, verbatim:** `SubagentCall.allowance` → `spawn_allowance`,
  `.lineage` → `lineage`, `.max_steps` → `max_steps`.
- **Spawn allowance = 1.** `spawn_allowance` defaults to `NO_SPAWNS`, at which
  no child can exist at all. One is the minimum strictly-positive value and is
  exactly right here: the child is minted with `attenuate(1) == 0`, so depth is
  bounded at 1 and the whole subtree at `2**1 - 1 == 1` — one decision point,
  one unit, one consultation. **30 spawns requested, 30 granted, lineage depth
  1 on every one, allowance remaining 0 on every one.**
- **Levels are typed** with `ROLE_CORTEX` (top level) and `ROLE_SUBAGENT`
  (child) per [colleague#352](https://github.com/agentculture/colleague/issues/352).
  With no configured identity the framing functions are byte-identical no-ops
  — asserted in the suite, not assumed — so the level typing costs the
  measurement nothing.

The arena is reached only through its own CLI in a subprocess
(`team register`, `cmatch new|show|act|tick`, `match score`); a test asserts
the harness touches no other verb, and `import league` appears nowhere.

### Why the continuous lane

`league cmatch` asks for exactly **one unit's action at a decision point** —
the instant that unit goes idle — where `league match` asks for a whole-team
turn order. So the commander/unit split is **native**: the arena poses the
unit-level question, the unit agent answers it, and the commander adjudicates.
Nothing about the split is simulated by the harness.

The roster declares **a model per unit** (`--agent ID:MODEL:ROLE`), so the
arena keeps its own record of who played what, independent of ours — and each
match log header records `driver_kinds.red == "bot"`, the fixed house opponent,
identical in every arm. **P4 holds: 12 of 12 headers agree with the report.**

## The primary series — `c-skirmish-1`, 12 matches

### Per-arm outcome

| arm | commander | units | n | margins | mean margin | W-D-L | mean blue grade | validity |
|---|---|---|---|---|---|---|---|---|
| **B** | Gemma 4 31B | Qwen 3.6 27B | 3 | [19, 19, 19] | +19.00 | 3-0-0 | 2350 | passes all gates |
| **C** (mirror) | Qwen 3.6 27B | Gemma 4 31B | 3 | [19, 19, 19] | +19.00 | 3-0-0 | 2350 | passes all gates |
| **A-qwen** (flat) | *none* | Qwen 3.6 27B | 3 | [19, 19, 19] | +19.00 | 3-0-0 | 2350 | passes all gates |
| **A-gemma** (flat) | *none* | Gemma 4 31B | 3 | [19, 19, 19] | +19.00 | 3-0-0 | 2350 | passes all gates |

Twelve matches. Twelve identical results. No arm is `VOID` — every validity
gate passes, so **the tie is a fact about the arena, not about the
instrument**. That distinction is the whole reason the gates were written
first.

### Cost, per LEVEL — the operator's other question

| arm | level | model | calls | prompt tok | completion tok | total tok | **tok / match** |
|---|---|---|---|---|---|---|---|
| B | commander | Gemma 4 31B | 30 | 39,543 | 1,955 | 41,498 | **13,832** |
| B | unit | Qwen 3.6 27B | 15 | 20,384 | 14,497 | 34,881 | **11,627** |
| C | commander | Qwen 3.6 27B | 30 | 45,742 | 13,703 | 59,445 | **19,815** |
| C | unit | Gemma 4 31B | 15 | 18,616 | 699 | 19,315 | **6,438** |
| A-qwen | flat | Qwen 3.6 27B | 15 | 19,302 | 12,807 | 32,109 | **10,703** |
| A-gemma | flat | Gemma 4 31B | 15 | 16,842 | 639 | 17,481 | **5,827** |

**Per match: B = 25,459 tokens, C = 26,253, A-qwen = 10,703, A-gemma = 5,827.**

- **P1 holds, with a number.** Hierarchy cost **2.4×** the flat Qwen baseline
  and **4.4×** the flat Gemma one, for a result identical to both.
- **The commander's cost is almost entirely PROMPT.** Gemma-on-top: 39,543
  prompt against 1,955 completion — a 20:1 ratio. It re-reads the whole board
  twice per decision (once to ask, once to decide) and writes 65 tokens. A
  large commander on this shape is paying to *read*, not to think.
- **Two commander calls per decision is structural**, not overhead that could
  be tuned away: consult, then order. That is what doubles the prompt bill.
- **The two models are not comparable per token.** Qwen is a thinking model:
  it spent 14,497 completion tokens across 15 unit calls (≈966 each) where
  Gemma spent 699 (≈47 each), a 20× difference in what a "call" costs. Any
  price comparison that ignores this is measuring the wrong thing.

### The instrument

| arm | decisions | parked | finish reasons | `length` | spawns granted | override rate | retries | contention calls |
|---|---|---|---|---|---|---|---|---|
| B | 15 | 0 | `tool_calls`: 45 | 0.00% | 15/15 (100%) | **0.0** | 0 | 0 |
| C | 15 | 0 | `tool_calls`: 45 | 0.00% | 15/15 (100%) | **0.0** | 0 | 0 |
| A-qwen | 15 | 0 | `tool_calls`: 15 | 0.00% | n/a | n/a | 0 | 0 |
| A-gemma | 15 | 0 | `tool_calls`: 15 | 0.00% | n/a | n/a | 0 | 0 |

**120 model calls. Every one finished `tool_calls`. Zero `length` finishes at
`max_tokens=16000`, zero parked units, zero invalid indices, zero retries, zero
calls over the 600-second contention line.** The 16000-token budget was the
right call and the instrument recorded nothing to explain away.

- **V1 (truncation)** — 0% against a 10% ceiling. Pass.
- **V2 (orders reached the arena)** — 0 parked against a 20% ceiling. Pass.
- **V3 (the hierarchy is real)** — 100% spawn grants against a 90% floor. The
  commander consulted at every single decision point. Pass. **This matters:
  the 0.0 override rate is not "the commander never asked" — it asked every
  time, read the answer, and agreed every time.**
- **V4 (same arena, same opponent)** — every log header agrees. Pass.

### The pre-registered fold, applied without amendment

| hypothesis | delta | direction | identical sets | **verdict** |
|---|---|---|---|---|
| **H1 — hierarchy vs flat** (B vs A-qwen) | 0.00 | 3 of 3 required, 3 met | **yes** | **`INCONCLUSIVE`** |
| **H2 — Gemma on top vs Qwen on top** (B vs C) | 0.00 | 3 of 3 required, 3 met | **yes** | **`INCONCLUSIVE`** |
| **E3 (secondary) — blue unit grade** | 0.00 | — | **yes** | **`INCONCLUSIVE`** |

The ceiling clause fired exactly as written: identical margin sets means the
design could not resolve the question, and that is `INCONCLUSIVE` — **not**
"no effect". Four experiments in this cycle reached this point and stopped
([issue #35](https://github.com/agentculture/embodiment/issues/35)); this one
climbed.

## The finding the ceiling did not hide: the commander is a relay

**0 overrides in 30 opportunities**, across both hierarchical arms, with the
commander consulting at 15 of 15 decision points in each. The pre-registered
P2 said an override rate at either endpoint means "the hierarchy is decorative
and the arms are not measuring what they claim". It is at the 0.0 endpoint, in
both directions.

It is not that the commander was idle. The transcripts show it engaging with
the board — this is Gemma's own `consult_unit` question at the opening
decision point, verbatim from
`league-commander-wiring-smoke-transcripts.jsonl`:

> We are at the start of the match. You are a defender. The control point
> cp-crossing is currently neutral and is the location for the ms-hold
> mission. The enemy has a unit (redb-u2) already at that location. Should we
> move to cp-crossing to contest it, or head to rn-home?

Qwen proposed index 1 ("contesting the nearby control point and engaging the
enemy unit already stationed there is our highest priority objective"), and
Gemma ordered index 1, giving its own reason. That is a *good* exchange. It is
also, measurably, one that changed nothing: the same order would have been
issued with no commander at all, for 5,827 fewer tokens.

**Three readings, and the honest one is the third.**

1. *The commander is worthless.* Not supported — a commander that agrees with
   a correct proposal is behaving correctly.
2. *The commander is a rubber stamp by construction.* Not supported either —
   V3 shows it consulted every time, and the questions are board-specific
   rather than boilerplate.
3. **The problem never presented a decision worth overriding.** With 5 decision
   points, 2–4 menu entries each, and a menu where the role-appropriate action
   is nearly always obvious (a harvester at a resource node gathers), there was
   no disagreement to have. **A commander is a mechanism for resolving
   disagreement, and this arena produced none.** That is a statement about the
   test, and it is why E1 exists.

## Escalation E1 — the bigger decision surface

`c-frontier-1`: three roles instead of two, **28 blue decision points per
match** instead of 5, time limit 30 instead of 20. Verified to run before the
pre-registration was committed, and chosen as rung 1 precisely because a
ceiling caused by too few decisions is not cured by more samples.

**It is not at a ceiling.** Flat Gemma scores **10–0** there, well inside the
range, where it scored the maximum 19–0 on `c-skirmish-1`.

<!-- E1-RESULTS -->

## Cost and contention, recorded because it must be visible

The rig was shared for the entire run: task **t23** was executing
`challenge_register.py` against the same gateway at 95% GPU utilisation
throughout. **Wall clock is therefore not a quality signal and is not used as
one anywhere above** — and in any case only the Qwen cortex is local, while
Gemma is proxied from a peer, so a latency gap between arms is a topology fact.

| arm | wall clock per match | what dominates |
|---|---|---|
| A-gemma | 21–25s | one proxied Gemma call per decision |
| B | 286–378s | one local Qwen unit call per decision |
| A-qwen | 198–351s | one local Qwen call per decision |
| C | 532–710s | **two** local Qwen commander calls per decision |

Measured Qwen latency ranged 37–177 seconds per call against a 55-second
single-shot pilot; **no call crossed the 600-second contention line, so no
retry fired and none is recorded.** The `retries` and `contention_calls`
columns are 0 because nothing degraded, not because nothing was watched.

## Artifacts

Everything below is committed beside this document; the verdict is not the
only thing kept.

| file | what it is |
|---|---|
| `league-commander.jsonl` | one record per primary-series match, with the per-decision records embedded |
| `league-commander-transcripts.jsonl` | **every model call**: full messages, full response, `finish_reason`, tokens, latency, retries |
| `league-commander-config.json` | the complete configuration, including that `--seed` is metadata only |
| `league-commander-logs/` | the **arena's own** match log per match — its record, not ours |
| `league-commander-frontier*.jsonl` | the same three artifacts for escalation E1 |
| `league-commander-wiring-smoke*.jsonl` | the pilot match, named as a pilot and counted nowhere |

Reproduce with:

```bash
uv run python examples/league_commander.py play --root /tmp/lc --arm all --n 3 --live \
    --out docs/live-test-results/league-commander.jsonl \
    --transcripts docs/live-test-results/league-commander-transcripts.jsonl \
    --config-out docs/live-test-results/league-commander-config.json \
    --logs docs/live-test-results/league-commander-logs
uv run python examples/league_commander.py analyse \
    --out docs/live-test-results/league-commander.jsonl
```

## What this does and does not license

**It does license:** the claim that `embodiment.loop.run` supports a different
model per level today, with no source change, and that the delegation bound is
observable in the artifact (30 spawns, all granted, depth 1, allowance 0).

**It does license:** the cost numbers. Paying for a large commander on this
shape costs 2.4–4.4× and its bill is 95% prompt.

**It does NOT license** any claim that Gemma is or is not the better commander.
Both hierarchical arms and both flat arms produced identical results on the
primary arena, and the escalation is partial. *The data does not say.*

**It does NOT license** treating the 0.0 override rate as a property of these
models. It is a property of this problem: a decision surface that offered
nothing to disagree about. The next version of this experiment should
*construct* disagreement — a scenario where the role-obvious move is wrong —
rather than hope for it.
