"""No degradation is swallowed in silence — proved over the source (task t9).

Constraint **C3**: *every degradation records a host-visible transition; nothing
degrades silently.* Every lane's own suite proves that behaviourally, one path
at a time. This file proves the negative that behaviour cannot: that there is no
*other* path — no ``except: pass`` sitting quietly on a presence path, catching
a failure the host will never hear about.

Read as AST, never as text
--------------------------
Half this package's modules legitimately *discuss* exception handling in prose:
``lifecycle._emit``'s docstring says "so nothing here is a silent ``except:
pass``", and ``presence_engine``'s ``PresenceIO`` documents which single
callback's exceptions are swallowed. A substring scan would fail the build for
writing exactly the documentation the constraint deserves — the same trap
``tests/test_no_shell_host.py`` avoids for ``shell_cli`` and
``tests/test_continuity.py`` for ``subprocess``. A guard that punishes the
behaviour it exists to encourage gets deleted the first time it blocks someone.
So this one parses, classifies handlers, and lets prose through.

Two tiers, both allow-listed BY NAME
------------------------------------
A handler is *inert* when its body produces no effect at all. Two kinds are
broad enough to hide a real failure, and each is pinned to an explicit
``(module, function)`` allow-list carrying the justification:

* :data:`SILENT_SWALLOWS` — a broad handler whose body is ``pass``. Nothing is
  recorded, nothing is returned, nothing is re-raised. Three exist, and each is
  a case where the *recording already happened* or there is nothing left to
  record.
* :data:`ABSENT_BY_DESIGN` — a broad handler whose body is ``return None``.
  Not silent: ``None`` is the package's chosen honest reading of "unmeasurable"
  or "unreadable" (task t7 refused to stamp ``0.0`` for a missing clock; task
  t10a read a ``0 + 0`` token pair as unreported). The host sees an absent
  value, which is legible. Still allow-listed, so a new one is a deliberate act
  rather than a place to hide a swallow.

A narrowly-typed inert handler (``except (ValueError, OverflowError): pass`` in
``presence``'s env parsers) needs no entry: it catches one named, expected
condition and falls back to a documented default. Only breadth hides.

Both lists are checked in both directions — an entry naming a handler that no
longer exists fails too, so the allow-list cannot rot into a rubber stamp.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

EMBODIMENT_ROOT = Path(__file__).resolve().parents[1] / "embodiment"

#: Exception expressions broad enough to catch a failure nobody anticipated.
#: A bare ``except:`` is broadest of all and is represented by ``None``.
_BROAD = {"Exception", "BaseException"}


# ── the allow-lists ───────────────────────────────────────────────────────────

#: ``(module, qualified function) -> why this one records nothing at all.``
SILENT_SWALLOWS: dict[tuple[str, str], str] = {
    (
        "presence_engine.py",
        "PresenceEngine._narrate",
    ): (
        "The ONE sanctioned swallow. A voice hook must never disturb the text "
        "path it narrates: render() has already run and its failure is the "
        "host's to see, so an unwired or broken synthesis leaves every rendered "
        "line byte-identical. Documented on PresenceIO.narrate."
    ),
    (
        "events.py",
        "EventEmitter._degrade",
    ): (
        "The degradation is ALREADY on self.degradations when this runs — the "
        "append happens first and unconditionally. Only the optional on_degrade "
        "notification hook's own failure is swallowed, and notifying a host "
        "about a broken notifier through the broken notifier is not available."
    ),
    (
        "events.py",
        "EventEmitter.close",
    ): (
        "Teardown of an already-detached transport client (self._client is set "
        "to None before the call). Mirrors events-cli's own 'safe to call from "
        "a finally' contract; there is no run left to degrade."
    ),
}

#: ``(module, qualified function) -> why None is the honest answer here.``
ABSENT_BY_DESIGN: dict[tuple[str, str], str] = {
    (
        "muse.py",
        "_attr",
    ): "A hostile property reads as absent; callers name the field they lost.",
    (
        "muse.py",
        "_now",
    ): "A broken clock degrades the MEASUREMENT only — latency stays None, never 0.0.",
    (
        "perception.py",
        "_now",
    ): "Same rule for intake: a clock failure is not one of the four fault classes.",
    (
        "workspace.py",
        "_attr",
    ): (
        "The same rule as muse.py's `_attr`, applied to a foreign type: a "
        "headspace result package is read duck-typed because "
        "`headspace.core.result` is private, so a section that cannot be read "
        "is ABSENT and the caller renders the sections that could. A result "
        "with no readable section at all is NOT silent — it records "
        "DEGRADED_UNREADABLE_RESULT."
    ),
    (
        "continuity.py",
        "traverse._fetch_one",
    ): (
        "A graph hop that cannot be resolved is ABSENT, which is what eidetic's "
        "traversal already means by a dangling id — it skips them without error. "
        "The walk's own outcome still records whether it ran at all, so nothing "
        "about the traversal is silent; only the single unresolvable edge is."
    ),
}

#: Markers a sanctioned ``pass``-bodied swallow must carry in its own source, so
#: an allow-list entry can never be the only justification.
_REQUIRED_MARKERS = ("nosec B110", "noqa: BLE001")


# ── the scan ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Handler:
    """One ``except`` clause, located and classified."""

    module: str
    function: str
    lineno: int
    exception: str
    broad: bool
    inert: bool
    returns_none: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.module, self.function)


def _is_inert(statement: ast.stmt) -> tuple[bool, bool]:
    """``(inert, returns_none)`` for one statement in a handler body."""
    if isinstance(statement, ast.Pass):
        return True, False
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
        # A bare ``...`` or a lone docstring — no effect either way.
        return True, False
    if isinstance(statement, ast.Return):
        value = statement.value
        if value is None or (isinstance(value, ast.Constant) and value.value is None):
            return True, True
    return False, False


def _classify(handler: ast.ExceptHandler) -> tuple[bool, bool, bool, str]:
    """``(broad, inert, returns_none, rendered exception expression)``."""
    node = handler.type
    if node is None:
        broad, rendered = True, "bare"
    elif isinstance(node, ast.Tuple):
        names = {ast.unparse(item) for item in node.elts}
        broad, rendered = bool(names & _BROAD), ast.unparse(node)
    else:
        rendered = ast.unparse(node)
        broad = rendered in _BROAD

    verdicts = [_is_inert(statement) for statement in handler.body]
    inert = bool(verdicts) and all(verdict for verdict, _ in verdicts)
    returns_none = inert and any(is_return for _, is_return in verdicts)
    return broad, inert, returns_none, rendered


def scan(source: str, module: str) -> list[Handler]:
    """Every ``except`` clause in *source*, with its enclosing function named.

    Docstrings and comments are structurally invisible here: an ``ast.Str``
    inside a docstring is never an ``ast.ExceptHandler``, so prose about
    exception handling cannot be mistaken for exception handling.
    """
    found: list[Handler] = []

    def walk(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = f"{scope}.{child.name}" if scope else child.name
            if isinstance(child, ast.ExceptHandler):
                broad, inert, returns_none, rendered = _classify(child)
                found.append(
                    Handler(
                        module=module,
                        function=scope or "<module>",
                        lineno=child.lineno,
                        exception=rendered,
                        broad=broad,
                        inert=inert,
                        returns_none=returns_none,
                    )
                )
            walk(child, inner)

    walk(ast.parse(source), "")
    return found


def _package_handlers() -> list[Handler]:
    """Every handler in every module under ``embodiment/``."""
    found: list[Handler] = []
    for path in sorted(EMBODIMENT_ROOT.rglob("*.py")):
        module = str(path.relative_to(EMBODIMENT_ROOT))
        found.extend(scan(path.read_text(encoding="utf-8"), module))
    return found


HANDLERS = _package_handlers()
SILENT = [h for h in HANDLERS if h.broad and h.inert and not h.returns_none]
ABSENT = [h for h in HANDLERS if h.broad and h.inert and h.returns_none]


# ── 1. no unsanctioned silent swallow anywhere in the package ─────────────────


class TestNoSilentSwallow:
    """The acceptance criterion: no silent ``except: pass`` on any path."""

    def test_every_silent_swallow_is_sanctioned_by_name(self) -> None:
        unknown = [h for h in SILENT if h.key not in SILENT_SWALLOWS]
        assert not unknown, "unsanctioned silent swallow(s): " + ", ".join(
            f"{h.module}:{h.lineno} in {h.function} (except {h.exception})" for h in unknown
        )

    def test_every_degrade_to_absent_is_sanctioned_by_name(self) -> None:
        unknown = [h for h in ABSENT if h.key not in ABSENT_BY_DESIGN]
        assert not unknown, "unsanctioned broad degrade-to-None: " + ", ".join(
            f"{h.module}:{h.lineno} in {h.function}" for h in unknown
        )

    def test_the_sanctioned_set_is_exactly_three(self) -> None:
        """Stated as a number so growth is visible in a diff, not just in a set."""
        assert len(SILENT_SWALLOWS) == 3
        assert {h.key for h in SILENT} == set(SILENT_SWALLOWS)

    def test_the_narrate_swallow_is_the_only_one_on_the_presence_pump(self) -> None:
        """The pump is the presence path; exactly one callback may go quiet."""
        on_the_pump = [h for h in SILENT if h.module == "presence_engine.py"]
        assert [h.function for h in on_the_pump] == ["PresenceEngine._narrate"]

    def test_the_loop_swallows_nothing_in_silence(self) -> None:
        """The core seam carries no sanctioned swallow at all, of either tier."""
        assert not [h for h in SILENT + ABSENT if h.module == "loop.py"]


# ── 2. the allow-lists cannot rot ─────────────────────────────────────────────


class TestTheAllowListCannotRot:
    """An entry for a handler that no longer exists is a stale permission."""

    @pytest.mark.parametrize("key", sorted(SILENT_SWALLOWS))
    def test_each_sanctioned_swallow_still_exists(self, key: tuple[str, str]) -> None:
        assert key in {h.key for h in SILENT}, f"{key} is allow-listed but no longer present"

    @pytest.mark.parametrize("key", sorted(ABSENT_BY_DESIGN))
    def test_each_sanctioned_absence_still_exists(self, key: tuple[str, str]) -> None:
        assert key in {h.key for h in ABSENT}, f"{key} is allow-listed but no longer present"

    @pytest.mark.parametrize("key", sorted(SILENT_SWALLOWS))
    def test_each_justification_is_a_real_explanation(self, key: tuple[str, str]) -> None:
        assert len(SILENT_SWALLOWS[key]) > 40

    @pytest.mark.parametrize("key", sorted(SILENT_SWALLOWS))
    def test_the_source_itself_justifies_each_swallow(self, key: tuple[str, str]) -> None:
        """The in-source marker, not just this file's opinion of it.

        ``# nosec B110`` is what tells bandit this ``try/except/pass`` is
        deliberate. Requiring it means a swallow cannot be waved through by
        editing the test alone.
        """
        module, function = key
        lines = (EMBODIMENT_ROOT / module).read_text(encoding="utf-8").splitlines()
        for handler in SILENT:
            if handler.key != key:
                continue
            line = lines[handler.lineno - 1]
            assert all(marker in line for marker in _REQUIRED_MARKERS), (
                f"{module}:{handler.lineno} swallows silently without the "
                f"required markers {_REQUIRED_MARKERS}: {line.strip()}"
            )


# ── 3. the guard's own honesty ────────────────────────────────────────────────


class TestTheGuardItself:
    """A scan nobody has tested is an assertion about nothing."""

    def test_prose_about_swallowing_is_not_a_swallow(self) -> None:
        """The false positive a substring scan would produce, refused."""
        documented = (
            '"""Nothing here is a silent except: pass.\n\n'
            "A failing sink is recorded, never swallowed.\n"
            '"""\n'
            "# except Exception: pass  <- deliberately absent\n"
            "VALUE = 'except: pass'\n"
        )
        assert scan(documented, "prose.py") == []

    def test_a_real_silent_swallow_is_caught(self) -> None:
        snippet = "def f():\n    try:\n        g()\n    except Exception:\n        pass\n"
        found = scan(snippet, "new.py")
        assert [(h.function, h.broad, h.inert, h.returns_none) for h in found] == [
            ("f", True, True, False)
        ]
        assert found[0].key not in SILENT_SWALLOWS

    @pytest.mark.parametrize(
        "clause",
        [
            "except:",
            "except Exception:",
            "except BaseException:",
            "except (ValueError, Exception):",
        ],
    )
    def test_every_broad_form_is_recognised(self, clause: str) -> None:
        found = scan(f"def f():\n    try:\n        g()\n    {clause}\n        pass\n", "b.py")
        assert found[0].broad is True and found[0].inert is True

    def test_a_narrow_inert_handler_is_not_flagged(self) -> None:
        snippet = "def f():\n    try:\n        g()\n    except (ValueError, OverflowError):\n        pass\n"  # noqa: E501
        found = scan(snippet, "n.py")
        assert found[0].broad is False and found[0].inert is True

    def test_a_recording_handler_is_not_inert(self) -> None:
        snippet = "def f():\n    try:\n        g()\n    except Exception as exc:\n        record(exc)\n"  # noqa: E501
        assert scan(snippet, "r.py")[0].inert is False

    def test_returning_a_value_is_not_inert(self) -> None:
        snippet = "def f():\n    try:\n        g()\n    except Exception:\n        return 0\n"
        assert scan(snippet, "v.py")[0].inert is False

    def test_a_reraising_handler_is_not_inert(self) -> None:
        snippet = "def f():\n    try:\n        g()\n    except Exception:\n        raise\n"
        assert scan(snippet, "x.py")[0].inert is False

    def test_a_method_is_named_with_its_class(self) -> None:
        snippet = "class C:\n    def m(self):\n        try:\n            g()\n        except Exception:\n            pass\n"  # noqa: E501
        assert scan(snippet, "c.py")[0].function == "C.m"

    def test_the_package_actually_has_handlers_to_scan(self) -> None:
        """Guard against a scan that passes because it found nothing at all."""
        assert len(HANDLERS) > 30
        assert len({h.module for h in HANDLERS}) >= 8
