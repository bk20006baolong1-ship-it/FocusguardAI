#!/usr/bin/env python3
"""Guided real-webcam verification of the classroom pipeline (no browser needed).

Runs the REAL pipeline (YOLOv8 + ByteTrack, InsightFace, MediaPipe, IdentityManager,
SessionRuntime, FocusEngine) on the Mac webcam, prompts the volunteer through the
scenarios in docs/REAL_CAMERA_VERIFICATION.md (spoken with macOS `say` and printed),
logs structured per-frame state and computes a verdict per scenario.

Privacy:
  * no image or video is ever written to disk;
  * the enrolment face embedding is stored only in the throw-away SQLite DB under data/
    (git-ignored) and is erased at the end unless --keep-embeddings is given;
  * the JSON log contains ids, timings, states and scores only, and is written to
    evaluation/private/ (git-ignored). Do not commit it.

Usage:
  python tools/real_camera_check.py                      # scenarios 1-5, 8, 9 (one volunteer)
  python tools/real_camera_check.py --second-person      # also 6 (crossing, second registered volunteer)
  python tools/real_camera_check.py --unknown-person     # also 7 (unregistered volunteer)
  options: --camera 0  --no-speak  --no-preview  --db data/real_camera_check.db
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

STUDENT_A = 3      # demo account "hocsinh" (class 1)
STUDENT_B = 4      # demo account "diyasharma" (class 1)
CLASS_ID = 1


# ------------------------------------------------------------------ prompts
def vietnamese_voice():
    """Name of an installed macOS Vietnamese voice (e.g. 'Linh'), or None."""
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if "vi_VN" in line or "vi-VN" in line:
            return line.split("  ")[0].strip()
    return None


class Prompter:
    """Prints every prompt; speaks it only with a Vietnamese voice (the default
    English voice cannot pronounce Vietnamese and is unintelligible)."""

    def __init__(self, speak=True):
        self.voice = vietnamese_voice() if (speak and sys.platform == "darwin") else None
        if speak and sys.platform == "darwin" and self.voice is None:
            print("No Vietnamese voice installed: prompts are printed only "
                  "(System Settings > Accessibility > Spoken Content > add 'Linh').")

    def say(self, text, wait=False):
        print(f"\n>>> {text}", flush=True)
        if self.voice:
            try:
                p = subprocess.Popen(["say", "-v", self.voice, text])
                if wait:
                    p.wait(timeout=20)
            except Exception:
                pass

    def beep(self):
        print(">>> BÍP: quay đầu sang một bên khoảng 1 giây", flush=True)
        if sys.platform == "darwin":
            try:
                subprocess.Popen(["afplay", "/System/Library/Sounds/Ping.aiff"])
            except Exception:
                pass


# ------------------------------------------------------------------ verdicts (pure)
def _frames(log, step_prefix):
    return [f for f in log["frames"] if f["step"].startswith(step_prefix)]


def _student(frame, sid):
    return frame["students"].get(str(sid)) or {}


def _episodes_started(log, sid, kind, t0, t1):
    return [e for e in log["episodes"] if e["student_id"] == sid and e["type"] == kind
            and t0 - 0.01 <= e["start_time"] <= t1 + 0.01]


def verdict_identity(log, sid=STUDENT_A):
    fr = _frames(log, "S1")
    if not fr:
        return {"status": "NOT_RUN"}
    confirmed = [f for f in fr if _student(f, sid).get("identity_status") == "CONFIRMED"]
    dup = [f["t"] for f in fr if sum(1 for tr in f["tracks"] if tr["student_id"] == sid) > 1]
    first = confirmed[0]["t"] - fr[0]["t"] if confirmed else None
    after = [f for f in fr if confirmed and f["t"] >= confirmed[0]["t"]]
    stable = sum(1 for f in after if _student(f, sid).get("identity_status") == "CONFIRMED") / len(after) if after else 0
    resets = []
    prev = None
    for f in fr:
        sc = _student(f, sid).get("focus_score")
        if prev is not None and sc is not None and sc - prev > 5:
            resets.append(f["t"])
        prev = sc if sc is not None else prev
    wrong = [f["t"] for f in fr for tr in f["tracks"] if tr["student_id"] not in (None, sid)]
    ok = first is not None and stable >= 0.8 and not dup and not resets and not wrong
    return {"status": "PASS" if ok else "FAIL", "time_to_confirmation_s": first,
            "confirmed_fraction_after_first_confirmation": round(stable, 3),
            "duplicate_frames": len(dup), "score_resets": len(resets), "wrong_identity_frames": len(wrong)}


def _closures(frames, sid, closed_below=0.12):
    """Measured eye-closure runs (seconds) from logged EAR of the student's track."""
    runs, start, last = [], None, None
    for f in frames:
        tid = str(_student(f, sid).get("active_track_id"))
        lm = (f.get("detector") or {}).get("landmarks", {}).get(tid)
        ear = lm[0] if lm else None
        if ear is not None and ear < closed_below:
            start = f["t"] if start is None else start
            last = f["t"]
        elif start is not None:
            runs.append((start, last))
            start = None
    if start is not None:
        runs.append((start, last))
    return runs


def verdict_blink(log, sid=STUDENT_A, min_seconds=1.5, frame_dt=0.14):
    """Short closures (blinks) must never create DROWSY; closures measured as
    sustained (>= min_seconds) are expected to, and are reported separately."""
    fr = _frames(log, "S2")
    if not fr:
        return {"status": "NOT_RUN"}
    if not any((f.get("detector") or {}).get("landmarks") for f in fr):
        return verdict_no_episode(log, "S2", "DROWSY", sid)       # older logs without EAR
    # Closures are measured over the whole log so one that started before the
    # scenario window is not truncated into a "blink".
    runs = [(a, b) for a, b in _closures(log["frames"], sid) if b >= fr[0]["t"] - 2 and a <= fr[-1]["t"]]
    eps = _episodes_started(log, sid, "DROWSY", fr[0]["t"] - 2, fr[-1]["t"])
    short = [(a, b) for a, b in runs if b - a + frame_dt < min_seconds]
    long_ = [(a, b) for a, b in runs if b - a + frame_dt >= min_seconds]

    def overlaps(ep, a, b):
        end = ep.get("end_time") or ep["start_time"] + (ep.get("duration") or 0)
        return ep["start_time"] <= b + 0.5 and end >= a - 0.5
    false_alerts = [e for e in eps if not any(overlaps(e, a, b) for a, b in long_)]
    measured = sum(1 for f in fr if _student(f, sid).get("focus_state") not in (None, "UNKNOWN"))
    ok = bool(short) and not false_alerts and measured >= 0.5 * len(fr)
    return {"status": "PASS" if ok else ("INCONCLUSIVE" if not short else "FAIL"),
            "blinks_measured": len(short), "blink_durations_s": [round(b - a + frame_dt, 2) for a, b in short],
            "drowsy_episodes": len(eps), "false_drowsy_alerts": len(false_alerts),
            "sustained_closures_s": [round(b - a + frame_dt, 2) for a, b in long_],
            "note": ("sustained eye closures were flagged as designed" if long_ else None)}


def verdict_no_episode(log, step, kind, sid=STUDENT_A):
    fr = _frames(log, step)
    if not fr:
        return {"status": "NOT_RUN"}
    eps = _episodes_started(log, sid, kind, fr[0]["t"], fr[-1]["t"])
    measured = sum(1 for f in fr if _student(f, sid).get("focus_state") not in (None, "UNKNOWN"))
    ok = not eps and measured >= 0.5 * len(fr)
    return {"status": "PASS" if ok else "FAIL", f"{kind.lower()}_episodes": len(eps),
            "episode_durations_s": [e.get("duration") for e in eps],
            "measured_fraction": round(measured / len(fr), 3),
            "note": None if measured >= 0.5 * len(fr) else "face not measured in most frames; inconclusive"}


def verdict_phone(log, sid=STUDENT_A):
    fr = _frames(log, "S4")
    if not fr:
        return {"status": "NOT_RUN"}
    eps = _episodes_started(log, sid, "PHONE", fr[0]["t"], fr[-1]["t"])
    other = [e for e in log["episodes"] if e["type"] == "PHONE" and e["student_id"] != sid
             and fr[0]["t"] <= e["start_time"] <= fr[-1]["t"]]
    durations = [e.get("duration") for e in eps]
    total = sum(d or 0 for d in durations)
    confs = [e.get("confidence") for e in eps]
    alert = any(l.get("type") == "behavior_started" and (l.get("episode") or {}).get("type") == "PHONE"
                for l in log.get("logs", []))
    ok = bool(eps) and total >= 5.0 and all(c is not None and 0 < c <= 1 for c in confs) and not other and alert
    return {"status": "PASS" if ok else "FAIL", "episodes": len(eps), "durations_s": durations,
            "total_phone_seconds": round(total, 2), "confidences": confs,
            "episodes_on_other_students": len(other), "teacher_alert_logged": alert,
            "note": "more than one episode means detection gaps > gap tolerance" if len(eps) > 1 else None}


def verdict_leave(log, sid=STUDENT_A):
    before, away_prompt, back_prompt = _frames(log, "S5a"), _frames(log, "S5b"), _frames(log, "S5c")
    if not (before and away_prompt and back_prompt):
        return {"status": "NOT_RUN"}
    # People do not leave/return exactly on cue: judge the absence over the whole
    # leave+return window; "returned" = first confirmed frame after being AWAY.
    window = away_prompt + back_prompt
    vis = [_student(f, sid).get("visibility_status") for f in window]
    away_idx = next((i for i, v in enumerate(vis) if v == "AWAY"), None)
    returned = []
    if away_idx is not None:
        returned = [f for f in window[away_idx:] if _student(f, sid).get("identity_status") == "CONFIRMED"]
    eps = _episodes_started(log, sid, "AWAY", before[-1]["t"] - 1, window[-1]["t"])
    recovery = None
    if returned:
        first_visible_again = next(f for f in window[away_idx:] if _student(f, sid).get("visibility_status") != "AWAY")
        recovery = returned[0]["t"] - first_visible_again["t"]
    tracks_before = {_student(f, sid).get("active_track_id") for f in before} - {None}
    tracks_after = {_student(f, sid).get("active_track_id") for f in returned} - {None}
    score_before = next((_student(f, sid).get("focus_score") for f in reversed(before)
                         if _student(f, sid).get("focus_score") is not None), None)
    score_after = next((_student(f, sid).get("focus_score") for f in returned
                        if _student(f, sid).get("focus_score") is not None), None)
    conn = {_student(f, sid).get("connection_status") for f in window}
    no_reset = score_before is None or score_after is None or score_after <= score_before + 1
    ok = (len(eps) == 1 and away_idx is not None and "TEMPORARILY_NOT_VISIBLE" in vis and bool(returned)
          and no_reset and conn <= {"NOT_APPLICABLE"})
    return {"status": "PASS" if ok else "FAIL", "away_episodes": len(eps),
            "visibility_states": sorted({v for v in vis if v}),
            "connection_states": sorted(c for c in conn if c),
            "reconfirm_after_return_s": None if recovery is None else round(recovery, 2),
            "track_ids_before": sorted(tracks_before), "track_ids_after": sorted(tracks_after),
            "score_before": score_before, "score_after": score_after, "score_not_reset": no_reset}


def verdict_crossing(log):
    fr = _frames(log, "S6")
    if not fr:
        return {"status": "USER_ACTION_REQUIRED", "reason": "needs a second registered, consenting volunteer"}
    dup = [f["t"] for f in fr for sid in (STUDENT_A, STUDENT_B)
           if sum(1 for tr in f["tracks"] if tr["student_id"] == sid) > 1]
    # identity flip: a track that was confirmed as one student later confirmed as the other
    owner = {}
    flips = 0
    for f in fr:
        for tr in f["tracks"]:
            if tr["student_id"] is None:
                continue
            prev = owner.get(tr["track_id"])
            if prev is not None and prev != tr["student_id"]:
                flips += 1
            owner[tr["track_id"]] = tr["student_id"]
    ok = not dup and flips == 0
    return {"status": "PASS" if ok else "FAIL", "duplicate_frames": len(dup), "track_identity_flips": flips}


def verdict_unknown(log):
    fr = _frames(log, "S7")
    if not fr:
        return {"status": "USER_ACTION_REQUIRED", "reason": "needs an unregistered, consenting volunteer"}
    # the registered volunteer is out of frame; nobody may be confirmed
    wrong = [f["t"] for f in fr for tr in f["tracks"] if tr["student_id"] is not None]
    seen = any(f["tracks"] for f in fr)
    ok = seen and not wrong
    return {"status": "PASS" if ok else ("FAIL" if wrong else "INCONCLUSIVE"),
            "frames_with_person": sum(1 for f in fr if f["tracks"]), "forced_matches": len(wrong)}


def verdict_interrupt(log):
    stall = log.get("interrupt") or {}
    if not stall:
        return {"status": "NOT_RUN"}
    ok = (stall.get("camera_status_during_stall") == "NO_SIGNAL" and stall.get("score_frozen")
          and stall.get("camera_status_after_resume") == "ACTIVE" and stall.get("frames_after_resume", 0) > 0
          and stall.get("same_runtime"))
    return {"status": "PASS" if ok else "FAIL", **stall}


def verdict_end(log):
    end = log.get("end") or {}
    if not end:
        return {"status": "NOT_RUN"}
    ok = (end.get("open_episodes_after_end") == 0 and end.get("session_students_rows") == end.get("enrolled")
          and end.get("runtime_cleaned") and end.get("new_class_clean")
          and end.get("stale_pipeline_fed_new_runtime") is False)
    return {"status": "PASS" if ok else "FAIL", **end}


def compute_verdicts(log):
    return {
        "1_single_identity": verdict_identity(log),
        "2_blink": verdict_blink(log),
        "3_quick_glance": verdict_no_episode(log, "S3", "HEAD_AWAY"),
        "4_phone": verdict_phone(log),
        "5_leave_return": verdict_leave(log),
        "6_crossing": verdict_crossing(log),
        "7_unknown_person": verdict_unknown(log),
        "8_camera_interrupt": verdict_interrupt(log),
        "9_end_class": verdict_end(log),
    }


# ------------------------------------------------------------------ hardware run
CAMERA_HELP = ("Camera not available. On macOS allow camera access for the app running this command "
               "(System Settings > Privacy & Security > Camera > Terminal), quit and reopen Terminal, then rerun.")


def open_camera(index, timeout=30.0, prompter=None):
    """Open the webcam, waiting for the macOS permission prompt to be answered.

    On first use macOS answers 'not determined' and OpenCV fails immediately
    while the permission dialog is shown; retrying gives the user time to click
    Allow. Raises SystemExit with instructions if no frame arrives in time.
    """
    import cv2
    deadline = time.time() + timeout
    announced = False
    while time.time() < deadline:
        cap = cv2.VideoCapture(index)
        for _ in range(20):
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                return cap
            time.sleep(0.05)
        cap.release()
        if not announced:
            msg = "Đang chờ quyền camera. Nếu macOS hiện hộp thoại, hãy bấm Allow."
            (prompter.say(msg) if prompter else print(msg))
            announced = True
        time.sleep(1.0)
    raise SystemExit(CAMERA_HELP)


def enroll(face_recognizer, cap, prompter, seconds=6.0, min_samples=8):
    import numpy as np
    prompter.say("Đăng ký khuôn mặt. Hãy nhìn thẳng vào camera và giữ yên.", wait=True)
    embs, t_end = [], time.time() + seconds
    while time.time() < t_end:
        ok, frame = cap.read()
        if not ok:
            continue
        faces = face_recognizer.app.get(frame)
        if len(faces) == 1:
            embs.append(np.asarray(faces[0].embedding, dtype=float))
    if len(embs) < min_samples:
        raise SystemExit(f"Enrolment failed: only {len(embs)} single-face frames (need {min_samples}).")
    v = np.mean(embs, axis=0)
    return (v / np.linalg.norm(v)).tolist(), len(embs)


def snapshot(cam, runtime, step, t, fps):
    tracks = [{"track_id": tid, "status": tr.status, "student_id": tr.student_id,
               "confidence": round(float(tr.confidence), 3)} for tid, tr in cam.identity.tracks.items()
              if tr.last_seen is not None and t - tr.last_seen < 0.5]
    students = {}
    for sid in (STUDENT_A, STUDENT_B):
        v = runtime.student_view(sid) or {}
        students[str(sid)] = {k: v.get(k) for k in ("identity_status", "identity_confidence", "active_track_id",
                                                      "attendance_status", "visibility_status", "connection_status",
                                                      "focus_state", "focus_score", "event_counts", "away_count")}
        an = runtime.analyzers.get(sid)
        if an is not None:
            students[str(sid)]["ear_baseline"] = an.ear_baseline
            students[str(sid)]["ear_closed_threshold"] = an.ear_closed_threshold
    return {"t": t, "step": step, "fps": round(fps, 1), "camera_status": runtime.camera_status,
            "tracks": tracks, "students": students, "detector": dict(getattr(cam, "last_debug", {}) or {})}


def run(args):
    import cv2
    from classroom_ai import ClassroomAI
    from face_recognition import FaceRecognizer
    from repository import SQLiteRepository
    from session_manager import SessionManager

    os.chdir(ROOT)  # model paths (yolov8n.pt, face_landmarker.task) are relative to the repo
    prompter = Prompter(speak=not args.no_speak)
    cap = open_camera(args.camera, prompter=prompter)   # fail fast before anything is written
    recognizer = FaceRecognizer()
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(args.db + suffix):
            os.remove(args.db + suffix)
    repo = SQLiteRepository(args.db)
    sm = SessionManager(repo=repo, seed_demo_accounts=True)
    emb_a, n_a = enroll(recognizer, cap, prompter)
    repo.save_face_embedding(STUDENT_A, json.dumps(emb_a))
    enrolled = {"A": n_a}
    if args.second_person:
        prompter.say("Người thứ hai vào khung hình một mình, người thứ nhất tạm ra ngoài.", wait=True)
        time.sleep(4)
        emb_b, n_b = enroll(recognizer, cap, prompter)
        repo.save_face_embedding(STUDENT_B, json.dumps(emb_b))
        enrolled["B"] = n_b
        prompter.say("Người thứ nhất quay lại, người thứ hai ra ngoài.", wait=True)
        time.sleep(4)
    cap.release()
    del recognizer

    rt = sm.start_class(CLASS_ID, mode="classroom")
    cam = ClassroomAI(sm, CLASS_ID, camera_index=args.camera, class_session_id=rt.class_session_id)
    log = {"started": time.time(), "enrolled_samples": enrolled, "frames": [], "episodes": [], "logs": []}

    def loop(step, seconds):
        t_end = time.time() + seconds
        last = time.time()
        while time.time() < t_end:
            frame_bytes, _, _ = cam.get_frame()
            now = time.time()
            fps = 1.0 / max(1e-3, now - last)
            last = now
            log["frames"].append(snapshot(cam, rt, step, now, fps))
            if args.preview and frame_bytes:
                import numpy as np
                img = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
                cv2.imshow("FocusGuard real-camera check (nothing is recorded)", img)
                cv2.waitKey(1)

    def step(name, text, seconds, lead=4):
        prompter.say(text)
        loop(name + "_lead", lead)
        loop(name, seconds)

    step("S1", "Kịch bản 1. Ngồi trước camera, nhìn bình thường, giữ yên.", 25, lead=3)
    step("S2", "Kịch bản 2. Chớp mắt tự nhiên như bình thường.", 30)
    prompter.say("Kịch bản 3. Khi nghe tiếng chuông, liếc đầu sang một bên thật nhanh, dưới một giây, rồi quay lại ngay.")
    loop("S3_lead", 4)
    for i in range(3):
        loop(f"S3_{i}_look", 5)
        prompter.beep()
        loop(f"S3_{i}_glance", 3)
    step("S4", "Kịch bản 4. Cầm điện thoại trước ngực và dùng liên tục cho đến khi được bảo dừng.", 15)
    prompter.say("Cất điện thoại đi.")
    loop("S4_after", 6)
    step("S5a", "Kịch bản 5. Ngồi yên.", 6, lead=2)
    prompter.say("Đứng dậy và ra khỏi khung hình ngay bây giờ. Chờ đến khi được gọi quay lại.")
    loop("S5b", 18)
    prompter.say("Quay lại chỗ ngồi.")
    loop("S5c", 15)
    if args.second_person:
        step("S6", "Kịch bản 6. Hai người đi ngang cắt nhau trước camera, lặp lại vài lần.", 25)
    if args.unknown_person:
        prompter.say("Kịch bản 7. Người đã đăng ký ra khỏi khung hình. Người chưa đăng ký vào khung hình.")
        loop("S7_lead", 8)
        loop("S7", 20)
        prompter.say("Người chưa đăng ký ra ngoài. Người đã đăng ký quay lại.")
        loop("S7_after", 8)

    # ---- Scenario 8: stall the stream (no frames) then close + reopen the device
    prompter.say("Kịch bản 8. Ngồi yên. Hệ thống sẽ tạm ngắt camera khoảng tám giây.")
    loop("S8_before", 4)
    score_before = rt.states[STUDENT_A].focus_score
    time.sleep(8.0)                                  # no frames: like a stalled/disconnected stream
    status_stall = rt.heartbeat(time.time())
    score_stall = rt.states[STUDENT_A].focus_score
    cam.cap.release()                                # device closed; get_frame must reopen it
    t_resume = time.time()
    loop("S8_after", 8)
    after = [f for f in log["frames"] if f["step"] == "S8_after"]
    log["interrupt"] = {
        "camera_status_during_stall": status_stall,
        "score_before": score_before, "score_during_stall": score_stall,
        "score_frozen": score_before == score_stall,
        "camera_status_after_resume": rt.camera_status,
        "frames_after_resume": len(after),
        "seconds_to_first_frame": (after[0]["t"] - t_resume) if after else None,
        "same_runtime": sm.class_runtime(CLASS_ID) is rt,
    }

    # ---- Scenario 9: end class, persistence, clean restart, stale pipeline isolation
    prompter.say("Kịch bản 9. Kết thúc buổi học.")
    old_cs = rt.class_session_id
    log["episodes"] = [e.to_dict() for e in rt.episodes] + [e.to_dict() for a in rt.analyzers.values()
                                                            for e in a.active_episodes()]
    log["logs"] = list(rt.logs)
    result = sm.end_class(CLASS_ID)
    log["episodes"] = [e.to_dict() for e in rt.episodes]
    events = repo.get_focus_events(class_session_id=old_cs)
    rows = repo.get_session_students(class_session_id=old_cs)
    rt2 = sm.start_class(CLASS_ID, mode="classroom")
    before_new = rt2.student_view(STUDENT_A)
    for _ in range(10):
        cam.get_frame()                               # old pipeline bound to old_cs
    after_new = rt2.student_view(STUDENT_A)
    log["end"] = {
        "events_persisted": len(events),
        "open_episodes_after_end": sum(1 for e in events if e.get("end_time") is None),
        "session_students_rows": len(rows), "enrolled": len(rt.states),
        "runtime_cleaned": sm.class_runtime(CLASS_ID) is rt2 and not rt.active,
        "new_class_clean": before_new["attendance_status"] == "NOT_YET" and before_new["focus_score"] is None,
        "stale_pipeline_fed_new_runtime": after_new["attendance_status"] != "NOT_YET",
        "summary": {k: v for k, v in sm.class_session_summary(old_cs, CLASS_ID).items() if k != "top_students"},
    }
    sm.end_class(CLASS_ID)
    cam.release()
    if args.preview:
        cv2.destroyAllWindows()
    if not args.keep_embeddings:
        for sid in (STUDENT_A, STUDENT_B):
            repo.save_face_embedding(sid, None)
    log["frames_total"] = len(log["frames"])
    fps_vals = [f["fps"] for f in log["frames"] if f["fps"] > 0]
    log["median_fps"] = sorted(fps_vals)[len(fps_vals) // 2] if fps_vals else None
    return log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=int(os.environ.get("FOCUSGUARD_CAMERA_INDEX", "0")))
    ap.add_argument("--db", default=os.path.join("data", "real_camera_check.db"))
    ap.add_argument("--out", default=os.path.join("evaluation", "private", "real_camera_run.json"))
    ap.add_argument("--second-person", action="store_true")
    ap.add_argument("--unknown-person", action="store_true")
    ap.add_argument("--no-speak", action="store_true")
    ap.add_argument("--no-preview", dest="preview", action="store_false")
    ap.add_argument("--keep-embeddings", action="store_true")
    ap.add_argument("--from-log", help="recompute verdicts from an existing JSON log")
    args = ap.parse_args()

    if args.from_log:
        log = json.load(open(args.from_log, encoding="utf-8"))
    else:
        log = run(args)
    log["verdicts"] = compute_verdicts(log)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1, ensure_ascii=False, default=str)
    print("\n=== REAL-CAMERA VERDICTS (behaviour/integration, not accuracy) ===")
    for k, v in log["verdicts"].items():
        print(f"{k:22s} {v['status']}")
    print(f"median FPS: {log.get('median_fps')}   log (private, do not commit): {args.out}")


if __name__ == "__main__":
    main()
