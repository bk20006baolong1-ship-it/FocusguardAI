"""UI/UX hardening regressions: every page renders for its role, auth redirects
are honest, session start/stop is idempotent, errors are designed, and the
state the UI shows is produced by the server."""
import os
import re

import pytest

from app import CameraHub
from focusguard import camera_state

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "templates")

PAGES = {
    "hocsinh": ["/", "/stats", "/reports", "/settings"],
    "teacher": ["/teacher/dashboard", "/teacher/students", "/teacher/attendance", "/teacher/alerts",
                "/teacher/analytics", "/teacher/reports", "/teacher/settings"],
    "phuhuynh": ["/parent/dashboard"],
    "admin": ["/admin/accounts", "/admin/classes"],
}


# ---------------------------------------------------------------- pages / nav
@pytest.mark.parametrize("username", sorted(PAGES))
def test_every_page_of_a_role_renders(login_as, username):
    client = login_as(username)
    for path in PAGES[username]:
        resp = client.get(path)
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200, (username, path)
        assert 'id="fgApp"' in html and "css/app.css" in html, path
        assert html.count('aria-current="page"') == 1, f"{path}: exactly one active nav item"


def test_navigation_shows_only_the_roles_own_links(login_as):
    html = login_as("hocsinh").get("/").get_data(as_text=True)
    assert "/teacher/" not in html and "/admin/" not in html
    html = login_as("teacher").get("/teacher/dashboard").get_data(as_text=True)
    assert "/admin/" not in html
    html = login_as("phuhuynh").get("/parent/dashboard").get_data(as_text=True)
    assert "/teacher/" not in html and "/admin/" not in html


def test_templates_reference_only_existing_routes_and_static_files(app):
    endpoints = {r.endpoint for r in app.url_map.iter_rules()}
    for name in os.listdir(TEMPLATES):
        src = open(os.path.join(TEMPLATES, name), encoding="utf-8").read()
        for endpoint in re.findall(r"url_for\('([a-z_]+)'\)", src):
            assert endpoint in endpoints, (name, endpoint)
        for endpoint in re.findall(r"\('([a-z_]+)', '[a-z]+', 'fa-", src):       # nav table in base.html
            assert endpoint in endpoints, (name, endpoint)
        for static in re.findall(r"filename='([^']+)'", src):
            assert os.path.exists(os.path.join(ROOT, "static", static)), (name, static)


def test_wrong_role_is_sent_home_not_shown_the_page(login_as):
    for username, path in (("hocsinh", "/teacher/dashboard"), ("hocsinh", "/admin/accounts"),
                           ("teacher", "/admin/classes"), ("phuhuynh", "/stats"), ("teacher", "/parent/dashboard")):
        resp = login_as(username).get(path)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/"), (username, path)


def test_anonymous_pages_redirect_to_login_and_legacy_urls_still_work(app, login_as):
    anon = app.test_client()
    for path in ("/", "/stats", "/teacher/dashboard", "/admin/accounts", "/parent/dashboard"):
        resp = anon.get(path)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], path
    student = login_as("hocsinh")
    assert student.get("/mobile").status_code == 302          # phone mock-up replaced by responsive pages
    assert student.get("/alerts").status_code == 302


def test_expired_session_explains_itself(app, repo, login_as):
    client = login_as("hocsinh")
    repo.delete_user(3)                                        # the account behind the cookie is gone
    resp = client.get("/stats")
    assert resp.status_code == 302 and "expired=1" in resp.headers["Location"]
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Phiên đăng nhập đã hết hạn" in page


# ---------------------------------------------------------------------- login
def test_login_failure_is_generic_and_does_not_leak_account_existence(app):
    client = app.test_client()
    wrong_password = client.post("/login", data={"username": "teacher", "password": "nope"})
    unknown_user = client.post("/login", data={"username": "khong-co-ai", "password": "nope"})
    assert wrong_password.status_code == unknown_user.status_code == 401
    message = "Tên đăng nhập hoặc mật khẩu không đúng."
    for resp, username in ((wrong_password, "teacher"), (unknown_user, "khong-co-ai")):
        html = resp.get_data(as_text=True)
        assert message in html
        assert f'value="{username}"' in html                   # the typed username is kept
        assert "nope" not in html                              # the password is never echoed


def test_login_page_never_prints_passwords(app):
    html = app.test_client().get("/login").get_data(as_text=True)
    assert 'data-demo-user="teacher"' in html                  # dev-only convenience: usernames only
    assert not re.search(r"(mật khẩu|password)\s*[:=]\s*\S*123", html, re.I)
    assert ">123<" not in html


def test_demo_accounts_are_hidden_outside_development(repo):
    from app import create_app
    from session_manager import SessionManager
    env = {"FOCUSGUARD_ENV": "production", "FLASK_SECRET_KEY": "s" * 48, "SEED_DEMO_ACCOUNTS": "false",
           "CORS_ALLOWED_ORIGINS": "https://focusguard.example"}
    manager = SessionManager(repo=repo, seed_demo_accounts=False)
    app, _ = create_app(env=env, async_mode="threading", start_background=False, session_manager=manager)
    html = app.test_client().get("/login").get_data(as_text=True)
    assert 'data-demo-user="' not in html and "Tài khoản thử nghiệm" not in html


# ---------------------------------------------------------------- error pages
def test_error_pages_are_designed_for_people_and_json_for_apis(app, login_as):
    client = login_as("teacher")
    page = client.get("/khong-ton-tai")
    assert page.status_code == 404 and "Không tìm thấy trang" in page.get_data(as_text=True)
    assert "Traceback" not in page.get_data(as_text=True)
    api = client.get("/api/khong-ton-tai")
    assert api.status_code == 404 and api.get_json()["status"] == "error"
    assert client.get("/api/teacher/start_class").status_code == 405
    assert client.get("/api/teacher/start_class").get_json()["status"] == "error"


def test_unhandled_errors_do_not_leak_internals(app, login_as, monkeypatch):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    manager = app.extensions["focusguard"]["session_manager"]

    def boom(*_a, **_k):
        raise RuntimeError("secret-db-path /var/lib/focusguard.db")
    monkeypatch.setattr(manager, "class_session_history", boom)
    resp = login_as("teacher").get("/api/teacher/class_sessions")
    assert resp.status_code == 500
    assert "secret-db-path" not in resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "error"


# ------------------------------------------------------- class session flow
def test_start_class_twice_keeps_one_session(login_as, repo):
    t = login_as("teacher")
    first = t.post("/api/teacher/start_class", json={"class_id": 1}).get_json()
    second = t.post("/api/teacher/start_class", json={"class_id": 1}).get_json()
    assert first["already_active"] is False and second["already_active"] is True
    assert first["class_session_id"] == second["class_session_id"]
    assert len(repo.get_class_sessions(1)) == 1


def test_starting_another_mode_while_active_is_refused(login_as, repo):
    t = login_as("teacher")
    assert t.post("/api/teacher/start_class", json={"class_id": 1}).status_code == 200
    resp = t.post("/api/teacher/start_offline_class", json={"class_id": 1})
    assert resp.status_code == 409 and resp.get_json()["status"] == "error"
    assert len(repo.get_class_sessions(1)) == 1
    assert t.get("/api/teacher/class_status").get_json()["mode"] == "online"


def test_end_class_twice_is_harmless(login_as):
    t = login_as("teacher")
    t.post("/api/teacher/start_class", json={"class_id": 1})
    first = t.post("/api/teacher/end_class", json={"class_id": 1}).get_json()
    second = t.post("/api/teacher/end_class", json={"class_id": 1}).get_json()
    assert first["was_active"] is True and first["class_session_id"]
    assert second["status"] == "success" and second["was_active"] is False


def test_class_without_students_cannot_be_started(login_as, repo):
    repo.add_class_member(2, 2, "teacher")                     # teacher also owns the empty class 10A2
    resp = login_as("teacher").post("/api/teacher/start_class", json={"class_id": 2})
    assert resp.status_code == 409 and "chưa có học sinh" in resp.get_json()["message"]
    assert repo.get_class_sessions(2) == []


def test_teacher_cannot_start_a_class_they_do_not_own(login_as, repo):
    resp = login_as("teacher").post("/api/teacher/start_class", json={"class_id": 2})
    assert resp.status_code == 403 and repo.get_class_sessions(2) == []


def test_class_status_is_the_single_source_for_the_dashboard(login_as):
    t = login_as("teacher")
    idle = t.get("/api/teacher/class_status").get_json()
    assert idle["class_session_active"] is False and idle["camera"] is None and idle["mode"] is None
    assert idle["statistics"]["average_focus_score"] is None   # no data is "no score", never 0
    t.post("/api/teacher/start_offline_class", json={"class_id": 1})
    live = t.get("/api/teacher/class_status").get_json()
    assert live["class_session_active"] is True and live["mode_label"] == "Camera lớp học"
    assert live["camera"]["state"] == camera_state.NOT_STREAMING        # no viewer yet: not "healthy"
    assert live["server_time"] and live["started_at"]
    student = live["students"][0]
    for key in ("behaviors", "not_visible_since", "focus_score", "attendance_status", "visibility_status"):
        assert key in student
    assert student["focus_score"] is None


def test_live_behaviour_carries_start_time_for_the_attention_list(login_as, manager, clock):
    from focusguard.behavior import Observation
    t = login_as("teacher")
    t.post("/api/teacher/start_class", json={"class_id": 1})
    rt = manager.class_runtime(1)
    sid = rt.enrolled_ids()[0]
    rt.set_connection(sid, True)
    for i in range(40):
        rt.ingest(sid, Observation(timestamp=clock.advance(0.2), visible=True, ear=0.3, yaw=0, pitch=0,
                                   phone_confidence=0.9 if i >= 5 else None))
    data = t.get("/api/teacher/class_status").get_json()
    student = next(s for s in data["students"] if s["student_id"] == sid)
    assert student["focus_state"] == "PHONE"
    behavior = student["behaviors"][0]
    assert behavior["type"] == "PHONE" and behavior["since"] <= data["server_time"]
    assert data["server_time"] - behavior["since"] >= 5        # "Dùng điện thoại · N giây" is computable
    assert any(log["type"] == "behavior_started" for log in data["logs"])


# ----------------------------------------------------- class session history
def test_class_session_history_and_detail(login_as):
    t = login_as("teacher")
    assert t.get("/api/teacher/class_sessions").get_json()["sessions"] == []
    t.post("/api/teacher/start_class", json={"class_id": 1})
    cs_id = t.post("/api/teacher/end_class", json={"class_id": 1}).get_json()["class_session_id"]
    sessions = t.get("/api/teacher/class_sessions").get_json()["sessions"]
    assert [s["class_session_id"] for s in sessions] == [cs_id]
    assert sessions[0]["average_score"] is None and sessions[0]["present"] == 0
    detail = t.get(f"/api/teacher/class_session/{cs_id}").get_json()
    assert detail["status"] == "success" and len(detail["students"]) == sessions[0]["enrolled"]
    assert all(s["attendance_status"] == "ABSENT" and s["average_score"] is None for s in detail["students"])
    assert t.get("/api/teacher/class_session/99999").status_code == 404


def test_class_session_history_is_not_visible_to_other_roles(login_as, repo):
    t = login_as("teacher")
    t.post("/api/teacher/start_class", json={"class_id": 1})
    cs_id = t.post("/api/teacher/end_class", json={"class_id": 1}).get_json()["class_session_id"]
    for username in ("hocsinh", "phuhuynh"):
        client = login_as(username)
        assert client.get("/api/teacher/class_sessions").status_code == 403
        assert client.get(f"/api/teacher/class_session/{cs_id}").status_code == 403
    repo.create_user("gv2", "pw-gv2", "Cô Hai", "teacher", 2)
    other = login_as("gv2", "pw-gv2")
    assert other.get(f"/api/teacher/class_session/{cs_id}").status_code == 403
    assert other.get("/api/teacher/class_sessions?class_id=1").status_code == 403


def test_teacher_class_choice_is_remembered_only_when_authorised(login_as, repo):
    repo.add_class_member(2, 2, "teacher")
    t = login_as("teacher")
    assert 'value="2" selected' in t.get("/teacher/students?class_id=2").get_data(as_text=True)
    assert t.get("/api/teacher/class_status").get_json()["class_id"] == 2      # remembered across pages
    assert t.get("/teacher/students?class_id=3").status_code == 200            # not theirs: falls back, no leak
    assert t.get("/api/teacher/class_status?class_id=3").status_code == 403


# ------------------------------------------------------ personal session flow
def test_personal_session_start_and_stop_are_idempotent(login_as, repo):
    s = login_as("hocsinh")
    first = s.post("/api/start_session", json={"subject": "Toán"}).get_json()
    second = s.post("/api/start_session", json={}).get_json()
    assert first["already_active"] is False and second["already_active"] is True
    assert first["session_id"] == second["session_id"] and len(repo.get_sessions_for_user(3)) == 1
    assert s.post("/api/stop_session").get_json()["was_active"] is True
    again = s.post("/api/stop_session").get_json()
    assert again["status"] == "success" and again["was_active"] is False


def test_student_stats_expose_truthful_camera_and_class_state(login_as):
    s = login_as("hocsinh")
    idle = s.get("/api/stats").get_json()
    assert idle["is_active"] is False and idle["camera"]["state"] == camera_state.OFF
    assert idle["focus_score"] is None and idle["class_live"] == {"active": False, "mode": None}
    s.post("/api/start_session", json={})
    live = s.get("/api/stats").get_json()
    assert live["camera"]["state"] == camera_state.NOT_STREAMING and live["focus_score"] is None
    login_as("teacher").post("/api/teacher/start_class", json={"class_id": 1})
    assert s.get("/api/stats").get_json()["class_live"] == {"active": True, "mode": "online"}
    assert "statistics" not in s.get("/api/stats").get_json()                  # no class-wide numbers


# --------------------------------------------------------------- camera state
@pytest.mark.parametrize("active,hub,runtime,expected", [
    (False, {}, None, camera_state.OFF),
    (True, {"starting": True}, "WAITING", camera_state.STARTING),
    (True, {"failed": True}, "WAITING", camera_state.UNAVAILABLE),
    (True, {"streaming": False}, "ACTIVE", camera_state.NOT_STREAMING),
    (True, {"streaming": True, "device_ok": False}, "WAITING", camera_state.UNAVAILABLE),
    (True, {"streaming": True, "device_ok": True}, "NO_SIGNAL", camera_state.NO_SIGNAL),
    (True, {"streaming": True, "device_ok": True}, "ACTIVE", camera_state.ACTIVE),
    (True, {"streaming": True, "device_ok": None}, "WAITING", camera_state.STARTING),
])
def test_camera_state_is_derived_from_facts(active, hub, runtime, expected):
    assert camera_state.derive(active, hub, runtime) == expected
    described = camera_state.describe(expected)
    assert described["label"] and described["help"]            # every state has words for the user


def test_camera_hub_reports_a_pipeline_that_failed_to_start():
    hub = CameraHub()
    key = ("classroom", 1, 7)
    mine = lambda k: k[:2] == ("classroom", 1)                 # noqa: E731

    def broken():
        raise RuntimeError("camera busy")
    with pytest.raises(RuntimeError):
        hub.acquire(key, broken)
    state = hub.describe(mine)
    assert state["failed"] is True and state["streaming"] is False and state["starting"] is False
    assert hub.describe(lambda k: k[:2] == ("classroom", 2))["failed"] is False

    class Pipeline:
        device_ok = True

        def release(self):
            pass
    _, generation = hub.acquire(key, Pipeline)                 # a retry clears the failure
    state = hub.describe(mine)
    assert state == {"starting": False, "failed": False, "streaming": True, "viewers": 1, "device_ok": True}
    hub.release(generation)
    assert hub.describe(mine)["streaming"] is False


# ----------------------------------------------------------------------- admin
def test_failed_admin_operations_are_http_errors(login_as):
    admin = login_as("admin")
    assert admin.post("/api/admin/create_class", json={"class_name": "10A1"}).status_code == 409
    assert admin.post("/api/admin/create_class", json={"class_name": "  "}).status_code == 400
    dup = admin.post("/api/admin/create_user", json={"username": "teacher", "password": "x", "display_name": "X",
                                                     "role": "student", "class_id": 1})
    assert dup.status_code == 409 and dup.get_json()["status"] == "error"
    assert admin.post("/api/admin/create_user", json={"username": "u1"}).status_code == 400


def test_class_with_a_running_session_cannot_be_deleted(login_as, repo):
    login_as("teacher").post("/api/teacher/start_class", json={"class_id": 1})
    resp = login_as("admin").post("/api/admin/delete_class/1")
    assert resp.status_code == 409 and repo.get_class_name(1) == "10A1"


def test_admin_user_list_supports_parent_linking_without_exposing_secrets(login_as):
    users = login_as("admin").get("/api/admin/users").get_json()
    parent = next(u for u in users if u["role"] == "parent")
    assert parent["linked_student_name"] and parent["student_id"] == 3
    assert sum(1 for u in users if u["is_self"]) == 1
    for u in users:
        assert "password" not in u and "face_embedding" not in u


def test_admin_cannot_delete_own_account(login_as):
    resp = login_as("admin").post("/api/admin/delete_user/1")
    assert resp.status_code == 400


# ------------------------------------------------------------ teacher settings
def test_teacher_settings_validate_and_round_trip(login_as):
    t = login_as("teacher")
    assert t.post("/api/teacher/settings", json={"display_name": "   "}).status_code == 400
    saved = t.post("/api/teacher/settings", json={"display_name": "Cô Lan", "min_focus_threshold": 250}).get_json()
    assert saved["display_name"] == "Cô Lan" and saved["min_focus_threshold"] == 100      # clamped
    assert t.get("/api/teacher/settings").get_json()["min_focus_threshold"] == 100
    settings = t.get("/api/settings").get_json()
    assert settings["editable"] is False and settings["away_threshold"] == 10.0


# ------------------------------------------------------- logs shown to teachers
def test_persistence_failures_are_logged_without_raw_exception_text(manager, clock):
    from focusguard.behavior import Observation
    rt = manager.start_class(1, mode="online")

    def broken(_row):
        raise RuntimeError("sqlite3.OperationalError: disk I/O error at /srv/private.db")
    rt.sink.save_focus_event = broken
    sid = rt.enrolled_ids()[0]
    for i in range(60):
        rt.ingest(sid, Observation(timestamp=clock.advance(0.2), visible=True, ear=0.3, yaw=0, pitch=0,
                                   phone_confidence=0.9 if 5 <= i < 30 else None))
    messages = [log["message"] for log in rt.class_view(clock())["logs"]]
    assert any(log_type == "error" for log_type in (l["type"] for l in rt.class_view(clock())["logs"]))
    assert not any("sqlite3" in m or "/srv/private.db" in m for m in messages)
    manager.end_class(1)
