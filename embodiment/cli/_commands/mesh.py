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
from embodiment.mesh_client import MeshConfig

__all__ = ["MESH_ENV", "build_report", "cmd_mesh", "register"]

MESH_ENV = (
    "EMBODIMENT_MESH",
    "EMBODIMENT_MESH_NICK",
    "EMBODIMENT_MESH_SERVER",
    "EMBODIMENT_MESH_CHANNELS",
)
REPO_AGENT_NICK = "spark-embodiment"

_STEPS = (
    "Connecting needs no registration: agentirc accepts any client whose nick starts "
    "with the server name, and Gwen's daemon holds the connection itself (no harness).",
    "Listing Gwen as a resident agent in culture is pending a culture-side change "
    "(an issue the operator will post). Until then: not yet supported.",
    "What the operator would run once it lands: not yet supported (no command is "
    "invented here).",
    "If ever registered, her socket would be /run/user/<uid>/culture-spark-gwen.sock; "
    "the repo's own agent is spark-embodiment (culture.yaml suffix embodiment).",
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
        "registered_resident": False,
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
