"""``embodiment tunnel`` — print the cultureflare/cloudflared commands, nothing else.

Dry-run **only**: unlike ``lobes tunnel`` (its model, in the sibling
``lobes-cli`` checkout), this verb has no ``--apply`` and no mutating branch at
all. Provisioning the tunnel — creating the Cloudflare Access app, the DNS
record, the service token — is the operator's act, run by hand from the
printed command. This module composes two argv lists and a documentation
string; it never imports ``subprocess`` and never calls ``os.system`` or any
``os.exec*``/``os.spawn*`` function, so there is no code path here that could
ever spawn ``cultureflare`` or ``cloudflared``.

What it prints
--------------
1. ``cultureflare remote-login setup --hostname <h> --service
   http://127.0.0.1:<port> [--allow <email>]... [--with-service-token]`` — the
   one-time provisioning command. The port defaults to
   :data:`embodiment.http.server.DEFAULT_PORT` (imported, never restated as a
   literal here).
2. ``cloudflared tunnel run`` — what the operator runs afterwards, once step 1
   has actually been applied.

See ``README.md``'s "Remote access" section for what Cloudflare Access
protects (only the public hostname) and how the daemon
(:mod:`embodiment.http.guard`) validates the assertion.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from embodiment.cli._output import emit_result
from embodiment.http.server import DEFAULT_PORT

__all__ = [
    "DEFAULT_HOSTNAME",
    "TunnelPlan",
    "build_plan",
    "cmd_tunnel",
    "register",
]

#: Default public hostname documented throughout this repo's docs. A
#: **judgement call**: any real deployment overrides it with ``--hostname``.
DEFAULT_HOSTNAME = "agent.culture.dev"


@dataclass(frozen=True)
class TunnelPlan:
    """The two commands an operator runs by hand, and what they mean.

    Nothing in this module executes either one — printing is the only thing a
    :class:`TunnelPlan` is for.
    """

    hostname: str
    port: int
    service_url: str
    allow: tuple[str, ...]
    with_service_token: bool
    setup_command: tuple[str, ...]
    run_command: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "hostname": self.hostname,
            "port": self.port,
            "service_url": self.service_url,
            "allow": list(self.allow),
            "with_service_token": self.with_service_token,
            "setup_command": list(self.setup_command),
            "run_command": list(self.run_command),
        }


def build_plan(
    *,
    hostname: str,
    port: int,
    allow: tuple[str, ...],
    with_service_token: bool,
) -> TunnelPlan:
    """Compose the two command lines. Pure: builds strings, runs nothing."""
    service_url = f"http://127.0.0.1:{port}"
    setup = [
        "cultureflare",
        "remote-login",
        "setup",
        "--hostname",
        hostname,
        "--service",
        service_url,
    ]
    for email in allow:
        setup.extend(["--allow", email])
    if with_service_token:
        setup.append("--with-service-token")
    run = ["cloudflared", "tunnel", "run"]
    return TunnelPlan(
        hostname=hostname,
        port=port,
        service_url=service_url,
        allow=allow,
        with_service_token=with_service_token,
        setup_command=tuple(setup),
        run_command=tuple(run),
    )


def _render(plan: TunnelPlan) -> str:
    lines = [
        "embodiment tunnel: DRY RUN — nothing was run. These are the commands to run",
        "yourself; provisioning is the operator's act, never this verb's.",
        "",
        "1) one-time provisioning (tunnel + DNS + Cloudflare Access app):",
        "     " + " ".join(plan.setup_command),
        "   this only PRINTS by default; re-run cultureflare remote-login setup ... "
        "--apply yourself once you have read what it would do.",
        "",
        "2) run the tunnel, after step 1 has actually been applied:",
        "     " + " ".join(plan.run_command),
        "",
        f"Cloudflare Access protects only the public hostname ({plan.hostname}); it "
        "never gates loopback names. The daemon's own guard "
        "(embodiment/http/guard.py) separately requires the install secret on every "
        "guarded request, Access or not. See README.md's 'Remote access' section for "
        "how the daemon validates the Cf-Access-Jwt-Assertion header and how a "
        "non-browser endpoint (a robot relay) authenticates with an Access service "
        "token instead of a browser session.",
    ]
    return "\n".join(lines)


def cmd_tunnel(args: argparse.Namespace) -> int:
    allow = tuple(args.allow) if args.allow else ()
    plan = build_plan(
        hostname=args.hostname,
        port=args.port,
        allow=allow,
        with_service_token=bool(args.with_service_token),
    )
    json_mode = bool(getattr(args, "json", False))
    emit_result(plan.to_dict() if json_mode else _render(plan), json_mode=json_mode)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "tunnel",
        help=(
            "Print the cultureflare/cloudflared commands for remote access "
            "(dry-run only; no --apply exists)."
        ),
    )
    p.add_argument(
        "--hostname",
        default=DEFAULT_HOSTNAME,
        help=f"Public hostname to provision (default: {DEFAULT_HOSTNAME}).",
    )
    p.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Local daemon HTTP port to route to (default: {DEFAULT_PORT}).",
    )
    p.add_argument(
        "--allow",
        action="append",
        default=None,
        metavar="EMAIL",
        help="Email to allow via Cloudflare Access (repeatable).",
    )
    p.add_argument(
        "--with-service-token",
        action="store_true",
        help="Also print --with-service-token, for a non-browser endpoint (e.g. a robot relay).",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_tunnel)
