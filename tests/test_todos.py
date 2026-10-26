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
        ("I've done the plumber call", True),
        ("I did the shopping", True),
        ("that's done", True),
        ("reopen the milk one", True),
        ("סמני שהתקשרתי לדנה", True),
        ("עשיתי את הקניות", True),
        ("בוצע", True),
        # questions and requests to look never permit a mark (review of PR #103)
        ("what haven't I done yet?", False),
        ("check my todos", False),
        ("what's still open from Tuesday?", False),
        ("מה נשאר לי לעשות?", False),
        ("מה עשיתי היום?", False),
        ("is the dentist one complete?", False),
        ("", False),
    ],
)
def test_what_counts_as_asking_to_mark(words, asks):
    assert t.user_asks_to_mark(words) is asks


def test_an_unreadable_state_file_is_set_aside_not_overwritten(tmp_path):
    """Review of PR #103: the next save used to land over it, losing every mark."""
    path = tmp_path / "s.json"
    path.write_text('{"a": {"done": true, "at": 1, "by": "operator"')  # cut off
    states = t.TodoStates(path)
    assert states.get("a")["done"] is False
    assert states.set("b", True, t.BY_OPERATOR) is not None
    kept = tmp_path / "s.json.unreadable"
    assert kept.read_text().startswith('{"a": {"done": true')


# ── Gwen's marks against library lines (embodiment#105 t5) ──────────────────


class _Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def _line(i, done=False, done_at=None, by=None):
    return {"id": i, "done": done, "done_at": done_at, "by": by}


def test_a_mark_shows_at_once_then_the_library_confirms_it(tmp_path):
    clock = _Clock()
    states = t.TodoStates(tmp_path / "s.json", clock=clock)
    at = states.mark("a", True, t.BY_GWEN)
    assert at == 1000.0
    shown, due, c = states.reconcile([_line("a")])
    assert shown == [{"id": "a", "done": True, "done_at": 1000.0, "done_by": "gwen"}]
    assert [d["id"] for d in due] == ["a"]
    assert c["pending"] == 1
    assert c["confirmed"] == 0
    shown, due, c = states.reconcile([_line("a", True, 1001.0, "operator")])
    assert shown[0] == {"id": "a", "done": True, "done_at": 1001.0, "done_by": "operator"}
    assert due == []
    assert c["confirmed"] == 1
    assert c["pending"] == 0


def test_a_library_line_older_than_the_mark_does_not_confirm(tmp_path):
    states = t.TodoStates(tmp_path / "s.json", clock=_Clock())
    states.mark("a", True, t.BY_GWEN)
    shown, due, _ = states.reconcile([_line("a", True, 999.0, "operator")])
    assert shown[0]["done_at"] == 1000.0
    assert len(due) == 1


def test_a_flip_rendered_to_the_second_confirms_the_mark_it_carries(tmp_path):
    # The event's at is 1000.7; culture-rules renders done_at to the second: 1000.0.
    states = t.TodoStates(tmp_path / "s.json", clock=_Clock(1000.7))
    states.mark("a", True, t.BY_GWEN)
    assert states.shown([_line("a", True, 1000.0, "operator")])[0]["done_by"] == "operator"
    _, due, c = states.reconcile([_line("a", True, 1000.0, "operator")])
    assert due == []
    assert c["confirmed"] == 1
    assert c["pending"] == 0


def test_a_newer_library_flip_the_other_way_wins(tmp_path):
    states = t.TodoStates(tmp_path / "s.json", clock=_Clock())
    states.mark("a", True, t.BY_GWEN)
    shown, due, c = states.reconcile([_line("a", False, 1005.0, "operator")])
    assert shown[0]["done"] is False
    assert due == []
    assert c["pending"] == 0
    assert c["confirmed"] == 0
    assert c["superseded"] == 1


def test_an_unconfirmed_mark_is_resent_each_check_then_expires_after_an_hour(tmp_path):
    clock = _Clock()
    states = t.TodoStates(tmp_path / "s.json", clock=clock)
    states.mark("a", True, t.BY_GWEN)
    for n in (1, 2, 3):
        clock.now += 60
        _, due, c = states.reconcile([_line("a")])
        assert len(due) == 1
        assert c["resends"] == n
        assert c["oldest_pending_age_s"] == 60 * n
    clock.now = 1000.0 + t.RESEND_WINDOW_S
    shown, due, c = states.reconcile([_line("a")])
    assert due == []
    assert c["unconfirmed"] == 1
    assert c["pending"] == 0
    assert shown[0]["done"] is False
    _, due, c = states.reconcile([_line("a")])
    assert due == []
    assert c["resends"] == 3


def test_a_mark_whose_line_disappears_is_dropped_and_counted(tmp_path):
    states = t.TodoStates(tmp_path / "s.json", clock=_Clock())
    states.mark("a", True, t.BY_GWEN)
    _, due, c = states.reconcile([_line("b")])
    assert due == []
    assert c["dropped"] == 1
    assert c["pending"] == 0
    assert states.get("a") == {"done": False, "at": None, "by": None}


def test_the_file_holds_only_current_ids_after_a_library_load(tmp_path):
    path = tmp_path / "s.json"
    old = t.TodoStates(path, clock=_Clock())
    old.set("old-memory-record-id", True, t.BY_OPERATOR)
    old.set("cur", True, t.BY_OPERATOR)
    fresh = t.TodoStates(path, clock=_Clock())
    fresh.reconcile([_line("cur")])
    import json

    assert set(json.loads(path.read_text())) == {"cur"}
    assert fresh.prune([]) == 1
    assert json.loads(path.read_text()) == {}
    assert (path.stat().st_mode & 0o777) == 0o600


def test_lines_may_be_objects(tmp_path):
    from types import SimpleNamespace

    states = t.TodoStates(tmp_path / "s.json", clock=_Clock())
    shown, _, _ = states.reconcile([SimpleNamespace(id="a", done=True, done_at=5.0, by="x")])
    assert shown == [{"id": "a", "done": True, "done_at": 5.0, "done_by": "x"}]


def test_shown_is_a_read_only_view_of_reconcile(tmp_path):
    clock = _Clock()
    states = t.TodoStates(tmp_path / "s.json", clock=clock)
    states.mark("a", True, t.BY_GWEN)
    lines = [_line("a"), _line("b", True, 900.0, "operator")]
    for _ in range(3):
        assert states.shown(lines) == [
            {"id": "a", "done": True, "done_at": 1000.0, "done_by": "gwen"},
            {"id": "b", "done": True, "done_at": 900.0, "done_by": "operator"},
        ]
    c = states.counters()
    assert (c["sent"], c["pending"], c["resends"], c["confirmed"]) == (1, 1, 0, 0)
    # a line that agrees shows the library's state, but only reconcile confirms it
    agreed = [_line("a", True, 1001.0, "operator")]
    assert states.shown(agreed)[0]["done_by"] == "operator"
    assert states.counters()["confirmed"] == 0
    assert states.counters()["pending"] == 1
    # an absent line drops nothing in the view
    assert states.shown([]) == []
    assert states.counters()["dropped"] == 0
    clock.now += t.RESEND_WINDOW_S
    assert states.shown([_line("a")])[0]["done"] is False, "past the window the library shows"
    shown, _, c = states.reconcile(agreed)
    assert shown == states.shown(agreed)
    assert c["confirmed"] == 1


def test_a_mark_that_reopens_a_to_do_is_confirmed_when_the_line_reads_open(tmp_path):
    # Code review of #109: an open line carries no done_at (parse_checklist stamps done
    # lines only), so a re-open mark used to be re-sent for an hour and never confirmed.
    states = t.TodoStates(tmp_path / "s.json", clock=_Clock())
    states.mark("a", False, t.BY_OPERATOR)
    _, due, c = states.reconcile([_line("a", True, 900.0, "operator")])
    assert len(due) == 1  # the library still shows it done: re-send
    _, due, c = states.reconcile([_line("a", False, None, None)])
    assert due == []
    assert c["confirmed"] == 1
    assert c["pending"] == 0


def test_an_incomplete_read_keeps_a_mark_whose_line_is_only_missing(tmp_path):
    # An unreadable to-do item hides its lines; its marks are not dropped for that.
    states = t.TodoStates(tmp_path / "s.json", clock=_Clock())
    states.mark("a", True, t.BY_GWEN)
    _, _, c = states.reconcile([_line("b")], prune=False)
    assert c["dropped"] == 0
    assert states.get("a")["done"] is True
