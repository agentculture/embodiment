"""embodiment.http.access — Cloudflare Access JWT verification, standard library only.

Task ``t3`` of the ``gwen-remembers-recordings`` plan: the verifier the guard's
``assertion_verifier`` seam (:data:`embodiment.http.guard.AssertionVerifier`)
has waited for since ``t16`` shipped a default that refuses everything.

Provenance
----------
Cited (cite-don't-import) from agentculture/pocketsync,
``pocketsync/web/access.py``, which is itself cited from agentculture/culture-rules
``culture_rules/auth/access.py``, which mirrors culture-nodes
``internal/auth/verifier.go``. **This copy is owned by embodiment** and may
diverge; there is no dependency on any of them. The RS256 check, the JWKS
cache (kid-keyed, rate-limited refetch, negative cache, single flight) and the
claim checks are pocketsync's, near verbatim. What embodiment changed:

* ``EMBODIMENT_ACCESS_*`` settings, and an **operator email allow-list**
  (``EMBODIMENT_ACCESS_EMAILS``) as the third member of the all-or-nothing set.
* **Identity is enforced here, not discarded.** pocketsync's server calls
  ``verify()`` and drops the identity, so any token valid for the AUD — a
  service token included — is admitted and the Access app's policy is the only
  identity check. Here only an ``sso`` identity whose email is on the
  allow-list is admitted; a service token, or any other person, is refused
  even when signature, ``iss`` and ``aud`` are all valid. Defence in depth: a
  loosened Access policy at the edge does not loosen the daemon.
* :meth:`AccessVerifier.verify` **never raises.** It returns an
  :class:`AccessResult` — a subclass of the guard's own
  :class:`~embodiment.http.guard.AssertionResult`, so the guard's ``isinstance``
  check takes it unchanged — whose ``code`` names the rule that refused.
* An unreachable JWKS is ``unavailable=True``: an infrastructure fault, not a
  bad token, so the guard can answer 503 rather than 401. Every such fault is
  **recorded** through ``on_degrade`` (C3) — once per failed fetch, not once
  per request, because the negative cache means there is no fetch per request.
* A partial configuration does not raise out of :func:`verifier_from_env`: it
  degrades to a verifier that refuses everything with
  :data:`CONFIG_INCOMPLETE_CODE`, and records that it did.

What is checked
---------------
The token must be a three-part RS256 JWT whose ``kid`` names a key in the team
JWKS (``https://<team>/cdn-cgi/access/certs``), whose signature verifies
(RSASSA-PKCS1-v1_5 / SHA-256, implemented here with ``pow`` — the standard
library has no RSA, but RSA *verification* is one modular exponentiation and a
byte comparison), whose ``iss`` is ``https://<team>``, whose ``aud`` contains
the application's AUD tag, which is inside ``nbf`` .. ``exp``, and which names
an allow-listed person. Each failure has its own code and a fixed reason
(:data:`REASONS`) that never carries token material, the email, or anything
else the presenter chose.

Configuration is all-or-nothing: ``EMBODIMENT_ACCESS_TEAM_DOMAIN``,
``EMBODIMENT_ACCESS_AUD`` and ``EMBODIMENT_ACCESS_EMAILS`` are set together
(Access on) or all unset (Access off, :meth:`AccessConfig.from_env` answers
``None`` and the guard keeps its refusing default, byte-identical to before).
An allow-list without the pair, or the pair without an allow-list, is partial.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Optional

from embodiment.http.guard import AssertionResult, AssertionVerifier
from embodiment.safe_reason import describe_exception

__all__ = [
    "ACCESS_ENV",
    "ENV_TEAM_DOMAIN",
    "ENV_AUD",
    "ENV_EMAILS",
    "MALFORMED_CODE",
    "UNKNOWN_KID_CODE",
    "BAD_SIGNATURE_CODE",
    "BAD_ISSUER_CODE",
    "BAD_AUDIENCE_CODE",
    "EXPIRED_CODE",
    "NOT_YET_VALID_CODE",
    "SERVICE_TOKEN_CODE",
    "EMAIL_NOT_ALLOWED_CODE",
    "UNAVAILABLE_CODE",
    "CONFIG_INCOMPLETE_CODE",
    "VERIFIER_ERROR_CODE",
    "REASONS",
    "AccessConfig",
    "AccessConfigError",
    "AccessResult",
    "AccessVerifier",
    "VerificationError",
    "verifier_from_env",
    "verify_rs256",
]

ENV_TEAM_DOMAIN = "EMBODIMENT_ACCESS_TEAM_DOMAIN"
ENV_AUD = "EMBODIMENT_ACCESS_AUD"
ENV_EMAILS = "EMBODIMENT_ACCESS_EMAILS"
ACCESS_ENV = (ENV_TEAM_DOMAIN, ENV_AUD, ENV_EMAILS)

# ── refusal codes: one per rule, each with a fixed reason ────────────────────

MALFORMED_CODE = "http-access-malformed"
UNKNOWN_KID_CODE = "http-access-unknown-kid"
BAD_SIGNATURE_CODE = "http-access-bad-signature"
BAD_ISSUER_CODE = "http-access-bad-issuer"
BAD_AUDIENCE_CODE = "http-access-bad-audience"
EXPIRED_CODE = "http-access-expired"
NOT_YET_VALID_CODE = "http-access-not-yet-valid"
#: A valid assertion for a service token. Gwen's public surface is for a person.
SERVICE_TOKEN_CODE = "http-access-service-token"  # nosec B105 - a code, not a secret
#: A valid assertion for a person who is not on the operator's allow-list.
EMAIL_NOT_ALLOWED_CODE = "http-access-email-not-allowed"
#: The JWKS could not be fetched (or held no usable key) and the token's ``kid``
#: is not cached. Infrastructure, not a bad token: ``unavailable=True``. Recorded.
UNAVAILABLE_CODE = "http-access-unavailable"
#: Access is partially configured, so every assertion is refused. Recorded.
CONFIG_INCOMPLETE_CODE = "http-access-config-incomplete"
#: Something inside the verifier failed unexpectedly. Fail closed. Recorded.
VERIFIER_ERROR_CODE = "http-access-verifier-error"

#: ``code -> reason``. Module literals only: a reason is safe to log because
#: nothing in it came from the request.
REASONS: dict[str, str] = {
    MALFORMED_CODE: "the Access assertion is not a well-formed RS256 JWT",
    UNKNOWN_KID_CODE: "the Access assertion names a signing key the team JWKS does not hold",
    BAD_SIGNATURE_CODE: "the Access assertion's signature does not verify",
    BAD_ISSUER_CODE: "the Access assertion was issued by another team",
    BAD_AUDIENCE_CODE: "the Access assertion is for another application",
    EXPIRED_CODE: "the Access assertion has expired",
    NOT_YET_VALID_CODE: "the Access assertion is not valid yet",
    SERVICE_TOKEN_CODE: "the Access assertion is for a service token, not a person",
    EMAIL_NOT_ALLOWED_CODE: "the Access assertion names a person not on the allow-list",
    UNAVAILABLE_CODE: "the Access signing keys could not be fetched, so nothing can be verified",
    CONFIG_INCOMPLETE_CODE: (
        "Cloudflare Access is partially configured, so every assertion is refused"
    ),
    VERIFIER_ERROR_CODE: "the Access verifier failed unexpectedly and refused",
}

#: Hard cap on the JWKS body read. pocketsync's figure, kept.
_JWKS_LIMIT = 1 << 20
#: Hard cap on a token. A Cloudflare assertion is ~1 KiB; a much longer string
#: is refused unparsed rather than base64-decoded.
_MAX_TOKEN_CHARS = 16384
#: The JWKS fetch's own timeout, and so how long a caller waits for another
#: caller's in-flight fetch — derived from the quantity it bounds.
_FETCH_TIMEOUT_S = 10.0
#: Schemes a pasted team-domain URL may carry; dropped, since the JWKS fetch is https-only.
_WEB_SCHEMES = frozenset({"https", "http"})
#: DER prefix of DigestInfo for SHA-256 (RFC 8017 section 9.2, note 1).
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
#: An allow-list is written comma- and/or whitespace-separated.
_EMAIL_SEPARATORS = re.compile(r"[,\s]+")


class AccessConfigError(ValueError):
    """The Access configuration is partial or malformed. ``missing`` names variables only."""

    def __init__(self, message: str, missing: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.missing = missing


class VerificationError(Exception):
    """Internal: a refused token. ``code`` is one of this module's codes, never token text."""

    def __init__(self, code: str) -> None:
        super().__init__(f"access token verification failed: {code}")
        self.code = code


@dataclass(frozen=True)
class AccessResult(AssertionResult):
    """The guard's :class:`AssertionResult`, plus what an Access verdict knows.

    ``unavailable`` is true only for :data:`UNAVAILABLE_CODE` — the guard's 503
    rather than its 401. ``email`` (lower-cased, empty unless ``valid``) and
    ``expires_at`` (the token's ``exp``, epoch seconds, ``None`` unless
    ``valid``) are for the host, e.g. to close a stream when its token lapses.
    ``email`` is kept out of ``repr`` so a logged result does not log a person.
    """

    unavailable: bool = False
    email: str = field(default="", repr=False)
    expires_at: Optional[float] = None


def _refusal(code: str) -> AccessResult:
    return AccessResult(
        valid=False,
        code=code,
        reason=REASONS[code],
        unavailable=code == UNAVAILABLE_CODE,
    )


def _parse_emails(raw: Any) -> frozenset[str]:
    if isinstance(raw, str):
        items: Iterable[Any] = _EMAIL_SEPARATORS.split(raw)
    else:
        items = raw or ()
    return frozenset(e.strip().lower() for e in items if isinstance(e, str) and e.strip())


@dataclass(frozen=True)
class AccessConfig:
    """The Access settings: the team domain and AUD tag pinned, and who may pass."""

    team_domain: str
    audience: str
    emails: frozenset[str]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Optional[AccessConfig]:
        """``None`` when all three are unset (off); :class:`AccessConfigError` when partial."""
        domain = (env.get(ENV_TEAM_DOMAIN) or "").strip()
        audience = (env.get(ENV_AUD) or "").strip()
        emails = _parse_emails(env.get(ENV_EMAILS) or "")
        present = {ENV_TEAM_DOMAIN: bool(domain), ENV_AUD: bool(audience), ENV_EMAILS: bool(emails)}
        if not any(present.values()):
            return None
        missing = tuple(name for name, set_ in present.items() if not set_)
        if missing:
            raise AccessConfigError(
                f"incomplete Access configuration: set {', '.join(ACCESS_ENV)} together, "
                f"or unset all of them (missing: {', '.join(missing)})",
                missing,
            )
        return cls(domain, audience, emails)


def _b64(segment: str) -> bytes:
    if not segment or not all(c.isalnum() or c in "-_" for c in segment):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _b64_int(segment: str) -> int:
    return int.from_bytes(_b64(segment), "big")


def verify_rs256(n: int, e: int, message: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 verification with SHA-256 (RFC 8017 8.2.2), standard library only."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    pad = k - len(t) - 3
    if pad < 8:
        return False
    expected = b"\x00\x01" + b"\xff" * pad + b"\x00" + t
    return hmac.compare_digest(em, expected)


def _parse_keys(document: Any) -> dict[str, tuple[int, int]]:
    keys: dict[str, tuple[int, int]] = {}
    entries = document.get("keys") if isinstance(document, Mapping) else None
    for jwk in entries if isinstance(entries, list) else []:
        if not isinstance(jwk, Mapping):
            continue
        kid = jwk.get("kid")
        if not kid or jwk.get("kty") != "RSA" or jwk.get("alg") != "RS256":
            continue
        if jwk.get("use") != "sig":
            continue
        try:
            n, e = _b64_int(str(jwk.get("n", ""))), _b64_int(str(jwk.get("e", "")))
        except ValueError:  # binascii.Error is a ValueError; this one key is skipped
            continue
        if n > 0 and e > 1:
            keys[str(kid)] = (n, e)
    return keys


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


class AccessVerifier:
    """Verify Cloudflare Access RS256 JWTs for one team, one AUD tag and an email allow-list.

    Callable: an instance *is* an :data:`~embodiment.http.guard.AssertionVerifier`.
    ``on_degrade`` is called ``(code, reason)`` when an infrastructure or internal
    fault refused — a JWKS that could not be fetched, a verifier fault — never on
    an ordinary refusal, which is the verifier working rather than degrading (the
    guard's own rule). A sink that raises is counted in ``hook_errors``.
    """

    def __init__(
        self,
        team_domain: str,
        audience: str,
        emails: Iterable[str],
        *,
        fetch_jwks: Optional[Callable[[], Any]] = None,
        clock: Optional[Callable[[], float]] = None,
        refetch_window: float = 60.0,
        on_degrade: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        domain = (team_domain or "").strip().rstrip("/")
        # A pasted URL is accepted for convenience: its web scheme is dropped, and the JWKS is
        # always fetched over https (below), whatever scheme the operator wrote.
        scheme, sep, rest = domain.partition("://")
        if sep and scheme.lower() in _WEB_SCHEMES:
            domain = rest
        audience = (audience or "").strip()
        if not domain or not audience:
            raise AccessConfigError("Access needs both a team domain and an audience")
        allowed = _parse_emails(list(emails or ()))
        if not allowed:
            raise AccessConfigError("Access needs at least one allow-listed email")
        self.team_domain = domain
        self.audience = audience
        self.emails = allowed
        self.jwks_url = f"https://{domain}/cdn-cgi/access/certs"
        self._fetch = fetch_jwks or self._fetch_over_https
        self._clock = clock or time.time
        self._window = refetch_window
        self._on_degrade = on_degrade
        self._keys: dict[str, tuple[int, int]] = {}
        self._last_forced: Optional[float] = None
        self._failed_at: Optional[float] = None
        self._load_failed = False
        self._inflight: Optional[threading.Event] = None
        self._lock = threading.Lock()
        #: Failed JWKS loads, counted so a host can see the fault without a log.
        self.jwks_failures = 0
        #: ``on_degrade`` calls that raised.
        self.hook_errors = 0

    def __call__(self, token: str) -> AccessResult:
        return self.verify(token)

    def _degrade(self, code: str, reason: str) -> None:
        if self._on_degrade is None:
            return
        try:
            self._on_degrade(code, reason)
        except Exception as exc:  # noqa: BLE001  # a broken sink is counted, not raised
            del exc
            self.hook_errors += 1

    def _fetch_over_https(self) -> Any:
        request = urllib.request.Request(self.jwks_url, headers={"Accept": "application/json"})
        # the URL is always https://<configured team domain>/... (built in __init__)
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_S) as resp:  # nosec B310
            return json.loads(resp.read(_JWKS_LIMIT))

    def _load(self) -> dict[str, tuple[int, int]]:
        """Fetch and parse the JWKS; any failure means "no usable keys", and is recorded."""
        try:
            keys = _parse_keys(self._fetch())
        except Exception as exc:  # noqa: BLE001  # a JWKS fault is a recorded unavailable
            self._load_failed = True
            self.jwks_failures += 1
            self._degrade(
                UNAVAILABLE_CODE,
                f"the Access JWKS could not be fetched: {describe_exception(exc)}",
            )
            return {}
        self._load_failed = not keys
        if not keys:
            self.jwks_failures += 1
            self._degrade(UNAVAILABLE_CODE, "the Access JWKS held no usable RS256 signing key")
        return keys

    def _fetch_due(self, now: float) -> bool:
        """Whether a miss may trigger a JWKS fetch now (caller holds the lock).

        An empty cache loads at once unless a load failed within the window (negative
        cache); a populated cache refetches for an unknown kid at most once per window.
        """
        if not self._keys:
            return self._failed_at is None or now - self._failed_at >= self._window
        if self._last_forced is None or now - self._last_forced >= self._window:
            self._last_forced = now
            return True
        return False

    def _key(self, kid: str) -> tuple[int, int]:
        with self._lock:
            if kid in self._keys:
                return self._keys[kid]
            inflight = self._inflight
            leader = inflight is None and self._fetch_due(self._clock())
            if leader:
                inflight = self._inflight = threading.Event()
        if leader and inflight is not None:
            keys: dict[str, tuple[int, int]] = {}
            try:
                keys = self._load()  # network I/O outside the lock
            finally:
                with self._lock:
                    # Release the waiters first: a clock that fails below must not
                    # leave every later caller waiting on a fetch that has ended.
                    self._inflight = None
                    inflight.set()
                    if keys:
                        self._keys = keys
                        self._failed_at = None
                    elif not self._keys:
                        self._failed_at = self._clock()
        elif inflight is not None:
            inflight.wait(_FETCH_TIMEOUT_S)  # single flight: share the leader's fetch
        with self._lock:
            if kid in self._keys:
                return self._keys[kid]
            unavailable = self._load_failed
        raise VerificationError(UNAVAILABLE_CODE if unavailable else UNKNOWN_KID_CODE)

    def verify(self, token: Any) -> AccessResult:
        """The verdict for one assertion. Never raises, whatever it is handed."""
        try:
            email, expires_at = self._verify(token)
        except VerificationError as refusal:
            return _refusal(refusal.code)
        except Exception as exc:  # noqa: BLE001  # a verifier that dies must not open the door
            self._degrade(
                VERIFIER_ERROR_CODE,
                f"the Access verifier failed: {describe_exception(exc)}",
            )
            return _refusal(VERIFIER_ERROR_CODE)
        return AccessResult(valid=True, email=email, expires_at=expires_at)

    def _verify(self, token: Any) -> tuple[str, float]:
        if not isinstance(token, str) or len(token) > _MAX_TOKEN_CHARS:
            raise VerificationError(MALFORMED_CODE)
        parts = token.split(".")
        if len(parts) != 3 or not all(parts):
            raise VerificationError(MALFORMED_CODE)
        try:
            header = json.loads(_b64(parts[0]))
            signature = _b64(parts[2])
        except ValueError:  # binascii.Error and JSONDecodeError are ValueErrors
            raise VerificationError(MALFORMED_CODE) from None
        if not isinstance(header, dict) or header.get("alg") != "RS256":
            raise VerificationError(MALFORMED_CODE)
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise VerificationError(MALFORMED_CODE)
        n, e = self._key(kid)
        if not verify_rs256(n, e, f"{parts[0]}.{parts[1]}".encode(), signature):
            raise VerificationError(BAD_SIGNATURE_CODE)
        try:
            claims = json.loads(_b64(parts[1]))
        except ValueError:
            raise VerificationError(MALFORMED_CODE) from None
        if not isinstance(claims, dict):
            raise VerificationError(MALFORMED_CODE)
        return self._check_claims(claims)

    def _check_claims(self, claims: dict[str, Any]) -> tuple[str, float]:
        if claims.get("iss") != f"https://{self.team_domain}":
            raise VerificationError(BAD_ISSUER_CODE)
        self._check_audience(claims)
        expires_at = self._check_validity(claims)
        return self._check_identity(claims), expires_at

    def _check_audience(self, claims: dict[str, Any]) -> None:
        aud = claims.get("aud")
        audiences = [aud] if isinstance(aud, str) else aud
        if not isinstance(audiences, list) or not audiences:
            raise VerificationError(MALFORMED_CODE)
        if self.audience not in audiences:
            raise VerificationError(BAD_AUDIENCE_CODE)

    def _check_validity(self, claims: dict[str, Any]) -> float:
        exp = _num(claims.get("exp"))
        nbf = _num(claims["nbf"]) if "nbf" in claims else None
        if exp is None or ("nbf" in claims and nbf is None):
            raise VerificationError(MALFORMED_CODE)
        now = self._clock()
        if now >= exp:
            raise VerificationError(EXPIRED_CODE)
        if nbf is not None and now < nbf:
            raise VerificationError(NOT_YET_VALID_CODE)
        return exp

    def _check_identity(self, claims: dict[str, Any]) -> str:
        """The allow-listed email a valid assertion names, or a refusal.

        Stricter than pocketsync's ``_identity_of``: any ``common_name`` marks a
        service token, and a service token is refused even if it also carries an
        allow-listed email. A person needs both ``email`` and ``sub``.
        """
        email = claims.get("email") or ""
        common_name = claims.get("common_name") or ""
        subject = claims.get("sub") or ""
        if not all(isinstance(v, str) for v in (email, common_name, subject)):
            raise VerificationError(MALFORMED_CODE)
        if common_name:
            raise VerificationError(SERVICE_TOKEN_CODE)
        if not (email and subject):
            raise VerificationError(MALFORMED_CODE)
        normalised = email.strip().lower()
        if normalised not in self.emails:
            raise VerificationError(EMAIL_NOT_ALLOWED_CODE)
        return normalised


def _refusing_misconfigured(token: str) -> AccessResult:
    """What a partial configuration degrades to: refuse every assertion, by name."""
    del token  # never read, never logged
    return _refusal(CONFIG_INCOMPLETE_CODE)


def verifier_from_env(
    env: Optional[Mapping[str, str]] = None,
    *,
    on_degrade: Optional[Callable[[str, str], None]] = None,
    fetch_jwks: Optional[Callable[[], Any]] = None,
    clock: Optional[Callable[[], float]] = None,
    refetch_window: float = 60.0,
) -> Optional[AssertionVerifier]:
    """The guard's ``assertion_verifier`` from the environment. Never raises.

    ``None`` when Access is off (all three unset): the guard keeps its own
    refusing default and behaves exactly as before. An :class:`AccessVerifier`
    when fully configured. When partial or malformed, a verifier that refuses
    every assertion with :data:`CONFIG_INCOMPLETE_CODE`, after recording that
    degradation through ``on_degrade`` — the reason names variables, never values.
    """
    source = os.environ if env is None else env
    try:
        config = AccessConfig.from_env(source)
        if config is None:
            return None
        return AccessVerifier(
            config.team_domain,
            config.audience,
            config.emails,
            fetch_jwks=fetch_jwks,
            clock=clock,
            refetch_window=refetch_window,
            on_degrade=on_degrade,
        )
    except AccessConfigError as exc:
        missing = ", ".join(exc.missing) or "a usable team domain and audience"
        reason = f"{REASONS[CONFIG_INCOMPLETE_CODE]} (missing: {missing})"
    except Exception as exc:  # noqa: BLE001  # config parsing must not take the daemon down
        reason = f"{REASONS[CONFIG_INCOMPLETE_CODE]} ({describe_exception(exc)})"
    if on_degrade is not None:
        try:
            on_degrade(CONFIG_INCOMPLETE_CODE, reason)
        except Exception:  # noqa: BLE001  # a broken sink: the refusal stands either way
            return _refusing_misconfigured
    return _refusing_misconfigured
