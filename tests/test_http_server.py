"""The HTTP surface, driven over a real socket (task ``t16``).

Everything here binds a real ``ThreadingHTTPServer`` on 127.0.0.1 and speaks
to it with ``http.client``, because the three refusals the acceptance criterion
names are properties of the *served* request, not of a function call: a
rebinding ``Host`` only means anything once something has parsed a request line.

Three criteria, and where each is proved:

1. the foreign ``Origin`` / rebinding ``Host`` / public-host-without-an-Access-
   assertion trio, refused on a control POST **and** on the SSE stream —
   :class:`TestTheThreeRefusalsOnTheControlApi`,
   :class:`TestTheThreeRefusalsOnTheStream`;
2. a routable bind without ``--bind-public`` is a ``CliError`` with a hint —
   :class:`TestResolveBind`;
3. no served asset and no captured stream carries the gateway key or the
   install secret, and a missing ``web/dist`` is a recorded no-dashboard state
   rather than a 500 — :class:`TestNoSecretIsEverServed`,
   :class:`TestAMissingDashboard`.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest

from embodiment.bus import Bus
from embodiment.cli._errors import CliError
from embodiment.http import guard as g
from embodiment.http import server as s

MARKER_GATEWAY_KEY = "MARKER-GATEWAY-KEY-8c41d2"  # nosec B105 - a test literal
MARKER_SECRET = "MARKER-INSTALL-SECRET-1f7be3"  # nosec B105 - a test literal
MARKER_SPEECH = "MARKER-WHAT-THE-USER-SAID"


def free_port() -> int:
    """A port nothing is listening on, so the Origin can be built before binding."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Harness:
    """A running server plus the request helpers every test here uses."""

    def __init__(self, server: s.DashboardServer, port: int) -> None:
        self.server = server
        self.port = port
        self.origin = f"http://127.0.0.1:{port}"

    def headers(self, **overrides: Optional[str]) -> dict[str, str]:
        base = {
            "Host": f"127.0.0.1:{self.port}",
            "Origin": self.origin,
            "Authorization": f"Bearer {MARKER_SECRET}",
        }
        for key, value in overrides.items():
            name = key.replace("_", "-")
            if value is None:
                base.pop(name, None)
            else:
                base[name] = value
        return base

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[bytes] = None,
        hdrs: Optional[dict[str, str]] = None,
    ) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=hdrs if hdrs is not None else {})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def open_stream(
        self, path: str = "/api/events", *, hdrs: Optional[dict[str, str]] = None
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers=hdrs if hdrs is not None else self.headers())
        return conn, conn.getresponse()


def read_stream(response: http.client.HTTPResponse, *, seconds: float = 3.0) -> str:
    """Whatever the stream emits within *seconds*. Never blocks past it."""
    chunks: list[bytes] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            line = response.fp.readline()  # type: ignore[union-attr]
        except (TimeoutError, OSError):
            break
        if not line:
            break
        chunks.append(line)
    return b"".join(chunks).decode("utf-8", "replace")


def read_until(response: http.client.HTTPResponse, needle: str, *, seconds: float = 5.0) -> str:
    """Read until *needle* appears or *seconds* elapse; return what was read."""
    buffered = ""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and needle not in buffered:
        try:
            line = response.fp.readline()  # type: ignore[union-attr]
        except (TimeoutError, OSError):
            break
        if not line:
            break
        buffered += line.decode("utf-8", "replace")
    return buffered


def build(
    *,
    dist_dir: Optional[Path] = None,
    bus: Optional[Bus] = None,
    controls: Optional[s.Controls] = None,
    redact: tuple[str, ...] = (MARKER_GATEWAY_KEY, MARKER_SECRET),
    public_hostname: Optional[str] = None,
    max_streams: int = s.DEFAULT_MAX_STREAMS,
    max_body_bytes: int = s.DEFAULT_MAX_BODY_BYTES,
) -> Harness:
    port = free_port()
    guard = g.Guard(
        g.GuardConfig(
            install_secret=MARKER_SECRET,
            allowed_origins=frozenset({f"http://127.0.0.1:{port}"}),
            public_hostname=public_hostname,
        )
    )
    config = s.ServerConfig(
        bind="127.0.0.1",
        port=port,
        dist_dir=dist_dir,
        redact=redact,
        max_streams=max_streams,
        max_body_bytes=max_body_bytes,
        stream_poll_s=0.05,
        keepalive_s=0.2,
    )
    server = s.DashboardServer(
        config=config, guard=guard, bus=bus, controls=controls or s.Controls()
    )
    server.start()
    return Harness(server, port)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    built = tmp_path / "dist"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text("<!doctype html><title>gwen</title>", encoding="utf-8")
    (built / "assets" / "app.js").write_text("export const hello = 1;\n", encoding="utf-8")
    (built / "assets" / "app.css").write_text(":root{color:#fff}\n", encoding="utf-8")
    return built


@pytest.fixture
def harness(dist: Path) -> Iterator[Harness]:
    built = build(dist_dir=dist)
    try:
        yield built
    finally:
        built.server.shutdown(2.0)


# ── criterion 2: the bind flag ───────────────────────────────────────────────


class TestResolveBind:
    """A routable bind without the explicit flag is a CliError with a hint."""

    @pytest.mark.parametrize("address", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.53"])
    def test_a_loopback_bind_needs_no_flag(self, address: str) -> None:
        assert s.resolve_bind(address, bind_public=False) == address

    @pytest.mark.parametrize(
        "address", ["0.0.0.0", "::", "192.168.1.10", "10.0.0.4", "gwen.example.org", ""]
    )
    def test_a_routable_bind_without_the_flag_is_a_cli_error(self, address: str) -> None:
        with pytest.raises(CliError) as caught:
            s.resolve_bind(address, bind_public=False)
        assert caught.value.code == 1
        assert "--bind-public" in caught.value.remediation

    def test_the_error_message_names_the_address_and_the_risk(self) -> None:
        with pytest.raises(CliError) as caught:
            s.resolve_bind("0.0.0.0", bind_public=False)
        assert "0.0.0.0" in caught.value.message

    @pytest.mark.parametrize("address", ["0.0.0.0", "192.168.1.10"])
    def test_the_flag_allows_it(self, address: str) -> None:
        assert s.resolve_bind(address, bind_public=True) == address

    def test_a_hostile_address_is_refused_rather_than_resolved(self) -> None:
        with pytest.raises(CliError):
            s.resolve_bind("127.0.0.1\nX: 1", bind_public=False)

    def test_the_server_refuses_a_routable_bind_at_construction(self) -> None:
        with pytest.raises(CliError):
            s.DashboardServer(
                config=s.ServerConfig(bind="0.0.0.0", port=free_port()),
                guard=g.Guard(g.GuardConfig(install_secret=MARKER_SECRET)),
            )


# ── criterion 1: the three refusals, on the control API ──────────────────────


class TestTheThreeRefusalsOnTheControlApi:
    def test_a_foreign_origin_post_is_refused(self, harness: Harness) -> None:
        status, body = harness.request(
            "POST", "/api/voice/start", hdrs=harness.headers(Origin="https://evil.example")
        )
        assert status == 403
        assert g.REFUSED_ORIGIN_CODE.encode() in body

    def test_a_rebinding_host_post_is_refused(self, harness: Harness) -> None:
        status, body = harness.request(
            "POST",
            "/api/voice/start",
            hdrs=harness.headers(Host="attacker.example", Origin=None),
        )
        assert status == 403
        assert g.REFUSED_HOST_CODE.encode() in body

    def test_the_public_host_without_an_assertion_is_refused(self) -> None:
        built = build(public_hostname="gwen.example.org")
        try:
            status, body = built.request(
                "POST",
                "/api/voice/start",
                hdrs=built.headers(Host="gwen.example.org", Origin=None),
            )
            assert status == 401
            assert g.REFUSED_ACCESS_MISSING_CODE.encode() in body
        finally:
            built.server.shutdown(2.0)

    def test_a_refusal_never_echoes_the_offending_header(self, harness: Harness) -> None:
        _, body = harness.request(
            "POST", "/api/voice/start", hdrs=harness.headers(Origin="https://evil.example")
        )
        assert b"evil.example" not in body

    def test_a_refused_request_never_reaches_the_control_callable(self) -> None:
        calls: list[str] = []
        built = build(controls=s.Controls(start_voice=lambda: calls.append("start") or {}))
        try:
            built.request(
                "POST", "/api/voice/start", hdrs=built.headers(Origin="https://evil.example")
            )
            assert calls == []
        finally:
            built.server.shutdown(2.0)

    def test_refusals_are_counted_in_status(self, harness: Harness) -> None:
        harness.request(
            "POST", "/api/voice/start", hdrs=harness.headers(Origin="https://evil.example")
        )
        status = harness.server.status()
        assert status["requests_refused"] == 1
        assert status["refusals_by_code"][g.REFUSED_ORIGIN_CODE] == 1


class TestTheThreeRefusalsOnTheStream:
    """The stream carries the transcript, so it is guarded exactly as a POST is."""

    def test_a_foreign_origin_stream_is_refused(self, harness: Harness) -> None:
        status, body = harness.request(
            "GET", "/api/events", hdrs=harness.headers(Origin="https://evil.example")
        )
        assert status == 403
        assert g.REFUSED_ORIGIN_CODE.encode() in body

    def test_a_rebinding_host_stream_is_refused(self, harness: Harness) -> None:
        status, body = harness.request(
            "GET", "/api/events", hdrs=harness.headers(Host="attacker.example", Origin=None)
        )
        assert status == 403
        assert g.REFUSED_HOST_CODE.encode() in body

    def test_the_public_host_stream_without_an_assertion_is_refused(self) -> None:
        built = build(public_hostname="gwen.example.org")
        try:
            status, body = built.request(
                "GET", "/api/events", hdrs=built.headers(Host="gwen.example.org", Origin=None)
            )
            assert status == 401
            assert g.REFUSED_ACCESS_MISSING_CODE.encode() in body
        finally:
            built.server.shutdown(2.0)

    def test_an_unauthenticated_stream_is_refused(self, harness: Harness) -> None:
        status, _ = harness.request("GET", "/api/events", hdrs=harness.headers(Authorization=None))
        assert status == 401

    def test_a_refused_stream_never_subscribes_to_the_bus(self) -> None:
        bus = Bus()
        built = build(bus=bus)
        try:
            built.request("GET", "/api/events", hdrs=built.headers(Origin="https://evil.example"))
            assert built.server.status()["streams_started"] == 0
        finally:
            built.server.shutdown(2.0)
            bus.close(1.0)


# ── criterion 3a: no secret is ever served ───────────────────────────────────


class TestNoSecretIsEverServed:
    def test_no_served_asset_carries_a_secret(self, harness: Harness, dist: Path) -> None:
        served = []
        for path in ("/", "/index.html", "/assets/app.js", "/assets/app.css"):
            status, body = harness.request("GET", path, hdrs=harness.headers())
            assert status == 200, path
            served.append(body)
        blob = b"".join(served)
        assert MARKER_GATEWAY_KEY.encode() not in blob
        assert MARKER_SECRET.encode() not in blob

    def test_no_api_response_carries_a_secret(self, harness: Harness) -> None:
        bodies = [
            harness.request("GET", "/api/status", hdrs=harness.headers())[1],
            harness.request("POST", "/api/voice/start", hdrs=harness.headers())[1],
            harness.request("GET", "/api/status", hdrs=harness.headers(Authorization=None))[1],
        ]
        blob = b"".join(bodies)
        assert MARKER_GATEWAY_KEY.encode() not in blob
        assert MARKER_SECRET.encode() not in blob

    def test_no_captured_stream_carries_a_secret(self) -> None:
        bus = Bus()
        built = build(bus=bus)
        try:
            conn, response = built.open_stream()
            bus.publish("state", {"component": "voice", "status": "listening"})
            bus.publish("transcript", {"role": "user", "text": MARKER_SPEECH})
            captured = read_until(response, MARKER_SPEECH, seconds=3.0)
            conn.close()
            assert MARKER_SPEECH in captured, "the stream did not deliver at all"
            assert MARKER_GATEWAY_KEY not in captured
            assert MARKER_SECRET not in captured
        finally:
            built.server.shutdown(2.0)
            bus.close(1.0)

    def test_an_event_that_would_carry_a_secret_is_dropped_and_recorded(self) -> None:
        bus = Bus()
        built = build(bus=bus)
        try:
            conn, response = built.open_stream()
            bus.publish("state", {"component": MARKER_GATEWAY_KEY, "status": "leaking"})
            bus.publish("state", {"component": "voice", "status": "listening"})
            captured = read_until(response, "listening", seconds=3.0)
            conn.close()
            assert MARKER_GATEWAY_KEY not in captured
            assert "listening" in captured
            codes = built.server.status()["degradation_counts"]
            assert codes.get(s.SECRET_IN_STREAM_CODE) == 1
        finally:
            built.server.shutdown(2.0)
            bus.close(1.0)

    def test_the_server_never_reads_a_gateway_key_attribute(self) -> None:
        """The key is redaction input only; nothing in the surface exposes it."""
        built = build()
        try:
            rendered = json.dumps(built.server.status())
            assert MARKER_GATEWAY_KEY not in rendered
            assert MARKER_SECRET not in rendered
        finally:
            built.server.shutdown(2.0)


# ── criterion 3b: a missing web/dist ─────────────────────────────────────────


class TestAMissingDashboard:
    def test_a_missing_dist_serves_a_minimal_page_not_a_500(self, tmp_path: Path) -> None:
        built = build(dist_dir=tmp_path / "never-built")
        try:
            status, body = built.request("GET", "/", hdrs=built.headers())
            assert status == 200
            assert b"no dashboard" in body.lower()
        finally:
            built.server.shutdown(2.0)

    def test_the_no_dashboard_state_is_recorded_once_and_counted(self, tmp_path: Path) -> None:
        built = build(dist_dir=tmp_path / "never-built")
        try:
            built.request("GET", "/", hdrs=built.headers())
            built.request("GET", "/index.html", hdrs=built.headers())
            status = built.server.status()
            assert status["dashboard"] == "absent"
            codes = [entry["code"] for entry in status["degradations"]]
            assert codes.count(s.NO_DASHBOARD_CODE) == 1
            assert status["degradation_counts"][s.NO_DASHBOARD_CODE] == 2
        finally:
            built.server.shutdown(2.0)

    def test_the_api_still_works_without_a_dashboard(self, tmp_path: Path) -> None:
        built = build(
            dist_dir=tmp_path / "never-built", controls=s.Controls(status=lambda: {"ok": True})
        )
        try:
            status, body = built.request("GET", "/api/status", hdrs=built.headers())
            assert status == 200
            assert json.loads(body)["daemon"] == {"ok": True}
        finally:
            built.server.shutdown(2.0)

    def test_a_dist_that_is_a_file_is_an_absent_dashboard(self, tmp_path: Path) -> None:
        blocker = tmp_path / "dist"
        blocker.write_text("not a directory", encoding="utf-8")
        built = build(dist_dir=blocker)
        try:
            status, body = built.request("GET", "/", hdrs=built.headers())
            assert status == 200
            assert b"no dashboard" in body.lower()
        finally:
            built.server.shutdown(2.0)

    def test_the_default_dist_dir_is_inside_the_repo(self) -> None:
        assert s.default_dist_dir().name == "dist"
        assert s.default_dist_dir().parent.name == "web"


# ── static serving ───────────────────────────────────────────────────────────


class TestStatic:
    def test_the_index_is_served_at_the_root(self, harness: Harness) -> None:
        status, body = harness.request("GET", "/", hdrs=harness.headers())
        assert status == 200
        assert b"<!doctype html>" in body

    def test_an_asset_is_served_with_its_content_type(self, harness: Harness) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", harness.port, timeout=5)
        try:
            conn.request("GET", "/assets/app.js", headers=harness.headers())
            response = conn.getresponse()
            body = response.read()
            assert response.status == 200
            assert response.getheader("Content-Type") == "text/javascript; charset=utf-8"
            assert response.getheader("X-Content-Type-Options") == "nosniff"
            assert b"hello" in body
        finally:
            conn.close()

    @pytest.mark.parametrize(
        "path",
        [
            "/assets/../../../etc/passwd",
            "/assets/..%2f..%2f..%2fetc%2fpasswd",
            "/assets/%2e%2e/%2e%2e/secret.txt",
            "/../secret.txt",
            "//etc/passwd",
            "/assets/./../../secret.txt",
            "/assets/.%00./secret.txt",
        ],
    )
    def test_traversal_is_refused(self, harness: Harness, dist: Path, path: str) -> None:
        (dist.parent / "secret.txt").write_text("PLANTED-OUTSIDE-DIST", encoding="utf-8")
        status, body = harness.request("GET", path, hdrs=harness.headers())
        assert status in (403, 404), path
        assert b"PLANTED-OUTSIDE-DIST" not in body

    def test_a_symlink_out_of_dist_is_refused(self, harness: Harness, dist: Path) -> None:
        (dist.parent / "secret.txt").write_text("PLANTED-OUTSIDE-DIST", encoding="utf-8")
        (dist / "assets" / "escape.txt").symlink_to(dist.parent / "secret.txt")
        status, body = harness.request("GET", "/assets/escape.txt", hdrs=harness.headers())
        assert status in (403, 404)
        assert b"PLANTED-OUTSIDE-DIST" not in body

    def test_an_unknown_path_is_a_404(self, harness: Harness) -> None:
        status, _ = harness.request("GET", "/nope/nowhere", hdrs=harness.headers())
        assert status == 404

    def test_a_directory_is_not_listed(self, harness: Harness) -> None:
        status, body = harness.request("GET", "/assets/", hdrs=harness.headers())
        assert status in (403, 404)
        assert b"app.js" not in body

    def test_static_needs_no_credential(self, harness: Harness) -> None:
        status, _ = harness.request("GET", "/", hdrs={"Host": f"127.0.0.1:{harness.port}"})
        assert status == 200


# ── the control API ──────────────────────────────────────────────────────────


class TestControlApi:
    def test_an_unbound_control_answers_503_and_records(self, harness: Harness) -> None:
        status, body = harness.request("POST", "/api/voice/start", hdrs=harness.headers())
        assert status == 503
        assert s.CONTROL_UNBOUND_CODE.encode() in body
        counts = harness.server.status()["degradation_counts"]
        assert counts[s.CONTROL_UNBOUND_CODE] == 1

    def test_start_and_stop_reach_their_callables(self) -> None:
        calls: list[str] = []
        controls = s.Controls(
            start_voice=lambda: (calls.append("start"), {"state": "listening"})[1],
            stop_voice=lambda: (calls.append("stop"), {"state": "idle"})[1],
        )
        built = build(controls=controls)
        try:
            status, body = built.request("POST", "/api/voice/start", hdrs=built.headers())
            assert status == 200
            assert json.loads(body)["result"] == {"state": "listening"}
            built.request("POST", "/api/voice/stop", hdrs=built.headers())
            assert calls == ["start", "stop"]
        finally:
            built.server.shutdown(2.0)

    def test_mute_passes_the_boolean_through(self) -> None:
        seen: list[bool] = []
        built = build(controls=s.Controls(set_mute=lambda muted: (seen.append(muted), {})[1]))
        try:
            status, _ = built.request(
                "POST", "/api/mic/mute", body=b'{"muted": true}', hdrs=built.headers()
            )
            assert status == 200
            built.request("POST", "/api/mic/mute", body=b'{"muted": false}', hdrs=built.headers())
            assert seen == [True, False]
        finally:
            built.server.shutdown(2.0)

    @pytest.mark.parametrize(
        "body", [b"", b"not json", b"{}", b'{"muted": "yes"}', b'{"muted": 1}', b"[]"]
    )
    def test_a_bad_mute_body_is_a_400_and_never_reaches_the_callable(self, body: bytes) -> None:
        seen: list[bool] = []
        built = build(controls=s.Controls(set_mute=lambda muted: (seen.append(muted), {})[1]))
        try:
            status, response = built.request(
                "POST", "/api/mic/mute", body=body, hdrs=built.headers()
            )
            assert status == 400
            assert s.BAD_REQUEST_CODE.encode() in response
            assert seen == []
        finally:
            built.server.shutdown(2.0)

    def test_an_oversized_body_is_refused_without_being_read_into_memory(self) -> None:
        built = build(max_body_bytes=64)
        try:
            status, body = built.request(
                "POST", "/api/mic/mute", body=b"x" * 5000, hdrs=built.headers()
            )
            assert status == 413
            assert s.BODY_TOO_LARGE_CODE.encode() in body
        finally:
            built.server.shutdown(2.0)

    def test_a_control_that_raises_is_a_500_and_a_recorded_degradation(self) -> None:
        def boom() -> dict[str, Any]:
            raise RuntimeError(f"the daemon said {MARKER_SPEECH}")

        built = build(controls=s.Controls(start_voice=boom))
        try:
            status, body = built.request("POST", "/api/voice/start", hdrs=built.headers())
            assert status == 500
            assert MARKER_SPEECH.encode() not in body
            recorded = built.server.status()["degradations"]
            assert [entry["code"] for entry in recorded] == [s.CONTROL_FAILED_CODE]
            assert MARKER_SPEECH not in json.dumps(recorded)
            # still serving afterwards
            assert built.request("GET", "/api/status", hdrs=built.headers())[0] in (200, 503)
        finally:
            built.server.shutdown(2.0)

    def test_status_returns_the_daemon_snapshot_and_the_server_facts(self) -> None:
        built = build(controls=s.Controls(status=lambda: {"recall": "lexical"}))
        try:
            code, body = built.request("GET", "/api/status", hdrs=built.headers())
            payload = json.loads(body)
            assert code == 200
            assert payload["daemon"] == {"recall": "lexical"}
            assert payload["http"]["dashboard"] in ("present", "absent")
        finally:
            built.server.shutdown(2.0)

    def test_a_get_on_a_control_route_is_405(self, harness: Harness) -> None:
        status, _ = harness.request("GET", "/api/voice/start", hdrs=harness.headers())
        assert status == 405

    def test_an_unknown_api_route_is_404_not_static(self, harness: Harness) -> None:
        status, _ = harness.request("POST", "/api/nope", hdrs=harness.headers())
        assert status == 404


# ── the stream ───────────────────────────────────────────────────────────────


class TestTheStream:
    def test_an_authorised_stream_receives_published_events(self) -> None:
        bus = Bus()
        built = build(bus=bus)
        try:
            conn, response = built.open_stream()
            assert response.status == 200
            assert response.getheader("Content-Type") == "text/event-stream"
            assert response.getheader("Cache-Control") == "no-store"
            bus.publish("mic", {"hot": True, "ear": "host"})
            captured = read_until(response, '"mic"', seconds=3.0)
            conn.close()
            assert '"kind":"mic"' in captured.replace(" ", "")
            assert captured.startswith(":") or "data:" in captured
        finally:
            built.server.shutdown(2.0)
            bus.close(1.0)

    def test_the_stream_carries_the_transcript_which_is_why_it_is_guarded(self) -> None:
        bus = Bus()
        built = build(bus=bus)
        try:
            conn, response = built.open_stream()
            bus.publish("transcript", {"role": "user", "text": MARKER_SPEECH})
            captured = read_until(response, MARKER_SPEECH, seconds=3.0)
            conn.close()
            assert MARKER_SPEECH in captured
        finally:
            built.server.shutdown(2.0)
            bus.close(1.0)

    def test_an_idle_stream_emits_a_keepalive_comment(self) -> None:
        bus = Bus()
        built = build(bus=bus)
        try:
            conn, response = built.open_stream()
            captured = read_stream(response, seconds=1.0)
            conn.close()
            assert ":" in captured
        finally:
            built.server.shutdown(2.0)
            bus.close(1.0)

    def test_a_stream_without_a_bus_is_503_and_recorded(self) -> None:
        built = build(bus=None)
        try:
            status, body = built.request("GET", "/api/events", hdrs=built.headers())
            assert status == 503
            assert s.NO_BUS_CODE.encode() in body
            assert built.server.status()["degradation_counts"][s.NO_BUS_CODE] == 1
        finally:
            built.server.shutdown(2.0)

    def test_too_many_streams_is_refused_and_recorded(self) -> None:
        bus = Bus()
        built = build(bus=bus, max_streams=1)
        try:
            conn, response = built.open_stream()
            read_stream(response, seconds=0.3)
            status, body = built.request("GET", "/api/events", hdrs=built.headers())
            assert status == 503
            assert s.TOO_MANY_STREAMS_CODE.encode() in body
            conn.close()
            assert built.server.status()["streams_refused"] == 1
        finally:
            built.server.shutdown(2.0)
            bus.close(1.0)

    def test_a_disconnected_stream_frees_its_slot(self) -> None:
        bus = Bus()
        built = build(bus=bus, max_streams=1)
        try:
            conn, response = built.open_stream()
            read_stream(response, seconds=0.3)
            # BOTH, and in this order: ``socket.makefile`` holds a reference
            # that defers the real close, so closing only the connection
            # leaves the server's socket very much open.
            response.close()
            conn.close()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and built.server.status()["streams_open"] > 0:
                bus.publish("mic", {"hot": False, "ear": "host"})
                time.sleep(0.05)
            assert built.server.status()["streams_open"] == 0
        finally:
            built.server.shutdown(2.0)
            bus.close(1.0)


# ── shutdown is a feature ────────────────────────────────────────────────────


class TestShutdown:
    def test_shutdown_returns_a_report_within_its_deadline(self) -> None:
        built = build()
        started = time.monotonic()
        report = built.server.shutdown(2.0)
        assert time.monotonic() - started < 2.5
        assert report.serve_thread_stopped is True
        assert report.already_closed is False

    def test_shutdown_is_idempotent_and_never_raises(self) -> None:
        built = build()
        built.server.shutdown(2.0)
        again = built.server.shutdown(2.0)
        assert again.already_closed is True
        assert built.server.status()["closed"] is True

    def test_shutdown_closes_an_open_stream_within_the_deadline(self) -> None:
        bus = Bus()
        built = build(bus=bus)
        conn, response = built.open_stream()
        read_stream(response, seconds=0.3)
        started = time.monotonic()
        report = built.server.shutdown(2.0)
        elapsed = time.monotonic() - started
        conn.close()
        bus.close(1.0)
        assert elapsed < 2.5
        assert report.streams_closed == 1

    def test_the_socket_is_released_after_shutdown(self) -> None:
        built = build()
        port = built.port
        built.server.shutdown(2.0)
        rebind = socket.socket()
        rebind.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            rebind.bind(("127.0.0.1", port))
        finally:
            rebind.close()

    def test_a_request_after_shutdown_simply_fails_to_connect(self) -> None:
        built = build()
        built.server.shutdown(2.0)
        with pytest.raises(OSError):
            built.request("GET", "/", hdrs=built.headers())


# ── status, and what it may never contain ────────────────────────────────────


class TestStatus:
    def test_status_reports_the_facts_a_host_would_look_for(self, harness: Harness) -> None:
        status = harness.server.status()
        for key in (
            "bind",
            "port",
            "public_bind",
            "dashboard",
            "streams_open",
            "streams_started",
            "streams_refused",
            "requests_refused",
            "refusals_by_code",
            "degradations",
            "degradation_counts",
            "hook_errors",
            "closed",
        ):
            assert key in status, key
        assert status["public_bind"] is False
        assert status["dashboard"] == "present"

    def test_status_is_json_serialisable(self, harness: Harness) -> None:
        json.dumps(harness.server.status())

    def test_a_degrade_hook_that_raises_is_counted_not_propagated(self) -> None:
        def boom(code: str, reason: str) -> None:
            raise RuntimeError("hook is broken")

        port = free_port()
        server = s.DashboardServer(
            config=s.ServerConfig(bind="127.0.0.1", port=port),
            guard=g.Guard(g.GuardConfig(install_secret=MARKER_SECRET)),
            on_degrade=boom,
        )
        server.start()
        built = Harness(server, port)
        try:
            built.request("POST", "/api/voice/start", hdrs=built.headers(Origin=None))
            assert server.status()["hook_errors"] >= 1
        finally:
            server.shutdown(2.0)

    def test_the_degradation_list_is_bounded(self) -> None:
        server = s.DashboardServer(
            config=s.ServerConfig(bind="127.0.0.1", port=free_port()),
            guard=g.Guard(g.GuardConfig(install_secret=MARKER_SECRET)),
        )
        for index in range(s.MAX_DEGRADATION_CODES * 3):
            server._degrade(f"http-synthetic-{index}", "a synthetic code")
        status = server.status()
        assert len(status["degradations"]) <= s.MAX_DEGRADATION_CODES
        assert status["degradations_dropped"] > 0


# ── hammering it the way a real client would ─────────────────────────────────


class TestUnderLoad:
    def test_a_thousand_refused_posts_do_not_grow_the_record(self, harness: Harness) -> None:
        for _ in range(200):
            harness.request(
                "POST", "/api/voice/start", hdrs=harness.headers(Origin="https://evil.example")
            )
        status = harness.server.status()
        assert status["requests_refused"] == 200
        assert len(status["degradations"]) <= s.MAX_DEGRADATION_CODES

    def test_concurrent_requests_are_all_answered(self, harness: Harness) -> None:
        results: list[int] = []
        lock = threading.Lock()

        def hit() -> None:
            code, _ = harness.request("GET", "/", hdrs=harness.headers())
            with lock:
                results.append(code)

        threads = [threading.Thread(target=hit) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert results == [200] * 16

    @pytest.mark.parametrize(
        "path", ["/" + "a" * 5000, "/assets/" + "b" * 5000, "/api/" + "c" * 200, "/events/x"]
    )
    def test_a_huge_path_is_answered_not_crashed(self, harness: Harness, path: str) -> None:
        status, _ = harness.request("GET", path, hdrs=harness.headers())
        assert status in (403, 404, 414)


class TestAStuckStream:
    """Found by attacking it: a client that stops reading is not a disconnect.

    A browser tab closing is ordinary and only counted. A client that holds
    the connection open and stops draining it is a *wedged dashboard*, which
    looks healthy from the outside — so it is dropped on the socket timeout
    and recorded. See ``scratchpad/agent-t16/attack_http.py``'s backpressure
    probe, which is where this came from.
    """

    def test_a_write_timeout_drops_the_stream_and_records_it(self) -> None:
        bus = Bus()
        port = free_port()
        server = s.DashboardServer(
            config=s.ServerConfig(
                bind="127.0.0.1",
                port=port,
                dist_dir=None,
                stream_poll_s=0.05,
                keepalive_s=0.1,
                socket_timeout_s=0.5,
                max_streams=2,
            ),
            guard=g.Guard(g.GuardConfig(install_secret=MARKER_SECRET)),
            bus=bus,
        )
        server.start()
        sock = socket.socket()
        try:
            sock.connect(("127.0.0.1", port))
            sock.sendall(
                b"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer "
                + MARKER_SECRET.encode()
                + b"\r\n\r\n"
            )
            time.sleep(0.3)
            blob = "x" * 7000
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and server.status()["streams_open"] > 0:
                for _ in range(200):
                    bus.publish("transcript", {"role": "user", "text": blob})
                time.sleep(0.01)
            status = server.status()
            assert status["streams_open"] == 0, "a wedged client kept its slot"
            assert status["streams_timed_out"] == 1
            assert status["degradation_counts"][s.STREAM_WRITE_TIMEOUT_CODE] == 1
        finally:
            sock.close()
            server.shutdown(2.0)
            bus.close(1.0)

    def test_an_ordinary_disconnect_is_counted_but_not_a_degradation(self) -> None:
        bus = Bus()
        built = build(bus=bus)
        try:
            conn, response = built.open_stream()
            read_stream(response, seconds=0.3)
            response.close()
            conn.close()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and built.server.status()["streams_open"] > 0:
                bus.publish("mic", {"hot": False, "ear": "host"})
                time.sleep(0.05)
            status = built.server.status()
            assert status["streams_disconnected"] == 1
            assert s.STREAM_WRITE_TIMEOUT_CODE not in status["degradation_counts"]
            assert s.STREAM_FAILED_CODE not in status["degradation_counts"]
        finally:
            built.server.shutdown(2.0)
            bus.close(1.0)
