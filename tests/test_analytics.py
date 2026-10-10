from focusguard import analytics as an


def test_no_data_is_insufficient_not_fabricated():
    bd = an.event_breakdown([], measured_sessions=0)
    assert bd["status"] == an.INSUFFICIENT and bd["percentages"] is None and bd["total"] == 0
    prof = an.student_profile([])
    assert prof["status"] == an.INSUFFICIENT and prof["average_focus_score"] is None and prof["trend"] is None
    assert an.danger_hour([])["hour"] is None
    assert an.study_time_recommendation([])["status"] == an.INSUFFICIENT
    assert an.class_session_summary([], {})["average_score"] is None
    heat = an.hourly_heatmap([])
    assert all(v["score"] is None for v in heat.values())


def test_analytics_zero_stays_zero():
    """Measured sessions without any events -> zero counts, never fallback numbers."""
    bd = an.event_breakdown([], measured_sessions=3)
    assert bd["status"] == an.OK
    assert bd["counts"] == {"PHONE": 0, "DROWSY": 0, "HEAD_AWAY": 0, "AWAY": 0}
    assert bd["total"] == 0 and bd["percentages"] is None


def test_breakdown_counts_real_events_including_legacy_types():
    events = [{"type": "PHONE", "duration_seconds": 5}, {"type": "PHONE", "duration_seconds": 3},
              {"type": "Buồn ngủ / Ngủ gật"}, {"type": "AWAY", "duration_seconds": 30}, {"type": "UNKNOWN_X"}]
    bd = an.event_breakdown(events, measured_sessions=1)
    assert bd["counts"] == {"PHONE": 2, "DROWSY": 1, "HEAD_AWAY": 0, "AWAY": 1}
    assert bd["percentages"]["PHONE"] == 50.0
    assert bd["durations_seconds"]["PHONE"] == 8.0


def test_profile_uses_only_completed_sessions_and_needs_data_for_trend():
    sessions = [
        {"start_time": "2026-01-01 08:00:00", "end_time": "2026-01-01 08:30:00", "duration_seconds": 1800, "avg_focus_score": 60},
        {"start_time": "2026-01-02 08:00:00", "end_time": "2026-01-02 08:30:00", "duration_seconds": 1800, "avg_focus_score": 90},
        {"start_time": "2026-01-03 08:00:00", "end_time": None, "duration_seconds": None, "final_score": None},
    ]
    prof = an.student_profile(sessions)
    assert prof["total_sessions"] == 2 and prof["average_focus_score"] == 75
    assert prof["trend"] is None   # < 4 sessions: no trend claimed


def test_recommendation_is_rule_based_and_needs_enough_sessions():
    rows = [{"start_time": f"2026-01-0{d} 0{h}:00:00", "end_time": "x", "avg_focus_score": sc}
            for d, h, sc in [(1, 8, 90), (2, 8, 88), (3, 9, 50), (4, 9, 55)]]
    rec = an.study_time_recommendation(rows)
    assert rec["status"] == an.OK and rec["best_time_range"] == "08:00 - 09:00"
    assert rec["method"] == "rule_based_average"
    assert "AI" not in rec["text"]


def test_danger_hour_requires_minimum_samples():
    snaps = [{"timestamp": 1_700_000_000 + i, "focus_score": 50} for i in range(10)]
    assert an.danger_hour(snaps, min_samples=30)["status"] == an.INSUFFICIENT
    assert an.danger_hour(snaps, min_samples=5)["status"] == an.OK


def test_attendance_history_includes_missed_sessions():
    cs = [{"id": 1, "started_at": "2026-01-01 08:00:00", "ended_at": "2026-01-01 09:00:00"},
          {"id": 2, "started_at": "2026-01-02 08:00:00", "ended_at": "2026-01-02 09:00:00"},
          {"id": 3, "started_at": "2026-01-03 08:00:00", "ended_at": None}]
    rows = [{"class_session_id": 1, "attendance_status": "PRESENT", "avg_focus_score": 80, "away_count": 1}]
    hist = an.attendance_history(cs, rows)
    assert [h["class_session_id"] for h in hist] == [2, 1]
    assert hist[0]["missed"] and hist[0]["attendance_status"] == "ABSENT" and hist[0]["focus_score"] is None
    assert not hist[1]["missed"] and hist[1]["focus_score"] == 80


def test_api_analytics_zero_stays_zero(login_as, manager, clock):
    from focusguard.behavior import Observation
    rt = manager.start_class(1, mode="classroom")
    sid = rt.enrolled_ids()[0]
    for _ in range(40):
        t = clock.advance(0.25)
        rt.process_frame(t, {sid: Observation(timestamp=t, visible=True, ear=0.3, yaw=0, pitch=0)})
    manager.end_class(1)
    data = login_as("teacher").get("/api/teacher/analytics_summary?class_id=1").get_json()
    assert data["breakdown"]["status"] == "ok"
    assert data["breakdown"]["total"] == 0
    assert data["root_cause"] is None     # no invented 5/3/12 split
    assert data["watchlist"] == []


def test_api_empty_student_history_is_na(login_as):
    c = login_as("hocsinh")
    prof = c.get("/api/student/profile").get_json()
    assert prof["average_focus_score"] is None and prof["status"] == "insufficient_data"
    assert c.get("/api/latest_session").get_json()["status"] == "empty"
    stats = c.get("/api/stats").get_json()
    assert stats["focus_score"] is None and stats["is_active"] is False
    for fake in ("current_emotion", "emotion_confidence", "predicted_score", "learning_risk", "ai_insight"):
        assert fake not in stats
