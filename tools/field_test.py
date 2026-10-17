#!/usr/bin/env python3
"""One-command real-classroom field test (bring the laptop + camera, run this).

    python tools/field_test.py --class-id 1 --teacher teacher            # guided, interactive
        [--camera-name classroom-front | --camera 0] [--scenarios 01_lecture,05_phone,...] [--strict]
        [--annotations-csv observer.csv] [--identity-csv identity.csv]   # import labels at the end
        [--answers answers.json]                                         # unattended: prompt -> answer
    python tools/field_test.py --simulate                                # SYNTHETIC rehearsal, no camera

Flow: preflight -> consent check -> camera check -> evaluation session ->
scenario guide (15 BTC scenarios) -> observer templates / label import ->
session close -> finalize -> report path. Nothing is uploaded; private data
stays in evaluation/private/<id>/ and the shareable report is privacy-scanned.

Camera: ``--camera-name`` = a camera saved in Cài đặt › Camera (EZVIZ / IP camera /
webcam) that the teacher may use for the class; without it, the class camera
assigned by the admin or the teacher's default camera; with no saved camera at
all, the local webcam ``--camera`` (default 0). Camera passwords are decrypted
server-side only and never printed or stored in the evaluation.

Without a camera or the camera libraries: ``STATUS: HARDWARE_REQUIRED``, exit 0
(``--strict``: exit 2). No result is ever faked. ``--simulate`` runs the same
flow on an isolated temporary database with scripted SYNTHETIC students; its
report says SYNTHETIC and can never count as real-world evidence.

Exit codes: 0 done (or hardware missing without --strict), 1 error / privacy
check failed, 2 hardware required (--strict), 3 consent missing or not confirmed.
"""
import argparse
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--class-id", type=int, default=1)
    ap.add_argument("--teacher", default="teacher")
    ap.add_argument("--camera", type=int, default=None, help="local webcam index (default 0)")
    ap.add_argument("--camera-name", default=None, help="a camera saved in Cài đặt › Camera")
    ap.add_argument("--scenarios", default=None, help="comma-separated scenario ids (default: all 15)")
    ap.add_argument("--annotations-csv", default=None)
    ap.add_argument("--identity-csv", default=None)
    ap.add_argument("--answers", default=None, help="JSON {prompt fragment: answer} for unattended runs "
                    "(a real run still needs the paper-consent confirmation explicitly set to \"y\")")
    ap.add_argument("--scenario-seconds", type=float, default=None,
                    help="fixed length of each scenario instead of pressing Enter (unattended runs)")
    ap.add_argument("--report-dir", default=None)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--simulate", action="store_true", help="SYNTHETIC rehearsal on a temporary database")
    ap.add_argument("--keep-simulation", action="store_true", help="keep the temporary simulation database")
    return ap


def resolve_camera(sm, a):
    """(field.CameraSource, error). --camera-name -> that saved camera; --camera N -> webcam N;
    neither -> the class camera / the teacher's default camera / webcam 0."""
    from focusguard.authz import build_principal
    from focusguard.cameras.service import CameraError
    from focusguard.evaluation.real_classroom import field
    if a.camera is not None and not a.camera_name:
        return field.CameraSource(a.camera), None
    user = sm.repo.get_user_by_username(a.teacher)
    p = build_principal(user["id"], user.get("role"), sm.repo) if user else None
    if p is None:
        if a.camera_name:
            return None, "teacher username not found"
        return field.CameraSource(0), None          # the field run itself reports the missing teacher
    if a.camera_name:
        cam = sm.cameras.find_by_name(p, a.class_id, a.camera_name)
        if cam is None:
            names = sorted(c.get("name") for c in sm.cameras.usable_cameras(p, a.class_id))
            return None, f"camera {a.camera_name!r} not found for this teacher/class; available: {names}"
        return field.CameraSource(camera=cam), None
    try:
        cam, reason = sm.cameras.resolve_for_class(p, a.class_id)
    except CameraError as exc:
        return None, exc.message
    if reason == "CHOOSE":
        names = sorted(c.get("name") for c in sm.cameras.usable_cameras(p, a.class_id))
        return None, f"several cameras and no default: pass --camera-name (one of {names})"
    return field.CameraSource(0, camera=cam), None


def main(argv=None, io=None):
    a = build_parser().parse_args(argv)
    os.chdir(ROOT)
    from focusguard.evaluation.real_classroom import field
    if io is None:
        if a.answers:
            with open(a.answers, "r", encoding="utf-8") as f:
                io = field.ScriptedIO(json.load(f))
        else:
            io = field.ConsoleIO(assume_defaults=a.simulate)
    scenarios = [s.strip() for s in a.scenarios.split(",")] if a.scenarios else None
    tmp = None
    if a.simulate:
        tmp = tempfile.mkdtemp(prefix="focusguard-field-sim-")
        sm, clock = field.simulation_manager(tmp)
        participants = [s["student_id"] for s in sm.evaluation.consented_students(a.class_id)]
        source = field.SimulatedSource(participants, clock)
        io.say(f"[SYNTHETIC] Simulation database: {tmp} (demo accounts, synthetic consent; not your data)")
    else:
        from repository import get_repository
        from session_manager import SessionManager
        sm = SessionManager(repo=get_repository())
        source, err = resolve_camera(sm, a)
        if err:
            io.say("STATUS: CONFIG_ERROR " + json.dumps({"reason": err}, ensure_ascii=False))
            return field.EXIT_ERROR
    run = field.FieldRun(sm, io, source, class_id=a.class_id, teacher=a.teacher, scenarios=scenarios,
                         report_dir=a.report_dir, strict=a.strict, simulate=a.simulate,
                         annotations_csv=a.annotations_csv, identity_csv=a.identity_csv,
                         scenario_seconds=a.scenario_seconds)
    try:
        return run.run()
    finally:
        if tmp and not a.keep_simulation:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
