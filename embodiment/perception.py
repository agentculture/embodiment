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

Fenced payloads (embodiment#15)
-------------------------------
The first live contact with a real senses model (Gemma 4 12B) found the seam
could not read a perfectly good answer: instruct models routinely wrap JSON in a
markdown code fence, and ``json.loads`` does not. :func:`_unfence` normalises
that shape — a leading ```` ```json ```` / ```` ``` ```` and its closing fence,
tolerant of a language tag, surrounding prose or whitespace, and a missing
closing fence.

Two properties keep it safe. It is a **fallback**: :func:`_parse_json_object`
parses the raw payload first and only reaches for the fence when that fails, so
every payload that parses today parses identically tomorrow (including one whose
*string values* contain a fence marker). And it is applied to **model output
only** — never to ``text`` — so it is structurally incapable of touching the
verbatim invariant. ``tests/test_perception.py`` asserts both over this module's
own AST.

Never-raise
-----------
:func:`perceive` is the ONE public entry point here and it never raises. The
four fault classes named in the build brief (C3) — a dead port, a request
error, a context/window overflow, and lossy/malformed JSON — are all just
``Exception`` subclasses raised either by the caller's injected ``interpret``
seam (a dead port, a request error, an overflow reported by the completion
layer) or by this module's own parsing (:func:`json.loads` on lossy JSON,
or an :class:`_IntakeFault` on empty/non-object content). They all degrade the
same way: the caller's verbatim text still becomes a returned packet's
``original`` (nothing is lost), and a degraded
:class:`~embodiment.contract.SensesRecord` is returned alongside it so the
fallback is never silent (C3). With no ``interpret`` seam configured at all,
``perceive`` still returns a packet (no model call is not a fault — it is the
museless-style default path, mirroring :mod:`embodiment.presence_engine`'s
"museless is the primary path" stance).

An outer guard wraps the whole intake so the promise holds even for the
unforeseen — a hostile response object whose token attributes explode, or a
host's own ``on_degrade`` sink raising. Both degrade; neither escapes. The ONE
thing deliberately outside that guard is the caller's own ``str(original)``
coercion: a value that cannot be rendered has no verbatim text to preserve, and
inventing one (``""``) would break the very invariant this module holds.

Degradation is never silent (C3)
--------------------------------
The second, more serious half of embodiment#15 was a run that returned empty
model-derived fields and reported ``degraded=False`` — the seam returned nothing
and called itself healthy. So a parsed payload that carries **no
interpretation** is a degradation, not a clean intake: interpretation is the
load-bearing field, and a packet without one carries no reading of the request
at all. It degrades to the same shape every other fault does.

Each degradation names itself with one of four codes
(:data:`DEGRADED_SEAM_FAULT`, :data:`DEGRADED_CONTENT_ABSENT`,
:data:`DEGRADED_PAYLOAD_UNREADABLE`, :data:`DEGRADED_INTERPRETATION_EMPTY`),
handed to the optional ``on_degrade`` sink as a :class:`PerceptionDegradation`
— the same ``code``/``reason`` shape :mod:`embodiment.loop`,
:mod:`embodiment.muse` and :mod:`embodiment.events` record, so one host reader
folds them all. The sink is opt-in; ``SensesRecord.degraded`` remains the
always-on signal, and it is now truthful.

Stdlib only (constraint C1): ``json``, ``dataclasses`` plus
:mod:`embodiment.contract`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from embodiment.contract import ContextPacket, ModelResponse, SensesRecord

__all__ = [
    "INTAKE_POINT",
    "DEGRADED_SEAM_FAULT",
    "DEGRADED_CONTENT_ABSENT",
    "DEGRADED_PAYLOAD_UNREADABLE",
    "DEGRADED_INTERPRETATION_EMPTY",
    "PerceptionDegradation",
    "perceive",
]

#: The invocation-point label recorded on a clean/degraded SensesRecord.
INTAKE_POINT = "perception-intake"

#: Mirrors ContextPacket's own ack cap (embodiment/contract.py's _MAX_ACK_LEN).
#: Reimplemented locally rather than imported: that constant is a private
#: module attribute of contract.py, and duplicating one small int keeps this
#: module's coercions self-contained rather than reaching into contract.py's
#: internals.
_MAX_ACK_LEN = 500

#: Cap on one degradation's reason text, as every sibling lane caps its own.
_MAX_REASON_LEN = 500

#: The FIVE ContextPacket fields perception may legitimately fill from a
#: completion's JSON — deliberately excludes "original". This is read by
#: _extract_fields below; nothing in this module ever does
#: ``data.get("original")``.
_KNOWN_FIELDS = ("interpretation", "confidence", "task_type", "omissions", "ack")


# ── degradation vocabulary (C3) ───────────────────────────────────────────────

#: The injected ``interpret`` seam raised. All three of the build brief's
#: *transport* fault classes land here identically — a dead port, a request
#: error, and an overflow reported by the completion layer — because the seam
#: reports them the same way (an exception) and the host's recourse is the same.
DEGRADED_SEAM_FAULT = "perception-seam-failed"
#: The seam returned, but with no usable completion text: ``None``, a non-string,
#: an empty/whitespace-only body, or an object with no readable ``content``. The
#: call happened; the answer did not.
DEGRADED_CONTENT_ABSENT = "perception-content-absent"
#: Text came back and is not a JSON object — lossy/truncated JSON, prose, or a
#: JSON list — even after :func:`_unfence` normalisation. The model spoke; the
#: protocol did not hold.
DEGRADED_PAYLOAD_UNREADABLE = "perception-payload-unreadable"
#: A JSON object parsed cleanly and carries no interpretation. The exact silent
#: case embodiment#15 found: fields empty, record claiming health. Named for the
#: *field* that is missing rather than for the payload, because that is what a
#: host must not mistake for "the operator said nothing interpretable".
DEGRADED_INTERPRETATION_EMPTY = "perception-interpretation-empty"

#: The markdown code-fence marker, in both the opening and closing position.
_FENCE = "```"

#: Longest run of characters accepted as a fence's language tag (``json``,
#: ``JSON``, ``json5``). Anything longer, or containing whitespace, is treated
#: as payload that began on the fence's own line rather than as a tag.
_MAX_LANGUAGE_TAG = 16


@dataclass(frozen=True)
class PerceptionDegradation:
    """One recorded, host-visible intake degradation (constraint C3).

    Field-for-field a prefix of :class:`embodiment.loop.LoopDegradation` and
    :class:`embodiment.muse.MuseDegradation` — ``code`` is the stable machine
    token a host branches on, ``reason`` is short human-readable cause — so the
    degradation ledger folds one shape rather than yet another. It carries no
    ``step_index``/``model_turns``: intake happens before any loop step exists,
    and stamping a ``0`` there would claim a step that never ran.
    """

    code: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason}


def perceive(
    original: Any,
    *,
    interpret: Optional[Callable[[str], ModelResponse | str]] = None,
    point: str = INTAKE_POINT,
    clock: Optional[Callable[[], float]] = None,
    on_degrade: Optional[Callable[[PerceptionDegradation], None]] = None,
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
    on_degrade:
        Optional sink, called once per degradation with the
        :class:`PerceptionDegradation` naming which fault class fired. Opt-in
        detail only: ``SensesRecord.degraded`` reports the transition whether or
        not a sink is wired. A sink that raises degrades the intake it was being
        told about (the record still says ``degraded``) and never escapes.

    Returns
    -------
    (ContextPacket, SensesRecord)
        Always a REAL packet (never ``None``) whose ``original`` is *text*,
        verbatim. On a clean intake the packet also carries the model's
        interpretation/confidence/task_type/omissions/ack and the record has
        ``degraded=False`` with the completion's summed token count. On ANY
        failure — no ``interpret`` result, a raised fault of any kind,
        unparsable/lossy JSON, or a payload carrying no interpretation — the
        packet still carries ``original`` and the record has ``degraded=True``,
        ``tokens=None``.
    """
    text = original if isinstance(original, str) else str(original)
    try:
        return _intake(text, interpret=interpret, point=point, clock=clock, on_degrade=on_degrade)
    except Exception:  # noqa: BLE001  # never-raise is this module's headline promise
        # The last guard, and deliberately not reachable by any named fault
        # class: those are handled inside _intake, with a code. What lands here
        # is the unforeseen — a hostile response object, or the host's own
        # on_degrade sink raising (which is why this path does not try to notify
        # it again). The host still gets its verbatim words and a degraded
        # record, which is the whole contract.
        return ContextPacket(original=text), SensesRecord(point=point, degraded=True)


# ── internals ────────────────────────────────────────────────────────────────


class _IntakeFault(Exception):
    """A fault this module diagnosed itself, carrying its own degradation code.

    Private control flow, not an API: it exists so the parse helpers can name
    *which* fault class fired without every helper needing the packet, the
    record and the sink in scope.
    """

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _intake(
    text: str,
    *,
    interpret: Optional[Callable[[str], ModelResponse | str]],
    point: str,
    clock: Optional[Callable[[], float]],
    on_degrade: Optional[Callable[[PerceptionDegradation], None]],
) -> tuple[ContextPacket, SensesRecord]:
    """The intake proper, under :func:`perceive`'s never-raise guard."""
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
        _require_interpretation(fields)
    except _IntakeFault as fault:
        return _degraded(text, point, _since(clock, start), fault.code, fault.reason, on_degrade)
    except Exception as exc:  # noqa: BLE001  # every seam fault degrades identically (C3)
        return _degraded(
            text,
            point,
            _since(clock, start),
            DEGRADED_SEAM_FAULT,
            f"{type(exc).__name__}: {exc}",
            on_degrade,
        )

    packet = ContextPacket(original=text, **fields)
    return packet, SensesRecord(
        point=point,
        latency=_since(clock, start),
        tokens=_token_total(response),
        degraded=False,
    )


def _degraded(
    text: str,
    point: str,
    latency: Optional[float],
    code: str,
    reason: str,
    on_degrade: Optional[Callable[[PerceptionDegradation], None]],
) -> tuple[ContextPacket, SensesRecord]:
    """The ONE degraded return: verbatim packet, degraded record, named cause.

    The result is built *before* the sink is called, so a sink that raises
    cannot decide what the host gets: :func:`perceive`'s outer guard returns the
    same degraded shape, losing only the latency measurement.
    """
    result = (
        ContextPacket(original=text),
        SensesRecord(point=point, latency=latency, tokens=None, degraded=True),
    )
    if on_degrade is not None:
        on_degrade(PerceptionDegradation(code=code, reason=reason[:_MAX_REASON_LEN]))
    return result


def _now(clock: Optional[Callable[[], float]]) -> Optional[float]:
    """Read *clock*, or ``None`` when absent or itself failing.

    A broken clock is not one of the four named fault classes and must not be
    mistaken for one: it degrades the TIMESTAMP only, never the intake.
    """
    if clock is None:
        return None
    try:
        return clock()
    except Exception:  # noqa: BLE001  # a clock failure is not an intake failure
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
    ``.content``) or a plain ``str`` (used as-is). Raises
    :class:`_IntakeFault` (:data:`DEGRADED_CONTENT_ABSENT`) for anything that
    yields no non-empty string — including ``None``, an empty string, or an
    object with no usable ``content``.
    """
    content = getattr(response, "content", None)
    if content is None and isinstance(response, str):
        content = response
    if not isinstance(content, str) or not content.strip():
        raise _IntakeFault(
            DEGRADED_CONTENT_ABSENT,
            f"intake produced no usable content ({type(response).__name__})",
        )
    return content


def _loads_object(raw: str) -> Optional[dict]:
    """*raw* parsed as a JSON object, or ``None`` when it is neither.

    Narrow by design: ``json.loads`` raises ``json.JSONDecodeError`` (a
    ``ValueError`` subclass) on lossy/malformed JSON, and that is the only
    failure this reads as "not a JSON object". Anything else is unforeseen and
    belongs to :func:`perceive`'s guard, not to a quiet ``None``.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _unfence(raw: str) -> str:
    """The payload inside a markdown code fence, or *raw* unchanged.

    Handles the shape embodiment#15 found in the wild — ```` ```json\\n{…}\\n```
    ```` — plus a bare fence with no language tag, a fence the model never
    closed, and prose or whitespace around the block. Applied to MODEL OUTPUT
    ONLY: this function must never see the caller's own text, and
    ``tests/test_perception.py`` asserts that over the AST.

    The closing marker is searched for from the END, so a fence marker inside a
    string value does not truncate the payload. (A raw payload that already
    parses never reaches here at all — see :func:`_parse_json_object`.)
    """
    text = raw.strip()
    start = text.find(_FENCE)
    if start == -1:
        return raw

    body = text[start + len(_FENCE) :]
    head, newline, rest = body.partition("\n")
    if newline and _is_language_tag(head.strip()):
        body = rest

    end = body.rfind(_FENCE)
    if end != -1:
        body = body[:end]
    return body.strip() or raw


def _is_language_tag(token: str) -> bool:
    """Is *token* a fence's language tag rather than the start of the payload?

    Empty (a bare ```` ``` ````) or one short bare word (``json``, ``JSON``,
    ``json5``). Anything else is payload that began on the fence's own line and
    is kept.
    """
    if not token:
        return True
    return len(token) <= _MAX_LANGUAGE_TAG and token.isalnum()


def _parse_json_object(raw: str) -> dict:
    """Parse *raw* as a JSON object, reaching for the fence only if it must.

    The raw payload is tried FIRST, so every completion that parses today parses
    identically — including one whose string values contain a fence marker.
    Only when that fails is :func:`_unfence` applied. Neither reading is a JSON
    object ⇒ :class:`_IntakeFault` (:data:`DEGRADED_PAYLOAD_UNREADABLE`).
    """
    data = _loads_object(raw)
    if data is None:
        data = _loads_object(_unfence(raw))
    if data is None:
        raise _IntakeFault(
            DEGRADED_PAYLOAD_UNREADABLE,
            "intake response is not a JSON object, fenced or bare",
        )
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
        "interpretation": _coerce_text(data.get("interpretation")),
        "confidence": _coerce_confidence(data.get("confidence")),
        "task_type": _coerce_text(data.get("task_type")),
        "omissions": _coerce_omissions(data.get("omissions")),
        "ack": _coerce_ack(data.get("ack")),
    }


def _coerce_text(value: Any) -> str:
    """Coerce a raw string field, reading an explicit JSON ``null`` as absent.

    ``str(None)`` is the string ``"None"`` — a five-character interpretation
    that reads as an answer, passes :func:`_require_interpretation`, and lands
    in a packet claiming health. A model that answers ``"interpretation": null``
    is telling the host it has nothing; ``""`` is what that means here, and it
    degrades like every other unreadable interpretation.
    """
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _require_interpretation(fields: dict) -> None:
    """Raise when the parsed payload carries no reading of the request.

    embodiment#15's defect 2. The other four fields are genuinely optional — a
    model may decline to guess a ``task_type``, or see no omissions — but an
    intake with no interpretation has perceived nothing, and returning it as a
    clean packet is the "appears attentive and is not" failure C3 exists to
    prevent. It degrades to the same shape every other fault does rather than
    handing back a partial packet, so a host has ONE degraded shape to read.
    """
    if not fields["interpretation"].strip():
        raise _IntakeFault(
            DEGRADED_INTERPRETATION_EMPTY,
            "intake payload parsed but carries no interpretation",
        )


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
