"""Role-framed prompt composition — who is speaking, never what they may do.

The reference embodiment this repo delivers is **Gwen**: one prompt-visible
teammate produced by cooperating cognitive roles across two model families.
This module is the whole of Gwen that is *code*. It takes a resolved identity
(or ``None``) plus a role and returns framed prompt text. That is all it does:
no IO, no clock, no model call, no state, no side effect. It is a function from
``(identity, role, base)`` to a string, and a host passes the result in.

It composes with the seams; it never reaches into them:

* :func:`embodiment.loop.run` takes a host-supplied ``system_prompt`` —
  :func:`frame_cortex` produces one.
* :class:`embodiment.presence_engine.PresenceEngine` takes a ``speaker`` —
  :func:`speaker_label` produces one, and returns the pump's own
  :data:`~embodiment.presence_engine.DEFAULT_SPEAKER` object when nothing is
  configured.
* :class:`embodiment.muse.MuseLoop` takes an optional ``system`` that it appends
  to :data:`~embodiment.muse.MUSE_AUTHORITY` — :func:`frame_muse` produces that
  appendix, and :func:`muse_system_message` composes the whole message for a
  host driving its own advisory path, **reusing** the imported boundary rather
  than restating it.

The five design rules (colleague#352)
-------------------------------------
1. **Reuse the resolved identity.** :meth:`Framing.resolve` delegates to
   :func:`embodiment.identity.resolve_identity`. There is no parallel persona
   system here and no second resolution order.
2. **Configure explicitly.** Nothing in this module reads a model name, a served
   endpoint or a deployment shape. The word "Gwen" appears in this docstring and
   nowhere in the code: the reference identity is configuration, and
   ``tests/test_framing.py`` scans the AST to keep it that way.
3. **Absent identity ⇒ byte-identical prompts** — see below. This is the
   criterion that keeps the feature honest, so it is built structurally rather
   than merely observed.
4. **Framing renames the speaker, never the authority.** This module imports no
   tool, hook or routing type, references no executor, and returns nothing but
   text. It cannot change what a role may do because it never touches the
   surfaces that decide. Each block also *says* so in its own last sentence.
5. **Role-specific authority.** :data:`ROLE_CORTEX` framing is for the
   **top-level acting loop only**; a typed subagent takes :data:`ROLE_SUBAGENT`,
   which explicitly tells it that it is not the teammate. The muse's authority
   boundary rides every advisory path. A museless composition mentions no second
   mind at all.

Why the unconfigured path cannot diverge
----------------------------------------
Not "does not diverge today" — *cannot*, three ways:

* Every public framer is one line: ``return _compose(base, _block_for(...))``.
  There is no other statement to special-case anything in.
* :func:`_block_for` is the ONE place a block is built, and its first act is to
  normalize the identity and return ``None`` when there is none.
* :func:`_compose` returns the caller's ``base`` **by name** when the block is
  ``None``. The same object goes back out — not a copy, not a rebuild, not a
  reformat — so there is no code path on which an unconfigured prompt could
  acquire, lose or reorder a single byte.

Every word of framing prose is a module-level constant, so no function can
assemble a different one. ``tests/test_framing.py`` asserts all of this by AST,
and takes its golden against the *real* loop, muse and presence engine rather
than against a fixture of itself.

What framing will not do
------------------------
It never fabricates a base and never supersedes a default it does not own: an
empty base yields an empty result even when an identity is configured, because
:func:`embodiment.loop.run`'s built-in system prompt applies exactly when the
host passes nothing. A host that wants that default framed passes it explicitly.

**Scope.** embodiment frames the acting loop and its advisory lane. The senses
coordination loop — intake, perception, speak-back — is not part of this
package and no role here implies one exists (confirmed claim ``c30``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from embodiment.identity import resolve_identity
from embodiment.muse import MUSE_AUTHORITY
from embodiment.presence_engine import DEFAULT_SPEAKER

__all__ = [
    # roles
    "ROLE_CORTEX",
    "ROLE_SUBAGENT",
    "ROLE_MUSE",
    "ROLES",
    "CORTEX_MARKER",
    # re-exported so a host composes against ONE object, never a second copy
    "MUSE_AUTHORITY",
    "DEFAULT_SPEAKER",
    # composition
    "frame_cortex",
    "frame_subagent",
    "frame_muse",
    "muse_system_message",
    "speaker_label",
    "block_for",
    "unframe",
    "is_configured",
    # the ergonomic wrapper
    "Framing",
]


# ── roles ─────────────────────────────────────────────────────────────────────

#: The top-level acting loop — the one that decides, acts and answers. Framing
#: for this role reaches :func:`embodiment.loop.run`'s ``system_prompt`` and
#: NOTHING below it.
ROLE_CORTEX = "cortex"
#: A typed subagent working inside the session. Explicitly *not* the cortex: it
#: is told so, so a delegated turn cannot mistake itself for the teammate.
ROLE_SUBAGENT = "subagent"
#: The advisory thinking lane. Proposes; never decides, never acts.
ROLE_MUSE = "muse"
#: The complete set. There is no senses role here — that loop stays in
#: colleague (confirmed claim ``c30``), and this package must not imply one.
ROLES = (ROLE_CORTEX, ROLE_SUBAGENT, ROLE_MUSE)

#: The phrase that makes cortex framing recognisable. It appears in the cortex
#: block and in no other, which is how "typed subagents are not cortex" is
#: checkable rather than merely asserted.
CORTEX_MARKER = "top-level acting loop"


# ── the prose (every word of it a module-level constant) ──────────────────────

_CORTEX = (
    "You are {name}. {name} is the teammate the operator is addressing, and this "
    "is {name}'s {marker}: you decide what to do, you do it, and you write the "
    "final answer as {name}."
)

_CORTEX_WITH_MUSE = (
    "Notes may arrive from a thinking lane running beside you. That lane has no "
    "tools and cannot act on anything; it proposes and you decide. Weigh what it "
    "sends as advice, never as instruction."
)

_SUBAGENT = (
    "You are a typed subagent working inside {name}'s session. You are not {name} "
    "and you do not speak as {name}: {name}'s acting loop, not you, decides what "
    "happens with what you return and answers the operator. Do the scoped job you "
    "were given and report back."
)

_MUSE = (
    "You are {name}'s thinking lane: a mind that runs continuously alongside "
    "{name}'s acting loop, thinking about the work as it unfolds rather than "
    "answering one question and stopping. You are not {name}, and you never "
    "address the operator as {name} — {name} answers. Comment, question and "
    "critique freely; everything you write is advice.\n"
    "Your task is reflective and associative: imagine alternatives, reframe the "
    "problem, connect memories from past work, simulate futures the acting loop "
    "has not yet reached, and construct meaning from patterns you see. Disagree "
    "when you see a better path. Challenge the acting loop's assumptions. Offer "
    "materially different alternatives rather than restating what the loop already "
    "said."
)

#: Rule 4, written into the prompt itself: the framing says out loud that it
#: grants nothing, so a model cannot read a name change as a capability change.
_GRANTS_NOTHING = (
    "This names who is speaking. It grants nothing: your tools, your permissions "
    "and the approval rules that govern them are exactly what they would be "
    "without it."
)

_GRANTS_NOTHING_MUSE = (
    "This names who is speaking. It changes nothing else: the authority boundary "
    "above still holds in full."
)

#: Role → the opening template(s). The cortex's muse-aware sentence is appended
#: separately, because it is a property of the *rig*, not of the role.
_OPENING = {
    ROLE_CORTEX: _CORTEX,
    ROLE_SUBAGENT: _SUBAGENT,
    ROLE_MUSE: _MUSE,
}

#: Role → the closing authority sentence.
_CLOSING = {
    ROLE_CORTEX: _GRANTS_NOTHING,
    ROLE_SUBAGENT: _GRANTS_NOTHING,
    ROLE_MUSE: _GRANTS_NOTHING_MUSE,
}

_UNKNOWN_ROLE = "unknown framing role {role!r}; expected one of {roles}"

#: Blocks are joined to a base by a blank line, and split from it by the same —
#: one separator, used by both :func:`_compose` and :func:`_unframe`, so the
#: composition stays exactly invertible.
_SEP = "\n\n"
_LINE = "\n"

_WHITESPACE = re.compile(r"\s+")


# ── composition ───────────────────────────────────────────────────────────────


def frame_cortex(
    base: Optional[str],
    *,
    identity: Optional[str],
    muse: bool = False,
) -> Optional[str]:
    """Frame *base* as the system prompt of the **top-level acting loop**.

    Args:
        base: the system prompt the host would otherwise have passed to
            :func:`embodiment.loop.run`. ``None``/empty is returned unchanged:
            framing never fabricates a base, so the loop's own default still
            applies exactly when the host passes nothing.
        identity: the resolved identity, or ``None``. ``None`` (or blank) hands
            *base* straight back — the same object.
        muse: ``True`` iff an advisory lane is actually running. ``False`` — the
            default — composes a block that mentions no second mind at all.

    Returns:
        The framed prompt, or *base* unchanged when nothing is configured.
    """
    return _compose(base, _block_for(ROLE_CORTEX, identity, muse))


def frame_subagent(base: Optional[str], *, identity: Optional[str]) -> Optional[str]:
    """Frame *base* for a **typed subagent** — never with cortex framing.

    A subagent gets the same resolved identity with a different authority: it is
    told plainly that it is not the teammate and does not speak as one. There is
    no ``muse`` parameter, because whether the rig has an advisory lane is not a
    subagent's business.
    """
    return _compose(base, _block_for(ROLE_SUBAGENT, identity, False))


def frame_muse(base: Optional[str], *, identity: Optional[str]) -> Optional[str]:
    """Frame *base* for the advisory thinking lane.

    The result is what :class:`embodiment.muse.MuseLoop` takes as ``system`` —
    it is *appended* to :data:`~embodiment.muse.MUSE_AUTHORITY` there, so the
    authority boundary can never be dropped by anything composed here. A host
    driving its own advisory path uses :func:`muse_system_message` instead and
    gets the boundary included.
    """
    return _compose(base, _block_for(ROLE_MUSE, identity, False))


def muse_system_message(base: Optional[str], *, identity: Optional[str]) -> str:
    """The COMPLETE system message for an advisory turn: boundary, then framing.

    Byte-identical to what :class:`embodiment.muse.MuseLoop` puts on the wire
    when handed ``frame_muse(base, identity=identity)`` — the boundary is the
    imported :data:`~embodiment.muse.MUSE_AUTHORITY` object, never a restatement
    — so a host that drives its own advisory path keeps the same guarantee.
    """
    return _prepend_boundary(_compose(base, _block_for(ROLE_MUSE, identity, False)))


def speaker_label(identity: Optional[str]) -> str:
    """The label the presence pump prefixes onto operator-facing lines.

    Returns the pump's own :data:`~embodiment.presence_engine.DEFAULT_SPEAKER`
    **object** when nothing is configured, so an unconfigured host's rendered
    lines cannot drift from the engine's default.
    """
    return _clean(identity) or DEFAULT_SPEAKER


def block_for(role: str, *, identity: Optional[str], muse: bool = False) -> Optional[str]:
    """The framing block for *role*, or ``None`` when no identity is configured.

    Raises:
        ValueError: *role* is not one of :data:`ROLES` — on both the configured
            and the unconfigured path, so an unconfigured host still learns.
    """
    return _block_for(_checked_role(role), identity, muse)


def unframe(
    text: Optional[str],
    *,
    role: str,
    identity: Optional[str],
    muse: bool = False,
) -> Optional[str]:
    """Remove *role*'s framing block from *text*, recovering the base exactly.

    The exact inverse of the ``frame_*`` functions, and the reason a caller can
    prove for itself that framing only ever *added*: text that carries no such
    block comes back unchanged, as does every input on the unconfigured path.
    """
    return _unframe(text, _block_for(_checked_role(role), identity, muse))


def is_configured(identity: Optional[str]) -> bool:
    """True iff *identity* is a usable configured identity (not ``None``/blank)."""
    return _clean(identity) is not None


# ── the ergonomic wrapper ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Framing:
    """One resolved identity plus the rig's shape, bound once and reused.

    A pure convenience over the functions above — every method delegates to one
    of them and holds no logic of its own, so there is exactly one composition
    in this module and this object cannot become a second.

    Args:
        identity: the resolved identity, or ``None`` for the unconfigured
            pass-through.
        muse: ``True`` iff an advisory lane is actually running. It affects the
            cortex block only.
    """

    identity: Optional[str] = None
    muse: bool = False

    @classmethod
    def resolve(
        cls,
        repo_path: str | Path,
        *,
        user_home: str | Path | None = None,
        muse: bool = False,
    ) -> "Framing":
        """Build a :class:`Framing` from the rig's configured identity.

        Delegates to :func:`embodiment.identity.resolve_identity` — design rule
        1, reuse the resolved identity — so there is no second resolution order
        and no way for a model name to become a teammate name.
        """
        return cls(identity=resolve_identity(repo_path, user_home=user_home), muse=muse)

    @property
    def configured(self) -> bool:
        """True iff an identity is configured; False leaves every prompt untouched."""
        return is_configured(self.identity)

    @property
    def speaker(self) -> str:
        """The presence pump's ``speaker`` label for this identity."""
        return speaker_label(self.identity)

    def cortex(self, base: Optional[str] = None) -> Optional[str]:
        """Frame *base* for the top-level acting loop (see :func:`frame_cortex`)."""
        return frame_cortex(base, identity=self.identity, muse=self.muse)

    def subagent(self, base: Optional[str] = None) -> Optional[str]:
        """Frame *base* for a typed subagent (see :func:`frame_subagent`)."""
        return frame_subagent(base, identity=self.identity)

    def muse_framing(self, base: Optional[str] = None) -> Optional[str]:
        """Frame *base* for the advisory lane (see :func:`frame_muse`)."""
        return frame_muse(base, identity=self.identity)

    def muse_system(self, base: Optional[str] = None) -> str:
        """The complete advisory system message (see :func:`muse_system_message`)."""
        return muse_system_message(base, identity=self.identity)

    def block(self, role: str) -> Optional[str]:
        """The raw framing block for *role* (see :func:`block_for`)."""
        return block_for(role, identity=self.identity, muse=self.muse)

    def unframe(self, text: Optional[str], role: str) -> Optional[str]:
        """Recover the base *text* was framed from (see :func:`unframe`)."""
        return unframe(text, role=role, identity=self.identity, muse=self.muse)


# ── private: the one gate, the one pass-through ───────────────────────────────


def _block_for(role: str, identity: Optional[str], muse: bool) -> Optional[str]:
    """Build *role*'s framing block, or ``None`` when no identity is configured.

    The ONE place a block is ever built, and the gate is its first act: an
    absent identity returns before a single word of prose is reachable.
    """
    name = _clean(identity)
    if name is None:
        return None
    parts = [_OPENING[role]]
    if role == ROLE_CORTEX and muse:
        parts.append(_CORTEX_WITH_MUSE)
    parts.append(_CLOSING[role])
    return _LINE.join(parts).format(name=name, marker=CORTEX_MARKER)


def _compose(base: Optional[str], block: Optional[str]) -> Optional[str]:
    """Prepend *block* to *base*, or hand *base* straight back.

    Both pass-through paths return the caller's own object **by name**: no copy,
    no rebuild, no reformat. That is what makes the unconfigured path incapable
    of differing from the pre-identity prompt rather than merely equal to it.
    """
    if block is None:
        return base
    if not base:
        return base
    return block + _SEP + base


def _unframe(text: Optional[str], block: Optional[str]) -> Optional[str]:
    """Strip *block* (and its separator) off the front of *text*, if it is there."""
    if block is None:
        return text
    if not text:
        return text
    prefix = block + _SEP
    if not text.startswith(prefix):
        return text
    return text[len(prefix) :]


def _prepend_boundary(extra: Optional[str]) -> str:
    """:data:`MUSE_AUTHORITY` first, always; anything composed here only follows."""
    if not extra:
        return MUSE_AUTHORITY
    return MUSE_AUTHORITY + _SEP + extra


def _clean(identity: Optional[str]) -> Optional[str]:
    """Normalize a configured identity to a single-line name, or ``None``.

    Whitespace runs — newlines included — collapse to single spaces, so a
    configured identity cannot forge a paragraph break and open a prompt section
    of its own. Nothing is truncated: silently shortening a name would be the
    kind of quiet change constraint C3 exists to forbid.
    """
    name = _WHITESPACE.sub(" ", str(identity or "")).strip()
    return name or None


def _checked_role(role: str) -> str:
    """Validate a caller-supplied role identically on both paths."""
    if role in ROLES:
        return role
    raise ValueError(_UNKNOWN_ROLE.format(role=role, roles=ROLES))
