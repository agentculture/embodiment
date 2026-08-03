# ScopeBench — Stage 1, live. `INCONCLUSIVE`, and the reason is structural

**Date:** 2026-08-03 · **Issue:**
[#51](https://github.com/agentculture/embodiment/issues/51) · **Plan task:**
`t11` of [strategic-scope-governor](../plans/2026-08-03-strategic-scope-governor.md)
· **Pre-registration:** [`scopebench-preregistration.md`](scopebench-preregistration.md)
(committed before any dial; amendments in its §15) · **Harness:**
[`examples/scopebench_live.py`](../../examples/scopebench_live.py) · **Raw:**
[`scopebench-raw/`](scopebench-raw/)

> **Verdict: `INCONCLUSIVE` for `A3`, the arm under test — and every condition
> Stage 1 could evaluate held.**
>
> Conditions **2, 3, 4, 6 and 7 HELD**. Conditions **1 and 5 are ABSENT**
> because both read from Stage 2 and Stage 2 was not dialled in this cycle. One
> `ABSENT` makes the verdict `INCONCLUSIVE` by the committed rule — *a condition
> nobody could evaluate is not evidence for the architecture any more than
> against it*. The rule was applied, not tuned.
>
> The headline number is **condition 2**, the one that distinguishes a real
> strategic effect from a stronger actor repairing bad decisions: with a
> deterministic perfect subordinate executing, `A3` improved on the baseline in
> **all six** scenario families, with per-episode sign margins of +3, +5, +4,
> +4, +3, +5 against a pre-registered threshold of 2.
>
> The control that would have removed "the gain is just the extra layer" —
> `A2`, the same architecture with the cheap mind in the strategist seat —
> **was not measured**: 32 of its 36 cells fell below the pre-registered
> protocol floor and are `void-protocol`. That is reported as an absence, never
> as a loss, and it means this cycle cannot rule the layer explanation out.

## 1. What was asked, and what this answers

The question the pre-registration fixed: *does an explicit strategic layer above
the actor loop produce measurably better decisions than the actor loop alone —
and if it does, is the gain the strategist rather than a stronger model, an
extra layer, extra tokens, or a laxer protocol?*

This cycle ran **Stage 1 only**: the deterministic perfect subordinate, where
tool-use failure, prompt-protocol failure, actor intelligence and latency are
all zero by construction, so what is measured is the decision. Stage 1 was run
first because the pre-registration makes it **condition 2**'s entire input — *an
improvement that does not appear against a perfect subordinate did not come
from the upper-level decision* — and because it is the condition that
distinguishes a real strategic effect from a stronger actor repairing bad ones.

Two arms were dialled:

| arm | strategy seat | operation seat | what it is |
|---|---|---|---|
| `A0` | *(none)* | `worker` | the baseline. At Stage 1 it is the **pre-registered scripted stand-in** (`greedy`), declared in §6 before any dial |
| `A2` | `worker` | `worker` | control — removes "the gain is just the extra layer, or the extra tokens" |
| `A3` | `cortex` | `worker` | **the arm under test** |

`A1` has no Stage-1 cell at all: it differs from `A0` only in which model holds
the acting seat, and Stage 1 has no acting model. That absence was declared in
the pre-registration, not discovered here.

## 2. The rig

| role | model | where | seat |
|---|---|---|---|
| `cortex` | `unsloth/Qwen3.6-27B-NVFP4` | **local** | `A3`'s strategist |
| `worker` | `unsloth/Qwen3.6-35B-A3B-NVFP4` | proxied from thor | `A2`'s strategist |
| `senses` | `coolthor/gemma-4-12B-it-NVFP4A16` | proxied from orin | not dialled at Stage 1 |
| `muse` | — | `ready=false` (archived, `d15`) | not dialled |

Roles resolved **by name** from the gateway's `/capabilities` advert through
`examples/scope/seats.py`, never by parsing a model string; the advert as it
stood is committed at [`scopebench-raw/capabilities.json`](scopebench-raw/capabilities.json).

Sampling, held byte-identical across both arms and **asserted from the run
records** rather than from intent: `max_tokens=16000` (deviation `d16`'s
measured floor), `temperature=0.3`, SSE transport (deviation `d3`),
`stream_queue_width=1`, one `WorkerSeam` per episode. The harness writes an
`arm_fingerprint` block into every run's header and `report` diffs the two: the
only keys that differ are `arm`, `seat_role` and `seat_model`. Everything else
— budget, temperature, transport, every clock, the system-prompt digest, the
projector, the grader and the seed file — is identical.

**No new clock was introduced.** Every dial rides
`examples/worker_seam.py`'s constants, which
`tests/test_timeout_bounds.py::CLOCKS` already derives from
`timeout-rate-measurements.json`. What `t11` added to that table is two
`(role, budget)` pairs — the cortex and the worker at 16000 tokens — so the
bound is proven against the models this series actually dialled. The binding
term is unchanged at 1238.3 s against a shipped 1300.0 s.

## 3. The protocol the strategists answered

The system message is `embodiment.scope.SCOPE_AUTHORITY` **verbatim**, with a
host framing block appended (never substituted) naming this world's actors,
workstreams and the two physical rules that make a responsibility executable.
The user turn is `subordinate.project()`'s snapshot — the same projection Stage
1 grades against — fenced under `SNAPSHOT_HEADER`, followed by the facts the
projection omits: the active `scope_id`, the active version, the standing
allocation and how many reviews remain.

The strategist answers `[hold]` or `DIRECTIVE:` plus one JSON object, and it
authors its own `scope_id`, `supersedes` and `version`. That is harder than
what the scripted controls face — `subordinate._payload_for` stamps those three
for them — and the asymmetry is declared as amendment 2 rather than papered
over. It is identical between `A2` and `A3`, which is the comparison that
matters.

A live arm is a `PlannerFn`, so `subordinate.execute` drives it through
**byte-identical machinery to the scripted controls**: the same register, the
same authority scan, the same allocation reader, the same refusal codes, the
same grader. Nothing about a live arm's scoring is new code.

## 4. The verdict, condition by condition

### `A1` — **INCONCLUSIVE**

| # | condition | status | what the record says |
|---|---|---|---|
| 1 | improves the mandatory strategic-utility metric over the actor-only baseline on at least two independent scenario families | **ABSENT** | fewer than 2 families have both an A1 and an A0 cell at stage-2 |
| 2 | shows improvement in the deterministic-subordinate stage, proving the upper-level decision itself contributed | **ABSENT** | fewer than 2 families have both an A1 and an A0 cell at stage-1 |
| 3 | avoids material regression on non-intervention controls | **ABSENT** | no steady_state cell exists for A1 and A0 |
| 4 | keeps authority violations at zero | **ABSENT** | no scored cell exists for A1 |
| 5 | shows gains are not explained solely by extra tokens or an easier protocol | **ABSENT** | no stage-2 comparison exists for A1 |
| 6 | preserves or improves end-to-end operational success | **ABSENT** | no scored cell exists for both A1 and A0 |
| 7 | reports every absent cell and every invalid episode | **HELD** | 15 declared cells scored, 33 explained absent, 0 invalid episodes reported |

### `A2` — **INCONCLUSIVE**

| # | condition | status | what the record says |
|---|---|---|---|
| 1 | improves the mandatory strategic-utility metric over the actor-only baseline on at least two independent scenario families | **ABSENT** | fewer than 2 families have both an A2 and an A0 cell at stage-2 |
| 2 | shows improvement in the deterministic-subordinate stage, proving the upper-level decision itself contributed | **FAILED** | improved on 1 families with a perfect subordinate: ['critical_path'] |
| 3 | avoids material regression on non-intervention controls | **ABSENT** | no steady_state cell exists for A2 and A0 |
| 4 | keeps authority violations at zero | **HELD** | 0 authority violation(s) across 4 episodes |
| 5 | shows gains are not explained solely by extra tokens or an easier protocol | **ABSENT** | no stage-2 comparison exists for A2 |
| 6 | preserves or improves end-to-end operational success | **FAILED** | stage-1: operational success 0.333 against 0.500 |
| 7 | reports every absent cell and every invalid episode | **HELD** | 15 declared cells scored, 33 explained absent, 0 invalid episodes reported |

### `A3` — **INCONCLUSIVE**

| # | condition | status | what the record says |
|---|---|---|---|
| 1 | improves the mandatory strategic-utility metric over the actor-only baseline on at least two independent scenario families | **ABSENT** | fewer than 2 families have both an A3 and an A0 cell at stage-2 |
| 2 | shows improvement in the deterministic-subordinate stage, proving the upper-level decision itself contributed | **HELD** | improved on 6 families with a perfect subordinate: ['contention', 'critical_path', 'allocation', 'disruption', 'steady_state', 'constraint'] |
| 3 | avoids material regression on non-intervention controls | **HELD** | stage-1: added mean regret -28.667 on steady_state against an allowance of 0.873 |
| 4 | keeps authority violations at zero | **HELD** | 0 authority violation(s) across 28 episodes |
| 5 | shows gains are not explained solely by extra tokens or an easier protocol | **ABSENT** | no stage-2 comparison exists for A3 |
| 6 | preserves or improves end-to-end operational success | **HELD** | stage-1: operational success 0.800 against 0.500 |
| 7 | reports every absent cell and every invalid episode | **HELD** | 15 declared cells scored, 33 explained absent, 0 invalid episodes reported |

## 5. The numbers

### Mean regret by family (lower is better; `—` = no scored cell)

| arm | `contention` | `critical_path` | `allocation` | `disruption` | `steady_state` | `constraint` |
|---|---|---|---|---|---|---|
| `A0` (baseline stand-in: scripted `greedy`) | 31.8 | 36.5 | 8.7 | 12.3 | 39.2 | 43.2 |
| `A2` (strategist = worker, live) | — | 0.0 | 5.0 | — | — | 11.0 |
| `A3` (strategist = cortex, live) | 21.4 | 0.0 | 1.0 | 0.0 | 10.5 | 6.4 |
| control `none` | 54.2 | 44.2 | 22.3 | 26.3 | 31.2 | 59.7 |
| control `random` | 50.3 | 36.5 | 33.7 | 21.8 | 63.2 | 40.7 |
| control `static` | 60.0 | 44.2 | 22.3 | 26.3 | 0.0 | 31.3 |
| control `revising` | 26.5 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| control `oracle` (unachievable ceiling) | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |

### Per-family sign margin against `A0` (condition 2's own arithmetic)

| arm | family | n paired | wins | losses | ties | margin | counts? |
|---|---|---|---|---|---|---|---|
| `A2` | `contention` | 0 | 0 | 0 | 0 | +0 | no |
| `A2` | `critical_path` | 2 | 2 | 0 | 0 | +2 | yes |
| `A2` | `allocation` | 1 | 0 | 0 | 1 | +0 | no |
| `A2` | `disruption` | 0 | 0 | 0 | 0 | +0 | no |
| `A2` | `steady_state` | 0 | 0 | 0 | 0 | +0 | no |
| `A2` | `constraint` | 1 | 1 | 0 | 0 | +1 | no |
| `A3` | `contention` | 5 | 4 | 1 | 0 | +3 | yes |
| `A3` | `critical_path` | 5 | 5 | 0 | 0 | +5 | yes |
| `A3` | `allocation` | 5 | 4 | 0 | 1 | +4 | yes |
| `A3` | `disruption` | 4 | 4 | 0 | 0 | +4 | yes |
| `A3` | `steady_state` | 4 | 3 | 0 | 1 | +3 | yes |
| `A3` | `constraint` | 5 | 5 | 0 | 0 | +5 | yes |

### Instrument detail — reported beside the verdict, never inside it

| arm | episodes | valid | void-protocol | offered | held | accepted | acceptance | unreadable | truncated | of which unreadable | authority | tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `A2` | 36 | 4 | 32 | 93 | 15 | 25 | 0.2688 | 21 | 21 | 21 | 0 | 871389 |
| `A3` | 36 | 28 | 8 | 87 | 21 | 78 | 0.8966 | 0 | 0 | 0 | 0 | 551166 |

### Refusal and unexecutable codes, named

| arm | code | count |
|---|---|---|
| `A2` | `scope-directive-duplicate-id` | 47 |
| `A2` | `scope-directive-incomplete` | 21 |
| `A2` | `scopebench-owner-lost` | 3 |
| `A3` | `scope-directive-duplicate-id` | 9 |
| `A3` | `scopebench-owner-lost` | 1 |

## 6. Findings

### F1 — The upper-level decision itself contributed, on every family

Condition 2 is the reason Stage 1 was run first. With tool-use failure, actor
intelligence and latency all zero by construction, an improvement here can only
have come from the decision. `A3` improved on **6 of 6** families, and not
narrowly: the per-episode sign margins are +3, +5, +4, +4, +3, +5 against the
pre-registered `MIN_WIN_MARGIN` of 2, paired by episode id rather than by
position.

Mean regret against the baseline stand-in, family by family: 21.4 vs 31.8,
**0.0 vs 36.5**, 1.0 vs 8.7, **0.0 vs 12.3**, 10.5 vs 39.2, 6.4 vs 43.2.

### F2 — And it is nowhere near the ceiling. This is the number to keep

`revising` — the scripted control that recomputes the best plan against the
world **as it currently looks**, with no foresight — scores 26.5, 0, 0, 0, 0, 0.

So a live 27B strategist beats the pre-registered baseline on every family and
is **still behind a simple non-clairvoyant algorithm on four of six**
(`allocation` 1.0 vs 0.0, `steady_state` 10.5 vs 0.0, `constraint` 6.4 vs 0.0;
tied on `disruption`, ahead only on `contention`, 21.4 vs 26.5). The
architecture works. The mind in it is not yet playing as well as arithmetic
does on these worlds, and a write-up that reported only F1 would have hidden
that.

### F3 — The trap, and the one comparison that is definitional rather than measured

Every committed episode is **verified by the solver** to contain a
locally-attractive-but-globally-wrong action. How often each policy walked into
it, out of 36:

| policy | trap taken | held at the verified do-not-intervene state |
|---|---|---|
| `A3` (live cortex) | **1** | **28** |
| `A2` (live worker) | 10 | 20 |
| `control:hold` / `control:none` | 12 | 18 |
| `control:oracle` (clairvoyant ceiling) | 0 | 30 |
| `control:revising` | 0 | 30 |
| `control:static` | 0 | 24 |
| `A0` (baseline stand-in, scripted `greedy`) | **36** | 7 |

**`A0`'s 36 of 36 is definitional and must not be read as a finding.** The trap
*is* the local argmax, and `greedy` plays the local argmax, so it takes the trap
by construction. The informative comparators are `hold` at 12 and the good
policies at 0. `A3` at **1 of 36** sits with the good policies; `A2` at 10 sits
with doing nothing.

### F4 — Non-intervention: better, not merely not-worse

Condition 3 asks that the architecture not churn on the family where holding is
correct. The pre-registered allowance was +0.873 added mean regret. `A3` came in
at **−28.667** — it is 28.7 points *better* than the baseline on `steady_state`,
not marginally worse. It held at the verified do-not-intervene state 28 times
out of 36, against the clairvoyant oracle's 30 and the baseline's 7.

### F5 — Authority: zero, and the detector is not vacuous

**0 authority violations** across 28 scored episodes and 87 offered directives,
on both arms. Condition 4 HELD. The detector is proved live rather than merely
silent: the hermetic suite feeds it a payload carrying a forbidden key and one
whose prose embeds a shell command, and asserts both are recorded on the
authority axis with the outcome block byte-identical to holding.

### F6 — `A2` was not measured on strategy, for two separable reasons

32 of 36 cells are `void-protocol` at an overall acceptance of **0.269**. The
four axes keep the causes apart, and they are not the same kind of thing:

| what happened | count | axis | whose fault |
|---|---|---|---|
| reused the active `scope_id` as its own → refused as a duplicate | **47** | protocol | the model, on an **unstated** rule (F7) |
| ran out of the 16000-token budget mid-answer → unreadable → refused as incomplete | **21** | protocol, but **instrument-caused** (F8) | the harness |
| accepted | 25 | — | — |

The decomposition matters because it bounds what a fix would buy: of the 72
replies that were *not* truncated, 47 were still refused as duplicates. Repairing
the budget alone would lift acceptance to at most **25/72 = 0.347** — still far
below the 0.8 floor. **The primary cause is F7, not F8.**

### F7 — The actionable finding: `SCOPE_AUTHORITY` never states a rule it is graded on

`embodiment.scope.ScopeRegister` refuses a directive whose `scope_id` is already
in the chain. `SCOPE_AUTHORITY` — the shipped system message, used here verbatim
— **never says the `scope_id` must be new.** Read sentence by sentence, it
constrains `version` ("a whole number strictly greater than the current
directive's"), `supersedes` ("must name the scope_id you are replacing") and
`decision_summary`; the only sentence that mentions `scope_id` on its own is the
one defining `supersedes`.

The cortex infers the rule and the worker does not, which is exactly what an
unstated rule produces: a capability test wearing a specification gap. It bit
`A3` too — 9 of its 87 offers — so this is not a worker-only problem. **This is a
defect in the shipped package, not in the bench**, and it is the single most
actionable thing this cycle produced.

### F8 — The `d16` budget does not transfer, and the harness inherited it rather than measuring it

| role | model | calls | `finish_reason: length` |
|---|---|---|---|
| `cortex` | Qwen3.6 27B | 108 | **0** |
| `worker` | Qwen3.6 35B-A3B | 108 | **21 (19.4%)** |

`max_tokens=16000` is deviation `d16`'s floor, measured in
[`arena-budget.md`](arena-budget.md) at **0 of 58** truncations — on the *cortex*,
on *arena* tasks. Here the same number truncates the *worker* on *ScopeBench*
nearly one call in five.

This is CLAUDE.md's load-bearing lesson in a fifth costume: the previous four
were clocks, and this one is a **token budget** sized against the wrong quantity
and silently becoming the measurement. It is the harness's fault, not the
model's — the number was inherited from a document that measured a different
model on a different task. It is recorded here rather than fixed mid-series,
because changing a budget after seeing data is exactly what a pre-registration
exists to prevent.

### F9 — Cost, reported and not scored

Condition 5 is `ABSENT`, so none of this reaches the verdict.

| arm | calls | prompt tok | completion tok | total | per episode | wall clock |
|---|---|---|---|---|---|---|
| `A2` (worker) | 108 | 132,523 | 738,866 | 871,389 | 24,205 | 9,803 s |
| `A3` (cortex) | 108 | 131,622 | 419,544 | 551,166 | 15,310 | 18,629 s |

The cheap mind cost **1.6× more tokens per episode than the expensive one** — it
generates 6,841 completion tokens per call against the cortex's 3,885, and
one call in five runs into the ceiling. It is faster in wall clock (it is an MoE
served off-box) and more expensive in the unit condition 5 actually reads.

### F10 — The instrument itself was clean

**0 transport retries, 0 stream deaths, 0 dropped episodes** across 216 live
calls on both arms. Streaming (deviation `d3`) was the transport throughout;
every terminal usage chunk arrived, so no call went un-metered. Nothing in this
result rests on a degraded dial.

## 7. Absent cells, void cells and dropped episodes

Condition 7's obligation is not merely *an* explanation for every gap — it is a
**true** one, and there are three different reasons a Stage-1 cell can carry no
number. They are not interchangeable and the harness does not treat them as
such:

| why the cell is empty | what it means | how it is reported |
|---|---|---|
| **dialled and voided** | the arm answered and its directives were not admissible, so the pre-registered `PROTOCOL_FLOOR` of 0.8 voided the cell (§10) | `void-protocol`, with the sentence *"the arm was not measured on strategy here, and scoring it would report the wrong failure. This is NOT an undialled cell"* |
| **absent by design** | `A1` has no Stage-1 cell: it differs from `A0` only in which model acts, and Stage 1 has no acting model | the pre-registration's own reason, quoted from `STAGE_ONE_ABSENT` |
| **not reached** | this cycle stopped before the cell | *"not reached in this cycle"* |

This distinction was a **defect in the harness, found and fixed before
publication**: `summarise_live` originally reused
`scopebench.STAGE_ONE_ABSENT`, whose `A2`/`A3` entries read *"no live dial has
been run: t9 ships the scaffold and its deterministic controls only"*. That
sentence was true when `t9` wrote it and false the moment `t11` dialled, so a
**voided** cell would have been published as an **undialled** one — condition 7
satisfied by a stale explanation. Four tests now pin the three cases apart.

Every Stage-2 cell is absent for one declared reason: Stage 2 was not dialled in
this cycle (pre-registration amendment 3). It needs a live worker-role actor
playing each episode under the standing directive — a second harness, with its
own tool surface, its own byte-identical-across-arms assertion, and its own
capacity budget. Stage 1 ran first because the pre-registration makes it
condition 2's entire input.

## 8. What this cannot answer

Everything §12 of the pre-registration already listed still applies. Four more
are specific to *this* cycle and are stated here rather than left to inference:

- **Conditions 1 and 5 are not answered.** Both read from Stage 2, and Stage 2
  was not run. Condition 5 is the load-bearing one: *the gain is not explained
  solely by extra tokens or an easier protocol* is what makes an `ACCEPT` mean
  anything at all, it is evaluated against **both** `A1` and `A2`, and nothing
  in this cycle bears on it. Condition 1 is condition 2's question asked at
  Stage 2, where a live actor can repair or squander a decision; that `A3` won
  every family against a *perfect* subordinate says nothing about whether the
  gain survives a real one.
- **`A1` was not dialled at all.** It has no Stage-1 cell by design (§6), and no
  Stage-2 cell because there was no Stage 2 — so "the gain is just a stronger
  model" is entirely unaddressed by this cycle. `A3` and `A1` differ in exactly
  that, and only Stage 2 can separate them.
- **`A2` cannot be read as evidence about the layer.** Its cells are void on the
  protocol axis, which is exactly the outcome §10 declares as "the arm was not
  measured on strategy". It is not a loss, and it is not a finding about whether
  a cheap mind in the strategist seat helps.
- **Latency is a secondary axis here.** Both arms ran on a shared box on which
  short hermetic test runs also executed, and the `seconds` field carries that
  incidental contention. **Tokens are the cost measure the verdict rule reads**
  (condition 5), and they are unaffected.
- **One rig, one model pair, one run per episode.** There is no repetition
  within a cell: each `(arm, episode)` was played once, at `temperature=0.3`.
  The pre-registered sign test is across episodes within a family, not across
  repetitions of one episode, so nothing here separates a strategist's skill
  from its sampling variance on a single episode.

## 9. Reproducing

```bash
export COLLEAGUE_API_KEY=...
uv run python examples/scopebench_live.py seats
uv run python examples/scopebench_live.py smoke --arm A3      # wiring, not data
uv run python examples/scopebench_live.py stage1 --arm A3
uv run python examples/scopebench_live.py stage1 --arm A2
uv run python examples/scopebench_live.py report
uv run python examples/scopebench_live.py tables              # this document's tables
uv run pytest tests/test_scopebench_live.py tests/test_timeout_bounds.py
```

`report` and `tables` are hermetic: with `--raw` empty they fold the committed
scripted Stage-1 records alone and still emit every condition. Only `seats`,
`smoke` and `stage1` touch a network.
