"""The guard: what may reach the daemon over HTTP, and what may never leave it.

Task ``t16``. Every acceptance criterion about refusal is proved here at the
policy level; ``tests/test_http_server.py`` proves the same three refusals
again end-to-end over a real socket, on both the control API and the stream.

The rule this file exists to pin: **loopback is not authentication.** A
Cloudflare tunnel delivers a remote request from ``cloudflared`` running on
127.0.0.1, so "the peer address is local" says nothing at all about who sent
it. What the guard checks instead is the install secret, the ``Host``, the
``Origin``, and — when the Host is the public hostname — a Cloudflare Access
assertion.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from embodiment.http import guard as g

MARKER_SECRET = "s3cr3t-MARKER-INSTALL-2f9a"  # nosec B105 - a test literal
MARKER_TOKEN = "MARKER-ACCESS-ASSERTION-7b31"  # nosec B105 - a test literal


def make_guard(**kwargs: object) -> g.Guard:
    """A guard on the defaults this task ships, with *kwargs* overriding."""
    config_kwargs: dict[str, object] = {
        "install_secret": MARKER_SECRET,
        "allowed_origins": frozenset({"http://127.0.0.1:8823"}),
    }
    verifier = kwargs.pop("assertion_verifier", None)
    on_degrade = kwargs.pop("on_degrade", None)
    config_kwargs.update(kwargs)
    config = g.GuardConfig(**config_kwargs)  # type: ignore[arg-type]
    return g.Guard(config, assertion_verifier=verifier, on_degrade=on_degrade)


def headers(**overrides: str | None) -> dict[str, str]:
    """The header set a same-origin browser POST would carry."""
    base = {
        "Host": "127.0.0.1:8823",
        "Origin": "http://127.0.0.1:8823",
        "Authorization": f"Bearer {MARKER_SECRET}",
    }
    for key, value in overrides.items():
        name = key.replace("_", "-")
        if value is None:
            base.pop(name, None)
        else:
            base[name] = value
    return base


# ── which requests are guarded at all ────────────────────────────────────────


class TestRequiresGuard:
    """State-changing requests AND the stream. Static assets are not guarded."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/api/voice/start"),
            ("POST", "/api/mic/mute"),
            ("DELETE", "/"),
            ("PUT", "/assets/app.js"),
            ("GET", "/api/events"),
            ("GET", "/api/events?since=4"),
            ("GET", "/api/status"),
        ],
    )
    def test_guarded(self, method: str, path: str) -> None:
        assert g.requires_guard(method, path) is True

    @pytest.mark.parametrize("path", ["/", "/index.html", "/assets/app.js", "/favicon.ico"])
    def test_static_get_is_not_guarded(self, path: str) -> None:
        assert g.requires_guard("GET", path) is False

    def test_the_routes_agreed_with_the_dashboard_are_all_guarded(self) -> None:
        """t17 connects to ``/api/events``; the whole ``/api/`` tree is guarded."""
        assert g.requires_guard("GET", "/api/events") is True
        assert g.requires_guard("GET", "/api/events?since=4") is True

    def test_the_original_bare_events_path_is_still_guarded(self) -> None:
        """The route moved under ``/api/`` mid-task. A stale client asking for
        the old path must be refused by the guard, never fall through to the
        static handler."""
        assert g.requires_guard("GET", "/events") is True

    def test_head_of_a_static_asset_is_not_guarded(self) -> None:
        assert g.requires_guard("HEAD", "/index.html") is False

    def test_a_lowercase_method_is_still_state_changing(self) -> None:
        assert g.requires_guard("post", "/") is True

    def test_an_unguarded_request_is_allowed_without_a_secret(self) -> None:
        decision = make_guard().check("GET", "/index.html", {"Host": "127.0.0.1:8823"})
        assert decision.allowed is True
        assert decision.guarded is False


# ── the three refusals the acceptance criterion names ────────────────────────


class TestForeignOriginIsRefused:
    def test_a_foreign_origin_is_refused(self) -> None:
        decision = make_guard().check(
            "POST", "/api/voice/start", headers(Origin="https://evil.example")
        )
        assert decision.allowed is False
        assert decision.code == g.REFUSED_ORIGIN_CODE
        assert decision.status == 403

    def test_the_same_foreign_origin_is_refused_on_the_stream(self) -> None:
        decision = make_guard().check("GET", "/api/events", headers(Origin="https://evil.example"))
        assert decision.allowed is False
        assert decision.code == g.REFUSED_ORIGIN_CODE

    def test_an_opaque_null_origin_is_refused(self) -> None:
        decision = make_guard().check("POST", "/api/mic/mute", headers(Origin="null"))
        assert decision.allowed is False
        assert decision.code == g.REFUSED_ORIGIN_CODE

    def test_a_prefix_of_an_allowed_origin_is_not_an_allowed_origin(self) -> None:
        decision = make_guard().check(
            "POST", "/api/mic/mute", headers(Origin="http://127.0.0.1:8823.evil.example")
        )
        assert decision.allowed is False
        assert decision.code == g.REFUSED_ORIGIN_CODE

    def test_the_allowed_origin_passes(self) -> None:
        assert make_guard().check("POST", "/api/voice/stop", headers()).allowed is True


class TestRebindingHostIsRefused:
    def test_a_rebinding_host_is_refused(self) -> None:
        decision = make_guard().check(
            "POST", "/api/voice/start", headers(Host="attacker.example", Origin=None)
        )
        assert decision.allowed is False
        assert decision.code == g.REFUSED_HOST_CODE
        assert decision.status == 403

    def test_the_same_rebinding_host_is_refused_on_the_stream(self) -> None:
        decision = make_guard().check("GET", "/api/events", headers(Host="attacker.example"))
        assert decision.allowed is False
        assert decision.code == g.REFUSED_HOST_CODE

    def test_an_absent_host_is_refused(self) -> None:
        decision = make_guard().check("POST", "/api/voice/start", headers(Host=None))
        assert decision.allowed is False
        assert decision.code == g.REFUSED_HOST_CODE

    def test_a_host_is_matched_case_insensitively_and_without_its_port(self) -> None:
        decision = make_guard().check("POST", "/api/voice/start", headers(Host="LOCALHOST:9999"))
        assert decision.allowed is True

    def test_a_bracketed_ipv6_loopback_is_allowed(self) -> None:
        decision = make_guard().check("POST", "/api/voice/start", headers(Host="[::1]:8823"))
        assert decision.allowed is True

    def test_the_configured_public_hostname_is_host_allowed_without_being_listed(self) -> None:
        """A public hostname the operator configured is never a rebinding host."""
        guard = make_guard(public_hostname="gwen.example.org")
        decision = guard.check(
            "POST",
            "/api/voice/start",
            headers(Host="gwen.example.org", **{"Cf-Access-Jwt-Assertion": MARKER_TOKEN}),
            # no verifier configured: refused, but for the ACCESS reason, not the host
        )
        assert decision.code != g.REFUSED_HOST_CODE


class TestPublicHostWithoutAnAssertionIsRefused:
    def test_the_public_host_without_an_assertion_is_refused(self) -> None:
        guard = make_guard(public_hostname="gwen.example.org")
        decision = guard.check("POST", "/api/voice/start", headers(Host="gwen.example.org"))
        assert decision.allowed is False
        assert decision.code == g.REFUSED_ACCESS_MISSING_CODE
        assert decision.status == 401

    def test_the_same_is_refused_on_the_stream(self) -> None:
        guard = make_guard(public_hostname="gwen.example.org")
        decision = guard.check("GET", "/api/events", headers(Host="gwen.example.org"))
        assert decision.allowed is False
        assert decision.code == g.REFUSED_ACCESS_MISSING_CODE

    def test_an_empty_assertion_header_counts_as_absent(self) -> None:
        guard = make_guard(public_hostname="gwen.example.org")
        decision = guard.check(
            "POST",
            "/api/voice/start",
            headers(Host="gwen.example.org", **{"Cf-Access-Jwt-Assertion": "   "}),
        )
        assert decision.code == g.REFUSED_ACCESS_MISSING_CODE

    def test_a_loopback_host_needs_no_assertion(self) -> None:
        guard = make_guard(public_hostname="gwen.example.org")
        assert guard.check("POST", "/api/voice/start", headers()).allowed is True


# ── the Access verifier seam: no dependency, so it fails closed ──────────────


class TestTheAccessVerifierSeam:
    def test_the_default_verifier_refuses_and_records_that_it_is_missing(self) -> None:
        recorded: list[tuple[str, str]] = []
        guard = make_guard(
            public_hostname="gwen.example.org",
            on_degrade=lambda code, reason: recorded.append((code, reason)),
        )
        decision = guard.check(
            "POST",
            "/api/voice/start",
            headers(Host="gwen.example.org", **{"Cf-Access-Jwt-Assertion": MARKER_TOKEN}),
        )
        assert decision.allowed is False
        assert decision.code == g.ACCESS_VERIFIER_MISSING_CODE
        assert [code for code, _ in recorded] == [g.ACCESS_VERIFIER_MISSING_CODE]

    def test_the_default_verifier_never_returns_valid(self) -> None:
        assert g.refusing_assertion_verifier(MARKER_TOKEN).valid is False
        assert g.refusing_assertion_verifier("").valid is False

    def test_an_injected_verifier_that_accepts_lets_the_request_through(self) -> None:
        guard = make_guard(
            public_hostname="gwen.example.org",
            assertion_verifier=lambda token: g.AssertionResult(valid=True),
        )
        decision = guard.check(
            "POST",
            "/api/voice/start",
            headers(Host="gwen.example.org", **{"Cf-Access-Jwt-Assertion": MARKER_TOKEN}),
        )
        assert decision.allowed is True

    def test_an_injected_verifier_that_rejects_is_refused_with_its_own_code(self) -> None:
        guard = make_guard(
            public_hostname="gwen.example.org",
            assertion_verifier=lambda token: g.AssertionResult(
                valid=False, code="http-refused-access-expired"
            ),
        )
        decision = guard.check(
            "GET",
            "/api/events",
            headers(Host="gwen.example.org", **{"Cf-Access-Jwt-Assertion": MARKER_TOKEN}),
        )
        assert decision.allowed is False
        assert decision.code == "http-refused-access-expired"

    def test_a_verifier_that_raises_refuses_and_records_rather_than_propagating(self) -> None:
        recorded: list[tuple[str, str]] = []

        def boom(token: str) -> g.AssertionResult:
            raise RuntimeError(f"jwks fetch failed for {token}")

        guard = make_guard(
            public_hostname="gwen.example.org",
            assertion_verifier=boom,
            on_degrade=lambda code, reason: recorded.append((code, reason)),
        )
        decision = guard.check(
            "POST",
            "/api/voice/start",
            headers(Host="gwen.example.org", **{"Cf-Access-Jwt-Assertion": MARKER_TOKEN}),
        )
        assert decision.allowed is False
        assert decision.code == g.REFUSED_ACCESS_VERIFIER_FAILED_CODE
        assert [code for code, _ in recorded] == [g.REFUSED_ACCESS_VERIFIER_FAILED_CODE]
        assert MARKER_TOKEN not in recorded[0][1]

    def test_a_verifier_returning_rubbish_is_treated_as_a_refusal(self) -> None:
        guard = make_guard(
            public_hostname="gwen.example.org",
            assertion_verifier=lambda token: "yes",  # type: ignore[return-value]
        )
        decision = guard.check(
            "POST",
            "/api/voice/start",
            headers(Host="gwen.example.org", **{"Cf-Access-Jwt-Assertion": MARKER_TOKEN}),
        )
        assert decision.allowed is False
        assert decision.code == g.REFUSED_ACCESS_VERIFIER_FAILED_CODE


# ── the install secret ───────────────────────────────────────────────────────


class TestTheInstallSecret:
    def test_no_credential_at_all_is_refused(self) -> None:
        decision = make_guard().check("POST", "/api/voice/start", headers(Authorization=None))
        assert decision.allowed is False
        assert decision.code == g.REFUSED_SECRET_CODE
        assert decision.status == 401

    def test_a_wrong_secret_is_refused(self) -> None:
        decision = make_guard().check(
            "POST", "/api/voice/start", headers(Authorization="Bearer not-the-secret")
        )
        assert decision.code == g.REFUSED_SECRET_CODE

    def test_a_secret_that_is_a_prefix_of_the_real_one_is_refused(self) -> None:
        decision = make_guard().check(
            "POST", "/api/voice/start", headers(Authorization=f"Bearer {MARKER_SECRET[:-1]}")
        )
        assert decision.code == g.REFUSED_SECRET_CODE

    def test_the_bearer_scheme_is_case_insensitive(self) -> None:
        decision = make_guard().check(
            "POST", "/api/voice/start", headers(Authorization=f"bearer {MARKER_SECRET}")
        )
        assert decision.allowed is True

    def test_a_cookie_carries_the_secret_for_eventsource(self) -> None:
        """``EventSource`` cannot set a header, so the stream authenticates by cookie."""
        decision = make_guard().check(
            "GET",
            "/api/events",
            headers(Authorization=None, Cookie=f"{g.SECRET_COOKIE_NAME}={MARKER_SECRET}"),
        )
        assert decision.allowed is True

    def test_a_cookie_among_other_cookies_is_found(self) -> None:
        decision = make_guard().check(
            "GET",
            "/api/events",
            headers(
                Authorization=None,
                Cookie=f"theme=dark; {g.SECRET_COOKIE_NAME}={MARKER_SECRET} ; tz=UTC",
            ),
        )
        assert decision.allowed is True

    def test_a_cookie_without_an_origin_is_refused(self) -> None:
        """The CSRF rule: a cookie is only trusted when a browser also named its origin."""
        decision = make_guard().check(
            "POST",
            "/api/voice/start",
            headers(
                Authorization=None,
                Origin=None,
                Cookie=f"{g.SECRET_COOKIE_NAME}={MARKER_SECRET}",
            ),
        )
        assert decision.allowed is False
        assert decision.code == g.REFUSED_COOKIE_WITHOUT_ORIGIN_CODE

    def test_a_bearer_without_an_origin_is_allowed_for_a_non_browser_client(self) -> None:
        decision = make_guard().check("POST", "/api/voice/start", headers(Origin=None))
        assert decision.allowed is True

    def test_an_unconfigured_secret_refuses_everything_guarded(self) -> None:
        guard = make_guard(install_secret="")
        decision = guard.check("POST", "/api/voice/start", headers(Authorization="Bearer "))
        assert decision.allowed is False
        assert decision.code == g.REFUSED_SECRET_CODE

    def test_an_unconfigured_secret_still_serves_static_assets(self) -> None:
        guard = make_guard(install_secret="")
        assert guard.check("GET", "/index.html", {"Host": "localhost"}).allowed is True


# ── header-level attacks ─────────────────────────────────────────────────────


class TestHeaderAttacks:
    def test_a_duplicated_host_header_is_refused(self) -> None:
        """Request smuggling: two Hosts, one for the guard and one for the router."""
        raw = [
            ("Host", "127.0.0.1:8823"),
            ("Host", "attacker.example"),
            ("Origin", "http://127.0.0.1:8823"),
            ("Authorization", f"Bearer {MARKER_SECRET}"),
        ]
        decision = make_guard().check("POST", "/api/voice/start", raw)
        assert decision.allowed is False
        assert decision.code == g.REFUSED_DUPLICATE_HEADER_CODE

    def test_a_duplicated_origin_header_is_refused(self) -> None:
        raw = [
            ("Host", "127.0.0.1:8823"),
            ("Origin", "http://127.0.0.1:8823"),
            ("Origin", "https://evil.example"),
            ("Authorization", f"Bearer {MARKER_SECRET}"),
        ]
        assert make_guard().check("POST", "/", raw).code == g.REFUSED_DUPLICATE_HEADER_CODE

    def test_an_identical_repeated_header_is_not_treated_as_smuggling(self) -> None:
        raw = [
            ("Host", "127.0.0.1:8823"),
            ("Host", "127.0.0.1:8823"),
            ("Origin", "http://127.0.0.1:8823"),
            ("Authorization", f"Bearer {MARKER_SECRET}"),
        ]
        assert make_guard().check("POST", "/", raw).allowed is True

    @pytest.mark.parametrize("payload", ["evil\r\nX: 1", "evil\n", "evil\x00", "evil\x85host"])
    def test_a_control_character_in_a_guarded_header_is_refused(self, payload: str) -> None:
        decision = make_guard().check("POST", "/api/voice/start", headers(Host=payload))
        assert decision.allowed is False
        assert decision.code in {g.REFUSED_MALFORMED_HEADER_CODE, g.REFUSED_HOST_CODE}

    def test_a_bidi_override_in_an_origin_is_refused(self) -> None:
        decision = make_guard().check(
            "POST", "/api/voice/start", headers(Origin="http://127.0.0.1:8823‮")
        )
        assert decision.allowed is False

    def test_a_ten_thousand_character_header_is_refused_and_not_echoed(self) -> None:
        huge = "h" * 10_000
        decision = make_guard().check("POST", "/api/voice/start", headers(Host=huge))
        assert decision.allowed is False
        assert huge not in decision.reason
        assert len(decision.reason) < 300

    def test_a_ten_thousand_character_cookie_does_not_match_the_secret(self) -> None:
        decision = make_guard().check(
            "GET", "/api/events", headers(Authorization=None, Cookie="a" * 10_000)
        )
        assert decision.code == g.REFUSED_SECRET_CODE

    def test_a_path_with_a_null_byte_is_still_classified(self) -> None:
        assert g.requires_guard("GET", "/events\x00.js") is True

    def test_checking_never_raises_on_hostile_input(self) -> None:
        for hostile in ("/" * 5000, "\x00", "/events?" + "a" * 20_000, "..%2f..%2fevents"):
            assert make_guard().check("POST", hostile, headers()) is not None


# ── nothing the guard emits carries a credential ─────────────────────────────


class TestTheGuardLeaksNothing:
    @pytest.mark.parametrize(
        "case",
        [
            {"Origin": "https://evil.example"},
            {"Host": "attacker.example"},
            {"Authorization": f"Bearer {MARKER_SECRET}x"},
            {"Authorization": None, "Cookie": f"{g.SECRET_COOKIE_NAME}={MARKER_SECRET}x"},
        ],
    )
    def test_no_decision_carries_the_secret_or_the_offending_value(self, case: dict) -> None:
        recorded: list[tuple[str, str]] = []
        guard = make_guard(on_degrade=lambda code, reason: recorded.append((code, reason)))
        decision = guard.check("POST", "/api/voice/start", headers(**case))
        rendered = f"{decision.code} {decision.reason} {recorded}"
        assert MARKER_SECRET not in rendered
        assert "evil.example" not in rendered
        assert "attacker.example" not in rendered

    def test_an_allowed_decision_carries_no_secret_either(self) -> None:
        decision = make_guard().check("POST", "/api/voice/start", headers())
        assert MARKER_SECRET not in f"{decision.code} {decision.reason}"

    def test_every_refusal_code_is_prefixed_with_the_module_name(self) -> None:
        codes = [value for name, value in vars(g).items() if name.endswith("_CODE")]
        assert codes
        assert all(code.startswith("http-") for code in codes)


# ── the secret file on disk ──────────────────────────────────────────────────


class TestInstallSecretFile:
    def test_a_first_start_creates_a_private_secret_and_records_it(self, tmp_path: Path) -> None:
        result = g.load_or_create_install_secret(tmp_path / "state")
        assert result.created is True
        assert result.persisted is True
        assert result.code == g.SECRET_CREATED_CODE
        assert len(result.secret) >= 32
        assert result.path is not None
        assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(result.path.parent.stat().st_mode) == 0o700

    def test_a_second_start_reuses_the_same_secret_silently(self, tmp_path: Path) -> None:
        first = g.load_or_create_install_secret(tmp_path)
        second = g.load_or_create_install_secret(tmp_path)
        assert second.secret == first.secret
        assert second.created is False
        assert second.code is None

    def test_two_calls_never_produce_the_same_secret_in_two_state_dirs(
        self, tmp_path: Path
    ) -> None:
        a = g.load_or_create_install_secret(tmp_path / "a")
        b = g.load_or_create_install_secret(tmp_path / "b")
        assert a.secret != b.secret

    def test_a_world_readable_secret_file_is_tightened_and_recorded(self, tmp_path: Path) -> None:
        path = tmp_path / g.INSTALL_SECRET_FILENAME
        path.write_text("an-existing-secret\n", encoding="utf-8")
        os.chmod(path, 0o644)
        result = g.load_or_create_install_secret(tmp_path)
        assert result.secret == "an-existing-secret"
        assert result.code == g.SECRET_TIGHTENED_CODE
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_an_empty_secret_file_is_replaced_rather_than_used(self, tmp_path: Path) -> None:
        path = tmp_path / g.INSTALL_SECRET_FILENAME
        path.write_text("   \n", encoding="utf-8")
        result = g.load_or_create_install_secret(tmp_path)
        assert result.secret.strip() == result.secret
        assert len(result.secret) >= 32
        assert result.code == g.SECRET_CREATED_CODE

    def test_an_oversized_secret_file_is_read_bounded(self, tmp_path: Path) -> None:
        path = tmp_path / g.INSTALL_SECRET_FILENAME
        path.write_text("x" * 100_000, encoding="utf-8")
        result = g.load_or_create_install_secret(tmp_path)
        assert len(result.secret) <= g.MAX_SECRET_BYTES

    def test_an_unwritable_state_dir_degrades_to_an_in_memory_secret(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("a file where a directory was asked for", encoding="utf-8")
        result = g.load_or_create_install_secret(blocker / "state")
        assert result.persisted is False
        assert result.code == g.SECRET_UNPERSISTED_CODE
        assert len(result.secret) >= 32

    def test_no_state_dir_at_all_still_yields_a_working_secret(self) -> None:
        result = g.load_or_create_install_secret(None)
        assert result.persisted is False
        assert result.code == g.SECRET_UNPERSISTED_CODE
        assert len(result.secret) >= 32

    def test_the_detail_never_contains_the_secret(self, tmp_path: Path) -> None:
        result = g.load_or_create_install_secret(tmp_path)
        assert result.secret not in result.detail
        assert result.secret not in str(result.to_dict())

    def test_the_status_dict_never_contains_the_secret(self, tmp_path: Path) -> None:
        result = g.load_or_create_install_secret(tmp_path)
        rendered = repr(result.to_dict())
        assert result.secret not in rendered
        assert "path" in result.to_dict()

    def test_a_symlinked_secret_file_is_refused_rather_than_followed(self, tmp_path: Path) -> None:
        target = tmp_path / "elsewhere.txt"
        target.write_text("planted-secret", encoding="utf-8")
        state = tmp_path / "state"
        state.mkdir(mode=0o700)
        (state / g.INSTALL_SECRET_FILENAME).symlink_to(target)
        result = g.load_or_create_install_secret(state)
        assert result.secret != "planted-secret"
        assert result.code == g.SECRET_UNREADABLE_CODE
