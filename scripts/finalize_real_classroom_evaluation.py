#!/usr/bin/env python3
"""Finalize one real-classroom evaluation session into a shareable report.

    python scripts/finalize_real_classroom_evaluation.py <evaluation-session-id>
        [--annotations-csv observer.csv --teacher <teacher-username>]   # import observer labels first
        [--identity-csv identity.csv]                                  # import the identity sheet first
        [--package package.json]                                       # offline: an exported package file
        [--output-dir DIR]                     # default evaluation/reports/real-classroom/<id>/
        [--calibrate]                          # also run the calibration sweep (recommendations only)

Writes evaluation-report.json, evaluation-summary.md, confusion-matrix.json,
identity-report.json, performance-report.json plus metrics.csv,
confusion-matrix.csv and scenarios.csv. Student ids only: no names, no free-text
notes, no images, no embeddings. The privacy scanner runs on the output and the
command fails if anything private slipped in.

Rates are NOT_ENOUGH_DATA (never 0%) below the minimum data in the metrics engine.
``REAL_WORLD_EVIDENCE: YES`` only for a consented REAL session with enough data.
"""
import argparse
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from focusguard.evaluation.real_classroom.report import build_report, write_outputs  # noqa: E402
from focusguard.privacy.artifact_scan import scan_paths  # noqa: E402


def read_csv(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load(args):
    """Returns (package, sm or None)."""
    if args.package:
        if args.annotations_csv or args.identity_csv:
            raise SystemExit("--package is an offline package: import the CSV files with "
                             "tools/run_classroom_evaluation.py import-annotations/import-identity --package first")
        with open(args.package, "r", encoding="utf-8") as f:
            pkg = json.load(f)
        if (pkg.get("metadata") or {}).get("evaluation_session_id") not in (None, args.session_id):
            raise SystemExit("package belongs to another evaluation session")
        return pkg, None
    from focusguard import authz
    from repository import get_repository
    from session_manager import SessionManager
    sm = SessionManager(repo=get_repository())
    es = sm.evaluation.get(args.session_id)
    if es is None:
        raise SystemExit(f"evaluation session {args.session_id} not found")
    if args.annotations_csv:
        teacher = sm.repo.get_user_by_username(args.teacher or "")
        if not teacher or teacher.get("role") != "teacher":
            raise SystemExit("--annotations-csv needs --teacher <teacher username> (the annotator of record)")
        actor = authz.build_principal(teacher["id"], "teacher", sm.repo)
        if not authz.can_run_evaluation(actor, es["class_id"]):
            raise SystemExit("this teacher is not assigned to the evaluated class")
        res = sm.evaluation.import_annotations(actor, args.session_id, read_csv(args.annotations_csv))
        print(f"annotations imported: {res['added']} (skipped {len(res['skipped'])})")
        for s in res["skipped"][:20]:
            print("  skipped", s)
    if args.identity_csv:
        res = sm.evaluation.import_identity(args.session_id, read_csv(args.identity_csv))
        print(f"identity sheet: {res['labelled_frames']}/{res['frames']} frames labelled")
    return sm.evaluation.package(args.session_id), sm


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_id")
    ap.add_argument("--annotations-csv")
    ap.add_argument("--teacher")
    ap.add_argument("--identity-csv")
    ap.add_argument("--package")
    ap.add_argument("--output-dir")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--names-from-db", action="store_true",
                    help="privacy scan also checks every student name in the database (default when using the DB)")
    a = ap.parse_args(argv)
    pkg, sm = load(a)
    report = build_report(pkg, sm)
    if a.calibrate:
        from focusguard.config import PipelineConfig
        from focusguard.evaluation.calibration import calibrate
        c = calibrate(pkg, PipelineConfig.from_env())
        report["calibration"] = {k: c.get(k) for k in ("status", "dataset_kind", "applied", "requires_human_approval",
                                                       "current_config", "recommended_config", "delta", "coverage")}
    out_dir = a.output_dir or os.path.join(ROOT, "evaluation", "reports", "real-classroom", a.session_id)
    files = write_outputs(report, out_dir)
    clean = scan_paths([out_dir], names_from_db=bool(a.names_from_db), repo=sm.repo if sm is not None else None)
    print(f"STATUS: FINALIZED evaluation_session={a.session_id} dataset_kind={report['metadata'].get('dataset_kind')}")
    for f in files:
        print("  ->", os.path.relpath(f, ROOT) if f.startswith(ROOT) else f)
    print("REAL_WORLD_EVIDENCE: " + ("YES" if report["real_world_evidence"] else "NO") + f" ({report['verdict']})")
    if not clean:
        print("ARTIFACT_PRIVACY_CHECK: FAIL — do not share this folder; fix the source and finalize again.")
        return 1
    print("ARTIFACT_PRIVACY_CHECK: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
