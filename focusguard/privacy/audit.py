"""Audit trail for privacy-relevant actions.

Metadata is sanitised: biometric vectors, images, passwords and secrets are
dropped before anything is written, whatever the caller passes in.
"""
import re
import time

from ..cameras.redact import redact

CONSENT_GRANTED = "CONSENT_GRANTED"
CONSENT_REVOKED = "CONSENT_REVOKED"
FACE_ENROLLED = "FACE_ENROLLED"
FACE_DELETED = "FACE_DELETED"
EVIDENCE_CREATED = "EVIDENCE_CREATED"
EVIDENCE_VIEWED = "EVIDENCE_VIEWED"
EVIDENCE_DELETED = "EVIDENCE_DELETED"
POLICY_CHANGED = "POLICY_CHANGED"
EVENT_REVIEWED = "EVENT_REVIEWED"
DATA_EXPORTED = "DATA_EXPORTED"
RETENTION_RUN = "RETENTION_RUN"
ACTIVITY_CONTEXT_CHANGED = "ACTIVITY_CONTEXT_CHANGED"
CAMERA_ADDED = "CAMERA_ADDED"
CAMERA_UPDATED = "CAMERA_UPDATED"
CAMERA_DELETED = "CAMERA_DELETED"
CAMERA_DEFAULT_CHANGED = "CAMERA_DEFAULT_CHANGED"
CAMERA_ASSIGNED_TO_CLASS = "CAMERA_ASSIGNED_TO_CLASS"
CAMERA_CREDENTIAL_UPDATED = "CAMERA_CREDENTIAL_UPDATED"
CAMERA_CONNECTION_TESTED = "CAMERA_CONNECTION_TESTED"
IDENTITY_TEACHER_TAGGED = "IDENTITY_TEACHER_TAGGED"
IDENTITY_TEACHER_CORRECTED = "IDENTITY_TEACHER_CORRECTED"
IDENTITY_TAG_REMOVED = "IDENTITY_TAG_REMOVED"
IDENTITY_AUTO_CONFIRMED = "IDENTITY_AUTO_CONFIRMED"
IDENTITY_PROFILE_SAMPLE_ADDED = "IDENTITY_PROFILE_SAMPLE_ADDED"
IDENTITY_PROFILE_SAMPLE_REJECTED = "IDENTITY_PROFILE_SAMPLE_REJECTED"
FACE_PROFILE_READY = "FACE_PROFILE_READY"
IDENTITY_SESSION_RESTORED = "IDENTITY_SESSION_RESTORED"
ATTENDANCE_TEACHER_CORRECTED = "ATTENDANCE_TEACHER_CORRECTED"
ACTIONS = (CONSENT_GRANTED, CONSENT_REVOKED, FACE_ENROLLED, FACE_DELETED, EVIDENCE_CREATED, EVIDENCE_VIEWED,
           EVIDENCE_DELETED, POLICY_CHANGED, EVENT_REVIEWED, DATA_EXPORTED, RETENTION_RUN, ACTIVITY_CONTEXT_CHANGED,
           CAMERA_ADDED, CAMERA_UPDATED, CAMERA_DELETED, CAMERA_DEFAULT_CHANGED, CAMERA_ASSIGNED_TO_CLASS,
           CAMERA_CREDENTIAL_UPDATED, CAMERA_CONNECTION_TESTED, IDENTITY_TEACHER_TAGGED, IDENTITY_TEACHER_CORRECTED,
           IDENTITY_TAG_REMOVED, IDENTITY_AUTO_CONFIRMED, IDENTITY_PROFILE_SAMPLE_ADDED,
           IDENTITY_PROFILE_SAMPLE_REJECTED, FACE_PROFILE_READY, ATTENDANCE_TEACHER_CORRECTED,
           IDENTITY_SESSION_RESTORED)

ACTION_LABELS_VI = {
    CONSENT_GRANTED: "Ghi nhận đồng ý", CONSENT_REVOKED: "Thu hồi đồng ý", FACE_ENROLLED: "Đăng ký khuôn mặt",
    FACE_DELETED: "Xóa dữ liệu khuôn mặt", EVIDENCE_CREATED: "Lưu đoạn minh chứng",
    EVIDENCE_VIEWED: "Xem đoạn minh chứng", EVIDENCE_DELETED: "Xóa đoạn minh chứng",
    POLICY_CHANGED: "Thay đổi chính sách", EVENT_REVIEWED: "Giáo viên xem lại sự kiện",
    DATA_EXPORTED: "Xuất dữ liệu", RETENTION_RUN: "Tự động xóa theo thời hạn",
    ACTIVITY_CONTEXT_CHANGED: "Đổi hoạt động lớp",
    CAMERA_ADDED: "Thêm camera", CAMERA_UPDATED: "Sửa camera", CAMERA_DELETED: "Xóa camera",
    CAMERA_DEFAULT_CHANGED: "Đổi camera mặc định", CAMERA_ASSIGNED_TO_CLASS: "Gán camera cho lớp",
    CAMERA_CREDENTIAL_UPDATED: "Cập nhật xác thực camera", CAMERA_CONNECTION_TESTED: "Kiểm tra kết nối camera",
    IDENTITY_TEACHER_TAGGED: "Giáo viên gắn tên học sinh", IDENTITY_TEACHER_CORRECTED: "Giáo viên sửa danh tính",
    IDENTITY_TAG_REMOVED: "Gỡ gắn tên", IDENTITY_AUTO_CONFIRMED: "AI xác nhận danh tính",
    IDENTITY_PROFILE_SAMPLE_ADDED: "Thêm mẫu nhận diện", IDENTITY_PROFILE_SAMPLE_REJECTED: "Loại mẫu nhận diện",
    FACE_PROFILE_READY: "Hồ sơ nhận diện sẵn sàng", ATTENDANCE_TEACHER_CORRECTED: "Giáo viên sửa điểm danh",
    IDENTITY_SESSION_RESTORED: "Khôi phục danh tính trong buổi (theo vị trí)",
}

_FORBIDDEN = ("embedding", "vector", "password", "secret", "token", "image", "frame", "jpeg", "photo", "biometric",
              "verification_code", "credential", "url")


def _forbidden_key(k) -> bool:
    k = str(k).lower()
    return any(f in k for f in _FORBIDDEN)


_DATA_URI_RX = re.compile(r"data:[a-z]+/[\w.+-]+;base64,", re.I)
_B64_RUN_RX = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")


def _looks_encoded(text: str) -> bool:
    if _DATA_URI_RX.search(text):
        return True
    for m in _B64_RUN_RX.finditer(text):       # real base64 mixes upper, lower case and digits
        run = m.group(0)
        if any(c.isupper() for c in run) and any(c.islower() for c in run) and any(c.isdigit() for c in run):
            return True
    return False


def sanitize(value, depth=0):
    """Remove anything that could carry biometric data or secrets.

    A key that names image/frame/embedding/secret data is dropped, except a plain
    integer count under it (e.g. ``frame_count``: a number cannot carry a face).
    String values that look like encoded data (data: URI, long base64) are
    replaced as well, whatever their key."""
    if depth > 4:
        return None
    if isinstance(value, dict):
        return {str(k): sanitize(v, depth + 1) for k, v in value.items()
                if not _forbidden_key(k) or (type(v) is int and str(k).lower().endswith("count"))}
    if isinstance(value, (list, tuple)):
        if len(value) > 8 and all(isinstance(x, (int, float)) for x in value):
            return f"<{len(value)} numbers removed>"
        return [sanitize(v, depth + 1) for v in list(value)[:50]]
    if isinstance(value, (bytes, bytearray)):
        return "<binary removed>"
    if isinstance(value, str):
        return "<encoded data removed>" if _looks_encoded(value) else redact(value)[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:200]


class AuditLog:
    def __init__(self, repo, clock=time.time):
        self.repo = repo
        self.clock = clock

    def record(self, action, actor=None, student_id=None, metadata=None):
        if action not in ACTIONS:
            raise ValueError(f"unknown audit action {action}")
        row = {
            "actor_id": getattr(actor, "user_id", None) if actor is not None and not isinstance(actor, int) else actor,
            "actor_role": getattr(actor, "role", None) if actor is not None and not isinstance(actor, int) else
            ("system" if actor is None else None),
            "student_id": None if student_id is None else int(student_id),
            "action": action,
            "timestamp": self.clock(),
            "metadata": sanitize(metadata or {}),
        }
        try:
            return self.repo.create_audit(row)
        except Exception as exc:
            # The protected action already happened; never lose the trace silently.
            print(f"[AUDIT] could not write {action}: {exc!r}")
            return None

    def list(self, student_id=None, action=None, limit=200):
        rows = self.repo.get_audit(student_id=student_id, action=action, limit=limit)
        for r in rows:
            r["action_label"] = ACTION_LABELS_VI.get(r.get("action"), r.get("action"))
        return rows
