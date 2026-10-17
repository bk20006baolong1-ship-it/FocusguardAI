#!/usr/bin/env python3
"""Cloud evaluation bundle for the BTC feedback (no camera, no private data).

    python scripts/run_competition_evaluation.py --output-dir evaluation/reports/ci [--heavy] [--grid quick|full]

Writes:
    evaluation-report.json   synthetic suite + replay ground-truth metrics + stress harness + verification matrix
    evaluation-summary.md    human-readable summary
    benchmark.json           runtime benchmark (lightweight, or --heavy)
    calibration.json         calibration on the replay fixture (+ calibration.md)

Every number here comes from SYNTHETIC/REPLAY data. Real-world accuracy stays
NOT_EVALUATED until a consented, annotated classroom evaluation exists.
"""
import argparse
import json
import os
import platform
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from focusguard.evaluation.real_classroom.metrics import NOT_ENOUGH_DATA, evaluate_package  # noqa: E402
from focusguard.evaluation.replay import generate_package  # noqa: E402
from focusguard.evaluation.runner import build_report  # noqa: E402
from focusguard.evaluation.stress import run_all  # noqa: E402

CLOUD_VERIFIED = "CLOUD_VERIFIED"
HARDWARE_REQUIRED = "HARDWARE_REQUIRED"
HUMAN_GROUND_TRUTH_REQUIRED = "HUMAN_GROUND_TRUTH_REQUIRED"

VERIFICATION_MATRIX = [
    ("Code compiles, imports, unit/contract tests", CLOUD_VERIFIED, "pytest in CI"),
    ("Security / authz / consent / evidence access contracts", CLOUD_VERIFIED, "tests/test_security_btc.py etc."),
    ("Context-aware policy (WRITING/GROUP_DISCUSSION/EXAM...)", CLOUD_VERIFIED, "tests/test_context_policy.py"),
    ("Metrics engine (P/R/F1, FP/h, latency, NOT_ENOUGH_DATA)", CLOUD_VERIFIED, "replay fixture"),
    ("Calibration tool", CLOUD_VERIFIED, "replay fixture (tool works; values not real calibration)"),
    ("Multi-person identity contract (10 synthetic scenarios)", CLOUD_VERIFIED, "focusguard/evaluation/stress.py"),
    ("Runtime performance budget (5/10/20 synthetic students)", CLOUD_VERIFIED, "benchmark.json"),
    ("Firestore backend contract", CLOUD_VERIFIED, "Firestore emulator job"),
    ("Real camera angle / position in a classroom", HARDWARE_REQUIRED, "protocol step 2"),
    ("Real crowded classroom, lighting, students crossing / strangers", HARDWARE_REQUIRED, "protocol scenarios"),
    ("Detector + face model FPS / latency on the classroom machine", HARDWARE_REQUIRED, "run_classroom_evaluation.py"),
    ("Head-pose sign / board direction on the real camera", HARDWARE_REQUIRED, "protocol head calibration"),
    ("Real-world precision / recall / false alerts per hour", HUMAN_GROUND_TRUTH_REQUIRED,
     "consented session + observer timeline"),
    ("Calibrated thresholds and context penalties", HUMAN_GROUND_TRUTH_REQUIRED, "calibrate_behavior.py on REAL data"),
    ("Teacher-confirmed false-positive rate", HUMAN_GROUND_TRUTH_REQUIRED, "teacher reviews in Sự kiện"),
]


def git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return os.environ.get("GITHUB_SHA")


def fmt(v):
    return "NOT_ENOUGH_DATA" if v == NOT_ENOUGH_DATA else ("—" if v is None else v)


def summary_md(report, bench, cal):
    rp = report["replay_ground_truth"]
    lines = ["# FocusGuard – cloud evaluation summary", "",
             f"* Commit: `{report['git_sha']}` · generated {report['generated_at']}",
             "* **All numbers below come from SYNTHETIC / REPLAY data generated in code.** They verify logic, "
             "contracts and performance budgets; they are **not** real-world accuracy.",
             f"* Real-world accuracy: **{report['real_world']['status']}**", "",
             "## Verification matrix", "", "| Item | Status | Evidence |", "|---|---|---|"]
    for item, status, ev in VERIFICATION_MATRIX:
        lines.append(f"| {item} | {status} | {ev} |")
    lines += ["", "## Replay classroom (REPLAY): raw AI signal vs context-aware", "",
              "| Behaviour | Raw P / R / F1 | Raw FP | Context-aware P / R / F1 | Context-aware FP |",
              "|---|---|---|---|---|"]
    raw, ca = rp["behavior"]["per_behavior"], rp["behavior"]["per_behavior_context_aware"]
    for b in raw:
        r, c = raw[b], ca[b]
        lines.append(f"| {b} | {fmt(r['precision'])} / {fmt(r['recall'])} / {fmt(r['f1'])} | {r['false_positives']} | "
                     f"{fmt(c['precision'])} / {fmt(c['recall'])} / {fmt(c['f1'])} | {c['false_positives']} |")
    idn = rp["identity"]
    lines += ["", f"Identity (synthetic): accuracy {fmt(idn.get('identity_match_accuracy'))}, switches "
              f"{idn.get('identity_switch_count')}, unknown rate {fmt(idn.get('unknown_rate'))}, stranger false accept "
              f"{fmt(idn.get('stranger_false_accept_rate'))}.", "",
              "## Multi-person stress harness (SYNTHETIC)", "", "| # | Scenario | Result | Failed checks |", "|---|---|---|---|"]
    for s in report["stress"]["scenarios"]:
        failed = [k for k, v in s["checks"].items() if not v]
        lines.append(f"| {s['scenario']} | {s['name']} | {'PASS' if s['passed'] else 'FAIL'} | {', '.join(failed) or '—'} |")
    lines += ["", "## Benchmark (runtime only, detectors not executed)", "",
              "| Students | FPS | p50 ms | p95 ms | CPU % | Peak RSS MB |", "|---|---|---|---|---|---|"]
    for r in bench["results"]:
        lines.append(f"| {r['student_count']} | {r['processing_fps']} | {r['frame_latency_p50']} | {r['frame_latency_p95']} | "
                     f"{r['cpu_usage_if_available']} | {r['memory_usage_if_available']} |")
    lines += ["", f"## Calibration (REPLAY): {cal['status']}, delta {list(cal['delta']) or 'none'}, applied {cal['applied']}",
              "", "Real classroom steps still required: see docs/REAL_CLASSROOM_EVALUATION_PROTOCOL.md.", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default=os.path.join(ROOT, "evaluation", "reports", "ci"))
    ap.add_argument("--heavy", action="store_true")
    ap.add_argument("--grid", choices=("quick", "full"), default="quick")
    a = ap.parse_args(argv)
    out = a.output_dir
    os.makedirs(out, exist_ok=True)

    import benchmark_classroom
    import calibrate_behavior
    bench_path = os.path.join(out, "benchmark.json")
    benchmark_classroom.main(["--output", bench_path] + (["--heavy"] if a.heavy else []))
    cal_path = os.path.join(out, "calibration.json")
    calibrate_behavior.main(["--replay-fixture", "--grid", a.grid, "--output", cal_path])

    base = build_report()
    replay = evaluate_package(generate_package())
    stress = run_all()
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_sha": git_sha(), "python": platform.python_version(),
        "dataset_policy": "Only SYNTHETIC/REPLAY data generated in code. No images, embeddings, clips or student data.",
        "synthetic": base["synthetic"], "real_world": base["real_world"], "hardware": base["hardware"],
        "replay_ground_truth": replay, "stress": stress,
        "verification": [{"item": i, "status": s, "evidence": e} for i, s, e in VERIFICATION_MATRIX],
        "passed": stress["passed"] and replay["status"] == "EVALUATED" and not replay["is_real_world_evidence"],
    }
    with open(os.path.join(out, "evaluation-report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    bench = json.load(open(bench_path, encoding="utf-8"))
    cal = json.load(open(cal_path, encoding="utf-8"))
    with open(os.path.join(out, "evaluation-summary.md"), "w", encoding="utf-8") as f:
        f.write(summary_md(report, bench, cal))
    print(f"evaluation bundle -> {out} (passed={report['passed']})")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
