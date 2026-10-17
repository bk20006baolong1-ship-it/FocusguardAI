"""Evaluation session metadata, ground-truth labels and package format."""
import math
from typing import Optional

from ...policies.contexts import ACTIVITY_CONTEXTS, CONTEXT_LABELS_VI  # noqa: F401

PACKAGE_FORMAT = "focusguard-evaluation-package/1"

REAL = "REAL"            # consented recording of a real classroom
REPLAY = "REPLAY"        # generated replay fixture (cloud CI)
SYNTHETIC = "SYNTHETIC"  # generated signals (cloud CI)
DATASET_KINDS = (REAL, REPLAY, SYNTHETIC)

FOCUSED = "FOCUSED"
WRITING = "WRITING"
LOOKING_AT_BOARD = "LOOKING_AT_BOARD"
GROUP_DISCUSSION = "GROUP_DISCUSSION"
PHONE_DISTRACTION = "PHONE_DISTRACTION"
DROWSY = "DROWSY"
AWAY = "AWAY"
OFF_TASK_LOOKING_AWAY = "OFF_TASK_LOOKING_AWAY"
UNKNOWN = "UNKNOWN"
GT_LABELS = (FOCUSED, WRITING, LOOKING_AT_BOARD, GROUP_DISCUSSION, PHONE_DISTRACTION, DROWSY, AWAY,
             OFF_TASK_LOOKING_AWAY, UNKNOWN)
GT_LABELS_VI = {
    FOCUSED: "Tập trung", WRITING: "Đang viết / chép bài", LOOKING_AT_BOARD: "Nhìn bảng",
    GROUP_DISCUSSION: "Thảo luận nhóm", PHONE_DISTRACTION: "Dùng điện thoại (không phục vụ bài)",
    DROWSY: "Buồn ngủ", AWAY: "Rời chỗ", OFF_TASK_LOOKING_AWAY: "Quay đi, không theo bài",
    UNKNOWN: "Không rõ (không tính)",
}
# Which AI behaviour a ground-truth label is the true positive for.
LABEL_TO_BEHAVIOR = {PHONE_DISTRACTION: "PHONE", DROWSY: "DROWSY", AWAY: "AWAY", OFF_TASK_LOOKING_AWAY: "HEAD_AWAY"}
# Labels that mean "on task": an AI distraction signal here is a false positive.
ON_TASK_LABELS = (FOCUSED, WRITING, LOOKING_AT_BOARD, GROUP_DISCUSSION)

METADATA_FIELDS = ("evaluation_session_id", "class_id", "camera_position", "camera_angle", "resolution",
                   "fps_target", "device", "student_count", "lighting_condition", "activity_context",
                   "started_at", "ended_at", "notes", "consent_verified")
CAMERA_POSITIONS = ("FRONT_ABOVE_BOARD", "FRONT_CORNER", "SIDE_WALL", "BACK_CORNER", "TEACHER_DESK",
                    "LAPTOP_WEBCAM", "OTHER")
LIGHTING = ("DAYLIGHT", "MIXED", "ARTIFICIAL", "LOW_LIGHT", "BACKLIT", "OTHER")


def _text(v, n=120) -> Optional[str]:
    v = (str(v).strip() if v is not None else "")[:n]
    return v or None


def clean_metadata(data: dict) -> dict:
    """Validate an evaluation session form. Raises ValueError with a Vietnamese message."""
    d = data or {}
    out = {
        "camera_position": _text(d.get("camera_position"), 40),
        "camera_angle": _text(d.get("camera_angle"), 60),
        "resolution": _text(d.get("resolution"), 20),
        "device": _text(d.get("device"), 120),
        "lighting_condition": _text(d.get("lighting_condition"), 40),
        "notes": _text(d.get("notes"), 1000),
        "consent_verified": bool(d.get("consent_verified") is True or d.get("consent_verified") == "true"),
        "dataset_kind": (d.get("dataset_kind") or REAL),
    }
    if out["camera_position"] and out["camera_position"] not in CAMERA_POSITIONS:
        raise ValueError("Vị trí camera không hợp lệ.")
    if out["lighting_condition"] and out["lighting_condition"] not in LIGHTING:
        raise ValueError("Điều kiện ánh sáng không hợp lệ.")
    if out["dataset_kind"] not in DATASET_KINDS:
        raise ValueError("Loại dữ liệu không hợp lệ.")
    if out["resolution"] and not _resolution_ok(out["resolution"]):
        raise ValueError("Độ phân giải phải có dạng 1280x720.")
    for key, lo, hi in (("fps_target", 0.5, 120), ("student_count", 1, 80)):
        raw = d.get(key)
        if raw in (None, ""):
            out[key] = None
            continue
        try:
            v = float(raw) if key == "fps_target" else int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Giá trị {key} không hợp lệ.")
        if not lo <= v <= hi:
            raise ValueError(f"Giá trị {key} nằm ngoài khoảng cho phép.")
        out[key] = v
    ctx = d.get("activity_context")
    if ctx not in (None, ""):
        if ctx not in ACTIVITY_CONTEXTS:
            raise ValueError("Hoạt động không hợp lệ.")
        out["activity_context"] = ctx
    else:
        out["activity_context"] = None
    return out


def _resolution_ok(v: str) -> bool:
    parts = v.lower().split("x")
    return len(parts) == 2 and all(p.isdigit() and 0 < int(p) <= 10000 for p in parts)


def clean_annotation(data: dict, session: dict, now: float) -> dict:
    d = data or {}
    label = d.get("label")
    if label not in GT_LABELS:
        raise ValueError("Nhãn không hợp lệ.")
    try:
        sid = int(d.get("student_id"))
        start = float(d.get("started_at"))
        end = float(d.get("ended_at"))
    except (TypeError, ValueError):
        raise ValueError("Thiếu học sinh hoặc thời gian của nhãn.")
    if not (math.isfinite(start) and math.isfinite(end)):
        raise ValueError("Thời gian của nhãn không hợp lệ.")
    if end <= start:
        raise ValueError("Thời điểm kết thúc phải sau thời điểm bắt đầu.")
    s0 = session.get("started_at")
    s1 = session.get("ended_at") or now
    if s0 is not None and (start < s0 - 1 or end > s1 + 1):
        raise ValueError("Nhãn phải nằm trong thời gian của buổi đánh giá.")
    if end - start > 4 * 3600:
        raise ValueError("Một nhãn không được dài quá 4 giờ.")
    return {"student_id": sid, "label": label, "started_at": start, "ended_at": end,
            "note": _text(d.get("note"), 500)}
