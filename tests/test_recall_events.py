"""Tests for :mod:`embodiment.daemon.recall_events` — background recall (plan t13).

Every seam is injected: recall, complete, speak, ``stop_playback``,
``floor_held``, the clock and the thread spawner. The spawner is the lever that
keeps these deterministic: :class:`ManualSpawn` holds the recall job instead of
starting a thread, and the test runs it at the exact moment the case needs —
inside the first completion (before the first sentence), inside the speak of
sentence 1 (during the reply), or after :meth:`run_turn` returned (a late
arrival). One test drives the real threaded spawner, bounded by ``wait``.

All text here is synthetic. Nothing is copied from Gwen's store.
"""

from __future__ import annotations

import ast
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from embodiment.daemon import recall_events as re_mod
from embodiment.daemon.recall_events import (
    DROP_ALREADY_SPOKEN,
    DROP_BELOW_THRESHOLD,
    DROP_BUSY,
    DROP_CLOSED,
    DROP_COMPOSE_FAILED,
    DROP_EMPTY,
    DROP_FLOOR_HELD,
    DROP_MALFORMED,
    DROP_NO_THRESHOLD,
    DROP_NO_WINDOW,
    DROP_OUTSIDE_WINDOW,
    DROP_RECALL_FAILED,
    DROP_SUPERSEDED,
    DROP_UNSPEAKABLE,
    LANDED_BEFORE_FIRST_SENTENCE,
    LANDED_DROPPED,
    LANDED_DURING_REPLY,
    LANDED_FOLLOW_UP,
    PURPOSE_FOLLOW_UP,
    PURPOSE_REMAINDER,
    PURPOSE_REPLY,
    PURPOSE_RESTART,
    RECALL_EVENTS_COMPLETE_FAILED,
    RECALL_EVENTS_RECALL_FAILED,
    RECALL_EVENTS_RESTARTED,
    RECALL_EVENTS_SEAM_FAILED,
    RECALL_EVENTS_SPEAK_FAILED,
    BackgroundRecall,
    CompletionRequest,
    Recalled,
    RecallEventsConfig,
    ReplyOutcome,
    Unprompted,
    UnpromptedEvent,
    UnpromptedOutcome,
)
from embodiment.safe_reason import LABEL_CHARSET, LABEL_PLACEHOLDER
from embodiment.turn import FALLBACK_TEXT

MEMORY = "[memory] the synthetic blue key is under the synthetic mat"
FIRST = "First one. First two. First three."
REMAINDER = "Second two. Second three."
WITH_MEMORY = "Remembered one. Remembered two."
FOLLOW_UP = "By the way, I remembered something."
RESTART = "Let me correct that. Here is the right answer."
WINDOW_S = 5.0
THRESHOLD = 0.5

COUNTERS = (
    LANDED_BEFORE_FIRST_SENTENCE,
    LANDED_DURING_REPLY,
    LANDED_FOLLOW_UP,
    LANDED_DROPPED,
)


# ── fakes ────────────────────────────────────────────────────────────────


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ManualSpawn:
    """Holds each job instead of running it; the test decides when it lands."""

    def __init__(self) -> None:
        self.jobs: list[Callable[[], None]] = []

    def __call__(self, job: Callable[[], None]) -> None:
        self.jobs.append(job)

    def land(self) -> None:
        job = self.jobs.pop(0)
        job()


class Harness:
    """One orchestrator with every seam faked and every call logged in order."""

    def __init__(
        self,
        *,
        first: str = FIRST,
        remainder: str = REMAINDER,
        follow_up: str = FOLLOW_UP,
        restart: str = RESTART,
        recalled: Any = None,
        window: Optional[float] = WINDOW_S,
        threshold: Optional[float] = THRESHOLD,
        contradicts: Optional[Callable[[str, tuple[str, ...]], bool]] = None,
    ) -> None:
        self.clock = FakeClock()
        self.spawn = ManualSpawn()
        self.floor = False
        self.log: list[tuple[str, str]] = []
        self.requests: list[CompletionRequest] = []
        self.records: list[tuple[str, str]] = []
        self.texts = {
            PURPOSE_REPLY: first,
            PURPOSE_REMAINDER: remainder,
            PURPOSE_FOLLOW_UP: follow_up,
            PURPOSE_RESTART: restart,
        }
        self.recalled = (
            recalled if recalled is not None else Recalled(rendered=MEMORY, relevance=0.9)
        )
        self.on_complete: dict[int, Callable[[], None]] = {}
        self.on_speak: dict[int, Callable[[], None]] = {}
        self.speak_count = 0
        self.stops = 0
        self.config = RecallEventsConfig(follow_up_window_s=window, follow_up_threshold=threshold)
        self.app = BackgroundRecall(
            recall=self.recall,
            complete=self.complete,
            speak=self.speak,
            stop_playback=self.stop_playback,
            floor_held=lambda: self.floor,
            clock=self.clock,
            config=self.config,
            contradicts=contradicts,
            record=lambda code, reason: self.records.append((code, reason)),
            spawn=self.spawn,
        )

    # seams
    def recall(self, query: str) -> Any:
        self.log.append(("recall", query))
        if isinstance(self.recalled, BaseException):
            raise self.recalled
        return self.recalled

    def complete(self, request: CompletionRequest) -> str:
        self.requests.append(request)
        hook = self.on_complete.pop(len(self.requests), None)
        if hook is not None:
            hook()
        self.log.append(("complete", request.purpose))
        text = self.texts[request.purpose]
        if request.purpose == PURPOSE_REPLY and request.memory:
            text = WITH_MEMORY
        if isinstance(text, BaseException):
            raise text
        return text

    def speak(self, sentence: str) -> None:
        self.speak_count += 1
        self.log.append(("speak", sentence))
        hook = self.on_speak.pop(self.speak_count, None)
        if hook is not None:
            hook()

    def stop_playback(self) -> None:
        self.stops += 1
        self.log.append(("stop", ""))

    # helpers
    @property
    def spoken(self) -> list[str]:
        return [text for kind, text in self.log if kind == "speak"]

    def counters(self) -> dict[str, int]:
        return dict(self.app.status()["arrivals"])

    def dropped_reasons(self) -> dict[str, int]:
        return dict(self.app.status()["dropped_reasons"])


def _assert_exactly_one(h: Harness, landed: str) -> None:
    counts = h.counters()
    assert set(counts) == set(COUNTERS)
    assert sum(counts.values()) == 1, counts
    assert counts[landed] == 1, counts


# ── c59 / h47: a recall during the reply regenerates the remainder ────────


class TestRemainderRegeneration:
    def test_recall_after_sentence_one_of_three_regenerates_the_remainder(self) -> None:
        h = Harness()
        h.on_speak[1] = h.spawn.land  # the recall lands after sentence 1 of 3
        outcome = h.app.run_turn("where is the key?")

        assert h.spoken == ["First one.", "Second two.", "Second three."]
        # The first completion's unspoken sentences are dropped, never spliced.
        assert "First two." not in h.spoken and "First three." not in h.spoken
        second = h.requests[1]
        assert second.purpose == PURPOSE_REMAINDER
        assert second.memory == MEMORY
        assert second.spoken == ("First one.",)
        assert second.query == "where is the key?"
        assert h.requests[0].memory == "" and h.requests[0].spoken == ()
        assert isinstance(outcome, ReplyOutcome)
        assert outcome.regenerated is True and outcome.restarted is False
        assert outcome.sentences_dropped == 2
        assert outcome.spoken == ("First one.", "Second two.", "Second three.")
        _assert_exactly_one(h, LANDED_DURING_REPLY)
        status = h.app.status()
        assert status["regenerations"] == 1 and status["sentences_dropped"] == 2
        assert h.stops == 0  # a remainder is not a restart; nothing is stopped

    def test_generation_starts_without_waiting_for_memory(self) -> None:
        h = Harness()
        h.app.run_turn("q")
        # The recall job was spawned and never ran; the reply was spoken anyway.
        assert len(h.spawn.jobs) == 1
        assert h.requests[0].memory == ""
        assert h.spoken == ["First one.", "First two.", "First three."]
        assert h.app.status()["pending"] == 1

    def test_recall_landing_before_the_first_sentence_feeds_the_whole_reply(self) -> None:
        h = Harness()
        h.on_complete[1] = h.spawn.land  # lands while the first completion runs
        outcome = h.app.run_turn("q")

        second = h.requests[1]
        assert second.purpose == PURPOSE_REPLY
        assert second.memory == MEMORY and second.spoken == ()
        assert h.spoken == ["Remembered one.", "Remembered two."]
        assert outcome.sentences_dropped == 3
        _assert_exactly_one(h, LANDED_BEFORE_FIRST_SENTENCE)

    def test_an_empty_recall_during_the_reply_changes_nothing_and_is_dropped(self) -> None:
        h = Harness(recalled=Recalled(rendered="", relevance=None))
        h.on_speak[1] = h.spawn.land
        h.app.run_turn("q")
        assert h.spoken == ["First one.", "First two.", "First three."]
        assert len(h.requests) == 1
        _assert_exactly_one(h, LANDED_DROPPED)
        assert h.dropped_reasons() == {DROP_EMPTY: 1}

    def test_a_failed_remainder_completion_degrades_to_the_original_and_is_recorded(
        self,
    ) -> None:
        h = Harness()
        h.texts[PURPOSE_REMAINDER] = RuntimeError("synthetic gateway fault with words")
        h.on_speak[1] = h.spawn.land
        outcome = h.app.run_turn("q")
        assert h.spoken == ["First one.", "First two.", "First three."]
        assert outcome.regenerated is False
        assert [code for code, _ in h.records] == [RECALL_EVENTS_COMPLETE_FAILED]
        assert "words" not in h.records[0][1]

    def test_an_unspeakable_remainder_degrades_to_the_original(self) -> None:
        h = Harness(remainder="  ...  ")
        h.on_speak[1] = h.spawn.land
        h.app.run_turn("q")
        assert h.spoken == ["First one.", "First two.", "First three."]
        assert [code for code, _ in h.records] == [RECALL_EVENTS_COMPLETE_FAILED]

    def test_a_failed_recall_is_recorded_and_dropped(self) -> None:
        h = Harness(recalled=OSError("synthetic store fault quoting a record"))
        h.on_speak[1] = h.spawn.land
        h.app.run_turn("q")
        assert h.spoken == ["First one.", "First two.", "First three."]
        _assert_exactly_one(h, LANDED_DROPPED)
        assert h.dropped_reasons() == {DROP_RECALL_FAILED: 1}
        assert [code for code, _ in h.records] == [RECALL_EVENTS_RECALL_FAILED]
        assert "quoting" not in h.records[0][1]

    def test_a_garbage_recall_result_is_treated_as_empty(self) -> None:
        h = Harness(recalled=object())
        h.on_speak[1] = h.spawn.land
        h.app.run_turn("q")
        _assert_exactly_one(h, LANDED_DROPPED)
        assert h.dropped_reasons() == {DROP_EMPTY: 1}

    def test_a_plain_string_recall_result_is_accepted_as_rendered_memory(self) -> None:
        h = Harness(recalled=MEMORY)
        h.on_speak[1] = h.spawn.land
        h.app.run_turn("q")
        assert h.requests[1].memory == MEMORY
        _assert_exactly_one(h, LANDED_DURING_REPLY)

    def test_the_first_completion_failing_still_speaks(self) -> None:
        """The reply keeps the never-silent rule; only the follow-up is exempt."""
        h = Harness()
        h.texts[PURPOSE_REPLY] = RuntimeError("synthetic")
        h.app.run_turn("q")
        assert h.spoken == [FALLBACK_TEXT]
        assert [code for code, _ in h.records] == [RECALL_EVENTS_COMPLETE_FAILED]


# ── c60 / h48: a late recall becomes at most one follow-up ───────────────


class TestFollowUp:
    def test_a_late_relevant_recall_is_one_follow_up(self) -> None:
        h = Harness()
        h.app.run_turn("q")
        h.clock.advance(1.0)
        h.spawn.land()
        assert h.spoken[-1:] == [FOLLOW_UP]
        follow = h.requests[-1]
        assert follow.purpose == PURPOSE_FOLLOW_UP
        assert follow.memory == MEMORY
        assert follow.spoken == ("First one.", "First two.", "First three.")
        _assert_exactly_one(h, LANDED_FOLLOW_UP)

    def test_below_the_threshold_it_is_dropped_and_silent(self) -> None:
        h = Harness(recalled=Recalled(rendered=MEMORY, relevance=0.1))
        h.app.run_turn("q")
        before = list(h.spoken)
        h.spawn.land()
        assert h.spoken == before
        _assert_exactly_one(h, LANDED_DROPPED)
        assert h.dropped_reasons() == {DROP_BELOW_THRESHOLD: 1}

    def test_exactly_at_the_threshold_it_clears(self) -> None:
        h = Harness(recalled=Recalled(rendered=MEMORY, relevance=THRESHOLD))
        h.app.run_turn("q")
        h.spawn.land()
        _assert_exactly_one(h, LANDED_FOLLOW_UP)

    def test_no_relevance_score_never_clears_the_threshold(self) -> None:
        h = Harness(recalled=Recalled(rendered=MEMORY, relevance=None))
        h.app.run_turn("q")
        h.spawn.land()
        assert h.dropped_reasons() == {DROP_BELOW_THRESHOLD: 1}

    def test_while_the_operator_holds_the_floor_it_is_dropped_and_silent(self) -> None:
        h = Harness()
        h.app.run_turn("q")
        before = list(h.spoken)
        h.floor = True  # a speech_started arrived
        h.spawn.land()
        assert h.spoken == before
        assert len(h.requests) == 1  # not even composed
        _assert_exactly_one(h, LANDED_DROPPED)
        assert h.dropped_reasons() == {DROP_FLOOR_HELD: 1}

    def test_after_the_window_it_is_dropped_and_silent(self) -> None:
        h = Harness()
        h.app.run_turn("q")
        before = list(h.spoken)
        h.clock.advance(WINDOW_S + 0.01)
        h.spawn.land()
        assert h.spoken == before
        _assert_exactly_one(h, LANDED_DROPPED)
        assert h.dropped_reasons() == {DROP_OUTSIDE_WINDOW: 1}

    def test_the_window_is_measured_from_the_end_of_the_reply(self) -> None:
        h = Harness()
        h.on_speak[3] = lambda: h.clock.advance(WINDOW_S * 3)  # a long reply
        h.app.run_turn("q")
        h.clock.advance(WINDOW_S - 0.5)
        h.spawn.land()
        _assert_exactly_one(h, LANDED_FOLLOW_UP)

    def test_with_no_window_configured_follow_ups_are_off(self) -> None:
        """The window is a parameter derived from measurement, never a guess here."""
        assert RecallEventsConfig().follow_up_window_s is None
        assert RecallEventsConfig().follow_up_threshold is None
        h = Harness(window=None)
        h.app.run_turn("q")
        h.spawn.land()
        assert h.dropped_reasons() == {DROP_NO_WINDOW: 1}

    def test_with_no_threshold_configured_follow_ups_are_off(self) -> None:
        h = Harness(threshold=None)
        h.app.run_turn("q")
        h.spawn.land()
        assert h.dropped_reasons() == {DROP_NO_THRESHOLD: 1}

    def test_a_newer_turn_supersedes_the_follow_up(self) -> None:
        h = Harness()
        h.app.run_turn("first question")
        h.app.run_turn("second question")
        h.spawn.land()  # the FIRST turn's recall, late
        assert h.dropped_reasons() == {DROP_SUPERSEDED: 1}
        assert FOLLOW_UP not in h.spoken

    def test_a_recall_landing_during_the_last_sentence_takes_the_late_path(self) -> None:
        h = Harness()
        h.on_speak[3] = h.spawn.land  # lands while the final sentence is speaking
        h.app.run_turn("q")
        assert h.spoken == ["First one.", "First two.", "First three.", FOLLOW_UP]
        _assert_exactly_one(h, LANDED_FOLLOW_UP)

    def test_a_follow_up_whose_completion_is_empty_is_dropped_never_the_fallback(self) -> None:
        h = Harness(follow_up="")
        h.app.run_turn("q")
        before = list(h.spoken)
        h.spawn.land()
        assert h.spoken == before
        assert FALLBACK_TEXT not in h.spoken
        assert h.dropped_reasons() == {DROP_UNSPEAKABLE: 1}

    def test_the_floor_taken_mid_follow_up_stops_it(self) -> None:
        h = Harness(follow_up="One. Two. Three.")
        h.app.run_turn("q")
        h.on_speak[4] = lambda: setattr(h, "floor", True)
        h.spawn.land()
        assert h.spoken[3:] == ["One."]
        _assert_exactly_one(h, LANDED_FOLLOW_UP)

    def test_a_barge_in_mid_reply_sends_the_arrival_down_the_late_path(self) -> None:
        h = Harness()

        def barge() -> None:
            h.spawn.land()
            h.floor = True

        h.on_speak[1] = barge
        outcome = h.app.run_turn("q")
        assert h.spoken == ["First one."]
        assert outcome.interrupted is True
        _assert_exactly_one(h, LANDED_DROPPED)
        assert h.dropped_reasons() == {DROP_FLOOR_HELD: 1}

    def test_after_close_a_late_arrival_is_dropped(self) -> None:
        h = Harness()
        h.app.run_turn("q")
        h.app.close()
        h.spawn.land()
        assert h.dropped_reasons() == {DROP_CLOSED: 1}
        assert FOLLOW_UP not in h.spoken


# ── c61 / h49: stop-and-restart ──────────────────────────────────────────


class TestRestart:
    def test_an_inconsistent_memory_stops_playback_records_why_and_speaks_anew(self) -> None:
        seen: list[tuple[str, tuple[str, ...]]] = []

        def contradicts(memory: str, spoken: tuple[str, ...]) -> bool:
            seen.append((memory, spoken))
            return True

        h = Harness(contradicts=contradicts)
        h.on_speak[1] = h.spawn.land
        outcome = h.app.run_turn("where is it?")

        assert seen == [(MEMORY, ("First one.",))]
        acts = [entry for entry in h.log if entry[0] != "recall"]
        stop_at = acts.index(("stop", ""))
        assert acts[stop_at - 1] == ("speak", "First one.")
        assert acts[stop_at + 1] == ("complete", PURPOSE_RESTART)  # stop, THEN the new words
        assert acts[stop_at + 2][0] == "speak"
        assert h.spoken == ["First one.", "Let me correct that.", "Here is the right answer."]
        assert h.stops == 1
        assert outcome.restarted is True
        restart = [r for r in h.records if r[0] == RECALL_EVENTS_RESTARTED]
        assert len(restart) == 1
        reason = restart[0][1]
        assert reason  # it says why
        for leaked in ("synthetic", "First", "key", "mat", "where"):
            assert leaked not in reason, reason
        assert h.app.status()["restarts"] == 1
        _assert_exactly_one(h, LANDED_DURING_REPLY)
        restart_request = [r for r in h.requests if r.purpose == PURPOSE_RESTART][0]
        assert restart_request.memory == MEMORY
        assert restart_request.spoken == ("First one.",)

    def test_a_restart_never_fires_while_the_operator_holds_the_floor(self) -> None:
        def contradicts(memory: str, spoken: tuple[str, ...]) -> bool:
            h.floor = True  # speech_started lands at the decision point
            return True

        h = Harness(contradicts=contradicts)
        h.on_speak[1] = h.spawn.land
        outcome = h.app.run_turn("q")
        assert h.stops == 0
        assert outcome.restarted is False
        assert not [r for r in h.records if r[0] == RECALL_EVENTS_RESTARTED]
        assert h.spoken == ["First one."]

    def test_a_restart_is_never_considered_before_anything_was_spoken(self) -> None:
        calls: list[int] = []
        h = Harness(contradicts=lambda m, s: calls.append(1) or True)
        h.on_complete[1] = h.spawn.land
        h.app.run_turn("q")
        assert calls == [] and h.stops == 0
        _assert_exactly_one(h, LANDED_BEFORE_FIRST_SENTENCE)

    def test_without_a_contradicts_seam_there_is_never_a_restart(self) -> None:
        h = Harness()
        h.on_speak[1] = h.spawn.land
        h.app.run_turn("q")
        assert h.stops == 0 and h.app.status()["restarts"] == 0

    def test_a_raising_contradicts_seam_is_recorded_and_means_no_restart(self) -> None:
        def contradicts(memory: str, spoken: tuple[str, ...]) -> bool:
            raise ValueError("synthetic judge fault")

        h = Harness(contradicts=contradicts)
        h.on_speak[1] = h.spawn.land
        h.app.run_turn("q")
        assert h.stops == 0
        assert h.spoken == ["First one.", "Second two.", "Second three."]
        assert RECALL_EVENTS_SEAM_FAILED in [c for c, _ in h.records]

    def test_a_failed_restart_completion_is_recorded_and_says_nothing_more(self) -> None:
        h = Harness(contradicts=lambda m, s: True)
        h.texts[PURPOSE_RESTART] = RuntimeError("synthetic")
        h.on_speak[1] = h.spawn.land
        h.app.run_turn("q")
        assert h.spoken == ["First one."]
        codes = [c for c, _ in h.records]
        assert codes == [RECALL_EVENTS_RESTARTED, RECALL_EVENTS_COMPLETE_FAILED]


# ── c62 / h50: every arrival increments exactly one of four counters ─────


def _before(h: Harness) -> None:
    h.on_complete[len(h.requests) + 1] = h.spawn.land
    h.app.run_turn("q")


def _during(h: Harness) -> None:
    h.on_speak[h.speak_count + 2] = h.spawn.land
    h.app.run_turn("q")


def _follow_up(h: Harness) -> None:
    h.app.run_turn("q")
    h.spawn.land()


def _dropped(h: Harness) -> None:
    h.app.run_turn("q")
    h.clock.advance(WINDOW_S * 2)
    h.spawn.land()


class TestArrivalCounters:
    @pytest.mark.parametrize(
        ("drive", "landed"),
        [
            (_before, LANDED_BEFORE_FIRST_SENTENCE),
            (_during, LANDED_DURING_REPLY),
            (_follow_up, LANDED_FOLLOW_UP),
            (_dropped, LANDED_DROPPED),
        ],
        ids=["before-first-sentence", "during-reply", "follow-up", "dropped"],
    )
    def test_each_arrival_increments_exactly_one_counter(
        self, drive: Callable[[Harness], None], landed: str
    ) -> None:
        h = Harness()
        drive(h)
        _assert_exactly_one(h, landed)
        status = h.app.status()
        assert status["pending"] == 0
        assert status["arrival_n"] == 1
        assert len(status["arrival_samples"]) == 1
        assert status["arrival_samples"][0]["landed"] == landed

    def test_counters_sum_to_arrivals_across_many_turns(self) -> None:
        h = Harness()
        for drive in (_before, _during, _follow_up, _dropped, _follow_up):
            drive(h)
        counts = h.counters()
        assert sum(counts.values()) == 5 == h.app.status()["arrival_n"]
        assert counts[LANDED_FOLLOW_UP] == 2

    def test_the_arrival_distribution_carries_latency_and_offset_from_reply_end(
        self,
    ) -> None:
        h = Harness()
        h.app.run_turn("q")  # the fake clock does not move during the reply
        h.clock.advance(1.25)
        h.spawn.land()
        sample = h.app.status()["arrival_samples"][0]
        assert sample["latency_s"] == pytest.approx(1.25)
        assert sample["after_reply_end_s"] == pytest.approx(1.25)

        h2 = Harness()
        h2.on_speak[1] = lambda: (h2.clock.advance(0.4), h2.spawn.land())
        h2.app.run_turn("q")
        sample2 = h2.app.status()["arrival_samples"][0]
        assert sample2["latency_s"] == pytest.approx(0.4)
        assert sample2["after_reply_end_s"] is None

    def test_the_sample_buffer_is_bounded(self) -> None:
        h = Harness()
        h.app = BackgroundRecall(
            recall=h.recall,
            complete=h.complete,
            speak=h.speak,
            stop_playback=h.stop_playback,
            floor_held=lambda: h.floor,
            clock=h.clock,
            config=RecallEventsConfig(max_samples=3),
            spawn=h.spawn,
        )
        for _ in range(5):
            h.app.run_turn("q")
            h.spawn.land()
        status = h.app.status()
        assert len(status["arrival_samples"]) == 3
        assert status["arrival_n"] == 5

    def test_a_late_answer_is_counted_not_recorded_as_a_degradation(self) -> None:
        h = Harness()
        _dropped(h)
        assert h.records == []
        assert h.app.status()["degradations"] == {}

    def test_status_is_json_shaped(self) -> None:
        import json

        h = Harness()
        _follow_up(h)
        json.dumps(h.app.status())


# ── c63 / h51: the unprompted-speech seam ────────────────────────────────


def _unprompted(**overrides: Any) -> tuple[Unprompted, list[str], dict[str, Any]]:
    spoken: list[str] = []
    state: dict[str, Any] = {"floor": False, "now": 10.0, "records": []}
    kwargs: dict[str, Any] = dict(
        speak=spoken.append,
        floor_held=lambda: state["floor"],
        clock=lambda: state["now"],
        record=lambda c, r: state["records"].append((c, r)),
    )
    kwargs.update(overrides)
    return Unprompted(**kwargs), spoken, state


class TestUnpromptedSeam:
    def test_a_non_recall_caller_can_make_gwen_speak_unprompted(self) -> None:
        """#89's shape: a job result spoken as a later turn nobody asked for."""
        seam, spoken, _ = _unprompted()
        outcome = seam.offer(
            UnpromptedEvent(source="job", key="job-1", text="The job is done. It passed.")
        )
        assert isinstance(outcome, UnpromptedOutcome)
        assert outcome.spoken is True and outcome.sentences_spoken == 2
        assert spoken == ["The job is done.", "It passed."]
        assert seam.status()["spoken"] == 1

    def test_the_background_recall_uses_the_same_seam(self) -> None:
        seam, spoken, _ = _unprompted()
        h = Harness()
        h.app = BackgroundRecall(
            recall=h.recall,
            complete=h.complete,
            speak=h.speak,
            stop_playback=h.stop_playback,
            floor_held=lambda: h.floor,
            clock=h.clock,
            config=h.config,
            spawn=h.spawn,
            unprompted=seam,
        )
        h.app.run_turn("q")
        h.spawn.land()
        assert spoken == [FOLLOW_UP]
        assert seam.status()["offered"] == 1
        assert h.app.status()["unprompted"] == seam.status()

    @pytest.mark.parametrize("composed", ["", "   ", "...", "‏"])
    def test_a_dropped_follow_up_never_fires_the_never_silent_rule(self, composed: str) -> None:
        seam, spoken, _ = _unprompted()
        outcome = seam.offer(UnpromptedEvent(source="job", key="k", compose=lambda: composed))
        assert outcome.spoken is False and outcome.reason == DROP_UNSPEAKABLE
        assert spoken == []  # no FALLBACK_TEXT, no audio at all

    def test_a_raising_composer_is_recorded_and_silent(self) -> None:
        def compose() -> str:
            raise RuntimeError("synthetic compose fault with words")

        seam, spoken, state = _unprompted()
        outcome = seam.offer(UnpromptedEvent(source="job", key="k", compose=compose))
        assert outcome.reason == DROP_COMPOSE_FAILED and spoken == []
        assert len(state["records"]) == 1 and "words" not in state["records"][0][1]

    def test_at_most_one_utterance_per_key(self) -> None:
        seam, spoken, _ = _unprompted()
        assert seam.offer(UnpromptedEvent(source="s", key="k", text="One.")).spoken
        second = seam.offer(UnpromptedEvent(source="s", key="k", text="Two."))
        assert second.spoken is False and second.reason == DROP_ALREADY_SPOKEN
        assert spoken == ["One."]

    def test_the_floor_and_the_window_gate_every_caller(self) -> None:
        seam, spoken, state = _unprompted()
        state["floor"] = True
        assert seam.offer(UnpromptedEvent(source="s", key="a", text="x.")).reason == (
            DROP_FLOOR_HELD
        )
        state["floor"] = False
        late = UnpromptedEvent(source="s", key="b", text="x.", not_after=9.0)
        assert seam.offer(late).reason == DROP_OUTSIDE_WINDOW
        stale = UnpromptedEvent(source="s", key="c", text="x.", still_relevant=lambda: False)
        assert seam.offer(stale).reason == DROP_SUPERSEDED
        assert spoken == []
        assert seam.status()["dropped"] == {
            DROP_FLOOR_HELD: 1,
            DROP_OUTSIDE_WINDOW: 1,
            DROP_SUPERSEDED: 1,
        }

    def test_busy_drops_rather_than_talking_over_a_reply(self) -> None:
        seam, spoken, _ = _unprompted(busy=lambda: True)
        assert seam.offer(UnpromptedEvent(source="s", key="k", text="x.")).reason == DROP_BUSY
        assert spoken == []

    def test_the_floor_is_rechecked_after_composing(self) -> None:
        seam, spoken, state = _unprompted()

        def compose() -> str:
            state["floor"] = True
            return "Something."

        outcome = seam.offer(UnpromptedEvent(source="s", key="k", compose=compose))
        assert outcome.reason == DROP_FLOOR_HELD and spoken == []

    @pytest.mark.parametrize("bad", [None, "text", 42, object()])
    def test_a_malformed_event_is_dropped_never_raised(self, bad: Any) -> None:
        seam, spoken, _ = _unprompted()
        outcome = seam.offer(bad)
        assert outcome.spoken is False and outcome.reason == DROP_MALFORMED
        assert spoken == []

    def test_a_raising_floor_seam_counts_as_held(self) -> None:
        def floor() -> bool:
            raise RuntimeError("synthetic")

        seam, spoken, state = _unprompted(floor_held=floor)
        assert seam.offer(UnpromptedEvent(source="s", key="k", text="x.")).reason == (
            DROP_FLOOR_HELD
        )
        assert spoken == [] and state["records"]

    def test_a_raising_speak_is_recorded_and_returned(self) -> None:
        def speak(sentence: str) -> None:
            raise OSError("synthetic device fault")

        seam, _, state = _unprompted(speak=speak)
        outcome = seam.offer(UnpromptedEvent(source="s", key="k", text="x. y."))
        assert outcome.spoken is False
        assert [c for c, _ in state["records"]] == [RECALL_EVENTS_SPEAK_FAILED]

    def test_the_source_label_is_restricted(self) -> None:
        seam, _, _ = _unprompted()
        seam.offer(UnpromptedEvent(source="ג'וב <b>", key="k", text="x."))
        allowed = set(LABEL_CHARSET) | {LABEL_PLACEHOLDER}
        sources = list(seam.status()["by_source"])
        assert len(sources) == 1 and set(sources[0]) <= allowed


# ── degrade, never raise (C3) ────────────────────────────────────────────


class TestNeverRaises:
    def test_a_raising_speak_ends_the_reply_and_is_recorded(self) -> None:
        h = Harness()

        def speak(sentence: str) -> None:
            raise OSError("synthetic")

        h.app._speak = speak  # type: ignore[attr-defined]
        outcome = h.app.run_turn("q")
        assert outcome.spoken == ()
        assert RECALL_EVENTS_SPEAK_FAILED in [c for c, _ in h.records]

    def test_a_raising_stop_playback_is_recorded_and_the_restart_proceeds(self) -> None:
        h = Harness(contradicts=lambda m, s: True)

        def stop() -> None:
            raise OSError("synthetic")

        h.app._stop_playback = stop  # type: ignore[attr-defined]
        h.on_speak[1] = h.spawn.land
        h.app.run_turn("q")
        assert RECALL_EVENTS_SEAM_FAILED in [c for c, _ in h.records]
        assert h.spoken[-1] == "Here is the right answer."

    def test_a_raising_clock_is_recorded_and_late_arrivals_drop(self) -> None:
        h = Harness()
        h.app.run_turn("q")

        def clock() -> float:
            raise RuntimeError("synthetic")

        h.app._clock = clock  # type: ignore[attr-defined]
        h.spawn.land()
        _assert_exactly_one(h, LANDED_DROPPED)
        assert RECALL_EVENTS_SEAM_FAILED in [c for c, _ in h.records]

    def test_a_raising_spawn_drops_the_recall_and_still_replies(self) -> None:
        h = Harness()

        def spawn(job: Callable[[], None]) -> None:
            raise RuntimeError("synthetic: can't start new thread")

        h.app._spawn = spawn  # type: ignore[attr-defined]
        outcome = h.app.run_turn("q")
        assert outcome.spoken == ("First one.", "First two.", "First three.")
        _assert_exactly_one(h, LANDED_DROPPED)
        assert h.dropped_reasons() == {DROP_RECALL_FAILED: 1}

    @pytest.mark.parametrize("query", [None, 7, b"bytes"])
    def test_a_non_text_query_still_replies(self, query: Any) -> None:
        h = Harness()
        outcome = h.app.run_turn(query)
        assert outcome.spoken
        assert h.requests[0].query == ""

    def test_run_turn_after_close_says_nothing_and_records(self) -> None:
        h = Harness()
        h.app.close()
        outcome = h.app.run_turn("q")
        assert outcome.spoken == () and h.log == []
        assert h.records

    def test_a_raising_record_sink_is_counted_not_raised(self) -> None:
        def sink(code: str, reason: str) -> None:
            raise RuntimeError("synthetic sink fault")

        h = Harness()
        h.app._record_sink = sink  # type: ignore[attr-defined]
        h.app._unprompted._record_sink = sink  # type: ignore[attr-defined]
        h.texts[PURPOSE_REPLY] = RuntimeError("synthetic")
        h.app.run_turn("q")
        status = h.app.status()
        assert status["record_sink_failures"] == 1
        assert status["degradations"] == {RECALL_EVENTS_COMPLETE_FAILED: 1}
        seam, _, _ = _unprompted(record=sink, floor_held=lambda: 1 / 0)
        seam.offer(UnpromptedEvent(source="s", key="k", text="x."))
        assert seam.status()["record_sink_failures"] == 1


# ── the real threaded spawner, bounded ───────────────────────────────────


class TestThreaded:
    def test_the_default_spawner_runs_recall_off_the_turn_and_wait_is_bounded(self) -> None:
        release = threading.Event()
        spoken: list[str] = []
        clock = FakeClock()

        def recall(query: str) -> Recalled:
            release.wait(timeout=5.0)
            return Recalled(rendered=MEMORY, relevance=0.9)

        def complete(request: CompletionRequest) -> str:
            return FOLLOW_UP if request.purpose == PURPOSE_FOLLOW_UP else "Hello there."

        app = BackgroundRecall(
            recall=recall,
            complete=complete,
            speak=spoken.append,
            stop_playback=lambda: None,
            floor_held=lambda: False,
            clock=clock,
            config=RecallEventsConfig(follow_up_window_s=WINDOW_S, follow_up_threshold=0.5),
        )
        app.run_turn("q")
        assert spoken == ["Hello there."]  # the turn did not wait on recall
        assert app.wait(timeout=0.01) is False  # still pending, bounded
        release.set()
        assert app.wait(timeout=5.0) is True
        assert spoken == ["Hello there.", FOLLOW_UP]
        assert app.status()["arrivals"][LANDED_FOLLOW_UP] == 1


# ── no new dependency ────────────────────────────────────────────────────


def test_the_module_imports_only_the_stdlib_and_embodiment() -> None:
    source = Path(re_mod.__file__).read_text(encoding="utf-8")
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
    foreign = {r for r in roots if r != "embodiment" and r not in sys.stdlib_module_names}
    assert foreign == set()
