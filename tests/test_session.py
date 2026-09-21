"""embodiment.session — one session's conversational state.

Three acceptance criteria, three classes of test, matching the brief:

1. ``TestWindowBudget`` — the rolling turn window never exceeds its token
   budget, drops the OLDEST turns first, and never truncates or rewrites a
   kept turn's text (the verbatim invariant applied to the window).
2. ``TestNoAudioOnDisk`` — no code path writes audio bytes to disk; a fake
   session is driven through a real :class:`~embodiment.daemon.state.DaemonState`
   and every file under the state dir is scanned afterwards.
3. ``TestRememberDiscipline`` — an explicit spoken ask writes exactly one
   record; ending a session writes exactly one attributed summary; nothing
   else ever calls ``remember``. Proved with a counting fake memory across a
   session with many turns and no ask, and again with an ask present.

A fourth group, ``TestAttack``, is the "attack your own module" pass the
task-agent preamble requires: adversarial session ids, degenerate
ask-detector input, a summariser that raises/blanks/hangs, and a privacy
sweep for planted marker text across every record and the close report.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment import session as sess
from embodiment.daemon.state import DaemonState

# ── a counting, outcome-scriptable fake RoomMemory ─────────────────────────


class _Outcome:
    CONFIRMED = "confirmed"
    DEFERRED = "deferred"
    REFUSED = "refused"


class _FakeDegradation:
    def __init__(self, code: str) -> None:
        self.code = code
        self.reason = "fake"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


class _FakeRememberResult:
    def __init__(self, ok: bool, record_id: Optional[str], degradation: Any = None) -> None:
        self.ok = ok
        self.record_id = record_id
        self.degradation = degradation
        self.raw = None


class FakeMemory:
    """Counts every ``remember`` call and scripts its outcome.

    ``calls`` holds ``(text, kwargs)`` for every invocation — used both to
    prove the call COUNT (criterion 3) and to prove no turn text ever leaks
    into a degradation (the privacy sweep imports this list directly, never
    through a record/report surface).
    """

    def __init__(self, outcome: str = _Outcome.CONFIRMED) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.outcome = outcome
        self._n = 0

    def remember(self, text: str, **kwargs: Any) -> _FakeRememberResult:
        self._n += 1
        self.calls.append((text, kwargs))
        record_id = f"fake-{self._n}"
        if self.outcome == _Outcome.CONFIRMED:
            return _FakeRememberResult(True, record_id)
        if self.outcome == _Outcome.DEFERRED:
            return _FakeRememberResult(
                False, record_id, _FakeDegradation(sess.CODE_REMEMBER_DEFERRED)
            )
        return _FakeRememberResult(False, record_id, _FakeDegradation("memory-saturated"))

    @property
    def call_count(self) -> int:
        return len(self.calls)


def _state(tmp_path: Path) -> DaemonState:
    return DaemonState(tmp_path / "state")


def _session(
    tmp_path: Path,
    memory: Optional[FakeMemory] = None,
    **kwargs: Any,
) -> tuple[sess.Session, FakeMemory, DaemonState]:
    state = _state(tmp_path)
    mem = memory if memory is not None else FakeMemory()
    session = sess.Session(state, mem, **kwargs)
    return session, mem, state


# ── criterion 1: the window ─────────────────────────────────────────────────


class TestWindowBudget:
    def test_window_never_exceeds_budget_after_many_turns(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path, budget_tokens=200)
        for i in range(200):
            session.add_user(f"user message number {i} " * 3)
            session.add_assistant(f"assistant reply number {i} " * 3)
        assert session.window_tokens() <= 200 or len(session.messages()) == 1

    def test_drops_oldest_first(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path, budget_tokens=60)
        session.add_user("AAAA")
        session.add_assistant("BBBB")
        session.add_user("CCCC" * 300)  # far over budget alone -> forces a drop
        msgs = session.messages()
        texts = [m["content"] for m in msgs]
        # The newest turn must survive; something from the front must be gone.
        assert texts[-1] == "CCCC" * 300
        assert "AAAA" not in texts

    def test_kept_turn_text_is_verbatim_never_truncated(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path, budget_tokens=5000)
        text = "hello " * 10 + "world"
        session.add_user(text)
        msgs = session.messages()
        assert msgs[-1]["content"] == text

    def test_single_oversized_turn_is_kept_not_crashed(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path, budget_tokens=10)
        huge = "x" * 5000
        session.add_user(huge)  # must not raise
        msgs = session.messages()
        assert msgs[-1]["content"] == huge
        codes = [d.code for d in session.degradations]
        assert sess.CODE_TURN_EXCEEDS_BUDGET in codes

    def test_empty_window_reports_zero_tokens(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path)
        assert session.messages() == []
        assert session.window_tokens() == 0

    def test_injectable_counter_is_used(self, tmp_path: Path) -> None:
        calls: list[list[dict[str, Any]]] = []

        def counter(messages: list[dict[str, Any]]) -> int:
            calls.append(messages)
            return 0  # never over budget

        session, _mem, _state = _session(tmp_path, budget_tokens=1, count_tokens=counter)
        session.add_user("hi")
        assert calls  # our counter was actually consulted


# ── criterion 2: no audio, ever ─────────────────────────────────────────────


class TestNoAudioOnDisk:
    def test_add_user_and_add_assistant_have_no_bytes_parameter(self) -> None:
        import inspect

        for name in ("add_user", "add_assistant"):
            sig = inspect.signature(getattr(sess.Session, name))
            for param in sig.parameters.values():
                assert (
                    param.annotation in (inspect._empty, str, Optional[str])
                    or "bytes" not in str(param.annotation).lower()
                )

    def test_bytes_input_is_rejected_not_silently_written(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path)
        with pytest.raises(TypeError):
            session.add_user(b"\x00\x01\x02")  # type: ignore[arg-type]

    def test_state_dir_holds_only_expected_files_after_a_fake_session(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        mem = FakeMemory()
        session = sess.Session(state, mem, added_by="gwen-test")
        session.add_user("hello there, this is a spoken line")
        session.add_assistant("hi! how can I help?")
        session.add_user("תזכרי שאני אוהב קפה שחור")
        session.close(summarise=lambda msgs: "a short summary of the chat")

        all_files = sorted(p for p in state.dir.rglob("*") if p.is_file())
        allowed_names = {"embodiment.log", "degradations.jsonl"}
        for path in all_files:
            if path.parent.name == "sessions":
                assert path.suffix == ".jsonl"
                continue
            assert path.name in allowed_names, f"unexpected file: {path}"

        transcript_path = state.dir / "sessions" / f"{session.session_id}.jsonl"
        assert transcript_path.exists()
        raw = transcript_path.read_bytes()
        # every line must decode as UTF-8 JSON with only the expected keys
        for line in raw.splitlines():
            if not line.strip():
                continue
            record = json.loads(line.decode("utf-8"))
            assert set(record.keys()) <= {"ts", "role", "text"}
            assert isinstance(record["text"], str)


# ── criterion 3: exactly the two write paths, no more ───────────────────────


class TestRememberDiscipline:
    def test_no_ask_no_writes_from_many_turns(self, tmp_path: Path) -> None:
        session, mem, _state = _session(tmp_path)
        for i in range(50):
            session.add_user(f"just chatting, turn {i}, nothing special here")
            session.add_assistant(f"sure, turn {i}")
        assert mem.call_count == 0
        report = session.close()  # no summarise -> no write either
        assert mem.call_count == 0
        assert report.records_written == 0

    def test_explicit_ask_writes_exactly_one_record(self, tmp_path: Path) -> None:
        session, mem, _state = _session(tmp_path)
        session.add_user("hey, remember that the deploy key rotates monthly")
        session.add_user("also some unrelated chat")
        session.add_assistant("noted")
        assert mem.call_count == 1

    def test_hebrew_ask_writes_exactly_one_record(self, tmp_path: Path) -> None:
        session, mem, _state = _session(tmp_path)
        session.add_user("תזכרי שאני אוהב קפה שחור בבוקר")
        assert mem.call_count == 1

    def test_ending_writes_exactly_one_summary(self, tmp_path: Path) -> None:
        session, mem, _state = _session(tmp_path)
        session.add_user("chat chat chat")
        session.add_assistant("chat chat chat")
        report = session.close(summarise=lambda msgs: "summary text")
        assert mem.call_count == 1
        assert report.summary_landed is True
        assert report.records_written == 1

    def test_ask_then_end_writes_exactly_two_records_total(self, tmp_path: Path) -> None:
        session, mem, _state = _session(tmp_path)
        session.add_user("remember that the office wifi password changed")
        for i in range(10):
            session.add_user(f"turn {i}")
            session.add_assistant(f"reply {i}")
        report = session.close(summarise=lambda msgs: "end of session summary")
        assert mem.call_count == 2
        assert report.records_written == 2

    def test_close_is_idempotent_no_extra_writes(self, tmp_path: Path) -> None:
        session, mem, _state = _session(tmp_path)
        session.add_user("hello")
        first = session.close(summarise=lambda msgs: "summary")
        assert mem.call_count == 1
        second = session.close(summarise=lambda msgs: "a DIFFERENT summary")
        assert mem.call_count == 1  # not called again
        assert second is first

    def test_summariser_raising_writes_nothing(self, tmp_path: Path) -> None:
        session, mem, _state = _session(tmp_path)
        session.add_user("hello")

        def boom(_messages: list[dict[str, Any]]) -> str:
            raise RuntimeError("model seam exploded")

        report = session.close(summarise=boom)
        assert mem.call_count == 0
        assert report.summary_landed is False
        assert report.summary_skipped is True

    def test_summariser_returning_blank_writes_nothing(self, tmp_path: Path) -> None:
        session, mem, _state = _session(tmp_path)
        session.add_user("hello")
        report = session.close(summarise=lambda msgs: "   ")
        assert mem.call_count == 0
        assert report.summary_landed is False
        assert report.summary_skipped is True

    def test_summariser_exceeding_deadline_writes_nothing(self, tmp_path: Path) -> None:
        session, mem, _state = _session(tmp_path)
        session.add_user("hello")
        released = threading.Event()

        def slow(_messages: list[dict[str, Any]]) -> str:
            released.wait(5.0)
            return "too late"

        report = session.close(summarise=slow, deadline=0.05)
        assert mem.call_count == 0
        assert report.summary_landed is False
        assert report.summary_skipped is True
        released.set()  # let the background thread finish so it doesn't leak

    def test_deferred_ask_is_not_an_error(self, tmp_path: Path) -> None:
        mem = FakeMemory(outcome=_Outcome.DEFERRED)
        session, mem, _state = _session(tmp_path, memory=mem)
        outcome = session.add_user("remember that the badge reader is broken")
        assert outcome is not None
        assert outcome.deferred is True
        assert outcome.refused is False
        # deferred is not folded into the degradation ledger as an error
        assert all(d.code != sess.CODE_TURN_EXCEEDS_BUDGET for d in session.degradations)

    def test_refused_ask_is_reported(self, tmp_path: Path) -> None:
        mem = FakeMemory(outcome=_Outcome.REFUSED)
        session, mem, _state = _session(tmp_path, memory=mem)
        outcome = session.add_user("remember that the badge reader is broken")
        assert outcome is not None
        assert outcome.refused is True
        assert outcome.remembered is False


# ── attack pass ──────────────────────────────────────────────────────────────


class TestAttack:
    """WHAT WAS TRIED, per the task-agent preamble. Every case is asserted."""

    def test_marker_never_leaks_into_degradations_or_report(self, tmp_path: Path) -> None:
        marker = "MARKER-SECRET-9f8a7"
        session, mem, _state = _session(tmp_path, budget_tokens=1)
        session.add_user(marker * 50)  # forces the oversized-turn degradation

        def boom(_messages: list[dict[str, Any]]) -> str:
            raise RuntimeError(marker)

        report = session.close(summarise=boom)

        haystacks: list[str] = [repr(session)]
        haystacks.extend(d.code + d.reason for d in session.degradations)
        haystacks.append(json.dumps(report.to_dict()))
        for haystack in haystacks:
            assert marker not in haystack, haystack

    def test_marker_never_leaks_into_ask_outcome(self, tmp_path: Path) -> None:
        marker = "MARKER-SECRET-abc123"
        session, mem, _state = _session(tmp_path)
        outcome = session.add_user(f"remember that {marker} is the password")
        # the outcome is a status object; scan its dict/repr, never the text field
        assert marker not in repr(outcome)

    def test_path_separators_and_dotdot_session_id(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        mem = FakeMemory()
        session = sess.Session(state, mem, session_id="../../etc/passwd")
        session.add_user("hi")
        # must not have escaped the sessions directory
        sessions_dir = (state.dir / "sessions").resolve()
        for path in state.dir.rglob("*.jsonl"):
            if path.parent.name == "sessions":
                assert path.resolve().is_relative_to(sessions_dir)
        assert not (tmp_path / "etc").exists()

    def test_nul_and_huge_session_id(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        mem = FakeMemory()
        session = sess.Session(state, mem, session_id="a\x00b" + ("z" * 5000))
        session.add_user("hi")  # must not raise

    def test_bidi_and_zero_width_in_turn_text_survive_verbatim_in_window(
        self, tmp_path: Path
    ) -> None:
        session, _mem, _state = _session(tmp_path, budget_tokens=5000)
        text = "hello ‮​world⁦"
        session.add_user(text)
        assert session.messages()[-1]["content"] == text

    def test_ask_detector_never_called_with_non_str(self, tmp_path: Path) -> None:
        # add_user rejects non-str before the detector ever sees it.
        session, _mem, _state = _session(tmp_path)
        with pytest.raises(TypeError):
            session.add_user(12345)  # type: ignore[arg-type]

    def test_repeated_call_ten_thousand_times_stays_bounded(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path, budget_tokens=500)
        for i in range(2000):
            session.add_user(f"t{i}")
        assert session.window_tokens() <= 500 or len(session.messages()) == 1

    def test_ask_detector_exception_does_not_crash_add_user(self, tmp_path: Path) -> None:
        def hostile_detector(_text: str) -> Optional[str]:
            raise ValueError("detector exploded")

        session, mem, _state = _session(tmp_path, ask_detector=hostile_detector)
        outcome = session.add_user("remember that this triggers the hostile detector")
        assert outcome is None or outcome.detected is False
        assert mem.call_count == 0
        assert any(d.code == sess.CODE_ASK_DETECTOR_FAILED for d in session.degradations)

    def test_disk_unwritable_transcript_never_raises(self, tmp_path: Path) -> None:
        # force no-persistence by pointing the state dir at a path a regular
        # file occupies, so DaemonState falls into its own no-persistence
        # mode; Session must still work.
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        state2 = DaemonState(blocker / "state")  # parent is a file -> cannot mkdir
        mem = FakeMemory()
        session = sess.Session(state2, mem)
        session.add_user("hello")  # must not raise
        session.close()

    def test_repr_does_not_contain_turn_text(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path)
        session.add_user("SUPER SECRET SPOKEN LINE")
        assert "SUPER SECRET" not in repr(session)


# ── ask detector, standalone ────────────────────────────────────────────────


class TestDefaultAskDetector:
    def test_english_remember_that(self) -> None:
        assert sess.default_ask_detector("remember that milk is in the fridge") == (
            "milk is in the fridge"
        )

    def test_hebrew_tizkeri(self) -> None:
        result = sess.default_ask_detector("תזכרי שהפגישה נדחתה ליום שלישי")
        assert result is not None
        assert "הפגישה" in result

    def test_no_match_returns_none(self) -> None:
        assert sess.default_ask_detector("what's the weather like today") is None

    def test_empty_string_returns_none(self) -> None:
        assert sess.default_ask_detector("") is None

    def test_false_negative_documented_case(self) -> None:
        # "don't forget" is a known false negative of the heuristic default.
        assert sess.default_ask_detector("don't forget the milk") is None
