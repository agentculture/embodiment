"""Streamed completions (deviation d5; operator, 2026-10-31: "stream the tokens for now,
so we can see reasoning and no timeout").

Every chat completion the daemon makes streams. The read timeout then bounds the
SILENCE between chunks, not the answer's length (lesson 1: a 60 s clock on a whole
cortex completion cut it off 2 of 3 runs). A typed turn that names a request_id
publishes what has arrived so far -- reasoning and answer, each cumulative and
scrubbed -- as ``stream`` events the page shows live.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from embodiment import bus as bus_mod
from embodiment.contract import ModelResponse
from embodiment.daemon import app as app_module
from tests import test_daemon_app as base
from tests.test_daemon_app import REPLY

harness = base.harness

RID = "req-0123456789abcdef"


def _sse(*chunks: dict[str, Any], done: bool = True) -> list[bytes]:
    lines = [f"data: {json.dumps(c)}\n".encode() for c in chunks]
    return lines + ([b"data: [DONE]\n"] if done else [])


def _delta(**delta: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta}]}


class _FakeStream:
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}

    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False

    def __iter__(self) -> Iterator[bytes]:
        return iter(self.lines)

    def read(self, *args: Any) -> bytes:  # the non-streaming path must not be taken
        raise AssertionError("a streamed completion read the whole body")


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Any:
    calls: list[dict[str, Any]] = []

    def serve(lines: list[bytes]) -> list[dict[str, Any]]:
        def fake_urlopen(request: Any, timeout: float = 0) -> Any:
            calls.append({"body": json.loads(request.data), "timeout": timeout})
            return _FakeStream(lines)

        monkeypatch.setattr(app_module.urllib.request, "urlopen", fake_urlopen)
        return calls

    return serve


# ── the wire: one streamed completion ────────────────────────────────────────


def test_a_streamed_completion_assembles_reasoning_content_tools_and_usage(served: Any) -> None:
    calls = served(
        _sse(
            _delta(role="assistant"),
            _delta(reasoning="Need the "),
            _delta(reasoning="newest paper."),
            _delta(content="Here"),
            _delta(content=" it is."),
            _delta(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "search_library"},
                    }
                ]
            ),
            _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"query": "late'}}]),
            _delta(tool_calls=[{"index": 0, "function": {"arguments": 'st"}'}}]),
            {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 22}},
        )
    )
    seen: list[tuple[str, str]] = []
    response = app_module.http_complete(
        [{"role": "user", "content": "q"}],
        gateway_url="http://gateway.invalid",
        role="cortex",
        deadline=42.0,
        stream=True,
        on_delta=lambda channel, text: seen.append((channel, text)),
    )
    assert response.content == "Here it is."
    assert response.reasoning == "Need the newest paper."
    assert [(c.name, c.arguments) for c in response.tool_calls] == [
        ("search_library", {"query": "latest"})
    ]
    assert (response.prompt_tokens, response.completion_tokens) == (11, 22)
    assert seen == [
        ("reasoning", "Need the "),
        ("reasoning", "newest paper."),
        ("content", "Here"),
        ("content", " it is."),
    ]
    body = calls[0]["body"]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    # the read timeout is now a bound on silence between chunks, not on length
    assert calls[0]["timeout"] == 42.0


def test_reasoning_content_is_read_too_and_junk_lines_are_skipped(served: Any) -> None:
    served(
        [b": keep-alive\n", b"\n", b"data: not json\n"]
        + _sse(_delta(reasoning_content="think"), _delta(content="ok"), done=False)
    )
    response = app_module.http_complete([], gateway_url="http://g.invalid", stream=True)
    assert (response.reasoning, response.content) == ("think", "ok")


def test_a_stream_past_the_size_bound_raises(served: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(app_module, "MAX_STREAM_CHARS", 10)
    served(_sse(_delta(content="x" * 6), _delta(content="y" * 6)))
    with pytest.raises(ValueError):
        app_module.http_complete([], gateway_url="http://g.invalid", stream=True)


def test_a_gateway_that_ignores_stream_is_read_as_json(monkeypatch: Any) -> None:
    payload = {"choices": [{"message": {"content": "plain"}}], "usage": {}}

    class Plain:
        headers = {"Content-Type": "application/json"}

        def __enter__(self) -> "Plain":
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def read(self, *args: Any) -> bytes:
            return json.dumps(payload).encode()

    monkeypatch.setattr(app_module.urllib.request, "urlopen", lambda r, timeout=0: Plain())
    response = app_module.http_complete([], gateway_url="http://g.invalid", stream=True)
    assert response.content == "plain"


def test_a_failing_live_view_is_recorded_and_goes_quiet(monkeypatch: Any) -> None:
    monkeypatch.setattr(app_module, "STREAM_PUBLISH_INTERVAL_S", 0.0)
    recorded: list[str] = []

    def publish(kind: str, data: dict[str, Any]) -> None:
        raise RuntimeError("bus gone")

    live = app_module._LiveText(publish, lambda t: t, RID, lambda c, r: recorded.append(c))
    live("content", "a")  # must not raise
    live("content", "b")
    live.flush()
    assert recorded == [app_module.APP_STREAM_FAILED]


def test_the_gateway_seam_streams_and_feeds_the_holders_sink(served: Any) -> None:
    calls = served(_sse(_delta(reasoning="r"), _delta(content="c")))
    holder = app_module.RoleHolder()
    seam = app_module.gateway_seam(app_module.AppConfig(gateway_url="http://g.invalid"), holder)
    got: list[tuple[str, str]] = []
    with holder.streaming(lambda channel, text: got.append((channel, text))):
        assert seam([{"role": "user", "content": "q"}]).content == "c"
    # each model call opens with a step marker, then its pieces
    assert got == [(app_module.STREAM_STEP, ""), ("reasoning", "r"), ("content", "c")]
    assert calls[0]["body"]["stream"] is True
    # outside the block, nothing is fed (even when the completion itself streams)
    with holder.using("senses", 60.0, stream=True):
        seam([{"role": "user", "content": "q"}])
    assert len(got) == 3


# ── the bus kind ─────────────────────────────────────────────────────────────


def test_stream_is_a_private_droppable_speech_kind() -> None:
    assert "stream" in bus_mod.EVENT_KINDS
    assert "stream" in bus_mod.SPEECH_KINDS  # carries Gwen's words: never a default subscription
    # it reaches the broker like every speech kind (operator, 2026-10-24: mark, don't withhold)
    assert "stream" not in bus_mod.LOCAL_ONLY_KINDS
    assert "stream" in bus_mod._DROP_FIRST_KINDS  # cumulative: the next one supersedes
    assert bus_mod._REQUIRED_DATA_FIELDS["stream"] == ("request_id", "channel", "text")


# ── the typed turn: live text for the page ───────────────────────────────────


def _streaming_complete(pieces: list[tuple[str, str]], final: str = REPLY) -> Any:
    """A senses seam that feeds the daemon's sink like a streamed completion would."""

    def complete(messages: Any, tools: Any = None) -> ModelResponse:
        sink = complete.holder.sink()  # type: ignore[attr-defined]
        for channel, text in pieces:
            if sink is not None:
                sink(channel, text)
        return ModelResponse(content=final, prompt_tokens=1, completion_tokens=1)

    return complete


def test_a_named_typed_turn_publishes_cumulative_reasoning_and_answer(
    harness: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(app_module, "STREAM_PUBLISH_INTERVAL_S", 0.0)
    complete = _streaming_complete(
        [("reasoning", "Look "), ("reasoning", "it up."), ("content", REPLY[:3])]
    )
    h = harness(complete=complete)
    complete.holder = h.app._role_holder
    h.app.controls().chat("שאלה", None, RID)
    events = [e.data for e in h.events("stream")]
    assert events, "no stream events were published"
    assert all(e["request_id"] == RID for e in events)
    reasoning = [e["text"] for e in events if e["channel"] == "reasoning"]
    assert reasoning[-1] == "Look it up."
    assert all(b.startswith(a) for a, b in zip(reasoning, reasoning[1:]))  # cumulative
    assert [e["text"] for e in events if e["channel"] == "content"][-1] == REPLY[:3]
    assert all(isinstance(e["step"], int) for e in events)


def test_live_text_is_scrubbed_like_a_reply(harness: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(app_module, "STREAM_PUBLISH_INTERVAL_S", 0.0)
    secret = "SYNTHETIC-GATEWAY-KEY-7731"
    complete = _streaming_complete([("reasoning", f"the key is {secret}")])
    h = harness(
        complete=complete,
        config=app_module.AppConfig(poll_interval_s=0.01, api_key=secret),
    )
    complete.holder = h.app._role_holder
    h.app.controls().chat("שאלה", None, RID)
    texts = [e.data["text"] for e in h.events("stream")]
    assert texts
    assert all(secret not in t for t in texts)


def test_each_model_step_restarts_the_live_text(monkeypatch: Any) -> None:
    monkeypatch.setattr(app_module, "STREAM_PUBLISH_INTERVAL_S", 0.0)
    sent: list[dict[str, Any]] = []
    live = app_module._LiveText(lambda kind, data: sent.append(data), lambda t: t, RID)
    for channel, piece in [
        (app_module.STREAM_STEP, ""),
        ("reasoning", "first"),
        (app_module.STREAM_STEP, ""),
        ("reasoning", "second"),
    ]:
        live(channel, piece)
    live.flush()
    assert [(e["step"], e["text"]) for e in sent] == [(0, "first"), (1, "second")]


def test_throttled_text_is_still_flushed_at_the_end(monkeypatch: Any) -> None:
    monkeypatch.setattr(app_module, "STREAM_PUBLISH_INTERVAL_S", 3600.0)
    sent: list[dict[str, Any]] = []
    live = app_module._LiveText(lambda kind, data: sent.append(data), lambda t: t, RID)
    for piece in ("a", "b", "c"):
        live("content", piece)
    assert [e["text"] for e in sent] == ["a"]  # the rest waits for the interval
    live.flush()
    assert [e["text"] for e in sent] == ["a", "abc"]


def test_a_typed_turn_without_a_request_id_publishes_no_stream(harness: Any) -> None:
    complete = _streaming_complete([("reasoning", "x"), ("content", "y")])
    h = harness(complete=complete)
    complete.holder = h.app._role_holder
    h.app.controls().chat("שאלה")
    assert h.events("stream") == []


# ── which turns stream ───────────────────────────────────────────────────────


def test_only_a_typed_turn_streams_a_spoken_turn_keeps_its_total_bound(monkeypatch: Any) -> None:
    """A spoken turn's deadline on a measured role is a TOTAL latency (the voice gate);
    streamed it would only bound silence and a listener could wait out a long
    reasoning. So the seam streams inside ``using(..., stream=True)`` only."""
    calls: list[dict[str, Any]] = []
    plain = json.dumps({"choices": [{"message": {"content": "c"}}], "usage": {}}).encode()

    class Plain(_FakeStream):
        headers = {"Content-Type": "application/json"}

        def read(self, *args: Any) -> bytes:
            return plain

    def fake_urlopen(request: Any, timeout: float = 0) -> Any:
        body = json.loads(request.data)
        calls.append({"body": body})
        return _FakeStream(_sse(_delta(content="c"))) if body.get("stream") else Plain([])

    monkeypatch.setattr(app_module.urllib.request, "urlopen", fake_urlopen)
    holder = app_module.RoleHolder()
    seam = app_module.gateway_seam(app_module.AppConfig(gateway_url="http://g.invalid"), holder)
    with holder.using("cortex", 18.1):
        seam([{"role": "user", "content": "spoken"}])
    with holder.using("cortex", 60.0, stream=True):
        seam([{"role": "user", "content": "typed"}])
    assert [c["body"].get("stream", False) for c in calls] == [False, True]


# ── a translated turn's live text (review of #117, finding 1) ────────────────


def test_a_translated_turn_shows_only_the_chosen_models_reasoning_live(harness: Any) -> None:
    """The translator's calls (the operator's words and recall into English, the reply
    back) and the chosen model's English draft are not Gwen's answer: only the chosen
    model's reasoning reaches the page live; every call still streams (silence bound)."""
    from embodiment.translate import OriginalText

    h = harness()
    holder = h.app._role_holder
    seen: list[tuple[str, bool]] = []

    def raw(messages: Any, tools: Any = None) -> ModelResponse:
        role = holder.current()[0]
        seen.append((role, holder.streams()))
        sink = holder.sink()
        if sink is not None:
            sink(app_module.STREAM_STEP, "")
            sink("reasoning", f"{role} thinks")
            sink("content", f"{role} says")
        text = "Hello there." if role == "worker" else "שלום."
        return ModelResponse(content=text, prompt_tokens=1, completion_tokens=1)

    translation = app_module._Translation(via="senses", language="he", pinned=OriginalText())
    target = app_module._ChatTarget(role="worker", deadline=30.0)
    seam = h.app._translated_seam(raw, translation, target)
    got: list[tuple[str, str]] = []
    with (
        holder.using("worker", 30.0, stream=True),
        holder.streaming(lambda c, t: got.append((c, t))),
    ):
        seam([{"role": "system", "content": "s"}, {"role": "user", "content": "שלום"}])
    assert {r for r, _ in seen} == {"senses", "worker"}
    assert all(streams for _, streams in seen)
    assert ("reasoning", "worker thinks") in got
    assert not [t for c, t in got if c == "content"]
    assert not [t for _, t in got if t.startswith("senses")]


def test_a_conversations_turns_share_one_translation_cache(harness: Any) -> None:
    """Review of #117, finding 6: each turn's seam re-translated the whole history."""
    from embodiment.translate import OriginalText

    h = harness()
    calls: list[str] = []

    def raw(messages: Any, tools: Any = None) -> ModelResponse:
        role = h.app._role_holder.current()[0]
        if role == "senses":
            calls.append(messages[-1]["content"])
            return ModelResponse(content=f"EN({messages[-1]['content']})")
        return ModelResponse(content="draft")

    target = app_module._ChatTarget(role="worker", deadline=30.0)

    def turn(conversation: str, window: list[str]) -> None:
        cache = h.app._translation_cache(conversation, "senses", "Hebrew")
        translation = app_module._Translation(
            via="senses", language="Hebrew", pinned=OriginalText(), cache=cache
        )
        h.app._translated_seam(raw, translation, target)(
            [{"role": "user", "content": t} for t in window]
        )

    turn("c1", ["one"])
    turn("c1", ["one", "two"])
    turn("c2", ["one"])  # another conversation translates on its own
    assert [c for c in calls if c != "draft"].count("one") == 2
    assert h.app._translation_cache("c1", "senses", "Hebrew") is h.app._translation_cache(
        "c1", "senses", "Hebrew"
    )
