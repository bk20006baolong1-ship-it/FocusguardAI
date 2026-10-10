# Kiểm tra với camera thật (macOS)

Mục đích: xác minh **tích hợp hệ thống và hành vi** trên webcam thật. Tài liệu này **không** đo độ chính xác;
kết quả thực tế vẫn là `NOT_EVALUATED` cho tới khi có bộ dữ liệu có nhãn (mục 6).

## 0. Chuẩn bị (một lần)

```bash
cd /Users/dkdeveloper/projects/focusguardAI
git checkout fix/competition-core-hardening
source .venv/bin/activate            # hoặc: python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # OpenCV, MediaPipe, ultralytics (YOLOv8), insightface, onnxruntime, eventlet, Firestore libs
python tools/download_models.py      # tải InsightFace buffalo_sc vào ~/.insightface (cần mạng, một lần)
```

* `yolov8n.pt` và `face_landmarker.task` đã có sẵn trong repo.
* `insightface` có thể cần biên dịch C++ trên macOS (Xcode Command Line Tools: `xcode-select --install`).
* macOS phải cho phép **Terminal / ứng dụng chạy Python** truy cập Camera
  (System Settings → Privacy & Security → Camera). Nếu iPhone Continuity Camera đang bật, camera 0 có thể là iPhone.

Biến môi trường mà code thực sự đọc (không có `CAMERA_MODE`; pipeline luôn dùng camera index 0):

| Biến | Dùng cho test này |
|---|---|
| `DATABASE_TYPE=sqlite`, `SQLITE_PATH=...` | Dùng SQLite riêng cho buổi test. `.env` local đang để `firestore` + emulator; biến đặt trong shell sẽ ghi đè `.env` mà không phải sửa file. |
| `FOCUSGUARD_ENV=development` | cho phép tài khoản demo |
| `DEMO_ACCOUNT_PASSWORD` | mật khẩu tài khoản demo (mặc định `123`) |
| `SOCKETIO_ASYNC_MODE` | để trống = eventlet (có trong requirements.txt), hoặc `threading` |
| `HOST`, `PORT` | mặc định `127.0.0.1:5001` |
| `FG_*` | ghi đè ngưỡng (xem `focusguard/config.py`); **để mặc định** khi test |

> Trên macOS, chạy các lệnh cần camera từ **Terminal.app** (đã được cấp quyền Camera). Kết quả lần chạy cuối:
> `docs/REAL_CAMERA_RESULTS.md`.

## 1. Test phần cứng tự động

```bash
FOCUSGUARD_HARDWARE_TESTS=1 FOCUSGUARD_CAMERA_INDEX=0 python -m pytest tests/hardware -rs -v
```

Không có pytest marker `hardware`; các test trong `tests/hardware/` tự skip (`HARDWARE_REQUIRED`) nếu
thiếu `FOCUSGUARD_HARDWARE_TESTS=1`. Ba test: camera trả frame, YOLOv8 + MediaPipe load được, InsightFace load được.

## 2. Chạy app thật

```bash
mkdir -p data
DATABASE_TYPE=sqlite SQLITE_PATH=data/camera_test.db FOCUSGUARD_ENV=development python app.py
# mở http://127.0.0.1:5001
```

`data/` đã được git-ignore. Xoá `data/camera_test.db` để bắt đầu lại từ đầu.

**Đăng ký khuôn mặt** (trước khi bắt đầu lớp, vì trình duyệt và OpenCV dùng chung webcam):
đăng nhập `admin` → Tài khoản → đăng ký khuôn mặt cho `hocsinh` (người A) và `diyasharma` (người B), đều thuộc
lớp 10A1 (class_id 1). Hệ thống chỉ lưu vector đặc trưng, không lưu ảnh.

**Bắt đầu lớp:** đăng nhập `teacher` → Dashboard → chọn **Lớp học Trực tiếp** → **Bắt đầu giám sát**.
Luồng video chỉ chạy khi trang giáo viên đang mở `/video_feed`.

**Quan sát trạng thái** (cửa sổ terminal thứ hai, dùng cookie của giáo viên):

```bash
J=/tmp/fg_teacher.cookie
curl -s -c $J -d "username=teacher&password=123" http://127.0.0.1:5001/login >/dev/null
watch -n 1 "curl -s -b $J 'http://127.0.0.1:5001/api/teacher/class_status?class_id=1' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[\"camera_status\"]); [print(s[\"student_id\"], s[\"name\"], s[\"identity_status\"], s[\"active_track_id\"], s[\"attendance_status\"], s[\"visibility_status\"], s[\"focus_state\"], s[\"focus_score\"], s[\"event_counts\"], s[\"away_count\"]) for s in d[\"students\"]]'"
```

(`watch` không có sẵn trên macOS; thay bằng `while true; do ...; sleep 1; done` nếu cần.)

Sau khi kết thúc lớp, xem sự kiện đã lưu:

```bash
sqlite3 data/camera_test.db "select student_id,type,datetime(start_time,'unixepoch','localtime'),round(duration_seconds,1),round(confidence,2),source,class_session_id from focus_events order by id;"
sqlite3 data/camera_test.db "select student_id,attendance_status,round(avg_focus_score,1),round(final_focus_score,1),away_count,round(away_seconds,1),event_counts_json from session_students order by id desc limit 20;"
```

## 3. Kịch bản

Lưu ý khi đọc trạng thái: nếu thấy người nhưng không thấy mặt (cúi xuống, quay lưng lại), `focus_state=UNKNOWN`
và điểm đứng yên. Đây là "không đo được", không phải "tập trung". Nhiều tab cùng xem `/video_feed` sẽ dùng chung
một pipeline; pipeline chỉ dừng khi tab cuối cùng đóng.

Ghi kết quả vào bảng ở cuối: PASS / FAIL + ghi chú. Ngưỡng mặc định: xác nhận danh tính ≥ 3 lần khớp
liên tiếp trong ≥ 0.6 s; PHONE ≥ 1 s; DROWSY ≥ 1.5 s; HEAD_AWAY ≥ 2 s; AWAY ≥ 10 s.

### Kịch bản 1: một học sinh, danh tính ổn định
Người A ngồi trước camera 60 s.
* Overlay chuyển từ `Track n: CANDIDATE` sang tên A. API trả `identity_status=CONFIRMED` và `student_id` của A.
* `student_id` không đổi suốt 60 s, không học sinh nào khác được gán cùng lúc (không trùng).
* `focus_score` chỉ tăng hoặc giảm dần, không bị đặt lại về 100 một cách vô cớ.

### Kịch bản 2: chớp mắt
Chớp mắt tự nhiên nhiều lần trong 30 s.
* `event_counts` không có `DROWSY`, `focus_state` vẫn là `FOCUSED`, không có cảnh báo.

### Kịch bản 3: liếc nhanh
Quay đầu sang bên 1–1.5 s, lặp lại 3 lần.
* Không có `HEAD_AWAY`. Nếu quay giữ đúng 2 s trở lên thì được tính (đó là ngưỡng thiết kế).

### Kịch bản 4: điện thoại
Cầm và dùng điện thoại trước người trong 10–15 s.
* `focus_state=PHONE` sau khoảng 1 s. Nhật ký giáo viên có dòng "…: Dùng điện thoại".
* Sau khi cất điện thoại, `focus_events` có một dòng `PHONE` với `duration_seconds` khoảng 10–15,
  `confidence` trong khoảng (0, 1], đúng `student_id`, `source=classroom_camera`.
* Ghi chú: YOLOv8n có thể bỏ sót điện thoại khi bị che hoặc ở góc nghiêng; ghi lại nếu bị thiếu.

### Kịch bản 5: rời chỗ và quay lại
Rời khỏi khung hình 15–20 s rồi quay lại.
* Trong 10 s đầu: `visibility_status=TEMPORARILY_NOT_VISIBLE`, điểm đứng yên. Sau 10 s: `AWAY`, `away_count=1`.
* Khi quay lại: `VISIBLE`, danh tính được khôi phục đúng `student_id` (track id có thể khác), điểm không bị đặt lại.
* `connection_status` là `NOT_APPLICABLE` ở chế độ camera lớp (khác với visibility); ở chế độ cá nhân,
  trạng thái này chỉ đổi khi socket thật sự ngắt.
* `focus_events` có đúng một dòng `AWAY` với thời lượng xấp xỉ khoảng thời gian vắng.

### Kịch bản 6: hai học sinh đi cắt nhau
A và B (đã đăng ký) đi ngang qua nhau trước camera, lặp lại 3 lần.
* Không có thời điểm nào cùng một `student_id` xuất hiện ở hai track.
* Danh tính không bị đổi chéo sau khi đi qua. Nếu mâu thuẫn kéo dài ≥ 1 s, track có thể tạm về
  `UNCERTAIN`/`CONFLICT` rồi xác nhận lại; chấp nhận được, miễn là **không gán sai**.

### Kịch bản 7: người lạ
Một người chưa đăng ký đứng trước camera 30 s.
* Overlay hiện `Track n: UNKNOWN`/`UNCERTAIN`, không gán tên học sinh nào. Không có dòng điểm danh mới.

### Kịch bản 8: mất / có lại camera
Rút webcam ngoài, hoặc dùng app khác chiếm camera, hoặc đóng tab giáo viên (ngắt `/video_feed`) khoảng 10 s, rồi khôi phục.
* Server không crash. Sau khoảng 3 s không có frame: `camera_status=NO_SIGNAL`, học sinh
  `visibility_status=NO_CAMERA_SIGNAL`, điểm **đóng băng** (không trừ).
* Khi frame trở lại (mở lại dashboard): `camera_status=ACTIVE`, runtime vẫn là buổi học cũ.
* Các trạng thái camera có trong code là `WAITING / ACTIVE / NO_SIGNAL`
  (không có `OFFLINE/RECONNECTING` riêng). ClassroomAI chỉ gọi `cap.open()` lại khi thiết bị không còn ở trạng thái mở;
  nếu thiết bị đã mở nhưng không đọc được, cần đóng/mở lại trang để khởi tạo lại pipeline. Ghi lại hành vi quan sát được.

### Kịch bản 9: kết thúc lớp
Bấm **Kết thúc** khi một hành vi đang diễn ra (ví dụ đang cầm điện thoại).
* Episode đang mở được đóng: có `end_time` trong `focus_events`.
* `session_students` có đủ một dòng cho mỗi học sinh của lớp; ai không xuất hiện là `ABSENT`.
* Popup tóm tắt dùng số liệu thật (có mặt x/y, điểm TB của những em đo được hoặc "Không đủ dữ liệu").
* `/api/teacher/class_status` trả `class_session_active=false`. Bắt đầu lớp mới thì mọi học sinh về `NOT_YET` (không rò trạng thái cũ).

### Bảng kết quả

| # | Kịch bản | Kết quả | Ghi chú |
|---|---|---|---|
| 1 | Danh tính ổn định | | |
| 2 | Chớp mắt | | |
| 3 | Liếc nhanh | | |
| 4 | Điện thoại | | |
| 5 | Rời chỗ và quay lại | | |
| 6 | Hai người cắt nhau | | |
| 7 | Người lạ | | |
| 8 | Mất / có lại camera | | |
| 9 | Kết thúc lớp | | |

Máy / camera / độ sáng / khoảng cách: ____________________

## 4. Chế độ cá nhân (tùy chọn)
Đăng nhập `hocsinh` → Bắt đầu học. Danh tính lấy từ tài khoản đăng nhập (không nhận diện khuôn mặt).
Lặp lại kịch bản 2–5 và 8, rồi kiểm tra bảng `sessions` và `focus_events` (có `session_id`).

## 5. Ghi nhận hiệu năng
Ghi lại FPS quan sát được: video mượt hay giật, và độ trễ từ lúc cầm điện thoại đến khi dashboard báo.
Chưa có công cụ đo tự động, nên phần hiệu năng vẫn là `HARDWARE_REQUIRED`.

## 6. Dữ liệu thật và đánh giá độ chính xác
* Repo **chưa có** công cụ ghi dữ liệu để gán nhãn. `scripts/run_evaluation.py --real-dataset <file.json>` nhận file JSON gồm
  `episodes_gt`, `episodes_pred`, `identity_frames`, `duration_seconds` (xem `focusguard/evaluation/runner.py`).
  Chưa có file đó thì kết quả là **NOT_EVALUATED**.
* Chỉ thu dữ liệu khi có **đồng ý bằng văn bản** của học sinh và phụ huynh. Lưu trong `evaluation/private/` hoặc
  `evaluation/datasets/` (đã git-ignore). **Không commit** video, ảnh khuôn mặt hay nhãn chứa danh tính thật.

## 7. Firestore (emulator)
Backend Firestore được kiểm tra **trên emulator** bằng bộ test hợp đồng `tests/firestore/`: tài khoản, hash và migrate
mật khẩu, lớp và thành viên lớp, embedding khuôn mặt, `sessions`, `class_sessions`, `session_students`,
`focus_events`, `focus_snapshots`, và vòng đời một buổi học qua `SessionManager`.

```bash
bash scripts/firestore_emulator_check.sh      # cần Java 11+ và Node; dùng project demo-focusguard (chỉ emulator)
```

Bộ test tự từ chối chạy nếu thiếu `FIRESTORE_EMULATOR_HOST` hoặc project không bắt đầu bằng `demo-`, nên không thể
ghi vào Firestore production. **Firestore production (Cloud) chưa được kiểm tra.**
