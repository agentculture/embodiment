"""``embodiment tunnel`` — print the cultureflare/cloudflared commands, nothing else.

Dry-run **only**: unlike ``lobes tunnel`` (its model, in the sibling
``lobes-cli`` checkout), this verb has no ``--apply`` and no mutating branch at
all. Provisioning the tunnel — creating the Cloudflare Access app, the DNS
record, the service token — is the operator's act, run by hand from the
printed command. This module composes two argv lists and a documentation
string; it never imports ``subprocess`` and never calls ``os.system`` or any
``os.exec*``/``os.spawn*`` function, so there is no code path here that could
ever spawn ``cultureflare`` or ``cloudflared``.

A printed command is only honest if a paste of it does what it says. Round 3
closed a MAJOR found in review: ``--hostname``/``--allow``/``--tunnel-name``
values were joined into the printed line with a bare ``" ".join`` and no
charset check, so ``--tunnel-name 'a; touch pwned'`` printed a second shell
command after a semicolon. Two independent layers now hold, on purpose:
``--hostname`` (RFC-1123 labels), ``--allow`` (one ``@``, no whitespace) and
``--tunnel-name`` ([A-Za-z0-9._-]) are rejected at argument-parsing time
(:func:`_hostname_type`, :func:`_email_type`, :func:`_tunnel_name_type`) if
they fall outside a safe charset — the CLI's own structured error contract
(``error:``/``hint:``, exit 1) refuses them before a :class:`TunnelPlan` ever
exists; and every token in a *rendered* command line is
``shlex.quote``-d (:func:`_quoted_line`) regardless, so a value that ever
reached :func:`build_plan`/:func:`_render` by some other path than the
registered argparse flags — a direct caller of this module, for instance —
still prints as inert quoted text rather than a second command. The two
argv-list fields on :class:`TunnelPlan` (``setup_command``/``run_command``)
stay unquoted: they are structured data for a JSON consumer, not a shell
string, and quoting them would corrupt the literal argv a script wants back.

What it prints
--------------
1. ``cultureflare remote-login setup --hostname <h> --service
   http://127.0.0.1:<port> [--allow <email>]... [--with-service-token]
   [--tunnel-name <name>]`` — the one-time provisioning command. The port
   defaults to :data:`embodiment.http.server.DEFAULT_PORT` (imported, never
   restated as a literal here).
2. ``cloudflared tunnel run <name>`` — what the operator runs afterwards, once
   step 1 has actually been applied. ``cloudflared`` refuses a bare
   ``cloudflared tunnel run`` with no name (or ``--token``), and
   ``cultureflare`` derives that name itself unless ``--tunnel-name``
   overrides it — a rule this module does not know and will not guess. So
   with ``--tunnel-name`` given, that exact name is used in *both* commands;
   without it, step 2 prints :data:`TUNNEL_NAME_PLACEHOLDER` and the text
   output says, in one line, that the real value is whatever step 1 printed.

See ``README.md``'s "Remote access" section for what Cloudflare Access
protects (only the public hostname) and how the daemon
(:mod:`embodiment.http.guard`) validates the assertion.
"""

from __future__ import annotations

import argparse
import re
import shlex
from dataclasses import dataclass
from typing import Optional

from embodiment.cli._output import emit_result
from embodiment.http.server import DEFAULT_PORT

__all__ = [
    "DEFAULT_HOSTNAME",
    "TUNNEL_NAME_PLACEHOLDER",
    "TunnelPlan",
    "build_plan",
    "cmd_tunnel",
    "register",
]

#: Default public hostname documented throughout this repo's docs. A
#: **judgement call**: any real deployment overrides it with ``--hostname``.
DEFAULT_HOSTNAME = "agent.culture.dev"

#: Printed in place of the tunnel name when ``--tunnel-name`` is not given.
#: ``cultureflare remote-login setup`` derives the tunnel name itself from
#: rules this module does not own and must not guess at; the operator reads
#: the real name off step 1's own output and substitutes it here before
#: running step 2. Angle-bracketed so it reads as a placeholder, not a literal
#: cloudflared tunnel name.
TUNNEL_NAME_PLACEHOLDER = "<tunnel-name-from-step-1>"

# ── input validation (round 3) ───────────────────────────────────────────────
# Charsets are deliberately narrow: every character a real hostname, email or
# cloudflared tunnel name needs, and nothing a shell would ever treat
# specially. This is the FIRST of two independent layers — see the module
# docstring; :func:`_quoted_line` is the second and holds even if a value ever
# reaches :func:`build_plan` some other way.

#: One RFC-1123 label: letters/digits, interior hyphens only, 1-63 chars.
_HOSTNAME_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME_RE = re.compile(rf"^{_HOSTNAME_LABEL}(?:\.{_HOSTNAME_LABEL})*$")
#: RFC 1035/1123 caps the whole name at 253 octets.
_MAX_HOSTNAME_LEN = 253

#: Deliberately narrower than RFC 5322: one ``@``, no whitespace, a
#: hostname-shaped domain. Good enough to keep a pasted command inert; this is
#: not an email-deliverability validator.
_EMAIL_RE = re.compile(rf"^[A-Za-z0-9][A-Za-z0-9._%+-]*@{_HOSTNAME_LABEL}(?:\.{_HOSTNAME_LABEL})+$")
_MAX_EMAIL_LEN = 254

#: cultureflare's own tunnel-name charset — letters, digits, dot, underscore,
#: hyphen. No spaces, no shell metacharacters, nothing quoting has to fight.
_TUNNEL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
#: A **judgement call**: generous headroom over any real tunnel name, bounding
#: an absurd value rather than picking a number cultureflare itself documents.
_MAX_TUNNEL_NAME_LEN = 255


def _hostname_type(value: str) -> str:
    """argparse ``type=`` for ``--hostname``: RFC-1123 labels only."""
    if not value or len(value) > _MAX_HOSTNAME_LEN or not _HOSTNAME_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"not a valid hostname (RFC-1123 labels: letters, digits, hyphens, dots "
            f"only, max {_MAX_HOSTNAME_LEN} chars): {value!r}"
        )
    return value


def _email_type(value: str) -> str:
    """argparse ``type=`` for ``--allow``: exactly one ``@``, no whitespace."""
    if not value or len(value) > _MAX_EMAIL_LEN or not _EMAIL_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"not a valid --allow email (exactly one '@', no whitespace, a "
            f"hostname-shaped domain, max {_MAX_EMAIL_LEN} chars): {value!r}"
        )
    return value


def _tunnel_name_type(value: str) -> str:
    """argparse ``type=`` for ``--tunnel-name``: cultureflare's own charset."""
    if not value or len(value) > _MAX_TUNNEL_NAME_LEN or not _TUNNEL_NAME_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"not a valid --tunnel-name (letters, digits, '.', '_', '-' only, max "
            f"{_MAX_TUNNEL_NAME_LEN} chars): {value!r}"
        )
    return value


def _quoted_line(command: tuple[str, ...]) -> str:
    """A command line safe to paste: every token individually ``shlex.quote``-d.

    The second, independent defence (see the module docstring): holds even for
    a value that reached :func:`build_plan` by some path other than the
    registered, charset-validated argparse flags.
    """
    return " ".join(shlex.quote(part) for part in command)


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
    tunnel_name: Optional[str]
    setup_command: tuple[str, ...]
    run_command: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "hostname": self.hostname,
            "port": self.port,
            "service_url": self.service_url,
            "allow": list(self.allow),
            "with_service_token": self.with_service_token,
            "tunnel_name": self.tunnel_name,
            "setup_command": list(self.setup_command),
            "run_command": list(self.run_command),
        }


def build_plan(
    *,
    hostname: str,
    port: int,
    allow: tuple[str, ...],
    with_service_token: bool,
    tunnel_name: Optional[str] = None,
) -> TunnelPlan:
    """Compose the two command lines. Pure: builds strings, runs nothing.

    ``tunnel_name`` is never derived here — only echoed back when the caller
    supplies it. Absent, step 2 carries :data:`TUNNEL_NAME_PLACEHOLDER`
    instead of a guessed name, because ``cultureflare``'s derivation rule is
    not this module's to reimplement.
    """
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
    if tunnel_name:
        setup.extend(["--tunnel-name", tunnel_name])
    run = ["cloudflared", "tunnel", "run", tunnel_name if tunnel_name else TUNNEL_NAME_PLACEHOLDER]
    return TunnelPlan(
        hostname=hostname,
        port=port,
        service_url=service_url,
        allow=allow,
        with_service_token=with_service_token,
        tunnel_name=tunnel_name,
        setup_command=tuple(setup),
        run_command=tuple(run),
    )


def _render(plan: TunnelPlan) -> str:
    lines = [
        "embodiment tunnel: DRY RUN — nothing was run. These are the commands to run",
        "yourself; provisioning is the operator's act, never this verb's.",
        "",
        "1) one-time provisioning (tunnel + DNS + Cloudflare Access app):",
        "     " + _quoted_line(plan.setup_command),
        "   this only PRINTS by default; re-run cultureflare remote-login setup ... "
        "--apply yourself once you have read what it would do.",
        "",
        "2) run the tunnel, after step 1 has actually been applied:",
        "     " + _quoted_line(plan.run_command),
    ]
    if plan.tunnel_name is None:
        lines.append(
            f"   {TUNNEL_NAME_PLACEHOLDER} is not a real name: cultureflare derives the "
            "tunnel name itself in step 1 unless --tunnel-name overrides it, so read the "
            "actual name off step 1's own output and substitute it there."
        )
    lines += [
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
        tunnel_name=args.tunnel_name,
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
        type=_hostname_type,
        default=DEFAULT_HOSTNAME,
        help=(
            f"Public hostname to provision (default: {DEFAULT_HOSTNAME}). "
            "RFC-1123 labels only (letters, digits, hyphens, dots)."
        ),
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
        type=_email_type,
        default=None,
        metavar="EMAIL",
        help="Email to allow via Cloudflare Access (repeatable). Exactly one '@', no whitespace.",
    )
    p.add_argument(
        "--tunnel-name",
        type=_tunnel_name_type,
        default=None,
        metavar="NAME",
        help=(
            "Tunnel name for both commands (cultureflare's own --tunnel-name; letters, "
            "digits, '.', '_', '-' only). Omit it and step 2 prints "
            f"{TUNNEL_NAME_PLACEHOLDER} — cultureflare derives the name itself in step 1, "
            "and this verb will not guess it."
        ),
    )
    p.add_argument(
        "--with-service-token",
        action="store_true",
        help="Also print --with-service-token, for a non-browser endpoint (e.g. a robot relay).",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_tunnel)
