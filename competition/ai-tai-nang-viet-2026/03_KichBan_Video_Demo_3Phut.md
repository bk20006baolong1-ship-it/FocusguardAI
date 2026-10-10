# KỊCH BẢN VIDEO DEMO 3 PHÚT

**FocusGuard • Bảng A THCS**

> Quay một mạch hoặc cắt ghép tối thiểu. Chỉ dùng dữ liệu thật, không dựng dashboard hay số liệu giả.

| Thời gian | Hình ảnh / thao tác | Lời dẫn |
|---|---|---|
| 0:00–0:15 | Đăng nhập → trang Giám sát của giáo viên | “Đây là FocusGuard, hệ thống hỗ trợ giáo viên nhận biết tín hiệu cần chú ý trong lớp học.” |
| 0:15–0:35 | Chọn Camera lớp học → Bắt đầu buổi học → khung camera báo “Đang hoạt động” | “Giáo viên bấm bắt đầu buổi học. Camera chuyển từ đang khởi động sang đang hoạt động.” |
| 0:35–0:55 | Học sinh xuất hiện; thẻ học sinh chuyển sang “Có mặt · Tập trung” | “Số theo dõi của camera chỉ là tạm thời. Hệ thống xác nhận đúng học sinh rồi mới ghi dữ liệu cho em đó.” |
| 0:55–1:20 | Giơ điện thoại rõ trước camera; mục Cần chú ý ngay hiện “Dùng điện thoại · N giây” | “Phải thấy điện thoại liên tục từ 1 giây mới tạo sự kiện. Điện thoại lướt qua không bị tính.” |
| 1:20–1:40 | Cất điện thoại; điểm tăng dần trở lại | “Điểm là quy tắc công khai theo thời gian, không phải AI đọc suy nghĩ.” |
| 1:40–2:00 | Ra khỏi khung hình vài giây, rồi hơn 10 giây | “Khuất vài giây là Tạm khuất, không trừ điểm; từ 10 giây mới là Rời chỗ.” |
| 2:00–2:18 | Ngắt camera thật nếu an toàn; khung camera báo “Mất tín hiệu camera” hoặc “Không dùng được camera” | “Mất camera thì điểm giữ nguyên; khi camera quay lại, buổi học tiếp tục.” |
| 2:18–2:42 | Kết thúc buổi học → hộp xác nhận → bảng Tổng kết buổi học → Lịch sử buổi học | “Kết thúc buổi học, hệ thống lưu sự kiện và bảng tổng kết từ dữ liệu đã ghi nhận.” |
| 2:42–3:00 | Trang Phân tích / thông điệp cuối | “Khi chưa đủ dữ liệu, FocusGuard ghi ‘Chưa có’ chứ không điền số. Hệ thống không nhận diện cảm xúc, không ghi video và chỉ hỗ trợ giáo viên.” |

## Checklist trước khi quay
- Đăng ký khuôn mặt người demo trước; chỉ dùng người có đồng ý.
- Chạy thử toàn bộ các bước trước khi quay; mục tiêu video dưới 2:50.
- Chạy `python app.py` từ Terminal để máy cấp quyền camera; mở trang Giám sát trong suốt lúc quay (camera chỉ phân tích khi trang này đang mở).
- Phát hiện điện thoại còn đứt đoạn trên webcam laptop: giơ điện thoại ngang mặt, đủ sáng, và thử trước vài lần. Nếu không lên sự kiện thì quay lại, không cắt ghép hay dựng sự kiện.
- Không chèn accuracy hoặc số liệu không có bằng chứng.
- Ẩn thông tin cá nhân không cần thiết.
- Ưu tiên chụp/ghi giao diện mới nhất từ chính branch đang nộp.
