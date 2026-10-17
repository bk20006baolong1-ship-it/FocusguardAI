#!/usr/bin/env python3
"""Classroom performance benchmark (hardware-free part of the pipeline).

    python scripts/benchmark_classroom.py [--students 5,10,20] [--seconds 20] [--fps 10]
                                          [--resolution 1280x720] [--heavy] [--output benchmark.json]

Measures IdentityManager + SessionRuntime (+ BehaviorAnalyzer + FocusEngine)
per frame with N synthetic students. The detectors (YOLOv8, InsightFace,
MediaPipe) are NOT executed here, so ``input_resolution`` is nominal and the
camera-side cost is HARDWARE_REQUIRED (tools/run_classroom_evaluation.py
measures it on a real machine).
"""
import argparse
import json
import os
import platform
import resource
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from focusguard.evaluation import SYNTHETIC  # noqa: E402
from focusguard.evaluation.runner import run_behavior  # noqa: E402
from focusguard.evaluation.metrics import behavior_metrics  # noqa: E402
from focusguard.evaluation.stress import run_scenario  # noqa: E402
from focusguard.evaluation.synthetic import STANDARD_BEHAVIOR_SCRIPT, STANDARD_DURATION, ground_truth  # noqa: E402


def crowd_spec(rel, sid):
    if (int(rel * 10) + sid * 13) % 53 == 0:
        return None
    return (sid, sid, 0.06, False)


def event_latency(fps):
    pred, _, _ = run_behavior(fps)
    m = behavior_metrics(pred, ground_truth(STANDARD_BEHAVIOR_SCRIPT), STANDARD_DURATION)
    return {t: v["mean_latency_seconds"] for t, v in m["per_type"].items()}


def bench(n, seconds, fps, resolution):
    ru0, w0 = resource.getrusage(resource.RUSAGE_SELF), time.perf_counter()
    r = run_scenario(n, seconds, fps, crowd_spec, seed=100 + n)
    ru1, w1 = resource.getrusage(resource.RUSAGE_SELF), time.perf_counter()
    perf = r["performance"]
    cpu = (ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime)
    mean_ms = perf["frame_latency_ms_mean"] or 0.0
    return {
        "student_count": n,
        "input_resolution": f"{resolution} (nominal: detectors not executed in this benchmark)",
        "frames": perf["frames"],
        "processing_fps": round(1000.0 / mean_ms, 1) if mean_ms > 0 else None,
        "frame_latency_p50": perf["frame_latency_ms_p50"],
        "frame_latency_p95": perf["frame_latency_ms_p95"],
        "identity_latency": {"p50": perf["identity_latency_ms_p50"], "p95": perf["identity_latency_ms_p95"]},
        "latency_unit": "ms",
        "cpu_usage_if_available": round(100.0 * cpu / (w1 - w0), 1) if w1 > w0 else None,
        "memory_usage_if_available": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1),
        "memory_unit": "MB (peak RSS of the process)",
        "identity_contract": {"wrong_assignments": r["identity"]["wrong_assignments"],
                              "duplicate_identity_frames": r["identity"]["duplicate_identity_frames"],
                              "never_confirmed": r["identity"]["never_confirmed"]},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", default="5,10,20")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--resolution", default="1280x720")
    ap.add_argument("--heavy", action="store_true", help="5,10,20,40 students x 60 s (workflow_dispatch)")
    ap.add_argument("--output", default=os.path.join(ROOT, "evaluation", "reports", "benchmark.json"))
    a = ap.parse_args(argv)
    counts = [5, 10, 20, 40] if a.heavy else [int(x) for x in a.students.split(",") if x.strip()]
    seconds = 60.0 if a.heavy else a.seconds
    results = [bench(n, seconds, a.fps, a.resolution) for n in counts]
    report = {
        "dataset_kind": SYNTHETIC, "mode": "heavy" if a.heavy else "lightweight",
        # This is the RUNTIME benchmark (identity + behaviour + focus + session per frame).
        # It is NOT the full detector camera benchmark (YOLOv8 + ByteTrack + InsightFace + MediaPipe).
        "benchmark_kind": "RUNTIME_BENCHMARK",
        "full_detector_camera_benchmark": {
            "status": "HARDWARE_REQUIRED",
            "how": "python tools/field_test.py --class-id <id> --teacher <username> (performance-report.json: "
                   "benchmark_kind FULL_DETECTOR_CAMERA, measured per frame on the classroom machine)"},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": platform.python_version(), "machine": platform.machine(), "platform": platform.platform(),
        "simulated_fps": a.fps, "seconds_per_run": seconds,
        "event_latency": {"unit": "seconds from behaviour onset to event (algorithmic, by design of the "
                                  "temporal thresholds)", "by_type": event_latency(a.fps)},
        "results": results,
        "hardware": {"status": "HARDWARE_REQUIRED",
                     "reason": "Camera capture, YOLOv8, InsightFace and MediaPipe throughput must be measured on "
                               "the classroom machine (tools/run_classroom_evaluation.py)."},
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    with open(a.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    for r in results:
        print(f"[SYNTHETIC][RUNTIME_BENCHMARK] {r['student_count']:>3} students: {r['processing_fps']} fps runtime-only, "
              f"p95 {r['frame_latency_p95']} ms, wrong={r['identity_contract']['wrong_assignments']}")
    print(f"benchmark -> {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
