"""The ingest contract: a fixed source registry, record validation, caps, stored ids.

Pure: no IO, no model call, no clock (a date is passed in). Nothing here raises —
a bad batch comes back as one :class:`Refusal` whose ``error`` is a
:class:`~embodiment.cli._errors.CliError` *value* the caller may raise or print.
That refusal is the recorded degradation (C3): it names the first failing line
and why. Embodiment makes no content decisions on ingest; it checks shape and
size, and stamps the fields the file may not choose.

Wire ids look like ``pocket:<rec>:<kind>:<n>``. ``:`` is outside
:data:`~embodiment.memory.HEADER_LABEL_CHARSET`, so the stored id replaces it
with ``.``. To keep that map injective, a wire id may not itself contain ``.``;
the original stays in ``metadata["wire_id"]``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Optional

from embodiment.cli._errors import EXIT_USER_ERROR, CliError
from embodiment.memory import HEADER_FIELD_LIMIT, HEADER_LABEL_CHARSET

#: The one shared private scope every external source lands in.
INGEST_SCOPE = "gwen-ingest"
INGEST_VISIBILITY = "private"
INGEST_ADDED_BY = "culture-rules"

WIRE_SEPARATOR = ":"
STORED_SEPARATOR = "."
#: ``<source>:<rec>:<kind>:<n>`` — four non-empty segments.
WIRE_SEGMENTS = 4

#: What the verb says when it names the registry in a refusal.
REGISTRY_HINT = "the ingest registry holds exactly: pocket, newspaper"


@dataclass(frozen=True)
class Source:
    """One registered producer: what it may send and how much of it."""

    name: str
    id_prefix: str
    types: frozenset
    required: tuple  # metadata provenance fields that must be non-empty strings
    optional: tuple  # metadata fields kept when present
    max_text_chars: int  # per-record cap
    max_records: int  # per-batch cap


REGISTRY: dict = {
    "pocket": Source(
        name="pocket",
        id_prefix="pocket",
        types=frozenset({"fact", "summary", "action_item"}),
        required=("recording_id", "title", "date"),
        optional=("timestamp",),
        max_text_chars=1000,
        max_records=200,
    ),
    "newspaper": Source(
        name="newspaper",
        id_prefix="newspaper",
        types=frozenset({"item"}),
        required=("issue_date", "section", "url"),
        optional=(),
        max_text_chars=600,
        max_records=400,
    ),
}

#: A metadata value is a short label, never a document.
MAX_METADATA_CHARS = 300


@dataclass(frozen=True)
class Refusal:
    """A whole batch refused: the first failing line and a CliError-shaped error."""

    line: int  # 1-based; 0 when the batch as a whole is at fault
    reason: str
    error: CliError


@dataclass(frozen=True)
class Batch:
    """A whole stamped batch: every record validated, none half-accepted."""

    source: str
    records: tuple


def source_refusal(name: object) -> Optional[CliError]:
    """None when ``name`` is registered, else a CliError value naming the registry."""
    if isinstance(name, str) and name in REGISTRY:
        return None
    shown = name if isinstance(name, str) and len(name) <= 40 else "(not a usable name)"
    return CliError(
        code=EXIT_USER_ERROR,
        message=f"source {shown!r} is not in the ingest registry",
        remediation=REGISTRY_HINT,
    )


def stored_id(wire_id: object) -> Optional[str]:
    """The charset-safe stored id for a wire id, or None if it cannot map safely.

    Deterministic (``:`` becomes ``.``), injective (a wire id with ``.`` is
    refused, so two distinct wire ids can never meet), and bounded by
    :data:`HEADER_FIELD_LIMIT` so the header never truncates it.
    """
    if not isinstance(wire_id, str):
        return None
    parts = wire_id.split(WIRE_SEPARATOR)
    if len(parts) != WIRE_SEGMENTS or not all(parts):
        return None
    if parts[0] not in REGISTRY or not parts[3].isascii() or not parts[3].isdigit():
        return None
    mapped = wire_id.replace(WIRE_SEPARATOR, STORED_SEPARATOR)
    if len(mapped) > HEADER_FIELD_LIMIT:
        return None
    if any(c not in HEADER_LABEL_CHARSET for c in mapped):
        return None
    # '.' in a segment would let two wire ids map to one stored id.
    if any(STORED_SEPARATOR in p for p in parts):
        return None
    return mapped


def _refuse(line: int, reason: str) -> Refusal:
    where = f"line {line}: " if line else ""
    hint = REGISTRY_HINT if "registry" in reason else "fix the record and resend the whole batch"
    return Refusal(
        line=line,
        reason=reason,
        error=CliError(code=EXIT_USER_ERROR, message=f"{where}{reason}", remediation=hint),
    )


def _check_source(raw: dict, source: str | None) -> tuple | str:
    """Return (metadata, source_name) or a reason string. Never raises."""
    meta = raw.get("metadata")
    if not isinstance(meta, dict):
        return "metadata must be an object"
    name = meta.get("source")
    if source_refusal(name) is not None:
        return "metadata.source is not in the ingest registry"
    if source is not None and name != source:
        return f"a batch carries one source; saw {name!r} after {source!r}"
    return meta, name


def _check_body(raw: dict, spec: Source) -> tuple | str:
    """Return (stored_id, type, text) or a reason string: the id, type and text checks."""
    sid = stored_id(raw.get("id"))
    if sid is None:
        return f"id is not a valid {spec.id_prefix}:<rec>:<kind>:<n> wire id"
    if raw["id"].split(WIRE_SEPARATOR)[0] != spec.id_prefix:
        return f"id prefix does not match source {spec.name}"
    typ = raw.get("type")
    if typ not in spec.types:
        return f"type is not allowed for {spec.name} (allowed: {', '.join(sorted(spec.types))})"
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        return "text must be a non-empty string"
    if len(text) > spec.max_text_chars:
        return f"text is over the {spec.max_text_chars}-character cap for {spec.name}"
    return sid, typ, text


def _check_metadata(meta: dict, spec: Source) -> dict | str:
    """Return the kept provenance fields, or a reason string for the first bad one."""
    kept: dict = {}
    for key in spec.required + spec.optional:
        val = meta.get(key)
        if val is None and key in spec.optional:
            continue
        if not isinstance(val, str) or not val.strip():
            return f"metadata.{key} is required for {spec.name} and must be a non-empty string"
        if len(val) > MAX_METADATA_CHARS:
            return f"metadata.{key} is over {MAX_METADATA_CHARS} characters"
        kept[key] = val
    return kept


def _check_record(raw: object, source: str | None) -> tuple | str:
    """Return (source_name, stamped_record) or a reason string. Never raises."""
    if not isinstance(raw, dict):
        return "a record must be a JSON object"
    head = _check_source(raw, source)
    if isinstance(head, str):
        return head
    meta, name = head
    spec = REGISTRY[name]
    body = _check_body(raw, spec)
    if isinstance(body, str):
        return body
    sid, typ, text = body
    fields = _check_metadata(meta, spec)
    if isinstance(fields, str):
        return fields
    kept = {"source": name, "wire_id": raw["id"], **fields}
    record = {"id": sid, "text": text, "type": typ, "metadata": kept}
    sup = raw.get("supersedes")
    if sup is not None:
        ssid = stored_id(sup)
        if ssid is None or sup.split(WIRE_SEPARATOR)[0] != spec.id_prefix:
            return "supersedes is not a valid wire id of the same source"
        record["supersedes"] = ssid
    return name, record


def _parse_line(number: int, line: object) -> object | Refusal:
    """The decoded JSON value of one line, or the Refusal for a line that is not usable."""
    if not isinstance(line, str):
        return _refuse(number, "a line must be text")
    try:
        return json.loads(line)
    except (ValueError, RecursionError):
        return _refuse(number, "line is not valid JSON")


def _admission_reason(record: dict, source: str, count: int, seen: dict) -> Optional[str]:
    """Why this checked record cannot join the batch (cap, duplicate id), else None."""
    cap = REGISTRY[source].max_records
    if count + 1 > cap:
        return f"batch is over the {cap}-record cap for {source}"
    if record["id"] in seen:
        return f"duplicate id (first seen on line {seen[record['id']]})"
    return None


def validate_batch(lines: Iterable[str], *, created: str | None = None) -> Batch | Refusal:
    """Validate every NDJSON line before returning anything.

    Whole batch or one refusal naming the first failing line (1-based, blank
    lines count but are skipped). Stamping forces visibility, scope and
    ``added_by`` whatever the input says; ``created`` (a date the caller owns)
    is stamped when given.
    """
    try:
        items = list(lines)
    except Exception:  # noqa: BLE001  # degrade, never raise
        return _refuse(0, "the batch could not be read")
    source: Optional[str] = None
    records: list = []
    seen: dict = {}
    for number, line in enumerate(items, start=1):
        if isinstance(line, str) and not line.strip():
            continue
        raw = _parse_line(number, line)
        if isinstance(raw, Refusal):
            return raw
        checked = _check_record(raw, source)
        if isinstance(checked, str):
            return _refuse(number, checked)
        source, record = checked
        reason = _admission_reason(record, source, len(records), seen)
        if reason is not None:
            return _refuse(number, reason)
        seen[record["id"]] = number
        record["visibility"] = INGEST_VISIBILITY
        record["scope"] = INGEST_SCOPE
        record["added_by"] = INGEST_ADDED_BY
        if created:
            record["created"] = created
        records.append(record)
    if source is None:
        return _refuse(0, "the batch holds no records")
    return Batch(source=source, records=tuple(records))
