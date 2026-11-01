"""``embodiment mesh`` — print Gwen's culture-mesh identity and setup, run nothing.

Prints the nick, server, channel allow-list, env names and the registration
steps. Imports no process, socket or OS API: there is no code path here that
could spawn ``culture`` or connect anywhere.

Gwen (``spark-gwen``) is a different identity from this repo's own agent
(``spark-embodiment``, ``culture.yaml`` suffix ``embodiment``): they share no
nick, no socket and no ``culture.yaml`` entry.
"""

from __future__ import annotations

import argparse

from embodiment.cli._output import emit_result
from embodiment.mesh_client import ENV_AGENT_SECRET, MeshConfig

__all__ = ["MESH_ENV", "build_report", "cmd_mesh", "register"]

MESH_ENV = (
    "EMBODIMENT_MESH",
    "EMBODIMENT_MESH_NICK",
    "EMBODIMENT_MESH_SERVER",
    "EMBODIMENT_MESH_CHANNELS",
)
REPO_AGENT_NICK = "spark-embodiment"
#: The systemd user unit culture names as spark-gwen's owner (culture#506).
OWNER_UNIT = "embodiment-gwen.service"

_STEPS = (
    "Connecting: Gwen's daemon holds its own agentirc connection (no harness). Without an "
    "account any client whose nick starts with the server name may register.",
    "Residency (culture#506): culture lists spark-gwen as an external resident owned by "
    f"{OWNER_UNIT}; she shows up from the PRESENCE her client publishes (MODE +A, "
    "idle/thinking/draining). Check with: culture agents status",
    "Account, the operator's acts (embodiment#121 part 2): 1. culture agents account "
    "activate spark-gwen (prints a grant secret NAME, publishes nothing). 2. In "
    f"{OWNER_UNIT}, wrap ExecStart as: grant run --inject {ENV_AGENT_SECRET}=<that name> "
    "-- <daemon> (do not restart yet). 3. culture agents account activate spark-gwen "
    "--owner-ready, then restart the unit.",
    f"The daemon reads {ENV_AGENT_SECRET} once, removes it from its environment and logs "
    "in with SASL PLAIN; a refused login stops only the mesh lane (status.mesh.sasl).",
    "The repo's own agent is spark-embodiment (culture.yaml suffix embodiment); the two "
    "share no nick, socket or culture.yaml entry.",
)


def build_report() -> dict[str, object]:
    # The daemon's own reading of the env (review of #117: a second parser here had
    # drifted -- 'false' was on, invalid channels were listed). Building it connects
    # nothing: MeshConfig is data.
    config = MeshConfig.from_env()
    return {
        "enabled": config.enabled,
        "nick": config.nick,
        "server": f"{config.host}:{config.port}",
        "channels": list(config.channels),
        "error": config.error,
        "env": list(MESH_ENV),
        "account_env": ENV_AGENT_SECRET,
        "owner_unit": OWNER_UNIT,
        "repo_agent_nick": REPO_AGENT_NICK,
        "registration_steps": list(_STEPS),
    }


def _render(r: dict[str, object]) -> str:
    lines = [
        "embodiment mesh: nothing was run. This describes Gwen's mesh identity.",
        "",
        f"nick:     {r['nick']}  (enabled: {'yes' if r['enabled'] else 'no'})",
        f"server:   {r['server']}",
        f"channels: {', '.join(r['channels'])}",  # type: ignore[arg-type]
        "",
        "env names (names only):",
        *(f"  {n}" for n in MESH_ENV),
        f"  {ENV_AGENT_SECRET}  (the account secret: injected by grant, read once)",
        "",
        "registration steps:",
        *(f"  {i}. {s}" for i, s in enumerate(_STEPS, 1)),
    ]
    return "\n".join(lines)


def cmd_mesh(args: argparse.Namespace) -> int:
    report = build_report()
    json_mode = bool(getattr(args, "json", False))
    emit_result(report if json_mode else _render(report), json_mode=json_mode)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "mesh",
        help="Print Gwen's culture-mesh nick, channels, env names and setup (prints only).",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_mesh)
