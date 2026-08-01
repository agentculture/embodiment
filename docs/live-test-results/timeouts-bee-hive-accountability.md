# The `error-derived-timeouts-bee-hive-architecture` cycle — results, verdicts and the accountability map

**Task `t14`.** Every promise this cycle made, resolved to a shipped artifact
path or an honest **ABSENT**. Every verdict published with its decision rule
quoted beside it. Every before-state figure cited to the document that measured
it rather than restated from memory — that last one is honesty condition `h19`,
and it is the reason this file cites rather than asserts.

Sources of truth, in the order a reader should reach for them:
[spec](../specs/2026-08-01-error-derived-timeouts-bee-hive-architecture.md) ·
[plan](../plans/2026-08-01-error-derived-timeouts-bee-hive-architecture.md) ·
[session contract](../session-contract-2026-08-01.md) ·
[corrections](corrections.md) · `devague deviate --list`.

## Read this first — the three uncomfortable facts

Stated at the top rather than buried, because the previous cycle's summary set
that bar and this one inherits it.

1. **The Bee-Hive was measured on one arm, not against the field.** `h1`
   requires *"B and P dialled against E/W/M/H — not merely designed; an
   unmeasured design does not count as shipped."* **B1 was dialled** — 48 cells,
   7,200 calls. **B0, B2 and P were not**, and neither was any comparison
   against E/W/M/H. The stop rule said `CONTINUE`; the cycle closed at rung one.
   `h1` is **partially ABSENT** and §5 says exactly which halves.
2. **One of `c30`'s three success signals failed on measurement.** A drone's
   second evocation makes **0 cortex calls** (PASS, and structurally so) at
   **32.0%** of its authoring tokens against a ≤5% target (**FAIL**). The cause
   is a real defect this cycle found and did not fix — the drone tier cannot
   turn thinking off. Reported as a failure, not renegotiated into a pass.
3. **I published a defect against the width rung that its own records refute.**
   The rung *did* pin thinking-off on the wire and assert it in all 48 cells;
   `drone-economics.md` said it did not. Corrected in
   [`corrections.md` §12](corrections.md), and unusual for being an error in the
   *unflattering* direction.

---

## 1. `c30` signal 1 — no timeout can censor evidence again

> `c30`: *"`test_timeout_bounds.py` goes red on any timeout mutated below its
> derived bound."*

**Verdict: PASS**, machine-checkable on every CI run.
Artifacts: [`tests/test_timeout_bounds.py`](../../tests/test_timeout_bounds.py)
(120 passed, 3 skipped) · [`tests/rate_config.py`](../../tests/rate_config.py) ·
[`timeout-rate-measurements.md`](timeout-rate-measurements.md).

### Every constant the walk covers, by value

Recomputed from the committed rate config, not typed by hand. The rule is
`bound = max_tokens / slowest_measured_rate` per fronted model, times the turn
budget, with the queue allowance added or absorbed by the arithmetic in
`per_turn_bound` rather than by judgement.

| constant | kind | shipped | derived bound | margin | in #42's audit |
|---|---|---:|---:|---:|---|
| `worker_seam.REQUEST_TIMEOUT` | client-timeout | 1300.0 | 1238.29 | 1.05× | yes |
| `league_commander.REQUEST_TIMEOUT` | client-timeout | 1600.0 | 1501.29 | 1.07× | yes |
| `league_h2h.REQUEST_TIMEOUT` | client-timeout | 1800.0 | 1501.29 | 1.20× | yes |
| `devague_legs.REQUEST_TIMEOUT_S` | client-timeout | 600.0 | 365.72 | 1.64× | yes |
| `muse_arms.REQUEST_TIMEOUT_S` | client-timeout | 600.0 | 427.14 | 1.40× | yes |
| `worker_throughput.BATCH_WAIT_TIMEOUT_SECONDS` | wait-deadline | 300.0 | 272.13 | 1.10× | yes |
| `orchestrator_tools.DEFAULT_FANOUT_TIMEOUT` | wait-deadline | 14860.0 | 14859.53 | 1.00× | yes |
| `worker_scoped_overhead.BATCH_WAIT_TIMEOUT_SECONDS` | wait-deadline | 300.0 | 230.97 | 1.30× | **no** |

Plus **3 streaming clocks** (`STREAM_FIRST_CHUNK_TIMEOUT` 2958.6 s,
`STREAM_IDLE_TIMEOUT` 60.0 s, `STREAM_TOTAL_TIMEOUT` 4258.6 s) and **4
retry backoffs** declared with a stated exemption apiece.

### `r7`: the count moved from 7 to 8, and why that is the point

The signal as written says *"7 of 7 constants under the CI test."* **The shipped
test walks 8.** Plan risk `r7` pre-declared this, so the honest report is
**8 of 8 pass**, not 7 of 7.

The eighth is `worker_scoped_overhead.BATCH_WAIT_TIMEOUT_SECONDS`. Nobody had
listed it: #42's audit named six, `c16` found the fan-out deadline it missed
making seven, and `t2` was scoped against those seven. The test closes the
category **by AST** — every module-level float in `examples/` whose name reads
like a timeout must be walked — so the eighth turned itself in. Full account in
[`corrections.md` §11](corrections.md).

That is the difference between a list and a category: *a list somebody maintains
goes stale the first time it is not maintained; a category that closes itself
does not.* Reporting 7 of 7 would have hidden the one result that proves the
mechanism works.

### The four clocks this cycle found mis-sized

The load-bearing lesson, because it recurred four times in one cycle: **a clock
sized against the wrong quantity silently becomes the measurement.** Three of
the four were invisible in the record at the moment they fired.

| clock | was | was sized against | is | what it cost |
|---|---:|---|---:|---|
| `worker_seam.REQUEST_TIMEOUT` | 300 s | a latency percentile | 1300 s | censored a completion-length distribution while recording `truncated: false` |
| `orchestrator_tools.DEFAULT_FANOUT_TIMEOUT` | 60 s | one call | 14860 s | **1/248th** of the drive it bounded ([README](README.md#the-experiments), amendment 2) |
| `worker_seam.RETRY_SLEEP_SECONDS` | 20 s | nothing | 20 s (exempt, recorded) | timed *inside* the call it retries; corrupted three results until gate 1 |
| lobes' `GATEWAY_READ_TIMEOUT` | 600 s | nothing reachable | sibling's | killed two live series calls at 2460 s each while the client sat at 1200 s ([streaming-probe.md](streaming-probe.md)) |

The fourth is the one that generalises furthest, and **#42's rule alone cannot
fix it**: it lives in the *lobes process*, where no client value can
reach it. #42's rule is necessary and not sufficient — **every hop between
caller and model has its own bound, and the smallest binds.** Filed as
[lobes-cli#169](https://github.com/agentculture/lobes-cli/issues/169).

### Streaming, which retires the class rather than raising it

Deviation **`d3`** (approved, `risky`) promoted streaming from a flag-gated
pilot to the **default transport** for cortex and worker dials.
[`streaming-probe.md`](streaming-probe.md) measured why: through a **43.45 s**
think the largest inter-chunk gap was **0.124 s** — a **~4,800×** margin against
even a 600 s idle bound. With chunks flowing, generation length stops being the
binding quantity at every hop at once.

Two live findings the probe returned that a design could not have:

- the reasoning delta field is **`delta.reasoning`**, *not* vLLM's documented
  `reasoning_content`;
- the terminal usage chunk needs `stream_options: {include_usage: true}`,
  otherwise token counts vanish from a streamed call.

**The scoped lane deliberately does not stream** (`examples/arch_hive.py`,
`examples/drone_host.py`). Its calls are tens of tokens, there is nothing to
watch arrive, and — decisively — **its clock is the width rung's outcome
metric**. Pinned by `tests/test_arch_hive.py::TestTheScopedLaneDoesNotStream`,
including an assertion that the *reason* stays adjacent to the code.

---

## 2. `c30` signal 2 — the width rung produces a verdict either way

> `c30`: *"the width rung produces either measured separation or an honest
> `CEILING`/`INCONCLUSIVE` published either way."*

**Verdict: PASS — and the outcome was separation, on all four grains.**
Artifacts: [`bee-hive-width.md`](bee-hive-width.md) ·
[`bee-hive-width-preregistration.md`](bee-hive-width-preregistration.md)
(1,310 lines, committed before the first cell) ·
[`bee-hive-width-raw/`](bee-hive-width-raw/) ·
[`tests/test_bee_hive_width_preregistration.py`](../../tests/test_bee_hive_width_preregistration.py)
(75 tests pinning the constants).

### The decision rule, quoted before the result

> §10 rule 3, and `direction_required(3) = 3`: **a width separates only by
> beating the control on all three paired repetitions.**
> `items_per_second = ITEMS_PER_CELL / cell_wall_seconds`, computed from **clean
> batches only** — gate 1 (`retry-contaminated`) drops any batch whose calls
> carried a transport retry, because a 20 s backoff timed inside the call it
> retries is the instrument, not the result.

| grain | items/call | verdict | separated at width |
|---|---:|---|---:|
| `unit` | 1 | **SEPARATED-WIDTH** | 8 |
| `pair` | 2 | **SEPARATED-WIDTH** | 8 |
| `batch4` | 4 | **SEPARATED-WIDTH** | 8 |
| `batch8` | 8 | **SEPARATED-WIDTH** | 8 |

**Stop rule: `CONTINUE`** — the ladder continues to W2, then H0, then the
B0/B1/B2 sweep. None of those ran; see §5.

This is the axis **four prior experiments never reached**. The counsel and
sequential axes tied at a ceiling every time; width is where orchestration must
win by construction, and it did.

### Wall-clock and token tables, per arm per rung

W1 is a **within-arm** rung — all 48 cells are arm `B1` — so the arm column is
constant and the real decomposition is grain × width. Saying so beats faking a
second arm column.

| arm | grain | width | cells | wall (s) | raw items/s | graded items/s | calls | prompt tok | completion tok | tok/call | retries |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B1 | `unit` | 1 | 3 | 218.4 | 4.396 | **4.397** | 960 | 131,958 | 6,396 | 6.66 | 0 |
| B1 | `unit` | 2 | 3 | 171.6 | 5.596 | **5.596** | 960 | 131,958 | 6,396 | 6.66 | 0 |
| B1 | `unit` | 4 | 3 | 106.6 | 9.009 | **9.016** | 960 | 131,958 | 6,396 | 6.66 | 0 |
| B1 | `unit` | 8 | 3 | 453.5 | *2.117* | **14.093** | 960 | 131,958 | 6,396 | 6.66 | 18 |
| B1 | `pair` | 1 | 3 | 160.5 | 5.981 | **5.981** | 480 | 91,494 | 6,396 | 13.32 | 0 |
| B1 | `pair` | 2 | 3 | 119.4 | 8.041 | **8.041** | 480 | 91,494 | 6,396 | 13.32 | 0 |
| B1 | `pair` | 4 | 3 | 74.0 | 12.969 | **12.970** | 480 | 91,494 | 6,396 | 13.32 | 0 |
| B1 | `pair` | 8 | 3 | 197.7 | *4.857* | **20.706** | 480 | 91,494 | 6,396 | 13.32 | 7 |
| B1 | `batch4` | 1 | 3 | 122.6 | 7.833 | **7.833** | 240 | 71,262 | 6,396 | 26.65 | 0 |
| B1 | `batch4` | 2 | 3 | 87.7 | 10.948 | **10.949** | 240 | 71,262 | 6,396 | 26.65 | 0 |
| B1 | `batch4` | 4 | 3 | 56.0 | 17.157 | **17.158** | 240 | 71,262 | 6,396 | 26.65 | 0 |
| B1 | `batch4` | 8 | 3 | 140.9 | *6.813* | **26.561** | 240 | 71,262 | 6,396 | 26.65 | 5 |
| B1 | `batch8` | 1 | 3 | 104.0 | 9.233 | **9.233** | 120 | 61,146 | 6,396 | 53.30 | 0 |
| B1 | `batch8` | 2 | 3 | 72.9 | 13.173 | **13.176** | 120 | 61,146 | 6,396 | 53.30 | 0 |
| B1 | `batch8` | 4 | 3 | 46.2 | 20.765 | **20.767** | 120 | 61,146 | 6,396 | 53.30 | 0 |
| B1 | `batch8` | 8 | 3 | 73.2 | *13.114* | **30.490** | 120 | 61,146 | 6,396 | 53.30 | 2 |

**Rung totals: 48 cells, 2,205.0 s wall clock, 7,200 model calls, 32 retries,
1,423,440 prompt + 102,336 completion tokens.**

### The gate is not a rounding detail — it inverts the result

| grain | graded w1 | graded w8 | speedup | raw w8 | raw speedup |
|---|---:|---:|---:|---:|---:|
| `unit` | 4.397 | 14.093 | **3.21×** | 2.117 | *0.48×* |
| `pair` | 5.981 | 20.706 | **3.46×** | 4.857 | *0.81×* |
| `batch4` | 7.833 | 26.561 | **3.39×** | 6.813 | *0.87×* |
| `batch8` | 9.233 | 30.490 | **3.30×** | 13.114 | *1.42×* |

On raw whole-cell wall clock, width 8 reads **slower than width 1** at three of
four grains — the fastest configuration reported as the slowest, purely because
20 s backoffs are timed inside the cells. Every retry in the series happened at
width 8 (**32 of 7,200 calls, 1.78%**); widths 1, 2 and 4 recorded **zero in
5,400 calls**. Publishing only the raw reading would have refuted the design
with an artifact of the instrument.

The gate was **pre-registered**, not invented once the numbers looked wrong.
That is the whole reason it is trustworthy here.

### Validity, stated with its limits

- **Acceptance was perfect in every cell**: 7,200 of 7,200 accepted, all
  `finish_reason: stop`, **0 truncations, 0 refusals, 0 abandoned calls**. Every
  width-8 retry eventually succeeded, so the instability cost wall clock and
  never data.
- **Prefill binds, not generation**: 1,423,440 prompt against 102,336
  completion tokens is **13.9:1**. At 14.21 completion tokens per call, a
  scoped-call sweep is a prompt-processing workload wearing a generation
  workload's costume — which is why the achieved speedup sits near 3.3× rather
  than 8×.
- **`t8`'s prediction held.** [`worker-scoped-overhead.md`](worker-scoped-overhead.md)
  predicted width 8 would buy **3.44× lean / 1.83× realistic**, against the
  ~9× the older throughput sweep implied. W1 measured **3.21×–3.46×**. The
  pre-measurement bracketed the result; the older extrapolation would not have.
- **Acceptance is not correctness.** No cell in this rung graded whether a
  scoped answer was *right* — `items_per_second` is the outcome metric and
  acceptance was deliberately kept out of the numerator. The rung says nothing
  about answer quality, at any width or any thinking setting.
- **`n` is 3 paired repetitions per cell on one rig with one model**
  (`unsloth/Qwen3.6-35B-A3B-NVFP4` on Thor). Consistency within cells is tight
  (unit width 8: 14.091 / 14.068 / 14.120); generality beyond this rig is
  unmeasured.

---

## 3. `c30` signal 3 — authored intelligence becomes reusable

> `c30`: *"a drone's second evocation completes its task with zero cortex turns,
> reporting call-acceptance as its own axis"* — and the spec's numeric form:
> **0 cortex calls at ≤5% of authoring tokens.**

**Verdict: split — PASS on cortex calls, FAIL on tokens.**
Artifacts: [`drone-economics.md`](drone-economics.md) ·
[`drone-authoring-cost.json`](drone-authoring-cost.json) ·
[`drone-evocation-cost.json`](drone-evocation-cost.json) ·
`.drones/index-gaps/` · [`examples/author_drone.py`](../../examples/author_drone.py)
· [`examples/drone_host.py`](../../examples/drone_host.py).

| | measured | target | |
|---|---:|---:|---|
| cortex calls, second evocation | **0** | 0 | **PASS** |
| completion tokens, second evocation | **1,532** | ≤ 239 | **FAIL** |
| ratio of authoring cost | **32.0%** | ≤ 5% | **FAIL** |

Authoring: **4,785 completion tokens over 199.3 s**, one cortex turn, streamed,
`finish_reason: stop`. Second evocation: **3 scoped worker calls, all
accepted**, 277 prompt + 1,532 completion tokens, correct answer (it found 3
genuinely unindexed results documents).

**Zero cortex calls is structural, not lucky.** A drone's only outward seam is
`DroneRequest.ask`, which reaches the worker the host wires and nothing else;
there is no escalation path in v1 (`c46`). That half cannot fail without the
design changing.

**The token half fails for one identifiable reason: 511 completion tokens per
scoped call against `t8`'s ~15** — a 34× gap that is not the prompt. It is that
`WorkerSeam` takes no thinking/extra-body parameter, so nothing downstream can
send `chat_template_kwargs`, and the server default for this prompt shape is
*on*. The first attempt made it maximally visible: at `max_tokens=256` the call
spent **all 256 tokens thinking and emitted no content** — issue #32's exact
shape, reproduced in a second lane.

**This is reported as a failure, not renegotiated.** With thinking off the cost
target would clear at ≈45 tokens (0.94%), but arriving there by disabling
reasoning would be **optimising the metric rather than the outcome**: nothing in
this cycle graded whether thinking-off scoped answers are *correct*. `t8`'s "106
of 106 answered" measured shape; W1 kept acceptance out of its numerator on
purpose. The honest remedy is to make the mode controllable and pinned, then
measure cost **and** quality at both settings on a graded task — recorded in
`drone-economics.md` §"What is owed", not silently fixed here.

`r6` predicted this signal might have to be reported **ABSENT** for want of a
denominator. It is not ABSENT: both halves were measured. One passed and one
failed, which is a better outcome than an unmeasured target either way.

---

## 4. Before-state → after-state, every figure cited

`h19`: *every before-state figure stays cited to its measurement doc.*
`h21`: *each after-state promise maps to a plan task with acceptance criteria.*

| `c27` before-state, as written | cited to | `c29` after-state promise | shipped as | state |
|---|---|---|---|---|
| seven per-file timeout constants | #42 audit + `c16` | every client timeout and wait deadline derived from its token budget, CI red on drift | `tests/test_timeout_bounds.py` (8 clocks, 3 stream clocks, 4 backoffs) | **delivered** — and 8, not 7 |
| one constant already censored a completion-length distribution while recording `truncated: false` | [`arena-budget.md`](arena-budget.md) (2048 default truncated **5 of 83 = 6.0%**; 16000: **0 of 58**) | no timeout can censor evidence again | derived bounds + streaming default (`d3`) | **delivered** |
| a flat-favouring verdict measured entirely on the counsel/sequential axis, width never reached | [`league-h2h.md`](league-h2h.md), [`muse-arms.md`](muse-arms.md), [`association-work.md`](association-work.md) | a measured dose-response curve (B0/B1/B2 sweep, P with controls) | **width rung only** — `bee-hive-width.md` | **partial / ABSENT** (§5) |
| no reuse of authored intelligence — every recurring task re-pays a full cortex turn (5,000–14,265 tokens, 400–730 s) | [`league-commander.md`](league-commander.md), [`arena-series.md`](arena-series.md) | an evoked drone repeats a recurring task for tens of worker tokens and zero cortex turns | `embodiment/drone.py` + `.drones/index-gaps/` | **delivered, target missed** — 0 cortex turns ✓, 1,532 tokens ✗ |
| the ~9× concurrency figure, assumed to transfer to scoped calls | [`worker-throughput.md`](worker-throughput.md) — and its stability section is **superseded**, see [`corrections.md` §10](corrections.md) | granularity chosen from measured tiny-call overhead | [`worker-scoped-overhead.md`](worker-scoped-overhead.md) → W1 confirmed 3.21–3.46× | **delivered** |

---

## 5. What is ABSENT, and why — `h1` in full

`h1` is the honesty condition with teeth: *"the bee-hive measured — B and P
dialled against E/W/M/H — not merely designed; **an unmeasured design does not
count as shipped**."* Measured against that sentence, this cycle is short.

| promised | state | reason |
|---|---|---|
| arm **B1** dialled | **delivered** | 48 cells, `bee-hive-width.md` |
| arm **B0** dialled | **ABSENT** | built and hermetically tested (`examples/arch_hive.py`); B0/B1 differ in exactly one field (`answerer`), making B0 a **control**, not a second experiment. Never dialled. |
| arm **B2** dialled | **ABSENT** | the sweep sits behind W2 and H0 in the registered stop rule; the ladder reached rung one |
| arm **P** dialled | **ABSENT** | built with all three controls (random / hand-written / no-op) and a network-less grading jail (`examples/arch_policy.py`); `t7`'s acceptance was *"dialable control arms **before** any measured dial"*, which it met. No measured dial happened. |
| B and P **against E/W/M/H** | **ABSENT** | E/W/M/H were measured in the *previous* cycle's orchestrator-worker ladder; no head-to-head between the tiers exists |
| rungs **W2**, **H0**, the **B0/B1/B2 sweep** | **ABSENT** | the stop rule returned `CONTINUE`; the cycle closed with the ladder still climbing |

**So `h1` is met on "measured, not merely designed" and unmet on "against
E/W/M/H."** The harnesses are real, tested and dialable; the comparison is not
made. Calling this shipped would be exactly the overclaim `h1` exists to
prevent.

The scheduling constraint was named in the session contract in advance —
*"not that every wave completes if the running series' schedule forbids it"* —
and it bound: W1 alone consumed 2,205 s of exclusive worker time plus setup, and
the ladder's own stop rule sequences W2 and H0 ahead of the sweep.

---

## 6. Predictions that held, and predictions that were refuted

Negatives travel alongside wins; a results document that reports only what
worked is not this contract met.

**Held:**

- `t8` predicted 3.44× lean / 1.83× realistic at width 8 → W1 measured
  3.21–3.46×.
- Streaming would retire whole-request clocks → 0.124 s max inter-chunk gap
  through a 43.45 s think, ~4,800× margin.
- Width is the axis where orchestration wins by construction → SEPARATED-WIDTH
  on 4 of 4 grains.
- Zero-cortex-call reuse is structural → 0 cortex calls, by design not luck.

**Refuted or missed:**

- **The ≤5% drone token target**: missed by 6.4×, cause identified.
- **"7 of 7 constants"**: the category is 8. The signal's own count was wrong.
- **`reasoning_content`**: vLLM documents it; this rig emits `delta.reasoning`.
  A design that trusted the docs would have streamed a null field.
- **The raw width reading**: had gate 1 not been pre-registered, W1 would have
  published width 8 as *slower* than width 1 at three of four grains.
- **My own claim that the width rung did not pin thinking**: refuted by 48 of
  48 cell records ([`corrections.md` §12](corrections.md)).

---

## 7. Corrections this cycle produced

Full text in [`corrections.md`](corrections.md); listed here so the map is
complete.

- **§10** — `worker-throughput.md` reported zero retries against records holding
  nine; its stability section is superseded. Pinned by
  `tests/test_worker_throughput_retries.py`.
- **§11** — the derived timeout that was itself below bound (amendment 1's own
  value failed amendment 1's own rule), `league_commander`'s 900 s, and the
  eighth constant nobody had listed.
- **§12** — the width-rung thinking-wire claim, refuted by the rung's own
  records. Notable for erring in the *unflattering* direction.

Two more corrections are recorded outside this file:

- A deviation id asserted in prose that had **no ledger record** — CHANGELOG
  0.10.0 and the pre-registration §18 both named a `d4` that did not exist.
  Verified against `devague deviate --list`, created late as `d5` and marked
  `needs-follow-up`; §18 deliberately left unrewritten so the drift stays
  visible.
- A `CLAUDE.md` drift entry that asserted an upstream bug which did not exist
  (see `corrections.md` §5). The rule it produced — *verify a drift entry
  against current upstream before acting on it* — was applied twice this cycle
  and caught one false claim about `k1.py`.

---

## 8. Deviations

`devague deviate --list` is the authority; reproduced for readability.

| id | task | risk | what changed |
|---|---|---|---|
| `d1` | `t13` | acceptable | governance continuity moves from wave 0 to wave 1, gaining a dependency on `t11` |
| `d2` | `t13` | acceptable | `t13` gains a second dependency, on `t12` as well as `t11` |
| `d3` | `t5` | **risky** | streaming promoted from a flag-gated pilot in one harness to the **default transport** for every cortex/worker dial, landing **before** `t10`'s measured series rather than after it |

`d3` is the one worth re-reading: it changed the transport under a series that
had not yet run. It was taken deliberately, with the operator's standing
approval, because the alternative was measuring a rung on a transport the cycle
had already established was the wrong one — and the scoped lane was explicitly
exempted so W1's clock stayed the clock its baselines were measured against.

---

## 9. Reproduce

```bash
# signal 1 — the CI bound, and the test-of-the-test that proves it can fail
uv run pytest tests/test_timeout_bounds.py -q          # 120 passed, 3 skipped
uv run pytest tests/test_rate_config.py -q

# signal 2 — the width rung
cd docs/live-test-results/bee-hive-width-raw
EMBODIMENT_LIVE_RIG=1 bash run-w1.sh    # dials the rung, commits each block
./decide.py --json                       # the registered decision rule
./render.py > ../bee-hive-width.md       # the results document
uv run pytest tests/test_bee_hive_width_preregistration.py -q   # 75 pins

# signal 3 — the drone economics
COLLEAGUE_API_KEY=… OUT_DIR=. uv run python examples/author_drone.py
EMBODIMENT_LIVE_RIG=1 EMBODIMENT_DRONES_ENABLED=1 \
EMBODIMENT_WORKER_URL=http://thor…:8000/v1 \
EMBODIMENT_WORKER_MODEL=unsloth/Qwen3.6-35B-A3B-NVFP4 \
  uv run python examples/drone_host.py index-gaps --repo .

# the harnesses that exist but were never dialled (§5)
uv run pytest tests/test_arch_hive.py tests/test_arch_policy.py -q
```

The two tables in §1 and §2 are **generated**, not typed: §1 from
`tests/test_timeout_bounds.py`'s own `evaluate()` against the committed rate
config, §2 from `bee-hive-width-raw/{cells,calls}.jsonl` plus `decide.py --json`.
A rate-config edit moves this document and the CI gate together or neither.
