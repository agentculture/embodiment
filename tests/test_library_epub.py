"""EPUB extraction: bounded stdlib text + passage anchors (t2). Fixtures are synthetic."""

from __future__ import annotations

import io
import zipfile

import pytest

from embodiment.library.epub import EpubRefused, Extracted, Limits, Passage, extract

CONTAINER = (
    '<?xml version="1.0"?><container><rootfiles>'
    '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
    "</rootfiles></container>"
)
OPF = (
    '<?xml version="1.0"?><package><manifest>'
    '<item id="b" href="two.xhtml" media-type="application/xhtml+xml"/>'
    '<item id="a" href="one.xhtml" media-type="application/xhtml+xml"/>'
    '<item id="css" href="s.css" media-type="text/css"/>'
    '</manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>'
)
ONE = (
    "<html><head><title>T</title><style>p{color:red}</style></head><body>"
    "<script>var leak = 1;</script>"
    "<h2>Alpha</h2><p>First para.</p><p>Second   para.</p>"
    "<h2>Beta</h2><p>Third para.</p></body></html>"
)
TWO = "<html><body><p>Later doc.</p></body></html>"


def build(files: dict[str, str | bytes], *, container: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if container:
            z.writestr("META-INF/container.xml", CONTAINER)
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


def basic(**over) -> bytes:
    files = {"OEBPS/content.opf": OPF, "OEBPS/one.xhtml": ONE, "OEBPS/two.xhtml": TWO}
    files.update(over)
    return build(files)


LIM = Limits()


def test_spine_order_and_shape():
    out = extract(basic(), limits=LIM)
    assert isinstance(out, Extracted)
    texts = [p.text for p in out.passages]
    assert texts == ["First para.", "Second para.", "Third para.", "Later doc."]
    assert all(isinstance(p, Passage) for p in out.passages)
    assert out.chars == sum(len(t) for t in texts)


def test_headings_attach_and_scripts_dropped():
    out = extract(basic(), limits=LIM)
    assert [p.heading for p in out.passages] == ["Alpha", "Alpha", "Beta", ""]
    assert not any("leak" in p.text or "color" in p.text for p in out.passages)


def test_anchors_stable_and_unique():
    a = extract(basic(), limits=LIM)
    b = extract(basic(), limits=LIM)
    assert [p.anchor for p in a.passages] == [p.anchor for p in b.passages]
    anchors = [p.anchor for p in a.passages]
    assert len(set(anchors)) == len(anchors)
    assert all(x.startswith("OEBPS/") and "#" in x for x in anchors)


def test_timing_attributes_and_speaker():
    html = (
        '<body><p data-start="1.5" data-end="4" data-speaker="Dana">Hello there.</p>'
        "<p>Plain.</p></body>"
    )
    p = extract(basic(**{"OEBPS/one.xhtml": html}), limits=LIM).passages
    assert (p[0].start_s, p[0].end_s, p[0].speaker, p[0].text) == (1.5, 4.0, "Dana", "Hello there.")
    assert (p[1].start_s, p[1].end_s, p[1].speaker) == (None, None, None)


def test_leading_token_timing_and_label():
    html = "<body><p>[00:01:05] Dana: Hi.</p><p>2:30 Lee: Yo.</p><p>Note: not timed</p></body>"
    p = extract(basic(**{"OEBPS/one.xhtml": html}), limits=LIM).passages
    assert (p[0].start_s, p[0].speaker, p[0].text) == (65.0, "Dana", "Hi.")
    assert (p[1].start_s, p[1].speaker, p[1].text) == (150.0, "Lee", "Yo.")
    assert (p[2].start_s, p[2].speaker, p[2].text) == (None, None, "Note: not timed")


def refused(data: bytes, limits: Limits) -> str:
    with pytest.raises(EpubRefused) as ei:
        extract(data, limits=limits)
    return ei.value.code


def test_too_many_members():
    assert refused(basic(), Limits(max_members=3)) == "too_many_members"


def test_oversized_member():
    big = "<body><p>" + "x" * 5000 + "</p></body>"
    assert refused(basic(**{"OEBPS/one.xhtml": big}), Limits(max_member_bytes=2000)) == (
        "member_too_large"
    )


def test_zip_bomb_total():
    bomb = "<body><p>" + "a" * 400_000 + "</p></body>"
    data = basic(**{"OEBPS/one.xhtml": bomb, "OEBPS/two.xhtml": bomb})
    assert len(data) < 5000
    assert refused(data, Limits(max_member_bytes=500_000, max_total_bytes=600_000)) == (
        "total_too_large"
    )


def test_char_cap():
    assert refused(basic(), Limits(max_chars=10)) == "too_many_chars"


def test_not_a_zip_and_missing_container():
    assert refused(b"nope", LIM) == "bad_epub"
    assert refused(build({"x": "y"}, container=False), LIM) == "bad_epub"


def test_extract_is_the_one_argument_seam_the_daemon_hands_the_sync():
    """Found live 2026-10-24: the daemon passes epub.extract to LibrarySync, which calls
    extract(data); with a required ``limits`` every real item failed (extract_failed)."""
    from embodiment.daemon import app as daemon_app
    from embodiment.library import epub as epub_mod

    out = epub_mod.extract(basic())  # no limits: the defaults apply
    assert out.passages
    import inspect

    default = inspect.signature(daemon_app.build_library_lane).parameters["extract"].default
    assert default(basic()).passages  # the daemon's own default, called as sync calls it
