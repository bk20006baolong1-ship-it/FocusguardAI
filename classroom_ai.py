"""Classroom camera pipeline (one camera, many students).

    frame -> YOLOv8 + ByteTrack (temporary track_id)
          -> InsightFace faces, associated one-to-one to tracks
          -> IdentityManager (canonical student_id with temporal confirmation)
          -> per-student Observation (EAR, head pose, associated phone)
          -> SessionRuntime.process_frame (the single FocusEngine scores)
          -> visualisation overlay

This module owns NO focus score and NO per-student state beyond caches of raw
measurements. Requires hardware + model weights.
"""
import time

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from face_recognition import FaceRecognizer
from focusguard.cameras.reader import LatestFrameReader
from focusguard.cameras.sources import WebcamSource
from focusguard.association import associate_faces, associate_phones
from focusguard.behavior import Observation
from focusguard.classroom_identity import ClassroomIdentity, FaceObs
from focusguard.identity import CONFIRMED
from focusguard.vision_metrics import mean_ear
from focusguard import runtime_state as rs
from seating_manager import SeatingManager
from tracker import PersonTracker

STATE_COLORS = {
    rs.FOCUS_FOCUSED: (0, 200, 0),
    rs.FOCUS_DROWSY: (0, 165, 255),
    rs.FOCUS_HEAD_AWAY: (0, 255, 255),
    rs.FOCUS_PHONE: (0, 0, 255),
}
LANDMARK_INTERVAL = 0.3      # seconds between MediaPipe runs per track
LANDMARK_MAX_AGE = 0.6       # cached EAR/pose older than this is "not measured"


class ClassroomAI:
    mode = "classroom"

    def __init__(self, session_manager, class_id, camera_index=0, class_session_id=None, source=None):
        self.session_manager = session_manager
        self.class_id = int(class_id)
        # The pipeline only ever feeds the classroom session it was started for.
        self.class_session_id = class_session_id
        self.camera_index = camera_index
        # Any CameraSource (saved IP camera / webcam); default: the local webcam, as always.
        self.source = source if source is not None else WebcamSource(camera_index, warmup_reads=15)
        self.config = session_manager.config
        self.tracker = PersonTracker()
        self.face_recognizer = FaceRecognizer()
        self.seating_manager = SeatingManager(rows=7, cols=4)
        options = vision.FaceLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path="face_landmarker.task"),
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True,
            num_faces=1,
        )
        self.detector = vision.FaceLandmarker.create_from_options(options)
        # Dedicated reader thread, latest frame only: no backlog, no frame analysed twice.
        self.reader = LatestFrameReader(self.source, stale_after=3.0).start()
        self._seq = 0
        self._offline_at = 0.0
        self.device_ok = None
        # Identity layer: auto recognition (multi-template), teacher tags, enrollment, attendance plan.
        self.coord = ClassroomIdentity(session_manager, self.class_id, class_session_id, config=self.config)
        self.identity = self.coord.identity
        self.names = dict(self.coord.roster)
        self._landmark_cache = {}   # track_id -> (timestamp, ear, yaw, pitch)
        self.last_debug = {}
        self.last_frame_meta = {}   # identity decisions + latency of the last frame (evaluation tool)
        evidence = getattr(session_manager, "evidence", None)
        if evidence is not None and evidence.enabled:
            evidence.cropper = crop_jpeg

    def reload_gallery(self):
        # Only students with ACTIVE face-recognition consent (server-enforced).
        self.coord.reload_gallery()
        print(f"[CLASSROOM AI] Gallery: {self.coord.gallery_size} students with face templates, class {self.class_id}")

    # Backwards compatibility with admin face registration hook in app.py
    @property
    def known_students(self):
        return self.names

    def release(self, final=False):
        """``final``: the class itself ended (pending progressive samples may be committed)."""
        self.reader.stop()
        self.coord.close(final=final)

    def camera_health(self):
        return self.reader.health()

    def _offline(self):
        """No new frame and no signal: nothing is analysed (NO_CAMERA_SIGNAL, never AWAY)."""
        # Still connecting for the first time: not "unavailable" yet (the UI shows STARTING).
        self.device_ok = None if (self.reader.state == "CONNECTING" and self.reader.seq == 0) else False
        self.last_frame_meta = {}  # no frame analysed: evaluation tools must not record one
        now = time.time()
        if now - self._offline_at < 0.5:
            return None, False, "NO_CAMERA_SIGNAL"
        self._offline_at = now
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        label = "DANG KET NOI LAI CAMERA..." if self.reader.state == "RECONNECTING" else "CLASSROOM CAMERA OFFLINE"
        cv2.putText(dummy, label, (110, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        _, buf = cv2.imencode(".jpg", dummy)
        return buf.tobytes(), False, "NO_CAMERA_SIGNAL"

    def _landmarks(self, track_id, crop, now):
        cached = self._landmark_cache.get(track_id)
        if cached and now - cached[0] < LANDMARK_INTERVAL:
            return cached[1:]
        if crop is None or crop.size == 0:
            return (None, None, None) if not cached or now - cached[0] > LANDMARK_MAX_AGE else cached[1:]
        res = self.detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                            data=cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))
        if not res.face_landmarks:
            self._landmark_cache[track_id] = (now, None, None, None)
            return None, None, None
        ear = mean_ear(res.face_landmarks[0])
        angles, *_ = cv2.RQDecomp3x3(np.asarray(res.facial_transformation_matrixes[0])[0:3, 0:3])
        value = (now, ear, float(angles[1]), float(angles[0]))
        self._landmark_cache[track_id] = value
        return value[1:]

    def get_frame(self, block=0.0):
        """Analyse the newest frame. ``block`` > 0 (plain-thread tools only) waits up to that many
        seconds for a new frame; the server's stream loop polls with block=0 (never blocks eventlet)."""
        runtime = self.session_manager.classroom_runtime(self.class_id, self.class_session_id)
        latest = self.reader.wait_newer(self._seq, block) if block else self.reader.get_newer(self._seq)
        if latest is None:
            if self.reader.has_signal():
                self.device_ok = True
                return None, False, "OK"      # stream healthy, no NEW frame yet: nothing to analyse
            return self._offline()
        self.device_ok = True         # read by the server to report a truthful camera state
        self._seq = latest.seq
        frame = latest.image.copy()   # overlays are drawn on our copy only
        now = latest.timestamp        # when the frame arrived (not when we got round to it)
        frame_age_ms = max(0.0, (time.time() - now) * 1000.0)   # camera ingest latency, measured separately
        t_start = time.perf_counter()
        h, w = frame.shape[:2]
        tracked = self.tracker.track(frame)
        persons = {p["track_id"]: tuple(p["bbox"]) for p in tracked}
        seats = self.seating_manager.assign_seats(tracked, w, h)

        phones = self.tracker.detect_phones(frame)   # separate detector: never touches ByteTrack state
        beh = self.config.behavior
        phone_by_track = associate_phones(persons, phones, beh.phone_min_confidence, beh.phone_min_containment)

        try:
            faces = self.face_recognizer.app.get(frame)
        except Exception as exc:
            print(f"[CLASSROOM AI] Face detection error: {exc}")
            faces = []
        face_boxes = [tuple(map(float, f.bbox)) for f in faces]
        face_by_track = associate_faces(persons, face_boxes)

        # Raw detector numbers for verification logs (no images).
        self.last_debug = {
            "faces": len(faces), "persons": len(persons), "faces_associated": len(face_by_track),
            "phones": [round(c, 3) for _, c in phones],
            "phone_boxes": [[round(v) for v in b] for b, _ in phones],
            "person_boxes": {str(k): [round(v) for v in b] for k, b in persons.items()},
            "frame_hw": [h, w],
            "phones_associated": {str(k): round(v, 3) for k, v in phone_by_track.items()},
        }
        t_id = time.perf_counter()
        decisions = self.coord.identify(
            now, persons, {tid: faces[face_by_track[tid]].embedding for tid in persons if tid in face_by_track},
            occluded=set(phone_by_track), frame_hw=(h, w))
        identity_ms = (time.perf_counter() - t_id) * 1000.0

        observations, face_obs, poses = {}, {}, {}
        for tid, box in persons.items():
            d = decisions.get(tid)
            crop = None
            if tid in face_by_track:
                fx1, fy1, fx2, fy2 = map(int, face_boxes[face_by_track[tid]])
                fw, fh = fx2 - fx1, fy2 - fy1
                crop = frame[max(0, fy1 - fh // 4):min(h, fy2 + fh // 4), max(0, fx1 - fw // 4):min(w, fx2 + fw // 4)]
            if d is not None and d.status == CONFIRMED and d.student_id is not None:
                poses[tid] = self._landmarks(tid, crop, now)
                ear, yaw, pitch = poses[tid]
                self.last_debug.setdefault("landmarks", {})[str(tid)] = [
                    None if v is None else round(float(v), 3) for v in (ear, yaw, pitch)]
            if tid in face_by_track:
                f = faces[face_by_track[tid]]
                fx1, fy1, fx2, fy2 = map(int, face_boxes[face_by_track[tid]])
                gray = cv2.cvtColor(frame[max(0, fy1):max(0, fy2), max(0, fx1):max(0, fx2)], cv2.COLOR_BGR2GRAY) \
                    if fx2 > fx1 and fy2 > fy1 else None
                _e, yaw, pitch = poses.get(tid, (None, None, None))
                # in memory for this frame only: quality check of an enrollment candidate, never stored
                face_obs[tid] = FaceObs(f.embedding, face_boxes[face_by_track[tid]],
                                        float(getattr(f, "det_score", 0.0) or 0.0), gray, yaw, pitch)
        plan = self.coord.after_frame(now, decisions, face_obs, frame_hw=(h, w), occluded=set(phone_by_track))
        if plan.get("closed"):
            runtime = None          # the pipeline was closed meanwhile: this frame updates no one
        for tid, sid in plan["confirmed"].items():
            d = decisions[tid]
            ear, yaw, pitch = poses.get(tid, (None, None, None))
            observations[sid] = Observation(
                timestamp=now, visible=True, face_visible=tid in face_by_track, ear=ear, yaw=yaw, pitch=pitch,
                phone_confidence=phone_by_track.get(tid), identity_confidence=d.confidence, track_id=tid,
                seat=seats.get(tid), source="classroom_camera",
                extra=dict(plan["bindings"].get(tid) or {}))      # binding provenance for attendance evidence
        for sid in plan["unresolved_students"]:
            # Someone unidentified is in the frame who could be this student: no AWAY timer, no absence.
            observations.setdefault(sid, Observation(timestamp=now, visible=False, source="classroom_camera",
                                                     extra={"identity_unresolved": True}))
        for tid in list(self._landmark_cache):
            if tid not in persons:
                del self._landmark_cache[tid]

        if runtime is not None:
            runtime.process_frame(now, observations, unidentified=plan["unidentified"])

        evidence = getattr(self.session_manager, "evidence", None)
        evidence_on = evidence is not None and evidence.enabled and runtime is not None
        # Evidence frames come from the camera image BEFORE names/states/scores are drawn on it.
        clean_jpeg = cv2.imencode(".jpg", frame)[1].tobytes() if evidence_on else None

        # ---- visualisation only -------------------------------------------------
        for tid, (x1, y1, x2, y2) in persons.items():
            d = decisions.get(tid)
            if d is not None and d.status == CONFIRMED and runtime is not None and not d.review_required:
                view = runtime.student_view(d.student_id) or {}
                state = view.get("focus_state", rs.FOCUS_UNKNOWN)
                color = STATE_COLORS.get(state, (200, 200, 200))
                score = view.get("focus_score")
                label = f"{self.names.get(d.student_id, d.student_id)} {state} {'' if score is None else score}"
            else:
                color = (128, 128, 128)
                status = d.status if d else "UNKNOWN"
                label = f"Track {tid}: {status}"
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
            cv2.putText(frame, label, (int(x1), max(12, int(y1) - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        _, buf = cv2.imencode(".jpg", frame)
        jpeg = buf.tobytes()
        self.last_frame_meta = {
            "t": now, "latency_ms": (time.perf_counter() - t_start) * 1000.0, "identity_latency_ms": identity_ms,
            "frame_age_ms": frame_age_ms, "camera_id": getattr(self.source, "camera_id", None),
            "tracks": {tid: {"status": d.status, "student_id": d.student_id if d.status == CONFIRMED else None,
                             "confidence": round(float(d.confidence), 4)} for tid, d in decisions.items()},
        }
        if evidence_on:
            # RAM ring buffer only; a clip is written only around an event of a
            # student with ACTIVE event-evidence consent (OFF by default), and
            # only from frames cropped to that student (EvidenceService._save_clip).
            boxes = {sid: persons[tid] for tid, sid in plan["confirmed"].items() if tid in persons}
            evidence.push_frame(runtime.class_session_id, now, clean_jpeg, boxes)
        return jpeg, False, "OK"


def crop_jpeg(jpeg, box, margin=0.15):
    """Crop an evidence frame to one student's box so a clip shows as little of
    other students as possible."""
    arr = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return None
    h, w = arr.shape[:2]
    x1, y1, x2, y2 = box
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    x1, y1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
    x2, y2 = min(w, int(x2 + mx)), min(h, int(y2 + my))
    if x2 <= x1 or y2 <= y1:
        return None
    ok, buf = cv2.imencode(".jpg", arr[y1:y2, x1:x2])
    return buf.tobytes() if ok else None
