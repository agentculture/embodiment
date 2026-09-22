# Realtime embodiment app — progress and handoff

The running record of executing
[`2026-09-21-realtime-embodiment-app.md`](2026-09-21-realtime-embodiment-app.md)
(the plan) against
[`../specs/2026-09-21-realtime-embodiment-app.md`](../specs/2026-09-21-realtime-embodiment-app.md)
(the spec). Written so that a fresh session — or this one after its context is
compacted — can resume without re-deriving anything. **Update it at every merge.**

Last updated: 2026-09-22 03:30, wave 2 built and verified, reviews in progress, `t4b` merged.

## State

| Phase | Tasks | State |
|-------|-------|-------|
| Planning | — | merged to `main` in #83 (0.14.1) |
| A — archive | `t1` `t2` | merged to `main` in #84 (0.15.0). Tag `archive/pre-realtime-0.14.0` = the archive commit's parent |
| B wave 1 | `t3` `t4` `t8` `t9` `t10` | **merged on `realtime/phase-b`**, integrated. Two follow-ups from the wave review: `t4b` (bounded-log performance + accounting) **merged** `c81d40b`, 1734 tests; `w1-privacy` (no exception text in a record; a private store) built, verified, awaiting its review |
| B wave 2 | `t5` `t6` `t7` `t11` `t13` | **all built and verified by the integrator**, each after a round 2 of defects found by running it; awaiting reviews, then adoption of `safe_reason`, then merge (see *Wave 2* below) |
| B wave 3 | `t12` `t14` `t16` `t17` | not started |
| B wave 4 | `t15` `t18` `t19` `t20` | not started |
| B wave 5 | `t21` live acceptance | not started — **needs the operator, at the microphone, in Hebrew** |
| B wave 6 | `t22` release docs + the final PR | not started |

`realtime/phase-b` is **local only** — nothing of phase B has been pushed. The one
final PR (human gate 3) comes after `t22`. `devague plan status` and
`devague deviate --list` are the live plan state; the approved split is
[`…-split.md`](2026-09-21-realtime-embodiment-app-split.md).

## How a task goes from brief to merge

1. `scripts/task-brief.py <tN>` renders the brief **verbatim** from the plan. The task
   agent and the reviewer get the same text.
2. A worktree: `git worktree add ../.worktrees.embodiment/realtime-<tN> -b realtime/<tN> realtime/phase-b`.
3. A task agent (model per the split file) reads
   [`scripts/task-agent-preamble.md`](../../scripts/task-agent-preamble.md), its brief,
   and task-specific forward-notes; works test-first; commits on its branch.
4. **The main agent verifies independently** — not by reading the report: run the suite
   in the worktree, then attack the module with realistic and hostile input. Probe
   scripts live in the session scratchpad.
5. `scripts/dual-review.sh <label> realtime/phase-b realtime/<tN> <brief>` — a read-only
   review in a throwaway worktree. The brief gets **mandatory integrator questions**
   appended; a review that skips them is incomplete.
6. Every finding is reproduced before it is acted on. Accepted findings go back to the
   SAME task agent (it keeps its context) as a new round: tests first, red first, a new
   commit, never an amend.
7. Merge `--no-ff` with the suite run **before and after**, a merge message recording
   rounds, accepted and rejected findings, and what carries forward. Remove the worktree
   and branch.
8. After a wave: an integration commit, then a wave review over `main...realtime/phase-b`.

Reviewers (deviations `d1`, `d3`): **Qwen Code against the `worker` role, alone.** pi
against `associate` is paused by the operator — it repeatedly spent its whole output
budget reasoning about large diffs and never wrote an answer. A `qwen27` reviewer (Qwen
Code against the dense Qwen 3.8 27B) is wired and opt-in
(`DUAL_REVIEW_REVIEWERS="qwen qwen27"`); the operator is bringing the model up and will
say when. Smoke-test it before relying on it.

**What the reviews are worth.** Every real defect in wave 1 was found by *running* the
code, not by reading it. Qwen Code pointed at most of them, often with the wrong
mechanism or the wrong severity, and produced several false findings. Treat a review as
a list of places to probe, never as a verdict in either direction.

## Wave 1 — what each module is, and what it cost

All five met every acceptance criterion in round 1. Every later round fixed a defect the
criteria could not see.

| Task | Module | Rounds | The defects underneath |
|------|--------|--------|------------------------|
| `t3` | `pyproject.toml`, `tests/test_zero_deps.py` | 1 | — `websockets>=15` base, `sounddevice>=0.5` in the `audio` extra, stdlib HTTP; extras pinned; `lobes-cli` forbidden |
| `t4` | `embodiment/daemon/state.py` | 3 | session-id path traversal; world-readable transcripts; an unfindable `mkdtemp` fallback; `status()` hiding write errors; a last-resort raise out of `__init__` |
| `t8` | `embodiment/audio/features.py` | 3 | an aliased waveform (480 Hz effective sampling); **zero frames** when fed 20 ms chunks; a 2% payload margin |
| `t9` | `embodiment/turn.py`, `tools.py` | 5 | an inert truncation detector recorded nowhere; unbound tools silent; misnamed degradations; a whitespace fallback → silence; invisible-only output → silence |
| `t10` | `embodiment/memory.py` | 5 | an abandoned recall blocking the next write for 5.9 s; `close()` silent about in-flight writes; **a prompt-fence escape through the header fields**; a ledger that evicted silently |

## Wave 2 — state at the last update

Every task met its criteria in round 1 and every one had real defects found only by
running it: `t7` discarded 78% of a streamed reply and could not stop playback (barge-in
was impossible); `t13` blocked its caller on a slow broker and leaked transcript text
through a broker error; `t11` stored memories nobody asked for and raised out of
`close()`; `t5` let a stale pidfile in the fallback dir speak for a clean state dir; `t6`
inherited a 50 s liveness bound from the transport (now 8 s, three terms). The exact
probes are in the session scratchpad (`probe_<tN>*.py`); the findings and dispositions go
into each merge message.

| Branch | Verified commit | Review | Notes |
|--------|-----------------|--------|-------|
| `w1-privacy` | `bbbf23c` (3 commits) | 27B running | merge next; then every wave-2 branch adopts `safe_reason.describe_exception` |
| `t11` | `7f31162` | queued | |
| `t13` | `225b8d6` | queued | edits `tests/test_no_silent_degradation.py` (allow-list count); expect a conflict with nothing else |
| `t7` | `e2647a4` | queued | the `AudioEndpoint` Protocol grew `stop_playback()`, `playing`, and `close()` returns `EndpointCloseReport` - `t14`/`t15` briefs must say so; `audio/__init__` re-exports collide on `SAMPLE_RATE_HZ` |
| `t5` | `807a459` | queued | `start`/`stop`/`status` verbs live; `DEFAULT_TARGET` is `embodiment.daemon.app:main` (t15) |
| `t6` | `07ecb66` | 27B queued (commit 1 reviewed by the worker) | `websockets` imported lazily in `connect()`, so `tests/test_zero_deps.py` needed no edit; the plan's "declare aec_mode=aec and language=he" is met via the connect URL (lobes reads only `tools`, `tool_choice`, `language` from `session.update`; the operator confirmed no deviation record) |

Also on `realtime/phase-b` since wave 1: `tests/conftest.py` repoints `TMPDIR` per test
(tests from three tasks had been writing into the machine's real fallback state dir);
the task-agent preamble gained two rules (never open a credential file; never touch a
machine-global path) after a `t6` agent read the gateway key from `~/.lobes/.env` for a
live dial (the value was found nowhere afterwards).

**Reviewer, by the operator's word (no deviation record):** the 27B `cortex` alone,
`DUAL_REVIEW_REVIEWERS` default `qwen27`, now with a `worker` subagent it can delegate
to (`~/.qwen/agents/worker.md`). On the one diff both read, the 27B found every worker
finding plus three more, all reproduced. **Strictly one review at a time**: six at once
starved the rig and all timed out empty.

**Merge order:** `w1-privacy` -> each wave-2 branch merges `realtime/phase-b` in, adopts
`describe_exception` via its own agent, re-runs its probe -> `t13`, `t11`, `t5`, `t6`,
`t7` -> wave-2 integration commit (`embodiment/__init__._SUBMODULES` += `bus`, `session`,
`realtime`; `audio/__init__` re-exports; `CLAUDE.md` code map and the "no verb starts
anything" sentence, now false) -> wave review. Cleanup owed: `/tmp/embodiment-state-1000`
and `/tmp/embodiment-state-fallback-*` are test debris (confirm no real daemon first).

## Carried forward — obligations later tasks inherit

| To | What |
|----|------|
| `t5` | Single-instance lock (two daemons sharing a state dir sweep each other's temp files). `status`/`stop` search `candidate_state_dirs()`. Bounded stop: unfinished ids into the ledger, THEN hard-exit — non-daemon worker threads hold the interpreter open up to 10 s |
| `t6` | First module-scope `websockets` import: `tests/test_zero_deps.py` needs `websockets` in `_REQUIRED_RUNTIME_IMPORTS`, owned by `embodiment.realtime.client`. **The main agent applies that at merge and shows the operator** — it is the dependency gate |
| `t15` | Wire each log's `on_degrade` into the ledger (`DaemonState` builds its logs without one, so `state-record-exceeds-bound` is not durable); surface `TranscriptLog.status()` counters and `RoomMemory.store_permission_failures` / `store_symlinks_skipped`; `daemon/state.py` still builds ledger details from `str(exc)` (paths, not speech) - move to `safe_reason`. `files` memory backend ONLY (own-only recall holds through the `EIDETIC_DATA_DIR` pin, not for mongo/neo4j). Set a low `EIDETIC_EMBED_TIMEOUT` at start. On stop, write `CloseReport.unconfirmed` ids to the crash ledger before any hard exit. One `FeatureExtractor` per audio direction, never shared across threads. Dedupe `turn-truncation-undetectable` (first + count). Stamp a `source` on every folded degradation. Bind tools LAST (`bind_tools` marker is lost if the seam is wrapped afterwards) |
| `t16` | `transcript`/`reply` events carry speech: authenticated subscribers only. Loopback is not authentication (spec `c34`) |
| `t18` | Label `zero_crossing_hz` as a zero-crossing rate, not pitch. The envelope is base64 int8: 16 mins then 16 maxes; decode with `atob` → `Int8Array` |
| `t21` | Does the rig's seam report `completion_tokens`? (If not, every turn carries `turn-truncation-undetectable`.) Does `is_speakable` match what the synthesiser voices? Is the hardware echo cancellation real — does Gwen ever transcribe herself? |

## Operator decisions on record (do not re-ask)

Daemon-run turn, lobes socket ears-only. Host mic + speaker, hot on `start`, hardware AEC
(`aec_mode=aec`). Hebrew. Gwen; the speaker is the `senses` role (Gemma 4 26B A4B). Tools
and agent triggers later, so the turn sits on `loop.py` with an empty registry. Remote:
the daemon's own dashboard at `agent.culture.dev` via `cultureflare remote-login`,
Cloudflare Access SSO; control + text only, no phone voice. MQTT is the internal event
substrate; SSE is its projection. Dashboard like `culture-nodes` (React + Vite + TS), a
live assistant waveform as the v1 centrepiece, a speech-driven face later. Retention: no
raw audio on disk, private bounded transcript logs. Remembering: an explicit spoken ask
plus one session-end summary. A Reachy Mini relay with a secret, later. Dependencies
approved (`d2`). pi reviews paused (`d3`).

**Never without an explicit go-ahead:** `cultureflare … --apply`; posting to a sibling
repo (the colleague#358 comment and the ~50 moot-issue closures are drafted in
[`../archive/2026-09-21-external-pointers.md`](../archive/2026-09-21-external-pointers.md),
unposted); pushing `realtime/phase-b`.

## Tooling gotchas already paid for

- `version-bump/scripts/bump.py` reads stdin: run it alone with `</dev/null`.
- Never edit a bash script while an instance is running; `dual-review.sh` runs from
  `main()` for that reason.
- `git merge` takes its message from `-F <file>`, not from stdin.
- `devague interrogate <cN> --honesty … --instruction …` puts the instruction on the
  CLAIM and un-confirms it.
- `pkill -f` with a plain pattern matches its own command line: use `"[x]yz"`.
