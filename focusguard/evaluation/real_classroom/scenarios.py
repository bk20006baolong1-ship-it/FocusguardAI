"""Scenario pack for real-classroom evaluation (BTC feedback A + B).

Each scenario is a short, scripted part of a consented evaluation session. The
operator starts and stops it (web page or field tool); the system records only
the time window (``evaluation_scenarios`` table) so metrics can be broken down
per scenario. The scenario script tells volunteers what to do and tells the
observer which ground-truth labels to expect. The expected labels are a HINT for
the observer, never a substitute for what the observer actually saw.

Activity context is chosen by the teacher/operator (here: suggested per
scenario); it is never inferred by the AI.
"""
from typing import Dict, List, Optional

from ...policies.contexts import (GROUP_DISCUSSION, LECTURE, LOOKING_AT_BOARD, TRANSITION,
                                  WRITING as CTX_WRITING)
from .models import (AWAY, DROWSY, FOCUSED, GROUP_DISCUSSION as GT_GROUP, LOOKING_AT_BOARD as GT_BOARD,
                     PHONE_DISTRACTION, UNKNOWN, WRITING as GT_WRITING)

SCENARIO_PACK_VERSION = "btc-2026.1"

# What a scenario measures (keys of the metrics report)
M_FP_RATE = "false_positives_per_hour"
M_CONTEXT = "context_aware_false_positives"
M_PHONE = "PHONE precision/recall"
M_DROWSY = "DROWSY precision/recall"
M_AWAY = "AWAY precision/recall"
M_IDENTITY = "identity accuracy / switches"
M_STRANGER = "stranger false accept"
M_RECOVERY = "identity recovery"
M_UNKNOWN = "unknown rate"
M_HEAD_POSE = "head-pose reference"
M_PERF = "FPS / latency"


def _s(sid, title, group, context, minutes, participants, steps, observer, expected, measures,
       identity_sheet=False):
    return {"scenario_id": sid, "title": title, "btc_group": group, "context": context,
            "suggested_minutes": minutes, "participants": participants, "steps": steps,
            "observer": observer, "expected_labels": expected, "measures": measures,
            "identity_sheet": identity_sheet}


SCENARIOS: List[dict] = [
    _s("01_lecture", "Nghe giảng", "A", LECTURE, 3, "cả nhóm",
       ["Tất cả ngồi đúng chỗ, nhìn về phía giáo viên/bảng như giờ học bình thường.",
        "Không ai dùng điện thoại hay rời chỗ."],
       "Gán FOCUSED cho từng học sinh; đoạn nào không chắc thì UNKNOWN.",
       [FOCUSED], [M_FP_RATE]),
    _s("02_writing", "Chép bài", "A", CTX_WRITING, 3, "cả nhóm",
       ["Tất cả cúi xuống vở và chép bài trong 2–3 phút.", "Thỉnh thoảng ngẩng lên nhìn bảng rồi chép tiếp."],
       "Gán WRITING khi cúi viết, LOOKING_AT_BOARD khi ngẩng nhìn bảng.",
       [GT_WRITING, GT_BOARD], [M_FP_RATE, M_CONTEXT, M_HEAD_POSE]),
    _s("03_looking_at_board", "Nhìn bảng", "A", LOOKING_AT_BOARD, 2, "cả nhóm",
       ["Giáo viên chỉ vào các phần khác nhau trên bảng; học sinh nhìn theo.",
        "20 giây đầu cả nhóm cùng nhìn thẳng lên bảng (dùng để hiệu chỉnh hướng đầu)."],
       "Gán LOOKING_AT_BOARD.", [GT_BOARD], [M_FP_RATE, M_CONTEXT, M_HEAD_POSE]),
    _s("04_group_discussion", "Thảo luận nhóm", "A", GROUP_DISCUSSION, 4, "cả nhóm",
       ["Chia nhóm 2–4 em, quay sang nhau thảo luận.", "Một vài em đổi chỗ ngắn (dưới 10 giây) rồi về chỗ."],
       "Gán GROUP_DISCUSSION; em nào rời khỏi khung hình lâu thì AWAY.",
       [GT_GROUP], [M_FP_RATE, M_CONTEXT]),
    _s("05_phone", "Dùng điện thoại", "A", LECTURE, 3, "2–3 em",
       ["Trong lúc nghe giảng, 2–3 em lần lượt dùng điện thoại 10–30 giây (để trên bàn hoặc cầm tay).",
        "Các em còn lại nghe giảng bình thường."],
       "Gán PHONE_DISTRACTION đúng khoảng thời gian em đó dùng điện thoại; các em khác FOCUSED.",
       [PHONE_DISTRACTION, FOCUSED], [M_PHONE, M_FP_RATE]),
    _s("06_drowsy", "Giả lập buồn ngủ", "A", LECTURE, 2, "1–2 em",
       ["1–2 em nhắm mắt 5–15 giây, gục nhẹ đầu, rồi tỉnh lại.", "Các em khác chớp mắt bình thường."],
       "Gán DROWSY khi mắt nhắm liên tục; chớp mắt bình thường vẫn là FOCUSED.",
       [DROWSY, FOCUSED], [M_DROWSY, M_FP_RATE]),
    _s("07_away", "Rời chỗ", "A", LECTURE, 2, "1–2 em",
       ["1–2 em đứng dậy, ra khỏi khung hình hơn 10 giây, sau đó quay lại đúng chỗ."],
       "Gán AWAY từ lúc em ra khỏi khung hình tới lúc ngồi lại.",
       [AWAY, FOCUSED], [M_AWAY, M_RECOVERY], identity_sheet=True),
    _s("08_crossing", "Hai học sinh đi cắt nhau", "B", TRANSITION, 2, "2 em",
       ["Hai em đi cắt ngang nhau trước camera 3–5 lần, chậm rồi nhanh, rồi về chỗ."],
       "Ghi bảng danh tính theo số track hiện trên màn hình xem trước (track_id → student_id).",
       [UNKNOWN], [M_IDENTITY, M_RECOVERY], identity_sheet=True),
    _s("09_stranger", "Người lạ đi qua", "B", TRANSITION, 2, "1 người chưa đăng ký",
       ["Một người KHÔNG đăng ký khuôn mặt (đã đồng ý xuất hiện) đi qua và đứng trong lớp 10–20 giây."],
       "Bảng danh tính: track của người lạ ghi STRANGER. Hệ thống không bao giờ được gán thành học sinh.",
       [UNKNOWN], [M_STRANGER, M_IDENTITY], identity_sheet=True),
    _s("10_face_occlusion", "Che mặt", "B", LECTURE, 2, "2–3 em",
       ["2–3 em che mặt bằng tay hoặc khẩu trang 5–10 giây, rồi bỏ ra."],
       "Gán FOCUSED nếu em vẫn nghe giảng; bảng danh tính cho các track trong lúc che.",
       [FOCUSED, UNKNOWN], [M_IDENTITY, M_UNKNOWN, M_FP_RATE], identity_sheet=True),
    _s("11_phone_occlusion", "Điện thoại che mặt", "B", LECTURE, 2, "1–2 em",
       ["1–2 em giơ điện thoại lên trước mặt (che một phần khuôn mặt) 10–20 giây."],
       "Gán PHONE_DISTRACTION; bảng danh tính cho track của em đó.",
       [PHONE_DISTRACTION], [M_PHONE, M_IDENTITY, M_UNKNOWN], identity_sheet=True),
    _s("12_camera_angle_change", "Đổi góc camera", "B", LECTURE, 3, "cả nhóm",
       ["Dừng 30 giây, đổi vị trí hoặc góc camera (ghi lại góc mới vào ghi chú), rồi nghe giảng tiếp.",
        "Sau khi đổi góc: 20 giây nhìn bảng, 20 giây cúi viết."],
       "Gán FOCUSED / LOOKING_AT_BOARD / WRITING như thực tế; bảng danh tính trước và sau khi đổi góc.",
       [FOCUSED, GT_BOARD, GT_WRITING], [M_IDENTITY, M_HEAD_POSE, M_FP_RATE], identity_sheet=True),
    _s("13_track_replacement", "Đổi track (che khuất ngắn)", "B", LECTURE, 2, "2 em",
       ["Một em đi ngang che khuất bạn ngồi phía sau 2–5 giây, lặp lại 3 lần.",
        "Mục đích: tracker đổi số track, hệ thống phải nhận lại đúng học sinh."],
       "Bảng danh tính cho các track mới xuất hiện sau mỗi lần che.",
       [FOCUSED, UNKNOWN], [M_RECOVERY, M_IDENTITY], identity_sheet=True),
    _s("14_leave_and_return", "Ra ngoài rồi quay lại", "B", LECTURE, 3, "1–2 em",
       ["1–2 em ra khỏi lớp 30–60 giây rồi quay lại, có thể ngồi sai chỗ cũ."],
       "Gán AWAY lúc vắng mặt; bảng danh tính lúc quay lại.",
       [AWAY, FOCUSED], [M_AWAY, M_RECOVERY], identity_sheet=True),
    _s("15_crowded_classroom", "Lớp đông, hoạt động hỗn hợp", "B", LECTURE, 5, "toàn bộ người tham gia",
       ["Toàn bộ người tham gia ngồi kín khung hình, học bình thường 5 phút: nghe giảng, chép bài, 1 em dùng "
        "điện thoại, 1 em rời chỗ.", "Giáo viên đổi hoạt động trên công cụ khi lớp đổi hoạt động."],
       "Gán nhãn đầy đủ cho mọi học sinh trong khung hình.",
       [FOCUSED, GT_WRITING, PHONE_DISTRACTION, AWAY], [M_PERF, M_IDENTITY, M_FP_RATE, M_PHONE, M_AWAY],
       identity_sheet=True),
]
SCENARIO_IDS = tuple(s["scenario_id"] for s in SCENARIOS)
_BY_ID: Dict[str, dict] = {s["scenario_id"]: s for s in SCENARIOS}


def get_scenario(scenario_id) -> Optional[dict]:
    return _BY_ID.get(scenario_id)


def public_pack() -> dict:
    return {"version": SCENARIO_PACK_VERSION, "scenarios": [dict(s) for s in SCENARIOS]}


def scenario_at(runs: List[dict], t: float) -> Optional[str]:
    """Scenario whose window contains ``t`` (latest started wins)."""
    hit = None
    for r in runs:
        a, b = r.get("started_at"), r.get("ended_at")
        if a is not None and a <= t and (b is None or t < b):
            if hit is None or a >= hit[0]:
                hit = (a, r.get("scenario_id"))
    return hit[1] if hit else None
