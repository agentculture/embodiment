# embodiment

A small, tested core for giving an app an embodied AI presence — being rebuilt
into **Gwen, a background realtime voice app**.

## Status

**The realtime app is planned, not built.** As of this release nothing in the
package listens, speaks, runs as a daemon or serves a dashboard. What ships is
the core below and a CLI with introspection verbs only.

- The direction is specified in
  [`docs/specs/2026-09-21-realtime-embodiment-app.md`](docs/specs/2026-09-21-realtime-embodiment-app.md)
  and planned in
  [`docs/plans/2026-09-21-realtime-embodiment-app.md`](docs/plans/2026-09-21-realtime-embodiment-app.md).
- Everything that used to live here — the strategist tier, the configuration
  lane, the muse, drones, the example hosts and every live-test result — is
  **archived in git history**, not deleted from the record. The tag
  [`archive/pre-realtime-0.14.0`](https://github.com/agentculture/embodiment/tree/archive/pre-realtime-0.14.0)
  is the last commit that contains it.

### Why the archive

The reason is the old package's own published verdicts, not a change of taste.
By its final cycle this was a 37.7k-line library of loop, strategist,
configuration-lane and muse experiments, and its own record said, cycle after
cycle, *mechanism proven, value not*:

- The strategist's ScopeBench Stage 1 returned **`INCONCLUSIVE`**, and the one
  matched live control had the governed arm spend 189.6 s and 3663 strategist
  tokens to apply **zero** directives and return a materially identical answer.
- The configuration lane shipped with its value **unmeasured**.
- The muse measured a **1.2%** intervention rate for 2.4–4.4× the token cost,
  and its tool lane returned `INCONCLUSIVE` while measuring real harm.

Every one of those tiers shipped opt-in and off. Nothing in the package ran as
an app, nothing listened or spoke, and no sibling repo imported it. Presence is
a felt claim, and the library never let anyone feel it.

**The redesign does not claim value either.** It has no measurement yet. When
it does, the number is published as measured, including if it is bad.

## Software presence, not a body

"Embodiment" is an overloaded word in this mesh. `reachy-mini-cli` owns the
physical robot — and has its own `agent embody` layer, which is a different
thing from this package — and `reachy-lobes` owns its local brain. This package
gives an *application* a loop and a presence. It does not drive hardware and
nothing here claims a body. A later stage lets a Reachy Mini act as a relay for
Gwen's ears and voice; that is a planned, stated seam, never an implication
drawn from the name.

## What is in the package today

| Module | What it is |
|--------|------------|
| `loop.py` | The bounded perceive → decide → act tool loop. Termination is proved *structurally* by AST tests, not only behaviourally. Needs `context.py`, `contract.py`, `media.py` and `subagent.py` |
| `subagent.py` | Delegation as an injected seam, bounded by arithmetic |
| `presence_engine.py`, `presence.py` | The presence pump (no TTY, no thread, no clock — all IO injected) and its pure policy half |
| `perception.py` | The verbatim invariant: the user's words come from the caller's input, never from model output; intake never raises |
| `identity.py`, `framing.py` | Explicitly configured identity and pure prompt framing. Absent identity ⇒ byte-identical prompts |
| `senses_text.py` | `SENSES_GROUNDING` and `KNOWLEDGE_ATTRIBUTION`, measured host-composable prompt text |
| `continuity.py` | Memory and coherence through `eidetic-cli` and `coherence-cli`, imported in-process; every degradation recorded |
| `events.py` | Optional observer publishing loop and presence events over `events-cli` (MQTT) |
| `contract.py`, `context.py`, `media.py` | The shared data contract, context windowing, media handling |

1271 tests, 97% coverage.

## What is planned

Gwen, on one rig, built on the [lobes](https://github.com/agentculture/lobes-cli)
`/v1/realtime` API:

- **A daemon** — `embodiment start` / `stop` / `status`. It works with no
  browser open; the dashboard is one client of it.
- **The daemon runs the turn.** The realtime socket stays ears-only. The daemon
  takes the transcript, injects recalled memory, calls the speaker model and
  speaks. The turn is built on `loop.py` with an empty tool registry, so tools
  and agent triggers arrive later as additions rather than a rewrite.
- **Interchangeable ears.** Host microphone and speaker, a browser, and later a
  secret-authenticated robot relay — one active at a time.
- **Memory that is private by default.** Nothing said in the room reaches a
  committed store unless the operator promotes it.
- **A dashboard** with a live assistant waveform, reachable remotely behind
  Cloudflare Access. MQTT is the internal event substrate; the browser sees a
  server-sent projection of it.

Not in the first release: tools, vision, a face, voice from the phone, robot
support, or any claim that this is useful. The base is accepted on **one rig
only**.

## Inbound realtime endpoint — a browser today, robot-shaped seam

`embodiment/audio/remote.py` is a `websockets` server that speaks the lobes
`/v1/realtime` wire **inbound**, implementing the same `AudioEndpoint`
protocol `embodiment/audio/host.py` implements for the machine's own
microphone and speaker. A browser tab dials in, the WebSocket handshake
completes carrying no credential at all, and the FIRST message on the socket
must be `{"type": "auth", "secret": "..."}` — checked against an install
secret (or a per-endpoint secret) in constant time before any other message,
audio included, is ever processed. A wrong, missing, malformed or late first
message closes the socket (code 1008) and is recorded; only then is the
browser Gwen's ears and mouth over that socket. The secret deliberately never
rides the connect URL — a URL lands in proxy access logs, browser history and
`Referer` headers, all of which this repo's privacy rules forbid for a
credential.

**v1 ships no robot support.** Only a browser is an exercised, supported
consumer of this endpoint today. What ships is the *seam* — one
`AudioEndpoint` implementation among several the daemon can compose — not a
robot integration: a robot relay speaking the same wire is a future,
separate implementation of that seam, arrived at without touching the daemon
that composes endpoints.

## Dependencies — what installing this costs you

Dependencies are human-gated: `tests/test_zero_deps.py` pins the exact approved
set and fails on any change in either direction.

| Dependency | Pulls in |
|------------|----------|
| `eidetic-cli>=0.12` | `data-refinery-cli[store]` → neo4j + pymongo (installed, never imported at module scope) |
| `coherence-cli>=0.6` | numpy + httpx |
| `events-cli>=0.10` | paho-mqtt (imported lazily) |

`headspace-cli` (and with it the docker SDK) left with its only consumer in the
archive.

## CLI

```bash
embodiment whoami        # identity from culture.yaml
embodiment learn         # structured self-teaching prompt
embodiment explain       # markdown docs; `explain <path>` for any verb
embodiment overview      # descriptive snapshot
embodiment doctor        # agent-identity invariants
embodiment cli overview  # the CLI surface
```

Every verb takes `--json`. Results go to stdout, errors and diagnostics to
stderr, never mixed. Exit codes: `0` success, `1` user error, `2` environment
error. No verb drives the loop or starts anything yet.

## Development

```bash
uv sync
uv run pytest -n auto
uv run pytest --cov=embodiment --cov-report=term   # gate: 60%
uv run teken cli doctor . --strict                 # the agent-first rubric CI enforces
```

Lint stack (line length 100): `black`, `isort`, `flake8`, `bandit`,
`markdownlint-cli2`. Every PR bumps the version.

## Where embodiment sits

| Layer | Package | Owns |
|-------|---------|------|
| Surface | `agentfront` | CLI / MCP / HTTP fronts |
| Tools | `shell-cli` | The file-and-shell tool surface |
| **Loop + presence** | **`embodiment`** | **Perceive → decide → act, and the presence between acts** |
| Models, ears, voice | `lobes-cli` | The gateway, roles, `/v1/realtime`, STT and TTS |
| Memory / trust | `eidetic-cli`, `coherence-cli` | Durable recall; consistency signals |
| Product | `colleague` | The coder-agent harness |

## License

See [LICENSE](LICENSE).
