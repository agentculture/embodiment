"""Resolved-identity seam — how a consuming rig configures a teammate identity.

This module answers a *different* question than
``embodiment.cli._commands.whoami``. ``whoami``/``doctor`` report **this
repo's own** mesh identity (``culture.yaml`` alongside this package, located
by walking up from ``__file__``). ``resolve_identity`` in this module instead
resolves the identity of an **arbitrary consuming rig** — e.g. the reference
"Gwen" teammate identity a host application configures for itself — given an
explicit ``repo_path``. The two are never conflated: a consuming rig's
configuration can never change what ``embodiment whoami``/``embodiment
doctor`` report about this repo (see
``docs/specs/2026-07-24-gwen-loop-presence-continuity.md`` and
``tests/test_identity.py``).

Mirrors colleague's ``colleague/identity.py`` resolution order exactly
(stdlib-only, no PyYAML, no import of colleague):

1. ``culture.yaml`` at the repo root — a top-level ``nick:`` field, else the
   first agent block's ``suffix:`` field (the canonical AgentCulture template
   shape: ``agents:\\n- suffix: ...``). Parsed with a minimal line-scan.
2. (reserved insertion point for a future sub-identity source — see below)
3. ``.colleague/identity.json``'s ``"as"`` key — repo-level before
   user-home, matching colleague's own config-dir precedence.

Returns ``None`` when nothing resolves.

# Reserved insertion point (mirrors colleague/identity.py:22's TODO):
# a future sub-identity resolution source would slot in HERE, between
# source 1 (culture.yaml) and source 2 (.colleague/identity.json). No such
# source exists yet in embodiment or colleague; nothing to resolve here today.

**The load-bearing invariant**: identity is configuration, never inference.
This module never reads a ``model:`` field, never inspects a served model
name, and never derives an identity from one. "Gwen" (G-from-Gemma +
wen-from-Qwen) is a name a consuming rig configures explicitly; the code must
never notice that etymology on its own. Swap the model name and the resolved
identity is unaffected — see
``test_resolve_identity_unaffected_by_model_name_change`` in
``tests/test_identity.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_IDENTITY_JSON_DIRNAME = ".colleague"
_NICK_PREFIX = "nick:"


def resolve_identity(
    repo_path: str | Path,
    *,
    user_home: str | Path | None = None,
) -> str | None:
    """Resolve the configured identity for *repo_path*.

    Resolution order (first non-empty value wins):

    1. ``culture.yaml`` at *repo_path* — top-level ``nick:``, else the first
       agent block's ``suffix:``.
    2. ``.colleague/identity.json`` (repo-level, then user-home) — ``"as"``.

    Never reads or considers a ``model:`` field anywhere. Never raises:
    a missing/unreadable/malformed source is treated as absent and the next
    source is tried.

    Args:
        repo_path: Path to the consuming rig's repo directory.
        user_home: (test fixture) path to the user's home directory; defaults
            to ``Path.home()``.

    Returns:
        The resolved identity string, or ``None`` when nothing resolves.
    """
    repo_path = Path(repo_path)

    # --- Source 1: culture.yaml — top-level nick:, else first agent suffix: ---
    # Read once, scan the buffer for both keys, so the suffix fallback never
    # re-reads the same file.
    culture_yaml = _read_culture_yaml(repo_path)
    if culture_yaml is not None:
        nick = scan_top_level_nick(culture_yaml)
        if nick:
            return nick
        suffix = scan_first_agent_suffix(culture_yaml)
        if suffix:
            return suffix

    # --- (reserved insertion point — see module docstring) ---

    # --- Source 2: .colleague/identity.json (repo then user) ---
    as_name = _read_identity_json(repo_path, user_home)
    if as_name:
        return as_name

    return None


def scalar_value(line: str, key: str) -> str | None:
    """Extract and normalize the scalar value after ``key:`` in a raw config line.

    Strips surrounding whitespace and a single layer of matching quotes.
    This is the one shared low-level primitive behind every ``culture.yaml``
    scalar read in the package — ``embodiment.cli._commands.whoami`` reuses
    it too, so there is exactly one implementation of "read a YAML-ish
    scalar" rather than a third, independent one growing here.

    Args:
        line: The raw (or already-stripped) line containing ``key:``.
        key: The key whose value to extract (e.g. ``"nick"``, ``"suffix"``).

    Returns:
        The normalized value, or ``None`` if absent/blank.
    """
    _, _, value = line.partition(f"{key}:")
    value = value.strip().strip("'\"")
    return value or None


def scan_top_level_nick(content: str) -> str | None:
    """Scan ``culture.yaml`` text for a top-level ``nick:`` value.

    Only an un-indented ``nick: <value>`` line counts — a nested ``nick:``
    (e.g. under ``agents:``) is not the document's nick and is skipped. YAML
    block scalars, anchors, and flow mappings are outside scope and treated
    as absent.

    Args:
        content: The culture.yaml text.

    Returns:
        The nick string if found and non-empty, otherwise ``None``.
    """
    for line in content.splitlines():
        if line != line.lstrip():
            continue  # indented — nested under some other key, not top-level
        if line.startswith(_NICK_PREFIX):
            return scalar_value(line, "nick")
    return None


def scan_first_agent_suffix(content: str) -> str | None:
    """Scan ``culture.yaml`` text for the first agent block's ``suffix:``.

    The canonical AgentCulture template nests the nick as a ``suffix:``
    under an ``agents:`` list rather than a top-level ``nick:``::

        agents:
        - suffix: gwen
          backend: colleague

    Matches a ``suffix:`` key whether bare or the first key of a list item
    (``- suffix: ...``); takes the FIRST match only (the first agent block).

    Args:
        content: The culture.yaml text.

    Returns:
        The first agent's suffix if found and non-empty, otherwise ``None``.
    """
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- suffix:", "suffix:")):
            return scalar_value(stripped, "suffix")
    return None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _read_culture_yaml(repo_path: Path) -> str | None:
    """Read the repo-root ``culture.yaml`` once, returning its text.

    Returns ``None`` if the file is absent or unreadable — never raises.
    """
    culture_yaml = repo_path / "culture.yaml"
    if not culture_yaml.is_file():
        return None
    try:
        return culture_yaml.read_text(encoding="utf-8")
    except OSError:
        return None


def _identity_json_roots(repo_path: Path, user_home: Path) -> list[Path]:
    """Existing ``.colleague`` config directories, repo before user-home."""
    roots = []
    repo_dir = repo_path / _IDENTITY_JSON_DIRNAME
    if repo_dir.is_dir():
        roots.append(repo_dir)
    user_dir = user_home / _IDENTITY_JSON_DIRNAME
    if user_dir.is_dir():
        roots.append(user_dir)
    return roots


def _read_identity_json(repo_path: Path, user_home: str | Path | None) -> str | None:
    """Read the ``"as"`` field from ``identity.json`` across the config roots.

    Repo-level ``.colleague/identity.json`` shadows user-level
    ``~/.colleague/identity.json``. Returns the first non-empty ``"as"``
    value found, or ``None``.
    """
    home = Path(user_home) if user_home is not None else Path.home()
    for root in _identity_json_roots(repo_path, home):
        value = _parse_identity_json_as(root / "identity.json")
        if value:
            return value
    return None


def _parse_identity_json_as(path: Path) -> str | None:
    """Parse the ``"as"`` key from *path* if it exists and is readable.

    Never raises: a missing file, unreadable file, or malformed JSON all
    degrade to ``None``.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    value = data.get("as", "")
    return str(value).strip() if value else None
