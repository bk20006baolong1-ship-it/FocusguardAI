"""Real classroom evaluation mode, ground truth and real-world metrics (BTC feedback A/B/C)."""
import json
import os

import pytest

from conftest import add_teacher, grant_consent
from focusguard.authz import Principal
from focusguard.behavior import Observation
from focusguard.evaluation.calibration import calibrate
from focusguard.evaluation.real_classroom.metrics import (NOT_ENOUGH_DATA, behavior_report, confusion_matrix,
                                                          evaluate_package, identity_report, performance_report)
from focusguard.evaluation.real_classroom.models import GT_LABELS, clean_metadata
from focusguard.evaluation.real_classroom.service import EvaluationError
from focusguard.evaluation.replay import generate_package

TEACHER = Principal(2, "teacher", frozenset({1}))
T0 = 1_700_000_000.0


# --------------------------------------------------------------- metadata
def test_metadata_fields_and_validation():
    m = clean_metadata({"camera_position": "FRONT_CORNER", "camera_angle": "cao 2.2m", "resolution": "1280x720",
                        "fps_target": "7.5", "device": "MacBook", "student_count": "8", "lighting_condition": "DAYLIGHT",
                        "activity_context": "WRITING", "notes": "x", "consent_verified": True})
    assert m["fps_target"] == 7.5 and m["student_count"] == 8 and m["activity_context"] == "WRITING"
    for bad in ({"camera_position": "CEILING_DRONE"}, {"resolution": "big"}, {"fps_target": "999"},
                {"activity_context": "PARTY"}, {"lighting_condition": "DISCO"}, {"dataset_kind": "FAKE"}):
        with pytest.raises(ValueError):
            clean_metadata(bad)


def test_ground_truth_labels_cover_the_required_minimum():
    assert set(GT_LABELS) >= {"FOCUSED", "WRITING", "LOOKING_AT_BOARD", "GROUP_DISCUSSION", "PHONE_DISTRACTION",
                              "DROWSY", "AWAY", "UNKNOWN"}


# ---------------------------------------------------------------- service
def test_real_evaluation_requires_consent(manager):
    with pytest.raises(EvaluationError):
        manager.evaluation.start(TEACHER, 1, {"consent_verified": False})
    with pytest.raises(EvaluationError):                      # verified, but no student consented to evaluation
        manager.evaluation.start(TEACHER, 1, {"consent_verified": True})
    grant_consent(manager, 3, evaluation=True)
    es = manager.evaluation.start(TEACHER, 1, {"consent_verified": True, "camera_position": "BACK_CORNER"})
    assert es["status"] == "ACTIVE" and es["consent_verified"] is True and es["consent_verified_by"] == 2


def test_annotations_are_stored_separately_from_ai_predictions(manager, clock, repo):
    grant_consent(manager, 3, evaluation=True)
    rt = manager.start_class(1, mode="classroom")
    es = manager.evaluation.start(TEACHER, 1, {"consent_verified": True, "activity_context": "LECTURE"})
    eid = es["evaluation_session_id"]
    start = clock.t
    for i in range(50):
        t = clock.advance(0.2)
        rt.process_frame(t, {3: Observation(timestamp=t, visible=True, face_visible=True, ear=0.3, yaw=0.0, pitch=0.0,
                                            phone_confidence=0.9 if 10 <= i < 40 else None)})
    ann = manager.evaluation.annotate(TEACHER, eid, {"student_id": 3, "label": "PHONE_DISTRACTION",
                                                     "started_at": start + 2, "ended_at": start + 8,
                                                     "annotated_by": 999})
    assert ann["annotated_by"] == 2                           # annotator from the session, not the body
    manager.evaluation.end(TEACHER, eid)
    manager.end_class(1)
    raw = repo.get_focus_events(class_session_id=rt.class_session_id)
    assert [e["type"] for e in raw] == ["PHONE"] and "label" not in raw[0]
    assert [a["label"] for a in repo.get_annotations(evaluation_session_id=eid)] == ["PHONE_DISTRACTION"]
    pkg = manager.evaluation.package(eid)
    assert pkg["predictions"][0]["type"] == "PHONE" and pkg["annotations"][0]["label"] == "PHONE_DISTRACTION"
    assert pkg["observations"] and pkg["context_segments"][0]["context"] == "LECTURE"
    assert all(o["student_id"] == 3 for o in pkg["observations"])


def test_students_without_evaluation_consent_are_excluded(manager, clock):
    grant_consent(manager, 3, evaluation=True)
    rt = manager.start_class(1, mode="classroom")
    es = manager.evaluation.start(TEACHER, 1, {"consent_verified": True})
    eid = es["evaluation_session_id"]
    for i in range(30):
        t = clock.advance(0.2)
        rt.process_frame(t, {s: Observation(timestamp=t, visible=True, ear=0.3, yaw=0, pitch=0) for s in (3, 4)})
    with pytest.raises(EvaluationError):
        manager.evaluation.annotate(TEACHER, eid, {"student_id": 4, "label": "FOCUSED",
                                                   "started_at": clock.t - 5, "ended_at": clock.t})
    pkg = manager.evaluation.package(eid)
    assert pkg["enrolled"] == [3] and {o["student_id"] for o in pkg["observations"]} == {3}


def test_annotation_validation(manager, clock):
    grant_consent(manager, 3, evaluation=True)
    eid = manager.evaluation.start(TEACHER, 1, {"consent_verified": True})["evaluation_session_id"]
    clock.advance(60)
    for bad in ({"student_id": 3, "label": "BORED", "started_at": clock.t - 10, "ended_at": clock.t},
                {"student_id": 3, "label": "FOCUSED", "started_at": clock.t, "ended_at": clock.t - 10},
                {"student_id": 3, "label": "FOCUSED", "started_at": clock.t - 9999, "ended_at": clock.t},
                {"student_id": 99, "label": "FOCUSED", "started_at": clock.t - 10, "ended_at": clock.t}):
        with pytest.raises(EvaluationError):
            manager.evaluation.annotate(TEACHER, eid, bad)


def test_evaluation_api_authorisation_and_flow(login_as, manager, repo):
    grant_consent(manager, 3, evaluation=True)
    t = login_as("teacher")
    assert t.post("/api/teacher/evaluations", json={"class_id": 1, "consent_verified": False}).status_code == 400
    es = t.post("/api/teacher/evaluations", json={"class_id": 1, "consent_verified": True,
                                                  "camera_position": "FRONT_CORNER"}).get_json()
    eid = es["session"]["evaluation_session_id"]
    assert [p["student_id"] for p in es["participants"]] == [3]
    assert t.post(f"/api/teacher/evaluations/{eid}/context", json={"context": "WRITING"}).status_code == 200
    assert t.post(f"/api/teacher/evaluations/{eid}/context", json={"context": "NAP"}).status_code == 400
    add_teacher(repo)
    for user in ("teacher2", "hocsinh", "phuhuynh", "admin"):
        c = login_as(user)
        assert c.get(f"/api/teacher/evaluations/{eid}").status_code == 403, user
        assert c.post(f"/api/teacher/evaluations/{eid}/annotations", json={}).status_code == 403, user
    m = t.get(f"/api/teacher/evaluations/{eid}/metrics").get_json()
    assert m["evaluation_status"] == NOT_ENOUGH_DATA and m["is_real_world_evidence"] is False
    assert m["behavior"]["per_behavior"]["PHONE"]["precision"] == NOT_ENOUGH_DATA
    exp = t.get(f"/api/teacher/evaluations/{eid}/export")
    assert exp.status_code == 200 and json.loads(exp.get_data())["format"].startswith("focusguard-evaluation-package")
    assert t.post(f"/api/teacher/evaluations/{eid}/end").status_code == 200
    assert t.post(f"/api/teacher/evaluations/{eid}/end").status_code == 409
    assert t.get("/api/teacher/evaluations/not-a-hex-id").status_code == 404


def test_standalone_evaluation_records_context_segments(manager, clock, repo):
    grant_consent(manager, 3, evaluation=True)
    eid = manager.evaluation.start(TEACHER, 1, {"consent_verified": True, "activity_context": "LECTURE"})["evaluation_session_id"]
    clock.advance(30)
    manager.evaluation.set_context(TEACHER, eid, "GROUP_DISCUSSION")
    clock.advance(30)
    manager.evaluation.end(TEACHER, eid)
    segs = manager.evaluation.package(eid)["context_segments"]
    assert [s["context"] for s in segs] == ["LECTURE", "GROUP_DISCUSSION"]


# --------------------------------------------------------- metrics engine
def _pkg(preds, anns, **extra):
    return dict({"dataset_kind": "SYNTHETIC", "metadata": {}, "predictions": preds, "annotations": anns}, **extra)


def _ann(sid, label, a, b):
    return {"student_id": sid, "label": label, "started_at": T0 + a, "ended_at": T0 + b}


def _pred(sid, typ, a, b, detected=None, interp=None):
    return {"student_id": sid, "type": typ, "start_time": T0 + a, "end_time": T0 + b,
            "metadata": {"detected_at": T0 + (detected if detected is not None else a + 1)}, "interpreted_state": interp}


def test_precision_recall_f1_on_a_known_fixture():
    # 3 students x 20 min annotated; 4 phone GT events; AI: 3 hits, 1 miss, 2 false alarms
    anns = []
    for sid in (1, 2, 3):
        anns.append(_ann(sid, "FOCUSED", 0, 1200))
    gts = [(1, 100, 130), (1, 500, 520), (2, 300, 330), (3, 700, 710)]
    for sid, a, b in gts:
        anns.append(_ann(sid, "PHONE_DISTRACTION", a, b))
    preds = [_pred(1, "PHONE", 101, 129, detected=102), _pred(1, "PHONE", 501, 518, detected=503),
             _pred(2, "PHONE", 302, 331, detected=304),
             _pred(2, "PHONE", 900, 905), _pred(3, "PHONE", 50, 55),
             _pred(1, "PHONE", 5000, 5010)]        # outside annotated time: not scored
    rep = behavior_report(_pkg(preds, anns))["per_behavior"]["PHONE"]
    assert (rep["true_positives"], rep["false_positives"], rep["false_negatives"]) == (3, 2, 1)
    assert rep["precision"] == 0.6 and rep["recall"] == 0.75 and rep["f1"] == pytest.approx(0.6667, abs=1e-4)
    assert rep["false_positives_per_hour"] == pytest.approx(2.0, abs=1e-3)        # 2 FP / 1 student-hour
    assert rep["event_onset_latency_seconds"]["mean"] == pytest.approx((2 + 3 + 4) / 3, abs=1e-3)
    assert rep["false_positives_by_ground_truth_label"] == {"FOCUSED": 2}


def test_insufficient_ground_truth_reports_not_enough_data_not_zero():
    anns = [_ann(1, "FOCUSED", 0, 60), _ann(1, "DROWSY", 10, 20)]
    preds = [_pred(1, "DROWSY", 11, 19)]
    rep = behavior_report(_pkg(preds, anns))["per_behavior"]
    assert rep["DROWSY"]["true_positives"] == 1
    for k in ("precision", "recall", "f1", "false_positives_per_hour"):
        assert rep["DROWSY"][k] == NOT_ENOUGH_DATA
    assert rep["PHONE"]["precision"] == NOT_ENOUGH_DATA and rep["PHONE"]["false_positives"] == 0
    full = evaluate_package(_pkg(preds, anns))
    assert full["status"] == NOT_ENOUGH_DATA and full["identity"]["status"] == NOT_ENOUGH_DATA
    assert full["performance"]["status"] == NOT_ENOUGH_DATA


def test_context_aware_view_drops_expected_signals():
    anns = [_ann(s, "WRITING", 0, 1200) for s in (1, 2, 3, 4)]
    preds = [_pred(s, "HEAD_AWAY", 100, 110, interp="EXPECTED_IN_CONTEXT") for s in (1, 2, 3, 4)]
    rep = behavior_report(_pkg(preds, anns))
    assert rep["per_behavior"]["HEAD_AWAY"]["false_positives"] == 4
    assert rep["per_behavior"]["HEAD_AWAY"]["false_positives_by_ground_truth_label"] == {"WRITING": 4}
    assert rep["per_behavior_context_aware"]["HEAD_AWAY"]["false_positives"] == 0


def test_confusion_matrix_is_time_sampled():
    anns = [_ann(1, "FOCUSED", 0, 10), _ann(1, "PHONE_DISTRACTION", 10, 20)]
    cm = confusion_matrix(_pkg([_pred(1, "PHONE", 12, 20)], anns))
    assert cm["rows"]["FOCUSED"]["NO_EVENT"] == 10
    assert cm["rows"]["PHONE_DISTRACTION"]["PHONE"] == 8 and cm["rows"]["PHONE_DISTRACTION"]["NO_EVENT"] == 2


def test_identity_metrics_switches_unknown_recovery():
    frames = []
    for i in range(100):
        t = T0 + i * 0.1
        pred = None if 20 <= i < 25 else (1 if i < 60 else 2)          # unknown for 0.5 s, then a wrong switch
        frames.append({"t": t, "track_id": 1, "gt_student_id": 1, "pred_student_id": pred})
    for i in range(130, 160):                                          # disappears 7 s, comes back, recovered
        frames.append({"t": T0 + i * 0.1, "track_id": 5, "gt_student_id": 1, "pred_student_id": 1})
    frames.append({"t": T0, "track_id": 9, "gt_student_id": None, "pred_student_id": None})
    rep = identity_report(frames)
    assert rep["identity_switch_count"] == 2 and rep["unknown_rate"] == pytest.approx(5 / 130, abs=1e-4)
    assert rep["reappearances"] == 1 and rep["recovery_rate"] == 1.0
    assert rep["stranger_false_accept_rate"] == 0.0 and rep["wrong_assignments"] == 40


def test_performance_metrics():
    perf = {"frame_timestamps": [T0 + i / 10 for i in range(101)], "frame_latency_ms": list(range(1, 102))}
    rep = performance_report(perf)
    assert rep["fps"] == 10.0 and rep["latency_p50_ms"] == 51.0 and rep["latency_p95_ms"] == 96.0


def test_replay_fixture_is_labelled_and_never_real_world_evidence():
    pkg = generate_package()
    rep = evaluate_package(pkg)
    assert pkg["dataset_kind"] == "REPLAY" and rep["is_real_world_evidence"] is False
    assert rep["behavior"]["per_behavior"]["PHONE"]["f1"] != NOT_ENOUGH_DATA
    raw = rep["behavior"]["per_behavior"]["HEAD_AWAY"]["false_positives"]
    aware = rep["behavior"]["per_behavior_context_aware"]["HEAD_AWAY"]["false_positives"]
    assert aware < raw                                     # the context policy measurably removes false alarms


def test_real_package_with_consent_and_data_is_marked_as_evidence():
    pkg = generate_package()
    pkg["dataset_kind"] = "REAL"
    pkg["metadata"]["consent_verified"] = True
    assert evaluate_package(pkg)["is_real_world_evidence"] is True
    pkg["metadata"]["consent_verified"] = False
    assert evaluate_package(pkg)["is_real_world_evidence"] is False


# ------------------------------------------------------------- calibration
def test_calibration_outputs_recommendation_without_applying():
    pkg = generate_package()
    grid = {"PHONE": {"phone_min_seconds": [0.5, 3.0]}, "HEAD_AWAY": {"head_away_min_seconds": [1.0, 4.0]},
            "DROWSY": {"drowsy_min_seconds": [1.5]}, "AWAY": {"away_min_seconds": [10.0]}}
    from focusguard.config import DEFAULT_CONFIG
    before = DEFAULT_CONFIG.behavior.phone_min_seconds
    rep = calibrate(pkg, grid=grid)
    for key in ("current_config", "recommended_config", "delta", "evidence"):
        assert key in rep
    assert rep["applied"] is False and rep["requires_human_approval"] is True and rep["dataset_kind"] == "REPLAY"
    assert rep["evidence"]["PHONE"]["candidates_evaluated"] == 2
    assert rep["evidence"]["AWAY"]["status"] == NOT_ENOUGH_DATA          # only one AWAY ground-truth event
    assert DEFAULT_CONFIG.behavior.phone_min_seconds == before
    assert rep["head_pose"]["status"] == "OK"


def test_calibration_without_observations_is_not_enough_data():
    rep = calibrate({"dataset_kind": "REAL", "annotations": [], "predictions": []})
    assert rep["status"] == NOT_ENOUGH_DATA and rep["applied"] is False


def test_recorder_writes_numbers_only(manager, clock):
    grant_consent(manager, 3, evaluation=True)
    rt = manager.start_class(1, mode="classroom")
    eid = manager.evaluation.start(TEACHER, 1, {"consent_verified": True})["evaluation_session_id"]
    t = clock.advance(0.2)
    rt.process_frame(t, {3: Observation(timestamp=t, visible=True, ear=0.3, yaw=1.0, pitch=2.0,
                                        extra={"jpeg": b"secret"})})
    manager.evaluation.end(TEACHER, eid)
    path = os.path.join(manager.privacy.evaluation_private_dir, eid, "observations.jsonl")
    text = open(path, encoding="utf-8").read()
    assert '"ear":0.3' in text and "jpeg" not in text and "secret" not in text
