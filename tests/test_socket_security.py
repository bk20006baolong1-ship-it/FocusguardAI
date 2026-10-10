"""Server-authoritative Socket.IO with class-isolated rooms."""
import pytest

from focusguard import realtime


@pytest.fixture
def sio(app_bundle):
    return app_bundle[1]


def connect(app, sio, username, password="123"):
    # conftest.login is loaded by path-independent import: a third-party wheel
    # may ship its own top-level "tests" package in site-packages.
    import conftest
    flask_client = conftest.login(app, username, password)
    return sio.test_client(app, flask_test_client=flask_client)


def events(client, name):
    return [e for e in client.get_received() if e["name"] == name]


def test_unauthenticated_socket_is_refused(app, sio):
    c = sio.test_client(app)
    assert not c.is_connected()


def test_client_cannot_push_ai_state_or_identity(app, sio, manager):
    manager.start_class(1, mode="online")
    student = connect(app, sio, "hocsinh")
    teacher = connect(app, sio, "teacher")
    teacher.emit("join_teacher_room", {"class_id": 1})
    teacher.get_received()
    student.get_received()
    student.emit("student_data_push", {"name": "Trần Thị Bình", "state": "Focused", "focus_score": 100,
                                       "distractions": 0})
    errs = events(student, "error")
    assert errs and errs[0]["args"][0]["code"] == "client_state_rejected"
    assert "focus_score" in errs[0]["args"][0]["rejected_fields"]
    # nothing was relayed to the teacher and no state changed
    assert events(teacher, "student_update") == []
    rt = manager.class_runtime(1)
    assert all(st.focus_score is None for st in rt.states.values())
    student.emit("student_frame_push", {"name": "x", "frame": "data:image/jpeg;base64,AAAA"})
    assert events(student, "error")[0]["args"][0]["code"] == "client_state_rejected"
    assert events(teacher, "student_frame") == []
    manager.end_class(1)


def test_student_cannot_impersonate_via_join_name(app, sio):
    student = connect(app, sio, "hocsinh")
    student.get_received()
    student.emit("join_student_room", {"name": "Trần Thị Bình"})
    joined = events(student, "joined")
    assert joined and joined[0]["args"][0]["rooms"] == [realtime.student_room(3)]


def test_student_cannot_join_teacher_room(app, sio):
    student = connect(app, sio, "hocsinh")
    student.get_received()
    student.emit("join_teacher_room", {"class_id": 1})
    assert events(student, "error")[0]["args"][0]["code"] == "forbidden"
    student.emit("request_class_snapshot", {"class_id": 1})
    assert events(student, "class_snapshot") == []


def test_teacher_rooms_are_class_isolated(app, sio, repo, app_bundle):
    repo.create_user("teacher2", "pw-teacher2", "Cô Lan", "teacher", 2)
    t1 = connect(app, sio, "teacher")
    t2 = connect(app, sio, "teacher2", "pw-teacher2")
    t1.emit("join_teacher_room", {})
    t2.emit("join_teacher_room", {})
    t2.emit("join_teacher_room", {"class_id": 1})      # not allowed
    assert any(e["args"][0].get("code") == "forbidden" for e in events(t2, "error"))
    t1.get_received()
    t2.get_received()
    sio.emit("class_snapshot", {"class_id": 1, "students": []}, room=realtime.teacher_room(1))
    assert len(events(t1, "class_snapshot")) == 1
    assert events(t2, "class_snapshot") == []
    t2.emit("request_class_snapshot", {"class_id": 1})
    assert events(t2, "class_snapshot") == []
    t2.emit("request_class_snapshot", {"class_id": 2})
    snap = events(t2, "class_snapshot")
    assert snap and snap[0]["args"][0]["class_id"] == 2


def test_broadcaster_sends_class_data_only_to_that_class_and_self_view_to_student(app, sio, repo, manager, clock):
    from focusguard.behavior import Observation
    repo.create_user("teacher2", "pw-teacher2", "Cô Lan", "teacher", 2)
    t1 = connect(app, sio, "teacher")
    t2 = connect(app, sio, "teacher2", "pw-teacher2")
    t1.emit("join_teacher_room", {})
    t2.emit("join_teacher_room", {})
    student = connect(app, sio, "hocsinh")
    manager.start_personal_session(3)
    manager.route_observation(3, Observation(timestamp=clock.advance(0.5), visible=True, ear=0.3, yaw=0, pitch=0))
    rt = manager.start_class(1, mode="classroom")
    for c in (t1, t2, student):
        c.get_received()
    # run one broadcaster iteration without the infinite loop
    broadcaster_body(app, sio, manager)
    assert events(t1, "class_snapshot") and events(t2, "class_snapshot") == []
    mine = events(student, "my_stats_update")
    assert mine and mine[0]["args"][0]["student_id"] == 3
    assert "students" not in mine[0]["args"][0] and "statistics" not in mine[0]["args"][0]
    manager.end_class(1)


def broadcaster_body(app, sio, manager):
    for cid in manager.registry.active_classes():
        view = manager.class_live_view(cid)
        sio.emit("class_snapshot", {"class_id": cid, "students": view["students"]}, room=realtime.teacher_room(cid))
    for sid in list(manager._personal_sessions):
        sio.emit("my_stats_update", realtime.student_self_view(manager.student_live_view(sid)["student"]),
                 room=realtime.student_room(sid))


def test_rooms_never_use_global_or_name_based_names():
    assert realtime.teacher_room(3) == "class:3:teachers"
    assert realtime.class_students_room(3) == "class:3:students"
    assert realtime.student_room(7) == "student:7"
    import inspect
    import app as app_module
    src = inspect.getsource(app_module)
    assert "room='teachers'" not in src and 'room="teachers"' not in src
    assert "student_{name}" not in src and 'room=f"student_' not in src
