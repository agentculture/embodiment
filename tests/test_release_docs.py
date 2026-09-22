"""Release docs for the first daemon release — plan task t22.

The three acceptance criteria, verbatim:

1. a test asserts the ``explain`` root text and the README contain the
   software-presence boundary and the not-yet list
2. README's status section says the daemon's usefulness is unmeasured and that
   acceptance was on one rig, citing t21's result file
3. ``markdownlint-cli2``, the rubric gate and ``version-check`` pass

Criterion 3 is CI's, not this file's. Criteria 1 and 2 are pinned here against
the REAL ``README.md`` and the REAL ``explain`` root entry, so a later edit
that softens either surface goes red instead of drifting. The not-yet list is
one tuple below, shared by both assertions: the README and ``explain`` are two
renderings of one boundary and must not disagree about what is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from embodiment.cli import main
from embodiment.explain.catalog import ENTRIES

_REPO_ROOT = Path(__file__).resolve().parent.parent
_README_PATH = _REPO_ROOT / "README.md"

#: The one sentence both surfaces must carry, verbatim: this package is
#: software presence, and the body belongs to ``reachy-mini-cli``.
BOUNDARY_SENTENCE = "software presence, not a body"

#: The sibling whose name this package must not overclaim, and the layer of
#: its that collides with this package's name (constraint C2).
NAME_COLLISION = ("reachy-mini-cli", "agent embody")

#: What v1 does NOT ship. Each phrase must appear, lower-cased, in the
#: ``## Not yet`` section of both surfaces.
NOT_YET = (
    "tools",
    "vision",
    "a face",
    "phone voice",
    "robot relay",
    "cloudflare access",
    "semantic recall",
    "value",
)

#: Where t21 — the human acceptance run — publishes latency as measured.
T21_RESULT_FILE = "docs/live-test-results/2026-09-22-t21-acceptance.md"


def _section(text: str, heading: str) -> str:
    """The body of the ``## <heading>`` section of *text*, up to the next ``## ``."""
    start = text.index(f"## {heading}")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


@pytest.fixture(scope="module")
def readme() -> str:
    return _README_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def explain_root() -> str:
    return ENTRIES[()]


def test_explain_prints_the_catalog_root_entry(capsys: pytest.CaptureFixture[str]) -> None:
    # Guard the fixture above: what ``embodiment explain`` prints IS the root
    # entry, so the assertions below are about the shipped text.
    rc = main(["explain"])
    assert rc == 0
    assert ENTRIES[()].strip() in capsys.readouterr().out


# --- criterion 1: boundary + not-yet list, on both surfaces -----------------


@pytest.mark.parametrize("surface", ["readme", "explain_root"])
def test_surface_states_software_presence_boundary(
    surface: str, request: pytest.FixtureRequest
) -> None:
    text = request.getfixturevalue(surface).lower()
    assert BOUNDARY_SENTENCE in text
    for phrase in NAME_COLLISION:
        assert phrase in text, f"{surface} does not name the {phrase!r} collision"


@pytest.mark.parametrize("surface", ["readme", "explain_root"])
def test_surface_has_the_not_yet_list(surface: str, request: pytest.FixtureRequest) -> None:
    text = request.getfixturevalue(surface)
    section = _section(text, "Not yet").lower()
    missing = [phrase for phrase in NOT_YET if phrase not in section]
    assert not missing, f"{surface} 'Not yet' section lacks {missing}"


# --- criterion 2: README status — unmeasured, one rig, t21's result file ----


def test_readme_status_says_unmeasured_on_one_rig_and_cites_t21(readme: str) -> None:
    status = _section(readme, "Status").lower()
    assert "unmeasured" in status
    assert "one rig" in status
    assert T21_RESULT_FILE in status


def test_readme_status_does_not_claim_the_app_is_unbuilt(readme: str) -> None:
    # The 0.15.0 README said "planned, not built". That sentence is now false
    # and must not survive into the daemon release.
    status = _section(readme, "Status").lower()
    assert "planned, not built" not in status
