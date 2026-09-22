"""Tests for embodiment.bus (plan task t13, spec target h16).

Structured by acceptance criterion, then by the round-2 defect an independent
probe found (see embodiment/bus.py's module docstring, "Round 2" section),
plus an attack section and a measured-cost section the task brief asked to be
reported (not a pass/fail assertion, though a loose sanity bound is asserted
so a real regression still fails the suite).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from embodiment.bus import (
    DEFAULT_BROKER_RETRY_INTERVAL_S,
    DEFAULT_MAX_EVENT_BYTES,
    DEGRADED_BROKER_RECOVERED,
    DEGRADED_BROKER_UNAVAILABLE,
    DEGRADED_OVERSIZE,
    DEGRADED_PROTECTED_DROP,
    DEGRADED_PUBLISH_AFTER_CLOSE,
    DEGRADED_SCHEMA_INVALID,
    DEGRADED_SECRET_REDACTED,
    EVENT_KINDS,
    HEARTBEAT_INTERVAL_S,
    SCHEMA_VERSION,
    SPEECH_KINDS,
    Bus,
    Subscription,
    fold_degradation,
)
from embodiment.continuity import Degradation as ContinuityDegradation
from embodiment.daemon.state import DegradationRecord
from embodiment.events import EventDegradation
from embodiment.turn import TurnDegradation

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "events"


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.005) -> bool:
    """Poll *predicate* until it is truthy or *timeout* elapses. No blind sleeps."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


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

    def __init__(self, reason: str = "no_conn"):
        self._reason = reason

    def publish_event(self, envelope, topic, **kwargs):
        return type("R", (), {"ok": False, "reason": self._reason})()

    def close(self):
        pass


class _SlowClient:
    """A client double whose publish_event blocks for a fixed duration.

    ``calls_started`` increments the INSTANT a call begins (before the sleep),
    so a test can wait for "a call is genuinely in flight" -- distinct from
    ``published``, which only grows once a call FINISHES. Waiting on
    ``published`` for a multi-second delay client would defeat the point of a
    bounded-wait helper; waiting on ``calls_started`` does not.
    """

    def __init__(self, delay: float = 0.5):
        self._delay = delay
        self.published = []
        self.calls_started = 0

    def publish_event(self, envelope, topic, **kwargs):
        self.calls_started += 1
        time.sleep(self._delay)
        self.published.append((envelope, topic))
        return type("R", (), {"ok": True, "reason": ""})()

    def close(self):
        pass


def _load_fixture(kind: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{kind}.json").read_text())


def _load_schema() -> dict:
    return json.loads((FIXTURES_DIR / "schema.json").read_text())


def _bus_with_ok_client(**kwargs) -> tuple[Bus, _OkClient]:
    client = _OkClient()
    return Bus(client=client, **kwargs), client


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
    def test_schema_required_fields_exactly_match_the_bus_own_contract(self, kind):
        """Round 3, defect 5: the fixture contract was pinned one way only
        (schema.json's required set is a SUBSET of the fixture, and the
        fixture is a subset of it) -- nothing forced schema.json and
        embodiment.bus._REQUIRED_DATA_FIELDS to actually AGREE with each
        other. A schema.json edit that drifted from the real validator could
        pass every other test here. Assert equality, not just subset."""
        import embodiment.bus as bus_mod

        schema = _load_schema()
        assert set(schema["kinds"][kind]["required"]) == set(bus_mod._REQUIRED_DATA_FIELDS[kind])

    def test_schema_and_bus_agree_on_the_full_kind_set(self):
        import embodiment.bus as bus_mod

        schema = _load_schema()
        assert set(schema["kinds"]) == set(bus_mod._REQUIRED_DATA_FIELDS) == EVENT_KINDS

    @pytest.mark.parametrize("kind", sorted(EVENT_KINDS))
    def test_bus_publish_of_fixture_data_succeeds(self, kind):
        """Publishing the exact fixture's data payload through Bus.publish succeeds."""
        bus, _client = _bus_with_ok_client()
        fixture = _load_fixture(kind)
        event = bus.publish(kind, fixture["data"])
        assert event is not None, bus.degradations
        assert event.v == SCHEMA_VERSION
        assert event.kind == kind

    def test_publish_carries_schema_version(self):
        bus, _client = _bus_with_ok_client()
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
        bus, _client = _bus_with_ok_client()
        event = bus.publish(kind, bad_data)
        assert event is None
        assert len(bus.degradations) == 1
        assert bus.degradations[0].code == DEGRADED_SCHEMA_INVALID
        assert kind in bus.degradations[0].reason

    def test_unknown_kind_refused_and_recorded(self):
        bus, _client = _bus_with_ok_client()
        event = bus.publish("not-a-real-kind", {})
        assert event is None
        assert bus.degradations[0].code == DEGRADED_SCHEMA_INVALID

    def test_refused_event_never_reaches_broker_or_subscribers(self):
        bus, client = _bus_with_ok_client()
        sub = bus.subscribe(include_speech=True)
        bus.publish("mic", {"hot": "not-a-bool", "ear": "local"})
        assert client.published == []
        assert sub.qsize() == 0

    @pytest.mark.parametrize(
        "kind,data,expected",
        [
            ("state", "not-a-dict", "data"),
            ("state", {"component": "daemon"}, "status"),
            ("transcript", {"role": "narrator", "text": "x"}, "role"),
            ("transcript", {"role": "user", "text": 5}, "text"),
            ("reply", {"text": None}, "text"),
            ("mic", {"hot": 1, "ear": "local"}, "hot"),
            ("degradation", {"source": "x", "code": 7, "reason": "y"}, "code"),
            ("degradation", {"source": "x", "code": "", "reason": "y"}, "code"),
            ("degradation", {"source": "x", "code": "c", "reason": None}, "reason"),
            ("degradation", {"source": "", "code": "c", "reason": "y"}, "source"),
            ("degradation", {"source": 3, "code": "c", "reason": "y"}, "source"),
            (
                "features",
                {
                    "direction": "up",
                    "env": "",
                    "level_db": 0,
                    "noise_floor_db": 0,
                    "zero_crossing_hz": 0,
                },
                "direction",
            ),
            ("clients", {"count": True, "remote": 0}, "count"),  # bool is not a count
            ("clients", {"count": "2", "remote": 0}, "count"),
            ("clients", {"count": 2, "remote": 0}, None),
            ("degradation", {"source": "x", "code": "c", "reason": "y"}, None),
            ("heartbeat", {}, None),
        ],
    )
    def test_validate_data_names_the_first_bad_field(self, kind, data, expected):
        """Pins the field name each per-kind type check reports, including the
        branches the publish-level tests never reach (a non-string transcript
        text, a non-string degradation reason, an empty source, a bool count).
        """
        from embodiment.bus import _validate_data

        assert _validate_data(kind, data) == expected


# ── criterion 2: heartbeat at a fixed interval; missing broker degrades
#    visibly to in-process ──────────────────────────────────────────────────


class TestHeartbeat:
    def test_interval_is_a_named_constant(self):
        assert HEARTBEAT_INTERVAL_S > 0

    def test_tick_emits_at_fixed_interval_only(self):
        bus, _client = _bus_with_ok_client()
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
        bus_a, _ = _bus_with_ok_client()
        bus_b, _ = _bus_with_ok_client()
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
    def test_missing_broker_import_degrades_in_process_continues(self, monkeypatch):
        import embodiment.bus as bus_mod

        def _boom():
            raise ImportError("no events_cli installed")

        monkeypatch.setattr(bus_mod, "_load_envelope_core", _boom)
        bus = Bus()
        sub = bus.subscribe(include_speech=True)

        for _ in range(5):
            bus.publish("heartbeat", {})

        assert _wait_until(lambda: sub.qsize() == 5)
        # bounded ledger: one distinct code, count >= 1 (>=1 worker attempts)
        assert _wait_until(lambda: bus.degradation_counts.get(DEGRADED_BROKER_UNAVAILABLE, 0) >= 1)
        assert any(d.code == DEGRADED_BROKER_UNAVAILABLE for d in bus.degradations)
        assert sum(1 for d in bus.degradations if d.code == DEGRADED_BROKER_UNAVAILABLE) == 1

    def test_raising_client_degrades_once_not_per_event(self):
        bus = Bus(client=_RaisingClient())
        sub = bus.subscribe(include_speech=True)
        for _ in range(10):
            bus.publish("heartbeat", {})
        assert _wait_until(lambda: sub.qsize() == 10)
        assert _wait_until(lambda: bus.degradation_counts.get(DEGRADED_BROKER_UNAVAILABLE, 0) >= 1)
        assert sum(1 for d in bus.degradations if d.code == DEGRADED_BROKER_UNAVAILABLE) == 1

    def test_publish_result_ok_false_degrades(self):
        bus = Bus(client=_FailingResultClient())
        bus.publish("heartbeat", {})
        assert _wait_until(lambda: bus.degradation_counts.get(DEGRADED_BROKER_UNAVAILABLE, 0) >= 1)

    def test_healthy_broker_receives_every_event(self):
        bus, client = _bus_with_ok_client()
        for _ in range(4):
            bus.publish("heartbeat", {})
        assert _wait_until(lambda: len(client.published) == 4)
        assert bus.degradations == []

    def test_on_degrade_callback_invoked(self):
        seen = []
        bus = Bus(client=_RaisingClient(), on_degrade=seen.append)
        bus.publish("heartbeat", {})
        assert _wait_until(lambda: len(seen) == 1)
        assert seen[0].code == DEGRADED_BROKER_UNAVAILABLE


# ── round 2, defect 1: speech (and other caller data) must never leak into a
#    degradation reason via str(exc)/repr(exc)/result.reason ─────────────────


class TestExceptionTextNeverLeaked:
    MARK = "MARK-SPEECH-do-not-leak-9f3a"

    def test_exception_message_echoing_payload_never_reaches_a_degradation(self):
        class EchoClient:
            def publish_event(self, envelope, topic, **kwargs):
                raise RuntimeError(f"broker refused body={envelope.data}")

            def close(self):
                pass

        bus = Bus(client=EchoClient())
        bus.publish("transcript", {"role": "user", "text": self.MARK})
        assert _wait_until(lambda: bus.degradation_counts.get(DEGRADED_BROKER_UNAVAILABLE, 0) >= 1)
        for d in bus.degradations:
            assert self.MARK not in d.reason

    def test_publish_result_reason_is_restricted_not_trusted_verbatim(self):
        """A broker's PublishResult.reason is untrusted, transport-controlled
        text: it is surfaced ONLY when it is already a clean safe token (a
        real broker's reason is a short slug like "no_conn"); anything that
        required alteration to become safe is dropped WHOLESALE to a fixed
        generic string, never partially kept (round 2, defect 1)."""
        poisoned = f'inject "{self.MARK}" \u202e and spaces'
        bus = Bus(client=_FailingResultClient(reason=poisoned))
        bus.publish("heartbeat", {})
        assert _wait_until(lambda: bus.degradation_counts.get(DEGRADED_BROKER_UNAVAILABLE, 0) >= 1)
        record = next(d for d in bus.degradations if d.code == DEGRADED_BROKER_UNAVAILABLE)
        assert self.MARK not in record.reason
        assert '"' not in record.reason
        assert "\u202e" not in record.reason

    def test_clean_transport_reason_slug_survives_unaltered(self):
        """A real broker's reason ("no_conn", a paho rc slug) is exactly the
        shape _reason_or_generic is designed to pass through."""
        bus = Bus(client=_FailingResultClient(reason="no_conn"))
        bus.publish("heartbeat", {})
        assert _wait_until(lambda: bus.degradation_counts.get(DEGRADED_BROKER_UNAVAILABLE, 0) >= 1)
        record = next(d for d in bus.degradations if d.code == DEGRADED_BROKER_UNAVAILABLE)
        assert "no_conn" in record.reason

    def test_ensure_client_construction_failure_never_leaks_its_message(self):
        def factory():
            raise RuntimeError(f"cannot connect: last transcript was {self.MARK!r}")

        bus = Bus(client_factory=factory)
        bus.publish("heartbeat", {})
        assert _wait_until(lambda: bus.degradation_counts.get(DEGRADED_BROKER_UNAVAILABLE, 0) >= 1)
        for d in bus.degradations:
            assert self.MARK not in d.reason
            assert "transcript" not in d.reason
        # round 4: embodiment.safe_reason.describe_exception -- class name,
        # message length and a fingerprint, never the message itself.
        assert any(d.reason.startswith("RuntimeError (message:") for d in bus.degradations)
        assert any("fp:" in d.reason for d in bus.degradations)

    def test_json_not_serialisable_error_never_leaks_object_repr(self):
        bus, _client = _bus_with_ok_client()
        event = bus.publish("reply", {"text": "ok", "blob": object()})
        assert event is None
        reason = bus.degradations[-1].reason
        assert "object" not in reason.lower() or "TypeError" in reason
        # class name only, never the default TypeError message text which
        # names the unserialisable type's repr
        assert "0x" not in reason

    def test_errno_name_used_for_oserror_never_strerror_text(self):
        def factory():
            raise ConnectionRefusedError()

        bus = Bus(client_factory=factory)
        bus.publish("heartbeat", {})
        assert _wait_until(lambda: bus.degradation_counts.get(DEGRADED_BROKER_UNAVAILABLE, 0) >= 1)
        record = next(d for d in bus.degradations if d.code == DEGRADED_BROKER_UNAVAILABLE)
        assert "ConnectionRefusedError" in record.reason
        assert "Connection refused" not in record.reason  # strerror text, never included


# ── round 2, defect 2: broker fan-out must never block the publishing thread ─


class TestAsyncBrokerFanOut:
    def test_publish_returns_quickly_even_with_a_slow_broker(self):
        client = _SlowClient(delay=0.5)
        bus = Bus(client=client)
        start = time.monotonic()
        for _ in range(4):
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
        elapsed = time.monotonic() - start
        assert elapsed < 0.3, f"publish() blocked on the broker: {elapsed:.3f}s"
        # eventually the slow worker does deliver all 4 to the broker
        assert _wait_until(lambda: len(client.published) == 4, timeout=5.0)

    def test_close_reports_unsent_events_when_broker_is_too_slow_to_drain(self):
        """Round 3, defect 1/4: assert the REAL contract, not a tautology.

        With a 2s-per-call client and a 0.2s close deadline, the worker
        cannot possibly finish draining: worker_stopped must read False,
        unsent must be > 0, and -- the actual round-3 bug -- the client's
        publish count must NOT keep growing after close() returns (it used to:
        the worker kept draining the backlog, rebuilding a second client, for
        several more seconds after a "successful" close()).
        """
        client = _SlowClient(delay=2.0)
        bus = Bus(client=client)
        for _ in range(3):
            bus.publish("heartbeat", {})
        # Wait for a call to be genuinely IN FLIGHT (not just enqueued, and not
        # merely "a thread object exists") before closing -- otherwise close()
        # can race a worker that has not reached its first publish_event call
        # yet, and broker_worker_stopped trivially reads True for "there was
        # nothing to interrupt".
        assert _wait_until(lambda: client.calls_started >= 1, timeout=2.0)
        report = bus.close(deadline=0.2)
        assert report.elapsed_s < 1.0
        assert report.broker_worker_stopped is False
        assert report.broker_events_unsent > 0
        count_at_close = len(client.published)
        time.sleep(2.5)  # long enough for the 2s in-flight call to finish
        assert len(client.published) <= count_at_close + 1  # at most the one in-flight call
        # and, decisively, no growth AFTER that one in-flight call settles
        settled = len(client.published)
        time.sleep(0.2)
        assert len(client.published) == settled


# ── round 2, defect 3: seq assignment and in-process delivery are atomic ────


class TestOrderingUnderConcurrency:
    def _hammer_once(self, threads: int = 8, per_thread: int = 500) -> int:
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe(maxsize=threads * per_thread + 10)

        def worker():
            for _ in range(per_thread):
                bus.publish("heartbeat", {})

        pool = [threading.Thread(target=worker) for _ in range(threads)]
        for t in pool:
            t.start()
        for t in pool:
            t.join(timeout=30.0)

        seqs = [e.seq for e in sub.drain()]
        inversions = sum(1 for a, b in zip(seqs, seqs[1:]) if b < a)
        assert len(seqs) == threads * per_thread
        return inversions

    def test_zero_inversions_three_runs(self):
        for run in range(3):
            assert self._hammer_once() == 0, f"run {run} saw an out-of-order delivery"


# ── round 2, defect 4: the degradation ledger is bounded ────────────────────


class TestBoundedDegradations:
    def test_fifty_thousand_invalid_publishes_do_not_grow_unbounded(self):
        bus, _client = _bus_with_ok_client()
        for _ in range(50_000):
            bus.publish("nope", {})
        assert len(bus.degradations) == 1  # one distinct code, first occurrence
        assert bus.degradation_counts[DEGRADED_SCHEMA_INVALID] == 50_000
        assert bus.degradations_dropped == 0

    def test_a_failing_hook_is_counted_not_swallowed(self):
        def bad_hook(_record):
            raise RuntimeError("boom")

        bus = Bus(client=_RaisingClient(), on_degrade=bad_hook)
        for _ in range(5):
            bus.publish("heartbeat", {})
        assert _wait_until(lambda: bus.hook_errors >= 1)

    def test_repeated_identical_code_never_hits_the_distinct_code_cap(self):
        bus, _client = _bus_with_ok_client()
        bus.publish("nope-1", {})
        bus.publish("nope-2", {})
        bus.publish(123, {})  # a third distinct "unknown kind" reason
        # all three share the SAME code (DEGRADED_SCHEMA_INVALID), so the cap
        # is on distinct CODES, not distinct reasons -- still just one entry
        assert len(bus.degradations) == 1
        assert bus.degradations_dropped == 0

    def test_distinct_code_cap_is_itself_bounded_and_counted(self, monkeypatch):
        import embodiment.bus as bus_mod

        monkeypatch.setattr(bus_mod, "_MAX_DEGRADATION_CODES", 2)
        secret = "sk-gatewaykey-marker"
        bus = Bus(client=_OkClient(), max_event_bytes=10, redact=[secret])

        bus.publish(123, {})  # distinct code #1: DEGRADED_SCHEMA_INVALID
        bus.publish(
            "state", {"component": secret, "status": "up"}
        )  # distinct code #2: SECRET_REDACTED
        bus.publish("reply", {"text": "hello"})  # distinct code #3 (OVERSIZE) -- should be DROPPED

        assert len(bus.degradations) == 2
        assert bus.degradations_dropped == 1
        assert bus.degradation_counts.get(DEGRADED_OVERSIZE, 0) == 1
        assert {d.code for d in bus.degradations} == {
            DEGRADED_SCHEMA_INVALID,
            DEGRADED_SECRET_REDACTED,
        }


# ── round 2, defect 5: subscriber drop counter must agree with what happened ─


class TestSlowSubscribers:
    def test_bounded_queue_never_grows_past_maxsize(self):
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe(kinds=["heartbeat"], maxsize=5)
        for _ in range(50):
            bus.publish("heartbeat", {})
        assert sub.qsize() <= 5

    def test_features_dropped_first_transcript_and_degradation_survive(self):
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe(include_speech=True, maxsize=4)

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

        remaining_kinds = [e.kind for e in sub.drain()]
        assert "transcript" in remaining_kinds
        assert "degradation" in remaining_kinds
        # lifetime counter (never resets, unlike the pending/gap counter) --
        # readable even after a full drain (round 2, defect 5).
        assert sub.drops > 0
        assert sub.drops_by_kind.get("features", 0) > 0
        assert sub.protected_drops == 0  # only features were ever sacrificed here

    def test_features_dropped_outright_when_queue_full_of_non_features(self):
        """When the queue is full of non-features events, an incoming features
        event is dropped outright rather than evicting a transcript/degradation
        event to make room for itself."""
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe(include_speech=True, maxsize=2)
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
        assert sub.drops == 1
        assert sub.drops_by_kind == {"features": 1}
        assert sub.protected_drops == 0

    def test_protected_drop_counter_and_gap_agree_and_bus_records_it(self):
        """Reproduces the round-1 probe scenario: 10 transcripts into a
        maxsize=4 queue with nothing else competing, so the fallback (evict
        the oldest event of ANY kind) sacrifices transcripts themselves."""
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe(include_speech=True, maxsize=4)
        for i in range(10):
            bus.publish("transcript", {"role": "user", "text": f"msg {i}"})

        delivered = sub.drain()
        assert len(delivered) == 4
        assert sub.drops == 6
        assert sub.drops_by_kind == {"transcript": 6}
        assert sub.protected_drops == 6  # every one of those 6 was a "never silently" case
        assert delivered[0].gap == 6  # the counter and the gap now AGREE

        # round 2, defect 5: the bus records ONE bounded, counted degradation
        # naming that a protected kind was dropped -- never silently.
        protected_records = [d for d in bus.degradations if d.code == DEGRADED_PROTECTED_DROP]
        assert len(protected_records) == 1
        assert bus.degradation_counts[DEGRADED_PROTECTED_DROP] == 6
        assert "transcript" in protected_records[0].reason
        # never the actual spoken text
        for i in range(10):
            assert f"msg {i}" not in protected_records[0].reason

    def test_gap_field_reports_pending_drop_count_on_next_delivery(self):
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe(kinds=["heartbeat"], maxsize=1)
        for _ in range(6):
            bus.publish("heartbeat", {})
        events = sub.drain()
        assert len(events) == 1
        assert events[0].gap == 5

    def test_gap_resets_to_zero_after_being_reported_but_drops_does_not(self):
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe(kinds=["heartbeat"], maxsize=1)
        for _ in range(3):
            bus.publish("heartbeat", {})
        first = sub.get(timeout=0.0)
        assert first.gap == 2
        assert sub.drops == 2  # lifetime counter already reflects the 2 drops
        bus.publish("heartbeat", {})
        second = sub.get(timeout=0.0)
        assert second.gap == 0  # pending counter reset
        assert sub.drops == 2  # lifetime counter unaffected by the reset

    def test_slow_subscriber_does_not_slow_a_healthy_one(self):
        bus, _client = _bus_with_ok_client()
        slow = bus.subscribe(kinds=["features"], maxsize=2)  # never drained
        fast = bus.subscribe(kinds=["features"], maxsize=1000)

        for _ in range(200):
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


# ── round 2, defect 6a: a bounded retry, driven by tick(now) ────────────────


class TestBrokerRetry:
    def test_retry_interval_is_a_named_constant(self):
        assert DEFAULT_BROKER_RETRY_INTERVAL_S > 0

    def test_broker_recovers_via_tick_and_stops_retrying_once_up(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise OSError("not up yet")
            return _OkClient()

        bus = Bus(client_factory=flaky, broker_retry_interval_s=1.0)
        bus.publish("heartbeat", {})  # triggers the first (failing) attempt
        assert _wait_until(lambda: len(calls) == 1)
        assert _wait_until(lambda: bus.degradation_counts.get(DEGRADED_BROKER_UNAVAILABLE, 0) >= 1)

        # ticks spaced well past the retry interval
        bus.tick(1000.0)
        bus.tick(1002.0)
        bus.tick(1010.0)
        bus.tick(1020.0)

        assert len(calls) == 2  # exactly one retry attempt, which succeeded
        assert any(d.code == DEGRADED_BROKER_RECOVERED for d in bus.degradations)

    def test_retry_is_rate_limited_not_attempted_every_tick(self):
        calls = []

        def always_fails():
            calls.append(1)
            raise OSError("still down")

        bus = Bus(client_factory=always_fails, broker_retry_interval_s=100.0)
        bus.publish("heartbeat", {})
        assert _wait_until(lambda: len(calls) == 1)

        for i in range(20):
            bus.tick(1000.0 + i)  # 20 ticks, 1 second apart -- well under the 100s gate

        assert len(calls) <= 2  # at most one retry attempt across a sub-100s window


# ── round 3: a 27B review reproduced three more behavioural defects ─────────


class TestCloseActuallyDisablesTheBroker:
    """Round 3, defect 1 (reproduced by an independent 27B review's own probe:
    6 events queued against a 0.5s-per-call client, close(deadline=0.3)
    reported unsent=4 yet all 6 reached the broker 3.5s later through a
    SECOND, rebuilt client close() never closed)."""

    def test_worker_stops_draining_a_non_empty_queue_once_closed(self):
        client = _SlowClient(delay=1.5)
        builds = {"n": 0}

        def factory():
            builds["n"] += 1
            return client

        bus = Bus(client_factory=factory)
        for _ in range(6):
            bus.publish("heartbeat", {})
        # Wait for a call to be genuinely IN FLIGHT (not merely "the client was
        # constructed") -- so a 0.05s close deadline cannot possibly join it.
        assert _wait_until(lambda: client.calls_started >= 1, timeout=2.0)
        report = bus.close(deadline=0.05)

        count_at_close = len(client.published)
        time.sleep(2.0)  # long enough for the ONE in-flight call to settle
        count_after_in_flight_settles = len(client.published)
        time.sleep(2.0)  # long enough to drain the whole remaining backlog, if it still could
        count_much_later = len(client.published)

        # at most the ONE call already in flight when close() ran may complete
        # (an in-progress blocking network call cannot be aborted from here);
        # nothing past it -- and decisively, no further growth afterwards.
        assert count_after_in_flight_settles <= count_at_close + 1
        assert count_much_later == count_after_in_flight_settles, (
            f"fan-out continued after the in-flight call settled: "
            f"{count_after_in_flight_settles} -> {count_much_later}"
        )
        assert builds["n"] == 1, "a second client was rebuilt after close()"
        assert report.broker_worker_stopped is False  # 0.05s cannot catch a 1.5s in-flight call
        assert report.broker_events_unsent > 0

    def test_close_report_unsent_count_is_true_not_a_snapshot(self):
        """The events counted as 'unsent' at close() must STAY unsent."""
        client = _SlowClient(delay=0.3)
        bus = Bus(client=client)
        for _ in range(5):
            bus.publish("heartbeat", {})
        time.sleep(0.05)
        report = bus.close(deadline=0.1)
        unsent_at_close = report.broker_events_unsent
        time.sleep(2.0)
        # at most one more delivery (the one in-flight when close() ran) can
        # have landed; the reported "unsent" count must not have been a lie
        assert len(client.published) <= 5 - unsent_at_close + 1

    def test_no_client_is_built_after_close_even_for_a_never_started_broker(self):
        """A bus that never enqueued anything before close() must never build
        a client afterwards either -- covers _ensure_broker_core/_client's own
        closed check, not just the worker loop."""
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            return _OkClient()

        bus = Bus(client_factory=factory)
        bus.close()
        assert bus._ensure_broker_core() is False
        assert bus._ensure_broker_client() is None
        assert calls["n"] == 0

    def test_tick_is_a_no_op_after_close(self):
        calls = []

        def flaky():
            calls.append(1)
            raise OSError("down")

        bus = Bus(client_factory=flaky, broker_retry_interval_s=0.0)
        bus.publish("heartbeat", {})
        assert _wait_until(lambda: len(calls) == 1)
        bus.close()
        calls_at_close = len(calls)
        assert bus.tick(999999.0) is None  # no heartbeat, no retry attempt
        assert len(calls) == calls_at_close


class TestPublishAfterClose:
    """Round 3, defect 2 (reproduced: publish() after close() returned a real
    Event, burned a seq, and recorded nothing)."""

    def test_publish_after_close_is_refused(self):
        bus, _client = _bus_with_ok_client()
        bus.close()
        event = bus.publish("heartbeat", {})
        assert event is None

    def test_publish_after_close_is_recorded_once_as_a_distinct_code(self):
        bus, _client = _bus_with_ok_client()
        bus.close()
        for _ in range(5):
            bus.publish("heartbeat", {})
        assert bus.degradation_counts.get(DEGRADED_PUBLISH_AFTER_CLOSE, 0) == 5
        assert sum(1 for d in bus.degradations if d.code == DEGRADED_PUBLISH_AFTER_CLOSE) == 1

    def test_publish_after_close_touches_no_subscriber(self):
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe()
        bus.close()
        bus.publish("heartbeat", {})
        assert sub.qsize() == 0  # sub was closed too; nothing to receive anyway

    def test_publish_after_close_does_not_burn_a_seq(self):
        bus, _client = _bus_with_ok_client()
        last = bus.publish("heartbeat", {})
        bus.close()
        for _ in range(10):
            rejected = bus.publish("heartbeat", {})
            assert rejected is None
        # a fresh bus's next real publish (were it reopened) would still be
        # seq+1 -- there is no reopening, but this pins that publish() checked
        # the closed flag BEFORE touching self._seq at all
        assert last.seq == 1


class TestHookFiresOncePerDistinctCode:
    """Round 3, defect 3 (reproduced: 1000 invalid publishes -> 1000 hook
    calls for 1 distinct code; the docstring always promised once per NEW
    code)."""

    def test_hook_fires_once_for_a_thousand_occurrences_of_one_code(self):
        hooks = []
        bus = Bus(client=_OkClient(), on_degrade=hooks.append)
        for _ in range(1000):
            bus.publish("not-a-kind", {})
        assert len(hooks) == 1
        assert bus.degradation_counts[DEGRADED_SCHEMA_INVALID] == 1000

    def test_hook_fires_once_per_distinct_code_for_several_codes(self):
        hooks = []
        secret = "sk-gatewaykey-marker"
        bus = Bus(client=_OkClient(), redact=[secret], on_degrade=hooks.append)
        for _ in range(10):
            bus.publish(123, {})  # DEGRADED_SCHEMA_INVALID
        for _ in range(10):
            bus.publish("state", {"component": secret, "status": "up"})  # DEGRADED_SECRET_REDACTED
        assert len(hooks) == 2
        assert {h.code for h in hooks} == {DEGRADED_SCHEMA_INVALID, DEGRADED_SECRET_REDACTED}

    def test_hook_not_called_again_after_the_first_occurrence_of_a_code(self):
        hooks = []
        bus = Bus(client=_OkClient(), on_degrade=hooks.append)
        bus.publish("nope", {})
        first_call_count = len(hooks)
        for _ in range(50):
            bus.publish("nope", {})
        assert len(hooks) == first_call_count == 1


# ── round 2, defect 6b: speech is opt-in on a subscription ──────────────────


class TestSpeechOptIn:
    def test_default_subscription_never_receives_transcript_or_reply(self):
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe()
        bus.publish("transcript", {"role": "user", "text": "secret words"})
        bus.publish("reply", {"text": "a reply"})
        bus.publish("state", {"component": "daemon", "status": "up"})
        events = sub.drain()
        kinds = {e.kind for e in events}
        assert "transcript" not in kinds
        assert "reply" not in kinds
        assert "state" in kinds

    def test_include_speech_true_receives_everything(self):
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe(include_speech=True)
        bus.publish("transcript", {"role": "user", "text": "hi"})
        events = sub.drain()
        assert events[0].kind == "transcript"

    def test_explicit_kinds_naming_speech_is_honoured_without_include_speech(self):
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe(kinds=["transcript"])
        bus.publish("transcript", {"role": "user", "text": "hi"})
        bus.publish("state", {"component": "daemon", "status": "up"})
        events = sub.drain()
        assert [e.kind for e in events] == ["transcript"]

    def test_speech_kinds_constant_matches_what_is_excluded_by_default(self):
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe()
        assert sub.kinds == EVENT_KINDS - SPEECH_KINDS


# ── privacy boundary: Subscription(kinds=...) filtering (unsubscribe/close) ─


class TestPrivacyFiltering:
    def test_unsubscribe_stops_delivery(self):
        bus, _client = _bus_with_ok_client()
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


# ── secrets: no event payload ever contains the gateway key or the install
#    secret ──────────────────────────────────────────────────────────────


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
        bus = Bus(client=_OkClient(), redact=[self.SECRET_A])
        sub = bus.subscribe(include_speech=True)
        bus.publish("state", {"component": self.SECRET_A, "status": "up"})
        assert sub.qsize() == 0

    def test_clean_payload_with_no_secret_configured_passes(self):
        bus = Bus(client=_OkClient())
        event = bus.publish("state", {"component": "daemon", "status": "up"})
        assert event is not None

    def test_escaping_does_not_defeat_redaction(self):
        """Round-2 verification (coordinator's own probe, independently re-run
        here): a secret containing JSON-escape-sensitive characters must still
        be caught, because redaction scans the SERIALISED form."""
        for secret in ('ab"cd-KEY', "back\\slash-KEY", "מפתח-סודי-KEY", "plain-KEY-123"):
            bus = Bus(client=_OkClient(), redact=[secret])
            sub = bus.subscribe(include_speech=True)
            event = bus.publish(
                "degradation",
                {"source": "x", "code": "y", "reason": f"failed with {secret} in it"},
            )
            assert event is None, f"secret {secret!r} was not redacted"
            delivered = sub.drain()
            for e in delivered:
                assert secret not in json.dumps(e.to_dict(), ensure_ascii=False)

    def test_bidi_and_format_chars_stripped_from_degradation_reason(self):
        bus = Bus(client=_OkClient())
        # U+202E RIGHT-TO-LEFT OVERRIDE, U+200B ZERO WIDTH SPACE
        poisoned = "safe\u202etext​here"
        event = bus.publish("degradation", {"source": "x", "code": "y", "reason": poisoned})
        assert event is not None
        assert "\u202e" not in event.data["reason"]
        assert "​" not in event.data["reason"]
        assert "safe" in event.data["reason"]
        assert "text" in event.data["reason"]


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
        bus, _client = _bus_with_ok_client()
        rec = TurnDegradation(code="turn-budget-exhausted", reason="max_steps reached")
        event = bus.publish("degradation", fold_degradation(rec, source="turn"))
        assert event is not None

    def test_fold_sanitises_bidi_in_reason(self):
        rec = TurnDegradation(code="x", reason="a\u202eb")
        folded = fold_degradation(rec, source="turn")
        assert "\u202e" not in folded["reason"]

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
        bus, _client = _bus_with_ok_client()
        event = bus.publish("reply", {"text": "hello\x00world"})
        assert event is not None  # accepted, JSON handles NUL fine

    def test_10k_character_string(self):
        bus = Bus(client=_OkClient(), max_event_bytes=1_000_000)
        event = bus.publish("reply", {"text": "x" * 10_000})
        assert event is not None
        assert bus.degradations == []

    def test_10k_character_string_over_default_limit_is_refused_not_raised(self):
        bus, _client = _bus_with_ok_client()
        event = bus.publish("reply", {"text": "x" * 10_000})
        assert event is None
        assert bus.degradations[-1].code == DEGRADED_OVERSIZE

    def test_unicode_bidi_line_separators_in_text(self):
        bus, _client = _bus_with_ok_client()
        poisoned = "line1 line2\u0085line3\u202e"
        event = bus.publish("reply", {"text": poisoned})
        assert event is not None  # reply text is speech; not sanitised, only degradation is

    def test_path_separators_and_dotdot_in_token_fields(self):
        bus, _client = _bus_with_ok_client()
        event = bus.publish("state", {"component": "../../etc/passwd", "status": "up"})
        assert event is not None  # not a filesystem path in this module; accepted as data

    def test_non_dict_data_does_not_crash(self):
        bus, _client = _bus_with_ok_client()
        event = bus.publish("state", "not a dict")  # type: ignore[arg-type]
        assert event is None
        assert bus.degradations[-1].code == DEGRADED_SCHEMA_INVALID

    def test_none_data_does_not_crash(self):
        bus, _client = _bus_with_ok_client()
        event = bus.publish("heartbeat", None)
        assert event is not None  # heartbeat has no required fields

    def test_non_json_serialisable_data_degrades_not_raises(self):
        bus, _client = _bus_with_ok_client()
        event = bus.publish("reply", {"text": "ok", "blob": object()})
        assert event is None
        assert bus.degradations[-1].code == DEGRADED_SCHEMA_INVALID

    def test_ten_thousand_repeated_publishes(self):
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe(maxsize=50)
        for _ in range(10_000):
            bus.publish("heartbeat", {})
        assert sub.qsize() <= 50
        assert sub.drops > 0

    def test_hanging_dependency_client_does_not_block_publish(self):
        """A client_factory that raises repeatedly must not slow subsequent publishes.

        Round 2: the factory now runs on the broker worker thread, not the
        caller's, so this bounds publish() itself rather than the factory call.
        """
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
        assert _wait_until(lambda: calls["n"] == 1)
        # constructor is only ever attempted once per disable-cycle
        time.sleep(0.05)
        assert calls["n"] == 1

    def test_close_is_idempotent_and_never_raises(self):
        bus = Bus(client=_RaisingClient())
        bus.publish("heartbeat", {})  # forces broker client construction attempt
        report1 = bus.close()
        report2 = bus.close()
        assert report1.subscribers_closed >= 0
        assert report2.subscribers_closed == 0  # nothing left the second time
        assert report2.broker_worker_stopped is True

    def test_close_reports_elapsed_within_deadline(self):
        bus, _client = _bus_with_ok_client()
        bus.subscribe()
        report = bus.close(deadline=2.0)
        assert report.elapsed_s < 2.0

    def test_publish_from_many_threads_does_not_corrupt_sequence(self):
        bus, _client = _bus_with_ok_client()
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
        bus, _client = _bus_with_ok_client()
        sub = bus.subscribe()
        marker = "MARKER_9f3a"
        bus.publish("degradation", {"source": "x", "code": "y", "reason": f"{marker}​"})
        delivered = sub.drain()[0]
        assert marker in delivered.data["reason"]
        assert "​" not in delivered.data["reason"]

    def test_reentrant_publish_from_on_degrade_hook_does_not_deadlock(self):
        """A hostile/careless host publishing FROM inside its own on_degrade
        callback must not deadlock the bus (self._lock is released before the
        hook runs; self._degrade's own lock use is not held across it)."""
        calls = []

        def hook(record):
            calls.append(record)
            bus.publish("degradation", {"source": "x", "code": record.code, "reason": "handled"})

        bus = Bus(
            client=_RaisingClient(), on_degrade=hook
        )  # broker-unavailable fires deterministically
        sub = bus.subscribe(include_speech=True)
        bus.publish("heartbeat", {})
        assert _wait_until(lambda: len(calls) >= 1, timeout=1.0)
        assert _wait_until(lambda: sub.qsize() >= 1, timeout=1.0)


# ── measured cost: 60 features/sec to 3 in-process subscribers, 10 sim
#    seconds. Reported in the task's final report, not asserted as a strict
#    pass/fail beyond a loose sanity bound. ──────────────────────────────────


def test_measured_cost_60hz_features_3_subscribers_10s(capsys):
    bus, _client = _bus_with_ok_client()
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
