"""t28 — the commander harness, driven end to end through its real entry point.

Task t12 committed challenge harnesses that could not run at all, and nobody
noticed until t18 tried to use them: each built a ``Task``/``run`` call the
contract has no names for, so every invocation raised ``TypeError`` before its
first model call. The lesson is the acceptance bar here — this harness is
**driven end to end**, arms and all, not merely inspected for plausible
constants.

Every test is hermetic:

* **no model** — the scripted seams answer by rule (``examples/league_commander.py``'s
  ``scripted_seam``), so the delegation, the ledger and the fold all run with no
  network and no key;
* **no arena** — ``tests/fake_cleague.py`` answers the ``cmatch`` subset the
  harness calls, in league's own JSON shapes.

The one thing these tests cannot check is whether a real model plays well. That
is what the live series is for, and its rule is pre-registered in
``tests/test_league_commander_preregistration.py``.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import ROLE_SUBAGENT, ModelResponse, ToolCall, ToolError  # noqa: E402
from embodiment.subagent import attenuate  # noqa: E402
from examples import league_commander as lc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "examples" / "league_commander.py"
FAKE_CLEAGUE = REPO_ROOT / "tests" / "fake_cleague.py"
FAKE_BIN = f"{sys.executable} {FAKE_CLEAGUE}"

#: Every league verb the harness is allowed to reach the arena with. A call
#: outside this set means the host stopped using the public surface.
PUBLIC_VERBS = {
    ("team", "register"),
    ("cmatch", "new"),
    ("cmatch", "show"),
    ("cmatch", "act"),
    ("cmatch", "tick"),
    ("match", "score"),
}


def briefing(options: int = 3, unit_id: str = "blue-u1") -> dict[str, Any]:
    return {
        "game_time": 4,
        "you": {
            "unit_id": unit_id,
            "team_id": "blue",
            "role": "defender",
            "pos": {"x": 1, "y": 2},
            "carrying": 0,
        },
        "menu": [
            {"kind": "move", "target": f"p{i}", "duration": i + 1, "completion_time": 5 + i}
            for i in range(options)
        ],
        "outlook": [],
        "board": {"clock": 4},
    }


def play(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    root = tmp_path / "arena"
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    # Prove the hermetic path needs no key even on a machine that has one.
    env.pop(lc.API_KEY_ENV, None)
    return subprocess.run(  # nosec B603
        [
            sys.executable,
            str(HARNESS),
            "play",
            "--root",
            str(root),
            "--league",
            FAKE_BIN,
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def ledger(path: Path) -> list[dict[str, Any]]:
    return lc.load_matches(path)


# ── the wiring: one commander, one unit agent, a different model per level ───


class TestTheWiring:
    def test_the_commander_is_the_top_level_and_the_unit_is_its_child(self) -> None:
        calls: list[lc.CallRecord] = []
        context: dict[str, Any] = {}
        record = lc.decide(
            arm=lc.ARM_B,
            match_key="B-0",
            decision_index=0,
            briefing=briefing(),
            build_minds=lc.build_scripted_minds(lc.ARM_B, sink=calls.append, context=context),
            context=context,
        )
        assert record.hierarchical is True
        assert record.commander_model == lc.GEMMA
        assert record.unit_model == lc.QWEN
        assert record.spawn_outcomes == ["granted"]
        assert record.lineage_depth == 1
        assert record.ordered_index is not None

    def test_the_mirror_swaps_the_models_and_nothing_else(self) -> None:
        records = {}
        for arm in lc.HIERARCHICAL_ARMS:
            calls: list[lc.CallRecord] = []
            context: dict[str, Any] = {}
            records[arm] = lc.decide(
                arm=arm,
                match_key=f"{arm}-0",
                decision_index=0,
                briefing=briefing(),
                build_minds=lc.build_scripted_minds(arm, sink=calls.append, context=context),
                context=context,
            )
        b, c = records[lc.ARM_B], records[lc.ARM_C]
        assert (b.commander_model, b.unit_model) == (c.unit_model, c.commander_model)
        assert b.ordered_index == c.ordered_index
        assert b.spawn_outcomes == c.spawn_outcomes

    def test_the_spawn_allowance_is_strictly_positive_and_bounds_depth_at_one(self) -> None:
        calls: list[lc.CallRecord] = []
        context: dict[str, Any] = {}
        record = lc.decide(
            arm=lc.ARM_B,
            match_key="B-0",
            decision_index=0,
            briefing=briefing(),
            build_minds=lc.build_scripted_minds(lc.ARM_B, sink=calls.append, context=context),
            context=context,
        )
        assert lc.SPAWN_ALLOWANCE > 0
        # The child was minted with attenuate(1) == 0, so it can spawn nothing.
        assert attenuate(lc.SPAWN_ALLOWANCE) == 0
        assert record.spawn_allowance_remaining == 0

    def test_a_flat_arm_spawns_nothing_and_has_no_commander(self) -> None:
        calls: list[lc.CallRecord] = []
        context: dict[str, Any] = {}
        record = lc.decide(
            arm=lc.ARM_A_QWEN,
            match_key="A-qwen-0",
            decision_index=0,
            briefing=briefing(),
            build_minds=lc.build_scripted_minds(lc.ARM_A_QWEN, sink=calls.append, context=context),
            context=context,
        )
        assert record.hierarchical is False
        assert record.commander_model == ""
        assert record.spawn_outcomes == []
        assert record.proposed_index is None
        assert record.ordered_index is not None

    def test_the_child_is_typed_as_a_subagent_not_a_cortex(self) -> None:
        executor = lc.CommanderExecutor(briefing=briefing(), decision_id="d", unit_model=lc.QWEN)
        outcome = executor.execute("consult_unit", {"question": "?"})
        assert outcome.spawn is not None
        assert outcome.spawn.role == ROLE_SUBAGENT
        assert outcome.spawn.max_steps == lc.UNIT_MAX_STEPS
        assert outcome.spawn.model == lc.QWEN

    def test_with_no_identity_the_framing_is_a_byte_identical_no_op(self) -> None:
        # colleague#352's acceptance criterion, inherited: absent identity means
        # today's prompts survive unchanged, byte for byte.
        assert lc.frame_cortex(lc.COMMANDER_SYSTEM, identity=None, muse=False) == (
            lc.COMMANDER_SYSTEM
        )
        assert lc.frame_subagent(lc.UNIT_SYSTEM, identity=None) == lc.UNIT_SYSTEM


# ── authority: only the commander reaches the arena ─────────────────────────


class TestAuthority:
    def test_a_unit_agent_has_no_way_to_order(self) -> None:
        executor = lc.UnitExecutor(3)
        with pytest.raises(Exception) as excinfo:
            executor.execute("order", {"menu_index": 0})
        assert "not available to a unit agent" in str(excinfo.value)

    def test_a_commander_cannot_propose(self) -> None:
        executor = lc.CommanderExecutor(briefing=briefing(), decision_id="d", unit_model=lc.QWEN)
        with pytest.raises(Exception) as excinfo:
            executor.execute("propose", {"menu_index": 0})
        assert "not available to the commander" in str(excinfo.value)

    def test_a_flat_arm_cannot_delegate(self) -> None:
        executor = lc.FlatExecutor(3)
        with pytest.raises(Exception) as excinfo:
            executor.execute("consult_unit", {"question": "?"})
        assert "not available in the flat arm" in str(excinfo.value)

    def test_an_out_of_range_index_is_a_refusal_not_a_success(self) -> None:
        # Returning a ToolOutcome carrying an error string would be a *success*
        # the loop records as fine; ToolError is one self-correcting step.
        executor = lc.FlatExecutor(2)
        with pytest.raises(ToolError):
            executor.execute("order", {"menu_index": 7})
        assert executor.invalid_orders == 1
        assert executor.ordered_index is None

    def test_a_non_integer_index_is_a_refusal(self) -> None:
        with pytest.raises(ToolError):
            lc._menu_index({"menu_index": "left"}, 3)


# ── the commander is not a rubber stamp ─────────────────────────────────────


class TestOverride:
    def test_an_override_is_recorded_when_the_commander_disagrees(self) -> None:
        record = lc.DecisionRecord(
            arm=lc.ARM_B,
            match_key="B-0",
            decision_index=0,
            unit_id="u",
            role="defender",
            game_time=0,
            options=3,
            hierarchical=True,
            proposed_index=0,
            ordered_index=2,
        )
        lc._grade_override(record)
        assert record.overridden is True

    def test_agreement_is_recorded_as_agreement_not_as_absence(self) -> None:
        record = lc.DecisionRecord(
            arm=lc.ARM_B,
            match_key="B-0",
            decision_index=0,
            unit_id="u",
            role="defender",
            game_time=0,
            options=3,
            hierarchical=True,
            proposed_index=1,
            ordered_index=1,
        )
        lc._grade_override(record)
        assert record.overridden is False

    def test_a_missing_proposal_is_not_scored_either_way(self) -> None:
        record = lc.DecisionRecord(
            arm=lc.ARM_B,
            match_key="B-0",
            decision_index=0,
            unit_id="u",
            role="defender",
            game_time=0,
            options=3,
            hierarchical=True,
            proposed_index=None,
            ordered_index=1,
        )
        lc._grade_override(record)
        assert record.overridden is None


# ── the instrument: finish_reason on every call, retries recorded ───────────


class TestTheInstrument:
    def _seam(self, payload: dict[str, Any], sink: list[lc.CallRecord]) -> Any:
        import urllib.request

        class _Response:
            def __enter__(self_inner):  # noqa: N805
                return self_inner

            def __exit__(self_inner, *_a):  # noqa: N805
                return False

            def read(self_inner):  # noqa: N805
                return json.dumps(payload).encode("utf-8")

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _Response()  # type: ignore[assignment]
        try:
            seam = lc.gateway_seam(
                "http://example.invalid/v1",
                lc.QWEN,
                "key",
                tools=lc.FLAT_TOOLS,
                sink=sink.append,
                context={"level": lc.LEVEL_FLAT, "arm": lc.ARM_A_QWEN},
            )
            return seam([{"role": "user", "content": "hi"}])
        finally:
            urllib.request.urlopen = original

    def test_a_length_finish_is_recorded_not_hidden(self) -> None:
        sink: list[lc.CallRecord] = []
        payload = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": None, "reasoning_content": "x" * 12857},
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 16000},
        }
        response = self._seam(payload, sink)
        assert isinstance(response, ModelResponse)
        assert sink[0].finish_reason == "length"
        assert sink[0].reasoning_chars == 12857
        assert sink[0].content_chars == 0
        assert sink[0].completion_tokens == 16000

    def test_every_call_carries_its_level_and_model(self) -> None:
        sink: list[lc.CallRecord] = []
        payload = {
            "choices": [{"finish_reason": "tool_calls", "message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 6},
        }
        self._seam(payload, sink)
        assert sink[0].level == lc.LEVEL_FLAT
        assert sink[0].model == lc.QWEN
        assert sink[0].finish_reason == "tool_calls"

    def test_a_transport_failure_is_retried_and_every_retry_is_recorded(self) -> None:
        import urllib.error
        import urllib.request

        attempts = {"n": 0}

        class _Response:
            def __enter__(self_inner):  # noqa: N805
                return self_inner

            def __exit__(self_inner, *_a):  # noqa: N805
                return False

            def read(self_inner):  # noqa: N805
                return json.dumps(
                    {
                        "choices": [{"finish_reason": "stop", "message": {"content": "late"}}],
                        "usage": {},
                    }
                ).encode("utf-8")

        def flaky(*_a: Any, **_k: Any) -> Any:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise urllib.error.URLError("timed out")
            return _Response()

        original = urllib.request.urlopen
        urllib.request.urlopen = flaky  # type: ignore[assignment]
        sink: list[lc.CallRecord] = []
        try:
            seam = lc.gateway_seam(
                "http://example.invalid/v1",
                lc.GEMMA,
                "key",
                tools=[],
                sink=sink.append,
                context={"level": lc.LEVEL_COMMANDER},
                sleep=lambda _s: None,
            )
            seam([{"role": "user", "content": "hi"}])
        finally:
            urllib.request.urlopen = original
        assert attempts["n"] == 3
        assert sink[0].retries == 2
        assert len(sink[0].retry_reasons) == 2
        assert sink[0].finish_reason == "stop"

    def test_a_call_that_never_succeeds_is_still_recorded_before_it_raises(self) -> None:
        import urllib.error
        import urllib.request

        def always_fail(*_a: Any, **_k: Any) -> Any:
            raise urllib.error.URLError("gateway down")

        original = urllib.request.urlopen
        urllib.request.urlopen = always_fail  # type: ignore[assignment]
        sink: list[lc.CallRecord] = []
        try:
            seam = lc.gateway_seam(
                "http://example.invalid/v1",
                lc.QWEN,
                "key",
                tools=[],
                sink=sink.append,
                context={"level": lc.LEVEL_UNIT},
                sleep=lambda _s: None,
            )
            with pytest.raises(urllib.error.URLError):
                seam([{"role": "user", "content": "hi"}])
        finally:
            urllib.request.urlopen = original
        assert sink and sink[0].error
        assert sink[0].retries == lc.MAX_RETRIES + 1

    def test_the_tool_schema_never_carries_a_tool_choice(self) -> None:
        # `tool_choice` is broken on the reference rig, so it is never sent.
        assert "tool_choice" not in json.dumps(lc.COMMANDER_TOOLS)
        assert "tool_choice" not in json.dumps(lc.UNIT_TOOLS)

    def test_a_model_answer_is_never_retried_for_a_better_one(self) -> None:
        import urllib.request

        calls = {"n": 0}

        class _Response:
            def __enter__(self_inner):  # noqa: N805
                return self_inner

            def __exit__(self_inner, *_a):  # noqa: N805
                return False

            def read(self_inner):  # noqa: N805
                calls["n"] += 1
                return json.dumps(
                    {"choices": [{"finish_reason": "length", "message": {}}], "usage": {}}
                ).encode("utf-8")

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _Response()  # type: ignore[assignment]
        sink: list[lc.CallRecord] = []
        try:
            seam = lc.gateway_seam(
                "http://example.invalid/v1",
                lc.QWEN,
                "key",
                tools=[],
                sink=sink.append,
                context={},
                sleep=lambda _s: None,
            )
            seam([{"role": "user", "content": "hi"}])
        finally:
            urllib.request.urlopen = original
        assert calls["n"] == 1
        assert sink[0].retries == 0


# ── the transcript ──────────────────────────────────────────────────────────


class TestTranscripts:
    def test_a_transcript_carries_the_messages_and_the_response(self, tmp_path: Path) -> None:
        import urllib.request

        class _Response:
            def __enter__(self_inner):  # noqa: N805
                return self_inner

            def __exit__(self_inner, *_a):  # noqa: N805
                return False

            def read(self_inner):  # noqa: N805
                return json.dumps(
                    {
                        "choices": [
                            {
                                "finish_reason": "tool_calls",
                                "message": {
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "c1",
                                            "function": {
                                                "name": "order",
                                                "arguments": '{"menu_index": 1}',
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                    }
                ).encode("utf-8")

        written: list[dict[str, Any]] = []
        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _Response()  # type: ignore[assignment]
        try:
            seam = lc.gateway_seam(
                "http://example.invalid/v1",
                lc.QWEN,
                "key",
                tools=lc.FLAT_TOOLS,
                sink=lambda _r: None,
                context={"level": lc.LEVEL_FLAT},
                transcript=written.append,
            )
            seam([{"role": "user", "content": "board"}])
        finally:
            urllib.request.urlopen = original
        assert written[0]["messages"][0]["content"] == "board"
        assert written[0]["response"]["tool_calls"][0]["name"] == "order"
        assert written[0]["call"]["finish_reason"] == "tool_calls"


# ── the prompts ─────────────────────────────────────────────────────────────


class TestThePrompts:
    def test_every_arm_sees_the_identical_board_text(self) -> None:
        block = lc.briefing_block(briefing())
        assert block in lc.commander_instruction(briefing())
        assert block in lc.flat_instruction(briefing())
        assert block in lc.unit_instruction(briefing(), "why?")

    def test_the_menu_is_numbered_so_an_index_answer_is_possible(self) -> None:
        rendered = lc.render_menu(briefing(options=3)["menu"])
        assert rendered.startswith("[0] ")
        assert "[2] " in rendered

    def test_an_empty_menu_says_so_rather_than_rendering_nothing(self) -> None:
        assert "park" in lc.render_menu([])

    def test_the_commander_is_told_it_decides_and_the_unit_is_told_it_does_not(self) -> None:
        assert "final authority" in lc.COMMANDER_SYSTEM
        assert "You propose; the commander decides." in lc.UNIT_SYSTEM
        assert "no way to act" in lc.UNIT_SYSTEM


# ── a decision point with no legal actions ──────────────────────────────────


class TestDegradation:
    def test_no_legal_actions_parks_the_unit_and_is_recorded(self) -> None:
        calls: list[lc.CallRecord] = []
        context: dict[str, Any] = {}
        record = lc.decide(
            arm=lc.ARM_B,
            match_key="B-0",
            decision_index=0,
            briefing=briefing(options=0),
            build_minds=lc.build_scripted_minds(lc.ARM_B, sink=calls.append, context=context),
            context=context,
        )
        assert record.no_order is True
        assert record.exit_reason == "no-legal-actions"
        assert calls == []

    def test_a_mind_that_never_orders_parks_rather_than_crashing(self) -> None:
        context: dict[str, Any] = {}

        def silent(_messages: list[dict[str, Any]]) -> ModelResponse:
            return ModelResponse(content="thinking about it")

        def factory(_briefing: dict[str, Any]) -> lc.Minds:
            return lc.Minds(commander=None, unit=silent, commander_model="", unit_model=lc.QWEN)

        record = lc.decide(
            arm=lc.ARM_A_QWEN,
            match_key="A-qwen-0",
            decision_index=0,
            briefing=briefing(),
            build_minds=factory,
            context=context,
        )
        assert record.no_order is True
        assert record.ordered_index is None

    def test_a_seam_that_raises_is_recorded_and_the_decision_still_returns(self) -> None:
        context: dict[str, Any] = {}

        def explode(_messages: list[dict[str, Any]]) -> ModelResponse:
            raise RuntimeError("gateway down")

        def factory(_briefing: dict[str, Any]) -> lc.Minds:
            return lc.Minds(commander=None, unit=explode, commander_model="", unit_model=lc.QWEN)

        record = lc.decide(
            arm=lc.ARM_A_QWEN,
            match_key="A-qwen-0",
            decision_index=0,
            briefing=briefing(),
            build_minds=factory,
            context=context,
        )
        assert "gateway down" in record.error
        assert record.no_order is True

    def test_a_child_that_dies_leaves_the_commander_able_to_decide(self) -> None:
        context: dict[str, Any] = {}
        state = {"turn": 0}

        def commander(_messages: list[dict[str, Any]]) -> ModelResponse:
            state["turn"] += 1
            if state["turn"] == 1:
                return ModelResponse(
                    tool_calls=[ToolCall(id="c", name="consult_unit", arguments={"question": "?"})]
                )
            return ModelResponse(
                tool_calls=[ToolCall(id="o", name="order", arguments={"menu_index": 1})]
            )

        def dead_unit(_messages: list[dict[str, Any]]) -> ModelResponse:
            raise RuntimeError("unit offline")

        def factory(_briefing: dict[str, Any]) -> lc.Minds:
            return lc.Minds(
                commander=commander,
                unit=dead_unit,
                commander_model=lc.GEMMA,
                unit_model=lc.QWEN,
            )

        record = lc.decide(
            arm=lc.ARM_B,
            match_key="B-0",
            decision_index=0,
            briefing=briefing(),
            build_minds=factory,
            context=context,
        )
        assert record.ordered_index == 1
        assert record.proposed_index is None
        assert record.overridden is None


# ── the series, end to end, through the real entry point ────────────────────


class TestTheSeries:
    def test_the_whole_matrix_runs_and_records_one_line_per_match(self, tmp_path: Path) -> None:
        out = tmp_path / "ledger.jsonl"
        result = play(tmp_path, "--arm", "all", "--n", "2", "--out", str(out))
        assert result.returncode == 0, result.stderr
        records = ledger(out)
        assert len(records) == len(lc.ARMS) * 2
        assert {r["arm"] for r in records} == set(lc.ARMS)

    def test_cost_is_reported_per_level_not_only_per_match(self, tmp_path: Path) -> None:
        out = tmp_path / "ledger.jsonl"
        assert play(tmp_path, "--arm", "B", "--n", "1", "--out", str(out)).returncode == 0
        record = ledger(out)[0]
        assert set(record["tokens_by_level"]) == {lc.LEVEL_COMMANDER, lc.LEVEL_UNIT}
        assert record["calls_by_level"][lc.LEVEL_COMMANDER] > 0
        assert record["calls_by_level"][lc.LEVEL_UNIT] > 0

    def test_a_flat_arm_reports_one_level_and_no_spawns(self, tmp_path: Path) -> None:
        out = tmp_path / "ledger.jsonl"
        assert play(tmp_path, "--arm", "A-gemma", "--n", "1", "--out", str(out)).returncode == 0
        record = ledger(out)[0]
        assert set(record["tokens_by_level"]) == {lc.LEVEL_FLAT}
        assert record["spawn_granted"] == 0

    def test_the_arena_carries_its_own_record_of_who_played_what(self, tmp_path: Path) -> None:
        out = tmp_path / "ledger.jsonl"
        assert play(tmp_path, "--arm", "B", "--n", "1", "--out", str(out)).returncode == 0
        roster = json.loads(
            (tmp_path / "arena" / ".league" / "teams" / "blueb.json").read_text(encoding="utf-8")
        )
        assert {a["model"] for a in roster["agents"]} == {lc.QWEN}
        # And the opponent is the fixed house bot, in league's own header.
        header = json.loads(
            (tmp_path / "arena" / ".league" / "matches" / "b0" / "log.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        assert header["driver_kinds"]["redb"] == lc.OPPONENT_DRIVER
        assert header["driver_kinds"]["blueb"] == lc.OUR_DRIVER

    def test_an_interrupted_series_continues_where_it_stopped(self, tmp_path: Path) -> None:
        out = tmp_path / "ledger.jsonl"
        assert play(tmp_path, "--arm", "B", "--n", "1", "--out", str(out)).returncode == 0
        first = len(ledger(out))
        second = play(tmp_path, "--arm", "B", "--n", "1", "--out", str(out))
        assert second.returncode == 0
        assert "skip B-0" in second.stderr
        assert len(ledger(out)) == first

    def test_only_public_league_verbs_are_used(self, tmp_path: Path) -> None:
        out = tmp_path / "ledger.jsonl"
        recorder = tmp_path / "argv.log"
        shim = tmp_path / "shim.py"
        shim.write_text(
            "import subprocess, sys, pathlib\n"
            f"pathlib.Path({str(recorder)!r}).open('a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            f"raise SystemExit(subprocess.run([{sys.executable!r}, {str(FAKE_CLEAGUE)!r}]"
            " + sys.argv[1:]).returncode)\n",
            encoding="utf-8",
        )
        root = tmp_path / "arena"
        root.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(  # nosec B603
            [
                sys.executable,
                str(HARNESS),
                "play",
                "--root",
                str(root),
                "--league",
                f"{sys.executable} {shim}",
                "--arm",
                "B",
                "--n",
                "1",
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        used = set()
        for line in recorder.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                used.add((parts[0], parts[1]))
        assert used <= PUBLIC_VERBS, used - PUBLIC_VERBS

    def test_the_config_record_names_every_setting(self, tmp_path: Path) -> None:
        out = tmp_path / "ledger.jsonl"
        config = tmp_path / "config.json"
        assert (
            play(
                tmp_path,
                "--arm",
                "B",
                "--n",
                "1",
                "--out",
                str(out),
                "--config-out",
                str(config),
            ).returncode
            == 0
        )
        payload = json.loads(config.read_text(encoding="utf-8"))
        assert payload["spawn_allowance"] == lc.SPAWN_ALLOWANCE
        assert payload["max_tokens"] == lc.MAX_TOKENS
        assert payload["temperature"] == lc.TEMPERATURE
        assert payload["opponent_driver"] == lc.OPPONENT_DRIVER
        assert payload["seed_is_metadata_only"] is True
        assert payload["arm_models"]["B"] == [lc.GEMMA, lc.QWEN]

    def test_the_arena_log_is_copied_beside_the_report(self, tmp_path: Path) -> None:
        out = tmp_path / "ledger.jsonl"
        logs = tmp_path / "logs"
        assert (
            play(
                tmp_path, "--arm", "B", "--n", "1", "--out", str(out), "--logs", str(logs)
            ).returncode
            == 0
        )
        assert (logs / "B-0.jsonl").exists()

    def test_live_needs_a_key_and_says_so_without_dialling(self, tmp_path: Path) -> None:
        result = play(tmp_path, "--arm", "B", "--n", "1", "--live")
        assert result.returncode == 2
        assert lc.API_KEY_ENV in result.stderr
        assert "hint:" in result.stderr


# ── the pre-registered fold ─────────────────────────────────────────────────


def _summary(arm: str, margins: list[int], **extra: Any) -> dict[str, Any]:
    import statistics

    base = {
        "arm": arm,
        "matches": len(margins),
        "margins": margins,
        "mean_margin": statistics.fmean(margins),
        "median_margin": statistics.median(margins),
        "void_reasons": [],
    }
    base.update(extra)
    return base


class TestTheFold:
    def test_a_clear_win_is_an_effect(self) -> None:
        high = _summary("B", [8, 9, 7, 8, 9])
        low = _summary("C", [1, 0, 2, 1, 0])
        assert lc.compare(high, low)["verdict"] == lc.VERDICT_EFFECT

    def test_a_tiny_difference_with_variance_is_no_effect(self) -> None:
        high = _summary("B", [3, 2, 4, 3, 2])
        low = _summary("C", [3, 3, 2, 3, 3])
        assert lc.compare(high, low)["verdict"] == lc.VERDICT_NO_EFFECT

    def test_identical_sets_are_inconclusive_not_no_effect(self) -> None:
        # The ceiling clause. t18 tied 12/12 and only escaped overclaiming
        # because this arm of the rule was written first.
        high = _summary("B", [4, 4, 4, 4, 4])
        low = _summary("C", [4, 4, 4, 4, 4])
        assert lc.compare(high, low)["verdict"] == lc.VERDICT_INCONCLUSIVE

    def test_a_big_mean_with_the_wrong_direction_is_inconclusive(self) -> None:
        high = _summary("B", [40, -5, -5, -5, -5])
        low = _summary("C", [1, 1, 1, 1, 1])
        result = lc.compare(high, low)
        assert result["delta"] >= lc.MARGIN_EFFECT
        assert result["direction_matches"] < result["direction_required"]
        assert result["verdict"] == lc.VERDICT_INCONCLUSIVE

    def test_an_arm_that_did_not_run_is_absent_not_a_loss(self) -> None:
        high = _summary("B", [5, 5, 5, 5, 5])
        low = {"arm": "C", "matches": 0}
        assert lc.compare(high, low)["verdict"] == lc.VERDICT_ABSENT

    def test_a_voided_arm_voids_the_comparison(self) -> None:
        high = _summary("B", [5, 5, 5, 5, 5], void_reasons=[lc.VOID_TRUNCATED])
        low = _summary("C", [0, 0, 0, 0, 0])
        assert lc.compare(high, low)["verdict"] == lc.VERDICT_VOID

    def test_the_direction_bar_scales_with_n(self) -> None:
        high = _summary("B", [8, 9, 7])
        low = _summary("C", [1, 0, 2])
        result = lc.compare(high, low)
        assert result["direction_required"] == 3
        assert result["verdict"] == lc.VERDICT_EFFECT


class TestTheValidityGates:
    def _match(self, arm: str, **extra: Any) -> dict[str, Any]:
        base = {
            "kind": "match",
            "arm": arm,
            "margin": 1,
            "blue_grade": 100.0,
            "decisions": 10,
            "no_order": 0,
            "spawn_granted": 10,
            "winner": "blue",
            "team_id": "blue",
            "calls_by_level": {lc.LEVEL_COMMANDER: 10, lc.LEVEL_UNIT: 10},
            "tokens_by_level": {},
            "seconds_by_level": {},
            "finish_reasons": {"tool_calls": 20},
            "overrides": 3,
            "override_opportunities": 10,
        }
        base.update(extra)
        return base

    def test_a_clean_arm_is_not_voided(self) -> None:
        summary = lc.arm_summary(lc.ARM_B, [self._match(lc.ARM_B)])
        assert summary["void_reasons"] == []

    def test_too_much_truncation_voids_the_arm_as_an_instrument_event(self) -> None:
        summary = lc.arm_summary(
            lc.ARM_B,
            [self._match(lc.ARM_B, finish_reasons={"tool_calls": 10, "length": 10})],
        )
        assert summary["void_reasons"] == [lc.VOID_TRUNCATED]

    def test_too_many_parked_units_voids_the_arm(self) -> None:
        summary = lc.arm_summary(lc.ARM_B, [self._match(lc.ARM_B, no_order=9)])
        assert lc.VOID_DEGRADED in summary["void_reasons"]

    def test_a_commander_that_never_consults_is_not_a_hierarchy(self) -> None:
        summary = lc.arm_summary(lc.ARM_B, [self._match(lc.ARM_B, spawn_granted=0)])
        assert lc.VOID_NOT_HIERARCHICAL in summary["void_reasons"]

    def test_a_flat_arm_is_never_voided_for_not_spawning(self) -> None:
        summary = lc.arm_summary(lc.ARM_A_QWEN, [self._match(lc.ARM_A_QWEN, spawn_granted=0)])
        assert lc.VOID_NOT_HIERARCHICAL not in summary["void_reasons"]

    def test_an_arm_with_no_matches_is_absent(self) -> None:
        assert lc.arm_summary(lc.ARM_C, [])["verdict"] == lc.VERDICT_ABSENT

    def test_the_override_rate_is_reported(self) -> None:
        summary = lc.arm_summary(lc.ARM_B, [self._match(lc.ARM_B)])
        assert summary["override_rate"] == 0.3


class TestTheAnalysisEntryPoint:
    def test_analyse_runs_over_a_real_ledger_and_names_every_arm(self, tmp_path: Path) -> None:
        out = tmp_path / "ledger.jsonl"
        assert play(tmp_path, "--arm", "all", "--n", "2", "--out", str(out)).returncode == 0
        result = subprocess.run(  # nosec B603
            [sys.executable, str(HARNESS), "analyse", "--out", str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert set(report["summaries"]) == set(lc.ARMS)
        assert report["arms_absent"] == []
        assert report["h1_hierarchy_vs_flat"]["verdict"] in lc.VERDICTS
        assert report["h2_gemma_vs_qwen_commander"]["verdict"] in lc.VERDICTS

    def test_a_partial_series_names_the_missing_arms_absent(self, tmp_path: Path) -> None:
        out = tmp_path / "ledger.jsonl"
        assert play(tmp_path, "--arm", "B", "--n", "1", "--out", str(out)).returncode == 0
        result = subprocess.run(  # nosec B603
            [sys.executable, str(HARNESS), "analyse", "--out", str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads(result.stdout)
        assert report["arms_run"] == [lc.ARM_B]
        assert set(report["arms_absent"]) == {lc.ARM_C, lc.ARM_A_QWEN, lc.ARM_A_GEMMA}
        assert report["h2_gemma_vs_qwen_commander"]["verdict"] == lc.VERDICT_ABSENT

    def test_a_corrupt_line_does_not_lose_the_rest_of_the_ledger(self, tmp_path: Path) -> None:
        out = tmp_path / "ledger.jsonl"
        out.write_text(
            '{"kind": "match", "arm": "B", "margin": 1}\nnot json\n'
            '{"kind": "match", "arm": "B", "margin": 2}\n',
            encoding="utf-8",
        )
        assert len(lc.load_matches(out)) == 2
