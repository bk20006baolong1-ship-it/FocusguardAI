"""Firestore backend contract tests against the LOCAL EMULATOR only.

Run:
    npx firebase emulators:start --only firestore --project demo-focusguard      # terminal 1
    FOCUSGUARD_FIRESTORE_TESTS=1 FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \\
        GOOGLE_CLOUD_PROJECT=demo-focusguard python -m pytest tests/firestore -rs -v

Safety: refuses to run unless FIRESTORE_EMULATOR_HOST is set and the project id
starts with "demo-" (demo projects can only ever talk to the emulator).
Skipped with EMULATOR_REQUIRED otherwise.
"""
import os
import time
import uuid

import pytest

EMULATOR = os.environ.get("FIRESTORE_EMULATOR_HOST")
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")

pytestmark = pytest.mark.skipif(
    os.environ.get("FOCUSGUARD_FIRESTORE_TESTS") != "1" or not EMULATOR or not PROJECT.startswith("demo-"),
    reason="EMULATOR_REQUIRED")


@pytest.fixture
def repo():
    import json
    import urllib.request
    os.environ["USE_EMULATOR"] = "true"
    # Fresh database for every test (emulator REST endpoint).
    req = urllib.request.Request(
        f"http://{EMULATOR}/emulator/v1/projects/{PROJECT}/databases/(default)/documents", method="DELETE")
    urllib.request.urlopen(req, timeout=10).read()
    from repository import FirestoreRepository
    r = FirestoreRepository()
    assert r.db.project == PROJECT
    return r


def test_init_seeds_demo_accounts_with_hashed_passwords(repo):
    from focusguard.security import is_hashed
    repo.init_db(seed_demo_accounts=True, demo_password="pw-demo")
    users = repo.get_users_list()
    assert {u["username"] for u in users} >= {"admin", "teacher", "hocsinh", "phuhuynh"}
    assert all("password" not in u for u in users)
    assert is_hashed(repo.get_password_hash(3))
    assert repo.authenticate_user("hocsinh", "pw-demo")[0] == 3
    assert repo.authenticate_user("hocsinh", "wrong") is None


def test_plaintext_password_migration(repo):
    from focusguard.security import is_hashed
    repo.db.collection("users").document("77").set(
        {"id": 77, "username": "legacy", "password": "oldpw", "display_name": "L", "role": "student", "class_id": 1})
    assert repo.migrate_plaintext_passwords() == 1
    assert is_hashed(repo.get_password_hash(77))
    assert repo.authenticate_user("legacy", "oldpw")[0] == 77
    repo.db.collection("users").document("78").set(
        {"id": 78, "username": "legacy2", "password": "pw2", "display_name": "L2", "role": "student"})
    assert repo.authenticate_user("legacy2", "pw2") is not None
    assert is_hashed(repo.get_password_hash(78))


def test_users_classes_and_membership(repo):
    repo.init_db(seed_demo_accounts=True, demo_password="pw")
    ok, _ = repo.create_class("11B1")
    assert ok and not repo.create_class("11B1")[0]
    cid = next(c["id"] for c in repo.get_classes_list() if c["class_name"] == "11B1")
    assert repo.create_user("gv2", "pw2", "Cô B", "teacher", cid)[0]
    assert repo.create_user("hs2", "pw3", "HS B", "student", cid)[0]
    assert not repo.create_user("hs2", "x", "dup", "student", cid)[0]
    assert not repo.create_user("bad", "x", "x", "superuser", cid)[0]
    tid = repo.get_user_by_username("gv2")["id"]
    assert repo.get_teacher_class_ids(tid) == [cid]
    assert repo.get_teacher_class_ids(2) == [1]
    assert [s["display_name"] for s in repo.get_students_in_class(cid)] == ["HS B"]
    # moving the teacher revokes the old class membership
    assert repo.update_user(tid, "gv2", None, "Cô B", "teacher", 1)[0]
    assert repo.get_teacher_class_ids(tid) == [1]
    assert repo.get_student_id_for_parent(100) == 3
    repo.update_display_name(tid, "Cô Bình")
    assert repo.get_user_by_id(tid)["display_name"] == "Cô Bình"
    repo.delete_class(cid)
    assert repo.get_user_by_username("hs2")["class_id"] is None


def test_face_embeddings_only_for_students(repo):
    repo.init_db(seed_demo_accounts=True, demo_password="pw")
    assert repo.save_face_embedding(3, "[0.1, 0.2]")
    assert not repo.save_face_embedding(2, "[0.1, 0.2]")      # teacher
    rows = repo.get_student_embeddings_by_class(1)
    assert [r["student_id"] for r in rows] == [3] and rows[0]["embedding"] == [0.1, 0.2]


def test_sessions_events_snapshots_and_session_students(repo):
    repo.init_db(seed_demo_accounts=True, demo_password="pw")
    sid = repo.create_session(3, "2026-01-01 08:00:00", subject="Toán")
    repo.update_session(sid, "2026-01-01 08:30:00", 1800, 80, 2, avg_focus_score=81.5)
    assert repo.get_session(sid)["avg_focus_score"] == 81.5
    assert [s["id"] for s in repo.get_sessions_for_user(3)] == [sid]
    cs = repo.create_class_session(1, "2026-01-01 09:00:00", mode="classroom", started_by=2)
    repo.end_class_session(cs, "2026-01-01 09:45:00")
    assert repo.get_class_session(cs)["mode"] == "classroom" and repo.get_class_session(cs)["ended_at"]
    assert repo.get_class_sessions(1)[0]["id"] == cs
    repo.create_focus_event({"student_id": 3, "class_session_id": cs, "class_id": 1, "type": "PHONE",
                             "start_time": time.time(), "end_time": time.time() + 12, "duration_seconds": 12.0,
                             "confidence": 0.7, "source": "classroom_camera", "metadata": {"frames_on": 90}})
    ev = repo.get_focus_events(class_session_id=cs)
    assert len(ev) == 1 and ev[0]["duration_seconds"] == 12.0 and ev[0]["metadata"]["frames_on"] == 90
    repo.create_focus_snapshot({"student_id": 3, "class_session_id": cs, "class_id": 1, "timestamp": time.time(),
                                "focus_score": 90.0, "focus_state": "FOCUSED"})
    assert len(repo.get_focus_snapshots(class_id=1)) == 1
    repo.save_session_student({"class_session_id": cs, "class_id": 1, "student_id": 3, "attendance_status": "PRESENT",
                               "avg_focus_score": 88.0, "event_counts": {"PHONE": 1}})
    rows = repo.get_session_students(class_session_id=cs)
    assert rows[0]["event_counts"] == {"PHONE": 1} and repo.get_session_students(student_id=3)


def test_full_class_lifecycle_on_firestore(repo):
    from focusguard.behavior import Observation
    from session_manager import SessionManager
    t = [1_800_000_000.0]
    sm = SessionManager(repo=repo, seed_demo_accounts=True, clock=lambda: t[0])
    rt = sm.start_class(1, mode="classroom", started_by=2)
    for i in range(80):
        t[0] += 0.25
        rt.process_frame(t[0], {3: Observation(timestamp=t[0], visible=True, ear=0.3, yaw=0, pitch=0,
                                               phone_confidence=0.9 if 20 <= i < 60 else None)})
    result = sm.end_class(1)
    rows = repo.get_session_students(class_session_id=result["class_session_id"])
    assert len(rows) == len(rt.states)
    assert next(r for r in rows if r["student_id"] == 3)["attendance_status"] == "PRESENT"
    assert [e["type"] for e in repo.get_focus_events(class_session_id=result["class_session_id"])] == ["PHONE"]
    analytics = sm.class_analytics(1)
    assert analytics["breakdown"]["counts"]["PHONE"] == 1
    assert sm.class_session_summary(result["class_session_id"], 1)["present"] == 1
