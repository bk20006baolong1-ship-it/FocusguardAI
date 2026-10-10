# Kết quả kiểm tra với camera thật (tóm tắt đã làm sạch)

**Đây là kiểm tra tích hợp và hành vi, không phải đánh giá độ chính xác.** Chỉ có một tình nguyện viên, một máy
và một buổi thử. Không có con số độ chính xác nào được rút ra từ đây; độ chính xác thực tế vẫn là **NOT_EVALUATED**.
Không lưu ảnh hay video; embedding khuôn mặt bị xoá sau mỗi lần chạy. Log chi tiết (chỉ gồm id, thời gian, trạng thái
và điểm) nằm ở `evaluation/private/`, đã git-ignore và không commit.

## Môi trường
* MacBook Air Apple M3 (arm64), macOS 26.5.1, Python 3.12.8, camera FaceTime HD (index 0).
* `requirements.txt` đầy đủ. `mediapipe` 0.10.35 kéo theo `opencv-contrib-python` 5.0, đè lên bản 4.13 được pin.
* Pipeline lớp học (YOLOv8n + ByteTrack, InsightFace buffalo_sc, MediaPipe Face Landmarker) đạt khoảng **7.5 FPS** (trung vị) trên CPU.
* Công cụ: `tools/real_camera_check.py`, chạy từ Terminal.app vì macOS không cấp quyền camera cho tiến trình của Claude.

## Hardware tests
`FOCUSGUARD_HARDWARE_TESTS=1 python -m pytest tests/hardware`: **3/3 PASS** (camera trả frame thật; YOLOv8 + MediaPipe
load được; InsightFace load được).

## Ứng dụng web với webcam
`python app.py` (SQLite), giáo viên bắt đầu lớp "Trực tiếp": `/video_feed` trả frame 1920×1080, runtime nhận frame
(`camera_status=ACTIVE`), websocket đã xác thực kết nối được, console trình duyệt không có lỗi. Các trang giáo viên, học sinh,
phụ huynh và admin đều trả 200; kết thúc lớp cho tóm tắt từ dữ liệu thật.

## Kịch bản (lần chạy cuối, code tại commit 64c6732)
| # | Kịch bản | Kết quả | Dữ liệu |
|---|---|---|---|
| 1 | Danh tính một người | PASS | xác nhận sau 0.85 s, 100% frame sau đó CONFIRMED, không trùng, không reset điểm |
| 2 | Chớp mắt | PASS | 4 lần chớp ngắn (0.4–1.2 s) bị bỏ qua; các lần nhắm thật 1.6–2.8 s (EAR ~0.03) được báo DROWSY đúng thiết kế |
| 3 | Liếc nhanh | PASS | 3 lần liếc, 0 episode HEAD_AWAY |
| 4 | Điện thoại | PASS | 1 episode PHONE 14.7 s, đúng student_id, có cảnh báo cho giáo viên |
| 5 | Rời chỗ và quay lại | PASS | 1 episode AWAY 18.7 s, track mới (1 → 19) nhận lại đúng học sinh ngay, điểm không reset |
| 6 | Hai người cắt nhau | USER_ACTION_REQUIRED | cần tình nguyện viên thứ hai đã đăng ký |
| 7 | Người lạ | USER_ACTION_REQUIRED | cần tình nguyện viên chưa đăng ký |
| 8 | Ngắt camera | PASS | NO_SIGNAL khi đứng hình, điểm đóng băng, mở lại camera có frame sau 0.33 s, vẫn cùng runtime |
| 9 | Kết thúc lớp | PASS | mọi episode đã đóng, 9/9 dòng session_students, lớp mới bắt đầu sạch, pipeline cũ không ghi vào lớp mới |

## Lỗi thật tìm được nhờ camera (đã sửa, có regression test)
1. **Không ghép được khuôn mặt vào người khi quay cận** (webcam laptop): trước đây chỉ nhận khuôn mặt nằm trong nửa trên của
   khung người; nay nhận trong 85% phía trên.
2. **Điện thoại che mặt làm mất danh tính** (độ tương đồng 0.87 → 0.13–0.44): khi có vật che đã biết thì không tính là
   mâu thuẫn; người lạ chỉ làm nhả danh tính sau 5 s không khớp.
3. **Ngưỡng EAR cố định báo buồn ngủ sai** khi mắt đang mở: nay dùng baseline mắt mở của từng học sinh (tỷ lệ 0.6),
   có thời gian khởi động, và không tính mắt nhìn xuống khi đang dùng điện thoại.
4. **Tracker tạo hai track cho cùng một người**, khiến học sinh chập chờn "không thấy": track đang giữ danh tính chỉ chặn track khác
   khi nó có mặt trong cùng frame; track vừa bàn giao được nhận lại ngay.
5. **Tín hiệu điện thoại bị đứt đoạn** (YOLOv8n cho độ tin cậy 0.25–0.62, mất frame): ngưỡng 0.25, dung sai khoảng hở riêng 1 s;
   điện thoại cầm ngoài khung người (trong tầm tay, lề 25%) vẫn được ghép.
6. Công cụ: chờ hộp thoại quyền camera của macOS; giọng đọc tiếng Việt (Linh).

## Hạn chế quan sát được
* Lúc quay lại chỗ ngồi có một episode PHONE thứ hai (3.5 s, độ tin cậy 0.12) từ các lần phát hiện rải rác 0.25–0.46.
  Không có ground truth để biết đó có phải cảnh báo sai hay không.
* EAR không phân biệt nhắm mắt với nhìn xuống rất thấp (khi không có điện thoại).
* Tốc độ 7.5 FPS trên CPU; chưa đo trên phần cứng khác.
* Chỉ một người, một buổi, một máy: không suy ra độ chính xác.
