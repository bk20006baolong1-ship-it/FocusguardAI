"""Hardware-dependent checks. Skipped unless FOCUSGUARD_HARDWARE_TESTS=1.

They need a webcam and the model weights / CV stack (requirements.txt), so
CI reports them as SKIPPED: HARDWARE_REQUIRED instead of pretending they pass.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("FOCUSGUARD_HARDWARE_TESTS") != "1",
                                reason="HARDWARE_REQUIRED")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_camera_delivers_frames():
    """Real frames from the webcam. Waits up to 30 s for the macOS permission
    dialog on first use (OpenCV fails immediately while it is pending)."""
    import sys
    import time
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from real_camera_check import open_camera
    cap = open_camera(int(os.environ.get("FOCUSGUARD_CAMERA_INDEX", "0")), timeout=30.0)
    try:
        frames = 0
        t_end = time.time() + 2.0
        while time.time() < t_end:
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                frames += 1
        assert frames >= 5, f"only {frames} frames in 2 s"
    finally:
        cap.release()


def test_yolo_and_mediapipe_models_load():
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from ultralytics import YOLO
    YOLO(os.path.join(ROOT, "yolov8n.pt"))
    opts = vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=os.path.join(ROOT, "face_landmarker.task")), num_faces=1)
    vision.FaceLandmarker.create_from_options(opts)


def test_insightface_model_loads():
    from face_recognition import FaceRecognizer
    assert FaceRecognizer().app is not None
