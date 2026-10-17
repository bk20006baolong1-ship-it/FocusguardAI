"""Multi-person identity robustness (BTC feedback B) on SYNTHETIC fixtures.

Regression / load / identity-contract checks only: real crossing, occlusion,
camera angle and crowding are HARDWARE_REQUIRED (protocol in docs/)."""
import numpy as np
import pytest

from focusguard.evaluation import stress
from focusguard.evaluation.synthetic import noisy, random_gallery
from focusguard.identity import CONFIRMED, CONFLICT, IdentityManager


@pytest.mark.parametrize("fn", stress.SCENARIOS, ids=lambda f: f.__name__)
def test_every_stress_scenario_meets_its_contract(fn):
    r = fn()
    assert r["dataset_kind"] == "SYNTHETIC"
    assert r["passed"], {k: v for k, v in r["checks"].items() if not v}
    assert r["identity"]["duplicate_identity_frames"] == 0


def test_crossing_never_assigns_the_wrong_student():
    r = stress.s1_crossing()
    assert r["identity"]["wrong_assignments"] == 0 and r["identity"]["identity_switches"] == 0


def test_stranger_is_never_accepted():
    assert stress.s4_stranger()["identity"]["stranger_false_accept_rate"] == 0.0


def test_track_replacement_recovers_identity():
    r = stress.s2_track_id_change()
    assert r["checks"]["student3_recovered_on_new_track"] and r["identity_events"]["CONFIRMED"] >= 4


def test_occlusion_keeps_the_lock():
    assert stress.s5_face_occlusion()["checks"]["lock_kept_during_occlusion"]
    assert stress.s6_phone_covers_face()["checks"]["lock_kept"]


def test_same_face_on_two_live_tracks_is_a_conflict_not_a_duplicate():
    rng = np.random.default_rng(3)
    gal = random_gallery(3, seed=3)
    mgr = IdentityManager(gal)
    t = 1_000_000.0
    for i in range(10):
        mgr.process_frame([(1, noisy(gal[1], 0.05, rng))], t + i * 0.1)
    assert mgr.student_for_track(1) == 1
    for i in range(10, 30):
        d = mgr.process_frame([(1, noisy(gal[1], 0.05, rng)), (2, noisy(gal[1], 0.05, rng))], t + i * 0.1)
    assert d[1].status == CONFIRMED and d[2].status == CONFLICT
    assert mgr.bindings() == {1: 1}
    assert any(e.kind == "CONFLICT" for e in mgr.events)


def test_twenty_student_workload_meets_the_performance_budget():
    r = stress.s10_twenty()
    perf = r["performance"]
    assert perf["students"] == 20 and perf["frame_latency_ms_p95"] <= perf["budget_ms_p95"]
    assert r["never_confirmed"] == []


def test_run_all_is_labelled_synthetic_not_real_world():
    out = stress.run_all()
    assert out["dataset_kind"] == "SYNTHETIC" and "Not real-world accuracy" in out["note"]
    assert len(out["scenarios"]) == 10 and out["passed"]
