"""Server-authoritative realtime helpers (room naming + payload policy).

Room layout (never global rooms like ``teachers`` / ``students`` or rooms keyed
by a user-supplied display name):

    class:{class_id}:teachers   teachers of that class only
    class:{class_id}:students   generic class notices (start/end), no numbers
    student:{student_id}        that student's own live data
"""

# Fields a browser is never allowed to set: identity, AI state, scores and
# attendance are produced by the server-side pipeline only.
AUTHORITATIVE_FIELDS = frozenset({
    "name", "student_id", "user_id", "class_id", "state", "focus_state", "focus_score",
    "distractions", "emotion", "early_warning", "attendance", "attendance_status",
    "identity_status", "visibility_status", "online", "frame",
})


def teacher_room(class_id) -> str:
    return f"class:{int(class_id)}:teachers"


def class_students_room(class_id) -> str:
    return f"class:{int(class_id)}:students"


def student_room(student_id) -> str:
    return f"student:{int(student_id)}"


def rejected_fields(payload) -> list:
    """Authoritative fields present in a client payload (to be rejected)."""
    if not isinstance(payload, dict):
        return []
    return sorted(k for k in payload.keys() if k in AUTHORITATIVE_FIELDS)


def student_self_view(view: dict) -> dict:
    """What a student (or parent) may receive live: own data, no class numbers."""
    if not view:
        return {}
    keep = ("student_id", "name", "focus_state", "focus_score", "average_focus_score",
            "attendance_status", "visibility_status", "connection_status",
            "identity_status", "event_counts", "distractions", "away_count",
            "away_seconds", "visible_seconds", "active_behaviors", "labels", "state",
            "attendance", "checkin_offset_seconds")
    return {k: view.get(k) for k in keep}
