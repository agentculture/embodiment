"""embodiment.http.guard — who may reach the daemon over HTTP, and on what proof.

Task ``t16`` of the ``realtime-embodiment-app`` plan. This module is pure
policy: it takes the facts of one request — method, path, headers — and returns
a :class:`GuardDecision`. It opens no socket, reads no body, and never raises.
:mod:`embodiment.http.server` is the only caller.

Loopback is not authentication
------------------------------
``CLAUDE.md``'s constraint, restated because it is the whole reason this module
exists: a Cloudflare tunnel delivers a remote request from ``cloudflared``
running on 127.0.0.1. The peer address of a tunnelled request from the far side
of the internet is the same peer address as the operator's own browser. So the
guard never consults the peer address at all. What it checks instead:

1. **A duplicated or malformed guarded header** is refused outright. Two
   ``Host`` headers is request smuggling: one for whatever is in front, one for
   what is behind. A header carrying ``\\r``, ``\\n``, ``\\x00`` or any other
   control character never came from a well-behaved client.
2. **The ``Host``** must be on the allow-list — loopback names plus the
   operator's configured public hostname. This is the DNS-rebinding check: an
   attacker's page resolving ``attacker.example`` to 127.0.0.1 reaches the
   socket but arrives carrying its own name in ``Host``.
3. **The ``Origin``**, when present, must be on the allow-list. A browser sets
   it on every state-changing request and on a cross-origin ``EventSource``, so
   a foreign page cannot ride the operator's cookie.
4. **The install secret**, as ``Authorization: Bearer <secret>`` **or** the
   ``embodiment_secret`` cookie. Both exist because ``EventSource`` — the only
   way a browser consumes an SSE stream — cannot set a request header, so the
   stream must be able to authenticate by cookie. Compared with
   :func:`hmac.compare_digest`.
5. **A cookie credential must be vouched for by the browser itself** — by an
   allow-listed ``Origin``, or, when there is no ``Origin``, by
   ``Sec-Fetch-Site: same-origin``. Anything else is refused
   (:data:`REFUSED_COOKIE_WITHOUT_ORIGIN_CODE`). This is the CSRF rule, and
   round 2 corrected it against a real browser: the first version assumed a
   browser always names its origin on anything carrying a cookie, and Chrome
   sends **no** ``Origin`` on a same-origin ``EventSource`` GET — which is
   precisely the dashboard's own stream. It was refused 403 on every
   reconnect. What Chrome does send on every request is the fetch-metadata
   headers, which page script cannot forge, so the rule now reads the header
   the browser actually sets. ``same-site`` is refused alongside
   ``cross-site`` and ``none``: a sibling subdomain is not this origin. A
   client with neither header vouching for it — a pre-metadata browser, or a
   forged request — is refused and should present the bearer header instead,
   which keeps its own rule (a non-browser client needs no ``Origin``).
6. **A Cloudflare Access assertion**, required when — and only when — the
   request's ``Host`` is the configured public hostname.

What is guarded: every state-changing method, every ``/api/`` route, and
``/api/events``. Static assets are not: the dashboard has to load before it can
present a credential, and it ships no secrets (proved in
``tests/test_http_server.py``).

The Access assertion is an INJECTED seam, and its default REFUSES
---------------------------------------------------------------------
A real check validates the ``Cf-Access-Jwt-Assertion`` JWT's RS256 signature
against the team's JWKS at
``https://<team>.cloudflareaccess.com/cdn-cgi/access/certs``. **The standard
library cannot verify RS256** — ``hashlib``/``hmac`` give no RSA primitive —
and this package takes no new dependency for it (plan task ``t3`` pins the
approved set). So there is no verification here, and this module does not
pretend otherwise: :func:`refusing_assertion_verifier` is the default, it
always refuses, and it records :data:`ACCESS_VERIFIER_MISSING_CODE` so the
absence is a host-visible state rather than a quiet hole. The operator's chosen
verifier is injected through ``assertion_verifier``; a verifier that raises is
itself a recorded refusal (:data:`REFUSED_ACCESS_VERIFIER_FAILED_CODE`) —
fail closed, never fail open, and never on a JWKS fetch that timed out.

Nothing a refusal says can be read back
---------------------------------------
Wave-1 lesson 5. A :class:`GuardDecision`'s ``reason`` is a **fixed string per
code**, chosen from this module's own literals. It never contains the presented
secret, the assertion token, the offending ``Origin`` or ``Host``, or any other
attacker-controlled bytes: an attacker who can make the daemon log a string of
their choosing has a log-injection primitive, and an operator reading
``attacker.example`` back out of their own ledger learns nothing they could not
learn from the code alone. What the host needs is *which rule refused*, and
that is the code.
"""

from __future__ import annotations

import hmac
import os
import secrets
import stat
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, Union

from embodiment.safe_reason import describe_exception

__all__ = [
    "SECRET_COOKIE_NAME",
    "AUTHORIZATION_SCHEME",
    "ACCESS_ASSERTION_HEADER",
    "SEC_FETCH_SITE_HEADER",
    "SAME_ORIGIN_SITE",
    "INSTALL_SECRET_FILENAME",
    "MAX_SECRET_BYTES",
    "MIN_SECRET_CHARS",
    "SECRET_ENTROPY_BYTES",
    "GUARDED_PATHS",
    "GUARDED_PATH_PREFIXES",
    "STATE_CHANGING_METHODS",
    "SENSITIVE_HEADERS",
    "DEFAULT_ALLOWED_HOSTS",
    "REFUSED_HOST_CODE",
    "REFUSED_ORIGIN_CODE",
    "REFUSED_SECRET_CODE",
    "REFUSED_COOKIE_WITHOUT_ORIGIN_CODE",
    "REFUSED_ACCESS_MISSING_CODE",
    "REFUSED_ACCESS_VERIFIER_FAILED_CODE",
    "REFUSED_DUPLICATE_HEADER_CODE",
    "REFUSED_MALFORMED_HEADER_CODE",
    "ACCESS_VERIFIER_MISSING_CODE",
    "SECRET_CREATED_CODE",
    "SECRET_TIGHTENED_CODE",
    "SECRET_UNPERSISTED_CODE",
    "SECRET_UNREADABLE_CODE",
    "AssertionResult",
    "AssertionVerifier",
    "GuardConfig",
    "GuardDecision",
    "Guard",
    "InstallSecret",
    "requires_guard",
    "refusing_assertion_verifier",
    "load_or_create_install_secret",
]

#: The cookie the dashboard's ``EventSource`` authenticates with. A header is
#: preferred everywhere a header is possible; this exists because
#: ``EventSource`` cannot set one.
SECRET_COOKIE_NAME = "embodiment_secret"  # nosec B105 - a cookie NAME, not a secret

#: The ``Authorization`` scheme, matched case-insensitively per RFC 7235.
AUTHORIZATION_SCHEME = "bearer"  # nosec B105 - a scheme name, not a secret

#: The header Cloudflare Access puts its signed JWT in.
ACCESS_ASSERTION_HEADER = "cf-access-jwt-assertion"

#: The fetch-metadata header a browser sets on every request and page script
#: cannot touch. It is what lets a same-origin ``EventSource`` — which sends no
#: ``Origin`` — prove it is same-origin. See the module docstring, rule 5.
SEC_FETCH_SITE_HEADER = "sec-fetch-site"

#: The ONLY ``Sec-Fetch-Site`` value that vouches for a cookie credential,
#: matched exactly (surrounding whitespace aside). ``same-site`` does not: a
#: sibling subdomain is not this origin.
SAME_ORIGIN_SITE = "same-origin"

#: The install secret's filename inside the daemon's state directory.
INSTALL_SECRET_FILENAME = "install-secret"  # nosec B105 - a filename, not a secret

#: Hard cap on how much of the secret file is read. A **judgement call**: the
#: secret this module generates is ~43 characters, so 4 KiB is generous
#: headroom for an operator-supplied one while bounding what a planted
#: multi-gigabyte file at that path can cost.
MAX_SECRET_BYTES = 4096

#: Below this, a secret read off disk is treated as no secret at all.
MIN_SECRET_CHARS = 16

#: Suffix of the lock one daemon takes to repair an unusable secret file, so
#: that concurrent starts converge instead of each replacing the other's work.
#: See :func:`_repair_unusable`.
LOCK_SUFFIX = ".lock"

#: How long a daemon waits for whoever holds that lock. A **judgement call**
#: bounded by what it waits FOR: writing ~43 bytes and renaming them, which is
#: microseconds, so two seconds is four orders of magnitude of slack and still
#: a deadline rather than a hang.
REPAIR_WAIT_S = 2.0

#: Poll interval while waiting. Short because the thing waited on is short.
REPAIR_POLL_S = 0.005

#: How many times the create path re-tries the whole read/publish/repair cycle
#: before giving up on persistence. Two: one ordinary attempt, one after
#: stealing a lock whose holder died.
CREATE_ATTEMPTS = 2

#: Bytes of entropy in a generated secret — 32 bytes, ~43 url-safe characters.
SECRET_ENTROPY_BYTES = 32

#: Exact paths that are guarded whatever the method, on top of everything
#: under :data:`GUARDED_PATH_PREFIXES`. The live stream is ``/api/events`` and
#: is already covered by the ``/api/`` prefix; bare ``/events`` is listed
#: because it was this task's original route, and a stale client asking for it
#: must be refused by the guard rather than falling through to static serving.
GUARDED_PATHS = frozenset({"/events", "/api/events"})

#: Path prefixes that are guarded whatever the method.
GUARDED_PATH_PREFIXES = ("/api/",)

#: Methods that change state, and are therefore guarded on every path.
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Headers whose duplication or malformation is refused outright: each one is
#: an input to a decision below, so two of them is two decisions.
SENSITIVE_HEADERS = (
    "host",
    "origin",
    "authorization",
    "cookie",
    ACCESS_ASSERTION_HEADER,
    SEC_FETCH_SITE_HEADER,
)

#: The hosts a loopback-bound daemon answers to. The operator's public hostname
#: is added by :class:`Guard` when one is configured.
DEFAULT_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "0:0:0:0:0:0:0:1"})

# ── the refusal vocabulary (C3: name the fault the host would look for) ──────

#: The ``Host`` is not on the allow-list — the DNS-rebinding refusal.
REFUSED_HOST_CODE = "http-refused-host"
#: An ``Origin`` header was present and is not on the allow-list.
REFUSED_ORIGIN_CODE = "http-refused-origin"
#: No install secret was presented, or the one presented did not match.
REFUSED_SECRET_CODE = "http-refused-secret"  # nosec B105 - a code, not a secret
#: A cookie credential arrived with neither an allow-listed ``Origin`` nor
#: ``Sec-Fetch-Site: same-origin`` — the CSRF refusal. The code keeps its
#: original name after the round-2 widening: it is what a host greps for, and
#: renaming it would break every ledger entry already written.
REFUSED_COOKIE_WITHOUT_ORIGIN_CODE = "http-refused-cookie-without-origin"
#: The ``Host`` is the public hostname and no Access assertion was presented.
REFUSED_ACCESS_MISSING_CODE = "http-refused-access-missing"
#: The injected verifier raised, or returned something that is not an
#: :class:`AssertionResult`. Fail closed.
REFUSED_ACCESS_VERIFIER_FAILED_CODE = "http-refused-access-verifier-failed"
#: A sensitive header appeared twice with different values (smuggling).
REFUSED_DUPLICATE_HEADER_CODE = "http-refused-duplicate-header"
#: A guarded header carried a control or format character.
REFUSED_MALFORMED_HEADER_CODE = "http-refused-malformed-header"
#: No Access verifier is configured, so a public-hostname request cannot be
#: validated and is refused. The honest name for "this half is not built".
ACCESS_VERIFIER_MISSING_CODE = "http-access-verifier-missing"

#: A fresh install secret was generated and written.
SECRET_CREATED_CODE = "http-install-secret-created"  # nosec B105
#: An existing secret file was more open than 0600 and was tightened.
SECRET_TIGHTENED_CODE = "http-install-secret-tightened"  # nosec B105
#: The secret could not be written; the daemon is running on an in-memory one
#: that dies with the process (every client must re-read it after a restart).
SECRET_UNPERSISTED_CODE = "http-install-secret-unpersisted"  # nosec B105
#: A secret file exists but could not be read (a symlink, wrong owner, an
#: OSError). Never followed, never overwritten — refused into an in-memory one.
SECRET_UNREADABLE_CODE = "http-install-secret-unreadable"  # nosec B105

#: The fixed reason text for each refusal. Fixed, because a reason built from
#: request data is a log-injection primitive (see the module docstring).
_REASONS: dict[str, str] = {
    REFUSED_HOST_CODE: "the Host header is not on the allow-list",
    REFUSED_ORIGIN_CODE: "the Origin header is not on the allow-list",
    REFUSED_SECRET_CODE: "no valid install secret was presented",
    REFUSED_COOKIE_WITHOUT_ORIGIN_CODE: (
        "a cookie credential arrived with neither an allow-listed Origin nor "
        "Sec-Fetch-Site: same-origin; use the Authorization header for a "
        "non-browser client"
    ),
    REFUSED_ACCESS_MISSING_CODE: ("the public hostname requires a Cf-Access-Jwt-Assertion header"),
    REFUSED_ACCESS_VERIFIER_FAILED_CODE: "the Access assertion verifier failed",
    REFUSED_DUPLICATE_HEADER_CODE: "a guarded header was sent more than once",
    REFUSED_MALFORMED_HEADER_CODE: "a guarded header carried a control character",
    ACCESS_VERIFIER_MISSING_CODE: (
        "no Cloudflare Access assertion verifier is configured, so a request "
        "on the public hostname cannot be validated and is refused"
    ),
}

#: Unicode categories no guarded header value may contain. The same set
#: :mod:`embodiment.safe_reason` strips, used here to REFUSE rather than to
#: clean: a ``Host`` with a bidi override in it is not a host.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})

#: Hard cap on a guarded header value. Longer is refused unread. A
#: **judgement call**: a Host, an Origin, a bearer token and a JWT assertion
#: are all well under this, and the one header that legitimately gets long is
#: ``Cookie``, which has its own larger cap below.
_MAX_HEADER_CHARS = 2048

#: ``Cookie`` alone: a browser's jar for one origin can genuinely run to a few
#: KiB, and refusing it as malformed would refuse the operator rather than an
#: attacker. Still bounded, and still control-character-free.
_MAX_COOKIE_CHARS = 16384

HeaderSource = Union[Mapping[str, str], Sequence[tuple[str, str]], Any]


@dataclass(frozen=True)
class AssertionResult:
    """What an injected Access verifier answers. Default: not valid."""

    valid: bool = False
    code: str = ""
    reason: str = ""


#: An operator-supplied Access verifier: token in, verdict out. It must never
#: raise — but if it does, :class:`Guard` refuses and records rather than
#: letting the exception reach the request handler.
AssertionVerifier = Callable[[str], AssertionResult]


def refusing_assertion_verifier(token: str) -> AssertionResult:
    """The default verifier: **always refuses**, and says why.

    See the module docstring. Verifying an RS256 JWT needs an RSA
    implementation the standard library does not provide, and this package
    takes no new dependency for it. A verifier that cannot verify must not
    pass, so this one refuses and names itself as the reason.
    """
    del token  # never read, never logged
    return AssertionResult(
        valid=False,
        code=ACCESS_VERIFIER_MISSING_CODE,
        reason=_REASONS[ACCESS_VERIFIER_MISSING_CODE],
    )


@dataclass(frozen=True)
class GuardConfig:
    """What the guard checks against. All of it is the operator's own config."""

    install_secret: str = ""
    allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_HOSTS
    allowed_origins: frozenset[str] = frozenset()
    public_hostname: Optional[str] = None


@dataclass(frozen=True)
class GuardDecision:
    """One verdict. ``code`` is empty only when the request was allowed."""

    allowed: bool
    guarded: bool
    status: int
    code: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "guarded": self.guarded,
            "status": self.status,
            "code": self.code,
            "reason": self.reason,
        }


_ALLOWED = GuardDecision(allowed=True, guarded=True, status=200, code="", reason="")
_ALLOWED_UNGUARDED = GuardDecision(allowed=True, guarded=False, status=200, code="", reason="")


def requires_guard(method: str, path: str) -> bool:
    """Whether this request must present credentials. Never raises.

    Every state-changing method, plus ``/events`` and everything under
    ``/api/`` whatever the method — the stream carries the transcript, so a
    ``GET`` of it is as sensitive as any write.
    """
    try:
        if str(method).upper() in STATE_CHANGING_METHODS:
            return True
        route = str(path).split("?", 1)[0].split("#", 1)[0]
        if _has_forbidden_character(route):
            # A path carrying a NUL, a newline or a bidi override is not a
            # route anybody meant to request. Guarded, so it is refused by the
            # credential check rather than falling through to static serving.
            return True
        if route in GUARDED_PATHS:
            return True
        return any(route.startswith(prefix) for prefix in GUARDED_PATH_PREFIXES)
    except Exception as exc:  # noqa: BLE001 - an unreadable request is guarded
        del exc
        return True


def _header_pairs(headers: HeaderSource) -> list[tuple[str, str]]:
    """``[(lowercased name, value)]`` from a dict, a pair list, or an
    ``email.message.Message`` (which is what ``http.server`` hands over).

    Duplicates are PRESERVED — detecting them is a check, so collapsing them
    here would be the bug this guard is supposed to catch.
    """
    getter = getattr(headers, "items", None)
    try:
        items = getter() if callable(getter) else headers
    except Exception as exc:  # noqa: BLE001 - an unreadable header set has none
        del exc
        return []
    pairs: list[tuple[str, str]] = []
    try:
        for name, value in items:
            pairs.append((str(name).strip().lower(), str(value)))
    except Exception as exc:  # noqa: BLE001 - a partially readable set is still usable
        del exc
        return pairs
    return pairs


def _has_forbidden_character(value: str) -> bool:
    return any(unicodedata.category(character) in _FORBIDDEN_CATEGORIES for character in value)


def _is_clean(name: str, value: str) -> bool:
    """A header value with no control/format character and a sane length."""
    cap = _MAX_COOKIE_CHARS if name == "cookie" else _MAX_HEADER_CHARS
    if len(value) > cap:
        return False
    return not _has_forbidden_character(value)


def _hostname_of(host_header: str) -> str:
    """The hostname in a ``Host`` header, lowercased, port removed.

    ``[::1]:8823`` -> ``[::1]``; ``LOCALHOST:9999`` -> ``localhost``. An IPv6
    literal keeps its brackets, which is why both bracketed and bare forms are
    in :data:`DEFAULT_ALLOWED_HOSTS`.
    """
    value = host_header.strip().lower()
    if value.startswith("["):
        closing = value.find("]")
        return value[: closing + 1] if closing != -1 else value
    return value.split(":", 1)[0]


def _cookie_value(cookie_header: str, name: str) -> str:
    """One cookie's value, parsed by hand.

    ``http.cookies.SimpleCookie`` raises ``CookieError`` on input an attacker
    fully controls, and silently drops the rest of the jar on some malformed
    input. Splitting on ``;`` cannot do either.
    """
    for crumb in cookie_header.split(";"):
        key, separator, value = crumb.partition("=")
        if separator and key.strip() == name:
            return value.strip()
    return ""


class Guard:
    """The policy, with the Access verifier and the degradation sink injected.

    Never raises, never blocks, holds no state but counters. ``on_degrade`` is
    called ``(code, reason)`` when a *configuration* fault is what refused the
    request — a missing verifier, a verifier that blew up — not on an ordinary
    refusal, which is the guard working rather than degrading.
    """

    def __init__(
        self,
        config: GuardConfig,
        *,
        assertion_verifier: Optional[AssertionVerifier] = None,
        on_degrade: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._config = config
        self._verifier: AssertionVerifier = assertion_verifier or refusing_assertion_verifier
        self._on_degrade = on_degrade
        self._secret = config.install_secret or ""
        hosts = set(config.allowed_hosts or frozenset())
        public = (config.public_hostname or "").strip().lower()
        self._public_hostname = public
        if public:
            hosts.add(public)
        self._allowed_hosts = frozenset(hosts)
        self._allowed_origins = frozenset(
            origin.strip().rstrip("/").lower() for origin in (config.allowed_origins or ())
        )
        #: Counted so a host can see a broken hook rather than infer it.
        self.hook_errors = 0

    @property
    def config(self) -> GuardConfig:
        return self._config

    @property
    def public_hostname(self) -> str:
        return self._public_hostname

    def _degrade(self, code: str, reason: str) -> None:
        if self._on_degrade is None:
            return
        try:
            self._on_degrade(code, reason)
        except Exception as exc:  # noqa: BLE001 - a broken sink is counted, not raised
            del exc
            self.hook_errors += 1

    @staticmethod
    def _refuse(code: str, status: int) -> GuardDecision:
        return GuardDecision(
            allowed=False,
            guarded=True,
            status=status,
            code=code,
            reason=_REASONS.get(code, "refused"),
        )

    def check(self, method: str, path: str, headers: HeaderSource) -> GuardDecision:
        """The verdict for one request. Never raises, whatever it is handed."""
        try:
            return self._check(method, path, headers)
        except Exception as exc:  # noqa: BLE001 - a guard that can crash is a guard that opens
            self._degrade(
                REFUSED_MALFORMED_HEADER_CODE,
                f"the guard could not evaluate the request: {describe_exception(exc)}",
            )
            return self._refuse(REFUSED_MALFORMED_HEADER_CODE, 400)

    def _check(self, method: str, path: str, headers: HeaderSource) -> GuardDecision:
        if not requires_guard(method, path):
            return _ALLOWED_UNGUARDED

        pairs = _header_pairs(headers)
        seen: dict[str, set[str]] = {}
        for name, value in pairs:
            if name in SENSITIVE_HEADERS:
                seen.setdefault(name, set()).add(value)
        if any(len(values) > 1 for values in seen.values()):
            return self._refuse(REFUSED_DUPLICATE_HEADER_CODE, 400)
        if any(not _is_clean(name, next(iter(values))) for name, values in seen.items()):
            return self._refuse(REFUSED_MALFORMED_HEADER_CODE, 400)

        def one(name: str) -> str:
            values = seen.get(name)
            return next(iter(values)) if values else ""

        host = _hostname_of(one("host"))
        if not host or host not in self._allowed_hosts:
            return self._refuse(REFUSED_HOST_CODE, 403)

        origin = one("origin").strip()
        if origin:
            if origin.rstrip("/").lower() not in self._allowed_origins:
                return self._refuse(REFUSED_ORIGIN_CODE, 403)

        secret_decision = self._check_secret(
            one("authorization"),
            one("cookie"),
            origin_present=bool(origin),
            # The header NAME is matched case-insensitively, as every header
            # here is; the VALUE is matched exactly. ``Sec-Fetch-Site`` carries
            # a lowercase token a browser generates, never something a human
            # types, so accepting ``SAME-ORIGIN`` could only ever widen the
            # rule for a client that is not a browser. Found by attacking the
            # round-2 rule: case-folding the value let that through.
            same_origin_metadata=one(SEC_FETCH_SITE_HEADER).strip() == SAME_ORIGIN_SITE,
        )
        if secret_decision is not None:
            return secret_decision

        if self._public_hostname and host == self._public_hostname:
            return self._check_assertion(one(ACCESS_ASSERTION_HEADER).strip())

        return _ALLOWED

    def _check_secret(
        self,
        authorization: str,
        cookie: str,
        *,
        origin_present: bool,
        same_origin_metadata: bool,
    ) -> Optional[GuardDecision]:
        """``None`` when the credential is good; a refusal otherwise.

        A bearer credential needs nothing else: it cannot be attached to a
        request by a cross-site page, because a page cannot set a header on a
        request it did not author. A cookie credential can, so it needs the
        browser to vouch for the request — an allow-listed ``Origin`` (already
        checked by the caller, which is why only its *presence* arrives here)
        or ``Sec-Fetch-Site: same-origin``.
        """
        if not self._secret:
            return self._refuse(REFUSED_SECRET_CODE, 401)

        scheme, _, presented = authorization.partition(" ")
        bearer = presented.strip() if scheme.strip().lower() == AUTHORIZATION_SCHEME else ""
        from_cookie = _cookie_value(cookie, SECRET_COOKIE_NAME) if cookie else ""

        for candidate, is_cookie in ((bearer, False), (from_cookie, True)):
            if not candidate:
                continue
            if not hmac.compare_digest(candidate, self._secret):
                continue
            if is_cookie and not (origin_present or same_origin_metadata):
                return self._refuse(REFUSED_COOKIE_WITHOUT_ORIGIN_CODE, 403)
            return None
        return self._refuse(REFUSED_SECRET_CODE, 401)

    def _check_assertion(self, token: str) -> GuardDecision:
        if not token:
            return self._refuse(REFUSED_ACCESS_MISSING_CODE, 401)
        try:
            result = self._verifier(token)
        except Exception as exc:  # noqa: BLE001 - a verifier that dies must not open the door
            reason = f"the Access assertion verifier raised: {describe_exception(exc)}"
            self._degrade(REFUSED_ACCESS_VERIFIER_FAILED_CODE, reason)
            return self._refuse(REFUSED_ACCESS_VERIFIER_FAILED_CODE, 401)
        if not isinstance(result, AssertionResult):
            self._degrade(
                REFUSED_ACCESS_VERIFIER_FAILED_CODE,
                "the Access assertion verifier returned a non-AssertionResult value",
            )
            return self._refuse(REFUSED_ACCESS_VERIFIER_FAILED_CODE, 401)
        if result.valid:
            return _ALLOWED
        code = result.code or ACCESS_VERIFIER_MISSING_CODE
        if code == ACCESS_VERIFIER_MISSING_CODE:
            self._degrade(code, _REASONS[ACCESS_VERIFIER_MISSING_CODE])
        return GuardDecision(
            allowed=False,
            guarded=True,
            status=401,
            code=code,
            reason=_REASONS.get(code, "the Access assertion was refused"),
        )


# ── the install secret on disk ───────────────────────────────────────────────


@dataclass(frozen=True)
class InstallSecret:
    """A usable secret, plus what had to happen to get one.

    ``secret`` is the only field carrying it. :meth:`to_dict` deliberately
    omits it: a status snapshot is exactly the surface that ends up in a log.
    """

    secret: str
    path: Optional[Path]
    created: bool
    persisted: bool
    code: Optional[str]
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """Everything about the secret EXCEPT the secret."""
        return {
            "path": str(self.path) if self.path is not None else None,
            "created": self.created,
            "persisted": self.persisted,
            "code": self.code,
            "detail": self.detail,
        }


def _generate() -> str:
    return secrets.token_urlsafe(SECRET_ENTROPY_BYTES)


def _read_existing(path: Path) -> tuple[Optional[str], Optional[str], str]:
    """``(secret, code, detail)`` for a secret file that may or may not exist.

    A symlink is refused rather than followed (``O_NOFOLLOW``): the state
    directory is 0700, but a file planted there before the daemon first ran —
    or a shared-temp fallback — must not turn into "read whatever that points
    at". A file owned by someone else is refused for the same reason.
    """
    try:
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None, None, ""
    except OSError as exc:
        return (
            None,
            SECRET_UNREADABLE_CODE,
            f"could not read the install secret file: {describe_exception(exc)}",
        )
    try:
        info = os.fstat(handle)
        uid = getattr(os, "getuid", None)
        if uid is not None and info.st_uid != uid():
            return (
                None,
                SECRET_UNREADABLE_CODE,
                "the install secret file is owned by another user; refusing to read it",
            )
        raw = os.read(handle, MAX_SECRET_BYTES)
        mode = stat.S_IMODE(info.st_mode)
    except OSError as exc:
        return (
            None,
            SECRET_UNREADABLE_CODE,
            f"could not read the install secret file: {describe_exception(exc)}",
        )
    finally:
        os.close(handle)

    secret = raw.decode("utf-8", "replace").strip()
    if len(secret) < MIN_SECRET_CHARS:
        return None, None, ""
    if mode != 0o600:
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            return (
                secret,
                SECRET_TIGHTENED_CODE,
                f"the install secret file is {oct(mode)} and could not be tightened: "
                f"{describe_exception(exc)}",
            )
        return (
            secret,
            SECRET_TIGHTENED_CODE,
            f"tightened the install secret file from {oct(mode)} to 0o600",
        )
    return secret, None, ""


def _ensure_dir(directory: Path) -> Optional[str]:
    """Create *directory* at 0700 regardless of umask. ``None`` when usable.

    :class:`embodiment.daemon.state.DaemonState` normally owns this directory
    and has already made it 0700; this is the case where the secret is asked
    for first, and it must not be the thing that widens it.

    **A state directory that is itself a symlink is refused outright**, the
    same way :mod:`embodiment.memory` refuses a symlinked store root, and for
    a sharper reason: the secret *file* is opened ``O_NOFOLLOW``, but that
    protects nothing if the directory it is created in is a link somewhere
    else — whoever controls the link target controls the credential.
    ``mkdir(exist_ok=True)`` on an existing symlink-to-a-directory succeeds
    silently and ``os.chmod`` follows the link, so neither of those would
    have noticed; only an ``lstat`` before anything else does. Found in
    review, round 3.

    Only the final component is checked. A symlinked *parent* — an operator
    whose whole ``~/.local/state`` lives on another volume — is ordinary, and
    refusing it would refuse the machine rather than an attacker.
    """
    try:
        info: Optional[os.stat_result] = directory.lstat()
    except FileNotFoundError:
        info = None
    except OSError as exc:
        return f"could not inspect the state directory: {describe_exception(exc)}"

    if info is not None and stat.S_ISLNK(info.st_mode):
        return (
            "refusing a state directory that is a symlink: the install secret "
            "would be created behind it, where whatever controls the link "
            "target controls the credential"
        )

    try:
        if info is None:
            directory.mkdir(parents=True, exist_ok=True)
        if stat.S_IMODE(directory.lstat().st_mode) != 0o700:
            os.chmod(directory, 0o700)
    except OSError as exc:
        return f"could not prepare the state directory: {describe_exception(exc)}"
    return None


def load_or_create_install_secret(
    state_dir: Optional[Union[str, Path]],
) -> InstallSecret:
    """The daemon's install secret: read it, or mint one on first start.

    Never raises. Every departure from silence is a code the caller records:
    a fresh secret (:data:`SECRET_CREATED_CODE`), a file that had to be
    tightened (:data:`SECRET_TIGHTENED_CODE`), a file that could not be read
    and was NOT overwritten (:data:`SECRET_UNREADABLE_CODE`), or a secret that
    exists only in memory because nothing could be written
    (:data:`SECRET_UNPERSISTED_CODE` — every client must re-read it after a
    restart, which is exactly the kind of "looks healthy, is not" state C3
    exists to make visible).

    The file is created with ``O_EXCL`` at mode 0600, so two daemons racing
    the first start converge on one secret rather than clobbering each other.
    """
    if state_dir is None:
        return InstallSecret(
            secret=_generate(),
            path=None,
            created=True,
            persisted=False,
            code=SECRET_UNPERSISTED_CODE,
            detail="no state directory was given; the install secret lives in memory only",
        )

    directory = Path(state_dir)
    path = directory / INSTALL_SECRET_FILENAME
    problem = _ensure_dir(directory)
    if problem is not None:
        return InstallSecret(
            secret=_generate(),
            path=None,
            created=True,
            persisted=False,
            code=SECRET_UNPERSISTED_CODE,
            detail=problem,
        )

    existing, code, detail = _read_existing(path)
    if existing is not None:
        return InstallSecret(
            secret=existing, path=path, created=False, persisted=True, code=code, detail=detail
        )
    if code == SECRET_UNREADABLE_CODE:
        return InstallSecret(
            secret=_generate(), path=path, created=True, persisted=False, code=code, detail=detail
        )

    return _create(path)


def _create(path: Path) -> InstallSecret:
    """Publish a fresh secret ATOMICALLY, or degrade to an in-memory one.

    The obvious shape — ``O_CREAT|O_EXCL`` on the real path, then write —
    leaves a window in which the file exists and is **empty**. A second daemon
    starting inside that window finds a file, reads nothing usable from it,
    and replaces it; the first is then serving a secret that is no longer on
    disk, and every client that re-reads the file is refused. Measured with 24
    concurrent callers on a fresh state dir: **4 different secrets** came
    back. The window was always there — round 3's ``lstat`` fast path merely
    made the callers arrive closer together and turned it from rare into
    reproducible, which is a good argument for treating a concurrency probe
    that passes as weak evidence.

    Two mechanisms, one per case:

    * **No file yet** — the secret is written to a private temporary sibling,
      flushed, and linked into place with ``os.link``, which refuses to
      clobber exactly like ``O_EXCL`` but publishes the **complete** file. A
      racer sees either no file or a whole one, so every loser reads the
      winner's secret and all of them converge.
    * **A file that no daemon could authenticate with** (a truncated write, a
      crash fragment) — replacing it cannot be done by everyone at once, so
      one daemon takes a lock and the others wait for it and re-read. See
      :func:`_repair_unusable`.
    """
    fresh = _generate()
    for _attempt in range(CREATE_ATTEMPTS):
        published = _publish(path, fresh, clobber=False)
        if published is not None:
            return published

        existing, code, detail = _read_existing(path)
        if existing is not None:
            return InstallSecret(
                secret=existing, path=path, created=False, persisted=True, code=code, detail=detail
            )

        repaired = _repair_unusable(path, fresh)
        if repaired is not None:
            return repaired

        waited = _await_usable(path)
        if waited is not None:
            return waited

        # Whoever held the lock never finished — it died mid-repair, or the
        # lock is left over from a previous crash. Steal it and go round once.
        _unlink(path.with_name(path.name + LOCK_SUFFIX))

    return _unpersisted(
        path, fresh, "could not converge on an install secret file", OSError("unconverged")
    )


def _repair_unusable(path: Path, fresh: str) -> Optional[InstallSecret]:
    """Replace an unusable secret file, under a lock. ``None`` = not our turn.

    Without exclusion every concurrent starter replaces the same corrupt file
    with its own secret and each keeps the one it wrote, so they end up
    disagreeing — the very defect the link publish closes for the fresh case.
    Measured before this existed: 12 callers against a 4-byte fragment
    produced 12 different secrets.

    The lock is a file created ``O_EXCL``; one daemon wins it, re-checks under
    it (somebody may have repaired the file while this one was deciding), and
    replaces. Everyone else gets ``None`` and waits in :func:`_await_usable`.
    A lock whose holder died is stolen by the caller's second attempt, so a
    crash mid-repair costs one poll interval, not a wedged start.
    """
    lock = path.with_name(path.name + LOCK_SUFFIX)
    try:
        handle = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return None
    except OSError as exc:
        return _unpersisted(path, fresh, "could not lock the install secret for repair", exc)
    try:
        os.close(handle)
    except OSError as exc:
        del exc  # the lock exists, which is all the open was for
    try:
        existing, code, detail = _read_existing(path)
        if existing is not None:
            return InstallSecret(
                secret=existing, path=path, created=False, persisted=True, code=code, detail=detail
            )
        return _publish(path, fresh, clobber=True)
    finally:
        _unlink(lock)


def _await_usable(path: Path) -> Optional[InstallSecret]:
    """Wait :data:`REPAIR_WAIT_S` for another daemon's repair. ``None`` = it never came."""
    deadline = time.monotonic() + REPAIR_WAIT_S
    while time.monotonic() < deadline:
        existing, code, detail = _read_existing(path)
        if existing is not None:
            return InstallSecret(
                secret=existing, path=path, created=False, persisted=True, code=code, detail=detail
            )
        time.sleep(REPAIR_POLL_S)
    return None


def _publish(path: Path, fresh: str, *, clobber: bool) -> Optional[InstallSecret]:
    """Write *fresh* to a temp sibling and move it onto *path*. Never raises.

    ``clobber=False`` uses ``os.link``, which fails with ``EEXIST`` rather
    than overwriting — that is the first-start race, and ``None`` comes back
    so the caller reads whoever won. ``clobber=True`` uses ``os.replace``,
    which is atomic and is only ever called under the repair lock, so it is
    never two daemons overwriting each other.
    """
    what = (
        "could not replace the unusable install secret"
        if clobber
        else ("could not write the install secret")
    )
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        return _unpersisted(path, fresh, what, exc)
    problem = _write_secret(handle, fresh)
    if problem is not None:
        _unlink(tmp)
        return _unpersisted(path, fresh, what, problem)
    try:
        if clobber:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)
    except FileExistsError:
        _unlink(tmp)
        return None
    except OSError as exc:
        _unlink(tmp)
        return _unpersisted(path, fresh, what, exc)
    finally:
        if not clobber:
            _unlink(tmp)
    return InstallSecret(
        secret=fresh,
        path=path,
        created=True,
        persisted=True,
        code=SECRET_CREATED_CODE,
        detail=(
            "replaced an unusable install secret file"
            if clobber
            else "generated a new install secret on first start"
        ),
    )


def _unlink(path: Path) -> None:
    """Remove a temporary file. Never raises; a leftover is swept next start."""
    try:
        os.unlink(path)
    except OSError as exc:
        del exc  # already gone, or the directory is read-only


def _unpersisted(path: Path, fresh: str, what: str, exc: BaseException) -> InstallSecret:
    """The in-memory floor: a working secret that dies with the process."""
    return InstallSecret(
        secret=fresh,
        path=path,
        created=True,
        persisted=False,
        code=SECRET_UNPERSISTED_CODE,
        detail=f"{what}: {describe_exception(exc)}",
    )


def _write_secret(handle: int, fresh: str) -> Optional[OSError]:
    """Write and flush *fresh* to *handle*, closing it. The error, or ``None``."""
    problem: Optional[OSError] = None
    try:
        os.write(handle, fresh.encode("utf-8"))
        os.fsync(handle)
    except OSError as exc:
        problem = exc
    finally:
        try:
            os.close(handle)
        except OSError as exc:
            del exc  # a close that fails after a successful fsync changes nothing
    return problem
