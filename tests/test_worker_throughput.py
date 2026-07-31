"""Task t3 — worker throughput and effective concurrency at widths 1, 2, 8, 14.

Everything below except :class:`TestLiveWorkerThroughputSmoke` is hermetic: no
test in this module reaches a socket unless ``EMBODIMENT_LIVE_RIG=1`` is set,
matching the convention `tests/test_worker_seam.py` and every other
live-gated module in this repo pins.

What each acceptance criterion needs proved, and where:

1. **Per-stream vs aggregate tok/s, never conflated** —
   :class:`TestSummarizeWidth` computes both from synthetic records with a
   known answer; :class:`TestWidthRunEffectiveConcurrency` proves the
   ``effective_concurrency`` ratio reads ``≈ width`` under true parallelism and
   ``≈ 1`` under queueing.
2. **Reasoning tokens carried apart from content tokens** —
   :class:`TestReasoningTokensFrom` (mirrors `tests/test_arch_arms.py`'s tests
   for the function of the same name) and :class:`TestThroughputSeamCapturesRawPayload`.
3. **A fixed, committed prompt set** — :class:`TestPromptSet`.
4. **Warm-up policy is real and excludes exactly one call** —
   :class:`TestRunSweepWarmup`.
5. **Sustained-load stability: errors, timeouts, truncations are recorded, not
   swallowed** — :class:`TestOneCallNeverRaises`, :class:`TestRunBatchTimeout`.
6. **The batch really does run concurrently** (a fan-out proved only against a
   fake scheduler proves nothing about the one it runs on) —
   :class:`TestRunBatchIsReallyConcurrent`, using the same
   `threading.Barrier` technique `tests/test_orchestrator_tools.py` uses for
   the same reason.
7. **The operator's "50 tok/s x 14" figure is checked both ways, not decided
   for the reader** — :class:`TestCheckOperatorClaim`.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples import worker_seam as ws  # noqa: E402
from examples import worker_throughput as wt  # noqa: E402
from examples.worker_seam import WorkerConfig  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def _config(**overrides: Any) -> WorkerConfig:
    kwargs: dict[str, Any] = dict(base_url="http://example.invalid/v1", model="m", api_key="k")
    kwargs.update(overrides)
    return WorkerConfig(**kwargs)


# ── criterion 3: the fixed, committed prompt set ──────────────────────────────


class TestPromptSet:
    def test_prompt_set_is_non_empty_and_every_entry_is_a_non_empty_string(self) -> None:
        assert len(wt.PROMPT_SET) >= 4
        for prompt in wt.PROMPT_SET:
            assert isinstance(prompt, str)
            assert prompt.strip()

    def test_prompt_set_entries_are_distinct(self) -> None:
        # Distinct prompts avoid prefix-cache skew across one concurrent batch.
        assert len(set(wt.PROMPT_SET)) == len(wt.PROMPT_SET)

    def test_default_widths_and_batches_cover_every_width(self) -> None:
        for width in wt.DEFAULT_WIDTHS:
            assert width in wt.DEFAULT_BATCHES_PER_WIDTH
            assert wt.DEFAULT_BATCHES_PER_WIDTH[width] >= 1

    def test_operator_claim_widths_is_one_of_the_swept_widths(self) -> None:
        assert wt.OPERATOR_CLAIM_WIDTH in wt.DEFAULT_WIDTHS


# ── criterion 2: reasoning tokens read apart from content tokens ─────────────


class TestReasoningTokensFrom:
    def test_nested_completion_tokens_details_wins(self) -> None:
        usage = {"completion_tokens_details": {"reasoning_tokens": 420}, "reasoning_tokens": 1}
        value, source = wt.reasoning_tokens_from(usage)
        assert value == 420
        assert source == wt.TOKEN_DETAIL_USAGE_DETAILS

    def test_flat_reasoning_tokens_is_read_when_nested_is_absent(self) -> None:
        value, source = wt.reasoning_tokens_from({"reasoning_tokens": 99})
        assert value == 99
        assert source == wt.TOKEN_DETAIL_USAGE_FLAT

    def test_absence_is_none_not_zero(self) -> None:
        value, source = wt.reasoning_tokens_from({})
        assert value is None
        assert source == wt.TOKEN_DETAIL_ABSENT

    def test_non_integer_nested_value_is_ignored_and_falls_through(self) -> None:
        usage = {"completion_tokens_details": {"reasoning_tokens": "lots"}}
        value, source = wt.reasoning_tokens_from(usage)
        assert value is None
        assert source == wt.TOKEN_DETAIL_ABSENT


# ── criterion 2: the raw payload capture ──────────────────────────────────────


class TestThroughputSeamCapturesRawPayload:
    def test_last_payload_is_none_before_any_call(self) -> None:
        seam = wt.ThroughputSeam(base_url="http://x/v1", model="m", api_key="k", max_tokens=10)
        assert seam.last_payload is None

    def test_last_payload_is_the_raw_response_after_a_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Patched on the SUPERCLASS, not the instance and not `ThroughputSeam`
        # itself: `ThroughputSeam._post` must still run (it is what captures
        # `last_payload`), and its own body calls `super()._post(body)` — an
        # instance-level `seam._post = ...` would shadow the override
        # entirely, proving nothing about what this subclass adds.
        seam = wt.ThroughputSeam(base_url="http://x/v1", model="m", api_key="k", max_tokens=10)
        canned = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 50,
                "completion_tokens_details": {"reasoning_tokens": 40},
            },
        }
        monkeypatch.setattr(ws.WorkerSeam, "_post", lambda self, body: canned)
        reply = seam(messages=[{"role": "user", "content": "hi"}])

        assert reply.content == "ok"
        assert seam.last_payload == canned
        assert seam.last_payload is not None
        assert seam.last_payload["usage"]["completion_tokens_details"]["reasoning_tokens"] == 40
        # The base class's own metering still works unchanged (reuse, not reimplementation).
        assert seam.meter.calls == 1
        assert seam.meter.prompt_tokens == 5


# ── _one_call: never raises, records reasoning tokens and truncation ─────────


class TestOneCallNeverRaises:
    def _spec(self, **overrides: Any) -> wt.CallSpec:
        kwargs: dict[str, Any] = dict(width=1, batch=0, slot=0, prompt_id=0, prompt="hello")
        kwargs.update(overrides)
        return wt.CallSpec(**kwargs)

    def test_a_successful_call_records_tokens_reasoning_and_latency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_post(self: ws.WorkerSeam, body: dict[str, Any]) -> dict[str, Any]:
            return {
                "choices": [{"message": {"content": "hi there"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 120,
                    "completion_tokens_details": {"reasoning_tokens": 100},
                },
            }

        # Patched on the SUPERCLASS -- see the note in
        # TestThroughputSeamCapturesRawPayload for why patching
        # `ThroughputSeam._post` directly would silently drop `last_payload`.
        monkeypatch.setattr(ws.WorkerSeam, "_post", fake_post)
        record = wt._one_call(_config(), self._spec(), max_tokens=200, temperature=0.1)

        assert record.ok is True
        assert record.error is None
        assert record.completion_tokens == 120
        assert record.reasoning_tokens == 100
        assert record.reasoning_token_source == wt.TOKEN_DETAIL_USAGE_DETAILS
        assert record.content_chars == len("hi there")
        assert record.finish_reason == "stop"
        assert record.truncated is False
        assert record.latency_seconds is not None and record.latency_seconds >= 0
        assert record.tokens_per_second is not None and record.tokens_per_second > 0

    def test_a_truncated_call_is_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_post(self: ws.WorkerSeam, body: dict[str, Any]) -> dict[str, Any]:
            return {
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 200},
            }

        monkeypatch.setattr(ws.WorkerSeam, "_post", fake_post)
        record = wt._one_call(_config(), self._spec(), max_tokens=200, temperature=0.1)

        assert record.ok is True
        assert record.truncated is True
        assert record.finish_reason == wt.FINISH_TRUNCATED

    def test_a_transport_failure_is_a_record_not_a_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.error

        def always_fails(self: wt.ThroughputSeam, body: dict[str, Any]) -> dict[str, Any]:
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(wt.ThroughputSeam, "_post", always_fails)
        record = wt._one_call(
            _config(),
            self._spec(),
            max_tokens=200,
            temperature=0.1,
            sleep=lambda _seconds: None,  # no real backoff in a hermetic test
        )

        assert record.ok is False
        assert record.error is not None
        assert "WorkerTransportError" in record.error or "URLError" in record.error
        assert record.completion_tokens is None
        assert record.tokens_per_second is None


# ── _percentile ─────────────────────────────────────────────────────────────


class TestPercentile:
    def test_empty_is_zero(self) -> None:
        assert wt._percentile([], 95) == 0.0

    def test_single_value_is_itself_at_any_percentile(self) -> None:
        assert wt._percentile([7.0], 50) == 7.0
        assert wt._percentile([7.0], 99) == 7.0

    def test_median_of_five_known_values(self) -> None:
        assert wt._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0

    def test_p95_of_known_values_matches_linear_interpolation(self) -> None:
        values = [float(i) for i in range(1, 11)]  # 1..10
        # rank = (10-1) * 0.95 = 8.55 -> interpolate between index 8 (9) and 9 (10)
        assert wt._percentile(values, 95) == pytest.approx(9.55)


# ── criterion 6: the batch really runs concurrently, and respects a deadline ─


class TestRunBatchIsReallyConcurrent:
    WIDTH = 6

    def test_all_calls_in_a_batch_are_in_flight_at_once(self) -> None:
        """Without this, the concurrency-scaling numbers this task exists to
        produce would be measuring a hidden serial loop instead."""
        barrier = threading.Barrier(self.WIDTH, timeout=15)

        def call_fn(config: WorkerConfig, spec: wt.CallSpec) -> wt.CallRecord:
            barrier.wait(timeout=10)
            return wt.CallRecord(
                width=spec.width,
                batch=spec.batch,
                slot=spec.slot,
                prompt_id=spec.prompt_id,
                warmup=spec.warmup,
                ok=True,
                error=None,
                latency_seconds=0.01,
                prompt_tokens=1,
                completion_tokens=1,
                reasoning_tokens=None,
                reasoning_token_source=wt.TOKEN_DETAIL_ABSENT,
                content_chars=1,
                reasoning_chars=0,
                finish_reason="stop",
                truncated=False,
                retries=0,
                tokens_per_second=100.0,
            )

        specs = [
            wt.CallSpec(width=self.WIDTH, batch=0, slot=i, prompt_id=i, prompt=f"p{i}")
            for i in range(self.WIDTH)
        ]
        try:
            records, elapsed = wt._run_batch(_config(), specs, call_fn=call_fn, timeout=12)
        finally:
            barrier.abort()

        assert len(records) == self.WIDTH
        assert all(record.ok for record in records)
        assert elapsed < 5.0, "a real barrier release should be near-instant, not serial"

    def test_records_come_back_in_spec_submission_order_not_completion_order(self) -> None:
        # Slot 0 finishes last on purpose; the result list must still be spec-ordered.
        release_first = threading.Event()

        def call_fn(config: WorkerConfig, spec: wt.CallSpec) -> wt.CallRecord:
            if spec.slot == 0:
                release_first.wait(timeout=5)
            else:
                release_first.set()
            return wt.CallRecord(
                width=spec.width,
                batch=spec.batch,
                slot=spec.slot,
                prompt_id=spec.prompt_id,
                warmup=spec.warmup,
                ok=True,
                error=None,
                latency_seconds=0.0,
                prompt_tokens=0,
                completion_tokens=0,
                reasoning_tokens=None,
                reasoning_token_source=wt.TOKEN_DETAIL_ABSENT,
                content_chars=0,
                reasoning_chars=0,
                finish_reason="stop",
                truncated=False,
                retries=0,
                tokens_per_second=None,
            )

        specs = [
            wt.CallSpec(width=3, batch=0, slot=i, prompt_id=i, prompt=f"p{i}") for i in range(3)
        ]
        records, _elapsed = wt._run_batch(_config(), specs, call_fn=call_fn, timeout=10)
        assert [record.slot for record in records] == [0, 1, 2]


class TestRunBatchTimeout:
    def test_a_hung_call_is_recorded_as_a_timeout_and_does_not_block_the_batch(self) -> None:
        gate = threading.Event()

        def call_fn(config: WorkerConfig, spec: wt.CallSpec) -> wt.CallRecord:
            if spec.slot == 0:
                gate.wait(timeout=5)  # released in `finally` below
            return wt.CallRecord(
                width=spec.width,
                batch=spec.batch,
                slot=spec.slot,
                prompt_id=spec.prompt_id,
                warmup=spec.warmup,
                ok=True,
                error=None,
                latency_seconds=0.0,
                prompt_tokens=1,
                completion_tokens=1,
                reasoning_tokens=None,
                reasoning_token_source=wt.TOKEN_DETAIL_ABSENT,
                content_chars=1,
                reasoning_chars=0,
                finish_reason="stop",
                truncated=False,
                retries=0,
                tokens_per_second=1.0,
            )

        specs = [
            wt.CallSpec(width=2, batch=0, slot=0, prompt_id=0, prompt="p0"),
            wt.CallSpec(width=2, batch=0, slot=1, prompt_id=1, prompt="p1"),
        ]
        started = time.monotonic()
        try:
            records, elapsed = wt._run_batch(_config(), specs, call_fn=call_fn, timeout=0.2)
        finally:
            gate.set()
        wall = time.monotonic() - started

        assert wall < 2.0, "the batch must not block past its own timeout"
        assert elapsed < 2.0
        by_slot = {record.slot: record for record in records}
        assert by_slot[0].ok is False
        assert by_slot[0].error == "timeout"
        assert by_slot[1].ok is True

    def test_an_empty_spec_list_returns_no_records_and_zero_elapsed(self) -> None:
        def unreachable(config: WorkerConfig, spec: wt.CallSpec) -> wt.CallRecord:
            raise AssertionError("call_fn must not be invoked for an empty batch")

        records, elapsed = wt._run_batch(_config(), [], call_fn=unreachable)
        assert records == []
        assert elapsed == 0.0


# ── aggregation: per-stream vs aggregate, never conflated ────────────────────


def _ok_record(
    *, width: int, slot: int, completion_tokens: int, latency: float, **overrides: Any
) -> wt.CallRecord:
    kwargs: dict[str, Any] = dict(
        width=width,
        batch=0,
        slot=slot,
        prompt_id=0,
        warmup=False,
        ok=True,
        error=None,
        latency_seconds=latency,
        prompt_tokens=10,
        completion_tokens=completion_tokens,
        reasoning_tokens=None,
        reasoning_token_source=wt.TOKEN_DETAIL_ABSENT,
        content_chars=20,
        reasoning_chars=0,
        finish_reason="stop",
        truncated=False,
        retries=0,
        tokens_per_second=completion_tokens / latency if latency else None,
    )
    kwargs.update(overrides)
    return wt.CallRecord(**kwargs)


class TestSummarizeWidth:
    def test_ok_and_error_counts(self) -> None:
        records = [
            _ok_record(width=2, slot=0, completion_tokens=100, latency=2.0),
            wt._failed_record(
                wt.CallSpec(width=2, batch=0, slot=1, prompt_id=0, prompt="x"),
                error="boom",
                elapsed=1.0,
            ),
        ]
        summary = wt._summarize_width(2, 1, records)
        assert summary.calls == 2
        assert summary.ok == 1
        assert summary.errors == 1
        assert summary.timeouts == 0

    def test_timeouts_are_a_subset_of_errors(self) -> None:
        spec = wt.CallSpec(width=1, batch=0, slot=0, prompt_id=0, prompt="x")
        records = [wt._failed_record(spec, error="timeout", elapsed=5.0)]
        summary = wt._summarize_width(1, 1, records)
        assert summary.errors == 1
        assert summary.timeouts == 1

    def test_truncated_count_and_finish_reasons(self) -> None:
        records = [
            _ok_record(width=1, slot=0, completion_tokens=50, latency=1.0, finish_reason="stop"),
            _ok_record(
                width=1,
                slot=0,
                completion_tokens=50,
                latency=1.0,
                finish_reason=wt.FINISH_TRUNCATED,
                truncated=True,
            ),
        ]
        summary = wt._summarize_width(1, 1, records)
        assert summary.truncated == 1
        assert summary.finish_reasons == {"stop": 1, wt.FINISH_TRUNCATED: 1}

    def test_per_stream_mean_is_the_mean_of_individual_call_rates(self) -> None:
        # call A: 100 tok in 2s = 50 tok/s; call B: 100 tok in 4s = 25 tok/s
        records = [
            _ok_record(width=2, slot=0, completion_tokens=100, latency=2.0),
            _ok_record(width=2, slot=1, completion_tokens=100, latency=4.0),
        ]
        summary = wt._summarize_width(2, 1, records)
        assert summary.per_stream_tokens_per_second_mean == pytest.approx(37.5)

    def test_reasoning_tokens_reported_counts_only_calls_with_a_value(self) -> None:
        records = [
            _ok_record(width=1, slot=0, completion_tokens=50, latency=1.0, reasoning_tokens=40),
            _ok_record(width=1, slot=0, completion_tokens=50, latency=1.0, reasoning_tokens=None),
        ]
        summary = wt._summarize_width(1, 1, records)
        assert summary.reasoning_tokens_sum == 40
        assert summary.reasoning_tokens_reported == 1


class TestWidthRunEffectiveConcurrency:
    """The whole point of the ``effective_concurrency`` ratio: it must read
    close to ``width`` under true parallelism and close to 1 under queueing."""

    def test_true_parallelism_reads_close_to_the_width(self) -> None:
        # 4 streams, each 100 tok in 2s (50 tok/s per-stream) running truly in
        # parallel: the whole batch also takes ~2s wall clock, so aggregate is
        # 400 tok / 2s = 200 tok/s = 4x the per-stream rate.
        width = 4
        records = [
            _ok_record(width=width, slot=i, completion_tokens=100, latency=2.0)
            for i in range(width)
        ]
        run = wt.WidthRun(width=width, batches=1, records=records, batch_elapsed_seconds=[2.0])
        summary = run.summary()
        assert summary.aggregate_tokens_per_second == pytest.approx(200.0)
        assert summary.per_stream_tokens_per_second_mean == pytest.approx(50.0)
        assert summary.effective_concurrency == pytest.approx(4.0)

    def test_pure_queueing_reads_close_to_one(self) -> None:
        # Same 4 streams, same per-call numbers, but the SERVER queued them:
        # the batch's wall clock is 4x a single call's (8s), so the aggregate
        # tok/s collapses to the same as one stream alone.
        width = 4
        records = [
            _ok_record(width=width, slot=i, completion_tokens=100, latency=2.0)
            for i in range(width)
        ]
        run = wt.WidthRun(width=width, batches=1, records=records, batch_elapsed_seconds=[8.0])
        summary = run.summary()
        assert summary.aggregate_tokens_per_second == pytest.approx(50.0)
        assert summary.effective_concurrency == pytest.approx(1.0)

    def test_no_ok_calls_yields_none_rather_than_a_divide_by_zero(self) -> None:
        spec = wt.CallSpec(width=1, batch=0, slot=0, prompt_id=0, prompt="x")
        records = [wt._failed_record(spec, error="boom", elapsed=1.0)]
        run = wt.WidthRun(width=1, batches=1, records=records, batch_elapsed_seconds=[1.0])
        summary = run.summary()
        assert (
            summary.aggregate_tokens_per_second is None
            or summary.aggregate_tokens_per_second == 0.0
        )
        assert summary.effective_concurrency is None


# ── criterion 7: the operator's claim, checked both readings ─────────────────


class TestCheckOperatorClaim:
    def _summary(
        self, *, per_stream: Optional[float], aggregate: Optional[float]
    ) -> wt.WidthSummary:
        return wt.WidthSummary(
            width=14,
            batches=2,
            calls=28,
            ok=28,
            errors=0,
            timeouts=0,
            truncated=0,
            finish_reasons={"stop": 28},
            aggregate_seconds=10.0,
            aggregate_completion_tokens=1000,
            aggregate_tokens_per_second=aggregate,
            per_stream_tokens_per_second_mean=per_stream,
            per_stream_tokens_per_second_median=per_stream,
            latency_seconds_mean=1.0,
            latency_seconds_median=1.0,
            latency_seconds_p95=1.0,
            effective_concurrency=None,
            reasoning_tokens_sum=0,
            reasoning_tokens_reported=0,
        )

    def test_both_readings_reproduce_when_numbers_clear_both_bars(self) -> None:
        result = wt.check_operator_claim(self._summary(per_stream=60.0, aggregate=800.0))
        assert result["per_stream_reading_reproduced"] is True
        assert result["aggregate_reading_reproduced"] is True

    def test_neither_reading_reproduces_when_numbers_fall_short(self) -> None:
        result = wt.check_operator_claim(self._summary(per_stream=10.0, aggregate=90.0))
        assert result["per_stream_reading_reproduced"] is False
        assert result["aggregate_reading_reproduced"] is False

    def test_a_missing_number_never_reads_as_reproduced(self) -> None:
        result = wt.check_operator_claim(self._summary(per_stream=None, aggregate=None))
        assert result["per_stream_reading_reproduced"] is False
        assert result["aggregate_reading_reproduced"] is False

    def test_the_aggregate_target_is_the_per_stream_target_times_the_width(self) -> None:
        assert wt.OPERATOR_CLAIM_AGGREGATE_TOK_S == (
            wt.OPERATOR_CLAIM_PER_STREAM_TOK_S * wt.OPERATOR_CLAIM_WIDTH
        )


# ── criterion 4: warm-up policy ────────────────────────────────────────────────


class TestRunSweepWarmup:
    def _scripted_call_fn(self, calls: list[wt.CallSpec]) -> wt.CallFn:
        def call_fn(config: WorkerConfig, spec: wt.CallSpec) -> wt.CallRecord:
            calls.append(spec)
            return _ok_record(width=spec.width, slot=spec.slot, completion_tokens=10, latency=0.1)

        return call_fn

    def test_warmup_true_makes_exactly_one_extra_call_marked_warmup(self) -> None:
        calls: list[wt.CallSpec] = []
        result = wt.run_sweep(
            _config(),
            widths=(1,),
            batches_per_width={1: 1},
            warmup=True,
            call_fn=self._scripted_call_fn(calls),
        )
        warmup_specs = [spec for spec in calls if spec.warmup]
        assert len(warmup_specs) == 1
        assert result.warmup is not None
        # The warm-up record is not counted in the width's own summary.
        assert result.widths[0].summary().calls == 1

    def test_warmup_false_makes_no_extra_call(self) -> None:
        calls: list[wt.CallSpec] = []
        result = wt.run_sweep(
            _config(),
            widths=(1,),
            batches_per_width={1: 1},
            warmup=False,
            call_fn=self._scripted_call_fn(calls),
        )
        assert result.warmup is None
        assert len(calls) == 1

    def test_batches_per_width_override_controls_call_count(self) -> None:
        calls: list[wt.CallSpec] = []
        result = wt.run_sweep(
            _config(),
            widths=(2,),
            batches_per_width={2: 3},
            warmup=False,
            call_fn=self._scripted_call_fn(calls),
        )
        assert len(calls) == 2 * 3
        assert result.widths[0].batches == 3

    def test_all_records_includes_warmup_plus_every_width(self) -> None:
        result = wt.run_sweep(
            _config(),
            widths=(1, 2),
            batches_per_width={1: 1, 2: 1},
            warmup=True,
            call_fn=self._scripted_call_fn([]),
        )
        assert len(result.all_records()) == 1 + 1 + 2

    def test_prompts_cycle_across_the_whole_sweep_not_reset_per_width(self) -> None:
        calls: list[wt.CallSpec] = []
        wt.run_sweep(
            _config(),
            widths=(2, 2),
            batches_per_width={2: 1},
            warmup=False,
            call_fn=self._scripted_call_fn(calls),
        )
        # 4 calls total (2 widths listed, but widths is a plain sequence so a
        # duplicate is legal input); prompt_ids should not all be identical.
        prompt_ids = [spec.prompt_id for spec in calls]
        assert len(set(prompt_ids)) > 1


# ── CLI ────────────────────────────────────────────────────────────────────────


class TestCLI:
    def test_missing_everything_exits_2_and_never_calls_run_sweep(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.delenv("EMBODIMENT_WORKER_URL", raising=False)
        monkeypatch.delenv("EMBODIMENT_WORKER_MODEL", raising=False)
        monkeypatch.delenv("COLLEAGUE_API_KEY", raising=False)

        called = {"n": 0}
        monkeypatch.setattr(
            wt, "run_sweep", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
        )

        exit_code = wt.main([])
        assert exit_code == 2
        assert called["n"] == 0
        err = capsys.readouterr().err
        assert "worker-url-absent" in err or "worker-model-absent" in err

    def test_full_config_calls_run_sweep_and_writes_both_files(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
    ) -> None:
        scripted = wt.SweepResult(
            config=_config(),
            max_tokens=100,
            temperature=0.1,
            warmup=None,
            widths=[
                wt.WidthRun(
                    width=1,
                    batches=1,
                    records=[_ok_record(width=1, slot=0, completion_tokens=10, latency=0.5)],
                    batch_elapsed_seconds=[0.5],
                )
            ],
        )
        seen: dict[str, Any] = {}

        def fake_run_sweep(config: WorkerConfig, **kwargs: Any) -> wt.SweepResult:
            seen["config"] = config
            seen["kwargs"] = kwargs
            return scripted

        monkeypatch.setattr(wt, "run_sweep", fake_run_sweep)

        out_path = tmp_path / "raw.jsonl"
        summary_path = tmp_path / "summary.json"
        exit_code = wt.main(
            [
                "--worker-url",
                "http://example.invalid/v1",
                "--worker-model",
                "m",
                "--out",
                str(out_path),
                "--summary-out",
                str(summary_path),
            ]
        )
        assert exit_code == 0
        assert seen["config"].base_url == "http://example.invalid/v1"

        lines = out_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["width"] == 1

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["widths"][0]["width"] == 1
        assert "operator_claim" in summary

    def test_widths_flag_is_parsed_into_a_tuple_of_ints(self) -> None:
        assert wt._parse_widths("1,2,8,14") == (1, 2, 8, 14)

    def test_batches_flag_is_parsed_into_a_width_to_count_mapping(self) -> None:
        assert wt._parse_batches("1:3,2:2,8:2,14:2") == {1: 3, 2: 2, 8: 2, 14: 2}


def test_every_skip_gated_class_in_this_module_is_live_prefixed() -> None:
    """A live class not named ``TestLive*`` keeps the network bomb and lies."""
    for name, value in vars(sys.modules[__name__]).items():
        if isinstance(value, type) and getattr(value, "pytestmark", None):
            assert name.startswith("TestLive"), f"{name} is skip-gated but not TestLive-prefixed"


# ── the live rig, small and cheap, on purpose ─────────────────────────────────

LIVE_ENABLED = os.environ.get("EMBODIMENT_LIVE_RIG") == "1"
LIVE_KEY = os.environ.get("COLLEAGUE_API_KEY", "")


@pytest.mark.skipif(not LIVE_ENABLED, reason="set EMBODIMENT_LIVE_RIG=1 to test the real rig")
@pytest.mark.skipif(not LIVE_KEY, reason="COLLEAGUE_API_KEY is not set")
class TestLiveWorkerThroughputSmoke:
    """One tiny real sweep: width 1, one batch, no warm-up. Proves the wiring;
    the actual widths-1/2/8/14 measurement is run by hand and committed under
    ``docs/live-test-results/`` -- see that directory's results doc for t3."""

    def _config(self) -> WorkerConfig:
        from examples import worker_seam as ws

        resolution = ws.resolve_worker_config(
            cli_url=os.environ.get(ws.WORKER_URL_ENV) or ws.THOR_WORKER_URL_DOCUMENTED,
            cli_model=os.environ.get(ws.WORKER_MODEL_ENV) or ws.THOR_WORKER_MODEL_DOCUMENTED,
            cli_api_key=LIVE_KEY,
        )
        assert resolution.config is not None, resolution.to_dict()
        return resolution.config

    def test_a_single_stream_call_reaches_the_real_worker(self) -> None:
        result = wt.run_sweep(
            self._config(),
            widths=(1,),
            batches_per_width={1: 1},
            max_tokens=200,
            warmup=False,
        )
        summary = result.widths[0].summary()
        assert summary.calls == 1
        assert summary.ok == 1
        assert summary.aggregate_completion_tokens > 0
