import json
import os
import subprocess
import sys

from focusguard.evaluation import NOT_EVALUATED, SYNTHETIC
from focusguard.evaluation.metrics import behavior_metrics, fps_consistency, identity_metrics, match_episodes
from focusguard.evaluation.runner import build_report, evaluate_labelled_dataset

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_behavior_metrics_prf_and_false_alerts():
    gt = [{"type": "PHONE", "start_time": 10, "end_time": 20}, {"type": "DROWSY", "start_time": 30, "end_time": 40}]
    pred = [{"type": "PHONE", "start_time": 11, "end_time": 20, "metadata": {"detected_at": 12}},
            {"type": "HEAD_AWAY", "start_time": 50, "end_time": 55}]
    m = behavior_metrics(pred, gt, duration_seconds=3600)
    assert m["overall"]["tp"] == 1 and m["overall"]["fp"] == 1 and m["overall"]["fn"] == 1
    assert m["overall"]["precision"] == 0.5 and m["overall"]["recall"] == 0.5
    assert m["false_alerts_per_hour"] == 1.0
    assert m["per_type"]["PHONE"]["mean_latency_seconds"] == 2.0


def test_matching_is_one_to_one():
    gt = [{"type": "PHONE", "start_time": 0, "end_time": 10}]
    pred = [{"type": "PHONE", "start_time": 0, "end_time": 10}, {"type": "PHONE", "start_time": 1, "end_time": 9}]
    matches, fp, fn = match_episodes(pred, gt)
    assert len(matches) == 1 and len(fp) == 1 and fn == []


def test_identity_metrics_switches_duplicates_and_strangers():
    frames = [
        {"t": 0, "track_id": 1, "gt_student_id": 1, "pred_student_id": None},
        {"t": 1, "track_id": 1, "gt_student_id": 1, "pred_student_id": 1},
        {"t": 2, "track_id": 1, "gt_student_id": 1, "pred_student_id": 2},
        {"t": 2, "track_id": 2, "gt_student_id": 2, "pred_student_id": 2},
        {"t": 3, "track_id": 9, "gt_student_id": None, "pred_student_id": 3},
    ]
    m = identity_metrics(frames)
    assert m["identity_switches"] == 1
    assert m["duplicate_identity_frames"] == 1
    assert m["stranger_false_accept_rate"] == 1.0
    assert m["mean_time_to_confirmation_seconds"] == 0.5   # (1s for student 1, 0s for student 2)


def test_fps_consistency():
    assert fps_consistency({5: 80.0, 15: 79.0, 30: 79.5})["max_abs_diff"] == 1.0


def test_report_is_labelled_synthetic_and_real_world_not_evaluated():
    report = build_report()
    assert report["synthetic"]["dataset_kind"] == SYNTHETIC
    assert report["real_world"]["status"] == NOT_EVALUATED
    assert report["hardware"]["status"] == "HARDWARE_REQUIRED"
    syn = report["synthetic"]
    for fps in ("5", "15", "30"):
        assert syn["behavior_by_fps"][fps]["overall"]["f1"] == 1.0
        assert syn["behavior_by_fps"][fps]["false_alerts_per_hour"] == 0.0
    assert syn["fps_consistency_final_score"]["max_abs_diff"] <= 1.5
    ident = syn["identity"]
    assert ident["accuracy_when_assigned"] == 1.0
    assert ident["duplicate_identity_frames"] == 0
    assert ident["stranger_false_accept_rate"] == 0.0
    assert ident["recovery_seconds"][1] is not None
    json.dumps(report)  # serialisable


def test_labelled_dataset_hook_without_data_is_not_evaluated(tmp_path):
    assert evaluate_labelled_dataset(None)["status"] == NOT_EVALUATED
    assert evaluate_labelled_dataset(str(tmp_path / "missing.json"))["status"] == NOT_EVALUATED


def test_run_evaluation_script(tmp_path):
    out = tmp_path / "report.json"
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "run_evaluation.py"), "--output", str(out)],
                   cwd=ROOT, check=True, capture_output=True)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["synthetic"]["dataset_kind"] == "SYNTHETIC"
    assert data["real_world"]["status"] == "NOT_EVALUATED"
