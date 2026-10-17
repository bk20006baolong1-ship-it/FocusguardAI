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
* Khung đánh giá lớp học thật (metadata, hoạt động, nhãn thực tế, chỉ số, hiệu chỉnh) đã có và được kiểm thử trên dữ
  liệu REPLAY; dữ liệu đánh giá thật lưu ở `evaluation/private/` và tự xóa sau `EVALUATION_DATA_RETENTION_DAYS`.

## Dữ liệu sinh trắc học

* Chỉ lưu **vector đặc trưng khuôn mặt** của học sinh (trong bảng `users`), không lưu ảnh khuôn mặt.
  Ảnh đăng ký chỉ nằm trong bộ nhớ khi trích xuất. Admin có thể xoá vector bất cứ lúc nào.
* **Cần đồng ý**: đăng ký khuôn mặt và nhận diện chỉ được thực hiện khi học sinh có bản ghi đồng ý của phụ huynh/người
  giám hộ còn hiệu lực với phạm vi nhận diện khuôn mặt (máy chủ kiểm tra). Thu hồi đồng ý dừng nhận diện ngay và xóa
  vector. Vector có thời hạn lưu (`FACE_EMBEDDING_RETENTION_DAYS`). Chi tiết: `docs/CONSENT_AND_RETENTION.md`.
* **Mặc định FocusGuard không lưu video. Evidence Mode là tùy chọn, chỉ lưu đoạn minh chứng ngắn quanh sự kiện khi
  được phép, giới hạn quyền truy cập và tự động xóa theo retention policy.** (`EVENT_EVIDENCE_ENABLED=false`; chỉ giáo
  viên của lớp xem; mỗi lần xem/xóa đều ghi nhật ký; tự xóa sau `EVENT_EVIDENCE_RETENTION_HOURS`, mặc định 24 giờ.)
* Luồng MJPEG chỉ gửi cho giáo viên của đúng lớp (hoặc chính học sinh ở chế độ cá nhân).
* Người không ghi danh / nhận dạng không chắc chắn luôn là UNKNOWN; không có danh tính nào bị đoán.

## Kiểm soát truy cập

* Phân quyền theo quyền sở hữu cho mọi API và Socket.IO (xem `focusguard/authz.py`).
* Học sinh/phụ huynh không nhận số liệu cả lớp (không có bảng xếp hạng lớp cho học sinh).
* Firestore: `allow read, write: if false` – chỉ máy chủ (service account) truy cập.

## Vệ sinh repo công khai

Tree hiện tại không chứa `.env`, khoá riêng, ảnh khuôn mặt, export Firebase Emulator, file `*.db`, dữ liệu
đánh giá riêng hay video lớp học; `scripts/security_check.py` chạy trong `scripts/ci_check.sh` để giữ điều này.
Báo cáo đánh giá trước khi chia sẻ được quét bằng `scripts/artifact_privacy_check.py` (ảnh/base64, vector, đường dẫn
ảnh/video/minh chứng, tên và tên đăng nhập học sinh/phụ huynh, email, số điện thoại; có tự kiểm bằng mẫu xấu cài sẵn).

**Lịch sử mã nguồn:** mã nguồn hiện tại bắt đầu từ một snapshot sạch, **không** mang theo lịch sử của bản lưu trữ
cũ. Bản lưu trữ cũ (trước khi chuyển) vẫn còn các commit chứa một ảnh khuôn mặt, export Firebase
Emulator, `.env` cũ (không có secret), file phiên học và một khoá riêng **giả dùng cho emulator**. Việc lưu trữ/xóa bản
cũ là quyết định của đội; coi khoá giả đó là đã lộ (không dùng ở đâu khác).

## Bảo mật ứng dụng

* Phân quyền theo quyền sở hữu ở máy chủ (giáo viên chỉ lớp mình, học sinh chỉ dữ liệu của mình, phụ huynh chỉ con
  đã liên kết); vai trò lấy từ phiên và kiểm lại với CSDL.
* Chống CSRF: cookie `SameSite=Lax` + mọi yêu cầu thay đổi dữ liệu phải đến từ cùng trang (kiểm tra `Origin`/`Referer`;
  `null` hoặc trang khác → 403).
* Tệp CSV xuất ra vô hiệu hoá công thức bảng tính (`=`, `+`, `-`, `@`); dữ liệu người dùng luôn được thoát HTML khi hiển thị.
* Đoạn minh chứng: tên tệp do máy chủ sinh (UUID), không có đường dẫn công khai, chống path traversal, tệp thiếu/hỏng →
  410 và gỡ bản ghi (ghi nhật ký).

## Tín hiệu AI và quyết định của giáo viên

* Hành vi quan sát được (cúi đầu, quay đầu, nhắm mắt, rời chỗ) **không phải lúc nào cũng là mất tập trung**. Giáo viên
  chọn hoạt động lớp (chép bài, nhìn bảng, thảo luận nhóm…); chính sách theo hoạt động quyết định có trừ điểm/cảnh
  báo hay không. Tín hiệu gốc luôn được lưu để xem lại.
* Mọi sự kiện có trạng thái xem lại: chưa xem, giáo viên xác nhận đúng, giáo viên xác nhận báo nhầm, không chắc. Giao
  diện dùng "AI phát hiện tín hiệu", "Sự kiện cần xem lại", không dùng "vi phạm" hay xếp hạng học sinh.
* Giá trị ngưỡng và hệ số phạt hiện là giá trị khởi đầu (`UNCALIBRATED_DEFAULT`). Chỉ hiệu chỉnh bằng bằng chứng thật
  (`docs/REAL_CLASSROOM_EVALUATION_PROTOCOL.md`) và có người duyệt.

## Nguyên tắc sử dụng

Kết quả là **tín hiệu hỗ trợ giáo viên**, không dùng để kỷ luật hay chấm điểm học sinh tự động. Giáo viên
luôn là người quyết định; hệ thống hiển thị thời lượng và độ tin cậy của từng sự kiện để kiểm tra lại.
