"""voice_latency: per-role/combination samples, the measured gate, the derived deadline."""

import json
import math
import stat

import pytest

from embodiment import voice_latency as vl


def _clock(step=0.5):
    t = [0.0]

    def now():
        t[0] += step
        return t[0]

    return now


def _store(tmp_path, **kw):
    return vl.VoiceLatency(tmp_path / vl.STORE_NAME, **kw)


def test_key_is_role_plus_optional_translator():
    assert vl.combo_key("worker") == "worker"
    assert vl.combo_key("worker", "senses") == "worker+senses"
    assert vl.combo_key("worker", "") == "worker"


def test_unmeasured_until_enough_samples(tmp_path):
    s = _store(tmp_path)
    assert s.state("worker") == vl.UNMEASURED
    assert s.spoken_deadline("worker") is None
    assert s.p90("worker") is None
    for i in range(vl.MEASURED_MIN_SAMPLES - 1):
        s.add_sample("worker", 1.0 + i)
    assert s.state("worker") == vl.UNMEASURED
    s.add_sample("worker", 2.0)
    assert s.state("worker") == vl.MEASURED
    assert s.spoken_deadline("worker") is not None


def test_p90_nearest_rank():
    assert vl.percentile90([float(i) for i in range(1, 11)]) == 9.0
    assert vl.percentile90([]) is None


def test_combination_is_separate_from_role(tmp_path):
    s = _store(tmp_path)
    for _ in range(vl.MEASURED_MIN_SAMPLES):
        s.add_sample("worker", 1.0, translate_via="senses")
    assert s.state("worker", "senses") == vl.MEASURED
    assert s.state("worker") == vl.UNMEASURED


def test_deadline_formula_named_clamped_never_60():
    assert vl.derive_spoken_deadline(None) is None
    assert vl.derive_spoken_deadline(float("nan")) is None
    assert vl.derive_spoken_deadline(-1) is None
    assert vl.derive_spoken_deadline(1.0) == pytest.approx(
        1.0 * vl.DEADLINE_HEADROOM + vl.DEADLINE_SLACK_S
    )
    assert vl.derive_spoken_deadline(0.0) == vl.DEADLINE_FLOOR_S
    assert vl.derive_spoken_deadline(1000.0) == vl.DEADLINE_CAP_S
    assert vl.DEADLINE_CAP_S < 60
    assert math.isfinite(vl.DEADLINE_CAP_S)


def test_probe_records_and_measures(tmp_path):
    s = _store(tmp_path, clock=_clock(0.5))
    prompts = []

    def complete(prompt):
        prompts.append(prompt)

    out = s.probe("worker", complete)
    assert out["state"] == vl.MEASURED
    assert len(prompts) == len(vl.PROBE_PROMPTS) * vl.PROBE_ROUNDS >= vl.MEASURED_MIN_SAMPLES
    assert all(p.strip() and len(p) < 120 for p in vl.PROBE_PROMPTS)
    assert s.p90("worker") == pytest.approx(0.5)
    assert s.state("worker") == vl.MEASURED


def test_probe_state_probing_during(tmp_path):
    s = _store(tmp_path)
    seen = []
    s.probe("worker", lambda p: seen.append(s.state("worker")))
    assert set(seen) == {vl.PROBING}
    assert s.state("worker") == vl.MEASURED


def test_probe_first_token_returned_is_kept(tmp_path):
    s = _store(tmp_path, clock=_clock(1.0))
    s.probe("worker", lambda p: 0.25)
    rec = json.loads((tmp_path / vl.STORE_NAME).read_text())["worker"]
    assert rec["samples"][0]["first_token_s"] == 0.25
    assert rec["samples"][0]["completion_s"] == 1.0


def test_failed_probe_never_raises_and_stays_unmeasured(tmp_path):
    s = _store(tmp_path)

    def boom(prompt):
        raise RuntimeError("secret speech text 123")

    out = s.probe("worker", boom)
    assert out["state"] == vl.UNMEASURED
    assert out["failures"] == len(vl.PROBE_PROMPTS) * vl.PROBE_ROUNDS
    assert s.state("worker") == vl.UNMEASURED
    raw = (tmp_path / vl.STORE_NAME).read_text()
    assert "secret" not in raw and "RuntimeError" in raw


def test_partial_failure_keeps_good_samples_but_stays_unmeasured(tmp_path):
    s = _store(tmp_path)
    n = [0]

    def flaky(prompt):
        n[0] += 1
        if n[0] == 1:
            raise TimeoutError()

    out = s.probe("worker", flaky)
    assert out["failures"] == 1
    assert out["state"] == vl.UNMEASURED  # one short of MEASURED_MIN_SAMPLES
    s.add_sample("worker", 1.0)  # a live spoken turn tops it up
    assert s.state("worker") == vl.MEASURED


def test_probe_with_bad_role_never_raises(tmp_path):
    s = _store(tmp_path)
    assert s.probe("bad role!", lambda p: None)["state"] == vl.UNMEASURED
    assert s.add_sample("bad role!", 1.0) is False
    assert s.add_sample("worker", float("nan")) is False
    assert s.add_sample("worker", -1.0) is False
    assert s.add_sample("worker", True) is False


def test_persisted_0600_and_reloaded(tmp_path):
    s = _store(tmp_path)
    for _ in range(vl.MEASURED_MIN_SAMPLES):
        s.add_sample("worker", 1.5)
    path = tmp_path / vl.STORE_NAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    again = _store(tmp_path)
    assert again.state("worker") == vl.MEASURED
    assert again.p90("worker") == 1.5


def test_samples_bounded_keeping_most_recent(tmp_path):
    s = _store(tmp_path)
    for i in range(vl.MAX_SAMPLES + 20):
        s.add_sample("worker", float(i))
    rec = json.loads((tmp_path / vl.STORE_NAME).read_text())["worker"]
    assert len(rec["samples"]) == vl.MAX_SAMPLES
    assert rec["samples"][-1]["completion_s"] == float(vl.MAX_SAMPLES + 19)


def test_corrupt_file_set_aside_and_counted(tmp_path):
    (tmp_path / vl.STORE_NAME).write_text("{nope")
    s = _store(tmp_path)
    assert s.state("worker") == vl.UNMEASURED
    assert s.unreadable == 1
    assert (tmp_path / (vl.STORE_NAME + ".unreadable")).exists()


def test_save_failure_counted_not_raised(tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    s = vl.VoiceLatency(blocker / "sub" / vl.STORE_NAME)
    assert s.add_sample("worker", 1.0) is False
    assert s.save_failures == 1


def test_snapshot_lists_combos(tmp_path):
    s = _store(tmp_path)
    s.add_sample("worker", 1.0, translate_via="senses")
    snap = s.snapshot()
    assert snap["worker+senses"]["state"] == vl.UNMEASURED
    assert snap["worker+senses"]["n"] == 1
