import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("DATABASE_TYPE", "sqlite")
# Evaluation logs and evidence clips written by tests never land in the repo.
import tempfile  # noqa: E402
_TMP = tempfile.mkdtemp(prefix="focusguard-tests-")
os.environ.setdefault("EVALUATION_PRIVATE_DIR", os.path.join(_TMP, "evaluation-private"))
os.environ.setdefault("EVENT_EVIDENCE_DIR", os.path.join(_TMP, "evidence"))
# Development camera credential key file of the test run (never the repository's .focusguard/).
os.environ.setdefault("FG_CAMERA_KEY_FILE", os.path.join(_TMP, "camera_credential.key"))
# Deterministic camera-network policy for the test run (independent of this machine's interfaces):
# the synthetic cameras of the tests live in 192.168.0.0/16. Policy tests build their own NetGuard.
os.environ.setdefault("FG_CAMERA_ALLOWED_NETWORKS", "192.168.0.0/16")

from repository import SQLiteRepository  # noqa: E402
from session_manager import SessionManager  # noqa: E402

DEV_ENV = {"FOCUSGUARD_ENV": "development", "SEED_DEMO_ACCOUNTS": "true", "FLASK_SECRET_KEY": "x" * 40}


class FakeClock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds
        return self.t


@pytest.fixture
def repo():
    r = SQLiteRepository(":memory:")
    return r


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def manager(repo, clock):
    return SessionManager(repo=repo, seed_demo_accounts=True, clock=clock)


@pytest.fixture
def app_bundle(manager):
    from app import create_app
    app, socketio = create_app(env=DEV_ENV, async_mode="threading", start_background=False,
                               session_manager=manager)
    app.config["TESTING"] = True
    return app, socketio, manager


@pytest.fixture
def app(app_bundle):
    return app_bundle[0]


def login(app, username, password="123"):
    client = app.test_client()
    resp = client.post("/login", data={"username": username, "password": password})
    assert resp.status_code == 302, f"login failed for {username}"
    return client


@pytest.fixture
def login_as(app):
    def _login(username, password="123"):
        return login(app, username, password)
    return _login


def run_class_with_events(manager, clock, *, class_id=1, student_id=3, phone_seconds=6.0, head_seconds=5.0,
                          context=None, fps=5.0, end=True):
    """Start a classroom session, feed one student a PHONE then a HEAD_AWAY
    episode through the real SessionRuntime, and (optionally) end the class.
    Returns (runtime, events_of_that_class_session)."""
    from focusguard.behavior import Observation
    rt = manager.start_class(class_id, mode="classroom", started_by=2)
    if context:
        manager.set_activity_context(class_id, context)

    def feed(seconds, **kw):
        for _ in range(int(seconds * fps)):
            t = clock.advance(1.0 / fps)
            base = dict(ear=0.30, yaw=0.0, pitch=0.0, phone_confidence=None)
            base.update(kw)
            rt.process_frame(t, {student_id: Observation(timestamp=t, visible=True, face_visible=True, **base)})
    feed(3)
    feed(phone_seconds, phone_confidence=0.8)
    feed(3)
    feed(head_seconds, yaw=45.0)
    feed(3)
    if end:
        manager.end_class(class_id)
    return rt, manager.repo.get_focus_events(class_session_id=rt.class_session_id)


def add_teacher(repo, username="teacher2", class_id=2):
    ok, _ = repo.create_user(username, "123", "Cô Lan", "teacher", class_id)
    assert ok
    return repo.get_user_by_username(username)["id"]


def grant_consent(manager, student_id, **scopes):
    from focusguard.authz import Principal
    scopes = scopes or {"face_recognition": True}
    return manager.consent.grant(Principal(1, "admin"), student_id, guardian_name="Phụ huynh",
                                 guardian_relationship="Mẹ", scopes=scopes)
