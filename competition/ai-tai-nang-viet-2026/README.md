# Hồ sơ AI Tài Năng Việt 2026 – FocusGuard (Bảng A THCS)

Thư mục này chứa **bản nguồn để review và cập nhật hồ sơ dự thi**. Mọi claim kỹ thuật phải đối chiếu với source/test hiện tại của repository.

## Nội dung
- `01_FocusGuard_HoSo_BangA_8trang_SOURCE.md` – nguồn nội dung hồ sơ chính.
- `02_KichBan_ThuyetTrinh_5Phut.md` – kịch bản thuyết trình.
- `03_KichBan_Video_Demo_3Phut.md` – kịch bản video demo.
- `04_PromptLog_MinhChung_HuongDan.md` – hướng dẫn Prompt Log & evidence.
- `05_Bo_CauHoi_PhanBien.md` – bộ câu hỏi luyện phản biện.
- `CLAUDE_REVIEW_AND_SCREENSHOT_PROMPT.md` – prompt dùng cho Claude Code để audit claim, chạy test và chụp UI thật.
- `screenshots/` – 5 ảnh giao diện thật; `screenshots/README.md` ghi quy ước và nguồn gốc từng ảnh.

## Nguyên tắc
1. Không sửa claim kỹ thuật chỉ để hồ sơ “đẹp” hơn.
2. Không đưa accuracy thực tế khi chưa có dataset gán nhãn hợp lệ.
3. Không dùng screenshot giả, mock data hoặc dashboard dựng riêng cho hồ sơ.
4. Screenshot phải lấy từ phiên bản code thực tế đang đề xuất nộp.
5. Prompt Log phải là lịch sử thật; không dựng lại.
6. Không commit secret, token, cookie, ảnh khuôn mặt riêng tư hoặc dữ liệu học sinh.

## Nguồn đối chiếu bắt buộc
- `README.md`, `SUMMARY.md`
- `docs/BTC_FEEDBACK_CLOSURE.md` (đối chiếu từng góp ý BTC với mã, test, bằng chứng)
- `docs/COMPETITION_ARCHITECTURE.md`, `docs/PRIVACY_AND_AI_SAFETY.md`, `docs/CONSENT_AND_RETENTION.md`
- `docs/REAL_CAMERA_RESULTS.md`, `docs/REAL_CLASSROOM_EVALUATION_PROTOCOL.md`
- `scripts/ci_check.sh`, `scripts/firestore_emulator_check.sh` (cổng kiểm chứng chạy trực tiếp; GitHub Actions không dùng làm cổng)
- test suite và lịch sử commit hiện tại của `DKTECHVN/focusAI`

## Trạng thái hiện tại
Repo chính thức: `DKTECHVN/focusAI`, nhánh `main`. Trạng thái: **`CODE_COMPLETE_CLOUD_VERIFIED_REAL_CLASSROOM_EVIDENCE_PENDING`**.

* Đã triển khai đủ 5 nhóm góp ý của Ban Tổ chức (lớp học trực tiếp, nhiều người, cảnh báo sai và hiệu chỉnh, giáo viên
  xem lại, đồng ý và quyền riêng tư) và bộ công cụ đánh giá lớp thật (`tools/field_test.py`, 15 kịch bản,
  `scripts/finalize_real_classroom_evaluation.py`).
* Kiểm chứng trực tiếp (clone mới, Python 3.12): `ci_check.sh` 675 đạt / 27 bỏ qua có lý do;
  Firestore emulator 24/24 PASS; đi thử giao diện 222/222 kiểm tra đạt; chạy thử quy trình thực địa (mô phỏng) đủ 15 kịch bản.
* Không có con số độ chính xác thực tế (`NOT_EVALUATED`); câu về video: "Mặc định FocusGuard không lưu video. Evidence
  Mode là tùy chọn…".
* Lịch sử: trước đợt góp ý 184 bài kiểm thử (commit `950f8f5`/`a9a0406` của repo cũ, rà soát 04/10/2026); sau đợt
  góp ý, trước khi chuyển repo 334 bài. Các commit đó không có trong repo mới (repo mới bắt đầu từ snapshot sạch).

Còn cần người làm trước khi nộp lại:
- Kê khai đóng góp của từng thành viên (mục **ĐỘI ĐIỀN** trong `01`) và đính kèm Prompt Log gốc.
- Sinh lại bản PDF/DOCX trong `artifacts/` từ bản nguồn (xem danh sách nội dung cũ trong `artifacts/README.md`) và
  chụp lại `screenshots/` trên giao diện hiện tại nếu muốn ảnh mới.
- Chạy buổi đánh giá lớp thật có đồng ý; chỉ cập nhật hồ sơ bằng số liệu có `REAL_WORLD_EVIDENCE: YES`.
