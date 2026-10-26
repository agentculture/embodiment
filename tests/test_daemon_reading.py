"""The reading routes behind the guard, and status.reading (embodiment#107, plan t7).

Fakes only, synthetic items: the library stand-in and the daemon harness of
``tests/test_daemon_library.py``, the real HTTP server and its guard. The senses seam is
a fake that fails on ANY call: reading aloud asks no model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.contract import ModelResponse
from embodiment.http import server as s
from tests.test_daemon_app import REPLY
from tests.test_daemon_library import (
    PLANTED,
    PLANTED_FORGET,
    FakeLibrary,
    GuardMemory,
    ToolScript,
    _build,
    _call,
    _item,
    _lane,
)
from tests.test_daemon_library import harness as _harness
from tests.test_http_server import PUBLIC_HOST, _Asserted
from tests.test_http_server import build as build_server

#: The daemon tests' fixture, reused (pytest finds a fixture by its module attribute).
harness = _harness

BOOK = _item(31, title="Synthetic Book", sha="b" * 64)
PARAGRAPHS = [
    {"heading": "One", "text": "First paragraph, verbatim: <b>not markup</b> & more."},
    {"heading": "One", "text": "Second paragraph."},
    {"heading": "Two", "text": "Third paragraph."},
]
TEXTS = [p["text"] for p in PARAGRAPHS]


class NoModel:
    """A senses fake that fails on any call."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, messages: Any, tools: Any = None) -> ModelResponse:
        self.calls += 1
        raise AssertionError("reading aloud must not call a model")


def _library() -> FakeLibrary:
    return FakeLibrary({BOOK: list(PARAGRAPHS)})


class Page:
    """The real server over a daemon's reading controls."""

    def __init__(self, controls: Any, **kw: Any) -> None:
        self.built = build_server(
            controls=s.Controls(
                reading_list=controls.reading_list,
                reading_next=controls.reading_next,
                reading_heard=controls.reading_heard,
                reading_clear=controls.reading_clear,
            ),
            **kw,
        )

    def get(self, path: str, **hdr: Optional[str]) -> tuple[int, Any]:
        status, body = self.built.request("GET", path, hdrs=self.built.headers(**hdr))
        return status, _json(body)

    def post(self, path: str, payload: Any = None, **hdr: Optional[str]) -> tuple[int, Any]:
        headers = self.built.headers(Content_Type="application/json", **hdr)
        body = json.dumps({} if payload is None else payload).encode()
        status, raw = self.built.request("POST", path, body=body, hdrs=headers)
        return status, _json(raw)

    def close(self) -> None:
        self.built.server.shutdown(2.0)


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


@pytest.fixture
def rig(harness: Any, tmp_path: Path):
    model = NoModel()
    h = _build(harness, tmp_path, complete=model, library=_library())
    page = Page(h.app.controls())
    yield h, page, model, tmp_path
    page.close()


def _positions_file(h: Any) -> Path:
    return Path(h.app._state.dir) / "reading-positions.json"


class TestAWholeRead:
    def test_three_paragraphs_one_stale_report(self, rig: Any) -> None:
        h, page, model, _ = rig
        served = []
        first = None
        for n in range(3):
            status, unit = page.get("/api/reading/31/next")
            assert status == 200, unit
            served.append(unit["text"])
            assert unit["title"] == "Synthetic Book"
            if n == 0:
                first = unit["position"]
            status, heard = page.post("/api/reading/31/heard", {"position": unit["position"]})
            assert status == 200 and heard["moved"] is True
            if n == 1:  # a stale report: the first unit again
                status, stale = page.post("/api/reading/31/heard", {"position": first})
                assert status == 200 and stale["moved"] is False
        assert served == TEXTS  # the source text, verbatim
        status, end = page.get("/api/reading/31/next")
        assert (status, end.get("done")) == (200, True)
        reading = h.app.status()["reading"]
        assert (reading["served"], reading["heard"], reading["refused"]) == (3, 3, 1)
        assert model.calls == 0

    def test_status_without_a_library_is_unconfigured(self, harness: Any, tmp_path: Path) -> None:
        h = _build(harness, tmp_path, lane=None)
        controls = h.app.controls()
        assert controls.reading_next is None and controls.reading_list is None
        assert h.app.status()["reading"] == {"configured": False}
        page = Page(controls)
        try:
            assert page.get("/api/reading")[0] == 503
            assert page.get("/api/reading/31/next")[0] == 503
        finally:
            page.close()

    def test_the_listing_and_clear(self, rig: Any) -> None:
        _, page, _, _ = rig
        page.get("/api/reading/31/next")
        status, body = page.get("/api/reading")
        assert status == 200
        (entry,) = body["positions"]
        assert entry["item_id"] == "31" and entry["gone"] is False
        assert {"sha256", "section", "paragraph", "part", "updated_at"} <= set(entry)
        assert page.post("/api/reading/31/clear") == (200, {"cleared": True})
        assert page.get("/api/reading")[1] == {"positions": []}

    def test_a_seek_re_pins_the_cursor(self, rig: Any) -> None:
        h, page, _, _ = rig
        status, unit = page.get("/api/reading/31/next?section=1&paragraph=0")
        assert status == 200 and unit["text"] == TEXTS[2] and unit["section"] == 1
        assert page.post("/api/reading/31/heard", {"position": unit["position"]})[1]["moved"]
        assert h.app.status()["reading"]["seeks"] == 1


class TestRefusals:
    def test_next_and_heard_need_the_secret_and_a_refused_heard_leaves_the_file(
        self, rig: Any
    ) -> None:
        h, page, _, _ = rig
        unit = page.get("/api/reading/31/next")[1]
        before = _positions_file(h).read_bytes()
        status, _ = page.get("/api/reading/31/next", Authorization=None)
        assert status in (401, 403)
        status, _ = page.post(
            "/api/reading/31/heard", {"position": unit["position"]}, Authorization=None
        )
        assert status in (401, 403)
        assert _positions_file(h).read_bytes() == before
        assert h.app.status()["reading"]["heard"] == 0

    def test_the_public_host_refuses_without_a_valid_assertion(
        self, harness: Any, tmp_path: Path
    ) -> None:
        h = _build(harness, tmp_path, complete=NoModel(), library=_library())
        page = Page(
            h.app.controls(),
            public_hostname=PUBLIC_HOST,
            assertion_verifier=lambda token: _Asserted(valid=False),
        )
        try:
            for hdr in ({}, {"Cf_Access_Jwt_Assertion": "BAD"}):
                status, _ = page.get("/api/reading/31/next", Host=PUBLIC_HOST, **hdr)
                assert status in (401, 403), hdr
                status, _ = page.post(
                    "/api/reading/31/heard", {"position": "x"}, Host=PUBLIC_HOST, **hdr
                )
                assert status in (401, 403), hdr
            assert h.app.status()["reading"]["units_served"] == 0
        finally:
            page.close()

    def test_a_valid_assertion_is_admitted_on_the_public_host(
        self, harness: Any, tmp_path: Path
    ) -> None:
        h = _build(harness, tmp_path, complete=NoModel(), library=_library())
        page = Page(
            h.app.controls(),
            public_hostname=PUBLIC_HOST,
            assertion_verifier=lambda token: _Asserted(valid=True),
        )
        try:
            status, unit = page.get(
                "/api/reading/31/next",
                Host=PUBLIC_HOST,
                Authorization=None,
                Cf_Access_Jwt_Assertion="SYNTHETIC",
            )
            assert status == 200, unit
        finally:
            page.close()


class TestCodes:
    def test_not_found_gone_and_unavailable(self, rig: Any) -> None:
        h, page, _, _ = rig
        assert page.get("/api/reading/999/next")[0] == 404
        assert page.get("/api/reading/bad%20id/next")[0] == 404
        page.get("/api/reading/31/next")
        h.app._library.catalog._items = ()  # the library dropped it
        h.app._library.catalog._by_id = {}
        status, body = page.get("/api/reading/31/next")
        assert status == 410 and body["error"]["code"] == "library-item-gone"
        assert page.get("/api/reading")[1]["positions"][0]["gone"] is True

    def test_a_catalog_never_listed_is_unavailable(self, harness: Any, tmp_path: Path) -> None:
        h = _build(harness, tmp_path, complete=NoModel(), library=_library(), tick=False)
        page = Page(h.app.controls())
        try:
            status, body = page.get("/api/reading/31/next")
            assert status == 503 and body["error"]["code"] == "library_unavailable"
        finally:
            page.close()

    def test_bad_requests(self, rig: Any) -> None:
        _, page, _, _ = rig
        assert page.get("/api/reading/31/next?section=-1")[0] == 400
        assert page.get("/api/reading/31/next?section=a")[0] == 400
        assert page.get("/api/reading/31/next?x=1")[0] == 400
        assert page.post("/api/reading/31/heard", {"position": 3})[0] == 400
        assert page.post("/api/reading/31/next")[0] == 405
        assert page.get("/api/reading/31/heard")[0] == 405
        assert page.get("/api/reading/31/other")[0] == 404


class TestTheWriteGuardHolds:
    def test_a_turn_holding_read_text_cannot_forget_unasked(
        self, harness: Any, tmp_path: Path
    ) -> None:
        planted = _item(21, title="Synthetic web clipping")
        library = FakeLibrary({planted: [{"text": PLANTED}]})
        memory = GuardMemory()
        script = ToolScript(
            _call("read_item", item_id="21"),
            _call("forget", fact=PLANTED_FORGET),
            ModelResponse(content=REPLY),
        )
        h = _build(harness, tmp_path, complete=script, memory=memory, lane=_lane(library))
        h.app.controls().chat("read me the clipping about golf")
        assert memory.forgotten == []
        assert h.app.status()["memory"]["library_guarded_writes"] == 1
