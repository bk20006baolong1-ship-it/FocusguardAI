# Kiến trúc bản thi – FocusGuard

## 1. Luồng dữ liệu

```
Camera
 └─ YOLOv8 + ByteTrack ─ track_id (TẠM THỜI, chỉ là "cùng một khung hình người")
     └─ InsightFace embedding, ghép 1–1 với track (associate_faces)
         └─ IdentityManager ─ student_id (danh tính CHUẨN)
             └─ Observation (EAR, yaw/pitch, điện thoại đã ghép, độ tin cậy danh tính, ghế)
                 └─ SessionRuntime.process_frame(now, {student_id: Observation})
                     ├─ BehaviorAnalyzer (per student, theo thời gian) → BehaviorEpisode
                     ├─ FocusEngine (một engine, tích phân theo giây)
                     ├─ StudentRuntimeState (các trạng thái tách biệt)
                     └─ sink → focus_events / focus_snapshots / session_students
 Socket.IO (máy chủ quyết định) ← class:{id}:teachers | class:{id}:students | student:{id}
 Analytics ← chỉ đọc bảng đã lưu (focusguard/analytics.py)
```

Pipeline camera (`classroom_ai.py`, `camera_ai.py`) **không** giữ điểm và **không** tự cộng/trừ điểm;
test `test_camera_pipelines_do_not_compute_scores` kiểm tra điều này bằng phân tích AST.

## 2. IdentityManager (`focusguard/identity.py`)

* `track_id` không bao giờ là danh tính. Tên học sinh không bao giờ là khoá runtime.
* So khớp cosine với gallery: cần `best ≥ similarity_threshold` **và** `best − second_best ≥ min_margin`
  (nếu không → `AMBIGUOUS`, giữ UNKNOWN/UNCERTAIN).
* Xác nhận theo thời gian: cùng một học sinh thắng `confirm_consecutive` lần liên tiếp và kéo dài
  ≥ `confirm_min_seconds`.
* Khoá danh tính: chỉ nhả khi có ≥ `unlock_consecutive` lần mâu thuẫn **và** kéo dài ≥ `unlock_min_seconds`
  (chống một frame lỗi và hai người đi cắt nhau).
* Một `student_id` không thể gắn với hai track đang sống (`CONFLICT`).
* Cooldown sau khi nhả vì mâu thuẫn; chỉ gắn lại ngay khi similarity ≥ `steal_similarity`.
* Phục hồi track: track mất quá `track_lost_seconds` → danh tính vào danh sách "vừa mất";
  track mới khớp được phục hồi với `recovery_consecutive` lần (sự kiện `RECOVERED`).
* Mọi quyết định được ghi vào `events` (CONFIRMED/RECOVERED/RELEASED/CONFLICT/LOST) để đánh giá.

## 3. StudentRuntimeState (`focusguard/runtime_state.py`)

Khoá theo `student_id`, tách riêng: `identity_status`, `connection_status`, `attendance_status`,
`visibility_status`, `focus_state`, `focus_score` (None khi chưa đo), `active_track_id`,
`identity_confidence`, ghế, episode đang mở, mốc thời gian, bộ đếm sự kiện.
"Ngoại tuyến", "vắng mặt", "không thấy" và "mất tập trung" là bốn giá trị khác nhau, ở bốn trường khác nhau.

## 4. Hành vi theo thời gian (`focusguard/behavior.py`)

| Hành vi | Tín hiệu thô | Thời gian tối thiểu |
|---|---|---|
| PHONE | điện thoại ghép với người, conf ≥ 0.35, ≥ 60% hộp nằm trong người | 1.0 s |
| DROWSY | EAR < 0.22 | 1.5 s |
| HEAD_AWAY | \|yaw\| hoặc \|pitch\| > 20° | 2.0 s |
| TEMPORARILY_NOT_VISIBLE | không thấy người | ngay lập tức, không phạt |
| AWAY | không thấy người | ≥ 10 s (một lần rời chỗ = một episode) |

Chớp mắt, liếc nhanh, điện thoại lóe 0.2 s không tạo sự kiện. Khoảng hở < `gap_tolerance_seconds` không cắt
episode. Episode có `student_id, type, start_time, end_time, duration, confidence, source, metadata`.

## 5. FocusEngine duy nhất (`focusguard/focus_engine.py`)

`score(t+dt) = clamp(score(t) + rate(state)·dt)`; FOCUSED hồi +0.5/s; PHONE −2/s, DROWSY −1.5/s,
HEAD_AWAY −1/s, AWAY −1/s; TEMPORARILY_NOT_VISIBLE/UNKNOWN/NO_CAMERA_SIGNAL: đóng băng. `dt` bị chặn
ở `max_step_seconds` nên camera treo không xoá điểm. Test: cùng kịch bản ở 5/15/30 FPS lệch ≤ 1.5 điểm
(thực đo trên SYNTHETIC: 0.8).

## 6. SessionRuntime (`focusguard/session_runtime.py`)

* Phạm vi: `class_session_id` (hoặc `session_id` cá nhân) × `student_id`. Học sinh không ghi danh bị bỏ qua.
* Bắt đầu lớp: runtime mới, gắn toàn bộ học sinh ghi danh, không mang trạng thái cũ (`RuntimeRegistry`).
* Kết thúc: đóng episode, snapshot cuối, ghi `session_students`, NOT_YET → ABSENT.
* Trình duyệt ngắt `/video_feed` chỉ giải phóng thiết bị camera; runtime vẫn chạy, báo `NO_CAMERA_SIGNAL`
  và đóng băng điểm.

## 7. Realtime do máy chủ quyết định (`app.py`, `focusguard/realtime.py`)

* Kết nối Socket.IO chưa đăng nhập bị từ chối. Danh tính lấy từ Flask session.
* `student_data_push`, `student_frame_push` bị từ chối (`client_state_rejected`); client không thể gửi tên,
  điểm, trạng thái AI, điểm danh.
* Room: `class:{id}:teachers`, `class:{id}:students`, `student:{id}`. Giáo viên chỉ vào room của lớp mình.
* Học sinh/phụ huynh chỉ nhận dữ liệu của chính học sinh đó (`student_self_view`), không có số liệu cả lớp.

## 8. Phân quyền (`focusguard/authz.py`)

Mỗi request dựng `Principal` từ DB (không tin `role` trong cookie). Học sinh: dữ liệu của mình; giáo viên:
lớp trong `class_members`; phụ huynh: học sinh được liên kết; admin: phạm vi admin tường minh.
Bảo vệ: `/video_feed`, breakdown phiên, phiên mới nhất, danh sách phiên, analytics, điểm danh, báo cáo.

## 9. Dữ liệu

`users`, `classes`, `class_members`, `sessions`, `class_sessions`, `session_students`, `focus_events`
(student_id, class_session_id/session_id, start/end, duration, confidence, source, metadata),
`focus_snapshots`. `history_json` không còn là nguồn analytics. Không seed phiên giả.

## 10. Bảo mật

Secret từ `FLASK_SECRET_KEY` (production bắt buộc), cookie HttpOnly/SameSite/Secure (production),
CORS theo `ALLOWED_ORIGINS`, mật khẩu Werkzeug hash (migrate plaintext cũ), Firestore rules chặn toàn bộ
truy cập trực tiếp từ client, `scripts/security_check.py` chặn file nhạy cảm trong tree hiện tại.
