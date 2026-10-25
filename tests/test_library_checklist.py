"""library/checklist.py: culture-rules' rendered to-do item read into lines (#105 t2)."""

from __future__ import annotations

from datetime import datetime, timezone

from embodiment.daemon.todos import valid_todo_id
from embodiment.library.checklist import parse_checklist, parse_group, recording_id_of

REC = "5e7a1c00-0000-4000-8000-00000000c0de"
DONE_AT = "2026-10-24T09:30:00+00:00"
DOC = f"""# 2026-10-24 Planning the garden

Some prose that mentions - [ ] a box but is not one.

## Open
- [ ] Buy seeds &amp; soil <!-- todo:pocket.{REC}.1 -->
- [x] Call the plumber <!-- todo:pocket.{REC}.2 done_at={DONE_AT} by=operator -->
- [ ] A line with no id
- [x] Done, no id <!-- note -->
- [x] Bad stamp <!-- todo:pocket.{REC}.3 done_at=yesterday by=gwen -->
"""


def test_one_line_per_id_comment_and_noise_ignored():
    lines = parse_checklist(DOC)
    assert [line.id for line in lines] == [f"pocket.{REC}.{n}" for n in (1, 2, 3)]
    assert [line.done for line in lines] == [False, True, True]
    assert lines[0].text == "Buy seeds &amp; soil"


def test_done_at_epoch_and_by_kept():
    done = parse_checklist(DOC)[1]
    assert done.done_at == datetime(2026, 10, 24, 9, 30, tzinfo=timezone.utc).timestamp()
    assert done.by == "operator"


def test_malformed_done_at_leaves_done_with_none():
    bad = parse_checklist(DOC)[2]
    assert bad.done is True and bad.done_at is None and bad.by == "gwen"
    assert parse_checklist(f"- [ ] x <!-- todo:pocket.{REC}.1 -->")[0].done_at is None


def test_clean_applied_once_per_text():
    seen = []

    def clean(text):
        seen.append(text)
        return text.upper()

    lines = parse_checklist(DOC, clean)
    assert lines[1].text == "CALL THE PLUMBER"
    assert len(seen) == 3


def test_recording_id_of():
    assert recording_id_of(f"pocket.{REC}.1") == REC
    for bad in ("other." + REC + ".1", f"pocket.{REC}", f"pocket.{REC}.0", "pocket.x.1", "", None):
        assert recording_id_of(bad) is None
    assert valid_todo_id(f"pocket.{REC}.1")


def test_group_title_date_recording():
    group = parse_group("2026-10-24 Planning the garden", DOC)
    assert group.recording_id == REC
    assert group.date == "2026-10-24"
    assert group.title == "2026-10-24 Planning the garden"
    assert len(group.items) == 3
    empty = parse_group("Untitled", "nothing here")
    assert empty.recording_id == "" and empty.date == "" and empty.items == ()
