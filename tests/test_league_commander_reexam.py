"""Pins for the `league_commander` censoring re-exam (plan task t4).

The re-exam script lives at ``docs/live-test-results/league-commander-reexam.py`` and
its verdict is written up in ``docs/live-test-results/corrections.md``. This file is the
pin, not the argument, and it follows the discipline the rest of this suite uses for
measured claims: **recompute from the committed records rather than assert a remembered
number.** Every value below is derived here, from the same jsonl the script reads.

Two kinds of test are here and they fail for different reasons:

* the *arithmetic* tests check the script's own maths against the harness constants — if
  ``examples/league_commander.py`` changes a timeout or a retry count, the retry rungs
  move and these say so;
* the *census* tests check what the committed records actually contain. They encode the
  CLEAN verdict. If a future run appends a censored call to these files, they go red —
  which is the point: the verdict is pinned to the evidence, not to a memory of it.

Nothing here dials anything or runs a live model.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from examples import league_commander as lc  # noqa: E402

RESULTS = REPO_ROOT / "docs" / "live-test-results"
REEXAM_PATH = RESULTS / "league-commander-reexam.py"

# The two committed series, by ledger stem. The re-exam covers both; #42's audit named
# only the harness, and the escalation run dials the same constant.
STEMS = ("league-commander", "league-commander-frontier")


def _load_reexam() -> Any:
    """Import the hyphenated analysis script by path, the way an operator runs it."""
    spec = importlib.util.spec_from_file_location("league_commander_reexam", REEXAM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: the module defines a dataclass, and `@dataclass` resolves
    # string annotations through `sys.modules[cls.__module__]`. Skip this and the very
    # first decorator raises `AttributeError: 'NoneType' object has no attribute
    # '__dict__'` — an import-mechanics failure that looks nothing like its cause.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def reexam() -> Any:
    return _load_reexam()


@pytest.fixture(scope="module")
def runs(reexam: Any) -> list[Any]:
    return [reexam.load_run(*spec) for spec in reexam.RUNS]


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


# --- the arithmetic ---------------------------------------------------------


def test_retry_rungs_derive_from_the_as_run_constants(reexam: Any) -> None:
    """A clock-cut call cannot record less than one timeout plus one wait.

    ``gateway_seam`` starts its stopwatch before the first attempt and never resets it,
    so the rungs are cumulative. The exhausted value is the identity that matched
    ``worker_seam``'s two lost calls to 0.4 s — here it is 4 x 900 + 3 x 30, because this
    harness waits 30 s between attempts and not the 20 s the audit's example used.

    **From the as-run config, not the live constants.** These records were produced at
    900.0 s; plan task ``t2`` has since raised the harness to 1600.0 on this very
    re-exam's Gemma finding, and rungs computed from the new value would describe a
    signature these records could never carry. That was a live defect — the script did
    read the live constants — and the fix is the same one `corrections.md` §10 named
    one file over: read the field that reports the event, not the one that happens to
    be nearby.
    """
    settings = reexam.as_run_config()
    rungs = dict(reexam.retry_rungs())
    step = settings["request_timeout"] + settings["retry_wait_seconds"]
    assert rungs["1 timeout(s) then success (floor)"] == pytest.approx(step)
    assert rungs["1 timeout(s) then success (floor)"] == pytest.approx(930.0)
    assert rungs["all attempts exhausted (exact)"] == pytest.approx(
        (settings["max_retries"] + 1) * settings["request_timeout"]
        + settings["max_retries"] * settings["retry_wait_seconds"]
    )
    assert rungs["all attempts exhausted (exact)"] == pytest.approx(3690.0)


def test_the_as_run_clock_is_the_committed_one_and_not_the_live_one(reexam: Any) -> None:
    """The distinction this re-exam now turns on, asserted rather than trusted."""
    settings = reexam.as_run_config()
    assert settings["request_timeout"] == pytest.approx(900.0)
    assert settings["max_tokens"] == lc.MAX_TOKENS
    assert lc.REQUEST_TIMEOUT > settings["request_timeout"], (
        "the harness has been raised past the value these records were taken at; if it "
        "is ever lowered back, this re-exam's framing needs revisiting"
    )


def test_linear_fit_recovers_a_planted_rate(reexam: Any) -> None:
    """A test of the test: the fit used to estimate each model's rate must be right."""
    tokens = [100, 500, 1000, 2000]
    seconds = [5.0 + t / 20.0 for t in tokens]
    slope, intercept, r_squared = reexam._linear_fit(tokens, seconds)
    assert 1 / slope == pytest.approx(20.0)
    assert intercept == pytest.approx(5.0)
    assert r_squared == pytest.approx(1.0)


# --- the census: what the committed records contain -------------------------


def test_no_call_records_a_retry(runs: list[Any]) -> None:
    """`retries > 0` is the first tell, and it fires nowhere in either series."""
    for run in runs:
        assert sum(int(m.get("retries") or 0) for m in run.matches) == 0, run.label
        assert [c for c in run.calls if int(c.get("retries") or 0) > 0] == [], run.label
        assert [c for c in run.calls if c.get("error")] == [], run.label


def test_no_call_reaches_the_timeout_let_alone_a_retry_rung(runs: list[Any]) -> None:
    """The slowest committed call is 193.6 s against a 900 s clock — 21.5% of it."""
    slowest = max(c["seconds"] for run in runs for c in run.calls)
    assert slowest < lc.REQUEST_TIMEOUT
    assert slowest == pytest.approx(193.609)
    for run in runs:
        assert [c for c in run.calls if c["seconds"] >= lc.REQUEST_TIMEOUT] == [], run.label


def test_every_call_finished_tool_calls(runs: list[Any]) -> None:
    """Zero `length` finishes: the 16000-token budget never bound, so the clock cannot
    have been what stopped a turn."""
    for run in runs:
        reasons = {c.get("finish_reason") for c in run.calls}
        assert reasons == {"tool_calls"}, (run.label, reasons)


def test_no_match_was_discarded_or_replayed(runs: list[Any]) -> None:
    """``run_series`` wraps ``play_match`` in no ``try``.

    An exhausted-retry call raises through it, so the ledger row is never appended while
    the calls the match already paid for are already in the transcript file. Orphaned
    transcript keys are the signature of a discarded match; a per-key count mismatch is
    the signature of one replayed after a crash. Neither exists.
    """
    for run in runs:
        ledger: dict[str, int] = {}
        for match in run.matches:
            key = match["match_key"]
            ledger[key] = ledger.get(key, 0) + sum((match.get("calls_by_level") or {}).values())
        transcript: dict[str, int] = {}
        for call in run.calls:
            transcript[call["match_key"]] = transcript.get(call["match_key"], 0) + 1
        assert ledger == transcript, run.label
        assert run.log_keys == set(ledger), run.label


def test_the_committed_call_count_is_what_was_published(runs: list[Any]) -> None:
    assert len(runs[0].calls) == 120
    assert len(runs[1].calls) == 264


# --- the census: the figures the repo cites ---------------------------------


def test_the_published_cost_ratios_recompute_from_the_ledger(reexam: Any, runs: list[Any]) -> None:
    """2.4x and 4.4x are what the raw ledger says, to the digit the write-up rounded to."""
    per_match: dict[str, float] = {}
    counts: dict[str, int] = {}
    totals: dict[str, int] = {}
    for match in runs[0].matches:
        arm = match["arm"]
        counts[arm] = counts.get(arm, 0) + 1
        spend = sum(
            level["prompt"] + level["completion"]
            for level in (match.get("tokens_by_level") or {}).values()
        )
        totals[arm] = totals.get(arm, 0) + spend
    for arm, total in totals.items():
        per_match[arm] = total / counts[arm]

    assert per_match["B"] == pytest.approx(25459.667, abs=0.01)
    assert per_match["A-qwen"] == pytest.approx(10703.0)
    assert per_match["A-gemma"] == pytest.approx(5827.0)
    assert round(per_match["B"] / per_match["A-qwen"], 1) == reexam.PUBLISHED_RATIO_VS_QWEN
    assert round(per_match["B"] / per_match["A-gemma"], 1) == reexam.PUBLISHED_RATIO_VS_GEMMA


def test_the_largest_completion_is_nowhere_near_the_budget(runs: list[Any]) -> None:
    """14.1% of `max_tokens`. A clock can only censor what a budget lets grow."""
    largest = max(c["completion_tokens"] for run in runs for c in run.calls)
    assert largest == 2253
    assert largest / lc.MAX_TOKENS < 0.15


# --- the advisories the clean verdict does not cover ------------------------


def test_the_margin_was_generation_only_and_the_as_run_series_exceeded_its_slack(
    reexam: Any, runs: list[Any]
) -> None:
    """The 1.21x margin had 155.8 s of slack; one Qwen call spent 179.3 s not generating.

    This does not make the records dirty — that call produced 365 tokens, not 16000. It
    is why the as-run margin is recorded as thin rather than as comfortable, and it is
    the measurement plan task ``t2`` turned into ``derive_timeout``'s explicit
    ``non_generation_s`` term. Stated against the **as-run** clock: the finding is about
    what was shipped when these records were taken.
    """
    as_run = float(reexam.as_run_config()["request_timeout"])
    bound = lc.MAX_TOKENS / 21.5
    assert as_run / bound == pytest.approx(1.21, abs=0.005)
    slack = as_run - bound

    qwen = [c for run in runs for c in run.calls if c["model"] == lc.QWEN]
    fastest = max(c["completion_tokens"] / c["seconds"] for c in qwen)
    assert fastest == pytest.approx(25.43, abs=0.01), "corroborates corrections.md's 21.5-25.4 band"
    worst_overhead = max(c["seconds"] - c["completion_tokens"] / fastest for c in qwen)
    assert worst_overhead > slack


def test_the_gemma_role_was_below_bound_and_the_raise_has_closed_it(
    reexam: Any, runs: list[Any]
) -> None:
    """One timeout fronts two models; #42's audit derived it at the cortex rate only.

    Both halves are asserted: that the finding was real against the as-run clock, and
    that the constant shipping today clears the bound it identified. The first half
    keeps the published verdict honest; the second is what stops the finding from being
    re-discovered.
    """
    as_run = float(reexam.as_run_config()["request_timeout"])
    gemma = [c for run in runs for c in run.calls if c["model"] == lc.GEMMA]
    fastest = max(c["completion_tokens"] / c["seconds"] for c in gemma)
    assert fastest < 13.0
    gemma_bound = lc.MAX_TOKENS / fastest
    assert gemma_bound > as_run, "the finding, against the clock these records ran under"
    assert lc.REQUEST_TIMEOUT > gemma_bound, (
        "the harness still ships below the Gemma bound this re-exam found; "
        "tests/test_timeout_bounds.py is the gate that should have caught it"
    )
    # ...and the reason it never mattered: Gemma's completions stayed tiny here.
    assert max(c["completion_tokens"] for c in gemma) < 250


# --- the script itself ------------------------------------------------------


def test_the_reexam_script_exits_clean_on_the_committed_records(reexam: Any) -> None:
    """The verdict is reproducible, not merely reported."""
    assert reexam.main([]) == 0


def test_the_record_files_the_verdict_rests_on_are_present(runs: list[Any]) -> None:
    for stem in STEMS:
        assert (RESULTS / f"{stem}.jsonl").exists()
        assert (RESULTS / f"{stem}-transcripts.jsonl").exists()
        assert _jsonl(RESULTS / f"{stem}.jsonl"), stem
