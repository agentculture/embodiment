"""The Cloudflare Access verifier: synthetic RSA keys, a fake JWKS, no network.

Task ``t3`` of the ``gwen-remembers-recordings`` plan. Modelled on pocketsync's
``tests/test_web_access.py`` (cite-don't-import, like the module under test),
with what embodiment adds on top: the operator's email allow-list, the refusal
of service tokens, a result the guard's ``AssertionVerifier`` seam takes, a
``verify`` that never raises, and a JWKS fault that is recorded (C3).

Every key here is generated at test time from a fixed seed by a small
pure-Python keygen — the dev environment carries no ``cryptography``, and this
package takes no dependency for a test. Every identity is synthetic: the repo
is public, and nothing here comes from Gwen's real store.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import subprocess  # nosec B404 - fixed argv, no shell, test-only interpreter probe
import sys
from typing import Any

import pytest

from embodiment.http import access as a
from embodiment.http import guard as g

TEAM = "example-team.cloudflareaccess.com"
AUD = "synthetic-aud-tag-0123456789"
OPERATOR = "operator@example.com"
NOW = 1_800_000_000
_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


# ── synthetic test RSA keys: generated at test time, never committed ────────


def _is_prime(n: int, rng: random.Random) -> bool:
    if n < 4:
        return n in (2, 3)
    if n % 2 == 0:
        return False
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(20):
        x = pow(rng.randrange(2, n - 1), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, rng: random.Random) -> int:
    while True:
        cand = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_prime(cand, rng) and (cand - 1) % 65537:
            return cand


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int(value: int) -> str:
    return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


class Keypair:
    """A 1024-bit RSA key from a fixed seed (insecure on purpose: test material only)."""

    def __init__(self, kid: str, seed: int) -> None:
        rng = random.Random(seed)  # nosec B311 - synthetic test key
        p, q = _prime(512, rng), _prime(512, rng)
        self.kid, self.n, self.e = kid, p * q, 65537
        self.d = pow(self.e, -1, (p - 1) * (q - 1))

    def jwk(self) -> dict[str, str]:
        return {
            "kid": self.kid,
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "n": _int(self.n),
            "e": _int(self.e),
        }

    def sign(self, data: bytes) -> bytes:
        k = (self.n.bit_length() + 7) // 8
        t = _DIGEST_INFO + hashlib.sha256(data).digest()
        em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
        return pow(int.from_bytes(em, "big"), self.d, self.n).to_bytes(k, "big")


_KEYS: dict[str, Keypair] = {}


def keypair(kid: str = "kid-1", seed: int | None = None) -> Keypair:
    key = f"{kid}/{seed}"
    if key not in _KEYS:
        _KEYS[key] = Keypair(kid, seed if seed is not None else sum(map(ord, kid)))
    return _KEYS[key]


def jwks(*pairs: Keypair) -> dict[str, Any]:
    return {"keys": [p.jwk() for p in (pairs or (keypair(),))]}


def claims(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "aud": [AUD],
        "iss": f"https://{TEAM}",
        "sub": "user-sub-1",
        "email": OPERATOR,
        "iat": NOW - 10,
        "nbf": NOW - 10,
        "exp": NOW + 600,
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not None}


def token(body: Any = None, *, pair: Keypair | None = None, header: Any = None) -> str:
    pair = pair or keypair()
    head = header if header is not None else {"alg": "RS256", "kid": pair.kid, "typ": "JWT"}
    payload = body if body is not None else claims()
    signing = f"{b64(json.dumps(head).encode())}.{b64(json.dumps(payload).encode())}"
    return f"{signing}.{b64(pair.sign(signing.encode()))}"


def verifier(
    fetches: list[int] | None = None,
    keyset: Any = None,
    emails: frozenset[str] = frozenset({OPERATOR}),
    **kw: Any,
) -> a.AccessVerifier:
    def fetch() -> Any:
        if fetches is not None:
            fetches.append(1)
        return keyset if keyset is not None else jwks()

    return a.AccessVerifier(TEAM, AUD, emails, fetch_jwks=fetch, clock=lambda: float(NOW), **kw)


def refused(v: a.AccessVerifier, tok: Any) -> a.AccessResult:
    result = v.verify(tok)
    assert isinstance(result, a.AccessResult)
    assert result.valid is False
    assert result.email == ""
    return result


def code(v: a.AccessVerifier, tok: Any) -> str:
    return refused(v, tok).code


# ── the result fits the guard's seam ─────────────────────────────────────────


def test_valid_operator_token_is_admitted_with_its_email_and_expiry():
    result = verifier().verify(token())
    assert isinstance(result, g.AssertionResult)  # the guard's isinstance check passes
    assert result.valid is True
    assert result.code == ""
    assert result.unavailable is False
    assert result.email == OPERATOR
    assert result.expires_at == float(NOW + 600)


def test_the_verifier_is_an_assertion_verifier_the_guard_admits():
    guard = g.Guard(
        g.GuardConfig(install_secret="s" * 32, public_hostname="gwen.example.com"),
        assertion_verifier=verifier(),
    )
    base = {"Host": "gwen.example.com", "Authorization": "Bearer " + "s" * 32}
    ok = guard.check("GET", "/api/status", {**base, "Cf-Access-Jwt-Assertion": token()})
    assert ok.allowed
    forged = token(pair=keypair("kid-1", seed=999))
    bad = guard.check("GET", "/api/status", {**base, "Cf-Access-Jwt-Assertion": forged})
    assert not bad.allowed
    assert bad.code == a.BAD_SIGNATURE_CODE


def test_audience_may_be_a_single_string():
    assert verifier().verify(token(claims(aud=AUD))).valid


# ── each failure has its own safe reason ─────────────────────────────────────


def test_forged_signature_is_refused():
    forger = keypair("kid-1", seed=999)  # same kid, different key
    assert forger.n != keypair().n
    assert code(verifier(), token(pair=forger)) == a.BAD_SIGNATURE_CODE


def test_tampered_payload_is_refused():
    head, _, sig = token().split(".")
    evil = b64(json.dumps(claims(email="intruder@example.com")).encode())
    assert code(verifier(), f"{head}.{evil}.{sig}") == a.BAD_SIGNATURE_CODE


def test_expired_token_is_refused():
    assert code(verifier(), token(claims(exp=NOW - 1))) == a.EXPIRED_CODE
    assert code(verifier(), token(claims(exp=NOW))) == a.EXPIRED_CODE


def test_not_yet_valid_token_is_refused():
    assert code(verifier(), token(claims(nbf=NOW + 60))) == a.NOT_YET_VALID_CODE


def test_wrong_audience_is_refused():
    assert code(verifier(), token(claims(aud=["someone-else"]))) == a.BAD_AUDIENCE_CODE


def test_wrong_issuer_is_refused():
    assert code(verifier(), token(claims(iss="https://evil.example.com"))) == a.BAD_ISSUER_CODE


def test_unknown_kid_is_refused():
    assert code(verifier(), token(pair=keypair("kid-2"))) == a.UNKNOWN_KID_CODE


@pytest.mark.parametrize(
    "header",
    [{"alg": "none", "kid": "kid-1"}, {"alg": "HS256", "kid": "kid-1"}, {"alg": "RS256"}],
)
def test_algorithm_confusion_and_missing_kid_are_malformed(header):
    assert code(verifier(), token(header=header)) == a.MALFORMED_CODE


@pytest.mark.parametrize("bad", ["", "a.b", "a.b.c.d", "!!.!!.!!", "x.y.z", None, 42, b"a.b.c"])
def test_malformed_tokens_are_refused(bad):
    assert code(verifier(), bad) == a.MALFORMED_CODE


def test_missing_exp_is_malformed():
    assert code(verifier(), token(claims(exp=None))) == a.MALFORMED_CODE


def test_every_refusal_code_is_distinct_and_has_a_fixed_reason():
    codes = [
        a.MALFORMED_CODE,
        a.UNKNOWN_KID_CODE,
        a.BAD_SIGNATURE_CODE,
        a.BAD_ISSUER_CODE,
        a.BAD_AUDIENCE_CODE,
        a.EXPIRED_CODE,
        a.NOT_YET_VALID_CODE,
        a.SERVICE_TOKEN_CODE,
        a.EMAIL_NOT_ALLOWED_CODE,
        a.UNAVAILABLE_CODE,
        a.CONFIG_INCOMPLETE_CODE,
        a.VERIFIER_ERROR_CODE,
    ]
    assert len(set(codes)) == len(codes)
    assert all(c.startswith("http-access-") for c in codes)
    assert set(a.REASONS) == set(codes)
    assert len(set(a.REASONS.values())) == len(codes)


def test_reason_never_carries_token_material_or_the_email():
    tok = token(claims(email="intruder@example.com"))
    result = refused(verifier(), tok)
    for part in tok.split("."):
        assert part not in result.reason
    assert "intruder" not in result.reason
    assert "intruder" not in repr(result)
    assert result.reason == a.REASONS[result.code]


# ── the allow-list: only the operator's sso identity is admitted ─────────────


def test_another_email_is_refused_even_with_a_valid_signature():
    assert code(verifier(), token(claims(email="intruder@example.com"))) == (
        a.EMAIL_NOT_ALLOWED_CODE
    )


def test_service_token_is_refused_even_with_a_valid_signature():
    service = claims(email=None, common_name="ci.abc.access", sub="")
    assert code(verifier(), token(service)) == a.SERVICE_TOKEN_CODE


def test_service_token_naming_an_allowed_email_is_still_refused():
    # A common_name marks a service token; an email beside it does not make it a person.
    both = claims(common_name="ci.abc.access")
    assert code(verifier(), token(both)) == a.SERVICE_TOKEN_CODE


def test_allow_list_matches_email_case_insensitively():
    v = verifier(emails=frozenset({"Operator@Example.COM"}))
    assert v.verify(token(claims(email="OPERATOR@example.com"))).valid


def test_sso_identity_needs_a_subject():
    assert code(verifier(), token(claims(sub=None))) == a.MALFORMED_CODE


def test_an_empty_allow_list_is_refused_at_construction():
    empty, blank = frozenset(), frozenset({" ", ""})
    with pytest.raises(a.AccessConfigError):
        verifier(emails=empty)
    with pytest.raises(a.AccessConfigError):
        verifier(emails=blank)


# ── JWKS cache: refetch window, rotation, unreachable ────────────────────────


def test_unknown_kid_refetches_once_per_window_then_refuses():
    fetches: list[int] = []
    v = verifier(fetches)
    assert v.verify(token()).valid
    assert len(fetches) == 1
    other = keypair("kid-2")
    assert code(v, token(pair=other)) == a.UNKNOWN_KID_CODE
    assert code(v, token(pair=other)) == a.UNKNOWN_KID_CODE
    assert len(fetches) == 2  # one forced refetch inside the window, not one per request


def test_unknown_kid_refetches_again_after_window():
    now = {"t": float(NOW)}
    fetches: list[int] = []

    def fetch() -> Any:
        fetches.append(1)
        return jwks()

    v = a.AccessVerifier(
        TEAM, AUD, {OPERATOR}, fetch_jwks=fetch, clock=lambda: now["t"], refetch_window=60
    )
    assert v.verify(token()).valid
    other = keypair("kid-2")
    assert code(v, token(pair=other)) == a.UNKNOWN_KID_CODE
    now["t"] += 61
    assert code(v, token(pair=other)) == a.UNKNOWN_KID_CODE
    assert len(fetches) == 3


def test_key_rotation_is_picked_up_on_kid_miss():
    box = {"value": jwks()}
    v = a.AccessVerifier(
        TEAM, AUD, {OPERATOR}, fetch_jwks=lambda: box["value"], clock=lambda: float(NOW)
    )
    assert v.verify(token()).valid
    box["value"] = jwks(keypair("kid-2"))
    assert v.verify(token(pair=keypair("kid-2"))).valid


def test_jwks_unreachable_is_unavailable_distinct_from_a_refusal_and_negative_cached():
    calls: list[int] = []
    recorded: list[tuple[str, str]] = []

    def boom() -> Any:
        calls.append(1)
        raise OSError("network down MARKER-SECRET-HOSTNAME")

    v = a.AccessVerifier(
        TEAM,
        AUD,
        {OPERATOR},
        fetch_jwks=boom,
        clock=lambda: float(NOW),
        on_degrade=lambda c, r: recorded.append((c, r)),
    )
    first = refused(v, token())
    assert (first.code, first.unavailable) == (a.UNAVAILABLE_CODE, True)
    assert refused(v, token()).unavailable is True
    assert len(calls) == 1  # negative cache: no fetch per request
    # Recorded once, per failed fetch, and the exception's message never rides along.
    assert [c for c, _ in recorded] == [a.UNAVAILABLE_CODE]
    assert "MARKER" not in recorded[0][1]
    assert "OSError" in recorded[0][1]
    assert v.jwks_failures == 1
    # An ordinary refusal is NOT unavailable: the guard can tell 503 from 401.
    assert refused(verifier(), token(claims(exp=NOW - 1))).unavailable is False


def test_jwks_with_no_usable_key_is_recorded_as_unavailable():
    recorded: list[str] = []
    v = verifier(keyset={"keys": []}, on_degrade=lambda c, r: recorded.append(c))
    assert refused(v, token()).unavailable is True
    assert recorded == [a.UNAVAILABLE_CODE]


def test_jwks_recovers_after_window():
    now = {"t": float(NOW)}
    up = {"ok": False}

    def fetch() -> Any:
        if not up["ok"]:
            raise OSError("down")
        return jwks()

    v = a.AccessVerifier(
        TEAM, AUD, {OPERATOR}, fetch_jwks=fetch, clock=lambda: now["t"], refetch_window=60
    )
    assert code(v, token()) == a.UNAVAILABLE_CODE
    up["ok"] = True
    now["t"] += 61
    assert v.verify(token()).valid


def test_a_broken_degradation_sink_is_counted_not_raised():
    def sink(c: str, r: str) -> None:
        raise RuntimeError("sink down")

    v = verifier(keyset={"keys": []}, on_degrade=sink)
    assert refused(v, token()).code == a.UNAVAILABLE_CODE
    assert v.hook_errors == 1


def test_jwks_entries_that_are_not_rs256_signing_keys_are_ignored():
    jwk = keypair().jwk()
    keyset = {"keys": [{**jwk, "use": "enc"}, {**jwk, "kty": "EC"}, {**jwk, "alg": "RS512"}]}
    assert code(verifier(keyset=keyset), token()) in (a.UNKNOWN_KID_CODE, a.UNAVAILABLE_CODE)


def test_team_domain_scheme_is_dropped_and_jwks_is_https():
    v = a.AccessVerifier(f"HTTP://{TEAM}/", AUD, {OPERATOR}, fetch_jwks=jwks)
    assert v.team_domain == TEAM
    assert v.jwks_url == f"https://{TEAM}/cdn-cgi/access/certs"


def test_verify_rs256_roundtrip():
    pair = keypair()
    sig = pair.sign(b"hello")
    assert a.verify_rs256(pair.n, pair.e, b"hello", sig)
    assert not a.verify_rs256(pair.n, pair.e, b"hellO", sig)
    assert not a.verify_rs256(pair.n, pair.e, b"hello", sig[:-1])
    assert not a.verify_rs256(pair.n, pair.e, b"hello", b"\x00" * len(sig))


# ── verify never raises ──────────────────────────────────────────────────────


def test_an_unexpected_fault_inside_verify_is_a_recorded_refusal(monkeypatch):
    recorded: list[str] = []
    v = verifier(on_degrade=lambda c, r: recorded.append(c))

    def explode(*args: Any) -> bool:
        raise ArithmeticError("boom")

    monkeypatch.setattr(a, "verify_rs256", explode)
    result = refused(v, token())
    assert result.code == a.VERIFIER_ERROR_CODE
    assert result.unavailable is False
    assert recorded == [a.VERIFIER_ERROR_CODE]


def test_a_clock_that_raises_is_a_refusal_not_an_exception():
    def clock() -> float:
        raise RuntimeError("no clock")

    v = a.AccessVerifier(TEAM, AUD, {OPERATOR}, fetch_jwks=jwks, clock=clock)
    assert refused(v, token()).code == a.VERIFIER_ERROR_CODE


# ── all-or-nothing configuration ─────────────────────────────────────────────

ENV = {
    "EMBODIMENT_ACCESS_TEAM_DOMAIN": TEAM,
    "EMBODIMENT_ACCESS_AUD": AUD,
    "EMBODIMENT_ACCESS_EMAILS": f"{OPERATOR}, second@example.com",
}


def test_env_names_are_the_embodiment_pair_plus_emails():
    assert a.ACCESS_ENV == (
        "EMBODIMENT_ACCESS_TEAM_DOMAIN",
        "EMBODIMENT_ACCESS_AUD",
        "EMBODIMENT_ACCESS_EMAILS",
    )


def test_config_all_set():
    cfg = a.AccessConfig.from_env(ENV)
    assert cfg is not None
    assert (cfg.team_domain, cfg.audience) == (TEAM, AUD)
    assert cfg.emails == frozenset({OPERATOR, "second@example.com"})


def test_config_all_unset_is_off():
    assert a.AccessConfig.from_env({}) is None
    assert a.AccessConfig.from_env({k: " " for k in ENV}) is None


@pytest.mark.parametrize("missing", sorted(ENV))
def test_config_partial_is_refused(missing):
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(a.AccessConfigError) as ei:
        a.AccessConfig.from_env(env)
    assert "together" in str(ei.value)


def test_config_emails_of_only_separators_is_partial():
    with pytest.raises(a.AccessConfigError):
        a.AccessConfig.from_env({**ENV, "EMBODIMENT_ACCESS_EMAILS": " , ,"})


def test_verifier_requires_domain_and_audience():
    with pytest.raises(a.AccessConfigError):
        a.AccessVerifier("", AUD, {OPERATOR})
    with pytest.raises(a.AccessConfigError):
        a.AccessVerifier(TEAM, "", {OPERATOR})


def test_verifier_from_env_unset_is_none_so_the_guard_keeps_its_default():
    recorded: list[str] = []
    assert a.verifier_from_env({}, on_degrade=lambda c, r: recorded.append(c)) is None
    assert recorded == []


def test_verifier_from_env_complete_verifies():
    v = a.verifier_from_env(ENV, fetch_jwks=jwks, clock=lambda: float(NOW))
    assert isinstance(v, a.AccessVerifier)
    assert v.verify(token()).valid
    assert v.verify(token(claims(email="second@example.com"))).valid


@pytest.mark.parametrize("missing", sorted(ENV))
def test_verifier_from_env_partial_degrades_to_a_recorded_refusal(missing):
    recorded: list[tuple[str, str]] = []
    env = {k: v for k, v in ENV.items() if k != missing}
    v = a.verifier_from_env(env, on_degrade=lambda c, r: recorded.append((c, r)))
    assert v is not None
    result = v(token())
    assert isinstance(result, g.AssertionResult)
    assert (result.valid, result.code) == (False, a.CONFIG_INCOMPLETE_CODE)
    assert [c for c, _ in recorded] == [a.CONFIG_INCOMPLETE_CODE]
    assert missing in recorded[0][1]  # names the variable, never a value
    assert AUD not in recorded[0][1]
    assert TEAM not in recorded[0][1]


def test_access_module_is_stdlib_only():
    code_ = (
        "import sys\n"
        "for m in ('cryptography', 'jwt', 'httpx', 'culture_rules', 'pocketsync'):\n"
        "    sys.modules[m] = None\n"
        "import embodiment.http.access\n"
    )
    subprocess.run([sys.executable, "-c", code_], check=True)  # nosec B603


def test_module_header_cites_its_origins_and_owner():
    doc = a.__doc__ or ""
    for origin in (
        "pocketsync/web/access.py",
        "culture_rules/auth/access.py",
        "internal/auth/verifier.go",
    ):
        assert origin in doc
    assert "owned by embodiment" in doc
