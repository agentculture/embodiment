"""The shared no-speech-in-a-record guarantee, and the guard that keeps it.

Wave 1's lesson 5 says a degradation reason never carries what the user said.
Every module had that story in its docstring, and every module broke it the same
way: ``str(exc)``. An exception message is not the module's text — it is the
*dependency's*, and dependencies quote their input. An HTTP client raising
``400 bad request: body=[…]`` puts the whole request, system prompt and user
turn alike, into a string that this repo then copies into a degradation reason,
the operational log and the dashboard event stream.

Measured on the merged wave-1 code, before this change: six leaking surfaces
across three modules from one marker string.

This file holds three things:

* the unit tests for :mod:`embodiment.safe_reason`;
* :func:`assert_no_speech`, the ONE reusable property helper the ``turn``,
  ``tools`` and ``memory`` suites all import, so the three modules are held to a
  single definition of "leaked" rather than three that drift;
* the AST guard, which is what makes this a property of the source rather than
  of the cases someone remembered to test.
"""

from __future__ import annotations

import ast
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import pytest

from embodiment import safe_reason
from embodiment.safe_reason import describe_exception

EMBODIMENT_ROOT = Path(safe_reason.__file__).resolve().parent

#: The marker planted as the user's words / a tool argument / a memory record.
#: Deliberately unmistakable: no legitimate field is going to contain it, so a
#: hit is a hit.
MARKER = "ZQXMARKER-the-user-said-this"


# ── the shared property helper ────────────────────────────────────────────────


def walk_strings(value: Any, *, depth: int = 0) -> Iterable[str]:
    """Every string reachable from *value*, including through dataclasses.

    ``repr`` alone is not enough — a dataclass whose ``repr`` is customised, or
    a tuple of records, can hide a field — so this walks the structure *and*
    the tests also scan ``repr``. Two overlapping scans, because the failure
    being guarded against is precisely "we only looked where we expected".
    """
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, (bytes, bytearray)):
        yield value.decode("utf-8", "replace")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_strings(key, depth=depth + 1)
            yield from walk_strings(item, depth=depth + 1)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from walk_strings(item, depth=depth + 1)
        return
    fields = getattr(value, "__dataclass_fields__", None)
    if fields:
        for name in fields:
            yield from walk_strings(getattr(value, name, None), depth=depth + 1)
        return
    mapping = getattr(value, "__dict__", None)
    if isinstance(mapping, dict):
        yield from walk_strings(mapping, depth=depth + 1)
        return
    yield repr(value)


def find_speech(marker: str, *surfaces: Any) -> list[str]:
    """Every place *marker* is reachable from *surfaces*. Empty means clean."""
    hits: list[str] = []
    for index, surface in enumerate(surfaces):
        for text in walk_strings(surface):
            if marker in text:
                hits.append(f"surface {index}: {text[:200]!r}")
        rendered = repr(surface)
        if marker in rendered:
            hits.append(f"surface {index} repr: {rendered[:200]!r}")
        to_dict = getattr(surface, "to_dict", None)
        if callable(to_dict):
            try:
                dumped = repr(to_dict())
            except Exception:  # noqa: BLE001  # a to_dict that raises is a different bug
                dumped = ""
            if marker in dumped:
                hits.append(f"surface {index} to_dict: {dumped[:200]!r}")
    return hits


def assert_no_speech(marker: str, *surfaces: Any) -> None:
    """THE shared assertion. Used by the turn, tools and memory suites alike."""
    hits = find_speech(marker, *surfaces)
    assert not hits, "speech reached a record:\n" + "\n".join(hits[:6])


def assert_speech_present(marker: str, *surfaces: Any) -> None:
    """The negative control: with the escape hatch on, the marker MUST appear.

    Without this, every ``assert_no_speech`` in the suite could be passing
    because the plumbing never ran.
    """
    assert find_speech(marker, *surfaces), "the unsafe escape hatch produced no message"


# ── exceptions that hide the marker in every corner they can ──────────────────


class Hostile(Exception):
    """An exception that carries the marker everywhere an exception can."""


def hostile_exception(marker: str) -> BaseException:
    """An exception with *marker* in its message, args, ``__cause__`` and notes."""
    cause = ValueError(f"cause says {marker}")
    exc = Hostile(f"message says {marker}", {"arg": marker})
    exc.__cause__ = cause
    exc.add_note(f"note says {marker}")
    return exc


# ── 1. describe_exception ─────────────────────────────────────────────────────


class TestDescribeExceptionSaysNothingItWasToldIn:
    """What it returns is safe BY CONSTRUCTION, not by filtering."""

    def test_the_message_never_appears(self) -> None:
        described = describe_exception(hostile_exception(MARKER))
        assert MARKER not in described

    def test_the_class_name_is_named(self) -> None:
        assert "Hostile" in describe_exception(hostile_exception(MARKER))

    def test_the_cause_chain_is_named(self) -> None:
        described = describe_exception(hostile_exception(MARKER))
        assert "ValueError" in described
        assert MARKER not in described

    def test_a_context_chain_is_named(self) -> None:
        try:
            try:
                raise ValueError(f"inner {MARKER}")
            except ValueError:
                raise KeyError(f"outer {MARKER}")
        except KeyError as exc:
            described = describe_exception(exc)
        assert "KeyError" in described and "ValueError" in described
        assert MARKER not in described

    def test_the_chain_is_depth_bounded(self) -> None:
        exc: BaseException = ValueError("root")
        for index in range(50):
            nested = RuntimeError(f"level {index}")
            nested.__cause__ = exc
            exc = nested
        described = describe_exception(exc)
        assert described.count("<-") <= safe_reason.MAX_CHAIN_DEPTH
        assert len(described) <= safe_reason.MAX_DESCRIPTION_CHARS

    def test_a_self_referential_chain_terminates(self) -> None:
        exc = ValueError("loop")
        exc.__cause__ = exc
        assert "ValueError" in describe_exception(exc)

    def test_the_message_length_is_reported_not_the_message(self) -> None:
        described = describe_exception(ValueError("x" * 137))
        assert "message: 137 chars" in described

    def test_an_empty_message_is_reported_as_zero(self) -> None:
        assert "message: 0 chars" in describe_exception(ValueError())

    def test_the_fingerprint_correlates_two_occurrences(self) -> None:
        first = describe_exception(ValueError(f"the same fault {MARKER}"))
        second = describe_exception(ValueError(f"the same fault {MARKER}"))
        third = describe_exception(ValueError("a different fault"))

        assert "fp:" in first
        assert first == second
        assert first != third

    def test_the_fingerprint_is_short_hex(self) -> None:
        described = describe_exception(ValueError("x"))
        fingerprint = described.split("fp:")[1].split(")")[0].split(",")[0].strip()
        assert len(fingerprint) == 8
        assert all(character in "0123456789abcdef" for character in fingerprint)

    def test_an_oserror_reports_its_errno_name(self) -> None:
        import errno

        exc = OSError(errno.ENOSPC, f"No space left on device {MARKER}")
        described = describe_exception(exc)
        assert "ENOSPC" in described
        assert MARKER not in described
        assert "No space left" not in described

    def test_an_unnamed_errno_is_reported_numerically(self) -> None:
        exc = OSError(999_999, "nonsense")
        assert "errno=999999" in describe_exception(exc)

    def test_an_http_status_attribute_is_reported(self) -> None:
        for attribute in ("status", "status_code", "code"):
            exc = RuntimeError(f"boom {MARKER}")
            setattr(exc, attribute, 401)
            described = describe_exception(exc)
            assert "status=401" in described, attribute
            assert MARKER not in described

    def test_a_status_outside_the_http_range_is_not_reported(self) -> None:
        exc = RuntimeError("boom")
        exc.status_code = 70_000
        assert "status=" not in describe_exception(exc)

    def test_a_non_integer_status_is_not_reported(self) -> None:
        exc = RuntimeError("boom")
        exc.status = MARKER
        described = describe_exception(exc)
        assert "status=" not in described
        assert MARKER not in described

    def test_a_boolean_status_is_not_reported(self) -> None:
        """``True`` is an ``int`` in Python; it is not an HTTP status."""
        exc = RuntimeError("boom")
        exc.status = True
        assert "status=" not in describe_exception(exc)

    def test_a_status_property_that_raises_is_survived(self) -> None:
        class Nasty(Exception):
            @property
            def status(self) -> int:
                raise RuntimeError(f"even the getter says {MARKER}")

        described = describe_exception(Nasty("x"))
        assert MARKER not in described
        assert "Nasty" in described

    def test_a_class_name_carrying_format_characters_is_stripped(self) -> None:
        hostile = type("Ev‮il", (Exception,), {})
        described = describe_exception(hostile("x"))
        assert "‮" not in described

    def test_no_format_or_separator_characters_ever_survive(self) -> None:
        for character in ("‮", "⁦", "​", "﻿", " ", "\u0085"):
            exc = OSError(1, f"a{character}b")
            setattr(exc, "weird", character)
            described = describe_exception(exc)
            assert character not in described, repr(character)

    def test_a_str_that_raises_is_survived(self) -> None:
        class Unstringable(Exception):
            def __str__(self) -> str:
                raise RuntimeError(f"cannot render {MARKER}")

        described = describe_exception(Unstringable())
        assert MARKER not in described
        assert "Unstringable" in described

    def test_it_never_raises_on_anything(self) -> None:
        class Awkward(Exception):
            def __getattribute__(self, name: str) -> Any:
                if name in {"__class__", "__cause__", "__context__"}:
                    return object.__getattribute__(self, name)
                raise RuntimeError("no attributes for you")

        assert isinstance(describe_exception(Awkward()), str)
        assert isinstance(describe_exception(None), str)  # type: ignore[arg-type]

    def test_a_safe_detail_the_author_declared_is_kept(self) -> None:
        """The one channel for a message-shaped fact, and it is opt-in.

        A raiser that *knows* a string is safe can say so by setting
        ``safe_detail``. It is restricted and capped like any other label, so
        declaring it wrong costs a mangled string rather than a leak.
        """
        exc = ValueError(f"raw {MARKER}")
        exc.safe_detail = "missing-required-argument"
        described = describe_exception(exc, allow_detail=True)
        assert "missing-required-argument" in described
        assert MARKER not in described

    def test_a_safe_detail_is_ignored_unless_the_caller_vouches(self) -> None:
        """Found by attacking this module, not by the brief.

        ``safe_detail`` is an attribute on an object this module did not
        create. "The raiser declared it safe" only means something when the
        caller vouches for the raiser, so the default is off and a dependency's
        exception never gets the benefit of the doubt.
        """
        exc = ValueError("clean")
        exc.safe_detail = MARKER
        assert MARKER not in describe_exception(exc)
        assert MARKER in describe_exception(exc, allow_detail=True)

    def test_a_declared_detail_is_capped_tighter_than_a_label(self) -> None:
        exc = ValueError("x")
        exc.safe_detail = "a" * 500
        described = describe_exception(exc, allow_detail=True)
        detail = described.split("detail=")[1].split(",")[0]
        assert len(detail) == safe_reason.MAX_DETAIL_CHARS

    def test_a_synthesised_class_name_is_bounded(self) -> None:
        """The documented residual: a class name built from remote data.

        Some RPC and cloud SDKs synthesise an exception class per server-supplied
        error code, so this name is not always a literal from somebody's source.
        Restriction keeps charset-clean text rather than removing it, so the
        exposure is BOUNDED rather than closed — and that bound is asserted
        here so it cannot quietly grow.
        """
        hostile = type("E" + "x" * 300, (Exception,), {})
        described = describe_exception(hostile("m"))
        assert len(described.split(" (")[0]) <= safe_reason.MAX_CLASS_NAME_CHARS

    def test_a_safe_detail_is_restricted_to_the_label_charset(self) -> None:
        exc = ValueError("x")
        exc.safe_detail = f"has spaces and {MARKER[:8]}<<<brackets"
        described = describe_exception(exc, allow_detail=True)
        assert "<<<" not in described
        detail = described.split("detail=")[1].split(",")[0]
        assert " " not in detail
        assert set(detail) <= safe_reason.LABEL_CHARSET | {safe_reason.LABEL_PLACEHOLDER}


class TestTheUnsafeEscapeHatch:
    """Loud, off by default, and read at CALL time so a test can flip it."""

    def test_it_is_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(safe_reason.UNSAFE_ENV, raising=False)
        assert MARKER not in describe_exception(hostile_exception(MARKER))

    def test_it_puts_the_message_back_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(safe_reason.UNSAFE_ENV, "1")
        described = describe_exception(hostile_exception(MARKER))
        assert MARKER in described
        assert "UNSAFE" in described

    def test_the_env_var_is_read_at_call_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Import-time reading would make the hatch untestable and unflippable."""
        monkeypatch.delenv(safe_reason.UNSAFE_ENV, raising=False)
        assert MARKER not in describe_exception(ValueError(MARKER))
        monkeypatch.setenv(safe_reason.UNSAFE_ENV, "1")
        assert MARKER in describe_exception(ValueError(MARKER))
        monkeypatch.delenv(safe_reason.UNSAFE_ENV, raising=False)
        assert MARKER not in describe_exception(ValueError(MARKER))

    def test_only_an_exact_opt_in_enables_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for value in ("0", "", "true", "yes", "no"):
            monkeypatch.setenv(safe_reason.UNSAFE_ENV, value)
            assert MARKER not in describe_exception(ValueError(MARKER)), value

    def test_the_appended_message_is_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(safe_reason.UNSAFE_ENV, "1")
        described = describe_exception(ValueError("y" * 5000))
        assert described.count("y") <= safe_reason.UNSAFE_MESSAGE_CHARS

    def test_even_unsafe_mode_strips_format_characters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(safe_reason.UNSAFE_ENV, "1")
        described = describe_exception(ValueError("a‮b c"))
        assert "‮" not in described and " " not in described

    def test_the_env_var_name_says_what_it_does(self) -> None:
        assert "UNSAFE" in safe_reason.UNSAFE_ENV

    def test_the_docstring_warns_about_speech_in_logs(self) -> None:
        doc = (describe_exception.__doc__ or "") + (safe_reason.__doc__ or "")
        assert "UNSAFE" in doc
        assert "speech" in doc.lower()


class TestSafeLabel:
    """Attacker-controlled identifiers are restricted, not interpolated."""

    def test_a_hostile_tool_name_is_restricted(self) -> None:
        cleaned = safe_reason.safe_label(f"tool<<<{MARKER} name")
        assert "<" not in cleaned and " " not in cleaned
        assert set(cleaned) <= safe_reason.LABEL_CHARSET | {safe_reason.LABEL_PLACEHOLDER}

    def test_restriction_does_not_remove_charset_clean_text(self) -> None:
        """Stated as a test because it is the limit of what a label can promise.

        ``safe_label`` makes an identifier structurally safe; it does not and
        cannot make it *contentless*. A string that is already
        ``[A-Za-z0-9._-]`` passes through whole. That is why an identifier with
        no host provenance — an unregistered tool name the model invented — is
        FINGERPRINTED by its caller rather than merely restricted.
        """
        assert safe_reason.safe_label(MARKER) == MARKER

    def test_a_benign_name_survives(self) -> None:
        assert safe_reason.safe_label("read_file.v2-beta") == "read_file.v2-beta"

    def test_it_is_capped(self) -> None:
        assert len(safe_reason.safe_label("a" * 500)) <= safe_reason.MAX_LABEL_CHARS

    def test_an_empty_label_has_a_fallback(self) -> None:
        assert safe_reason.safe_label("") == safe_reason.LABEL_FALLBACK

    def test_format_characters_never_survive(self) -> None:
        assert "‮" not in safe_reason.safe_label("a‮b")


class TestTheSharedCategorySetLivesHere:
    """One definition, imported by memory — not two that drift."""

    def test_memory_imports_the_set_from_safe_reason(self) -> None:
        from embodiment import memory

        assert memory.STRIPPED_CATEGORIES is safe_reason.STRIPPED_CATEGORIES

    def test_the_set_covers_the_categories_that_matter(self) -> None:
        assert {"Cc", "Cf", "Zl", "Zp"} <= safe_reason.STRIPPED_CATEGORIES

    def test_scrub_removes_every_member_of_the_set(self) -> None:
        for character in ("‮", "⁦", "​", "﻿", " ", " ", "\x85"):
            assert character not in safe_reason.scrub(f"a{character}b")
            assert unicodedata.category(character) in safe_reason.STRIPPED_CATEGORIES


# ── 2. the AST guard ──────────────────────────────────────────────────────────

#: Exactly what the guard rejects inside an ``except`` handler, outside
#: ``safe_reason.py``. Each is a way an exception's *message* becomes text.
REJECTED_PATTERNS = (
    "str(<caught exception>)",
    "repr(<caught exception>)",
    "f-string interpolation of <caught exception>",
    "<caught exception>.args",
    "'{}'.format(<caught exception>) / '%s' % <caught exception>",
)


def _is_exception_annotation(node: ast.expr | None) -> bool:
    """Whether an annotation names an exception type.

    ``Optional[BaseException]`` and ``BaseException`` alike. This exists because
    the first version of this guard checked ``except`` handlers only — and
    ``turn.py`` sailed through it, because its handler passed ``exc`` to a
    *helper* that did the interpolation one function away. Moving the render
    out of the handler is the obvious refactor, so a guard that does not follow
    it is a guard that will be walked around without anyone meaning to.
    """
    if node is None:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and (
            child.id.endswith(("Error", "Exception")) or child.id == "BaseException"
        ):
            return True
    return False


def _handler_violations(source: str, module: str) -> list[str]:
    """Every rejected rendering of an exception, in a handler or a helper."""
    found: list[str] = []

    class Scan(ast.NodeVisitor):
        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.name:
                for child in ast.walk(node):
                    found.extend(_uses(child, node.name, module, node.lineno))
            self.generic_visit(node)

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            for argument in arguments:
                if _is_exception_annotation(argument.annotation):
                    for child in ast.walk(node):
                        found.extend(_uses(child, argument.arg, module, node.lineno))
            self.generic_visit(node)

        visit_FunctionDef = _function  # type: ignore[assignment]
        visit_AsyncFunctionDef = _function  # type: ignore[assignment]

    Scan().visit(ast.parse(source))
    return sorted(set(found))


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _uses(node: ast.AST, name: str, module: str, lineno: int) -> list[str]:
    """Which rejected pattern *node* is, if any."""
    where = f"{module}:{getattr(node, 'lineno', lineno)}"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in {"str", "repr"} and any(_is_name(a, name) for a in node.args):
            return [f"{where}: {node.func.id}({name})"]
    if isinstance(node, ast.FormattedValue) and _is_name(node.value, name):
        return [f"{where}: f-string interpolation of {name}"]
    if isinstance(node, ast.Attribute) and node.attr == "args" and _is_name(node.value, name):
        return [f"{where}: {name}.args"]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        if _is_name(node.right, name) or (
            isinstance(node.right, ast.Tuple)
            and any(_is_name(item, name) for item in node.right.elts)
        ):
            return [f"{where}: %-formatting of {name}"]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "format" and any(_is_name(a, name) for a in node.args):
            return [f"{where}: .format({name})"]
    return []


def _modules_bound_by_the_guard() -> list[Path]:
    """Every package module that imports ``safe_reason`` — plus the three named.

    Dynamic on purpose: a module that adopts the sanitiser is a module that has
    accepted the rule, so the guard follows adoption rather than a list that
    goes stale the moment a fourth module lands.
    """
    named = {"turn.py", "tools.py", "memory.py"}
    bound: list[Path] = []
    for path in sorted(EMBODIMENT_ROOT.rglob("*.py")):
        if path.name == "safe_reason.py":
            continue
        text = path.read_text(encoding="utf-8")
        if path.name in named or "safe_reason" in text:
            bound.append(path)
    return bound


class TestNoExceptionMessageBecomesText:
    """The guard. Without it this fix survives exactly until the next edit."""

    def test_the_three_modules_are_all_covered(self) -> None:
        covered = {path.name for path in _modules_bound_by_the_guard()}
        assert {"turn.py", "tools.py", "memory.py"} <= covered

    @pytest.mark.parametrize("path", _modules_bound_by_the_guard(), ids=lambda p: p.name)
    def test_no_handler_renders_its_exception(self, path: Path) -> None:
        violations = _handler_violations(path.read_text(encoding="utf-8"), path.name)
        assert not violations, (
            "an exception message becomes text here; use "
            "safe_reason.describe_exception instead:\n  " + "\n  ".join(violations)
        )

    def test_safe_reason_itself_is_exempt_and_needs_to_be(self) -> None:
        """The exemption is real: the sanitiser must touch the message."""
        source = (EMBODIMENT_ROOT / "safe_reason.py").read_text(encoding="utf-8")
        assert "str(" in source
        assert (EMBODIMENT_ROOT / "safe_reason.py") not in _modules_bound_by_the_guard()

    @pytest.mark.parametrize(
        "snippet",
        [
            "try:\n    f()\nexcept Exception as exc:\n    log(str(exc))\n",
            "try:\n    f()\nexcept Exception as exc:\n    log(repr(exc))\n",
            "try:\n    f()\nexcept Exception as exc:\n    log(f'failed: {exc}')\n",
            "try:\n    f()\nexcept Exception as exc:\n    log(exc.args)\n",
            "try:\n    f()\nexcept Exception as exc:\n    log('%s' % exc)\n",
            "try:\n    f()\nexcept Exception as exc:\n    log('{}'.format(exc))\n",
            "try:\n    f()\nexcept Exception as exc:\n    log('%s %s' % (1, exc))\n",
            # The escape the first version of this guard missed: the render
            # moved one function away from the handler.
            "def _record(code: str, exc: BaseException) -> str:\n    return f'{exc}'\n",
            "def _record(exc: Optional[BaseException]) -> str:\n    return str(exc)\n",
            "def _record(error: ValueError) -> str:\n    return repr(error)\n",
        ],
    )
    def test_the_guard_catches_a_planted_violation(self, snippet: str) -> None:
        """A test of the test: every rejected pattern really is rejected."""
        assert _handler_violations(snippet, "planted.py")

    def test_the_guard_follows_the_exception_out_of_the_handler(self) -> None:
        """Named separately because it is the escape that got past version one."""
        snippet = (
            "def _degradation(code: str, exc: BaseException) -> str:\n"
            "    return f'{type(exc).__name__}: {exc}'\n"
            "\n"
            "def run():\n"
            "    try:\n"
            "        f()\n"
            "    except Exception as exc:\n"
            "        return _degradation('c', exc)\n"
        )
        violations = _handler_violations(snippet, "helper.py")
        assert any("_degradation" not in v and "interpolation" in v for v in violations)

    @pytest.mark.parametrize(
        "snippet",
        [
            "try:\n    f()\nexcept Exception as exc:\n    log(describe_exception(exc))\n",
            "try:\n    f()\nexcept Exception as exc:\n    log(type(exc).__name__)\n",
            "try:\n    f()\nexcept Exception:\n    log('failed')\n",
            "def f(exc):\n    return str(exc)\n",
        ],
    )
    def test_the_guard_allows_what_it_should(self, snippet: str) -> None:
        assert _handler_violations(snippet, "fine.py") == []

    def test_the_rejected_patterns_are_documented(self) -> None:
        assert len(REJECTED_PATTERNS) == 5
