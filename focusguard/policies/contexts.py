"""Activity contexts a teacher or tester can select for a class session.

Phase 1 rule: the context is always chosen by a person. FocusGuard never
infers the activity from the camera.
"""

LECTURE = "LECTURE"
WRITING = "WRITING"
LOOKING_AT_BOARD = "LOOKING_AT_BOARD"
GROUP_DISCUSSION = "GROUP_DISCUSSION"
SELF_STUDY = "SELF_STUDY"
EXAM = "EXAM"
TRANSITION = "TRANSITION"

ACTIVITY_CONTEXTS = (LECTURE, WRITING, LOOKING_AT_BOARD, GROUP_DISCUSSION, SELF_STUDY, EXAM, TRANSITION)

CONTEXT_LABELS_VI = {
    LECTURE: "Nghe giảng",
    WRITING: "Chép bài / viết",
    LOOKING_AT_BOARD: "Nhìn bảng",
    GROUP_DISCUSSION: "Thảo luận nhóm",
    SELF_STUDY: "Tự học",
    EXAM: "Kiểm tra",
    TRANSITION: "Chuyển hoạt động",
}

CONTEXT_HELP_VI = {
    LECTURE: "Giáo viên giảng bài, học sinh hướng lên bảng/giáo viên.",
    WRITING: "Học sinh cúi xuống vở để viết hoặc chép bài.",
    LOOKING_AT_BOARD: "Học sinh nhìn lên bảng, có thể lệch so với hướng camera.",
    GROUP_DISCUSSION: "Học sinh quay sang bạn để trao đổi, có thể di chuyển chỗ ngồi.",
    SELF_STUDY: "Học sinh tự đọc, tự làm bài.",
    EXAM: "Làm bài kiểm tra.",
    TRANSITION: "Chuyển giữa các hoạt động, học sinh có thể đứng dậy, đi lại.",
}


def is_valid_context(value) -> bool:
    return value in ACTIVITY_CONTEXTS


def normalize_context(value):
    """Return a valid context, or None for "no context selected" (legacy behaviour)."""
    if value is None or value == "":
        return None
    v = str(value).strip().upper()
    if v not in ACTIVITY_CONTEXTS:
        raise ValueError(f"unknown activity context: {value!r}")
    return v
