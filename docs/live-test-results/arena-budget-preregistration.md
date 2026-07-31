# Arena series C — the token budget in the arena — pre-registration

**Written:** 2026-07-31 · **Task:** t24 · **Rig:** [`arena-budget-raw/capabilities.json`](arena-budget-raw/capabilities.json)

Committed **before the first measured match**, for the same reason task t19's
[arena series B](arena-series-preregistration.md) was: the rule for "the design
could not resolve it" has to exist before the data does, or it gets written to
fit whatever came back.

The only live match played before this file was committed was **one pilot**
(command arm, one turn, `--max-tokens 16000`) whose sole purpose was to time a
turn and confirm the trace instrument writes. Its outcome is not data. It is
named in the cost section below and its numbers are quoted only as a timing
estimate.

## Why this series and not a repeat of series B

Task **t23** measured that this cortex is token-hungry and that
`embodiment.contract.ModelResponse` **cannot carry `finish_reason`**
([embodiment#37](https://github.com/agentculture/embodiment/issues/37)) — so a
turn cut off by the token cap and a turn where the model chose to stop arrive at
the loop as the *same object*: empty content, no tool calls. It found two
challenge harnesses truncating at a full 16000 and one turn returning
`finish_reason: length` with **zero** content characters.

Series B never looked. Worse, it could not have: `arena_series.seat_argv` passes
no `--max-tokens`, so **the whole published 24-match matrix ran at
`league_seat.py`'s default of 2048**, and its config preamble recorded
temperature but not the cap. The budget the arena's published numbers were
measured through is not written down anywhere in that record.

This series makes the budget the independent variable and reads
`finish_reason` off the wire.

## What is being measured

`examples/league_seat.py` plays league-of-agents through league's own CLI. Two
factors, fully crossed:

| | `--max-tokens 2048` | `--max-tokens 16000` |
|---|---|---|
| **`--arm command`** | cell **C2** | cell **C16** |
| **`--arm resident`** | cell **R2** | cell **R16** |

- **2048** is `league_seat.DEFAULT_MAX_TOKENS` — the shipped default, and the
  budget series B was actually measured at.
- **16000** is the budget t23 used after correcting the same defect elsewhere.
- **`--arm command`** — a fresh subprocess per league turn, one drive each,
  `max_steps=16` per drive.
- **`--arm resident`** — ONE drive spans the whole match,
  `max_steps = 16 × turns + 8 = 56`.

**The muse is OFF in every cell**, and every config preamble will therefore read
`"muse_model": null`. Series B already ran the muse factor and returned
`INCONCLUSIVE` at a ceiling; spending this series' wall clock on it again would
buy nothing, and recording a muse for a run where none was dialled is the exact
defect t23 found in the challenge harnesses' `--muse` flag.

### Colour

The seat plays **blue** in all 12 matches; league's own `bot` driver plays red.
**No colour swap is performed and no blue-versus-red claim is made.** An
identical-mind control elsewhere in this cycle measured **90 vs 45** under fog
on this map, so the map has a large colour bias and any cross-colour comparison
from a single-colour series would be measuring the map.

Cross-cell comparisons here are all *within* blue, at the same seeds, against
the same deterministic house policy, so the bias is constant across cells and
cannot produce a cell-to-cell difference.

## Predictions, as falsifiable statements

**P1 — the shipped default truncates this cortex in the arena.** At 2048, at
least one model turn per match returns `finish_reason: length`. At 16000, no
model turn does. *Falsified if the 2048 cells show no `length` turn at all, or
if any 16000 turn shows one.*

**P2 — a truncated turn is invisible in the loop's own record.** Every `length`
turn carries **zero** content characters and **zero** tool calls, and the
match's `degradations` list records nothing naming it. *Falsified if any
`length` turn carries content or a tool call, or if any degradation names
truncation.* This is [embodiment#37](https://github.com/agentculture/embodiment/issues/37)
tested in the arena rather than on a puzzle.

**P3 — the second truncation ends the drive.** `LoopControls.max_continue_nudges`
defaults to **1**, and `nudges` is cumulative across a drive rather than
consecutive, so the first no-tool turn is nudged and the second returns
`EXIT_STOPPED`. Predict: every drive recording ≥2 `length` turns exits
`stopped`; no drive with ≤1 exits `stopped`. *Falsified by either direction.*

**P4 — `turns_played` cannot see any of it.** All 12 matches play their full 3
turns whatever the budget, because a drive that ends with nothing submitted
still files `{"actions": []}` and league accepts an empty order set as a turn.
*Falsified if any match plays fewer than 3 turns.*

P4 is the prediction that matters for reading series B. Its published table
scores every cell **9/9 turns completed**, and if P4 holds, that metric is
structurally blind to the failure this series is looking for.

**P5 — the optional `message` is omitted, and league's tie-break scores it.**
`submit`'s `message` field is labelled *"Optional team message"* and league
scores `cooperation.signals.communication` from it
([league-of-agents#42](https://github.com/agentculture/league-of-agents/issues/42)).
Predict: blue's `communication` signal is **0.0** in the majority of matches and
`orders.messages` is absent from the majority of turns. Recorded as an
observation with its n, **not** as a comparison between cells.

## The decision rule

Applied to whatever comes back, without amendment.

**P1–P4 are structural pass/fail claims, not comparisons.** Any single
falsifying match falsifies the claim, and the falsifying match is quoted.

**P5 is a count.** Report the counts and the n. No verdict.

**On any metric where cells tie:** if every cell scores identically at the top
or the bottom of its range, the design could not resolve the question and the
verdict is **`INCONCLUSIVE`**, *not* "no effect". n=3 per cell cannot detect a
small effect, and failing to find one is not finding its absence. This is t19's
rule and t18's before it, restated because it is the arm that keeps a null
reportable.

**No claim about match outcome, score, or winning is made or graded.** The
opponent is a deterministic house policy, not a mind; the seat plays one
colour; n is 3. Scores are committed with the raw records because deleting them
would be worse, and they are not interpreted.

## n, seeds, and the cost that justifies them

**Measured pilot cost** (not data): one command-arm league turn at
`--max-tokens 16000`, muse off, took **95.8s** wall clock end to end and spent
**2372** completion tokens on a single model turn with
`finish_reason: tool_calls`.

That single number is already most of P1's motivation — a routine opening turn
overshot the shipped 2048 default by 16% — and it is exactly why the prediction
is written down before the series rather than after.

**Therefore: n = 3 per cell, `--max-turns 3`, 4 cells = 12 matches.** At roughly
5 minutes per match at 16000, and unknown-but-larger at 2048 (a truncated turn
is a *spent* turn, and the drive takes another), that is an estimated 1–2 hours
of serial wall clock.

Matches run **serially**. The cortex is local and single, and task **t28** is
using the same rig concurrently; parallel matches would contend and every
latency figure would be fiction.

**Seeds:** `4242`, `4243`, `4244` — repetition *i* of every cell uses seed
`4242 + i`, the same three seeds series B used, so the same three scenarios are
played in all four cells and a cell-to-cell difference cannot be a scenario
difference.

`n = 3` is small and is stated as small. **No claim in the results document may
be stated without its n.**

## Rules fixed in advance

1. **A degraded match is data.** A match that records a degradation, or whose
   drive aborts, is counted and reported with its degradation. It is not
   discarded and not re-run.
2. **Nothing is re-run to get a better number.** Each cell is run once at the
   pre-registered n. The runner writes each match's report, trace and match log
   the moment it finishes and **skips any match already on disk**, so an
   interrupted series resumes and never replays a completed match.
3. **A gateway failure is an infrastructure event, not a result.** t28 shares
   this rig. An HTTP 503 or a >600s stall is recorded as a failed match *with
   its error*, the series continues, and any retry is reported **as a retry**.
4. **The result is recorded either way**, including `INCONCLUSIVE` and
   including a prediction that fails. Every lane that does not run is reported
   **ABSENT** in its own row.
5. **The store is scratch.** `--store` and `--workdir` point outside any git
   work tree. `.eidetic/memory/embodiment__public.jsonl` is tracked in this
   repo, and a match's memories written there would be committed and shared
   with mesh peers.
6. **Raw artifacts are committed**, not just verdicts: every match's report
   JSON, its per-completion trace, its match log and its config preamble.

## The instrument change, declared before the runs

`examples/league_seat.py` is edited **before the first measured match**, and the
edit is named here rather than left for a reader to find in the diff:

1. **`--trace-out`** — appends one JSON object per live completion carrying
   `finish_reason`, both token counts, content and reasoning character counts,
   the tool-call names, the role and the pid. **Defaults to off**, so the
   shipped path parses and drops the raw payload exactly as before. Without it
   this series cannot be run at all: `finish_reason` is the entire measurement
   and `ModelResponse` does not carry it.

   It flushes **per completion**, not per run — a harness that writes at process
   end hands back a zero-byte file when the gateway 503s mid-series, which is
   how a sibling task lost 41 minutes of live cortex work. In the command arm
   every turn is a different process appending to the same file, serially.

2. **`cortex_max_tokens`, `muse_max_tokens`, `max_steps` and `trace_out` in the
   config preamble.** The preamble recorded per-role temperature but not the
   token cap, which is how series B's budget came to be unrecorded. A setting a
   run varies but does not record is a hidden variable.

Neither change alters what the seat does with a model's answer. Both were made
before any measured match, and no measured match had been seen when they were
written.

## What this series cannot show

- **Nothing about model quality, and nothing about winning.** One task shape,
  one deterministic house rival, one scenario family, one colour, n=3.
- **No generalisation past this rig.** One cortex, one machine, one day.
- **Not that 16000 is sufficient in general.** t23 exhausted 16000 on two
  independent puzzle problems. If no truncation appears at 16000 here, that is a
  statement about *this* task shape at *this* n, not a budget recommendation.
- **Not a re-scoring of series B.** This series measures what the budget does.
  It does not re-run series B's matrix and makes no claim about which of that
  document's findings would change.
