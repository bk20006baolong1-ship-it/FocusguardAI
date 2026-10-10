# Quyền riêng tư & an toàn AI

## Những gì hệ thống làm và KHÔNG làm

| Có làm | Không làm |
|---|---|
| Phát hiện người/điện thoại (YOLOv8), mốc khuôn mặt (MediaPipe), vector khuôn mặt (InsightFace) | Nhận diện cảm xúc |
| Quy tắc thời gian cho hành vi quan sát được | Đo hướng nhìn (gaze) – "tập trung" ≠ "nhìn màn hình" |
| Điểm tập trung heuristic, công thức công khai | Dự đoán bằng học máy (không có mô hình dự báo được kiểm chứng) |
| Thống kê khung giờ học từ dữ liệu đã lưu | "AI coach" / khuyến nghị giả dạng mô hình |

Mọi chỉ số chưa được đo hiển thị **"Chưa có" / "Chưa đủ dữ liệu"**, không bao giờ là số điền sẵn.

## Trạng thái đánh giá

* Logic thời gian & danh tính: kiểm thử trên dữ liệu **SYNTHETIC** (`focusguard/evaluation`).
* Độ chính xác trên lớp học thật: **NOT_EVALUATED**. Cần bộ dữ liệu có nhãn, có sự đồng ý của học sinh
  và phụ huynh; bộ dữ liệu đó phải để ngoài repo (`evaluation/private/`, `evaluation/datasets/` bị git-ignore).
* Hiệu năng phần cứng (FPS thực tế, độ trễ camera): **HARDWARE_REQUIRED**.

## Dữ liệu sinh trắc học

* Chỉ lưu **vector đặc trưng khuôn mặt** của học sinh (trong bảng `users`), không lưu ảnh khuôn mặt.
  Ảnh đăng ký chỉ nằm trong bộ nhớ khi trích xuất. Admin có thể xoá vector bất cứ lúc nào.
* Không ghi video. Luồng MJPEG chỉ gửi cho giáo viên của đúng lớp (hoặc chính học sinh ở chế độ cá nhân).
* Người không ghi danh / nhận dạng không chắc chắn luôn là UNKNOWN; không có danh tính nào bị đoán.

## Kiểm soát truy cập

* Phân quyền theo quyền sở hữu cho mọi API và Socket.IO (xem `focusguard/authz.py`).
* Học sinh/phụ huynh không nhận số liệu cả lớp (không có bảng xếp hạng lớp cho học sinh).
* Firestore: `allow read, write: if false` – chỉ máy chủ (service account) truy cập.

## Vệ sinh repo công khai

Tree hiện tại không chứa `.env`, khoá riêng, ảnh khuôn mặt, export Firebase Emulator, file `*.db`, dữ liệu
đánh giá riêng hay video lớp học; `scripts/security_check.py` chạy trong CI để giữ điều này.

**Lưu ý lịch sử git:** các commit cũ (trước nhánh này) vẫn chứa: một ảnh khuôn mặt
(`static/uploads/avatars/student_3.jpg`), export Firebase Emulator (tài khoản demo), file `.env` (không có
secret), các file phiên học trong `data/`, và một khoá riêng **giả dùng cho emulator** nhúng trong
`repository.py`/`scratch/`. Nhiệm vụ này **không** viết lại lịch sử. Khuyến nghị: coi khoá đó là đã lộ
(không dùng ở đâu khác), xin phép chủ ảnh hoặc dùng `git filter-repo` + force-push có kế hoạch riêng.

## Nguyên tắc sử dụng

Kết quả là **tín hiệu hỗ trợ giáo viên**, không dùng để kỷ luật hay chấm điểm học sinh tự động. Giáo viên
luôn là người quyết định; hệ thống hiển thị thời lượng và độ tin cậy của từng sự kiện để kiểm tra lại.
