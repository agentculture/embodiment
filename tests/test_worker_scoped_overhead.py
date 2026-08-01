"""Task t8 — the tiny-scoped-call overhead probe, and the arithmetic it reports.

Every test here is **hermetic**: nothing in this module reaches a socket. The
live dial lives in the results document's `## Reproduce` section, not in the
suite — matching `tests/test_worker_throughput.py`'s convention.

What each acceptance criterion needs proved, and where:

1. **A probe at widths 1 and 8 with realistic league-unit prompts and
   tens-of-token completions** — :class:`TestCellTable` pins the committed cell
   table (both widths present, the scoped budget genuinely *tens* of tokens);
   :class:`TestPrompts` pins the prompts as real scoped questions with an
   enumerable answer space and a shared preamble that is identical across
   calls.
2. **Prompt-token accounting per call** — :class:`TestCallRecordShape` and
   :class:`TestCellSummary` prove prompt tokens survive from the wire into the
   per-call record and into the per-cell summary as mean/median/total, never
   folded into completion tokens.
3. **The residual metric's own arithmetic, pinned before it is trusted on live
   numbers** — :class:`TestResidualSeconds` computes it against synthetic data
   with a known answer, including the negative-residual and missing-input
   cases.
4. **The derived answers the sweep will cite** —
   :class:`TestConcurrencyTransfer` proves the transfer ratio reads correctly
   under both true parallelism and pure queueing;
   :class:`TestScopedCallsPerCortexTurn` proves the N-calls-per-cortex-turn
   arithmetic and that an absent cell yields ABSENT rather than a number.
5. **The thinking toggle actually reaches the wire** —
   :class:`TestScopedSeamWire`, asserted on the request body rather than on a
   branch.
6. **A failed call is data, never a raise from a thread** —
   :class:`TestOneCallNeverRaises`, :class:`TestRunBatchTimeout`.
7. **The batch really runs concurrently** — :class:`TestRunBatchIsReallyConcurrent`,
   using the `threading.Barrier` technique `tests/test_worker_throughput.py`
   uses for the same reason.
8. **Every committed table is generated, never typed** —
   :class:`TestRenderMarkdown`.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples import worker_scoped_overhead as wso  # noqa: E402
from examples import worker_seam as ws  # noqa: E402
from examples.worker_seam import WorkerConfig  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def _config(**overrides: Any) -> WorkerConfig:
    kwargs: dict[str, Any] = dict(base_url="http://example.invalid/v1", model="m", api_key="k")
    kwargs.update(overrides)
    return WorkerConfig(**kwargs)


def _spec(**overrides: Any) -> wso.CallSpec:
    kwargs: dict[str, Any] = dict(
        cell="t",
        width=1,
        batch=0,
        slot=0,
        situation_id=0,
        context=wso.CONTEXT_LEAN,
        thinking="off",
        max_tokens=wso.SCOPED_MAX_TOKENS,
    )
    kwargs.update(overrides)
    return wso.CallSpec(**kwargs)


def _record(**overrides: Any) -> wso.CallRecord:
    """A synthetic ok record with a residual already computed consistently."""
    latency = overrides.pop("latency_seconds", 1.0)
    completion = overrides.pop("completion_tokens", 40)
    modelled, residual, fraction = wso.residual_seconds(latency, completion)
    kwargs: dict[str, Any] = dict(
        cell="t",
        width=1,
        batch=0,
        slot=0,
        situation_id=0,
        context=wso.CONTEXT_LEAN,
        thinking="off",
        max_tokens=wso.SCOPED_MAX_TOKENS,
        warmup=False,
        ok=True,
        error=None,
        latency_seconds=latency,
        prompt_tokens=200,
        completion_tokens=completion,
        content_chars=30,
        reasoning_chars=0,
        finish_reason="stop",
        truncated=False,
        retries=0,
        tokens_per_second=(completion / latency if latency else None),
        menu_index=1,
        answered=True,
        modelled_decode_seconds=modelled,
        residual_seconds=residual,
        residual_fraction=fraction,
    )
    kwargs.update(overrides)
    return wso.CallRecord(**kwargs)


def _completion(
    *, content: str = "menu_index=1: run it home", prompt: int = 200, completion: int = 40
) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


# ── criterion 1: the committed cell table ────────────────────────────────────


class TestCellTable:
    def test_both_widths_the_claim_rests_on_are_present(self) -> None:
        widths = {cell.width for cell in wso.CELLS}
        assert {1, 8} <= widths, "the B1 economics claim rests on widths 1 and 8"

    def test_the_scoped_budget_really_is_tens_of_tokens(self) -> None:
        assert 10 <= wso.SCOPED_MAX_TOKENS < 100, (
            "the whole point of this probe is the tens-of-tokens regime; a budget "
            "outside it measures something else"
        )
        scoped = [cell for cell in wso.CELLS if cell.budget == "scoped"]
        assert scoped, "there must be scoped cells"
        assert all(cell.max_tokens == wso.SCOPED_MAX_TOKENS for cell in scoped)

    def test_a_natural_budget_arm_exists_so_the_cap_is_not_the_only_reading(self) -> None:
        natural = [cell for cell in wso.CELLS if cell.budget == "natural"]
        assert natural, (
            "capping every cell would hide a worker that cannot answer in tens of "
            "tokens at all — that is a finding, not a nuisance"
        )
        assert all(cell.max_tokens == wso.NATURAL_MAX_TOKENS for cell in natural)

    def test_both_thinking_modes_are_measured_not_assumed(self) -> None:
        assert {cell.thinking for cell in wso.CELLS} == {"on", "off"}

    def test_both_context_tiers_are_measured_at_width_1_and_8(self) -> None:
        pairs = {(cell.context, cell.width) for cell in wso.CELLS if cell.thinking == "off"}
        assert {
            (wso.CONTEXT_LEAN, 1),
            (wso.CONTEXT_LEAN, 8),
            (wso.CONTEXT_RICH, 1),
            (wso.CONTEXT_RICH, 8),
        } <= pairs, "the prefill axis needs both tiers at both widths to be readable"

    def test_cell_ids_are_unique(self) -> None:
        ids = [cell.id for cell in wso.CELLS]
        assert len(ids) == len(set(ids))

    def test_every_cell_states_why_it_exists(self) -> None:
        assert all(cell.why.strip() for cell in wso.CELLS)

    def test_calls_is_width_times_batches(self) -> None:
        for cell in wso.CELLS:
            assert cell.calls == cell.width * cell.batches

    def test_thinking_wire_has_an_entry_for_every_dialled_mode(self) -> None:
        # A mode with no entry must be a config error, never a silent no-op.
        for cell in wso.CELLS:
            assert cell.thinking in wso.THINKING_WIRE


# ── criterion 1: the prompts are genuine scoped questions ────────────────────


class TestPrompts:
    def test_the_shared_preamble_is_identical_across_every_situation(self) -> None:
        rendered = [wso.build_prompt(s, wso.CONTEXT_RICH) for s in wso.SITUATIONS]
        assert all(page.startswith(wso.SHARED_PREAMBLE) for page in rendered), (
            "the repeated-context axis only means anything if the repeated part "
            "is byte-identical across calls"
        )

    def test_rich_is_materially_larger_than_lean_and_both_ask_the_same_question(self) -> None:
        for situation in wso.SITUATIONS:
            lean = wso.build_prompt(situation, wso.CONTEXT_LEAN)
            rich = wso.build_prompt(situation, wso.CONTEXT_RICH)
            assert len(rich) > 4 * len(lean), "the tiers must differ enough to be readable"
            assert lean.endswith(wso.SCOPED_QUESTION)
            assert rich.endswith(wso.SCOPED_QUESTION)
            # Only the head moved; the unit's own block is byte-identical.
            assert rich.endswith(lean)

    def test_every_situation_offers_a_small_enumerable_answer_space(self) -> None:
        for situation in wso.SITUATIONS:
            assert 2 <= len(situation.menu) <= 6, "a scoped question has a small answer space"

    def test_the_menu_indices_a_model_may_answer_with_are_rendered(self) -> None:
        situation = wso.SITUATIONS[0]
        lean = wso.build_prompt(situation, wso.CONTEXT_LEAN)
        for index in range(len(situation.menu)):
            assert f"  {index}. " in lean

    def test_situations_are_eight_wide_so_no_batch_slot_shares_a_prompt(self) -> None:
        widest = max(cell.width for cell in wso.CELLS)
        assert len(wso.SITUATIONS) >= widest

    def test_an_unknown_context_tier_raises_rather_than_defaulting(self) -> None:
        with pytest.raises(ValueError, match="unknown context tier"):
            wso.build_prompt(wso.SITUATIONS[0], "enormous")

    @pytest.mark.parametrize(
        "content,expected",
        [
            ("menu_index=1: run it home", 1),
            ("menu_index = 3 : hold", 3),
            ("  MENU_INDEX=0", None),  # case-sensitive on purpose: the contract is exact
            ("index 2 is best", None),
            ("", None),
            (None, None),
        ],
    )
    def test_parse_menu_index(self, content: Optional[str], expected: Optional[int]) -> None:
        assert wso.parse_menu_index(content) == expected


# ── criterion 5: the thinking toggle reaches the wire ────────────────────────


class TestScopedSeamWire:
    def _seam(self, monkeypatch: pytest.MonkeyPatch, mode: str) -> wso.ScopedSeam:
        monkeypatch.setattr(ws.WorkerSeam, "_post", lambda self, body: _completion())
        return wso.ScopedSeam(
            base_url="http://example.invalid/v1",
            model="m",
            api_key="k",
            max_tokens=wso.SCOPED_MAX_TOKENS,
            wire_extra=wso.THINKING_WIRE[mode],
        )

    def test_thinking_off_puts_enable_thinking_false_on_the_wire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seam = self._seam(monkeypatch, "off")
        seam([{"role": "user", "content": "hi"}])
        assert seam.last_body is not None
        assert seam.last_body["chat_template_kwargs"] == {"enable_thinking": False}

    def test_thinking_on_puts_enable_thinking_true_on_the_wire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seam = self._seam(monkeypatch, "on")
        seam([{"role": "user", "content": "hi"}])
        assert seam.last_body is not None
        assert seam.last_body["chat_template_kwargs"] == {"enable_thinking": True}

    def test_the_inherited_body_keys_survive_the_merge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seam = self._seam(monkeypatch, "off")
        seam([{"role": "user", "content": "hi"}])
        assert seam.last_body is not None
        assert seam.last_body["model"] == "m"
        assert seam.last_body["max_tokens"] == wso.SCOPED_MAX_TOKENS
        assert seam.last_body["messages"] == [{"role": "user", "content": "hi"}]

    def test_the_raw_response_is_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seam = self._seam(monkeypatch, "off")
        seam([{"role": "user", "content": "hi"}])
        assert seam.last_payload is not None
        assert seam.last_payload["usage"]["prompt_tokens"] == 200


# ── criterion 3: the residual metric's own arithmetic ────────────────────────


class TestResidualSeconds:
    def test_a_call_that_decodes_at_exactly_the_reference_rate_has_zero_residual(self) -> None:
        completion = 76
        latency = completion / wso.REFERENCE_DECODE_TOK_S
        modelled, residual, fraction = wso.residual_seconds(latency, completion)
        assert modelled == pytest.approx(latency)
        assert residual == pytest.approx(0.0, abs=1e-9)
        assert fraction == pytest.approx(0.0, abs=1e-9)

    def test_a_tiny_completion_in_a_long_call_is_almost_all_residual(self) -> None:
        # 40 tokens is 0.523s of decode at 76.43 tok/s. In a 2s call, 74% of the
        # wall clock is something other than decoding — that share IS the thing
        # this task exists to measure.
        modelled, residual, fraction = wso.residual_seconds(2.0, 40)
        assert modelled == pytest.approx(40 / 76.43, rel=1e-6)
        assert residual == pytest.approx(2.0 - 40 / 76.43, rel=1e-6)
        assert fraction == pytest.approx(1 - (40 / 76.43) / 2.0, rel=1e-6)
        assert 0.73 < (fraction or 0) < 0.75

    def test_a_1200_token_call_is_mostly_decode_which_is_why_the_old_series_missed_this(
        self,
    ) -> None:
        # This probe's OWN measured per-call overhead (0.35s at `rich-off-w1`),
        # put under a 1200-token completion — the length the old throughput
        # series measured. The residual share collapses from 66% to ~2%. This is
        # the arithmetic behind c43: the overhead did not change between the two
        # series, the denominator did. The results document quotes this
        # derivation, so it is pinned here rather than left as prose.
        measured_overhead = 0.35
        decode = 1200 / wso.REFERENCE_DECODE_TOK_S
        _modelled, residual, fraction = wso.residual_seconds(decode + measured_overhead, 1200)
        assert residual == pytest.approx(measured_overhead, abs=1e-9)
        assert fraction is not None
        assert fraction == pytest.approx(0.0218, abs=5e-4)

        # ...and the same 0.35s on a 15-token scoped completion is most of it.
        _m, _r, scoped_fraction = wso.residual_seconds(15 / wso.REFERENCE_DECODE_TOK_S + 0.35, 15)
        assert scoped_fraction is not None
        assert scoped_fraction > 0.64

    def test_a_negative_residual_is_reported_not_clamped(self) -> None:
        # Faster than the reference rate. Flooring this at zero would turn "the
        # reference rate is wrong for this regime" into "there is no overhead".
        modelled, residual, fraction = wso.residual_seconds(0.1, 40)
        assert modelled == pytest.approx(40 / 76.43, rel=1e-6)
        assert residual is not None
        assert residual < 0
        assert fraction is not None
        assert fraction < 0

    @pytest.mark.parametrize("latency,completion", [(None, 40), (1.0, None), (None, None)])
    def test_a_missing_input_yields_no_residual_rather_than_a_fabricated_one(
        self, latency: Optional[float], completion: Optional[int]
    ) -> None:
        assert wso.residual_seconds(latency, completion) == (None, None, None)

    def test_a_nonpositive_rate_yields_no_residual_rather_than_a_division_error(self) -> None:
        assert wso.residual_seconds(1.0, 40, rate_tok_s=0.0) == (None, None, None)

    def test_zero_latency_yields_no_fraction(self) -> None:
        modelled, residual, fraction = wso.residual_seconds(0.0, 40)
        assert modelled is not None
        assert residual is not None
        assert fraction is None

    def test_the_reference_rate_is_the_committed_width1_figure(self) -> None:
        # If this constant drifts, every residual in every committed run becomes
        # incomparable. It is pinned to the published number on purpose.
        assert wso.REFERENCE_DECODE_TOK_S == 76.43
        assert wso.REFERENCE_WIDTH8_EFFECTIVE_CONCURRENCY == 6.14


# ── criterion 2 + 6: one call, fully accounted, and it never raises ──────────


class TestCallRecordShape:
    def test_prompt_and_completion_tokens_survive_onto_the_record_separately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ws.WorkerSeam,
            "_post",
            lambda self, body: _completion(prompt=1743, completion=37),
        )
        record = wso._one_call(_config(), _spec())
        assert record.ok
        assert record.prompt_tokens == 1743
        assert record.completion_tokens == 37
        assert record.to_dict()["prompt_tokens"] == 1743

    def test_the_answer_is_parsed_and_recorded_as_answered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ws.WorkerSeam, "_post", lambda self, body: _completion(content="menu_index=2: go")
        )
        record = wso._one_call(_config(), _spec())
        assert record.menu_index == 2
        assert record.answered is True

    def test_a_reply_with_no_parseable_answer_is_recorded_unanswered_not_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The #32 shape: a worker handed a narrow contract that writes something
        # else. It must be a measured rate, never a silent ok.
        monkeypatch.setattr(
            ws.WorkerSeam, "_post", lambda self, body: _completion(content="I would hold.")
        )
        record = wso._one_call(_config(), _spec())
        assert record.ok is True
        assert record.answered is False
        assert record.menu_index is None

    def test_the_residual_is_computed_onto_the_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ws.WorkerSeam, "_post", lambda self, body: _completion(completion=40))
        record = wso._one_call(_config(), _spec())
        assert record.modelled_decode_seconds == pytest.approx(40 / 76.43, rel=1e-6)
        assert record.residual_seconds is not None
        assert record.residual_fraction is not None

    def test_the_wire_carries_the_cells_thinking_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def capture(self: ws.WorkerSeam, body: dict[str, Any]) -> dict[str, Any]:
            seen.update(body)
            return _completion()

        monkeypatch.setattr(ws.WorkerSeam, "_post", capture)
        wso._one_call(_config(), _spec(thinking="on"))
        assert seen["chat_template_kwargs"] == {"enable_thinking": True}

    def test_the_system_prompt_and_the_scoped_question_are_both_sent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def capture(self: ws.WorkerSeam, body: dict[str, Any]) -> dict[str, Any]:
            seen.update(body)
            return _completion()

        monkeypatch.setattr(ws.WorkerSeam, "_post", capture)
        wso._one_call(_config(), _spec(context=wso.CONTEXT_RICH))
        roles = [message["role"] for message in seen["messages"]]
        assert roles == ["system", "user"]
        assert seen["messages"][0]["content"] == wso.SCOPED_SYSTEM_PROMPT
        assert seen["messages"][1]["content"].startswith(wso.SHARED_PREAMBLE)


class TestOneCallNeverRaises:
    def test_a_transport_failure_becomes_a_record(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def always_fails(self: ws.WorkerSeam, body: dict[str, Any]) -> dict[str, Any]:
            raise OSError("connection reset")

        monkeypatch.setattr(ws.WorkerSeam, "_post", always_fails)
        record = wso._one_call(_config(), _spec(), sleep=lambda _s: None)
        assert record.ok is False
        assert record.error is not None
        assert "WorkerTransportError" in record.error
        assert record.retries == ws.MAX_TRANSPORT_RETRIES + 1
        assert record.residual_seconds is None, "a failed call has no residual to report"

    def test_a_truncated_call_is_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def truncated(self: ws.WorkerSeam, body: dict[str, Any]) -> dict[str, Any]:
            payload = _completion(content="")
            payload["choices"][0]["finish_reason"] = "length"
            return payload

        monkeypatch.setattr(ws.WorkerSeam, "_post", truncated)
        record = wso._one_call(_config(), _spec())
        assert record.truncated is True
        assert record.finish_reason == "length"
        assert record.answered is False

    def test_an_unknown_thinking_mode_is_a_recorded_failure_not_a_silent_no_op(self) -> None:
        record = wso._one_call(_config(), _spec(thinking="maybe"))
        assert record.ok is False
        assert record.error is not None
        assert "unknown thinking mode" in record.error


# ── criterion 6/7: the batch really is concurrent, and bounded ───────────────


class TestRunBatchIsReallyConcurrent:
    def test_eight_slots_run_at_once(self) -> None:
        # A fan-out proved only against a fake scheduler proves nothing about the
        # one it runs on: the barrier can only clear if all eight threads are
        # genuinely in flight together.
        barrier = threading.Barrier(8, timeout=10)

        def call_fn(config: WorkerConfig, spec: wso.CallSpec) -> wso.CallRecord:
            barrier.wait()
            return _record(cell=spec.cell, width=spec.width, slot=spec.slot)

        specs = [_spec(slot=slot, width=8) for slot in range(8)]
        records, elapsed = wso._run_batch(_config(), specs, call_fn=call_fn, timeout=20)
        assert len(records) == 8
        assert elapsed < 10

    def test_records_come_back_in_submission_order(self) -> None:
        def call_fn(config: WorkerConfig, spec: wso.CallSpec) -> wso.CallRecord:
            time.sleep(0.02 * (4 - spec.slot))  # finish in reverse order
            return _record(slot=spec.slot)

        specs = [_spec(slot=slot, width=4) for slot in range(4)]
        records, _elapsed = wso._run_batch(_config(), specs, call_fn=call_fn, timeout=20)
        assert [record.slot for record in records] == [0, 1, 2, 3]

    def test_an_empty_batch_is_zero_work_not_a_crash(self) -> None:
        assert wso._run_batch(_config(), [], call_fn=lambda c, s: _record()) == ([], 0.0)


class TestRunBatchTimeout:
    def test_a_call_that_misses_the_deadline_is_recorded_as_a_timeout(self) -> None:
        release = threading.Event()

        def call_fn(config: WorkerConfig, spec: wso.CallSpec) -> wso.CallRecord:
            release.wait(timeout=30)
            return _record()

        try:
            specs = [_spec(slot=0)]
            records, _elapsed = wso._run_batch(_config(), specs, call_fn=call_fn, timeout=0.2)
            assert len(records) == 1
            assert records[0].ok is False
            assert records[0].error == "timeout"
            assert records[0].latency_seconds == 0.2
        finally:
            release.set()


# ── criterion 2: per-cell aggregation ────────────────────────────────────────


class TestCellSummary:
    def _run(self, **cell_overrides: Any) -> wso.CellRun:
        kwargs: dict[str, Any] = dict(
            id="c",
            budget="scoped",
            thinking="off",
            context=wso.CONTEXT_LEAN,
            width=2,
            max_tokens=wso.SCOPED_MAX_TOKENS,
            batches=1,
            why="test",
        )
        kwargs.update(cell_overrides)
        return wso.CellRun(cell=wso.Cell(**kwargs))

    def test_prompt_tokens_are_summarised_separately_from_completion_tokens(self) -> None:
        run = self._run()
        run.records = [
            _record(prompt_tokens=100, completion_tokens=20, latency_seconds=1.0),
            _record(prompt_tokens=300, completion_tokens=40, latency_seconds=1.0),
        ]
        run.batch_elapsed_seconds = [1.0]
        summary = run.summary()
        assert summary.prompt_tokens_total == 400
        assert summary.prompt_tokens_mean == pytest.approx(200.0)
        assert summary.prompt_tokens_median == pytest.approx(200.0)
        assert summary.completion_tokens_total == 60
        assert summary.completion_tokens_mean == pytest.approx(30.0)

    def test_seconds_per_call_is_batch_wall_clock_over_calls_not_per_call_latency(self) -> None:
        # Eight calls that each took 3s, dispatched in one 3s batch, cost 0.375s
        # of wall clock *per call*. These two numbers are different on purpose
        # and a sweep budgets against the second.
        run = self._run(width=8)
        run.records = [_record(latency_seconds=3.0, completion_tokens=40) for _ in range(8)]
        run.batch_elapsed_seconds = [3.0]
        summary = run.summary()
        assert summary.latency_seconds_mean == pytest.approx(3.0)
        assert summary.seconds_per_call_wallclock == pytest.approx(0.375)
        assert summary.calls_per_second == pytest.approx(8 / 3.0)

    def test_errors_timeouts_truncations_and_unanswered_are_all_counted(self) -> None:
        run = self._run()
        run.records = [
            _record(),
            _record(ok=False, error="timeout", latency_seconds=None, completion_tokens=None),
            _record(truncated=True, finish_reason="length", answered=False, menu_index=None),
        ]
        run.batch_elapsed_seconds = [1.0]
        summary = run.summary()
        assert summary.calls == 3
        assert summary.ok == 2
        assert summary.errors == 1
        assert summary.timeouts == 1
        assert summary.truncated == 1
        assert summary.answered == 1
        assert summary.finish_reasons == {"stop": 1, "length": 1}

    def test_failed_calls_do_not_pollute_the_token_or_residual_means(self) -> None:
        run = self._run()
        run.records = [
            _record(prompt_tokens=100, completion_tokens=20, latency_seconds=1.0),
            _record(
                ok=False,
                error="boom",
                prompt_tokens=None,
                completion_tokens=None,
                latency_seconds=None,
                residual_seconds=None,
                residual_fraction=None,
                modelled_decode_seconds=None,
                tokens_per_second=None,
            ),
        ]
        run.batch_elapsed_seconds = [1.0]
        summary = run.summary()
        assert summary.prompt_tokens_mean == pytest.approx(100.0)
        assert summary.completion_tokens_total == 20

    def test_effective_concurrency_reads_width_under_true_parallelism(self) -> None:
        # Four streams, each 40 tokens in 1s (40 tok/s per stream), whole batch
        # done in 1s => 160 tok/s aggregate => effective concurrency 4.
        run = self._run(width=4)
        run.records = [_record(latency_seconds=1.0, completion_tokens=40) for _ in range(4)]
        run.batch_elapsed_seconds = [1.0]
        assert run.summary().effective_concurrency == pytest.approx(4.0)

    def test_effective_concurrency_reads_one_under_pure_queueing(self) -> None:
        # Same four streams, but the server served them one after another: the
        # batch took 4s, so aggregate is 40 tok/s and the ratio collapses to 1.
        run = self._run(width=4)
        run.records = [_record(latency_seconds=1.0, completion_tokens=40) for _ in range(4)]
        run.batch_elapsed_seconds = [4.0]
        assert run.summary().effective_concurrency == pytest.approx(1.0)

    def test_a_cell_with_no_successful_call_summarises_to_none_not_zero(self) -> None:
        run = self._run()
        run.records = [
            _record(
                ok=False,
                error="boom",
                prompt_tokens=None,
                completion_tokens=None,
                latency_seconds=None,
                tokens_per_second=None,
                residual_seconds=None,
                residual_fraction=None,
                modelled_decode_seconds=None,
            )
        ]
        run.batch_elapsed_seconds = [1.0]
        summary = run.summary()
        assert summary.prompt_tokens_mean is None
        assert summary.latency_seconds_mean is None
        assert summary.effective_concurrency is None


class TestPercentile:
    def test_matches_linear_interpolation(self) -> None:
        assert wso._percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
        assert wso._percentile([1, 2, 3, 4], 95) == pytest.approx(3.85)

    def test_degenerate_inputs(self) -> None:
        assert wso._percentile([], 95) == 0.0
        assert wso._percentile([7.0], 95) == 7.0


# ── criterion 4: the derived answers ─────────────────────────────────────────


def _summary(**overrides: Any) -> wso.CellSummary:
    kwargs: dict[str, Any] = dict(
        cell="c",
        budget="scoped",
        thinking="off",
        context=wso.CONTEXT_LEAN,
        width=1,
        max_tokens=wso.SCOPED_MAX_TOKENS,
        batches=1,
        calls=1,
        ok=1,
        errors=0,
        timeouts=0,
        truncated=0,
        answered=1,
        finish_reasons={"stop": 1},
        prompt_tokens_mean=200.0,
        prompt_tokens_median=200.0,
        prompt_tokens_total=200,
        completion_tokens_mean=40.0,
        completion_tokens_median=40.0,
        completion_tokens_total=40,
        latency_seconds_mean=1.0,
        latency_seconds_median=1.0,
        latency_seconds_p95=1.0,
        per_stream_tokens_per_second_mean=40.0,
        aggregate_seconds=1.0,
        aggregate_tokens_per_second=40.0,
        effective_concurrency=1.0,
        seconds_per_call_wallclock=1.0,
        calls_per_second=1.0,
        residual_seconds_mean=0.5,
        residual_seconds_median=0.5,
        residual_fraction_mean=0.5,
        residual_fraction_median=0.5,
        content_chars_total=30,
        reasoning_chars_total=0,
    )
    kwargs.update(overrides)
    return wso.CellSummary(**kwargs)


class TestConcurrencyTransfer:
    def test_a_full_transfer_reads_ratio_one(self) -> None:
        width1 = _summary(cell="w1", calls_per_second=1.0)
        width8 = _summary(
            cell="w8",
            width=8,
            calls_per_second=8.0,
            effective_concurrency=wso.REFERENCE_WIDTH8_EFFECTIVE_CONCURRENCY,
        )
        row = wso.concurrency_transfer(width1, width8)
        assert row["measured"] is True
        assert row["effective_concurrency_transfer_ratio"] == pytest.approx(1.0)
        assert row["call_throughput_speedup"] == pytest.approx(8.0)
        assert row["call_throughput_efficiency"] == pytest.approx(1.0)

    def test_a_collapsed_transfer_is_visible_as_a_small_ratio(self) -> None:
        width1 = _summary(cell="w1", calls_per_second=1.0)
        width8 = _summary(cell="w8", width=8, calls_per_second=1.5, effective_concurrency=1.2)
        row = wso.concurrency_transfer(width1, width8)
        # Reported values are rounded to four places on their way out, so the
        # tolerance is absolute rather than relative — the rounding is part of
        # the contract, not noise to be asserted around.
        assert row["effective_concurrency_transfer_ratio"] == pytest.approx(1.2 / 6.14, abs=5e-5)
        assert row["call_throughput_speedup"] == pytest.approx(1.5)
        assert row["call_throughput_efficiency"] == pytest.approx(1.5 / 8)

    def test_the_two_readings_can_come_apart_which_is_why_both_are_reported(self) -> None:
        # Token-throughput concurrency can look healthy while the thing a sweep
        # actually buys — answers per second — barely moves.
        width1 = _summary(cell="w1", calls_per_second=1.0)
        width8 = _summary(cell="w8", width=8, calls_per_second=2.0, effective_concurrency=6.0)
        row = wso.concurrency_transfer(width1, width8)
        assert row["effective_concurrency_transfer_ratio"] == pytest.approx(6.0 / 6.14, abs=5e-5)
        assert row["call_throughput_speedup"] == pytest.approx(2.0)

    def test_an_absent_cell_yields_measured_false_not_a_number(self) -> None:
        assert wso.concurrency_transfer(None, _summary())["measured"] is False
        assert wso.concurrency_transfer(_summary(), None)["measured"] is False


class TestPrefillCost:
    def test_the_implied_prefill_rate_is_a_difference_of_differences(self) -> None:
        lean = _summary(
            cell="lean", prompt_tokens_mean=240.0, latency_seconds_mean=0.4, calls_per_second=2.4
        )
        rich = _summary(
            cell="rich", prompt_tokens_mean=1040.0, latency_seconds_mean=0.9, calls_per_second=1.2
        )
        row = wso.prefill_cost(lean, rich)
        assert row["extra_prompt_tokens"] == pytest.approx(800.0)
        assert row["extra_latency_seconds"] == pytest.approx(0.5)
        assert row["implied_prefill_tokens_per_second"] == pytest.approx(1600.0)
        assert row["throughput_ratio_rich_over_lean"] == pytest.approx(0.5)

    def test_no_extra_latency_yields_no_implied_rate_rather_than_infinity(self) -> None:
        lean = _summary(cell="lean", prompt_tokens_mean=240.0, latency_seconds_mean=0.5)
        rich = _summary(cell="rich", prompt_tokens_mean=1040.0, latency_seconds_mean=0.5)
        assert wso.prefill_cost(lean, rich)["implied_prefill_tokens_per_second"] is None

    def test_an_absent_cell_is_absent_not_a_number(self) -> None:
        assert wso.prefill_cost(None, _summary())["measured"] is False
        assert wso.prefill_cost(_summary(), None)["measured"] is False


class TestThinkingCost:
    def test_the_multiples_are_computed_both_ways(self) -> None:
        off = _summary(
            cell="off", completion_tokens_mean=15.0, latency_seconds_mean=0.5, answered=6, ok=6
        )
        on = _summary(
            cell="on",
            completion_tokens_mean=1500.0,
            latency_seconds_mean=20.0,
            answered=2,
            ok=4,
            truncated=2,
        )
        row = wso.thinking_cost(off, on)
        assert row["completion_token_multiple"] == pytest.approx(100.0)
        assert row["latency_multiple"] == pytest.approx(40.0)
        assert row["off_answered"] == "6/6"
        assert row["on_answered"] == "2/4"
        assert row["on_truncated"] == 2

    def test_a_zero_baseline_yields_no_multiple_rather_than_a_division_error(self) -> None:
        off = _summary(cell="off", completion_tokens_mean=0.0, latency_seconds_mean=0.0)
        row = wso.thinking_cost(off, _summary(cell="on"))
        assert row["completion_token_multiple"] is None
        assert row["latency_multiple"] is None

    def test_an_absent_cell_is_absent_not_a_number(self) -> None:
        assert wso.thinking_cost(None, _summary())["measured"] is False
        assert wso.thinking_cost(_summary(), None)["measured"] is False


class TestScopedCallsPerCortexTurn:
    def test_the_wall_clock_and_token_arithmetic(self) -> None:
        width8 = _summary(
            cell="w8",
            width=8,
            seconds_per_call_wallclock=0.5,
            completion_tokens_mean=40.0,
            prompt_tokens_mean=1700.0,
        )
        row = wso.scoped_calls_per_cortex_turn(width8)
        assert row["measured"] is True
        assert row["scoped_calls_per_cortex_turn_wallclock_low"] == pytest.approx(
            wso.CORTEX_TURN_SECONDS_LOW / 0.5
        )
        assert row["scoped_calls_per_cortex_turn_wallclock_high"] == pytest.approx(
            wso.CORTEX_TURN_SECONDS_HIGH / 0.5
        )
        assert row["scoped_calls_per_cortex_turn_completion_tokens_low"] == pytest.approx(
            wso.CORTEX_TURN_TOKENS_LOW / 40.0
        )
        assert row["prompt_tokens_per_scoped_call"] == pytest.approx(1700.0)

    def test_both_ends_of_the_cortex_range_are_reported_never_a_midpoint(self) -> None:
        row = wso.scoped_calls_per_cortex_turn(_summary(seconds_per_call_wallclock=1.0))
        assert row["cortex_turn_seconds_range"] == [
            wso.CORTEX_TURN_SECONDS_LOW,
            wso.CORTEX_TURN_SECONDS_HIGH,
        ]
        assert row["cortex_turn_completion_tokens_range"] == [
            wso.CORTEX_TURN_TOKENS_LOW,
            wso.CORTEX_TURN_TOKENS_HIGH,
        ]

    def test_an_absent_cell_yields_absent_not_a_number(self) -> None:
        assert wso.scoped_calls_per_cortex_turn(None)["measured"] is False
        assert (
            wso.scoped_calls_per_cortex_turn(_summary(seconds_per_call_wallclock=None))["measured"]
            is False
        )

    def test_the_cortex_reference_is_the_specs_own_measured_range(self) -> None:
        assert (wso.CORTEX_TURN_TOKENS_LOW, wso.CORTEX_TURN_TOKENS_HIGH) == (5000, 14265)
        assert (wso.CORTEX_TURN_SECONDS_LOW, wso.CORTEX_TURN_SECONDS_HIGH) == (400.0, 730.0)


# ── the probe's own scheduling ───────────────────────────────────────────────


class TestRunProbe:
    def _fake(self) -> Any:
        seen: list[wso.CallSpec] = []

        def call_fn(config: WorkerConfig, spec: wso.CallSpec) -> wso.CallRecord:
            seen.append(spec)
            return _record(
                cell=spec.cell,
                width=spec.width,
                batch=spec.batch,
                slot=spec.slot,
                situation_id=spec.situation_id,
                context=spec.context,
                thinking=spec.thinking,
                max_tokens=spec.max_tokens,
                warmup=spec.warmup,
            )

        return call_fn, seen

    def test_the_warmup_is_exactly_one_call_and_is_marked(self) -> None:
        call_fn, seen = self._fake()
        result = wso.run_probe(_config(), cells=wso.CELLS[:1], call_fn=call_fn)
        assert result.warmup is not None
        assert result.warmup.warmup is True
        assert sum(1 for spec in seen if spec.warmup) == 1
        assert result.warmup not in result.cells[0].records

    def test_no_warmup_means_no_extra_call(self) -> None:
        call_fn, seen = self._fake()
        cell = wso.CELLS[0]
        wso.run_probe(_config(), cells=(cell,), call_fn=call_fn, warmup=False)
        assert len(seen) == cell.calls

    def test_every_cell_runs_its_declared_number_of_calls(self) -> None:
        call_fn, _seen = self._fake()
        result = wso.run_probe(_config(), cells=wso.CELLS, call_fn=call_fn, warmup=False)
        for run in result.cells:
            assert len(run.records) == run.cell.calls
            assert len(run.batch_elapsed_seconds) == run.cell.batches

    def test_the_situation_cursor_never_repeats_inside_one_batch(self) -> None:
        call_fn, seen = self._fake()
        wide = [cell for cell in wso.CELLS if cell.width == 8]
        wso.run_probe(_config(), cells=tuple(wide), call_fn=call_fn, warmup=False)
        by_batch: dict[tuple[str, int], list[int]] = {}
        for spec in seen:
            by_batch.setdefault((spec.cell, spec.batch), []).append(spec.situation_id)
        for key, ids in by_batch.items():
            assert len(ids) == len(set(ids)), f"{key} repeated a situation inside one batch"

    def test_the_summary_carries_every_cell_and_both_derived_answers(self) -> None:
        call_fn, _seen = self._fake()
        result = wso.run_probe(_config(), cells=wso.CELLS, call_fn=call_fn, warmup=False)
        summary = result.to_summary_dict()
        assert len(summary["cells"]) == len(wso.CELLS)
        assert set(summary["concurrency_transfer"]) == {"lean", "rich"}
        assert summary["scoped_calls_per_cortex_turn"]["measured"] is True
        assert summary["reference"]["measured_on_completion_tokens"] == 1200

    def test_the_declared_window_rides_into_the_summary(self) -> None:
        call_fn, _seen = self._fake()
        window = {"declared": True, "checks": [{"label": "before", "verdict": "idle"}]}
        result = wso.run_probe(
            _config(), cells=wso.CELLS[:1], call_fn=call_fn, warmup=False, window=window
        )
        assert result.to_summary_dict()["window"] == window

    def test_summary_for_an_unknown_cell_is_none(self) -> None:
        call_fn, _seen = self._fake()
        result = wso.run_probe(_config(), cells=wso.CELLS[:1], call_fn=call_fn, warmup=False)
        assert result.summary_for("nope") is None


class TestBatchOverrides:
    def test_parses_pairs_and_ignores_blanks(self) -> None:
        assert wso.parse_batch_overrides("a:8, b:4 ,") == {"a": 8, "b": 4}
        assert wso.parse_batch_overrides(None) == {}
        assert wso.parse_batch_overrides("") == {}

    def test_an_override_changes_only_the_batch_count(self) -> None:
        cell = wso.CELLS[0]
        (overridden,) = wso.apply_batch_overrides((cell,), {cell.id: 9})
        assert overridden.batches == 9
        assert overridden.calls == cell.width * 9
        # Everything that defines the *condition* is untouched.
        assert (overridden.id, overridden.width, overridden.max_tokens, overridden.thinking) == (
            cell.id,
            cell.width,
            cell.max_tokens,
            cell.thinking,
        )

    def test_the_committed_cell_table_is_never_mutated(self) -> None:
        before = [cell.batches for cell in wso.CELLS]
        wso.apply_batch_overrides(wso.CELLS, {cell.id: 99 for cell in wso.CELLS})
        assert [cell.batches for cell in wso.CELLS] == before

    def test_an_override_for_an_unselected_cell_is_harmless(self) -> None:
        cells = wso.apply_batch_overrides(wso.CELLS[:1], {"not-a-cell": 3})
        assert cells[0].batches == wso.CELLS[0].batches

    def test_the_summary_states_the_n_it_was_measured_at(self) -> None:
        def call_fn(config: WorkerConfig, spec: wso.CallSpec) -> wso.CallRecord:
            return _record(cell=spec.cell, width=spec.width, batch=spec.batch, slot=spec.slot)

        cells = wso.apply_batch_overrides(wso.CELLS[:1], {wso.CELLS[0].id: 3})
        result = wso.run_probe(_config(), cells=cells, call_fn=call_fn, warmup=False)
        summary = result.cells[0].summary()
        assert summary.batches == 3
        assert summary.calls == 3 * wso.CELLS[0].width


class TestRetryContamination:
    def _run(self, retried_batch: Optional[int]) -> wso.CellRun:
        cell = wso.Cell(
            id="c",
            budget="scoped",
            thinking="off",
            context=wso.CONTEXT_LEAN,
            width=2,
            max_tokens=wso.SCOPED_MAX_TOKENS,
            batches=3,
            why="test",
        )
        run = wso.CellRun(cell=cell)
        for batch in range(3):
            for slot in range(2):
                retried = batch == retried_batch and slot == 1
                run.records.append(
                    _record(
                        batch=batch,
                        slot=slot,
                        retries=1 if retried else 0,
                        latency_seconds=21.0 if retried else 1.0,
                    )
                )
            run.batch_elapsed_seconds.append(21.0 if batch == retried_batch else 1.0)
        return run

    def test_the_backoff_constant_matches_the_seam_it_describes(self) -> None:
        # If worker_seam's backoff moves and this constant does not, every
        # published ratio silently changes meaning.
        assert wso.RETRY_BACKOFF_SECONDS == ws.RETRY_SLEEP_SECONDS

    def test_retry_batches_names_only_contaminated_batches(self) -> None:
        assert wso.retry_batches(self._run(1)) == (1,)
        assert wso.retry_batches(self._run(None)) == ()

    def test_a_contaminated_batch_is_dropped_whole_not_call_by_call(self) -> None:
        # Dropping only the retried call would leave its neighbours measured
        # against a batch clock that still contained the 20s sleep.
        clean = wso.without_retry_batches(self._run(1))
        assert {record.batch for record in clean.records} == {0, 2}
        assert clean.batch_elapsed_seconds == [1.0, 1.0]
        assert clean.summary().calls == 4
        assert clean.summary().batches == 3, "the cell's declared design is unchanged"

    def test_dropping_the_contaminated_batch_restores_the_per_call_clock(self) -> None:
        dirty = self._run(1).summary()
        clean = wso.without_retry_batches(self._run(1)).summary()
        assert dirty.seconds_per_call_wallclock == pytest.approx(23.0 / 6)
        assert clean.seconds_per_call_wallclock == pytest.approx(2.0 / 4)
        assert clean.calls_per_second is not None
        assert dirty.calls_per_second is not None
        assert clean.calls_per_second > dirty.calls_per_second

    def test_a_clean_run_is_returned_unchanged(self) -> None:
        run = self._run(None)
        clean = wso.without_retry_batches(run)
        assert len(clean.records) == len(run.records)
        assert clean.batch_elapsed_seconds == run.batch_elapsed_seconds

    def test_transport_summary_reports_the_rate_and_the_backoff_ratio(self) -> None:
        records = self._run(1).records
        transport = wso.transport_summary(records)
        assert transport["calls"] == 6
        assert transport["calls_with_retries"] == 1
        assert transport["retry_rate"] == pytest.approx(1 / 6, abs=5e-5)
        assert transport["median_healthy_latency_seconds"] == pytest.approx(1.0)
        assert transport["backoff_over_median_healthy_latency"] == pytest.approx(20.0)
        assert transport["retried_calls"][0]["batch"] == 1

    def test_transport_summary_on_a_clean_run_reports_zero_not_none(self) -> None:
        transport = wso.transport_summary(self._run(None).records)
        assert transport["calls_with_retries"] == 0
        assert transport["retry_rate"] == 0.0
        assert transport["retried_calls"] == []

    def test_transport_summary_of_nothing_does_not_divide_by_zero(self) -> None:
        transport = wso.transport_summary([])
        assert transport["calls"] == 0
        assert transport["retry_rate"] is None
        assert transport["backoff_over_median_healthy_latency"] is None

    def test_the_summary_publishes_both_readings_never_only_the_clean_one(self) -> None:
        def call_fn(config: WorkerConfig, spec: wso.CallSpec) -> wso.CallRecord:
            retried = spec.batch == 0 and spec.slot == 0
            return _record(
                cell=spec.cell,
                width=spec.width,
                batch=spec.batch,
                slot=spec.slot,
                retries=1 if retried else 0,
            )

        result = wso.run_probe(_config(), cells=wso.CELLS, call_fn=call_fn, warmup=False)
        summary = result.to_summary_dict()
        assert summary["transport"]["calls_with_retries"] > 0
        assert summary["retry_contaminated_batches"], "contaminated batches must be named"
        assert summary["cells_excluding_retry_batches"], "the second reading must be present"
        # The as-measured cells are still there, unmodified.
        assert len(summary["cells"]) == len(wso.CELLS)
        assert "concurrency_transfer" in summary
        assert "concurrency_transfer_excluding_retry_batches" in summary

    def test_a_clean_probe_publishes_no_exclusion_tables(self) -> None:
        def call_fn(config: WorkerConfig, spec: wso.CallSpec) -> wso.CallRecord:
            return _record(cell=spec.cell, width=spec.width, batch=spec.batch, slot=spec.slot)

        result = wso.run_probe(_config(), cells=wso.CELLS[:2], call_fn=call_fn, warmup=False)
        summary = result.to_summary_dict()
        assert summary["retry_contaminated_batches"] == {}
        assert summary["cells_excluding_retry_batches"] == []


class TestRebuildFromArtifacts:
    def test_a_committed_run_round_trips_to_identical_tables(self, tmp_path: Path) -> None:
        def call_fn(config: WorkerConfig, spec: wso.CallSpec) -> wso.CallRecord:
            return _record(
                cell=spec.cell,
                width=spec.width,
                batch=spec.batch,
                slot=spec.slot,
                situation_id=spec.situation_id,
                context=spec.context,
                thinking=spec.thinking,
                max_tokens=spec.max_tokens,
                warmup=spec.warmup,
                retries=1 if (spec.cell == wso.CELLS[0].id and spec.batch == 0) else 0,
            )

        original = wso.run_probe(_config(), cells=wso.CELLS, call_fn=call_fn, warmup=True)
        # The fake scheduler's real batch clocks are sub-microsecond, and
        # `batch_elapsed_seconds` is committed rounded to 4 places — so a
        # degenerate clock would fail this round trip on rounding alone and
        # prove nothing. Realistic clocks make the assertion about the rebuild.
        for run in original.cells:
            run.batch_elapsed_seconds = [
                1.5 + index for index in range(len(run.batch_elapsed_seconds))
            ]
        summary = original.to_summary_dict()
        records_path, summary_path = tmp_path / "r.jsonl", tmp_path / "s.json"
        records_path.write_text(
            "".join(json.dumps(r.to_dict()) + "\n" for r in original.all_records()),
            encoding="utf-8",
        )
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        rebuilt = wso.rebuild_from_artifacts(records_path, summary_path)
        assert wso.render_markdown(rebuilt.to_summary_dict()) == wso.render_markdown(summary)

    def test_the_warmup_is_restored_as_a_warmup_not_as_a_cell_record(self, tmp_path: Path) -> None:
        def call_fn(config: WorkerConfig, spec: wso.CallSpec) -> wso.CallRecord:
            return _record(cell=spec.cell, width=spec.width, warmup=spec.warmup)

        original = wso.run_probe(_config(), cells=wso.CELLS[:1], call_fn=call_fn, warmup=True)
        records_path, summary_path = tmp_path / "r.jsonl", tmp_path / "s.json"
        records_path.write_text(
            "".join(json.dumps(r.to_dict()) + "\n" for r in original.all_records()),
            encoding="utf-8",
        )
        summary_path.write_text(json.dumps(original.to_summary_dict()), encoding="utf-8")

        rebuilt = wso.rebuild_from_artifacts(records_path, summary_path)
        assert rebuilt.warmup is not None
        assert rebuilt.warmup.warmup is True
        assert all(not r.warmup for run in rebuilt.cells for r in run.records)

    def test_a_record_the_summary_does_not_describe_refuses_rather_than_vanishes(
        self, tmp_path: Path
    ) -> None:
        records_path, summary_path = tmp_path / "r.jsonl", tmp_path / "s.json"
        records_path.write_text(
            json.dumps(_record(cell="ghost").to_dict()) + "\n", encoding="utf-8"
        )
        summary_path.write_text(
            json.dumps({"worker": {"base_url": "http://x/v1", "model": "m"}, "cells": []}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="ghost"):
            wso.rebuild_from_artifacts(records_path, summary_path)

    def test_the_api_key_is_never_reconstructed(self, tmp_path: Path) -> None:
        records_path, summary_path = tmp_path / "r.jsonl", tmp_path / "s.json"
        records_path.write_text("", encoding="utf-8")
        summary_path.write_text(
            json.dumps({"worker": {"base_url": "http://x/v1", "model": "m"}, "cells": []}),
            encoding="utf-8",
        )
        rebuilt = wso.rebuild_from_artifacts(records_path, summary_path)
        assert rebuilt.config.api_key == ""
        assert rebuilt.config.model == "m"

    def test_the_cli_rebuild_path_never_needs_a_dial_or_a_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
    ) -> None:
        monkeypatch.delenv(ws.WORKER_URL_ENV, raising=False)
        monkeypatch.delenv(ws.API_KEY_ENV, raising=False)
        monkeypatch.setattr(wso, "run_probe", lambda *a, **k: pytest.fail("rebuild must not dial"))

        def call_fn(config: WorkerConfig, spec: wso.CallSpec) -> wso.CallRecord:
            return _record(cell=spec.cell, width=spec.width, batch=spec.batch, slot=spec.slot)

        seeded = wso.CellRun(cell=wso.CELLS[0])
        seeded.records = [_record(cell=wso.CELLS[0].id)]
        seeded.batch_elapsed_seconds = [1.0]
        seed = wso.ProbeResult(config=_config(), temperature=0.3, warmup=None, cells=[seeded])
        records_path, summary_path = tmp_path / "r.jsonl", tmp_path / "s.json"
        records_path.write_text(
            "".join(json.dumps(r.to_dict()) + "\n" for r in seed.all_records()), encoding="utf-8"
        )
        summary_path.write_text(json.dumps(seed.to_summary_dict()), encoding="utf-8")

        md_out = tmp_path / "t.md"
        code = wso.main(
            ["--rebuild-from", str(records_path), str(summary_path), "--markdown-out", str(md_out)]
        )
        capsys.readouterr()
        assert code == 0
        assert "### Table 1" in md_out.read_text(encoding="utf-8")

    def test_a_missing_artifact_is_a_user_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
    ) -> None:
        assert wso.main(["--rebuild-from", str(tmp_path / "nope.jsonl"), str(tmp_path / "no.json")])
        capsys.readouterr()


# ── criterion 8: every committed table is generated ──────────────────────────


class TestRenderMarkdown:
    def _summary_dict(self) -> dict[str, Any]:
        def call_fn(config: WorkerConfig, spec: wso.CallSpec) -> wso.CallRecord:
            return _record(
                cell=spec.cell,
                width=spec.width,
                batch=spec.batch,
                slot=spec.slot,
                context=spec.context,
                thinking=spec.thinking,
                max_tokens=spec.max_tokens,
            )

        result = wso.run_probe(_config(), cells=wso.CELLS, call_fn=call_fn, warmup=False)
        return result.to_summary_dict()

    def test_every_cell_gets_a_row_in_the_cost_table(self) -> None:
        text = wso.render_markdown(self._summary_dict())
        for cell in wso.CELLS:
            assert f"`{cell.id}`" in text

    def test_all_five_tables_render(self) -> None:
        text = wso.render_markdown(self._summary_dict())
        for heading in (
            "Table 1",
            "Table 2",
            "Table 3",
            "Table 4",
            "Table 5",
            "Table 6",
            "Table 7",
            "Table 8",
        ):
            assert f"### {heading}" in text

    def test_the_output_says_it_is_generated(self) -> None:
        assert "generated by examples/worker_scoped_overhead.py" in wso.render_markdown(
            self._summary_dict()
        )

    def test_the_reference_rate_is_quoted_in_the_residual_table(self) -> None:
        text = wso.render_markdown(self._summary_dict())
        assert "76.43 tok/s" in text
        assert "worker-throughput.md" in text

    def test_an_absent_derived_answer_renders_as_absent_not_a_blank_number(self) -> None:
        summary = self._summary_dict()
        summary["scoped_calls_per_cortex_turn"] = {"measured": False, "why": "the cell is absent"}
        summary["concurrency_transfer"] = {"lean": {"measured": False}}
        text = wso.render_markdown(summary)
        assert "**ABSENT**" in text
        assert "| ABSENT |" in text

    def test_none_renders_as_a_dash_never_as_zero(self) -> None:
        assert wso._fmt(None) == "—"
        assert wso._pct(None) == "—"
        assert wso._fmt(0.0) == "0.00"


# ── the CLI ──────────────────────────────────────────────────────────────────


class TestCLI:
    def test_absent_config_exits_two_and_never_dials(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.delenv(ws.WORKER_URL_ENV, raising=False)
        monkeypatch.delenv(ws.WORKER_MODEL_ENV, raising=False)
        monkeypatch.delenv(ws.API_KEY_ENV, raising=False)
        monkeypatch.setattr(
            wso, "run_probe", lambda *a, **k: pytest.fail("must not dial without config")
        )
        assert wso.main([]) == 2
        assert json.loads(capsys.readouterr().out)["ok"] is False

    def test_an_unknown_cell_id_is_a_user_error_not_a_silent_empty_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ws.WORKER_URL_ENV, "http://example.invalid/v1")
        monkeypatch.setenv(ws.WORKER_MODEL_ENV, "m")
        monkeypatch.setenv(ws.API_KEY_ENV, "test-key-not-a-real-credential")
        monkeypatch.setattr(
            wso, "run_probe", lambda *a, **k: pytest.fail("must not dial on a bad cell id")
        )
        assert wso.main(["--cells", "no-such-cell"]) == 1

    def test_artifacts_are_written_where_asked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
    ) -> None:
        monkeypatch.setenv(ws.WORKER_URL_ENV, "http://example.invalid/v1")
        monkeypatch.setenv(ws.WORKER_MODEL_ENV, "m")
        monkeypatch.setenv(ws.API_KEY_ENV, "test-key-not-a-real-credential")

        def fake_probe(config: WorkerConfig, **kwargs: Any) -> wso.ProbeResult:
            run = wso.CellRun(cell=wso.CELLS[0])
            run.records = [_record(cell=wso.CELLS[0].id)]
            run.batch_elapsed_seconds = [1.0]
            return wso.ProbeResult(
                config=config, temperature=0.3, warmup=None, cells=[run], window=kwargs["window"]
            )

        monkeypatch.setattr(wso, "run_probe", fake_probe)
        out, summary_out, md_out = (
            tmp_path / "r.jsonl",
            tmp_path / "s.json",
            tmp_path / "t.md",
        )
        code = wso.main(
            [
                "--out",
                str(out),
                "--summary-out",
                str(summary_out),
                "--markdown-out",
                str(md_out),
            ]
        )
        assert code == 0
        capsys.readouterr()
        assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 1
        assert json.loads(summary_out.read_text(encoding="utf-8"))["kind"] == (
            "worker-scoped-overhead"
        )
        assert "### Table 1" in md_out.read_text(encoding="utf-8")

    def test_a_missing_window_log_is_recorded_as_undeclared_not_invented(self) -> None:
        assert wso._load_window(None)["declared"] is False
        assert wso._load_window("/nonexistent/window.jsonl")["declared"] is False

    def test_a_real_window_log_is_summarised(self, tmp_path: Path) -> None:
        log = tmp_path / "window.jsonl"
        log.write_text(
            json.dumps({"label": "before", "at": "2026-08-01T10:00:00+03:00", "verdict": "idle"})
            + "\n",
            encoding="utf-8",
        )
        window = wso._load_window(str(log))
        assert window["declared"] is True
        assert window["checks"] == [
            {"label": "before", "at": "2026-08-01T10:00:00+03:00", "verdict": "idle"}
        ]


# ── the harness's own termination discipline ─────────────────────────────────


class TestNoUnboundedLoops:
    def test_the_module_contains_no_while_statement(self) -> None:
        # Structural, matching this repo's habit of proving termination by AST
        # rather than by behaviour: the fan-out is bounded by one finite
        # `concurrent.futures.wait`, and a `while` would be the way that
        # guarantee quietly stopped holding.
        import ast

        source = (REPO_ROOT / "examples" / "worker_scoped_overhead.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert not [node for node in ast.walk(tree) if isinstance(node, ast.While)]
