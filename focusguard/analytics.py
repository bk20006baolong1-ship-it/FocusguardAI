"""Analytics computed ONLY from persisted records.

Inputs are plain rows loaded from ``focus_events``, ``focus_snapshots``,
``class_sessions``, ``session_students`` and personal ``sessions``. Nothing is
invented: when there is not enough data a result says ``insufficient_data`` and
numeric fields are ``None`` (rendered as "N/A" / "Không đủ dữ liệu").
Zero stays zero: measured sessions without events produce zero counts, never
fallback numbers.
"""
from collections import defaultdict
from datetime import datetime
from typing import Iterable, List, Optional

OK = "ok"
INSUFFICIENT = "insufficient_data"
NOT_ENOUGH_TEXT = "Không đủ dữ liệu"

BEHAVIOR_TYPES = ("PHONE", "DROWSY", "HEAD_AWAY", "AWAY")
BEHAVIOR_LABELS_VI = {
    "PHONE": "Dùng điện thoại",
    "DROWSY": "Buồn ngủ",
    "HEAD_AWAY": "Quay đi chỗ khác",
    "AWAY": "Rời chỗ",
}

# Legacy event type strings written by the previous implementation.
_LEGACY_TYPES = {
    "dùng điện thoại": "PHONE",
    "buồn ngủ / ngủ gật": "DROWSY",
    "ngoảnh mặt đi": "HEAD_AWAY",
}


def normalize_event_type(t) -> Optional[str]:
    if not t:
        return None
    s = str(t).strip()
    if s.upper() in BEHAVIOR_TYPES:
        return s.upper()
    return _LEGACY_TYPES.get(s.lower())


def _to_dt(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromtimestamp(float(s))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def session_score(row: dict) -> Optional[float]:
    """Time-weighted average if recorded, else final score, else None."""
    for key in ("avg_focus_score", "final_score", "final_focus_score"):
        v = row.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def completed_sessions(sessions: Iterable[dict]) -> List[dict]:
    return [s for s in sessions if s.get("end_time") and session_score(s) is not None]


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _round(v):
    return None if v is None else int(round(v))


# --------------------------------------------------------------------- events
def event_breakdown(events: Iterable[dict], measured_sessions: int) -> dict:
    counts = {k: 0 for k in BEHAVIOR_TYPES}
    durations = {k: 0.0 for k in BEHAVIOR_TYPES}
    for e in events:
        t = normalize_event_type(e.get("type"))
        if t is None:
            continue
        counts[t] += 1
        durations[t] += float(e.get("duration_seconds") or 0.0)
    total = sum(counts.values())
    if measured_sessions <= 0 and total == 0:
        return {"status": INSUFFICIENT, "counts": counts, "total": 0,
                "percentages": None, "durations_seconds": durations,
                "message": NOT_ENOUGH_TEXT}
    pct = None
    if total > 0:
        pct = {k: round(100.0 * v / total, 1) for k, v in counts.items()}
    return {"status": OK, "counts": counts, "total": total, "percentages": pct,
            "durations_seconds": {k: round(v, 1) for k, v in durations.items()}}


# ------------------------------------------------------------------- students
def student_profile(sessions: Iterable[dict], min_sessions_for_trend: int = 4) -> dict:
    done = completed_sessions(sessions)
    if not done:
        return {"status": INSUFFICIENT, "total_sessions": 0, "total_duration_seconds": 0,
                "average_focus_score": None, "trend": None, "message": NOT_ENOUGH_TEXT}
    done_sorted = sorted(done, key=lambda s: (_to_dt(s.get("start_time")) or datetime.min))
    scores = [session_score(s) for s in done_sorted]
    trend = None
    if len(scores) >= min_sessions_for_trend:
        half = len(scores) // 2
        older, recent = _mean(scores[:half]), _mean(scores[half:])
        if recent - older > 2:
            trend = "up"
        elif older - recent > 2:
            trend = "down"
        else:
            trend = "stable"
    return {
        "status": OK,
        "total_sessions": len(done),
        "total_duration_seconds": int(sum(int(s.get("duration_seconds") or 0) for s in done)),
        "average_focus_score": _round(_mean(scores)),
        "trend": trend,
    }


def hourly_heatmap(sessions: Iterable[dict], hours=range(8, 23)) -> dict:
    buckets = defaultdict(list)
    for s in completed_sessions(sessions):
        dt = _to_dt(s.get("start_time"))
        if dt is not None:
            buckets[dt.hour].append(session_score(s))
    out = {}
    for h in hours:
        vals = buckets.get(h)
        if not vals:
            out[f"{h:02d}:00"] = {"score": None, "status": "none", "samples": 0}
            continue
        score = _round(_mean(vals))
        status = "high" if score >= 80 else ("med" if score >= 50 else "low")
        out[f"{h:02d}:00"] = {"score": score, "status": status, "samples": len(vals)}
    return out


def subject_analytics(sessions: Iterable[dict]) -> List[dict]:
    buckets = defaultdict(list)
    for s in completed_sessions(sessions):
        buckets[s.get("subject") or "Không rõ"].append(session_score(s))
    return [{"subject": k, "score": _round(_mean(v)), "sessions": len(v)} for k, v in sorted(buckets.items())]


def session_comparison(sessions: Iterable[dict], limit: int = 5) -> List[dict]:
    done = sorted(completed_sessions(sessions), key=lambda s: (_to_dt(s.get("start_time")) or datetime.min))
    return [{"id": s.get("id"), "start_time": s.get("start_time"), "score": _round(session_score(s)),
             "subject": s.get("subject")} for s in done[-limit:]]


def study_time_recommendation(sessions: Iterable[dict], min_sessions: int = 3,
                              min_per_hour: int = 2) -> dict:
    """Rule-based (not ML): the start hour with the best average score."""
    done = completed_sessions(sessions)
    if len(done) < min_sessions:
        return {"status": INSUFFICIENT, "best_time_range": None, "method": "rule_based_average",
                "text": f"{NOT_ENOUGH_TEXT}: cần ít nhất {min_sessions} phiên học đã hoàn thành."}
    buckets = defaultdict(list)
    for s in done:
        dt = _to_dt(s.get("start_time"))
        if dt is not None:
            buckets[dt.hour].append(session_score(s))
    eligible = {h: _mean(v) for h, v in buckets.items() if len(v) >= min_per_hour}
    if not eligible:
        return {"status": INSUFFICIENT, "best_time_range": None, "method": "rule_based_average",
                "text": f"{NOT_ENOUGH_TEXT}: chưa có khung giờ nào có từ {min_per_hour} phiên trở lên."}
    best = max(eligible, key=lambda h: eligible[h])
    rng = f"{best:02d}:00 - {(best + 1) % 24:02d}:00"
    return {
        "status": OK, "best_time_range": rng, "method": "rule_based_average",
        "average_score": _round(eligible[best]), "sessions_considered": len(done),
        "text": (f"Theo thống kê {len(done)} phiên đã ghi nhận, khung giờ {rng} có điểm tập trung "
                 f"trung bình cao nhất ({_round(eligible[best])}/100). Đây là thống kê đơn giản, không phải dự đoán."),
    }


# -------------------------------------------------------------------- classes
def danger_hour(snapshots: Iterable[dict], min_samples: int = 30) -> dict:
    buckets = defaultdict(list)
    for snap in snapshots:
        if snap.get("focus_score") is None:
            continue
        dt = _to_dt(snap.get("timestamp"))
        if dt is not None:
            buckets[dt.hour].append(float(snap["focus_score"]))
    eligible = {h: _mean(v) for h, v in buckets.items() if len(v) >= min_samples}
    if not eligible:
        return {"status": INSUFFICIENT, "hour": None, "label": NOT_ENOUGH_TEXT}
    h = min(eligible, key=lambda k: eligible[k])
    return {"status": OK, "hour": h, "label": f"{h:02d}:00 - {(h + 1) % 24:02d}:00",
            "average_score": _round(eligible[h]), "samples": len(buckets[h])}


def student_averages(rows: Iterable[dict]) -> dict:
    """Aggregate session_students rows -> {student_id: {avg, sessions, attended}}."""
    per = defaultdict(lambda: {"scores": [], "sessions": 0, "attended": 0})
    for r in rows:
        sid = r.get("student_id")
        if sid is None:
            continue
        per[sid]["sessions"] += 1
        if r.get("attendance_status") in ("PRESENT", "LATE"):
            per[sid]["attended"] += 1
        sc = session_score(r)
        if sc is not None:
            per[sid]["scores"].append(sc)
    return {sid: {"average_focus_score": _round(_mean(v["scores"])), "sessions": v["sessions"],
                  "attended": v["attended"], "measured_sessions": len(v["scores"])}
            for sid, v in per.items()}


def watchlist(rows: Iterable[dict], names: dict, threshold: float = 70.0, min_measured: int = 2) -> List[dict]:
    out = []
    for sid, agg in student_averages(rows).items():
        avg = agg["average_focus_score"]
        if avg is None or agg["measured_sessions"] < min_measured:
            continue
        if avg < threshold:
            out.append({"id": sid, "display_name": names.get(sid, f"#{sid}"), "score": avg,
                        "measured_sessions": agg["measured_sessions"]})
    return sorted(out, key=lambda r: r["score"])


def leaderboard(rows: Iterable[dict], names: dict) -> List[dict]:
    out = []
    for sid, agg in student_averages(rows).items():
        if agg["average_focus_score"] is None:
            continue
        out.append({"student_id": sid, "display_name": names.get(sid, f"#{sid}"),
                    "score": agg["average_focus_score"]})
    return sorted(out, key=lambda r: -r["score"])


def attendance_history(class_sessions: Iterable[dict], rows_for_student: Iterable[dict]) -> List[dict]:
    """Every class session of the student's class, including missed ones."""
    by_cs = {r.get("class_session_id"): r for r in rows_for_student}
    out = []
    for cs in sorted(class_sessions, key=lambda c: (_to_dt(c.get("started_at")) or datetime.min), reverse=True):
        if not cs.get("ended_at"):
            continue
        r = by_cs.get(cs.get("id"))
        out.append({
            "class_session_id": cs.get("id"),
            "started_at": cs.get("started_at"),
            "ended_at": cs.get("ended_at"),
            "mode": cs.get("mode"),
            "attendance_status": (r or {}).get("attendance_status") or "ABSENT",
            "focus_score": _round(session_score(r)) if r else None,
            "away_count": (r or {}).get("away_count"),
            "missed": r is None or (r.get("attendance_status") == "ABSENT"),
        })
    return out


def class_session_summary(rows: Iterable[dict], names: dict) -> dict:
    rows = list(rows)
    if not rows:
        return {"status": INSUFFICIENT, "average_score": None, "top_students": [], "summary_text": NOT_ENOUGH_TEXT}
    scored = [(r["student_id"], session_score(r)) for r in rows if session_score(r) is not None]
    present = sum(1 for r in rows if r.get("attendance_status") in ("PRESENT", "LATE"))
    late = sum(1 for r in rows if r.get("attendance_status") == "LATE")
    if not scored:
        return {"status": INSUFFICIENT, "average_score": None, "top_students": [],
                "present": present, "late": late, "enrolled": len(rows),
                "summary_text": f"Có mặt {present}/{len(rows)} học sinh. {NOT_ENOUGH_TEXT} về điểm tập trung."}
    avg = _round(_mean([s for _, s in scored]))
    top = sorted(scored, key=lambda t: -t[1])[:3]
    top_list = [{"student_id": sid, "name": names.get(sid, f"#{sid}"), "score": _round(sc)} for sid, sc in top]
    text = (f"Có mặt {present}/{len(rows)} học sinh (đi muộn: {late}). Điểm tập trung trung bình "
            f"(đo được cho {len(scored)} học sinh): {avg}/100.")
    return {"status": OK, "average_score": avg, "top_students": top_list, "present": present,
            "late": late, "enrolled": len(rows), "measured": len(scored), "summary_text": text}
