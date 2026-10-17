"""Guided real-classroom field test (BTC feedback A/B/C), one command:

    preflight -> consent check -> camera check -> session -> scenario guide ->
    observer templates / annotation import -> session close -> finalize -> report

Used by ``python tools/field_test.py`` and ``python tools/run_classroom_evaluation.py
start``. Interaction goes through an ``io`` object (console, or scripted answers
for unattended runs and tests). Camera frames come from the classroom pipeline
(``CameraSource``) or, with ``--simulate``, from :mod:`.simulation` (SYNTHETIC,
isolated temporary database, never real-world evidence).

Nothing is uploaded. Private data (observation log, identity frames, observer
templates) stays in ``evaluation/private/<id>/``; the shareable report is the
finalize output, privacy-scanned before the path is printed.
"""
import csv
import json
import os
import shutil
import sys
import threading
import time
from typing import Callable, List, Optional

from ...cameras.redact import redact
from ...privacy.artifact_scan import scan_paths
from .models import CAMERA_POSITIONS, LIGHTING, REAL, SYNTHETIC
from .report import build_report, write_outputs
from .scenarios import SCENARIOS, get_scenario

HARDWARE_REQUIRED = "HARDWARE_REQUIRED"
EXIT_OK, EXIT_ERROR, EXIT_HARDWARE, EXIT_CONSENT = 0, 1, 2, 3
RECOMMENDED_PARTICIPANTS = 5
MODEL_FILES = ("yolov8n.pt", "face_landmarker.task")
HW_PACKAGES = ("cv2", "mediapipe", "ultralytics", "insightface")


# ------------------------------------------------------------------- io
class ConsoleIO:
    def __init__(self, assume_defaults=False):
        self.assume_defaults = assume_defaults

    def say(self, text=""):
        print(text, flush=True)

    def ask(self, prompt, default=None, choices=None):
        hint = f" [{default}]" if default not in (None, "") else ""
        if choices:
            hint = f" ({'/'.join(choices)})" + hint
        while True:
            if self.assume_defaults:
                self.say(f"{prompt}{hint}: {default}")
                return default
            raw = input(f"{prompt}{hint}: ").strip()
            if raw == "" and default is not None:
                return default                    # "" is an allowed answer when it is the default
            value = raw
            if value == "":
                continue
            if choices and value not in choices:
                self.say(f"  Chọn một trong: {', '.join(choices)}")
                continue
            return value

    def confirm(self, prompt, default=True):
        return self.ask(prompt, "y" if default else "n", ["y", "n"]) == "y"


class ScriptedIO(ConsoleIO):
    """Answers from a dict (prompt key -> value); anything else takes its default."""

    def __init__(self, answers=None, echo=True):
        super().__init__(assume_defaults=True)
        self.answers = dict(answers or {})
        self.echo = echo
        self.lines: List[str] = []

    def say(self, text=""):
        self.lines.append(str(text))
        if self.echo:
            print(text, flush=True)

    def ask(self, prompt, default=None, choices=None):
        for key, value in self.answers.items():
            if key in prompt:
                self.say(f"{prompt}: {value}")
                return str(value)
        self.say(f"{prompt}: {default}")
        return default


# -------------------------------------------------------------- sources
class CameraSource:
    """Real classroom pipeline (YOLOv8 + ByteTrack + InsightFace + MediaPipe).

    ``camera``: a saved camera (Cài đặt › Camera, e.g. an EZVIZ / IP camera) opened through
    CameraService; None = the local webcam ``camera_index`` (as before)."""
    kind = REAL

    def __init__(self, camera_index=0, camera=None):
        self.camera_index = camera_index
        self.camera = camera
        self.cam = None

    def camera_metadata(self):
        from ...cameras.service import CameraService
        return CameraService.evaluation_metadata(self.camera)

    def check(self, sm, timeout=10.0):
        """Preflight for a saved camera: can it deliver one frame? (never prints credentials)"""
        if self.camera is None:
            return _camera_ok(self.camera_index)
        from ...cameras import health as H
        from ...cameras.reader import LatestFrameReader
        try:
            reader = LatestFrameReader(sm.cameras.open_source(self.camera), auto_reconnect=False).start()
        except Exception as exc:
            return False, f"camera {self.camera.get('name')!r}: {getattr(exc, 'message', type(exc).__name__)}"
        try:
            f = reader.wait_newer(0, timeout)
            state = reader.health()["state"]
        finally:
            reader.stop()
        if f is not None:
            return True, f"camera {self.camera.get('name')!r} ok"
        return False, f"camera {self.camera.get('name')!r}: {H.LABELS_VI.get(state, state)}"

    def open(self, sm, class_id, class_session_id):
        from classroom_ai import ClassroomAI
        source = sm.cameras.open_source(self.camera) if self.camera is not None else None
        self.cam = ClassroomAI(sm, class_id, camera_index=self.camera_index, class_session_id=class_session_id,
                               source=source)

    def measured(self, wait=10.0):
        self.cam.reader.wait_newer(0, wait)            # first frame (or give up after ``wait`` s)
        h = self.cam.camera_health()
        return {"resolution": h["resolution"], "camera_fps": h["reported_fps"] or h["measured_fps"],
                "measured_fps": h["measured_fps"]}

    def run(self, scenario_id, stop: threading.Event, sink):
        last_t = None
        while not stop.is_set():
            self.cam.get_frame(block=0.5)       # feeds the runtime (and the evaluation recorder hook)
            meta = self.cam.last_frame_meta
            if meta and meta["t"] != last_t:    # camera offline -> no new frame -> nothing recorded
                last_t = meta["t"]
                sink(meta["t"], [{"track_id": tid, "gt_student_id": "UNLABELLED", "pred_student_id": d["student_id"],
                                  "status": d["status"]} for tid, d in meta["tracks"].items()],
                     meta["latency_ms"], meta["identity_latency_ms"])

    def close(self):
        if self.cam is not None:
            self.cam.release()


class SimulatedSource:
    """SYNTHETIC frames through the real runtime; the clock is fast-forwarded."""
    kind = SYNTHETIC

    def __init__(self, participants, clock):
        from .simulation import SimulatedClassroom
        self.sim = SimulatedClassroom(participants)
        self.clock = clock
        self.rt = None
        self.ground_truth = []

    def open(self, sm, class_id, class_session_id):
        self.rt = sm.class_runtime(class_id)

    def measured(self):
        return {"resolution": "1280x720", "camera_fps": 5.0}

    def run(self, scenario_id, stop, sink):
        t0 = self.clock()
        self.ground_truth += self.sim.ground_truth(scenario_id, t0)
        for t, obs, ident, lat, id_lat in self.sim.frames(scenario_id, t0):
            self.clock.t = t
            self.rt.process_frame(t, obs)
            sink(t, ident, lat, id_lat)
        stop.set()

    def close(self):
        pass


class SimClock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


# ------------------------------------------------------------- preflight
def preflight(sm, camera_index=0, simulate=False, check_camera: Optional[Callable] = None) -> List[dict]:
    """[(check, status PASS/WARN/FAIL/HARDWARE_REQUIRED, detail)] - never raises."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    out = []

    def add(name, status, detail=""):
        out.append({"check": name, "status": status, "detail": detail})
    add("python", "PASS" if sys.version_info >= (3, 10) else "FAIL", sys.version.split()[0])
    missing = []
    for mod in HW_PACKAGES:
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    add("camera_ai_packages", "PASS" if not missing else ("WARN" if simulate else HARDWARE_REQUIRED),
        "ok" if not missing else "missing: " + ", ".join(missing) + " (pip install -r requirements.txt)")
    absent = [f for f in MODEL_FILES if not os.path.exists(os.path.join(root, f))]
    add("model_files", "PASS" if not absent else ("WARN" if simulate else HARDWARE_REQUIRED),
        "ok" if not absent else "missing: " + ", ".join(absent) + " (python tools/download_models.py)")
    if simulate:
        add("camera", "WARN", "SIMULATED (no camera used)")
    else:
        ok, reason = (check_camera or _camera_ok)(camera_index)
        reason = redact(reason)
        add("camera", "PASS" if ok else HARDWARE_REQUIRED, reason)
    priv = sm.privacy.evaluation_private_dir
    try:
        os.makedirs(priv, mode=0o700, exist_ok=True)
        probe = os.path.join(priv, ".write-test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        ignored = _git_ignored(root, priv)
        add("private_storage", "PASS" if ignored is not False else "FAIL",
            f"{priv}" + ("" if ignored is not False else " is NOT git-ignored"))
    except OSError as exc:
        add("private_storage", "FAIL", f"{priv}: {exc}")
    free_mb = shutil.disk_usage(priv if os.path.isdir(priv) else root).free // (1024 * 1024)
    add("disk_space", "PASS" if free_mb >= 500 else "WARN", f"{free_mb} MB free")
    try:
        sm.repo.get_users_list()
        add("database", "PASS", type(sm.repo).__name__)
    except Exception as exc:
        add("database", "FAIL", repr(exc))
    add("evidence_mode", "PASS", "ON: clips only for students with the evidence scope, cropped, 24 h"
        if sm.evidence.enabled else "OFF (no video is stored)")
    return out


def _camera_ok(index):
    try:
        import cv2
    except Exception as exc:
        return False, f"OpenCV not installed ({exc.__class__.__name__}); install requirements.txt"
    try:
        cap = cv2.VideoCapture(index)
        ok = cap.isOpened() and cap.read()[0]
        cap.release()
    except Exception as exc:
        return False, f"camera error {exc.__class__.__name__}"
    return (True, f"camera {index} ok") if ok else (False, f"no frame from camera index {index}")


def _git_ignored(root, path):
    import subprocess
    try:
        rel = os.path.relpath(path, root)
        if rel.startswith(".."):
            return None                        # outside the repository: cannot be committed by accident
        r = subprocess.run(["git", "check-ignore", "-q", rel + "/x"], cwd=root, capture_output=True)
        return r.returncode == 0
    except Exception:
        return None


# --------------------------------------------------------------- the run
class FieldRun:
    def __init__(self, sm, io, source, *, class_id, teacher, scenarios=None, report_dir=None,
                 strict=False, simulate=False, annotations_csv=None, identity_csv=None, check_camera=None,
                 scenario_seconds=None):
        self.sm = sm
        self.io = io
        self.source = source
        self.class_id = int(class_id)
        self.teacher = teacher
        self.scenario_ids = scenarios or [s["scenario_id"] for s in SCENARIOS]
        self.report_dir = report_dir
        self.strict = strict
        self.simulate = simulate
        self.annotations_csv = annotations_csv
        self.identity_csv = identity_csv
        self.check_camera = check_camera
        self.scenario_seconds = scenario_seconds     # unattended real runs: fixed scenario length
        self.identity_frames, self.perf = [], {"frame_timestamps": [], "frame_latency_ms": [],
                                               "identity_latency_ms": []}
        self.result = {}

    def status(self, kind, **extra):
        self.io.say("STATUS: " + kind + (" " + json.dumps(extra, ensure_ascii=False) if extra else ""))
        self.result.setdefault("status", kind)

    def _sink(self, t, ident, lat, id_lat):
        self.perf["frame_timestamps"].append(t)
        if lat is not None:                       # measured only by the real camera pipeline
            self.perf["frame_latency_ms"].append(lat)
        if id_lat is not None:
            self.perf["identity_latency_ms"].append(id_lat)
        for row in ident:
            self.identity_frames.append(dict(row, t=t))

    # ---------------------------------------------------------------- steps
    def run(self) -> int:
        io = self.io
        io.say("== 1/9 Kiểm tra trước (preflight) ==")
        check_camera = self.check_camera
        if check_camera is None and getattr(self.source, "camera", None) is not None:
            check_camera = lambda _idx: self.source.check(self.sm)       # noqa: E731  saved IP camera / webcam
        checks = preflight(self.sm, simulate=self.simulate, check_camera=check_camera,
                           camera_index=getattr(self.source, "camera_index", 0))
        for c in checks:
            io.say(f"  [{c['status']:17s}] {c['check']}: {c['detail']}")
        self.result["preflight"] = checks
        if any(c["status"] == "FAIL" for c in checks):
            self.status("PREFLIGHT_FAILED")
            return EXIT_ERROR
        hw = [c for c in checks if c["status"] == HARDWARE_REQUIRED]
        if hw:
            self.status(HARDWARE_REQUIRED, missing=[c["check"] for c in hw])
            io.say("Không có camera / thư viện camera: bước thực địa chưa chạy được. Không có kết quả giả.")
            return EXIT_HARDWARE if self.strict else EXIT_OK

        io.say("== 2/9 Đồng ý (consent) ==")
        from ...authz import build_principal, can_run_evaluation
        user = self.sm.repo.get_user_by_username(self.teacher)
        if not user or user.get("role") != "teacher":
            self.status("CONFIG_ERROR", reason="teacher username not found")
            return EXIT_ERROR
        actor = build_principal(user["id"], "teacher", self.sm.repo)
        if not can_run_evaluation(actor, self.class_id):
            self.status("CONFIG_ERROR", reason="teacher not assigned to this class")
            return EXIT_ERROR
        participants = self.sm.evaluation.consented_students(self.class_id)
        io.say(f"  Học sinh có đồng ý phạm vi 'Tham gia đánh giá thử nghiệm': {len(participants)}")
        for p in participants:
            io.say(f"   - #{p['student_id']} {p['display_name']}")
        if not participants:
            self.status("CONSENT_REQUIRED", reason="no student with the evaluation consent scope")
            io.say("Quản trị viên cần ghi nhận đồng ý (Quản trị > Dữ liệu & quyền riêng tư) trước.")
            return EXIT_CONSENT
        if len(participants) < RECOMMENDED_PARTICIPANTS:
            io.say(f"  Lưu ý: khuyến nghị ít nhất {RECOMMENDED_PARTICIPANTS} học sinh; hiện có {len(participants)}.")
        # A real run needs an explicit "y" from the operator (or the answers file); only the
        # synthetic rehearsal may default it.
        if not io.confirm("Phiếu đồng ý giấy khớp danh sách trên, camera đặt công khai, học sinh đã được thông báo?",
                          default=self.simulate):
            self.status("CONSENT_REQUIRED", reason="operator did not confirm consent")
            return EXIT_CONSENT

        io.say("== 3/9 Camera ==")
        camera_meta = self.source.camera_metadata() if hasattr(self.source, "camera_metadata") else None
        if camera_meta:
            io.say(f"  Camera: {camera_meta.get('camera_name')} ({camera_meta.get('camera_source_type')})")
        rt = self.sm.start_class(self.class_id, mode="classroom", started_by=actor.user_id, camera=camera_meta)
        eid, meta, failure = None, {"device": None}, None
        try:
            self.source.open(self.sm, self.class_id, rt.class_session_id)
            measured = self.source.measured()
            io.say(f"  Đo được: độ phân giải {measured.get('resolution')}, FPS camera {measured.get('camera_fps')}")
            meta = {
                "camera_position": io.ask("Vị trí camera", "FRONT_CORNER", list(CAMERA_POSITIONS)),
                "camera_angle": io.ask("Góc / độ cao camera", "góc trái, cao 2,2 m, nghiêng 20°"),
                "lighting_condition": io.ask("Ánh sáng", "DAYLIGHT", list(LIGHTING)),
                "device": io.ask("Thiết bị (máy + camera)", "SIMULATED" if self.simulate else ""),
                "resolution": measured.get("resolution"), "fps_target": io.ask("FPS mục tiêu", "7.5"),
                "student_count": len(participants), "consent_verified": True,
                "dataset_kind": self.source.kind, "activity_context": "LECTURE",
                "notes": "field_test.py" + (" --simulate" if self.simulate else ""),
            }
            io.say("== 4/9 Tạo buổi đánh giá ==")
            es = self.sm.evaluation.start(actor, self.class_id, meta, camera_meta=camera_meta)
            eid = es["evaluation_session_id"]
            self.result["evaluation_session_id"] = eid
            io.say(f"  evaluation_session_id = {eid} ({meta['dataset_kind']})")
            apply_ctx = io.confirm("Tự đổi hoạt động lớp theo từng kịch bản?", True)

            io.say("== 5/9 Kịch bản ==")
            for sid in self.scenario_ids:
                sc = get_scenario(sid)
                if sc is None:
                    io.say(f"  bỏ qua kịch bản không tồn tại: {sid}")
                    continue
                io.say(f"\n--- {sid}: {sc['title']} ({sc['suggested_minutes']} phút, {sc['participants']}) ---")
                for i, step in enumerate(sc["steps"], 1):
                    io.say(f"  {i}. {step}")
                io.say(f"  Người quan sát: {sc['observer']}")
                choice = io.ask("Enter = bắt đầu, s = bỏ qua, q = dừng", "go", None)
                if choice == "q":
                    break
                if choice == "s":
                    continue
                self.sm.evaluation.start_scenario(actor, eid, sid, apply_context=apply_ctx)
                self._run_scenario(sid, sc)
                self.sm.evaluation.stop_scenario(actor, eid)
                io.say(f"  Đã ghi kịch bản {sid}.")
        except Exception as exc:          # camera/model failure mid-run: close everything, report, never fake
            failure = exc
        finally:
            self.source.close()
            if eid is not None and (self.sm.evaluation.get(eid) or {}).get("status") == "ACTIVE":
                self.sm.evaluation.end(actor, eid)
            if self.sm.class_runtime(self.class_id) is not None:
                self.sm.end_class(self.class_id)
            if eid is not None:
                self.sm.evaluation.save_field_recording(eid, self.identity_frames,
                                                        dict(self.perf, device=meta["device"]))
        if failure is not None:
            self.status("FIELD_ERROR", reason=repr(failure)[:300], evaluation_session_id=eid)
            io.say("Buổi đánh giá và buổi học đã được kết thúc an toàn; dữ liệu đã ghi vẫn được giữ, không có kết quả giả.")
            return EXIT_ERROR

        io.say("== 6/9 Kết thúc buổi ==")
        es = self.sm.evaluation.get(eid)
        io.say(f"  FPS thực đo: {es.get('actual_fps')}")

        io.say("== 7/9 Nhãn của người quan sát ==")
        ann_tpl, id_tpl = self.write_templates(eid)
        io.say(f"  Mẫu nhãn: {ann_tpl}\n  Mẫu danh tính: {id_tpl}")
        ann_path = self.annotations_csv
        if self.simulate and ann_path is None:
            ann_path = self.write_simulated_annotations(eid)
            io.say(f"  [SYNTHETIC] nhãn mô phỏng: {ann_path}")
        elif ann_path is None:
            ann_path = io.ask("Đường dẫn file nhãn đã điền (Enter = hoàn tất sau bằng finalize)", "") or None
        if ann_path:
            with open(ann_path, "r", encoding="utf-8-sig") as f:
                res = self.sm.evaluation.import_annotations(actor, eid, list(csv.DictReader(f)))
            io.say(f"  Đã nhập {res['added']} nhãn (bỏ qua {len(res['skipped'])}).")
        id_path = self.identity_csv or (self.write_simulated_identity(eid) if self.simulate else None)
        if id_path:
            with open(id_path, "r", encoding="utf-8-sig") as f:
                res = self.sm.evaluation.import_identity(eid, list(csv.DictReader(f)))
            io.say(f"  Bảng danh tính: {res['labelled_frames']}/{res['frames']} khung hình có nhãn.")

        io.say("== 8/9 Tổng hợp (finalize) ==")
        report = build_report(self.sm.evaluation.package(eid), self.sm)
        out_dir = self.report_dir or os.path.join(_root(), "evaluation", "reports",
                                                  "field-simulation" if self.simulate else "real-classroom", eid)
        write_outputs(report, out_dir)
        clean = scan_paths([out_dir], quiet=True, repo=self.sm.repo)
        self.result.update(report_dir=out_dir, verdict=report["verdict"],
                           real_world_evidence=report["real_world_evidence"], privacy_clean=clean)

        io.say("== 9/9 Kết quả ==")
        io.say(f"  Báo cáo: {out_dir}")
        io.say("  REAL_WORLD_EVIDENCE: " + ("YES" if report["real_world_evidence"] else "NO") + f" ({report['verdict']})")
        io.say("  ARTIFACT_PRIVACY_CHECK: " + ("PASS" if clean else "FAIL"))
        if not ann_path:
            io.say("  Sau khi điền nhãn: python scripts/finalize_real_classroom_evaluation.py "
                   f"{eid} --annotations-csv <file> --teacher {self.teacher} --identity-csv <file>")
        self.status("FIELD_TEST_COMPLETE", evaluation_session_id=eid, dataset_kind=report["metadata"]["dataset_kind"])
        return EXIT_OK if clean else EXIT_ERROR

    def _run_scenario(self, sid, sc):
        """Frames flow on a worker thread until the operator stops the scenario. A worker
        failure is re-raised here (never silently 'recording nothing')."""
        stop, errors = threading.Event(), []

        def work():
            try:
                self.source.run(sid, stop, self._sink)
            except Exception as exc:
                errors.append(exc)
                stop.set()
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        if not self.simulate:
            timed = self.scenario_seconds
            if timed is None and isinstance(self.io, ScriptedIO):
                timed = sc["suggested_minutes"] * 60.0          # unattended: the scenario's suggested length
            if timed is not None:
                stop.wait(timed)
            else:
                self.io.ask("Enter = kết thúc kịch bản", "stop", None)
            stop.set()
        worker.join()
        if errors:
            raise RuntimeError(f"camera loop failed during {sid}: {errors[0]!r}")

    # ----------------------------------------------------------- templates
    def write_templates(self, eid):
        """Observer sheets pre-filled with scenario windows; labels left EMPTY
        (the expected labels are only a hint - the observer writes what they saw)."""
        run_dir = self.sm.evaluation.run_dir(eid)
        os.makedirs(run_dir, mode=0o700, exist_ok=True)
        es = self.sm.evaluation.get(eid)
        t0 = es["started_at"]
        people = self.sm.evaluation.consented_students(self.class_id)
        ann_path = os.path.join(run_dir, "observer-annotations-template.csv")
        id_path = os.path.join(run_dir, "observer-identity-template.csv")
        runs = self.sm.evaluation.scenario_runs(eid)
        with open(ann_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["student_id", "label", "start_s", "end_s", "note", "scenario_id", "expected_label_hint"])
            for r in runs:
                sc = get_scenario(r["scenario_id"]) or {}
                for p in people:
                    w.writerow([p["student_id"], "", round(r["started_at"] - t0, 1),
                                round((r.get("ended_at") or r["started_at"]) - t0, 1), "", r["scenario_id"],
                                "|".join(sc.get("expected_labels", []))])
        with open(id_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["track_id", "student_id", "start_s", "end_s", "scenario_id"])
            for r in runs:
                if (get_scenario(r["scenario_id"]) or {}).get("identity_sheet"):
                    w.writerow(["", "", round(r["started_at"] - t0, 1),
                                round((r.get("ended_at") or r["started_at"]) - t0, 1), r["scenario_id"]])
        return ann_path, id_path

    def write_simulated_annotations(self, eid):
        t0 = self.sm.evaluation.get(eid)["started_at"]
        path = os.path.join(self.sm.evaluation.run_dir(eid), "synthetic-annotations.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["student_id", "label", "start_s", "end_s", "note"])
            for g in self.source.ground_truth:
                w.writerow([g["student_id"], g["label"], round(g["t0"] + g["start_s"] - t0, 3),
                            round(g["t0"] + g["end_s"] - t0, 3), "SYNTHETIC " + g["scenario_id"]])
        return path

    def write_simulated_identity(self, eid):
        """In simulation the true identity of each track is known: write the sheet an observer would."""
        t0 = self.sm.evaluation.get(eid)["started_at"]
        spans = {}
        for fr in self.identity_frames:
            key = fr["track_id"]
            a, b, gt = spans.get(key, (fr["t"], fr["t"], fr["gt_student_id"]))
            spans[key] = (min(a, fr["t"]), max(b, fr["t"]), gt)
        path = os.path.join(self.sm.evaluation.run_dir(eid), "synthetic-identity.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["track_id", "student_id", "start_s", "end_s"])
            for tid, (a, b, gt) in sorted(spans.items()):
                w.writerow([tid, "STRANGER" if gt is None else gt, round(a - t0 - 0.01, 3), round(b - t0 + 0.01, 3)])
        return path


def _root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ---------------------------------------------------------- simulation env
def simulation_manager(tmp_dir, participants=6):
    """Isolated SQLite database + private folder for --simulate (never the real data)."""
    os.makedirs(tmp_dir, exist_ok=True)
    from ...authz import Principal
    from ...privacy.settings import PrivacySettings
    from repository import SQLiteRepository
    from session_manager import SessionManager
    clock = SimClock(time.time())
    settings = PrivacySettings(evaluation_private_dir=os.path.join(tmp_dir, "evaluation-private"),
                               evidence_dir=os.path.join(tmp_dir, "evidence"))
    sm = SessionManager(repo=SQLiteRepository(os.path.join(tmp_dir, "simulation.db")), seed_demo_accounts=True,
                        clock=clock, privacy_settings=settings)
    admin = Principal(1, "admin")
    for s in sm.repo.get_students_in_class(1)[:participants]:
        sm.consent.grant(admin, s["student_id"], guardian_name="SYNTHETIC guardian", guardian_relationship="SYNTHETIC",
                         scopes={"evaluation": True})
    return sm, clock
