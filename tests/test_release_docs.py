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
    "other tools",
    "vision",
    "a face",
    "live realtime voice",
    "speech-to-speech",
    "robot relay",
    "value",
)

#: Shipped on this branch (Access verifier, semantic recall) and so dropped from
#: the not-yet list: no item of that section may be headed by either again.
SHIPPED_NOT_LISTED = (
    "cloudflare access",
    "semantic recall",
    "phone voice",
    # 33 transcripts were in the library on 2026-10-24 and retrieval was measured.
    "transcripts in the library",
)

#: Still unmeasured at this release; the README status must say so, by name.
STILL_UNMEASURED = (
    "provisional",
    "follow-up window",
    "webm",
    "recall-gemma2",
)

#: t18's published recall measurement (t23 acted on it). The README Status
#: cites it by name and quotes the numbers it changed, with their n.
RECALL_RESULTS_FILE = "docs/live-test-results/2026-10-23-recall-gemma2.md"

#: The tools the daemon binds: ``remember`` (``d7``) and ``forget`` (``d8``),
#: plus ``search_library`` and ``read_passage`` on typed turns when a library is
#: configured (gwen-is-pocketchat t11/t13), and ``search_conversations`` over her
#: own settled conversations (deviation d8). Both surfaces must name all five, in
#: the same ``## Not yet`` section that used to say the registry was empty.
TOOLS_BOUND = (
    "remember",
    "forget",
    "search_library",
    "read_passage",
    "read_item",
    "fetch_source",
    "search_conversations",
    "list_todos",
    "set_todo_done",
)

#: The library cycle's names, which README and the explain root must both carry.
LIBRARY_TERMS = (
    "culture-library",
    "get-only",  # the catalog client is still GET-only
    "one retrieve POST",  # and the retrieve client's single request says so
    "write guard",
    "EMBODIMENT_LIBRARY_URL",
    "EMBODIMENT_LIBRARY_KEY",
    "chat-first",
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


@pytest.mark.parametrize("surface", ["readme", "explain_root"])
def test_not_yet_list_no_longer_lists_what_shipped(
    surface: str, request: pytest.FixtureRequest
) -> None:
    section = _section(request.getfixturevalue(surface), "Not yet")
    headings = [ln.lower() for ln in section.splitlines() if ln.startswith("- **")]
    assert headings, f"{surface} 'Not yet' has no items"
    for shipped in SHIPPED_NOT_LISTED:
        assert not [h for h in headings if h.startswith(f"- **{shipped}")], shipped


@pytest.mark.parametrize("surface", ["readme", "explain_root"])
def test_surface_names_both_tools_and_no_empty_registry(
    surface: str, request: pytest.FixtureRequest
) -> None:
    section = _section(request.getfixturevalue(surface), "Not yet").lower()
    for tool in TOOLS_BOUND:
        assert f"`{tool}`" in section, f"{surface} does not name the {tool} tool"
    assert "empty tool registry" not in section, "the registry is no longer empty (d7, d8)"


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


def test_readme_status_names_what_is_still_unmeasured(readme: str) -> None:
    status = _section(readme, "Status").lower()
    missing = [phrase for phrase in STILL_UNMEASURED if phrase not in status]
    assert not missing, f"README Status does not say these are unmeasured: {missing}"


def test_readme_status_cites_the_recall_measurement_it_quotes(readme: str) -> None:
    # t18's file landed; t23 applied it. Every recall number the README quotes
    # comes with the file it came from and stays marked provisional.
    status = _section(readme, "Status")
    assert RECALL_RESULTS_FILE in status
    assert "0.715" in status
    assert "n = " in status
    lowered = status.lower()
    assert "uncalibrated" not in lowered, "the threshold is calibrated, provisionally"
    assert "forthcoming" not in lowered, "the recall measurement is published"
    assert "40 s clip deadline" not in lowered, "the stt clock is measured now"


@pytest.mark.parametrize("surface", ["readme", "explain_root"])
def test_no_surface_still_calls_the_old_threshold_the_default(
    surface: str, request: pytest.FixtureRequest
) -> None:
    text = request.getfixturevalue(surface)
    assert "`0.78`" not in text
    assert "<date>-recall-gemma2" not in text


@pytest.mark.parametrize("surface", ["readme", "explain_root"])
def test_surface_describes_the_library_read_path(
    surface: str, request: pytest.FixtureRequest
) -> None:
    text = request.getfixturevalue(surface)
    lowered = text.lower()
    missing = [t for t in LIBRARY_TERMS if t.lower() not in lowered]
    assert not missing, f"{surface} lacks {missing}"


@pytest.mark.parametrize("surface", ["readme", "explain_root"])
def test_surface_points_at_the_library_measurement(
    surface: str, request: pytest.FixtureRequest
) -> None:
    text = request.getfixturevalue(surface)
    assert "2026-10-24-library.md" in text
    assert "33" in text  # the transcripts that reached the library that day


def test_readme_status_reports_library_retrieval_as_measured_once(readme: str) -> None:
    status = _section(readme, "Status").lower()
    assert "measured once" in status
    assert "19 of 32" in status
    assert "no longer exists" in status
    assert "unmeasured" in status
    assert "value is unmeasured" in status


# --- embodiment#107 (0.23.0): the library is queried, not copied -------------

PROBE_DAY = "2026-10-26"
READING_TERMS = ("/api/reading", "reading-positions.json", "/api/rag/retrieve")


@pytest.mark.parametrize("surface", ["readme", "explain_root"])
def test_surface_describes_retrieve_retired_index_and_read_aloud(
    surface: str, request: pytest.FixtureRequest
) -> None:
    text = request.getfixturevalue(surface)
    missing = [t for t in READING_TERMS if t not in text]
    assert not missing, f"{surface} lacks {missing}"
    lowered = text.lower()
    assert "no switch back" in lowered
    assert PROBE_DAY in text, "measured claims cite the 2026-10-26 probes"
    assert "not yet done" in lowered and "(t9)" in lowered, "live acceptance is not claimed"


def test_readme_status_does_not_claim_the_old_index_or_threshold_for_the_library(
    readme: str,
) -> None:
    status = _section(readme, "Status")
    assert "uncalibrated for library text" not in status
    assert "not calibrated on library text" not in status
    assert "no similarity floor" in status


def test_claude_md_code_map_has_no_live_sync_and_says_the_index_is_unused() -> None:
    claude = (_REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "LibrarySync: bounded ticks" not in claude
    assert "reading-positions.json" in claude
    assert "retrieve.py" in claude
    assert "not yet deleted" in claude
    assert "uncalibrated for library text" not in claude
    assert not (_REPO_ROOT / "embodiment" / "library" / "sync.py").exists()


def test_changelog_names_the_release_removal_and_its_rollback() -> None:
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("## [0.23.0]")
    section = changelog[start : changelog.index("\n## [", start + 1)].lower()
    assert "removes the local library index" in section
    assert "deletes its file once" in section
    assert "no switch back" in section
    assert "previous release" in section
    assert "not yet done" in section and "t9" in section


def test_version_is_0_23_0() -> None:
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.23.0"' in pyproject
