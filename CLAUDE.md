# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`embodiment` is the **loop + presence layer** of the AgentCulture stack, being
rebuilt into **Gwen: a background realtime voice app** on top of the `lobes`
gateway. It is a Python package with a small tested core, a CLI, and — once the
plan is built — a daemon, a dashboard and a voice.

### Honest status — read this before you plan work

**The realtime app is built on the branch `realtime/phase-b` and runs.** Every
task of waves 1–4 is merged there, each after its own review and a probe on the
device or in a browser; the daemon (`daemon/app.py`, t15) has run live on the
rig from this tree — Hebrew turns with the operator through the reSpeaker,
memory stored and recalled across a restart, barge-in, the dashboard reviewed
on a phone over Tailscale — and `embodiment start` on this branch launches it.
What `main` holds is still the small core and the introspection verbs until
the PR lands. Not yet done: the wave review of the whole diff, `t21` (the human
acceptance run, latency published as measured) and `t22` (release docs). The
state table in `docs/plans/…-progress.md` is the live truth; decisions live on
embodiment#85.

- **Spec:** `docs/specs/2026-09-21-realtime-embodiment-app.md` — 50 claims, 33
  honesty conditions, after a rigorous `/challenge` pass.
- **Plan:** `docs/plans/2026-09-21-realtime-embodiment-app.md` — 22 tasks in 7
  waves, 26 obligations, 9 risks. The approved implementation split sits beside
  it (`…-split.md`). `devague plan status` is the live state.
- **The archive:** the strategist tier, the configuration lane, the muse,
  drones, `examples/` and every live-test result were removed in one reviewed
  PR. The tag **`archive/pre-realtime-0.14.0`** is that commit's parent — the
  last tree that contains them. Restore a file with
  `git show archive/pre-realtime-0.14.0:<path>`; cite it, don't resurrect it
  wholesale. The earlier specs, plans and delivery records stay in `docs/` and
  `.devague/` as history.

**Why it was archived**, in the old package's own words rather than this
file's: three cycles running, the published verdict was *mechanism proven,
value not*. ScopeBench Stage 1 returned `INCONCLUSIVE`; the one matched live
control had the governed arm spend 189.6 s and 3663 strategist tokens to apply
**zero** directives for a materially identical answer; the configuration lane
shipped with its value unmeasured; the muse measured a 1.2% intervention rate
for 2.4–4.4× the token cost. Every tier shipped opt-in and off, nothing ran as
an app, and no sibling repo imported the package.

**The redesign makes no value claim either.** It is accepted on one rig, its
usefulness is unmeasured, and the plan's acceptance task (`t21`) publishes
latency as measured, including if it is bad.

### Three lessons the archived cycles paid for

They outlive the code, so they stay here.

1. **A clock sized against the wrong quantity silently becomes the
   measurement.** A 300 s request timeout censored a completion-length
   distribution; a 600 s gateway read timeout — in a process no client value
   could reach — cost a live series two repetitions. Derive every clock from
   the quantity it bounds. This bears directly on a voice loop: a recall
   deadline, a TTS timeout and a barge-in bound are all clocks.
2. **A mechanism that holds on every structural check can still buy nothing.**
   Structural proof and measured value are different claims. Publish the second
   one when it comes back negative.
3. **A tier can be correctly wired, fully armed, and still do nothing — with
   every counter at zero and no error anywhere.** `armed == True` is not
   evidence that something is alive; a counter that increments is. For the
   daemon: a healthy-looking `status` is not evidence that Gwen heard anything.

## Where embodiment sits

| Layer | Package | Owns |
|-------|---------|------|
| Surface | `agentfront` | "Agent First Interface" — CLI / MCP / HTTP / TAUI fronts |
| Tools | `shell-cli` | The file-and-shell tool surface + approval policy |
| **Loop + presence** | **`embodiment`** | **Perceive → decide → act, and the presence between acts** |
| Models, ears, voice | `lobes-cli` | The gateway, role resolution, `/v1/realtime`, STT and TTS |
| Memory / trust | `eidetic-cli`, `coherence-cli` | Durable recall + provenance; consistency signals |
| Product | `colleague` | The coder-agent harness |

`lobes-cli` is reached **over the network only** — never imported. It ships no
Python client; embodiment writes its own against the documented wire
(`lobes/realtime/_wire.py`, `_session.py`, `docs/realtime-pipeline.md` in that
repo).

`colleague` was to be the first consumer of the extracted loop, proposed in
[colleague#358](https://github.com/agentculture/colleague/issues/358). That
proposal predates the redesign; whether it still stands is colleague's call and
the operator's, and any update goes through the `communicate` skill — never a
push.

## Identity — embodiment, and the Gwen it ships

Two distinct identities live in this repo. Do not conflate them.

### 1. This agent (the repo's own mesh identity)

```yaml
# culture.yaml
agents:
- suffix: embodiment
  backend: colleague
  model: sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP
```

`backend: colleague` fixes the **resident prompt file** to
`AGENTS.colleague.md` — that is what the mesh runtime reads. This `CLAUDE.md`
is Claude Code's guidance file and is *not* the backend-consistency target.
`embodiment doctor` checks the same two invariants `steward doctor` does, plus
a skills-present warning.

### 2. Gwen — the teammate we ship

Gwen is one prompt-visible teammate. The name came from **G**emma + Q**wen**,
and it stays: the speaker is a Gemma, and a later stage gives Gwen a tool that
operates Qwen Code through the `worker` role.

The rig, **as the gateway reported it on 2026-09-21** (`GET /capabilities`).
Roles resolve **by name**, never by parsing model names, and this table drifts
— re-probe before relying on it:

| Role | Model | State | Gwen uses it for |
|------|-------|-------|------------------|
| `senses` | `nvidia/Gemma-4-26B-A4B-NVFP4` | feasible, ready | **the speaker** — generates every spoken reply |
| `stt` | `ivrit-ai/whisper-large-v3-turbo` | feasible, ready, advertises `realtime_vad_session` | ears (Hebrew) |
| `tts` | `notmax123/BlueTTS2.5-onnx` | feasible, ready | voice (Hebrew) |
| `worker` | `nvidia/Qwen3.6-35B-A3B-NVFP4` | ready, not feasible here | later: Qwen Code, behind a tool |
| `embedder` | `Qwen/Qwen3-Embedding-0.6B` | **not ready** | semantic recall — so recall is lexical-only today |
| `cortex` | `unsloth/Qwen3.8-27B-NVFP4` | not feasible | not used by the base |

Gwen **listens and speaks Hebrew** in v1, matching the deployed audio lane.
`senses` does not advertise image understanding on this rig, so a later vision
stage needs another role.

**Design rules for Gwen**, from
[colleague#352](https://github.com/agentculture/colleague/issues/352):

- Reuse the **resolved identity** (`identity.py` → `culture.yaml`
  `nick`/`suffix`, then `.colleague/identity.json`); no parallel persona system.
- Configure Gwen **explicitly**. Never infer an identity from model names.
- **Absent identity ⇒ byte-identical prompts.** This is the acceptance
  criterion that keeps the feature honest.
- Identity framing **must not** modify tool authority, routing, or safety
  policy. It renames who is speaking, never what they may do.
- A single-model run must not claim another mind exists.
- Traces still expose the **actual** contributing role, model, and machine.
- Role names are design metaphors for allocating responsibility across model
  seams, not claims about cognition.

### The realtime interface (lobes) — what the redesign decided

The gateway serves a `/v1/realtime` WebSocket: server-VAD segmenter, pcm16 @
24 kHz, base64 JSON events, a conversational-floor model, a TTS lane. The
operator's decisions, recorded in the spec:

- **Ears-only, and the daemon runs the turn.** The client never sends
  `response.create`. lobes *can* now run the whole turn server-side; the
  redesign declined that so memory (and later vision and tools) can enter the
  prompt. The daemon takes the transcript, recalls, calls `senses`, and speaks
  through `POST /v1/audio/speech`.
- **The daemon owns barge-in.** An ears-only session never receives
  `response.interrupted`, and lobes accepts `aec_mode` without acting on it. On
  `speech_started` during playback the daemon stops its own speaker.
- **Hot mic on `start`**, always visible, always mutable, with mute enforced in
  the capture path before encode — not in the UI. This *replaces* the old
  opt-in-mic rule; the visibility and the mute are what it was traded for.
- **Hardware echo cancellation**, on the operator's word, so the session
  declares `aec_mode=aec`. It is unmeasured until `t21`, which checks that Gwen
  never transcribes her own voice.
- **One active ear at a time.** lobes says concurrent realtime sessions are not
  yet validated. Host devices, the browser, and later a robot relay implement
  one endpoint interface; the inbound side speaks the lobes wire so a Reachy
  Mini needs a URL and a secret, not a new protocol.
- **The session never crosses machines.** The gateway refuses to tunnel a
  WebSocket to a proxied peer, so the daemon needs a gateway whose `stt` lane
  is local. The session is audio-only; vision is a separate
  `/v1/chat/completions` call.
- **Degrade, never raise.** A missing extra, a dead gateway, no audio device, a
  mid-session drop: each is a recorded degradation and a running daemon.

## Constraints that are not yours to resolve by guessing

- **Dependencies are human-gated** (deviation `d2` of the first cycle,
  superseding the original zero-dependency rule). `tests/test_zero_deps.py`
  pins the exact approved set — today `eidetic-cli`, `coherence-cli`,
  `events-cli` — and fails on any delta in either direction, naming what
  approval is being requested. Install footprint and import footprint are
  pinned **separately**: `neo4j`, `pymongo` and `paho` are installed and never
  imported at module scope, and that smaller set must not be "fixed" to match.
  Plan task `t3` is where the redesign's new dependencies get approved.
  Consequence recorded so it is not rediscovered: colleague's own zero-deps
  test fails if colleague ever depends on this package.
- **C2 — do not overclaim the name.** `reachy-mini-cli` owns the robot body and
  has its own `agent embody` layer; `reachy-lobes` its local brain. This package
  is **software presence**. State that in the README and in `explain` output
  rather than leaving it to inference.
- **C3 — degradation must be observable to the host.** Presence is a felt
  claim, and the worst outcome is an app that *appears* attentive and is not.
  Every degradation records a transition; nothing degrades silently. Known
  trap: eidetic falls back to lexical recall **silently** when the embedder is
  down — which is this rig's state today — so the daemon must report the recall
  mode actually in effect.
- **The verbatim invariant.** `ContextPacket.original` comes from the caller's
  input **verbatim, never from model output**, and intake **never raises**.
- **Privacy of the room.** `continuity.py` pins `DEFAULT_VISIBILITY = "public"`
  and this repo commits `.eidetic/memory/`. Anything the daemon writes must
  default to the **private** store under a pinned data dir. Raw audio is never
  written to disk; transcripts go to a private, size-bounded per-session log.
- **Loopback is not authentication.** A Cloudflare tunnel delivers remote
  requests from `cloudflared` on 127.0.0.1. Every state-changing request — and
  the event stream, which carries the transcript — needs the install secret, a
  Host and Origin allow-list, and a valid Access assertion when the Host is the
  public hostname.
- **Recalled memory is untrusted data.** The public eidetic pool is writable by
  every agent on this host. Recall enters the prompt at one point, quoted and
  attributed (`KNOWLEDGE_ATTRIBUTION`), never as instructions.
- **Provisioning and cross-repo posts are the operator's acts.** Nothing here
  runs `cultureflare … --apply`, and nothing is posted to a sibling repo
  without an explicit go-ahead. Draft, then ask.

## Commands

```bash
uv sync                                            # create .venv, install dev deps (incl. teken)
uv run pytest -n auto                              # full suite, parallel
uv run pytest tests/test_cli.py::test_whoami_text  # a single test
uv run pytest --cov=embodiment --cov-report=term   # coverage (gate: fail_under=60)
uv run embodiment whoami                           # identity from culture.yaml
uv run embodiment learn --json                     # structured self-teaching prompt
uv run embodiment doctor                           # agent-identity invariants
uv run teken cli doctor . --strict                 # the agent-first rubric gate CI enforces
```

Lint stack — CI's `lint` job runs all of these; line length is **100** everywhere:

```bash
uv run black --check embodiment tests
uv run isort --check-only embodiment tests
uv run flake8 embodiment tests
uv run bandit -c pyproject.toml -r embodiment
markdownlint-cli2 "**/*.md" "#node_modules" "#.local" "#.claude/skills" "#.teken"
```

CI (`.github/workflows/tests.yml`) has three jobs: `test` (pytest + SonarCloud,
which self-skips when `SONAR_TOKEN` is empty — fork PRs stay green), `lint`
(the stack above plus the rubric gate), and `version-check` (PR-only; blocks
merge when `pyproject.toml`'s version matches `main`). `publish.yml` pushes to
TestPyPI on PRs from this repo and to PyPI on merge to `main`, both via Trusted
Publishing.

## Code map (what exists today)

```text
embodiment/
  loop.py               the bounded tool loop; termination proved by AST tests
  subagent.py           delegation as an injected seam, bounded by arithmetic
  context.py media.py   context windowing; media handling   (both needed by loop)
  contract.py           the shared data contract
  presence_engine.py    the pump - no TTY, no thread, no clock
  presence.py           the pure policy half (cadence + clarify)
  perception.py         verbatim-invariant intake + never-raise
  identity.py           resolved identity
  framing.py            pure prompt framing; absent identity => identical prompts
  senses_text.py        SENSES_GROUNDING + KNOWLEDGE_ATTRIBUTION (measured text)
  continuity.py         the eidetic/coherence seam, in-process
  events.py             optional observer onto events-cli (MQTT)
  turn.py               ONE spoken turn, driven through loop.run; never silent, never raises
  tools.py              ToolRegistry (empty by default) + bind_tools; the additive tool seam
  memory.py             RoomMemory over continuity: private, pinned, deadline-bounded;
                        render_recalled is the ONE place recall enters a prompt
  session.py            the conversation: explicit-ask detector (Hebrew/English), the
                        turn queue, supersede on barge-in, summary on close
  safe_reason.py        degradation reasons that never carry speech, a secret or a raw id
  bus.py                in-process event bus + optional MQTT publish; redact at publish
  audio/features.py     FeatureExtractor: streaming min/max envelope + level, no IO
  audio/endpoint.py     AudioEndpoint protocol (+ NullEndpoint): start_capture, play,
                        stop_playback, playing, sample_rate, close -> EndpointCloseReport
  audio/host.py         the reSpeaker through pw-record/pw-play (arecord/aplay fallback,
                        d4): targets by pipewire node name, link verified, own stream by
                        client pid, mute in the capture path; imported only inside
                        daemon/app.py:main() (AST test)
  audio/remote.py       the inbound /v1/realtime endpoint (browser ear / robot relay):
                        first-message auth, one peer, bounded handshake reclaim
  realtime/wire.py      the lobes /v1/realtime wire, typed
  realtime/client.py    RealtimeEars: ears-only client (never response.create), bounded
  http/guard.py         install secret + Host/Origin allow-list + Access assertion seam
                        (refusing by default); the secret file, 0600, race-free
  http/server.py        loopback (or --bind-public) HTTP: static dashboard, SSE
                        projection with a last-ditch redact, control API under a deadline
  daemon/state.py       state dir (0700), bounded log, crash-durable degradation ledger,
                        per-session transcript logs (0600)
  daemon/lifecycle.py   start/status/stop by (pid, starttime) identity; never raises
  cli/__init__.py       parser + dispatch; _CliArgumentParser routes argparse
                        errors through the structured format; _json_hint is
                        pre-set from raw argv so parse-time errors honour --json
  cli/_errors.py        CliError{code,message,remediation} + exit-code policy
  cli/_output.py        emit_result / emit_error / emit_diagnostic
  cli/_commands/        whoami, learn, explain, overview, doctor, cli, start, status, stop, tunnel
  daemon/app.py         the daemon: one ear, the ears session, session + memory + voice, the
                        HTTP surface, close(deadline) with derived shares; degrade, never raise
  voice.py              Voice.speak: sentences -> /v1/audio/speech -> endpoint, paced features
  cli/_commands/tunnel  prints the cultureflare/cloudflared commands; never runs them
  explain/              catalog.py: markdown keyed by command-path tuples
web/                    the dashboard (Vite/React/TS): live waveform, transcript,
                        degradations; fetch-streamed SSE with the secret in the
                        Authorization header; web/dist is built, gitignored, shipped in
                        the wheel (t19)
scripts/dual-review.sh  the local review harness: 35B worker drafts, 27B cortex
                        verifies cited lines; one review at a time (kept deliverable)
tests/                  3085 tests
.claude/skills/         19 skills, all vendored (cite-don't-import)
docs/skill-sources.md   provenance ledger + re-sync procedure
docs/live-test-results/ only senses-grounding{.md,-probe.py}: the measured
                        evidence tests/test_senses_text.py pins against
docs/plans/…-progress.md the redesign's running state: what merged, what each round found
```

Also merged: `voice.py` (t12: sentence-by-sentence TTS, barge-in owned by the
daemon, a bounded body read, redirects refused), `cli/_commands/tunnel.py`
(t20: prints the provisioning commands, runs nothing), the packaging hook
(t19), the oscilloscope and BrowserEar (t18), and **`daemon/app.py` — the
daemon (t15)**. `turn.py`'s truncation proxy and `is_speakable` stay
unmeasured against the real synthesiser until plan task `t21`.

`start`, `status` and `stop` are the only verbs that touch a process; nothing
under `cli/_commands/` reaches `embodiment.loop` directly — the daemon does.
`framing.py` keeps `frame_muse` and `ROLE_MUSE` (pure text) but no longer has
`muse_system_message` — that needed the archived muse module's `MUSE_AUTHORITY`.

Contracts worth knowing before you add a verb:

- **Every failure raises `CliError`.** `_dispatch` catches it, routes through
  `_output`, and wraps anything else so **no Python traceback ever reaches
  stderr**. Exit codes: `0` success, `1` user error, `2` environment error,
  `3+` reserved.
- **Results to stdout, errors and diagnostics to stderr — never mixed**, in
  both text and JSON mode. Agents parse on this invariant.
- **Every command takes `--json`.** Text errors render as `error: …` then
  `hint: …`; the `hint:` prefix is required by the agent-first rubric.
- **Register a verb** by adding a `_commands/<verb>.py` with a `register(sub)`
  function, wiring it in `_build_parser()`, and adding a matching
  `explain/catalog.py` entry — `tests/test_cli_introspection.py` and the rubric
  gate both check the catalog covers the surface.
- **`culture.yaml` is parsed with a stdlib line-scan**, not PyYAML
  (`whoami.py:read_agent_fields`) — that is what keeps runtime deps empty.
  `find_culture_yaml()` walks up from `__file__`, *not* the CWD, so identity is
  always the agent's own; a wheel install with no `culture.yaml` alongside it
  falls back to literals and `doctor` reports a single info check.

## Conventions

- **Every PR bumps the version** — even docs/config/CI-only PRs. Use the
  `version-bump` skill; the `version-check` job blocks merge otherwise.
- **PRs go through the `cicd` skill** (`devex pr` + SonarCloud gating; `status`
  and `await` are this repo's extensions). Sign online posts as
  `- embodiment (Claude)`; the `cicd` / `communicate` scripts resolve the nick
  from `culture.yaml`, so don't hand-sign inside a body those scripts author.
- **Reach for `ask-colleague` reflexively** — it is the teammate at the next
  desk, and its value is a *second, independent mind* (a different
  backend/model), not a stronger one. Before presenting or opening a PR on a
  non-trivial committed diff, run `review`; for a fresh read of an unfamiliar
  area, run `explore`. Both are read-only in a throwaway worktree, so the
  reflex is always safe. `write --apply` / `write --pr` still needs the user's
  go-ahead. Its output is a second opinion to verify and own, never authority.
- **Frame-first for cross-repo work.** This lane abuts colleague, shell-cli,
  agentfront, eidetic, coherence, lobes and the reachy family. Use
  `/scope` → `/think` → `/challenge` → `/spec-to-plan` before writing code that
  crosses a sibling's boundary — that is exactly the case those skills exist for.
- **Propose to siblings; don't push to them.** Cross-repo asks go through the
  `communicate` skill (`agtag`-backed issue posts).
- **Vendored skills are cited verbatim** — never reformat or edit their
  scripts. Re-sync from guildmaster (or the tracked-divergence origin) per
  `docs/skill-sources.md`.

### Worktrees

Every worktree you create by hand lives in **`../.worktrees.embodiment/<name>/`**
— one repo-named directory beside the checkout, one subfolder per worktree:

```bash
git worktree add ../.worktrees.embodiment/<name> -b <branch>
```

Never a shared `../worktrees/`: this workspace holds ~150 sibling projects, and
a generic folder accumulates orphaned trees from several repos with nothing
indicating ownership — a stale-tree sweep can't tell a live lane from junk.
Scope the branch prefix to the work (`extract/t2`, not `agent/t2`); plain
`agent/*` collides with leftovers from earlier fan-outs and `git worktree add
-b` fails on an existing branch. The vendored `assign-to-workforce` skill now
mandates this same `.worktrees.<repo-name>` root (re-synced 2026-09-21), but its
fan-out example still uses `agent/<task-id>` — it is cited verbatim and must not
be edited, so override the branch prefix when following it. Tear down
with `git worktree remove <path>` (`prune` only clears metadata for directories
that are already gone). Tool-managed throwaways are out of scope:
`ask-colleague`'s read-only verbs create a detached worktree under
`${TMPDIR:-/tmp}` and reap it on an EXIT trap.

### Memory discipline — recall before, remember after

`/recall` before you start a non-trivial task (prior decisions, gotchas, "have
we done this before?"), `/remember` when something worth keeping surfaces (a
non-obvious decision *and its rationale*, a constraint, a fix and why it was
needed). Capture it as it happens, not at the end when it has faded.

**A plain `/remember` here is PUBLIC and COMMITTED.** This repo's vendored
wrappers inject `--scope embodiment --visibility public` — the memory
scope+visibility convention v1 (eidetic `docs/contract.md`, eidetic-cli#28).
That public default is eidetic's *own* contract, not a downstream override: it
matches the plain `eidetic remember` CLI's default and colleague's
`memory.py` hardcode, so a no-flag remember here and a no-flag `eidetic
remember` elsewhere land mutually-visible records. A public record inside a git
repo routes to `<repo-root>/.eidetic/memory` — committed, shared with the team
and mesh peers. The scope is resolved at runtime from `culture.yaml`'s
`suffix`, so the Claude and colleague backends share one store. Pass
**`--visibility private`** to keep a record in `$HOME/.eidetic/memory` instead
(never committed); `/recall` reads both stores and merges. An explicit
`--scope` or `--visibility` on the command line always wins. With no resolvable
suffix (a wheel install with no `culture.yaml`) the wrapper leaves both flags
unset so the plain CLI defaults apply — `default`/public: the same visibility,
just grouped under the `default` scope name. There is no stderr warning on that
path and no privacy downgrade either way.

Don't store what the repo already records (code structure, git history,
`CHANGELOG.md`) — store what you would otherwise re-derive.

## Known doc drift (fix when you touch these)

- `docs/skill-sources.md` still describes this repo through the template's
  lens in places; consumer-identifying prose was adapted, upstream citations
  intentionally were not.

Resolved 2026-07-25 (kept as a caution about how this list is read):

- ~~`docs/skill-sources.md` has 16 rows but `.claude/skills/` holds 18.~~
  `remember` / `recall` now carry provenance entries (origin:
  `agentculture/eidetic-cli`).
- ~~**Vendored-script drift (upstream bug).**~~ **It was not an upstream bug.**
  The vendored `remember`/`recall` copies were a mid-flight snapshot of
  eidetic-cli#28: they had the flipped code line but not the documentation pass
  that followed, so the scripts *and both `SKILL.md`s* still claimed a private
  default while injecting `--visibility public`. eidetic-cli 0.12.1 was already
  correct on every surface, so there was nothing to file — the defect was local
  staleness, fixed by re-syncing all four files verbatim. Details and the
  re-sync path are in `docs/skill-sources.md`.

  The lesson worth keeping: this section asserted an upstream bug that did not
  exist. Verify a drift entry against the current upstream before acting on it
  — filing it as written would have opened an issue on a sibling repo for
  something they had already closed.
