# Pre-registration — tool-session latency against the drive tail (plan task t16)

**Written and committed BEFORE the first measured dial of this experiment.**
Nothing in this file was chosen after seeing a measured number, and the git
history is the evidence: this document and the literal constants it quotes land
in their own commit (`tests/test_muse_latency_preregistration.py`), and the
probe that produces the numbers (`examples/muse_latency.py`) lands after it.

## Why this experiment exists — lapse `l1`

Claim `c29` fixes a measurable finish line:

> the close-time late drop goes from exactly one per run (4 of 4 runs, 25% of
> counsel) to zero per run across a live series of at least four drives

That target was set from **tools-off** latency baselines, in the same frame
that decided the muse should hold tools. Claim `c31` names the interaction:

> pad-wielding muse sessions grew 5.5s to 30.4s across five turns in the issue
> 21 probe (about 6x tools-off), so with tools on, the final in-flight session
> rarely completes before synthesis and the drain recovers nothing — the
> zero-late-drop target must be evaluated under the shipped tool-use latency
> profile, not the tools-off one

and `c31`'s honesty condition requires exactly that. The frame records the gap
as lapse `l1` (`assumption-for-measurement`): *"the c29 zero-late-drop target
was fixed from tools-off latency baselines … the target predates any
measurement of tool-session completion odds."* This experiment closes it.

The failure mode being guarded against is specific: under deviation `d1` the
actor **never waits** on the muse, so a session that takes longer than the
drive's remaining tail does not land, and the terminal drain recovers nothing.
A zero-late-drop target could therefore be unreachable *in principle* once
tools are on, and tuning would be chasing a number that cannot move.

## The structural fact that shapes the design, stated before it is measured

**At most one insight per drive can be dropped late.** The runner holds one
work item and runs one session at a time (`ThreadedMuseRunner._offer` /
`_take`), and since task `t4` the terminal beat *drains without considering*
(`presence_engine.on_terminal_boundary`, `consider=False`), so the only session
that can strand is the **last one started before the terminal beat**. This is
what the baseline's "exactly one per run, 4 of 4 runs" already reports, and it
is why the decision rule below is written about *the last session*, not about a
random one.

## What is measured, in two lanes

### Lane 1 — the session-latency distribution

Four cells, each a real `MuseLoop` session against the real muse endpoint
through the lobes proxy. The tools-on cells use `MusePad.bench(...)`, the
shipped tool seam, with `embodiment.scratchpad`'s own schemas.

| cell | tools | host framing | `max_turns` | `max_tool_rounds` | what it is |
|---|---|---|---|---|---|
| `off-2` | off | none | 2 | — | the baseline series' own config (`proof.py`'s `MUSE_MAX_TURNS`); ties this lane to lane 2 |
| `off-4` | off | none | 4 | 3 | the degrade floor at the package-default budget |
| `pad-4` | pad | `MUSE_PAD_PROTOCOL` | 4 | 3 | **the shipped tools-on configuration** task `t20` would flip to |
| `primed-4` | pad | `MUSE_PAD_PROTOCOL` + `PRIMING` | 4 | 3 | **an upper bound**, not a shipped configuration — see below |

`primed-4` exists because the question `c31` asks is *"how long does a session
take when the muse actually uses its pad"*, and a muse that declines to call a
tool answers a different question. It appends one pre-registered sentence
(`PRIMING`) to the host framing, through `MuseLoop(system=…)` — the existing
host-framing seam, no package change. It is declared here as an
**upper-bound arm** and must never be reported as the shipped profile.

`tool_choice` is **not** used to force tool calls. It was checked against this
rig before this document was written and does not work: with
`tool_choice: "required"` the gateway returned `finish_reason: "tool_calls"`
and a `null` tool-call list, and a named-function `tool_choice` returned
ordinary content. That check is instrument verification, is recorded here so it
is not rediscovered, and is **not a measured cell**.

Each cell runs `N_SEQUENCES` simulated drives × `BOUNDARY_STEPS` positions,
against one fixed committed transcript, so the same boundary text reaches every
cell. The pad is fresh per sequence and persists across that sequence's
boundaries, as it would inside one drive.

Per session the probe records: wall-clock seconds, **time to first insight**,
model turns, tool rounds, completion tokens, exit reason, every degradation
code, and the pad counts.

**Time to first insight (`TTFI`) is the dependent variable that matters**, not
total session wall clock. An insight reaches the runner's buffer through the
sink the moment its turn is folded (`embodiment.muse._advance_turn` →
`_emit`), so a long session whose first insight lands early still delivers.
Total wall clock is reported beside it as the cost.

### Lane 2 — the drive tail

`N_DRIVES` live drives of the `proof.py` harness (problem `factorial`,
`--identity Gwen`, `max_steps` 14, `MuseControls(max_turns=2)`), reusing that
module's `PROBLEM`, `TOOLS`, `ProofBench`, `gateway` and `GuidanceRelay`
**imported unchanged**, so the instrument is the one that produced the baseline
rather than a new one.

The probe wraps two things, both through public seams:

- the muse completion, to timestamp every model call and to detect a session's
  opening call (`len(messages) == 2` — `embodiment.muse._build_messages`
  returns exactly the system and boundary messages);
- the presence sink, with a forwarding proxy that timestamps
  `on_progress_boundary` and `on_terminal_boundary`. The loop duck-types the
  sink (`loop._presence_terminal` probes by name), so no package change is
  needed.

**The tail** is `t(terminal beat) − t(last session start before it)`. That is
the window the last session actually had.

## What this design assumes, named rather than buried

**The tail is actor-determined, so a tools-off drive measures it.** Deviation
`d1` says the actor never waits on the muse, and on this rig only the cortex is
local — the muse is proxied — so muse work does not contend for the actor's
GPU. The pad executes in-process and is a list append. The tail measured on a
tools-off drive is therefore taken as the tail a tools-on drive would have.

This is an assumption, it is the load-bearing one, and it is the reason the
completion odds below are an **estimate combining two measured distributions**
rather than a directly observed rate. It is declared here, before the dial, and
must be repeated in the results document. It is also why lane 2 doubles as a
grader self-check: for the tools-off profile the estimator's prediction is
compared against the drives' own observed late drops (`DV-CHECK`).

**Why a tools-on drive is not run instead**, stated prominently rather than
implied: `ThreadedMuseRunner.__init__` builds its `MuseLoop` with no `tools=`
argument (`muse_runner.py`, and its own docstring calls the injected seam "the
**tools-off** thinking seam"). There is no supported path for a tool bench to
reach the muse inside a live drive. Task `t16` may not change
`embodiment/`, so **the tools-on-in-drive lane is absent, and is reported as
absent, not implied.**

## The estimator

For a cell with sessions `i` and drives `j`:

```text
P(cell) = |{ (i, j) : TTFI_i <= tail_j }| / (n_sessions * n_drives)
```

Two are reported:

- **`P_all`** — every session in the cell. Optimistic by construction: it
  includes early-boundary sessions with small contexts, and the session at risk
  is the last one.
- **`P_late`** — the subset at the two largest `BOUNDARY_STEPS`. Structurally
  the right match, at a smaller n.

**`KEEP` requires both.** A conjunctive rule means the smaller-n subset can
only ever make the verdict stricter, never manufacture a pass.

## Literal thresholds — all pinned by value in the test

| constant | value | reasoning |
|---|---|---|
| `MIN_DRIVES` | **4** | the acceptance criterion's own floor: a series under four drives reports `INCONCLUSIVE` |
| `MIN_SESSIONS_PER_ARM` | **6** | the acceptance criterion's own floor: an arm under n=6 reports `INCONCLUSIVE` |
| `N_DRIVES` | **4** | what this probe runs |
| `N_SESSIONS_PER_ARM` | **8** | `N_SEQUENCES` (2) × `BOUNDARY_STEPS` (4), two above the floor |
| `KEEP_THRESHOLD` | **0.95** | derived, not chosen — see below |
| `SERIES_CONFIDENCE` | **0.8** | the probability a *reachable* target should have of being met when the mechanism works |
| `MAX_ERROR_FRACTION` | **1/3** | reused verbatim from `association-work-preregistration.md`'s `V2` and `devague-legs-preregistration.md`; the same reasoning holds regardless of domain |
| `MIN_TOOL_EXERCISE_FRACTION` | **0.5** | an arm labelled tools-on must actually put tool calls on the wire in at least half its sessions, or the label is a claim rather than a condition |

**`KEEP_THRESHOLD` is derived from `SERIES_CONFIDENCE` and `MIN_DRIVES`.** At
most one session per drive can strand (see the structural fact above), so a
zero-late-drop series across four drives needs the last session of each of four
drives to land. With per-drive completion probability `p`, that series succeeds
with probability `p ** MIN_DRIVES`. Requiring `p ** 4 >= 0.8` gives
`p >= 0.8 ** 0.25 = 0.9457…`, rounded up to **0.95**. A target that only
succeeds half the time when the fix is working is not a target; it is a coin.

## The decision rule and the `INCONCLUSIVE` condition

**Validity gates — the verdict is `INCONCLUSIVE` unless all hold:**

| id | condition |
|---|---|
| V1 | at least `MIN_DRIVES` drives completed with a measurable tail (a terminal beat *and* at least one muse session started before it) |
| V2 | the governing arm has at least `MIN_SESSIONS_PER_ARM` sessions that returned an outcome |
| V3 | the governing arm exercised tools in at least `MIN_TOOL_EXERCISE_FRACTION` of its sessions, if its label claims tools |
| V4 | the governing arm's degraded-session fraction does not exceed `MAX_ERROR_FRACTION` |

**The governing arm** is `pad-4` — the shipped configuration — **if it passes
V3**. If `pad-4` fails V3, that failure is itself a headline finding (the
shipped tools-on muse does not use its pad), it is reported as such, and
`primed-4` becomes the governing arm for the completion-odds question, labelled
as the upper bound it is.

**Verdict, applied only once V1–V4 hold for the governing arm:**

- **`KEEP`** — `P_all >= KEEP_THRESHOLD` **and** `P_late >= KEEP_THRESHOLD`.
  The zero-late-drop target stands as written; `c29` is unchanged.
- **`REPLACE`** — otherwise. The original target is stated plainly as
  **unreachable under the measured profile**, and the replacement is computed
  by the formula below rather than chosen after the fact.
- **`INCONCLUSIVE`** — any validity gate fails. Published as a result.

**The replacement target, computed not chosen.** Let `P` be the governing arm's
`P_late` (the conservative of the two). The replacement is:

```text
late drops per run <= R,  R = ceil_2(1 - P)
```

where `ceil_2` rounds up to two decimals, reported together with `P`, the two
n values, and the arms it came from. `R` is an expectation over a series, not a
per-run integer: at most one late drop per run is structurally possible, so
`R = 0.25` means "at most one late drop across a four-drive series."

`DECISIONS = ("KEEP", "REPLACE", "INCONCLUSIVE")`, pinned by value.

## `DV-CHECK` — the grader self-check

The estimator is a model, and a model that has never been checked against an
observation is an assumption with arithmetic on it. So: apply the estimator to
the `off-2` cell (the same configuration lane 2's drives run) and compare its
prediction — expected late drops across the series, `N_DRIVES * (1 - P_all)` —
against the drives' **own observed** `insights_dropped_late` total.

This runs once, is reported whatever it says, and **is not itself a measured
cell**. A disagreement is published as a defect in the estimator, in the
`corrections.md` tradition, not smoothed over. It does not gate the verdict:
the drives are n=4 and their observed count is itself a small sample.

## Rules fixed in advance

1. **A degraded or failed call is data.** A degraded session, a harness error
   or an aborted drive is counted and reported with its degradation. It is
   never silently retried for a better number.
2. **Nothing is re-run to get a better number.** Each cell runs once at the
   pre-registered configuration.
3. **The result is recorded either way.** `KEEP`, `REPLACE` and `INCONCLUSIVE`
   are all publishable; none is a prompt to re-run.
4. **Every raw session and drive record is committed** as JSONL beside the
   results document, so the fold can be recomputed from the raw data.
5. **An absent lane is reported as absent.** The tools-on-in-drive lane is
   already known to be absent (there is no seam for it); it is named in the
   results document's own summary, not left to a reader to notice.
6. **The instrument check is not a measured cell.** The `tool_choice`
   probe recorded above, and any smoke run of the probe, are declared and
   excluded from every reported n.

## What this pre-registration cannot show

- **Anything about model latency in general.** One rig, one muse model, one
  proxy, one cortex. A number here is about this deployment.
- **Whether the muse's counsel is any good.** This measures when counsel
  arrives, never whether it was worth arriving. That is tasks `t17`/`t18`.
- **A directly observed tools-on-in-drive late-drop rate.** The seam does not
  exist; the odds are an estimate from two measured distributions under the
  stated assumption, and no wording in the results may imply otherwise.
- **Generalisation past this gateway build.** `tool_choice` behaviour, and the
  muse's willingness to call a tool at all, are properties of the deployed
  model and template as much as of the design.

## Reproducing

```bash
export COLLEAGUE_API_KEY=...

uv run python examples/muse_latency.py --lane sessions \
    --out docs/live-test-results/muse-latency-sessions.jsonl
uv run python examples/muse_latency.py --lane drives \
    --out docs/live-test-results/muse-latency-drives.jsonl
uv run python examples/muse_latency.py --analyse \
    --sessions docs/live-test-results/muse-latency-sessions.jsonl \
    --drives docs/live-test-results/muse-latency-drives.jsonl
```
