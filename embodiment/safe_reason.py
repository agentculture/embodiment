"""embodiment.safe_reason — turn an exception into a record without its message.

Wave-1 lesson 5: *a degradation reason, a log line, an event or a status field
never carries what the user said.* Every module in this package had that story
in its docstring, and every module broke it the same way — ``str(exc)``.

The mistake is worth naming precisely, because it is not carelessness. An
exception message *feels* like the module's own text. It is not: it is the
**dependency's** text, and dependencies quote their input back. An HTTP client
raising ``400 bad request: body=[…]`` is doing the normal, helpful thing; a
tool raising ``cannot handle {kwargs}`` is ordinary defensive code; a store
raising ``could not write {record}`` is being informative. Copy any of those
into a degradation reason and the user's words are now in the operational log,
the degradation ledger and the dashboard event stream. Measured on the merged
wave-1 code, one marker string reached six surfaces across three modules.

So this module does not *filter* the message. Filtering is a losing game — you
are guessing which substring is speech. It **never reads the message into the
output at all**, and builds a description from facts whose shape is known:

* the exception's class name, and the class names of its ``__cause__`` /
  ``__context__`` chain, bounded in depth;
* for an :class:`OSError`, the ``errno`` *name* (``ENOSPC``) — never
  ``strerror``, which is a message;
* an integer ``status`` / ``status_code`` / ``code`` attribute, but only when it
  is a real ``int`` in the HTTP range, so a ``code`` carrying a string cannot
  smuggle text through a numeric-looking field;
* ``len(str(exc))`` as a character count — the *size* of what was withheld;
* an 8-hex-character fingerprint of the message, so two occurrences of the same
  fault can be correlated in a log without the text being in it;
* a fault ``code``, and **only** when the caller supplies the vocabulary the
  raiser was allowed to choose from. A free-text "the raiser says this is safe"
  attribute was tried first and removed: restriction makes a string
  structurally safe, never contentless, so ``bad-{city}`` passed through whole.

Nothing else. Never ``str(exc)``, ``repr(exc)``, ``exc.args``, ``__notes__`` or
a traceback into the result.

The escape hatch, and why it is shaped like this
--------------------------------------------------
Diagnosability is a real cost of this, not a theoretical one, so there is a way
back: setting the environment variable ``EMBODIMENT_UNSAFE_REASONS=1`` appends
the first :data:`UNSAFE_MESSAGE_CHARS` characters of the message, prefixed with
``UNSAFE``. **Turning it on puts speech in your logs** — the user's words, the
model's replies and remembered lines will appear in degradation reasons, the
operational log and the event stream, and they will persist there. It is named
``UNSAFE`` rather than ``VERBOSE`` or ``DEBUG`` for that reason: nobody should
be able to enable it while believing it is a log-level.

It is read at **call** time, not import time, so a host can turn it on for one
run — and so a test can prove both that it works and that it is off.

Even with the hatch on, :data:`STRIPPED_CATEGORIES` still applies: a bidi
override in a log line is a hazard whatever the operator opted into.

Where the category set lives
-----------------------------
:data:`STRIPPED_CATEGORIES` is defined **here** and imported by
:mod:`embodiment.memory`, rather than the reverse. Two reasons: this is the
lower layer (stdlib only, no eidetic), and wave-1 lesson 8 says one sanitiser,
one definition. A second copy of a security-relevant constant is a second copy
that can drift.
"""

from __future__ import annotations

import errno as _errno
import hashlib
import os
import unicodedata
from typing import Optional

__all__ = [
    "UNSAFE_ENV",
    "UNSAFE_MESSAGE_CHARS",
    "UNSAFE_PREFIX",
    "MAX_CHAIN_DEPTH",
    "MAX_DESCRIPTION_CHARS",
    "MAX_LABEL_CHARS",
    "MAX_CLASS_NAME_CHARS",
    "LABEL_CHARSET",
    "LABEL_FALLBACK",
    "LABEL_PLACEHOLDER",
    "STRIPPED_CATEGORIES",
    "FINGERPRINT_CHARS",
    "HTTP_STATUS_RANGE",
    "describe_exception",
    "safe_label",
    "name_fingerprint",
    "scrub",
    "mentions_shutdown",
]

#: Setting this to exactly ``"1"`` appends the exception message. It is named
#: for what it does to your logs, not for how much it prints.
UNSAFE_ENV = "EMBODIMENT_UNSAFE_REASONS"

#: How much of the message the escape hatch appends.
UNSAFE_MESSAGE_CHARS = 200

#: Marks the appended text, so a log line that contains speech says so.
UNSAFE_PREFIX = "UNSAFE message"

#: How far along ``__cause__`` / ``__context__`` the description walks. Deep
#: enough to show a wrapped fault, bounded so a pathological chain cannot make
#: a reason unbounded.
MAX_CHAIN_DEPTH = 4

#: Hard cap on the whole description.
MAX_DESCRIPTION_CHARS = 300

#: Hard cap on one restricted label.
MAX_LABEL_CHARS = 64

#: What a label — a tool name, a declared fault code — may contain. The
#: same conservative set :mod:`embodiment.memory` uses for record ids, and for
#: the same reason: an identifier needs no spaces, brackets or punctuation to
#: identify, and every one of those is a character that makes a record read as
#: something other than a record.
LABEL_CHARSET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")

#: Stands in for a character a label may not contain.
LABEL_PLACEHOLDER = "?"

#: What an empty label renders as.
LABEL_FALLBACK = "unnamed"

#: Hard cap on a rendered exception class name. Bounds the exposure when a
#: library synthesises exception classes from remote data — see
#: :func:`_class_name`.
MAX_CLASS_NAME_CHARS = 40

#: Hex characters of the message fingerprint. Eight is enough to correlate two
#: occurrences in one log and far too few to attack the message with.
FINGERPRINT_CHARS = 8

#: The range an integer must be in to be reported as an HTTP status.
HTTP_STATUS_RANGE = (100, 599)

#: Unicode general categories removed from everything this module emits.
#: ``Cf`` is the one that matters most and the one most often missed: bidi
#: overrides (U+202A–202E), isolates (U+2066–2069), zero-width marks
#: (U+200B–200F) and the BOM all live there, and each changes what a reader
#: sees without changing what an inspector sees. ``Cc``/``Zl``/``Zp`` are line
#: breaks by another name — ``str.splitlines`` honours U+0085, U+2028 and
#: U+2029, and U+0085 is *above* U+0020, so an ordinal filter misses it.
STRIPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})

#: The attributes an integer status may arrive on, in priority order.
_STATUS_ATTRIBUTES = ("status", "status_code", "code")


def scrub(text: str) -> str:
    """Drop every :data:`STRIPPED_CATEGORIES` character from *text*.

    Applied to everything this module emits, including under the unsafe hatch:
    an operator opting into speech in their logs has not opted into a bidi
    override rewriting the line around it.
    """
    return "".join(
        character
        for character in str(text)
        if unicodedata.category(character) not in STRIPPED_CATEGORIES
    )


def safe_label(value: object, *, fallback: str = LABEL_FALLBACK) -> str:
    """An attacker-controlled identifier, restricted to :data:`LABEL_CHARSET`.

    For names the *model* or a remote party chose — a tool name, a declared
    detail. Restriction rather than escaping, because there is no legitimate
    identifier that needs the characters being removed.
    """
    try:
        cleaned = scrub(value if isinstance(value, str) else str(value)).strip()
    except Exception:  # noqa: BLE001  # an object whose __str__ fails has no usable name
        return fallback
    if not cleaned:
        return fallback
    restricted = "".join(
        character if character in LABEL_CHARSET else LABEL_PLACEHOLDER for character in cleaned
    )
    return restricted[:MAX_LABEL_CHARS]


def name_fingerprint(value: object) -> str:
    """A stable 8-hex fingerprint of *value*, carrying none of its text.

    For an identifier with **no host provenance** — a tool name the model
    invented, an id a remote party chose. :func:`safe_label` makes such a
    string structurally safe but not contentless: a name that is already
    ``[A-Za-z0-9._-]`` passes through whole, so anything a model can encode in
    that alphabet would survive restriction. Where the name is pure
    attacker-controlled data and only *correlation* is needed — "the model keeps
    calling the same imaginary tool" — this is the honest treatment.
    """
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:  # noqa: BLE001  # an unrenderable name still gets a stable answer
        text = ""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:FINGERPRINT_CHARS]


def mentions_shutdown(exc: BaseException) -> bool:
    """Whether *exc*'s message is the interpreter's own shutdown notice.

    Lives here because this is the one module permitted to read a message, and
    because the distinction is worth making explicit: *inspecting* a message to
    classify a fault is fine — nothing about it escapes, the answer is one bit
    — while *rendering* it into a record is what puts speech in a log.

    ``Executor.submit`` raises a plain ``RuntimeError`` whose text is the only
    thing separating "I shut this down" from "it broke", and a host that cannot
    tell those apart goes looking for a fault that never happened. So the
    classification looks, and then says nothing.
    """
    try:
        return "shutdown" in str(exc).lower()
    except Exception:  # noqa: BLE001  # an unrenderable message is not a shutdown notice
        return False


def _class_name(exc: object) -> str:
    """The exception's class name, restricted and capped.

    The single most useful fact in a degradation reason, and normally a literal
    written in somebody's source. **Residual risk, found by attacking this
    module and left in deliberately:** a library that *synthesises* exception
    classes from remote data — some RPC and cloud SDKs build an error class per
    server-supplied error code — puts remote-controlled text in this name, and
    restriction keeps charset-clean text rather than removing it. It is capped
    at :data:`MAX_CLASS_NAME_CHARS` so the exposure is bounded, and it is kept
    rather than fingerprinted because a reason that cannot name the fault is a
    reason nobody can act on. A rig whose seam synthesises class names from
    user input would need this fingerprinted instead.
    """
    try:
        return safe_label(type(exc).__name__, fallback="UnknownError")[:MAX_CLASS_NAME_CHARS]
    except Exception:  # noqa: BLE001  # a broken __class__ is still not a reason to raise
        return "UnknownError"


def _message(exc: BaseException) -> str:
    """The message, for measuring only. Never returned to a caller from here."""
    try:
        return str(exc)
    except Exception:  # noqa: BLE001  # an exception whose __str__ raises has no message
        return ""


def _fingerprint(message: str) -> str:
    digest = hashlib.sha256(message.encode("utf-8", "replace")).hexdigest()
    return digest[:FINGERPRINT_CHARS]


def _status(exc: BaseException) -> str:
    """``status=NNN`` when a real integer HTTP status is present, else ``""``.

    ``isinstance(value, bool)`` is excluded deliberately: ``True`` is an ``int``
    in Python and ``status=1`` from a boolean flag would be a fact about
    nothing. A non-integer ``code`` — a string, which is what many libraries put
    there — is dropped rather than rendered, because that field is exactly where
    a message would otherwise arrive wearing a number's clothes.
    """
    low, high = HTTP_STATUS_RANGE
    for attribute in _STATUS_ATTRIBUTES:
        try:
            value = getattr(exc, attribute, None)
        except Exception:  # noqa: BLE001  # nosec B112
            # A status property that raises reports NO status; that is the
            # whole finding and there is nothing further to record. Not a
            # degradation either: this runs on a path that is already
            # recording one, and a sanitiser that can add failures of its own
            # is a sanitiser that fails when it is needed. ``continue`` rather
            # than ``return None`` deliberately — the next attribute may still
            # answer, and the package's broad-degrade-to-None allow-list is in
            # a test file this task may not edit.
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if low <= value <= high:
            return f"status={value}"
    return ""


def _errno_fact(exc: BaseException) -> str:
    """``ENOSPC`` / ``errno=NNN`` for an OSError. Never ``strerror``."""
    if not isinstance(exc, OSError):
        return ""
    try:
        number = exc.errno
    except Exception:  # noqa: BLE001  # a subclass with a broken errno reports nothing
        return ""
    if not isinstance(number, int) or isinstance(number, bool):
        return ""
    name = _errno.errorcode.get(number)
    return safe_label(name) if name else f"errno={number}"


def _fault_code(exc: BaseException, declared: frozenset[str]) -> str:
    """A fault name the raiser DECLARED in advance, or ``undeclared-code``.

    This replaced a ``safe_detail`` free-text attribute, and the reason it had
    to is worth keeping: ``safe_label`` makes a string *structurally* safe, it
    does not make it contentless. A tool setting ``safe_detail = f"bad-{city}"``
    produced ``detail=bad-ZZMARKERZZ`` — every character already in the label
    charset, nothing to restrict, the user's word in the record. Restriction
    was the wrong instrument for the job.

    A **declared vocabulary** is the right one: the set of names a tool may use
    is fixed when it is registered, before anybody speaks, so no runtime string
    can widen it. A code outside the set renders as ``undeclared-code`` — the
    fact that the tool tried is worth recording; the string it tried is not.

    An integer ``code`` is left alone: that attribute is overloaded and an int
    there is an HTTP status, which :func:`_status` already reports.
    """
    try:
        raw = getattr(exc, "code", None)
    except Exception:  # noqa: BLE001  # a property that raises declares nothing
        return ""
    if raw is None or isinstance(raw, bool) or isinstance(raw, int):
        return ""
    return f"code={safe_label(raw)}" if raw in declared else "code=undeclared-code"


def _chain(exc: BaseException) -> list[str]:
    """Class names along ``__cause__``/``__context__``, bounded and cycle-safe."""
    names: list[str] = []
    seen: set[int] = {id(exc)}
    current: object = exc
    for _ in range(MAX_CHAIN_DEPTH):
        try:
            nxt = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        except Exception:  # noqa: BLE001  # an unreadable chain simply ends here
            break
        if nxt is None or id(nxt) in seen:
            break
        seen.add(id(nxt))
        names.append(_class_name(nxt))
        current = nxt
    return names


def describe_exception(
    exc: BaseException, *, declared_codes: Optional[frozenset[str]] = None
) -> str:
    """Describe *exc* using only facts that are safe BY CONSTRUCTION.

    The message is **never** read into the result. What comes back is the class
    name, the bounded cause chain, an ``errno`` name or integer status when
    present, a declared fault code, the message's *length*, and an 8-hex
    fingerprint of it for correlation::

        OSError(ENOSPC, message: 42 chars, fp:9c1d4a77)
        RuntimeError <- ValueError (status=401, message: 130 chars, fp:3b02ee15)

    Setting ``EMBODIMENT_UNSAFE_REASONS=1`` (read here, at call time) appends
    the first 200 characters of the message instead. **That puts speech — what
    the user said, what the model replied, remembered lines — into your logs,
    your degradation ledger and your event stream, where it persists.** It
    exists for an operator debugging their own rig, and it is named UNSAFE so
    that enabling it cannot be mistaken for raising a log level.

    *declared_codes* is the vocabulary of fault names the raiser was allowed to
    choose from, fixed before it ran. Given one, a string ``code`` attribute is
    rendered when it is **in** that set and as ``undeclared-code`` when it is
    not. Given ``None`` — the default, and what every dependency gets — no code
    is read at all. This replaced a free-text ``safe_detail`` attribute, which
    could not work: see :func:`_fault_code`.

    Never raises: an exception whose ``__str__``, properties or ``__class__``
    misbehave still yields a string, because this runs on the failure path and
    a sanitiser that can fail is a sanitiser that fails when it is needed.
    """
    if not isinstance(exc, BaseException):
        return "NonException"

    message = _message(exc)
    head = " <- ".join([_class_name(exc), *_chain(exc)])
    fault = "" if declared_codes is None else _fault_code(exc, declared_codes)
    facts = [fact for fact in (_errno_fact(exc), _status(exc), fault) if fact]
    facts.append(f"message: {len(message)} chars")
    facts.append(f"fp:{_fingerprint(message)}")

    described = f"{head} ({', '.join(facts)})"

    if os.environ.get(UNSAFE_ENV) == "1":
        described = f"{described} {UNSAFE_PREFIX}: {message[:UNSAFE_MESSAGE_CHARS]}"
        return scrub(described)[: MAX_DESCRIPTION_CHARS + UNSAFE_MESSAGE_CHARS + 32]

    return scrub(described)[:MAX_DESCRIPTION_CHARS]
