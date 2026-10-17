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

# identity_source (provenance of the current binding; kept apart from identity_status)
SRC_AUTO_FACE = "AUTO_FACE"                  # face recognition, temporal confirmation
SRC_TEACHER_TAG = "TEACHER_TAG"              # teacher tagged the person track
SRC_AUTO_RECOVERED = "AUTO_RECOVERED"        # re-identified after the track was lost
SRC_TEACHER_CORRECTED = "TEACHER_CORRECTED"  # teacher replaced a wrong identity

# attendance_status
ATT_NOT_SEEN = "NOT_SEEN"         # class running, student not confirmed yet (not "absent")
ATT_NOT_YET = ATT_NOT_SEEN        # backwards-compatible name
ATT_PRESENT = "PRESENT"
ATT_LATE = "LATE"
ATT_ABSENT = "ABSENT"             # final value: never confirmed before attendance finalisation
# attendance_source
ATT_SRC_AUTO_FACE = "AUTO_FACE"
ATT_SRC_TEACHER_CONFIRMED = "TEACHER_CONFIRMED"   # teacher tagged / confirmed the student on camera
ATT_SRC_TEACHER_CORRECTED = "TEACHER_CORRECTED"   # teacher overrode the status (never auto-overwritten)
ATT_SRC_AUTO_FINALIZED = "AUTO_FINALIZED"         # NOT_SEEN -> ABSENT at finalisation

# visibility_status
VIS_NOT_SEEN = "NOT_SEEN"
VIS_VISIBLE = "VISIBLE"
VIS_TEMPORARILY_NOT_VISIBLE = "TEMPORARILY_NOT_VISIBLE"
VIS_AWAY = "AWAY"
VIS_NO_SIGNAL = "NO_CAMERA_SIGNAL"  # camera pipeline stalled; nothing is known
# Not confirmed on any track, but a person whose identity is not resolved is in the frame
# and could be this student: never counted as AWAY / absent while unresolved.
VIS_IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"

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
    ATT_NOT_SEEN: "Chưa thấy",
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
    VIS_IDENTITY_UNRESOLVED: "Chưa xác định danh tính",
    # identity source
    SRC_AUTO_FACE: "Nhận diện khuôn mặt", SRC_TEACHER_TAG: "Giáo viên gắn tên",
    SRC_AUTO_RECOVERED: "Nhận lại tự động", SRC_TEACHER_CORRECTED: "Giáo viên sửa",
    ATT_SRC_TEACHER_CONFIRMED: "Giáo viên xác nhận", ATT_SRC_AUTO_FINALIZED: "Tự chốt khi kết thúc",
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
class AttendanceEvidence:
    """One sighting that supports a check-in, with its provenance (session_runtime evidence ledger).
    ``key`` groups repeated sightings of the same binding; revoked evidence is kept, never deleted."""
    evidence_id: str
    key: str
    student_id: int
    class_session_id: Optional[int]
    class_id: Optional[int]
    track_id: Optional[int]
    binding_id: Optional[str]
    identity_source: Optional[str]          # AUTO_FACE / AUTO_RECOVERED / TEACHER_TAG / TEACHER_CORRECTED
    attendance_source: str                  # ATT_SRC_AUTO_FACE / ATT_SRC_TEACHER_CONFIRMED
    first_seen_at: float                    # the person was in the room from (lateness is decided on this)
    confirmed_at: float                     # identity confirmed (evidence recorded)
    last_seen_at: float
    late: bool
    revoked_at: Optional[float] = None
    revoked_reason: Optional[str] = None
    revoked_by: Optional[int] = None

    def row(self) -> dict:
        return {"evidence_id": self.evidence_id, "class_session_id": self.class_session_id, "class_id": self.class_id,
                "student_id": self.student_id, "track_id": self.track_id, "binding_id": self.binding_id,
                "identity_source": self.identity_source, "attendance_source": self.attendance_source,
                "first_seen_at": self.first_seen_at, "confirmed_at": self.confirmed_at,
                "last_seen_at": self.last_seen_at, "late": self.late, "revoked_at": self.revoked_at,
                "revoked_reason": self.revoked_reason, "revoked_by": self.revoked_by}


@dataclass
class StudentRuntimeState:
    student_id: int
    display_name: str = ""
    class_id: Optional[int] = None
    roll: Optional[str] = None

    identity_status: str = ID_NOT_SEEN
    identity_confidence: Optional[float] = None
    active_track_id: Optional[int] = None
    identity_source: Optional[str] = None       # SRC_* of the current / last binding
    identity_review: bool = False               # contradicting evidence: teacher must resolve

    connection_status: str = CONN_NOT_APPLICABLE
    attendance_status: str = ATT_NOT_SEEN
    attendance_source: Optional[str] = None
    attendance_locked: bool = False             # teacher override: never auto-overwritten this session
    visibility_status: str = VIS_NOT_SEEN

    focus_state: str = FOCUS_UNKNOWN
    focus_score: Optional[float] = None  # None until the student is measured

    seat_row: Optional[int] = None
    seat_col: Optional[int] = None

    first_seen_at: Optional[float] = None
    last_seen_at: Optional[float] = None
    checkin_at: Optional[float] = None
    checkin_track_id: Optional[int] = None     # person track of the check-in sighting (server-side only)
    finalized_absent: bool = False             # auto-finalised ABSENT (a later sighting is LATE)
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
    # Context reading of the behaviour in force (focusguard.policies); None
    # when no sustained behaviour. Observed focus_state is never replaced.
    interpretation: Optional[dict] = None

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
            "identity_source": self.identity_source,
            "identity_review": self.identity_review,
            "attendance_source": self.attendance_source,
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
                           "confidence": ep.get("confidence"),
                           "alert": (ep.get("interpretation") or {}).get("alert", True),
                           "interpreted_state": (ep.get("interpretation") or {}).get("interpreted_state"),
                           "penalty_multiplier": (ep.get("interpretation") or {}).get("penalty_multiplier", 1.0),
                           "severity": (ep.get("interpretation") or {}).get("severity", "normal")}
                          for k, ep in sorted(self.active_episodes.items(),
                                              key=lambda kv: kv[1].get("start_time") or 0)],
            "interpretation": self.interpretation,
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
