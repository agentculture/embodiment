"""Task t1 — the committed, dated generation-rate config every timeout bound derives from.

Issue [#42](https://github.com/agentculture/embodiment/issues/42) fixed the
timeout rule: ``REQUEST_TIMEOUT >= max_tokens / slowest_measured_rate``. The
challenge pass then found the rule's *input* was the weak link — claim ``c39``:
a bare rate literal in test code goes stale silently after a rig change, and
claim ``c40``: the rate is condition-dependent, so a number with no stated
concurrency is not a measurement.

This module proves t1's three acceptance criteria, each named where it is
proved:

1. **A committed config carries rate, date, n, model and concurrency
   condition, and no rate literal appears in test code** —
   :class:`TestCommittedConfig` (the file loads and every field is present),
   :class:`TestConditionRecorded` (c40), and :class:`TestNoRateLiteralInCode`,
   which walks the AST of every rate-deriving test module and asserts none of
   the config's own measured values appears as a float constant.
2. **Deleting the config fails the bound test with a message naming the missing
   measurement, never a silent default** — :class:`TestAbsentConfigRefuses`.
   The real file is never touched: the loader is pointed at a ``tmp_path`` that
   holds no config.
3. **The re-derivation procedure is documented beside the config** —
   :class:`TestProcedureDocumented`.

And one criterion nobody asked for but the numbers need:
:class:`TestWorkerRatesMatchRawRecords` recomputes every worker figure from the
committed ``worker-throughput.jsonl`` and asserts the config matches. A
transcription slip in a hand-written config is the same silent-staleness defect
c39 names, one layer in.
"""

from __future__ import annotations

import ast
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import rate_config as rc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "docs" / "live-test-results"
WORKER_RAW = RESULTS_DIR / "worker-throughput.jsonl"

#: Every test module that touches the measured rate. Each one that exists is
#: walked by :class:`TestNoRateLiteralInCode`.
#:
#: This module is on the list, which is why nothing below asserts a rate
#: against a number: the worker's figures are reconciled against
#: ``worker-throughput.jsonl`` and the cortex's against the text of the doc
#: that cites them, so a re-measurement means editing the config and nothing
#: else. ``test_timeout_bounds.py`` is task t2's and is checked the moment it
#: lands — listed now so the guard is waiting for it rather than being
#: remembered later.
RATE_DERIVING_MODULES = (
    "rate_config.py",
    "test_rate_config.py",
    "test_timeout_bounds.py",
)


def _float_constants(path: Path) -> list[float]:
    """Every float literal in a module, via the AST — comments and strings excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]


def _worker_rows() -> list[dict[str, Any]]:
    lines = WORKER_RAW.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ── criterion 1: the config is committed, complete, and loads ────────────────


class TestCommittedConfig:
    def test_the_config_is_committed_beside_the_other_live_test_results(self) -> None:
        assert rc.DEFAULT_CONFIG_PATH.parent == RESULTS_DIR
        assert rc.DEFAULT_CONFIG_PATH.is_file()

    def test_it_loads(self) -> None:
        config = rc.load_rate_config()
        assert config.path == rc.DEFAULT_CONFIG_PATH
        assert config.version == 1
        assert set(config.roles) == {"cortex", "worker"}

    def test_every_measurement_carries_rate_date_n_and_model(self) -> None:
        config = rc.load_rate_config()
        for name in ("cortex", "worker"):
            rate = config.rate(name)
            assert rate.slowest_tok_s > 0
            assert rate.mean_tok_s >= rate.slowest_tok_s
            assert rate.fastest_tok_s >= rate.mean_tok_s
            # A date, not a vibe: parseable ISO, so "recently" can never stand in.
            assert len(rate.measured_on) == 10 and rate.measured_on.count("-") == 2
            assert rate.n_calls > 0
            assert 0 < rate.n_rate_bearing <= rate.n_calls
            assert rate.model
            assert rate.base_url.startswith("http")
            assert rate.max_tokens > 0
            assert rate.sources

    def test_cortex_records_what_it_actually_measured(self) -> None:
        """The measured id, not the id CLAUDE.md names — they differ, and that is c39.

        `orchestrator-worker-preregistration.md` §1 says it plainly: *"The
        cortex the probes measured is `unsloth/Qwen3.6-27B-NVFP4`"*, while
        `culture.yaml` and every `examples/*.py` default name
        `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP`. A config that recorded the
        harness default would be attributing a measurement to a model that
        never produced it.
        """
        cortex = rc.load_rate_config().rate("cortex")
        assert cortex.model == "unsloth/Qwen3.6-27B-NVFP4"
        assert cortex.measured_on == "2026-08-01"
        assert cortex.n_calls == 10
        assert cortex.n_rate_bearing == 8
        assert cortex.max_tokens == 16000
        # No rate literal here on purpose — see TestNoRateLiteralInCode. The
        # cortex figures are pinned to a committed citation instead, in
        # TestCortexCitationsAreReachable.

    def test_worker_records_its_own_rig(self) -> None:
        worker = rc.load_rate_config().rate("worker")
        assert worker.model == "unsloth/Qwen3.6-35B-A3B-NVFP4"
        assert worker.base_url == "http://thor.tail0be7e0.ts.net:8000/v1"
        assert worker.measured_on == "2026-07-31"
        assert worker.max_tokens == 1200
        # Its rates are not asserted against literals either: every one of them
        # is recomputed from worker-throughput.jsonl in
        # TestWorkerRatesMatchRawRecords, including n_calls.

    def test_the_rule_the_bounds_derive_from_is_stated_in_the_config(self) -> None:
        config = rc.load_rate_config()
        assert "max_tokens" in config.rule_formula
        assert config.rule_source


# ── criterion 1 (c40): the concurrency condition, on every measurement ───────


class TestConditionRecorded:
    def test_every_measurement_names_its_concurrency_condition(self) -> None:
        config = rc.load_rate_config()
        for name in ("cortex", "worker"):
            rate = config.rate(name)
            assert rate.concurrency >= 1
            assert rate.condition.strip()

    def test_cortex_is_single_stream_and_says_what_would_invalidate_it(self) -> None:
        cortex = rc.load_rate_config().rate("cortex")
        assert cortex.concurrency == 1
        assert cortex.remeasure_when

    def test_worker_carries_a_measurement_per_dialled_width(self) -> None:
        worker = rc.load_rate_config().rate("worker")
        assert sorted(worker.by_width) == [1, 2, 8, 14]
        for width, at_width in worker.by_width.items():
            assert at_width.width == width
            assert at_width.n_calls > 0
            assert at_width.slowest_tok_s > 0

    def test_the_rate_falls_with_width_which_is_why_width_is_the_condition(self) -> None:
        worker = rc.load_rate_config().rate("worker")
        means = [worker.by_width[w].mean_tok_s for w in sorted(worker.by_width)]
        assert means == sorted(means, reverse=True)

    def test_an_unmeasured_width_refuses_rather_than_interpolating(self) -> None:
        worker = rc.load_rate_config().rate("worker")
        with pytest.raises(rc.RateConfigError) as caught:
            worker.at_width(4)
        message = str(caught.value)
        assert "4" in message
        assert "1, 2, 8, 14" in message

    def test_the_headline_worker_rate_is_the_slowest_across_measured_widths(self) -> None:
        worker = rc.load_rate_config().rate("worker")
        assert worker.slowest_tok_s == min(w.slowest_tok_s for w in worker.by_width.values())


# ── criterion 1: no rate literal in test code ────────────────────────────────


class TestNoRateLiteralInCode:
    def test_at_least_one_rate_deriving_module_is_actually_walked(self) -> None:
        """A guard that checks nothing is not a guard."""
        present = [
            name for name in RATE_DERIVING_MODULES if (Path(__file__).parent / name).is_file()
        ]
        assert "rate_config.py" in present

    def test_no_measured_rate_appears_as_a_literal_in_rate_deriving_code(self) -> None:
        """Values, not explanations.

        The walk is over float `ast.Constant` nodes, so a rate quoted in a
        docstring or a comment is not a finding — prose that says *why* 21.5
        is the cortex floor cannot go stale in the way c39 means, because
        nothing divides by it. A number a bound could reach is the target.
        """
        config = rc.load_rate_config()
        measured = set(config.every_measured_rate())
        assert measured, "the config declares no rates, so this guard would pass vacuously"
        for name in RATE_DERIVING_MODULES:
            module = Path(__file__).parent / name
            if not module.is_file():
                continue
            leaked = sorted(value for value in _float_constants(module) if value in measured)
            assert not leaked, (
                f"tests/{name} carries measured rate literal(s) {leaked}. "
                f"Read them from {rc.DEFAULT_CONFIG_PATH.name} instead — a literal "
                "goes stale silently when the rig changes (claim c39)."
            )

    def test_the_loader_hardcodes_no_numeric_rate_defaults(self) -> None:
        """The loader may carry structural numbers; it may not carry a rate.

        Anything above 1.0 in `rate_config.py` would be a candidate rate or
        timeout. Tolerances and sentinels stay below it.
        """
        for value in _float_constants(Path(rc.__file__)):
            assert value <= 1.0, f"rate_config.py holds float literal {value}"


# ── criterion 2: absence refuses, loudly and by name ─────────────────────────


class TestAbsentConfigRefuses:
    def test_a_missing_config_raises_naming_the_missing_measurement(self, tmp_path: Path) -> None:
        """Criterion 2, proved without touching the committed file.

        The loader is pointed at a directory that holds no config — the same
        state a `git rm` of the real one would produce — and must refuse by
        name rather than fall back to a built-in rate.
        """
        absent = tmp_path / "timeout-rate-measurements.json"
        assert not absent.exists()

        with pytest.raises(rc.RateConfigError) as caught:
            rc.load_rate_config(absent)

        message = str(caught.value)
        assert str(absent) in message
        assert "generation-rate measurement" in message
        assert rc.PROCEDURE_DOC_NAME in message

    def test_the_default_path_is_the_one_that_would_go_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deleting the *default* file — not just any file — is what must refuse."""
        monkeypatch.setattr(rc, "DEFAULT_CONFIG_PATH", tmp_path / "timeout-rate-measurements.json")
        with pytest.raises(rc.RateConfigError):
            rc.load_rate_config()

    def test_unreadable_json_refuses_rather_than_defaulting(self, tmp_path: Path) -> None:
        broken = tmp_path / "timeout-rate-measurements.json"
        broken.write_text("{not json", encoding="utf-8")
        with pytest.raises(rc.RateConfigError) as caught:
            rc.load_rate_config(broken)
        assert "readable JSON" in str(caught.value)

    def test_a_non_object_payload_refuses(self, tmp_path: Path) -> None:
        listy = tmp_path / "timeout-rate-measurements.json"
        listy.write_text("[]", encoding="utf-8")
        with pytest.raises(rc.RateConfigError):
            rc.load_rate_config(listy)

    def test_a_missing_field_names_the_field_and_the_file(self, tmp_path: Path) -> None:
        payload = json.loads(rc.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        del payload["roles"]["cortex"]["slowest_tok_s"]
        partial = tmp_path / "timeout-rate-measurements.json"
        partial.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(rc.RateConfigError) as caught:
            rc.load_rate_config(partial)
        message = str(caught.value)
        assert "slowest_tok_s" in message
        assert str(partial) in message

    def test_a_missing_condition_refuses_because_c40_makes_it_load_bearing(
        self, tmp_path: Path
    ) -> None:
        payload = json.loads(rc.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        del payload["roles"]["cortex"]["condition"]
        partial = tmp_path / "timeout-rate-measurements.json"
        partial.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(rc.RateConfigError) as caught:
            rc.load_rate_config(partial)
        assert "condition" in str(caught.value)

    def test_a_missing_role_refuses_naming_the_role(self, tmp_path: Path) -> None:
        payload = json.loads(rc.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        del payload["roles"]["worker"]
        partial = tmp_path / "timeout-rate-measurements.json"
        partial.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(rc.RateConfigError) as caught:
            rc.load_rate_config(partial)
        assert "worker" in str(caught.value)

    def test_an_unknown_role_refuses_naming_the_known_ones(self) -> None:
        config = rc.load_rate_config()
        with pytest.raises(rc.RateConfigError) as caught:
            config.rate("muse")
        message = str(caught.value)
        assert "muse" in message
        assert "cortex" in message and "worker" in message


# ── the numbers are the raw records', not a transcription ────────────────────


class TestWorkerRatesMatchRawRecords:
    """Recompute every worker figure from the committed jsonl and compare.

    This is what makes the config a *derived* artifact rather than a hand-typed
    one. `tokens_per_second` in the jsonl is already rounded to three decimals
    by `examples/worker_throughput.py`'s `_round`, so recomputation here
    reproduces the file's own values exactly for min/max and to within a
    rounding step for the mean.
    """

    @staticmethod
    def _by_width() -> dict[int, list[dict[str, Any]]]:
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in _worker_rows():
            if row.get("ok") is not True or row.get("warmup"):
                continue
            grouped[row["width"]].append(row)
        return grouped

    def test_the_raw_file_is_the_one_the_config_cites(self) -> None:
        worker = rc.load_rate_config().rate("worker")
        assert any(WORKER_RAW.name in source for source in worker.sources)

    def test_every_per_width_figure_reproduces(self) -> None:
        grouped = self._by_width()
        worker = rc.load_rate_config().rate("worker")
        assert sorted(worker.by_width) == sorted(grouped)

        for width, rows in grouped.items():
            rates = [row["tokens_per_second"] for row in rows]
            recorded = worker.by_width[width]
            assert recorded.n_calls == len(rows), f"width {width}: n"
            assert recorded.slowest_tok_s == pytest.approx(min(rates), abs=5e-4)
            assert recorded.fastest_tok_s == pytest.approx(max(rates), abs=5e-4)
            assert recorded.mean_tok_s == pytest.approx(statistics.fmean(rates), abs=5e-4)

    def test_the_retry_bearing_count_reproduces(self) -> None:
        grouped = self._by_width()
        worker = rc.load_rate_config().rate("worker")
        for width, rows in grouped.items():
            retried = sum(1 for row in rows if row["retries"])
            assert worker.by_width[width].retry_bearing_calls == retried, f"width {width}"

    def test_the_retry_clean_floor_reproduces(self) -> None:
        """The number the caveat rests on: the slowest call that never retried."""
        grouped = self._by_width()
        worker = rc.load_rate_config().rate("worker")
        for width, rows in grouped.items():
            clean = [row["tokens_per_second"] for row in rows if not row["retries"]]
            assert worker.by_width[width].slowest_retry_clean_tok_s == pytest.approx(
                min(clean), abs=5e-4
            ), f"width {width}"

    def test_the_total_measured_call_count_reproduces(self) -> None:
        worker = rc.load_rate_config().rate("worker")
        measured = [r for r in _worker_rows() if r.get("ok") is True and not r.get("warmup")]
        assert worker.n_calls == len(measured)
        assert worker.n_rate_bearing == sum(1 for r in measured if r["tokens_per_second"])

    def test_the_role_headline_reproduces(self) -> None:
        """The envelope, not just the per-width cells."""
        worker = rc.load_rate_config().rate("worker")
        measured = [r for r in _worker_rows() if r.get("ok") is True and not r.get("warmup")]
        rates = [row["tokens_per_second"] for row in measured]
        assert worker.slowest_tok_s == pytest.approx(min(rates), abs=5e-4)
        assert worker.fastest_tok_s == pytest.approx(max(rates), abs=5e-4)
        assert worker.mean_tok_s == pytest.approx(statistics.fmean(rates), abs=5e-4)

    def test_the_warmup_call_is_excluded_from_every_figure(self) -> None:
        """The harness records the warm-up with ``width: 0``; it is not a measurement."""
        worker = rc.load_rate_config().rate("worker")
        assert 0 not in worker.by_width
        warmups = [r for r in _worker_rows() if r.get("warmup")]
        assert warmups, "the raw file has no warm-up row, so this guard proves nothing"
        assert worker.n_calls == len(_worker_rows()) - len(warmups)


class TestCortexCitationsAreReachable:
    """The cortex's raw records are not on this branch — say so, don't pretend.

    `C1-E-at-300s.jsonl` and pre-registration §18 live on branch `owa/t12` and
    reach main only when it merges. The config records that explicitly, and the
    citations it makes that *are* readable here are checked to actually contain
    the figures.
    """

    def test_the_config_declares_which_citations_are_not_yet_on_main(self) -> None:
        cortex = rc.load_rate_config().rate("cortex")
        assert cortex.pending_sources
        assert any("owa/t12" in note for note in cortex.pending_sources)

    def test_every_cited_source_is_actually_readable_from_here(self) -> None:
        cortex = rc.load_rate_config().rate("cortex")
        reachable = [REPO_ROOT / source for source in cortex.sources]
        assert reachable, "the cortex measurement cites nothing readable from this branch"
        for path in reachable:
            assert path.is_file(), f"cited source is missing: {path}"

    def test_the_published_figures_appear_verbatim_in_a_cited_doc(self) -> None:
        """The cortex numbers are pinned to a citation, not to a literal here.

        `corrections.md` §5 publishes the band as *"21.5-25.4 tok/s"*, so the
        config's own ``cited_as`` and ``cited_fastest_as`` must be the figures
        that doc carries. Re-measure the cortex and this fails until the config
        and the doc it cites agree again — which is the point: a rate that
        drifts away from its own published citation is exactly c39.
        """
        cortex = rc.load_rate_config().rate("cortex")
        corrections = (RESULTS_DIR / "corrections.md").read_text(encoding="utf-8")
        assert str(cortex.cited_as) in corrections
        assert str(cortex.cited_fastest_as) in corrections

    def test_the_published_figures_are_the_recomputed_ones_rounded(self) -> None:
        """`cited_as` is a rounding of the measurement, never an independent number."""
        cortex = rc.load_rate_config().rate("cortex")
        assert cortex.cited_as == round(cortex.slowest_tok_s, 1)
        assert cortex.cited_fastest_as == round(cortex.fastest_tok_s, 1)


# ── criterion 3: the re-derivation procedure, beside the config ──────────────


class TestProcedureDocumented:
    def test_the_procedure_sits_beside_the_config(self) -> None:
        assert rc.PROCEDURE_DOC_PATH.parent == rc.DEFAULT_CONFIG_PATH.parent
        assert rc.PROCEDURE_DOC_PATH.is_file()

    def test_it_tells_a_reader_how_to_re_measure_each_role(self) -> None:
        text = rc.PROCEDURE_DOC_PATH.read_text(encoding="utf-8")
        for needle in (
            "worker_throughput",
            "worker-throughput.jsonl",
            "EMBODIMENT_LIVE_RIG",
            rc.DEFAULT_CONFIG_PATH.name,
        ):
            assert needle in text, f"the procedure never mentions {needle!r}"

    def test_it_names_what_triggers_a_re_measurement(self) -> None:
        text = rc.PROCEDURE_DOC_PATH.read_text(encoding="utf-8").lower()
        assert "concurren" in text
        assert "re-measure" in text

    def test_the_config_points_at_the_procedure_and_back(self) -> None:
        payload = json.loads(rc.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        assert payload["procedure"] == rc.PROCEDURE_DOC_NAME
        assert rc.DEFAULT_CONFIG_PATH.name in rc.PROCEDURE_DOC_PATH.read_text(encoding="utf-8")
