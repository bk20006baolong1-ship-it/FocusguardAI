"""Teacher event review (BTC feedback D).

Every AI focus event has a review state. Reviews are stored in
``event_reviews`` next to the raw event; the raw event row is never changed.
A later review supersedes an earlier one (history is kept).

    UNREVIEWED      no teacher has looked yet (default)
    CONFIRMED       "Giáo viên xác nhận đúng"
    FALSE_POSITIVE  "Giáo viên xác nhận báo nhầm"
    UNCERTAIN       "Không chắc"

The reviewer is always the authenticated principal; a client cannot choose it.
"""
import time
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional

from .analytics import BEHAVIOR_LABELS_VI, BEHAVIOR_TYPES, normalize_event_type
from .privacy import audit as A

UNREVIEWED = "UNREVIEWED"
CONFIRMED = "CONFIRMED"
FALSE_POSITIVE = "FALSE_POSITIVE"
UNCERTAIN = "UNCERTAIN"
VERDICTS = (CONFIRMED, FALSE_POSITIVE, UNCERTAIN)
REVIEW_STATES = (UNREVIEWED,) + VERDICTS
REVIEW_LABELS_VI = {
    UNREVIEWED: "Chưa xem lại",
    CONFIRMED: "Giáo viên xác nhận đúng",
    FALSE_POSITIVE: "Giáo viên xác nhận báo nhầm",
    UNCERTAIN: "Không chắc",
}
NOT_ENOUGH_DATA = "NOT_ENOUGH_DATA"
MAX_NOTE = 500


class ReviewError(ValueError):
    pass


def latest_reviews(reviews: Iterable[dict]) -> Dict[int, dict]:
    out: Dict[int, dict] = {}
    for r in sorted(reviews, key=lambda r: r.get("reviewed_at") or 0):
        out[int(r["event_id"])] = r
    return out


def _date_of(ts) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _rate(n, d, min_d):
    if d < min_d:
        return NOT_ENOUGH_DATA
    return round(n / d, 4)


def feedback_metrics(events: Iterable[dict], reviews: Iterable[dict], min_reviews: int = 5) -> dict:
    """Per behaviour: AI events vs what teachers confirmed.

    Rates are ``NOT_ENOUGH_DATA`` until at least ``min_reviews`` events of that
    behaviour were reviewed (never a misleading 0% or 100%)."""
    latest = latest_reviews(reviews)
    per = {}
    for t in BEHAVIOR_TYPES:
        per[t] = {"ai_events": 0, "reviewed": 0, CONFIRMED: 0, FALSE_POSITIVE: 0, UNCERTAIN: 0}
    for e in events:
        t = normalize_event_type(e.get("type"))
        if t is None:
            continue
        per[t]["ai_events"] += 1
        rv = latest.get(int(e["id"])) if e.get("id") is not None else None
        if rv:
            per[t]["reviewed"] += 1
            per[t][rv["verdict"]] += 1
    out = {}
    for t, c in per.items():
        out[t] = {
            "label": BEHAVIOR_LABELS_VI[t], "ai_events": c["ai_events"], "reviewed": c["reviewed"],
            "teacher_confirmed_events": c[CONFIRMED], "false_positive_events": c[FALSE_POSITIVE],
            "uncertain_events": c[UNCERTAIN],
            "confirmed_rate": _rate(c[CONFIRMED], c["reviewed"], min_reviews),
            "false_positive_rate": _rate(c[FALSE_POSITIVE], c["reviewed"], min_reviews),
            "uncertain_rate": _rate(c[UNCERTAIN], c["reviewed"], min_reviews),
            "review_coverage": _rate(c["reviewed"], c["ai_events"], 1),
        }
    tot_ai = sum(c["ai_events"] for c in per.values())
    tot_rv = sum(c["reviewed"] for c in per.values())
    return {"per_behavior": out, "min_reviews_for_rates": min_reviews,
            "overall": {"ai_events": tot_ai, "reviewed": tot_rv,
                        "teacher_confirmed_events": sum(c[CONFIRMED] for c in per.values()),
                        "review_coverage": _rate(tot_rv, tot_ai, 1)},
            "note": "AI event = tín hiệu AI phát hiện. Teacher confirmed event = giáo viên đã xác nhận đúng."}


class ReviewService:
    def __init__(self, repo, audit: A.AuditLog, clock: Callable[[], float] = time.time, evidence=None):
        self.repo = repo
        self.audit = audit
        self.clock = clock
        self.evidence = evidence

    # ------------------------------------------------------------- write
    def review(self, actor, event: dict, verdict: str, note: Optional[str] = None) -> dict:
        if verdict not in VERDICTS:
            raise ReviewError("Kết quả xem lại không hợp lệ.")
        if event.get("class_session_id") is None:
            raise ReviewError("Chỉ xem lại được sự kiện của buổi học trên lớp.")
        note = (note or "").strip()[:MAX_NOTE] or None
        row = {"event_id": int(event["id"]), "student_id": event.get("student_id"),
               "class_session_id": event.get("class_session_id"), "class_id": event.get("class_id"),
               "verdict": verdict, "teacher_note": note,
               "reviewed_by": actor.user_id,           # from the authenticated session only
               "reviewed_at": self.clock()}
        rid = self.repo.create_event_review(row)
        self.audit.record(A.EVENT_REVIEWED, actor, event.get("student_id"),
                          {"event_id": int(event["id"]), "verdict": verdict, "has_note": bool(note)})
        row["event_review_id"] = rid
        return row

    # -------------------------------------------------------------- read
    def class_events(self, class_id, names: dict, *, student_id=None, date=None, event_type=None,
                     review_status=None, class_session_id=None, context=None, limit=500) -> List[dict]:
        events = [e for e in self.repo.get_focus_events(class_id=class_id) if e.get("class_session_id") is not None]
        latest = latest_reviews(self.repo.get_event_reviews(class_id=class_id))
        now = self.clock()
        out = []
        for e in events:
            t = normalize_event_type(e.get("type")) or e.get("type")
            rv = latest.get(int(e["id"]))
            state = rv["verdict"] if rv else UNREVIEWED
            if student_id is not None and e.get("student_id") != student_id:
                continue
            if event_type and t != event_type:
                continue
            if review_status and state != review_status:
                continue
            if class_session_id is not None and e.get("class_session_id") != class_session_id:
                continue
            if context and (e.get("activity_context") or "NONE") != context:
                continue
            if date and _date_of(e.get("start_time")) != date:
                continue
            ev = None
            if self.evidence is not None:
                uid = e.get("episode_uid") or (e.get("metadata") or {}).get("episode_uid")
                clip = self.evidence.for_episode(uid)
                ev = self.evidence.describe(clip, now) if clip else None
            out.append({
                "event_id": e["id"], "student_id": e.get("student_id"),
                "name": names.get(e.get("student_id"), f"#{e.get('student_id')}"),
                "type": t, "type_label": BEHAVIOR_LABELS_VI.get(t, t),
                "start_time": e.get("start_time"), "end_time": e.get("end_time"),
                "duration_seconds": e.get("duration_seconds"), "confidence": e.get("confidence"),
                "activity_context": e.get("activity_context"), "interpreted_state": e.get("interpreted_state"),
                "class_session_id": e.get("class_session_id"), "source": e.get("source"),
                "review_state": state, "review_label": REVIEW_LABELS_VI[state],
                "teacher_note": rv.get("teacher_note") if rv else None,
                "reviewed_at": rv.get("reviewed_at") if rv else None,
                "reviewed_by": rv.get("reviewed_by") if rv else None,
                "evidence": ev,
            })
        out.sort(key=lambda r: -(r.get("start_time") or 0))
        return out[:limit]

    def history(self, event_id) -> List[dict]:
        return [dict(r, verdict_label=REVIEW_LABELS_VI.get(r["verdict"])) for r in
                self.repo.get_event_reviews(event_id=event_id)]

    def review_queue(self, class_id, names: dict, recent_seconds: float = 7 * 86400, limit: int = 10) -> List[dict]:
        """Students with many events nobody has confirmed yet.

        Ranked only by unreviewed_event_count, recent_event_count and
        total_event_duration. This is a to-do list for the teacher, not a
        judgement about the student."""
        now = self.clock()
        rows = self.class_events(class_id, names, limit=100000)
        per: Dict[int, dict] = {}
        for r in rows:
            sid = r["student_id"]
            p = per.setdefault(sid, {"student_id": sid, "name": r["name"], "unreviewed_event_count": 0,
                                     "recent_event_count": 0, "total_event_duration": 0.0, "types": {}})
            if r["review_state"] == UNREVIEWED:
                p["unreviewed_event_count"] += 1
                p["types"][r["type"]] = p["types"].get(r["type"], 0) + 1
            if r.get("start_time") and now - r["start_time"] <= recent_seconds:
                p["recent_event_count"] += 1
            p["total_event_duration"] += float(r.get("duration_seconds") or 0.0)
        queue = [p for p in per.values() if p["unreviewed_event_count"] > 0]
        queue.sort(key=lambda p: (-p["unreviewed_event_count"], -p["recent_event_count"],
                                  -p["total_event_duration"], p["name"]))
        for p in queue:
            p["total_event_duration"] = round(p["total_event_duration"], 1)
            p["message"] = f"Có {p['unreviewed_event_count']} sự kiện chưa được xác nhận"
        return queue[:limit]

    def class_feedback_metrics(self, class_id, min_reviews: int = 5) -> dict:
        events = [e for e in self.repo.get_focus_events(class_id=class_id) if e.get("class_session_id") is not None]
        return feedback_metrics(events, self.repo.get_event_reviews(class_id=class_id), min_reviews)
