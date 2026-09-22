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

Round 2 (this file's newer classes) is an independent operator probe run
against the REAL ``RoomMemory`` and hostile input, which found five defects
in the round-1 implementation:

1. ``TestDefaultAskDetector`` (rewritten, table-driven) — the ask detector
   fired on plain speech ABOUT remembering, not just imperatives.
2. ``TestMemoryRaises`` — a raising ``memory.remember`` propagated out of
   ``add_user``/``close``, and a second ``close()`` after that then raised
   ``AssertionError``.
3. ``TestBoundedDegradations`` — the degradation list grew without bound
   (20,000 near-identical entries from 20,000 oversized turns).
4. ``TestSummaryHonesty`` — the summary record's own metadata implied it
   covered the whole session when it only ever saw the current window.
5. ``TestThreadExit`` — a hung summariser's worker thread (a
   ``ThreadPoolExecutor``, non-daemon) measurably kept the whole PROCESS
   alive past its own ``main()`` returning, proven with a subprocess.

Round 3 is an independent 27B-model review of round 2's commit, reproduced by
the operator, which found three more defects:

1. ``TestWindowPerformance`` — ``_enforce_budget`` recounted the WHOLE window
   on every pop (O(window size) per turn), and zero-cost turns
   (``add_user("")``) accumulated without bound since they never trigger
   eviction: 20,000 empty turns then one 15 kB turn measured 24.4 seconds
   inside a single ``add_assistant`` call.
2. ``TestDefaultAskDetector`` (extended again) — the trigger was checked in
   EVERY clause, so a non-imperative first clause ("don't you see, ...") or
   a "?" anywhere but the end ("...? sorry, wrong chat") still matched.
3. ``TestAttack.test_untrusted_memory_code_and_record_id_are_sanitized`` —
   a fake ``memory`` whose ``degradation.code`` and ``record_id`` carried a
   marker string landed that marker in ``session.degradations`` and
   ``report.to_dict()``; both are now allow-listed/shape-checked.

Also pinned (not a defect, a reviewer claim rejected with evidence — see
``TestAttack.test_lone_surrogate_does_not_raise``): a lone UTF-16 surrogate in
turn text does NOT raise through ``TranscriptLog.write``, because
``json.dumps``'s default ``ensure_ascii=True`` escapes it as ``\\ud800``
rather than trying to encode it as UTF-8.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed argv, no shell, a throwaway child interpreter
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment import session as sess
from embodiment.context import count_tokens_chars
from embodiment.daemon.state import DaemonState

MARK = "MARKER-SECRET-9f8a7"

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


class RaisingMemory:
    """A ``memory`` whose ``remember`` always raises. ``RoomMemory`` itself
    promises never to; this simulates a duck-typed host object that breaks
    that promise, which round 2 found ``Session`` did not defend against.
    """

    def __init__(self, message: str = "store exploded") -> None:
        self.message = message
        self.calls = 0

    def remember(self, text: str, **kwargs: Any) -> Any:
        self.calls += 1
        raise RuntimeError(self.message)


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

    def test_zero_cost_turn_is_not_added_to_the_window(self, tmp_path: Path) -> None:
        # round 3: a turn whose own estimate is 0 is transcripted and counted
        # in turns_seen, but never enters the window at all - there is
        # nothing for the budget check to ever evict it for.
        session, _mem, _state = _session(tmp_path, budget_tokens=100)
        session.add_user("")
        assert session.messages() == []
        assert session.window_tokens() == 0
        assert session.turns_seen == 1


# ── round 3, point 1: O(1) amortised enforcement, zero-cost turns bounded ──


class TestWindowPerformance:
    def test_count_tokens_call_count_is_linear_in_turns_not_quadratic(self, tmp_path: Path) -> None:
        """The operator's "better" ask: count calls rather than wall-clock.

        One call per turn (to estimate THAT turn's own cost) - never a
        recount of the whole window on every eviction, which is what made
        the old ``_enforce_budget`` O(window size) per pop.
        """
        calls = 0

        def counting_counter(messages: list[dict[str, Any]]) -> int:
            nonlocal calls
            calls += 1
            return count_tokens_chars(messages)

        session, _mem, _state = _session(tmp_path, budget_tokens=50, count_tokens=counting_counter)
        self._isolate_from_transcript_io(session)
        n = 500
        for i in range(n):
            session.add_user(f"turn number {i} with a handful of words in it")
        assert calls == n, f"expected exactly {n} counter calls (one per turn), got {calls}"

    @staticmethod
    def _isolate_from_transcript_io(session: "sess.Session") -> None:
        """Stub the transcript write to a no-op, AFTER construction.

        t4's ``TranscriptLog.write`` does a full bounded-log rewrite +
        ``fsync`` per line — independently measured here at ~3.6ms/call
        REGARDLESS of ``max_bytes``, i.e. dominated by the fsync itself, not
        by window enforcement. The operator flagged that cost as a SEPARATE,
        already-tracked issue ("Not yours... already being fixed on another
        branch. Do not work around it.") — this stub does not touch
        session.py or the transcript layer at all, so it isolates the thing
        THIS round's point 1 is actually about (window enforcement) from
        that unrelated, disk-bound cost for the purpose of measurement only.
        """
        session.transcript.write = lambda *args, **kwargs: None  # type: ignore[method-assign]

    def test_20k_empty_turns_then_one_large_turn_is_fast(self, tmp_path: Path) -> None:
        """Reproduces the operator-measured scenario at the same scale.

        Old behaviour: 24.4s for this exact shape (20,000 empties then one
        15 kB turn), because each empty turn never evicts and each later
        append rescans the whole window. Measured against the fix (isolated
        from the unrelated transcript-write cost, see
        ``_isolate_from_transcript_io``): ~8ms. The bound below is a huge,
        CI-safe multiple of that — it would still fail hard against the old
        O(n^2) behaviour while never flaking on a loaded box.
        """
        session, _mem, _state = _session(tmp_path, budget_tokens=1000)
        self._isolate_from_transcript_io(session)
        start = time.perf_counter()
        for _ in range(20_000):
            session.add_user("")
        session.add_assistant("x" * 15_000)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"took {elapsed:.3f}s; expected well under a second"
        assert session.turns_seen == 20_001

    def test_4k_one_char_turns_then_large_turn_is_fast(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path, budget_tokens=1000)
        self._isolate_from_transcript_io(session)
        start = time.perf_counter()
        for _ in range(4_000):
            session.add_user("x")
        session.add_assistant("x" * 16_000)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"took {elapsed:.3f}s; expected well under a second"


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
        # NOT "hey, remember that ..." - round 3 tightened the detector so
        # only the first clause (or the second when the first is PURELY the
        # recognized "Gwen,"/"גוון," address) is ever eligible; "hey," is an
        # ordinary first clause, not a recognized address, so it now blocks
        # the ask. See TestDefaultAskDetector's negative table.
        session.add_user("Gwen, remember that the deploy key rotates monthly")
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
        text = "hello \u202e​world\u2066"
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

    def test_untrusted_memory_code_and_record_id_are_sanitized(self, tmp_path: Path) -> None:
        """Round 3: a hostile fake's ``degradation.code``/``record_id`` must
        never reach ``session.degradations`` or ``report.to_dict()`` verbatim.
        """

        class HostileDegradation:
            code = f"remember that {MARK}"  # not one of the known memory codes
            reason = "fake"

            def to_dict(self) -> dict[str, str]:
                return {"code": self.code, "reason": self.reason}

        class HostileResult:
            ok = False
            # a space and angle brackets put this outside the bounded safe
            # shape - MARK alone is hyphen/alnum only and would (correctly)
            # be ACCEPTED as a plausible opaque id, so this must not rely on
            # MARK's own shape to prove rejection.
            record_id = f"id {MARK} <<<injected>>>"
            degradation = HostileDegradation()
            raw = None

        class HostileMemory:
            def remember(self, text: str, **kwargs: Any) -> HostileResult:
                return HostileResult()

        session, _mem, _state = _session(tmp_path, memory=HostileMemory())
        outcome = session.add_user(f"remember that {MARK} is secret")
        assert outcome is not None
        assert outcome.refused is True
        assert outcome.record_id is None  # discarded, not passed through

        report = session.close(summarise=lambda msgs: f"summary mentioning {MARK}")

        haystacks = [repr(session), json.dumps(report.to_dict()), repr(outcome)]
        haystacks.extend(d.code + d.reason for d in session.degradations)
        for haystack in haystacks:
            assert MARK not in haystack, haystack
        # the code was rejected as unknown, not silently dropped
        assert any(
            "unknown" in d.reason for d in session.degradations if d.code == sess.CODE_ASK_REFUSED
        )
        # the bad record_id was counted, not silently discarded
        assert sess.CODE_UNSAFE_RECORD_ID in session.degradation_counts

    def test_safe_shaped_record_id_still_passes_through(self, tmp_path: Path) -> None:
        # the sanitizer must not reject a LEGITIMATE record_id.
        mem = FakeMemory()
        session, mem, _state = _session(tmp_path, memory=mem)
        outcome = session.add_user("remember that the door code changed")
        assert outcome is not None
        assert outcome.remembered is True
        assert outcome.record_id == "fake-1"  # FakeMemory's own shape: safe, passes

    def test_lone_surrogate_does_not_raise(self, tmp_path: Path) -> None:
        """Rejected reviewer claim, pinned: json.dumps's default
        ensure_ascii=True escapes a lone surrogate as \\ud800 rather than
        raising UnicodeEncodeError, so this must not raise through
        TranscriptLog.write either.
        """
        session, _mem, _state = _session(tmp_path)
        session.add_user("a\ud800b")  # must not raise
        assert session.messages()[-1]["content"] == "a\ud800b"


# ── ask detector, standalone (round 2+3: table-driven, positives AND negatives) ─

# (text, expected_substring_or_None). A substring, not an exact match, for
# the Hebrew positives — the interesting fact is WHAT survived the clause/
# address stripping, not the exact whitespace.
_ASK_POSITIVES: tuple[tuple[str, str], ...] = (
    ("remember that milk is in the fridge", "milk is in the fridge"),
    ("Gwen, remember that milk is in the fridge", "milk is in the fridge"),
    ("Gwen remember that milk is in the fridge", "milk is in the fridge"),
    ("תזכרי שהפגישה נדחתה ליום שלישי", "הפגישה"),
    ("גוון, תזכרי שהפגישה עם דני ביום שלישי בשמונה", "הפגישה"),
    ("תזכרי שאני אוהב קפה שחור בבוקר", "קפה"),
    # round 3: reported speech is a DOCUMENTED, ACCEPTED false positive, not
    # a bug — telling a command from a quotation needs understanding the
    # sentence, which this regex heuristic does not attempt. Kept here as a
    # positive ON PURPOSE so a future "fix" doesn't break it by accident
    # without noticing this is the intentional case.
    ("remember that we won, he said, and it was 1998", "we won"),
)

_ASK_NEGATIVES: tuple[str, ...] = (
    "",
    "what's the weather like today",
    "don't forget the milk",  # documented false negative
    # the three round-2 measured false positives, verbatim:
    "I don't remember that he ever called me back",
    "do you remember that film we saw",
    "אני לא בטוח, תזכרי שאמרתי משהו?",
    # additional negation/question frames the same rule must reject:
    "did you remember that we need milk",
    "can you remember that",
    "could you remember that the door is unlocked",
    # a trigger present, but the WHOLE utterance is a question:
    "remember that milk is in the fridge?",
    "תזכרי שהפגישה ביום שלישי?",
    # the three round-3 measured false positives, verbatim (the third is a
    # DUPLICATE of a round-2 case, re-asserted here since it is exactly the
    # "'?' anywhere, not just at the end" rule this round tightened):
    "remember that the milk is gone? sorry, wrong chat",
    "don't you see, remember that we're all in this together",
    # round 3: a filler word before the trigger that is NOT the recognized
    # address is now a false negative on purpose (tightened deliberately;
    # only the first clause, or the second when the first is PURELY "Gwen,"/
    # "גוון,", is ever eligible):
    "hey, remember that the deploy key rotates monthly",
)


class TestDefaultAskDetector:
    @pytest.mark.parametrize("text,expected_substring", _ASK_POSITIVES)
    def test_positive(self, text: str, expected_substring: str) -> None:
        result = sess.default_ask_detector(text)
        assert result is not None, text
        assert expected_substring in result, (text, result)

    @pytest.mark.parametrize("text", _ASK_NEGATIVES)
    def test_negative(self, text: str) -> None:
        assert sess.default_ask_detector(text) is None, text

    def test_empty_string_returns_none(self) -> None:
        assert sess.default_ask_detector("") is None


# ── round 2, point 2: memory.remember raising must never propagate ─────────


class TestTriggerPunctuation:
    """A transcriber's comma between the trigger and its ש (t15 round 6).

    Measured on the rig: the operator opened with «תזכרי ש…» several times and
    whisper wrote one of them with a comma. Both are the same breath and the
    same ask, and without this the comma made the trigger a clause of its own
    — leaving «תזכרי» alone as the only eligible clause, matching nothing.

    Added from ``realtime/t15`` with the coordinator's go-ahead; the module is
    t11's and everything else about the detector is unchanged.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "תזכרי שהחלב נגמר",
            "תזכרי, שהחלב נגמר",
            "תזכרי , שהחלב נגמר",
            "תזכרי. שהחלב נגמר",
            "גוון, תזכרי, שהחלב נגמר",
            "זכרי, שהחלב נגמר",
        ],
    )
    def test_the_same_ask_however_it_is_punctuated(self, text: str) -> None:
        assert sess.default_ask_detector(text) == "החלב נגמר"

    @pytest.mark.parametrize(
        "text",
        [
            "לא תזכרי, שהחלב נגמר",
            "אל תזכרי, שהחלב נגמר",
            "תזכרי, מה קרה",
            "תזכרי, ש",
            "האם תזכרי, שהחלב נגמר?",
        ],
    )
    def test_what_it_must_not_make_eligible(self, text: str) -> None:
        assert sess.default_ask_detector(text) is None

    def test_it_cannot_join_two_unrelated_clauses(self) -> None:
        """The narrowness is the point: only trigger-then-ש is rewritten."""
        assert sess.default_ask_detector("בוקר טוב, שמח לראות אותך") is None
        assert sess.default_ask_detector("תודה, שבאת") is None


class TestMemoryRaises:
    def test_add_user_returns_refused_outcome_not_an_exception(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        mem = RaisingMemory(f"store exploded while writing {MARK}")
        session = sess.Session(state, mem)
        outcome = session.add_user(f"remember that {MARK} is secret")  # must not raise
        assert outcome is not None
        assert outcome.detected is True
        assert outcome.refused is True
        assert outcome.remembered is False
        assert mem.calls == 1
        assert any(d.code == sess.CODE_ASK_MEMORY_ERROR for d in session.degradations)

    def test_close_returns_a_report_not_an_exception(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        mem = RaisingMemory()
        session = sess.Session(state, mem)
        session.add_user("hello")
        report = session.close(summarise=lambda msgs: "a fine summary")  # must not raise
        assert report.summary_landed is False
        assert report.summary_skip_reason == sess.CODE_SUMMARY_MEMORY_ERROR

    def test_second_close_after_memory_raised_returns_same_report(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        mem = RaisingMemory()
        session = sess.Session(state, mem)
        session.add_user("hello")
        first = session.close(summarise=lambda msgs: "a fine summary")
        second = session.close()  # must not raise (AssertionError, round 2's bug)
        assert second is first

    def test_no_exception_text_leaks_anywhere(self, tmp_path: Path) -> None:
        state = _state(tmp_path)
        mem = RaisingMemory(f"boom: {MARK}")
        session = sess.Session(state, mem)
        session.add_user(f"remember that {MARK} matters")
        report = session.close(summarise=lambda msgs: f"summary mentioning {MARK}")
        haystacks = [repr(session), json.dumps(report.to_dict())]
        haystacks.extend(d.code + d.reason for d in session.degradations)
        for haystack in haystacks:
            assert MARK not in haystack, haystack


# ── round 2, point 3: degradations are bounded, deduped, and counted ───────


class TestBoundedDegradations:
    def test_deduped_to_one_representative_with_an_accurate_count(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path, budget_tokens=1)
        for _ in range(500):
            session.add_assistant("x" * 50)  # each is, alone, over budget=1
        assert len(session.degradations) == 1
        assert session.degradation_counts[sess.CODE_TURN_EXCEEDS_BUDGET] == 500
        assert session.degradations_dropped == 0

    def test_close_report_carries_the_same_counts(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path, budget_tokens=1)
        for _ in range(37):
            session.add_assistant("x" * 50)
        report = session.close()
        assert len(report.degradations) == 1
        assert report.degradation_counts[sess.CODE_TURN_EXCEEDS_BUDGET] == 37
        assert report.degradations_dropped == 0

    def test_distinct_codes_each_get_a_representative(self, tmp_path: Path) -> None:
        mem = RaisingMemory()
        session, mem, _state = _session(tmp_path, memory=mem, budget_tokens=1)
        session.add_assistant("x" * 50)  # CODE_TURN_EXCEEDS_BUDGET
        session.add_user(f"remember that {MARK}")  # CODE_ASK_MEMORY_ERROR
        codes = {d.code for d in session.degradations}
        assert sess.CODE_TURN_EXCEEDS_BUDGET in codes
        assert sess.CODE_ASK_MEMORY_ERROR in codes


# ── round 2, point 4: the summary is honest about what it saw ──────────────


class TestSummaryHonesty:
    def test_report_distinguishes_seen_from_summarised(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path, budget_tokens=30)
        for i in range(50):
            session.add_user(f"turn number {i} with some words in it")
        report = session.close(summarise=lambda msgs: "a summary")
        assert report.turns_seen == 50
        assert 0 < report.turns_summarised < 50

    def test_written_record_metadata_carries_both_counts(self, tmp_path: Path) -> None:
        session, mem, _state = _session(tmp_path, budget_tokens=30)
        for i in range(50):
            session.add_user(f"turn number {i} with some words in it")
        report = session.close(summarise=lambda msgs: "a summary")
        _text, kwargs = mem.calls[-1]
        assert kwargs["metadata"]["turns_seen"] == 50
        assert kwargs["metadata"]["turns_summarised"] == report.turns_summarised
        assert kwargs["metadata"]["turns_summarised"] < kwargs["metadata"]["turns_seen"]

    def test_no_summarise_reports_zero_turns_summarised(self, tmp_path: Path) -> None:
        session, _mem, _state = _session(tmp_path)
        session.add_user("hello")
        report = session.close()  # no summarise
        assert report.turns_summarised == 0


# ── round 2, point 6: a hung summariser must not hold the process hostage ──


class TestThreadExit:
    def test_hung_summariser_does_not_block_process_exit(self, tmp_path: Path) -> None:
        """Proof, not argument: run a real close() with a never-returning
        summariser in a CHILD interpreter and assert the process exits on
        its own well inside a generous external timeout.

        A prior implementation used a ``ThreadPoolExecutor`` for this:
        measured separately (see the round-2 report), its non-daemon worker
        thread survives ``shutdown(wait=False)`` and is joined by
        ``concurrent.futures``'s own ``atexit`` hook, which blocked the
        WHOLE PROCESS from exiting even after ``close()`` itself had
        returned and even after the script's ``print`` had already run.
        This test would hang (and fail via ``TimeoutExpired``) against that
        implementation; it passes against the current daemon-thread one.
        """
        repo_root = Path(sess.__file__).resolve().parent.parent
        state_dir = tmp_path / "state"
        script = f"""
import sys
sys.path.insert(0, {str(repo_root)!r})
import threading
from embodiment import session as sess
from embodiment.daemon.state import DaemonState


class _Result:
    ok = True
    record_id = "x"
    degradation = None


class FakeMemory:
    def remember(self, text, **kw):
        return _Result()


state = DaemonState({str(state_dir)!r})
s = sess.Session(state, FakeMemory())
s.add_user("hello")
never = threading.Event()
report = s.close(lambda m: never.wait() or "late", deadline=0.2)
print("CLOSE_RETURNED", report.summary_skip_reason)
"""
        result = subprocess.run(  # nosec B603 - fixed argv, no shell, sys.executable
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert "CLOSE_RETURNED" in result.stdout, result.stdout + result.stderr
        assert result.returncode == 0, result.stdout + result.stderr
