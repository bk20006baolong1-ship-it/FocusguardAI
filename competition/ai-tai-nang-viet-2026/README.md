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
- `README.md`
- `SUMMARY.md`
- `docs/COMPETITION_ARCHITECTURE.md`
- `docs/PRIVACY_AND_AI_SAFETY.md`
- `docs/REAL_CAMERA_RESULTS.md`
- `.github/workflows/ci.yml`
- test suite và commit history hiện tại

## Trạng thái (rà soát ngày 04/10/2026)
Hồ sơ mô tả sản phẩm ở nhánh chính `main` tại commit `950f8f5` (PR #3 – giao diện mới – đã được merge). Kiểm thử và ảnh chụp được thực hiện tại commit `a9a0406`, có cùng mã nguồn với `950f8f5` (cùng tree `6d16bc8`).

Đã xong:
- Rà soát từng claim với mã nguồn; số liệu, ngưỡng và thuật ngữ đã cập nhật theo phiên bản trên.
- Chạy lại: `bash scripts/ci_check.sh` (184 đạt, 9 bỏ qua), Firestore emulator (6/6), kiểm thử phần cứng (3/3).
- 5 ảnh giao diện thật trong `screenshots/`.

Còn thiếu trước khi nộp (cần người làm):
- Điền tên đội, thành viên, trường/lớp, giáo viên hướng dẫn.
- Đính kèm Prompt Log gốc và kê khai đóng góp của từng thành viên (xem `04_PromptLog_MinhChung_HuongDan.md`).
- Sinh lại bản PDF/DOCX từ bản nguồn này.
