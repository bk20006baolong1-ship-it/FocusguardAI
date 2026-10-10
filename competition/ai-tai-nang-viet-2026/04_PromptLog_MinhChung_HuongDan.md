# PROMPT LOG & MINH CHỨNG PHÁT TRIỂN

> Prompt Log dùng lịch sử thật do đội cung cấp, không dựng lại nội dung hoặc timestamp.

## 1. Prompt thực tế đã sử dụng

Bản Word/PDF trong `competition/ai-tai-nang-viet-2026/artifacts/` hiện đã nhúng **đầy đủ 3 prompt thật** lấy trực tiếp từ tài liệu Google Drive `một số câu lệnh` của đội:

- Prompt 1: xử lý lỗi dữ liệu admin bị reset sau khi chạy lại web.
- Prompt 2: khóa phạm vi tối ưu Camera Performance (camera capture thread, AI worker, YOLO, MediaPipe, recognition, JPEG, thread safety, Socket.IO, benchmark).
- Prompt 3: nâng cấp AI camera cho 1 học sinh và nhiều học sinh, identity stability, temporal behavior, focus score theo thời gian, performance, test plan và acceptance criteria.

Nội dung trong bản DOCX/PDF được giữ nguyên từ nguồn đội cung cấp. Ghi chú của thí sinh về việc chỉ chọn 3 prompt tiêu biểu cũng được giữ lại.

## 2. Hình ảnh minh chứng

Bản Word/PDF đã nhúng trực tiếp các ảnh:
- lịch sử các cuộc hội thoại phát triển FocusGuard;
- minh chứng trợ lý AI lập kế hoạch Classroom AI Monitoring;
- minh chứng phát triển/đối chiếu giao diện;
- minh chứng verification sau triển khai.

Thư mục minh chứng gốc:
https://drive.google.com/drive/folders/1K6HAi9LngnBFuSgdzzg4nP0t6cMwB042

## 3. Manifest minh chứng trong repo

| Minh chứng | Nội dung |
|---|---|
| README.md | Kiến trúc, trạng thái test, hạn chế, cách chạy |
| SUMMARY.md | Competition hardening |
| docs/COMPETITION_ARCHITECTURE.md | Identity, behavior, FocusEngine, runtime, realtime, authz |
| docs/REAL_CAMERA_RESULTS.md | Camera thật, kịch bản PASS, lỗi đã sửa |
| docs/PRIVACY_AND_AI_SAFETY.md | Sinh trắc, không ghi video, phạm vi sử dụng |
| .github/workflows/ci.yml | CI compile/import/pytest/JS/security/evaluation |
| docs/UI_UX.md | Hệ thống thiết kế và UX |
| tests/ | 184 tests trong lần kiểm tra gần nhất |
| competition/ai-tai-nang-viet-2026/screenshots/ | Ảnh giao diện thật |
| Git history | Quá trình phát triển, sửa lỗi và kiểm thử |

## 4. Công cụ, thư viện và trợ lý AI

- YOLOv8 / Ultralytics: mô hình/thư viện có sẵn; đội tích hợp và kiểm thử.
- ByteTrack: tracker có sẵn; track_id chỉ là tạm thời.
- MediaPipe Face Landmarker: landmark khuôn mặt.
- InsightFace: embedding khuôn mặt.
- Flask / Socket.IO: backend/realtime.
- Firebase Admin / Firestore: backend tùy chọn.
- ChatGPT: phân tích yêu cầu, hỗ trợ xây prompt, rà soát hồ sơ và giải thích.
- Claude Code: đọc/sửa mã nguồn theo prompt, chạy test, audit và verification.

**Theo yêu cầu cập nhật hồ sơ, không còn mục 5 “Kiểm tra trước khi upload”.**
