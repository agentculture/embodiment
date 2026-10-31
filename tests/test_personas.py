"""Persona model and private store (gwen-personas t1)."""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import stat
from pathlib import Path

import pytest

from embodiment import personas
from embodiment.personas import (
    BUILTIN_ID,
    MAX_TEXT_CHARS,
    PersonaError,
    PersonaStore,
    builtin_persona,
)


def _store(tmp_path: Path) -> PersonaStore:
    return PersonaStore(tmp_path / "personas.json")


def test_builtin_is_todays_behaviour():
    p = builtin_persona()
    assert p.id == BUILTIN_ID
    assert p.name == "Gwen"
    assert p.language == "he"
    assert p.length == "unset"
    assert p.allow_formatting is False
    assert p.translate_via is None
    assert p.mesh_enabled is True
    assert p.default_role == "senses"
    assert p.text == ""


def test_persona_is_frozen():
    persona = builtin_persona()
    with pytest.raises(dataclasses.FrozenInstanceError):
        persona.name = "x"  # type: ignore[misc]


def test_empty_store_lists_only_builtin(tmp_path):
    s = _store(tmp_path)
    assert [p.id for p in s.list()] == [BUILTIN_ID]
    assert s.degradations == []


def test_create_update_delete_survive_reload(tmp_path):
    s = _store(tmp_path)
    p = s.create(name="Noa", language="en", length="brief", text="be kind", translate_via="worker")
    assert p.id != BUILTIN_ID
    s2 = _store(tmp_path)
    got = s2.get(p.id)
    assert got == p
    upd = s2.update(p.id, name="Noa2", language="none", mesh_enabled=False)
    assert upd.language == "none"
    assert upd.mesh_enabled is False
    assert upd.text == "be kind"
    s3 = _store(tmp_path)
    assert s3.get(p.id) == upd
    assert s3.delete(p.id) is True
    assert _store(tmp_path).get(p.id) is None
    assert s3.delete(p.id) is False


def test_file_is_0600_with_schema_version(tmp_path):
    s = _store(tmp_path)
    s.create(name="A")
    path = tmp_path / "personas.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    data = json.loads(path.read_text())
    assert data["schema_version"] == personas.SCHEMA_VERSION
    assert BUILTIN_ID not in [p["id"] for p in data["personas"]]
    assert list(tmp_path.glob(".personas-*")) == []


def test_builtin_cannot_be_edited_or_deleted(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(PersonaError):
        s.update(BUILTIN_ID, name="Evil")
    with pytest.raises(PersonaError):
        s.delete(BUILTIN_ID)
    with pytest.raises(PersonaError):
        s.create(name="x", persona_id=BUILTIN_ID)
    assert s.get(BUILTIN_ID) == builtin_persona()


def test_a_stored_builtin_id_is_ignored(tmp_path):
    path = tmp_path / "personas.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "personas": [{"id": BUILTIN_ID, "name": "Evil", "text": "x"}],
            }
        )
    )
    assert PersonaStore(path).get(BUILTIN_ID) == builtin_persona()


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '{"schema_version": 99, "personas": []}',
        '{"schema_version": 1, "personas": 5}',
    ],
)
def test_corrupt_file_degrades_to_builtin_with_reason(tmp_path, content):
    path = tmp_path / "personas.json"
    path.write_text(content)
    s = PersonaStore(path)
    assert [p.id for p in s.list()] == [BUILTIN_ID]
    assert len(s.degradations) == 1
    assert isinstance(s.degradations[0], str)
    assert s.degradations[0]
    assert "not json" not in s.degradations[0]


def test_one_bad_record_is_dropped_others_kept(tmp_path):
    s = _store(tmp_path)
    good = s.create(name="Good")
    data = json.loads((tmp_path / "personas.json").read_text())
    data["personas"].append({"id": "bad", "name": "", "language": "fr"})
    (tmp_path / "personas.json").write_text(json.dumps(data))
    s2 = PersonaStore(tmp_path / "personas.json")
    assert s2.get(good.id) == good
    assert s2.get("bad") is None
    assert s2.degradations


def test_unreadable_path_never_raises(tmp_path):
    d = tmp_path / "personas.json"
    d.mkdir()
    s = PersonaStore(d)
    assert [p.id for p in s.list()] == [BUILTIN_ID]
    assert s.degradations


@pytest.mark.parametrize(
    "kw",
    [
        {"name": ""},
        {"name": "x" * 200},
        {"name": "a", "language": "fr"},
        {"name": "a", "length": "huge"},
        {"name": "a", "text": "x" * (MAX_TEXT_CHARS + 1)},
        {"name": "a", "translate_via": "Qwen/Qwen3-Embedding-0.6B"},
        {"name": "a", "default_role": "org/model"},
        {"name": "a", "allow_formatting": "yes"},
        {"name": "a", "mesh_enabled": 1},
    ],
)
def test_invalid_input_is_refused(tmp_path, kw):
    s = _store(tmp_path)
    with pytest.raises(PersonaError):
        s.create(**kw)
    assert [p.id for p in s.list()] == [BUILTIN_ID]


def test_text_at_cap_is_accepted(tmp_path):
    p = _store(tmp_path).create(name="a", text="x" * MAX_TEXT_CHARS)
    assert len(p.text) == MAX_TEXT_CHARS


def test_language_none_is_the_string_none(tmp_path):
    p = _store(tmp_path).create(name="a", language="none")
    assert p.language == "none"
    assert _store(tmp_path).get(p.id).language == "none"


def test_update_unknown_id_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(PersonaError):
        store.update("nope", name="x")


def test_update_cannot_change_id(tmp_path):
    s = _store(tmp_path)
    p = s.create(name="a")
    with pytest.raises(PersonaError):
        s.update(p.id, id="other")


def test_failed_save_rolls_back_and_records(tmp_path):
    s = PersonaStore(tmp_path / "ro" / "personas.json")
    (tmp_path / "ro").write_text("a file, so mkdir fails")
    with pytest.raises(PersonaError):
        s.create(name="a")
    assert [p.id for p in s.list()] == [BUILTIN_ID]


def test_module_never_takes_or_inspects_a_model_id():
    tree = ast.parse(Path(personas.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            assert "model" not in node.arg.lower(), node.arg
        if isinstance(node, ast.Attribute):
            assert "model" not in node.attr.lower(), node.attr
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    for n in imports:
        mod = getattr(n, "module", None) or ""
        names = [a.name for a in n.names]
        assert not any("daemon" in x for x in [mod, *names]), mod
