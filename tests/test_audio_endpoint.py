"""Tests for embodiment.audio.endpoint — the AudioEndpoint protocol and NullEndpoint.

Covers plan task t7's interface half. The host implementation's own
acceptance criteria (no-voice/never-raise, mute-before-encode, the
import-graph guard) live in tests/test_audio_host.py, since they exercise
HostEndpoint's behaviour, not the protocol shape.
"""

from __future__ import annotations

import inspect

from embodiment.audio.endpoint import (
    CHANNELS,
    DEGRADED_NO_ENDPOINT,
    SAMPLE_RATE_HZ,
    SAMPLE_WIDTH_BYTES,
    AudioEndpoint,
    EndpointCloseReport,
    EndpointDegradation,
    NullEndpoint,
)
from embodiment.audio.features import SAMPLE_RATE_HZ as FEATURES_SAMPLE_RATE_HZ


def test_frame_format_matches_features_contract():
    """The endpoint's frame format is the SAME 24 kHz the feature extractor contracts on."""
    assert SAMPLE_RATE_HZ == FEATURES_SAMPLE_RATE_HZ == 24000
    assert SAMPLE_WIDTH_BYTES == 2
    assert CHANNELS == 1


def test_null_endpoint_satisfies_the_protocol_structurally():
    assert isinstance(NullEndpoint(), AudioEndpoint)


def test_null_endpoint_never_raises_across_full_lifecycle():
    """Attack: drive every public method, including out-of-order and repeated calls."""
    endpoint = NullEndpoint()
    received: list[bytes] = []

    # out of order: detach/stop before attach/start; close before anything
    endpoint.detach()
    endpoint.stop_capture()
    endpoint.close(1.0)

    endpoint.attach()
    endpoint.attach()  # idempotent
    endpoint.start_capture(received.append)
    endpoint.start_capture(received.append)  # re-registering, still no raise
    endpoint.play(b"\x00\x00" * 100)
    endpoint.play(b"")
    endpoint.play(object())  # type: ignore[arg-type]  # attack: wrong type
    assert endpoint.stop_playback() == 0
    assert endpoint.playing is False
    endpoint.mute(True)
    endpoint.mute(True)
    endpoint.mute(False)
    endpoint.stop_capture()
    endpoint.detach()
    report = endpoint.close(0.0)  # zero deadline
    assert isinstance(report, EndpointCloseReport)
    endpoint.close(-5.0)  # negative deadline: attack

    assert received == []  # NullEndpoint never delivers a frame — no source exists


def test_null_endpoint_mute_property_reflects_last_call():
    endpoint = NullEndpoint()
    assert endpoint.muted is False
    endpoint.mute(True)
    assert endpoint.muted is True
    endpoint.mute(False)
    assert endpoint.muted is False


def test_null_endpoint_status_names_the_no_endpoint_degradation():
    endpoint = NullEndpoint()
    status = endpoint.status()
    assert status["degradation"]["code"] == DEGRADED_NO_ENDPOINT
    # no speech, no secrets, no unbounded attacker text in the reason field
    assert len(status["degradation"]["reason"]) < 200


def test_null_endpoint_status_is_plain_json_serialisable_data():
    import json

    endpoint = NullEndpoint()
    endpoint.attach()
    endpoint.start_capture(lambda _frame: None)
    endpoint.mute(True)
    json.dumps(endpoint.status())  # raises TypeError if not plain data


def test_endpoint_degradation_is_frozen():
    degradation = EndpointDegradation("some-code", "some reason")
    try:
        degradation.code = "other"  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised, "EndpointDegradation must be immutable"


def test_endpoint_degradation_to_dict_carries_only_code_and_reason():
    degradation = EndpointDegradation("audio-x-y", "reason text")
    assert degradation.to_dict() == {"code": "audio-x-y", "reason": "reason text"}


def test_protocol_module_imports_nothing_host_audio_specific():
    """endpoint.py must stay free of anything host-audio-specific (module docstring rule).

    Checked at the import-statement level, not by scanning prose: the module
    docstring itself legitimately *names* sounddevice/PortAudio when
    describing what a concrete implementation looks like.
    """
    import ast

    import embodiment.audio.endpoint as endpoint_module

    source = inspect.getsource(endpoint_module)
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)

    for forbidden in ("sounddevice", "portaudio"):
        assert not any(forbidden in name.lower() for name in imported_names)


def test_protocol_members_match_the_documented_contract():
    expected = {
        "attach",
        "detach",
        "start_capture",
        "stop_capture",
        "play",
        "stop_playback",
        "playing",
        "mute",
        "muted",
        "close",
        "status",
    }
    assert expected <= set(dir(AudioEndpoint))


def test_endpoint_close_report_is_frozen_and_carries_the_four_fields():
    report = EndpointCloseReport(
        capture_thread_stopped=True,
        writer_thread_stopped=False,
        samples_discarded=42,
        elapsed_s=0.05,
    )
    assert report.to_dict() == {
        "capture_thread_stopped": True,
        "writer_thread_stopped": False,
        "samples_discarded": 42,
        "elapsed_s": 0.05,
    }
    try:
        report.samples_discarded = 0  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised, "EndpointCloseReport must be immutable"


def test_null_endpoint_close_returns_a_close_report():
    endpoint = NullEndpoint()
    report = endpoint.close(1.0)
    assert isinstance(report, EndpointCloseReport)
    assert report.capture_thread_stopped is True
    assert report.writer_thread_stopped is True
    assert report.samples_discarded == 0
