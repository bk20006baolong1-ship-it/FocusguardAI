"""Teacher event review (BTC feedback D): review states, authorisation, raw events untouched."""
import pytest

from conftest import add_teacher, run_class_with_events
from focusguard.review import CONFIRMED, FALSE_POSITIVE, NOT_ENOUGH_DATA, UNCERTAIN, feedback_metrics


@pytest.fixture
def events(manager, clock):
    _, evs = run_class_with_events(manager, clock)
    assert [e["type"] for e in evs] == ["PHONE", "HEAD_AWAY"]
    return evs


def test_event_list_shows_review_state_unreviewed_by_default(login_as, events):
    res = login_as("teacher").get("/api/teacher/events?class_id=1").get_json()
    assert res["status"] == "success" and len(res["events"]) == 2
    row = res["events"][0]
    for key in ("event_id", "name", "type", "duration_seconds", "confidence", "activity_context", "start_time",
                "review_state"):
        assert key in row
    assert {r["review_state"] for r in res["events"]} == {"UNREVIEWED"}


def test_teacher_of_own_class_can_review_and_raw_event_is_not_mutated(login_as, repo, events):
    ev = events[0]
    before = repo.get_focus_event(ev["id"])
    t = login_as("teacher")
    resp = t.post(f"/api/teacher/events/{ev['id']}/review", json={"verdict": "FALSE_POSITIVE", "note": "đang tra cứu"})
    assert resp.status_code == 200 and resp.get_json()["review"]["verdict"] == FALSE_POSITIVE
    assert repo.get_focus_event(ev["id"]) == before                         # raw AI output unchanged
    rows = repo.get_event_reviews(event_id=ev["id"])
    assert len(rows) == 1 and rows[0]["teacher_note"] == "đang tra cứu"
    listed = t.get("/api/teacher/events?class_id=1&review_status=FALSE_POSITIVE").get_json()["events"]
    assert [r["event_id"] for r in listed] == [ev["id"]]


def test_reviewer_identity_comes_from_the_session_not_the_client(login_as, repo, events):
    t = login_as("teacher")
    t.post(f"/api/teacher/events/{events[0]['id']}/review",
           json={"verdict": "CONFIRMED", "reviewed_by": 1, "reviewed_at": 0, "student_id": 999})
    row = repo.get_event_reviews(event_id=events[0]["id"])[0]
    assert row["reviewed_by"] == 2 and row["reviewed_at"] > 0 and row["student_id"] == 3


def test_other_teacher_student_and_parent_cannot_review(login_as, repo, events):
    add_teacher(repo)
    ev = events[0]
    for user in ("teacher2", "hocsinh", "phuhuynh"):
        c = login_as(user)
        assert c.post(f"/api/teacher/events/{ev['id']}/review", json={"verdict": "CONFIRMED"}).status_code == 403, user
        assert c.get(f"/api/teacher/events/{ev['id']}").status_code == 403, user
        assert c.get("/api/teacher/events?class_id=1").status_code == 403, user
    assert repo.get_event_reviews(event_id=ev["id"]) == []


def test_admin_cannot_review_on_behalf_of_teacher(login_as, events):
    assert login_as("admin").post(f"/api/teacher/events/{events[0]['id']}/review",
                                  json={"verdict": "CONFIRMED"}).status_code == 403


def test_invalid_verdict_and_unknown_event(login_as, events):
    t = login_as("teacher")
    assert t.post(f"/api/teacher/events/{events[0]['id']}/review", json={"verdict": "GUILTY"}).status_code == 400
    assert t.post("/api/teacher/events/99999/review", json={"verdict": "CONFIRMED"}).status_code == 404
    assert t.get("/api/teacher/events?class_id=1&review_status=MAYBE").status_code == 400
    assert t.get("/api/teacher/events?class_id=1&type=EMOTION").status_code == 400


def test_review_history_is_kept_and_latest_wins(login_as, repo, events):
    t = login_as("teacher")
    eid = events[0]["id"]
    t.post(f"/api/teacher/events/{eid}/review", json={"verdict": "UNCERTAIN"})
    t.post(f"/api/teacher/events/{eid}/review", json={"verdict": "CONFIRMED", "note": "đã hỏi lại"})
    detail = t.get(f"/api/teacher/events/{eid}").get_json()["event"]
    assert detail["review_state"] == CONFIRMED
    assert [h["verdict"] for h in detail["history"]] == [UNCERTAIN, CONFIRMED]
    assert len(repo.get_event_reviews(event_id=eid)) == 2


def test_filters_by_student_type_date_and_context(login_as, repo, events):
    t = login_as("teacher")
    from datetime import datetime
    day = datetime.fromtimestamp(events[0]["start_time"]).strftime("%Y-%m-%d")
    assert len(t.get(f"/api/teacher/events?class_id=1&date={day}").get_json()["events"]) == 2
    assert t.get("/api/teacher/events?class_id=1&date=1999-01-01").get_json()["events"] == []
    assert [e["type"] for e in t.get("/api/teacher/events?class_id=1&type=PHONE").get_json()["events"]] == ["PHONE"]
    assert t.get("/api/teacher/events?class_id=1&student_id=4").get_json()["events"] == []
    assert len(t.get("/api/teacher/events?class_id=1&context=NONE").get_json()["events"]) == 2


def test_needs_review_queue_ranks_by_unreviewed_events_with_neutral_wording(login_as, manager, clock, events):
    run_class_with_events(manager, clock, student_id=4, head_seconds=0.0)     # student 4: one PHONE event
    t = login_as("teacher")
    queue = t.get("/api/teacher/review_queue?class_id=1").get_json()["queue"]
    assert [q["student_id"] for q in queue] == [3, 4]
    assert queue[0]["unreviewed_event_count"] == 2 and "total_event_duration" in queue[0]
    assert queue[0]["message"] == "Có 2 sự kiện chưa được xác nhận"
    for e in events:
        t.post(f"/api/teacher/events/{e['id']}/review", json={"verdict": "CONFIRMED"})
    queue = t.get("/api/teacher/review_queue?class_id=1").get_json()["queue"]
    assert [q["student_id"] for q in queue] == [4]


def test_dashboard_wording_never_ranks_or_judges_students(login_as):
    html = login_as("teacher").get("/teacher/dashboard").get_data(as_text=True)
    assert "Cần giáo viên xem lại" in html
    for banned in ("Học sinh kém nhất", "Mất tập trung nhất", "Xếp hạng", "Học sinh vi phạm", "AI violation", "AI sai"):
        assert banned not in html


def test_human_feedback_metrics_need_enough_reviews():
    events = [{"id": i, "type": "PHONE"} for i in range(1, 9)] + [{"id": 20, "type": "AWAY"}]
    reviews = ([{"event_id": i, "verdict": CONFIRMED, "reviewed_at": i} for i in range(1, 5)] +
               [{"event_id": 5, "verdict": FALSE_POSITIVE, "reviewed_at": 5},
                {"event_id": 20, "verdict": FALSE_POSITIVE, "reviewed_at": 6}])
    m = feedback_metrics(events, reviews, min_reviews=5)
    phone = m["per_behavior"]["PHONE"]
    assert phone["ai_events"] == 8 and phone["reviewed"] == 5 and phone["teacher_confirmed_events"] == 4
    assert phone["confirmed_rate"] == 0.8 and phone["false_positive_rate"] == 0.2 and phone["uncertain_rate"] == 0.0
    assert phone["review_coverage"] == 0.625
    away = m["per_behavior"]["AWAY"]
    assert away["confirmed_rate"] == NOT_ENOUGH_DATA and away["review_coverage"] == 1.0
    assert m["per_behavior"]["DROWSY"]["review_coverage"] == NOT_ENOUGH_DATA      # no AI events: not 0%


def test_review_metrics_api_separates_ai_and_teacher_confirmed(login_as, events):
    t = login_as("teacher")
    t.post(f"/api/teacher/events/{events[0]['id']}/review", json={"verdict": "CONFIRMED"})
    m = t.get("/api/teacher/review_metrics?class_id=1").get_json()
    assert m["overall"]["ai_events"] == 2 and m["overall"]["teacher_confirmed_events"] == 1
    assert m["per_behavior"]["PHONE"]["confirmed_rate"] == NOT_ENOUGH_DATA


def test_review_is_audited(login_as, manager, events):
    login_as("teacher").post(f"/api/teacher/events/{events[0]['id']}/review", json={"verdict": "CONFIRMED", "note": "x"})
    rows = manager.audit.list(action="EVENT_REVIEWED")
    assert len(rows) == 1 and rows[0]["actor_id"] == 2 and rows[0]["metadata"]["verdict"] == CONFIRMED
