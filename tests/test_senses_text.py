"""Tests for :mod:`embodiment.senses_text` (task t11).

Four things this module has to be, and each gets its own test:

1. **Present and exported**, on the module and at the package root, the same
   way every other composable prompt constant (:data:`~embodiment.scope.
   SCOPE_AUTHORITY`, :data:`~embodiment.muse.MUSE_AUTHORITY`) is reachable.
2. **Verbatim against the measurement.** :data:`~embodiment.senses_text.
   SENSES_GROUNDING` is checked directly against the reproducible probe script
   that produced the 0/16-vs-16/16 result
   (``docs/live-test-results/senses-grounding-probe.py``), not against a
   second hand-typed copy of the sentence that could itself be wrong.
3. **Documented as required, not advisory** (the #63 recommendation) — the
   module docstring is checked for that framing rather than trusted by eye.
4. **On the right side of the embodiment-frames-cortex-not-senses boundary**
   (README "Scope: embodiment frames cortex, not senses", colleague#352,
   confirmed claim ``c30``): this module ships text, never a function that
   frames a senses role. An AST walk asserts no function or class is defined
   here at all — only data.
"""

from __future__ import annotations

import ast
from pathlib import Path

import embodiment
from embodiment import senses_text
from embodiment.senses_text import SENSES_GROUNDING

_PROBE_SRC = (
    Path(__file__).resolve().parents[1] / "docs" / "live-test-results" / "senses-grounding-probe.py"
)


def _probe_voice() -> str:
    """The measured ``VOICE`` constant, read straight out of the probe script."""
    tree = ast.parse(_PROBE_SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "VOICE" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, str)
            return value
    raise AssertionError("VOICE not found in senses-grounding-probe.py")


# ── 1. present and exported ─────────────────────────────────────────────────


def test_senses_grounding_is_a_nonempty_string() -> None:
    assert isinstance(SENSES_GROUNDING, str)
    assert SENSES_GROUNDING.strip() == SENSES_GROUNDING
    assert SENSES_GROUNDING


def test_senses_text_is_a_curated_submodule() -> None:
    """``from embodiment import senses_text`` resolves without hoisting internals."""
    assert "senses_text" in embodiment._SUBMODULES
    assert senses_text.SENSES_GROUNDING == SENSES_GROUNDING


def test_senses_grounding_exported_from_package_root() -> None:
    """The constant is reachable as ``embodiment.SENSES_GROUNDING`` (PEP 562)."""
    assert embodiment.SENSES_GROUNDING is senses_text.SENSES_GROUNDING
    assert "SENSES_GROUNDING" in embodiment.__all__
    assert embodiment._LAZY_NAMES["SENSES_GROUNDING"] == "senses_text"


def test_senses_grounding_module_declares_all() -> None:
    assert senses_text.__all__ == ["SENSES_GROUNDING"]


# ── 2. verbatim against the measurement ─────────────────────────────────────


def test_senses_grounding_matches_the_measured_probe_verbatim() -> None:
    """The shipped constant is the exact tail of the measured ``VOICE`` prompt.

    ``docs/live-test-results/senses-grounding.md`` measured 16/16 abstention
    with this clause present and 16/16 fabrication with it removed — F1, "as
    clean an isolation as this repo has measured." The clause must therefore
    ship unchanged, not paraphrased or re-punctuated.
    """
    voice = _probe_voice()
    assert voice.endswith(SENSES_GROUNDING)


def test_senses_grounding_exact_text() -> None:
    """Pinned so a future edit to this file has to change this test too.

    This is intentionally a literal string, not a derived one: it is the
    second, independent statement of what F1 measured, so a corruption of
    either this constant or the extraction in the test above is still caught.
    """
    assert SENSES_GROUNDING == "You can see only the status block you are given."


def test_senses_grounding_is_not_the_whole_prompt() -> None:
    """It is a clause a host appends, not a complete voice of its own."""
    voice = _probe_voice()
    assert voice != SENSES_GROUNDING
    assert len(voice) > len(SENSES_GROUNDING)


# ── 3. documented as required, not advisory ─────────────────────────────────


def test_module_docstring_states_required_not_advisory() -> None:
    doc = senses_text.__doc__ or ""
    assert "Required, not advisory" in doc
    assert "issue #63" in doc or "#63" in doc
    assert "16 of 16" in doc or "16/16" in doc


def test_constant_comment_states_required() -> None:
    """The ``#:`` doc-comment immediately above the constant also says so.

    A reader who jumps straight to the constant (skipping the module
    docstring) still has to be told this is required, not optional.
    """
    src = Path(senses_text.__file__).read_text()
    # The comment block immediately preceding the assignment.
    marker = "SENSES_GROUNDING = "
    idx = src.index(marker)
    preceding = src[:idx]
    comment_lines = []
    for line in reversed(preceding.splitlines()):
        stripped = line.strip()
        if stripped.startswith("#:") or stripped.startswith("#"):
            comment_lines.insert(0, stripped)
            continue
        break
    comment = "\n".join(comment_lines)
    assert "REQUIRED" in comment
    assert "not advisory" in comment


# ── 4. the embodiment-frames-cortex-not-senses boundary ────────────────────


def test_module_defines_no_function_or_class() -> None:
    """Data only. A ``frame_senses()``-shaped function would cross the boundary
    this module's docstring states — see README "Scope: embodiment frames
    cortex, not senses" and confirmed claim ``c30``.
    """
    src = Path(senses_text.__file__).read_text()
    tree = ast.parse(src)
    defs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert defs == []


def test_module_docstring_names_the_boundary() -> None:
    doc = senses_text.__doc__ or ""
    assert "not a role embodiment frames" in doc
    assert "colleague#352" in doc


def test_module_imports_nothing_from_colleague_or_loop_seams() -> None:
    """This module composes no seam and drives nothing — it is pure data.

    A regression that starts importing :mod:`embodiment.loop`,
    :mod:`embodiment.scope` or anything colleague-shaped here would mean the
    module stopped being "text a host composes" and started being a seam.
    """
    tree = ast.parse(Path(senses_text.__file__).read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert imported == [] or all(
        not name.startswith("embodiment.") and name != "embodiment" for name in imported
    )
