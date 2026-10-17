#!/usr/bin/env python3
"""Seeded FocusGuard server for the browser walkthrough (developer tool, not production).

    python tools/ui_demo_server.py DATA_DIR [PORT]

Uses a temporary SQLite database in DATA_DIR. Everything is seeded through the
real domain layer: demo accounts, consents, a classroom session whose events
come from the real SessionRuntime, one evidence clip of plain grey frames (no
faces), and stored-XSS probes in a display name and a guardian name. Needs
Pillow (requirements-dev.txt) for the grey frames and the evidence crop.

Camera settings run against TEST DOUBLES only (focusguard/cameras/testing.py):
LAN discovery "finds" one simulated EZVIZ camera at 192.168.1.64 (verification
code ABCDEF), the stream is synthetic frames, nothing touches the network. A
second teacher account (teacher2, class 10A2) exists for the IDOR checks.
"""
import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 5055
os.makedirs(DATA, exist_ok=True)
os.environ.update(DATABASE_TYPE="sqlite", FOCUSGUARD_ENV="development",
                  EVALUATION_PRIVATE_DIR=os.path.join(DATA, "evaluation-private"),
                  EVENT_EVIDENCE_DIR=os.path.join(DATA, "evidence"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image  # noqa: E402

from app import create_app  # noqa: E402
from focusguard.authz import Principal  # noqa: E402
from focusguard.behavior import Observation  # noqa: E402
from focusguard.privacy.consent import PrivacySettings  # noqa: E402
from repository import SQLiteRepository  # noqa: E402
from session_manager import SessionManager  # noqa: E402


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s
        return self.t


def grey_jpeg(level):
    buf = io.BytesIO()
    Image.new("RGB", (160, 120), (level, level, level)).save(buf, "JPEG")
    return buf.getvalue()


clock = Clock(time.time() - 1800)
settings = PrivacySettings(evidence_enabled=True, evidence_dir=os.environ["EVENT_EVIDENCE_DIR"],
                           evidence_fps=5.0, evidence_retention_hours=24.0)
sm = SessionManager(repo=SQLiteRepository(os.path.join(DATA, "demo.db")), seed_demo_accounts=True,
                    clock=clock, privacy_settings=settings)
admin = Principal(1, "admin")


def pil_crop(jpeg, box, margin=0.15):
    """Same contract as classroom_ai.crop_jpeg (which ClassroomAI installs at runtime), with Pillow."""
    img = Image.open(io.BytesIO(jpeg))
    x1, y1, w, h = box
    x2, y2 = x1 + w, y1 + h
    mx, my = w * margin, h * margin
    c = img.crop((max(0, int(x1 - mx)), max(0, int(y1 - my)), min(img.width, int(x2 + mx)), min(img.height, int(y2 + my))))
    buf = io.BytesIO()
    c.save(buf, "JPEG")
    return buf.getvalue()


sm.evidence.cropper = pil_crop


# ---- camera settings with test doubles (no network, no real camera) -------------------------
from focusguard.cameras import connect as cam_connect, rtsp as cam_rtsp  # noqa: E402
from focusguard.cameras.crypto import CredentialVault, generate_key  # noqa: E402
from focusguard.cameras.discovery import DiscoveryManager  # noqa: E402
from focusguard.cameras.netguard import NetGuard  # noqa: E402
from focusguard.cameras.onvif import OnvifError  # noqa: E402
from focusguard.cameras.service import CameraService  # noqa: E402
from focusguard.cameras.testing import MockCameraSource, mock_discovery_strategy  # noqa: E402

DEMO_CAMERA_CODE = "ABCDEF"


class DemoCameraDeps(cam_connect.Deps):
    class onvif_client:
        def __init__(self, *a, **k):
            pass

        def device_information(self):
            raise OnvifError("demo: no ONVIF")

    @staticmethod
    def rtsp_options(ip, port):
        return cam_rtsp.OK, "EZVIZ-demo"

    @staticmethod
    def rtsp_probe(ip, port, path, username=None, secret=None, **_k):
        if not username:
            return cam_rtsp.ProbeResult(cam_rtsp.AUTH_REQUIRED, "EZVIZ-demo", False, None, 401)
        if secret != DEMO_CAMERA_CODE:
            return cam_rtsp.ProbeResult(cam_rtsp.AUTH_FAILED, "EZVIZ-demo", False, None, 401)
        ok = path == "/H.264"
        return cam_rtsp.ProbeResult(cam_rtsp.OK if ok else cam_rtsp.NOT_FOUND, "EZVIZ-demo", ok, "H264", 200)


def pil_jpeg(image):
    buf = io.BytesIO()
    Image.fromarray(image[:, :, ::-1]).save(buf, "JPEG", quality=70)
    return buf.getvalue()


guard = NetGuard()
sm.cameras = CameraService(
    sm.repo, sm.audit, vault=CredentialVault(generate_key()), netguard=guard,
    discovery=DiscoveryManager(guard, strategies=[mock_discovery_strategy("192.168.1.64")], local_ips=[],
                               neighbours=[]),
    connect_deps=DemoCameraDeps, measure_seconds=1.0,
    source_factory=lambda spec: MockCameraSource(name=spec.get("name"), width=640, height=360, fps=15,
                                                 source_type=spec.get("source_type")))
sm.cameras.jpeg_encoder = pil_jpeg
sm.repo.create_user("teacher2", os.environ.get("DEMO_ACCOUNT_PASSWORD") or "123", "Cô Lan", "teacher", 2)

# ---- classroom identity with the SYNTHETIC classroom simulator (focusguard/classroom_sim.py) -------
# Five people in front of the "camera": P1 = student 3 and P2 = student 4 (face profiles enrolled),
# P3 = student 9 whose face was WRONGLY enrolled under student 10 (AI suggests the wrong name),
# P4 = student 7 (no face-recognition consent: session-only tag), P5 = student 8 (consent, no profile yet).
from focusguard.classroom_sim import SimulatedClassroomPipeline, default_people, synthetic_templates  # noqa: E402

for sid in (8, 9):
    sm.consent.grant(admin, sid, guardian_name="Phụ huynh (demo)", guardian_relationship="Mẹ",
                     scopes={"face_recognition": True})
for sid, person, n in ((3, "P1", 5), (4, "P2", 3), (10, "P3", 4)):
    for i, v in enumerate(synthetic_templates(person, n=n, seed=sid)):
        sm.repo.add_face_template({"student_id": sid, "embedding": v.tolist(), "source": "ADMIN_ENROLLMENT",
                                   "quality_score": 0.8, "pose_bucket": ["FRONT", "LEFT", "RIGHT"][i % 3]})
    sm.repo.mark_face_enrolled(sid)


def sim_factory(manager, class_id, class_session_id):
    return SimulatedClassroomPipeline(manager, class_id, class_session_id, default_people(), jpeg_encoder=pil_jpeg,
                                      noise=0.10)


sm.classroom_pipeline_factory = sim_factory
sm.consent.grant(admin, 3, guardian_name="Phụ huynh (demo)", guardian_relationship="Mẹ",
                 scopes={"face_recognition": True, "event_evidence": True})
sm.consent.grant(admin, 4, guardian_name="Phụ huynh (demo)", guardian_relationship="Bố",
                 scopes={"face_recognition": True})
# Stored-XSS probes: a display name and a guardian name that would run script if rendered unescaped.
sm.repo.update_display_name(11, '<img src=x onerror="window.__xss=1">Đỗ Minh Quân')
sm.consent.grant(admin, 10, guardian_name='<svg onload="window.__xss=2">Mẹ', guardian_relationship="Mẹ",
                 scopes={"face_recognition": True})

rt = sm.start_class(1, mode="classroom", started_by=2)
sm.set_activity_context(1, "LECTURE")
for i in range(150):                      # 30 s @ 5 fps: phone (3), head away (4), phone (4) later
    t = clock.advance(0.2)
    obs = {}
    for sid in (3, 4, 5):
        kw = dict(ear=0.30, yaw=0.0, pitch=0.0, phone_confidence=None)
        if sid == 3 and 10 <= i < 45:
            kw["phone_confidence"] = 0.9
        if sid == 4 and 60 <= i < 95:
            kw["yaw"] = 45.0
        if sid == 4 and 110 <= i < 140:
            kw["phone_confidence"] = 0.85
        obs[sid] = Observation(timestamp=t, visible=True, face_visible=True, **kw)
    rt.process_frame(t, obs)
    sm.evidence.push_frame(rt.class_session_id, t, grey_jpeg(60 + i), {3: (40, 30, 60, 60), 4: (90, 30, 60, 60)})
sm.end_class(1)
sm.clock = time.time                      # live clock for the walkthrough itself

app, sio = create_app(env={"FOCUSGUARD_ENV": "development", "SEED_DEMO_ACCOUNTS": "true",
                           "FLASK_SECRET_KEY": "w" * 40}, async_mode="threading",
                      start_background=False, session_manager=sm)
from flask import jsonify, request  # noqa: E402


@app.route("/__demo/sim", methods=["POST"])
def demo_sim_control():
    """Demo server only (never in the app): scenario controls for the simulated classroom."""
    hub = app.extensions["focusguard"]["cameras"]
    pipeline = hub.pipeline
    data = request.get_json(silent=True) or {}
    if not isinstance(pipeline, SimulatedClassroomPipeline):
        return jsonify({"status": "error", "message": "no simulated classroom running"}), 409
    if data.get("action") == "rebuild":
        # the camera pipeline goes away mid-class (reconnect / reload): a NEW tracker on the next view
        hub.force_release(lambda key: key[:1] == ("classroom",))
        return jsonify({"status": "success"})
    ok = pipeline.control(data.get("action"), data.get("person"), data.get("other"), float(data.get("weight", 0.6)))
    return jsonify({"status": "success" if ok else "error"}), (200 if ok else 400)


print("events:", len(sm.repo.get_focus_events(class_session_id=rt.class_session_id)),
      "evidence:", len(sm.repo.list_evidence()), flush=True)
sio.run(app, host="127.0.0.1", port=PORT, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
