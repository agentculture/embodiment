# The League commander experiment — pre-registration

**Written:** 2026-07-31 · **Task:** t28 · **Issue:**
[#36](https://github.com/agentculture/embodiment/issues/36) · **Deviations:**
`d12`, `d13`, `d14` · **Rig:** see [README](README.md)

Committed **before the first measured match**. The house rule this repo now
runs on: the decision rule has to exist before the data does, or it gets
written to fit whatever came back. Four experiments in this cycle
(t18, t19, t23, t26 — see [issue #35][i35]) died at a ceiling, and the ones
that stayed honest are the ones whose ceiling clause was written first.

[i35]: https://github.com/agentculture/embodiment/issues/35

## The operator's question

> Gemma 4 31B as coordinator / architect / final judge, with Qwen 3.6 27B as
> the primary developer — for example, Gemma playing League of Agents with Qwen
> as unit agents.

Is that arrangement better than what we run today, and if so, is it better
because **Gemma is the better commander**, or because **hierarchy beats a flat
mind**? Those are two different claims and a two-arm comparison cannot tell
them apart.

## What runs before this file was committed

Two kinds of pilot, named here so they are never mistaken for data:

1. **Four arena pilots** driven by a hard-coded policy (`menu[0]`,
   `menu[-1]`) — no model was called. Their only purpose was to time a match,
   count blue decision points, and confirm `c-skirmish-1` and `c-frontier-1`
   run end to end through `league cmatch`. Outcomes observed: `9–10` and `8–2`
   on `c-skirmish-1` (5 and 7 blue decision points), `0–19` on `c-frontier-1`
   (13 blue decision points). Not counted, not data.
2. **Two single-shot model smokes** — one Qwen call and one Gemma call against
   a synthetic decision point, to confirm both roles answer a tool schema at
   all and to measure per-call latency. Not counted, not data.

## The architecture — host wiring only, no embodiment source change

`embodiment.loop.run` already lets the host pick a **different model per
level**. Nothing in `embodiment/` is touched by this experiment.

| the proposal | embodiment |
|---|---|
| coordinator / architect / final judge | the **top-level** `run()` — it already holds final authority |
| primary developer / unit agent | the **child** the injected `SubagentFn` drives, with its own `complete` |

- **Commander** = top-level `run(complete=<commander model>, subagent=…,
  spawn_allowance=1)`. Its tool surface is `consult_unit` (returns a
  `SpawnRequest`) and `order` (finishes the drive). The `order` call is what
  the arena executes: final authority is structural, not a prompt claim.
- **Unit agent** = the child drive, `run(complete=<unit model>, …)` with a
  single `propose` tool. It proposes; it never submits to the arena.
- `SubagentCall.allowance` is passed through as `spawn_allowance`, `.lineage`
  as `lineage`, `.max_steps` as `max_steps` — the whole protocol, verbatim from
  `embodiment/subagent.py`.
- **Spawn allowance = 1.** Strictly positive (at the default `NO_SPAWNS` no
  child can exist at all), and exactly one: the child is minted with
  `attenuate(1) == 0`, so depth is bounded at 1 and the subtree at
  `2**1 - 1 == 1`. One decision point, one unit, one consultation.
- Levels are typed with `embodiment.framing`: `ROLE_CORTEX` for the top-level
  acting loop, `ROLE_SUBAGENT` for the child, per
  [colleague#352](https://github.com/agentculture/colleague/issues/352). With
  **no configured identity** the framing functions are byte-identical no-ops —
  asserted, not assumed — so the framing seam is exercised without smuggling a
  persona into the measurement.

## The arena — why the continuous lane

`league cmatch` asks for exactly **one unit's action at a decision point** (the
instant that unit goes idle). `league match` asks for a whole-team turn order.
So the commander/unit split is **native** to the continuous lane rather than
simulated by a harness: the arena itself poses the unit-level question, the
unit agent answers it, and the commander adjudicates.

- Scenario **`c-skirmish-1`** (roles `defender`, `harvester`; time limit 20).
- Opponent: the **same fixed house bot in every arm** —
  `--driver red:bot`, league's in-harness greedy continuous policy. It is code,
  not a mind, and it never sees a byte of the seat contract.
- Our side is declared `--driver blue:stateless` — league's own word for "a
  fresh drive per decision point", which is what this harness does.
- The roster declares **a model per unit** via
  `league team register <id> --agent ID:MODEL:ROLE --apply`, so the arena keeps
  its own fairness record of who played what, independent of our report.
- State is a pure fold of the log, so a killed harness resumes from the same
  working directory. The harness's ledger is resumable for the same reason.

**Recorded now because it will otherwise be re-derived:** on `c-skirmish-1`
the `--seed` is **metadata only**. Two matches at seeds 7 and 99 produced a
byte-identical initial state (SHA-256 `6dc62720bd35d7a5…` over the state with
`match_id`/`seed` removed). The seeds below are therefore *labels*, not
worlds — the only source of between-match variation within an arm is the
model's own sampling at `temperature = 0.7`, and that is why the temperature is
non-zero and identical in every arm.

## The arms

| arm | commander (final authority) | unit agents | why it is here |
|---|---|---|---|
| **B** | Gemma 4 31B | Qwen 3.6 27B | the proposal |
| **C** | Qwen 3.6 27B | Gemma 4 31B | the **mirror — mandatory** |
| **A-qwen** | *none* | Qwen 3.6 27B decides every unit | flat baseline |
| **A-gemma** | *none* | Gemma 4 31B decides every unit | flat baseline |

B alone cannot be attributed: against today's arrangement it moves *who holds
final authority* and *whether the second model executes or advises* at the same
time. **C** separates architecture from model assignment. **A** is what says
whether hierarchy helps at all. Both flat baselines run so that "Gemma is
simply the better player here" is a hypothesis the data can kill, rather than
an alternative explanation left standing.

The flat arms are deliberately *not* today's shipped arrangement (Qwen actor
with a tools-off Gemma muse that proposes and never decides). Today's
arrangement is a third thing, and measuring it is
[muse-arms.md](muse-arms.md)'s lane, not this one's. This experiment's question
is commander-vs-flat, and the flat control has to differ from the hierarchical
arms in **one** variable — the commander — not two.

## Fixed configuration

Every number below is pinned by value in
`tests/test_league_commander_preregistration.py`, and the harness's own copies
are asserted equal to them there. Changing one after the first dial means
editing a test that says so out loud in a diff a reviewer sees.

| knob | value | why |
|---|---|---|
| `SPAWN_ALLOWANCE` | 1 | strictly positive, depth bounded at 1 |
| `COMMANDER_MAX_STEPS` | 6 | consult + order + the child's charged turns + slack |
| `UNIT_MAX_STEPS` | 3 | requested on the `SpawnRequest`; clamped down, never up |
| `FLAT_MAX_STEPS` | 4 | the flat arm's whole budget for one decision |
| `MAX_TOKENS` | 16000 | mandated for any Qwen level; used for **both** models so the cap is not an asymmetry to explain |
| `TEMPERATURE` | 0.7 | identical in every arm; the only source of between-match variation |
| `REQUEST_TIMEOUT` | 900s | above the 600s contention line, so a timeout is unambiguous |
| `MAX_RETRIES` / `RETRY_WAIT_SECONDS` | 3 / 30s | **transport** failures only |
| `N_MATCHES` | 5 per arm | 20 matches; see the cost section |
| `SEEDS` | 101–105 | labels, not worlds (above) |

**Retries are for contention, never for a better number.** A transport failure
(timeout, connection reset, HTTP 5xx) is retried and **every retry is
recorded** on the call. A *model* answer — an empty content, a `length` finish,
a refusal, an out-of-range menu index — is **data** and is never retried for a
nicer result. `finish_reason` is recorded on every single call.

**A `length` finish is truncation — an instrument event.** It is never scored
as a bad decision or a lost match. At `max_tokens=6000` this cortex returns
`finish_reason=length` with empty content and 12,857 characters of reasoning;
that already caused one published misreading in this repo
([designed-problem.md](designed-problem.md)).

## What is measured

**Primary metric — `margin = blue_points − red_points`** from
`league match score --json`'s `outcome`. Margin rather than blue's own points,
because the pilots showed blue scoring *higher* in a match it lost (9 vs 10)
than in one it won (8 vs 2): the opponent's score is part of the result.

**Secondary metrics**, reported always, decisive only under escalation E3:

- win / draw / loss per match;
- total blue **unit grade** (`match score --json`'s per-unit `grade`), a
  continuous 0–2000-ish number that does not saturate the way outcome points do;
- **override rate** — how often the commander's `order` differs from the unit's
  `propose`. This is the diagnostic for whether the commander is doing anything
  at all. It is not a quality measure and is never used as one.

**Cost, reported per LEVEL** — commander tokens and unit tokens separately, per
arm, with call counts, latency and `finish_reason` histograms. The entire
question is whether paying for a large commander is worth it, and a per-match
total cannot answer it.

## Predictions, as falsifiable statements

- **P1 — hierarchy costs more than flat.** Total tokens per match in B and C
  exceed both flat arms'. *Falsified if a hierarchical arm is cheaper.* (This
  one is nearly certain; it is stated because a prediction that cannot fail is
  worth nothing, and because the interesting number is **how much** more.)
- **P2 — the commander overrides sometimes.** Override rate in B and C is
  strictly between 0.0 and 1.0. *Falsified at either endpoint* — an always-
  accept commander is a rubber stamp and an always-override commander never
  read the proposal; either endpoint means the hierarchy is decorative and the
  arms are not measuring what they claim.
- **P3 — no measured commander effect at this n.** I expect all four arms to
  land within the no-effect band. Stated because it is what the four ceiling
  experiments in this cycle found, and because predicting an effect and finding
  none reads as a failed experiment when it is a result.
- **P4 — the arena's roster agrees with the harness.** Every match's
  `.league` team record names the unit model the harness reports, and every
  match log header records `driver_kinds.red == "bot"`.
  *Falsified by any disagreement*, and a disagreement voids the series.

## The decision rule

Applied to whatever comes back, without amendment.

Let `mean_margin(arm)` be the arm's mean margin over its completed matches.

**H1 — does hierarchy help at all?** Compare
`best(mean_margin(B), mean_margin(C))` against
`best(mean_margin(A-qwen), mean_margin(A-gemma))`.

**H2 — is Gemma the better commander?** Compare `mean_margin(B)` against
`mean_margin(C)`.

For either comparison, with `X` the higher arm and `Y` the lower:

- **EFFECT** — `mean_margin(X) − mean_margin(Y) ≥ MARGIN_EFFECT` (**3.0**)
  **and** X's per-match margin is `≥` Y's *median* margin in at least
  `DIRECTION_MIN` (**4**) of `N_MATCHES` (**5**) matches. Both conditions, or
  it is not an effect.

  The direction bar is stated at `N_MATCHES`, and escalation E1 runs at a
  smaller n. So it is carried as a **fraction derived from its own inputs**,
  `DIRECTION_FRACTION = DIRECTION_MIN / N_MATCHES = 0.8`, and the requirement
  at any n is `ceil(0.8 × n)` — 4 of 5, 3 of 3. Deriving it rather than writing
  a second literal is what stops the smaller run's bar from being picked after
  the data is in.
- **NO EFFECT** — `|mean_margin(X) − mean_margin(Y)| < NO_EFFECT_BAND`
  (**1.0**) **and** the two arms' margin *sets* are not identical — i.e. the
  instrument demonstrably had variance to detect with.
- **`INCONCLUSIVE`** — anything else, **and specifically the ceiling/floor
  case**: if every arm's margin set is identical, the design could not resolve
  the question and the verdict is `INCONCLUSIVE`, **not** "no effect".

**`INCONCLUSIVE` may not be published until the escalation ladder below has
been climbed or its rungs individually reported ABSENT with a reason.**

## Validity gates — an arm that fails one is `VOID`, not a loser

A void arm's numbers are reported in full and excluded from H1/H2. Voiding an
arm is a statement about the instrument, never about the model.

- **V1 — truncation.** `length` finishes must be
  `≤ LENGTH_FRACTION_MAX` (**0.10**) of an arm's calls. Above that the arm is
  `TRUNCATED`: the measurement is of the token cap, not the mind.
- **V2 — orders reached the arena.** Decision points that produced no order
  (the unit was parked) must be `≤ NO_ORDER_FRACTION_MAX` (**0.20**) of an
  arm's decision points. Above that the arm is `DEGRADED`.
- **V3 — the hierarchy is real.** In B and C, `SpawnRecord.outcome ==
  "granted"` on `≥ SPAWN_GRANT_MIN_FRACTION` (**0.90**) of decision points. A
  commander that never consults is not a commander, and the arm measures a flat
  Gemma or a flat Qwen wearing a hierarchical label.
- **V4 — same arena, same opponent.** Every arm plays `c-skirmish-1` against
  `--driver red:bot`, and each match log header says so. Any disagreement voids
  the **series**, not one arm.

## The ceiling risk, and the escalation path

**The risk, concretely.** `c-skirmish-1` gives blue only **5–7 decision
points** per match against a deterministic bot, and outcome points live in a
small integer range. Four arms can easily land on the identical margin — which
is exactly how t18, t19, t23 and t26 ended, and publishing `INCONCLUSIVE` from
a first ceiling is what issue #35 says to stop doing.

**The ladder, in this order, executed before any `INCONCLUSIVE` is published:**

1. **E1 — a bigger decision surface.** Re-run **all four arms** on
   `c-frontier-1` at `ESCALATION_N` (**3**) matches each: 3 roles instead of 2,
   **13** blue decision points instead of 5–7, time limit 30 instead of 20.
   Verified to run end to end before this file was committed. A ceiling caused
   by too few decisions is not fixed by more samples, so this rung comes first.
2. **E2 — more samples.** Double `N_MATCHES` (5 → 10) on whichever lane showed
   any between-match variance at all.
3. **E3 — the finer metric.** If outcomes still tie, decide on total blue unit
   grade, applying the same EFFECT/NO-EFFECT/`INCONCLUSIVE` rule with
   `GRADE_EFFECT` (**200**) in place of `MARGIN_EFFECT`. This verdict is
   labelled **secondary** wherever it appears and never presented as the
   primary result.

Each rung not climbed is reported **ABSENT with its reason** (rig time, rig
contention, budget) in the results document, prominently — not omitted.

## Cost, and the n that follows from it

Measured on 2026-07-31 in the two single-shot smokes named above, one decision
point's worth of prompt:

| model | latency | completion tokens | reasoning chars | `finish_reason` |
|---|---|---|---|---|
| Qwen 3.6 27B (local) | **55.0s** | 1058 | 3697 | `tool_calls` |
| Gemma 4 31B (proxied) | **3.6s** | 40 | 0 | `tool_calls` |

At ~6 blue decision points per match and ~3 calls per hierarchical decision
point (commander consult, unit propose, commander order):

| arm | calls/match | est. wall clock/match | × 5 matches |
|---|---|---|---|
| A-gemma | ~9 Gemma | ~40s | ~3 min |
| A-qwen | ~9 Qwen | ~8 min | ~40 min |
| B (Gemma cmd) | ~12 Gemma + ~6 Qwen | ~6 min | ~32 min |
| C (Qwen cmd) | ~12 Qwen + ~6 Gemma | ~11 min | ~57 min |

**Therefore `N_MATCHES = 5`, four arms, 20 matches ≈ 2.2 hours of serial wall
clock** before contention — which is what one shared rig can deliver in a
sitting while tasks t23 and t27 are also using it.

**Wall clock is not a quality signal across arms** and is never used as one:
only the cortex is local, Gemma is proxied through a peer, so a latency
difference between arms is a topology fact. It is recorded because contention
has to be visible, not because it means anything about the minds.

**Contention protocol.** A request exceeding `CONTENTION_SECONDS` (**600**) is
contention, not a result: the call is retried up to `MAX_RETRIES` after
`RETRY_WAIT_SECONDS`, and **every retry is recorded on the call** with its
reason. An arm abandoned to contention is reported **ABSENT**, never as a loss.

## Publication rule

Publish either way. Any arm that did not run is reported **ABSENT**,
prominently, at the top of the results document. An honest partial is the
required outcome; a series quietly reduced to the arms that finished is not.

Raw transcripts — every model call's messages, response, `finish_reason`,
tokens and latency — and the arena's own match logs are committed beside the
verdict, not just the verdict.
