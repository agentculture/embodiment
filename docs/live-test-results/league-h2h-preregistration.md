# League head-to-head — pre-registration

**Written:** 2026-07-31 · **Task:** t27 · **Issue:**
[#34](https://github.com/agentculture/embodiment/issues/34) · **Deviations:**
`d9` (the experiment), `d10` (head-to-head rather than solo runs) · **Rig:**
see [README](README.md)

Committed **before the first live dial**, with the harness
(`examples/league_h2h.py`) and its pin (`tests/test_league_h2h.py`) in the same
change. Every threshold below is asserted by value in that test file, so moving
one after a result exists is a deliberate edit a reviewer sees in a diff.

The only runs that happened before this file was committed were **offline**: a
hermetic dry run of the whole ladder with no network (see
[The identical-mind control](#the-identical-mind-control-run-offline-before-this-file)).
Its numbers are a property of the map, not of any model, and they are committed
alongside this file.

## The operator's question

*Should I switch models, or consolidate to one?*

Every live experiment in this cycle — `association-work`, `arena-series`,
`devague-legs`, `muse-arms`, `muse-latency` — used the **mixed** pairing: Qwen
3.6 27B as cortex, Gemma 4 31B as muse. **Nothing has measured whether one
model alone does as well.** That became an economic question rather than a
hypothetical when `devague-legs` measured the muse reaching identical verdicts
to the cortex on all 12 cases for roughly 1/25 the completion tokens.

## The three arms

Three arms, differing **only** in which model serves which cognitive role.

| arm | cortex | muse |
|---|---|---|
| `full-gemma` | `nvidia/Gemma-4-31B-IT-NVFP4` | `nvidia/Gemma-4-31B-IT-NVFP4` |
| `mixed` *(control — ships today)* | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | `nvidia/Gemma-4-31B-IT-NVFP4` |
| `full-qwen` | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` |

An arm is a **model pair**, never a code branch: `ARMS` is data and `seat_turn`
takes the two ids as arguments.

**Senses stays Gemma 4 12B (`coolthor/gemma-4-12B-it-NVFP4A16`) in all three
arms — and this harness never dials it.** The league seat has no
intake/speakback lane at all, so senses is a *recorded* constant, not a
measured one. Said plainly rather than implied, because a constant nobody calls
cannot confound anything and pretending otherwise would be padding.

## Round-robin: both teams are model seats

Three pairings, played at every rung: `gemma-vs-mixed`, `mixed-vs-qwen`,
`qwen-vs-gemma`.

`examples/league_seat.py` drives its rival with `rival_orders()` — a
deterministic scripted policy, **not a model**. Round-robin means **both**
teams are model seats, so this harness runs the seat logic twice per league
turn, once per team, with different model configs. league's
`match act <id> --team T` seam is symmetric, which is what makes that possible
without touching the arena.

`examples/league_seat.py` is **imported, never edited** — task t24 owns it.
`tests/test_league_h2h.py` asserts structurally that `rival_orders` is never
reached from this module and that `examples/league_seat.py` is untouched on
this branch.

## The ladder — and what replaced "+5 in size"

**The operator asked for boards "+5 in size". Board size is not a knob.**
`league arena list` ships exactly three fixed scenarios and `league match new`
has no grid parameter. `tests/test_league_h2h.py` asserts that the rung
contract carries no size field at all, so nobody later reads one in.

What was substituted, in rising order, is built from knobs that do exist: the
scenario ladder, league's engine-enforced `--max-actions` handicap, and a
genuinely fogged observation.

| rung | scenario | grid / turn limit | seed | turns | `--max-actions` | observation |
|---|---|---|---|---|---|---|
| **L1** | `skirmish-1` | 12×10, limit 30 | 4242 | 3 | uncapped | full (`match show`) |
| **L2** | `recon-1` | 14×12, limit 20 | 4243 | 3 | uncapped | full |
| **L3** | `skirmish-2` | 14×12, limit 16 | 4244 | 3 | **2** | full |
| **L4** | `skirmish-2` | 14×12, limit 16 | 4245 | 3 | **2** | **fogged** (`match brief --team T`) |

Why each rung is harder than the one below it:

- **L1 → L2.** A bigger board, a shorter limit, and a four-unit roster in which
  the *explorer* and the *planner* can neither gather nor capture. Role
  reasoning is engine-enforced in `recon-1`, not prompt convention.
- **L2 → L3.** `skirmish-2`'s turn limit sits below the best one-action-per-turn
  solo run and its two objectives are twelve tiles apart, and only **two of
  three** units may be ordered per turn. The seat has to choose.
- **L3 → L4.** The same board seen only through league's own fogged brief. At
  turn 0 a team knows fifteen cells, its own three units, **no control point
  and no resource node**. `--map-read fog` and `--unit-comms off` are declared
  for both teams alongside it.

**Fog is real, not metadata.** `--map-read`/`--unit-comms` are league's
*declared* fairness axes and change no engine behaviour on their own; they are
set for the record. What actually fogs L4 is swapping the observation source
from `match show` (omniscient) to `match brief --team T` (only what the team
has seen). Two decisions inside that swap, applied identically to both seats:

1. **Own-unit legality is carried over from `match show`.** `match brief` has no
   `legal_actions`, and a seat that cannot tell which squares its *own* units
   may step to is being tested on guessing league's movement rules. Nothing
   else crosses.
2. **Everything else comes from the brief only.** Unseen control points,
   resource nodes and enemy units are simply absent — which is what makes the
   rung hard.

**Climb until the arms separate, then stop.** Separation is the result;
exhausting the ladder is the fallback. Three experiments this cycle returned
`INCONCLUSIVE` at a ceiling — `association-work` (interaction 0.00),
`arena-series` (9/9 in every muse cell), `muse-arms` (arm A 8/8 correct). A
rising ladder is the direct structural fix.

## Fairness rules, fixed in advance

1. **Both arms play both colours at every rung.** Each pairing is played twice
   at the same seed: once with the first arm as blue, once with it as red.
   Without this, map or seat asymmetry confounds the whole result — and the
   offline control below measured that asymmetry to be real and, under fog,
   large.
2. **Seeds are fixed and recorded**: 4242 / 4243 / 4244 / 4245, one per rung,
   and both colour assignments of a pairing use the same seed, so the swap is
   played on an identical board.
3. **Both seats in a match receive identical budgets** — the same `max_steps`
   (8), the same `max_tokens`, the same temperatures, the same muse controls,
   the same system prompt and the same operator directive. `system_prompt()`
   and `directive_for()` take a rung and no arm or colour argument, and the
   test file pins that.
4. **One transport.** `MeteredSeam` builds the same request body against the
   same endpoint for every arm. The cortex lane carries `TOOL_SCHEMA`; the muse
   lane carries no schema at all, and that absence is the whole of "tools-off".
5. **Only the cortex model is local.** A `full-qwen` arm puts **both** roles on
   the one local GPU while `full-gemma` puts both on the proxy and `mixed`
   splits them. **Wall clock is therefore not a clean quality signal across
   arms** and is reported as cost, never as skill.

### Amendment 1 — the token budget, corrected before the first dial

**This task was briefed with "budget 3000+ for any Qwen seat". That number is
wrong and it was corrected to 16000 before anything was dialled.** The
amendment is recorded here rather than folded in silently, because a threshold
that moves after a result is not evidence.

The measured evidence, from two sibling tasks on this same rig:

- at `max_tokens=6000` on a hard problem the Qwen cortex returns
  `finish_reason=length` with **empty content and ~12,857 characters of
  reasoning** — it never reaches an answer;
- at `max_tokens=16000` on the same problem it answers correctly;
- `designed-problem.md` read an `exit=stopped` as the model abandoning the
  protocol. It was **truncation**. That reading has since been corrected.

Why this matters more here than anywhere else: "both seats get identical
`max_tokens`" is a fairness rule, and if that identical budget truncates Qwen
while Gemma answers comfortably inside it, **the head-to-head measures
truncation, not skill**, and `full-qwen` loses for a reason that has nothing to
do with which model is better. That is the confound most likely to have
silently invalidated this entire experiment.

There are two defensible fairness definitions and this experiment does not pick
one silently:

| definition | fair in | failure mode |
|---|---|---|
| **equal budget** — same cap for both | cost | truncates the hungrier model |
| **sufficient budget** — enough that neither truncates | capability | costs more wall clock |

**This experiment uses *sufficient*, and gets *equal* for free by reporting
tokens actually consumed as the cost column.** Gemma will simply not spend the
headroom. Capability is measured at quality; cost is measured by real
consumption rather than by a cap.

- `max_tokens = 16000` (cortex) and `muse_max_tokens = 16000`. The muse gets the
  same budget because in the `full-qwen` arm the muse *is* the thinking model;
  capping it short would truncate one arm's counsel for an instrument reason.
- `32000` is recorded as available headroom. If 16000 still truncates, the
  answer is to **raise it and say so in the write-up**, never to score the
  truncated turn.
- `REQUEST_TIMEOUT = 1800s`. A slow response on a busy shared GPU is contention,
  not a result.

### Amendment 2 — the wall-clock caps, sized to the amended budget

Also made **before the first dial**, and for the same reason amendment 1 was
made: a thinking cortex given five times the token headroom takes
correspondingly longer per turn, and the caps written for a 3000-token budget
would have cut rungs off for a clock reason rather than a result reason.

- `RUNG_CAP_SECONDS`: 5400 → **10800** (3 h)
- `LADDER_CAP_SECONDS`: 21600 → **28800** (8 h)

The instruction that arrived with the budget correction was explicit and is
followed here: **size the ladder to the budget and report unrun rungs `ABSENT`,
never shrink the budget to fit the clock.** Nothing else moved. In particular
the n per rung (six matches), the turns per match (three), the seeds and the
decision rule are unchanged.

A pre-dial measurement that motivated the number, recorded as a rig
observation rather than as data: with the gateway under load from a sibling
task, a trivial "reply with exactly: X" prompt cost the Qwen cortex **54.8 s
for 172 completion tokens** (~3 tokens/s) against a **2.7 s** Gemma answer, and
`nvidia-smi` showed the single GB10 at 94% utilisation. The README's
uncontended baseline for the same prompt is 9.3 s. Contention of that size is
not a property of either model and must not be read as one.

### Truncation is an instrument event, never a loss

Pre-registered, and it is a decision rule, not a caveat:

- Every turn's `finish_reason` is recorded. A turn with `finish_reason ==
  "length"` is counted as **truncated** and announced on stderr the moment it
  happens (C3 — nothing degrades silently).
- **Truncation counts are reported per arm, prominently**, on every match, every
  rung and the ladder summary, so a reader can see that neither arm was capped
  short. Zero is the claim being made.
- **A match containing any truncated turn is excluded from the decision.** It
  stays in the artifact and in the cost fold; it is not scored, and it is not a
  loss. A truncated turn returns empty content, which the loop reads as a mind
  with nothing to say, which costs that seat its orders and therefore its
  cooperation score — scoring that would be scoring the instrument. Its pairing
  then has fewer than two usable colour assignments and so cannot separate,
  which is the correct conservative outcome.

## The decision rule

Applied to whatever comes back, without amendment.

### One match

Strict priority order. The first metric that is not a tie decides.

1. **`outcome.total`** — league's own score (missions + control + resources).
2. **`cooperation.score` at `--cooperation-version v1`** — league's own
   content-aware metric: delegation spread, message utility, plan fidelity,
   discipline. If the arena cannot serve v1, the harness falls back to v0,
   **stamps which version it used into the payload and prints one notice** — it
   never grades on a different instrument silently.
3. **Fewer faults** — `rejections + cap_dropped`, lower is better.
4. Otherwise **DRAW**, with no winner.

**On the `--max-actions` rungs, an over-cap submission is truncated to the first
N staged orders — the seat's own priority order — and the surplus is counted as
`cap_dropped`.** league refuses a whole `match act` that over-declares;
forfeiting the turn would put both arms on a floor and resolve nothing. The
truncation is the harness's, identical for both seats, and the overrun is a
reported competence signal rather than a hidden repair.

**`outcome.total` is expected to tie at zero.** At three turns no roster in any
of these scenarios can cross from its home corner to a control point. This is
stated *before* the run, not discovered after it, and the offline control
confirms it in all 24 hermetic matches. The tie-breaks are expected to carry the
result, and that is a real limitation of a three-turn match, named here.

### One pairing

**A pairing SEPARATES only when one arm wins BOTH colour assignments.** One win
and one loss is a split, and a split is not a separation. A draw is not a
separation. A pairing with fewer than two usable matches cannot separate.

That rule is deliberately conservative: a colour-linked advantage flips sides on
the swap and therefore cannot win twice.

`net_margin` is recorded beside it and **is not the decision rule**. It is the
paired statistic the colour swap makes available — first-arm minus second-arm on
each metric, summed over the two colour assignments — in which a constant colour
bias `b` enters as `+b` once and `-b` once and cancels exactly. It is reported as
an effect size. It is not promoted to a verdict after the fact.

### One rung

- **`SEPARATED`** — at least **2 of the 3** pairings separated. The implied arm
  ordering is reported, and **a rock-paper-scissors cycle is reported as
  cyclic** rather than smoothed into a ranking that does not exist.
- **`INCONCLUSIVE`** — fewer than 2 pairings separated.
- **`ABSENT`** — the rung's six matches did not all run.

### The ladder

Rungs run in order L1 → L2 → L3 → L4 and the climb **stops at the first
`SEPARATED` rung**. Every rung above it is reported **`ABSENT`**, prominently and
by name — never implied, never padded to look complete.

If no rung separates, the ladder verdict is **`INCONCLUSIVE`**, and it is
published as such.

### Cost decides what quality does not

**Cost is a result, not overhead.** Completion tokens, prompt tokens, wall clock
and truncation counts are reported per arm beside every outcome.

**If quality does not separate, the recommendation is the cheapest arm, and that
is a publishable answer to the operator's question.** `devague-legs` already
measured one mind reaching identical verdicts to the other for ~1/25 the tokens.
Equal quality at 25× the cost is itself the answer.

The wall-clock caveat is repeated wherever wall clock appears: only the cortex is
local, so `full-qwen` puts both roles on the one local GPU while `full-gemma`
puts both on the proxy. Tokens are the portable cost number; seconds are this
rig's.

## The identical-mind control, run offline before this file

Running the ladder with **no `--live`** puts the *same* hermetic scripted mind in
all three arms. Every number it produces is therefore map and seed bias with the
models removed. It is committed as
[`league-h2h-scripted-control.jsonl`](league-h2h-scripted-control.jsonl) (24
matches, 4 rungs, ~49 s, regenerable) and it establishes three facts this
pre-registration leans on:

1. **Identical minds never separate.** All four rungs return `INCONCLUSIVE`. If
   they had separated, the instrument would be measuring the map.
2. **The colour bias is real, and under fog it is large.** At L4, two
   byte-identical minds score **90 against 45** on `cooperation_v1` in *every*
   fogged match, purely by which corner they started in. At L1 the bias is +4;
   at L2 and L3 it is 0.
3. **The paired `net_margin` cancels it exactly** — 0.0 on every metric, in
   every pairing, at every rung, including the +45 fogged rung.

Fact 2 is the reason the colour swap is non-negotiable and the reason `net_margin`
is reported. It also sets an honest expectation: at L4 a model difference smaller
than the map's own 45-point swing cannot win both colours, so **L4 is the rung
least likely to separate**, not the most. That is recorded now rather than
discovered later.

Per-seat-turn records are omitted from the committed control (they are hermetic
and regenerable); the live series commits its transcripts in full.

## Predictions, as falsifiable statements

**P1 — no truncation at 16000.** Zero turns in the whole series report
`finish_reason == "length"`. *Falsified by any truncated turn, in which case the
budget rises and the write-up says so.*

**P2 — `outcome.total` ties at 0–0 in every match.** Three turns is not enough
board time to score. *Falsified by any non-zero total.*

**P3 — the ladder does not separate at L1.** The easiest rung, full board view,
no handicap: whatever the arms are, this is the rung with the least room to
express a difference.

**P4 — `full-gemma` is the cheapest arm in completion tokens by a wide margin.**
The Qwen cortex reasons at length before emitting; Gemma answers directly. The
measured baseline is ~30× per answer on a trivial prompt and ~25× on the
devague-legs judgement task. *Falsified if `full-gemma` is not cheapest.*

**P5 — the arms do not separate anywhere on the ladder.** Stated because it is
what three experiments in this cycle found on their closest questions, and
because a prediction that costs nothing to make is worth nothing. *Falsified by
any `SEPARATED` rung.*

I expect **P5 to hold and P4 to hold**, which would make the honest answer to
the operator "quality did not separate at this n; cost says consolidate on
Gemma" — with the n stated loudly enough that nobody reads it as a strong claim.

## n, cost, and the caps that bound it

- **6 matches per rung** (3 pairings × 2 colour assignments), **3 league turns
  per match**, **2 seat-turns per league turn**, **≤8 model turns per
  seat-turn**. Measured hermetically: 5 cortex calls and ~4 muse calls per
  seat-turn, so ~30 cortex and ~28 muse calls per match.
- **`RUNG_CAP_SECONDS = 10800`** (3 h) and **`LADDER_CAP_SECONDS = 28800`**
  (8 h) — see amendment 2. A rung that would run past its cap stops, and every
  rung above it is reported `ABSENT`.
- **Matches run serially.** The cortex is local and single; parallel matches
  would contend and every latency figure would be fiction.
- **One live timing pilot may be run before the series** to size the ladder
  against the amended 16000-token budget. If it runs, it is named as a pilot in
  the results document, its outcome is **not data**, and it is not counted.

**n is small and is stated as small.** No claim in the results document may be
stated without its n.

## Rules fixed in advance

1. **A degraded or failed call is data.** A degradation, an aborted drive or an
   exhausted transport is counted and reported with its record. It is not
   discarded and not re-run for a better number.
2. **A transport timeout or connection error is contention, not a result.** It
   is retried up to 3 times with a 20 s wait, and **the retry count rides into
   the artifact**. Nothing is ever retried because the answer was disliked —
   only because the HTTP round trip did not complete.
3. **Nothing is re-run to get a better number.** A rung is run once at the
   pre-registered n. An interrupted series resumes; it never replays a
   completed match.
4. **The result is published either way**, including `INCONCLUSIVE`, and
   **every rung that did not run is reported `ABSENT` by name**. An honest
   partial is the required outcome; a padded complete-looking one is a failure.
5. **Raw transcripts are committed**, not only verdicts: every model turn's own
   content, reasoning (clipped at 2000 characters), tool calls, finish reason,
   tokens and latency.
6. **The store is scratch.** Every match's eidetic store lives under a temp home
   outside any git work tree. `.eidetic/memory/embodiment__public.jsonl` is
   tracked in this repo and a match's memories written there would be committed
   and shared with mesh peers.
7. **Software presence, not a robot body** (C2). The seat commands units on a
   grid in someone else's simulation. It drives no hardware.

## What this series cannot show

- **Anything about general model quality.** One task shape, one arena, three
  scenarios, three league turns per match. A model that plays a grid skirmish
  well is not thereby a better coder.
- **Whether the muse contributes at all.** Every arm has a muse; there is no
  muse-off cell. This series compares *which model* serves the roles, not
  *whether the split exists* — that was `association-work`'s question and it
  returned `INCONCLUSIVE`.
- **Generalisation past this rig.** One gateway, one local GPU, one proxy, one
  night.
- **That the arms are equivalent if P5 holds.** A six-match rung cannot detect a
  small effect; failing to find one is not finding its absence, and the results
  document must say so in those words.
