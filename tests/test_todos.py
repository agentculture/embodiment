"""To-dos' done state is Gwen's; Gwen's two to-do tools (deviation d16)."""

from __future__ import annotations

import pytest

from embodiment.daemon import todos as t


def test_a_mark_survives_a_restart_privately(tmp_path):
    states = t.TodoStates(tmp_path / "s.json", clock=lambda: 50.0)
    assert states.get("pocket.r1.0") == {"done": False, "at": None, "by": None}
    assert states.set("pocket.r1.0", True, t.BY_OPERATOR) == {
        "done": True,
        "at": 50.0,
        "by": "operator",
    }
    again = t.TodoStates(tmp_path / "s.json")
    assert again.get("pocket.r1.0")["done"] is True
    assert (tmp_path / "s.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("todo_id", "done", "by"),
    [("../x", True, "operator"), ("a", "yes", "operator"), ("a", True, "mallory")],
)
def test_a_bad_mark_is_refused(tmp_path, todo_id, done, by):
    assert t.TodoStates(tmp_path / "s.json").set(todo_id, done, by) is None


def test_a_failed_save_keeps_the_old_state(tmp_path):
    (tmp_path / "dir").write_text("a file where the directory should be")
    states = t.TodoStates(tmp_path / "dir" / "s.json")
    assert states.set("a", True, t.BY_GWEN) is None
    assert states.get("a")["done"] is False
    assert states.save_failures == 1


def test_an_unreadable_file_reads_as_all_open(tmp_path):
    (tmp_path / "s.json").write_text("not json")
    assert t.TodoStates(tmp_path / "s.json").get("a")["done"] is False


ITEMS = [
    {"id": "a", "text": "call Dana", "title": "Standup", "date": "2026-10-23", "done": False},
    {"id": "b", "text": "buy milk", "title": "Errands", "date": "2026-10-24", "done": True},
]


def _tools(asked=True, marked=None):
    calls = []

    def set_done(todo_id, done):
        calls.append((todo_id, done))
        return marked if marked is not None else {"id": todo_id}

    listed, setter = t.build_todo_tools(
        list_items=lambda: ITEMS,
        set_done=set_done,
        asked=lambda: asked,
        render=lambda items: "|".join(i["id"] for i in items),
    )
    return listed, setter, calls


@pytest.mark.parametrize(
    ("kwargs", "ids"),
    [
        ({}, "a|b"),
        ({"status": "open"}, "a"),
        ({"status": "done"}, "b"),
        ({"date": "2026-10-24"}, "b"),
        ({"words": "dana"}, "a"),
    ],
)
def test_list_todos_filters(kwargs, ids):
    listed, _, _ = _tools()
    assert listed.fn(**kwargs) == ids


def test_list_todos_with_nothing_matching_is_fixed_text():
    listed, _, _ = _tools()
    assert listed.fn(words="unicorn") == t.NO_TODOS


def test_set_todo_done_runs_only_when_the_operator_asked():
    _, setter, calls = _tools(asked=False)
    assert setter.fn(todo_id="a") == t.NOT_ASKED
    assert calls == []
    _, setter, calls = _tools(asked=True)
    assert setter.fn(todo_id="a", done=True) == "Marked done: a."
    assert calls == [("a", True)]


def test_set_todo_done_reports_unknown_and_unsaved():
    _, setter, _ = _tools()
    setter_unknown = t.build_todo_tools(
        list_items=lambda: [], set_done=lambda i, d: None, asked=lambda: True, render=str
    )[1]
    assert setter_unknown.fn(todo_id="zz") == t.UNKNOWN_TODO
    _, setter, _ = _tools(marked={"saved": False})
    assert setter.fn(todo_id="a") == t.NOT_SAVED


def test_bad_arguments_are_a_declared_fault():
    listed, setter, _ = _tools()
    with pytest.raises(t.TodoToolError):
        listed.fn(status="maybe")
    with pytest.raises(t.TodoToolError):
        setter.fn(todo_id="../etc")


@pytest.mark.parametrize(
    ("words", "asks"),
    [
        ("mark the dentist one done", True),
        ("סמני שהתקשרתי לדנה", True),
        ("בוצע", True),
        ("what's still open from Tuesday?", False),
        ("מה נשאר לי לעשות?", False),
    ],
)
def test_what_counts_as_asking_to_mark(words, asks):
    assert t.user_asks_to_mark(words) is asks
