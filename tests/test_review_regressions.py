"""Regression tests for issues found in the final PR review."""
import os
import re
import sys
import types

import numpy as np
import pytest

from focusguard import runtime_state as rs
from focusguard.behavior import BehaviorAnalyzer, Observation
from focusguard.evaluation.synthetic import noisy, random_gallery
from focusguard.identity import CONFIRMED, IdentityManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------ tracker: phone != tracking
def test_phone_detection_uses_a_separate_yolo_instance(monkeypatch):
    calls = []

    class FakeYOLO:
        instances = []

        def __init__(self, path):
            FakeYOLO.instances.append(self)

        def track(self, **kw):
            calls.append(("track", self, tuple(kw["classes"])))
            return []

        def predict(self, **kw):
            calls.append(("predict", self, tuple(kw["classes"])))
            return []

        def __call__(self, *a, **kw):  # the old code path; must not be used
            calls.append(("call", self, tuple(kw.get("classes", ()))))
            return []

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))
    sys.modules.pop("tracker", None)
    import tracker
    t = tracker.PersonTracker("yolov8n.pt")
    t.track(object())
    t.detect_phones(object())
    assert len(FakeYOLO.instances) == 2
    track_model = next(c[1] for c in calls if c[0] == "track")
    phone_model = next(c[1] for c in calls if c[0] == "predict")
    assert track_model is not phone_model
    assert ("track", track_model, (0,)) in calls and ("predict", phone_model, (67,)) in calls
    assert not any(c[0] == "call" for c in calls)
    sys.modules.pop("tracker", None)


def test_classroom_pipeline_uses_phone_detector_and_session_bound_runtime():
    src = open(os.path.join(ROOT, "classroom_ai.py"), encoding="utf-8").read()
    assert "self.tracker.detect_phones(frame)" in src
    assert "self.tracker.model(" not in src
    assert "classroom_runtime(self.class_id, self.class_session_id)" in src


# ----------------------------------------- runtime scoping for the camera
def test_classroom_camera_never_feeds_online_or_newer_session(manager):
    rt = manager.start_class(1, mode="classroom")
    assert manager.classroom_runtime(1, rt.class_session_id) is rt
    online = manager.start_class(1, mode="online")          # teacher switches to an online class
    assert manager.classroom_runtime(1) is None
    assert manager.classroom_runtime(1, rt.class_session_id) is None
    manager.end_class(1)
    rt2 = manager.start_class(1, mode="classroom")
    assert manager.classroom_runtime(1, rt.class_session_id) is None   # stale session id
    assert manager.classroom_runtime(1, rt2.class_session_id) is rt2
    manager.end_class(1)
    assert online.active is False


# ------------------------------------------------------------- CameraHub
class FakePipeline:
    def __init__(self, name):
        self.name = name
        self.released = False
        self.frames = 0

    def get_frame(self):
        self.frames += 1
        return (b"jpg-" + self.name.encode(), False, "OK")

    def release(self):
        self.released = True


def test_camera_hub_refcounts_viewers_and_stops_stale_generators():
    from app import CameraHub
    hub = CameraHub()
    a, gen_a = hub.acquire(("classroom", 1, 10), lambda: FakePipeline("a"))
    a2, gen_a2 = hub.acquire(("classroom", 1, 10), lambda: FakePipeline("unused"))
    assert a is a2 and gen_a == gen_a2 and hub.users == 2
    hub.release(gen_a)                       # one tab closes
    assert not a.released and hub.read(gen_a2)[0] == b"jpg-a"
    b, gen_b = hub.acquire(("personal", 3), lambda: FakePipeline("b"))
    assert a.released and gen_b != gen_a
    assert not hub.is_current(gen_a) and hub.read(gen_a) is None    # old generator stops, no reopen
    hub.release(gen_a2)                      # stale release must not close the new pipeline
    assert not b.released
    hub.force_release(lambda key: key == ("personal", 3))
    assert b.released and hub.pipeline is None


# ------------------------------------------------ identity: stranger on track
def test_unenrolled_face_on_locked_track_releases_identity():
    gallery = random_gallery(4, seed=11)
    stranger = random_gallery(1, seed=4242)[1]
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(0)
    t = 0.0
    for _ in range(20):                      # student 2 locks on track 5
        t += 0.1
        d = mgr.process_frame([(5, noisy(gallery[2], 0.03, rng))], t)
    assert d[5].status == CONFIRMED and d[5].student_id == 2
    for _ in range(70):                      # tracker id switch: a stranger stays on track 5 for 7 s
        t += 0.1
        d = mgr.process_frame([(5, noisy(stranger, 0.03, rng))], t)
    assert d[5].student_id is None
    assert mgr.track_for_student(2) is None


def _partially_occluded(vec, rng, keep=0.25):
    """Same person, face partly covered: similarity to the gallery drops to ~0.1-0.4."""
    other = rng.normal(size=vec.shape)
    other /= np.linalg.norm(other)
    mixed = keep * vec + (1 - keep) * other
    return mixed / np.linalg.norm(mixed)


def test_phone_in_front_of_face_does_not_release_identity():
    """Regression (real webcam): a phone held in front of the face dropped the
    similarity from ~0.87 to 0.13-0.44 and the identity was released after 1 s,
    discarding the phone evidence."""
    gallery = random_gallery(4, seed=21)
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(3)
    t = 0.0
    for _ in range(20):
        t += 0.1
        mgr.process_frame([(1, noisy(gallery[3], 0.03, rng))], t)
    sims = []
    for _ in range(150):                     # 15 s of phone use, phone associated to the track
        t += 0.1
        emb = _partially_occluded(gallery[3], rng)
        sims.append(float(mgr.match(emb).best_similarity))
        d = mgr.process_frame([(1, emb, True)], t)
        assert d[1].student_id == 3
    assert sorted(sims)[len(sims) // 2] < 0.4   # the scenario really is mostly a non-match


def test_brief_unflagged_occlusion_keeps_identity():
    gallery = random_gallery(4, seed=22)
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(4)
    t = 0.0
    for _ in range(20):
        t += 0.1
        mgr.process_frame([(1, noisy(gallery[2], 0.03, rng))], t)
    for _ in range(35):                      # hand over the face 3.5 s, no known occluder
        t += 0.1
        d = mgr.process_frame([(1, _partially_occluded(gallery[2], rng))], t)
    assert d[1].student_id == 2
    t += 0.1
    assert mgr.process_frame([(1, noisy(gallery[2], 0.03, rng))], t)[1].student_id == 2


def test_missing_face_does_not_release_locked_identity():
    gallery = random_gallery(3, seed=5)
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(1)
    t = 0.0
    for _ in range(20):
        t += 0.1
        mgr.process_frame([(1, noisy(gallery[1], 0.03, rng))], t)
    for _ in range(50):                      # face turned away / occluded, body still tracked
        t += 0.1
        d = mgr.process_frame([(1, None)], t)
    assert d[1].student_id == 1


# ------------------------------------------------- no face is not "focused"
def test_body_visible_without_face_is_unmeasured_not_focused():
    an = BehaviorAnalyzer(1)
    for i in range(30):
        u = an.update(Observation(timestamp=100 + i * 0.1, visible=True, face_visible=False))
    assert u.focus_state == rs.FOCUS_UNKNOWN and u.visibility_status == rs.VIS_VISIBLE


def test_no_face_freezes_score_in_runtime():
    from focusguard.session_runtime import SessionRuntime
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "A"}], started_at=0.0)
    t = 0.0
    for _ in range(20):
        t += 0.1
        rt.process_frame(t, {1: Observation(timestamp=t, visible=True, face_visible=True, ear=0.3, yaw=0, pitch=0)})
    rt.process_frame(t + 0.1, {1: Observation(timestamp=t + 0.1, visible=True, face_visible=False)})
    t += 0.1
    score = rt.states[1].focus_score
    for _ in range(50):
        t += 0.1
        rt.process_frame(t, {1: Observation(timestamp=t, visible=True, face_visible=False)})
    assert rt.states[1].focus_score == score          # frozen: neither recovery nor penalty
    assert rt.student_view(1)["focus_state"] == rs.FOCUS_UNKNOWN


# ------------------------------------------- online class relink + staleness
def test_student_in_self_study_is_linked_when_online_class_starts(manager, clock, repo):
    session_id = manager.start_personal_session(3)
    manager.route_observation(3, Observation(timestamp=clock.advance(0.2), visible=True, ear=0.3, yaw=0, pitch=0))
    rt = manager.start_class(1, mode="online")
    assert manager.runtime_for_student(3) is rt
    assert manager.registry.personal_runtime(3) is None
    for _ in range(10):
        manager.route_observation(3, Observation(timestamp=clock.advance(0.2), visible=True, ear=0.3, yaw=0, pitch=0))
    s = rt.student_view(3)
    assert s["attendance_status"] == rs.ATT_PRESENT and s["connection_status"] == rs.CONN_ONLINE
    manager.end_class(1)
    assert repo.get_session(session_id)["end_time"] is not None


def test_online_class_marks_only_the_stalled_student_no_signal(manager, clock):
    rt = manager.start_class(1, mode="online")
    ids = rt.enrolled_ids()[:2]
    for _ in range(10):
        t = clock.advance(0.2)
        for sid in ids:
            rt.ingest(sid, Observation(timestamp=t, visible=True, ear=0.3, yaw=0, pitch=0))
    for _ in range(30):                      # only the first student's camera keeps sending
        t = clock.advance(0.2)
        rt.ingest(ids[0], Observation(timestamp=t, visible=True, ear=0.3, yaw=0, pitch=0))
    rt.heartbeat(clock())
    assert rt.student_view(ids[0])["visibility_status"] == rs.VIS_VISIBLE
    assert rt.student_view(ids[1])["visibility_status"] == rs.VIS_NO_SIGNAL
    assert rt.class_view(clock())["statistics"]["visible"] == 1
    assert rt.camera_status == "ACTIVE"
    manager.end_class(1)


# ------------------------------------------ class analytics exclude self-study
def test_class_analytics_exclude_personal_sessions(manager, clock, repo):
    manager.start_personal_session(3)
    for i in range(60):
        manager.route_observation(3, Observation(timestamp=clock.advance(0.2), visible=True, ear=0.3, yaw=0, pitch=0,
                                                 phone_confidence=0.9 if i > 5 else None))
    manager.stop_personal_session(3)
    assert [e["type"] for e in repo.get_focus_events(student_id=3)] == ["PHONE"]
    data = manager.class_analytics(1)
    assert data["breakdown"]["total"] == 0 and data["breakdown"]["status"] == "insufficient_data"
    assert all(r.get("class_id") is None for r in repo.get_focus_events(student_id=3))


# ------------------------------------------------------------ frontend
UI_SCRIPTS = ("core.js", "teacher.js", "student.js", "admin.js")
UI_TEMPLATES = ("base.html", "login.html", "error.html", "teacher.html", "student.html", "parent.html", "admin.html")
USER_FIELDS = r"(display_name|username|class_name|name|subject|linked_student_name|message)"


def _ui_script(name):
    return open(os.path.join(ROOT, "static", "js", name), encoding="utf-8").read()


def test_ui_scripts_escape_user_controlled_values():
    """Anything a user can type (names, class names, subjects, messages) must go
    through esc() before it is concatenated into an HTML string."""
    import re
    field = re.compile(r"\b[\w.]+\." + USER_FIELDS + r"\b")
    safe_prefix = ("esc(", "FG.initials(")
    for name in UI_SCRIPTS:
        js = _ui_script(name)
        assert "${" not in js, f"{name}: template literals bypass the esc() convention"
        for lineno, line in enumerate(js.splitlines(), 1):
            if "'<" not in line and '"<' not in line:
                continue            # not building HTML on this line
            for m in field.finditer(line):
                before = line[:m.start()]
                assert before.endswith(safe_prefix), f"{name}:{lineno}: unescaped {m.group(0)}"


def test_teacher_snapshots_are_scoped_to_selected_class():
    js = _ui_script("teacher.js")
    assert 'emit("request_class_snapshot", { class_id: S.classId })' in js
    assert 'emit("join_teacher_room", { class_id: S.classId })' in js
    assert "data.class_id !== S.classId" in js          # snapshots of another class are dropped


def test_ui_has_no_hardcoded_numbers_or_fake_data():
    import re
    for name in UI_TEMPLATES:
        html = open(os.path.join(ROOT, "templates", name), encoding="utf-8").read()
        for fake in ("28/28", "27/28", "0/28", ">84%<", ">88%<", ">92%<", ">95%<", "20/05/2026"):
            assert fake not in html, (name, fake)
    for name in UI_SCRIPTS:
        js = _ui_script(name)
        assert not re.search(r"Math\.random\(", js), name


def test_ui_never_decides_role_or_ai_state_on_the_client():
    """The browser may not push AI state, and no page reads the role from the DOM."""
    for name in UI_SCRIPTS:
        js = _ui_script(name)
        for forbidden in ("student_data_push", "student_frame_push", "localStorage.getItem(\"role\")",
                          "firebase", "firestore"):
            assert forbidden not in js, (name, forbidden)


def test_teacher_display_name_xss_payload_is_stored_but_rendered_escaped(login_as, app):
    payload = "<img src=x onerror=alert(1)>"
    t = login_as("teacher")
    assert t.post("/api/teacher/settings", json={"display_name": payload}).status_code == 200
    page = t.get("/teacher/dashboard").get_data(as_text=True)
    assert payload not in page and "&lt;img src=x onerror=alert(1)&gt;" in page


def test_reset_face_rejects_non_numeric_user_id(login_as):
    admin = login_as("admin")
    assert admin.post("/api/admin/reset_face", json={"user_id": "abc"}).status_code == 400


def test_duplicate_tracker_ids_for_one_person_do_not_flicker_identity():
    """Regression (real webcam): ByteTrack kept ids 1 and 2 for ONE person and
    reported one or the other per frame; id 2 carried the face but stayed in
    CONFLICT, so the student flickered to 'not visible' ~20% of the time."""
    gallery = random_gallery(3, seed=31)
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(9)
    t = 0.0
    for _ in range(15):                                   # id 1 confirms first
        t += 0.13
        mgr.process_frame([(1, noisy(gallery[3], 0.03, rng))], t)
    seen_missing = 0
    pattern = [[1], [1, 2], [2], [2], [1], [1, 2], [2]]
    for i in range(200):                                  # ~26 s at 7.5 FPS
        t += 0.13
        ids = pattern[i % len(pattern)]
        face_on = ids[-1]                                  # the face is associated to one box only
        obs = [(tid, noisy(gallery[3], 0.03, rng) if tid == face_on else None) for tid in ids]
        d = mgr.process_frame(obs, t)
        owners = [tid for tid, x in d.items() if x.student_id == 3]
        assert len(owners) <= 1                            # never two tracks for one student
        if i > 20 and not owners:
            seen_missing += 1
    assert seen_missing == 0


def test_phone_signal_survives_low_confidence_frames_and_dropouts():
    """Regression (real webcam): YOLOv8n scored a phone in use 0.25-0.62 with
    dropped frames; the old 0.35/0.5 s rules produced only a 2.4 s episode
    over ~8.5 s of phone use."""
    confs = [0.318, 0.525, 0.403, 0.296, None, 0.506, 0.599, 0.407, None, None, 0.287, None, None, 0.329, 0.331,
             None, 0.357, 0.253, 0.438, 0.365, 0.443, 0.415, None, None, None, 0.509, 0.259, 0.263, None, None,
             0.283, 0.396, None, 0.28, 0.323, None, None, None, 0.281, 0.262, 0.287, None, None, 0.313, 0.483,
             0.27, 0.572, 0.578, 0.389, 0.567, 0.594, 0.531, 0.443, 0.379, 0.587, 0.469, 0.35, 0.463, 0.524,
             0.476, 0.431, 0.618, 0.282]
    an = BehaviorAnalyzer(1)
    t, ended, started = 100.0, [], []
    for i in range(20):                                   # warm-up, normal face
        t += 0.135
        an.update(Observation(timestamp=t, visible=True, face_visible=True, ear=0.26, yaw=0, pitch=0))
    t_phone = t
    for c in confs:
        t += 0.135
        u = an.update(Observation(timestamp=t, visible=True, face_visible=True, ear=0.09, yaw=0, pitch=0,
                                  phone_confidence=c))
        started += u.started
        ended += u.ended
    ended += an.close(t)
    phone = [e for e in ended if e.type == "PHONE"]
    assert len(phone) == 1 and phone[0].duration > 7.0
    assert not [e for e in started if e.type == "DROWSY"]   # looking down at the phone is not drowsiness


def test_single_phone_flash_still_ignored_with_new_tolerances():
    an = BehaviorAnalyzer(1)
    started = []
    for i in range(60):
        t = 100 + i * 0.133
        started += an.update(Observation(timestamp=t, visible=True, face_visible=True, ear=0.26, yaw=0, pitch=0,
                                         phone_confidence=0.9 if 3.0 <= i * 0.133 < 3.2 else None)).started
    assert started == []
