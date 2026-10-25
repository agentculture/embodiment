"""The Markdown extractor: the epub extractor's shape from a format=md item (synthetic)."""

import pytest

from embodiment.library import markdown
from embodiment.library.epub import EpubRefused

ITEM = """# 2026-10-24 Warehouse call

A short call about the [shipping plan](https://example.com/plan) and nothing else.

## Key facts

- The pallets leave on Monday
- Budget is unchanged <!-- note: internal -->

## Transcript

<div dir="rtl">
first line of talk &amp; more
second line
</div>

another paragraph
"""


def test_structure_headings_anchors_links_in_order():
    ex = markdown.extract(ITEM.encode())
    rows = [(p.anchor, p.heading, p.text) for p in ex.passages]
    assert rows == [
        (
            "md#0",
            "2026-10-24 Warehouse call",
            "A short call about the shipping plan and nothing else.",
        ),
        ("md#1", "Key facts", "The pallets leave on Monday"),
        ("md#2", "Key facts", "Budget is unchanged"),
        ("md#3", "Transcript", "first line of talk & more second line"),
        ("md#4", "Transcript", "another paragraph"),
    ]
    assert ex.passages[0].links == (("shipping plan", "https://example.com/plan"),)
    assert ex.chars == sum(len(p.text) for p in ex.passages)


def test_tags_comments_entities():
    ex = markdown.extract(b'# T\n\n<div dir="rtl">a &lt;b&gt; &amp; c</div>\n<!--\nhidden\n-->\nz')
    assert [p.text for p in ex.passages] == ["a <b> & c", "z"]


def test_todo_lines():
    ex = markdown.extract(
        b"# Todos\n- [ ] call Dana <!-- todo:pocket.u.1 -->\n- [x] pay <!-- todo:p.2 done_at=1 -->"
    )
    assert [p.text for p in ex.passages] == ["[ ] call Dana", "[x] pay"]


def test_caps_cut_not_raise():
    lim = markdown.Limits(max_line_chars=50, max_lines=5, max_chars=80, max_passage_chars=40)
    ex = markdown.extract((("x" * 10_000 + "\n") * 10).encode(), limits=lim)
    assert ex.passages and all(len(p.text) <= 40 for p in ex.passages)
    assert ex.chars <= 80
    one = markdown.extract(("y" * 10_000).encode())
    assert len(one.passages[0].text) == markdown.Limits().max_line_chars
    many = markdown.extract(("w\n\n" * 50).encode(), limits=markdown.Limits(max_lines=10))
    assert len(many.passages) <= 5


@pytest.mark.parametrize("bad", [b"", b"  \n\n", b"\xff\xfe\x00bad", b"<!-- only -->"[:0]])
def test_refusals(bad):
    with pytest.raises(EpubRefused) as exc:
        markdown.extract(bad)
    assert exc.value.code == "bad_markdown"


def test_clean_text_is_one_plain_bounded_line():
    raw = '<div dir="rtl">call &amp; <b>book</b> the [vet](https://example.com/v)</div><!-- x -->'
    assert markdown.clean_text(raw) == "call & book the vet"
    assert markdown.clean_text("  a \t b  ") == "a b"
    assert markdown.clean_text("![pic](https://example.com/p.png) left") == "left"
    assert len(markdown.clean_text("x" * 10_000)) == markdown.Limits().max_line_chars
    small = markdown.Limits(max_line_chars=5)
    assert markdown.clean_text("abcdefgh", limits=small) == "abcde"
    assert markdown.clean_text(None) == ""  # type: ignore[arg-type]
