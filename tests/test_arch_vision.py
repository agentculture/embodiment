"""Tests for the perception-routing screen (plan task t8, rescoped by deviation ``d1``).

The rung is an **instrument**, so this file is written as an adversarial kit
rather than a smoke test: every acceptance criterion has a named class, and
every guard that could pass vacuously carries a control proving it can fail.

Acceptance criteria, and where each is proved:

1. **All three routes are defined with exactly what each mind receives, stated
   in committed config, and stage 2 dials only the route stage 1 selected —
   the post-hoc path structurally impossible, not merely discouraged.**
   → :class:`TestRoutesAreCommittedConfig` (the declaration is checked against
   the payload actually built, not merely read back) and
   :class:`TestStageTwoCannotBeToldARoute` (an AST proof that no route can be
   injected, plus behavioural proof that the route *moves* when the stage-1
   evidence moves).

2. **Fog scoping binds every route identically (h15).**
   → :class:`TestFogScopingBindsEveryRoute`, parametrized over every registered
   route, over t9's own fixture, with two vacuity controls — a ground-truth
   image that must leak, and a lying senses whose text must be caught.

3. **The routing rung is exempt from information matching (c40/h31), and the
   exemption relaxes matching only — never visibility.**
   → :class:`TestInformationMatchingExemption`.

4. **Capability facts come from the committed probes, never ``/capabilities``
   (c43/h32).**
   → :class:`TestCapabilityFactsComeFromProbes`.

Everything else here exists because the criteria above are only meaningful if
the harness underneath them is honest: the senses lane really is absent on the
native route (:class:`TestSensesLane`), a refused image degrades visibly rather
than vanishing (:class:`TestMediaRejectionDegrades`), the graders can actually
fail (:class:`TestGraders`), and nothing dials a model (:class:`TestNoLiveDial`).
"""

from __future__ import annotations

import ast
import base64
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.contract import ModelResponse, ToolCall
from examples import arch_arms as aa
from examples import arch_vision as av
from examples import map_render

SOURCE = Path(av.__file__).read_text(encoding="utf-8")


# ── helpers ──────────────────────────────────────────────────────────────────


def _func(name: str) -> ast.FunctionDef:
    """The named top-level function, as an AST node."""
    for node in ast.parse(SOURCE).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not a top-level function of arch_vision")


def _param_names(func: ast.FunctionDef) -> list[str]:
    args = func.args
    names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return names


def _string_constants(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def _config(tmp_path: Path, mutate: Any = None) -> Path:
    """A copy of the committed sampling table, optionally mutated."""
    raw = json.loads(av.CONFIG_PATH.read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(raw)
    path = tmp_path / "sampling.json"
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return path


def _senses_saying(text: str) -> Any:
    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        return ModelResponse(content=text)

    return complete


def _seams(
    log: aa.CallLog,
    config: aa.ArchConfig,
    *,
    minds: Optional[dict[str, Any]] = None,
) -> aa.ScriptedSeams:
    return aa.ScriptedSeams(minds or {}, config=config, log=log)


def _stimulus(tmp_path: Path, briefing: Any = None) -> av.VisionStimulus:
    return av.build_stimulus(tmp_path / "raw", briefing=briefing)


def _delivery(
    tmp_path: Path,
    route: str,
    *,
    briefing: Any = None,
    description: str = "the map shows two friendly units",
) -> av.Delivery:
    perception = av.load_perception_config()
    spec = perception.spec(route)
    stimulus = _stimulus(tmp_path, briefing)
    return av.build_delivery(
        spec,
        stimulus,
        description=description if spec.senses_description else "",
        senses_parts=av.senses_payload(stimulus) if spec.senses_description else (),
    )


def _image_bytes(part: dict[str, Any]) -> bytes:
    url = part["image_url"]["url"]
    return base64.b64decode(url.split(",", 1)[1])


# ── criterion 1a: the routes, and exactly what each mind receives ────────────


class TestRoutesAreCommittedConfig:
    """The three routes, and the content each mind gets, live in a committed file.

    "Stated in committed config" is a weak criterion if the statement is only
    read back — a config that said one thing while the harness sent another
    would satisfy it. So the load-bearing test here compares the *declaration*
    against the payload :func:`arch_vision.build_delivery` actually produces.
    """

    def test_the_committed_table_declares_every_route(self) -> None:
        perception = av.load_perception_config()
        assert perception.path == av.CONFIG_PATH
        assert tuple(perception.order) == av.ROUTE_ORDER
        for route in av.ROUTE_ORDER:
            spec = perception.spec(route)
            assert spec.why, f"route {route} carries no stated reason"
            assert spec.cortex_receives, f"route {route} declares nothing for the deciding mind"

    def test_every_declared_content_token_is_from_the_closed_vocabulary(self) -> None:
        perception = av.load_perception_config()
        for route in av.ROUTE_ORDER:
            spec = perception.spec(route)
            for token in (*spec.cortex_receives, *spec.senses_receives):
                assert token in av.CONTENTS, f"{route} declares unknown content {token!r}"

    @pytest.mark.parametrize("route", av.ROUTE_ORDER)
    def test_the_declaration_matches_what_the_deciding_mind_is_handed(
        self, tmp_path: Path, route: str
    ) -> None:
        perception = av.load_perception_config()
        delivery = _delivery(tmp_path, route)
        assert delivery.contents_for(aa.ROLE_CORTEX) == tuple(
            perception.spec(route).cortex_receives
        )

    @pytest.mark.parametrize("route", av.ROUTE_ORDER)
    def test_the_declaration_matches_what_senses_is_handed(
        self, tmp_path: Path, route: str
    ) -> None:
        perception = av.load_perception_config()
        delivery = _delivery(tmp_path, route)
        assert delivery.contents_for(aa.ROLE_SENSES) == tuple(
            perception.spec(route).senses_receives
        )

    def test_the_three_routes_deliver_materially_different_payloads(self, tmp_path: Path) -> None:
        """A screen whose arms are identical measures nothing."""
        seen = {route: _delivery(tmp_path, route).fingerprint() for route in av.ROUTE_ORDER}
        assert len(set(seen.values())) == len(av.ROUTE_ORDER), seen

    def test_native_carries_an_image_and_no_description(self, tmp_path: Path) -> None:
        delivery = _delivery(tmp_path, av.ROUTE_NATIVE)
        assert delivery.image_parts()
        assert not delivery.description

    def test_described_carries_a_description_and_no_image(self, tmp_path: Path) -> None:
        delivery = _delivery(tmp_path, av.ROUTE_DESCRIBED)
        assert not delivery.image_parts()
        assert delivery.description

    def test_both_carries_the_description_and_the_image(self, tmp_path: Path) -> None:
        delivery = _delivery(tmp_path, av.ROUTE_BOTH)
        assert delivery.image_parts()
        assert delivery.description

    @pytest.mark.parametrize("route", av.ROUTE_ORDER)
    def test_the_config_governs_the_payload_even_when_a_caller_overreaches(
        self, tmp_path: Path, route: str
    ) -> None:
        """A caller offering content the route does not declare must not get it through.

        Without this the declaration is only as strong as every call site's
        discipline, and "stated in committed config" quietly becomes "stated in
        committed config and also in whichever harness happens to call it".
        """
        perception = av.load_perception_config()
        spec = perception.spec(route)
        stimulus = _stimulus(tmp_path, None)
        delivery = av.build_delivery(
            spec,
            stimulus,
            description="SMUGGLED DESCRIPTION",
            senses_parts=av.senses_payload(stimulus),
        )
        assert delivery.contents_for(aa.ROLE_CORTEX) == tuple(spec.cortex_receives)
        assert delivery.contents_for(aa.ROLE_SENSES) == tuple(spec.senses_receives)
        if not spec.senses_description:
            assert "SMUGGLED" not in delivery.acting_text("Q")
            assert delivery.senses_parts == ()

    def test_a_route_missing_from_the_config_raises_rather_than_defaulting(
        self, tmp_path: Path
    ) -> None:
        def drop(raw: dict[str, Any]) -> None:
            del raw[av.CONFIG_KEY]["routes"][av.ROUTE_BOTH]

        path = _config(tmp_path, drop)
        with pytest.raises(aa.ConfigError) as caught:
            av.load_perception_config(path)
        assert av.ROUTE_BOTH in str(caught.value)

    def test_a_route_missing_its_declared_contents_raises(self, tmp_path: Path) -> None:
        def drop(raw: dict[str, Any]) -> None:
            del raw[av.CONFIG_KEY]["routes"][av.ROUTE_NATIVE]["cortex_receives"]

        path = _config(tmp_path, drop)
        with pytest.raises(aa.ConfigError):
            av.load_perception_config(path)

    def test_the_module_carries_no_fallback_route_table(self) -> None:
        """No dict in the code may map a route id to its contents.

        The config is only the source of truth if there is nothing in the code
        to fall back to — the same rule t5 applies to the sampling table.
        """
        tree = ast.parse(SOURCE)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            assert not keys >= set(av.ROUTE_ORDER), (
                "a dict in arch_vision.py is keyed by every route id — that is a "
                "fallback route table, and the committed config stops being the "
                "only source of what a mind receives"
            )


# ── criterion 1b: the post-hoc pick is structurally impossible ───────────────


class TestStageTwoCannotBeToldARoute:
    """Stage 2 derives its route; it cannot be handed one.

    Two prior experiments died on an underpowered full crossing, so ``c41``
    refuses one by rule. That rule is only worth anything if the two-stage
    shape cannot be quietly turned back into a crossing plus a favourite — so
    the guarantee here is structural (no parameter exists to inject a route,
    and the selection function refuses stage-2 data) rather than a convention.
    """

    def test_run_stage2_takes_no_route_or_selection_parameter(self) -> None:
        names = _param_names(_func("run_stage2"))
        offenders = [n for n in names if "route" in n.lower() or "select" in n.lower()]
        assert not offenders, (
            f"run_stage2 accepts {offenders} — a caller who has seen stage-2 data "
            "could then choose the route, which is exactly the post-hoc pick h30 forbids"
        )

    def test_run_stage1_takes_no_route_parameter(self) -> None:
        """A screen you can run one route at a time is a screen you can stop early."""
        names = _param_names(_func("run_stage1"))
        assert not [n for n in names if "route" in n.lower()]

    def test_run_stage2_names_no_route_literal_in_its_body(self) -> None:
        literals = _string_constants(_func("run_stage2"))
        assert not literals & set(av.ROUTE_ORDER)

    def test_run_stage2_derives_its_route_from_select_route_alone(self) -> None:
        """The only value flowing into the run is the selection function's output."""
        func = _func("run_stage2")
        calls = {
            child.func.id
            for child in ast.walk(func)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
        assert "select_route" in calls, "run_stage2 does not call select_route at all"
        assignments = [
            node
            for node in ast.walk(func)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "selection" for t in node.targets  # noqa: E501
            )
        ]
        assert len(assignments) == 1, "the selection is assigned more than once"
        value = assignments[0].value
        assert isinstance(value, ast.Call)
        assert getattr(value.func, "id", "") == "select_route"

    def test_the_parameter_guard_would_catch_a_planted_route(self) -> None:
        """Vacuity guard: the AST scan must fail on a source that *does* take one."""
        planted = SOURCE.replace(
            "def run_stage2(\n    *,\n    stage1: Any,",
            "def run_stage2(\n    *,\n    route: str,\n    stage1: Any,",
            1,
        )
        assert planted != SOURCE, "the planted-parameter mutation did not apply"
        func = next(
            node
            for node in ast.parse(planted).body
            if isinstance(node, ast.FunctionDef) and node.name == "run_stage2"
        )
        assert [n for n in _param_names(func) if "route" in n]

    def test_the_cli_exposes_no_route_flag(self) -> None:
        parser = av.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["stage2", "--stage1", "x", "--route", av.ROUTE_NATIVE])

    def test_select_route_refuses_records_from_stage_two(self) -> None:
        records = _screen_records({av.ROUTE_NATIVE: 4, av.ROUTE_DESCRIBED: 1, av.ROUTE_BOTH: 1})
        records.append({**records[0], "stage": av.STAGE_CARRY})
        with pytest.raises(av.PostHocError):
            av.select_route(records)

    def test_select_route_refuses_an_incomplete_screen(self) -> None:
        records = _screen_records({av.ROUTE_NATIVE: 4, av.ROUTE_DESCRIBED: 4})
        with pytest.raises(av.IncompleteScreen) as caught:
            av.select_route(records)
        assert av.ROUTE_BOTH in str(caught.value)

    def test_select_route_refuses_an_underpowered_screen(self) -> None:
        records = _screen_records({r: 1 for r in av.ROUTE_ORDER}, attempts=1)
        with pytest.raises(av.IncompleteScreen):
            av.select_route(records)

    def test_the_selection_follows_the_committed_rule(self) -> None:
        selection = av.select_route(
            _screen_records({av.ROUTE_NATIVE: 1, av.ROUTE_DESCRIBED: 4, av.ROUTE_BOTH: 2})
        )
        assert selection.route == av.ROUTE_DESCRIBED
        assert selection.basis == av.BASIS_CORRECTNESS
        assert selection.separated is True

    def test_a_correctness_tie_falls_to_the_committed_tie_break(self) -> None:
        selection = av.select_route(
            _screen_records(
                {av.ROUTE_NATIVE: 4, av.ROUTE_DESCRIBED: 4, av.ROUTE_BOTH: 4},
                tokens={av.ROUTE_NATIVE: 900, av.ROUTE_DESCRIBED: 400, av.ROUTE_BOTH: 1500},
            )
        )
        assert selection.route == av.ROUTE_DESCRIBED
        assert selection.basis == av.BASIS_COST
        assert selection.separated is False, "a cost tie-break is not a measured separation"

    def test_a_total_tie_falls_to_the_committed_precedence(self) -> None:
        selection = av.select_route(
            _screen_records(
                {r: 4 for r in av.ROUTE_ORDER},
                tokens={r: 1000 for r in av.ROUTE_ORDER},
            )
        )
        assert selection.route == av.load_perception_config().rule.precedence[0]
        assert selection.basis == av.BASIS_PRECEDENCE
        assert selection.separated is False

    def test_stage2_runs_only_the_route_stage1_selected(self, tmp_path: Path) -> None:
        stage1 = tmp_path / "stage1.jsonl"
        stage1.write_text(
            "\n".join(
                json.dumps(r)
                for r in _screen_records(
                    {av.ROUTE_NATIVE: 1, av.ROUTE_DESCRIBED: 4, av.ROUTE_BOTH: 2}
                )
            )
            + "\n",
            encoding="utf-8",
        )
        report = _run_stage2(tmp_path, stage1)
        routes = {record["route"] for record in report["records"] if record["kind"] == "attempt"}
        assert routes == {av.ROUTE_DESCRIBED}
        assert report["selection"]["route"] == av.ROUTE_DESCRIBED

    def test_changing_the_stage1_evidence_changes_the_stage2_route(self, tmp_path: Path) -> None:
        """Proof of derivation: the same call, different evidence, different route."""
        seen = set()
        for winner in av.ROUTE_ORDER:
            stage1 = tmp_path / f"stage1-{winner}.jsonl"
            scores = {r: (4 if r == winner else 1) for r in av.ROUTE_ORDER}
            stage1.write_text(
                "\n".join(json.dumps(r) for r in _screen_records(scores)) + "\n",
                encoding="utf-8",
            )
            report = _run_stage2(tmp_path / winner, stage1)
            seen.add(report["selection"]["route"])
        assert seen == set(av.ROUTE_ORDER)

    def test_every_stage2_record_carries_the_selection_seal(self, tmp_path: Path) -> None:
        stage1 = _write_screen(
            tmp_path, {av.ROUTE_NATIVE: 4, av.ROUTE_DESCRIBED: 1, av.ROUTE_BOTH: 1}
        )
        report = _run_stage2(tmp_path, stage1)
        seal = report["selection"]["seal"]
        assert seal
        for record in report["records"]:
            if record["kind"] in (aa.KIND_ATTEMPT, aa.KIND_CELL):
                assert record["selection_seal"] == seal
                assert record["stage"] == av.STAGE_CARRY

    def test_verify_stage2_accepts_a_matching_pair(self, tmp_path: Path) -> None:
        stage1 = _write_screen(
            tmp_path, {av.ROUTE_NATIVE: 4, av.ROUTE_DESCRIBED: 1, av.ROUTE_BOTH: 1}
        )
        out = tmp_path / "stage2.jsonl"
        _run_stage2(tmp_path, stage1, out=out)
        report = av.verify_stage2(stage1, out)
        assert report["ok"] is True
        assert report["route"] == av.ROUTE_NATIVE

    def test_verify_stage2_catches_a_stage2_artifact_for_another_route(
        self, tmp_path: Path
    ) -> None:
        """The tamper case: a stage-2 artifact whose route the screen did not pick."""
        stage1 = _write_screen(
            tmp_path, {av.ROUTE_NATIVE: 4, av.ROUTE_DESCRIBED: 1, av.ROUTE_BOTH: 1}
        )
        out = tmp_path / "stage2.jsonl"
        _run_stage2(tmp_path, stage1, out=out)
        lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        for record in lines:
            if record.get("route") == av.ROUTE_NATIVE:
                record["route"] = av.ROUTE_BOTH
        out.write_text("\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8")
        with pytest.raises(av.PostHocError):
            av.verify_stage2(stage1, out)

    def test_verify_stage2_catches_an_edited_screen(self, tmp_path: Path) -> None:
        """Editing stage 1 after the fact breaks the seal rather than moving the route."""
        stage1 = _write_screen(
            tmp_path, {av.ROUTE_NATIVE: 4, av.ROUTE_DESCRIBED: 1, av.ROUTE_BOTH: 1}
        )
        out = tmp_path / "stage2.jsonl"
        _run_stage2(tmp_path, stage1, out=out)
        records = [json.loads(line) for line in stage1.read_text(encoding="utf-8").splitlines()]
        for record in records:
            if record.get("route") == av.ROUTE_DESCRIBED and record.get("kind") == aa.KIND_ATTEMPT:
                record["is_correct"] = True
        stage1.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        with pytest.raises(av.PostHocError):
            av.verify_stage2(stage1, out)

    def test_a_selection_cannot_be_built_with_a_forged_seal(self) -> None:
        selection = av.select_route(
            _screen_records({av.ROUTE_NATIVE: 4, av.ROUTE_DESCRIBED: 1, av.ROUTE_BOTH: 1})
        )
        with pytest.raises(av.PostHocError):
            av.RouteSelection(
                route=av.ROUTE_BOTH,
                rule=selection.rule,
                basis=selection.basis,
                separated=selection.separated,
                evidence_hash=selection.evidence_hash,
                seal=selection.seal,
                ranking=selection.ranking,
                evidence=selection.evidence,
            )


def _screen_records(
    correct: dict[str, int],
    *,
    tokens: Optional[dict[str, int]] = None,
    attempts: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Synthetic stage-1 attempt records: *n* attempts per route, *correct* of them right."""
    perception = av.load_perception_config()
    total = perception.rule.min_attempts_per_route if attempts is None else attempts
    records: list[dict[str, Any]] = []
    for route, hits in correct.items():
        per_call = int((tokens or {}).get(route, 1000)) // max(total, 1)
        for index in range(total):
            records.append(
                {
                    "kind": aa.KIND_ATTEMPT,
                    "stage": av.STAGE_SCREEN,
                    "arm": aa.ARM_EXISTING,
                    "rung": av.RUNG_ID,
                    "route": route,
                    "problem": f"synthetic-{index}",
                    "is_correct": index < hits,
                    "degradation_codes": [],
                    "cost": {
                        "prompt_tokens": per_call,
                        "completion_tokens": 0,
                        "truncated_calls": 0,
                    },
                }
            )
    return records


def _write_screen(tmp_path: Path, correct: dict[str, int]) -> Path:
    path = tmp_path / "stage1.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in _screen_records(correct)) + "\n", encoding="utf-8"
    )
    return path


def _run_stage2(root: Path, stage1: Path, *, out: Optional[Path] = None) -> dict[str, Any]:
    config = aa.load_config()
    log = aa.CallLog()
    return av.run_stage2(
        stage1=stage1,
        config=config,
        perception=av.load_perception_config(),
        seams=_seams(log, config),
        log=log,
        raw_dir=root / "raw",
        out=out,
    )


# ── criterion 2: fog scoping binds every route identically ───────────────────


class TestFogScopingBindsEveryRoute:
    """h15 — no route may show a mind a cell its visibility excludes.

    The fixture is **t9's own** (:func:`map_render.fog_leak_fixture`) rather
    than a second copy: a fog fixture maintained in two places is a fog fixture
    that drifts, which is the defect the renderer's whole design avoids.

    The scan runs over the *delivered payload* — the bytes and the strings that
    actually reach a mind — not over the renderer's in-memory state, because
    the routing rung adds two new ways for state to reach a model and only one
    of them is the image.
    """

    def test_the_fixture_is_t9s_not_a_second_copy(self) -> None:
        assert av.RUNG_BRIEFING == map_render.fog_leak_fixture().fogged
        assert av.HIDDEN == map_render.fog_leak_fixture().hidden

    def test_the_harness_refuses_to_run_a_cell_whose_payload_leaks(self, tmp_path: Path) -> None:
        """The rule binds at run time, not only in this file.

        A senses description naming an unobserved entity is the one leak a
        hermetic test cannot pre-empt, because it is model output. So the run
        checks its own payload before every attempt and stops — a leaked cell
        is a defective instrument, and its answer would look ordinary.
        """
        leaking = _senses_saying(f"I can clearly see {av.HIDDEN[0]['id']} to the east.")
        config = aa.load_config()
        log = aa.CallLog()
        perception = av.load_perception_config()
        seams = _seams(log, config, minds={aa.ROLE_SENSES: leaking})
        with pytest.raises(av.FogLeak) as caught:
            av.run_stage1(
                config=config,
                perception=perception,
                seams=seams,
                log=log,
                raw_dir=tmp_path / "raw",
                out=tmp_path / "s1.jsonl",
            )
        assert str(av.HIDDEN[0]["id"]) in str(caught.value)

    def test_the_runtime_refusal_lets_a_clean_screen_through(self, tmp_path: Path) -> None:
        """Vacuity control: the refusal above must not simply refuse everything."""
        honest = _senses_saying("Two friendly units, a held control point, a node and a mission.")
        config = aa.load_config()
        log = aa.CallLog()
        report = av.run_stage1(
            config=config,
            perception=av.load_perception_config(),
            seams=_seams(log, config, minds={aa.ROLE_SENSES: honest}),
            log=log,
            raw_dir=tmp_path / "raw",
        )
        assert len(report["cells"]) == len(av.ROUTE_ORDER)

    @pytest.mark.parametrize("route", av.ROUTE_ORDER)
    def test_no_hidden_entity_reaches_any_mind(self, tmp_path: Path, route: str) -> None:
        delivery = _delivery(tmp_path / route, route)
        stimulus = _stimulus(tmp_path / route)
        assert av.scan_delivery(delivery, stimulus, av.HIDDEN) == []

    @pytest.mark.parametrize("route", av.ROUTE_ORDER)
    def test_the_scan_reads_the_bytes_that_were_actually_delivered(
        self, tmp_path: Path, route: str
    ) -> None:
        """A scan of the renderer's output would miss a payload built from elsewhere."""
        delivery = _delivery(tmp_path / route, route)
        stimulus = _stimulus(tmp_path / route)
        for part in delivery.image_parts():
            assert _image_bytes(part) == stimulus.png_path.read_bytes()

    def test_the_image_scan_has_teeth_on_ground_truth(self, tmp_path: Path) -> None:
        """Vacuity control: the same scan must FIND the leak on the fogless twin."""
        truth = map_render.fog_leak_fixture().ground_truth
        for route in (av.ROUTE_NATIVE, av.ROUTE_BOTH):
            delivery = _delivery(tmp_path / f"gt-{route}", route, briefing=truth)
            stimulus = _stimulus(tmp_path / f"gt-{route}", truth)
            leaks = av.scan_delivery(delivery, stimulus, av.HIDDEN)
            assert leaks, f"the ground-truth control did not leak on route {route}"

    def test_the_text_scan_has_teeth_against_a_lying_senses(self, tmp_path: Path) -> None:
        """Vacuity control: a description naming an unobserved entity must be caught.

        This is not hypothetical politeness. A senses description is model
        output, and the one failure mode that would destroy the described route
        is a mind that names something the image never showed. The scan runs on
        the delivered text in every run, live included, so the guard is real.
        """
        leaked = " ".join(entity["id"] for entity in av.HIDDEN)
        for route in (av.ROUTE_DESCRIBED, av.ROUTE_BOTH):
            delivery = _delivery(tmp_path / f"lie-{route}", route, description=leaked)
            stimulus = _stimulus(tmp_path / f"lie-{route}")
            leaks = av.scan_delivery(delivery, stimulus, av.HIDDEN)
            assert leaks, f"a lying senses description passed the scan on route {route}"

    def test_the_scan_covers_every_registered_route_with_no_exemptions(self) -> None:
        """No route may opt out of the visibility rule — including the exempt rung's."""
        perception = av.load_perception_config()
        assert set(perception.routes) == set(av.ROUTE_ORDER)
        assert av.FOG_SCAN_APPLIES_TO == av.ROUTE_ORDER

    def test_the_stimulus_refuses_a_briefing_that_is_not_the_rungs(self, tmp_path: Path) -> None:
        """The graded truth and the delivered image must describe one state."""
        truth = map_render.fog_leak_fixture().ground_truth
        stimulus = av.build_stimulus(tmp_path / "raw", briefing=truth)
        assert stimulus.snapshot_hash != av.RUNG_SNAPSHOT_HASH
        with pytest.raises(aa.ConfigError):
            av.assert_rung_stimulus(stimulus)

    def test_a_measured_turns_map_is_committed_beside_its_hash(self, tmp_path: Path) -> None:
        stimulus = _stimulus(tmp_path)
        assert stimulus.png_path.exists()
        meta = json.loads(stimulus.artifact.meta_path.read_text(encoding="utf-8"))
        assert meta["snapshot_hash"] == stimulus.snapshot_hash == av.RUNG_SNAPSHOT_HASH


# ── criterion 3: the information-matching exemption ──────────────────────────


class TestInformationMatchingExemption:
    """c40/h31 — the exemption relaxes matching only, never visibility.

    ``c22``/``h16`` exist so an image cell cannot smuggle extra state past its
    text twin. Routing arms deliberately vary information content — a senses
    description is lossy compression by design — so the matching rule would
    forbid the very contrast this rung measures. The exemption is therefore
    stated per rung, and the fog rule keeps binding.
    """

    def test_the_exemption_is_declared_for_this_rung_only(self) -> None:
        exemption = av.load_perception_config().exemption
        assert exemption["applies_to_rung"] == av.RUNG_ID
        assert exemption["why"], "an exemption with no stated reason is a loophole"

    def test_the_exemption_relaxes_matching_and_nothing_else(self) -> None:
        exemption = av.load_perception_config().exemption
        assert list(exemption["relaxes"]) == [av.RULE_INFORMATION_MATCHING]
        assert av.RULE_FOG_SCOPING in exemption["still_binds"]

    def test_a_config_that_tried_to_exempt_visibility_is_refused(self, tmp_path: Path) -> None:
        def widen(raw: dict[str, Any]) -> None:
            raw[av.CONFIG_KEY]["information_matching_exemption"]["relaxes"].append(
                av.RULE_FOG_SCOPING
            )

        path = _config(tmp_path, widen)
        with pytest.raises(aa.ConfigError) as caught:
            av.load_perception_config(path)
        assert av.RULE_FOG_SCOPING in str(caught.value)

    def test_a_config_that_dropped_visibility_from_still_binds_is_refused(
        self, tmp_path: Path
    ) -> None:
        def drop(raw: dict[str, Any]) -> None:
            raw[av.CONFIG_KEY]["information_matching_exemption"]["still_binds"] = []

        path = _config(tmp_path, drop)
        with pytest.raises(aa.ConfigError):
            av.load_perception_config(path)

    def test_the_exemption_is_what_the_routes_actually_need(self, tmp_path: Path) -> None:
        """The routes really do carry different information — hence the exemption."""
        native = _delivery(tmp_path / "n", av.ROUTE_NATIVE)
        described = _delivery(tmp_path / "d", av.ROUTE_DESCRIBED)
        assert native.acting_text("Q") != described.acting_text("Q")
        assert bool(native.image_parts()) != bool(described.image_parts())

    def test_the_exemption_does_not_switch_off_the_fog_scan(self, tmp_path: Path) -> None:
        """The exempt rung's own routes are still scanned, and the scan still bites."""
        perception = av.load_perception_config()
        assert perception.exemption["applies_to_rung"] == av.RUNG_ID
        leaked = av.HIDDEN[0]["id"]
        delivery = _delivery(tmp_path, av.ROUTE_DESCRIBED, description=f"I can see {leaked}")
        assert av.scan_delivery(delivery, _stimulus(tmp_path), av.HIDDEN)


# ── criterion 4: capability facts come from the committed probes ─────────────


class TestCapabilityFactsComeFromProbes:
    """c43/h32 — the advert understates the cortex, so the probe is the source.

    A consumer obeying this repo's own "resolve by name, never parse model
    names" rule would read ``/capabilities``, conclude the cortex is blind, and
    silently drop the native route. The measurement says otherwise, so the
    measurement is what this rung reads.
    """

    def test_every_capability_fact_cites_a_committed_probe(self) -> None:
        for fact in av.CAPABILITY_FACTS.values():
            path = av.REPO_ROOT / fact.source
            assert path.exists(), f"{fact.source} is cited but not committed"

    def test_each_probe_carries_the_evidence_that_is_quoted_from_it(self) -> None:
        for fact in av.CAPABILITY_FACTS.values():
            text = (av.REPO_ROOT / fact.source).read_text(encoding="utf-8")
            assert fact.evidence in text, (
                f"{fact.source} no longer contains {fact.evidence!r} — the citation "
                "drifted from the record it claims to quote"
            )

    def test_the_native_route_rests_on_the_measurement_not_the_advert(self) -> None:
        fact = av.capability(aa.ROLE_CORTEX, av.MODALITY_IMAGE)
        assert fact.measured is True
        assert fact.advert_declares is False
        assert av.native_route_supported() is True

    def test_the_advert_disagreement_is_recorded_rather_than_absorbed(self) -> None:
        disagreeing = [key for key, fact in av.CAPABILITY_FACTS.items() if fact.disagrees]
        assert (aa.ROLE_CORTEX, av.MODALITY_IMAGE) in disagreeing
        assert av.ADVERT_SOURCE == "/capabilities"

    def test_no_code_path_reads_the_capabilities_advert(self) -> None:
        """The advert may be *named* as the thing not used; it may not be dialled."""
        tree = ast.parse(SOURCE)
        body = tree.body[1:] if ast.get_docstring(tree) else tree.body
        for node in body:
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    if "capabilities" not in child.value:
                        continue
                    assert child.value == av.ADVERT_SOURCE, (
                        f"arch_vision builds {child.value!r} — capability facts come "
                        "from the committed probes for as long as the advert disagrees"
                    )

    def test_no_model_id_is_hardcoded(self) -> None:
        """Identities come from the sampling table; arm E is whatever is dialled."""
        for fragment in ("Qwen", "qwen", "gemma", "Gemma", "unsloth", "NVFP4"):
            offenders = [
                value for value in _string_constants(ast.parse(SOURCE)) if fragment in value
            ]
            allowed = {fact.evidence for fact in av.CAPABILITY_FACTS.values()}
            assert not [v for v in offenders if v not in allowed], (
                f"{fragment!r} appears in arch_vision outside a probe citation; "
                "a harness that names a model names a mind that may not exist"
            )


# ── the senses lane: the whole question stage 1 asks ─────────────────────────


class TestSensesLane:
    """Does senses still earn its place once the cortex can see?

    The screen only answers that if the native route genuinely runs without
    senses — one senses call sneaking into the native arm would make the
    comparison meaningless while every number still looked plausible.
    """

    def test_the_native_route_makes_no_senses_call(self, tmp_path: Path) -> None:
        assert _senses_calls(_screen(tmp_path), av.ROUTE_NATIVE) == []

    @pytest.mark.parametrize("route", (av.ROUTE_DESCRIBED, av.ROUTE_BOTH))
    def test_the_describing_routes_make_exactly_one_senses_call_per_attempt(
        self, tmp_path: Path, route: str
    ) -> None:
        assert len(_senses_calls(_screen(tmp_path), route)) == len(av.RUNG.problems)

    def test_the_senses_call_receives_the_image_and_nothing_else(self, tmp_path: Path) -> None:
        stimulus = _stimulus(tmp_path)
        payload = av.senses_payload(stimulus)
        kinds = [part["type"] for part in payload]
        assert kinds == ["text", "image_url"]
        assert payload[0]["text"] == av.SENSES_INSTRUCTION

    def test_a_dead_senses_degrades_rather_than_raising(self, tmp_path: Path) -> None:
        """The verbatim/never-raise rule: senses failing must not kill the drive."""

        def dead(messages: list[dict[str, Any]]) -> ModelResponse:
            raise RuntimeError("connection refused")

        config = aa.load_config()
        log = aa.CallLog()
        seams = _seams(log, config, minds={aa.ROLE_SENSES: dead})
        text, degradations = av.describe_with_senses(
            _stimulus(tmp_path),
            seams=seams,
            ctx=aa.CallContext(
                arm=aa.ARM_EXISTING,
                rung=av.RUNG_ID,
                problem="p",
                route=av.ROUTE_DESCRIBED,
                senses_hash="h",
                live=False,
            ),
        )
        assert av.DEGRADED_SENSES_ABSENT in degradations
        assert av.DESCRIPTION_UNAVAILABLE in text

    def test_an_empty_description_is_recorded_as_a_degradation(self, tmp_path: Path) -> None:
        config = aa.load_config()
        log = aa.CallLog()
        seams = _seams(log, config, minds={aa.ROLE_SENSES: _senses_saying("   ")})
        _, degradations = av.describe_with_senses(
            _stimulus(tmp_path),
            seams=seams,
            ctx=aa.CallContext(
                arm=aa.ARM_EXISTING,
                rung=av.RUNG_ID,
                problem="p",
                route=av.ROUTE_DESCRIBED,
                senses_hash="h",
                live=False,
            ),
        )
        assert av.DEGRADED_SENSES_EMPTY in degradations


def _senses_calls(records: list[dict[str, Any]], route: str) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if record.get("kind") == aa.KIND_CALL
        and record.get("role") == aa.ROLE_SENSES
        and record.get("route") == route
    ]


def _screen(tmp_path: Path) -> list[dict[str, Any]]:
    config = aa.load_config()
    log = aa.CallLog()
    out = tmp_path / "stage1.jsonl"
    av.run_stage1(
        config=config,
        perception=av.load_perception_config(),
        seams=_seams(log, config),
        log=log,
        raw_dir=tmp_path / "raw",
        out=out,
    )
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]


# ── degradation is observable (C3) ───────────────────────────────────────────


class TestMediaRejectionDegrades:
    """A model that refuses the image must leave a record, not a silence."""

    def test_a_refused_image_flattens_and_records(self, tmp_path: Path) -> None:
        calls: list[Any] = []

        def picky(messages: list[dict[str, Any]]) -> ModelResponse:
            calls.append(messages)
            if any(isinstance(m.get("content"), list) for m in messages):
                raise RuntimeError("HTTP 400: At most 0 image(s) may be provided in one prompt")
            return ModelResponse(
                content="ok",
                tool_calls=[ToolCall(id="c1", name="finish", arguments={"answer": "2"})],
            )

        config = aa.load_config()
        log = aa.CallLog()
        base = _seams(log, config, minds={aa.ROLE_CORTEX: picky})
        delivery = _delivery(tmp_path, av.ROUTE_NATIVE)
        seams = av.RoutedSeams(base, delivery)
        mind = seams.build(
            aa.ROLE_CORTEX,
            aa.CallContext(
                arm=aa.ARM_EXISTING,
                rung=av.RUNG_ID,
                problem="p",
                route=av.ROUTE_NATIVE,
                senses_hash="h",
                live=False,
            ),
            None,
        )
        reply = mind([{"role": "user", "content": "how many units?"}])
        assert reply.content == "ok"
        assert av.DEGRADED_MEDIA_REJECTED in seams.degradations
        assert len(calls) == 2, "the retry must be a different attempt, not a repeat"
        assert not isinstance(calls[1][0]["content"], list)

    def test_an_unrelated_error_still_raises(self, tmp_path: Path) -> None:
        def broken(messages: list[dict[str, Any]]) -> ModelResponse:
            raise RuntimeError("connection reset by peer")

        config = aa.load_config()
        log = aa.CallLog()
        base = _seams(log, config, minds={aa.ROLE_CORTEX: broken})
        seams = av.RoutedSeams(base, _delivery(tmp_path, av.ROUTE_NATIVE))
        mind = seams.build(
            aa.ROLE_CORTEX,
            aa.CallContext(
                arm=aa.ARM_EXISTING,
                rung=av.RUNG_ID,
                problem="p",
                route=av.ROUTE_NATIVE,
                senses_hash="h",
                live=False,
            ),
            None,
        )
        with pytest.raises(RuntimeError):
            mind([{"role": "user", "content": "q"}])


# ── the graders (the M2 kit) ─────────────────────────────────────────────────


class TestGraders:
    """A wrong grader is a defective instrument, so each one must be able to fail."""

    def test_every_truth_is_computed_from_the_fog_snapshot(self) -> None:
        """Not typed in: a hand-written answer and a rendered map can disagree."""
        snapshot = map_render.fog_snapshot(av.RUNG_BRIEFING)
        for question in av.QUESTIONS:
            assert question.truth(snapshot) == question.truth(av.RUNG_SNAPSHOT)

    @pytest.mark.parametrize("question", av.QUESTIONS, ids=lambda q: q.id)
    def test_the_verified_answer_grades_correct(self, question: Any) -> None:
        expected = question.truth(av.RUNG_SNAPSHOT)
        assert av.PROBLEMS[question.id].grade(str(expected))["is_correct"] is True

    @pytest.mark.parametrize("question", av.QUESTIONS, ids=lambda q: q.id)
    def test_a_wrong_answer_grades_wrong(self, question: Any) -> None:
        assert av.PROBLEMS[question.id].grade(question.wrong)["is_correct"] is False

    @pytest.mark.parametrize("question", av.QUESTIONS, ids=lambda q: q.id)
    def test_a_paraphrase_of_the_right_answer_still_grades_correct(self, question: Any) -> None:
        assert av.PROBLEMS[question.id].grade(question.paraphrase)["is_correct"] is True

    def test_an_empty_answer_is_no_answer_not_a_pass(self) -> None:
        for problem in av.PROBLEMS.values():
            graded = problem.grade("")
            assert graded["is_correct"] is False
            assert graded["verdict"] == av.VERDICT_NO_ANSWER

    def test_hedging_across_both_candidates_does_not_score(self) -> None:
        """ "blue-1 or blue-2" is not an answer; a grader that takes it measures nothing."""
        graded = av.PROBLEMS["closest"].grade("either blue-1 or blue-2, hard to say")
        assert graded["is_correct"] is False

    def test_the_confabulation_trap_rejects_an_invented_rival(self) -> None:
        """The fogged board shows no rival; naming one is the failure this rung hunts."""
        graded = av.PROBLEMS["rival"].grade("red-1 is at the eastern edge")
        assert graded["is_correct"] is False
        assert av.PROBLEMS["rival"].grade("NONE VISIBLE")["is_correct"] is True

    def test_the_scripted_answer_never_scores(self) -> None:
        """A hermetic run must not be mistakable for data."""
        for problem in av.PROBLEMS.values():
            assert problem.grade(aa.SCRIPTED_ANSWER)["is_correct"] is False

    def test_the_rung_is_heterogeneous_so_the_hybrid_cell_is_gradeable(self) -> None:
        assert av.RUNG.heterogeneous(av.PROBLEMS) is True

    def test_the_problem_statements_are_reproducible_as_markdown(self) -> None:
        """docs/challenge-problems.md's rule: a problem must be re-authorable."""
        rendered = av.problems_markdown()
        for question in av.QUESTIONS:
            assert question.statement.splitlines()[0] in rendered
            assert str(question.truth(av.RUNG_SNAPSHOT)) in rendered


# ── the harness end to end, hermetically ─────────────────────────────────────


class TestScreenRuns:
    """The screen runs with scripted minds, records everything, and claims nothing."""

    def test_stage1_runs_every_route_on_the_flat_arm_only(self, tmp_path: Path) -> None:
        records = _screen(tmp_path)
        attempts = [r for r in records if r["kind"] == aa.KIND_ATTEMPT]
        assert {r["route"] for r in attempts} == set(av.ROUTE_ORDER)
        assert {r["arm"] for r in attempts} == {aa.ARM_EXISTING}

    def test_every_attempt_carries_its_stage_route_and_snapshot_hash(self, tmp_path: Path) -> None:
        for record in _screen(tmp_path):
            if record["kind"] == aa.KIND_ATTEMPT:
                assert record["stage"] == av.STAGE_SCREEN
                assert record["route"] in av.ROUTE_ORDER
                assert record["snapshot_hash"] == av.RUNG_SNAPSHOT_HASH

    def test_every_model_call_lands_a_record_with_a_finish_reason(self, tmp_path: Path) -> None:
        calls = [r for r in _screen(tmp_path) if r["kind"] == aa.KIND_CALL]
        assert calls
        for call in calls:
            assert "finish_reason" in call
            assert call["route"] in av.ROUTE_ORDER

    def test_the_scripted_screen_separates_on_nothing(self, tmp_path: Path) -> None:
        """Scripted minds answer nothing; the selection must say so, not pick a winner."""
        out = tmp_path / "stage1.jsonl"
        config = aa.load_config()
        log = aa.CallLog()
        av.run_stage1(
            config=config,
            perception=av.load_perception_config(),
            seams=_seams(log, config),
            log=log,
            raw_dir=tmp_path / "raw",
            out=out,
        )
        selection = av.select_route(out)
        assert selection.separated is False
        assert selection.basis in (av.BASIS_COST, av.BASIS_PRECEDENCE)

    def test_stage2_runs_all_four_arms(self, tmp_path: Path) -> None:
        stage1 = _write_screen(
            tmp_path, {av.ROUTE_NATIVE: 4, av.ROUTE_DESCRIBED: 1, av.ROUTE_BOTH: 1}
        )
        report = _run_stage2(tmp_path, stage1)
        attempts = [r for r in report["records"] if r["kind"] == aa.KIND_ATTEMPT]
        assert {r["arm"] for r in attempts} == set(aa.ARM_ORDER)

    def test_analyse_reads_a_stage2_artifact(self, tmp_path: Path) -> None:
        stage1 = _write_screen(
            tmp_path, {av.ROUTE_NATIVE: 4, av.ROUTE_DESCRIBED: 1, av.ROUTE_BOTH: 1}
        )
        out = tmp_path / "stage2.jsonl"
        _run_stage2(tmp_path, stage1, out=out)
        analysis = av.analyse_stage2(out)
        assert analysis["kind"] == aa.KIND_ANALYSIS
        assert analysis["verdict"] in aa.VERDICTS

    def test_the_measured_turns_map_is_committed_beside_its_sidecar(self, tmp_path: Path) -> None:
        _screen(tmp_path)
        raw = tmp_path / "raw"
        assert sorted(raw.glob("*.png")), "no measured turn's map was committed"
        assert sorted(raw.glob("*.json")), "a committed map with no sidecar is unpairable"


# ── the live gate ────────────────────────────────────────────────────────────


class TestNoLiveDial:
    """No live model call happens here, and the gate lives in the harness."""

    def test_the_module_opens_no_socket(self) -> None:
        tree = ast.parse(SOURCE)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in (node.names if isinstance(node, ast.Import) else node.names)
        }
        assert not imported & {"socket", "http", "urllib", "requests", "httpx"}

    def test_the_live_flag_is_refused_without_the_gate(self, monkeypatch: Any) -> None:
        monkeypatch.delenv(aa.LIVE_GATE_ENV, raising=False)
        code = av.main(["stage1", "--raw-dir", "/tmp/av", "--live"])  # nosec B108
        assert code == 2

    def test_the_live_lane_is_refused_even_with_the_gate_open(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(aa.LIVE_GATE_ENV, "1")
        code = av.main(["stage1", "--raw-dir", str(tmp_path), "--live"])
        assert code == 2


class TestCli:
    """The agent-first surface: results to stdout, errors to stderr with a hint."""

    def test_plan_prints_the_routes_and_the_rule(self, capsys: Any) -> None:
        assert av.main(["plan"]) == 0
        out = capsys.readouterr().out
        for route in av.ROUTE_ORDER:
            assert route in out
        assert av.RUNG_ID in out

    def test_plan_json_carries_the_exemption_and_the_capability_sources(self, capsys: Any) -> None:
        assert av.main(["plan", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["exemption"]["applies_to_rung"] == av.RUNG_ID
        assert payload["capability_facts"]

    def test_stage1_then_select_then_stage2_round_trips(self, tmp_path: Path, capsys: Any) -> None:
        stage1 = tmp_path / "s1.jsonl"
        assert av.main(["stage1", "--raw-dir", str(tmp_path / "raw"), "--out", str(stage1)]) == 0
        capsys.readouterr()
        assert av.main(["select", "--stage1", str(stage1)]) == 0
        selection = json.loads(capsys.readouterr().out)
        stage2 = tmp_path / "s2.jsonl"
        assert (
            av.main(
                [
                    "stage2",
                    "--stage1",
                    str(stage1),
                    "--raw-dir",
                    str(tmp_path / "raw2"),
                    "--out",
                    str(stage2),
                ]
            )
            == 0
        )
        capsys.readouterr()
        assert av.main(["verify", "--stage1", str(stage1), "--stage2", str(stage2)]) == 0
        verified = json.loads(capsys.readouterr().out)
        assert verified["route"] == selection["route"]

    def test_a_missing_stage1_artifact_is_an_error_not_a_traceback(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        assert av.main(["select", "--stage1", str(tmp_path / "nope.jsonl")]) == 2
        err = capsys.readouterr().err
        assert err.startswith("error: ")
        assert "hint: " in err
