"""Perception intake — the front door that builds a :class:`ContextPacket`
from an operator's request (task t8).

Colleague's ``senses.py`` (1139 lines, read-only reference for this task) names
the invariant this module exists to hold: ``ContextPacket.original`` is set from
the caller's input **verbatim, never from model output** — the core invariant of
the whole perception arc. A presence layer that can lose or mutate the user's
actual words is worse than no presence layer at all.

Structural enforcement, not convention
---------------------------------------
:func:`perceive` builds every returned :class:`~embodiment.contract.ContextPacket`
from exactly ONE call, ``ContextPacket(original=text, **fields)``, where ``text``
is the caller's argument (coerced to ``str``, never stripped/trimmed/normalized)
and ``fields`` comes from :func:`_extract_fields` — a fixed allowlist of the
FIVE non-``original`` :class:`ContextPacket` fields
(``interpretation``/``confidence``/``task_type``/``omissions``/``ack``).
:func:`_extract_fields` never reads a ``"original"`` key out of the model's
parsed JSON, so a hostile completion that emits one has *no path* into the
packet — there is no code to delete to introduce the bug, only code to
deliberately add. This is also why :func:`perceive` never routes model output
through :meth:`ContextPacket.from_dict`: that classmethod *does* read
``data.get("original", "")`` (it exists to round-trip a whole packet read back
from an artifact, where that is correct), so handing it raw model JSON would be
exactly the mistake this module exists to make impossible.

Never-raise
-----------
:func:`perceive` is the ONE public entry point here and it never raises. The
four fault classes named in the build brief (C3) — a dead port, a request
error, a context/window overflow, and lossy/malformed JSON — are all just
``Exception`` subclasses raised either by the caller's injected ``interpret``
seam (a dead port, a request error, an overflow reported by the completion
layer) or by this module's own parsing (:func:`json.loads` on lossy JSON,
or a defensive ``ValueError`` on empty/non-object content). One blanket
``except Exception`` degrades ALL of them identically: the caller's verbatim
text still becomes a returned packet's ``original`` (nothing is lost), and a
degraded :class:`~embodiment.contract.SensesRecord` is returned alongside it
so the fallback is never silent (C3). With no ``interpret`` seam configured at
all, ``perceive`` still returns a packet (no model call is not a fault — it is
the museless-style default path, mirroring
:mod:`embodiment.presence_engine`'s "museless is the primary path" stance).

Stdlib only (constraint C1): ``json`` plus :mod:`embodiment.contract`.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from embodiment.contract import ContextPacket, ModelResponse, SensesRecord

__all__ = ["INTAKE_POINT", "perceive"]

#: The invocation-point label recorded on a clean/degraded SensesRecord.
INTAKE_POINT = "perception-intake"

#: Mirrors ContextPacket's own ack cap (embodiment/contract.py's _MAX_ACK_LEN).
#: Reimplemented locally rather than imported: that constant is a private
#: module attribute of contract.py, and duplicating one small int keeps this
#: module's coercions self-contained rather than reaching into contract.py's
#: internals.
_MAX_ACK_LEN = 500

#: The FIVE ContextPacket fields perception may legitimately fill from a
#: completion's JSON — deliberately excludes "original". This is read by
#: _extract_fields below; nothing in this module ever does
#: ``data.get("original")``.
_KNOWN_FIELDS = ("interpretation", "confidence", "task_type", "omissions", "ack")


def perceive(
    original: Any,
    *,
    interpret: Optional[Callable[[str], ModelResponse | str]] = None,
    point: str = INTAKE_POINT,
    clock: Optional[Callable[[], float]] = None,
) -> tuple[ContextPacket, SensesRecord]:
    """Perceive *original* into a :class:`ContextPacket`. Never raises.

    Parameters
    ----------
    original:
        The operator's verbatim request. Coerced to ``str`` when not already
        one (a defensive, identity-preserving coercion — ``str()`` on a
        ``str`` returns the same value) and then NEVER touched again: no
        ``.strip()``, no truncation, no normalization. This is the only value
        that ever reaches the returned packet's ``original`` field.
    interpret:
        The injected model seam — the ONE thing in this module that may talk
        to a network. Called as ``interpret(text)`` with the same verbatim
        text, expected to return either an
        :class:`~embodiment.contract.ModelResponse` (its ``.content`` is read)
        or a plain ``str`` of the raw completion text. ``None`` (the default)
        means no perception seam is configured — a clean, non-degraded packet
        is still returned, carrying only ``original``.
    point:
        The invocation-point label recorded on the returned
        :class:`~embodiment.contract.SensesRecord` (default
        :data:`INTAKE_POINT`).
    clock:
        Optional wall-clock callable for measuring ``latency``. ``None`` (the
        default, and the common case — this module has no opinion about
        timekeeping) leaves ``latency`` at ``None`` rather than fabricating a
        measurement. A ``clock`` that itself raises is treated as "no
        timestamp available", never as an intake failure.

    Returns
    -------
    (ContextPacket, SensesRecord)
        Always a REAL packet (never ``None``) whose ``original`` is *text*,
        verbatim. On a clean intake the packet also carries the model's
        interpretation/confidence/task_type/omissions/ack and the record has
        ``degraded=False`` with the completion's summed token count. On ANY
        failure — no ``interpret`` result, a raised fault of any kind, or
        unparsable/lossy JSON — the packet still carries ``original`` and the
        record has ``degraded=True``, ``tokens=None``.
    """
    text = original if isinstance(original, str) else str(original)

    if interpret is None:
        # No perception seam configured: not a fault, the default path — zero
        # model calls, a clean (non-degraded) record, same shape as
        # presence_engine's "museless" lane.
        return ContextPacket(original=text), SensesRecord(point=point, degraded=False)

    start = _now(clock)
    try:
        response = interpret(text)
        raw = _extract_content(response)
        data = _parse_json_object(raw)
        fields = _extract_fields(data)
    except Exception:  # noqa: BLE001 - every fault class degrades identically (C3)
        return ContextPacket(original=text), SensesRecord(
            point=point,
            latency=_since(clock, start),
            tokens=None,
            degraded=True,
        )

    packet = ContextPacket(original=text, **fields)
    return packet, SensesRecord(
        point=point,
        latency=_since(clock, start),
        tokens=_token_total(response),
        degraded=False,
    )


# ── internals ────────────────────────────────────────────────────────────────


def _now(clock: Optional[Callable[[], float]]) -> Optional[float]:
    """Read *clock*, or ``None`` when absent or itself failing.

    A broken clock is not one of the four named fault classes and must not be
    mistaken for one: it degrades the TIMESTAMP only, never the intake.
    """
    if clock is None:
        return None
    try:
        return clock()
    except Exception:  # noqa: BLE001 - a clock failure is not an intake failure
        return None


def _since(clock: Optional[Callable[[], float]], start: Optional[float]) -> Optional[float]:
    """Elapsed time since *start*, or ``None`` when unmeasurable."""
    if start is None:
        return None
    now = _now(clock)
    if now is None:
        return None
    return now - start


def _extract_content(response: Any) -> str:
    """Pull the raw completion text out of *response*.

    Accepts a :class:`~embodiment.contract.ModelResponse`-shaped object (reads
    ``.content``) or a plain ``str`` (used as-is). Raises ``ValueError`` for
    anything that yields no non-empty string — including ``None``, an empty
    string, or an object with no usable ``content`` — which the caller's
    blanket ``except Exception`` folds into the same degraded path as every
    other fault class.
    """
    content = getattr(response, "content", None)
    if content is None and isinstance(response, str):
        content = response
    if not isinstance(content, str) or not content.strip():
        raise ValueError("perception intake produced no usable content")
    return content


def _parse_json_object(raw: str) -> dict:
    """Parse *raw* as a JSON object. Raises on non-JSON or non-object JSON.

    ``json.loads`` raises ``json.JSONDecodeError`` (a ``ValueError`` subclass)
    on lossy/malformed JSON — one of the four named fault classes — which
    :func:`perceive`'s blanket ``except Exception`` catches identically to
    every other fault.
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("perception intake response is not a JSON object")
    return data


def _extract_fields(data: dict) -> dict:
    """The allowlisted read of *data* into ContextPacket's non-``original`` fields.

    This is the ENTIRE verbatim-invariant enforcement: only the five keys in
    :data:`_KNOWN_FIELDS` are ever read from a completion's parsed JSON.
    ``data.get("original", ...)`` does not appear anywhere in this function —
    a hostile completion that includes an ``"original"`` key (a spoofed
    rewrite, a prompt-injection payload, a near-miss paraphrase, anything)
    is read here and then simply never looked at again.
    """
    return {
        "interpretation": str(data.get("interpretation", "")),
        "confidence": _coerce_confidence(data.get("confidence")),
        "task_type": str(data.get("task_type", "")),
        "omissions": _coerce_omissions(data.get("omissions")),
        "ack": _coerce_ack(data.get("ack")),
    }


def _coerce_confidence(value: Any) -> float:
    """Best-effort float coercion; a value that cannot be parsed degrades to 0.0."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _coerce_omissions(value: Any) -> list:
    """Coerce a raw ``omissions`` payload: list/tuple -> list[str], str -> [str],
    anything else (``None``, a number, a dict) -> ``[]``."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return []


def _coerce_ack(value: Any) -> Optional[str]:
    """Coerce a raw ``ack`` payload: a non-string, or empty/whitespace-only
    string, degrades to ``None``; a usable string is stripped and hard-capped."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:_MAX_ACK_LEN]


def _token_total(response: Any) -> Optional[int]:
    """Sum ``prompt_tokens``/``completion_tokens`` off *response* when present.

    ``None`` when neither is reported (a plain ``str`` response, or an object
    with no token attributes) — never estimated, mirroring the token-honesty
    rule the rest of the contract already holds.
    """
    prompt = getattr(response, "prompt_tokens", None)
    completion = getattr(response, "completion_tokens", None)
    if prompt is None and completion is None:
        return None
    return int(prompt or 0) + int(completion or 0)
