"""Markdown catalog for ``embodiment explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("embodiment",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

_ROOT = """\
# embodiment

A small, tested core for giving an app an embodied AI presence — a bounded
perceive → decide → act **loop**, a **presence** pump, a verbatim **perception**
seam, configured **identity**, and **continuity** through eidetic — that is being
rebuilt into a background realtime voice app. The experiments that used to live
here are archived in git history (tag `archive/pre-realtime-0.14.0`).

## Status, stated plainly

**The realtime app is planned, not built.** Nothing in this package listens,
speaks, runs as a daemon or serves a dashboard yet. What ships today is the core
listed below and this CLI's introspection verbs. The spec and the plan are in
`docs/specs/` and `docs/plans/` (2026-09-21, "realtime embodiment app").

The archived tiers (strategist, configuration lane, muse, drones) were each
proven as mechanisms and never as value; their own published verdicts said so.
The redesign makes no value claim either, until it has a measurement.

## Software presence, not a robot body

"Embodiment" is an overloaded word in this mesh: `reachy-mini-cli` owns the
physical robot (and has its own `agent embody` layer), `reachy-lobes` its local
brain. This package gives an *application* a loop and a presence — it does not
drive hardware, and nothing here claims a body. A later stage lets a robot act
as a relay for the voice app's ears and voice; that is a stated, planned seam,
never an implication drawn from the name.

## Key concepts

- **Loop** (`embodiment.loop`) — The bounded perceive → decide → act cycle.
  Handed a `complete` callable that performs one model turn, it drives until
  the model finishes, stops requesting tools, or the step budget is reached.
  Termination is proved structurally by AST tests, not only behaviourally.
  Delegation rides the `embodiment.subagent` seam, bounded by arithmetic.
- **Presence** (`embodiment.presence_engine`, `embodiment.presence`) — The pump
  that keeps an app feeling attended to between acts, and its pure policy half.
  No TTY, no thread, no clock: all IO rides injected callbacks.
- **Perception** (`embodiment.perception`) — The verbatim invariant: the user's
  words are taken from the caller's input, never from model output, and intake
  never raises.
- **Identity and Gwen** (`embodiment.identity`, `embodiment.framing`) — Explicit
  configuration that frames who is speaking. Gwen is the teammate this repo
  ships. Absent identity means byte-identical prompts. Role names are design
  metaphors for allocating responsibility across model seams, not claims about
  cognition, and a single-model run never claims a second mind exists.
- **Continuity** (`embodiment.continuity`) — Memory and coherence as runtime
  subsystems through eidetic-cli and coherence-cli, imported in-process. Every
  degradation is recorded; nothing degrades silently.
- **Events** (`embodiment.events`) — An optional observer that publishes loop
  and presence events onto events-cli's MQTT fabric; free when unused.

## Verbs

- `embodiment whoami` — identity probe from `culture.yaml`.
- `embodiment learn` — structured self-teaching prompt.
- `embodiment explain <path>` — markdown docs for any noun/verb.
- `embodiment overview` — descriptive snapshot of the agent.
- `embodiment doctor` — check the agent-identity invariants.
- `embodiment cli overview` — describe the CLI surface.
- `embodiment start` — start the daemon as a detached background process.
- `embodiment stop` — stop the running daemon within a bounded time.
- `embodiment status` — report the daemon's state, truthfully.
- `embodiment tunnel` — print the cultureflare/cloudflared commands for
  remote access (dry-run only; no `--apply`).

The lifecycle verbs are the daemon's, not the loop's: no verb drives
`embodiment.loop`. And `start` starts a *lifecycle*, not yet an application —
the daemon application it runs (`embodiment/daemon/app.py`) is still being
built, so `embodiment start` with the default target reports a clean
environment error naming it rather than pretending to come up.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `embodiment explain whoami`
- `embodiment explain doctor`
"""

_WHOAMI = """\
# embodiment whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    embodiment whoami
    embodiment whoami --json
"""

_LEARN = """\
# embodiment learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    embodiment learn
    embodiment learn --json
"""

_EXPLAIN = """\
# embodiment explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    embodiment explain embodiment
    embodiment explain whoami
    embodiment explain --json <path>
"""

_OVERVIEW = """\
# embodiment overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the template carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    embodiment overview
    embodiment overview --json
"""

_DOCTOR = """\
# embodiment doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`colleague` → `AGENTS.colleague.md`), plus a
skills-present check. Exits 1 when unhealthy.

## Usage

    embodiment doctor
    embodiment doctor --json
"""

_CLI = """\
# embodiment cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    embodiment cli overview
    embodiment cli overview --json
"""


_START = """\
# embodiment start

Starts the daemon as a detached background process and returns once the daemon
has reported *itself* running — not merely once a process exists.

**Idempotent.** A second `start` finds the first through an exclusive lock on
`daemon.pid` in the state directory, exits `0`, and starts nothing. The lock,
not the pid, is what proves liveness, so a reused pid can never make a dead
daemon look alive. A pidfile left by a dead process is reclaimed and the
reclamation is recorded as a degradation.

**A target that cannot be imported is a clean refusal.** `--target` defaults
to `embodiment.daemon.app:main`, the daemon application. If the named module
cannot be found, this verb exits `2` with a hint naming it — it never raises,
never spawns, and never leaves a half-made claim behind.

`--target` imports and runs the module it names with your own authority. It is
an entry-point selector, not a sandbox.

The child is detached: its own session, `stdin` from `/dev/null`, `stdout` and
`stderr` into `<state dir>/daemon.err` (0600) rather than your terminal, and
its working directory set to the private state directory so nothing it writes
can land in a git checkout.

## Usage

    embodiment start
    embodiment start --json
    embodiment start --target my.app:main --state-dir /tmp/gwen
"""

_STOP = """\
# embodiment stop

Stops the running daemon within a bounded time: `SIGTERM`, then at most
`--timeout` seconds waiting for the daemon lock to be released, then `SIGKILL`
and at most `--kill-grace` more. The whole bound is under five seconds by
default.

An escalation to `SIGKILL` is **reported and recorded**, in the result and in
the degradation ledger: a daemon that had to be killed did not shut down.

**It only ever signals the daemon.** Immediately before each signal it
re-proves that the pid it is about to use still holds the lock and still has
the process start time recorded for the daemon, so a pid the kernel recycled
to another process after the daemon exited is refused rather than signalled
(`identity_verified: false`, `lifecycle-stop-target-changed`). Where `/proc`
cannot answer, it degrades to the pid alone and says so
(`identity_verified: null`).

Stopping nothing is not an error — it exits `0` and says `not running`, so
`stop` is as safe to repeat as `start`. A stop that cannot be confirmed (the
process still holds the lock after `SIGKILL`) exits `2` rather than claiming
success.

Inside the daemon the same bound is enforced from the other end: once a stop is
requested, a watchdog writes whatever is unfinished to the ledger and then hard
-exits, so a thread parked in a blocking read cannot hold the process open.

## Usage

    embodiment stop
    embodiment stop --json
    embodiment stop --timeout 1.5
"""

_STATUS = """\
# embodiment status

Reports the daemon's state. Read-only: it never writes, never creates the state
directory, and never starts anything.

Four states:

- `running` — a process holds the daemon lock, and the line below says whether
  that process's `(pid, start time)` still matches what the daemon recorded.
  This means the process is alive; it is **not** evidence that anything was
  heard. The degradation count and the recent ledger entries printed beside it
  are.
- `stopped` — the daemon exited and said so, or nothing has ever run here.
- `dead (unclean)` — a pidfile still says "running" while nothing holds its
  lock. Reported with the last ledger entries, because the question after an
  unclean death is *why*.
- `state unavailable` — no state directory could be read, so a running daemon
  could not be seen from here. Reporting `stopped` instead would be a lie.

It looks in every candidate state directory, in the order a daemon would have
used them, so a daemon that fell back during bootstrap is still found. But that
search only ever *promotes* a live daemon: with nothing running, the headline is
the state of the directory you named, and whatever another candidate holds is
printed under it as `other state dir: …`. A pidfile left behind in the
machine-wide fallback directory is a note about that directory — it never makes
a healthy machine report `dead (unclean)`, and the next `start` reclaims it.

Always exits `0`: a stopped or dead daemon is a fact to report, not a failure
of the command.

## Usage

    embodiment status
    embodiment status --json
"""


_TUNNEL = """\
# embodiment tunnel

Prints the two commands an operator runs by hand for remote access — never
runs either one. Dry-run only: there is no `--apply` flag here at all (unlike
`lobes tunnel`, its model), because provisioning is the operator's act.

1. `cultureflare remote-login setup --hostname <h> --service
   http://127.0.0.1:<port> [--allow <email>]... [--with-service-token]
   [--tunnel-name <name>]` — the one-time provisioning command (tunnel + DNS +
   Cloudflare Access app). It only prints unless the operator adds `--apply`
   themselves; re-run `cultureflare remote-login setup ... --apply yourself`
   once you have read what it would do.
2. `cloudflared tunnel run <name>` — what the operator runs afterwards, once
   step 1 has actually been applied. `cloudflared` refuses a bare
   `cloudflared tunnel run` (no name, no `--token`), and `cultureflare`
   derives that name itself unless `--tunnel-name` overrides it — a rule this
   verb does not know and will not guess. Given `--tunnel-name`, both commands
   carry that exact name; omitted, step 2 prints `<tunnel-name-from-step-1>`
   and says to read the real name off step 1's own output.

Cloudflare Access protects only the public hostname; it never gates loopback
names. The daemon's own guard (`embodiment/http/guard.py`) separately
requires the install secret on every guarded request, Access or not. See
`README.md`'s "Remote access" section for how the daemon validates the
`Cf-Access-Jwt-Assertion` header and how a non-browser endpoint authenticates
with an Access service token instead of a browser session.

## Usage

    embodiment tunnel
    embodiment tunnel --json
    embodiment tunnel --hostname gwen.example.org --allow me@example.com
    embodiment tunnel --with-service-token
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("embodiment",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
    ("start",): _START,
    ("stop",): _STOP,
    ("status",): _STATUS,
    ("tunnel",): _TUNNEL,
}
