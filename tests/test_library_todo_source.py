"""TodoSource: re-read by sha256, drop what left, keep the last good on failure."""

from __future__ import annotations

from embodiment.library.client import LibraryError, LibraryItem
from embodiment.library.todo_source import TodoSource


def _item(i, sha, title="List", created="2026-01-01"):
    return LibraryItem(i, sha, "todo", title, "", "txt", 1, created)


class FakeClient:
    """GET-only fake: a catalog and file bodies keyed by id."""

    def __init__(self):
        self.items = {}
        self.files = {}
        self.list_kinds = []
        self.fetched = []
        self.list_error = None
        self.fetch_error = None

    def list_items(self, kind=None, q=None):
        self.list_kinds.append(kind)
        if self.list_error:
            raise LibraryError(self.list_error)
        return list(self.items.values())

    def fetch_file(self, item_id):
        self.fetched.append(item_id)
        if self.fetch_error:
            raise LibraryError(self.fetch_error)
        return self.files[item_id]

    def put(self, i, sha, text, **kw):
        self.items[i] = _item(i, sha, **kw)
        self.files[i] = text.encode()


def _parse(meta, data):
    return [ln for ln in data.decode().splitlines() if ln]


def _source(client):
    now = [0.0]
    src = TodoSource(client, parse=_parse, clock=lambda: now[0])
    return src, now


def test_replaced_item_is_reread_unchanged_is_not():
    c = FakeClient()
    c.put("a", "s1", "one\ntwo")
    c.put("b", "s2", "x")
    src, now = _source(c)
    src.check()
    assert c.fetched == ["a", "b"]
    assert c.list_kinds == ["todo"]
    now[0] = 30
    src.check()
    assert c.fetched == ["a", "b"]
    c.put("a", "s1b", "one\ntwo\nthree")
    now[0] = 60
    src.check()
    assert c.fetched == ["a", "b", "a"]
    assert {g["id"]: g["lines"] for g in src.groups()}["a"] == ["one", "two", "three"]


def test_leaving_item_and_line_disappear_new_line_appears():
    c = FakeClient()
    c.put("a", "s1", "one\ntwo")
    c.put("b", "s2", "x")
    src, _ = _source(c)
    src.check()
    del c.items["b"]
    c.put("a", "s1b", "two\nnew")
    st = src.check()
    groups = src.groups()
    assert [g["id"] for g in groups] == ["a"]
    assert groups[0]["lines"] == ["two", "new"]
    assert st["items"] == 1
    assert st["lines"] == 2


def test_failed_list_keeps_last_good():
    c = FakeClient()
    c.put("a", "s1", "one")
    src, _ = _source(c)
    src.check()
    c.list_error = "library_unreachable"
    st = src.check()
    assert st["available"] is False
    assert st["code"] == "library_unreachable"
    assert st["failures"] == 1
    assert st["checks"] == 2
    assert st["items"] == 1
    assert st["lines"] == 1
    assert src.groups()[0]["lines"] == ["one"]
    c.list_error = None
    st = src.check()
    assert st["available"] is True
    assert st["code"] is None


def test_failed_fetch_keeps_old_version_and_retries():
    c = FakeClient()
    c.put("a", "s1", "one")
    src, _ = _source(c)
    src.check()
    c.put("a", "s2", "changed")
    c.fetch_error = "library_timeout"
    st = src.check()
    # one unreadable item does not take the source down (code review of #109)
    assert st["available"] is True
    assert st["code"] == "library_timeout"
    assert st["unreadable"] == 1
    assert st["complete"] is True  # it still has a good version
    assert src.groups()[0]["lines"] == ["one"]
    c.fetch_error = None
    st = src.check()
    assert st["available"] is True
    assert src.groups()[0]["lines"] == ["changed"]


def test_parse_failure_and_no_client_are_reported_not_raised():
    c = FakeClient()
    c.put("a", "s1", "one")

    def boom(meta, data):
        raise ValueError("secret text")

    src = TodoSource(c, parse=boom, clock=lambda: 0.0)
    st = src.check()
    assert st["available"] is True
    assert st["code"] == "todo_parse_failed"
    assert st["complete"] is False  # no good version: its lines are not known
    assert src.groups() == []
    none = TodoSource(None, parse=_parse, clock=lambda: 0.0)
    assert none.check()["available"] is False
    assert none.groups() == []


def test_groups_newest_first():
    c = FakeClient()
    c.put("a", "s1", "x", created="2026-01-01")
    c.put("b", "s2", "y", created="2026-03-01")
    src, _ = _source(c)
    src.check()
    assert [g["id"] for g in src.groups()] == ["b", "a"]


def test_one_bad_item_leaves_the_others_and_is_not_reread_until_it_changes():
    c = FakeClient()
    c.put("good", "s1", "one")
    c.put("bad", "b1", "BAD")

    def parse(meta, data):
        if meta.id == "bad":
            raise ValueError("not utf-8")
        return _parse(meta, data)

    src = TodoSource(c, parse=parse, clock=lambda: 0.0)
    st = src.check()
    assert st["available"] is True
    assert st["unreadable"] == 1
    assert [g["id"] for g in src.groups()] == ["good"]
    fetched = len(c.fetched)
    src.check()
    assert len(c.fetched) == fetched  # the same bytes are not fetched again
    c.put("bad", "b2", "fixed")
    src.check()
    assert len(c.fetched) == fetched + 1


def test_status_and_groups_hold_while_a_check_changes_the_items():
    import threading

    c = FakeClient()
    for i in range(50):
        c.put(f"i{i}", "s1", "x")
    src, _ = _source(c)
    src.check()
    stop = threading.Event()
    errors: list[BaseException] = []

    def churn():
        n = 0
        while not stop.is_set():
            n += 1
            for i in range(50):
                if (i + n) % 2:
                    c.items.pop(f"i{i}", None)
                else:
                    c.put(f"i{i}", f"s{n}", "x")
            src.check()

    t = threading.Thread(target=churn)
    t.start()
    try:
        for _ in range(2000):
            try:
                src.status()
                src.groups()
            except RuntimeError as exc:  # "dictionary changed size during iteration"
                errors.append(exc)
    finally:
        stop.set()
        t.join()
    assert errors == []
