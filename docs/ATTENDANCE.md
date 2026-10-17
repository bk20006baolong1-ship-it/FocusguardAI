# Điểm danh tự động theo danh sách lớp

Mã: `focusguard/session_runtime.py` (`_mark_seen`, `finalize_attendance`, `teacher_set_attendance`),
`focusguard/identity_routes.py` (`POST /api/teacher/attendance`).

## Trạng thái

| Trạng thái | Khi nào |
|---|---|
| `NOT_SEEN` (Chưa thấy) | đầu buổi, cho mọi học sinh trong danh sách lớp |
| `PRESENT` (Có mặt) | lần đầu được **xác nhận** trên camera (nhận diện hoặc giáo viên gắn tên) trong khung giờ |
| `LATE` (Đi muộn) | lần xác nhận đầu tiên sau `late_after_seconds` (`FG_LATE_AFTER_SECONDS`, mặc định 300 s) |
| `ABSENT` (Vắng mặt) | chưa từng được xác nhận khi chốt điểm danh |

Nguồn (`attendance_source`): `AUTO_FACE`, `TEACHER_CONFIRMED` (giáo viên gắn / xác nhận trên camera),
`TEACHER_CORRECTED` (giáo viên sửa), `AUTO_FINALIZED` (tự chốt vắng mặt). Giao diện hiện “GV sửa” cho `TEACHER_CORRECTED`.

## Không chốt vắng mặt quá sớm

* Mặc định điểm danh được chốt **khi kết thúc buổi**. Tùy chọn `FG_ATTENDANCE_FINALIZE_SECONDS`: chốt sớm, nhưng chỉ
  khi **không còn người chưa xác định** trong khung hình liên tục ≥ 5 giây và ≥ 10 khung hình (vài khung bỏ sót không đủ; mất tín hiệu
  đặt lại bộ đếm) — nếu còn,
  chờ giáo viên gắn tên.
* `LATE` không áp dụng trong lần **khởi tạo danh sách đầu buổi** (học sinh đang ngồi trong lớp khi giáo viên gắn tên);
  khởi tạo lại giữa giờ không miễn đi muộn.
* Ví dụ lớp 30, đã xác nhận 26, 2 người chưa xác định trong hình: hiển thị “Đã xác nhận: 26 · Chưa xác định: 2 ·
  Chưa thấy: 4”, không tuyên bố 4 em vắng.
* Học sinh bị tự chốt vắng mà sau đó được xác nhận → `LATE`.

## Bốn tình huống được phân biệt

| Tình huống | Điểm danh | Rời chỗ (AWAY) |
|---|---|---|
| camera mất tín hiệu | không đổi, không chốt vắng | không bao giờ |
| có người nhưng chưa xác định danh tính | học sinh có thể là người đó: không chốt vắng | đồng hồ rời chỗ **không** chạy (`IDENTITY_UNRESOLVED`) |
| học sinh được xác nhận trong hình | `PRESENT` / `LATE` | — |
| học sinh chưa từng được thấy | `NOT_SEEN` | — |

Danh tính đang xung đột (“Cần xem lại”) không làm học sinh nào “có mặt” nhầm. Một học sinh không thể có mặt trên hai
người cùng lúc.

## Sổ bằng chứng điểm danh (evidence ledger)

Mã: `SessionRuntime._mark_seen` / `_recompute_attendance` / `revoke_attendance_evidence`, bảng `attendance_evidence`.

* Mỗi lần học sinh được **xác nhận** (nhận diện khuôn mặt, giáo viên gắn tên, khôi phục danh tính trong buổi) là một
  **bằng chứng** gắn với ràng buộc danh tính đã dùng: `student_id`, `class_session_id`, `track_id`, `binding_id`,
  `identity_source`, `first_seen_at` (người đó có trong lớp từ lúc nào — track xuất hiện), `confirmed_at`,
  `last_seen_at`, `late`.
* Trạng thái điểm danh **được suy ra** từ bằng chứng còn hiệu lực **sớm nhất**: `PRESENT`, hoặc `LATE` nếu lần thấy đó
  sau `late_after_seconds` tính từ **đầu buổi** (quyết định một lần, lúc ghi bằng chứng). Không còn bằng chứng nào →
  `NOT_SEEN` (hoặc `ABSENT` nếu đã tự chốt vắng).
* Giáo viên sửa / gỡ tên → chỉ thu hồi bằng chứng **chứng minh được là phụ thuộc** ràng buộc sai: chính ràng buộc đó
  và các ràng buộc trước đó trên **cùng track** (cùng mã tracker = cùng một người). Ràng buộc trên track khác có xác
  nhận riêng (khuôn mặt / giáo viên) → không bị thu hồi; liên kết tracker đổi mã / khôi phục theo vị trí chỉ là suy
  luận. Nếu lần trước đó cũng sai, giáo viên sửa điểm danh trực tiếp (luôn thắng).
* Một khung hình đang xử lý dở khi giáo viên sửa tên không thể khôi phục bằng chứng đã bị thu hồi.
* Vì vậy: lần điểm danh đúng và độc lập trước đó được **giữ nguyên** (không bao giờ thành `LATE` hay `NOT_SEEN` vì một
  lần sửa sau đó); nếu bằng chứng duy nhất là ràng buộc sai → `NOT_SEEN`; học sinh thật sự đến sau ngưỡng → `LATE`.
* Không xóa lịch sử: bằng chứng bị thu hồi vẫn còn, có `revoked_at`, `revoked_reason`, `revoked_by`. Sửa của giáo
  viên (Có mặt / Đi muộn / Vắng mặt) luôn thắng và được khóa.
* Camera mất kết nối / tải lại trang không xóa bằng chứng (thuộc về buổi học, không thuộc về luồng camera).

## Giáo viên sửa

Trang **Điểm danh** → cột “Giáo viên sửa” (Có mặt / Đi muộn / Vắng mặt), cho buổi đang diễn ra **và** buổi đã kết thúc.
Chỉ giáo viên được phân công lớp. Sửa của giáo viên được khóa: hệ thống không ghi đè trong buổi đó. Buổi đã kết thúc
(hoặc học sinh thêm vào lớp sau khi bắt đầu): bản ghi `session_students` được cập nhật hoặc tạo mới. Lưu ở
`attendance_records` (người sửa, thời điểm) + `session_students.attendance_source`, ghi `ATTENDANCE_TEACHER_CORRECTED`.

Tóm tắt điểm danh không nằm trong lịch xóa tự động (như trước); lịch sử ràng buộc danh tính theo hạn lưu sự kiện.
