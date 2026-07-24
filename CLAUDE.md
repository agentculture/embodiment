# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`embodiment` is the **loop + presence layer** of the AgentCulture stack: the
agentic loop that gives an app an embodied AI presence. An app installs it
(imports the package) or wraps with it, supplies a **model seam** plus **IO
callbacks**, and gets the perceive → decide → act loop and the **presence pump**
that keeps the app feeling attended-to between acts. The loop is written once
here and imported, not reimplemented per host.

**Library first, CLI second.** The `embodiment` console script exists (and
carries the agent-first introspection verbs every Culture sibling has), but the
value is the importable seam an app depends on.

### Honest status — read this before you plan work

**The loop and the presence pump are not in this repo yet.** What is on disk
today is the `culture-agent-template` scaffold, renamed: an agent-first CLI
(`whoami` / `learn` / `explain` / `overview` / `doctor` / `cli`), a mesh
identity, the vendored skill kit, and green CI + PyPI publishing. `git log` is
two commits. Everything under [The extraction](#the-extraction-what-comes-from-colleague)
is **work to be done**, sourced from `colleague`, not code you can read here.

Keep this file's claims grounded in checked-in reality. When a section drifts
ahead of what exists, mark it `(planned)` or move it under a roadmap heading.

## Where embodiment sits

| Layer | Package | Owns |
|-------|---------|------|
| Surface | `agentfront` | "Agent First Interface" — CLI / MCP / HTTP / TAUI fronts, the App registry |
| Tools | `shell-cli` | The file-and-shell tool surface + approval policy |
| **Loop + presence** | **`embodiment`** | **The pump: perceive → decide → act, and the presence between acts** |
| Memory / trust | `eidetic-cli`, `coherence-cli` | Durable recall + provenance; drift/consistency signals (issue #2) |
| Product | `colleague` | The coder-agent harness — the opinions, not the plumbing |

Three questions, three layers: *how does a human or agent reach it*
(agentfront), *what can it do* (shell-cli), *what makes it keep going and feel
present* (embodiment).

`colleague` is the **first consumer, and this is a refactor** — the code
embodiment will ship already exists inside colleague `1.52.1` and works. The
job is to make it reusable, then **propose** that colleague import it.
colleague's refactor is colleague's to make: file an issue on
`agentculture/colleague`, never push there.

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
(The scaffold seed claimed `backend: claude`; `culture.yaml` is authoritative
and the discrepancy noted in issue #1 §6.2 is resolved in favour of
`colleague`.) `embodiment doctor` checks the same two invariants `steward
doctor` does, plus a skills-present warning.

### 2. Gwen — the embodiment we ship

The reference embodiment this repo delivers is **Gwen**: one prompt-visible
teammate produced by cooperating cognitive roles across two model families —
**G**\ from Gemma, **wen** from Qwen. Colleague remains the runtime and agent
type; embodiment is the loop that drives the roles; Gwen is the teammate the
operator actually talks to.

| Concept | Value | Serves |
|---------|-------|--------|
| Runtime | `colleague` | the harness |
| Loop + presence | `embodiment` (this repo) | the pump |
| **Teammate identity** | **Gwen** | what the operator addresses |
| Cortex | Qwen 3.6 27B (`sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP`) | bounded tool loop, repo actions, final synthesis, **final authority** |
| Muse | Gemma 4 31B (`nvidia/Gemma-4-31B-IT-NVFP4`) | tools-off advisory reasoning (deepthink); proposes, never decides |
| Senses | Gemma 4 12B (`coolthor/gemma-4-12B-it-NVFP4A16`) | intake, perception, conversational presence, speak-back; never acts on the repo |
| Ears / voice | Parakeet STT + Chatterbox TTS behind the lobes audio overlay | the realtime lane (below) |

Roles resolve **by name** from a `lobes` gateway's `/capabilities` contract
(`lobes/roles.py` — `cortex`, `senses`, `muse`, `embedder`, `reranker`, `stt`,
`tts`), never by parsing model names. The 31B muse is opt-in: it needs a
muse-hosting deployment shape (`lobes init --shape thor-muse`), because a 31B
cannot co-reside with the cortex+senses duo on a 128 GB box.

**Design rules for Gwen**, from
[colleague#352](https://github.com/agentculture/colleague/issues/352) (open;
no `Gwen` literal exists in colleague `1.52.1` yet — this is a contract to
build against, not code to read):

- Reuse the **resolved identity** (`colleague/identity.py` → `culture.yaml`
  `nick`/`suffix`, then `.colleague/identity.json`); do not add a parallel
  persona system.
- Configure Gwen **explicitly** for the reference rig. Never infer an identity
  from model names.
- **Absent identity ⇒ byte-identical prompts.** No configured identity means
  today's prompts survive unchanged, byte for byte. This is the acceptance
  criterion that keeps the feature honest.
- Identity framing **must not** modify tool authority, routing, or safety
  policy. It renames who is speaking, never what they may do.
- Every prompt-bearing role gets the *same* resolved identity with
  **role-specific authority**: senses speaks externally as Gwen in the first
  person; cortex is framed only on the **top-level acting loop** (typed
  subagents are not "cortex"); muse receives its authority boundary through a
  system message on **every** deepthink path; transcription stays
  identity-neutral.
- A single-model run must not claim another mind exists.
- Traces still expose the **actual** contributing role, model, and machine.

### The realtime interface (lobes)

The rig's `lobes` gateway serves a `/v1/realtime` WebSocket
(`lobes/realtime/` in `lobes-cli`: server-VAD segmenter, pcm16 @ 24 kHz, a
base64 event codec, a conversational-floor model, the Chatterbox TTS lane).
colleague already consumes it in `colleague/realtime.py`, and its discipline is
the discipline embodiment inherits:

- **Ears-only.** The client never sends `response.create` and never arms the
  bridge's own LLM turn — it consumes session lifecycle, VAD turn boundaries,
  and transcription only. **Senses stays the only mind that answers**, and
  replies ride the existing batch `synthesize()` TTS lane. The socket is a
  listening ear, never a second voice.
- **Opt-in mic.** The microphone is never hot by default (`/voice`, `--voice`).
- **Client-edge half-duplex mute** substitutes for AEC on hardware with no
  echo-cancelling front end (lobes deviation d1); capture drops frames *before*
  encode while the gate is held.
- **One sanctioned pump thread**, a `threading.Event` stop signal, a poll-wake
  read, and a **bounded** join so teardown never hangs on a parked blocking read.
- **Degrade, never raise.** A missing optional extra is a clean `CliError` with
  an install hint; a dial/handshake/mid-session failure returns `None` or flips
  a `degraded` flag with exactly one stderr notice. Turn-based voice is the
  degrade floor.
- Discovery rides the `stt` role's `realtime_vad_session` advert; env/config
  outrank it; same-origin key rules apply on the WS dial.

If embodiment owns the presence pump, this lane is a **perception seam**, not a
dependency: it must arrive through injected IO callbacks and an optional
extra, never as a base dependency (see the zero-deps rule below).

## Constraints that are not yours to resolve by guessing

These come from the build brief (issue #1) and hold until a sibling repo agrees
otherwise in writing.

- **C1 — pure-stdlib core.** colleague's `tests/test_zero_deps.py` asserts its
  `[project].dependencies` is *exactly* `["agentfront>=…"]` and that importing
  colleague adds no third-party top-level import. `embodiment` must clear the
  same bar or `pip install embodiment` breaks colleague's CI. `pyproject.toml`
  ships `dependencies = []` today — keep it that way; retrofitting
  stdlib-purity is far harder than starting there. Optional capabilities go
  behind extras, lazily imported inside a function, never at module load.
- **C1b — the third-base-dependency question is open.** "agentfront is the ONE
  sanctioned base dependency" does not survive being said three times. Does
  colleague allow-list three, or does embodiment *compose* `shell-cli` so
  colleague gains one dependency instead of two? Raise it with colleague and
  shell-cli before choosing.
- **C2 — do not overclaim the name.** In this mesh `reachy-mini-cli` owns the
  robot body, `reachy-lobes` its local brain, `reachy_nova` its AI brain. A
  package called `embodiment` reads as *physical* embodiment by default. On the
  ask as given this is **software presence** — an app gains a loop and a
  presence, not a body. State that boundary explicitly in the README and in
  `explain` output rather than leaving it to inference (the `shell-cli`
  precedent: state the threat model, don't let the name imply a sandbox).
- **C3 — degradation must be observable to the host.** Presence is a felt
  claim, and the worst available outcome is an app that *appears* attentive and
  is not. Every degradation records a transition; nothing degrades silently.
- **The verbatim invariant.** colleague's `senses.py` sets
  `ContextPacket.original` from the caller's input **verbatim, never from model
  output** — the arc's core invariant. And every senses invocation **never
  raises**: a dead port, request error, overflow, or lossy JSON degrades to
  `None` plus a degraded record. Inherit both. A presence layer that can lose
  the user's words, or fail loudly into an app's main path, is worse than none.

## The extraction (what comes from colleague)

Both seams were written **injection-shaped**, so this is decoupling work, not
redesign. Read these in `../colleague/` before designing anything:

| Source | Lines | What it already guarantees |
|--------|-------|----------------------------|
| `colleague/loop.py` | 4463 | Engine-agnostic bounded tool loop: handed a `complete` callable that performs *one* model turn, drives it until `finish` / an empty tool-call turn / the `max_steps` budget. Guaranteed termination is an honesty condition (h3). Hook lifecycle at `task_start` / `pre_tool` / `post_tool` / `finish`, with only `pre_tool` control-bearing (`deny`, `rewrite`); hooks add no exit path and cannot extend the budget. Public shapes: `ToolCall`, `ModelResponse`, `WorkAborted`, `TaskResult`. |
| `colleague/presence_engine.py` | 305 | ONE pump every surface shares (session, `talk` attach, background progress sink, mesh resident) so "talking to one colleague" feels identical everywhere. **No TTY, no thread, no clock** — all IO rides injected `PresenceIO` callbacks and cadence is step/phase-based. Imports no front module; that independence is pinned by an import-graph test. API: `PresenceIO`, `build_presence_executor`, `PresenceEngine`. |
| `colleague/presence.py` | 187 | The policy half — pure policy, no IO, no model calls, no clock: `UpdateCadence`, `should_update`, `ClarifyPolicy`, `should_clarify`, `is_go_word`, plus `*_from_env` builders that never raise on malformed values. |
| `colleague/senses_loop.py` | 524 | The second loop: bounded coordination that is agentic **without ever putting a tool schema on the wire** — each turn is one tools-off completion returning prompted JSON. Per-boundary completion cap (`DEFAULT_LOOP_CAP`), per-completion context windowing, and an explicit degradation ladder `loop → beats → off` that **records** every transition. |
| `colleague/senses.py` | 1139 | The cortex/senses arc: intake, speakback, live talk, update. Source of the verbatim invariant and never-raise rule. |
| `colleague/realtime.py` | — | The ears-only realtime client described above. |

**Do the triage early.** `loop.py` imports roughly a dozen colleague modules at
module scope (`affectedtests`, `autosplit`, `backpressure`, `coherence`, …).
Classify each as *loop-essential* or *colleague-policy*; the second group
becomes an injection point or stays behind. That triage **is** the size
estimate for the extraction.

## Open issues — the current agenda

### #1 — Build brief (the founding document)

Read it in full (`gh issue view 1`) before non-trivial work. It carries the
layer map, the constraints above, the provisioning record, and five **parked
questions you must answer before building**:

1. **One loop or two?** colleague has the cortex tool-loop *and* the senses
   coordination loop. The ask names both. They are separable and shipping order
   matters. Decide, and say why.
2. **Does presence require two model seams?** colleague's presence rests on the
   cortex/senses two-lobe split. Inheriting it means every consumer needs two
   endpoints. Does embodiment *require* two seams, or degrade coherently to one?
3. **Library mode vs wrapper mode.** "Install in apps or wrap" is two very
   different contracts (app imports you and supplies callbacks vs you drive the
   app from outside). Which ships first, and does the second follow or get
   dropped?
4. **What is the decoupling cost?** The `loop.py` import triage above.
5. **Do you own the presence event stream?** `harmonics-cli` maps state onto
   sonic signatures; `reterminal` renders a thinking/message/emotion feed onto
   e-paper. Those are presence *expression* surfaces with **no shared
   producer**. Is defining that event stream embodiment's job, or is embodiment
   loop-only?

No comments on the issue yet — answers belong there (or in a `/think` frame
exported to `docs/specs/`), not only in a commit message.

### #2 — Continuity through Eidetic and Coherence

"An embodiment without memory is a sequence of awakenings. An embodiment
without coherence is a sequence of plausible but potentially different selves."

Compose, don't reimplement: `eidetic-cli` owns memory (recall, provenance,
consolidation, relevance, ageing, forgetting); `coherence-cli` owns the
relationship between memory and the present (quality, meaning, signal,
investiture, frames — five domains, each with honest can't-verify diagnostics);
**embodiment owns the lived sequence** — when something is perceived,
considered, acted on, remembered, or revisited.

Load-bearing principles from the issue:

- Memory and coherence are **runtime**, not tools the model may pick arbitrarily.
- Preserve provenance from perception → action → durable memory.
- **Remember selectively.** Storing everything is not understanding what matters.
- Check coherence at meaningful **boundaries**: before consequential action,
  before final completion, before durable memory.
- Keep permission separate from coherence: coherence asks whether an action
  makes *sense*; the capability layer decides whether it is *permitted*.
- Degradation in either subsystem is visible to the host, never silent
  corruption of continuity.
- Definition of done includes a demonstration in a **non-colleague** app.

Note the dependency tension with C1: both are CLIs, consumable over a
subprocess boundary — which is exactly how consumers stay dependency-free
(eidetic's own README makes this argument). Prefer the subprocess seam or an
injected port over a base dependency.

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
  cli/__init__.py       parser + dispatch; _CliArgumentParser routes argparse
                        errors through the structured format; _json_hint is
                        pre-set from raw argv so parse-time errors honour --json
  cli/_errors.py        CliError{code,message,remediation} + exit-code policy
  cli/_output.py        emit_result / emit_error / emit_diagnostic
  cli/_commands/        whoami, learn, explain, overview, doctor, cli
  explain/              catalog.py: markdown keyed by command-path tuples
tests/                  CLI smoke + introspection tests (22 tests)
.claude/skills/         18 vendored skills (cite-don't-import)
docs/skill-sources.md   provenance ledger + re-sync procedure
```

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
-b` fails on an existing branch. The vendored `assign-to-workforce` skill's
fan-out example uses *both* the shared path and `agent/<task-id>` — it is cited
verbatim and must not be edited, so override both when following it. Tear down
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
wrappers inject `--scope embodiment --visibility public` (the rollout-cli
eidetic-memory recipe's deliberate policy override of eidetic's own private
default), and a public record inside a git repo routes to
`<repo-root>/.eidetic/memory` — committed, shared with the team and mesh peers.
The scope is resolved at runtime from `culture.yaml`'s `suffix`, so the Claude
and colleague backends share one store. Pass **`--visibility private`** to keep
a record in `$HOME/.eidetic/memory` instead (never committed); `/recall` reads
both stores and merges. An explicit `--scope` or `--visibility` on the command
line always wins, and with no resolvable suffix the wrapper warns on stderr
before falling back to eidetic's `default`/public scope.

Trust the code, not the wrappers' prose: `remember.sh` / `recall.sh` still carry
header and `--help` text claiming a *private* default, which their own
flag-injection blocks (`remember.sh:148`, `recall.sh:144`) contradict. The
scripts are vendored cite-don't-import — do not edit them to fix this; the
divergence is logged under [Known doc drift](#known-doc-drift-fix-when-you-touch-these).

Don't store what the repo already records (code structure, git history,
`CHANGELOG.md`) — store what you would otherwise re-derive.

## Known doc drift (fix when you touch these)

- `docs/skill-sources.md` has **16** table rows but `.claude/skills/` holds
  **18** — the eidetic-origin `remember` / `recall` skills have no provenance
  entry. Add them (origin: `agentculture/eidetic-cli`) on the next skills PR.
- `docs/skill-sources.md` still describes this repo through the template's
  lens in places; consumer-identifying prose was adapted, upstream citations
  intentionally were not.
- **Vendored-script drift (upstream bug).** `remember.sh` / `recall.sh` document
  a `--visibility private` default in their header comments and `--help` usage,
  while the code injects `--visibility public` (`remember.sh:148`,
  `recall.sh:144`). The code is authoritative — a plain `/remember` commits to
  the repo. These are cited verbatim, so the fix belongs upstream in
  `agentculture/eidetic-cli`, not here; file it there when the next skills
  re-sync happens.
