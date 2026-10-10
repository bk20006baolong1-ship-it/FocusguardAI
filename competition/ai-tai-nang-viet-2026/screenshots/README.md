# Ảnh giao diện dùng trong hồ sơ

Chỉ lưu ảnh chụp từ **giao diện thật của phiên bản mô tả trong hồ sơ**.

## Quy tắc
- Không chụp mật khẩu, token, URL chứa thông tin đăng nhập.
- Không chụp khuôn mặt hay người thật nếu chưa có sự đồng ý phù hợp.
- Không chỉnh sửa số liệu sau khi chụp. Có thể cắt cho gọn nhưng không thay nội dung.
- Không dựng trạng thái không có thật. Trạng thái nào không tái tạo được thì ghi là BLOCKED.

## Nguồn gốc 5 ảnh (chụp ngày 04/10/2026)

Điểm chung:
- Nhánh `feat/competition-ui-ux-hardening`, commit `a9a0406b69f734141fc8fe72059963f152f6d019`. PR #3 đã merge commit này vào `main` (`950f8f5`); hai commit có cùng mã nguồn (tree `6d16bc8`).
- Ứng dụng chạy bằng `python app.py` với cơ sở dữ liệu SQLite mới tạo (chỉ có tài khoản mẫu của môi trường phát triển), không dùng công cụ bơm dữ liệu.
- Chụp bằng Chrome headless qua giao thức DevTools, khung nhìn 1440×900, tỷ lệ điểm ảnh 2 (ảnh 2880×1800), vai trò giáo viên (tài khoản mẫu `teacher`). Ảnh không qua chỉnh sửa hay cắt.
- Tên học sinh trong ảnh là tên của tài khoản mẫu, không phải người thật. Không có khuôn mặt nào trong ảnh.

| Ảnh | Đường dẫn | Trạng thái và dữ liệu | Claim được chứng minh | Trang hồ sơ |
|---|---|---|---|---|
| `teacher-dashboard.png` | `/teacher/dashboard` | Chưa bắt đầu buổi học, cơ sở dữ liệu trống | Giao diện không điền số khi chưa có dữ liệu; có hai hình thức buổi học; trạng thái camera rõ | Mục 2 (Ảnh 1) |
| `live-monitoring.png` | `/teacher/dashboard` | Buổi học trực tuyến đang diễn ra (phút 03:06). Một người thử thật ngồi trước camera FaceTime HD của MacBook Air M3, đăng nhập tài khoản mẫu `hocsinh`; pipeline camera thật | Học sinh có mặt hiện "Tập trung" kèm điểm; học sinh chưa vào lớp hiện "Ngoại tuyến", không bị coi là mất tập trung | Mục 5 (Ảnh 2) |
| `behavior-event-phone.png` | `/teacher/dashboard` | Cùng buổi học (phút 06:35), người thử cầm điện thoại trước camera | Mục Cần chú ý ngay nêu tên, hành vi, thời gian ("Dùng điện thoại · 8 giây") và gợi ý xử lý; điểm giảm theo quy tắc | Mục 5 (Ảnh 3) |
| `session-summary.png` | `/teacher/dashboard` (bảng Tổng kết buổi học) | Ngay sau khi kết thúc chính buổi học trên: 7 phút 13 giây, có mặt 1/9, điểm trung bình 92, 16 sự kiện | Tổng kết lấy từ dữ liệu đã ghi nhận; học sinh vắng ghi "Chưa có" thay vì 0 | Mục 6 (Ảnh 4) |
| `analytics-empty-or-real.png` | `/teacher/analytics` | Sau buổi học trên: 1 buổi, biểu đồ số lần ghi nhận từng hành vi | Thống kê chỉ dùng dữ liệu thật; phần chưa đủ dữ liệu nêu rõ điều kiện cần có | Dự phòng (thuyết trình / phụ lục), không đưa vào 8 trang |

## Điều cần biết về buổi học trong ảnh
- Một người, một máy, một buổi; không dùng để suy ra độ chính xác.
- Hình thức Lớp trực tuyến được chọn để ảnh không có khuôn mặt. Hình thức Camera lớp học (có khung hình camera và nhận diện khuôn mặt) không có trong bộ ảnh này.
- Phát hiện điện thoại bị đứt đoạn: cả buổi ghi 6 lần "Dùng điện thoại" (tổng 22,3 giây), 9 lần "Quay đi chỗ khác" (tổng 60,0 giây) và 1 lần "Buồn ngủ" (2,6 giây). Người thử cầm điện thoại ba lượt theo lời nhắc: lượt đầu chỉ được ghi thành hai lần 1–2 giây; lượt hai có một lần 6,1 giây nhưng công cụ chụp gặp lỗi nên không chụp được; ảnh là của lượt ba. Không có nhãn đúng/sai để biết lần nào là cảnh báo sai.
- Cơ sở dữ liệu tạm của buổi chụp nằm ngoài repo và không được commit.
