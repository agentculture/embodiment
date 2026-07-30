# League head-to-head — full-Gemma vs mixed vs full-Qwen

**Run:** 2026-07-31, 01:04–02:41 (5750 s) · **Task:** t27 · **Issue:**
[#34](https://github.com/agentculture/embodiment/issues/34) · **Deviations:**
`d9`, `d10`

**Contract:** [league-h2h-preregistration.md](league-h2h-preregistration.md),
committed with the harness **before the first dial**. Every rule below was fixed
in advance; nothing was amended after a number existed.

**Artifacts:** [`league-h2h.jsonl`](league-h2h.jsonl) (config, 36 seat-turns
with raw transcripts, 6 matches, the rung, the ladder),
[`league-h2h-config.json`](league-h2h-config.json),
[`league-h2h-smoke.json`](league-h2h-smoke.json) (the instrument check),
[`league-h2h-scripted-control.jsonl`](league-h2h-scripted-control.jsonl) (the
offline identical-mind control).

---

## Verdict

**`SEPARATED` at L1 — the first and easiest rung — with all three pairings
separating in the same direction and no cycle:**

> **full-qwen > mixed > full-gemma**

**L2, L3 and L4 are `ABSENT`. They did not run.** The pre-registered rule is to
stop climbing at the first rung that separates, and L1 separated. Nothing below
is a statement about a harder board.

**And the separation is on ONE signal.** Read the mechanism section before
using the ordering for anything: the arms are indistinguishable on the game
itself, and the whole ranking is *whether the seat filled in one optional
field*.

**Cost, per match, same task, zero truncation:**

| arm | completion tokens / match | × cheapest | wall clock / match | × cheapest |
|---|---|---|---|---|
| `full-gemma` | **950** | 1.0× | **59.8 s** | 1.0× |
| `mixed` *(ships today)* | **12,819** | 13.5× | 541.0 s | 9.0× |
| `full-qwen` | **18,410** | 19.4× | 834.1 s | 14.0× |

n = 4 matches per arm, 12 seat-turns per arm, one rig, one night. **No claim
here may be read without that n.**

---

## Answering the operator's question

*Should I switch models, or consolidate to one?*

**On this evidence: consolidating on Gemma buys a 13–19× token saving and costs
one specific, cheap-to-fix behaviour.** The honest recommendation is
**neither "switch" nor "consolidate" as stated** — it is:

1. **Fix the prompt first, then re-measure.** The entire measured quality gap is
   that the Gemma cortex never used `submit`'s optional `message` field —
   **0 of 12 seat-turns**, against `mixed`'s 4/12 and `full-qwen`'s 9/12. The
   tool schema calls that field *"Optional team message."* Gemma read
   "optional" as "skip". That is a one-line prompt change, not a capability
   ceiling, and until it is tried, "Qwen plays better" is not the right reading
   of this data.
2. **If the prompt fix closes the gap, consolidate on Gemma.** Everything else
   is already tied at a ceiling (below), and 950 tokens against 18,410 is not a
   close call.
3. **Do not consolidate on Qwen.** `full-qwen` beat `mixed` — the same cortex,
   the muse swapped from Gemma to Qwen — but it paid **10.5× more muse tokens
   per seat-turn** (2,293 vs 219) for it. That is the worst cost-per-unit-of-
   quality on the board.

**What would change this answer:** a rung where the game itself separates. L1's
primary metric tied 0–0 in every match, so "who is better at league" is a
question this run did not reach. That is what L2–L4 were for, and they are
`ABSENT`.

---

## The mechanism — three of four signals are on a ceiling

`outcome.total` (missions + control + resources) was **0–0 in all six matches**,
exactly as pre-registered: three turns is not enough board time for any roster
to cross from its home corner. So every match was decided by the first
tie-break, league's own content-aware `cooperation_v1`. Its four signals,
averaged over each arm's four matches:

| arm | delegation_spread | discipline | plan_fidelity | **message_utility** |
|---|---|---|---|---|
| `full-gemma` | 0.986 | 0.972 | 1.000 | **0.000** |
| `mixed` | 0.986 | 0.972 | 1.000 | **0.750** |
| `full-qwen` | 1.000 | 1.000 | 1.000 | **1.000** |

Three of four are at or within 3% of a ceiling for all three arms. **All three
arms ordered every unit on every turn** (`declared: 9` in every one of the
twelve team-records) and **the whole series produced two rejected orders**, one
by `full-gemma` and one by `mixed`.

The separation is `message_utility`, and it is binary at the source:

| arm | seat-turns that sent a team message | judged useful |
|---|---|---|
| `full-gemma` | **0 / 12** | — |
| `mixed` | **4 / 12** | 4 / 4 |
| `full-qwen` | **9 / 12** | 9 / 9 |

Every message any arm sent was scored useful. The ranking is the *message rate*
and nothing else. A representative message, from `full-qwen`:

> `All units moving toward cp-west [3,8].`

**Said plainly: this series separated the arms on interface compliance, not on
play.** That is a real and useful signal — a teammate that silently drops an
optional coordination channel is worse to work with — but it is not "the better
strategist", and reporting it as one would be the overclaim this lane exists to
avoid.

### One observation that is not a claim

`mixed` and `full-qwen` run the **same cortex**. They differ only in the muse.
Yet `full-qwen` sent messages on 9/12 seat-turns and `mixed` on 4/12, and
`full-qwen` won both colour assignments against `mixed`. Either the Qwen muse's
counsel pushed the cortex to communicate more, or that is sampling noise at
temperature 0.3 with n=12 seat-turns per arm. **This run cannot tell those
apart**, and a muse-off cell — which would be the control — does not exist here.
Recorded because it is the first thing in this cycle that looks like a muse
effect on behaviour, and flagged because looking like one is not being one.

---

## Per-match results

Every match, in run order. Both teams are model seats; the arena is league's
own CLI; `outcome` is league's score and `coop` is `cooperation_v1`.

| # | blue arm | red arm | decided by | winner | coop blue/red | outcome | rejections | truncated | wall | blue tokens | red tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | full-gemma | mixed | `cooperation_v1` | **mixed** | 70 / 100 | 0–0 | 0 | 0 | 645 s | 897 | 15,583 |
| 2 | mixed | full-gemma | `cooperation_v1` | **mixed** | 100 / 66 | 0–0 | 1 (red) | 0 | 449 s | 10,444 | 852 |
| 3 | mixed | full-qwen | `cooperation_v1` | **full-qwen** | 70 / 100 | 0–0 | 0 | 0 | 1500 s | 7,174 | 21,943 |
| 4 | full-qwen | mixed | `cooperation_v1` | **full-qwen** | 100 / 96 | 0–0 | 1 (red) | 0 | 1629 s | 18,817 | 18,074 |
| 5 | full-qwen | full-gemma | `cooperation_v1` | **full-qwen** | 100 / 70 | 0–0 | 0 | 0 | 744 s | 12,891 | 949 |
| 6 | full-gemma | full-qwen | `cooperation_v1` | **full-qwen** | 70 / 100 | 0–0 | 0 | 0 | 779 s | 1,102 | 19,988 |

### Per pairing, with the colour swap

A pairing separates only when one arm wins **both** colours. All three did.
`net_margin` is the paired statistic (first arm minus second, summed across the
swap), in which a constant colour bias cancels exactly — it is reported as an
effect size and **is not the decision rule**.

| pairing | winner | wins | net `cooperation_v1` | net `outcome` |
|---|---|---|---|---|
| `gemma-vs-mixed` | **mixed** | 2–0 | −64 (mixed ahead) | 0 |
| `mixed-vs-qwen` | **full-qwen** | 2–0 | −34 (full-qwen ahead) | 0 |
| `qwen-vs-gemma` | **full-qwen** | 2–0 | +60 (full-qwen ahead) | 0 |

Three of three separated against a pre-registered bar of two, and the wins
compose into a consistent total order — **`cyclic: false`**. A
rock-paper-scissors result was a real possible outcome here and would have been
reported as one.

---

## Cost, per seat-turn

The portable number is tokens. Wall clock is reported because it is real, and
immediately qualified because it is not portable.

| arm | seat-turns | cortex calls / turn | cortex tokens / turn | **muse tokens / turn** | s / turn | orders / turn | truncated | retries |
|---|---|---|---|---|---|---|---|---|
| `full-gemma` | 12 | 1.3 | 223 | 94 | 19.9 | 3.00 | 0 | 0 |
| `mixed` | 12 | 4.2 | 4,054 | 219 | 180.3 | 3.00 | 0 | 0 |
| `full-qwen` | 12 | 3.4 | 3,843 | **2,293** | 278.0 | 3.00 | 0 | 0 |

Two things worth naming:

- **The Gemma cortex finishes a whole seat-turn in 1.3 model calls.** It emits
  `note`, three `order`s and `submit` as five parallel tool calls in a *single*
  response. The Qwen cortex takes 3.4–4.2 calls to do the same work, and spends
  ~4,000 completion tokens doing it against Gemma's ~220 — an **18× cortex
  token ratio at identical output** (all three arms staged exactly 3.00 orders
  per turn).
- **Swapping the muse from Gemma to Qwen costs 10.5× the muse tokens**
  (219 → 2,293 per seat-turn). That is the price of the `mixed` → `full-qwen`
  step, and it bought +0.25 `message_utility`, +0.014 `delegation_spread` and
  +0.028 `discipline`.

**Wall clock is not a clean quality signal across these arms, and here the
direction is counter-intuitive.** Only the cortex is served locally: `full-qwen`
puts **both** roles on the one local GB10, `mixed` splits them, and `full-gemma`
puts **both** on the proxy — so the fastest arm is the one running entirely off
this box. The 14× wall-clock spread is model speed *and* placement, tangled.
Tokens are not.

---

## Predictions, graded

| | prediction | outcome |
|---|---|---|
| **P1** | no truncation at `max_tokens=16000` | **HELD** — 0 truncated turns in 36 seat-turns and 196 model calls, both models |
| **P2** | `outcome.total` ties 0–0 in every match | **HELD** — 6/6 |
| **P3** | the ladder does not separate at L1 | **FALSIFIED** — L1 separated, 3/3 pairings |
| **P4** | `full-gemma` is cheapest by a wide margin | **HELD** — 13.5× and 19.4× cheaper than the other two |
| **P5** | the arms do not separate anywhere | **FALSIFIED** — see P3 |

Two of five falsified, and the two that were falsified are the ones I said I
expected to hold. Recorded as such: this is the first experiment in this cycle
that did **not** die at a ceiling, and the reason it did not is that the ladder
was built to escape one — even though it never needed a second rung.

**Amendment 1 earned its keep.** The budget correction from 3,000 to 16,000
tokens, made before the first dial, is why P1 held: the Qwen cortex spent
**~4,000 completion tokens per seat-turn**. At the originally briefed 3,000-token
cap it would have truncated on essentially every turn, returned empty content,
staged no orders, and lost every match — and this document would have reported
that as Gemma being better. That is not a hypothetical; it is arithmetic off the
measured spend.

---

## What did not run, and why

| rung | scenario | status |
|---|---|---|
| **L1** | `skirmish-1`, 12×10, seed 4242 | ran, 6/6 matches, **`SEPARATED`** |
| **L2** | `recon-1`, 14×12, seed 4243 | **`ABSENT`** |
| **L3** | `skirmish-2`, capped to 2 actions, seed 4244 | **`ABSENT`** |
| **L4** | `skirmish-2`, capped and fogged, seed 4245 | **`ABSENT`** |

L2–L4 did not run because the pre-registered rule says the climb **stops at the
first rung that separates**. Separation is the result; exhausting the ladder is
the fallback.

That is the rule working, and it also bounds the finding: **nothing here says
anything about a bigger board, a tighter action cap, or fog.** In particular the
offline control predicted L4 would be the rung *least* likely to separate (the
map's own colour bias there is 45 cooperation points between byte-identical
minds), so an absent L4 is not a missing confirmation — it is a missing test.

**"+5 in size" was not expressible.** `league arena list` ships exactly three
fixed scenarios and `league match new` has no grid parameter. What was
substituted, in rising order, was the scenario ladder, league's engine-enforced
`--max-actions` handicap, and a genuinely fogged observation
(`match brief --team T` instead of `match show`). Only the first rung of that
substitution was reached.

---

## Rig, instrument and degradation record

**Contention.** The series was queued behind a sibling task (t23,
`challenge_subset.py`) which held the GPU at 94% utilisation. Under that load a
trivial "reply with exactly: X" prompt cost the Qwen cortex **99.7 s and 147.8 s
for ~170 tokens** on two probes, against the README's uncontended 9.3 s baseline.
The series was **not** started under that load: t23 exited at 01:01:09, a probe
15 s later returned **7.24 s**, and the first dial was at 01:04. **The series
recorded 0 transport retries and 0 transport failures in ~120 model calls**, so
no measurement in this document is a contention artifact.

**Instrument check** ([`league-h2h-smoke.json`](league-h2h-smoke.json)), run
before the series and reported as an instrument check rather than as data: both
candidate cortex models return parseable tool calls through this gateway on the
real schema — `['note', 'order', 'submit']` from each. This is the check that
had to pass before the head-to-head could mean anything: a cortex whose tool
calls the harness could not read would stage nothing, score nothing, lose every
match, and be written up as worse. `tool_choice` was never sent — it is broken
on this rig.

**Degradations**, folded from every lane (C3 — nothing degrades silently). Three
in the whole series, all in the muse lane, none affecting an order:

| arm | code | count |
|---|---|---|
| `mixed` | `muse-boundary-superseded` | 1 |
| `mixed` | `muse-insight-late` | 1 |
| `full-qwen` | `muse-boundary-superseded` | 1 |
| `full-gemma` | `muse-insight-late` | 1 |

All 36 seat-turns exited `finished`. No drive aborted. No match was excluded for
truncation. No match was resumed.

---

## What this does not show

- **Anything about general model quality.** One task shape, one scenario, three
  league turns per match, four matches per arm. A model that fills in an
  optional message field is not thereby a better coder.
- **That the arms differ at playing league.** They tied 0–0 on league's own
  outcome metric in every match. What separated was a coordination-interface
  behaviour measured by a secondary metric.
- **That the gap survives a prompt fix.** The single differentiating behaviour
  is the one most likely to move with one sentence of prompt. Until that is
  tried, the ordering above is an ordering of *these prompts on these models*,
  not of the models.
- **That the muse mattered.** Every arm had a muse and there is no muse-off
  cell. The `mixed` vs `full-qwen` difference is suggestive and is explicitly
  not claimed.
- **Generalisation past this rig.** One gateway, one local GB10, one proxy, one
  night, n=4 matches per arm. n=4 cannot detect a small effect, and failing to
  find one is not finding its absence.
