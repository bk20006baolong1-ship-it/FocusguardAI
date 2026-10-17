"""The ONE place privacy wording is written (UI pages, docs export, consent dialog).

Templates and JS receive these strings from the server; they are not copied
into several files.
"""
from .settings import PrivacySettings

POLICY_VERSION = "privacy-2026.10-1"


def _hours(h):
    return f"{int(h)} giờ" if float(h).is_integer() else f"{h:g} giờ"


def _days(d):
    return f"{int(d)} ngày" if float(d).is_integer() else f"{d:g} ngày"


def privacy_notice(s: PrivacySettings) -> str:
    """One line shown in the sidebar / camera cards."""
    if s.evidence_enabled:
        return ("Mặc định FocusGuard không lưu video. Chế độ minh chứng đang bật: chỉ lưu đoạn ngắn quanh sự kiện "
                f"của học sinh đã đồng ý, chỉ giáo viên của lớp xem được, tự xóa sau {_hours(s.evidence_retention_hours)}.")
    return "Hình ảnh camera được phân tích trực tiếp, không ghi hình và không lưu ảnh."


def policy_texts(s: PrivacySettings) -> dict:
    evidence = (
        "Mặc định FocusGuard không lưu video. Evidence Mode (chế độ minh chứng) là tùy chọn, chỉ lưu đoạn minh chứng "
        f"ngắn quanh sự kiện ({s.evidence_pre_seconds:g} giây trước, {s.evidence_post_seconds:g} giây sau) khi được "
        "phép, giới hạn quyền truy cập và tự động xóa theo retention policy "
        f"({_hours(s.evidence_retention_hours)}). Không ghi video liên tục; khung hình chỉ nằm tạm trong bộ nhớ RAM.")
    return {
        "policy_version": POLICY_VERSION,
        "title": "Dữ liệu và quyền riêng tư",
        "summary": ("FocusGuard chỉ phát hiện tín hiệu có thể quan sát (điện thoại, nhắm mắt kéo dài, quay đi, rời chỗ). "
                    "Tín hiệu AI không phải kết luận sư phạm; giáo viên là người xem lại và quyết định."),
        "face": ("Chỉ lưu vector đặc trưng khuôn mặt (không lưu ảnh) và chỉ khi học sinh/phụ huynh đã đồng ý. "
                 "Vector không bao giờ được gửi tới trình duyệt, ghi vào nhật ký hay xuất ra file."),
        "evidence": evidence,
        "evidence_state": "Đang bật" if s.evidence_enabled else "Đang tắt (chỉ lưu thông tin sự kiện)",
        "access": ("Giáo viên chỉ xem dữ liệu lớp mình. Học sinh chỉ xem dữ liệu của mình. Phụ huynh chỉ xem dữ liệu "
                   "của con và không xem được đoạn minh chứng. Mọi lần xem/xóa minh chứng đều được ghi nhật ký."),
        "rights": ("Phụ huynh/học sinh có thể thu hồi đồng ý bất cứ lúc nào: nhận diện khuôn mặt dừng ngay, vector "
                   "khuôn mặt bị xóa" + (", đoạn minh chứng liên quan bị xóa" if s.revoke_deletes_evidence else "") +
                   ". Dữ liệu học tập khác được giữ theo thời hạn lưu trữ."),
        "retention": [
            {"key": "FACE_EMBEDDING_RETENTION_DAYS", "label": "Vector khuôn mặt",
             "value": _days(s.face_embedding_retention_days) + " kể từ lần đăng ký"},
            {"key": "EVENT_METADATA_RETENTION_DAYS", "label": "Sự kiện AI, điểm và xác nhận của giáo viên",
             "value": _days(s.event_metadata_retention_days)},
            {"key": "EVENT_EVIDENCE_RETENTION_HOURS", "label": "Đoạn minh chứng (nếu bật)",
             "value": _hours(s.evidence_retention_hours)},
            {"key": "EVALUATION_DATA_RETENTION_DAYS", "label": "Dữ liệu đánh giá lớp học thật",
             "value": _days(s.evaluation_data_retention_days)},
        ],
        "consent_scopes": [
            {"key": "scope_face_recognition", "label": "Nhận diện khuôn mặt",
             "help": "Cho phép lưu vector đặc trưng để camera lớp học nhận ra học sinh."},
            {"key": "scope_event_evidence", "label": "Đoạn minh chứng sự kiện",
             "help": "Cho phép lưu đoạn ngắn quanh sự kiện khi nhà trường bật chế độ minh chứng."},
            {"key": "scope_evaluation", "label": "Tham gia đánh giá thử nghiệm",
             "help": "Cho phép dùng dữ liệu (không có ảnh) trong buổi đánh giá độ chính xác có giám sát."},
        ],
        "notice": privacy_notice(s),
    }
