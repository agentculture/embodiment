"""A mesh ask Gwen claims but never made (the 2026-11-01 live run), and the fix.

Three parts, none of which trusts the model (operator, 2026-11-01: "fix, but don't trust
senses - try cortex if not bolt"):

* the claim check: a reply that says it asked the mesh, in a turn that posted no ask,
  keeps its words and gets a fixed correction, recorded and counted;
* the record: what a turn really did rides with it into the window (``[done: ...]``)
  and is written beside it in the transcript, so a later turn sees the deed, not the words;
* routing: the operator's explicit request for a mesh question runs on cortex, else
  worker, instead of the default speaker.

Every test runs on fakes: no gateway, no agentirc (the lane never builds a client).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from embodiment import mesh_asks, mesh_tools
from embodiment import voice_latency as vl
from embodiment.daemon import app as app_module
from embodiment.daemon.state import SESSIONS_DIRNAME, DaemonState
from embodiment.mesh_client import MeshConfig
from embodiment.session import ACTIONS_FIELD, Session
from tests import test_daemon_app as base
from tests.test_daemon_models import (
    SENSES,
    WORKER,
    FakeCapabilities,
    Gateway,
    _wired,
)

harness = base.harness

CORTEX = WORKER.__class__(
    role="cortex", model="unsloth/Qwen3.8-27B-NVFP4", ready=True, host="spark2"
)
REQUEST = "Please ask Culture chat, addressed to spark-associate: what are you working on?"
CLAIM_HE = "שלחתי את השאלה ל-spark-associate. אעדכן אותך כשיגיע תשובה."


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Gateway:
    fake = Gateway()
    monkeypatch.setattr(app_module.urllib.request, "urlopen", fake)
    return fake


def _lane(tmp_path: Path, *, enabled: bool = True) -> app_module.MeshLane:
    """A mesh lane that never dials: no client factory, so start() connects nothing."""
    return app_module.MeshLane(
        config=MeshConfig(enabled=enabled, host="127.0.0.1", port=9),
        book=mesh_asks.AskBook(tmp_path / "asks.json"),
        client_factory=None,
    )


def _with_mesh(harness: Any, tmp_path: Path, roles: tuple = (SENSES, CORTEX, WORKER), **kw: Any):
    return _wired(
        harness,
        tmp_path,
        capabilities=FakeCapabilities(roles=roles),
        lanes_extra={"mesh": _lane(tmp_path, enabled=kw.pop("enabled", True))},
        **kw,
    )


def _assistant_rows(h: Any) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((Path(h.app._state.dir) / SESSIONS_DIRNAME).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("role") == "assistant":
                rows.append(row)
    return rows


# ── the detectors: fixed patterns, Hebrew and English ─────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        REQUEST,
        "Please ask Culture chat this, addressed to spark-culture by name: which version?",
        "תשאלי את הרשת מה הספרייה מאנדקסת",
        "ask the other agents whether thor is up",
    ],
)
def test_an_explicit_mesh_request_is_recognised(text: str) -> None:
    assert mesh_tools.explicit_mesh_request(text)


@pytest.mark.parametrize(
    "text", ["What was in the latest newspaper?", "ask me anything", "מה שלומך?", ""]
)
def test_other_words_are_not_a_mesh_request(text: str) -> None:
    assert not mesh_tools.explicit_mesh_request(text)


LIVE_CORTEX = "השאלה נשלחה ל־spark-culture בקanal #general. התשובה תגיע עוד מעט."
LIVE_SENSES = (
    "השאלה נשלחה ל־spark-colleague בקנאל #general. התשובה תגיע עוד מעט.\n"
    "[done: asked Culture chat on #general]"
)


@pytest.mark.parametrize(
    "text",
    [
        CLAIM_HE,
        LIVE_CORTEX,  # the passive both models wrote live (2026-11-01, second run)
        "I asked Culture chat; I will update you.",
        "I have sent the question to the mesh.",
        "Your question was sent to spark-associate on #general.",
    ],
)
def test_a_claimed_ask_is_recognised(text: str) -> None:
    assert mesh_tools.claims_mesh_ask(text)


@pytest.mark.parametrize("text", ["The newspaper had three stories.", "שלחתי לך סיכום.", ""])
def test_other_replies_claim_nothing(text: str) -> None:
    assert not mesh_tools.claims_mesh_ask(text)


# ── the claim check ───────────────────────────────────────────────────────────


class TestTheClaimCheck:
    def test_a_claim_without_a_post_gets_the_correction_and_is_recorded(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        gateway.reply = CLAIM_HE
        h = _with_mesh(harness, tmp_path, roles=(SENSES,))  # nothing to route to
        answer = h.app.controls().chat("ומה עם spark-associate?")
        assert answer["ok"] is True
        assert answer["reply"].startswith(CLAIM_HE)
        assert answer["reply"].endswith(mesh_tools.CLAIM_CORRECTION_HE)
        assert h.app.status()["mesh"]["claims_unbacked"] == 1
        assert h.app.status()["degradations"][app_module.APP_MESH_CLAIM_UNBACKED] == 1
        (row,) = _assistant_rows(h)
        assert row[ACTIONS_FIELD] == ["claimed an ask on Culture chat; nothing was sent"]

    def test_a_reply_that_claims_nothing_is_untouched(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _with_mesh(harness, tmp_path)
        answer = h.app.controls().chat("מה שלומך?")
        assert answer["reply"] == base.REPLY
        assert h.app.status()["mesh"]["claims_unbacked"] == 0
        (row,) = _assistant_rows(h)
        assert ACTIONS_FIELD not in row

    def test_without_a_mesh_lane_nothing_is_checked(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        gateway.reply = CLAIM_HE
        h = _wired(harness, tmp_path)
        assert h.app.controls().chat("מה שלומך?")["reply"] == CLAIM_HE


# ── the record in the window ──────────────────────────────────────────────────


def test_the_window_shows_what_a_turn_did() -> None:
    window = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a", ACTIONS_FIELD: ["asked Culture chat on #general"]},
    ]
    prompt = app_module._compose_prompt("base", window, "")
    assert "assistant: a\n  [done: asked Culture chat on #general]" in prompt


def test_a_window_without_actions_is_byte_identical() -> None:
    window = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    prompt = app_module._compose_prompt("base", window, "")
    assert prompt == f"base\n\n{app_module.WINDOW_HEADER}\nuser: q\nassistant: a"


def test_the_session_keeps_actions_and_restores_them(tmp_path: Path) -> None:
    from tests.test_session import FakeMemory

    state = DaemonState(tmp_path / "state")
    session = Session(state, FakeMemory())
    session.add_user("q")
    session.add_assistant("a", metadata={ACTIONS_FIELD: ["used search_library", 7, ""]})
    assert session.messages()[-1] == {
        "role": "assistant",
        "content": "a",
        ACTIONS_FIELD: ["used search_library"],
    }
    again = Session(DaemonState(tmp_path / "state2"), FakeMemory())
    again.restore([("user", "q"), ("assistant", "a", ["asked Culture chat on #general"])])
    assert again.messages()[-1][ACTIONS_FIELD] == ["asked Culture chat on #general"]
    assert ACTIONS_FIELD not in again.messages()[0]


def test_a_later_turn_sees_the_earlier_claim_marked(
    harness: Any, tmp_path: Path, gateway: Gateway
) -> None:
    gateway.reply = CLAIM_HE
    h = _with_mesh(harness, tmp_path, roles=(SENSES,))
    h.app.controls().chat("ומה עם spark-associate?")
    gateway.reply = base.REPLY
    h.app.controls().chat("ומה עכשיו?")
    system = gateway.chat()[-1]["body"]["messages"][0]["content"]
    assert "[done: claimed an ask on Culture chat; nothing was sent]" in system


# ── routing an explicit request off the default speaker ───────────────────────


class TestRouting:
    def test_an_explicit_request_runs_on_cortex(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _with_mesh(harness, tmp_path)
        assert h.app.controls().chat(REQUEST)["ok"] is True
        assert gateway.models() == ["cortex"]
        assert h.app.status()["mesh"]["routed_turns"] == 1
        (row,) = _assistant_rows(h)
        assert row["chat_role"] == "cortex"
        assert row[app_module.CHAT_ROUTED_FIELD] == "mesh-request"
        assert h.app.conversation_role() == "senses"  # the conversation itself is unchanged

    def test_worker_when_cortex_is_not_ready(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        down = CORTEX.__class__(role="cortex", model="x", ready=False, host="spark2")
        h = _with_mesh(harness, tmp_path, roles=(SENSES, down, WORKER))
        h.app.controls().chat(REQUEST)
        assert gateway.models() == ["worker"]

    def test_neither_ready_stays_on_senses_and_is_recorded(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _with_mesh(harness, tmp_path, roles=(SENSES,))
        h.app.controls().chat(REQUEST)
        assert gateway.models() == ["senses"]
        assert h.app.status()["degradations"][app_module.APP_MESH_ROUTE_UNAVAILABLE] == 1

    def test_other_words_stay_on_senses(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _with_mesh(harness, tmp_path)
        h.app.controls().chat("What was in the latest newspaper?")
        assert gateway.models() == ["senses"]
        assert h.app.status()["mesh"]["routed_turns"] == 0

    def test_a_chosen_model_wins(self, harness: Any, tmp_path: Path, gateway: Gateway) -> None:
        h = _with_mesh(harness, tmp_path)
        assert h.app.set_conversation_role("senses") == "senses"  # chosen, even if default
        h.app.controls().chat(REQUEST)
        assert gateway.models() == ["senses"]

    def test_the_mesh_off_routes_nothing(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _with_mesh(harness, tmp_path, enabled=False)
        h.app.controls().chat(REQUEST)
        assert gateway.models() == ["senses"]

    def test_a_spoken_request_needs_a_measured_voice(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _with_mesh(harness, tmp_path)
        h.app.attach_ear("host", base.FakeEndpoint())
        h.app.run_turn(REQUEST)
        assert gateway.chat()[0]["body"]["model"] == "senses"  # no voice measured: not routed
        for _ in range(vl.MEASURED_MIN_SAMPLES):
            h.app.voice_latency.add_sample("worker", 1.2)
        h.app.run_turn(REQUEST)
        assert gateway.chat()[-1]["body"]["model"] == "worker"


def test_a_forged_record_line_is_stripped() -> None:
    text, count = mesh_tools.strip_action_records(LIVE_SENSES)
    assert count == 1
    assert "[done:" not in text
    assert text.startswith("השאלה נשלחה")


def test_text_without_a_record_line_is_untouched() -> None:
    assert mesh_tools.strip_action_records("a [done: x] inline mention") == (
        "a [done: x] inline mention",
        0,
    )


def test_the_live_senses_lie_is_stripped_and_corrected(
    harness: Any, tmp_path: Path, gateway: Gateway
) -> None:
    gateway.reply = LIVE_SENSES
    h = _with_mesh(harness, tmp_path)
    assert h.app.set_conversation_role("senses") == "senses"  # chosen: not routed
    answer = h.app.controls().chat(REQUEST)
    assert "[done:" not in answer["reply"]
    assert answer["reply"].endswith(mesh_tools.CLAIM_CORRECTION_HE)
    status = h.app.status()
    assert status["mesh"]["claims_unbacked"] == 1
    assert status["degradations"][app_module.APP_ACTION_RECORD_FORGED] == 1
