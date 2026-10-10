"""Ownership-aware authorization: login alone is never enough."""
import pytest

from focusguard import authz


@pytest.fixture
def world(app, repo):
    """class 1: teacher(2), hocsinh(3) ... ; class 2: teacher2, student2; parent(100)->3."""
    repo.create_user("teacher2", "pw-teacher2", "Cô Lan", "teacher", 2)
    repo.create_user("student2", "pw-student2", "Học sinh lớp 2", "student", 2)
    ids = {u["username"]: u["id"] for u in repo.get_users_list()}
    s3 = repo.create_session(3, "2026-01-01 08:00:00", subject="Toán")
    repo.update_session(s3, "2026-01-01 08:30:00", 1800, 80, 1, avg_focus_score=82.0)
    s_other = repo.create_session(ids["student2"], "2026-01-01 09:00:00", subject="Văn")
    repo.update_session(s_other, "2026-01-01 09:30:00", 1800, 60, 3, avg_focus_score=61.0)
    return {"ids": ids, "session_own": s3, "session_other": s_other}


def test_unauthenticated_api_is_rejected(app):
    c = app.test_client()
    for path in ("/api/stats", "/api/sessions", "/api/latest_session", "/api/teacher/class_status",
                 "/api/admin/users", "/api/student/profile", "/video_feed", "/api/session/1/breakdown"):
        assert c.get(path).status_code == 401, path


def test_student_sees_only_own_sessions(login_as, world):
    c = login_as("hocsinh")
    sessions = c.get("/api/sessions").get_json()
    assert {s["user_id"] for s in sessions} == {3}
    # cannot ask for somebody else's data by parameter
    other = c.get(f"/api/sessions?student_id={world['ids']['student2']}").get_json()
    assert {s["user_id"] for s in other} == {3}
    assert c.get(f"/api/session/{world['session_other']}/breakdown").status_code == 403
    assert c.get(f"/api/session/{world['session_own']}/breakdown").status_code == 200
    assert c.get("/api/latest_session").get_json()["id"] == world["session_own"]


def test_student_cannot_use_teacher_or_admin_endpoints(login_as, world):
    c = login_as("hocsinh")
    for path in ("/api/teacher/class_status?class_id=1", "/api/teacher/analytics_summary",
                 "/api/teacher/logs", "/api/leaderboard", "/api/admin/users"):
        assert c.get(path).status_code == 403, path
    assert c.post("/api/teacher/start_offline_class", json={"class_id": 1}).status_code == 403
    assert c.get("/teacher/dashboard").status_code == 302


def test_students_and_parents_do_not_receive_class_wide_numbers(login_as, world):
    for user in ("hocsinh", "phuhuynh"):
        c = login_as(user)
        assert c.get("/api/leaderboard").status_code == 403
        assert c.get("/api/teacher/class_summary?class_id=1").status_code == 403


def test_teacher_limited_to_own_class(login_as, world, manager):
    t1 = login_as("teacher")
    assert t1.get("/api/teacher/class_status?class_id=1").status_code == 200
    assert t1.get("/api/teacher/class_status?class_id=2").status_code == 403
    assert t1.post("/api/teacher/start_offline_class", json={"class_id": 2}).status_code == 403
    assert manager.class_runtime(2) is None
    # student data of another class
    sid2 = world["ids"]["student2"]
    assert t1.get(f"/api/sessions?student_id={sid2}").status_code == 403
    assert t1.get(f"/api/session/{world['session_other']}/breakdown").status_code == 403
    # own class student is allowed
    assert t1.get("/api/sessions?student_id=3").status_code == 200
    t2 = login_as("teacher2", "pw-teacher2")
    assert t2.get("/api/teacher/class_status?class_id=2").status_code == 200
    assert t2.get("/api/teacher/class_status?class_id=1").status_code == 403
    assert t2.get("/api/sessions?student_id=3").status_code == 403


def test_teacher_class_status_contains_only_that_class(login_as, world):
    t1 = login_as("teacher")
    data = t1.get("/api/teacher/class_status?class_id=1").get_json()
    assert data["students"] and all(s["class_id"] == 1 for s in data["students"])
    assert world["ids"]["student2"] not in {s["student_id"] for s in data["students"]}


def test_parent_limited_to_linked_student(login_as, world):
    p = login_as("phuhuynh")
    own = p.get("/api/sessions").get_json()
    assert {s["user_id"] for s in own} == {3}
    other = p.get(f"/api/sessions?student_id={world['ids']['student2']}").get_json()
    assert {s["user_id"] for s in other} == {3}
    assert p.get(f"/api/session/{world['session_other']}/breakdown").status_code == 403
    assert p.get("/parent/dashboard").status_code == 200
    assert p.get("/api/stats").status_code == 403


def test_admin_scope_is_explicit(login_as, world):
    a = login_as("admin")
    assert a.get("/api/admin/users").status_code == 200
    assert a.get("/api/sessions?student_id=3").status_code == 200
    s = login_as("hocsinh")
    assert s.post("/api/admin/create_user", json={"username": "x", "password": "y", "display_name": "x",
                                                  "role": "admin"}).status_code == 403


def test_video_feed_requires_ownership(login_as, world, manager):
    s = login_as("hocsinh")
    assert s.get("/video_feed").status_code == 409          # no session running -> no camera
    t2 = login_as("teacher2", "pw-teacher2")
    assert t2.get("/video_feed?mode=classroom&class_id=1").status_code == 403
    p = login_as("phuhuynh")
    assert p.get("/video_feed").status_code == 403


def test_policy_functions_directly():
    teacher = authz.Principal(2, "teacher", frozenset({1}))
    student = authz.Principal(3, "student", frozenset({1}))
    parent = authz.Principal(100, "parent", frozenset(), linked_student_id=3)
    admin = authz.Principal(1, "admin")
    assert authz.can_manage_class(teacher, 1) and not authz.can_manage_class(teacher, 2)
    assert not authz.can_manage_class(student, 1)
    assert authz.can_view_student(student, 3) and not authz.can_view_student(student, 4, 1)
    assert authz.can_view_student(parent, 3) and not authz.can_view_student(parent, 4)
    assert authz.can_view_student(teacher, 9, 1) and not authz.can_view_student(teacher, 9, 2)
    assert authz.can_view_student(admin, 42, 7)
    assert not authz.can_view_student(None, 3)


def test_stale_session_role_is_not_trusted(app, repo, login_as):
    c = login_as("hocsinh")
    with c.session_transaction() as sess:
        sess["role"] = "teacher"   # forged role in a (hypothetically) tampered cookie
    assert c.get("/api/teacher/class_status?class_id=1").status_code == 401
