"""The embodiment data contract — the shapes the loop and the presence pump share.

Every model seam an app injects consumes a :class:`Task` and produces a
:class:`TaskResult` of the *same shape*, regardless of which model ran
underneath. That uniformity is what lets one loop be written here and imported
per host instead of reimplemented.

The types are plain dataclasses with explicit ``to_dict`` / ``from_dict`` so a
result round-trips through JSON unchanged: a host's handoff artifact is simply
``TaskResult.to_dict()`` serialized, and reloading it yields an equal object.

**Stdlib only** (constraint C1): this module imports ``uuid``, ``dataclasses``
and ``typing`` and nothing else — not at module scope, not lazily inside a
function. Nothing here imports the first consumer.

Provenance and the carve boundary (task t1)
-------------------------------------------
These shapes are carved from colleague ``1.52.1``'s ``colleague/contract.py``
(plus ``ToolCall`` / ``ModelResponse`` / ``WorkAborted``, which lived in its
``loop.py``), with field names, field order and serialization semantics kept at
parity so an artifact written by either package reads back in the other.

What deliberately stayed behind, because it is *policy* the host owns rather
than data the loop produces — each becomes an injection point in a later task,
never a contract member here:

* ``HookFiring`` + the ``DECISION_*`` vocabulary (the hook lifecycle arrives as
  injected callbacks; the loop task owns its record shape);
* ``CapacityDecision`` / ``capacity_warning`` / ``gates_deferred`` (fill-line
  and chain-gate policy);
* ``LintReport`` / ``CoherenceReport`` / ``TestIntegrityReport`` /
  ``AffectedTestsReport`` (pre-finish gates; the last two are not even
  expressible here — they are defined in host modules);
* ``DeepthinkCall`` + ``TaskResult.deepthink`` (the escalation record; the
  advisory *muse* seam defines its own);
* ``ChainView`` + ``TaskResult.chain`` (multi-episode dispatch accounting);
* ``SensesDirectRecord`` (a front-door classifier record) and the senses
  coordination loop's point-prefix convention — one loop ships here, so the
  second loop's naming convention has no consumer.

Fields typed by those shapes were dropped with them; every other field is kept
verbatim so a host that *does* run a git/PR handoff, a named command, or a
destination frame can still record it without a parallel schema.

Three deliberate deviations, all additive:

* ``ToolCall`` / ``ModelResponse`` gained ``to_dict`` / ``from_dict``. They
  carried none upstream because they lived in the loop module and never reached
  an artifact; here every dataclass in the contract round-trips, so a host can
  record a model turn without inventing a serializer. Their *fields* are
  unchanged.
* ``_coerce_acceptance_outcomes`` and the ``records`` / ``injections`` / ``chat``
  readers reject a non-list payload instead of iterating it. Upstream, a scalar
  where a list belonged raised ``TypeError`` mid-``from_dict`` — a latent
  contradiction of the never-raises stance those docstrings already claimed.
* ``TaskResult.memory`` is type-guarded on read like ``media`` is, so a
  malformed non-mapping payload degrades to ``None`` rather than surviving to
  crash the next ``to_dict``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

# TaskResult.status values.
OK = "ok"
ERROR = "error"
INCOMPLETE = "incomplete"

# Sentinel assigned to TaskResult.summary when a work item ended without calling
# ``finish`` and produced no substantive model content. Callers compare
# ``result.summary == NO_RESULT_PRODUCED`` to detect the empty case without
# string-matching a step-count fallback such as "completed in N step(s)".
# The loop is responsible for assigning this value; the contract owns the stable
# string so every seam and every caller share one importable reference.
#
# The value is a deliberately machine-oriented marker (sentinel affixes + a
# token unlikely in prose) rather than a plain-English phrase: the sentinel
# lives in ``summary``, a free-form *model text* field, so if it read like
# normal output the model could legitimately emit it as its last substantive
# content and a caller would misclassify a real result as the empty case.
#
# The spelling is FROZEN at the value colleague 1.52.1 already writes into its
# artifacts. It is a wire value, not an import: keeping it byte-identical is
# what lets an artifact written before the extraction still read back as the
# empty case afterwards. Renaming it would silently reclassify old artifacts.
NO_RESULT_PRODUCED = "__COLLEAGUE_NO_RESULT_PRODUCED__"

# Conventional ``SensesBlock.chat`` entry ``"kind"`` values: the ONE closed
# vocabulary every surface draws from — ``"talk"`` (implied when the key is
# absent), ``"ack"`` (the intake acknowledgment), ``"update"`` (a proactive
# progress narration), and ``"clarify"`` (a clarifying question/answer exchange
# before dispatch). No surface may grow its own record schema; import this
# constant rather than re-typing the literal strings.
SENSES_CHAT_KINDS: tuple[str, ...] = ("talk", "ack", "update", "clarify")

#: Hard cap on ``ContextPacket.ack`` length.
_MAX_ACK_LEN = 500

__all__ = [
    "OK",
    "ERROR",
    "INCOMPLETE",
    "NO_RESULT_PRODUCED",
    "SENSES_CHAT_KINDS",
    "ToolCall",
    "ModelResponse",
    "WorkAborted",
    "Usage",
    "WorkStats",
    "SubResult",
    "ContextPacket",
    "SensesRecord",
    "SensesBlock",
    "IncompletionRecord",
    "Step",
    "Task",
    "TaskResult",
]


def _coerce_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """Best-effort ``int`` coercion; ``default`` when the value cannot be parsed.

    Malformed structured payloads read back from an artifact degrade rather than
    abort a whole ``from_dict`` call — the never-raises stance every coercion in
    this module shares.
    """
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Best-effort ``float`` coercion; ``default`` when the value cannot be parsed."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        """Coerce a raw ``ToolCall``-shaped mapping.

        ``arguments`` degrades to ``{}`` when it is not a mapping (a model that
        returned a JSON *string* of arguments is the caller's to parse — this
        contract holds the parsed form).
        """
        raw_arguments = data.get("arguments")
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            arguments=dict(raw_arguments) if isinstance(raw_arguments, dict) else {},
        )


@dataclass
class ModelResponse:
    """One model turn: free text, reasoning, any tool calls, and token usage.

    ``reasoning`` is the model's chain-of-thought when the server returns it as a
    separate field (OpenAI-compatible ``message.reasoning`` /
    ``reasoning_content``), distinct from ``content`` (the final answer). It is
    generated but never saved to a file, so the loop measures it as the "thought"
    portion of a work item (char/byte lengths in :class:`WorkStats`). Empty for
    servers/models that do not emit a reasoning field.
    """

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelResponse":
        """Coerce a raw ``ModelResponse``-shaped mapping; malformed entries drop."""
        raw_calls = data.get("tool_calls")
        calls = raw_calls if isinstance(raw_calls, (list, tuple)) else []
        return cls(
            content=str(data.get("content", "")),
            tool_calls=[ToolCall.from_dict(c) for c in calls if isinstance(c, dict)],
            prompt_tokens=_coerce_int(data.get("prompt_tokens"), 0) or 0,
            completion_tokens=_coerce_int(data.get("completion_tokens"), 0) or 0,
            reasoning=str(data.get("reasoning", "")),
        )


@dataclass
class Usage:
    """Token accounting for a work item, summed across the loop's model calls."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Usage":
        return cls(
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
        )


@dataclass
class WorkStats:
    """Always-on per-work-item statistics — the cost+shape record of one work item.

    Sits alongside :class:`Usage` (which holds the exact API-reported token
    counts) and captures everything else worth knowing about a work item so a
    caller can compute the cost of the work: how long it took, what it did, and
    how much it produced. Populated runtime-side by the loop, so every seam
    fills it identically.

    Token honesty: tokens live on :class:`Usage` and are taken *verbatim* from
    the model response ``usage`` — never estimated. Models reporting no
    reasoning-token breakdown make "thought vs written" measurable only as exact
    **chars/bytes**, not tokens: ``reasoning_*`` is the model's chain-of-thought
    (the separate ``message.reasoning`` field, generated but not saved to a
    file), ``answer_*`` is ``message.content`` (the final answer), and
    ``bytes_written`` is the exact UTF-8 byte count written to files. There is no
    tokenizer (zero runtime deps), so a reasoning / written *token* count is
    deliberately not synthesised.

    Fields
    ------
    request:
        The originating task instruction (the request the work item answered).
    engine:
        The seam that ran the work item (e.g. ``mock`` / ``vllm-openai``) —
        ``task.engine``. With ``model`` it makes the record self-describing: a
        caller comparing two artifacts knows which mind produced each.
    model:
        The model id the seam was configured to call. Empty when no model was
        threaded.
    started_at:
        ISO-8601 UTC timestamp of when the loop began.
    duration_seconds:
        Wall-clock loop duration (monotonic delta), seconds.
    model_turns:
        Number of model turns (``complete`` calls) the loop ran.
    step_count:
        Number of tool-call steps recorded (mirrors ``len(steps)``).
    tool_counts:
        Per-tool call counts aggregated from ``steps`` (tool name → count).
    files_changed:
        Number of distinct files the work item wrote (mirrors
        ``len(changed_files)``).
    bytes_written:
        Total UTF-8 bytes written to files, summed over the work item.
    reasoning_chars / reasoning_bytes:
        Length of all ``message.reasoning`` text generated (chain-of-thought
        "thought" not saved to a file), in Unicode chars and UTF-8 bytes.
    answer_chars / answer_bytes:
        Length of all ``message.content`` text generated (the final answer), in
        Unicode chars and UTF-8 bytes.
    """

    request: str = ""
    engine: str = ""
    model: str = ""
    started_at: str = ""
    duration_seconds: float = 0.0
    model_turns: int = 0
    step_count: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    files_changed: int = 0
    bytes_written: int = 0
    reasoning_chars: int = 0
    reasoning_bytes: int = 0
    answer_chars: int = 0
    answer_bytes: int = 0

    def add_generated(self, *, reasoning: str = "", answer: str = "") -> None:
        """Accumulate one turn's generated text into the char/byte counters.

        Called once per model turn by the loop with that turn's
        ``message.reasoning`` and ``message.content``. Char counts are Unicode
        code points (``len``); byte counts are UTF-8 (``len(.encode("utf-8"))``).
        """
        self.reasoning_chars += len(reasoning)
        self.reasoning_bytes += len(reasoning.encode("utf-8"))
        self.answer_chars += len(answer)
        self.answer_bytes += len(answer.encode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "engine": self.engine,
            "model": self.model,
            "started_at": self.started_at,
            "duration_seconds": self.duration_seconds,
            "model_turns": self.model_turns,
            "step_count": self.step_count,
            "tool_counts": dict(self.tool_counts),
            "files_changed": self.files_changed,
            "bytes_written": self.bytes_written,
            "reasoning_chars": self.reasoning_chars,
            "reasoning_bytes": self.reasoning_bytes,
            "answer_chars": self.answer_chars,
            "answer_bytes": self.answer_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkStats":
        return cls(
            request=str(data.get("request", "")),
            engine=str(data.get("engine", "")),
            model=str(data.get("model", "")),
            started_at=str(data.get("started_at", "")),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            model_turns=int(data.get("model_turns", 0)),
            step_count=int(data.get("step_count", 0)),
            tool_counts={str(k): int(v) for k, v in (data.get("tool_counts") or {}).items()},
            files_changed=int(data.get("files_changed", 0)),
            bytes_written=int(data.get("bytes_written", 0)),
            reasoning_chars=int(data.get("reasoning_chars", 0)),
            reasoning_bytes=int(data.get("reasoning_bytes", 0)),
            answer_chars=int(data.get("answer_chars", 0)),
            answer_bytes=int(data.get("answer_bytes", 0)),
        )


@dataclass
class SubResult:
    """The result of one delegated sub-task driven by a nested child work item.

    A work item may delegate a scoped sub-task to a nested child work item; each
    child produces a ``SubResult`` recorded on the parent
    ``TaskResult.sub_results``.

    Cost attribution is **nested-only**: the child carries its OWN ``usage`` and
    the parent ``TaskResult.usage`` is NOT summed with its children's — a reader
    sums them explicitly if a roll-up is wanted.
    """

    task_id: str
    engine: str
    model: str
    status: str
    summary: str = ""
    changed_files: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    role: Optional[str] = None
    """The typed-subagent role this child ran as, or ``None`` for the default
    full-surface delegation. Omitted from ``to_dict`` when None so a role-less
    child serializes byte-identically to the pre-role contract."""
    parent: Optional[str] = None
    """The parent work item's ``task_id`` (lineage), or ``None`` when the child
    was not recorded with a parent link. Lets a subagent tree be walked from
    artifacts alone — child artifacts name their parent, so a tree of delegated
    work items is reconstructable without external bookkeeping. Populated
    structurally by the caller that mints the child, never inferred. Omitted
    from ``to_dict`` when ``None``."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "engine": self.engine,
            "model": self.model,
            "status": self.status,
            "summary": self.summary,
            "changed_files": list(self.changed_files),
            "usage": self.usage.to_dict(),
        }
        # Omit-when-None: a role-less child is byte-identical to the pre-role shape.
        if self.role is not None:
            d["role"] = self.role
        # Same omit-when-None treatment for lineage.
        if self.parent is not None:
            d["parent"] = self.parent
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubResult":
        return cls(
            task_id=str(data["task_id"]),
            engine=str(data["engine"]),
            model=str(data["model"]),
            status=str(data["status"]),
            summary=str(data.get("summary", "")),
            changed_files=list(data.get("changed_files", [])),
            usage=Usage.from_dict(data.get("usage", {})),
            role=data.get("role"),
            parent=data.get("parent"),
        )


@dataclass
class ContextPacket:
    """The perception seam's interpretation of an operator's request.

    The "senses" seam is a tools-off front door that reads the operator's
    *verbatim* request and produces a structured interpretation before the
    "cortex" seam drives the loop. The packet rides the contract as the optional
    ``Task.context_packet`` and is echoed back (serialized) inside the
    :class:`SensesBlock` on ``TaskResult.senses``.

    Fields
    ------
    original:
        The operator's verbatim original text. This must round-trip through
        JSON **byte-for-byte** — no normalization, no trimming — because it is
        the audit-trail record of exactly what was asked, and it is set from the
        caller's input, never from model output. (Only ``interpretation`` is a
        derived/normalized reading; ``original`` is sacrosanct.)
    interpretation:
        What the perception seam believes the request means — a normalized,
        possibly reworded reading of ``original``.
    confidence:
        The seam's confidence in ``interpretation`` (typically 0.0-1.0).
    task_type:
        A short classification of the request (e.g. ``"bugfix"``, ``"feature"``,
        ``"docs"``).
    omissions:
        What the seam judged the request left implicit or omitted — one short
        string per gap (e.g. ``"which file"``, ``"acceptance criteria"``).
    ack:
        The acknowledgment line for this request — produced in the SAME intake
        completion as the rest of the packet (zero extra calls, zero extra
        latency), rendered before the loop's first step. ``None`` when no ack was
        produced (a degraded intake, or a run that predates this field) —
        omitted from ``to_dict`` so a packet without an acknowledgment
        serializes byte-identically to before this field existed.
    """

    original: str
    interpretation: str = ""
    confidence: float = 0.0
    task_type: str = ""
    omissions: list[str] = field(default_factory=list)
    ack: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "original": self.original,
            "interpretation": self.interpretation,
            "confidence": self.confidence,
            "task_type": self.task_type,
            "omissions": list(self.omissions),
        }
        # ack gets the same omit-when-None treatment as the rest of the
        # contract's optional fields.
        if self.ack is not None:
            data["ack"] = self.ack
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextPacket":
        """Coerce a raw ``ContextPacket``-shaped mapping read back from an artifact.

        ``original`` is kept **verbatim**: ``str()`` on an already-string value
        is identity, so the operator's exact text (whitespace, newlines,
        unicode) survives byte-for-byte. ``confidence`` is a best-effort numeric
        coercion — a value that cannot be parsed as ``float`` falls back to
        ``0.0`` rather than raising. ``ack`` is defensively coerced via
        :func:`_coerce_ack`: a non-string value (absent, explicit ``null``, a
        number, or a dict from a malformed artifact) degrades to ``None``; a
        string is stripped of surrounding whitespace (an empty/whitespace-only
        result also degrading to ``None``) and hard-capped to
        :data:`_MAX_ACK_LEN` characters.
        """
        return cls(
            original=str(data.get("original", "")),
            interpretation=str(data.get("interpretation", "")),
            confidence=_coerce_float(data.get("confidence"), 0.0) or 0.0,
            task_type=str(data.get("task_type", "")),
            omissions=_coerce_omissions(data.get("omissions")),
            ack=_coerce_ack(data.get("ack")),
        )


@dataclass
class SensesRecord:
    """One perception-seam invocation record.

    A single per-invocation fact collected inside the :class:`SensesBlock` on
    ``TaskResult.senses`` — the unit a degradation ledger is built from: every
    invocation that fell back records one, so an app that *appears* attentive
    and is not is diagnosable from the artifact alone.

    Fields
    ------
    point:
        Which invocation point fired (a free-form label, e.g. ``"interpret"``).
    latency:
        Wall-clock seconds the call took, or ``None`` when not measured.
    tokens:
        Total tokens used by the completion, or ``None`` when not reported
        (e.g. a degraded call that never reached the wire).
    degraded:
        ``True`` iff the call fell back / never completed against the perception
        seam (a dead endpoint, request error, or overflow) instead of actually
        completing. Default ``False``.
    """

    point: str
    latency: Optional[float] = None
    tokens: Optional[int] = None
    degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "point": self.point,
            "latency": self.latency,
            "tokens": self.tokens,
            "degraded": self.degraded,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SensesRecord":
        """Coerce a raw ``SensesRecord``-shaped mapping read back from an artifact.

        ``latency``/``tokens`` are best-effort numeric coercions: a value that
        cannot be parsed as ``float``/``int`` falls back to ``None`` rather than
        raising and aborting the whole ``TaskResult.from_dict`` call.
        ``point``/``degraded`` still survive from the rest of the entry.
        """
        return cls(
            point=str(data.get("point", "")),
            latency=_coerce_float(data.get("latency")),
            tokens=_coerce_int(data.get("tokens")),
            degraded=bool(data.get("degraded", False)),
        )


@dataclass
class SensesBlock:
    """The perception record for a work item.

    A block of shape ``{mode, packet, records}`` recorded on
    ``TaskResult.senses``: ``mode`` names how the cortex/senses split resolved
    (e.g. ``"split"`` when a perception seam interpreted the request, or
    ``"cortex-only"`` when it did not), ``packet`` is the :class:`ContextPacket`
    the perception seam produced (or ``None``), and ``records`` is the ordered
    list of per-invocation :class:`SensesRecord` entries.

    A run with no perception involvement leaves ``TaskResult.senses`` at
    ``None``, so the key is omitted entirely.

    ONE SHARED SHAPE ACROSS EVERY SURFACE: an interactive session, an attached
    talk lane, a background run and a one-shot run all record their beats (ack,
    proactive updates, clarify, guidance relay) into this SAME dataclass — the
    SAME ``records``/``chat``/``injections`` fields and the SAME
    :data:`SENSES_CHAT_KINDS` vocabulary. No surface defines its own record type;
    a surface that needs a genuinely new beat extends THIS shape, never a
    parallel one.

    ``injections`` records every APPLIED guidance injection
    (``{text, at, source}``, ``at`` a wall-clock float — never estimated);
    ``chat`` folds the operator-facing exchanges
    (``{message, answer, kind, ...}``) so a mid-run conversation is
    reconstructable from the artifact alone. Both are omit-when-empty. Each
    ``chat`` entry MAY carry an optional ``"kind"`` naming which exchange
    produced it (``"talk"`` is implied when absent, so a pre-existing entry
    keeps its meaning); ``chat`` stays a list of plain dicts and
    (de)serialization passes every entry through verbatim.
    """

    mode: str
    packet: Optional[ContextPacket] = None
    records: list[SensesRecord] = field(default_factory=list)
    injections: list[dict[str, Any]] = field(default_factory=list)
    chat: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "mode": self.mode,
            "packet": self.packet.to_dict() if self.packet is not None else None,
            "records": [r.to_dict() for r in self.records],
        }
        # Omit-when-empty: a block with no live lane keeps the smaller shape.
        if self.injections:
            out["injections"] = [dict(entry) for entry in self.injections]
        if self.chat:
            out["chat"] = [dict(entry) for entry in self.chat]
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SensesBlock":
        """Coerce a raw ``SensesBlock``-shaped mapping read back from an artifact.

        ``packet`` is parsed only when it is a mapping (a malformed non-dict
        packet degrades to ``None``); malformed (non-dict) ``records`` /
        ``injections`` / ``chat`` entries are dropped rather than raising.
        """
        raw_packet = data.get("packet")
        return cls(
            mode=str(data.get("mode", "")),
            packet=ContextPacket.from_dict(raw_packet) if isinstance(raw_packet, dict) else None,
            records=[
                SensesRecord.from_dict(entry)
                for entry in _as_sequence(data.get("records"))
                if isinstance(entry, dict)
            ],
            injections=[
                dict(entry)
                for entry in _as_sequence(data.get("injections"))
                if isinstance(entry, dict)
            ],
            chat=[
                dict(entry) for entry in _as_sequence(data.get("chat")) if isinstance(entry, dict)
            ],
        )


@dataclass(frozen=True)
class IncompletionRecord:
    """Record of why a work item was incomplete.

    Fields
    ------
    reason:
        Human-readable explanation of why the work item did not complete.
    evidence:
        Supporting detail (e.g. last tool-call output, error text).
    recommendation:
        Suggested next step for the operator or a follow-up work item.
    """

    reason: str
    evidence: str
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "IncompletionRecord":
        """Best-effort coercion: each field coerced to str, empty string on failure.

        Robust to a malformed payload (a non-dict, or an explicit ``null``
        field): a non-dict ``data`` yields an all-empty record, and
        ``data.get(...) or ""`` turns a ``None`` value into ``""`` rather than
        the string ``"None"``.
        """
        if not isinstance(data, dict):
            return cls("", "", "")
        return cls(
            reason=str(data.get("reason") or ""),
            evidence=str(data.get("evidence") or ""),
            recommendation=str(data.get("recommendation") or ""),
        )


@dataclass
class Step:
    """One iteration of the agentic tool-loop: a tool call and its result."""

    index: int
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "arguments": self.arguments,
            "result": self.result,
            "ok": self.ok,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Step":
        return cls(
            index=int(data["index"]),
            tool=str(data["tool"]),
            arguments=dict(data.get("arguments", {})),
            result=str(data.get("result", "")),
            ok=bool(data.get("ok", True)),
        )


@dataclass
class Task:
    """A unit of work handed to a model seam.

    ``engine`` names the seam to run it through (e.g. ``mock`` or
    ``vllm-openai``); swapping it is the only change needed to run the identical
    task on a different model.
    """

    id: str
    repo_path: str
    instruction: str
    context: str = ""
    constraints: list[str] = field(default_factory=list)
    engine: str = "mock"
    watch: bool = False
    """When True the work item is a *watchable flight*: the host arms a
    file-based flight-control plane so a pilot can read the live feed and inject
    ``stop``/``guidance`` directives. Default ``False`` is a strict no-op and is
    omitted from ``to_dict`` so an unwatched task serializes byte-identically."""
    goal: Optional[str] = None
    """The pre-execution goal for this work item — a one-line, human-readable
    statement of what "done" looks like, set before the loop runs. ``None`` is
    the default. Omitted from ``to_dict`` when ``None``."""
    acceptance: Optional[list[str]] = None
    """Machine-readable acceptance criteria for this work item — one short
    string per criterion. The loop's bounded pre-finish self-check turn
    evaluates these into ``TaskResult.acceptance_outcomes``; setting a goal
    without acceptance criteria is fine (no self-check runs). ``None`` is the
    default. Omitted from ``to_dict`` when ``None``."""
    attachments: Optional[list[dict[str, Any]]] = None
    """Optional media attachments for this work item — each entry is a
    ``{"path": str, "media_type": str}`` mapping (e.g. an image or audio file to
    route into a multimodal completion). ``None`` is the default. Omitted from
    ``to_dict`` when ``None``."""
    context_packet: Optional["ContextPacket"] = None
    """The perception seam's structured interpretation of this request, or
    ``None`` when no perception front door ran. A :class:`ContextPacket` whose
    ``original`` field preserves the operator's verbatim text. Omitted from
    ``to_dict`` when ``None``."""
    flight_repo_path: Optional[str] = None
    """The OPERATOR-repo path the flight-control plane is armed at, distinct
    from ``repo_path`` (the work CWD). Set on an isolated run so the flight
    feed/control live in the operator repo — reachable by an attached surface
    and surviving worktree cleanup — while the loop still executes in the
    throwaway worktree at ``repo_path``. ``None`` (the default) means "arm at
    ``repo_path``". Omitted from ``to_dict`` when ``None``."""

    @classmethod
    def new(
        cls,
        repo_path: str,
        instruction: str,
        *,
        engine: str = "mock",
        context: str = "",
        constraints: list[str] | None = None,
        watch: bool = False,
        goal: str | None = None,
        acceptance: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        context_packet: Optional["ContextPacket"] = None,
        flight_repo_path: str | None = None,
    ) -> "Task":
        """Create a task with a fresh short id."""
        return cls(
            id=uuid.uuid4().hex[:12],
            repo_path=repo_path,
            instruction=instruction,
            engine=engine,
            context=context,
            constraints=list(constraints or []),
            watch=watch,
            goal=goal,
            acceptance=list(acceptance) if acceptance is not None else None,
            attachments=(
                [dict(entry) for entry in attachments] if attachments is not None else None
            ),
            context_packet=context_packet,
            flight_repo_path=flight_repo_path,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "repo_path": self.repo_path,
            "instruction": self.instruction,
            "context": self.context,
            "constraints": list(self.constraints),
            "engine": self.engine,
        }
        # Omit when False so an unwatched task serializes byte-identically.
        if self.watch:
            data["watch"] = True
        # goal/acceptance get the same omit-when-None treatment: a task authored
        # without them serializes byte-identically.
        if self.goal is not None:
            data["goal"] = self.goal
        if self.acceptance is not None:
            data["acceptance"] = list(self.acceptance)
        if self.attachments is not None:
            data["attachments"] = [dict(entry) for entry in self.attachments]
        if self.context_packet is not None:
            data["context_packet"] = self.context_packet.to_dict()
        if self.flight_repo_path is not None:
            data["flight_repo_path"] = self.flight_repo_path
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        raw_acceptance = data.get("acceptance")
        # Only a list-shaped payload is acceptance criteria: a bare string would
        # explode into per-character "criteria" via list() and corrupt the loop's
        # acceptance self-check. Malformed shapes degrade to None.
        acceptance = (
            [str(criterion) for criterion in raw_acceptance]
            if isinstance(raw_acceptance, list)
            else None
        )
        raw_attachments = data.get("attachments")
        # Only a list of dict-shaped entries is a valid attachments payload: a
        # bare string would explode into per-character entries via list(), and a
        # bare dict has no list shape at all. A non-dict entry anywhere in the
        # list has no "path"/"media_type" to coerce, so the whole payload
        # degrades to None rather than partially dropping entries.
        attachments = (
            [
                {
                    "path": str(entry.get("path", "")),
                    "media_type": str(entry.get("media_type", "")),
                }
                for entry in raw_attachments
            ]
            if isinstance(raw_attachments, list)
            and all(isinstance(entry, dict) for entry in raw_attachments)
            else None
        )
        # Only a mapping is a valid context_packet: a bare string / list has no
        # packet shape to coerce, so it degrades to None rather than raising.
        raw_packet = data.get("context_packet")
        context_packet = (
            ContextPacket.from_dict(raw_packet) if isinstance(raw_packet, dict) else None
        )
        return cls(
            id=str(data["id"]),
            repo_path=str(data["repo_path"]),
            instruction=str(data["instruction"]),
            context=str(data.get("context", "")),
            constraints=list(data.get("constraints", [])),
            engine=str(data.get("engine", "mock")),
            watch=bool(data.get("watch", False)),
            goal=data.get("goal"),
            acceptance=acceptance,
            attachments=attachments,
            context_packet=context_packet,
            flight_repo_path=(
                str(data["flight_repo_path"]) if data.get("flight_repo_path") is not None else None
            ),
        )


@dataclass
class TaskResult:
    """The shape every model seam produces for a driven task.

    ``branch`` / ``pr_url`` are populated by a host's git/PR handoff and stay
    ``None`` for a host that has none. ``error`` is set only when
    ``status == ERROR``.
    """

    task_id: str
    status: str
    summary: str = ""
    changed_files: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stats: WorkStats = field(default_factory=WorkStats)
    """Always-on per-work-item statistics (timing, tools used, bytes/chars
    produced). Sits beside ``usage`` (exact API token counts); together they make
    a work item's cost readable from the artifact. Unlike the optional keys this
    one is ALWAYS serialized (it is never empty for a real drive)."""
    artifacts_path: Optional[str] = None
    error: Optional[str] = None
    branch: Optional[str] = None
    pr_url: Optional[str] = None
    sub_results: list[SubResult] = field(default_factory=list)
    """Results of any sub-tasks delegated to nested child work items, in order;
    empty when this drive delegated nothing. The serialized key is OMITTED (not
    null) when the list is empty. Cost is nested-only — the parent ``usage`` is
    NOT summed with these children's."""
    command: Optional[str] = None
    """The command-template name that originated this task, or ``None`` for an
    ad-hoc instruction. Populated by the host's driver."""
    destination: Optional[str] = None
    """The goal-frame slug the work item aimed at, or ``None`` when no
    destination was set."""
    announcement: Optional[str] = None
    """The announcement text declared on arrival at the destination, or ``None``
    when no destination was set or no announcement was produced."""
    not_finished: bool = False
    """True iff the work item exhausted the step budget without calling
    ``finish`` AND without raising :class:`WorkAborted` (i.e. the model ran out
    of turns but the seam itself did not error). False on a clean finish, a
    no-tool-call terminating answer, or the aborted path. Set by the loop from
    its own return value; never inferred from ``stats.step_count`` (which counts
    tool calls, not model turns)."""
    stopped_without_finish: bool = False
    """True iff the work item ended on a **no-tool-call turn** and — even after
    the loop's one-shot finish nudge — never called ``finish``. The ``summary``
    then holds the model's trailing prose, so a caller must treat it as a
    *partial*, not an authoritative result. Orthogonal to ``not_finished`` (the
    step-budget case) and to the aborted path; a clean finish leaves both
    False."""
    role: Optional[str] = None
    """The typed-subagent role this work item ran as, or ``None`` for the default
    full-surface behavior. Omitted from ``to_dict`` when ``None``."""
    mode: Optional[str] = None
    """The driving mode this work item ran under, or ``None`` when no mode was
    selected. Set at the host's entry door, NOT by the seam/loop. Omitted from
    ``to_dict`` when ``None``."""
    acceptance_outcomes: Optional[list[dict[str, Any]]] = None
    """Per-``Task.acceptance``-criterion self-check outcomes, or ``None`` when
    the work item carried no acceptance criteria (no self-check ran). Each entry
    is a plain ``{"criterion": str, "met": bool, "evidence": str}`` record
    populated by the loop's bounded pre-finish self-check turn. These are
    ADVISORY only — they never flip ``status``; operator confirmation stays
    authoritative. Omitted from ``to_dict`` when ``None``; ``from_dict``
    tolerates malformed entries by dropping them rather than raising."""
    finish_recovered: Optional[str] = None
    """How the summary was recovered when the model's finish *transport* failed,
    or ``None`` when the finish arrived intact. ``"literal-markup"``: the model
    emitted its finish as literal tool-call text in message content and the loop
    re-parsed it as the finish payload. ``"thin-finish-synthesis"``: the finish
    carried only a headline after a read-heavy zero-write run, so one forced
    synthesis turn produced the real report. An honest degradation marker: a
    recovered report is diagnosable from the artifact, never silent."""
    memory: Optional[dict[str, Any]] = None
    """The memory exchange for this work item, or ``None`` when memory never
    armed (no store, no CLI, or disabled). A plain ``{"query", "recalled",
    "injected_chars", "lesson_recorded"}`` record: what was recalled and injected
    before work (token-capped and diagnosable from the artifact — a misleading
    memory is traceable, never silent) and whether the post-run lesson landed in
    the store. Omitted from ``to_dict`` when ``None``."""
    media: Optional[dict[str, Any]] = None
    """Delivery record for the task's media attachments, or ``None`` for an
    attachment-less run. Shape: ``{"attachments": [{"path", "status"}]}`` where
    ``status`` is ``"delivered"``, ``"dropped"``, ``"unknown"`` (no usage
    reported), or ``"bridged"`` — the token-contribution verdict, which proves
    the media ENTERED a prompt, never that the model *understood* it. Omitted
    from ``to_dict`` when ``None``."""
    senses: Optional[SensesBlock] = None
    """The perception record for this work item, or ``None`` when no perception
    seam ran (a plain cortex-only drive). A :class:`SensesBlock` of shape
    ``{mode, packet, records}`` where ``packet`` is the :class:`ContextPacket`
    the perception seam produced and ``records`` is the list of per-invocation
    :class:`SensesRecord`. Omitted from ``to_dict`` when ``None``. The packet's
    ``original`` text round-trips verbatim."""
    incompletion: Optional[IncompletionRecord] = None
    """Record of why a work item was incomplete, or ``None`` when the work item
    completed normally. An :class:`IncompletionRecord` of shape
    ``{reason, evidence, recommendation}``. Omitted from ``to_dict`` when
    ``None``."""
    continued_from: Optional[str] = None
    """The task id of the prior work item this run CONTINUES, or ``None`` for an
    ordinary run. Set by the host's continue path when the new Task was seeded
    from a persisted artifact's continuation record — one-way lineage (the old
    artifact is never mutated). Omitted from ``to_dict`` when ``None``."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "changed_files": list(self.changed_files),
            "steps": [s.to_dict() for s in self.steps],
            "usage": self.usage.to_dict(),
            "stats": self.stats.to_dict(),
            "artifacts_path": self.artifacts_path,
            "error": self.error,
            "branch": self.branch,
            "pr_url": self.pr_url,
            "command": self.command,
            "not_finished": self.not_finished,
            "stopped_without_finish": self.stopped_without_finish,
        }
        # destination and announcement are OMITTED (not emitted as null) when
        # None. This preserves the smaller shape for the no-destination path.
        # It intentionally deviates from the convention used by
        # command/pr_url/etc. which always emit their key even as null.
        if self.destination is not None:
            d["destination"] = self.destination
        if self.announcement is not None:
            d["announcement"] = self.announcement
        # role gets the same omit-when-None treatment: a role-less work item
        # serializes byte-identically to the pre-role artifact (no extra key).
        if self.role is not None:
            d["role"] = self.role
        d.update(self._extra_fields_to_dict())
        # sub_results is OMITTED (not emitted as an empty list) when no sub-task
        # was delegated — mirroring the destination/announcement pattern above.
        if self.sub_results:
            d["sub_results"] = [s.to_dict() for s in self.sub_results]
        return d

    def _extra_fields_to_dict(self) -> dict[str, Any]:
        """The omit-when-None extras added after the destination convention —
        ``mode``, ``acceptance_outcomes``, ``finish_recovered``, ``memory``,
        ``media``, ``senses``, ``incompletion``, ``continued_from``.

        Split out of :meth:`to_dict` purely to hold its cognitive complexity
        under the analysis ceiling — pure extraction, no behavior change; the
        returned partial dict is merged into :meth:`to_dict`'s result in the
        SAME key order these are inserted in here.
        """
        extra: dict[str, Any] = {}
        if self.mode is not None:
            extra["mode"] = self.mode
        if self.acceptance_outcomes is not None:
            extra["acceptance_outcomes"] = [dict(entry) for entry in self.acceptance_outcomes]
        if self.finish_recovered is not None:
            extra["finish_recovered"] = self.finish_recovered
        if self.memory is not None:
            extra["memory"] = dict(self.memory)
        if self.media is not None:
            extra["media"] = {
                "attachments": [dict(entry) for entry in self.media.get("attachments", [])]
            }
        if self.senses is not None:
            extra["senses"] = self.senses.to_dict()
        if self.incompletion is not None:
            extra["incompletion"] = self.incompletion.to_dict()
        if self.continued_from is not None:
            extra["continued_from"] = self.continued_from
        return extra

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskResult":
        return cls(
            task_id=str(data["task_id"]),
            status=str(data["status"]),
            summary=str(data.get("summary", "")),
            changed_files=list(data.get("changed_files", [])),
            steps=[Step.from_dict(s) for s in data.get("steps", [])],
            usage=Usage.from_dict(data.get("usage", {})),
            stats=WorkStats.from_dict(data.get("stats", {})),
            artifacts_path=data.get("artifacts_path"),
            error=data.get("error"),
            branch=data.get("branch"),
            pr_url=data.get("pr_url"),
            sub_results=[SubResult.from_dict(s) for s in data.get("sub_results", [])],
            command=data.get("command"),
            destination=data.get("destination"),
            announcement=data.get("announcement"),
            not_finished=bool(data.get("not_finished", False)),
            stopped_without_finish=bool(data.get("stopped_without_finish", False)),
            role=data.get("role"),
            mode=data.get("mode"),
            acceptance_outcomes=_coerce_acceptance_outcomes(data.get("acceptance_outcomes")),
            finish_recovered=data.get("finish_recovered"),
            memory=data.get("memory") if isinstance(data.get("memory"), dict) else None,
            media=data.get("media") if isinstance(data.get("media"), dict) else None,
            senses=(
                SensesBlock.from_dict(data["senses"])
                if isinstance(data.get("senses"), dict)
                else None
            ),
            incompletion=(
                IncompletionRecord.from_dict(data["incompletion"])
                if isinstance(data.get("incompletion"), dict)
                else None
            ),
            continued_from=(
                str(data["continued_from"]) if data.get("continued_from") is not None else None
            ),
        )


class WorkAborted(Exception):
    """A model seam raised mid-loop; carries the partial result.

    The bounded loop catches the seam's exception, finalizes the partial
    :class:`TaskResult` (``status=error`` plus the ``steps`` / ``usage`` /
    ``changed_files`` accumulated so far) and raises this so the host can
    persist that partial artifact + non-empty trace before surfacing the error
    to the operator. The original exception is the ``__cause__``.
    """

    def __init__(self, result: TaskResult) -> None:
        super().__init__(result.error or "drive aborted")
        self.result = result


def _as_sequence(value: Any) -> list[Any]:
    """Return ``value`` as a list when it is list-shaped, else an empty list.

    Guards the ``for entry in raw`` loops below: a malformed artifact may carry
    a scalar (or ``None``) where a list belongs, and iterating that raises
    ``TypeError`` — or, worse, a bare string iterates per character. Neither may
    abort a whole ``from_dict``.
    """
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _coerce_omissions(value: Any) -> list[str]:
    """Coerce a raw ``omissions`` payload read back from an artifact.

    A malformed artifact's ``omissions`` may be missing, ``None``, a non-string
    scalar (e.g. an int), or a bare string — none of those should crash or
    misbehave (a bare string would otherwise iterate per character). A
    list/tuple becomes ``[str(x) for x in value]``; a bare string becomes a
    single-element list; anything else (``None``, a number, a dict) becomes
    ``[]`` — tolerant of a malformed artifact, never raises.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return []


def _coerce_ack(value: Any) -> Optional[str]:
    """Coerce a raw ``ack`` payload read back from an artifact.

    A non-string value (e.g. a number or dict from a malformed artifact)
    degrades to ``None`` rather than raising downstream in a renderer doing
    ``(ack or "").strip()``. A string is stripped of surrounding whitespace and
    hard-capped to :data:`_MAX_ACK_LEN` characters; an empty/whitespace-only
    result degrades to ``None`` — no usable ack is simply absent.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:_MAX_ACK_LEN]


def _coerce_acceptance_outcomes(raw: Any) -> Optional[list[dict[str, Any]]]:
    """Coerce a raw ``acceptance_outcomes`` payload read back from an artifact.

    ``None`` in, ``None`` out (no acceptance criteria were set — the common
    case). When a list is present, each entry is expected to be a
    ``{"criterion": str, "met": bool, "evidence": str}`` mapping; a malformed
    (non-dict) entry is dropped rather than raising.

    A non-list, non-``None`` payload also degrades to ``None``: the carve
    hardens the one latent raise-path in the original (a scalar payload made
    ``for entry in raw`` raise ``TypeError``, contradicting the never-raises
    stance the docstring already claimed).
    """
    if raw is None or not isinstance(raw, (list, tuple)):
        return None
    outcomes: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        outcomes.append(
            {
                "criterion": str(entry.get("criterion", "")),
                "met": bool(entry.get("met", False)),
                "evidence": str(entry.get("evidence", "")),
            }
        )
    return outcomes
