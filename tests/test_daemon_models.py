"""Daemon wiring B (t12): the conversation's model, its distinct failure, its trace, the gate.

Pinned on the REAL gateway seam (:func:`app_module.gateway_seam` over
:func:`app_module.http_complete`) with ``urllib.request.urlopen`` replaced, so a test
reads the request the gateway would have received (its ``model`` and its timeout)
and nothing reaches a real gateway:

* the seam and the voice path read the turn's role from ONE holder; the session
  summary stays on senses whatever the conversation chose;
* a 404 ``role_infeasible``, 503 ``role_unverified`` or 429 from the chosen role is
  ``app-text-model-unavailable`` (never the gateway-down ``turn-seam-aborted``), and
  the gateway sees exactly one request;
* every assistant record names the role, model and host the capabilities snapshot
  reported; stt, tts, realtime and recall see the same calls whatever role is chosen;
* a spoken turn on an unmeasured role asks it nothing and says so; once a probe is
  recorded it uses the role under the derived deadline (plan risk r2: h45 in the
  measured state, beside h49).
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from embodiment import voice_latency as vl
from embodiment.capabilities import ChatRole, Snapshot
from embodiment.daemon import app as app_module
from embodiment.daemon.state import SESSIONS_DIRNAME, DaemonState
from embodiment.memory import MemorySeams, RoomMemory, SemanticRecall
from embodiment.turn import DEGRADED_SEAM_ABORTED
from tests import test_daemon_app as base
from tests.test_daemon_app import REPLY, SPEECH, FakeEndpoint

harness = base.harness

TYPED = "מה שלומך?"
SENSES = ChatRole(role="senses", model="nvidia/Gemma-4-26B-A4B-NVFP4", ready=True, host="local")
WORKER = ChatRole(role="worker", model="nvidia/Qwen3.6-35B-A3B-NVFP4", ready=True, host="thor")


class FakeCapabilities:
    """The poller's surface: a fixed snapshot, start/stop counted."""

    def __init__(self, roles: tuple = (SENSES, WORKER)) -> None:
        self.roles = roles
        self.started = 0
        self.stopped = 0

    def snapshot(self) -> Snapshot:
        return Snapshot(roles=self.roles, age=1.0, stale=False)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


class Gateway:
    """``urlopen`` replaced: every chat request is kept (body + timeout) and answered."""

    def __init__(self, reply: str = REPLY) -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []
        self.fail: Optional[BaseException] = None

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        body = json.loads(request.data.decode("utf-8"))
        self.requests.append({"body": body, "timeout": timeout, "url": request.full_url})
        if self.fail is not None:
            raise self.fail
        payload = json.dumps(
            {"choices": [{"message": {"content": self.reply}}], "usage": {}}
        ).encode("utf-8")

        class Response:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *args: Any) -> bool:
                return False

            def read(self, *args: Any) -> bytes:
                return payload

        return Response()

    def models(self) -> list[str]:
        return [r["body"]["model"] for r in self.requests]

    def chat(self) -> list[dict[str, Any]]:
        """Requests that are not the session summary."""
        return [
            r
            for r in self.requests
            if r["body"]["messages"][0].get("content") != app_module.SUMMARY_PROMPT
        ]


class Spawned:
    """A ``probe_spawn`` that holds each job until the test runs it."""

    def __init__(self) -> None:
        self.jobs: list[Any] = []

    def __call__(self, job: Any) -> None:
        self.jobs.append(job)

    def run_all(self) -> None:
        jobs, self.jobs = self.jobs, []
        for job in jobs:
            job()


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Gateway:
    fake = Gateway()
    monkeypatch.setattr(app_module.urllib.request, "urlopen", fake)
    return fake


def _wired(harness: Any, tmp_path: Path, **overrides: Any) -> Any:
    """A harness whose seam and summariser are the shipped ones, over the fake gateway."""
    config = overrides.pop("config", None) or app_module.AppConfig(
        poll_interval_s=0.01, gateway_url="http://gateway.invalid"
    )
    holder = app_module.RoleHolder(config.role, config.completion_deadline)
    spawned = overrides.pop("spawned", None) or Spawned()
    capabilities = overrides.pop("capabilities", None) or FakeCapabilities()
    lanes = app_module.AppLanes(
        recall_spawn=base._sync_spawn,
        capabilities=capabilities,
        probe_spawn=spawned,
        role_holder=holder,
        **overrides.pop("lanes_extra", {}),
    )
    h = harness(
        config=config,
        complete=app_module.gateway_seam(config, holder),
        summarise=app_module.gateway_summariser(config),
        lanes=lanes,
        **overrides,
    )
    h.spawned = spawned
    h.capabilities = capabilities
    return h


def _wav() -> bytes:
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * 2400)
    return buf.getvalue()


def _measure(h: Any, role: str = "worker", seconds: float = 1.5) -> float:
    for _ in range(vl.MEASURED_MIN_SAMPLES):
        assert h.app.voice_latency.add_sample(role, seconds)
    deadline = h.app.voice_latency.spoken_deadline(role)
    assert deadline is not None
    return deadline


def _assistant_records(state: DaemonState) -> list[dict[str, Any]]:
    out = []
    for path in sorted((Path(state.dir) / SESSIONS_DIRNAME).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("role") == "assistant":
                out.append({**row, "_log": path.stem})
    return out


# ── one holder: the seam and the voice path; summary stays on senses ─────────


class TestOneHolder:
    def test_the_default_is_senses_and_its_spoken_bound_is_unchanged(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        assert h.app.conversation_role() == "senses"
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        (request,) = gateway.chat()
        assert request["body"]["model"] == "senses"
        assert request["timeout"] == app_module.AppConfig().completion_deadline == 60.0

    def test_a_typed_turn_asks_the_chosen_role(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        assert h.app.set_conversation_role("worker") == "worker"
        assert h.app.conversation_role() == "worker"
        assert h.app.controls().chat(TYPED)["ok"]
        assert gateway.models() == ["worker"]

    def test_the_session_summary_names_senses_with_worker_chosen(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("worker")
        assert h.app.controls().chat(TYPED)["ok"]
        h.app.close(deadline=3.0)
        summaries = [
            r
            for r in gateway.requests
            if r["body"]["messages"][0].get("content") == app_module.SUMMARY_PROMPT
        ]
        assert summaries, "the summariser ran"
        assert {r["body"]["model"] for r in summaries} == {"senses"}
        assert [r["body"]["model"] for r in gateway.chat()] == ["worker"]

    def test_a_persona_default_role_is_the_conversation_default(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.personas.create(persona_id="ada", name="Ada", language="en", default_role="worker")
        h.app.set_conversation_persona("ada")
        assert h.app.conversation_role() == "worker"
        h.app.controls().chat(TYPED)
        assert gateway.models() == ["worker"]

    def test_an_unknown_role_is_refused_and_changes_nothing(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        assert h.app.set_conversation_role("cortex") is None
        assert h.app.set_conversation_role("../x") is None
        assert h.app.conversation_role() == "senses"
        assert h.spawned.jobs == []

    def test_continuing_restores_the_role(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("worker")
        assert h.app.controls().chat(TYPED)["ok"]
        old = _assistant_records(h.state)[-1]["_log"]
        h.app.controls().new_conversation()
        h.app.controls().chat(TYPED)
        assert gateway.chat()[-1]["body"]["model"] == "senses"
        assert h.app.conversation_role(old) == "worker"
        assert h.app.controls().chat(TYPED, conversation=old)["ok"]
        assert gateway.chat()[-1]["body"]["model"] == "worker"

    def test_continuing_restores_the_role_after_a_restart(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        state_dir = tmp_path / "shared"
        first = _wired(harness, tmp_path, state=DaemonState(state_dir))
        first.app.set_conversation_role("worker")
        first.app.controls().chat(TYPED)
        old = _assistant_records(first.state)[-1]["_log"]
        second = _wired(harness, tmp_path, state=DaemonState(state_dir))
        assert second.app.conversation_role(old) == "worker"
        second.app.controls().chat(TYPED, conversation=old)
        assert gateway.chat()[-1]["body"]["model"] == "worker"

    def test_the_capabilities_poller_starts_and_stops_with_the_daemon(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.start()
        assert h.capabilities.started == 1
        h.app.close(deadline=3.0)
        assert h.capabilities.stopped == 1


# ── the chosen role's refusal: its own code, one request ─────────────────────


def _http_error(status: int, body: dict[str, Any]) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://gateway.invalid/v1/chat/completions",
        status,
        "refused",
        {},  # type: ignore[arg-type]
        io.BytesIO(json.dumps(body).encode("utf-8")),
    )


UNAVAILABLE = [
    pytest.param(404, {"error": "role_infeasible"}, id="404-role_infeasible"),
    pytest.param(503, {"error": {"code": "role_unverified"}}, id="503-role_unverified"),
    pytest.param(429, {"error": "rate_limited"}, id="429"),
]


class TestTextModelUnavailable:
    @pytest.mark.parametrize("status,body", UNAVAILABLE)
    def test_http_complete_names_the_role_and_the_status(
        self, gateway: Gateway, status: int, body: dict[str, Any]
    ) -> None:
        gateway.fail = _http_error(status, body)
        with pytest.raises(app_module.TextModelUnavailable) as raised:
            app_module.http_complete([], gateway_url="http://gateway.invalid", role="worker")
        assert (raised.value.role, raised.value.status) == ("worker", status)
        assert len(gateway.requests) == 1

    def test_another_404_is_not_classified(self, gateway: Gateway) -> None:
        gateway.fail = _http_error(404, {"error": "not_found"})
        with pytest.raises(urllib.error.HTTPError) as raised:
            app_module.http_complete([], gateway_url="http://gateway.invalid", role="worker")
        assert not isinstance(raised.value, app_module.TextModelUnavailable)

    @pytest.mark.parametrize("status,body", UNAVAILABLE)
    def test_a_typed_turn_answers_the_distinct_code_after_one_request(
        self,
        harness: Any,
        tmp_path: Path,
        gateway: Gateway,
        status: int,
        body: dict[str, Any],
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("worker")
        gateway.fail = _http_error(status, body)
        answer = h.app.controls().chat(TYPED)
        assert answer["code"] == app_module.APP_TEXT_MODEL_UNAVAILABLE
        assert answer["ok"] is False
        assert "worker" in answer["reply"]
        assert answer["reply"] != app_module.FALLBACK_TEXT
        assert len(gateway.chat()) == 1, "never retried, never sent to another role"
        assert gateway.models() == ["worker"]
        codes = h.ledger_codes()
        assert app_module.APP_TEXT_MODEL_UNAVAILABLE in codes
        assert DEGRADED_SEAM_ABORTED not in codes

    @pytest.mark.parametrize("status,body", UNAVAILABLE)
    def test_a_spoken_turn_says_so_after_one_request(
        self,
        harness: Any,
        tmp_path: Path,
        gateway: Gateway,
        status: int,
        body: dict[str, Any],
    ) -> None:
        h = _wired(harness, tmp_path)
        _measure(h)
        h.app.set_conversation_role("worker")
        gateway.fail = _http_error(status, body)
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn(SPEECH)
        assert "worker" in result.spoken
        assert len(gateway.chat()) == 1
        assert app_module.APP_TEXT_MODEL_UNAVAILABLE in h.ledger_codes()

    def test_the_gateway_down_path_keeps_its_own_code(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("worker")
        gateway.fail = urllib.error.URLError("refused")
        answer = h.app.controls().chat(TYPED)
        assert answer["code"] != app_module.APP_TEXT_MODEL_UNAVAILABLE
        assert answer["reply"] == app_module.FALLBACK_TEXT
        codes = h.ledger_codes()
        assert DEGRADED_SEAM_ABORTED in codes
        assert app_module.APP_TEXT_MODEL_UNAVAILABLE not in codes


# ── the trace, and the lanes the role must not touch ─────────────────────────


class TestTrace:
    def test_every_assistant_record_names_role_model_and_host(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        h.app.controls().chat(TYPED)
        _measure(h)
        h.app.set_conversation_role("worker")
        h.app.run_turn(SPEECH)
        h.app.controls().chat(TYPED)
        traces = [
            (
                r[app_module.CHAT_ROLE_FIELD],
                r[app_module.CHAT_MODEL_FIELD],
                r[app_module.CHAT_HOST_FIELD],
            )
            for r in _assistant_records(h.state)
        ]
        senses = (SENSES.role, SENSES.model, SENSES.host)
        worker = (WORKER.role, WORKER.model, WORKER.host)
        assert traces == [senses, senses, worker, worker]

    def test_a_role_the_snapshot_does_not_list_records_no_invented_model(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path, capabilities=FakeCapabilities(roles=()))
        h.app.controls().chat(TYPED)
        (record,) = _assistant_records(h.state)
        assert record[app_module.CHAT_ROLE_FIELD] == "senses"
        assert record[app_module.CHAT_MODEL_FIELD] is None
        assert record[app_module.CHAT_HOST_FIELD] is None

    def test_stt_tts_realtime_and_recall_are_unchanged_by_the_role(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        seen: dict[str, list[Any]] = {"tts": [], "stt": [], "recall": []}

        def run(role: Optional[str]) -> dict[str, list[Any]]:
            for key in seen:
                seen[key] = []
            memory = RoomMemory(
                tmp_path / f"memory-{role}",
                scope="gwen",
                seams=MemorySeams(
                    recall_fn=lambda query, **kw: (
                        seen["recall"].append((query, sorted(kw)))
                        or SimpleNamespace(ok=True, records=[], degradation=None)
                    ),
                    remember_fn=lambda text, **kw: SimpleNamespace(
                        ok=True, record_id="r1", degradation=None
                    ),
                ),
                semantic=SemanticRecall(embed_probe=lambda: False),
            )
            h = _wired(
                harness,
                tmp_path,
                memory=memory,
                synthesize=lambda sentence, config: (
                    seen["tts"].append((sentence, config)) or b"\x00\x00"
                ),
                lanes_extra={
                    "stt": lambda audio, filename, content_type: (
                        seen["stt"].append((audio, filename, content_type)) or TYPED
                    )
                },
            )
            if role is not None:
                _measure(h, role)
                h.app.set_conversation_role(role)
            h.app.attach_ear("host", FakeEndpoint())
            h.app.run_turn(SPEECH)
            assert h.app.controls().clip(_wav(), None)["ok"]
            return {k: list(v) for k, v in seen.items()} | {"rates": list(h.declared_rates)}

        before = run(None)
        after = run("worker")
        assert before["tts"]
        assert before["stt"]
        assert before["recall"]
        assert after == before


# ── the voice gate ───────────────────────────────────────────────────────────


class TestVoiceGate:
    def test_choosing_an_unmeasured_role_probes_once_in_the_background(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("worker")
        h.app.set_conversation_role("worker")
        assert len(h.spawned.jobs) == 1, "concurrent picks do not double-probe"
        assert gateway.requests == [], "the probe is in the background"
        h.spawned.run_all()
        assert gateway.models() == ["worker"] * vl.MEASURED_MIN_SAMPLES
        assert h.app.voice_latency.state("worker") == vl.MEASURED

    def test_senses_is_exempt_and_never_probed(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("senses")
        assert h.spawned.jobs == []
        h.app.attach_ear("host", FakeEndpoint())
        assert h.app.run_turn(SPEECH).spoken == REPLY

    def test_a_spoken_turn_while_probing_asks_nothing_and_says_probing(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("worker")  # the probe is held, not run
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn(SPEECH)
        assert gateway.requests == []
        assert result.spoken == app_module.voice_gate_text("worker", vl.PROBING)
        assert app_module.APP_VOICE_ROLE_GATED in h.ledger_codes()
        record = _assistant_records(h.state)[-1]
        assert record[app_module.VOICE_GATE_FIELD] == vl.PROBING
        assert h.app.status()["models"]["voice_gate"] == vl.PROBING

    def test_a_spoken_turn_after_a_failed_probe_says_unavailable(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("worker")
        gateway.fail = urllib.error.URLError("down")
        h.spawned.run_all()
        gateway.fail = None
        gateway.requests.clear()
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn(SPEECH)
        assert gateway.requests == []
        assert result.spoken == app_module.voice_gate_text("worker", vl.UNMEASURED)
        assert result.spoken != app_module.voice_gate_text("worker", vl.PROBING)

    def test_a_typed_turn_is_not_gated(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("worker")
        assert h.app.controls().chat(TYPED)["reply"] == REPLY
        assert gateway.models() == ["worker"]
        assert gateway.requests[0]["timeout"] == 60.0

    def test_after_a_recorded_probe_a_spoken_turn_names_worker_under_the_derived_deadline(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("worker")
        h.spawned.run_all()
        deadline = h.app.voice_latency.spoken_deadline("worker")
        assert deadline is not None
        assert deadline != 60.0
        gateway.requests.clear()
        h.app.attach_ear("host", FakeEndpoint())
        assert h.app.run_turn(SPEECH).spoken == REPLY
        (request,) = gateway.chat()
        assert request["body"]["model"] == "worker"
        assert request["timeout"] == deadline

    def test_a_live_spoken_turn_adds_a_sample_so_the_p90_follows_the_rig(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        """Review of #117, finding 9: only the probe ever recorded, so the p90 froze at
        probe time while the docstring said live turns keep adding samples."""
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("worker")
        h.spawned.run_all()
        before = h.app.voice_latency.snapshot()["worker"]["n"]
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        assert h.app.voice_latency.snapshot()["worker"]["n"] == before + 1

    def test_a_spoken_completion_cut_off_by_its_deadline_is_a_censored_sample(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        """Lesson 1: a call cut at the deadline took AT LEAST that long; dropping it
        would bias the p90 low exactly when the rig is slow."""
        h = _wired(harness, tmp_path)
        h.app.set_conversation_role("worker")
        h.spawned.run_all()
        deadline = h.app.voice_latency.spoken_deadline("worker")
        gateway.fail = TimeoutError("timed out")
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        store = h.app.voice_latency
        samples = json.loads(store._path.read_text(encoding="utf-8"))["worker"]["samples"]
        assert samples[-1]["censored"] is True
        assert samples[-1]["completion_s"] == deadline

    def test_status_reports_the_role_the_snapshot_and_latency(
        self, harness: Any, tmp_path: Path, gateway: Gateway
    ) -> None:
        h = _wired(harness, tmp_path)
        _measure(h)
        h.app.set_conversation_role("worker")
        models = h.app.status()["models"]
        assert models["role"] == "worker"
        assert models["voice_gate"] is None
        assert [r["role"] for r in models["roles"]] == ["senses", "worker"]
        assert models["voice_latency"]["worker"]["state"] == vl.MEASURED
        assert models["stale"] is False
