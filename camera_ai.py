"""Personal / online-class camera pipeline (one student in front of a webcam).

Produces observations only. The authenticated ``student_id`` is the identity
(the camera belongs to the logged-in student); no score is computed here.
Everything is routed to SessionManager -> the single SessionRuntime/FocusEngine
currently responsible for that student.

Requires hardware + model weights (OpenCV, MediaPipe, YOLOv8).
"""
import time

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from ultralytics import YOLO

from focusguard.association import associate_phones
from focusguard.behavior import Observation
from focusguard.vision_metrics import mean_ear
from focusguard import runtime_state as rs

STATE_COLORS = {
    rs.FOCUS_FOCUSED: (0, 200, 0),
    rs.FOCUS_PHONE: (0, 0, 255),
    rs.FOCUS_DROWSY: (0, 165, 255),
    rs.FOCUS_HEAD_AWAY: (0, 255, 255),
}


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


class FocusAI:
    mode = "personal"

    def __init__(self, session_manager, student_id, camera_index=0):
        self.session_manager = session_manager
        self.student_id = int(student_id)
        self.camera_index = camera_index
        self.config = session_manager.config.behavior
        self.yolo_model = YOLO("yolov8n.pt")
        options = vision.FaceLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path="face_landmarker.task"),
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True,
            num_faces=2,
        )
        self.detector = vision.FaceLandmarker.create_from_options(options)
        self.cap = _open_capture(camera_index)
        self.device_ok = None
        self._consecutive_failures = 0
        self._last_reopen_at = 0.0
        for _ in range(15):  # warm-up
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



        self.current_state = rs.FOCUS_UNKNOWN
        self.has_seen_face = False
        self.face_missing_since = None

    def get_frame(self):
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
        frame = cv2.flip(frame, 1)
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        persons, phones = {}, []
        for r in self.yolo_model(rgb, imgsz=320, classes=[0, 67], verbose=False):
            for i, box in enumerate(r.boxes):
                cls_id = int(box.cls[0])
                x1, y1, x2, y2 = map(float, box.xyxy[0])
                conf = float(box.conf[0])
                if cls_id == 67:
                    phones.append(((x1, y1, x2, y2), conf))
                elif cls_id == 0:
                    persons[i] = (x1, y1, x2, y2)

        result = self.detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        faces = result.face_landmarks or []
        ear = yaw = pitch = None
        if faces:
            self.has_seen_face = True
            self.face_missing_since = None
            ear = mean_ear(faces[0])
            angles, *_ = cv2.RQDecomp3x3(np.asarray(result.facial_transformation_matrixes[0])[0:3, 0:3])
            pitch, yaw = float(angles[0]), float(angles[1])
        else:
            if self.has_seen_face and self.face_missing_since is None:
                self.face_missing_since = now

        # The student is the largest person box (or the face if no person box).
        phone_conf = None
        if persons:
            main_id = max(persons, key=lambda k: (persons[k][2] - persons[k][0]) * (persons[k][3] - persons[k][1]))
            phone_conf = associate_phones({main_id: persons[main_id]}, phones,
                                          self.config.phone_min_confidence,
                                          self.config.phone_min_containment).get(main_id)
        visible = bool(faces) or bool(persons)
        now = time.time()
        obs = Observation(timestamp=now, visible=visible, face_visible=bool(faces), ear=ear, yaw=yaw,
                          pitch=pitch, phone_confidence=phone_conf, source="personal_camera",
                          extra={"people_in_frame": max(len(persons), len(faces))})
        self.session_manager.route_observation(self.student_id, obs)

        view = self.session_manager.student_live_view(self.student_id).get("student") or {}
        self.current_state = view.get("focus_state", rs.FOCUS_UNKNOWN)
        color = STATE_COLORS.get(self.current_state, (200, 200, 200))
        for (x1, y1, x2, y2), conf in phones:
            if conf >= self.config.phone_min_confidence:
                cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 2)
        if faces:
            xs = [lm.x * w for lm in faces[0]]
            ys = [lm.y * h for lm in faces[0]]
            cv2.rectangle(frame, (int(min(xs)) - 15, int(min(ys)) - 15), (int(max(xs)) + 15, int(max(ys)) + 15), color, 2)
        label = rs.VI_LABELS.get(self.current_state, self.current_state)
        cv2.putText(frame, f"{self.current_state} {'' if score is None else score}", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        ret, buf = cv2.imencode(".jpg", frame)
        if not ret or buf is None:
            print("[CAMERA] cv2.imencode failed to generate JPEG")
            return None, False, "JPEG_ENCODE_FAILED"
        return buf.tobytes(), self.current_state not in (rs.FOCUS_FOCUSED, rs.FOCUS_UNKNOWN), label

    def release(self):
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()
