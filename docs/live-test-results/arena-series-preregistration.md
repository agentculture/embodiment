# Arena series B — pre-registration

**Written:** 2026-07-29 · **Task:** t19 · **Rig:** see [README](README.md)

Committed **before the first measured match**. This is the same discipline that
made task t18's `INCONCLUSIVE` verdict reportable instead of embarrassing: the
rule for "the design could not resolve it" has to exist before the data does,
or it gets written to fit whatever came back.

The only runs that happened before this file was committed were **two pilot
matches** whose sole purpose was to time a match. Their outcomes are not data,
are not counted, and are named as pilots in the cost section below.

## What is being measured

`examples/league_seat.py` plays league-of-agents through league's own CLI. Two
factors, fully crossed:

| | muse off | muse on |
|---|---|---|
| **`--arm resident`** | cell **RO** | cell **RM** |
| **`--arm command`** | cell **CO** | cell **CM** |

- **`--arm resident`** — ONE `embodiment.run` drive spans the whole match; one
  muse thread lives across turns. league is told `--driver <me>:resident`.
- **`--arm command`** — a fresh subprocess per league turn, one drive each.
  Nothing survives in memory, so all continuity rides the scratchpad file and
  the eidetic store. league is told `--driver <me>:stateless`.

The two arms are the experiment; the muse is the second factor.

## Predictions, as falsifiable statements

**P1 — residency shows up in the artifact.** Every RO/RM match records
`driver_kinds.blue == "resident"` and every CO/CM match records
`"stateless"`, in league's **own** match-log header as well as in the seat's
report. *Falsified if any match's league header disagrees with its arm.*

**P2 — the command arm carries the directive across processes.** The operator
directive is given on turn 0 and never again. In CO/CM, turns 1+ must show
`directive_given == False` and `objective_seen == "cp-west"`, with a distinct
pid per turn. *Falsified if any later turn loses the objective.*

**P3 — replay hashes are stable for a fixed seed within a cell.** Two matches
in the same cell with the same seed produce the same `replay_sha256`.

I expect **P3 to be FALSIFIED**, and am recording that expectation here rather
than after the fact. The seed fixes league's scenario, not the model's choices,
and a sampling model at temperature > 0 will issue different orders. If the
hashes do match, that is the stronger and more surprising result. Either way
the hashes are committed — their job is to make a *replay* reproducible, not to
assert the mind is deterministic.

**P4 — continuity holds across matches.** A second match in the same eidetic
store, with a **fresh scratchpad**, recovers something only the first match
could have taught it. See the ground truth below.

**P5 — the muse changes nothing measurable at this n.** Stated because it is
what t18 found on the closest available question (interaction 0.00, all four
cells tied) and because a prediction that costs nothing to make is worth
nothing. *Falsified if any muse-on cell differs from its muse-off partner by
more than the decision rule's threshold.*

## The decision rule

Applied to whatever comes back, without amendment.

**On the muse factor (P5):**

- **Effect** — a muse-on cell beats its muse-off partner on turns completed
  without a degraded or aborted drive, by **≥ 2 of n matches** in the same
  direction in **both** arms.
- **No effect** — the difference is 0 in both arms.
- **`INCONCLUSIVE`** — anything else, *and specifically* the ceiling/floor
  case: if every cell scores identically at the top or the bottom of the
  range, the design could not resolve the question and the verdict is
  `INCONCLUSIVE`, **not** "no effect". t18 tied 12/12 vs 12/12 and 3/9 vs 3/9
  and only escaped overclaiming because this arm of the rule was written first.

**On residency (P1, P2) and continuity (P4):** these are pass/fail structural
claims, not comparisons. Any single falsifying match falsifies the claim, and
the falsifying match is quoted.

**On P3:** report the hashes and whether they matched. No verdict either way —
this is a record, not a test.

## Continuity across matches — the ground truth

Match 1 is told an operator directive naming an objective (`cp-west`). Match 2
runs in the **same eidetic store** with a **fresh scratchpad and a fresh arena**,
and is given **no directive at all**.

- **PASS** — match 2's `objective_seen` is `cp-west` on its first turn, and its
  orders move toward it. The only place that could have come from is the store.
- **FAIL** — match 2 has no objective, or names a different one.
- **CONTROL** — the same match 2 run against an **empty store**. It must FAIL.
  Without this arm, a pass proves nothing: the objective could have come from
  the prompt, the scenario, or the model's prior. This control is mandatory and
  is the same discipline that turned task t7's result from a correlation into a
  cause.

An ambiguous result — objective present but orders inconsistent with it — is
recorded as `UNREADABLE`, not as a pass. A verdict pair with no third arm
forces every ambiguous run into one of the two, and the flattering one gets
picked; that is exactly the defect t18 found in its own failure classifier.

## n, seeds, and the cost that justifies them

**Measured pilot cost, muse off, `--max-turns 3`:**

| arm | wall clock | turns |
|---|---|---|
| command | **384s** (6.4 min) | 3 |
| resident | see results doc — exceeded a 10-minute shell cap on the first attempt and was re-timed | 3 |

**Therefore: `n = 3` per cell, `--max-turns 3`, 4 cells = 12 matches**, plus 3
continuity pairs and their controls. At ~6–10 minutes per match that is roughly
**2–3 hours of serial wall clock**, which is what one local 27B cortex can
actually deliver in a sitting.

Matches run **serially**. The cortex is local and single; parallel matches
would contend and every latency figure would be fiction.

`n = 3` is small and is stated as small. t18's entire result set is n ≤ 4 on
one rig, and an honest small n beats an aspirational large one that never
finishes. **No claim in the results document may be stated without its n.**

**Seeds:** `4242`, `4243`, `4244` — repetition *i* of every cell uses seed
`4242 + i`, so the same three scenarios are played in all four cells and a
cell-to-cell difference cannot be a scenario difference.

## Rules fixed in advance

1. **A degraded match is data.** A match that records a degradation, or whose
   drive aborts, is counted and reported with its degradation. It is not
   discarded and not re-run. Degradations are the C3 surface; deleting them
   would be deleting the observation.
2. **Nothing is re-run to get a better number.** A cell is run once, at the
   pre-registered n. If the series is interrupted, the runner resumes from
   where it stopped — it never replays a completed match.
3. **The result is recorded either way.** A muse that does nothing, a
   continuity claim that fails, a residency that does not appear in the
   artifact — each is a finding about the lane and each is published.
4. **A warm-up match runs before the first measured cell**, and its latency is
   reported. Not because the muse is known to be cold — it is not; three
   completions today ran at 2.50s / 2.54s / 0.43s while the gateway reported
   `loaded: false` — but because the `/capabilities` contract does not let a
   caller tell warm from cold (lobes-cli#146). The warm-up is not data.
5. **The store is scratch.** `EIDETIC_DATA_DIR` points outside any git work
   tree. `.eidetic/memory/embodiment__public.jsonl` is tracked in this repo and
   a match's memories written there would be committed and shared with mesh
   peers.

## What this series cannot show

- **Anything about model quality.** The seat is one task shape against one
  scripted rival on one scenario family.
- **Generalisation past this rig.** One cortex, one muse, one machine.
- **That the muse is useless if P5 holds.** n=3 per cell cannot detect a small
  effect; failing to find one is not finding its absence, and the results
  document must say so in those words.
