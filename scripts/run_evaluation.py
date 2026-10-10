#!/usr/bin/env python3
"""Run the FocusGuard evaluation suite and write a JSON report.

    python scripts/run_evaluation.py [--output evaluation/reports/latest.json]
                                     [--real-dataset path/to/labelled.json]

Synthetic results are labelled SYNTHETIC. The real-world section stays
NOT_EVALUATED unless a labelled, consented dataset is supplied (kept outside
the repository, see .gitignore: evaluation/private/, evaluation/datasets/).
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from focusguard.evaluation.runner import build_report  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=os.path.join(ROOT, "evaluation", "reports", "latest.json"))
    ap.add_argument("--real-dataset", default=None)
    args = ap.parse_args()
    report = build_report(args.real_dataset)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    syn = report["synthetic"]
    print(f"[SYNTHETIC] behaviour F1 @5/15/30 FPS: "
          + ", ".join(f"{k}fps={v['overall']['f1']}" for k, v in syn["behavior_by_fps"].items()))
    print(f"[SYNTHETIC] FPS consistency (final score max diff): {syn['fps_consistency_final_score']['max_abs_diff']}")
    print(f"[SYNTHETIC] identity accuracy={syn['identity']['accuracy_when_assigned']} "
          f"switches={syn['identity']['identity_switches']} duplicates={syn['identity']['duplicate_identity_frames']}")
    print(f"[REAL WORLD] {report['real_world']['status']}")
    print(f"report -> {args.output}")


if __name__ == "__main__":
    main()
