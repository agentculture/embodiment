"""Tests for ``examples/proof.py``'s delivery reporting (plan task t18, part B).

``proof.py`` produced the 2-of-7 delivery baseline in
``docs/live-test-results/proof.md`` while reporting only
``ThreadedMuseRunner.snapshot()["counts"]`` — so the published number could not
be asked the one question t3's kind-aware delivery exists to answer: *which kind
of counsel was discarded?* It also wrote no configuration preamble at all, and
its temperature was a literal inside ``gateway``.

These tests cover the reporting added to close that, and they pin the finding
that matters most about it: **a degradation code that never fires reports
nothing, not a zero.** A fold that emitted ``0`` for an unfired code would make
"did not happen this run" indistinguishable from a number somebody measured.

They also carry the guard that caught embodiment#18. Two of the runner's nine
codes — ``DROPPED_COMPILATION_STARVED`` and ``DROPPED_COUNSEL_DISPLACED`` — were
declared, exported and had **no producer anywhere in the package**, so a host
branching exhaustively over ``RUNNER_CODES`` got two arms nothing could reach.
Both now have production emit sites (``muse_runner``'s background-compilation
work class), and the pin below was inverted rather than deleted: it now asserts
that *every* code in ``RUNNER_CODES`` has a producer, which is the check that
would have failed the original merge.

**The lane under test is ARCHIVED, and this file was kept anyway** (task
``t15``, embodiment#53, deviations ``d2``/``d3``). Two reasons, recorded so the
decision is not re-taken by whoever next tidies up. First, ``examples/proof.py``
produced a *published* live result — ``docs/live-test-results/proof.md``'s
2-of-7 delivery baseline — and these tests are what say that number was
measured the way the document claims; deleting them would leave a cited result
with nothing standing behind it. Second, the ``RUNNER_CODES``-has-a-producer
guard is the one that caught embodiment#18, and
``embodiment.muse_runner`` is still importable and still wireable by a host, so
the class of bug it catches has not gone away with the archival. The imports
below name ``embodiment.muse_runner`` explicitly rather than reaching it
through the package surface, because that surface no longer advertises it.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Optional

import pytest

import embodiment
import embodiment.muse_runner as muse_runner  # ARCHIVED lane, named explicitly (#53)
from embodiment.contract import ContextPacket, ModelResponse, Task, ToolCall
from embodiment.loop import ToolOutcome, run
from embodiment.muse import MuseDegradation
from embodiment.presence import UpdateCadence
from embodiment.presence_engine import MuseComment, PresenceEngine, PresenceIO
from examples import delivery_series, proof

#: ``{code: constant name}`` for the runner's whole declared vocabulary.
_CONSTANT_NAMES = {
    getattr(muse_runner, name): name
    for name in muse_runner.__all__
    if name.startswith(("DEGRADED_", "DROPPED_"))
}


def _record_call_arguments() -> dict[str, list[str]]:
    """``{identifier: [where]}`` for every name passed to ``_record``/``_degrade``.

    A real AST walk over the package, not a text scan: only a **call argument**
    counts, so a constant's declaration, its ``__all__`` entry and its place in
    the ``RUNNER_CODES`` tuple are never mistaken for a producer. That
    distinction is the whole point — embodiment#18 was two codes that appeared
    in all three of those places and in no call site.
    """
    root = Path(embodiment.__file__).resolve().parent
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called not in {"_record", "_degrade"}:
                continue
            for argument in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(argument, ast.Name):
                    identifier = argument.id
                elif isinstance(argument, ast.Attribute):
                    identifier = argument.attr
                else:
                    continue
                found.setdefault(identifier, []).append(f"{path.name}:{node.lineno}")
    return found


class TestFoldCodes:
    """The ledger folded by code, with absence preserved."""

    def test_no_muse_state_folds_to_nothing(self) -> None:
        assert proof._fold_codes(None) == {}

    def test_a_run_with_no_degradations_folds_to_nothing(self) -> None:
        assert proof._fold_codes({"degradations": []}) == {}

    def test_codes_are_tallied_by_name(self) -> None:
        state: dict[str, Any] = {
            "degradations": [
                MuseDegradation(code=muse_runner.DROPPED_STALE, reason="a"),
                MuseDegradation(code=muse_runner.DROPPED_STALE, reason="b"),
                MuseDegradation(code=muse_runner.DROPPED_LATE, reason="c"),
            ]
        }
        assert proof._fold_codes(state) == {
            muse_runner.DROPPED_STALE: 2,
            muse_runner.DROPPED_LATE: 1,
        }

    def test_a_code_that_never_fired_is_absent_rather_than_zero(self) -> None:
        """Absence and zero are different claims, and only one of them is true.

        A code that did not fire in a run must not appear in the fold at all.
        Reporting ``0`` would read as a measurement of that lane when nothing
        measured it — and, before embodiment#18 was fixed, would have read as
        "this run did not trip it" for two codes nothing could trip.
        """
        folded = proof._fold_codes(
            {"degradations": [MuseDegradation(code=muse_runner.DROPPED_STALE, reason="a")]}
        )
        assert muse_runner.DROPPED_COMPILATION_STARVED not in folded
        assert muse_runner.DROPPED_COUNSEL_DISPLACED not in folded


class TestEveryRunnerCodeHasAProducer:
    """Declared vocabulary must be REACHABLE vocabulary (embodiment#18).

    This class was ``TestUnproducedCodes``. It asserted that
    ``DROPPED_COMPILATION_STARVED`` and ``DROPPED_COUNSEL_DISPLACED`` were never
    passed to ``_record`` / ``_degrade``, so that wiring a producer for either
    would break it and force the results documents to be corrected rather than
    left to go stale. A producer was wired (``muse_runner``'s background
    compilation work class), it broke, and this is that correction — inverted
    into the stronger claim, which is the one that would have failed the merge
    that created the defect: **every** code in ``RUNNER_CODES`` has at least one
    call site.

    Reachability is not the same as coverage. ``tests/test_ledger.py``'s
    PROVOKERS table already required every code to appear in *a record*; both
    dead codes satisfied it by being recorded directly in a test. This checks
    the other half — that production code passes the constant somewhere — and
    task t3 closes the remaining gap by banning provokers that reach a private
    attribute.
    """

    ISSUE_18_CODES = (
        muse_runner.DROPPED_COMPILATION_STARVED,
        muse_runner.DROPPED_COUNSEL_DISPLACED,
    )

    @pytest.mark.parametrize("code", muse_runner.RUNNER_CODES)
    def test_the_code_is_part_of_the_declared_vocabulary(self, code: str) -> None:
        assert code in _CONSTANT_NAMES
        assert code in muse_runner.RUNNER_CODES

    @pytest.mark.parametrize("code", muse_runner.RUNNER_CODES)
    def test_some_module_in_the_package_records_it(self, code: str) -> None:
        """Something in ``embodiment`` passes the constant to ``_record``/``_degrade``."""
        name = _CONSTANT_NAMES[code]
        producers = _record_call_arguments().get(name, [])
        assert producers, (
            f"{name} is declared and exported but nothing in the package records it — "
            "a host branching exhaustively over RUNNER_CODES would get an arm that "
            "cannot fire. Wire an emit site, or drop the constant (embodiment#18)."
        )

    @pytest.mark.parametrize("code", ISSUE_18_CODES)
    def test_the_two_issue_18_codes_are_produced_by_the_runner(self, code: str) -> None:
        """The regression pin, named so a reader can find the history."""
        where = _record_call_arguments()[_CONSTANT_NAMES[code]]
        assert any(location.startswith("muse_runner.py:") for location in where), where


class TestPerKindAttribution:
    """Only ``drain`` attributes a kind. The other drop paths cannot.

    Declared in the pre-registration before the run, so that the gap in the
    per-kind report reads as a known limit of the instrument rather than as an
    excuse constructed afterwards.
    """

    def test_stale_drops_and_deliveries_carry_a_kind(self) -> None:
        source = __import__("inspect").getsource(muse_runner.ThreadedMuseRunner.drain)
        assert "_kind_dropped" in source
        assert "_kind_delivered" in source

    def test_late_and_overflow_drops_do_not(self) -> None:
        inspect = __import__("inspect")
        late = inspect.getsource(muse_runner.ThreadedMuseRunner._drop_late)
        deliver = inspect.getsource(muse_runner.ThreadedMuseRunner._deliver)
        assert "_kind_dropped" not in late
        assert "_kind_dropped" not in deliver


class TestGuidanceReachesTheCortex:
    """Issue #23, part two: counsel the pump delivers must reach the cortex.

    No checked-in host wired ``append_guidance`` — this harness included — so
    every muse comment landed on ``PresenceEngine``'s ``_noop_guidance``. The
    published delivery numbers measured delivery to *presence*; nothing measured
    whether the acting mind ever saw a word of it.
    """

    @staticmethod
    def _relay(*responses: Any) -> tuple[proof.GuidanceRelay, list[list[dict[str, Any]]]]:
        """A relay over a scripted gateway that records what each turn was sent."""
        seen: list[list[dict[str, Any]]] = []
        scripted = list(responses)

        def complete(messages: list[dict[str, Any]]) -> Any:
            seen.append([dict(m) for m in messages])
            return scripted.pop(0) if scripted else None

        return proof.GuidanceRelay(complete), seen

    def test_counsel_is_buffered_rather_than_appended_where_it_arrives(self) -> None:
        """A boundary fires INSIDE a turn; appending there would split it."""
        relay, _ = self._relay(None)
        messages = [{"role": "user", "content": "prove it"}]
        relay.append_guidance("you never checked n=0")
        assert messages == [{"role": "user", "content": "prove it"}]
        assert relay.pending == ["you never checked n=0"]

    def test_the_next_completion_carries_it_labelled_as_advisory(self) -> None:
        relay, seen = self._relay(None)
        messages = [{"role": "user", "content": "prove it"}]
        relay.append_guidance("you never checked n=0")
        relay.complete(messages)
        assert seen[-1][-1] == {
            "role": "user",
            "content": f"{proof.GUIDANCE_PREFIX}you never checked n=0",
        }
        assert relay.reached_cortex == ["you never checked n=0"]
        assert relay.pending == []

    def test_several_lines_flush_in_the_order_they_arrived(self) -> None:
        relay, seen = self._relay(None)
        for line in ("first", "second", "third"):
            relay.append_guidance(line)
        relay.complete([])
        assert [m["content"] for m in seen[-1]] == [
            f"{proof.GUIDANCE_PREFIX}{line}" for line in ("first", "second", "third")
        ]

    def test_blank_counsel_is_never_appended(self) -> None:
        relay, seen = self._relay(None)
        relay.append_guidance("")
        relay.append_guidance("   ")
        relay.complete([])
        assert (relay.appended, seen[-1]) == ([], [])

    def test_counsel_after_the_last_turn_is_reported_undelivered(self) -> None:
        """The clean-finish case, stated rather than smoothed over.

        The drive-end terminal drain fires on every exit — but a drive that
        finished cleanly has no later completion, so its counsel reaches the
        operator and the record and no cortex turn. Reporting it as delivered
        would repeat the mistake issue #23 exists to fix.
        """
        relay, _ = self._relay(None)
        relay.append_guidance("early counsel")
        relay.complete([])
        relay.append_guidance("counsel from the terminal drain")
        assert relay.reached_cortex == ["early counsel"]
        assert relay.pending == ["counsel from the terminal drain"]

    def test_the_harness_wires_the_relay_into_the_presence_io(self) -> None:
        """An AST check, because this is exactly the wiring that was missing."""
        source = Path(proof.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        wired = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "PresenceIO"
            and any(kw.arg == "append_guidance" for kw in node.keywords)
        ]
        assert len(wired) == 1

    def test_the_drive_is_handed_the_relay_rather_than_the_gateway(self) -> None:
        """Wiring the IO is only half of it; the loop must call THROUGH it."""
        main_source = __import__("inspect").getsource(proof.main)
        assert "cortex = relay.complete" in main_source
        assert "relay = GuidanceRelay(" in main_source

    def test_the_report_separates_reaching_the_cortex_from_being_handed_over(
        self,
    ) -> None:
        main_source = __import__("inspect").getsource(proof.main)
        for field in ("guidance_appended", "guidance_reached_cortex", "guidance_undelivered"):
            assert f'"{field}"' in main_source, field


class _OneBeatLateMuse:
    """Counsel ready one drain after the boundary that prompted it."""

    def __init__(self) -> None:
        self.considered: list[Any] = []
        self._in_flight: list[MuseComment] = []
        self._ready: list[MuseComment] = []

    def consider(self, boundary: Any) -> None:
        self.considered.append(boundary)
        self._in_flight.append(
            MuseComment(guidance=f"counsel on {boundary.kind}@{boundary.step_count}")
        )

    def drain(self, *, step_count: int = 0) -> list[MuseComment]:
        ready, self._ready = self._ready, self._in_flight
        self._in_flight = []
        return ready

    def degradation(self) -> Optional[str]:
        return None


class _Bench:
    """A two-tool stand-in for ``ProofBench``: read, then finish on request."""

    def __init__(self, *, finishes: bool) -> None:
        self._finishes = finishes
        self.calls: list[str] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="(n+1)! - 1")
        return ToolOutcome(result="S(3) = 23")

    def state(self) -> str:
        return f"step {len(self.calls)}"

    def script(self, messages: list[dict[str, Any]]) -> ModelResponse:
        turn = len(self.calls) + 1
        if self._finishes and turn >= 2:
            return ModelResponse(tool_calls=[ToolCall(id=f"c{turn}", name="finish", arguments={})])
        return ModelResponse(
            tool_calls=[ToolCall(id=f"c{turn}", name="sum_terms", arguments={"n": 3})]
        )


def _wired_drive(*, finishes: bool, max_steps: int) -> tuple[proof.GuidanceRelay, Any, list[str]]:
    """Drive the real loop with the harness's OWN wiring: relay + pump + muse."""
    bench = _Bench(finishes=finishes)
    relay = proof.GuidanceRelay(bench.script)
    lines: list[str] = []
    engine = PresenceEngine(
        io=PresenceIO(
            render=lines.append,
            task_state=bench.state,
            append_guidance=relay.append_guidance,
        ),
        muse=_OneBeatLateMuse(),
        cadence=UpdateCadence(every_steps=1),
    )
    outcome = run(
        relay.complete,
        Task(
            id="proof-factorial",
            repo_path="",
            instruction="prove it",
            context_packet=ContextPacket(original="prove it", ack="on it"),
        ),
        executor=bench,
        max_steps=max_steps,
        presence=engine,
    )
    return relay, outcome, lines


class TestTheWiredHarnessDeliversToTheCortex:
    """Both halves of issue #23 together, through the shipped wiring.

    The loop half (the drive-end terminal drain) is proved in
    ``tests/test_presence_engine.py``. This is the host half: with
    ``append_guidance`` wired, does counsel actually arrive on the wire?
    """

    def test_a_budget_drive_carries_the_terminal_counsel_into_the_synthesis_turn(
        self,
    ) -> None:
        relay, outcome, _ = _wired_drive(finishes=False, max_steps=3)
        assert outcome.exit_reason == "budget"
        # The drive-end drain fires before the forced synthesis turn, so its
        # counsel is on the wire for the one turn that can still use it.
        assert relay.reached_cortex[-1] == relay.appended[-1]
        assert relay.pending == []

    def test_a_clean_finish_delivers_what_it_can_and_reports_the_rest(self) -> None:
        relay, outcome, lines = _wired_drive(finishes=True, max_steps=4)
        assert outcome.exit_reason == "finished"
        # Counsel from earlier boundaries reached the cortex. Everything handed
        # over after the drive's last completion — the final step boundary's
        # counsel AND the drive-end drain's — is reported pending rather than
        # counted as delivered. A clean finish has no later turn to carry it,
        # and saying so is the point: the relay measures arrival, not handover.
        assert relay.reached_cortex
        assert relay.pending[-1] == relay.appended[-1]
        # The accounting closes: nothing is counted twice, nothing vanishes.
        assert len(relay.appended) == len(relay.reached_cortex) + len(relay.pending)
        assert lines  # and the operator heard the pump throughout


class TestProofConfigConstants:
    """The settings the baseline left as literals are now named and recordable."""

    def test_the_temperature_is_a_named_constant(self) -> None:
        assert proof.DEFAULT_TEMPERATURE == 0.3

    def test_the_muse_controls_are_named(self) -> None:
        assert proof.MUSE_MAX_TURNS == 2
        assert proof.MUSE_MAX_TOKENS == 1200

    def test_the_staleness_threshold_is_importable_for_the_record(self) -> None:
        assert proof.DEFAULT_STALE_LAG == 5

    def test_the_gateway_sends_the_temperature_it_was_given(self) -> None:
        """It used to accept one and send a hardcoded 0.3."""
        import inspect

        source = inspect.getsource(proof.gateway)
        assert '"temperature": temperature' in source
        assert '"temperature": 0.3' not in source


class TestDeliveryFold:
    """The per-kind fold across runs (``examples/delivery_series.py``)."""

    @staticmethod
    def _report(**counts: int) -> dict[str, Any]:
        base = {
            "insights_delivered": 0,
            "insights_dropped_stale": 0,
            "insights_dropped_late": 0,
            "insights_dropped_overflow": 0,
        }
        base.update(counts)
        return {"muse_counts": base}

    def test_it_reproduces_the_published_baseline_exactly(self) -> None:
        """2 of 7 — the number in proof.md. If the fold cannot restate the
        baseline, it cannot be trusted to restate its successor."""
        folded = delivery_series.fold(
            [
                self._report(
                    insights_delivered=2,
                    insights_dropped_stale=2,
                    insights_dropped_late=3,
                )
            ]
        )
        assert folded["insights_produced"] == 7
        assert folded["delivery_fraction"] == pytest.approx(0.2857, abs=1e-4)
        assert folded["baseline"] == delivery_series.BASELINE_COUNTS

    def test_late_and_overflow_drops_are_reported_unattributed(self) -> None:
        """They cannot carry a kind, so the fold must not pretend they do."""
        folded = delivery_series.fold(
            [
                {
                    "muse_counts": {
                        "insights_delivered": 1,
                        "insights_dropped_stale": 0,
                        "insights_dropped_late": 2,
                        "insights_dropped_overflow": 1,
                    },
                    "muse_kind_delivered": {"step": 1},
                }
            ]
        )
        assert folded["unattributable_drops"] == {"late": 2, "overflow": 1}
        assert folded["kind_delivered"] == {"step": 1}
        assert folded["kind_dropped_stale"] == {}

    def test_counts_sum_across_runs(self) -> None:
        folded = delivery_series.fold(
            [
                self._report(insights_delivered=3, insights_dropped_late=1),
                self._report(
                    insights_delivered=1, insights_dropped_stale=2, insights_dropped_late=1
                ),
            ]
        )
        assert folded["runs"] == 2
        assert folded["counts"]["insights_delivered"] == 4
        assert folded["insights_produced"] == 8

    def test_a_failed_run_is_counted_and_excluded_from_the_totals(self) -> None:
        folded = delivery_series.fold(
            [self._report(insights_delivered=2), {"harness_error": "boom"}]
        )
        assert folded["runs"] == 2
        assert folded["harness_errors"] == 1
        assert folded["counts"]["insights_delivered"] == 2

    def test_no_insights_reports_no_fraction_rather_than_zero(self) -> None:
        """A fraction with an empty denominator is not zero, it is absent."""
        assert delivery_series.fold([self._report()])["delivery_fraction"] is None

    def test_an_unfired_code_stays_absent_from_the_fold(self) -> None:
        folded = delivery_series.fold(
            [
                {
                    "muse_counts": {"insights_delivered": 1},
                    "muse_degradation_codes": {muse_runner.DROPPED_STALE: 1},
                }
            ]
        )
        assert folded["degradation_codes"] == {muse_runner.DROPPED_STALE: 1}
        assert muse_runner.DROPPED_COUNSEL_DISPLACED not in folded["degradation_codes"]
