"""Task t2 — worker dial config, per-call metering, and the wiring-smoke lane.

Everything below except :class:`TestLiveWorkerSmoke` is hermetic: no test in
this module reaches a socket unless ``EMBODIMENT_LIVE_RIG=1`` is set, matching
the convention every other live-gated module in this repo pins
(``tests/test_league_h2h.py``, ``tests/test_association_work.py``,
``tests/test_muse_challenge.py``).

Three acceptance criteria, three test classes carry the weight:

* ``TestNoSilentFallback`` — criterion 1: the worker endpoint/model come ONLY
  from explicit flags/env; absence is a recorded ``ABSENT`` degradation, never
  a silent substitution of the spark gateway.
* ``TestWorkerSeamMetering`` — criterion 3: every call records ``finish_reason``
  and token counts, since ``embodiment.contract.ModelResponse`` deliberately
  carries neither (issue #37).
* ``TestSmokeLane`` (hermetic) plus ``TestLiveWorkerSmoke`` (opt-in) —
  criterion 2: a bare completion and a bounded tool loop, through the exact
  path future measured arms reuse.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from embodiment.loop import EXIT_FINISHED  # noqa: E402
from examples import worker_seam as ws  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── criterion 1: no silent fallback, ever ─────────────────────────────────────


class TestNoSilentFallback:
    """The worker endpoint/model come ONLY from explicit flags/env.

    ``SPARK_GATEWAY_URL`` — the endpoint every OTHER harness in this repo
    defaults to — must never appear as a resolved value when the worker is
    unconfigured. Absence is always a returned :class:`WorkerConfigResolution`
    with ``config=None`` and one :class:`WorkerDegradation` per missing input,
    never a raised exception and never a substituted string.
    """

    def test_spark_gateway_constant_is_named_and_distinct(self) -> None:
        # Sanity: the two constants this whole criterion turns on are not
        # accidentally the same string.
        assert ws.SPARK_GATEWAY_URL == "http://localhost:8001/v1"
        assert ws.THOR_WORKER_URL_DOCUMENTED != ws.SPARK_GATEWAY_URL

    def test_completely_empty_env_and_flags_yields_absent_with_no_fallback(self) -> None:
        resolution = ws.resolve_worker_config(env={})

        assert resolution.ok is False
        assert resolution.config is None
        codes = {d.code for d in resolution.degradations}
        assert ws.DEGRADED_WORKER_URL_ABSENT in codes
        assert ws.DEGRADED_WORKER_MODEL_ABSENT in codes
        assert ws.DEGRADED_WORKER_API_KEY_ABSENT in codes
        # The one invariant the whole criterion exists to hold: nothing in the
        # resolution mentions the spark gateway.
        assert ws.SPARK_GATEWAY_URL not in json.dumps(resolution.to_dict())

    def test_an_unrelated_env_var_pointing_at_the_spark_gateway_is_never_read(self) -> None:
        # A host that also runs `examples/proof.py` or `league_seat.py` will
        # have EMBODIMENT_BASE_URL=http://localhost:8001/v1 in its shell.
        # This module must not treat that as satisfying its OWN config.
        env = {"EMBODIMENT_BASE_URL": ws.SPARK_GATEWAY_URL}
        resolution = ws.resolve_worker_config(env=env)

        assert resolution.ok is False
        assert any(d.code == ws.DEGRADED_WORKER_URL_ABSENT for d in resolution.degradations)

    def test_url_absent_alone_is_recorded_even_with_model_and_key_present(self) -> None:
        env = {ws.WORKER_MODEL_ENV: "m", ws.API_KEY_ENV: "k"}
        resolution = ws.resolve_worker_config(env=env)

        assert resolution.ok is False
        assert [d.code for d in resolution.degradations] == [ws.DEGRADED_WORKER_URL_ABSENT]

    def test_model_absent_alone_is_recorded_even_with_url_and_key_present(self) -> None:
        env = {ws.WORKER_URL_ENV: "http://thor:8000/v1", ws.API_KEY_ENV: "k"}
        resolution = ws.resolve_worker_config(env=env)

        assert resolution.ok is False
        assert [d.code for d in resolution.degradations] == [ws.DEGRADED_WORKER_MODEL_ABSENT]

    def test_api_key_absent_alone_is_recorded_even_with_url_and_model_present(self) -> None:
        env = {ws.WORKER_URL_ENV: "http://thor:8000/v1", ws.WORKER_MODEL_ENV: "m"}
        resolution = ws.resolve_worker_config(env=env)

        assert resolution.ok is False
        assert [d.code for d in resolution.degradations] == [ws.DEGRADED_WORKER_API_KEY_ABSENT]

    def test_env_alone_resolves_a_full_config(self) -> None:
        env = {
            ws.WORKER_URL_ENV: ws.THOR_WORKER_URL_DOCUMENTED,
            ws.WORKER_MODEL_ENV: ws.THOR_WORKER_MODEL_DOCUMENTED,
            ws.API_KEY_ENV: "secret",
        }
        resolution = ws.resolve_worker_config(env=env)

        assert resolution.ok is True
        assert resolution.config is not None
        assert resolution.config.base_url == ws.THOR_WORKER_URL_DOCUMENTED
        assert resolution.config.model == ws.THOR_WORKER_MODEL_DOCUMENTED
        assert resolution.config.api_key == "secret"
        assert resolution.degradations == ()

    def test_explicit_flag_wins_over_env(self) -> None:
        env = {
            ws.WORKER_URL_ENV: "http://env-url:8000/v1",
            ws.WORKER_MODEL_ENV: "env-model",
            ws.API_KEY_ENV: "env-key",
        }
        resolution = ws.resolve_worker_config(
            cli_url="http://flag-url:8000/v1",
            cli_model="flag-model",
            cli_api_key="flag-key",
            env=env,
        )

        assert resolution.config is not None
        assert resolution.config.base_url == "http://flag-url:8000/v1"
        assert resolution.config.model == "flag-model"
        assert resolution.config.api_key == "flag-key"

    def test_config_never_serializes_the_api_key(self) -> None:
        resolution = ws.resolve_worker_config(
            cli_url="http://thor:8000/v1", cli_model="m", cli_api_key="top-secret", env={}
        )
        assert resolution.config is not None
        assert "top-secret" not in json.dumps(resolution.config.to_dict())
        assert "top-secret" not in json.dumps(resolution.to_dict())

    def test_a_non_http_scheme_degrades_rather_than_silently_dialling(self) -> None:
        resolution = ws.resolve_worker_config(
            cli_url="ftp://thor:8000/v1", cli_model="m", cli_api_key="k", env={}
        )
        assert resolution.ok is False
        assert resolution.degradations[0].code == ws.DEGRADED_WORKER_URL_INVALID

    def test_degradation_records_are_plain_dicts_a_host_can_see(self) -> None:
        resolution = ws.resolve_worker_config(env={})
        for degradation in resolution.degradations:
            payload = degradation.to_dict()
            assert payload["code"]
            assert payload["detail"]


class TestCliRefusesToDialWithoutConfig:
    """The CLI surface of criterion 1: ``main()`` never reaches the network
    when config is absent — it prints the degradation and exits non-zero."""

    def test_missing_everything_exits_2_and_prints_degradations_never_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.delenv(ws.WORKER_URL_ENV, raising=False)
        monkeypatch.delenv(ws.WORKER_MODEL_ENV, raising=False)
        monkeypatch.delenv(ws.API_KEY_ENV, raising=False)

        exit_code = ws.main([])

        assert exit_code == 2
        out = capsys.readouterr()
        payload = json.loads(out.out)
        assert payload["ok"] is False
        assert ws.SPARK_GATEWAY_URL not in out.out
        assert "Traceback" not in out.err

    def test_missing_config_never_calls_run_smoke(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        monkeypatch.delenv(ws.WORKER_URL_ENV, raising=False)
        monkeypatch.delenv(ws.WORKER_MODEL_ENV, raising=False)
        monkeypatch.delenv(ws.API_KEY_ENV, raising=False)

        def _boom(*_a: Any, **_k: Any) -> None:
            raise AssertionError("run_smoke must not be called when config is absent")

        monkeypatch.setattr(ws, "run_smoke", _boom)

        assert ws.main([]) == 2
        capsys.readouterr()

    def test_full_config_calls_run_smoke_and_writes_out_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
    ) -> None:
        # Hermetic: run_smoke itself is stubbed so this test proves CLI wiring
        # (arg parsing, exit code, --out) without touching the network.
        seen: dict[str, Any] = {}

        def _fake_run_smoke(config: ws.WorkerConfig, **kwargs: Any) -> dict[str, Any]:
            seen["config"] = config
            seen["kwargs"] = kwargs
            return {"kind": "worker-smoke", "worker": config.to_dict()}

        monkeypatch.setattr(ws, "run_smoke", _fake_run_smoke)
        out_path = tmp_path / "smoke.json"

        exit_code = ws.main(
            [
                "--worker-url",
                "http://thor:8000/v1",
                "--worker-model",
                "m",
                "--out",
                str(out_path),
            ]
        )

        assert exit_code == 0
        assert seen["config"].base_url == "http://thor:8000/v1"
        assert out_path.exists()
        written = json.loads(out_path.read_text(encoding="utf-8"))
        assert written["kind"] == "worker-smoke"
        printed = json.loads(capsys.readouterr().out)
        assert printed == written


# ── criterion 3: per-call finish_reason + token records ──────────────────────


class TestParseCompletion:
    def test_reasoning_is_kept_separate_from_content(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {"content": "42", "reasoning_content": "thinking..."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
        reply = ws.parse_completion(payload)
        assert reply.content == "42"
        assert reply.reasoning == "thinking..."
        assert reply.prompt_tokens == 5
        assert reply.completion_tokens == 7

    def test_malformed_tool_call_arguments_degrade_to_empty_dict(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "add", "arguments": "not-json"}}
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        reply = ws.parse_completion(payload)
        assert reply.tool_calls[0].name == "add"
        assert reply.tool_calls[0].arguments == {}

    def test_well_formed_tool_call_round_trips(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {"name": "add", "arguments": '{"a": 17, "b": 25}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        reply = ws.parse_completion(payload)
        assert reply.tool_calls[0].arguments == {"a": 17, "b": 25}


class TestWorkerSeamMetering:
    """Every call lands one record with ``finish_reason`` and token counts."""

    def _seam(self, **overrides: Any) -> ws.WorkerSeam:
        kwargs: dict[str, Any] = dict(
            base_url="http://x/v1",
            model="m",
            api_key="k",
            role="worker",
            max_tokens=16000,
            temperature=0.3,
        )
        kwargs.update(overrides)
        return ws.WorkerSeam(**kwargs)

    def test_a_normal_turn_records_finish_reason_and_tokens(self) -> None:
        seam = self._seam()
        seam._post = lambda body: {  # type: ignore[method-assign]
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        }
        reply = seam([{"role": "user", "content": "hi"}])

        assert reply.content == "ok"
        assert seam.meter.finish_reasons == {"stop": 1}
        assert seam.meter.prompt_tokens == 11
        assert seam.meter.completion_tokens == 3
        assert seam.meter.transcript[0]["finish_reason"] == "stop"
        assert seam.meter.transcript[0]["prompt_tokens"] == 11
        assert seam.meter.transcript[0]["completion_tokens"] == 3

    def test_a_truncated_turn_is_counted_and_announced(self, capsys: Any) -> None:
        seam = self._seam()
        seam._post = lambda body: {  # type: ignore[method-assign]
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 16000},
        }
        seam([{"role": "user", "content": "hi"}])

        assert seam.meter.truncated == 1
        assert seam.meter.finish_reasons["length"] == 1
        # Nothing degrades silently (C3).
        assert "truncated" in capsys.readouterr().err

    def test_a_normal_turn_counts_no_truncation(self) -> None:
        seam = self._seam()
        seam._post = lambda body: {  # type: ignore[method-assign]
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        seam([{"role": "user", "content": "hi"}])
        assert seam.meter.truncated == 0

    def test_empty_content_and_no_tool_calls_is_flagged(self) -> None:
        seam = self._seam()
        seam._post = lambda body: {  # type: ignore[method-assign]
            "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
            "usage": {},
        }
        seam([{"role": "user", "content": "hi"}])
        assert seam.meter.empty_content == 1

    def test_tools_key_is_present_only_when_a_schema_was_passed(self) -> None:
        bare = self._seam(tools=None)
        with_tools = self._seam(tools=ws.SMOKE_TOOL_SCHEMA)
        seen: dict[str, Any] = {}

        def _capture(body: dict[str, Any]) -> dict[str, Any]:
            seen["body"] = body
            return {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {},
            }

        bare._post = _capture  # type: ignore[method-assign]
        bare([{"role": "user", "content": "hi"}])
        assert "tools" not in seen["body"]

        with_tools._post = _capture  # type: ignore[method-assign]
        with_tools([{"role": "user", "content": "hi"}])
        assert seen["body"]["tools"] == ws.SMOKE_TOOL_SCHEMA

    def test_retries_are_counted_and_bounded_then_raise(self) -> None:
        seam = self._seam(sleep=lambda _seconds: None)
        attempts = {"n": 0}

        def _always_fails(body: dict[str, Any]) -> dict[str, Any]:
            attempts["n"] += 1
            raise urllib.error.URLError("connection refused")

        seam._post = _always_fails  # type: ignore[method-assign]

        with pytest.raises(ws.WorkerTransportError):
            seam([{"role": "user", "content": "hi"}])

        assert attempts["n"] == ws.MAX_TRANSPORT_RETRIES + 1
        assert seam.meter.retries == ws.MAX_TRANSPORT_RETRIES + 1
        assert seam.meter.failures == 1
        assert seam.meter.calls == 0

    def test_a_transient_failure_then_success_is_counted_but_still_returns(self) -> None:
        seam = self._seam(sleep=lambda _seconds: None)
        attempts = {"n": 0}

        def _fails_once(body: dict[str, Any]) -> dict[str, Any]:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise urllib.error.URLError("timeout")
            return {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

        seam._post = _fails_once  # type: ignore[method-assign]
        reply = seam([{"role": "user", "content": "hi"}])

        assert reply.content == "ok"
        assert seam.meter.retries == 1
        assert seam.meter.calls == 1
        assert seam.meter.failures == 0

    def test_invalid_base_url_scheme_raises_at_construction(self) -> None:
        with pytest.raises(ValueError):
            ws.WorkerSeam(base_url="ftp://x/v1", model="m", api_key="k", max_tokens=100)

    def test_meter_to_dict_carries_every_criterion_3_field(self) -> None:
        meter = ws.Meter(role="worker", model="m")
        payload = meter.to_dict()
        for key in ("finish_reasons", "prompt_tokens", "completion_tokens", "truncated"):
            assert key in payload


# ── criterion 2: the smoke lane, hermetic drive ───────────────────────────────


class TestSmokeBench:
    def test_add_computes_the_exact_sum(self) -> None:
        bench = ws.SmokeBench()
        outcome = bench.execute("add", {"a": 17, "b": 25})
        assert outcome.result == "42"
        assert outcome.finished is False

    def test_finish_marks_the_tool_outcome_finished(self) -> None:
        bench = ws.SmokeBench()
        outcome = bench.execute("finish", {"answer": 42})
        assert outcome.finished is True
        assert outcome.finish_summary == "42"

    def test_non_integer_arguments_degrade_to_a_message_not_a_raise(self) -> None:
        bench = ws.SmokeBench()
        outcome = bench.execute("add", {"a": "not-a-number", "b": 1})
        assert "integers" in outcome.result

    def test_unknown_tool_reports_rather_than_raises(self) -> None:
        bench = ws.SmokeBench()
        outcome = bench.execute("mystery", {})
        assert "unknown tool" in outcome.result


class TestSmokeLaneHermetic:
    """Both smoke calls, driven through a scripted transport. No network."""

    def test_bare_completion_puts_no_tools_key_on_the_wire(self) -> None:
        config = ws.WorkerConfig(base_url="http://x/v1", model="m", api_key="k")
        seen: dict[str, Any] = {}
        original_init = ws.WorkerSeam.__init__

        def _patched_init(self: ws.WorkerSeam, **kwargs: Any) -> None:
            original_init(self, **kwargs)

            def _post(body: dict[str, Any]) -> dict[str, Any]:
                seen["body"] = body
                return {
                    "choices": [{"message": {"content": "WORKER-OK"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 4},
                }

            self._post = _post  # type: ignore[method-assign]

        import unittest.mock as mock

        with mock.patch.object(ws.WorkerSeam, "__init__", _patched_init):
            record = ws.run_bare_completion(config, max_tokens=100, temperature=0.1)

        assert record["kind"] == "bare-completion"
        assert record["content"] == "WORKER-OK"
        assert record["cost"]["finish_reasons"] == {"stop": 1}
        assert "tools" not in seen["body"]

    def test_tool_loop_round_trips_a_call_and_finishes_cleanly(self) -> None:
        config = ws.WorkerConfig(base_url="http://x/v1", model="m", api_key="k")
        bodies: list[dict[str, Any]] = []

        def _scripted_post(body: dict[str, Any]) -> dict[str, Any]:
            bodies.append(body)
            if len(bodies) == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "function": {
                                            "name": "add",
                                            "arguments": '{"a": 17, "b": 25}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                }
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c2",
                                    "function": {"name": "finish", "arguments": '{"answer": 42}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 5},
            }

        original_init = ws.WorkerSeam.__init__

        def _patched_init(self: ws.WorkerSeam, **kwargs: Any) -> None:
            original_init(self, **kwargs)
            self._post = _scripted_post  # type: ignore[method-assign]

        import unittest.mock as mock

        with mock.patch.object(ws.WorkerSeam, "__init__", _patched_init):
            record = ws.run_tool_loop(config, max_tokens=1000, temperature=0.1, max_steps=6)

        # The schema was on the wire for both turns.
        assert all(body.get("tools") == ws.SMOKE_TOOL_SCHEMA for body in bodies)
        # The tool call round-tripped and the loop finished cleanly.
        assert record["tools_called"] == ["add", "finish"]
        assert record["exit_reason"] == EXIT_FINISHED
        assert record["aborted"] is None
        assert record["cost"]["finish_reasons"] == {"tool_calls": 2}
        assert record["cost"]["calls"] == 2
        assert len(record["transcript"]) == 2


# ── module-level sanity: names this module promises exist ────────────────────


class TestPublicSurface:
    def test_smoke_tool_schema_names_exactly_add_and_finish(self) -> None:
        names = {entry["function"]["name"] for entry in ws.SMOKE_TOOL_SCHEMA}
        assert names == {"add", "finish"}

    def test_run_smoke_bundles_both_calls_and_the_config_used(self) -> None:
        import unittest.mock as mock

        config = ws.WorkerConfig(base_url="http://x/v1", model="m", api_key="k")
        with (
            mock.patch.object(
                ws, "run_bare_completion", return_value={"kind": "bare-completion"}
            ) as bare,
            mock.patch.object(ws, "run_tool_loop", return_value={"kind": "tool-loop"}) as loop,
        ):
            report = ws.run_smoke(config, max_tokens=123, temperature=0.5, max_steps=4)

        bare.assert_called_once()
        loop.assert_called_once()
        assert report["kind"] == "worker-smoke"
        assert report["worker"] == config.to_dict()
        assert report["max_tokens"] == 123
        assert report["bare_completion"] == {"kind": "bare-completion"}
        assert report["tool_loop"] == {"kind": "tool-loop"}


# ── the live lane is opt-in, and every live class says so in its name ────────


def test_every_skip_gated_class_in_this_module_is_live_prefixed() -> None:
    """A live class not named ``TestLive*`` keeps the network bomb and lies."""
    for name, value in vars(sys.modules[__name__]).items():
        if isinstance(value, type) and getattr(value, "pytestmark", None):
            assert name.startswith("TestLive"), f"{name} is skip-gated but not TestLive-prefixed"


LIVE_ENABLED = os.environ.get("EMBODIMENT_LIVE_RIG") == "1"
LIVE_KEY = os.environ.get(ws.API_KEY_ENV, "")


@pytest.mark.skipif(not LIVE_ENABLED, reason="set EMBODIMENT_LIVE_RIG=1 to test the real rig")
@pytest.mark.skipif(not LIVE_KEY, reason=f"{ws.API_KEY_ENV} is not set")
class TestLiveWorkerSmoke:
    """The real dial, against Thor. Small and cheap, on purpose.

    Uses the module's own env-driven resolution
    (:data:`worker_seam.WORKER_URL_ENV` / :data:`worker_seam.WORKER_MODEL_ENV`)
    rather than the documented constants directly, so this test also exercises
    criterion 1's resolution path end to end. If those env vars are not set,
    it falls back to the documented Thor address FOR THIS TEST ONLY — never
    inside the module itself.
    """

    def _config(self) -> ws.WorkerConfig:
        resolution = ws.resolve_worker_config(
            cli_url=os.environ.get(ws.WORKER_URL_ENV) or ws.THOR_WORKER_URL_DOCUMENTED,
            cli_model=os.environ.get(ws.WORKER_MODEL_ENV) or ws.THOR_WORKER_MODEL_DOCUMENTED,
            cli_api_key=LIVE_KEY,
        )
        assert resolution.config is not None, resolution.to_dict()
        return resolution.config

    def test_a_bare_completion_reaches_the_real_worker(self) -> None:
        record = ws.run_bare_completion(self._config(), max_tokens=64, temperature=0.1)
        assert record["cost"]["calls"] == 1
        assert record["transcript"][0]["finish_reason"]

    def test_a_bounded_tool_loop_round_trips_and_finishes(self) -> None:
        record = ws.run_tool_loop(self._config(), max_tokens=16000, temperature=0.1, max_steps=6)
        assert record["cost"]["calls"] >= 1
        assert record["transcript"], "at least one recorded call"
        for entry in record["transcript"]:
            assert entry["finish_reason"]
