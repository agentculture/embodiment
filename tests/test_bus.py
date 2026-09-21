"""Tests for embodiment.bus (plan task t13, spec target h16).

Structured by acceptance criterion, plus an attack section and a measured-cost
section the task brief asked to be reported (not a pass/fail assertion, though
a loose sanity bound is asserted so a real regression still fails the suite).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from embodiment.bus import (
    DEFAULT_MAX_EVENT_BYTES,
    DEGRADED_BROKER_UNAVAILABLE,
    DEGRADED_OVERSIZE,
    DEGRADED_SCHEMA_INVALID,
    DEGRADED_SECRET_REDACTED,
    EVENT_KINDS,
    HEARTBEAT_INTERVAL_S,
    SCHEMA_VERSION,
    Bus,
    Subscription,
    fold_degradation,
)
from embodiment.continuity import Degradation as ContinuityDegradation
from embodiment.daemon.state import DegradationRecord
from embodiment.events import EventDegradation
from embodiment.turn import TurnDegradation

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "events"


class _RaisingClient:
    """A client double whose every method raises — simulates a dead broker."""

    def publish_event(self, envelope, topic, **kwargs):
        raise ConnectionError("broker unreachable")

    def close(self):
        raise RuntimeError("close also fails")


class _OkClient:
    """A client double that always reports success, and counts publishes."""

    def __init__(self):
        self.published = []
        self.closed = False

    def publish_event(self, envelope, topic, **kwargs):
        self.published.append((envelope, topic))
        return type("R", (), {"ok": True, "reason": ""})()

    def close(self):
        self.closed = True


class _FailingResultClient:
    """A client double whose publish_event returns ok=False without raising."""

    def publish_event(self, envelope, topic, **kwargs):
        return type("R", (), {"ok": False, "reason": "no_conn"})()

    def close(self):
        pass


def _load_fixture(kind: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{kind}.json").read_text())


def _load_schema() -> dict:
    return json.loads((FIXTURES_DIR / "schema.json").read_text())


# ── criterion 1: every event validates against the committed schema fixtures,
#    and carries a schema version ──────────────────────────────────────────


class TestSchemaValidation:
    def test_all_nine_kinds_have_fixtures(self):
        schema = _load_schema()
        assert set(schema["kinds"]) == EVENT_KINDS

    @pytest.mark.parametrize("kind", sorted(EVENT_KINDS))
    def test_fixture_matches_required_fields(self, kind):
        schema = _load_schema()
        fixture = _load_fixture(kind)
        assert fixture["kind"] == kind
        assert fixture["v"] == SCHEMA_VERSION
        for field_name in ("v", "kind", "ts", "seq", "source", "data"):
            assert field_name in fixture
        for required in schema["kinds"][kind]["required"]:
            assert required in fixture["data"], f"{kind} fixture missing required '{required}'"

    @pytest.mark.parametrize("kind", sorted(EVENT_KINDS))
    def test_bus_publish_of_fixture_data_succeeds(self, kind):
        """Publishing the exact fixture's data payload through Bus.publish succeeds."""
        bus = Bus(client=_OkClient())
        fixture = _load_fixture(kind)
        event = bus.publish(kind, fixture["data"])
        assert event is not None, bus.degradations
        assert event.v == SCHEMA_VERSION
        assert event.kind == kind

    def test_publish_carries_schema_version(self):
        bus = Bus(client=_OkClient())
        event = bus.publish("heartbeat", {})
        assert event.v == 1
        assert event.to_dict()["v"] == 1

    @pytest.mark.parametrize(
        "kind,bad_data",
        [
            ("state", {"component": "daemon"}),  # missing status
            ("mic", {"hot": "yes", "ear": "local"}),  # hot not bool
            ("turn", {"phase": "started"}),  # missing step_count
            ("transcript", {"role": "narrator", "text": "x"}),  # bad role
            ("transcript", {"role": "user"}),  # missing text
            ("reply", {}),  # missing text
            ("degradation", {"source": "x", "code": "", "reason": "y"}),  # empty code
            (
                "features",
                {
                    "direction": "sideways",
                    "env": "",
                    "level_db": 0,
                    "noise_floor_db": 0,
                    "zero_crossing_hz": None,
                },
            ),
            ("clients", {"count": "two", "remote": 0}),  # count not int
        ],
    )
    def test_invalid_payload_refused_and_recorded(self, kind, bad_data):
        bus = Bus(client=_OkClient())
        event = bus.publish(kind, bad_data)
        assert event is None
        assert len(bus.degradations) == 1
        assert bus.degradations[0].code == DEGRADED_SCHEMA_INVALID
        assert kind in bus.degradations[0].reason

    def test_unknown_kind_refused_and_recorded(self):
        bus = Bus(client=_OkClient())
        event = bus.publish("not-a-real-kind", {})
        assert event is None
        assert bus.degradations[0].code == DEGRADED_SCHEMA_INVALID

    def test_refused_event_never_reaches_broker_or_subscribers(self):
        client = _OkClient()
        bus = Bus(client=client)
        sub = bus.subscribe()
        bus.publish("mic", {"hot": "not-a-bool", "ear": "local"})
        assert client.published == []
        assert sub.qsize() == 0


# ── criterion 2: heartbeat at a fixed interval; missing broker degrades
#    visibly to in-process ──────────────────────────────────────────────────


class TestHeartbeat:
    def test_interval_is_a_named_constant(self):
        assert HEARTBEAT_INTERVAL_S > 0

    def test_tick_emits_at_fixed_interval_only(self):
        bus = Bus(client=_OkClient())
        sub = bus.subscribe(kinds=["heartbeat"])

        t0 = 1000.0
        assert bus.tick(t0) is not None  # first tick always establishes baseline
        assert bus.tick(t0 + 1.0) is None  # well under the interval
        assert bus.tick(t0 + HEARTBEAT_INTERVAL_S - 0.01) is None  # just under
        ev = bus.tick(t0 + HEARTBEAT_INTERVAL_S)  # exactly at the interval
        assert ev is not None
        assert ev.kind == "heartbeat"

        events = sub.drain()
        assert len(events) == 2
        assert all(e.kind == "heartbeat" for e in events)

    def test_tick_never_reads_a_wall_clock(self):
        """Two identical (now) sequences produce identical emit/no-emit decisions."""
        bus_a = Bus(client=_OkClient())
        bus_b = Bus(client=_OkClient())
        sequence = [
            0.0,
            5.0,
            HEARTBEAT_INTERVAL_S,
            HEARTBEAT_INTERVAL_S + 2,
            HEARTBEAT_INTERVAL_S * 3,
        ]
        results_a = [bus_a.tick(t) is not None for t in sequence]
        results_b = [bus_b.tick(t) is not None for t in sequence]
        assert results_a == results_b


class TestBrokerDegradation:
    def test_missing_broker_import_degrades_once_in_process_continues(self, monkeypatch):
        import embodiment.bus as bus_mod

        def _boom():
            raise ImportError("no events_cli installed")

        monkeypatch.setattr(bus_mod, "_load_envelope_core", _boom)
        bus = Bus()
        sub = bus.subscribe()

        for i in range(5):
            bus.publish("heartbeat", {})

        assert len(bus.degradations) == 1
        assert bus.degradations[0].code == DEGRADED_BROKER_UNAVAILABLE
        # in-process delivery is unaffected by the broker being unreachable
        assert sub.qsize() == 5

    def test_raising_client_degrades_once_not_per_event(self):
        bus = Bus(client=_RaisingClient())
        sub = bus.subscribe()
        for _ in range(10):
            bus.publish("heartbeat", {})
        assert len(bus.degradations) == 1
        assert bus.degradations[0].code == DEGRADED_BROKER_UNAVAILABLE
        assert sub.qsize() == 10

    def test_publish_result_ok_false_degrades(self):
        bus = Bus(client=_FailingResultClient())
        bus.publish("heartbeat", {})
        assert bus.degradations[0].code == DEGRADED_BROKER_UNAVAILABLE

    def test_healthy_broker_receives_every_event(self):
        client = _OkClient()
        bus = Bus(client=client)
        for _ in range(4):
            bus.publish("heartbeat", {})
        assert len(client.published) == 4
        assert bus.degradations == []

    def test_on_degrade_callback_invoked(self):
        seen = []
        bus = Bus(client=_RaisingClient(), on_degrade=seen.append)
        bus.publish("heartbeat", {})
        assert len(seen) == 1
        assert seen[0].code == DEGRADED_BROKER_UNAVAILABLE


# ── criterion 3: no event payload ever contains the gateway key or the
#    install secret ─────────────────────────────────────────────────────────


class TestSecretRedaction:
    SECRET_A = "sk-gatewaykey-marker-zzz111"
    SECRET_B = "install-secret-marker-yyy222"

    def _bus(self):
        return Bus(client=_OkClient(), redact=[self.SECRET_A, self.SECRET_B])

    def test_top_level_field_redacted(self):
        bus = self._bus()
        event = bus.publish("state", {"component": self.SECRET_A, "status": "up"})
        assert event is None
        assert bus.degradations[-1].code == DEGRADED_SECRET_REDACTED

    def test_nested_payload_redacted(self):
        bus = self._bus()
        event = bus.publish(
            "degradation",
            {"source": "x", "code": "y", "reason": f"failed: {{'inner': '{self.SECRET_A}'}}"},
        )
        assert event is None
        assert bus.degradations[-1].code == DEGRADED_SECRET_REDACTED

    def test_degradation_reason_redacted(self):
        bus = self._bus()
        event = bus.publish(
            "degradation", {"source": "x", "code": "y", "reason": f"leaked {self.SECRET_B}"}
        )
        assert event is None

    def test_features_frame_redacted(self):
        bus = self._bus()
        event = bus.publish(
            "features",
            {
                "direction": "in",
                "env": "AAAA",
                "level_db": -10.0,
                "noise_floor_db": -50.0,
                "zero_crossing_hz": None,
                "debug_note": self.SECRET_B,
            },
        )
        assert event is None
        assert bus.degradations[-1].code == DEGRADED_SECRET_REDACTED

    def test_redacted_event_never_reaches_broker_or_subscribers(self):
        client = _OkClient()
        bus = Bus(client=client, redact=[self.SECRET_A])
        sub = bus.subscribe()
        bus.publish("state", {"component": self.SECRET_A, "status": "up"})
        assert client.published == []
        assert sub.qsize() == 0

    def test_clean_payload_with_no_secret_configured_passes(self):
        bus = Bus(client=_OkClient())
        event = bus.publish("state", {"component": "daemon", "status": "up"})
        assert event is not None

    def test_bidi_and_format_chars_stripped_from_degradation_reason(self):
        bus = Bus(client=_OkClient())
        # U+202E RIGHT-TO-LEFT OVERRIDE, U+200B ZERO WIDTH SPACE
        poisoned = "safe‮text​here"
        event = bus.publish("degradation", {"source": "x", "code": "y", "reason": poisoned})
        assert event is not None
        assert "‮" not in event.data["reason"]
        assert "​" not in event.data["reason"]
        assert "safe" in event.data["reason"] and "text" in event.data["reason"]


# ── size limit ───────────────────────────────────────────────────────────


class TestSizeLimit:
    def test_oversize_event_refused_and_recorded(self):
        bus = Bus(client=_OkClient(), max_event_bytes=200)
        event = bus.publish("reply", {"text": "x" * 1000})
        assert event is None
        assert bus.degradations[-1].code == DEGRADED_OVERSIZE

    def test_within_limit_accepted(self):
        bus = Bus(client=_OkClient(), max_event_bytes=DEFAULT_MAX_EVENT_BYTES)
        event = bus.publish("reply", {"text": "a short reply"})
        assert event is not None

    def test_default_limit_documented_constant(self):
        assert DEFAULT_MAX_EVENT_BYTES > 0


# ── slow subscribers: bounded, drop-oldest, features-first, gap reporting ──


class TestSlowSubscribers:
    def test_bounded_queue_never_grows_past_maxsize(self):
        bus = Bus(client=_OkClient())
        sub = bus.subscribe(kinds=["heartbeat"], maxsize=5)
        for _ in range(50):
            bus.publish("heartbeat", {})
        assert sub.qsize() <= 5

    def test_features_dropped_first_transcript_and_degradation_survive(self):
        bus = Bus(client=_OkClient())
        sub = bus.subscribe(maxsize=4)

        bus.publish("transcript", {"role": "user", "text": "hello"})
        bus.publish("degradation", {"source": "x", "code": "y", "reason": "z"})
        bus.publish(
            "features",
            {
                "direction": "in",
                "env": "AA",
                "level_db": 0,
                "noise_floor_db": 0,
                "zero_crossing_hz": None,
            },
        )
        bus.publish(
            "features",
            {
                "direction": "in",
                "env": "BB",
                "level_db": 0,
                "noise_floor_db": 0,
                "zero_crossing_hz": None,
            },
        )
        # queue is now full (4/4): transcript, degradation, features, features.
        # Publishing 20 more features events must never evict transcript/degradation.
        for _ in range(20):
            bus.publish(
                "features",
                {
                    "direction": "in",
                    "env": "CC",
                    "level_db": 0,
                    "noise_floor_db": 0,
                    "zero_crossing_hz": None,
                },
            )

        drops_before_drain = sub.drops
        remaining_kinds = [e.kind for e in sub.drain()]
        assert "transcript" in remaining_kinds
        assert "degradation" in remaining_kinds
        assert drops_before_drain > 0

    def test_features_dropped_outright_when_queue_full_of_non_features(self):
        """When the queue is full of non-features events, an incoming features
        event is dropped outright rather than evicting a transcript/degradation
        event to make room for itself."""
        bus = Bus(client=_OkClient())
        sub = bus.subscribe(maxsize=2)
        bus.publish("transcript", {"role": "user", "text": "a"})
        bus.publish("degradation", {"source": "x", "code": "y", "reason": "z"})
        bus.publish(
            "features",
            {
                "direction": "in",
                "env": "AA",
                "level_db": 0,
                "noise_floor_db": 0,
                "zero_crossing_hz": None,
            },
        )
        kinds = [e.kind for e in sub.drain()]
        assert kinds == ["transcript", "degradation"]

    def test_gap_field_reports_drop_count_on_next_delivery(self):
        bus = Bus(client=_OkClient())
        sub = bus.subscribe(kinds=["heartbeat"], maxsize=1)
        # only the first heartbeat.data differs by seq, so publish several
        for _ in range(6):
            bus.publish("heartbeat", {})
        events = sub.drain()
        assert len(events) == 1
        # 6 published, 1 delivered, 5 dropped -> gap == 5 on the delivered one
        assert events[0].gap == 5

    def test_gap_resets_to_zero_after_being_reported(self):
        bus = Bus(client=_OkClient())
        sub = bus.subscribe(kinds=["heartbeat"], maxsize=1)
        for _ in range(3):
            bus.publish("heartbeat", {})
        first = sub.get(timeout=0.0)
        assert first.gap == 2
        bus.publish("heartbeat", {})
        second = sub.get(timeout=0.0)
        assert second.gap == 0

    def test_slow_subscriber_does_not_slow_a_healthy_one(self):
        bus = Bus(client=_OkClient())
        slow = bus.subscribe(kinds=["features"], maxsize=2)  # never drained
        fast = bus.subscribe(kinds=["features"], maxsize=1000)

        for i in range(200):
            bus.publish(
                "features",
                {
                    "direction": "in",
                    "env": "Z",
                    "level_db": 0,
                    "noise_floor_db": 0,
                    "zero_crossing_hz": None,
                },
            )

        assert slow.qsize() <= 2
        assert fast.qsize() == 200  # a healthy, draining-capable subscriber loses nothing


# ── privacy boundary: Subscription(kinds=...) filtering ────────────────────


class TestPrivacyFiltering:
    def test_subscription_without_transcript_never_receives_one(self):
        bus = Bus(client=_OkClient())
        sub = bus.subscribe(kinds=["state", "mic", "turn"])
        bus.publish("transcript", {"role": "user", "text": "secret words"})
        bus.publish("reply", {"text": "a reply"})
        bus.publish("state", {"component": "daemon", "status": "up"})
        events = sub.drain()
        kinds = {e.kind for e in events}
        assert "transcript" not in kinds
        assert "reply" not in kinds
        assert "state" in kinds

    def test_no_kinds_filter_receives_everything(self):
        bus = Bus(client=_OkClient())
        sub = bus.subscribe()
        bus.publish("transcript", {"role": "user", "text": "hi"})
        events = sub.drain()
        assert events[0].kind == "transcript"

    def test_unsubscribe_stops_delivery(self):
        bus = Bus(client=_OkClient())
        sub = bus.subscribe()
        bus.unsubscribe(sub)
        bus.publish("heartbeat", {})
        assert sub.qsize() == 0

    def test_close_wakes_blocked_get(self):
        sub = Subscription(maxsize=8)
        result = {}

        def waiter():
            result["event"] = sub.get(timeout=5.0)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        sub.close()
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert result["event"] is None


# ── fold_degradation: the one fold function, against all four real shapes ──


class TestFoldDegradation:
    def test_folds_events_event_degradation(self):
        rec = EventDegradation(code="connect-failed", reason="ConnectionRefusedError")
        folded = fold_degradation(rec, source="events")
        assert folded == {
            "source": "events",
            "code": "connect-failed",
            "reason": "ConnectionRefusedError",
        }

    def test_folds_turn_degradation(self):
        rec = TurnDegradation(code="turn-budget-exhausted", reason="max_steps reached")
        folded = fold_degradation(rec, source="turn")
        assert folded["code"] == "turn-budget-exhausted"
        assert folded["reason"] == "max_steps reached"
        assert folded["source"] == "turn"

    def test_folds_continuity_degradation(self):
        rec = ContinuityDegradation(
            subsystem="eidetic", stage="remember", code="eidetic-unavailable", reason="down"
        )
        folded = fold_degradation(rec, source="continuity")
        assert folded == {"source": "continuity", "code": "eidetic-unavailable", "reason": "down"}

    def test_folds_daemon_state_degradation_record(self):
        rec = DegradationRecord(
            ts=0.0, code="state-dir-fallback", detail="used /tmp fallback", id="x"
        )
        folded = fold_degradation(rec, source="daemon")
        assert folded == {
            "source": "daemon",
            "code": "state-dir-fallback",
            "reason": "used /tmp fallback",
        }

    def test_fold_result_publishes_as_a_valid_degradation_event(self):
        bus = Bus(client=_OkClient())
        rec = TurnDegradation(code="turn-budget-exhausted", reason="max_steps reached")
        event = bus.publish("degradation", fold_degradation(rec, source="turn"))
        assert event is not None

    def test_fold_sanitises_bidi_in_reason(self):
        rec = TurnDegradation(code="x", reason="a‮b")
        folded = fold_degradation(rec, source="turn")
        assert "‮" not in folded["reason"]

    def test_fold_never_raises_on_missing_attributes(self):
        class _Empty:
            pass

        folded = fold_degradation(_Empty(), source="mystery")
        assert folded["code"] == "unknown"
        assert folded["reason"] == ""
        assert folded["source"] == "mystery"

    def test_fold_source_restricted_to_safe_charset(self):
        folded = fold_degradation(
            TurnDegradation(code="c", reason="r"), source="bad/../source\x00!"
        )
        assert all(ch.isalnum() or ch in "._-" for ch in folded["source"])


# ── attacks: fed the way an attacker or a hostile daemon feeds it ─────────


class TestAttacks:
    def test_nul_bytes_in_text_do_not_crash(self):
        bus = Bus(client=_OkClient())
        event = bus.publish("reply", {"text": "hello\x00world"})
        assert event is not None  # accepted, JSON handles NUL fine

    def test_10k_character_string(self):
        bus = Bus(client=_OkClient(), max_event_bytes=1_000_000)
        event = bus.publish("reply", {"text": "x" * 10_000})
        assert event is not None
        assert bus.degradations == []

    def test_10k_character_string_over_default_limit_is_refused_not_raised(self):
        bus = Bus(client=_OkClient())
        event = bus.publish("reply", {"text": "x" * 10_000})
        assert event is None
        assert bus.degradations[-1].code == DEGRADED_OVERSIZE

    def test_unicode_bidi_line_separators_in_text(self):
        bus = Bus(client=_OkClient())
        poisoned = "line1 line2\u0085line3‮"
        event = bus.publish("reply", {"text": poisoned})
        assert event is not None  # reply text is speech; not sanitised, only degradation is

    def test_path_separators_and_dotdot_in_token_fields(self):
        bus = Bus(client=_OkClient())
        event = bus.publish("state", {"component": "../../etc/passwd", "status": "up"})
        assert event is not None  # not a filesystem path in this module; accepted as data

    def test_non_dict_data_does_not_crash(self):
        bus = Bus(client=_OkClient())
        event = bus.publish("state", "not a dict")  # type: ignore[arg-type]
        assert event is None
        assert bus.degradations[-1].code == DEGRADED_SCHEMA_INVALID

    def test_none_data_does_not_crash(self):
        bus = Bus(client=_OkClient())
        event = bus.publish("heartbeat", None)
        assert event is not None  # heartbeat has no required fields

    def test_non_json_serialisable_data_degrades_not_raises(self):
        bus = Bus(client=_OkClient())
        event = bus.publish("reply", {"text": "ok", "blob": object()})
        assert event is None
        assert bus.degradations[-1].code == DEGRADED_SCHEMA_INVALID

    def test_ten_thousand_repeated_publishes(self):
        bus = Bus(client=_OkClient())
        sub = bus.subscribe(maxsize=50)
        for _ in range(10_000):
            bus.publish("heartbeat", {})
        assert sub.qsize() <= 50
        assert sub.drops > 0

    def test_hanging_dependency_client_does_not_block_publish(self):
        """A client_factory that raises repeatedly must not slow subsequent publishes."""
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            raise TimeoutError("simulated hang-then-fail")

        bus = Bus(client_factory=factory)
        start = time.monotonic()
        for _ in range(20):
            bus.publish("heartbeat", {})
        elapsed = time.monotonic() - start
        assert elapsed < 1.0
        # constructor is only ever attempted once (degrade-once), not per publish
        assert calls["n"] == 1

    def test_close_is_idempotent_and_never_raises(self):
        bus = Bus(client=_RaisingClient())
        bus.publish("heartbeat", {})  # forces broker client construction attempt
        report1 = bus.close()
        report2 = bus.close()
        assert report1.subscribers_closed >= 0
        assert report2.subscribers_closed == 0  # nothing left the second time

    def test_close_reports_elapsed_within_deadline(self):
        bus = Bus(client=_OkClient())
        bus.subscribe()
        report = bus.close(deadline=2.0)
        assert report.elapsed_s < 2.0

    def test_publish_from_many_threads_does_not_corrupt_sequence(self):
        bus = Bus(client=_OkClient())
        sub = bus.subscribe(maxsize=10_000)
        errors = []

        def worker():
            try:
                for _ in range(200):
                    bus.publish("heartbeat", {})
            except Exception as exc:  # pragma: no cover - would be the defect
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert errors == []
        events = sub.drain()
        seqs = [e.seq for e in events]
        assert len(seqs) == len(set(seqs)), "duplicate sequence numbers under concurrency"

    def test_unicode_format_chars_survive_general_attack_scan(self):
        """Plant a marker with a bidi char in a degradation reason and confirm
        the marker text survives while the bidi char itself is stripped."""
        bus = Bus(client=_OkClient())
        sub = bus.subscribe()
        marker = "MARKER_9f3a"
        bus.publish("degradation", {"source": "x", "code": "y", "reason": f"{marker}​"})
        delivered = sub.drain()[0]
        assert marker in delivered.data["reason"]
        assert "​" not in delivered.data["reason"]


# ── measured cost: 60 features/sec to 3 in-process subscribers, 10 sim
#    seconds. Reported in the task's final report, not asserted as a strict
#    pass/fail beyond a loose sanity bound. ──────────────────────────────────


def test_measured_cost_60hz_features_3_subscribers_10s(capsys):
    bus = Bus(client=_OkClient())
    subs = [bus.subscribe(kinds=["features"], maxsize=2000) for _ in range(3)]

    payload = {
        "direction": "in",
        "env": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "level_db": -20.0,
        "noise_floor_db": -55.0,
        "zero_crossing_hz": 180.0,
    }

    total_events = 60 * 10  # 60/s for 10 simulated seconds
    peak_depth = 0
    start = time.monotonic()
    for _ in range(total_events):
        bus.publish("features", payload)
        for sub in subs:
            depth = sub.qsize()
            if depth > peak_depth:
                peak_depth = depth
    elapsed = time.monotonic() - start

    for sub in subs:
        assert sub.qsize() == total_events  # never dropped (never above bounded maxsize)

    print(
        f"\nMEASURED: {total_events} features events to {len(subs)} subscribers "
        f"in {elapsed:.4f}s ({total_events / elapsed:.0f} events/s), "
        f"peak per-subscriber queue depth observed: {peak_depth}"
    )
    # Loose sanity bound only: this is a simulated-time test (no real sleeping),
    # so 600 in-process publishes must not take anywhere near a full second.
    assert elapsed < 2.0
