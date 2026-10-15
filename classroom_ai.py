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
from focusguard.association import associate_faces, associate_phones
from focusguard.behavior import Observation
from focusguard.identity import CONFIRMED, IdentityManager
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


import os

def _open_capture(index):
    if os.name == "nt":
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, test_frame = cap.read()
            if ok and test_frame is not None and test_frame.size > 0:
                print(f"[CAMERA] Opened camera {index} with DirectShow (CAP_DSHOW): {test_frame.shape[1]}x{test_frame.shape[0]}")
                return cap
            print(f"[CAMERA] CAP_DSHOW opened camera {index} but read() failed, falling back to default backend")
            cap.release()
    cap = cv2.VideoCapture(index)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, test_frame = cap.read()
        if ok and test_frame is not None and test_frame.size > 0:
            print(f"[CAMERA] Opened camera {index} with default backend: {test_frame.shape[1]}x{test_frame.shape[0]}")
            return cap
        print(f"[CAMERA] Default backend opened camera {index} but read() failed")
    else:
        print(f"[CAMERA] Failed to open camera index={index} (isOpened=False)")
    return cap


class ClassroomAI:
    mode = "classroom"

    def __init__(self, session_manager, class_id, camera_index=0, class_session_id=None):
        self.session_manager = session_manager
        self.class_id = int(class_id)
        # The pipeline only ever feeds the classroom session it was started for.
        self.class_session_id = class_session_id
        self.camera_index = camera_index
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
        self.cap = _open_capture(camera_index)
        self.device_ok = None
        self._consecutive_failures = 0
        self._last_reopen_at = 0.0
        for _ in range(15):
            if self.cap is not None and self.cap.isOpened():
                ok, _ = self.cap.read()
                if ok:
                    self.device_ok = True
                    break
            time.sleep(0.1)
        if self.device_ok is not True:
            self.device_ok = False
            print(f"[CAMERA] Warmup: no valid frames read from camera {camera_index} (device_ok=False)")
        else:
            print(f"[CAMERA] Warmup: camera {camera_index} verified valid frame read (device_ok=True)")



        self.identity = IdentityManager(config=self.config.identity)
        self.names = {}
        self.reload_gallery()
        self._landmark_cache = {}   # track_id -> (timestamp, ear, yaw, pitch)
        self.last_debug = {}

    def reload_gallery(self):
        rows = self.session_manager.repo.get_student_embeddings_by_class(self.class_id)
        self.identity.set_gallery({r["student_id"]: r["embedding"] for r in rows})
        self.names = {r["student_id"]: r["full_name"] for r in rows}
        print(f"[CLASSROOM AI] Gallery: {self.identity.gallery_size} enrolled faces for class {self.class_id}")

    # Backwards compatibility with admin face registration hook in app.py
    @property
    def known_students(self):
        return self.names

    def release(self):
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()

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

    def get_frame(self):
        runtime = self.session_manager.classroom_runtime(self.class_id, self.class_session_id)
        now = time.time()

        if self.cap is None or not self.cap.isOpened():
            if now - self._last_reopen_at >= 2.0:
                self._last_reopen_at = now
                print(f"[CAMERA] Capture handle closed, attempting reopen for camera {self.camera_index}")
                self.cap = _open_capture(self.camera_index)

        ok, frame = (False, None)
        if self.cap is not None and self.cap.isOpened():
            ok, frame = self.cap.read()

        if not ok or frame is None or frame.size == 0:
            self._consecutive_failures += 1
            self.device_ok = False
            if self._consecutive_failures == 1 or self._consecutive_failures % 25 == 0:
                print(f"[CAMERA] cap.read() failed for camera {self.camera_index} (failures={self._consecutive_failures})")
            # Safely recover camera connection after consecutive failures with rate limit
            if self._consecutive_failures >= 3 and (now - self._last_reopen_at) >= 2.0:
                self._last_reopen_at = now
                print(f"[CAMERA] Reconnecting camera {self.camera_index} after {self._consecutive_failures} failures...")
                try:
                    if self.cap is not None:
                        self.cap.release()
                except Exception:
                    pass
                time.sleep(0.15)
                self.cap = _open_capture(self.camera_index)

            time.sleep(0.1)
            return None, False, "NO_CAMERA_SIGNAL"

        self._consecutive_failures = 0
        self.device_ok = True

        now = time.time()
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
        now = time.time()
        decisions = self.identity.process_frame(
            [(tid, faces[face_by_track[tid]].embedding if tid in face_by_track else None, tid in phone_by_track)
             for tid in persons], now)

        now = time.time()
        observations = {}
        for tid, box in persons.items():
            d = decisions.get(tid)
            if d is None or d.status != CONFIRMED or d.student_id is None:
                continue
            crop = None
            if tid in face_by_track:
                fx1, fy1, fx2, fy2 = map(int, face_boxes[face_by_track[tid]])
                fw, fh = fx2 - fx1, fy2 - fy1
                crop = frame[max(0, fy1 - fh // 4):min(h, fy2 + fh // 4), max(0, fx1 - fw // 4):min(w, fx2 + fw // 4)]
            ear, yaw, pitch = self._landmarks(tid, crop, now)
            self.last_debug.setdefault("landmarks", {})[str(tid)] = [
                None if v is None else round(float(v), 3) for v in (ear, yaw, pitch)]
            observations[d.student_id] = Observation(
                timestamp=now, visible=True, face_visible=tid in face_by_track, ear=ear, yaw=yaw, pitch=pitch,
                phone_confidence=phone_by_track.get(tid), identity_confidence=d.confidence, track_id=tid,
                seat=seats.get(tid), source="classroom_camera")
        for tid in list(self._landmark_cache):
            if tid not in persons:
                del self._landmark_cache[tid]

        frame_now = time.time()
        if runtime is not None:
            runtime.process_frame(frame_now, observations)

        # ---- visualisation only -------------------------------------------------
        for tid, (x1, y1, x2, y2) in persons.items():
            d = decisions.get(tid)
            if d is not None and d.status == CONFIRMED and runtime is not None:
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
        ret, buf = cv2.imencode(".jpg", frame)
        if not ret or buf is None:
            print("[CAMERA] cv2.imencode failed to generate JPEG")
            return None, False, "JPEG_ENCODE_FAILED"
        return buf.tobytes(), False, "OK"
