from dataclasses import replace

import pytest

from embodiment import persona_prompt as pp
from embodiment import turn
from embodiment.daemon import app
from embodiment.personas import Persona, builtin_persona


def _p(**kw):
    return replace(Persona(id="x", name="Dana"), **kw)


def test_builtin_voice_is_system_prompt_byte_for_byte():
    assert pp.compose_base(builtin_persona(), "voice") == turn.SYSTEM_PROMPT


def test_builtin_typed_omits_speakability_and_stays_hebrew():
    typed = pp.compose_base(builtin_persona(), "typed")
    assert "ייאמר בקול רם" not in typed
    assert typed.startswith("את גוון")
    assert turn.SYSTEM_PROMPT.splitlines()[3] in typed
    assert len(typed.splitlines()) == 3


def test_bad_path():
    with pytest.raises(ValueError):
        pp.compose_base(builtin_persona(), "x")


def test_non_default_order_and_no_fixed_brevity():
    s = pp.compose_base(_p(language="en", length="unset", text="Be wry."), "voice")
    lines = s.splitlines()
    assert "Dana" in lines[0]
    assert lines[1] == "Answer in English."
    assert "aloud" in lines[2]
    assert lines[-1] == "Be wry."
    low = s.lower()
    assert "short" not in low and "brief" not in low and "קצר" not in s


def test_length_line_between_language_and_speakability():
    lines = pp.compose_base(_p(language="he", length="detailed", text="T"), "voice").splitlines()
    assert lines[1] == "עני בעברית."
    assert lines[2].startswith("אורך")
    assert "ייאמר" in lines[3] and lines[4] == "T"


def test_language_none_adds_no_language_line_and_typed_has_no_speakability():
    s = pp.compose_base(_p(language="none", text="T"), "typed")
    assert s.splitlines() == ["את Dana, בת שיחה.", "T"]


def test_typed_has_no_speakability_en():
    assert "aloud" not in pp.compose_base(_p(language="en"), "typed").split("\n", 1)[1]


def test_tool_prompt_language():
    assert pp.tool_prompt_language(builtin_persona()) == "he"
    assert pp.tool_prompt_language(_p(language="en")) == "en"
    assert pp.tool_prompt_language(_p(language="none")) == "he"


def test_english_tool_prompts_keep_obligations():
    en = pp.english_tool_prompts(_p(language="en"))
    assert set(en) == set(pp.TOOL_PROMPT_NAMES)
    for k in ("remember", "forget", "id=", "ok", "fact"):
        assert k in en["REMEMBER"]
    assert "one confirmation sentence in English" in en["REMEMBER"]
    assert "Hebrew" in pp.english_tool_prompts(_p(language="he"))["REMEMBER"]
    for k in ("search_library", "read_item", "fetch_source", "[n]", "YYYY-MM-DD"):
        assert k in en["LIBRARY"]
    assert "search_conversations" in en["CONVERSATIONS"]
    assert "list_todos" in en["TODOS"] and "set_todo_done" in en["TODOS"]


def test_tool_names_match_hebrew_originals():
    en = pp.english_tool_prompts()
    for key, he in (
        ("REMEMBER", app.REMEMBER_TOOL_PROMPT),
        ("LIBRARY", app.LIBRARY_TOOL_PROMPT),
        ("CONVERSATIONS", app.CONVERSATIONS_TOOL_PROMPT),
        ("TODOS", app.TODOS_TOOL_PROMPT),
    ):
        for tool in (
            "remember",
            "forget",
            "search_library",
            "read_item",
            "fetch_source",
            "search_conversations",
            "list_todos",
            "set_todo_done",
            "read_passage",
        ):
            assert (tool in he) == (tool in en[key]), (key, tool)
