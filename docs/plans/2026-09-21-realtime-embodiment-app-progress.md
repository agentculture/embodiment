# Realtime embodiment app — progress and handoff

The running record of executing
[`2026-09-21-realtime-embodiment-app.md`](2026-09-21-realtime-embodiment-app.md)
(the plan) against
[`../specs/2026-09-21-realtime-embodiment-app.md`](../specs/2026-09-21-realtime-embodiment-app.md)
(the spec). Written so that a fresh session — or this one after its context is
compacted — can resume without re-deriving anything. **Update it at every merge.**

Last updated: 2026-09-22 08:00, `t5` and `t6` merged, `t7` rebuilt on subprocess audio (d4) and
under review, every wave-3/4 branch built and verified, the daemon's first live turn done.

## State

| Phase | Tasks | State |
|-------|-------|-------|
| Planning | — | merged to `main` in #83 (0.14.1) |
| A — archive | `t1` `t2` | merged to `main` in #84 (0.15.0). Tag `archive/pre-realtime-0.14.0` = the archive commit's parent |
| B wave 1 | `t3` `t4` `t8` `t9` `t10` | **merged on `realtime/phase-b`**, integrated. Two follow-ups from the wave review: `t4b` (bounded-log performance + accounting) **merged** `c81d40b`, 1734 tests; `w1-privacy` (no exception text in a record; a private store) built, verified, awaiting its review |
| B wave 2 | `t5` `t6` `t7` `t11` `t13` | `t11` `9d25555`, `w1-privacy` `eac823f`, `t13` `59467ea`, `t5` `95e7f92`, `t6` `722056e` **merged**; `t7` rebuilt on subprocess audio (deviation `d4`), device-verified, under its second review; `t7` `3540a57` **merged** (10 rounds, 3 reviews; the last defect a first-poll race found on the device) |
| B wave 3 | `t12` `t14` `t16` `t17` | `t16` `4517365`, `t14` `d6fca10`, `t17` `371065a` + `t17b` `be1d2a0`, `t12` `5355db6` **merged on `realtime/phase-b`** (each rebased off its throwaway base, reviewed, probed) |
| B wave 4 | `t15` `t18` `t19` `t20` | `t20` `ed9d190`, `t19` `b58bd91`, `t18` `302db2f`, **`t15` `6aab47f`** merged on `realtime/phase-b` (t15 cherry-picked, 16 commits, live-driven under `grant run` before the merge) |
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
| `w1-privacy` | **merged** `eac823f` (`027cb1c`, 4 rounds) | 27B alone timed out at 40 min; with worker delegation 39 min, changes-requested: 3 MAJORs reproduced and fixed (symlinked store root followed; `safe_detail` free text; continuity reasons trusted as literals) | `describe_exception(declared_codes=)` replaces `allow_detail`; tools declare fault codes at registration; suite 1804 -> 1963 |
| `t11` | **merged** `9d25555` (`b8d4b2a`, 3 rounds) | 27B, 34 min, changes-requested: 3 findings reproduced and fixed, 1 rejected | already class-name-only, so it needed no `describe_exception` adoption and merged ahead of `w1-privacy`; suite 1734 -> 1804 |
| `t13` | **merged** `59467ea` (`a5bb8e5`, 4 rounds) | 27B + worker, 49 min, changes-requested: 3 behavioural findings reproduced and fixed, 2 test gaps | suite 1963 -> 2091 |
| `t7` | `840b751` (6 rounds) | first review (50 min, old prompt): 3 MAJORs, 2 reproduced; then the real reSpeaker showed the sounddevice build never played (`OutputStream.write(bytes)` is the numpy API), defaulted output to HDMI (no AEC far-end reference) and needed a 2:3 resampler for a 16 kHz-only device. **Deviation `d4`** (operator): rebuilt on `pw-record`/`pw-play` (`arecord`/`aplay` fallback) as lobes' accept script and shabbos-goy run this rig; `sounddevice` extra withdrawn. Second review running | device-verified by the integrator's probe: capture native 16 kHz channel 1, tone written 24000 samples, barge-in stop 1 ms with 52320 discarded (was 1.3 s: the writer had pushed everything into the pipe; now paced 20 ms slices, 100 ms lead, SIGKILL on stop), mute -> 0 frames, close 0.10 s, no leftover process. Protocol grew `stop_playback()`, `playing`, `sample_rate`, `EndpointCloseReport` |
| `t5` | **merged** `95e7f92` (`c62b32b`, 4 rounds) | 27B delegate prompt, 55 min on a 150 KB diff, approve + 1 MAJOR fixed: a pid-reuse window in `stop()`; identity is now (pid, `/proc` start time) re-checked before each signal, since `pidfd` is unavailable on this interpreter | three tests had begun spawning the real daemon once `t15` existed; an autouse guard now fails any test that would. Suite 2091 -> 2208 |
| `t6` | **merged** `722056e` (`f3318d4`, 3 rounds) | 27B, 26 min (first delegate run under the baseline), approve + 1 MINOR fixed (`graceful` derived from `deadline_exceeded` and `close_error`) plus a worker finding the lead had dropped unread: the liveness test could not fail; now bounded from below by the configured terms and the third term proved by moving it | live dial passes under the grant-injected key and skips, named, without one; measured on the rig: `session.created` 30 ms, end-of-speech -> transcript median 161 ms / p90 209 ms, 0 dropped frames. Suite 2208 -> 2362 |

Also on `realtime/phase-b` since wave 1: `tests/conftest.py` repoints `TMPDIR` per test
(tests from three tasks had been writing into the machine's real fallback state dir);
the task-agent preamble gained two rules (never open a credential file; never touch a
machine-global path) after a `t6` agent read the gateway key from `~/.lobes/.env` for a
live dial (the value was found nowhere afterwards).

**Reviewer, by the operator's word (no deviation record):** the 27B `cortex` alone,
`DUAL_REVIEW_REVIEWERS` default `qwen27`, with a `worker` subagent it can delegate to
(`~/.qwen/agents/worker.md`; plan mode refused the `agent` tool until
`--allowed-tools=agent` was added, and the prompt must then go through `-p`). Wall
times so far: `t4b` 40 min (64 kB), `t11` 34 min unaided (75 kB), `w1-privacy` >40 min
unaided (116 kB, timed out). On the one diff both read, the 27B found every worker
finding plus three more, all reproduced. **Strictly one review at a time**: six at once
starved the rig and all timed out empty.

**Merge order:** `t11`, `w1-privacy` (done) -> each remaining wave-2 branch merges
`realtime/phase-b` in, adopts `describe_exception` via its own agent, re-runs its probe
-> `t13`, `t5`, `t6`, `t7` -> wave 3 (`t12`, `t16`, `t14`, `t17`, built on a throwaway
pre-integration base of the verified wave-2 branches, see embodiment#85) -> wave-2 integration commit (`embodiment/__init__._SUBMODULES` += `bus`, `session`,
`realtime`; `audio/__init__` re-exports; `CLAUDE.md` code map and the "no verb starts
anything" sentence, now false) -> wave review. Cleanup owed: `/tmp/embodiment-state-1000`
and `/tmp/embodiment-state-fallback-*` are test debris (confirm no real daemon first).

## Waves 3 and 4 — built on a throwaway base before wave 2 finished reviewing

Integrator decision (embodiment#85): wave-3 and wave-4 branches start from
`realtime/wave2-preint` / `realtime/wave3-preint`, throwaway merges of the verified
wave-2 (then wave-3) heads, so the build does not idle behind the serial review lane.
They merge into `phase-b` after the wave-2 branches, each merging `phase-b` in first. The
bases are never merged and are deleted at the end.

| Branch | Head | Verified how | Review |
|--------|------|--------------|--------|
| `t12` voice | `6f94519` (2 rounds) | probe against a HostEndpoint-shaped fake: barge-in after `speak()` returned, paced feature trace within 2% of wall clock, no `voice` key sent to the gateway | queued |
| `t16` http | `cc82e43` (3 rounds) | served t17's real build with a real `Bus`; the dashboard connects through the guard in Chrome (cookie vouched for by `Sec-Fetch-Site: same-origin`; Chrome sends no `Origin` on a same-origin EventSource); the installed wheel resolves `embodiment/web/dist` | queued |
| `t14` remote endpoint | `11e2832` (2 rounds) | real websockets client: six refusal paths close 1008 before any frame; first-message auth, the secret never on the URL (a correction of the brief) | queued |
| `t17` web app | `3b9e621` (3 rounds) | built and opened in Chrome against a fixture SSE server and then the real t16 server; envelope unwrapped, honest recall default, install-secret cookie, Hebrew `dir="auto"` | queued |
| `t20` tunnel verb | `1849b7e` (2 rounds) | ran the verb: dry-run only, `--apply` refused, `--tunnel-name` in both commands | queued |
| `t19` packaging/CI | `075a31a` | `uv build` rebuilt `web/dist` through the hook, 8 files in the wheel, sdist clean, installed in a scratch venv; web build 3.4 s with a warm npm cache (cold registry unmeasured) | queued |
| `t18` oscilloscope | `e934a16` (2 rounds) | Chrome: min/max envelope band, readouts by presence, hi-DPI, token colours; lobes site scripts cited verbatim at pin `d2690a5`; no browser-ear UI in v1 (operator: phone is control + text) | queued |
| `t15` daemon | `19fe95a` (5 rounds + the `t7`/`t14`/`phase-b` merges) | **live on the rig under `grant run`**: ears session in < 5 s declared at the host ear's 16 kHz, household speech ran full turns (transcript -> recall -> `senses` -> spoken reply through the array), empty VAD commits counted not faulted, stop 0.25 s with a live session, ledger and `daemon.err` clean, `identity_verified: true`. Two real races fixed on the way (a stop during the handshake was never delivered; a close scheduled onto a stopped loop burned its slice) | queued, last |

`t12` later grew `drain_features(max_n)` and `set_endpoint()` for the daemon (`bf35d8b`);
`t14` grew `RemoteEndpoint.sample_rate = 24000` (`23d0d63`); `t16` `cc82e43`, `t17`
`3b9e621`, `t18` `e934a16`, `t19` `075a31a`, `t20` `1849b7e` are unchanged.

Reviewer harness since `9477de6`: the worker drafts the whole review and the 27B verifies
only the cited lines. Before that, the 27B's wall time was its own re-reading at 80-120K
tokens of context: t4b 40 min, t11 34 (unaided), t13 49 (one worker), w1-privacy-r2 39
(two workers); unaided it did not finish a 113 kB diff in 40. With delegation: t5 55 min
(150 kB; the lead's 40K thinking tokens at ~20 tok/s became the cost) and **t6r2 26 min**.
Measured effect on findings (t6r2): the worker drafted 7, the lead kept 1; five drops were
right, one was a real finding dropped because the worker cited the wrong file. `f67c570`
makes the worker cite working-tree lines; `ec5914c` makes the lead relocate a mis-cited
finding by symbol before judging and report what it could not read as *unverified*.
`scripts/dual-review.sh` is a kept deliverable (operator).

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

Every decision below, every deviation, and every integrator call is mirrored on
[embodiment#85](https://github.com/agentculture/embodiment/issues/85), the one
cumulative issue the operator reviews; append there when a new one is made.

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

Added 2026-09-22 (numbered as on #85):

- **`d4` — host audio through subprocesses.** `pw-record`/`pw-play`, `arecord`/`aplay` on
  `plughw` as fallback, the shape `lobes-cli/scripts/realtime-he-accept.py` and
  `shabbos-goy` run this array; `sounddevice` withdrawn from `d2`. What an in-process
  backend would add is embodiment#86, reopenable only on a measured gap after `t21`.
- **The rig's audio, measured:** Seeed reSpeaker XVF3800 (`hw:CARD=Array`, stable id
  `usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_114993702263100642`), 2 ch, 16 kHz
  only, the speaker on the same USB device; PortAudio's default output was the HDMI. One
  device for both directions (the array's AEC needs its own output as far-end reference).
  `../microphone-cli` reads the firmware: `microphone array aec get <id> --json` ->
  converged, echo on, bypass off. `t21` checks that as a precondition.
- **13 — the gateway key comes from `grant`**, never a shell export or a file:
  `grant run --inject EMBODIMENT_GATEWAY_KEY=LOBES_GATEWAY_API_KEY -- embodiment start`.
  No third env fallback. `grant` finds its store via `HOME`, so scratch environments go on
  the child, not on the `grant` call.
- **14 — empty VAD commits are background noise, not faults.** The threshold is lobes'
  (`_segmenter.py`; `session.update` acts only on `tools`/`tool_choice`/`language`). The
  daemon counts them (`status()["transcripts"].empty_commits`); `t21` publishes the
  ambient RMS and the empty-commit rate. A high rate becomes an ask to lobes.
- **15 — the ears send 16 kHz natively** (`input_sample_rate=16000`, no client
  resampling), from shabbos-goy's validated shape; channel 1 kept from lobes' duplex
  evidence until a mono-vs-channel-1 A/B with playback on. The endpoint protocol carries
  `sample_rate`; the daemon dials the session at the active ear's rate and re-dials on a
  handover to another rate.
- The operator validated the lobes realtime dashboard and speech-to-speech on the Hebrew
  endpoint on this rig: the gateway lane is the known-good baseline, `t21` measures only
  this package's side. `scripts/dual-review.sh` stays in the repo.

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

## Added 2026-09-22 (integration commit)

- **Merged into `phase-b` today:** `t7` `3540a57`, `t16` `4517365`, `t14` `d6fca10`, `t17`
  `371065a`; deviation `d5` recorded (`0f41683`, proposed). Waves 3–4 branches carried the
  throwaway `wave2-preint`/`wave3-preint` history, so each was **rebased onto `phase-b`**
  (`git rebase --onto realtime/phase-b <preint-head>`) before its merge; the preint branches
  are deleted once the last review that uses them as a base has run.
- **Integration:** `_SUBMODULES` and the type stub now name `bus`, `http`, `realtime`,
  `safe_reason`, `session`; `audio/host.py` closes a dead child's pipes (the capture child's
  stdout and both children's stderr were never closed - eight `ResourceWarning`s per suite
  run and a slow fd leak on a daemon that redials); `CLAUDE.md`'s honest status and code map
  say what is merged and what still lives on a branch.
- **Live today** (details on #85 c12–c13 and in the merge messages): the unattended acoustic
  self-test (gateway TTS through the monitor into the array) passed hear/answer/barge-in/
  remember/summary by counters; recall after a restart failed because eidetic's keyword
  tokeniser is `[a-z0-9]+` (every Hebrew recall returned nothing with `ok=True`) - `t15`
  round 7 works around it (`d5`) and the reply carries the fact; the dashboard over
  Tailscale exposed the cookie/secure-context trap (`t17` round 4: fetch-streamed SSE with
  the secret in the Authorization header) and dead controls for a late viewer (`t17` round
  5 in progress); `t20`'s printed commands were unquoted (round 3); `t14`'s aborted
  handshake bricked the endpoint (rounds 4–6).
- **Review wall times** (delegate prompt, vs the 40-min baseline): t7r3 26, t16 17, t14 20,
  t17 28, t20 19; t12's review hung on a provider retry and is rerun after the chain. The
  worker's test-file citations were wrong in every review; the lead relocated by symbol.

- **16:55 — waves 3–4 complete on `phase-b`** (`6aab47f`, suite 3085, vitest 355): `t12` `5355db6` (rerun review: bounded TTS read, redirects refused, spoken/unspoken state event, stall tolerance from the speech deadline), `t15` `6aab47f` (review approve with no findings; answering its seven questions found a mute lost across a handover and two unbounded waits, all closed; live drive by counters before the merge). All throwaway pre-integration branches deleted. Next: the wave review of the whole diff, `t21`, `t22`.
