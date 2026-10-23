"""The ingest contract: registry, whole-batch validation, stamping, stored ids.

Synthetic data only — the repo is public.
"""

from __future__ import annotations

import json

import pytest

from embodiment.cli._errors import CliError
from embodiment.ingest import contract as c
from embodiment.memory import HEADER_FIELD_LIMIT, HEADER_LABEL_CHARSET


def pocket(n=1, **over):
    rec = {
        "id": f"pocket:rec1:fact:{n}",
        "text": "The synthetic standup moved to Tuesday.",
        "type": "fact",
        "metadata": {
            "source": "pocket",
            "recording_id": "rec1",
            "title": "Synthetic standup",
            "date": "2026-01-02",
        },
    }
    rec.update(over)
    return rec


def paper(n=1, **over):
    rec = {
        "id": f"newspaper:2026-01-02:tech:{n}",
        "text": "A synthetic headline.",
        "type": "item",
        "metadata": {
            "source": "newspaper",
            "issue_date": "2026-01-02",
            "section": "tech",
            "url": "https://example.invalid/a",
        },
    }
    rec.update(over)
    return rec


def lines(*recs):
    return [json.dumps(r) for r in recs]


def test_registry_holds_exactly_pocket_and_newspaper():
    assert set(c.REGISTRY) == {"pocket", "newspaper"}
    for spec in c.REGISTRY.values():
        assert spec.types and spec.required and spec.id_prefix == spec.name
        assert spec.max_text_chars > 0 and spec.max_records > 0


def test_unknown_source_refused_with_clierror_naming_registry():
    err = c.source_refusal("slack")
    assert isinstance(err, CliError) and err.code == 1
    assert "registry" in err.message and "pocket" in err.remediation
    assert c.source_refusal("pocket") is None
    assert c.source_refusal(None) is not None
    r = c.validate_batch(lines(pocket(metadata={"source": "slack"})))
    assert isinstance(r, c.Refusal) and r.line == 1
    assert isinstance(r.error, CliError) and "registry" in r.error.message
    assert "pocket" in r.error.remediation


def test_valid_batches_are_returned_whole():
    r = c.validate_batch(lines(pocket(1), pocket(2)))
    assert isinstance(r, c.Batch) and r.source == "pocket" and len(r.records) == 2
    n = c.validate_batch(lines(paper(1)), created="2026-01-02")
    assert isinstance(n, c.Batch) and n.records[0]["created"] == "2026-01-02"


def test_one_bad_line_refuses_everything_with_first_failing_line():
    batch = lines(pocket(1), pocket(2), pocket(3, type="bogus"), pocket(4, text=""))
    r = c.validate_batch(batch)
    assert isinstance(r, c.Refusal) and r.line == 3
    # blank lines count toward the line number
    r = c.validate_batch([json.dumps(pocket(1)), "", "{not json"])
    assert isinstance(r, c.Refusal) and r.line == 3


@pytest.mark.parametrize(
    "bad",
    [
        pocket(type="item"),
        pocket(text="x" * (c.REGISTRY["pocket"].max_text_chars + 1)),
        pocket(id="newspaper:2026-01-02:tech:1"),
        pocket(metadata={"source": "pocket", "title": "t", "date": "d"}),
        pocket(metadata="nope"),
        pocket(supersedes="pocket:a.b:fact:1"),
    ],
)
def test_bad_records_refused(bad):
    assert isinstance(c.validate_batch(lines(bad)), c.Refusal)


def test_non_object_and_non_text_lines_degrade_not_raise():
    assert isinstance(c.validate_batch(["[1]"]), c.Refusal)
    assert isinstance(c.validate_batch([b"x"]), c.Refusal)
    assert isinstance(c.validate_batch(5), c.Refusal)
    assert isinstance(c.validate_batch([]), c.Refusal)
    assert isinstance(c.validate_batch(["[" * 100000]), c.Refusal)


def test_mixed_sources_and_duplicates_refused():
    r = c.validate_batch(lines(pocket(1), paper(1)))
    assert isinstance(r, c.Refusal) and r.line == 2
    r = c.validate_batch(lines(pocket(1), pocket(1)))
    assert isinstance(r, c.Refusal) and r.line == 2


def test_batch_cap_is_exact_cap_plus_one_refused():
    cap = c.REGISTRY["pocket"].max_records
    ok = c.validate_batch(lines(*[pocket(i) for i in range(1, cap + 1)]))
    assert isinstance(ok, c.Batch) and len(ok.records) == cap
    over = c.validate_batch(lines(*[pocket(i) for i in range(1, cap + 2)]))
    assert isinstance(over, c.Refusal) and over.line == cap + 1


def test_stamping_overrides_whatever_the_input_says():
    hostile = pocket(
        visibility="public", scope="gwen", added_by="someone-else", created="1999-01-01"
    )
    hostile["metadata"].update(visibility="public", added_by="x", scope="gwen")
    r = c.validate_batch(lines(hostile))
    rec = r.records[0]
    assert rec["visibility"] == "private"
    assert rec["scope"] == c.INGEST_SCOPE
    assert rec["added_by"] == "culture-rules"
    assert "created" not in rec  # the file does not choose it either
    assert set(rec["metadata"]) <= {"source", "wire_id", "recording_id", "title", "date"}


def test_wire_id_maps_into_header_charset_and_is_stable():
    wire = "pocket:rec1:fact:3"
    sid = c.stored_id(wire)
    assert sid == "pocket.rec1.fact.3"
    assert set(sid) <= HEADER_LABEL_CHARSET and len(sid) <= HEADER_FIELD_LIMIT
    assert c.stored_id(wire) == sid
    rec = c.validate_batch(lines(pocket(3))).records[0]
    assert rec["id"] == sid and rec["metadata"]["wire_id"] == wire


@pytest.mark.parametrize(
    "wire",
    [
        "pocket:rec 1:fact:1",
        "pocket:rec1:fact",
        "pocket:rec1:fact:x",
        "pocket::fact:1",
        "other:rec1:fact:1",
        "pocket:rec<1>:fact:1",
        "pocket:a.b:fact:1",
        "pocket:" + "r" * 80 + ":fact:1",
        None,
        7,
    ],
)
def test_unmappable_wire_ids_are_rejected(wire):
    assert c.stored_id(wire) is None


def test_inputs_that_would_collide_after_mapping_are_rejected():
    # 'a.b' and 'a:b' would both become 'a.b' were '.' allowed in a wire segment.
    assert c.stored_id("pocket:a.b:fact:1") is None
    assert c.stored_id("pocket:a:fact:1") != c.stored_id("pocket:b:fact:1")
