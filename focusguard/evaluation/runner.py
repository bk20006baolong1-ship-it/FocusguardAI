"""Runs the evaluation suite and returns a JSON-serialisable report.

Synthetic results are always labelled ``SYNTHETIC``. The real-world section is
``NOT_EVALUATED`` unless a labelled dataset is supplied (see
``evaluate_labelled_dataset``), and that dataset is not part of this repo.
"""
import json
import os
import platform
from datetime import datetime, timezone

from . import NOT_EVALUATED, SYNTHETIC, EVALUATED
from .metrics import behavior_metrics, fps_consistency, identity_metrics, recovery_times
from .synthetic import (STANDARD_BEHAVIOR_SCRIPT, STANDARD_DURATION, behavior_observations,
                        ground_truth, identity_scenario)
from ..behavior import BehaviorAnalyzer
from ..config import DEFAULT_CONFIG, PipelineConfig
from ..focus_engine import FocusEngine
from ..identity import IdentityManager
from ..session_runtime import SessionRuntime


def run_behavior(fps: float, config: PipelineConfig = DEFAULT_CONFIG, jitter: float = 0.0):
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "synthetic"}], started_at=1_000_000.0,
                        config=config, source="synthetic")
    for obs in behavior_observations(fps, STANDARD_DURATION, STANDARD_BEHAVIOR_SCRIPT, jitter=jitter):
        rt.process_frame(obs.timestamp, {1: obs})
    rt.end(1_000_000.0 + STANDARD_DURATION)
    pred = [e.to_dict() for e in rt.episodes]
    st = rt.states[1]
    return pred, st.focus_score, st


def run_identity(config: PipelineConfig = DEFAULT_CONFIG):
    gallery, frames = identity_scenario()
    mgr = IdentityManager(gallery, config.identity)
    rows = []
    for t, items in frames:
        decisions = mgr.process_frame([(tid, emb) for tid, emb, _ in items], t)
        for tid, _, gt in items:
            d = decisions[tid]
            rows.append({"t": t, "track_id": tid, "gt_student_id": gt,
                         "pred_student_id": d.student_id if d.status == "CONFIRMED" else None})
    metrics = identity_metrics(rows)
    metrics["recovery_seconds"] = recovery_times(rows, {1: 1_000_000.0 + 9.0})
    metrics["events"] = {k: sum(1 for e in mgr.events if e.kind == k)
                         for k in ("CONFIRMED", "RECOVERED", "RELEASED", "CONFLICT", "LOST")}
    return metrics


def run_synthetic_suite(config: PipelineConfig = DEFAULT_CONFIG) -> dict:
    gt = ground_truth(STANDARD_BEHAVIOR_SCRIPT)
    behavior = {}
    finals = {}
    for fps in (5.0, 15.0, 30.0):
        pred, score, _ = run_behavior(fps, config)
        behavior[str(int(fps))] = behavior_metrics(pred, gt, STANDARD_DURATION)
        finals[fps] = score
    jitter_pred, jitter_score, _ = run_behavior(15.0, config, jitter=0.3)
    return {
        "dataset_kind": SYNTHETIC,
        "behavior_by_fps": behavior,
        "behavior_with_timestamp_jitter_15fps": behavior_metrics(jitter_pred, gt, STANDARD_DURATION),
        "fps_consistency_final_score": fps_consistency(finals),
        "identity": run_identity(config),
        "notes": "Generated signals only; validates timing/identity logic, not real-world accuracy.",
    }


def evaluate_labelled_dataset(path: str = None) -> dict:
    """Hook for a real, labelled classroom dataset (not included).

    Expected format: JSON with ``episodes_gt``, ``episodes_pred`` and
    ``identity_frames`` produced by running the camera pipeline on recorded,
    consented footage. Without it the status is NOT_EVALUATED.
    """
    if not path or not os.path.exists(path):
        return {"status": NOT_EVALUATED,
                "reason": "No labelled real-world classroom dataset is available in this repository."}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "status": EVALUATED,
        "dataset": os.path.basename(path),
        "behavior": behavior_metrics(data["episodes_pred"], data["episodes_gt"], data["duration_seconds"]),
        "identity": identity_metrics(data["identity_frames"]),
    }


def build_report(real_dataset_path: str = None, config: PipelineConfig = DEFAULT_CONFIG) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "config": config.to_dict(),
        "synthetic": run_synthetic_suite(config),
        "real_world": evaluate_labelled_dataset(real_dataset_path),
        "hardware": {"status": "HARDWARE_REQUIRED",
                     "reason": "Camera, YOLOv8, MediaPipe and InsightFace throughput are not measured in CI."},
    }
