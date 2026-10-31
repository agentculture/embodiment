"""Personas: who Gwen is for a conversation, and the private file that stores them.

Pure data plus file IO. No daemon imports, and no prompt text here: a later task
composes a persona into a prompt. ``translate_via`` and ``default_role`` are gateway
role names, read from the persona and nowhere else; nothing in this module takes or
inspects a model id.

The built-in Gwen persona is read-only and represents today's behaviour exactly
(Hebrew, no length steer, plain text, no translation, mesh on, the ``senses`` role,
no persona text), so that composing it later yields a byte-identical prompt.
``language`` is ``"he"``, ``"en"`` or ``"none"`` (the string ``"none"`` means no
language line at all).

Storage mirrors ``daemon/reading.py``: ``personas.json`` is 0600, written to a temp
file in the same directory and swapped in with ``os.replace``. A corrupt or unreadable
file degrades to the built-in persona with a recorded, speech-free reason; the store
never raises on load.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 1
BUILTIN_ID = "gwen"

LANGUAGES = ("he", "en", "none")
LENGTHS = ("brief", "conversational", "detailed", "unset")

# Persona text is operator-authored and rides in every prompt for the conversation, so it
# is capped: 4,000 characters is about one printed page, ample for a character sketch,
# and bounds both the prompt share and the size of the private file.
MAX_TEXT_CHARS = 4000
MAX_NAME_CHARS = 80
MAX_PERSONAS = 200  # bounds the file; far above any realistic hand-made set

_ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")  # a role name; a model id has "/" or "."
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_FIELDS = (
    "name",
    "language",
    "length",
    "allow_formatting",
    "translate_via",
    "mesh_enabled",
    "text",
    "default_role",
)


class PersonaError(ValueError):
    """A refused persona operation; ``code`` is a short stable token."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    language: str = "he"
    length: str = "unset"
    allow_formatting: bool = False
    translate_via: Optional[str] = None
    mesh_enabled: bool = True
    text: str = ""
    default_role: str = "senses"

    @property
    def builtin(self) -> bool:
        return self.id == BUILTIN_ID

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, **{f: getattr(self, f) for f in _FIELDS}}


_BUILTIN = Persona(id=BUILTIN_ID, name="Gwen")


def builtin_persona() -> Persona:
    return _BUILTIN


def _check(fields: dict[str, Any]) -> None:
    """Raise PersonaError for the first invalid field value."""
    name = fields["name"]
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_CHARS:
        raise PersonaError("bad_name", f"name must be 1-{MAX_NAME_CHARS} characters")
    if fields["language"] not in LANGUAGES:
        raise PersonaError("bad_language", f"language must be one of {LANGUAGES}")
    if fields["length"] not in LENGTHS:
        raise PersonaError("bad_length", f"length must be one of {LENGTHS}")
    for flag in ("allow_formatting", "mesh_enabled"):
        if not isinstance(fields[flag], bool):
            raise PersonaError("bad_flag", f"{flag} must be true or false")
    via = fields["translate_via"]
    if via is not None and not (isinstance(via, str) and _ROLE_RE.match(via)):
        raise PersonaError("bad_translate_via", "translate_via must be a role name or null")
    role = fields["default_role"]
    if not (isinstance(role, str) and _ROLE_RE.match(role)):
        raise PersonaError("bad_default_role", "default_role must be a role name")
    text = fields["text"]
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise PersonaError("bad_text", f"text must be at most {MAX_TEXT_CHARS} characters")


def _build(persona_id: str, fields: dict[str, Any]) -> Persona:
    _check(fields)
    return Persona(id=persona_id, **{f: fields[f] for f in _FIELDS})


class PersonaStore:
    """Operator-made personas in a 0600 JSON file, plus the read-only built-in."""

    def __init__(self, path: Any) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._personas: Optional[dict[str, Persona]] = None
        self.degradations: list[str] = []

    # -- loading -----------------------------------------------------------

    def _degrade(self, reason: str) -> None:
        self.degradations.append(reason)

    def _load(self) -> dict[str, Persona]:
        if self._personas is not None:
            return self._personas
        out: dict[str, Persona] = {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, NotADirectoryError):
            data = None
        except (OSError, ValueError):
            self._degrade("personas_unreadable")
            data = None
        if data is not None:
            records = self._records(data)
            for rec in records:
                persona = self._parse(rec)
                if persona is None:
                    self._degrade("persona_record_dropped")
                elif persona.id in out:
                    self._degrade("persona_record_duplicate")
                else:
                    out[persona.id] = persona
        self._personas = out
        return out

    def _records(self, data: Any) -> list[Any]:
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            self._degrade("personas_schema_unsupported")
            return []
        records = data.get("personas")
        if not isinstance(records, list):
            self._degrade("personas_schema_unsupported")
            return []
        return records[:MAX_PERSONAS]

    @staticmethod
    def _parse(rec: Any) -> Optional[Persona]:
        if not isinstance(rec, dict):
            return None
        pid = rec.get("id")
        if not isinstance(pid, str) or not _ID_RE.match(pid) or pid == BUILTIN_ID:
            return None
        base = _BUILTIN.to_dict()
        fields = {f: rec.get(f, base[f]) for f in _FIELDS}
        try:
            return _build(pid, fields)
        except PersonaError:
            return None

    # -- reading -----------------------------------------------------------

    def list(self) -> list[Persona]:
        with self._lock:
            return [_BUILTIN, *self._load().values()]

    def get(self, persona_id: str) -> Optional[Persona]:
        if persona_id == BUILTIN_ID:
            return _BUILTIN
        with self._lock:
            return self._load().get(persona_id)

    # -- writing -----------------------------------------------------------

    def create(self, persona_id: Optional[str] = None, **fields: Any) -> Persona:
        with self._lock:
            personas = self._load()
            pid = persona_id if persona_id is not None else f"p-{uuid.uuid4().hex[:12]}"
            if pid == BUILTIN_ID:
                raise PersonaError("builtin_read_only", "the built-in persona cannot be replaced")
            if not _ID_RE.match(pid):
                raise PersonaError("bad_id", "id must be 1-64 of letters, digits, _ or -")
            if pid in personas:
                raise PersonaError("exists", "a persona with that id exists")
            if len(personas) >= MAX_PERSONAS:
                raise PersonaError("too_many", f"at most {MAX_PERSONAS} personas")
            unknown = set(fields) - set(_FIELDS)
            if unknown:
                raise PersonaError("unknown_field", "unknown field")
            if "name" not in fields:
                raise PersonaError("bad_name", "name is required")
            base = _BUILTIN.to_dict()
            persona = _build(pid, {f: fields.get(f, base[f]) for f in _FIELDS})
            personas[pid] = persona
            self._commit(personas, pid, None)
            return persona

    def update(self, persona_id: str, **fields: Any) -> Persona:
        with self._lock:
            if persona_id == BUILTIN_ID:
                raise PersonaError("builtin_read_only", "the built-in persona is read-only")
            personas = self._load()
            current = personas.get(persona_id)
            if current is None:
                raise PersonaError("not_found", "no such persona")
            if set(fields) - set(_FIELDS):
                raise PersonaError("unknown_field", "unknown field (the id cannot change)")
            new = _build(persona_id, {**current.to_dict(), **fields})
            personas[persona_id] = new
            self._commit(personas, persona_id, current)
            return new

    def delete(self, persona_id: str) -> bool:
        """True when a persona was removed, False when there was none."""
        with self._lock:
            if persona_id == BUILTIN_ID:
                raise PersonaError("builtin_read_only", "the built-in persona cannot be deleted")
            personas = self._load()
            current = personas.pop(persona_id, None)
            if current is None:
                return False
            self._commit(personas, persona_id, current)
            return True

    def _commit(self, personas: dict[str, Persona], pid: str, previous: Optional[Persona]) -> None:
        """Save; on failure restore the in-memory state, record, and raise."""
        if self._save(personas):
            return
        if previous is None:
            personas.pop(pid, None)
        else:
            personas[pid] = previous
        self._degrade("personas_save_failed")
        raise PersonaError("save_failed", "the personas file could not be written")

    def _save(self, personas: dict[str, Persona]) -> bool:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "personas": [p.to_dict() for p in personas.values()],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".personas-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self._path)
            except OSError:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            return False
        return True
