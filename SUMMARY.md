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
| Kiểm chứng | Có | chạy trực tiếp trên cloud (không dùng GitHub Actions làm cổng): compile, import, pytest, JS syntax, security check, đánh giá SYNTHETIC, gói đánh giá cloud, quét quyền riêng tư báo cáo, Firestore emulator, đi thử bằng trình duyệt |

## Cải tiến theo góp ý BTC (đã có trên `main` của `DKTECHVN/focusAI`; chi tiết: [docs/BTC_FEEDBACK_CLOSURE.md](docs/BTC_FEEDBACK_CLOSURE.md))

| Góp ý | Triển khai | Kiểm thử | Trạng thái |
|---|---|---|---|
| A. Lớp học trực tiếp | Đánh giá thực tế: metadata camera (cả FPS thực đo), hoạt động lớp, 15 kịch bản BTC, nhãn thực tế, công cụ thực địa một lệnh (`tools/field_test.py`), tổng hợp báo cáo (`scripts/finalize_real_classroom_evaluation.py`) | `test_real_classroom_evaluation.py`, `test_field_evaluation.py` | Code xong, chạy thử mô phỏng trên cloud; lớp thật HARDWARE_REQUIRED |
| B. Nhiều người | 10 kịch bản SYNTHETIC (cắt nhau, đổi track, biến mất, người lạ, che mặt, điện thoại che mặt, đổi góc, 5/10/20 HS), benchmark | `test_multiperson_stress.py`, `test_tools_and_benchmark.py` | CLOUD_VERIFIED (tổng hợp); camera thật HARDWARE_REQUIRED |
| C. Cảnh báo sai / hiệu chỉnh | `BehaviorInterpretationPolicy` theo 7 hoạt động; chỉ số P/R/F1, báo nhầm/giờ, `NOT_ENOUGH_DATA`; công cụ hiệu chỉnh | `test_context_policy.py` | Công cụ xong; giá trị hiệu chỉnh HUMAN_GROUND_TRUTH_REQUIRED |
| D. Giáo viên xem lại | Trang Sự kiện, 3 kết quả + ghi chú + lịch sử, Cần giáo viên xem lại, chỉ số phản hồi (thống kê bỏ sự kiện giáo viên xác nhận báo nhầm), minh chứng tùy chọn chỉ gồm khung hình đã cắt theo đúng học sinh | `test_teacher_review.py`, `test_evidence.py`, `test_privacy_hardening.py` | Xong |
| E. Đồng ý / quyền riêng tư | Đồng ý theo phạm vi, thu hồi, xóa, thời hạn lưu, nhật ký, xuất dữ liệu, trang quản trị, chống CSRF, quét quyền riêng tư báo cáo | `test_consent_privacy.py`, `test_security_btc.py`, `test_privacy_hardening.py`, `repo_contract.py` | Xong (SQLite + Firestore emulator) |

Trên replay (dữ liệu sinh tự động, **không phải độ chính xác thực tế**), chính sách theo hoạt động giảm báo nhầm
HEAD_AWAY từ 57 xuống 2 nhưng recall HEAD_AWAY giảm từ 4/4 xuống 2/4: cần hiệu chỉnh trên dữ liệu lớp thật.

## Trung thực về năng lực

* Điểm tập trung: **heuristic theo thời gian**, không phải mô hình học máy.
* Không nhận diện cảm xúc; không đo hướng nhìn.
* Độ chính xác thực tế: **NOT_EVALUATED**. Phần cứng: **HARDWARE_REQUIRED**.

## Kết quả kiểm thử (chạy trực tiếp, clone mới, Python 3.12)

* `bash scripts/ci_check.sh`: **675 passed, 0 failed, 27 skipped** (24 EMULATOR_REQUIRED —
  chạy riêng bằng Firestore emulator; 3 HARDWARE_REQUIRED), `CI CHECK: PASS`.
* `bash scripts/firestore_emulator_check.sh`: **24/24 PASS**.
* Nhóm test của gói thi (bối cảnh, xem lại, đồng ý, minh chứng, bảo mật, hợp đồng, giao diện, runtime, chấm điểm,
  hành vi, danh tính): **201 passed**; nhiều người: **17 passed, 10/10 kịch bản SYNTHETIC**.
* Đi thử bằng trình duyệt (Playwright, 1366 px và 390 px): **222/222 kiểm tra đạt** (gồm Cài đặt › Camera, khởi tạo danh sách / gắn tên trên lớp học mô phỏng).
* Trước khi chuyển repo: 334 passed / 18 skipped; trước đợt góp ý BTC: 184. Phần cứng (MacBook Air M3): hardware tests
  3/3 PASS; camera thật kịch bản 1–5, 8, 9 PASS (docs/REAL_CAMERA_RESULTS.md) — kiểm tra tích hợp, không phải độ chính xác.

## Việc còn lại (chỉ con người / phần cứng làm được)
1. Phiếu đồng ý thật ([docs/CONSENT_FIELD_PACK.md](docs/CONSENT_FIELD_PACK.md)), một buổi lớp thật với
   `python tools/field_test.py` (≥ 5 học sinh, 15 kịch bản), hai người quan sát gán nhãn, rồi
   `python scripts/finalize_real_classroom_evaluation.py <id>` — [checklist](docs/REAL_CLASSROOM_DAY_CHECKLIST.md).
2. Duyệt đề xuất hiệu chỉnh trước khi áp dụng.
3. Đo FPS và độ trễ của mô hình trên máy thi (công cụ thực địa đo tự động).
4. Firestore production (nếu dùng): chưa kiểm tra; emulator đã kiểm tra.
5. Lưu trữ/xóa repo cũ (còn tài sản nhạy cảm trong lịch sử) — xem `docs/PRIVACY_AND_AI_SAFETY.md`.

Tài liệu: [docs/COMPETITION_ARCHITECTURE.md](docs/COMPETITION_ARCHITECTURE.md) ·
[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) · [docs/PRIVACY_AND_AI_SAFETY.md](docs/PRIVACY_AND_AI_SAFETY.md)
