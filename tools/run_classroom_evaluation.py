#!/usr/bin/env python3
"""Real classroom evaluation tool (docs/REAL_CLASSROOM_EVALUATION_PROTOCOL.md).

    python tools/run_classroom_evaluation.py start  [--class-id 1 --teacher teacher] [--simulate]   # guided field test
    python tools/run_classroom_evaluation.py check
    python tools/run_classroom_evaluation.py record --class-id 1 --teacher teacher --minutes 20 \\
        --camera-position FRONT_CORNER --camera-angle "góc trái, cao 2.2 m" --lighting DAYLIGHT \\
        --contexts "0:LECTURE,5:WRITING,10:GROUP_DISCUSSION" --consent-verified [--camera 0] [--show]
    python tools/run_classroom_evaluation.py import-annotations --package P.json --csv observer.csv
    python tools/run_classroom_evaluation.py import-annotations --evaluation-session <id> --teacher teacher --csv observer.csv
    python tools/run_classroom_evaluation.py import-identity --package P.json --csv identity.csv
    python tools/run_classroom_evaluation.py import-identity --evaluation-session <id> --csv identity.csv
    python tools/run_classroom_evaluation.py export --evaluation-session <id> --output P.json
    python tools/run_classroom_evaluation.py metrics --package P.json [--output report.json]

``start`` is the guided one-command flow (same as ``tools/field_test.py``: preflight,
consent, camera, 15 BTC scenarios, observer templates, finalize). ``record`` needs
a webcam and the full requirements.txt. Without them it prints
``STATUS: HARDWARE_REQUIRED`` and exits 0 (``--strict`` exits 2), so CI never
fails because a cloud runner has no camera. Everything else works on files.

What is recorded: numbers only (observations, identity decisions per track,
frame timing, AI events, context segments). No image, no video, no face
embedding is written by this tool. Output goes to ``evaluation/private/<id>/``
(git-ignored, deleted after EVALUATION_DATA_RETENTION_DAYS).

Observer CSV (one row per labelled interval, offsets in seconds from the start
of the evaluation): ``student_id,label,start_s,end_s,note``. Labels:
FOCUSED, WRITING, LOOKING_AT_BOARD, GROUP_DISCUSSION, PHONE_DISTRACTION,
DROWSY, AWAY, OFF_TASK_LOOKING_AWAY, UNKNOWN.
"""
import argparse
import csv
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

HARDWARE_REQUIRED = "HARDWARE_REQUIRED"
EXIT_HARDWARE = 2
CONTEXT_KEYS = {ord(str(i + 1)): c for i, c in enumerate(
    ("LECTURE", "WRITING", "LOOKING_AT_BOARD", "GROUP_DISCUSSION", "SELF_STUDY", "EXAM", "TRANSITION"))}


def status(kind, **extra):
    print("STATUS: " + kind + (" " + json.dumps(extra, ensure_ascii=False) if extra else ""))


def hardware_available(camera_index=0):
    """(ok, reason). Never raises."""
    try:
        import cv2  # noqa: F401
    except Exception as exc:
        return False, f"OpenCV not installed ({exc.__class__.__name__}); install requirements.txt"
    for mod in ("mediapipe", "ultralytics", "insightface"):
        try:
            __import__(mod)
        except Exception:
            return False, f"{mod} not installed; install requirements.txt"
    try:
        import cv2
        cap = cv2.VideoCapture(camera_index)
        ok = cap.isOpened() and cap.read()[0]
        cap.release()
    except Exception as exc:
        return False, f"camera error {exc.__class__.__name__}"
    return (True, "camera ok") if ok else (False, f"no frame from camera index {camera_index}")


def hardware_exit(args, reason):
    status(HARDWARE_REQUIRED, reason=reason)
    return EXIT_HARDWARE if getattr(args, "strict", False) else 0


def cmd_check(args):
    ok, reason = hardware_available(args.camera)
    if not ok:
        return hardware_exit(args, reason)
    status("HARDWARE_OK", reason=reason)
    return 0


def parse_contexts(spec):
    out = []
    for part in (spec or "").split(","):
        if not part.strip():
            continue
        minute, ctx = part.split(":", 1)
        out.append((float(minute) * 60.0, ctx.strip().upper()))
    return sorted(out)


def cmd_record(args):
    ok, reason = hardware_available(args.camera)
    if not ok:
        return hardware_exit(args, reason)
    if not args.consent_verified:
        print("Refusing to record: confirm --consent-verified (no hidden recording, consent first).")
        return 3
    os.chdir(ROOT)
    import cv2
    from classroom_ai import ClassroomAI
    from focusguard import authz
    from repository import get_repository
    from session_manager import SessionManager
    sm = SessionManager(repo=get_repository())
    teacher = sm.repo.get_user_by_username(args.teacher)
    if not teacher or teacher.get("role") != "teacher":
        print("--teacher must be a teacher username")
        return 3
    actor = authz.build_principal(teacher["id"], "teacher", sm.repo)
    if not authz.can_manage_class(actor, args.class_id):
        print("teacher is not assigned to this class")
        return 3
    contexts = parse_contexts(args.contexts)
    rt = sm.start_class(args.class_id, mode="classroom", started_by=actor.user_id)
    es = sm.evaluation.start(actor, args.class_id, {
        "camera_position": args.camera_position, "camera_angle": args.camera_angle, "resolution": args.resolution,
        "fps_target": args.fps_target, "device": args.device, "lighting_condition": args.lighting,
        "notes": args.notes, "consent_verified": True, "dataset_kind": "REAL",
        "activity_context": contexts[0][1] if contexts else None})
    eid = es["evaluation_session_id"]
    cam = ClassroomAI(sm, args.class_id, camera_index=args.camera, class_session_id=rt.class_session_id)
    identity_frames, perf = [], {"frame_timestamps": [], "frame_latency_ms": [], "identity_latency_ms": [],
                                 "device": args.device}
    start = time.time()
    ci = 1
    print(f"Recording evaluation {eid}. Keys in the preview window: 1-7 change context, q quits.")
    try:
        while time.time() - start < args.minutes * 60.0:
            el = time.time() - start
            while ci < len(contexts) and contexts[ci][0] <= el:
                sm.evaluation.set_context(actor, eid, contexts[ci][1])
                print(f"[{el:7.1f}s] context -> {contexts[ci][1]}")
                ci += 1
            jpeg, _, _ = cam.get_frame(block=0.5)    # newest frame only, never the same one twice
            meta = cam.last_frame_meta
            if meta and (not perf["frame_timestamps"] or meta["t"] != perf["frame_timestamps"][-1]):
                perf["frame_timestamps"].append(meta["t"])
                perf["frame_latency_ms"].append(round(meta["latency_ms"], 2))
                perf["identity_latency_ms"].append(round(meta["identity_latency_ms"], 3))
                for tid, d in meta["tracks"].items():
                    # gt_student_id is filled from the observer's identity sheet later; UNLABELLED = not scored
                    identity_frames.append({"t": meta["t"], "track_id": tid, "gt_student_id": "UNLABELLED",
                                            "pred_student_id": d["student_id"], "status": d["status"]})
            if args.show:
                import numpy as np
                img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                cv2.imshow("FocusGuard evaluation (no recording)", img)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key in CONTEXT_KEYS:
                    sm.evaluation.set_context(actor, eid, CONTEXT_KEYS[key])
                    print(f"context -> {CONTEXT_KEYS[key]}")
    finally:
        cam.release()
        if args.show:
            cv2.destroyAllWindows()
        sm.evaluation.end(actor, eid)
        sm.end_class(args.class_id)
    pkg = sm.evaluation.package(eid)
    pkg["identity_frames"] = identity_frames
    pkg["performance"] = dict(pkg.get("performance") or {}, **perf)
    out_dir = os.path.join(sm.privacy.evaluation_private_dir, eid)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "package.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pkg, f, ensure_ascii=False)
    status("RECORDED", evaluation_session_id=eid, package=path)
    print("Next: observers fill the CSV timeline, then run import-annotations and metrics.")
    return 0


def _session_import(args, kind):
    """Import observer sheets straight into the database for one evaluation session
    (same consent / class / time checks as the web form)."""
    from focusguard import authz
    from repository import get_repository
    from session_manager import SessionManager
    sm = SessionManager(repo=get_repository())
    with open(args.csv, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if kind == "identity":
        res = sm.evaluation.import_identity(args.evaluation_session, rows)
        status("IDENTITY_LABELS_IMPORTED", **res)
        return 0
    es = sm.evaluation.get(args.evaluation_session)
    teacher = sm.repo.get_user_by_username(args.teacher or "")
    if es is None or not teacher or teacher.get("role") != "teacher":
        print("--evaluation-session must exist and --teacher must be a teacher username")
        return 3
    actor = authz.build_principal(teacher["id"], "teacher", sm.repo)
    if not authz.can_run_evaluation(actor, es["class_id"]):
        print("teacher is not assigned to the evaluated class")
        return 3
    res = sm.evaluation.import_annotations(actor, args.evaluation_session, rows)
    status("ANNOTATIONS_IMPORTED", added=res["added"], skipped=len(res["skipped"]))
    for s in res["skipped"][:20]:
        print("  skipped", s)
    return 0


def cmd_import(args):
    from focusguard.evaluation.real_classroom.models import GT_LABELS
    if args.evaluation_session:
        return _session_import(args, "annotations")
    with open(args.package, "r", encoding="utf-8") as f:
        pkg = json.load(f)
    t0 = float(pkg["metadata"]["started_at"])
    allowed = set(pkg.get("enrolled") or [])
    added, skipped = 0, []
    with open(args.csv, "r", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            try:
                sid = int(row["student_id"])
                label = row["label"].strip().upper()
                a, b = float(row["start_s"]), float(row["end_s"])
            except (KeyError, ValueError):
                skipped.append(f"line {i}: unreadable")
                continue
            if label not in GT_LABELS or b <= a or (allowed and sid not in allowed):
                skipped.append(f"line {i}: invalid label/time or student without evaluation consent")
                continue
            pkg.setdefault("annotations", []).append({
                "annotation_id": f"csv-{i}", "evaluation_session_id": pkg["metadata"].get("evaluation_session_id"),
                "student_id": sid, "label": label, "started_at": t0 + a, "ended_at": t0 + b,
                "annotated_by": args.annotated_by, "created_at": time.time(), "note": (row.get("note") or "")[:500]})
            added += 1
    out = args.output or args.package
    with open(out, "w", encoding="utf-8") as f:
        json.dump(pkg, f, ensure_ascii=False)
    status("ANNOTATIONS_IMPORTED", added=added, skipped=len(skipped))
    for s in skipped[:20]:
        print("  skipped", s)
    return 0


def cmd_import_identity(args):
    """Observer identity sheet: ``track_id,student_id,start_s,end_s`` (student_id STRANGER = not enrolled)."""
    if args.evaluation_session:
        return _session_import(args, "identity")
    with open(args.package, "r", encoding="utf-8") as f:
        pkg = json.load(f)
    t0 = float(pkg["metadata"]["started_at"])
    sheet = []
    with open(args.csv, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                gt = None if row["student_id"].strip().upper() == "STRANGER" else int(row["student_id"])
                sheet.append((int(row["track_id"]), gt, t0 + float(row["start_s"]), t0 + float(row["end_s"])))
            except (KeyError, ValueError):
                continue
    labelled = 0
    for fr in pkg.get("identity_frames") or []:
        for tid, gt, a, b in sheet:
            if int(fr["track_id"]) == tid and a <= fr["t"] < b:
                fr["gt_student_id"] = gt
                labelled += 1
                break
    out = args.output or args.package
    with open(out, "w", encoding="utf-8") as f:
        json.dump(pkg, f, ensure_ascii=False)
    status("IDENTITY_LABELS_IMPORTED", labelled_frames=labelled)
    return 0


def cmd_export(args):
    from repository import get_repository
    from session_manager import SessionManager
    sm = SessionManager(repo=get_repository())
    pkg = sm.evaluation.package(args.evaluation_session)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(pkg, f, ensure_ascii=False)
    status("EXPORTED", output=args.output, annotations=len(pkg["annotations"]), predictions=len(pkg["predictions"]))
    return 0


def cmd_metrics(args):
    from focusguard.evaluation.real_classroom.metrics import evaluate_package
    with open(args.package, "r", encoding="utf-8") as f:
        pkg = json.load(f)
    report = evaluate_package(pkg)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    status(report["status"], dataset_kind=report["dataset_kind"],
           real_world_evidence=report["is_real_world_evidence"])
    for b, m in report["behavior"]["per_behavior_context_aware"].items():
        print(f"  {b:10s} P={m['precision']} R={m['recall']} F1={m['f1']} FP/h={m['false_positives_per_hour']}")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["start"]:            # guided field test: one implementation (tools/field_test.py)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from field_test import main as field_main
        return field_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--camera", type=int, default=0)
    c.add_argument("--strict", action="store_true")
    r = sub.add_parser("record")
    r.add_argument("--class-id", type=int, required=True)
    r.add_argument("--teacher", required=True)
    r.add_argument("--minutes", type=float, default=20.0)
    r.add_argument("--camera", type=int, default=0)
    r.add_argument("--camera-position", default="FRONT_CORNER")
    r.add_argument("--camera-angle", default=None)
    r.add_argument("--resolution", default=None)
    r.add_argument("--fps-target", type=float, default=None)
    r.add_argument("--device", default=None)
    r.add_argument("--lighting", default=None)
    r.add_argument("--notes", default=None)
    r.add_argument("--contexts", default="0:LECTURE")
    r.add_argument("--consent-verified", action="store_true")
    r.add_argument("--show", action="store_true")
    r.add_argument("--strict", action="store_true")
    i = sub.add_parser("import-annotations")
    src = i.add_mutually_exclusive_group(required=True)
    src.add_argument("--package")
    src.add_argument("--evaluation-session")
    i.add_argument("--teacher", default=None)
    i.add_argument("--csv", required=True)
    i.add_argument("--annotated-by", default="observer")
    i.add_argument("--output", default=None)
    ii = sub.add_parser("import-identity")
    src = ii.add_mutually_exclusive_group(required=True)
    src.add_argument("--package")
    src.add_argument("--evaluation-session")
    ii.add_argument("--csv", required=True)
    ii.add_argument("--output", default=None)
    e = sub.add_parser("export")
    e.add_argument("--evaluation-session", required=True)
    e.add_argument("--output", required=True)
    m = sub.add_parser("metrics")
    m.add_argument("--package", required=True)
    m.add_argument("--output", default=None)
    args = ap.parse_args(argv)
    return {"check": cmd_check, "record": cmd_record, "import-annotations": cmd_import,
            "import-identity": cmd_import_identity, "export": cmd_export,
            "metrics": cmd_metrics}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
