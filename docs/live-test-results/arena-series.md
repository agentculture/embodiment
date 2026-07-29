# Arena series B — results

**Task:** t19 · **Run:** 2026-07-29 · **Rig:** see [README](README.md)
**Pre-registration:** [arena-series-preregistration.md](arena-series-preregistration.md)
(committed before the first measured match)

**24 matches, 24 completed, 0 aborted, every match played its full 3 turns.**
Raw records: [`arena-series.jsonl`](arena-series.jsonl) ·
config: [`arena-series-config.json`](arena-series-config.json).

`n = 3` per cell. Every number below is n=3 on one rig with one model pair.
No claim here is stated without its n, and none of them survives generalisation
past this rig.

## The headline

Three of the five pre-registered predictions returned something. **Two of them
returned a defect in the instrument rather than a fact about the lane** — and
under the pre-registration's own rule ("the result is recorded either way") that
is what gets published.

| | prediction | pre-registered verdict | note |
|---|---|---|---|
| **P1** | residency shows up in league's artifact | **CONFIRMED 24/24** | clean |
| **P2** | the command arm carries the directive across processes | **UNGRADEABLE** | the cells it named were never given a directive — runner defect |
| **P3** | replay hashes stable within a cell for a fixed seed | **FALSIFIED**, as predicted | 4 distinct hashes per seed, 3/3 seeds |
| **P4** | continuity holds across matches | **FAIL** as written | the grader could not see the mind's own paraphrase |
| **P5** | the muse changes nothing measurable at this n | **INCONCLUSIVE** | ceiling: all four cells 9/9 |

Two findings survive the instrument problems, both derived from committed data
and both **labelled post-hoc**: continuity did cross (§P4), and the counsel-loss
race has a mechanism (§degradations).

## P1 — residency is real and visible · CONFIRMED 24/24

Every match's `driver_kinds` comes from league's own match record, not the
seat's self-report. All 6 `--arm resident` matches record `blue: resident`; all
18 `--arm command` matches record `blue: stateless`. No match disagreed with its
arm.

The pids corroborate it independently: every command-arm turn ran in a distinct
process (e.g. `co-r0`: 798078 → 800494 → 802223), and resident-arm turns share
one.

## P2 — ungradeable, because the runner never gave the cells a directive

P2 was written about **CO and CM**: "turns 1+ must show `directive_given ==
False` and `objective_seen == "cp-west"`."

Every CO/CM turn records `directive_given: False` — **including turn 0.**
`matrix_specs()` leaves `Spec.directive` at its `""` default and only
`continuity_specs()` sets it. The matrix arm was never told the objective, so
"does it carry the directive across processes" could not be tested where P2 said
it would be.

This is a defect in the runner I wrote, found by grading rather than by review.

**Post-hoc**, the question is still answerable from the six directive-bearing
continuity leg-1 matches, which are command-arm and identical in shape:

- `cp-west` appears in the recalled text on **12 of 12** leg-1 turns 1+.
- The plan or summary adopts it on **9 of 12**.

The directive does cross the process boundary. That is a post-hoc reading of an
arm P2 did not name, and it does not convert P2 into a pass.

## P3 — falsified, exactly as pre-registered

| seed | RO | RM | CO | CM | distinct |
|---|---|---|---|---|---|
| 4242 | `5a5ae9f0` | `35bc12f4` | `efca4cef` | `3a113e05` | 4 |
| 4243 | `d5b9e052` | `e11db68c` | `73f3111a` | `d65b5bab` | 4 |
| 4244 | `616a5a15` | `c9b37f2f` | `4ca7a799` | `845debaf` | 4 |

The seed fixes league's scenario, not the model's choices. Recorded as a record,
not a test — the hashes exist to make a *replay* reproducible, not to assert the
mind is deterministic.

Writing the expectation down first is what makes this reportable instead of
retrofitted.

## P4 — continuity · FAIL as pre-registered, and the grader is why

### The rule, applied without amendment

> **PASS** — match 2's `objective_seen` is `cp-west` on its first turn.
> **FAIL** — match 2 has no objective, or names a different one.
> **CONTROL** — the same match 2 against an empty store. It must FAIL.

Applied as written: **memory FAIL 3/3, control FAIL 3/3.** Every leg-2 turn 0
records `objective_seen: ""`. The control failed as required; so did the arm it
was supposed to discriminate against.

### Why that verdict is about the instrument

```python
_OBJECTIVE_RE = re.compile(r"take (cp-[a-z0-9-]+)")
```

It matches the directive as issued — "take cp-west and hold it" — but **not the
mind's own paraphrase**, which is what actually lands in the store:

> `"Turn 0 complete. Units advanced toward cp-west. Objective: take and hold cp-west."`

"and hold" sits between `take` and `cp-west`, so the extractor returns `""`. The
objective was in the context on every one of those turns; the grader was blind
to the word order the mind happened to use.

### What actually happened — post-hoc, not pre-registered

Leg 2 gets a fresh arena and a fresh scratchpad. **The store is the only channel
that differs** between the two arms, and the recall counts confirm it:

| arm | leg-2 turn 0: records recalled |
|---|---|
| memory (shares leg 1's store) | **3, 3, 2** |
| control (own empty store) | **0, 0, 0** |

The directive said *"take cp-west and hold it; **ignore cp-east**."* So the
discriminating question is not whether cp-west is mentioned — it is whether the
mind **singles west out and drops east**, versus enumerating the board.

| arm | ADOPTED (west, not east) | GENERIC (enumerates board) | no plan text |
|---|---|---|---|
| memory, 9 leg-2 turns | **8** | **0** | 1 |
| control, 9 leg-2 turns | **0** | **8** | 1 |

Perfect separation. The control's plan is the same shape every time:

> "Standing plan: 1) Capture **cp-center [6,5] first** as it's most accessible.
> … 3) Expand to **cp-west [3,8] and cp-east [9,2]** once center is secured."

That is the board's own prior — center first, west and east symmetric — and it
names cp-east, which is precisely what the directive forbade. Nothing in the
scenario produces "west, not east"; only the store does.

**So continuity crossed, and the control proves the objective was not
recoverable without it.** This is a post-hoc analysis with a discriminator
chosen after seeing the data. It is reported as evidence, not as a pass. A
corrected P4 needs its own pre-registered series.

### A confound I talked myself into, then measured away

Mid-analysis I concluded the control had *also* reached cp-west and that
continuity was therefore not demonstrated. That came from testing `'cp-west' in
plan` — a substring check that scored the generic board plan as a hit because it
enumerates all three points. **The third defective measure in this task**, and
the only reason it did not reach this document is that the plan text was read
rather than trusted.

## P5 — the muse · INCONCLUSIVE (ceiling), not "no effect"

The pre-registered metric is turns completed without a degraded or aborted
drive.

| cell | turns completed | mean wall clock |
|---|---|---|
| RO | 9/9 | 274.6s |
| RM | 9/9 | 298.3s |
| CO | 9/9 | 326.6s |
| CM | 9/9 | 290.1s |

Every cell scored identically at the **top** of the range. The pre-registration
names this case explicitly:

> if every cell scores identically at the top or the bottom of the range, the
> design could not resolve the question and the verdict is `INCONCLUSIVE`, **not**
> "no effect".

So: `INCONCLUSIVE`. n=3 per cell cannot detect a small effect, and **failing to
find one is not finding its absence.** This is the second task in a row where
that arm of the rule was the difference between a reportable null and an
overclaim (t18 tied 12/12 vs 12/12).

The latency spread (274–327s) does not track the muse factor — the two
slowest cells are one muse-on and one muse-off.

## Degradations — a C3 violation, and a mechanism for embodiment#17

**14 degradations, every one `muse-insight-late` / "undrained when the runner
closed".** Zero of any other code. Zero aborted turns.

### The match-level field under-reports by 10

| cell | `degradations` field | turn records | truth |
|---|---|---|---|
| RM | 1, 2, 1 | 0, 0, 0 | 4 |
| CM | **0, 0, 0** | **2, 5, 3** | **10** |

`_play_command` in `examples/league_seat.py` returns no degradations, so
`match_degradations` stays `[]` while the per-turn records hold ten. Anyone
reading the match-level field would conclude the command arm degraded **never**.

That is a **C3 violation** — *"every degradation records a transition; nothing
degrades silently"* — in the very harness built to measure C3. Found by
cross-checking two surfaces that should have agreed. It is a reporting defect,
not a lost degradation: the turn records on disk are complete, which is the only
reason the true count is recoverable.

The instrument was **not repaired mid-series.** Fixing `league_seat.py` between
matches would have meant early matches were measured with one instrument and
late ones with another. Both defects are fixed after this document, not before.

### The rate is per drive close — which is the mechanism

The resident arm closes one drive per match; the command arm closes one per turn.

| arm | drive closes | late insights | per close |
|---|---|---|---|
| RM (resident) | 3 | 4 | **1.33** |
| CM (command) | 9 | 10 | **1.11** |

Near-constant **per close**, not per turn and not per match. That is
[embodiment#17](https://github.com/agentculture/embodiment/issues/17) —
deviation `d1` leaves a muse session in flight at the last step, so every close
races one insight — now independently replicated in a **third** task shape and,
for the first time, with a rate law. t18 measured 25% of counsel lost this way
in a coding task; here the command arm loses roughly one insight *per turn*
because it closes a drive every turn.

**The practical consequence is sharper than the issue currently states:** the
cost of the close-time race scales with how often a host closes a drive. A
per-turn command-arm host pays it 3× as often as a resident host doing the same
work.

## What this series cannot show

Restated from the pre-registration, unchanged:

- **Nothing about model quality.** One task shape, one scripted rival, one
  scenario family.
- **No generalisation past this rig.** One cortex, one muse, one machine, n=3.
- **Not that the muse is useless.** P5 is `INCONCLUSIVE` at a ceiling; that is a
  statement about the design's resolution, not about the muse.

## Defects this task found in its own instruments

Recorded here because [corrections.md](corrections.md) exists for exactly this,
and because three of the five predictions were affected:

1. **`read_objective`'s regex is word-order sensitive** — cannot see "take and
   hold cp-west". Broke P4's pre-registered verdict.
2. **`_play_command` reports no degradations** — C3 violation, 10 real
   degradations shown as 0.
3. **`matrix_specs()` passes no directive** — made P2 ungradeable in the cells it
   named.
4. **`'cp-west' in plan` is not an objective test** — my own mid-analysis
   measure, caught before it reached a conclusion.

Every one of them was found by *grading the data*, not by reading the code or by
a test failing. That is the same pattern as the rest of this fan-out: the
mechanism was right and the verification was the defect.
