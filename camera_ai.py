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
        self.cap = cv2.VideoCapture(camera_index)
        for _ in range(30):  # macOS AVFoundation warm-up
            ok, _ = self.cap.read()
            if ok:
                break
            time.sleep(0.1)
        self.current_state = rs.FOCUS_UNKNOWN
        self.has_seen_face = False
        self.face_missing_since = None

    def _offline_frame(self, text):
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(dummy, text, (110, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
        _, buf = cv2.imencode(".jpg", dummy)
        return buf.tobytes()

    def get_frame(self):
        if not self.cap.isOpened():
            self.cap.open(self.camera_index)
        ok, frame = self.cap.read()
        self.device_ok = bool(ok)     # read by the server to report a truthful camera state
        if not ok:
            time.sleep(0.5)
            return self._offline_frame("CAMERA KHONG KHA DUNG"), False, "NO_CAMERA_SIGNAL"

        now = time.time()
        frame = cv2.flip(frame, 1)
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        persons, phones = {}, []
        for r in self.yolo_model(rgb, classes=[0, 67], verbose=False):
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
            if self.has_seen_face:
                if self.face_missing_since is None:
                    self.face_missing_since = now
                missing_duration = now - self.face_missing_since
                # Khi học sinh quay mặt đi hoặc quay đầu khiến không nhận diện được khuôn mặt,
                # gán yaw = 35.0 (> yaw_limit_deg 20.0) để BehaviorAnalyzer đếm thời gian vi phạm (~2s)
                # và kích hoạt trạng thái HEAD_AWAY theo cơ chế tính điểm chuẩn của hệ thống.
                if persons or missing_duration < self.config.away_min_seconds:
                    yaw = 35.0
                    pitch = 0.0

        # The student is the largest person box (or the face if no person box).
        phone_conf = None
        if persons:
            main_id = max(persons, key=lambda k: (persons[k][2] - persons[k][0]) * (persons[k][3] - persons[k][1]))
            phone_conf = associate_phones({main_id: persons[main_id]}, phones,
                                          self.config.phone_min_confidence,
                                          self.config.phone_min_containment).get(main_id)
        visible = bool(faces) or bool(persons)
        if not visible and self.has_seen_face and self.face_missing_since is not None:
            if (now - self.face_missing_since) < self.config.away_min_seconds:
                visible = True
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
        score = view.get("focus_score")
        cv2.putText(frame, f"{self.current_state} {'' if score is None else score}", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        _, buf = cv2.imencode(".jpg", frame)
        return buf.tobytes(), self.current_state not in (rs.FOCUS_FOCUSED, rs.FOCUS_UNKNOWN), label

    def release(self):
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()
