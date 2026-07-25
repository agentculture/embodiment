# embodiment

The agentic loop that gives an app an embodied AI presence. Install `embodiment`
in an app or wrap one, supply a **model seam** plus **IO callbacks**, and it
drives the perceive → decide → act loop and the presence pump — extracted from
[`colleague`](https://github.com/agentculture/colleague) so the loop is written
once and imported, not reimplemented per host.

> **Software presence, not a robot body.** "Embodiment" is an overloaded word in
> this mesh: `reachy-mini-cli` owns the physical robot, `reachy-lobes` its local
> brain. This package gives an *application* a loop and a presence — it does not
> drive hardware, and nothing here claims a body. If that ever changes it will
> be a stated goal agreed with the robot siblings, never an implication drawn
> from the name.

## Status

**The extraction has largely landed.** The loop, the presence pump, the muse
thinking loop, perception, continuity and Gwen framing are checked in on the
`spec/gwen-loop-presence-continuity` branch — this section previously said they
were still in `colleague 1.52.1` awaiting extraction, which stopped being true
partway through that build. Still to land: the continuity lifecycle checkpoints,
the degradation ledger, the demo app, and the seam proposal to colleague. See
[Roadmap](#roadmap) and [issue #1](https://github.com/agentculture/embodiment/issues/1).
`CLAUDE.md` carries the module-by-module status.

## What you get

- **The bounded tool loop and the presence pump** — the actual point of the
  package. `run()` is driven by an injected model seam and an injected tool
  executor; termination is proved structurally, not just tested.
- **An agent-first CLI** cited from [teken](https://github.com/agentculture/teken)
  (`afi-cli`).
- **A mesh identity** — `culture.yaml` (`suffix` + `backend`) and the matching
  resident prompt file (`AGENTS.colleague.md`, since this agent runs
  `backend: colleague`).
- **The canonical guildmaster skill kit** (18 skills) under `.claude/skills/`,
  vendored cite-don't-import. See [`docs/skill-sources.md`](docs/skill-sources.md).
- **A build + deploy baseline** — pytest, lint, the agent-first rubric gate, and
  PyPI Trusted Publishing wired into GitHub Actions.

## Dependencies — what installing this costs you

embodiment is a library host apps import, so **its dependencies become theirs**.
That is worth stating plainly rather than burying:

| Dependency | Pulls in | For |
|------------|----------|-----|
| `eidetic-cli` | `data-refinery-cli[store]` → neo4j, pymongo | durable memory |
| `coherence-cli` | numpy, httpx | memory-to-present coherence |
| `events-cli` | paho-mqtt | event emission |

This package began with a hard zero-dependency rule; deviation `d2` replaced it
with a **human gate**. `tests/test_zero_deps.py` pins the approved set and the
runtime import set and fails on any change in *either* direction — a new
dependency cannot arrive without someone deciding, and the failure message says
what approval is being asked for. Adding one is a deliberate act, not an edit.

Two honest caveats:

- **Install footprint ≠ import footprint.** neo4j, pymongo and paho are
  installed but never imported at rest — data-refinery resolves its backends
  lazily and events-cli loads paho on demand. A test guards that distinction so
  nobody "fixes" one to match the other.
- **`import embodiment` still costs nothing.** The package root stays lazy
  (PEP 562), so a consumer that only wants the loop never pays for the
  continuity stack. This is the strongest surviving piece of the original rule.

## Quickstart

```bash
uv sync
uv run pytest -n auto                 # run the test suite
uv run embodiment whoami              # identity from culture.yaml
uv run embodiment learn               # self-teaching prompt (add --json)
uv run teken cli doctor . --strict    # the agent-first rubric gate CI runs
```

## CLI

| Verb | What it does |
|------|--------------|
| `whoami` | Report this agent's nick, version, backend, and model from `culture.yaml`. |
| `learn` | Print a structured self-teaching prompt. |
| `explain <path>` | Markdown docs for any noun/verb path. |
| `overview` | Read-only descriptive snapshot of the agent. |
| `doctor` | Check the agent-identity invariants (prompt-file-present, backend-consistency). |
| `cli overview` | Describe the CLI surface itself. |

Every command supports `--json`. Results go to stdout, errors/diagnostics to
stderr (never mixed). Exit codes: `0` success, `1` user error, `2` environment
error, `3+` reserved.

## Where embodiment sits

| Layer | Package | Owns |
|-------|---------|------|
| Surface | `agentfront` | CLI / MCP / HTTP / TAUI fronts, the App registry |
| Tools | `shell-cli` | The file-and-shell tool surface + approval policy |
| **Loop + presence** | **`embodiment`** | **The pump: perceive → decide → act, and the presence between acts** |
| Memory / trust | `eidetic-cli`, `coherence-cli` | Durable recall + provenance; drift and consistency signals |
| Product | `colleague` | The coder-agent harness — the opinions, not the plumbing |

Three questions, three layers: *how does a human or agent reach it*
(`agentfront`), *what can it do* (`shell-cli`), *what makes it keep going and
feel present* (`embodiment`).

## Gwen — the embodiment we ship

The reference embodiment is **Gwen**: one prompt-visible teammate produced by
cooperating cognitive roles across two model families — **G** from Gemma, **wen**
from Qwen. Colleague stays the runtime; embodiment drives the roles; Gwen is who
the operator talks to.

| Concept | Value | Authority |
|---------|-------|-----------|
| Runtime | `colleague` | the harness |
| Loop + presence | `embodiment` | the pump |
| **Teammate identity** | **Gwen** | who the operator addresses |
| Cortex | Qwen 3.6 27B (`sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP`) | bounded tool loop, repo actions, final synthesis — **final authority**. Framed by embodiment. |
| Muse | Gemma 4 31B (`nvidia/Gemma-4-31B-IT-NVFP4`) | a tools-off advisory mind running its own parallel thinking loop; proposes, never decides. Framed by embodiment. |
| Senses | Gemma 4 12B (`coolthor/gemma-4-12B-it-NVFP4A16`) | intake, perception, speak-back; never acts on the repo. **Lives in colleague, not here** — see the scope note below. |
| Ears / voice | Parakeet STT + Chatterbox TTS (lobes audio overlay) | the realtime lane below |

> **Scope: embodiment frames cortex and muse, not senses.** The table describes
> the whole reference rig, but only part of it is this package. embodiment ships
> **one** actor loop; colleague's senses coordination loop stays in colleague,
> and so does its framing. This split is recorded on
> [colleague#352](https://github.com/agentculture/colleague/issues/352#issuecomment-5073964358).
> A single-model run — the default tested path — starts no muse and claims no
> second mind.

Roles resolve **by name** from a [`lobes`](https://github.com/agentculture/lobes-cli)
gateway's `/capabilities` contract — never by parsing model names. The identity
contract is tracked in
[colleague#352](https://github.com/agentculture/colleague/issues/352) and holds
four rules that keep it honest:

1. **Absent identity ⇒ byte-identical prompts.** Un-named deployments behave
   exactly as they do today.
2. **Identity never changes authority.** Framing renames who speaks; it does not
   touch tool authority, routing, or safety policy.
3. **Same identity, role-specific authority.** Cortex is framed only on the
   top-level acting loop — typed subagents never receive it; muse receives its
   authority boundary on every advisory path. (Senses framing and neutral
   transcription are colleague's half of the contract, not embodiment's.)
4. **Traces stay truthful** — they always expose the actual contributing role,
   model, and machine, and a single-model run never claims another mind exists.

### The realtime interface (lobes)

The rig's lobes gateway serves a `/v1/realtime` WebSocket (server-VAD, pcm16 @
24 kHz). Gwen's ear rides it **ears-only**: the client never triggers the
bridge's own model, so senses stays the only mind that answers and replies go
out over the batch TTS lane. The microphone is opt-in per session, a
client-edge half-duplex mute substitutes for AEC on hardware without one, and
turn-based voice is the degrade floor — a flaky socket degrades with a recorded
notice, never an exception into the host's main path.

## Roadmap

Both seams already exist in `colleague`, written injection-shaped, so this is
decoupling work rather than redesign:

- **The bounded tool loop** (`colleague/loop.py`) — engine-agnostic: handed a
  `complete` callable that performs one model turn, it drives until the model
  finishes, stops requesting tools, or the step budget is reached. Termination
  is guaranteed; hooks at four lifecycle points can deny or rewrite a tool call
  but add no exit path.
- **The presence pump** (`colleague/presence_engine.py` + `presence.py`) — one
  pump every surface shares, assuming **no TTY, no thread, and no clock**: all
  IO rides injected callbacks and cadence is step/phase-based.
- **The senses coordination loop** (`colleague/senses_loop.py`) — agentic
  without ever putting a tool schema on the wire, with an explicit
  `loop → beats → off` degradation ladder that records every transition.

Two invariants come across unchanged: the operator's words are preserved
**verbatim**, never round-tripped through a model, and perception **never
raises** — it degrades to a recorded, observable failure. A presence layer that
can lose the user's words, or fail loudly into an app's main path, is worse than
no presence layer.

### Open issues

| Issue | What it asks |
|-------|--------------|
| [#1](https://github.com/agentculture/embodiment/issues/1) | The build brief: extract the loop + presence from colleague, stay pure-stdlib (colleague's zero-deps guard), don't overclaim the name, keep degradation observable. Parks five questions to answer before building — one loop or two, one model seam or two, library mode or wrapper mode first, the decoupling cost of `loop.py`'s dozen module-scope imports, and whether embodiment owns the presence *event stream* that `harmonics-cli` and `reterminal` currently consume without a shared producer. |
| [#2](https://github.com/agentculture/embodiment/issues/2) | Continuity: compose `eidetic-cli` (memory, provenance, ageing) and `coherence-cli` (agreement between memory and the present) rather than reimplementing either. Embodiment owns the lived sequence; memory and coherence are runtime, not tools the model picks arbitrarily; checks happen at meaningful boundaries; degradation is visible to the host. Done means a second, non-colleague app gains the same continuity. |

Neither issue has replies yet — answers belong on the issue or in an exported
spec, not only in a commit message.

## Make it your own

This repo was minted from
[`culture-agent-template`](https://github.com/agentculture/culture-agent-template).
To mint another agent from it:

1. Rename the package `embodiment/` and the `embodiment` CLI/dist name
   throughout `pyproject.toml`, the package, `tests/`,
   `sonar-project.properties`, and this `README.md`. The name is hard-coded in
   ~100 places, so list every occurrence first: `git grep -nF -e embodiment`.
2. Edit `culture.yaml` with your `suffix` and `backend`.
3. Rewrite `CLAUDE.md` for your agent and run `/init`.
4. Re-vendor only the skills you need from guildmaster (see
   [`docs/skill-sources.md`](docs/skill-sources.md)).

See [`CLAUDE.md`](CLAUDE.md) for the full conventions (version-bump-every-PR,
the `cicd` PR lane, worktree layout, memory discipline, deploy setup).

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
