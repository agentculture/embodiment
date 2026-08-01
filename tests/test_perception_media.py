"""Task t18 — a perception route's rendered parts actually reach the wire.

Why this file exists
--------------------
``examples/arch_league.py`` (t6) gave ``Perception`` a ``parts`` field and
``examples/arch_league_maps.py`` (t10) filled it with rendered PNG bytes. Then
**nothing consumed it**. An image cell rendered its map, hashed it, passed the
twin check, committed the PNG — and dialled the model with text only, reporting
an image result that was really a text result, with every guard passing.

That is the repo's most-recorded failure class: *the mechanism was right and
the verification was the defect*. So every assertion here is made on the
**payload that would go on the wire** — the body ``ArchSeam._transport`` is
handed, or the message list a scripted mind is handed — and never on the
route's own ``Perception`` record. Asserting on the record is precisely what
let the defect exist: ``perception.parts`` was non-empty the whole time.

Three criteria, three classes:

* :class:`TestAnImageRouteReachesTheWire` — criterion 1, the vacuity
  assertion (``docs/plans/next-cycle-candidates.md`` M2): the dialled body
  carries an ``image_url`` part whose bytes are the rendered map's, built
  through :mod:`embodiment.media`. :meth:`TestAnImageRouteReachesTheWire.
  test_the_same_extraction_finds_nothing_when_a_route_renders_no_parts` is the
  provocation in executable form — it drives an otherwise identical route whose
  ``parts`` are empty through the *same* extractor and shows it comes back
  empty, so the criterion-1 assertion is known to bite.
* :class:`TestTheTextRouteIsUnchanged` — criterion 2. A text round's body is a
  plain ``str`` equal to ``round_instruction``, its task carries no
  ``attachments`` key, and no staging directory is ever created.
* :class:`TestUnbuildablePartsRefuseTheCell` — criterion 3 (constraint C3).
  Parts that cannot be built raise **before** any mind is dialled, the refusal
  lands in the artifact, and ``analyse`` then reports the cell ``ABSENT``.

:class:`TestBytesMeetMediasFileValidation` holds the seam itself honest: bytes
in memory reach :func:`embodiment.media.build_part` only by way of
:func:`embodiment.media.validate_attachment`, so the size cap and the
media-type rules stay ``embodiment.media``'s and are not re-implemented here.

Fully hermetic: no socket, no live model, no live arena. The one arena used is
``tests/fake_cleague.py``.
"""

from __future__ import annotations

import ast
import copy
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from embodiment import loop, media  # noqa: E402
from embodiment.contract import ModelResponse  # noqa: E402
from examples import arch_arms as aa  # noqa: E402
from examples import arch_league as al  # noqa: E402
from examples import arch_league_maps as alm  # noqa: E402
from examples import league_commander as lc  # noqa: E402
from examples import map_render as mr  # noqa: E402
from examples import perception_media as pm  # noqa: E402
from examples import worker_seam as ws  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_CLEAGUE = REPO_ROOT / "tests" / "fake_cleague.py"
FAKE_BIN = f"{sys.executable} {FAKE_CLEAGUE}"

#: A one-frame GIF. Enough bytes for a real container header; the seam never
#: decodes it, so the pixels are irrelevant — only the signature is read.
GIF_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)

#: Bytes matching no container signature this seam knows.
JUNK_BYTES = b"\x00\x01\x02\x03 not any container"


# ── briefings and route stand-ins ────────────────────────────────────────────


def rich_briefing(
    *,
    unit_id: str = "bluee-u1",
    role: str = "defender",
    team_id: str = "bluee",
    game_time: int = 4,
    options: int = 3,
    match_id: str = "cm-t18",
) -> dict[str, Any]:
    """A briefing carrying both what ``unit_brief`` needs and what a map can draw."""
    board = {
        "match_id": match_id,
        "clock": game_time,
        "width": 24000,
        "height": 16000,
        "teams": [
            {"id": "bluee", "name": "Blue Watch", "resources": 7},
            {"id": "redd", "name": "Red Company", "resources": 4},
        ],
        "units": [
            {
                "id": "bluee-u1",
                "team_id": "bluee",
                "role": "defender",
                "pos": {"x": 3500, "y": 5000},
                "carrying": 0,
                "alive": True,
            },
            {
                "id": "bluee-u2",
                "team_id": "bluee",
                "role": "harvester",
                "pos": {"x": 7000, "y": 9000},
                "carrying": 2,
                "alive": True,
            },
        ],
        "control_points": [
            {"id": "cp-west", "pos": {"x": 5000, "y": 6500}, "owner": "bluee", "takers": []}
        ],
        "resource_nodes": [{"id": "rn-1", "pos": {"x": 6500, "y": 11000}, "remaining": 12}],
        "missions": [],
    }
    return {
        "game_time": game_time,
        "you": {
            "unit_id": unit_id,
            "team_id": team_id,
            "role": role,
            "pos": {"x": 3500, "y": 5000},
            "carrying": 0,
        },
        "menu": [
            {"kind": "move", "target": f"p{i}", "duration": i + 1, "completion_time": 5 + i}
            for i in range(options)
        ],
        "outlook": [],
        "messages": [],
        "board": board,
    }


@contextmanager
def route_yielding(route_id: str, parts: Any) -> Iterator[str]:
    """Register a route that describes exactly like the text one but hands *parts*.

    A stand-in rather than the real image route wherever the test is about the
    seam and not about maps: the twin rule is t10's and re-exercising it here
    would measure ``arch_league_maps`` instead of this wiring. Registered and
    popped in a ``finally``, per t6's own precedent for this shared registry.
    """

    def describe(brief: Any, team_id: str = "") -> al.Perception:
        base = al.describe_text(brief, team_id)
        rendered = parts(brief) if callable(parts) else parts
        return al.Perception(
            route=route_id, text=base.text, parts=tuple(rendered), snapshot_hash="t18-stand-in"
        )

    al.register_route(al.PerceptionRoute(id=route_id, why="a t18 stand-in", describe=describe))
    try:
        yield route_id
    finally:
        al.ROUTE_REGISTRY.pop(route_id, None)


def config() -> aa.ArchConfig:
    return aa.load_config()


# ── a seam that captures the BODY, not the record ────────────────────────────


def payload_from(reply: ModelResponse) -> dict[str, Any]:
    """Re-encode a scripted :class:`ModelResponse` as an OpenAI completion.

    So the round trip that ``ArchSeam`` performs is exercised for real: the
    seam shapes the body, this stands in for the server, and ``parse_completion``
    reads the answer back. Only the socket is missing.
    """
    return {
        "choices": [
            {
                "finish_reason": "tool_calls" if reply.tool_calls else "stop",
                "message": {
                    "content": reply.content,
                    "reasoning_content": reply.reasoning,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in reply.tool_calls
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 5},
    }


class WireSeams:
    """Real :class:`~examples.arch_arms.ArchSeam` objects with the socket removed.

    ``_transport`` is the seam's own round-trip hook — t5's test file patches the
    same one, deliberately, so the body-shaping step stays *inside* the code
    under test. Every body handed to it is kept, which makes ``bodies[0]`` the
    payload that would have gone on the wire.
    """

    live = False

    def __init__(
        self,
        arm: aa.Arm,
        *,
        cfg: aa.ArchConfig,
        log: aa.CallLog,
        refuse_media: bool = False,
    ) -> None:
        self.arm = arm
        self.config = cfg
        self.log = log
        self.bodies: list[dict[str, Any]] = []
        self.refuse_media = refuse_media
        self._commander = al.scripted_commander(arm)

    def model_for(self, role: str) -> str:
        return f"wire_{role}"

    def _answer(self, role: str, body: dict[str, Any]) -> dict[str, Any]:
        messages = body["messages"]
        if self.refuse_media and media_parts(body):
            # Verbatim from the live probe against the served 27B, which is what
            # `embodiment.context.is_media_rejection` was written against.
            raise ws.WorkerTransportError(
                "HTTP 400: At most 0 image(s) may be provided in one prompt"
            )
        if role == aa.ROLE_WORKER and self.arm.delegates:
            return payload_from(al.scripted_unit(messages))
        return payload_from(self._commander(messages))

    def build(
        self, role: str, ctx: aa.CallContext, tools: Optional[list[dict[str, Any]]]
    ) -> aa.ArchSeam:
        sampling = self.config.sampling_for(ctx.arm, role)
        seam = aa.ArchSeam(
            dial=aa.Dial(
                role=role,
                model=self.model_for(role),
                base_url="http://127.0.0.1:1/v1",
                api_key="not-a-real-key",
            ),
            sampling=sampling,
            wire_extra=self.config.wire_extra(sampling.thinking),
            role=role,
            ctx=ctx,
            log=self.log,
            tools=tools,
        )

        def transport(body: dict[str, Any]) -> dict[str, Any]:
            self.bodies.append(copy.deepcopy(body))
            return self._answer(role, body)

        seam._transport = transport  # type: ignore[method-assign]
        return seam


class RecordingSeams(aa.ScriptedSeams):
    """The hermetic lane, with every message list a mind was handed kept."""

    def __init__(self, arm: aa.Arm, *, cfg: aa.ArchConfig, log: aa.CallLog) -> None:
        self.seen: list[list[dict[str, Any]]] = []
        base = al.scripted_seams(arm, config=cfg, log=log)
        wrapped = {
            role: self._watch(mind) for role, mind in base.minds.items()  # type: ignore[arg-type]
        }
        super().__init__(wrapped, config=cfg, log=log)

    def _watch(self, mind: Any) -> Any:
        def watched(messages: list[dict[str, Any]]) -> ModelResponse:
            self.seen.append(copy.deepcopy(messages))
            return mind(messages)

        return watched


def drive_round(
    *,
    arm_id: str,
    route: str,
    seams: Any,
    briefings: Optional[list[dict[str, Any]]] = None,
    cfg: Optional[aa.ArchConfig] = None,
) -> al.RoundRecord:
    resolved = cfg if cfg is not None else config()
    return al.run_round(
        arm=aa.ARMS[arm_id],
        rung_id=al.LEAGUE_LADDER[0].id,
        match_key=f"{arm_id}-0",
        match_id=f"{arm_id.lower()}0",
        team_id="bluee",
        round_index=0,
        decision_base=0,
        briefings=briefings if briefings is not None else [rich_briefing()],
        seams=seams,
        config=resolved,
        senses_hash=aa.assert_senses_identical(resolved),
        route=route,
    )


# ── the extractor every wire assertion goes through ──────────────────────────


def media_parts(body_or_messages: Any, *, kind: str = "image_url") -> list[dict[str, Any]]:
    """Every *kind* content part in an outgoing payload. The one extractor.

    Criterion 1 and its provocation share this function on purpose: "the parts
    reached the wire" and "the parts did not reach the wire" must be the same
    question asked of the same place, or the pair proves nothing.
    """
    messages = (
        body_or_messages["messages"]
        if isinstance(body_or_messages, dict)
        else list(body_or_messages)
    )
    found: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        found += [part for part in content if isinstance(part, dict) and part.get("type") == kind]
    return found


# ═════════════════════════════════════════════════════════════════════════════
# criterion 1 — an image route's parts reach the dialled body
# ═════════════════════════════════════════════════════════════════════════════


class TestAnImageRouteReachesTheWire:
    def test_the_dialled_body_carries_the_rendered_map_as_an_image_part(self) -> None:
        """The whole point of t18, asserted on the body and nowhere else."""
        brief = rich_briefing()
        ledger = alm.TwinLedger()
        ledger.commit(brief)
        expected = mr.render_map(brief, team_id="bluee").png

        cfg = config()
        log = al.ThreadSafeCallLog()
        seams = WireSeams(aa.ARMS[aa.ARM_EXISTING], cfg=cfg, log=log)
        with alm.image_route_registered(ledger):
            drive_round(
                arm_id=aa.ARM_EXISTING, route=alm.ROUTE_IMAGE, seams=seams, briefings=[brief]
            )

        assert seams.bodies, "no body reached the transport: nothing was dialled"
        parts = media_parts(seams.bodies[0])
        assert len(parts) == 1, "the rendered map did not reach the dialled body"
        url = parts[0]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert media.flatten_parts([parts[0]]) == "[image attachment]"

        import base64

        assert base64.b64decode(url.split(",", 1)[1]) == expected

    def test_the_part_is_byte_identical_to_what_embodiment_media_builds(
        self, tmp_path: Path
    ) -> None:
        """Not merely *an* image part — the one ``embodiment.media`` would build."""
        brief = rich_briefing()
        ledger = alm.TwinLedger()
        ledger.commit(brief)
        png = mr.render_map(brief, team_id="bluee").png
        reference_path = tmp_path / "reference.png"
        reference_path.write_bytes(png)
        reference = media.build_part(media.validate_attachment(str(reference_path)))

        cfg = config()
        seams = WireSeams(aa.ARMS[aa.ARM_EXISTING], cfg=cfg, log=al.ThreadSafeCallLog())
        with alm.image_route_registered(ledger):
            drive_round(
                arm_id=aa.ARM_EXISTING, route=alm.ROUTE_IMAGE, seams=seams, briefings=[brief]
            )

        assert media_parts(seams.bodies[0]) == [reference]

    def test_the_same_extraction_finds_nothing_when_a_route_renders_no_parts(self) -> None:
        """The provocation, in executable form.

        A route identical to the image one except that it renders **no** parts
        drives the identical code path, and the identical extractor comes back
        empty. So the assertion above is known to distinguish the two worlds —
        it is not a test that would pass either way.
        """
        cfg = config()
        seams = WireSeams(aa.ARMS[aa.ARM_EXISTING], cfg=cfg, log=al.ThreadSafeCallLog())
        with route_yielding("t18-partless", ()) as route:
            drive_round(arm_id=aa.ARM_EXISTING, route=route, seams=seams)

        assert seams.bodies
        assert media_parts(seams.bodies[0]) == []

    def test_the_round_instruction_still_rides_the_same_message_as_a_text_part(self) -> None:
        """Attaching media must not cost the words. Both ride one user turn."""
        cfg = config()
        seams = WireSeams(aa.ARMS[aa.ARM_EXISTING], cfg=cfg, log=al.ThreadSafeCallLog())
        with route_yielding("t18-image-ish", (mr.render_map(rich_briefing()).png,)) as route:
            drive_round(arm_id=aa.ARM_EXISTING, route=route, seams=seams)

        content = seams.bodies[0]["messages"][1]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert al.ROUND_MARKER in content[0]["text"]
        assert "MENU" in content[0]["text"]

    @pytest.mark.parametrize("arm_id", aa.ARM_ORDER)
    def test_every_arm_puts_the_parts_on_its_seat_dial(self, arm_id: str) -> None:
        """All four arms, not just the flat one whose body is easiest to read."""
        cfg = config()
        seams = RecordingSeams(aa.ARMS[arm_id], cfg=cfg, log=al.ThreadSafeCallLog())
        png = mr.render_map(rich_briefing()).png
        with route_yielding("t18-per-arm", (png,)) as route:
            drive_round(
                arm_id=arm_id,
                route=route,
                seams=seams,
                briefings=[rich_briefing(unit_id="bluee-u1", role="defender")],
                cfg=cfg,
            )

        assert seams.seen, "no mind was handed a message list"
        assert media_parts(seams.seen[0]), f"arm {arm_id} dialled its seat with text only"

    def test_one_part_per_unit_reaches_the_body(self) -> None:
        """A three-unit round carries three images, not one."""
        cfg = config()
        seams = WireSeams(aa.ARMS[aa.ARM_EXISTING], cfg=cfg, log=al.ThreadSafeCallLog())
        briefings = [
            rich_briefing(unit_id="bluee-u1", role="defender"),
            rich_briefing(unit_id="bluee-u2", role="harvester"),
            rich_briefing(unit_id="bluee-u3", role="scout"),
        ]
        png = mr.render_map(rich_briefing()).png
        with route_yielding("t18-three", (png,)) as route:
            drive_round(
                arm_id=aa.ARM_EXISTING, route=route, seams=seams, briefings=briefings, cfg=cfg
            )

        assert len(media_parts(seams.bodies[0])) == 3

    def test_a_replay_part_rides_the_video_lane_not_the_image_one(self) -> None:
        """The seam carries video generically — the transport choice is the caller's."""
        cfg = config()
        seams = WireSeams(aa.ARMS[aa.ARM_EXISTING], cfg=cfg, log=al.ThreadSafeCallLog())
        replay = pm.MediaBytes(data=GIF_BYTES, as_video=True)
        with route_yielding("t18-replay", (replay,)) as route:
            drive_round(arm_id=aa.ARM_EXISTING, route=route, seams=seams)

        assert media_parts(seams.bodies[0], kind="image_url") == []
        videos = media_parts(seams.bodies[0], kind="video_url")
        assert len(videos) == 1
        assert videos[0]["video_url"]["url"].startswith("data:image/gif;base64,")


# ═════════════════════════════════════════════════════════════════════════════
# criterion 2 — the text route's payload is what it was
# ═════════════════════════════════════════════════════════════════════════════


class TestTheTextRouteIsUnchanged:
    def test_a_text_round_dials_a_plain_string(self) -> None:
        cfg = config()
        seams = WireSeams(aa.ARMS[aa.ARM_EXISTING], cfg=cfg, log=al.ThreadSafeCallLog())
        drive_round(arm_id=aa.ARM_EXISTING, route=al.ROUTE_TEXT, seams=seams, cfg=cfg)

        content = seams.bodies[0]["messages"][1]["content"]
        assert isinstance(content, str), "a media-less round must not meet a parts list"
        assert media_parts(seams.bodies[0]) == []

    def test_the_string_is_exactly_the_round_instruction(self) -> None:
        """Byte-for-byte the text t6 shipped — no wrapper, no marker, no prefix."""
        cfg = config()
        arm = aa.ARMS[aa.ARM_EXISTING]
        brief = rich_briefing()
        seams = WireSeams(arm, cfg=cfg, log=al.ThreadSafeCallLog())
        drive_round(
            arm_id=aa.ARM_EXISTING, route=al.ROUTE_TEXT, seams=seams, briefings=[brief], cfg=cfg
        )

        view = al.round_view(
            arm=arm,
            match_id="e0",
            team_id="bluee",
            round_index=0,
            units=[al.unit_brief(brief, route=al.ROUTE_TEXT, team_id="bluee")],
            board=brief["board"],
        )
        assert seams.bodies[0]["messages"][1]["content"] == al.round_instruction(view, arm=arm)

    def test_a_partless_task_carries_no_attachments_key_at_all(self) -> None:
        """``Task.to_dict`` omits ``attachments`` when it is ``None``; it must stay ``None``."""
        with pm.staged_attachments([("bluee-u1", ())]) as attachments:
            assert attachments == []
        task = al.Task(id="t", repo_path=".", instruction="i", attachments=attachments or None)
        assert "attachments" not in task.to_dict()

    def test_no_staging_directory_is_created_for_a_partless_round(self, monkeypatch: Any) -> None:
        """Zero overhead, proved by making the syscall impossible rather than by counting."""

        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("a partless round must not create a staging directory")

        monkeypatch.setattr(pm.tempfile, "TemporaryDirectory", refuse)
        cfg = config()
        seams = RecordingSeams(aa.ARMS[aa.ARM_EXISTING], cfg=cfg, log=al.ThreadSafeCallLog())
        drive_round(arm_id=aa.ARM_EXISTING, route=al.ROUTE_TEXT, seams=seams, cfg=cfg)
        assert seams.seen

    def test_a_scripted_text_match_still_finishes_for_every_arm(self, tmp_path: Path) -> None:
        """t6's own end-to-end shape, re-run here so the wiring cannot regress it."""
        for arm_id in aa.ARM_ORDER:
            root = tmp_path / arm_id
            root.mkdir(parents=True, exist_ok=True)
            cfg = config()
            log = al.ThreadSafeCallLog()
            record, _ = al.play_match(
                cli=lc.LeagueCli(root=root, binary=FAKE_BIN),
                arm=aa.ARMS[arm_id],
                rung=al.LEAGUE_LADDER[0],
                match_index=0,
                seed=al.LEAGUE_LADDER[0].seeds[0],
                seams=al.scripted_seams(aa.ARMS[arm_id], config=cfg, log=log),
                config=cfg,
                senses_hash=aa.assert_senses_identical(cfg),
            )
            assert record.status == "finished"


# ═════════════════════════════════════════════════════════════════════════════
# criterion 3 — unbuildable parts refuse the cell, and say so
# ═════════════════════════════════════════════════════════════════════════════


class TestUnbuildablePartsRefuseTheCell:
    def test_run_round_refuses_before_any_mind_is_dialled(self) -> None:
        cfg = config()
        log = al.ThreadSafeCallLog()
        seams = al.scripted_seams(aa.ARMS[aa.ARM_EXISTING], config=cfg, log=log)
        with route_yielding("t18-broken", (JUNK_BYTES,)) as route:
            with pytest.raises(al.PerceptionRefused):
                drive_round(arm_id=aa.ARM_EXISTING, route=route, seams=seams, cfg=cfg)
        assert log.records == [], "a refused round must not still have dialled a mind"

    def test_the_refusal_names_the_route_the_unit_and_the_reason(self) -> None:
        cfg = config()
        log = al.ThreadSafeCallLog()
        seams = al.scripted_seams(aa.ARMS[aa.ARM_EXISTING], config=cfg, log=log)
        with route_yielding("t18-broken", (JUNK_BYTES,)) as route:
            with pytest.raises(al.PerceptionRefused) as caught:
                drive_round(arm_id=aa.ARM_EXISTING, route=route, seams=seams, cfg=cfg)

        refusal = caught.value.refusal
        assert refusal.code == al.REFUSED_PERCEPTION_MEDIA
        assert refusal.route == route
        assert "bluee-u1" in refusal.reason
        assert refusal.to_dict()["kind"] == al.KIND_REFUSAL

    def test_play_match_records_the_refusal_then_re_raises(self, tmp_path: Path) -> None:
        out = tmp_path / "refused.jsonl"
        cfg = config()
        log = al.ThreadSafeCallLog()
        root = tmp_path / "arena"
        root.mkdir(parents=True, exist_ok=True)
        cli = lc.LeagueCli(root=root, binary=FAKE_BIN)
        seams = al.scripted_seams(aa.ARMS[aa.ARM_EXISTING], config=cfg, log=log)
        senses_hash = aa.assert_senses_identical(cfg)
        with route_yielding("t18-broken", (JUNK_BYTES,)) as route:
            with pytest.raises(al.PerceptionRefused):
                al.play_match(
                    cli=cli,
                    arm=aa.ARMS[aa.ARM_EXISTING],
                    rung=al.LEAGUE_LADDER[0],
                    match_index=0,
                    seed=al.LEAGUE_LADDER[0].seeds[0],
                    seams=seams,
                    config=cfg,
                    senses_hash=senses_hash,
                    route=route,
                    out=out,
                )

        records = aa.read_log(out)
        refusals = [entry for entry in records if entry.get("kind") == al.KIND_REFUSAL]
        assert len(refusals) == 1
        assert refusals[0]["code"] == al.REFUSED_PERCEPTION_MEDIA
        assert refusals[0]["arm"] == aa.ARM_EXISTING

    def test_the_artifact_then_analyses_as_absent_not_as_a_text_cell(self, tmp_path: Path) -> None:
        """The whole criterion in one assertion: recorded, and ABSENT."""
        out = tmp_path / "series.jsonl"
        cfg = config()
        log = al.ThreadSafeCallLog()
        with route_yielding("t18-broken", (JUNK_BYTES,)) as route:
            with pytest.raises(al.PerceptionRefused):
                al.run_series(
                    config=cfg,
                    arena=FAKE_BIN,
                    root=tmp_path / "arena",
                    log=log,
                    arms=(aa.ARM_EXISTING,),
                    out=out,
                    route=route,
                )

        records = aa.read_log(out)
        assert [entry for entry in records if entry.get("kind") == al.KIND_REFUSAL]
        assert [entry for entry in records if entry.get("kind") == aa.KIND_CELL] == []
        verdict = al.analyse(out)
        assert verdict["verdict"] == aa.VERDICT_ABSENT
        assert verdict["cells_total"] == 0

    def test_a_text_only_endpoint_refusing_the_image_lands_on_the_round_record(self) -> None:
        """The other way an image cell can quietly become a text cell, made loud.

        A text-only served model does not ignore an image part — it rejects the
        request outright ("At most 0 image(s)…"). ``embodiment``'s loop then
        flattens to placeholders and retries text-only, which is the right
        behaviour and would otherwise be *invisible*: the round would finish,
        the cell would grade, and its record would still say ``route=…``. So
        the degradation code has to reach ``RoundRecord`` — asserted here,
        because this lane's own artifact is where a reader looks.
        """
        cfg = config()
        seams = WireSeams(
            aa.ARMS[aa.ARM_EXISTING], cfg=cfg, log=al.ThreadSafeCallLog(), refuse_media=True
        )
        png = mr.render_map(rich_briefing()).png
        with route_yielding("t18-refused-media", (png,)) as route:
            record = drive_round(arm_id=aa.ARM_EXISTING, route=route, seams=seams, cfg=cfg)

        assert loop.DEGRADED_MEDIA_REJECTED in record.degradation_codes
        assert media_parts(seams.bodies[0]), "the first attempt did carry the image"
        assert media_parts(seams.bodies[-1]) == [], "the retry went text-only, as designed"

    def test_the_artifact_states_where_parts_actually_go(self, tmp_path: Path) -> None:
        """The surviving boundary is recorded, not left to be inferred (C3).

        Parts ride the **seat's** dial. A unit routed to the worker is briefed
        with the perception text alone, because a fan-out subtask is a plain
        string in ``orchestrator_tools`` — t4's surface, not this lane's. A
        reader of an image-route artifact would otherwise reasonably assume the
        worker saw the map, so the run says so about itself.
        """
        out = tmp_path / "series.jsonl"
        al.run_series(
            config=config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_MANAGER,),
            out=out,
        )
        preamble = next(r for r in aa.read_log(out) if r["kind"] == aa.KIND_PREAMBLE)
        assert preamble["perception_delivery"] == al.PERCEPTION_DELIVERY
        assert "worker" in al.PERCEPTION_DELIVERY.lower()

    def test_a_part_shape_the_seam_does_not_accept_is_refused_by_name(self) -> None:
        cfg = config()
        log = al.ThreadSafeCallLog()
        seams = al.scripted_seams(aa.ARMS[aa.ARM_EXISTING], config=cfg, log=log)
        with route_yielding("t18-broken", (object(),)) as route:
            with pytest.raises(al.PerceptionRefused) as caught:
                drive_round(arm_id=aa.ARM_EXISTING, route=route, seams=seams, cfg=cfg)
        assert "object" in caught.value.refusal.reason
        assert log.records == []

    def test_an_oversize_part_is_refused_before_it_is_written_to_disk(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """The cap is ``embodiment.media``'s, read at call time so this test can shrink it."""
        monkeypatch.setattr(media, "MAX_ATTACHMENT_BYTES", 8)
        png = mr.render_map(rich_briefing()).png
        with pytest.raises(pm.PerceptionMediaError) as caught:
            with pm.staged_attachments([("bluee-u1", (png,))]):
                pass  # pragma: no cover - the context manager raises on entry
        assert "MAX_ATTACHMENT_BYTES" in str(caught.value)
        assert list(tmp_path.iterdir()) == []


# ═════════════════════════════════════════════════════════════════════════════
# the seam itself: bytes in memory meet media's file-oriented validation
# ═════════════════════════════════════════════════════════════════════════════


class TestBytesMeetMediasFileValidation:
    def test_png_bytes_stage_to_a_validated_attachment(self) -> None:
        png = mr.render_map(rich_briefing()).png
        with pm.staged_attachments([("bluee-u1", (png,))]) as attachments:
            assert len(attachments) == 1
            entry = attachments[0]
            assert entry["media_type"] == "image/png"
            assert Path(entry["path"]).read_bytes() == png
            assert media.build_part(entry)["type"] == "image_url"

    def test_a_declared_replay_gains_medias_own_video_kind(self) -> None:
        with pm.staged_attachments(
            [("bluee-u1", (pm.MediaBytes(data=GIF_BYTES, as_video=True),))]
        ) as attachments:
            assert attachments[0]["kind"] == "video"
            assert media.build_part(attachments[0])["type"] == "video_url"

    def test_a_still_container_declared_as_a_replay_is_still_refused(self) -> None:
        """``media``'s rule, not a copy of it: a PNG cannot be delivered as video."""
        png = mr.render_map(rich_briefing()).png
        replay = pm.MediaBytes(data=png, as_video=True)
        with pytest.raises(pm.PerceptionMediaError) as caught:
            with pm.staged_attachments([("u", (replay,))]):
                pass  # pragma: no cover - the context manager raises on entry
        assert "single-frame" in str(caught.value)

    def test_bytes_matching_no_container_are_refused_rather_than_guessed(self) -> None:
        with pytest.raises(pm.PerceptionMediaError) as caught:
            with pm.staged_attachments([("u", (JUNK_BYTES,))]):
                pass  # pragma: no cover - the context manager raises on entry
        assert "signature" in str(caught.value)

    def test_a_file_a_route_already_wrote_is_validated_not_copied(self, tmp_path: Path) -> None:
        png_path = tmp_path / "already.png"
        png_path.write_bytes(mr.render_map(rich_briefing()).png)
        with pm.staged_attachments([("u", (png_path,))]) as attachments:
            assert attachments[0]["path"] == str(png_path)

    def test_the_staging_directory_is_removed_when_the_block_exits(self) -> None:
        png = mr.render_map(rich_briefing()).png
        with pm.staged_attachments([("u", (png,))]) as attachments:
            staged = Path(attachments[0]["path"])
            assert staged.exists()
        assert not staged.exists()
        assert not staged.parent.exists()

    def test_the_staging_directory_is_removed_even_when_the_body_raises(self) -> None:
        png = mr.render_map(rich_briefing()).png
        boom = RuntimeError("boom")
        staged_path: str
        with pytest.raises(RuntimeError):
            with pm.staged_attachments([("u", (png,))]) as attachments:
                staged_path = attachments[0]["path"]
                raise boom
        assert not Path(staged_path).exists()

    @pytest.mark.parametrize(
        "payload,suffix",
        [
            (b"\x89PNG\r\n\x1a\n rest", "png"),
            (GIF_BYTES, "gif"),
            (b"\xff\xd8\xff\xe0 rest", "jpg"),
            (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
            (b"RIFF\x00\x00\x00\x00WAVEfmt ", "wav"),
            (b"\x00\x00\x00\x18ftypmp42", "mp4"),
            (b"\x1aE\xdf\xa3 rest", "webm"),
            (b"OggS rest", "ogg"),
            (b"fLaC rest", "flac"),
        ],
    )
    def test_every_sniffed_suffix_is_one_media_can_validate(
        self, payload: bytes, suffix: str, tmp_path: Path
    ) -> None:
        """Sniffing must never invent an extension ``validate_attachment`` rejects."""
        assert pm.sniff_suffix(payload) == suffix
        probe = tmp_path / f"probe.{suffix}"
        probe.write_bytes(payload)
        assert media.validate_attachment(str(probe))["path"] == str(probe)

    def test_no_content_part_is_built_outside_embodiment_media(self) -> None:
        """Structural, not behavioural. A second encoder is a second place to rot.

        Behaviourally, a hand-rolled ``image_url`` part would pass every other
        test in this file — it would reach the wire and decode. This one fails
        the moment one is written, which is the guarantee "single point of
        validation" actually needs. Prose is excluded (docstrings and comments
        discuss these names on purpose); only executable string literals count.
        """
        for name in ("perception_media", "arch_league", "arch_arms"):
            tree = ast.parse((REPO_ROOT / "examples" / f"{name}.py").read_text(encoding="utf-8"))
            imported = {
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imported |= {
                (node.module or "").split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
            assert "base64" not in imported, f"{name} encodes media itself"

            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(
                    node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                )
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            literals = [
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ]
            for text in literals:
                for token in ("image_url", "video_url", "input_audio", ";base64,"):
                    assert token not in text, f"{name} builds a content part itself: {token}"
