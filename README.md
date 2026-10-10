# FocusGuard – Giám sát tập trung học tập bằng camera (bản thi)

FocusGuard dùng camera để ghi nhận **hành vi quan sát được** của học sinh (dùng điện thoại, nhắm mắt kéo dài,
quay đầu khỏi hướng học, rời chỗ) và tính **điểm tập trung theo thời gian** bằng một bộ quy tắc minh bạch.
Có bốn vai trò: **Học sinh**, **Giáo viên**, **Phụ huynh**, **Quản trị viên**.

> Trung thực về năng lực: các mô hình nhận dạng (YOLOv8, MediaPipe Face Landmarker, InsightFace) là mô hình
> có sẵn. Phần "đánh giá tập trung" là **heuristic theo thời gian**, không phải mô hình học máy dự đoán.
> Hệ thống **không** nhận diện cảm xúc và **không** đo hướng nhìn (gaze). Độ chính xác trên dữ liệu lớp học
> thật: **NOT_EVALUATED** (chưa có bộ dữ liệu có nhãn). Xem [docs/PRIVACY_AND_AI_SAFETY.md](docs/PRIVACY_AND_AI_SAFETY.md).

## Trạng thái bản thi

**Đã kiểm chứng**
* Kiến trúc lõi (IdentityManager, FocusEngine duy nhất theo thời gian, SessionRuntime, realtime do máy chủ quyết định,
  phân quyền theo quyền sở hữu): `bash scripts/ci_check.sh`, 184 test pass.
* Giao diện: mọi trang của bốn vai trò, luồng bắt đầu/kết thúc buổi học, trạng thái camera và trang lỗi có test hồi quy
  (`tests/test_ui_contract.py`) và đã được đi thử bằng trình duyệt ở 4 kích thước màn hình. Xem [docs/UI_UX.md](docs/UI_UX.md).
* Bảo mật và repo công khai: `scripts/security_check.py` PASS trên tree hiện tại.
* Firestore backend **trên emulator**: `bash scripts/firestore_emulator_check.sh` (6 test hợp đồng).
* **Phần cứng và camera thật** (MacBook Air M3, FaceTime HD, khoảng 7.5 FPS): hardware tests 3/3 PASS; ứng dụng web chạy với webcam;
  các kịch bản 1–5, 8, 9 PASS, kịch bản 6–7 cần thêm tình nguyện viên. Xem `docs/REAL_CAMERA_RESULTS.md`
  (đây là kiểm tra tích hợp và hành vi, **không phải** đánh giá độ chính xác).

**Chưa đánh giá**
* Độ chính xác ngoài thực tế: **NOT_EVALUATED** (chưa có bộ dữ liệu có nhãn và có sự đồng ý).
* Luồng camera thật trên giao diện mới: chưa chạy lại (kết quả ở `docs/REAL_CAMERA_RESULTS.md` là trên giao diện cũ;
  pipeline camera và logic chấm điểm không đổi).
* Hai người cắt nhau và người lạ trên camera thật: cần thêm tình nguyện viên.
* Firestore production (Cloud): chưa kiểm tra.

**Hạn chế đã biết**
* Lịch sử git cũ còn tài sản nhạy cảm (ảnh khuôn mặt, export emulator, `.env` cũ, khoá giả của emulator). Không viết lại
  lịch sử trong nhánh này; xem `docs/PRIVACY_AND_AI_SAFETY.md`.
* Heuristic EAR không phân biệt nhắm mắt với nhìn xuống rất thấp; YOLOv8n phát hiện điện thoại với độ tin cậy dao động.
* `mediapipe` kéo theo `opencv-contrib-python` 5.x, đè lên bản `opencv-python` 4.13 được pin.
* Trên macOS, lệnh cần camera phải chạy từ Terminal.app (quyền Camera).
* Camera chỉ phân tích khi trang Giám sát (giáo viên) hoặc Phiên học (học sinh) đang mở; giao diện có nhắc điều này.

## Kiến trúc

```
Camera → YOLOv8 + ByteTrack (track_id tạm thời) → InsightFace → IdentityManager (student_id chuẩn)
      → StudentRuntimeState → BehaviorAnalyzer (theo thời gian) → FocusEngine (DUY NHẤT, theo giây)
      → SessionRuntime → focus_events / focus_snapshots / session_students
      → Socket.IO do máy chủ quyết định, cô lập theo lớp → Analytics chỉ từ dữ liệu thật
```

Chi tiết: [docs/COMPETITION_ARCHITECTURE.md](docs/COMPETITION_ARCHITECTURE.md). Kịch bản demo:
[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

| Thành phần | File |
|---|---|
| Cấu hình ngưỡng tập trung | `focusguard/config.py` |
| IdentityManager | `focusguard/identity.py` |
| StudentRuntimeState | `focusguard/runtime_state.py` |
| BehaviorAnalyzer + BehaviorEpisode | `focusguard/behavior.py` |
| FocusEngine (duy nhất) | `focusguard/focus_engine.py` |
| SessionRuntime / RuntimeRegistry | `focusguard/session_runtime.py` |
| Phân quyền theo quyền sở hữu | `focusguard/authz.py` |
| Room realtime / chặn dữ liệu từ client | `focusguard/realtime.py` |
| Bảo mật (secret, cookie, CORS, mật khẩu) | `focusguard/security.py` |
| Analytics từ dữ liệu thật | `focusguard/analytics.py` |
| Đánh giá (metrics + dữ liệu SYNTHETIC) | `focusguard/evaluation/` |
| Pipeline camera lớp học / cá nhân | `classroom_ai.py`, `camera_ai.py` |
| Web + Socket.IO | `app.py` |
| Trạng thái camera hiển thị cho người dùng | `focusguard/camera_state.py` |
| Giao diện: hệ thống thiết kế | `static/css/app.css` |
| Giao diện: khung trang và các trang theo vai trò | `templates/base.html`, `teacher.html`, `student.html`, `parent.html`, `admin.html`, `login.html`, `error.html` |
| Giao diện: JS dùng chung và theo vai trò | `static/js/core.js`, `teacher.js`, `student.js`, `admin.js` |
| Lưu trữ (SQLite / Firestore) | `repository.py` |

## Cài đặt

Yêu cầu: Python 3.10–3.12, webcam (cho pipeline camera), Node.js (chỉ để kiểm tra cú pháp JS / Firebase Emulator).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # đầy đủ: OpenCV, MediaPipe, YOLOv8, InsightFace
cp .env.example .env                      # rồi thay các giá trị <placeholder>
python tools/download_models.py           # tải InsightFace buffalo_sc (một lần)
python app.py                             # http://127.0.0.1:5001
```

Chỉ chạy kiểm thử / đánh giá (không cần camera):

```bash
pip install -r requirements-ci.txt
bash scripts/ci_check.sh
```

### Cấu hình quan trọng (`.env`)

| Biến | Ý nghĩa |
|---|---|
| `FOCUSGUARD_ENV` | `development` hoặc `production`. Production từ chối cấu hình không an toàn. |
| `FLASK_SECRET_KEY` | Bắt buộc ở production (≥ 32 ký tự ngẫu nhiên). Dev: tự sinh ngẫu nhiên mỗi lần chạy. |
| `ALLOWED_ORIGINS` | Danh sách origin cho Socket.IO. Rỗng = cùng origin. `*` bị cấm ở production. |
| `SEED_DEMO_ACCOUNTS` / `DEMO_ACCOUNT_PASSWORD` | Tài khoản demo chỉ tạo ở môi trường dev. |
| `DATABASE_TYPE` | `sqlite` (mặc định) hoặc `firestore` (chỉ truy cập từ máy chủ). |

Tài khoản demo (chỉ dev, mật khẩu = `DEMO_ACCOUNT_PASSWORD`, mặc định `123`): `admin`, `teacher`, `hocsinh`,
`phuhuynh`. Trang đăng nhập ở môi trường dev chỉ gợi ý tên đăng nhập, không in mật khẩu. Mật khẩu luôn được lưu dạng hash Werkzeug; mật khẩu cũ dạng plaintext được hash lại khi khởi động.

## Kiểm thử & CI

* `bash scripts/ci_check.sh`: compile → import → pytest → kiểm tra cú pháp JS → quét bảo mật/public repo → đánh giá SYNTHETIC.
* GitHub Actions: `.github/workflows/ci.yml` chạy cùng script.
* Test phần cứng (camera, model) nằm ở `tests/hardware/`, **SKIPPED: HARDWARE_REQUIRED** trừ khi đặt
  `FOCUSGUARD_HARDWARE_TESTS=1` trên máy có webcam.

## Đánh giá

```bash
python scripts/run_evaluation.py --output evaluation/reports/latest.json
```

Kết quả hiện có đều trên dữ liệu **SYNTHETIC** (tín hiệu sinh tự động để kiểm tra logic thời gian/danh tính).
Mục `real_world` là **NOT_EVALUATED** cho tới khi có bộ dữ liệu lớp học thật, có nhãn và có sự đồng ý.
