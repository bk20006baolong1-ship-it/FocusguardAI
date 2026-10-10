# FocusGuard – Tóm tắt bản thi (Competition Hardening)

## Mục tiêu của bản này
Chuyển hệ thống từ `track_id + student_name + mock/global state` sang một pipeline có danh tính chuẩn,
một nguồn điểm duy nhất, realtime do máy chủ quyết định, phân quyền theo quyền sở hữu, và analytics chỉ
từ dữ liệu thật. Mọi tuyên bố về "AI" được giới hạn ở những gì đã kiểm chứng.

## Đã làm

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| IdentityManager | Có | ngưỡng + margin, xác nhận theo thời gian, khoá, cooldown, chống trùng, phục hồi track, chống cắt nhau |
| StudentRuntimeState | Có | tách identity / connection / attendance / visibility / focus |
| FocusEngine duy nhất, theo thời gian | Có | 5/15/30 FPS lệch ≤ 1.5 điểm (SYNTHETIC: 0.8) |
| Hành vi theo thời gian | Có | PHONE, DROWSY, HEAD_AWAY, TEMPORARILY_NOT_VISIBLE, AWAY; bỏ qua chớp mắt/liếc/lóe |
| SessionRuntime | Có | bắt đầu sạch, kết thúc ghi kết quả, camera ngắt không giết runtime |
| Socket.IO do máy chủ quyết định | Có | từ chối dữ liệu AI từ client; room `class:{id}:teachers`, `class:{id}:students`, `student:{id}` |
| Phân quyền theo quyền sở hữu | Có | học sinh / giáo viên theo lớp / phụ huynh theo liên kết / admin tường minh |
| Bảo mật | Có | secret từ env, cookie, CORS allow-list, Werkzeug hash + migrate, Firestore deny-all |
| Loại bỏ dữ liệu giả | Có | không mock students, drift ngẫu nhiên, cảm xúc, dự báo, root-cause mặc định, seed phiên giả |
| Analytics thật | Có | `insufficient_data` khi thiếu dữ liệu (giao diện ghi "Chưa có" / "Chưa đủ dữ liệu"); zero stays zero |
| Khung đánh giá | Có | P/R/F1, cảnh báo sai/giờ, độ trễ, danh tính, FPS – nhãn SYNTHETIC |
| CI | Có | compile, import, pytest, JS syntax, security check, đánh giá SYNTHETIC |

## Trung thực về năng lực

* Điểm tập trung: **heuristic theo thời gian**, không phải mô hình học máy.
* Không nhận diện cảm xúc; không đo hướng nhìn.
* Độ chính xác thực tế: **NOT_EVALUATED**. Phần cứng: **HARDWARE_REQUIRED**.

## Kết quả kiểm thử (máy local, `bash scripts/ci_check.sh`)
184 passed, 0 failed, 9 skipped (3 HARDWARE_REQUIRED, 6 EMULATOR_REQUIRED) sau đợt nâng cấp giao diện (142 tại thời điểm merge PR #1). Firestore emulator: 6/6 PASS. Hardware 3/3 PASS; camera thật: kịch bản 1–5, 8, 9 PASS (xem docs/REAL_CAMERA_RESULTS.md).

## Việc còn lại
1. Thu thập bộ dữ liệu lớp học thật có nhãn và có đồng ý → chạy `scripts/run_evaluation.py --real-dataset`.
2. Đo FPS và độ trễ trên phần cứng thi nếu khác MacBook Air M3 (khoảng 7.5 FPS).
3. Quyết định về lịch sử git (ảnh khuôn mặt, export emulator, khoá giả trong commit cũ) – xem
   `docs/PRIVACY_AND_AI_SAFETY.md`.
4. Firestore: đã kiểm tra trên emulator (`scripts/firestore_emulator_check.sh`); Firestore production chưa kiểm tra.
5. Kịch bản camera 6–7 (hai người cắt nhau, người lạ) cần thêm tình nguyện viên.

Tài liệu: [docs/COMPETITION_ARCHITECTURE.md](docs/COMPETITION_ARCHITECTURE.md) ·
[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) · [docs/PRIVACY_AND_AI_SAFETY.md](docs/PRIVACY_AND_AI_SAFETY.md)
