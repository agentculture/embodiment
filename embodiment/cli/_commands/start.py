"""``embodiment start`` — bring the daemon up as a detached background process.

The first verb in this CLI that *starts* anything. It is thin on purpose:
:mod:`embodiment.daemon.lifecycle` owns the mechanism and never raises, and
this module's only job is to turn a :class:`~embodiment.daemon.lifecycle.StartResult`
into the repo's error contract — ``CliError`` with a ``hint:``, results on
stdout, diagnostics on stderr, never a traceback.

Idempotent: starting a daemon that is already running exits ``0`` and starts
nothing, which is what makes ``start`` safe to put in a retry loop or a unit
file. A target module that cannot be imported — the default
(``embodiment/daemon/app.py``) or one given with ``--target`` — is a clean
environment error (exit ``2``) naming it, never a traceback and never a
half-made claim.

``--target`` imports and runs the module it names, with the invoking user's
full authority. It exists so a host can run its own daemon application (and so
the lifecycle can be tested against a fake one); it is not a sandbox and does
not pretend to be.

``--http-bind`` / ``--bind-public`` / ``--allowed-host`` configure where the
dashboard listens and which ``Host`` headers its guard accepts — for reviewing
the dashboard from a phone over a tailnet, say — and ``--public-hostname``
names the one Host the guard treats as public (behind a Cloudflare tunnel),
on which an Access assertion is required. The bind is checked *before*
anything is spawned, so a routable address without ``--bind-public`` is a
refusal here rather than a daemon that starts and then declines to serve. All
four reach the daemon through the environment
(:data:`~embodiment.daemon.app.ENV_HTTP_BIND` and its siblings), because
``start`` re-execs a fresh interpreter rather than forking this one.
"""

from __future__ import annotations

import argparse

from embodiment.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from embodiment.cli._output import emit_result
from embodiment.daemon import app as daemon_app
from embodiment.daemon import lifecycle
from embodiment.http import server as http_server

_HINTS = {
    lifecycle.TARGET_UNAVAILABLE_CODE: (
        "the named module could not be found on this interpreter's path; check "
        f"the spelling, or omit --target to run the default ({lifecycle.DEFAULT_TARGET})"
    ),
    lifecycle.TARGET_INVALID_CODE: (
        "--target must look like 'package.module:attribute', e.g. " f"'{lifecycle.DEFAULT_TARGET}'"
    ),
    lifecycle.CHILD_EXITED_EARLY_CODE: (
        "read the daemon's stderr log named above, then run 'embodiment status'"
    ),
    lifecycle.START_UNCONFIRMED_CODE: (
        "read the daemon's stderr log named above; 'embodiment stop' will clear a "
        "process that came up but never reported itself running"
    ),
    lifecycle.NO_STATE_DIR_CODE: (
        "make the state directory writable, or set EMBODIMENT_STATE_DIR to one that is"
    ),
    lifecycle.START_BUSY_CODE: (
        "another 'embodiment start' holds the lock right now; run 'embodiment status'"
    ),
}

_USER_ERROR_CODES = {lifecycle.TARGET_INVALID_CODE}


def _render(result: lifecycle.StartResult) -> str:
    if result.already_running:
        return f"embodiment start: already running (pid {result.pid})"
    lines = [f"embodiment start: running (pid {result.pid})"]
    if result.reclaimed_stale_pid is not None:
        lines.append(f"  note: reclaimed a stale pidfile left by pid {result.reclaimed_stale_pid}")
    lines.append(f"  state dir: {result.state_dir}")
    lines.append(f"  target: {result.target}")
    return "\n".join(lines)


def _http_env(args: argparse.Namespace) -> dict[str, str]:
    """The HTTP flags, as environment for the daemon child.

    ``start`` re-execs a fresh interpreter, so a flag parsed here reaches the
    daemon only through the environment. The bind is validated *here*, before
    anything is spawned: :func:`embodiment.http.server.resolve_bind` refuses a
    routable address without ``--bind-public`` and raises the ``CliError``
    this CLI already contracts for, which is a far better answer than a child
    that starts, refuses, and leaves the operator reading a log.
    """
    bind = args.http_bind
    http_server.resolve_bind(bind, bind_public=bool(args.bind_public))
    env = {
        daemon_app.ENV_HTTP_BIND: bind,
        daemon_app.ENV_BIND_PUBLIC: "1" if args.bind_public else "0",
    }
    hosts = daemon_app.parse_allowed_hosts(",".join(args.allowed_host or ()))
    if hosts:
        env[daemon_app.ENV_ALLOWED_HOSTS] = ",".join(hosts)
    public = (getattr(args, "public_hostname", None) or "").strip()
    if public:
        env[daemon_app.ENV_PUBLIC_HOSTNAME] = public
    return env


def cmd_start(args: argparse.Namespace) -> int:
    result = lifecycle.start(
        args.target,
        state_dir=args.state_dir,
        confirm_timeout=args.confirm_timeout,
        env=_http_env(args),
    )
    if not result.started and not result.already_running:
        code = EXIT_USER_ERROR if result.code in _USER_ERROR_CODES else EXIT_ENV_ERROR
        raise CliError(
            code=code,
            message=result.detail or "the daemon could not be started",
            remediation=_HINTS.get(result.code or "", "run 'embodiment status' for the details"),
        )
    json_mode = bool(getattr(args, "json", False))
    emit_result(result.to_dict() if json_mode else _render(result), json_mode=json_mode)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "start",
        help="Start the daemon as a detached background process (idempotent).",
    )
    p.add_argument(
        "--target",
        default=lifecycle.DEFAULT_TARGET,
        help=(
            "The daemon application to run, as 'package.module:attribute' "
            f"(default: {lifecycle.DEFAULT_TARGET}). It is imported and run with your "
            "own authority; there is no sandbox."
        ),
    )
    p.add_argument(
        "--state-dir",
        default=None,
        help="Override the state directory (default: EMBODIMENT_STATE_DIR, then XDG).",
    )
    p.add_argument(
        "--confirm-timeout",
        type=float,
        default=lifecycle.DEFAULT_START_CONFIRM_TIMEOUT,
        help="Seconds to wait for the daemon to report itself running.",
    )
    p.add_argument(
        "--http-bind",
        default="127.0.0.1",
        help=(
            "Address the dashboard and control API listen on (default: 127.0.0.1). "
            "Anything that is not a loopback address also requires --bind-public."
        ),
    )
    p.add_argument(
        "--bind-public",
        action="store_true",
        help=(
            "Accept that a non-loopback bind puts the dashboard, the event stream "
            "(which carries the transcript) and the control API on the network, "
            "behind the install secret and the Host/Origin allow-list."
        ),
    )
    p.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="HOST[:PORT]",
        help=(
            "A Host header the guard accepts beyond loopback, e.g. a tailnet "
            "address or name. Repeatable. Each is also accepted as an Origin "
            "with the plain-http scheme so the dashboard's own requests pass."
        ),
    )
    p.add_argument(
        "--public-hostname",
        default=None,
        metavar="HOSTNAME",
        help=(
            "The one Host the guard treats as public (a Cloudflare tunnel hostname). "
            "Requests for it must carry a Cloudflare Access assertion, and the shipped "
            "verifier refuses every one (http-access-verifier-missing) until an RS256 "
            "dependency is approved. Unset, no Host is public and nothing is asked."
        ),
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_start)
