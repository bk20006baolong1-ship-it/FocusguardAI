import pytest

from focusguard.behavior import Observation
from focusguard.config import AttendanceConfig, PipelineConfig
from focusguard.evaluation.synthetic import behavior_observations
from focusguard.session_runtime import RuntimeRegistry, SessionRuntime
from focusguard import runtime_state as rs


class MemorySink:
    def __init__(self):
        self.events, self.snapshots, self.students = [], [], []

    def save_focus_event(self, row):
        self.events.append(row)

    def save_focus_snapshot(self, row):
        self.snapshots.append(row)

    def save_session_student(self, row):
        self.students.append(row)


ENROLLED = [{"student_id": 1, "display_name": "A", "class_id": 1},
            {"student_id": 2, "display_name": "B", "class_id": 1},
            {"student_id": 3, "display_name": "C", "class_id": 1}]


def make(sink=None, late_after=60.0, start=1_000.0):
    cfg = PipelineConfig(attendance=AttendanceConfig(late_after_seconds=late_after))
    return SessionRuntime(enrolled=ENROLLED, started_at=start, class_session_id=77, class_id=1,
                          config=cfg, sink=sink or MemorySink())


def visible(t, **kw):
    return Observation(timestamp=t, visible=True, ear=0.3, yaw=0.0, pitch=0.0, **kw)


def test_start_is_clean_every_enrolled_student_attached():
    rt = make()
    view = rt.class_view(1_000.0)
    assert len(view["students"]) == 3
    for s in view["students"]:
        assert s["attendance_status"] == rs.ATT_NOT_YET
        assert s["focus_score"] is None
        assert s["visibility_status"] == rs.VIS_NOT_SEEN
        assert s["event_counts"] == {} and s["away_count"] == 0
    assert view["statistics"]["average_focus_score"] is None


def test_attendance_present_vs_late_uses_first_sighting_time():
    rt = make(late_after=60.0)
    t = 1_000.0
    for i in range(20):
        t = 1_000.0 + 10 + i * 0.5
        rt.process_frame(t, {1: visible(t)})
    for i in range(20):
        t = 1_000.0 + 120 + i * 0.5
        rt.process_frame(t, {1: visible(t), 2: visible(t)})
    s1, s2, s3 = (rt.student_view(i) for i in (1, 2, 3))
    assert s1["attendance_status"] == rs.ATT_PRESENT and s1["checkin_offset_seconds"] == 10
    assert s2["attendance_status"] == rs.ATT_LATE and s2["checkin_offset_seconds"] == 120
    assert s3["attendance_status"] == rs.ATT_NOT_YET


def test_absent_is_not_offline_is_not_distracted():
    rt = make()
    for i in range(50):
        t = 1_000.0 + i * 0.2
        rt.process_frame(t, {1: visible(t)})
    s3 = rt.student_view(3)
    assert s3["focus_state"] == rs.FOCUS_UNKNOWN and s3["focus_score"] is None
    assert s3["connection_status"] == rs.CONN_NOT_APPLICABLE
    summary = {r["student_id"]: r for r in rt.end(1_020.0)}
    assert summary[3]["attendance_status"] == rs.ATT_ABSENT
    assert summary[3]["final_focus_score"] is None


def test_away_duration_and_no_duplicate_leave_count():
    sink = MemorySink()
    rt = make(sink)
    segments = [(10.0, 40.0, "AWAY")]
    for obs in behavior_observations(10.0, 60.0, segments, start=1_000.0):
        rt.process_frame(obs.timestamp, {1: obs} if obs.visible else {})
    st = rt.student_view(1)
    assert st["away_count"] == 1
    assert st["away_seconds"] == pytest.approx(30, abs=1)
    rt.end(1_060.0)
    away_events = [e for e in sink.events if e["type"] == "AWAY"]
    assert len(away_events) == 1
    ev = away_events[0]
    assert ev["duration_seconds"] == pytest.approx(30.0, abs=0.2)
    assert ev["class_session_id"] == 77 and ev["student_id"] == 1
    for key in ("start_time", "end_time", "confidence", "source", "metadata"):
        assert key in ev


def test_short_occlusions_never_count_as_leaving():
    rt = make()
    segs = [(5.0, 7.0, "OCCLUSION"), (15.0, 18.0, "OCCLUSION"), (25.0, 29.0, "OCCLUSION")]
    for obs in behavior_observations(10.0, 40.0, segs, start=1_000.0):
        rt.process_frame(obs.timestamp, {1: obs} if obs.visible else {})
    assert rt.student_view(1)["away_count"] == 0


def test_end_closes_open_episodes_and_persists_summary():
    sink = MemorySink()
    rt = make(sink)
    for obs in behavior_observations(10.0, 20.0, [(5.0, 100.0, "PHONE")], start=1_000.0):
        rt.process_frame(obs.timestamp, {1: obs})
    summary = rt.end(1_020.0)
    assert not rt.active
    phone = [e for e in sink.events if e["type"] == "PHONE"]
    assert len(phone) == 1 and phone[0]["end_time"] is not None
    assert len(sink.students) == 3 and len(summary) == 3
    assert sink.snapshots, "final snapshots must be written"
    assert rt.process_frame(1_021.0, {1: visible(1_021.0)}) == []   # ended runtime ignores frames


def test_unenrolled_student_ids_are_ignored():
    rt = make()
    rt.process_frame(1_001.0, {99: visible(1_001.0)})
    assert 99 not in rt.states


def test_camera_stall_does_not_kill_runtime_and_freezes_score():
    rt = make()
    for i in range(30):
        t = 1_000.0 + i * 0.1
        rt.process_frame(t, {1: visible(t)})
    score = rt.states[1].focus_score
    assert rt.heartbeat(1_010.0) == "NO_SIGNAL"
    assert rt.active
    s = rt.student_view(1)
    assert s["visibility_status"] == rs.VIS_NO_SIGNAL and s["focus_state"] == rs.FOCUS_UNKNOWN
    assert rt.states[1].focus_score == score
    # frames resume -> ACTIVE again, no penalty for the stall
    rt.process_frame(1_020.0, {1: visible(1_020.0)})
    assert rt.camera_status == "ACTIVE"
    assert rt.states[1].focus_score == score


def test_registry_start_class_ends_previous_runtime_no_leakage():
    reg = RuntimeRegistry()
    a = make(start=1_000.0)
    reg.start_class(1, a, 1_000.0)
    a.process_frame(1_001.0, {1: visible(1_001.0)})
    b = make(start=2_000.0)
    reg.start_class(1, b, 2_000.0)
    assert not a.active and reg.class_runtime(1) is b
    assert b.student_view(1)["attendance_status"] == rs.ATT_NOT_YET


def test_session_lifecycle_through_manager(manager, clock, repo):
    rt = manager.start_class(1, mode="classroom", started_by=2)
    assert rt.class_session_id is not None
    sid = rt.enrolled_ids()[0]
    for i in range(40):
        t = clock.advance(0.25)
        rt.process_frame(t, {sid: visible(t)})
    result = manager.end_class(1)
    assert manager.class_runtime(1) is None
    rows = repo.get_session_students(class_session_id=result["class_session_id"])
    assert len(rows) == len(rt.enrolled_ids())
    present = [r for r in rows if r["attendance_status"] == "PRESENT"]
    assert [r["student_id"] for r in present] == [sid]
    cs = repo.get_class_session(result["class_session_id"])
    assert cs["ended_at"] is not None and cs["mode"] == "classroom"
    # a new class starts clean
    rt2 = manager.start_class(1)
    assert rt2.student_view(sid)["attendance_status"] == rs.ATT_NOT_YET
    manager.end_class(1)


def test_personal_session_routes_to_single_runtime(manager, clock, repo):
    session_id = manager.start_personal_session(3, subject="Toán")
    for i in range(60):
        t = clock.advance(0.2)
        manager.route_observation(3, Observation(timestamp=t, visible=True, ear=0.3, yaw=0, pitch=0,
                                                 phone_confidence=0.9 if 3 < i * 0.2 < 9 else None))
    result = manager.stop_personal_session(3)
    assert result["session_id"] == session_id
    row = repo.get_session(session_id)
    assert row["end_time"] and row["final_score"] is not None and row["final_score"] < 100
    events = repo.get_focus_events(session_id=session_id)
    assert [e["type"] for e in events] == ["PHONE"]
    assert manager.route_observation(3, Observation(timestamp=clock.advance(1), visible=True)) == []
