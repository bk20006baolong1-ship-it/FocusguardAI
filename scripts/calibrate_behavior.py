#!/usr/bin/env python3
"""Calibrate behaviour thresholds and context penalties against ground truth.

    python scripts/calibrate_behavior.py --evaluation-session <id>   # from the FocusGuard database
    python scripts/calibrate_behavior.py --package path/to/package.json
    python scripts/calibrate_behavior.py --replay-fixture             # generated data (cloud CI)
        [--grid quick|full] [--output-dir evaluation/reports] [--output file.json]

Writes ``calibration-<timestamp>.json`` and a Markdown summary next to it with
``current_config``, ``recommended_config``, ``delta`` and ``evidence``. It
NEVER changes the running configuration: applying a recommendation is a human
decision (FG_* environment variables / focusguard/policies/behavior_policy.json).
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from focusguard.config import PipelineConfig  # noqa: E402
from focusguard.evaluation.calibration import DEFAULT_GRID, calibrate  # noqa: E402

QUICK_GRID = {
    "PHONE": {"phone_min_seconds": [0.5, 1.0, 2.0]},
    "DROWSY": {"drowsy_min_seconds": [1.0, 1.5, 3.0]},
    "HEAD_AWAY": {"head_away_min_seconds": [1.0, 2.0, 4.0], "yaw_limit_deg": [20.0, 30.0]},
    "AWAY": {"away_min_seconds": [5.0, 10.0, 15.0]},
}


def load_package(args):
    if args.package:
        with open(args.package, "r", encoding="utf-8") as f:
            return json.load(f), None
    if args.replay_fixture:
        from focusguard.evaluation.replay import generate_package
        return generate_package(), None
    from repository import get_repository
    from session_manager import SessionManager
    sm = SessionManager(repo=get_repository())
    pkg = sm.evaluation.package(args.evaluation_session)
    feedback = sm.reviews.class_feedback_metrics(pkg["metadata"]["class_id"])
    return pkg, feedback


def markdown(report, source):
    lines = [f"# Calibration summary ({report['dataset_kind']})", "",
             f"* Source: `{source}`", f"* Status: **{report['status']}**",
             f"* Applied: **{report['applied']}** (requires human approval: {report['requires_human_approval']})",
             f"* Objective: {report.get('objective', '-')}", f"* Policy: `{report.get('policy_version')}`", ""]
    if report["dataset_kind"] != "REAL":
        lines += ["> Generated data. These numbers validate the tool, not real-world calibration.", ""]
    cov = report.get("coverage") or {}
    lines += [f"* Samples (observation rows): {report.get('sample_count', 0)} · annotated student-seconds: "
              f"{cov.get('annotated_student_seconds')} · frames: {cov.get('frames')}", ""]
    lines += ["| Behaviour | Status | Confidence | GT events | Current F1 | Recommended F1 | Recommended values |",
              "|---|---|---|---|---|---|---|"]
    for beh, e in report.get("evidence", {}).items():
        cur = e.get("current_metrics", {}).get("f1")
        rec = e.get("recommended_metrics", {}).get("f1", "-")
        lines.append(f"| {beh} | {e['status']} | {e.get('confidence', '-')} | {e.get('ground_truth_events', '-')} | "
                     f"{cur} | {rec} | `{json.dumps(e.get('recommended_values', {}))}` |")
    lines += ["", "## Delta", ""]
    if report["delta"]:
        for k, v in report["delta"].items():
            lines.append(f"* `{k}`: {v['current']} → {v['recommended']}")
    else:
        lines.append("* No change recommended.")
    hp = report.get("head_pose", {})
    lines += ["", f"## Head pose reference: {hp.get('status')}", "", f"* current: `{json.dumps(hp.get('current'))}`",
              f"* recommended: `{json.dumps(hp.get('recommended'))}`", "", "## Context penalty multipliers", "",
              "| Context | Behaviour | Current | Recommended | Scored events |", "|---|---|---|---|---|"]
    for ctx, per in (report.get("penalty", {}).get("by_context") or {}).items():
        for beh, v in per.items():
            lines.append(f"| {ctx} | {beh} | {v['current_multiplier']} | {v['recommended_multiplier']} | "
                         f"{v['scored_events']} |")
    if report.get("teacher_review_feedback"):
        lines += ["", "## Teacher review feedback", ""]
        for beh, v in report["teacher_review_feedback"]["per_behavior"].items():
            lines.append(f"* {beh}: reviewed {v['reviewed']}/{v['ai_events']}, confirmed_rate {v['confirmed_rate']},"
                         f" false_positive_rate {v['false_positive_rate']}")
    lines += ["", report.get("note", "")]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--evaluation-session")
    src.add_argument("--package")
    src.add_argument("--replay-fixture", action="store_true")
    ap.add_argument("--grid", choices=("quick", "full"), default="full")
    ap.add_argument("--output-dir", default=os.path.join(ROOT, "evaluation", "reports"))
    ap.add_argument("--output", default=None)
    a = ap.parse_args(argv)
    pkg, feedback = load_package(a)
    t = time.time()
    report = calibrate(pkg, PipelineConfig.from_env(), grid=QUICK_GRID if a.grid == "quick" else DEFAULT_GRID)
    report["grid"] = a.grid
    report["runtime_seconds"] = round(time.time() - t, 1)
    if feedback is not None:
        report["teacher_review_feedback"] = feedback
    out = a.output or os.path.join(a.output_dir, "calibration-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    md = os.path.splitext(out)[0] + ".md"
    source = a.package or ("replay-fixture" if a.replay_fixture else f"evaluation_session={a.evaluation_session}")
    with open(md, "w", encoding="utf-8") as f:
        f.write(markdown(report, source))
    print(f"[{report['dataset_kind']}] calibration {report['status']}: delta={list(report['delta'])} "
          f"(applied={report['applied']})")
    print(f"calibration -> {out}\nsummary -> {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
