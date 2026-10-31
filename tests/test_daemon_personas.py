"""Daemon wiring A (t11): personas in the prompt, per conversation, restored on continue.

The pins here are on the REAL composed prompt the seam receives, not on a helper:

* built-in Gwen's spoken prompt is byte-identical to before (``SYSTEM_PROMPT`` + clock +
  the Hebrew tool prompt);
* her typed prompt differs from before by exactly deviation d1 (operator, 2026-10-31:
  "short sentences feats speech not text") — the base's second and fourth lines lose
  "short", the speakability line goes — and by nothing else;
* a conversation's persona id is on every assistant transcript record, comes back on
  continue, and a deleted one answers nothing, visibly;
* tools, the write guard's inputs and identity framing do not depend on the persona.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from embodiment import persona_prompt
from embodiment.contract import ModelResponse
from embodiment.daemon import app as app_module
from embodiment.daemon.state import SESSIONS_DIRNAME, DaemonState
from embodiment.personas import BUILTIN_ID, builtin_persona
from embodiment.turn import SYSTEM_PROMPT
from embodiment.voice import MAX_REPLY_CHARS
from tests import test_daemon_app as base
from tests.test_daemon_app import FROZEN_NOW, REPLY, SPEECH, FakeEndpoint

harness = base.harness

TYPED = "מה שלומך?"

#: The prompt before t11, spelled out from the constants that existed before it.
_CLOCK = app_module.clock_line(FROZEN_NOW)
VOICE_BEFORE = "\n\n".join([SYSTEM_PROMPT, _CLOCK, app_module.REMEMBER_TOOL_PROMPT])
#: d1's typed base, written out literally (not via compose_base) so the test is a pin.
TYPED_BASE_D1 = "\n".join(
    [
        "את גוון, בת שיחה קולית. את מקשיבה למה שנאמר ועונה בקול.",
        "עני בעברית.",
        "אם משהו לא ברור, בקשי הבהרה במקום לנחש.",
    ]
)


class Seen:
    """A senses seam that records every system prompt and tool list it was handed."""

    def __init__(self, reply: str = REPLY, hook: Any = None) -> None:
        self.reply = reply
        self.hook = hook
        self.prompts: list[str] = []
        self.tools: list[Any] = []

    def __call__(self, messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
        self.prompts.append(messages[0]["content"])
        self.tools.append(sorted(t["function"]["name"] for t in (tools or [])))
        if self.hook is not None:
            self.hook()
        return ModelResponse(content=self.reply, prompt_tokens=3, completion_tokens=4)


def _assistant_records(state: DaemonState) -> list[dict[str, Any]]:
    out = []
    for path in sorted((Path(state.dir) / SESSIONS_DIRNAME).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("role") == "assistant":
                out.append({**row, "_log": path.stem})
    return out


def _english(h: Any, **fields: Any) -> Any:
    return h.app.personas.create(
        persona_id=fields.pop("persona_id", "ada"),
        name=fields.pop("name", "Ada"),
        language="en",
        **fields,
    )


# ── the composed prompt, byte for byte ───────────────────────────────────────


class TestBuiltinPromptBytes:
    def test_the_spoken_prompt_is_byte_identical_to_before(self, harness: Any) -> None:
        seen = Seen()
        h = harness(complete=seen)
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        assert seen.prompts[0] == VOICE_BEFORE

    def test_the_typed_prompt_differs_by_exactly_d1(self, harness: Any) -> None:
        seen = Seen()
        h = harness(complete=seen)
        assert h.app.controls().chat(TYPED)["ok"]
        typed = seen.prompts[0]
        assert typed == "\n\n".join([TYPED_BASE_D1, _CLOCK, app_module.REMEMBER_TOOL_PROMPT])
        # ...and the difference from before is the base's, and only the base's:
        assert typed.replace(TYPED_BASE_D1, SYSTEM_PROMPT, 1) == VOICE_BEFORE
        before = SYSTEM_PROMPT.splitlines()
        assert TYPED_BASE_D1.splitlines() == [
            before[0],
            "עני בעברית.",
            before[3].replace("הבהרה קצרה", "הבהרה"),
        ]

    def test_the_typed_base_is_persona_prompts_own(self) -> None:
        assert persona_prompt.compose_base(builtin_persona(), "typed") == TYPED_BASE_D1


class TestPersonaPrompt:
    def test_an_english_persona_composes_its_own_base_and_english_tools(self, harness: Any) -> None:
        seen = Seen()
        h = harness(complete=seen)
        ada = _english(h, text="You love otters.")
        assert h.app.set_conversation_persona("ada") == ada
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        assert h.app.controls().chat(TYPED)["ok"]
        remember = persona_prompt.english_tool_prompts(ada)["REMEMBER"]
        voice, typed = seen.prompts
        assert voice.startswith(persona_prompt.compose_base(ada, "voice") + "\n\n")
        assert typed.startswith(persona_prompt.compose_base(ada, "typed") + "\n\n")
        for prompt in (voice, typed):
            assert remember in prompt
            assert app_module.REMEMBER_TOOL_PROMPT not in prompt
            assert SYSTEM_PROMPT not in prompt

    def test_a_hebrew_persona_keeps_the_hebrew_tool_prompts(self, harness: Any) -> None:
        seen = Seen()
        h = harness(complete=seen)
        h.app.personas.create(persona_id="noa", name="נועה", language="he", length="brief")
        h.app.set_conversation_persona("noa")
        h.app.controls().chat(TYPED)
        noa = h.app.personas.get("noa")
        assert seen.prompts[0] == "\n\n".join(
            [persona_prompt.compose_base(noa, "typed"), _CLOCK, app_module.REMEMBER_TOOL_PROMPT]
        )

    def test_tools_and_identity_framing_do_not_depend_on_the_persona(
        self, harness: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configs: list[Any] = []
        real = app_module.run_one_turn

        def spy(query: Any, complete: Any, *, tools: Any = None, config: Any = None) -> Any:
            configs.append((config, sorted(getattr(tools, "specs", {}) or {})))
            return real(query, complete, tools=tools, config=config)

        monkeypatch.setattr(app_module, "run_one_turn", spy)
        seen = Seen()
        h = harness(complete=seen)
        h.app.controls().chat(TYPED)
        _english(h)
        h.app.set_conversation_persona("ada")
        h.app.controls().chat(TYPED)
        (gwen, gwen_tools), (ada, ada_tools) = configs
        assert gwen_tools == ada_tools
        assert seen.tools[0] == seen.tools[1]
        assert (gwen.identity, gwen.role, gwen.max_tokens, gwen.max_steps) == (
            ada.identity,
            ada.role,
            ada.max_tokens,
            ada.max_steps,
        )
        assert gwen.identity is None

    def test_a_long_persona_reply_is_still_capped_and_the_cut_recorded(self, harness: Any) -> None:
        long = "This is one sentence of a very detailed answer. " * 120  # ~5,900 characters
        h = harness(complete=Seen(reply=long))
        _english(h, length="detailed")
        h.app.set_conversation_persona("ada")
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        assert app_module.APP_REPLY_TRUNCATED in h.ledger_codes()
        spoken = sum(len(e.data.get("text", "")) for e in h.events("reply"))
        assert 0 < spoken <= MAX_REPLY_CHARS < len(long)

    def test_a_short_reply_records_no_cut(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        assert app_module.APP_REPLY_TRUNCATED not in h.ledger_codes()


# ── the persona id in the transcript, and its restore ────────────────────────


class TestTranscriptPersona:
    def test_every_assistant_record_names_the_built_in_by_default(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        h.app.controls().chat(TYPED)
        records = _assistant_records(h.state)
        assert len(records) == 2
        assert {r["persona"] for r in records} == {BUILTIN_ID}
        assert h.app.conversation_persona_id() == BUILTIN_ID

    def test_a_chosen_persona_is_on_every_later_assistant_record(self, harness: Any) -> None:
        h = harness()
        _english(h)
        h.app.set_conversation_persona("ada")
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        h.app.controls().chat(TYPED)
        assert [r["persona"] for r in _assistant_records(h.state)] == ["ada", "ada"]
        assert h.app.conversation_persona_id() == "ada"
        assert h.app.conversation_persona() == h.app.personas.get("ada")

    def test_an_unknown_persona_is_refused_and_changes_nothing(self, harness: Any) -> None:
        h = harness()
        assert h.app.set_conversation_persona("nobody") is None
        assert h.app.conversation_persona_id() == BUILTIN_ID

    def test_a_turn_snapshots_the_persona_at_its_start(self, harness: Any) -> None:
        h_box: list[Any] = []
        flipped: list[bool] = []

        def flip() -> None:
            if not flipped:
                flipped.append(True)
                h_box[0].app.set_conversation_persona("ada")

        seen = Seen(hook=flip)
        h = harness(complete=seen)
        h_box.append(h)
        _english(h)
        h.app.controls().chat(TYPED)  # flips to ada mid-turn
        h.app.controls().chat(TYPED)
        first, second = _assistant_records(h.state)
        assert (first["persona"], second["persona"]) == (BUILTIN_ID, "ada")
        assert seen.prompts[0].startswith(TYPED_BASE_D1)
        assert seen.prompts[1].startswith(
            persona_prompt.compose_base(h.app.personas.get("ada"), "typed")
        )


class TestContinueRestoresThePersona:
    def _old_conversation(self, h: Any) -> str:
        _english(h)
        h.app.set_conversation_persona("ada")
        assert h.app.controls().chat(TYPED)["ok"]
        old = _assistant_records(h.state)[-1]["_log"]
        h.app.controls().new_conversation()
        return old

    def test_continuing_restores_it_in_the_same_daemon(self, harness: Any) -> None:
        seen = Seen()
        h = harness(complete=seen)
        old = self._old_conversation(h)
        h.app.controls().chat(TYPED)  # a fresh live conversation: the built-in
        assert seen.prompts[-1].startswith(TYPED_BASE_D1)
        assert h.app.controls().chat(TYPED, conversation=old)["ok"]
        ada = h.app.personas.get("ada")
        assert seen.prompts[-1].startswith(persona_prompt.compose_base(ada, "typed"))
        assert h.app.conversation_persona_id(old) == "ada"
        assert [r["persona"] for r in _assistant_records(h.state) if r["_log"] == old] == [
            "ada",
            "ada",
        ]

    def test_continuing_restores_it_after_a_restart(self, harness: Any, tmp_path: Path) -> None:
        state_dir = tmp_path / "shared-state"
        first = harness(state=DaemonState(state_dir))
        old = self._old_conversation(first)
        seen = Seen()
        second = harness(state=DaemonState(state_dir), complete=seen)
        assert second.app.conversation_persona_id(old) == "ada"
        assert second.app.controls().chat(TYPED, conversation=old)["ok"]
        ada = second.app.personas.get("ada")
        assert seen.prompts[-1].startswith(persona_prompt.compose_base(ada, "typed"))

    def test_a_log_from_before_personas_continues_as_the_built_in(self, harness: Any) -> None:
        seen = Seen()
        h = harness(complete=seen)
        log = Path(h.state.dir) / SESSIONS_DIRNAME / "old-one.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            json.dumps({"ts": 1.0, "role": "user", "text": "היי"})
            + "\n"
            + json.dumps({"ts": 2.0, "role": "assistant", "text": "שלום"})
            + "\n",
            encoding="utf-8",
        )
        assert h.app.conversation_persona_id("old-one") == BUILTIN_ID
        assert h.app.controls().chat(TYPED, conversation="old-one")["ok"]
        assert seen.prompts[-1].startswith(TYPED_BASE_D1)


class TestADeletedPersona:
    def test_continuing_with_a_deleted_persona_answers_nothing_and_says_so(
        self, harness: Any
    ) -> None:
        seen = Seen()
        h = harness(complete=seen)
        old = TestContinueRestoresThePersona()._old_conversation(h)
        calls = len(seen.prompts)
        log = Path(h.state.dir) / SESSIONS_DIRNAME / f"{old}.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines()
        assert h.app.personas.delete("ada")
        answer = h.app.controls().chat(TYPED, conversation=old)
        assert answer["ok"] is False
        assert answer["code"] == app_module.APP_PERSONA_MISSING
        assert answer["reply"] == app_module.PERSONA_MISSING_TEXT
        assert len(seen.prompts) == calls, "no model was asked"
        assert log.read_text(encoding="utf-8").splitlines() == lines, "nothing was written"
        assert app_module.APP_PERSONA_MISSING in h.ledger_codes()
        # visible state, never silently the built-in:
        assert h.app.conversation_persona_id(old) == "ada"
        assert h.app.conversation_persona(old) is None

    def test_a_spoken_turn_with_a_deleted_persona_speaks_only_the_notice(
        self, harness: Any
    ) -> None:
        seen = Seen()
        h = harness(complete=seen)
        _english(h)
        h.app.set_conversation_persona("ada")
        h.app.personas.delete("ada")
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn(SPEECH)
        assert result.spoken == app_module.PERSONA_MISSING_TEXT
        assert seen.prompts == []
        assert app_module.APP_PERSONA_MISSING in h.ledger_codes()
        record = _assistant_records(h.state)[-1]
        assert (record["text"], record["persona"]) == (app_module.PERSONA_MISSING_TEXT, "ada")

    def test_a_live_typed_turn_with_a_deleted_persona_is_refused(self, harness: Any) -> None:
        seen = Seen()
        h = harness(complete=seen)
        _english(h)
        h.app.set_conversation_persona("ada")
        h.app.personas.delete("ada")
        answer = h.app.controls().chat(TYPED)
        assert (answer["ok"], answer["code"]) == (False, app_module.APP_PERSONA_MISSING)
        assert seen.prompts == []
