"""Context policy branches end to end, legacy (no context) golden values,
neutral wording on every role's pages, and per-role access to analytics."""
import os
import re

import pytest

from conftest import add_teacher, login
from focusguard.behavior import Observation
from focusguard.config import DEFAULT_CONFIG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FPS = 5.0
RATES = DEFAULT_CONFIG.focus.penalty_per_second
BEH = DEFAULT_CONFIG.behavior
KINDS = {"left": dict(yaw=45.0), "down": dict(pitch=35.0), "up": dict(pitch=-35.0), "drowsy": dict(ear=0.08),
         "away": None, "phone": dict(phone_confidence=0.9)}


def play(manager, clock, context, kind, seconds):
    rt = manager.start_class(1, mode="classroom")
    if context:
        manager.set_activity_context(1, context)

    def feed(sec, kw):
        for _ in range(int(sec * FPS)):
            t = clock.advance(1.0 / FPS)
            if kw is None:
                rt.process_frame(t, {})                       # student out of frame
                continue
            base = dict(ear=0.30, yaw=0.0, pitch=0.0, phone_confidence=None)
            base.update(kw)
            rt.process_frame(t, {3: Observation(timestamp=t, visible=True, face_visible=True, **base)})
    feed(5, {})
    feed(seconds, KINDS[kind])
    view = rt.student_view(3)
    state = {k: (e.get("interpretation") or {}).get("interpreted_state") for k, e in rt.states[3].active_episodes.items()}
    return round(view["focus_score"]), state


# ------------------------------------------------- legacy golden (no context)
@pytest.mark.parametrize("kind,beh,seconds,threshold", [
    ("left", "HEAD_AWAY", 10, BEH.head_away_min_seconds),
    ("away", "AWAY", 30, BEH.away_min_seconds),
    ("phone", "PHONE", 10, BEH.phone_min_seconds),
])
def test_no_context_scores_exactly_like_before_the_policy(manager, clock, kind, beh, seconds, threshold):
    score, state = play(manager, clock, None, kind, seconds)
    assert state == {beh: "SIGNAL"}
    assert score == round(100 - RATES[beh] * (seconds - threshold))


def test_lecture_has_no_rules_so_it_equals_no_context(manager, clock):
    a = play(manager, clock, None, "left", 10)
    manager.end_class(1)
    b = play(manager, clock, "LECTURE", "left", 10)
    assert a == b


# ------------------------------------------------------ untested branches
@pytest.mark.parametrize("context,kind,seconds,state,penalised", [
    ("WRITING", "down", 10, "EXPECTED_IN_CONTEXT", 0.0),
    ("WRITING", "left", 10, "REDUCED_IN_CONTEXT", 0.5),
    ("LOOKING_AT_BOARD", "up", 10, "EXPECTED_IN_CONTEXT", 0.0),
    ("LOOKING_AT_BOARD", "left", 10, "REDUCED_IN_CONTEXT", 0.5),
    ("SELF_STUDY", "down", 10, "EXPECTED_IN_CONTEXT", 0.0),
    ("SELF_STUDY", "left", 10, "SIGNAL", 1.0),
    ("EXAM", "down", 10, "EXPECTED_IN_CONTEXT", 0.0),
    ("TRANSITION", "drowsy", 10, "REDUCED_IN_CONTEXT", 0.5),
    ("GROUP_DISCUSSION", "away", 30, "REDUCED_IN_CONTEXT", 0.0),
])
def test_context_policy_branches(manager, clock, context, kind, seconds, state, penalised):
    score, states = play(manager, clock, context, kind, seconds)
    assert list(states.values()) == [state]
    beh = list(states)[0]
    threshold = {"HEAD_AWAY": BEH.head_away_min_seconds, "DROWSY": BEH.drowsy_min_seconds,
                 "AWAY": BEH.away_min_seconds}[beh]
    assert abs(score - (100 - penalised * RATES[beh] * (seconds - threshold))) <= 1


@pytest.mark.parametrize("context,kind,seconds", [
    ("WRITING", "left", 50),                 # looking sideways > 45 s while writing escalates
    ("TRANSITION", "drowsy", 40),            # > 30 s
    ("GROUP_DISCUSSION", "away", 200),       # out of frame > 180 s during group work
])
def test_long_episodes_escalate_back_to_a_signal(manager, clock, context, kind, seconds):
    _, states = play(manager, clock, context, kind, seconds)
    assert list(states.values()) == ["SIGNAL"]


def test_no_camera_code_ever_sets_the_activity_context():
    for name in ("camera_ai.py", "classroom_ai.py"):
        src = open(os.path.join(ROOT, name), encoding="utf-8").read()
        assert "set_activity_context" not in src and "activity_context" not in src, name


# ---------------------------------------------------------- wording / UI
BANNED_EVERYWHERE = ("AI violation", "Học sinh vi phạm", "Học sinh kém nhất", "Mất tập trung nhất", "kém nhất",
                     "Bảng xếp hạng", "leaderboard của lớp", "đọc suy nghĩ", "cảm xúc của học sinh")


def _pages(login_as):
    t = login_as("teacher")
    out = [t.get(p).get_data(as_text=True) for p in ("/teacher/dashboard", "/teacher/events", "/teacher/analytics",
                                                     "/teacher/evaluation")]
    out.append(login_as("hocsinh").get("/").get_data(as_text=True))
    out.append(login_as("phuhuynh").get("/parent/dashboard").get_data(as_text=True))
    for js in ("teacher.js", "student.js", "parent.js", "feedback.js", "core.js"):
        out.append(open(os.path.join(ROOT, "static", "js", js), encoding="utf-8").read())
    return out


def test_no_page_ranks_or_judges_students(login_as):
    for text in _pages(login_as):
        for banned in BANNED_EVERYWHERE:
            assert banned not in text, banned


def test_student_and_parent_pages_do_not_state_distraction_as_fact(login_as):
    student = login_as("hocsinh").get("/").get_data(as_text=True)
    js = open(os.path.join(ROOT, "static", "js", "student.js"), encoding="utf-8").read()
    for text in (student, js):
        assert "mất tập trung" not in text.lower()
    assert "Tín hiệu AI" in js


def test_todays_scores_are_listed_by_name_without_positions():
    js = open(os.path.join(ROOT, "static", "js", "teacher.js"), encoding="utf-8").read()
    block = js[js.index('$("leaderBody").innerHTML'):js.index('$("leaderBody").innerHTML') + 400]
    assert "(i + 1)" not in block and "FG.initials" in block
    from focusguard import analytics
    rows = [{"student_id": 3, "class_session_id": 1, "attendance_status": "PRESENT", "average_focus_score": 95,
             "focus_score": 95, "measured": True},
            {"student_id": 4, "class_session_id": 1, "attendance_status": "PRESENT", "average_focus_score": 40,
             "focus_score": 40, "measured": True}]
    names = {3: "Zung", 4: "An"}
    order = [r["display_name"] for r in analytics.leaderboard(rows, names)]
    assert order == sorted(order, key=str.lower)


def test_reduced_and_ambiguous_interpretations_are_not_called_free(manager, clock):
    src = open(os.path.join(ROOT, "static", "js", "core.js"), encoding="utf-8").read()
    block = src[src.index("FG.contextExpected = "):src.index("FG.REVIEW = {")]
    assert "REDUCED_IN_CONTEXT" in block and "AMBIGUOUS_IN_CONTEXT" in block
    assert re.search(r'm === 0 \? " Không trừ điểm\." : ""', block)
    rt = manager.start_class(1, mode="classroom")
    manager.set_activity_context(1, "WRITING")
    for _ in range(int(5 * FPS)):
        t = clock.advance(1 / FPS)
        rt.process_frame(t, {3: Observation(timestamp=t, visible=True, face_visible=True, ear=0.3, yaw=45.0, pitch=0.0)})
    b = rt.student_view(3)["behaviors"][0]
    assert b["interpreted_state"] == "REDUCED_IN_CONTEXT" and b["penalty_multiplier"] == 0.5
    logs = [x["message"] for x in rt.logs if x["type"] == "behavior_started"]
    assert logs and all("giảm mức theo hoạt động" in m and "phù hợp hoạt động" not in m for m in logs)


def test_parent_live_view_carries_the_interpretation():
    from focusguard import realtime
    view = {"student_id": 3, "behaviors": [{"type": "HEAD_AWAY", "alert": False}], "interpretation": {"x": 1},
            "class_average": 77}
    out = realtime.student_self_view(view)
    assert out["behaviors"] and out["interpretation"] and "class_average" not in out


# ------------------------------------------------------------ analytics
def test_false_positive_reviews_are_excluded_from_event_statistics(manager, app, clock):
    from conftest import run_class_with_events
    from focusguard.authz import Principal
    _, events = run_class_with_events(manager, clock)
    teacher = Principal(2, "teacher", frozenset({1}))
    manager.reviews.review(teacher, events[0], "FALSE_POSITIVE")
    bd = manager.class_analytics(1)["breakdown"]
    assert bd["excluded_false_positives"] == 1 and bd["total"] == len(events) - 1
    detail = manager.class_session_detail(events[0]["class_session_id"], 1)
    assert {e["review_state"] for e in detail["events"]} >= {"FALSE_POSITIVE"}
    assert all("activity_context" in e for e in detail["events"])
    raw = manager.repo.get_focus_events(class_session_id=events[0]["class_session_id"])
    assert len(raw) == len(events)                            # raw AI events untouched


def test_watchlist_rule_comes_from_config(manager):
    a = manager.class_analytics(1)
    assert a["watchlist_rule"] == {"score_below": DEFAULT_CONFIG.analytics.watchlist_score_below,
                                   "min_sessions": DEFAULT_CONFIG.analytics.watchlist_min_sessions}


# --------------------------------------------------------- access per role
STUDENT_ENDPOINTS = ("/api/student/heatmap", "/api/student/subject_analytics", "/api/student/profile",
                     "/api/student/session_comparison", "/api/student/recommendations",
                     "/api/student/attendance_history")


@pytest.mark.parametrize("path", STUDENT_ENDPOINTS)
def test_student_endpoints_only_serve_own_or_linked_data(app, path):
    s = login(app, "hocsinh")
    assert s.get(path).status_code == 200
    other = s.get(path + "?student_id=4")
    assert other.status_code in (200, 403)
    if other.status_code == 200:
        assert other.get_json() == s.get(path).get_json()     # forced to self
    parent = login(app, "phuhuynh")
    assert parent.get(path).status_code == 200
    p_other = parent.get(path + "?student_id=4")
    assert p_other.status_code in (200, 403)
    if p_other.status_code == 200:
        assert p_other.get_json() == parent.get(path).get_json()   # forced to the linked child


def test_teacher_of_another_class_cannot_read_class_analytics(manager, app):
    add_teacher(manager.repo, "teacher2", class_id=2)
    t2 = login(app, "teacher2")
    for path in ("/api/teacher/analytics_summary?class_id=1", "/api/leaderboard?class_id=1",
                 "/api/teacher/review_queue?class_id=1", "/api/teacher/review_metrics?class_id=1"):
        r = t2.get(path)
        assert r.status_code == 403 or (r.get_json() or {}).get("class_id") not in (1, "1"), path


def test_admin_threshold_settings_are_range_checked(app):
    a = login(app, "admin")
    assert a.post("/api/settings", json={"ear_threshold": 5}).status_code == 400
    assert a.post("/api/settings", json={"drowsy_threshold": "nan"}).status_code == 400
    assert a.post("/api/settings", json={"ear_threshold": 0.2}).status_code == 200
