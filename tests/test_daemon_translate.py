"""Daemon wiring C (t13): the translator, and the operator's words as the record.

A persona whose ``translate_via`` is set runs its turns through
:func:`embodiment.translate.translated_complete`: the conversation's chosen role reads
and answers English, the ``translate_via`` role translates in and out, and the
operator hears the persona's language. Pinned on the daemon's own seam (the ONE
:class:`RoleHolder` names the role each completion asks), so nothing reaches a gateway:

* a typed and a spoken turn both go through the translator; the chosen role receives
  English, the operator receives the persona's language, and the assistant record
  names both roles and both latencies;
* in a translated turn ``remember`` stores the operator's own words, and
  ``search_library`` and ``search_conversations`` receive the operator's query, not
  the chosen model's English; ``ContextPacket.original`` is the operator's text.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from embodiment import voice_latency as vl
from embodiment.contract import ModelResponse, ToolCall
from embodiment.daemon import app as app_module
from embodiment.daemon.state import SESSIONS_DIRNAME, DaemonState
from embodiment.memory import MemorySeams, RoomMemory, SemanticRecall
from embodiment.translate import DATA_TO_ENGLISH_PROMPT, TO_ENGLISH_PROMPT
from tests import test_daemon_app as base
from tests import test_daemon_library as lib
from tests.test_daemon_app import FakeEndpoint

harness = base.harness

TYPED = "מה שלומך היום?"
SPOKEN = "ספרי לי משהו"
#: What the fake translator makes of the operator's words.
ENGLISH = {TYPED: "How are you today?", SPOKEN: "Tell me something"}
#: The chosen role's English answer, and what the translator turns it into.
ENGLISH_REPLY = "I am well, thank you."
HEBREW_REPLY = "אני בסדר, תודה."
FROM_ENGLISH_PREFIX = "Translate the message into "


def _user_text(messages: list[dict[str, Any]]) -> str:
    users = [m for m in messages if m.get("role") == "user"]
    content = users[-1]["content"] if users else ""
    return content if isinstance(content, str) else ""


class Rig:
    """The daemon's seam over the ONE holder: senses translates, the chosen role answers.

    ``steps`` is the chosen role's script (a reply with tool calls, then prose);
    once it runs out the chosen role says :data:`ENGLISH_REPLY`.
    """

    def __init__(self, holder: app_module.RoleHolder, *steps: ModelResponse) -> None:
        self.holder = holder
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
        role, _ = self.holder.current()
        system = messages[0].get("content", "") if messages else ""
        self.calls.append({"role": role, "system": system, "messages": messages, "tools": tools})
        if role == "senses":
            if system == TO_ENGLISH_PROMPT:
                return ModelResponse(content=ENGLISH.get(_user_text(messages), "Translated."))
            if system == DATA_TO_ENGLISH_PROMPT:
                return ModelResponse(content=_user_text(messages))
            if system.startswith(FROM_ENGLISH_PREFIX):
                return ModelResponse(content=HEBREW_REPLY)
            return ModelResponse(content=base.REPLY)  # an untranslated senses turn
        if self.steps:
            return self.steps.pop(0)
        return ModelResponse(content=ENGLISH_REPLY)

    def by_role(self, role: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["role"] == role]

    def chosen_turns(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["role"] == "worker"]

    def translations(self) -> list[dict[str, Any]]:
        return [
            c
            for c in self.by_role("senses")
            if c["system"] in (TO_ENGLISH_PROMPT, DATA_TO_ENGLISH_PROMPT)
            or c["system"].startswith(FROM_ENGLISH_PREFIX)
        ]


def _remembering_memory(tmp_path: Path, stored: list[str]) -> RoomMemory:
    return RoomMemory(
        tmp_path / "memory-translate",
        scope="gwen",
        seams=MemorySeams(
            recall_fn=lambda query, **kw: SimpleNamespace(ok=True, records=[], degradation=None),
            remember_fn=lambda record, **kw: (
                stored.append(json.dumps(record, ensure_ascii=False))
                or SimpleNamespace(ok=True, record_id="r1", degradation=None)
            ),
        ),
        semantic=SemanticRecall(embed_probe=lambda: False),
    )


def _wired(
    harness: Any,
    *steps: ModelResponse,
    memory: Any = None,
    library: Any = None,
    conversations: Any = None,
) -> Any:
    config = app_module.AppConfig(poll_interval_s=0.01)
    holder = app_module.RoleHolder(config.role, config.completion_deadline)
    rig = Rig(holder, *steps)
    overrides: dict[str, Any] = {}
    if memory is not None:
        overrides["memory"] = memory
    h = harness(
        config=config,
        complete=rig,
        lanes=app_module.AppLanes(
            recall_spawn=base._sync_spawn,
            role_holder=holder,
            probe_spawn=lambda job: None,
            library=library,
            conversations=conversations,
        ),
        **overrides,
    )
    if library is not None:
        assert h.app._catalog_tick() is None
    h.rig = rig
    return h


def _translating(
    h: Any, *, translate_via: Optional[str] = "senses", measured: bool = False
) -> None:
    if measured:  # before the persona is chosen, so no probe is started for it
        _measure(h)
    h.app.personas.create(
        persona_id="ada",
        name="Ada",
        language="he",
        default_role="worker",
        translate_via=translate_via,
    )
    assert h.app.set_conversation_persona("ada") is not None
    assert h.app.conversation_role() == "worker"


def _measure(h: Any, role: str = "worker") -> None:
    for _ in range(vl.MEASURED_MIN_SAMPLES):
        assert h.app.voice_latency.add_sample(role, 1.5)


def _assistant_records(state: DaemonState) -> list[dict[str, Any]]:
    out = []
    for path in sorted((Path(state.dir) / SESSIONS_DIRNAME).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("role") == "assistant":
                out.append(row)
    return out


def _call(name: str, **arguments: Any) -> ModelResponse:
    return ModelResponse(
        content="", tool_calls=[ToolCall(id=f"call-{name}", name=name, arguments=arguments)]
    )


def _assert_translated_trace(record: dict[str, Any]) -> None:
    assert record[app_module.CHAT_ROLE_FIELD] == "worker"
    assert record[app_module.TRANSLATE_VIA_FIELD] == "senses"
    for field in (app_module.CHAT_LATENCY_FIELD, app_module.TRANSLATE_LATENCY_FIELD):
        assert isinstance(record[field], (int, float)) and record[field] >= 0, field


# ── criterion 1: both paths go through the translator; the trace names both ─


class TestTranslatedTurns:
    def test_a_typed_turn_reads_english_and_answers_in_the_persona_language(
        self, harness: Any
    ) -> None:
        h = _wired(harness)
        _translating(h)
        answer = h.app.controls().chat(TYPED)
        assert answer["ok"] is True, answer
        assert answer["reply"] == HEBREW_REPLY
        (chosen,) = h.rig.chosen_turns()
        assert _user_text(chosen["messages"]) == ENGLISH[TYPED]
        assert TYPED not in json.dumps(chosen["messages"], ensure_ascii=False)
        to_english = [c for c in h.rig.by_role("senses") if c["system"] == TO_ENGLISH_PROMPT]
        assert [_user_text(c["messages"]) for c in to_english] == [TYPED]
        back = [c for c in h.rig.by_role("senses") if c["system"].startswith(FROM_ENGLISH_PREFIX)]
        assert [_user_text(c["messages"]) for c in back] == [ENGLISH_REPLY]
        assert "Hebrew" in back[0]["system"]
        assert all(c["tools"] is None for c in h.rig.translations()), "no schema to the translator"
        (record,) = _assistant_records(h.state)
        assert record["text"] == HEBREW_REPLY
        _assert_translated_trace(record)

    def test_a_spoken_turn_reads_english_and_answers_in_the_persona_language(
        self, harness: Any
    ) -> None:
        h = _wired(harness)
        _translating(h, measured=True)  # the chosen role's own gate: measured, it may speak
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn(SPOKEN)
        assert result.spoken == HEBREW_REPLY
        chosen = h.rig.chosen_turns()
        assert chosen, "the chosen role answered"
        assert _user_text(chosen[0]["messages"]) == ENGLISH[SPOKEN]
        assert SPOKEN not in json.dumps(chosen[0]["messages"], ensure_ascii=False)
        to_english = [c for c in h.rig.by_role("senses") if c["system"] == TO_ENGLISH_PROMPT]
        assert _user_text(to_english[0]["messages"]) == SPOKEN
        record = _assistant_records(h.state)[-1]
        assert record["text"] == HEBREW_REPLY
        _assert_translated_trace(record)

    def test_a_spoken_turn_keeps_the_chosen_roles_gate(self, harness: Any) -> None:
        h = _wired(harness)
        _translating(h)  # worker unmeasured, its probe held: gated, nothing is asked
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn(SPOKEN)
        assert result.spoken == app_module.voice_gate_text("worker", vl.PROBING)
        assert h.rig.calls == []

    def test_without_translate_via_no_translation_runs(self, harness: Any) -> None:
        h = _wired(harness)
        _translating(h, translate_via=None)
        answer = h.app.controls().chat(TYPED)
        assert answer["reply"] == ENGLISH_REPLY
        assert h.rig.translations() == []
        (chosen,) = h.rig.chosen_turns()
        assert _user_text(chosen["messages"]) == TYPED
        (record,) = _assistant_records(h.state)
        assert record[app_module.CHAT_ROLE_FIELD] == "worker"
        assert app_module.TRANSLATE_VIA_FIELD not in record
        assert app_module.TRANSLATE_LATENCY_FIELD not in record


# ── criterion 2: the operator's words are the record and the query ──────────


class TestTheOperatorsWords:
    def test_remember_stores_the_operators_own_words_on_a_typed_turn(
        self, harness: Any, tmp_path: Path
    ) -> None:
        stored: list[str] = []
        h = _wired(
            harness,
            _call("remember", fact="The operator likes tea"),
            memory=_remembering_memory(tmp_path, stored),
        )
        _translating(h)
        assert h.app.controls().chat(TYPED)["ok"] is True
        assert any(TYPED in text for text in stored), stored
        assert not any("likes tea" in text for text in stored), stored

    def test_remember_stores_the_operators_own_words_on_a_spoken_turn(
        self, harness: Any, tmp_path: Path
    ) -> None:
        stored: list[str] = []
        h = _wired(
            harness,
            _call("remember", fact="Tell the operator something"),
            memory=_remembering_memory(tmp_path, stored),
        )
        _translating(h, measured=True)
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPOKEN)
        assert any(SPOKEN in text for text in stored), stored
        assert not any("Tell the operator" in text for text in stored), stored

    def test_search_library_receives_the_operators_query(self, harness: Any) -> None:
        library = lib._library()
        retrieve = lib.FakeRetrieve(library)
        h = _wired(
            harness,
            _call("search_library", query="hotel bridge"),
            library=lib._lane(library, retrieve),
        )
        _translating(h)
        assert h.app.controls().chat(TYPED)["ok"] is True
        assert [s["query"] for s in retrieve.seen] == [TYPED]

    def test_search_conversations_receives_the_operators_query(
        self, harness: Any, tmp_path: Path
    ) -> None:
        lane = lib._conversation_lane(tmp_path)
        queries: list[str] = []
        embed = lane.embed_query

        def spy(text: str) -> Any:
            queries.append(text)
            return embed(text)

        lane = app_module.ConversationLane(
            index=lane.index, embed_query=spy, threshold=lane.threshold
        )
        h = _wired(
            harness, _call("search_conversations", query="what did I ask"), conversations=lane
        )
        _translating(h)
        assert h.app.controls().chat(TYPED)["ok"] is True
        assert queries == [TYPED]

    def test_the_context_packet_original_is_the_operators_text(
        self, harness: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        packets: list[Any] = []
        real = app_module.run_one_turn

        def spy(text: str, complete: Any, **kw: Any) -> Any:
            result = real(text, complete, **kw)
            packets.append(result.packet)
            return result

        monkeypatch.setattr(app_module, "run_one_turn", spy)
        h = _wired(harness)
        _translating(h, measured=True)
        h.app.controls().chat(TYPED)
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPOKEN)
        originals = [p.original for p in packets if p is not None]
        assert originals[0] == TYPED
        assert SPOKEN in originals[1:]
        assert not any(o in ENGLISH.values() for o in originals)
