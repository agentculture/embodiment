"""The retry count in ``worker-throughput.jsonl``, recomputed rather than asserted.

`corrections.md` §10 records that `worker-throughput.md`'s stability section
claimed *"zero transport retries"* while its own committed records held nine.
The claim drifted because the document read ``ok`` — which is set **after** a
retry succeeds — instead of the ``retries`` counter sitting in the same row.

These tests recompute every figure in that correction from the committed
records, so the correction cannot quietly drift back. The M3 discipline: a
constant recomputed from a committed input breaks when the input changes; a
constant typed into prose does not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

#: The committed raw records the published tables were generated from.
RECORDS = Path(__file__).resolve().parents[1] / "docs/live-test-results/worker-throughput.jsonl"

#: `WorkerSeam.RETRY_SLEEP_SECONDS`. The seam's stopwatch starts before the
#: first attempt and is never reset, so a retried call carries this backoff —
#: and its failed attempt's own duration — inside ``latency_seconds``.
RETRY_BACKOFF_SECONDS = 20.0


def _measured() -> list[dict]:
    """Every measured (non-warm-up) call record."""
    rows = [json.loads(line) for line in RECORDS.read_text().splitlines() if line.strip()]
    return [row for row in rows if not row.get("warmup")]


def _corrected_rate(row: dict) -> float:
    """Generation rate with the retry backoff removed — conservative.

    Only the sleep is subtracted, never the failed attempt's own duration, so
    a corrected rate still understates the true one.
    """
    if not row.get("retries"):
        return float(row["tokens_per_second"])
    return float(row["completion_tokens"]) / (float(row["latency_seconds"]) - RETRY_BACKOFF_SECONDS)


class TestTheRetriesTheDocumentMissed:
    """The finding itself: nine retried calls, all at one width."""

    def test_nine_measured_calls_retried(self) -> None:
        retried = [row for row in _measured() if row.get("retries")]
        assert len(retried) == 9, (
            "corrections.md §10 records nine retried calls; the committed records now hold "
            f"{len(retried)}. If the records changed, §10 needs updating — not this test."
        )

    def test_every_retry_happened_at_width_14(self) -> None:
        widths = {row["width"] for row in _measured() if row.get("retries")}
        assert widths == {14}, f"expected every retry at width 14, found widths {sorted(widths)}"

    def test_no_call_exhausted_its_retries(self) -> None:
        """The half of the original claim that *was* true, pinned so it stays legible."""
        assert all(
            (row.get("retries") or 0) < 3 for row in _measured()
        ), "a call exhausted MAX_TRANSPORT_RETRIES; corrections.md §10 says none did"

    def test_ok_flag_cannot_reveal_a_retry(self) -> None:
        """Why nothing caught it: every rescued call still reports ``ok``."""
        assert all(row.get("ok") for row in _measured() if row.get("retries")), (
            "a retried call reported ok=false — then the stability section could have seen it, "
            "and corrections.md §10's explanation of the miss would be wrong"
        )

    def test_retries_are_per_call_not_cumulative(self) -> None:
        """Rules out the reading that would make this a non-finding.

        ``WorkerSeam.meter.retries`` accumulates, so nine rows reading ``1``
        could in principle be threads racing one shared counter. They are not:
        `worker_throughput.py` builds one fresh seam per call. A shared counter
        across a batch's retried slots would read 1, 2, 3, … — never ``1``
        repeatedly.
        """
        values = {row["retries"] for row in _measured() if row.get("retries")}
        assert values == {1}, (
            f"retry values {sorted(values)} climb, which is the signature of a shared cumulative "
            "counter rather than nine independent single-retry calls"
        )


class TestWhatTheCorrectionChanges:
    """The retry-corrected figures §10 publishes, recomputed here."""

    def test_uncontended_widths_carry_no_retries(self) -> None:
        for width in (1, 2, 8):
            calls = [row for row in _measured() if row["width"] == width]
            assert calls, f"no measured calls at width {width}"
            assert not any(row.get("retries") for row in calls), f"width {width} gained a retry"

    def test_published_slowest_rate_is_a_retry_artifact(self) -> None:
        slowest = min(row["tokens_per_second"] for row in _measured())
        assert round(slowest, 3) == 12.921, f"published slowest rate moved to {slowest}"
        row = next(r for r in _measured() if r["tokens_per_second"] == slowest)
        assert row.get("retries"), "the slowest measured call is no longer a retried one"

    def test_retry_clean_width_14_floor(self) -> None:
        clean = [
            row["tokens_per_second"]
            for row in _measured()
            if row["width"] == 14 and not row.get("retries")
        ]
        assert len(clean) == 19, f"expected 19 retry-clean width-14 calls, found {len(clean)}"
        assert round(min(clean), 3) == 30.558, f"retry-clean floor moved to {min(clean)}"

    def test_correction_lifts_every_retried_call_into_the_band(self) -> None:
        """Each contaminated call, corrected, rejoins the retry-clean width-14 band.

        The comparison is against width 14's own retry-clean floor, not width
        8's: these calls *were* running at width 14, and the claim is that they
        were never anomalously slow — not that they matched a lighter load.
        Four of the nine still land below the width-8 floor, which is exactly
        what real width-14 contention looks like.
        """
        clean_floor = min(
            row["tokens_per_second"]
            for row in _measured()
            if row["width"] == 14 and not row.get("retries")
        )
        for row in (r for r in _measured() if r.get("retries")):
            assert _corrected_rate(row) > clean_floor, (
                f"corrected rate {_corrected_rate(row):.2f} still sits below the retry-clean "
                f"width-14 floor {clean_floor:.2f}; §10's artifact explanation would not hold"
            )

    def test_corrected_range_matches_the_published_correction(self) -> None:
        corrected = sorted(_corrected_rate(r) for r in _measured() if r.get("retries"))
        assert round(corrected[0], 1) == 35.8, f"corrected floor moved to {corrected[0]:.2f}"
        assert round(corrected[-1], 1) == 44.5, f"corrected ceiling moved to {corrected[-1]:.2f}"

    def test_corrected_width_14_mean_and_effective_concurrency(self) -> None:
        calls = [row for row in _measured() if row["width"] == 14]
        corrected_mean = sum(_corrected_rate(row) for row in calls) / len(calls)
        assert round(corrected_mean, 2) == 37.00, f"corrected mean moved to {corrected_mean:.2f}"
        # Aggregate throughput is unchanged by the correction — it is measured
        # against the batch's own wall clock, not summed from per-call rates.
        published_aggregate = 268.14
        assert round(published_aggregate / corrected_mean, 2) == 7.25

    def test_the_derivation_input_over_protects(self) -> None:
        """Why 12.921 is kept: the bound it produces is larger, never smaller."""
        budget = 16000
        contaminated = min(row["tokens_per_second"] for row in _measured())
        clean = min(
            row["tokens_per_second"]
            for row in _measured()
            if row["width"] == 14 and not row.get("retries")
        )
        assert budget / contaminated > budget / clean, (
            "the contaminated rate no longer produces the larger bound, so keeping it is no "
            "longer the safe direction and corrections.md §10 needs revisiting"
        )
        assert round((budget / contaminated) / (budget / clean), 1) == 2.4


@pytest.mark.parametrize("claim", ["zero transport retries", "never exercised for real"])
def test_the_superseded_claim_still_carries_its_correction(claim: str) -> None:
    """The wrong paragraph is kept verbatim; it must never lose its marker."""
    published = (RECORDS.parent / "worker-throughput.md").read_text()
    assert claim in published, f"the superseded claim {claim!r} was edited away instead of marked"
    marker = published.index("SUPERSEDED 2026-08-01")
    assert marker < published.index(claim), "the correction marker no longer precedes the claim"
