"""Data export for authorised users (JSON / CSV).

Included: the student's AI events (raw) with teacher review state, reviews,
consent records and evaluation annotations. NEVER included: face embedding
(only whether one exists), evidence clip content, passwords.
"""
import csv
import io
import time

from .policy import POLICY_VERSION

EVENT_COLUMNS = ["event_id", "type", "start_time", "end_time", "duration_seconds", "confidence",
                 "activity_context", "interpreted_state", "class_session_id", "session_id", "review_verdict",
                 "reviewed_at"]


STAFF_ONLY_FIELDS = ("note", "annotated_by", "evidence_note", "revoke_reason", "revoked_by", "created_by")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(v):
    """Neutralise spreadsheet formulas in user-written text (CSV injection)."""
    if isinstance(v, str) and v.startswith(_FORMULA_START):
        return "'" + v
    return v


def build_student_export(repo, student_id, *, include_teacher_notes=True, review_service=None, now=None) -> dict:
    now = time.time() if now is None else now
    user = repo.get_user_by_id(student_id) or {}
    events = repo.get_focus_events(student_id=student_id)
    reviews = repo.get_event_reviews(student_id=student_id)
    latest = {}
    for r in reviews:
        latest[r["event_id"]] = r
    ev_rows = []
    for e in events:
        rv = latest.get(e.get("id"))
        ev_rows.append({
            "event_id": e.get("id"), "type": e.get("type"), "start_time": e.get("start_time"),
            "end_time": e.get("end_time"), "duration_seconds": e.get("duration_seconds"),
            "confidence": e.get("confidence"), "activity_context": e.get("activity_context"),
            "interpreted_state": e.get("interpreted_state"), "class_session_id": e.get("class_session_id"),
            "session_id": e.get("session_id"), "review_verdict": rv["verdict"] if rv else "UNREVIEWED",
            "reviewed_at": rv["reviewed_at"] if rv else None,
        })
    review_rows = [{k: v for k, v in r.items() if include_teacher_notes or k != "teacher_note"} for r in reviews]
    annotations = repo.get_annotations(student_id=student_id)
    consents = repo.get_consents(student_id=student_id)
    if not include_teacher_notes:
        # parent copy: internal staff notes and staff ids stay with the school
        annotations = [{k: v for k, v in a.items() if k not in STAFF_ONLY_FIELDS} for a in annotations]
        consents = [{k: v for k, v in c.items() if k not in STAFF_ONLY_FIELDS} for c in consents]
    enrollment = repo.get_face_enrollment(student_id)
    return {
        "generated_at": now, "policy_version": POLICY_VERSION,
        "student": {"student_id": int(student_id), "display_name": user.get("display_name"),
                    "class_id": user.get("class_id")},
        "face_embedding_exists": bool(enrollment.get("has_face")),
        "face_enrolled_at": enrollment.get("enrolled_at"),
        "consents": consents,
        "events": ev_rows,
        "reviews": review_rows,
        "evaluation_annotations": annotations,
        "excluded": ["face_embedding", "evidence_clip_content", "password"],
    }


def to_csv(export: dict, section: str = "events") -> str:
    buf = io.StringIO()
    if section == "events":
        w = csv.DictWriter(buf, fieldnames=EVENT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in export["events"]:
            w.writerow({k: csv_safe(v) for k, v in r.items()})
    else:
        rows = {"reviews": export["reviews"], "consents": export["consents"],
                "annotations": export["evaluation_annotations"]}.get(section)
        if rows is None:
            raise ValueError("unknown section")
        cols = sorted({k for r in rows for k in r}) or ["empty"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: csv_safe(v) for k, v in r.items()})
    return "﻿" + buf.getvalue()
