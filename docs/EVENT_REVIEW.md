# Giáo viên xem lại sự kiện (Teacher Event Review)

Góp ý của BTC: khi AI báo sai, giáo viên phải xem lại được, xác nhận đúng/sai/không chắc, có lịch sử, và biết nhóm
học sinh nào có nhiều sự kiện cần kiểm tra. Nguyên tắc: **tín hiệu AI không phải kết luận sư phạm; giáo viên là người
quyết định cuối cùng.**

## 1. Trạng thái xem lại

| Trạng thái | Ý nghĩa | Nút trên giao diện |
|---|---|---|
| `UNREVIEWED` | Chưa xem lại (mặc định của mọi sự kiện AI) | — |
| `CONFIRMED` | Giáo viên xác nhận đúng | **Xác nhận đúng** |
| `FALSE_POSITIVE` | Giáo viên xác nhận báo nhầm | **Báo nhầm** |
| `UNCERTAIN` | Không chắc | **Không chắc** |

Mỗi lần xem lại tạo một dòng `event_reviews`:
`event_review_id, event_id, student_id, class_session_id, class_id, verdict, teacher_note, reviewed_by, reviewed_at`.
Lần sau ghi đè trạng thái hiện tại nhưng **lịch sử được giữ**. Sự kiện gốc (`focus_events`) **không bao giờ bị sửa**:
loại tín hiệu, thời gian, độ tin cậy và cách hiểu theo hoạt động luôn là đầu ra thô của AI.

`reviewed_by` luôn là tài khoản đang đăng nhập; trường `reviewed_by`, `student_id`… gửi từ trình duyệt bị bỏ qua.

## 2. Quyền

| Ai | Xem danh sách sự kiện | Xem lại / ghi chú | Xem đoạn minh chứng | Xóa minh chứng |
|---|---|---|---|---|
| Giáo viên của lớp | Có | Có | Có (nếu tồn tại) | Có |
| Giáo viên lớp khác | Không | Không | Không | Không |
| Học sinh, phụ huynh | Không | Không | Không | Không |
| Quản trị viên | Không (không thay giáo viên) | Không | Không | Có (trang Quyền riêng tư) |

Sự kiện của phiên tự học cá nhân không có lớp nên không thuộc trang này. Mọi lần xem lại ghi nhật ký `EVENT_REVIEWED`.

## 3. Giao diện

* **Giáo viên > Sự kiện**: lọc theo lớp (chọn ở thanh trên), học sinh, ngày, loại tín hiệu, trạng thái xem lại,
  hoạt động. Bảng hiển thị học sinh, tín hiệu, thời lượng, độ tin cậy, hoạt động, thời điểm, trạng thái xem lại.
  Nút **Xem lại** mở hộp chi tiết: thông tin sự kiện, cách hiểu theo hoạt động, đoạn minh chứng (nếu có), ô **Ghi chú**,
  ba nút **Xác nhận đúng / Báo nhầm / Không chắc** và lịch sử. Không dùng hộp thoại của trình duyệt.
* **Giám sát > Cần giáo viên xem lại**: học sinh có sự kiện chưa được xác nhận, sắp xếp chỉ theo
  `unreviewed_event_count`, `recent_event_count` (7 ngày, `AnalyticsConfig.review_recent_days`), `total_event_duration`. Lời văn: "Có N sự kiện chưa được
  xác nhận". Không có "xếp hạng", "kém nhất", "mất tập trung nhất".
* **Phân tích > Sự kiện AI và sự kiện giáo viên xác nhận**: tách số sự kiện AI và số sự kiện giáo viên xác nhận đúng.
* **Kết quả xem lại được dùng**: biểu đồ "Hành vi được ghi nhận", số "Sự kiện đã ghi nhận" (Phân tích) và số sự kiện của
  từng buổi (Lịch sử buổi học) **không tính** các sự kiện giáo viên xác nhận báo nhầm và ghi rõ đã loại bao nhiêu; bảng
  tín hiệu trong tổng kết buổi hiện hoạt động lớp và trạng thái xem lại của từng sự kiện. Sự kiện AI gốc vẫn giữ nguyên.
  Điểm tập trung đã chốt của buổi cũ không được tính lại.
* **Phân tích > Điểm trung bình hôm nay / Có thể cần giáo viên trao đổi thêm**: liệt kê theo **tên**, không đánh số thứ
  hạng; điểm là ước lượng heuristic. Ngưỡng của danh sách thứ hai lấy từ `AnalyticsConfig` (mặc định dưới 70 trong ít
  nhất 2 buổi, `FG_WATCHLIST_SCORE_BELOW`).

## 4. Chỉ số phản hồi của giáo viên

`focusguard/review.py::feedback_metrics`, cho từng PHONE / DROWSY / HEAD_AWAY / AWAY:

* `confirmed_rate`, `false_positive_rate`, `uncertain_rate` = số xác nhận / số đã xem lại;
* `review_coverage` = số đã xem lại / số sự kiện AI.

Tỉ lệ chỉ hiện khi đã xem lại ≥ 5 sự kiện của loại đó, ngược lại là `NOT_ENOUGH_DATA` ("Chưa đủ dữ liệu"). Tỉ lệ báo
nhầm theo xác nhận của giáo viên là đầu vào cho hiệu chỉnh hệ số phạt (`scripts/calibrate_behavior.py
--evaluation-session`).

## 5. Đoạn minh chứng

Mặc định **tắt**: chỉ có thông tin sự kiện. Khi nhà trường bật Evidence Mode và học sinh có đồng ý phạm vi
*Đoạn minh chứng*, hộp chi tiết hiện **Xem đoạn minh chứng** (không tự phát), thời lượng, thời điểm, thời gian tự
xóa và nút **Xóa ngay**. Chi tiết ở `docs/CONSENT_AND_RETENTION.md`.

## 6. API

| Phương thức | Đường dẫn | Ghi chú |
|---|---|---|
| GET | `/api/teacher/events?class_id&student_id&date&type&review_status&context` | danh sách + trạng thái |
| GET | `/api/teacher/events/<event_id>` | chi tiết + lịch sử + minh chứng |
| POST | `/api/teacher/events/<event_id>/review` `{verdict, note}` | `verdict` ∈ CONFIRMED / FALSE_POSITIVE / UNCERTAIN |
| GET | `/api/teacher/review_queue?class_id` | danh sách cần xem lại |
| GET | `/api/teacher/review_metrics?class_id` | chỉ số phản hồi |
| POST | `/api/teacher/activity_context` `{context}` | chọn hoạt động lớp |

Kiểm thử: `tests/test_teacher_review.py`, `tests/test_security_btc.py`, `tests/test_ui_contract.py`.
