"""Security contracts of the BTC-feedback features."""
import os
import re
import sys

import pytest

from conftest import run_class_with_events

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

NEW_GET = ["/api/teacher/events", "/api/teacher/events/1", "/api/teacher/review_queue", "/api/teacher/review_metrics",
           "/api/teacher/evidence/" + "a" * 32, "/api/teacher/evaluations", "/api/admin/privacy/students",
           "/api/admin/consent/3", "/api/admin/audit", "/api/parent/privacy", "/api/export/student/3",
           "/api/feedback/vocabulary", "/api/privacy/policy"]
NEW_POST = ["/api/teacher/events/1/review", "/api/teacher/activity_context", "/api/evidence/" + "a" * 32 + "/delete",
            "/api/teacher/evaluations", "/api/admin/consent/3", "/api/consent/3/revoke",
            "/api/admin/privacy/3/delete_face", "/api/admin/privacy/3/delete_evidence", "/api/admin/retention/run"]
NEW_PAGES = ["/teacher/events", "/teacher/evaluation", "/admin/privacy"]


def test_new_endpoints_require_login(app):
    anon = app.test_client()
    for url in NEW_GET:
        assert anon.get(url).status_code == 401, url
    for url in NEW_POST:
        assert anon.post(url, json={}).status_code == 401, url
    for url in NEW_PAGES:
        r = anon.get(url)
        assert r.status_code == 302 and "/login" in r.headers["Location"], url


def test_students_cannot_reach_teacher_or_admin_privacy_features(login_as):
    s = login_as("hocsinh")
    for url in NEW_GET[:-2]:
        assert s.get(url).status_code in (403, 404), url
    for url in NEW_POST:
        assert s.post(url, json={}).status_code in (403, 404), url


def test_student_cannot_set_activity_context(login_as, manager):
    manager.start_class(1, mode="classroom")
    assert login_as("hocsinh").post("/api/teacher/activity_context", json={"context": "EXAM"}).status_code == 403
    assert manager.class_runtime(1).activity_context is None
    t = login_as("teacher")
    assert t.post("/api/teacher/activity_context", json={"context": "../../etc"}).status_code == 400
    assert t.post("/api/teacher/activity_context", json={"context": "WRITING", "class_id": 3}).status_code == 403


def test_activity_context_is_audited(login_as, manager):
    manager.start_class(1, mode="classroom")
    assert login_as("teacher").post("/api/teacher/activity_context", json={"context": "EXAM"}).status_code == 200
    assert manager.audit.list(action="ACTIVITY_CONTEXT_CHANGED")[0]["metadata"]["context"] == "EXAM"


def test_threshold_change_is_audited_as_policy_change(login_as, manager):
    assert login_as("admin").post("/api/settings", json={"ear_threshold": 0.2}).status_code == 200
    row = manager.audit.list(action="POLICY_CHANGED")[0]
    assert row["metadata"]["after"]["ear_threshold"] == 0.2


def test_browser_code_never_handles_face_vectors():
    for name in os.listdir(os.path.join(ROOT, "static", "js")):
        if name.endswith(".js") and not name.endswith(".min.js") and name not in ("chart.js",):
            text = open(os.path.join(ROOT, "static", "js", name), encoding="utf-8").read().lower()
            # only the boolean "exists" flag and the button wording may mention it
            text = text.replace("face_embedding_exists", "").replace("face embedding", "")
            assert "embedding" not in text, name


def test_scanner_flags_embeddings_in_logs():
    import security_check
    rx = next(rx for rx, label in security_check.FAKE_DATA_RULES if "embedding" in label)
    assert rx.search('print("emb", face.embedding)') and rx.search("logger.info(embedding)")
    assert not rx.search('print("face_embedding_exists", x)')


def test_evidence_store_location_is_outside_static_and_ignored():
    from focusguard.privacy.settings import PrivacySettings
    path = os.path.realpath(PrivacySettings().evidence_dir)
    assert os.path.commonpath([path, os.path.realpath(os.path.join(ROOT, "static"))]) != os.path.realpath(
        os.path.join(ROOT, "static"))
    import security_check
    joined = "\n".join(security_check.check(files=["instance/evidence/" + "a" * 32 + ".fgev", "x.fgev"]))
    assert "instance/evidence" in joined and "x.fgev" in joined


def test_event_review_cannot_target_personal_sessions(login_as, manager, repo):
    eid = repo.create_focus_event({"type": "PHONE", "student_id": 3, "session_id": 1, "class_id": None,
                                   "start_time": 1.0, "end_time": 5.0, "duration_seconds": 4.0})
    assert login_as("teacher").post(f"/api/teacher/events/{eid}/review", json={"verdict": "CONFIRMED"}).status_code == 403


def test_review_note_is_escaped_in_ui_code():
    js = open(os.path.join(ROOT, "static", "js", "feedback.js"), encoding="utf-8").read()
    assert "esc(h.teacher_note)" in js and "esc(r.name)" in js
    # no inline event handlers / eval in new UI code
    for name in ("feedback.js", "privacy.js", "parent.js"):
        text = open(os.path.join(ROOT, "static", "js", name), encoding="utf-8").read()
        assert not re.search(r"\beval\(|new Function\(|onclick=", text), name
        assert "window.alert(" not in text and "alert(" not in text.replace("role=\"alert\"", ""), name


def test_teacher_cannot_read_other_class_events(login_as, manager, clock, repo):
    from conftest import add_teacher
    _, events = run_class_with_events(manager, clock)
    add_teacher(repo)
    other = login_as("teacher2")
    assert other.get("/api/teacher/events?class_id=1").status_code == 403
    assert other.get(f"/api/teacher/events/{events[0]['id']}").status_code == 403
    assert other.get("/api/teacher/review_queue?class_id=1").status_code == 403
