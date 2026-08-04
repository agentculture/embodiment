"""The live ScopeBench harness, tested without a socket (plan task ``t11``).

``examples/scopebench_live.py`` is the one module in the scope lane that talks
to a network, so everything about it that *can* be proved hermetically is
proved here: how a strategist's reply is read, what happens when it cannot be
read, what happens when the transport dies, and — the load-bearing one — that a
live arm is graded by byte-identical machinery to the scripted controls.

The seam is never real. Every test below builds a genuine
:class:`examples.worker_seam.WorkerSeam` and replaces its ``_post`` with a
canned OpenAI-shaped payload, which is the same technique
``tests/test_worker_seam.py`` and ``tests/test_league_h2h.py`` use: the body
shaping, the metering, the truncation notice and the transcript all stay inside
the code under test rather than around it.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from embodiment.scope import (  # noqa: E402
    DROPPED_INCOMPLETE,
    MARKER_DIRECTIVE,
    MARKER_HOLD,
    SCOPE_AUTHORITY,
)
from examples import scopebench_live as sl  # noqa: E402
from examples import worker_seam as ws  # noqa: E402
from examples.scope import episodes as ep  # noqa: E402
from examples.scope import oracle as orc  # noqa: E402
from examples.scope import scopebench as sb  # noqa: E402
from examples.scope import subordinate as sub  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "examples" / "scopebench_live.py"


# ── fixtures and doubles ─────────────────────────────────────────────────────


@pytest.fixture(name="episode")
def _episode() -> ep.Episode:
    return ep.first_cycle_episodes()[0]


def _completion(content: str, *, finish_reason: str = "stop") -> dict[str, Any]:
    """One OpenAI-shaped non-streaming completion, the shape ``_post`` returns."""
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "reasoning": "..."},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 13},
    }


class _CannedSeam(ws.WorkerSeam):
    """A real seam whose transport is a list of scripted replies."""

    def __init__(self, replies: list[str], **kwargs: Any) -> None:
        super().__init__(
            base_url="http://localhost:9/v1",
            model="test-model",
            api_key="k",
            role="test-strategist",
            max_tokens=sl.STRATEGIST_MAX_TOKENS,
            temperature=sl.STRATEGIST_TEMPERATURE,
            sleep=lambda _seconds: None,
            **kwargs,
        )
        self._replies = list(replies)
        self.bodies: list[dict[str, Any]] = []

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        if not self._replies:
            raise OSError("no scripted reply left")
        return _completion(self._replies.pop(0))


class _DeadSeam(ws.WorkerSeam):
    """A seam whose transport always fails, so the retry ladder is exhausted."""

    def __init__(self) -> None:
        super().__init__(
            base_url="http://localhost:9/v1",
            model="test-model",
            api_key="k",
            role="dead-strategist",
            max_tokens=sl.STRATEGIST_MAX_TOKENS,
            temperature=sl.STRATEGIST_TEMPERATURE,
            sleep=lambda _seconds: None,
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        raise OSError("the port is closed")


def _directive_text(episode: ep.Episode, *, version: int, supersedes: Optional[str]) -> str:
    payload = {
        "scope_id": f"{episode.id}-v{version}",
        "supersedes": supersedes,
        "version": version,
        "objective": "finish what is worth the most",
        "priorities": ["migrate"],
        "constraints": [entry.text for entry in episode.constraints],
        "responsibilities": [
            {"owner": episode.actors[0].id, "responsibility": episode.workstreams[0].id},
            {"owner": episode.actors[1].id, "responsibility": ep.IDLE},
        ],
        "success_conditions": ["everything lands by the horizon"],
        "review_when": ["the next review"],
        "decision_summary": "put the fastest actor on the shortest stream",
    }
    return f"{MARKER_DIRECTIVE}\n{json.dumps(payload)}"


def _context(episode: ep.Episode, review: int = 0) -> sub.PlannerContext:
    register = sub.Register(sub.default_directive(episode))
    state = orc.initial_state(episode)
    return sub.PlannerContext(
        episode=episode,
        state=state,
        review=review,
        active=register.active,
        snapshot=sub.project(episode, state, review, register.active),
        next_version=register.version + 1,
    )


# ── the module's own hygiene ─────────────────────────────────────────────────


class TestTheHarnessIntroducesNoClock:
    """The rule this repo has been bitten by five times, applied to a new file.

    ``tests/test_timeout_bounds.py``'s AST guard already walks ``examples/``
    recursively and would fail an undeclared constant here. This is the same
    property stated where a reader of *this* module will look for it, and it is
    the reason the harness reuses ``WorkerSeam`` rather than growing a transport
    of its own: every clock in front of these dials is one that is already
    derived from the committed rate config.
    """

    @staticmethod
    def _module_level_numbers() -> list[str]:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        found: list[str] = []
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Constant):
                continue
            if not isinstance(node.value.value, (int, float)):
                continue
            found.extend(target.id for target in node.targets if isinstance(target, ast.Name))
        return found

    def test_no_module_level_timeout_constant(self) -> None:
        hits = [
            name
            for name in self._module_level_numbers()
            if any(hint in name for hint in ("TIMEOUT", "DEADLINE", "BACKOFF", "SLEEP"))
        ]
        assert hits == []

    def test_the_budget_constant_is_present_so_the_walk_can_resolve_it(self) -> None:
        assert "STRATEGIST_MAX_TOKENS" in self._module_level_numbers()

    def test_the_budget_is_deviation_d16s_measured_floor(self) -> None:
        assert sl.STRATEGIST_MAX_TOKENS == 16000

    def test_the_harness_dials_serially(self) -> None:
        """The cortex rate was measured at concurrency 1 and is never interpolated."""
        assert sl.STREAM_QUEUE_WIDTH == 1


class TestTheAuthorityFramingIsTheShippedOne:
    """``SCOPE_AUTHORITY`` is appended to, never substituted for."""

    def test_the_system_message_opens_with_the_package_text(self, episode: ep.Episode) -> None:
        seam = _CannedSeam([MARKER_HOLD])
        strategist = sl.LiveStrategist(seam=seam, episode=episode)
        strategist(_context(episode))
        assert seam.bodies[0]["messages"][0]["content"].startswith(SCOPE_AUTHORITY)

    def test_the_host_framing_is_appended_after_it(self, episode: ep.Episode) -> None:
        seam = _CannedSeam([MARKER_HOLD])
        strategist = sl.LiveStrategist(seam=seam, episode=episode)
        strategist(_context(episode))
        assert sl.episode_framing(episode) in seam.bodies[0]["messages"][0]["content"]

    def test_the_framing_names_every_actor(self, episode: ep.Episode) -> None:
        framing = sl.episode_framing(episode)
        missing = [actor.id for actor in episode.actors if actor.id not in framing]
        assert missing == []

    def test_the_framing_names_every_workstream(self, episode: ep.Episode) -> None:
        framing = sl.episode_framing(episode)
        missing = [entry.id for entry in episode.workstreams if entry.id not in framing]
        assert missing == []

    def test_the_framing_never_carries_the_episode_brief(self, episode: ep.Episode) -> None:
        """The brief describes the puzzle. Handing it over would be a hint.

        The scripted controls are algorithms and read no prose at all, so a live
        arm shown the brief would be reading something no control could — a
        difference between arms that is not the seat.
        """
        assert episode.brief not in sl.episode_framing(episode)

    def test_the_framing_is_one_template_for_every_episode(self) -> None:
        """It is vocabulary, never a briefing.

        Strip the two lines that carry this world's names and what is left must
        be byte-identical across all 36 committed episodes and all six families.
        A framing that varied by family would be the harness telling the
        strategist which puzzle it is looking at, which is the harness scoring
        itself.
        """
        skeletons = {
            "\n".join(
                line
                for line in sl.episode_framing(entry).splitlines()
                if not line.startswith(("Actors:", "Workstreams:"))
            )
            for entry in ep.first_cycle_episodes()
        }
        assert len(skeletons) == 1

    def test_the_review_message_states_facts_and_never_a_rule(self, episode: ep.Episode) -> None:
        """Pre-registration amendment 1, pinned.

        The rule about ``version`` and ``supersedes`` belongs to
        ``SCOPE_AUTHORITY``. A harness sentence that restates it beside the
        active ``scope_id`` invited the worker seat to echo that id into its own
        directive, which the register then refused as a duplicate — a protocol
        failure the harness helped cause. ``must`` is the verb that rule is
        written with, so its absence here is the checkable form of "facts only".

        Scoped to the harness's own trailing block: the projection quotes the
        episode's commitments verbatim ("Total spend ... must not exceed 54"),
        and that is the world's text rather than the harness's instruction.
        """
        context = _context(episode)
        projection = json.dumps(dict(context.snapshot), indent=2, sort_keys=True)
        tail = sl.review_message(context).split(projection, 1)[1]
        assert "must" not in tail.lower()

    def test_the_review_message_still_carries_the_active_scope_id(
        self, episode: ep.Episode
    ) -> None:
        """Facts only is not the same as facts withheld.

        The projection does not carry the active directive's version, and
        ``SCOPE_AUTHORITY`` requires one strictly greater than it. Withholding
        it would measure guessing rather than phrasing.
        """
        message = sl.review_message(_context(episode))
        assert f"{episode.id}-default" in message

    def test_the_projection_the_model_reads_is_the_graded_one(self, episode: ep.Episode) -> None:
        """Stage 1's grading and Stage 2's inputs cannot drift apart if there is one."""
        context = _context(episode)
        message = sl.review_message(context)
        assert json.dumps(dict(context.snapshot), indent=2, sort_keys=True) in message


# ── reading a reply ──────────────────────────────────────────────────────────


class TestFirstObject:
    def test_it_finds_a_flat_object(self) -> None:
        assert sl.first_object('noise {"a": 1} tail') == '{"a": 1}'

    def test_it_balances_nested_objects(self) -> None:
        assert sl.first_object('{"a": {"b": 2}}') == '{"a": {"b": 2}}'

    def test_a_brace_inside_a_string_does_not_close_the_object(self) -> None:
        assert sl.first_object('{"a": "}"}') == '{"a": "}"}'

    def test_an_escaped_quote_does_not_open_a_string(self) -> None:
        assert sl.first_object('{"a": "x\\"}y"}') == '{"a": "x\\"}y"}'

    def test_an_unbalanced_object_is_not_returned(self) -> None:
        assert sl.first_object('{"a": 1') is None

    def test_a_reply_with_no_object_returns_none(self) -> None:
        assert sl.first_object("no braces at all") is None


class TestReadReply:
    def test_a_directive_after_the_marker_is_a_directive(self) -> None:
        kind, payload, _detail = sl.read_reply(f'{MARKER_DIRECTIVE} {{"scope_id": "x"}}')
        assert kind == sl.REPLY_DIRECTIVE

    def test_the_directive_payload_is_the_parsed_object(self) -> None:
        _kind, payload, _detail = sl.read_reply(f'{MARKER_DIRECTIVE} {{"scope_id": "x"}}')
        assert payload == {"scope_id": "x"}

    def test_the_hold_marker_is_a_hold(self) -> None:
        kind, _payload, _detail = sl.read_reply("The current scope still fits. [hold]")
        assert kind == sl.REPLY_HOLD

    def test_a_hold_carries_no_payload(self) -> None:
        _kind, payload, _detail = sl.read_reply(MARKER_HOLD)
        assert payload is None

    def test_a_bare_object_with_no_marker_is_still_read(self) -> None:
        kind, _payload, _detail = sl.read_reply('{"scope_id": "x", "version": 1}')
        assert kind == sl.REPLY_DIRECTIVE

    def test_a_directive_wins_over_a_stray_hold_word(self) -> None:
        """The marker path is tried first, exactly as the shipped protocol states it."""
        text = f'I could hold. {MARKER_DIRECTIVE} {{"scope_id": "x"}} — or [hold].'
        kind, _payload, _detail = sl.read_reply(text)
        assert kind == sl.REPLY_DIRECTIVE

    def test_prose_with_neither_is_unreadable(self) -> None:
        kind, _payload, _detail = sl.read_reply("I think we should probably do something.")
        assert kind == sl.REPLY_UNREADABLE

    def test_an_empty_reply_is_unreadable_and_says_so(self) -> None:
        kind, _payload, detail = sl.read_reply("")
        assert kind == sl.REPLY_UNREADABLE
        assert "no content" in detail

    def test_an_unreadable_reply_is_never_read_as_a_hold(self) -> None:
        """Issue #37's lesson: two different events, never the same object."""
        kind, _payload, _detail = sl.read_reply("...")
        assert kind != sl.REPLY_HOLD


# ── the live strategist inside the graded machinery ──────────────────────────


class TestTheLiveArmIsGradedByTheScriptedMachinery:
    """A live arm is a ``PlannerFn``. Nothing about its scoring is new code."""

    def test_a_directive_reaches_the_register_and_is_accepted(self, episode: ep.Episode) -> None:
        replies = [
            _directive_text(episode, version=index + 1, supersedes=_supersedes(episode, index))
            for index in range(len(episode.review_ticks))
        ]
        seam = _CannedSeam(replies)
        rollout = sub.execute(episode, sl.LiveStrategist(seam=seam, episode=episode))
        assert rollout.accepted == len(episode.review_ticks)

    def test_a_hold_is_recorded_as_a_hold_not_as_an_offer(self, episode: ep.Episode) -> None:
        seam = _CannedSeam([MARKER_HOLD] * len(episode.review_ticks))
        rollout = sub.execute(episode, sl.LiveStrategist(seam=seam, episode=episode))
        assert rollout.holds == len(episode.review_ticks)

    def test_a_hold_offers_nothing(self, episode: ep.Episode) -> None:
        seam = _CannedSeam([MARKER_HOLD] * len(episode.review_ticks))
        rollout = sub.execute(episode, sl.LiveStrategist(seam=seam, episode=episode))
        assert rollout.offered == 0

    def test_an_unreadable_reply_is_an_offer_that_is_refused(self, episode: ep.Episode) -> None:
        seam = _CannedSeam(["I am not sure."] * len(episode.review_ticks))
        rollout = sub.execute(episode, sl.LiveStrategist(seam=seam, episode=episode))
        assert rollout.offered == len(episode.review_ticks)

    def test_an_unreadable_reply_lands_on_the_protocol_axis(self, episode: ep.Episode) -> None:
        seam = _CannedSeam(["I am not sure."] * len(episode.review_ticks))
        rollout = sub.execute(episode, sl.LiveStrategist(seam=seam, episode=episode))
        codes = {entry.code for entry in rollout.refusals}
        assert codes == {DROPPED_INCOMPLETE}

    def test_an_unreadable_reply_scores_exactly_as_holding_does(self, episode: ep.Episode) -> None:
        """The pre-registration's own criterion, on the live path.

        A refused directive's outcome block must be byte-identical to the
        outcome of holding instead — a protocol failure is never a strategic
        one.
        """
        solution = orc.solve(episode)
        turns = len(episode.review_ticks)
        unreadable = sub.execute(
            episode, sl.LiveStrategist(seam=_CannedSeam(["nope"] * turns), episode=episode)
        )
        holding = sub.execute(
            episode, sl.LiveStrategist(seam=_CannedSeam([MARKER_HOLD] * turns), episode=episode)
        )
        left = sb.grade(episode, solution, unreadable, sb.Cost())["outcome"]
        right = sb.grade(episode, solution, holding, sb.Cost())["outcome"]
        assert left == right

    def test_an_authority_violation_is_recorded_on_its_own_axis(self, episode: ep.Episode) -> None:
        """A forbidden key reaches the payload because the model wrote it."""
        text = f'{MARKER_DIRECTIVE} {{"scope_id": "x", "objective": "o", "command": "rm -rf"}}'
        seam = _CannedSeam([text] * len(episode.review_ticks))
        rollout = sub.execute(episode, sl.LiveStrategist(seam=seam, episode=episode))
        assert len(rollout.violations) == len(episode.review_ticks)

    def test_that_violation_is_not_an_outcome(self, episode: ep.Episode) -> None:
        text = f'{MARKER_DIRECTIVE} {{"scope_id": "x", "objective": "o", "command": "rm -rf"}}'
        seam = _CannedSeam([text] * len(episode.review_ticks))
        rollout = sub.execute(episode, sl.LiveStrategist(seam=seam, episode=episode))
        graded = sb.grade(episode, orc.solve(episode), rollout, sb.Cost())
        assert "authority_violations" not in graded["outcome"]


def _supersedes(episode: ep.Episode, index: int) -> str:
    return f"{episode.id}-default" if index == 0 else f"{episode.id}-v{index}"


# ── the transport failure path ───────────────────────────────────────────────


class TestATransportFailureAbandonsTheEpisode:
    """A dead port is an instrument event, and it is never any of the four axes.

    Charging it to protocol acceptance could void an arm for a rig fault, and
    reading it as a hold would record a decision nobody made. So the episode
    leaves the cell and is reported by id — the pre-registration's condition 7
    obligation, one level below a cell.
    """

    def test_the_strategist_raises_rather_than_answering(self, episode: ep.Episode) -> None:
        strategist = sl.LiveStrategist(seam=_DeadSeam(), episode=episode)
        context = _context(episode)  # built outside the block: python:S5778
        with pytest.raises(sl.StrategistUnavailable):
            strategist(context)

    def test_the_episode_record_is_a_drop(self, episode: ep.Episode, monkeypatch) -> None:
        monkeypatch.setattr(ws.WorkerSeam, "_post", _DeadSeam._post)
        dial = sl.SeatDialConfig(
            arm=sb.ARM_A3,
            seat="strategist",
            role="cortex",
            model="m",
            base_url="http://localhost:9/v1",
            api_key="k",
        )
        monkeypatch.setattr(ws, "RETRY_SLEEP_SECONDS", 0.0)
        record = sl.run_episode(sb.ARM_A3, episode, dial, stream=False)
        assert record["dropped"] is True

    def test_the_drop_names_its_code(self, episode: ep.Episode, monkeypatch) -> None:
        monkeypatch.setattr(ws.WorkerSeam, "_post", _DeadSeam._post)
        monkeypatch.setattr(ws, "RETRY_SLEEP_SECONDS", 0.0)
        dial = sl.SeatDialConfig(
            arm=sb.ARM_A3,
            seat="strategist",
            role="cortex",
            model="m",
            base_url="http://localhost:9/v1",
            api_key="k",
        )
        record = sl.run_episode(sb.ARM_A3, episode, dial, stream=False)
        assert record["code"] == sl.DROPPED_EPISODE_TRANSPORT


# ── the arms differ in one field, asserted from the record ───────────────────


class TestTheArmsDifferOnlyInTheSeat:
    """``t11`` acceptance criterion 2's discipline, applied at Stage 1."""

    @staticmethod
    def _dial(arm: str, role: str, model: str) -> sl.SeatDialConfig:
        return sl.SeatDialConfig(
            arm=arm,
            seat="strategist",
            role=role,
            model=model,
            base_url="http://localhost:8001/v1",
            api_key="k",
        )

    def test_only_the_seat_keys_differ(self) -> None:
        left = sl.arm_fingerprint(sb.ARM_A2, self._dial(sb.ARM_A2, "worker", "w"))
        right = sl.arm_fingerprint(sb.ARM_A3, self._dial(sb.ARM_A3, "cortex", "c"))
        differing = sorted(key for key in left if left[key] != right[key])
        assert differing == ["arm", "seat_model", "seat_role"]

    def test_the_sampling_is_identical(self) -> None:
        left = sl.arm_fingerprint(sb.ARM_A2, self._dial(sb.ARM_A2, "worker", "w"))
        right = sl.arm_fingerprint(sb.ARM_A3, self._dial(sb.ARM_A3, "cortex", "c"))
        assert left["max_tokens"] == right["max_tokens"]

    def test_the_system_prompt_digest_is_identical(self) -> None:
        left = sl.arm_fingerprint(sb.ARM_A2, self._dial(sb.ARM_A2, "worker", "w"))
        right = sl.arm_fingerprint(sb.ARM_A3, self._dial(sb.ARM_A3, "cortex", "c"))
        assert left["system_prompt_sha"] == right["system_prompt_sha"]

    def test_the_transport_field_reports_what_was_dialled(self) -> None:
        """A fingerprint that read the module default would lie under --no-stream.

        The one block whose entire job is to say what the instrument was must
        not report a default in place of the instrument.
        """
        dial = self._dial(sb.ARM_A3, "cortex", "c")
        assert (
            sl.arm_fingerprint(sb.ARM_A3, dial, stream=False)["transport"] == ws.TRANSPORT_BLOCKING
        )

    def test_the_shipped_runs_dialled_streaming(self) -> None:
        dial = self._dial(sb.ARM_A3, "cortex", "c")
        assert sl.arm_fingerprint(sb.ARM_A3, dial)["transport"] == ws.TRANSPORT_STREAM


# ── seat resolution degrades, never raises ───────────────────────────────────


class TestSeatResolutionDegrades:
    def test_a_missing_role_resolves_to_no_dial(self) -> None:
        dial, _degradations = sl.resolve_dial(
            sb.ARM_A3, {"worker": {"ready": True}}, gateway="http://x", api_key="k"
        )
        assert dial is None

    def test_a_missing_role_is_recorded(self) -> None:
        _dial, degradations = sl.resolve_dial(
            sb.ARM_A3, {"worker": {"ready": True}}, gateway="http://x", api_key="k"
        )
        codes = {entry.code for entry in degradations}
        assert st_absent() in codes

    def test_a_not_ready_role_resolves_to_no_dial(self) -> None:
        dial, _degradations = sl.resolve_dial(
            sb.ARM_A3, {"cortex": {"ready": False}}, gateway="http://x", api_key="k"
        )
        assert dial is None

    def test_an_unseated_arm_resolves_to_no_dial(self) -> None:
        dial, _degradations = sl.resolve_dial(
            sb.ARM_A0, {"cortex": {"ready": True}}, gateway="http://x", api_key="k"
        )
        assert dial is None

    def test_a_hostile_payload_does_not_raise(self) -> None:
        dial, _degradations = sl.resolve_dial(
            sb.ARM_A3, {"cortex": "not a mapping"}, gateway="http://x", api_key="k"
        )
        assert dial is None

    def test_the_api_key_is_never_serialised(self) -> None:
        dial = sl.SeatDialConfig(
            arm=sb.ARM_A3,
            seat="strategist",
            role="cortex",
            model="m",
            base_url="http://x/v1",
            api_key="secret-token",
        )
        assert "secret-token" not in json.dumps(dial.to_dict())


def st_absent() -> str:
    from examples.scope import seats

    return seats.DEGRADED_ROLE_ABSENT


# ── folding records back into the pre-registered rule ────────────────────────


class TestSummariseLive:
    def test_an_absence_is_never_claimed_for_a_scored_cell(self) -> None:
        records = sb.run_stage_one()
        summary = sl.summarise_live(records)
        scored = {key for key, cell in summary["cells"].items() if cell["n"]}
        assert not (scored & set(summary["absent"]))

    def test_every_declared_cell_is_scored_or_explained(self) -> None:
        records = sb.run_stage_one()
        summary = sl.summarise_live(records)
        declared = {
            f"{arm}|{stage}|{family}"
            for arm in sb.ARM_ORDER
            for stage in sb.STAGES
            for family in ep.FIRST_CYCLE
        }
        present = {key for key, cell in summary["cells"].items() if cell["n"]}
        assert not (declared - present - set(summary["absent"]))

    def test_the_stage_two_reason_is_t11s_not_t9s(self) -> None:
        """Quoting t9's reason after t11 has run would explain a real gap wrongly."""
        summary = sl.summarise_live(sb.run_stage_one())
        reason = summary["absent"][f"{sb.ARM_A3}|{sb.STAGE_TWO}|{ep.FIRST_CYCLE[0]}"]
        assert reason == sl.STAGE_TWO_ABSENT

    def test_no_verdict_is_emitted_while_a_control_is_absent(self) -> None:
        """The committed rule, applied not tuned."""
        summary = sl.summarise_live(sb.run_stage_one())
        assert sb.verdict(summary, sb.ARM_A3).verdict == sb.VERDICT_INCONCLUSIVE


class TestLoadRecords:
    def test_a_written_run_round_trips(self, tmp_path: Path, episode: ep.Episode) -> None:
        turns = len(episode.review_ticks)
        seam = _CannedSeam([MARKER_HOLD] * turns)
        rollout = sub.execute(episode, sl.LiveStrategist(seam=seam, episode=episode))
        graded = sb.grade(episode, orc.solve(episode), rollout, sb.Cost())
        record = sb.EpisodeRecord(
            arm=sb.ARM_A3,
            stage=sb.STAGE_ONE,
            family=episode.family,
            episode=episode.id,
            seed=episode.seed,
            planner="live:cortex",
            validity=sb.VALID,
            outcome=graded["outcome"],
            protocol=graded["protocol"],
            authority=graded["authority"],
            cost=graded["cost"],
        )
        payload = record.to_dict()
        payload["dropped"] = False
        path = tmp_path / "A3-stage1.jsonl"
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        loaded, dropped = sl.load_records([path])
        assert [entry.episode for entry in loaded] == [episode.id]
        assert dropped == []

    def test_a_dropped_episode_is_not_diagnosed_as_a_scored_one(self, tmp_path: Path) -> None:
        path = tmp_path / "A3-stage1.jsonl"
        path.write_text(
            json.dumps({"dropped": True, "arm": sb.ARM_A3, "episode": "x"}) + "\n",
            encoding="utf-8",
        )
        assert sl.arm_diagnostics([path]) == {}

    def test_a_dropped_episode_is_kept_apart_from_the_scored_ones(self, tmp_path: Path) -> None:
        path = tmp_path / "A3-stage1.jsonl"
        path.write_text(
            json.dumps({"dropped": True, "episode": "x", "code": sl.DROPPED_EPISODE_TRANSPORT})
            + "\n",
            encoding="utf-8",
        )
        loaded, dropped = sl.load_records([path])
        assert loaded == []
        assert len(dropped) == 1


# ── diagnostics ride beside the verdict and never reach it ───────────────────


class TestDiagnosticsAreReportedNeverScored:
    """The judge-is-secondary discipline (§8), applied to the instrument detail.

    ``arm_diagnostics`` exists so a ``void-protocol`` cell can be read for
    *why*. Like the judge lane it must be structurally unable to move a verdict:
    ``verdict()`` takes only the graded summary, and nothing folded here is in
    it.
    """

    @staticmethod
    def _written(tmp_path: Path, episode: ep.Episode, replies: list[str]) -> Path:
        seam = _CannedSeam(replies)
        strategist = sl.LiveStrategist(seam=seam, episode=episode)
        rollout = sub.execute(episode, strategist)
        graded = sb.grade(episode, orc.solve(episode), rollout, sb.Cost())
        payload = sb.EpisodeRecord(
            arm=sb.ARM_A3,
            stage=sb.STAGE_ONE,
            family=episode.family,
            episode=episode.id,
            seed=episode.seed,
            planner="live:cortex",
            validity=sb.validity_of((), graded["protocol"]),
            outcome=graded["outcome"],
            protocol=graded["protocol"],
            authority=graded["authority"],
            cost=graded["cost"],
        ).to_dict()
        payload["dropped"] = False
        payload["reply_kinds"] = {
            sl.REPLY_DIRECTIVE: sum(
                1 for call in strategist.calls if call.kind == sl.REPLY_DIRECTIVE
            ),
            sl.REPLY_HOLD: strategist.holds,
            sl.REPLY_UNREADABLE: strategist.unreadable,
        }
        payload["meter"] = seam.meter.to_dict()
        path = tmp_path / "A3-stage1.jsonl"
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return path

    def test_holds_are_counted(self, tmp_path: Path, episode: ep.Episode) -> None:
        turns = len(episode.review_ticks)
        path = self._written(tmp_path, episode, [MARKER_HOLD] * turns)
        assert sl.arm_diagnostics([path])[sb.ARM_A3]["holds"] == turns

    def test_reply_kinds_are_counted(self, tmp_path: Path, episode: ep.Episode) -> None:
        turns = len(episode.review_ticks)
        path = self._written(tmp_path, episode, ["prose only"] * turns)
        found = sl.arm_diagnostics([path])[sb.ARM_A3]["reply_kinds"]
        assert found[sl.REPLY_UNREADABLE] == turns

    def test_refusal_codes_are_named(self, tmp_path: Path, episode: ep.Episode) -> None:
        turns = len(episode.review_ticks)
        path = self._written(tmp_path, episode, ["prose only"] * turns)
        codes = sl.arm_diagnostics([path])[sb.ARM_A3]["refusals_by_code"]
        assert codes[DROPPED_INCOMPLETE] == turns

    def test_no_diagnostic_key_is_a_verdict_input(
        self, tmp_path: Path, episode: ep.Episode
    ) -> None:
        """The structural half: nothing here is a parameter ``verdict`` can read.

        ``verdict(summary, arm)`` has exactly two parameters and neither is a
        diagnostics block. Stated as a signature check so a future edit that
        threaded one in fails here rather than in a reviewer's head.
        """
        import inspect

        names = list(inspect.signature(sb.verdict).parameters)
        assert names == ["summary", "arm"]


class TestSignMargins:
    """Condition 1 and 2 both turn on a margin; a write-up has to show it."""

    def test_every_family_reports_a_paired_count(self) -> None:
        summary = sl.summarise_live(sb.run_stage_one())
        margins = sl._sign_margins(summary)
        assert set(margins) == {sb.ARM_A1, sb.ARM_A2, sb.ARM_A3}

    def test_pairing_is_by_episode_id_not_position(self) -> None:
        """A dropped episode must not silently shift the comparison by one."""
        records = [entry for entry in sb.run_stage_one() if entry.arm == sb.ARM_A0]
        shifted = [
            sb.EpisodeRecord(
                arm=sb.ARM_A3,
                stage=entry.stage,
                family=entry.family,
                episode=entry.episode,
                seed=entry.seed,
                planner="live:cortex",
                validity=entry.validity,
                outcome=entry.outcome,
                protocol=entry.protocol,
                authority=entry.authority,
                cost=entry.cost,
            )
            for entry in records[1:]
        ]
        summary = sl.summarise_live([*records, *shifted])
        margins = sl._sign_margins(summary)[sb.ARM_A3]
        totals = {family: row["n_paired"] for family, row in margins.items()}
        assert sum(totals.values()) == len(shifted)


# ── the CLI, offline ─────────────────────────────────────────────────────────


class TestTheOfflineVerbs:
    """``report`` and ``tables`` fold committed records; neither dials anything.

    Run through ``subprocess`` rather than by calling ``main`` so the check
    covers argument parsing and the module's import-time behaviour too — the
    same shape ``tests/test_scopebench.py::TestCli`` uses on the scaffold.
    """

    @staticmethod
    def _cli(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(  # nosec B603 - fixed argv, this repo's own example
            [sys.executable, str(REPO_ROOT / "examples" / "scopebench_live.py"), *args],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )

    def test_report_runs_with_no_raw_records_at_all(self, tmp_path: Path) -> None:
        result = self._cli("report", "--raw")
        assert result.returncode == 0

    def test_report_names_every_condition(self, tmp_path: Path) -> None:
        result = self._cli("report", "--raw")
        missing = [name for name in sb.VERDICT_CONDITIONS if name not in result.stdout]
        assert missing == []

    def test_report_emits_a_verdict_for_the_arm_under_test(self) -> None:
        result = self._cli("report", "--raw", "--json")
        payload = json.loads(result.stdout)
        assert payload["verdicts"][sb.ARM_A3]["verdict"] == sb.VERDICT_INCONCLUSIVE

    def test_tables_names_every_first_cycle_family(self) -> None:
        result = self._cli("tables", "--raw")
        missing = [name for name in ep.FIRST_CYCLE if name not in result.stdout]
        assert missing == []

    def test_tables_labels_every_control_as_a_control(self) -> None:
        """A control read as an arm is the one confusion the prefix exists to stop."""
        result = self._cli("tables", "--raw")
        rows = [line for line in result.stdout.splitlines() if sb.CONTROL_PREFIX in line]
        assert rows == []


class TestAnAbsenceReasonIsTrue:
    """Condition 7's real obligation: not merely *an* explanation, a true one.

    The bug this class was written for was in this harness, not the scaffold: a
    cell that had been dialled and voided would have been explained with
    ``scopebench.STAGE_ONE_ABSENT``'s "no live dial has been run", which is a
    sentence about ``t9`` and false the moment ``t11`` runs.
    """

    @staticmethod
    def _voided(episode: ep.Episode) -> sb.EpisodeRecord:
        return sb.EpisodeRecord(
            arm=sb.ARM_A2,
            stage=sb.STAGE_ONE,
            family=episode.family,
            episode=episode.id,
            seed=episode.seed,
            planner="live:worker",
            validity=sb.VOID_PROTOCOL,
            outcome={},
            protocol={"protocol_acceptance": 0.0},
            authority={},
            cost={},
        )

    def test_a_voided_cell_is_not_called_undialled(self, episode: ep.Episode) -> None:
        summary = sl.summarise_live([*sb.run_stage_one(), self._voided(episode)])
        reason = summary["absent"][f"{sb.ARM_A2}|{sb.STAGE_ONE}|{episode.family}"]
        assert "no live dial has been run" not in reason

    def test_a_voided_cell_names_the_protocol_floor(self, episode: ep.Episode) -> None:
        summary = sl.summarise_live([*sb.run_stage_one(), self._voided(episode)])
        reason = summary["absent"][f"{sb.ARM_A2}|{sb.STAGE_ONE}|{episode.family}"]
        assert sb.VOID_PROTOCOL in reason

    def test_an_unreached_cell_says_so(self, episode: ep.Episode) -> None:
        summary = sl.summarise_live(sb.run_stage_one())
        reason = summary["absent"][f"{sb.ARM_A3}|{sb.STAGE_ONE}|{episode.family}"]
        assert "not reached in this cycle" in reason

    def test_a1s_structural_absence_still_quotes_the_pre_registration(
        self, episode: ep.Episode
    ) -> None:
        """That reason was written before any dial and is still exactly true."""
        summary = sl.summarise_live(sb.run_stage_one())
        reason = summary["absent"][f"{sb.ARM_A1}|{sb.STAGE_ONE}|{episode.family}"]
        assert reason == sb.STAGE_ONE_ABSENT[sb.ARM_A1]

    def test_condition_seven_still_holds_with_a_voided_cell(self, episode: ep.Episode) -> None:
        summary = sl.summarise_live([*sb.run_stage_one(), self._voided(episode)])
        status, _detail = sb.verdict(summary, sb.ARM_A3).conditions[sb.CONDITION_REPORTING]
        assert status == sb.HELD
