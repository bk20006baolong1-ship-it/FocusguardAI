"""StudentRuntimeState: the single in-memory record per (class_session, student_id).

Separate status dimensions are kept apart on purpose. A student can be
``ONLINE`` (socket connected) yet ``NOT_VISIBLE`` to the camera, ``PRESENT``
for attendance yet currently ``AWAY`` from the seat, and so on. The UI must never
collapse "offline", "absent", "not visible" and "distracted" into one value.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional

# identity_status
ID_NOT_SEEN = "NOT_SEEN"          # never bound to a track this session
ID_CONFIRMED = "CONFIRMED"        # currently bound to a live track
ID_LOST = "LOST"                  # was confirmed, track currently missing

# connection_status (browser / socket presence; classroom camera mode is N/A)
CONN_ONLINE = "ONLINE"
CONN_OFFLINE = "OFFLINE"
CONN_NOT_APPLICABLE = "NOT_APPLICABLE"

# attendance_status
ATT_NOT_YET = "NOT_YET"           # class running, student not seen yet
ATT_PRESENT = "PRESENT"
ATT_LATE = "LATE"
ATT_ABSENT = "ABSENT"             # final value once the class ended unseen

# visibility_status
VIS_NOT_SEEN = "NOT_SEEN"
VIS_VISIBLE = "VISIBLE"
VIS_TEMPORARILY_NOT_VISIBLE = "TEMPORARILY_NOT_VISIBLE"
VIS_AWAY = "AWAY"
VIS_NO_SIGNAL = "NO_CAMERA_SIGNAL"  # camera pipeline stalled; nothing is known

# focus_state
FOCUS_UNKNOWN = "UNKNOWN"         # no measurement (not seen / no signal)
FOCUS_FOCUSED = "FOCUSED"         # visible, no sustained distraction behaviour
FOCUS_PHONE = "PHONE"
FOCUS_DROWSY = "DROWSY"
FOCUS_HEAD_AWAY = "HEAD_AWAY"
FOCUS_TEMPORARILY_NOT_VISIBLE = "TEMPORARILY_NOT_VISIBLE"
FOCUS_AWAY = "AWAY"

VI_LABELS = {
    # attendance
    ATT_NOT_YET: "Chưa điểm danh",
    ATT_PRESENT: "Có mặt",
    ATT_LATE: "Đi muộn",
    ATT_ABSENT: "Vắng mặt",
    # connection
    CONN_ONLINE: "Trực tuyến",
    CONN_OFFLINE: "Ngoại tuyến",
    CONN_NOT_APPLICABLE: "Không áp dụng",
    # visibility
    VIS_NOT_SEEN: "Chưa thấy",
    VIS_VISIBLE: "Trong khung hình",
    VIS_TEMPORARILY_NOT_VISIBLE: "Tạm khuất",
    VIS_AWAY: "Rời chỗ",
    VIS_NO_SIGNAL: "Mất tín hiệu camera",
    # focus
    FOCUS_UNKNOWN: "Không có dữ liệu",
    FOCUS_FOCUSED: "Tập trung",
    FOCUS_PHONE: "Dùng điện thoại",
    FOCUS_DROWSY: "Buồn ngủ",
    FOCUS_HEAD_AWAY: "Quay đi chỗ khác",
    # identity
    ID_NOT_SEEN: "Chưa nhận diện",
    ID_CONFIRMED: "Đã xác nhận",
    ID_LOST: "Mất dấu",
}

# Legacy dashboard state names still consumed by the existing templates/JS.
LEGACY_STATE = {
    FOCUS_FOCUSED: "Focused",
    FOCUS_PHONE: "Phone",
    FOCUS_DROWSY: "Sleepy",
    FOCUS_HEAD_AWAY: "Distracted",
    FOCUS_TEMPORARILY_NOT_VISIBLE: "NotVisible",
    FOCUS_AWAY: "Away",
    FOCUS_UNKNOWN: "Unknown",
}


@dataclass
class StudentRuntimeState:
    student_id: int
    display_name: str = ""
    class_id: Optional[int] = None
    roll: Optional[str] = None

    identity_status: str = ID_NOT_SEEN
    identity_confidence: Optional[float] = None
    active_track_id: Optional[int] = None

    connection_status: str = CONN_NOT_APPLICABLE
    attendance_status: str = ATT_NOT_YET
    visibility_status: str = VIS_NOT_SEEN

    focus_state: str = FOCUS_UNKNOWN
    focus_score: Optional[float] = None  # None until the student is measured

    seat_row: Optional[int] = None
    seat_col: Optional[int] = None

    first_seen_at: Optional[float] = None
    last_seen_at: Optional[float] = None
    checkin_at: Optional[float] = None
    not_visible_since: Optional[float] = None
    last_update_at: Optional[float] = None

    visible_seconds: float = 0.0
    measured_seconds: float = 0.0
    focused_seconds: float = 0.0
    away_count: int = 0
    away_seconds: float = 0.0
    event_counts: Dict[str, int] = field(default_factory=dict)
    active_episodes: Dict[str, dict] = field(default_factory=dict)
    score_integral: float = 0.0  # sum(score * dt) for time-weighted average

    def count_event(self, kind: str):
        self.event_counts[kind] = self.event_counts.get(kind, 0) + 1

    @property
    def total_events(self) -> int:
        return sum(v for k, v in self.event_counts.items() if k != "AWAY")

    @property
    def average_focus_score(self) -> Optional[float]:
        if self.measured_seconds <= 0:
            return None
        return self.score_integral / self.measured_seconds

    def to_public_dict(self, now: Optional[float] = None, class_started_at: Optional[float] = None) -> dict:
        score = None if self.focus_score is None else int(round(self.focus_score))
        avg = self.average_focus_score
        away_now = 0.0
        if self.visibility_status == VIS_AWAY and self.not_visible_since is not None and now is not None:
            away_now = max(0.0, now - self.not_visible_since)
        return {
            "student_id": self.student_id,
            "name": self.display_name,
            "class_id": self.class_id,
            "roll": self.roll,
            "identity_status": self.identity_status,
            "identity_confidence": None if self.identity_confidence is None else round(self.identity_confidence, 3),
            "active_track_id": self.active_track_id,
            "connection_status": self.connection_status,
            "attendance_status": self.attendance_status,
            "visibility_status": self.visibility_status,
            "focus_state": self.focus_state,
            "focus_score": score,
            "average_focus_score": None if avg is None else int(round(avg)),
            "seat_row": self.seat_row,
            "seat_col": self.seat_col,
            "checkin_at": self.checkin_at,
            "checkin_offset_seconds": (None if self.checkin_at is None or class_started_at is None
                                       else int(self.checkin_at - class_started_at)),
            "visible_seconds": int(self.visible_seconds),
            "away_count": self.away_count,
            "away_seconds": int(self.away_seconds + away_now),
            "event_counts": dict(self.event_counts),
            "distractions": self.total_events,
            "active_behaviors": sorted(self.active_episodes.keys()),
            # What the teacher needs to act on: which behaviour and since when
            # (server clock; pair with the snapshot's server_time for durations).
            "behaviors": [{"type": k, "since": ep.get("start_time"), "label": VI_LABELS.get(k, k),
                           "confidence": ep.get("confidence")}
                          for k, ep in sorted(self.active_episodes.items(),
                                              key=lambda kv: kv[1].get("start_time") or 0)],
            "not_visible_since": self.not_visible_since,
            "last_seen_at": self.last_seen_at,
            "labels": {
                "attendance": VI_LABELS.get(self.attendance_status, self.attendance_status),
                "connection": VI_LABELS.get(self.connection_status, self.connection_status),
                "visibility": VI_LABELS.get(self.visibility_status, self.visibility_status),
                "focus": VI_LABELS.get(self.focus_state, self.focus_state),
                "identity": VI_LABELS.get(self.identity_status, self.identity_status),
            },
            # Backwards-compatible fields for the existing dashboards.
            "state": LEGACY_STATE.get(self.focus_state, "Unknown"),
            "online": self.visibility_status == VIS_VISIBLE or self.connection_status == CONN_ONLINE,
            "attendance": VI_LABELS.get(self.attendance_status, self.attendance_status),
            "seat_leaving_count": self.away_count,
            "seat_leaving_duration": int(self.away_seconds + away_now),
        }
