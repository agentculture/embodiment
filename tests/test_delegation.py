"""Tests for the subagent attenuation example (task t11).

Acceptance criteria:
1. The example runs hermetically — no network, no live model, deterministic.
2. The child's tool surface is a strict subset of the parent's.
3. The child's allowance is exactly one less than the parent's.
4. The child's pad is readable after the run and contains what the child wrote.
"""

from __future__ import annotations

import json

from examples import delegation


class TestDelegationExample:
    """The example demonstrates subagent attenuation end-to-end."""

    def test_example_runs_hermetically(self) -> None:
        """The example completes without network access or a live model."""
        report = delegation.run_demo()
        assert report["parent"]["exit_reason"] == "finished"
        assert report["child"]["allowance"] is not None

    def test_child_tools_are_strict_subset(self) -> None:
        """Child tool names are a strict subset of parent tool names."""
        report = delegation.run_demo()
        parent_tools = set(report["parent"]["tool_names"])
        child_tools = set(report["child"]["tool_names"])
        assert child_tools < parent_tools, (
            f"child tools {child_tools} must be a strict subset of " f"parent tools {parent_tools}"
        )
        # The withheld tools are actually missing from the child.
        withheld = parent_tools - child_tools
        assert withheld == {
            "delegate",
            "analyze",
        }, f"expected withheld tools {{'delegate', 'analyze'}}, got {withheld}"

    def test_child_allowance_is_one_less(self) -> None:
        """Child allowance equals attenuate(parent_allowance)."""
        report = delegation.run_demo()
        parent_allowance = report["parent"]["allowance"]
        child_allowance = report["child"]["allowance"]
        expected = delegation.attenuate(parent_allowance)
        assert (
            child_allowance == expected
        ), f"child allowance {child_allowance} != attenuate({parent_allowance}) = {expected}"
        # The child asked for 99 but got attenuate(parent) anyway.
        spawns = report["spawns"]
        assert len(spawns) >= 1
        assert spawns[0]["allowance_requested"] == 99
        assert spawns[0]["allowance_granted"] == expected

    def test_child_pad_is_readable(self) -> None:
        """The child's scratchpad contains entries written during the run."""
        report = delegation.run_demo()
        pad_entries = report["child"]["pad_entries"]
        assert (
            len(pad_entries) >= 2
        ), f"child pad should have at least 2 entries, got {len(pad_entries)}"
        # First entry is an intent, second is a conclusion.
        kinds = [e["kind"] for e in pad_entries]
        assert "intend" in kinds
        assert "conclude" in kinds

    def test_child_pad_contains_childs_work(self) -> None:
        """The child's pad entries contain the child's recorded text."""
        report = delegation.run_demo()
        pad_entries = report["child"]["pad_entries"]
        texts = [e["text"] for e in pad_entries]
        assert any(
            "analysis" in t.lower() for t in texts
        ), f"child pad should contain analysis text: {texts}"

    def test_spawn_record_shows_attenuation(self) -> None:
        """The spawn record shows the child got less than it asked for."""
        report = delegation.run_demo()
        spawns = report["spawns"]
        assert len(spawns) >= 1
        record = spawns[0]
        assert record["outcome"] == "granted"
        assert record["allowance_requested"] == 99
        assert record["allowance_granted"] == 1
        assert record["allowance_granted"] < record["allowance_requested"]

    def test_report_is_json_serializable(self) -> None:
        """The report round-trips through JSON cleanly."""
        report = delegation.run_demo()
        dumped = json.dumps(report, indent=2)
        loaded = json.loads(dumped)
        assert loaded["attenuation"]["child_tools_strict_subset"] is True
        assert loaded["attenuation"]["decrement"] == 1

    def test_attenuation_report_is_legible(self) -> None:
        """The attenuation section shows the decrement without reading source."""
        report = delegation.run_demo()
        att = report["attenuation"]
        assert att["parent_allowance"] == 2
        assert att["child_allowance"] == 1
        assert att["decrement"] == 1
        assert att["child_tools_strict_subset"] is True
        assert set(att["withheld_tools"]) == {"analyze", "delegate"}

    def test_parent_finished_after_delegation(self) -> None:
        """The parent drive completed successfully after delegating."""
        report = delegation.run_demo()
        assert report["parent"]["exit_reason"] == "finished"
        assert "delegated" in report["parent"]["summary"].lower()

    def test_child_finished_cleanly(self) -> None:
        """The child drive completed successfully."""
        report = delegation.run_demo()
        spawns = report["spawns"]
        assert len(spawns) >= 1
        assert spawns[0]["exit_reason"] == "finished"
