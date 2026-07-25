"""The scratchpad's job is to survive the mind that wrote it.

These pin the properties that make a reset recoverable. The load-bearing one is
``open_intent``: because an intent is written *before* the act it describes, a
pad ending on an unanswered intent tells a successor both what was meant and
that it had not happened. A journal written after the fact cannot express that —
the entry that matters is the one that never got written.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed argv, no shell, real-process reset test
import sys
import textwrap

import pytest

from embodiment.scratchpad import (
    KINDS,
    PROTOCOL,
    RESUME_PROTOCOL,
    SCRATCHPAD_TOOLS,
    Scratchpad,
    render,
    resume_report,
    structure,
)


def _pad(tmp_path):
    return Scratchpad.load(tmp_path / "pad.jsonl")


class TestIntentBeforeAction:
    """The ordering rule that makes a reset survivable."""

    def test_an_unanswered_intent_is_visible(self, tmp_path):
        pad = _pad(tmp_path)
        pad.execute("intend", {"text": "Check n=5 before trusting the recurrence."})
        assert pad.open_intent is not None
        assert pad.open_intent.id == "n1"

    def test_an_observation_closes_it(self, tmp_path):
        pad = _pad(tmp_path)
        pad.execute("intend", {"text": "Check n=5."})
        pad.execute("observe", {"text": "n=5 gives 7 even of 13 — matches."})
        assert pad.open_intent is None

    def test_only_the_most_recent_intent_counts(self, tmp_path):
        pad = _pad(tmp_path)
        pad.execute("intend", {"text": "First."})
        pad.execute("observe", {"text": "Done."})
        pad.execute("intend", {"text": "Second."})
        assert pad.open_intent.text == "Second."

    def test_render_marks_where_it_was_interrupted(self, tmp_path):
        pad = _pad(tmp_path)
        pad.execute("intend", {"text": "Verify the base case."})
        assert "interrupted here" in render(pad)

    def test_a_completed_pad_is_not_marked(self, tmp_path):
        pad = _pad(tmp_path)
        pad.execute("intend", {"text": "Verify."})
        pad.execute("observe", {"text": "Verified."})
        assert "interrupted" not in render(pad)


class TestSurvivesTheProcess:
    """A working memory that dies with its process protects against nothing."""

    def test_a_blank_mind_recovers_the_open_intent(self, tmp_path):
        path = tmp_path / "pad.jsonl"
        first = Scratchpad.load(path)
        first.execute("intend", {"text": "Split by parity and recurse."})
        first.execute("observe", {"text": "n=3 gives 3 of 5."})
        first.execute("intend", {"text": "Check n=5 before trusting it."})
        del first

        resumed = Scratchpad.load(path)
        assert [e.kind for e in resumed.entries] == ["intend", "observe", "intend"]
        assert resumed.open_intent.text == "Check n=5 before trusting it."

    def test_it_survives_a_real_process_boundary(self, tmp_path):
        """Not two objects in one interpreter — two actual processes."""
        path = tmp_path / "pad.jsonl"
        writer = textwrap.dedent(f"""
            from embodiment.scratchpad import Scratchpad
            pad = Scratchpad.load({str(path)!r})
            pad.execute("intend", {{"text": "Enumerate the orders."}})
            """)
        subprocess.run(  # nosec B603 - fixed argv, shell=False
            [sys.executable, "-c", writer], check=True, capture_output=True
        )
        resumed = Scratchpad.load(path)
        assert resumed.open_intent is not None
        assert resumed.open_intent.text == "Enumerate the orders."

    def test_a_torn_final_write_is_survivable(self, tmp_path):
        """A process killed mid-write must not make the whole memory unreadable."""
        path = tmp_path / "pad.jsonl"
        pad = Scratchpad.load(path)
        pad.execute("intend", {"text": "Good entry."})
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"id": "n2", "kind": "obs')  # killed mid-write

        resumed = Scratchpad.load(path)
        assert len(resumed.entries) == 1
        assert resumed.open_intent.text == "Good entry."

    def test_the_answer_survives_too(self, tmp_path):
        path = tmp_path / "pad.jsonl"
        pad = Scratchpad.load(path)
        pad.execute("intend", {"text": "Compute it."})
        pad.execute("finish", {"answer": "76"})
        assert Scratchpad.load(path).answer == "76"

    def test_an_absent_file_is_a_fresh_mind(self, tmp_path):
        pad = Scratchpad.load(tmp_path / "never-written.jsonl")
        assert pad.entries == []
        assert pad.open_intent is None


class TestRevision:
    def test_a_revision_points_at_what_it_corrects(self, tmp_path):
        pad = _pad(tmp_path)
        pad.execute("conclude", {"text": "The answer is 72."})
        pad.execute("revise", {"id": "n1", "text": "72 assumed symmetry; it is 76."})
        assert pad.entries[-1].revises == "n1"
        assert "revises n1" in render(pad)

    def test_revising_an_unknown_id_is_refused_and_recorded(self, tmp_path):
        pad = _pad(tmp_path)
        outcome = pad.execute("revise", {"id": "n9", "text": "..."})
        assert "no entry" in outcome.result
        assert pad.rejected


class TestSurface:
    @pytest.mark.parametrize("kind", ("intend", "observe", "conclude"))
    def test_empty_text_is_refused(self, tmp_path, kind):
        pad = _pad(tmp_path)
        pad.execute(kind, {"text": "   "})
        assert pad.entries == []
        assert pad.rejected

    def test_unknown_tool_does_not_raise(self, tmp_path):
        assert "unknown tool" in _pad(tmp_path).execute("nope", {}).result

    def test_every_kind_has_a_tool(self):
        names = {t["function"]["name"] for t in SCRATCHPAD_TOOLS}
        assert {"intend", "observe", "conclude", "revise"} <= names
        assert set(KINDS) <= names | {"revise"}

    def test_the_protocol_states_the_ordering_rule(self):
        """The rule is the design; if the prompt stops saying it, it stops working."""
        assert "BEFORE" in PROTOCOL
        assert "interrupted" in PROTOCOL
        assert "no memory" in RESUME_PROTOCOL

    def test_structure_reports_facts_not_scores(self, tmp_path):
        pad = _pad(tmp_path)
        pad.execute("intend", {"text": "Do the thing."})
        report = structure(pad)
        assert report["open_intent"] == "n1"
        assert report["answered"] is False
        # Deliberately no quality score: counting entries measures nothing.
        assert not any("score" in key or "count" in key for key in report)

    def test_entries_are_persisted_as_they_are_written(self, tmp_path):
        """Not flushed at the end — a mind that dies mid-task keeps what it wrote."""
        path = tmp_path / "pad.jsonl"
        pad = Scratchpad.load(path)
        pad.execute("intend", {"text": "One."})
        lines = [json.loads(raw) for raw in path.read_text().splitlines() if raw.strip()]
        assert lines == [{"id": "n1", "kind": "intend", "text": "One."}]


class TestResumeReport:
    """resume_report renders scratchpad + degradations as one output."""

    def test_renders_open_intent_and_observations(self, tmp_path):
        pad = _pad(tmp_path)
        pad.execute("intend", {"text": "Check n=5."})
        pad.execute("observe", {"text": "n=5 gives 7 even of 13."})
        pad.execute("intend", {"text": "Verify n=7."})

        report = resume_report(pad)
        assert "SCRATCHPAD" in report
        assert "Check n=5" in report
        assert "n=5 gives 7 even of 13" in report
        assert "Verify n=7" in report

    def test_includes_degradations_when_present(self, tmp_path):
        from dataclasses import dataclass

        @dataclass
        class FakeDegradation:
            def to_dict(self):
                return {
                    "source": "loop",
                    "code": "budget-exceeded",
                    "reason": "ran out of steps",
                }

        pad = _pad(tmp_path)
        pad.execute("intend", {"text": "Compute the answer."})
        report = resume_report(pad, degradations=[FakeDegradation()])

        assert "DEGRADATIONS" in report
        assert "budget-exceeded" in report
        assert "ran out of steps" in report

    def test_empty_pad_still_renders(self, tmp_path):
        pad = _pad(tmp_path)
        report = resume_report(pad)
        assert "SCRATCHPAD" in report
        assert "(empty)" in report
