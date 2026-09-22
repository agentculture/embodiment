# embodiment

**Gwen, a background realtime voice app** — a daemon that listens through a
microphone array, answers in Hebrew through a speaker model on the
[lobes](https://github.com/agentculture/lobes-cli) gateway, remembers what it
is explicitly asked to, and shows what it is doing on a dashboard. Underneath
it is a small, tested core for giving an app an embodied AI presence.

## Status

**The daemon is built and runs, on one rig.** `embodiment start` launches it;
it has run live Hebrew turns with the operator through a reSpeaker XVF3800,
stored and recalled memory across a restart, been interrupted mid-sentence,
and been watched from a phone. Every module was reviewed and probed on the
device or in a browser before it was merged.

**Its usefulness is unmeasured.** Nothing here claims that Gwen is worth
talking to. Acceptance is on **one rig only** — one array, one gateway, one
operator, one language — and the acceptance run (`t21`, a human at the
microphone) publishes latency as measured, including if it is bad. See
[`docs/live-test-results/2026-09-22-t21-acceptance.md`](docs/live-test-results/2026-09-22-t21-acceptance.md)
when t21's result file lands; until it does, no number in this repo describes
the daemon's turn latency, its echo cancellation or its speakability, and
this README quotes none.

Known from the integration runs, not yet measured:

- The first word after a silence can be lost by the gateway's segmenter
  (observed 3 times in about 8 synthetic utterances; the operator's own ~30
  live turns did not show it). Pause, then speak.
- Hardware echo cancellation (`aec_mode=aec`) is declared and relied on; t21
  checks that Gwen never transcribes her own voice.
- The embedder on the rig is down, so recall is lexical; and eidetic's keyword
  tokeniser is ASCII-only, so Hebrew recall runs on an exact-substring fallback
  (deviation `d5`). The dashboard shows the recall mode actually in effect.

Everything that used to live here — the strategist tier, the configuration
lane, the muse, drones, the example hosts and the old live-test results — is
archived in git history, not deleted from the record. The tag
[`archive/pre-realtime-0.14.0`](https://github.com/agentculture/embodiment/tree/archive/pre-realtime-0.14.0)
is the last commit that contains it; its own published verdicts (mechanism
proven, value not) are quoted in `CLAUDE.md`, and the redesign inherits that
honesty: it makes no value claim until it has a measurement.

## Software presence, not a body

"Embodiment" is an overloaded word in this mesh. This package is **software
presence, not a body**: it gives an *application* ears, a voice, memory and a
loop. It does not drive hardware and nothing here claims a body.

- `reachy-mini-cli` owns the physical robot, and has its own **`agent embody`**
  layer — a name collision with this package, and a different thing. Nothing
  here is that.
- `reachy-lobes` owns the robot's local brain.
- A later stage lets a Reachy Mini act as a relay for Gwen's ears and voice.
  That is a planned, stated seam, never an implication drawn from the name.

## What runs

- **A daemon** — `embodiment start` / `status` / `stop`. It works with no
  browser open; the dashboard is one client of it. `status` reports process
  liveness, degradation counts and the recall mode in effect — and says
  plainly that a live process is not evidence that anything was heard.
- **Hot mic on `start`**, through the reSpeaker XVF3800 (`pw-record` /
  `pw-play` over PipeWire, `arecord` / `aplay` as fallback). The mic is always
  visible and always mutable; mute is enforced in the capture path, before
  encode, never in the UI.
- **An ears-only session** on lobes' `/v1/realtime`, dialled at the array's
  native 16 kHz. **The daemon runs the turn**: it never sends
  `response.create`. It takes the transcript, recalls, calls the `senses` role
  (a Gemma) for every spoken reply, and speaks through `POST /v1/audio/speech`
  into the same array.
- **Barge-in is the daemon's.** On speech during playback it stops its own
  speaker; the ears-only session never receives `response.interrupted`.
- **Memory in a private store** under the daemon's state directory, never the
  committed pool. Two things are remembered: an explicit spoken ask, and one
  summary at session end. Recalled memory enters the prompt at exactly one
  place, quoted, attributed and fenced — it is untrusted data, never
  instructions.
- **An HTTP surface** on loopback (or a tailnet address with `--bind-public`
  and `--allowed-host`): the dashboard, a control API, and a server-sent event
  stream. The install secret is required on every guarded request and on the
  stream, because the stream carries the transcript.
- **The dashboard** (React / Vite / TypeScript, shipped inside the wheel): a
  live waveform of what is being played, the transcript, and every degradation
  as it is recorded. It streams events with `fetch` and sends the secret in
  the `Authorization` header, so it works from a phone over TLS as well as on
  localhost.
- **`embodiment tunnel`** prints the `cultureflare` / `cloudflared` commands
  for remote access and never runs them.
- **Degrade, never raise.** A dead gateway, no audio device, a mid-session
  drop, a recall that missed its deadline: each is a recorded degradation,
  visible in `status`, on the dashboard and in a crash-durable ledger, and the
  daemon keeps running. Nothing degrades silently.

Retention: raw audio is never written to disk. Transcripts go to a private,
size-bounded, per-session log (0600) under the state directory (0700).

## How to run

```bash
uv sync                    # creates .venv with the approved dependencies
uv run embodiment doctor   # identity invariants; nothing is started
```

The gateway key comes from the `grant` CLI and is injected into the daemon's
environment for that one process. There is no env-file fallback: the daemon
reads `EMBODIMENT_GATEWAY_KEY` (or the mesh-wide `CULTURE_VLLM_API_KEY`) from
its environment and nothing else, and the key never reaches a browser, an
asset, an event, a log line or a `status` field.

```bash
grant run --inject EMBODIMENT_GATEWAY_KEY=LOBES_GATEWAY_API_KEY -- uv run embodiment start
uv run embodiment status
uv run embodiment stop
```

`start` returns once the daemon has reported *itself* running, not merely
once a process exists; a second `start` finds the first and starts nothing.
`EMBODIMENT_GATEWAY_URL` selects the gateway (default `http://localhost:8001`);
its `stt` lane must be local to that gateway, because the realtime session
never crosses machines (the gateway refuses to tunnel a WebSocket to a proxied
peer).

**The dashboard** is at `http://127.0.0.1:8823`. It asks for the install
secret; paste the contents of `<state dir>/install-secret`, where the
state directory is `$EMBODIMENT_STATE_DIR`, else `$XDG_STATE_HOME/embodiment`,
else `~/.local/state/embodiment`. The daemon creates that file (0600) on first
start.

**From another device on your tailnet**, bind off loopback and name the host
a browser will send:

```bash
grant run --inject EMBODIMENT_GATEWAY_KEY=LOBES_GATEWAY_API_KEY -- \
  uv run embodiment start --http-bind 100.x.y.z --bind-public --allowed-host 100.x.y.z:8823
```

`--bind-public` is required for any non-loopback bind — the event stream
carries the transcript — and `--allowed-host` is matched exactly as the
browser sends it. Put TLS in front (`tailscale serve` pointing at
`127.0.0.1:8823` is the cheapest way): over plain http to a non-localhost
address the browser withholds what the guard needs to vouch for a cookie, and
`status` reports `http.secure_context_required` when that applies.

## Boundaries

- **Hebrew only in v1**, listening and speaking, matching the deployed audio
  lane on the rig (`stt`: `ivrit-ai/whisper-large-v3-turbo`; `tts`:
  `notmax123/BlueTTS2.5-onnx`). Roles resolve by name on the gateway; the
  models behind them drift.
- **One active ear at a time.** lobes has not validated concurrent realtime
  sessions, so a second ear pre-empts or is refused, and either way the
  handover is published as one event and one ledger record.
- **The session never crosses machines.** The daemon needs a gateway whose
  `stt` lane is local. The session is audio-only.
- **Loopback is not authentication.** Every state-changing request and the
  event stream need the install secret; off loopback they also need a
  Host/Origin allow-list, and behind a public hostname a Cloudflare Access
  assertion (see below for what that verifier does today).
- **Identity is explicit.** Gwen is configured, never inferred from a model
  name; absent identity produces byte-identical prompts; identity framing
  renames who is speaking, never what they may do.

## Not yet

None of the following is in this release. Each is a stated seam or a stated
absence, not an implication.

- **Other tools.** The daemon binds exactly two tools, both over Gwen's own
  private memory: `remember` (d7) and `forget` (d8, which archives a record
  in place and never deletes a byte). Nothing else — no shell, no files, no
  Qwen Code. Gwen cannot act on anything outside her own memory.
- **Vision.** The session is audio-only; `senses` does not advertise image
  understanding on this rig.
- **A face.** The dashboard's centrepiece is a waveform; a speech-driven face
  is later.
- **Phone voice.** The phone is control and text. There is no browser
  microphone in v1 — the `BrowserEar` client and the inbound `/v1/realtime`
  endpoint (`audio/remote.py`) exist and are tested, and are **not wired** into
  the daemon.
- **A robot relay.** Same seam, same status: a Reachy Mini speaking the lobes
  wire into that endpoint is a future, separate implementation.
- **Cloudflare Access verification.** The guard *requires* the assertion on
  the public Host — the one named by `embodiment start --public-hostname` or
  `EMBODIMENT_PUBLIC_HOSTNAME`; with neither set, no Host is public and no
  assertion is asked for — and then **refuses every one**, because verifying
  its RS256 signature needs a dependency that is not yet approved. That
  refusal is a recorded state (`http-access-verifier-missing`), never a
  silent hole, and it means the dashboard is not reachable through a public
  hostname today.
- **Semantic recall.** The embedder is down on the rig; recall is lexical, and
  Hebrew recall runs on the exact-substring fallback above.
- **Reconnection.** A dropped realtime session is recorded and the daemon
  keeps running deaf until restarted.
- **A measured value claim.** See Status.

## Remote access

`embodiment tunnel` prints the `cultureflare`/`cloudflared` commands for
exposing the daemon beyond loopback — it is **dry-run only**: unlike `lobes
tunnel` there is no `--apply` flag here at all, because provisioning a
Cloudflare Tunnel and Access app is the operator's act, run by hand from the
printed command, never something this verb does itself.

```bash
embodiment tunnel
embodiment tunnel --json
embodiment tunnel --hostname gwen.example.org --allow me@example.com
embodiment tunnel --with-service-token
```

It prints two commands and runs neither:

1. `cultureflare remote-login setup --hostname <h> --service
   http://127.0.0.1:<port> [--allow <email>]... [--with-service-token]
   [--tunnel-name <name>]` — the one-time provisioning of the tunnel, the DNS
   record and the Cloudflare Access app/policy. `--allow <email>` (repeatable)
   is who the Access policy admits by browser login.
2. `cloudflared tunnel run <name>` — what the operator runs afterwards, once
   step 1 has actually been applied with `--apply`. `cloudflared` refuses a
   bare `cloudflared tunnel run` (it needs the name, or a `--token`), and
   `cultureflare` derives that name itself unless `--tunnel-name` overrides
   it — a rule `embodiment tunnel` does not know and will not guess. Pass
   `--tunnel-name <name>` and both commands carry that exact name; omit it and
   step 2 prints `<tunnel-name-from-step-1>` with a line saying to read the
   real name off step 1's own output before running it.

**A printed command is only honest if pasting it does what it says.**
`--hostname` (RFC-1123 labels), `--allow` (exactly one `@`, no whitespace) and
`--tunnel-name` (`[A-Za-z0-9._-]` only) are refused at parse time — a
structured `error:`/`hint:` and exit `1` — if they fall outside that charset,
and every token of a printed command line is `shlex.quote`-d regardless. So
`embodiment tunnel --tunnel-name 'a; touch pwned'` is refused outright, and
even a value that somehow bypassed that check would still print as one inert
quoted token, never a second shell command.

**What Cloudflare Access protects, and what it does not.** Access sits in
front of exactly one thing: requests arriving for the **public hostname**
named by `--hostname`. It never gates loopback names (`localhost`,
`127.0.0.1`, `::1`) — those never cross Cloudflare's edge at all, so there is
nothing for Access to intercept. Loopback is not authentication, so the
daemon does not rely on Access alone either (see below).

**How the daemon validates the assertion.** `embodiment/http/guard.py`
requires the `Cf-Access-Jwt-Assertion` header on every guarded request whose
`Host` is the configured public hostname, and only on those — a loopback
request is never asked for one. The hostname must be configured for the
rule to apply at all: `embodiment start --public-hostname <name>` (or
`EMBODIMENT_PUBLIC_HOSTNAME`); a daemon started without it treats no Host as
public, and `status()["http"]["public_hostname_configured"]` says which. Verifying that header's RS256 signature
against Cloudflare's JWKS needs an RSA primitive the standard library does
not have, and this package takes no new dependency for it (dependencies are
human-gated; see below). So the shipped verifier
(`refusing_assertion_verifier`) **always refuses** and records
`http-access-verifier-missing`: absence of real verification is a
host-visible, recorded state, never a silent hole. An operator who wants
Access enforced injects their own verifier through `GuardConfig`'s
`assertion_verifier` seam; a verifier that raises is itself treated as a
refusal (`http-refused-access-verifier-failed`) — fail closed, never fail
open.

Access is additive, not a replacement: whether or not a request carries a
valid Access assertion, the daemon's own install secret
(`Authorization: Bearer <secret>` or the `embodiment_secret` cookie) is still
required on every guarded route. A request that passes Access but has no
install secret is refused with `http-refused-secret`.

**Non-browser endpoints** (a future robot relay, a script) cannot complete
Cloudflare's browser login, so `--with-service-token` has
`cultureflare remote-login setup` mint an Access **service token** instead. A
service-token client presents it as two headers — `CF-Access-Client-Id` and
`CF-Access-Client-Secret` — in place of the interactive Access login; it still
needs the daemon's own install secret on top, same as a browser client.

## Inbound realtime endpoint — a browser-shaped, robot-shaped seam

`embodiment/audio/remote.py` is a `websockets` server that speaks the lobes
`/v1/realtime` wire **inbound**, implementing the same `AudioEndpoint`
protocol `embodiment/audio/host.py` implements for the machine's own
microphone and speaker. A client dials in, the WebSocket handshake completes
carrying no credential at all, and the FIRST message on the socket must be
`{"type": "auth", "secret": "..."}` — checked against an install secret (or a
per-endpoint secret) in constant time before any other message, audio
included, is ever processed. A wrong, missing, malformed or late first message
closes the socket (code 1008) and is recorded. The secret deliberately never
rides the connect URL — a URL lands in proxy access logs, browser history and
`Referer` headers, all of which this repo's privacy rules forbid for a
credential.

**v1 ships no robot support and wires no client to it.** The endpoint and the
browser-side `BrowserEar` are built and tested; the daemon composes only the
host endpoint today. What
ships is the *seam* — one `AudioEndpoint` implementation among several the
daemon can compose — so a browser ear or a robot relay speaking the same wire
is a future, separate step, arrived at without touching the daemon that
composes endpoints.

## The core underneath

| Module | What it is |
|--------|------------|
| `loop.py` | The bounded perceive → decide → act tool loop the turn runs on. Termination is proved *structurally* by AST tests |
| `turn.py`, `tools.py` | One spoken turn through `loop.run`, never silent, never raises; the tool registry (empty by default; the daemon binds `remember` and `forget`) |
| `session.py`, `memory.py` | The conversation (explicit-ask detector, turn queue, supersede on barge-in, summary on close) and `RoomMemory`: private, pinned, deadline-bounded |
| `realtime/`, `voice.py` | The typed lobes wire and the ears-only client; sentence-by-sentence TTS with paced waveform features |
| `audio/` | `AudioEndpoint` protocol, the host endpoint, the inbound endpoint, the feature extractor |
| `http/`, `bus.py` | Guard (secret + allow-list + Access seam), server (static dashboard, SSE, control API), the event bus with redaction at publish |
| `daemon/` | State dir, bounded logs, crash-durable ledger; lifecycle by `(pid, start time)`; the app that wires it all |
| `perception.py`, `identity.py`, `framing.py`, `senses_text.py` | The verbatim invariant; explicit identity; absent identity ⇒ byte-identical prompts; measured grounding text |
| `continuity.py`, `events.py`, `presence*.py`, `subagent.py`, `context.py`, `media.py`, `contract.py` | The eidetic/coherence seam, the MQTT observer, the presence pump, and what the loop needs |

## Dependencies — what installing this costs you

Dependencies are human-gated: `tests/test_zero_deps.py` pins the exact approved
set and fails on any change in either direction.

| Dependency | Pulls in |
|------------|----------|
| `eidetic-cli>=0.12` | `data-refinery-cli[store]` → neo4j + pymongo (installed, never imported at module scope) |
| `coherence-cli>=0.6` | numpy + httpx |
| `events-cli>=0.10` | paho-mqtt (imported lazily) |
| `websockets>=15` | the realtime client and the inbound endpoint |

Host audio runs through `pw-record`/`pw-play` (or `arecord`/`aplay`) as
subprocesses — no in-process audio library (deviation `d4`). `lobes-cli` is
reached over the network only and is forbidden as a dependency.

## CLI

```bash
embodiment start         # the daemon, detached; idempotent
embodiment status        # the daemon's state, truthfully; never starts anything
embodiment stop          # bounded stop; a SIGKILL is reported and recorded
embodiment tunnel        # print the remote-access commands; runs nothing
embodiment whoami        # identity from culture.yaml
embodiment learn         # structured self-teaching prompt
embodiment explain       # markdown docs; `explain <path>` for any verb
embodiment overview      # descriptive snapshot
embodiment doctor        # agent-identity invariants
embodiment cli overview  # the CLI surface
```

Every verb takes `--json`. Results go to stdout, errors and diagnostics to
stderr, never mixed. Exit codes: `0` success, `1` user error, `2` environment
error. No Python traceback ever reaches stderr.

## Development

```bash
uv sync
uv run pytest -n auto
uv run pytest --cov=embodiment --cov-report=term   # gate: 60%
uv run teken cli doctor . --strict                 # the agent-first rubric CI enforces
```

Lint stack (line length 100): `black`, `isort`, `flake8`, `bandit`,
`markdownlint-cli2`. Every PR bumps the version. The dashboard has its own
`vitest` suite under `web/`.

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
