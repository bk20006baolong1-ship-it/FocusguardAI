# FocusGuard – Giám sát tập trung học tập bằng camera (bản thi)

FocusGuard dùng camera để ghi nhận **hành vi quan sát được** của học sinh (dùng điện thoại, nhắm mắt kéo dài,
quay đầu khỏi hướng học, rời chỗ) và tính **điểm tập trung theo thời gian** bằng một bộ quy tắc minh bạch.
Có bốn vai trò: **Học sinh**, **Giáo viên**, **Phụ huynh**, **Quản trị viên**.

**Trạng thái: `CODE_COMPLETE_CLOUD_VERIFIED_REAL_CLASSROOM_EVIDENCE_PENDING`** — mã nguồn, kiểm thử, bảo mật, quyền
riêng tư và công cụ đánh giá lớp thật đã hoàn thành và kiểm chứng trực tiếp trên máy chủ đám mây; độ chính xác trên
lớp học thật **chưa đo** (cần camera, lớp học, sự đồng ý và nhãn thực tế). Camera IP EZVIZ / RTSP / ONVIF đã được
kiểm thử bằng camera mô phỏng, **chưa** thử trên camera EZVIZ thật: `EZVIZ_HARDWARE_VERIFICATION_PENDING`.
Bảng đối chiếu từng góp ý BTC với mã, test và bằng chứng: [docs/BTC_FEEDBACK_CLOSURE.md](docs/BTC_FEEDBACK_CLOSURE.md).

> Trung thực về năng lực: các mô hình nhận dạng (YOLOv8, MediaPipe Face Landmarker, InsightFace) là mô hình
> có sẵn. Phần "đánh giá tập trung" là **heuristic theo thời gian**, không phải mô hình học máy dự đoán.
> Hệ thống **không** nhận diện cảm xúc và **không** đo hướng nhìn (gaze). Độ chính xác trên dữ liệu lớp học
> thật: **NOT_EVALUATED** (chưa có bộ dữ liệu có nhãn). Xem [docs/PRIVACY_AND_AI_SAFETY.md](docs/PRIVACY_AND_AI_SAFETY.md).

## Quick Start (bắt đầu nhanh)

README này là điểm vào duy nhất. Mọi thư viện Python nằm trong các tệp `requirements*.txt`, mọi công cụ Node nằm
trong `package.json` + `package-lock.json`: chỉ cần `pip install -r …` và `npm ci`, không cài gói lẻ.

```bash
cd FocusGuard                          # thư mục mã nguồn
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt        # ứng dụng + camera + mô hình AI + công cụ thực địa
cp .env.example .env                   # rồi thay các giá trị <placeholder>
python tools/download_models.py        # tải mô hình InsightFace (một lần)
python app.py                          # http://127.0.0.1:5001
```

**Camera:** chạy web xong, vào **Cài đặt › Camera › Tìm camera trong mạng LAN** (xem [Camera setup](#camera-setup-cài-đặt--camera)).
FocusGuard dùng được mọi camera IP có **RTSP hoặc ONVIF** trong cùng mạng LAN, cùng webcam của máy chủ; **ưu tiên
hỗ trợ camera IP EZVIZ** (bật RTSP trong ứng dụng EZVIZ trước). Camera chỉ xem qua đám mây không kết nối được. Không cần
cài thêm thư viện camera: mọi thứ đã có trong `requirements.txt`. Danh sách đầy đủ: [Camera tương thích](#camera-tương-thích).

### Cần chuẩn bị ngoài pip (không phải thư viện Python)

| Thành phần | Dùng cho | Ghi chú |
|---|---|---|
| Python 3.12 (khuyến nghị; 3.10–3.13 dùng được) | mọi thứ | kiểm chứng trên 3.12 |
| Webcam | chạy camera thật, đánh giá lớp thật | không cần cho kiểm thử, mô phỏng hay giao diện |
| Quyền camera của hệ điều hành | camera thật | macOS: chạy từ Terminal.app và cho phép Camera; Windows: Settings › Privacy › Camera |
| Node.js 20+ (kèm npm) | Firebase Emulator, kiểm tra cú pháp JS | Firebase CLI được cài bằng `npm ci` từ `package-lock.json` |
| Java 11+ (khuyến nghị 21) | Firebase Emulator | chỉ cần khi chạy kiểm tra Firestore emulator |
| Trình duyệt Chromium cho Playwright | đi thử giao diện tự động | tải bằng `python -m playwright install chromium` (tệp chạy, không phải gói Python) |

### Tệp dependency

| Tệp | Nội dung | Khi nào cài |
|---|---|---|
| `requirements.txt` | Flask, Socket.IO, eventlet, OpenCV, MediaPipe, YOLOv8 (ultralytics), InsightFace, ONNX Runtime, NumPy, Firebase/Firestore, cryptography (mã hóa mật khẩu camera) | chạy FocusGuard, camera thật / camera IP, công cụ thực địa thật, tải/dùng mô hình |
| `requirements-ci.txt` | Flask, Flask-SocketIO, NumPy, pytest, cryptography | kiểm thử không cần phần cứng, đánh giá SYNTHETIC/REPLAY, quét bảo mật |
| `requirements-firestore-ci.txt` | `-r requirements-ci.txt` + google-cloud-firestore, google-auth | kiểm tra Firestore emulator |
| `requirements-dev.txt` | `-r requirements-ci.txt` + Playwright, Pillow | đi thử giao diện bằng trình duyệt (công cụ cho nhà phát triển) |
| `package.json` + `package-lock.json` | firebase-tools (Firebase CLI) | `npm ci` cho Firebase Emulator |

`python scripts/dependency_check.py` (chạy trong `ci_check.sh`) kiểm tra mọi import đều có trong đúng tệp và tài
liệu không còn lệnh cài gói lẻ.

## Camera setup (Cài đặt › Camera)

Không cần biết RTSP, ONVIF, cổng hay đường dẫn luồng. Máy chạy FocusGuard và camera phải **cùng mạng LAN**.

1. Chạy FocusGuard (`python app.py`) và đăng nhập bằng tài khoản giáo viên (hoặc quản trị viên cho camera của trường).
2. Mở **Camera** ở thanh bên trái (Cài đặt › Camera).
3. Bấm **Tìm camera trong mạng LAN** (chỉ chạy khi bạn bấm; chỉ dò mạng nội bộ, tối đa ~15 giây).
4. Chọn camera tìm thấy (“Cần xác thực” / “Có thể kết nối”).
5. Nhập tên đăng nhập và **mật khẩu / mã xác minh**. Camera EZVIZ: tên đăng nhập thường là `admin`, mật khẩu là mã
   xác minh 6 chữ in hoa trên nhãn dưới đáy camera.
6. Bấm **Kiểm tra kết nối**: hệ thống tự tìm luồng video (ONVIF hoặc đường dẫn RTSP quen thuộc của hãng).
7. Bấm **Xem trước** (tối đa 60 giây, không phân tích, không tạo sự kiện).
8. Bấm **Lưu camera**; tick **Đặt làm camera mặc định** nếu muốn.
9. Quản trị viên: camera của trường → **Gán lớp** để giáo viên lớp đó không phải cài lại.

Khi bắt đầu buổi học bằng camera lớp học, FocusGuard chọn: camera được gán cho lớp → camera mặc định của giáo viên →
hỏi chọn. Chưa lưu camera nào: dùng webcam 0 của máy chủ như trước. Mất tín hiệu camera: hệ thống tự kết nối lại,
điểm tạm dừng, **không ai bị tính là rời chỗ hay vắng mặt** vì lỗi camera.

### Camera tương thích

FocusGuard kết nối được **mọi camera IP phát luồng chuẩn RTSP hoặc ONVIF trong mạng LAN**, không phụ thuộc hãng, và
webcam của máy chủ. Không dùng thư viện riêng của hãng nào: dò camera, RTSP và ONVIF được viết bằng thư viện chuẩn
Python, video đọc bằng OpenCV (đã có trong `requirements.txt`).

| Mức hỗ trợ | Camera | Cách dùng |
|---|---|---|
| **Ưu tiên: camera IP EZVIZ** | EZVIZ có bật RTSP / ONVIF (C1C, H1C… tùy firmware) | Tìm camera → tên `admin` + mã xác minh; tự thử đường dẫn EZVIZ (`/H.264`, `/Streaming/Channels/101`…); camera EZVIZ chưa bật RTSP được nhận ra và hiện hướng dẫn bật |
| Có gợi ý theo hãng | Hikvision, Dahua, Imou, TP-Link (Tapo), Reolink | Tìm camera hoặc thêm thủ công; tự thử đường dẫn RTSP quen thuộc của hãng |
| Chuẩn chung | Camera IP khác có RTSP / ONVIF | Tìm camera (RTSP cổng 554 hoặc ONVIF); RTSP cổng khác: **Thêm camera thủ công** (IP, cổng, đường dẫn) |
| Không kết nối được | Camera **chỉ xem qua đám mây** (không có RTSP / ONVIF trong LAN), camera ở mạng khác / Wi-Fi khách, camera chỉ nhận mật khẩu trong đường dẫn | Bật RTSP / ONVIF trong ứng dụng của hãng nếu có, hoặc dùng camera khác |

**Camera EZVIZ:** bật luồng LAN trước — ứng dụng EZVIZ › Cài đặt › Xem trực tiếp qua LAN (LAN Live View) › chọn
camera › ⚙️ › Cài đặt dịch vụ cục bộ (Local Service Settings) › bật **RTSP**. Không phải mẫu / firmware EZVIZ nào cũng
có mục này; trang thông số H1C chỉ ghi giao thức đám mây EZVIZ. FocusGuard **không** đăng nhập đám mây EZVIZ, nên camera
không có luồng LAN hiện “Camera không tương thích”. Hỗ trợ EZVIZ đã kiểm thử bằng camera mô phỏng, **chưa** kiểm chứng
trên camera EZVIZ thật (`EZVIZ_HARDWARE_VERIFICATION_PENDING`). Hướng dẫn chi tiết, C1C/H1C, xử lý sự cố:
[docs/IP_CAMERA_SETUP.md](docs/IP_CAMERA_SETUP.md).

Bảo mật: mật khẩu / mã xác minh được mã hóa (`FG_CAMERA_CREDENTIAL_KEY`), không bao giờ hiện lại hay ghi nhật ký;
chỉ kết nối tới **mạng camera được phép** và cổng camera (RTSP / ONVIF): `FG_CAMERA_ALLOWED_NETWORKS` (ví dụ
`192.168.1.0/24`), hoặc mặc định dải /24 của chính máy chủ — camera tìm thấy bằng “Tìm camera” dùng được ngay; địa chỉ
nhập tay ở production phải nằm trong danh sách cho phép. Không dò dịch vụ nội bộ hay mạng khác; chặn Internet công
cộng, 169.254.169.254, `file://`… ([docs/IP_CAMERA_SETUP.md](docs/IP_CAMERA_SETUP.md) › Mạng camera được phép).

## Classroom initialization, tagging and attendance (Khởi tạo danh sách, gắn tên, điểm danh)

1. **Giám sát** → chế độ **Camera lớp học** → tick **Khởi tạo danh sách bằng camera** → **Bắt đầu buổi học**.
2. FocusGuard khoanh từng **người** trên hình camera. Học sinh đã có mẫu nhận diện hiện **“AI đề xuất”**.
3. Bấm vào khung một người → **Xác nhận** đề xuất hoặc **Chọn học sinh khác** → chọn trong danh sách lớp. Mỗi học sinh
   chỉ gắn cho một người; mỗi người chỉ một học sinh. “Xác nhận các đề xuất độ tin cậy cao” chỉ nhận các đề xuất rất chắc.
4. **Có đồng ý nhận diện khuôn mặt** (do quản trị viên ghi nhận): sau khi gắn tên, hệ thống theo dõi người đó vài giây
   và giữ lại vài mẫu khuôn mặt tốt, đa dạng (chỉ vector số, không lưu ảnh) → lần sau tự nhận ra.
   **Không có đồng ý**: gắn tên chỉ dùng trong buổi học, không tạo mẫu.
5. **Hoàn tất danh sách & bắt đầu**: từ đây mới chấm điểm và ghi sự kiện (lúc khởi tạo không ai bị trừ điểm).
6. Buổi sau: học sinh được nhận ra tự động. **AI nhận nhầm** → bấm vào người đó → **Đổi học sinh**: danh tính trong
   buổi sửa ngay, mẫu đang thu theo danh tính sai bị loại, mẫu cho học sinh đúng được thu tiếp (nếu có đồng ý).
   Khuôn mặt mâu thuẫn với tên giáo viên đã gắn → **“Cần xem lại”**, không bao giờ tự đổi tên.
7. **Camera mất kết nối / tải lại trang**: tên giáo viên đã gắn **được giữ cho cả buổi học** (không cần đồng ý sinh
   trắc, không lưu khuôn mặt). Khi có hình lại, người ngồi **đúng chỗ đó** (duy nhất, không giống học sinh khác) được
   gắn lại tên sau vài giây — “khôi phục theo vị trí”; không chắc chắn → học sinh hiện “chờ thấy lại”, giáo viên gắn lại.
8. **Điểm danh**: đầu buổi mọi học sinh “Chưa thấy”; được xác nhận trên camera → **Có mặt** (sau `FG_LATE_AFTER_SECONDS`
   → **Đi muộn**); kết thúc buổi mà chưa từng được xác nhận → **Vắng mặt**. Mỗi lần xác nhận là một **bằng chứng**
   gắn với danh tính đã dùng; giáo viên sửa tên chỉ bỏ bằng chứng của danh tính sai — lần điểm danh đúng trước đó
   được giữ nguyên (không bao giờ thành “đi muộn” vì một lần sửa). Người chưa xác định trong lớp được đếm riêng (“Chưa
   xác định”), không bị gán thành học sinh vắng. Giáo viên sửa điểm danh ở trang **Điểm danh** (cả buổi đã kết thúc);
   sửa của giáo viên không bị hệ thống ghi đè.

Chi tiết: [docs/CLASSROOM_IDENTITY.md](docs/CLASSROOM_IDENTITY.md), [docs/ATTENDANCE.md](docs/ATTENDANCE.md),
[docs/CONSENT_AND_RETENTION.md](docs/CONSENT_AND_RETENTION.md).

## Development / Browser Verification

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium   # tải trình duyệt (một lần)
python tools/ui_walkthrough.py          # ứng dụng thật trên CSDL tạm, 1366 px + 390 px
```

Kiểm tra lỗi JavaScript, request lỗi, cuộn ngang, hộp thoại gốc của trình duyệt, XSS; luồng giáo viên, quản trị viên,
học sinh, phụ huynh; Cài đặt › Camera với camera LAN mô phỏng; khởi tạo danh sách, gắn tên, sửa tên, điểm danh trên lớp
học mô phỏng (SYNTHETIC, `focusguard/classroom_sim.py`). In `WALKTHROUGH: N/N checks PASS`.

## Hardware-free Verification

```bash
pip install -r requirements-ci.txt
bash scripts/ci_check.sh
```

compile → import → pytest → cú pháp JS → quét bảo mật/public repo + dependency → đánh giá SYNTHETIC → gói đánh giá
cloud (REPLAY, nhiều người, benchmark, hiệu chỉnh) → kiểm tra quyền riêng tư của báo cáo → `CI CHECK: PASS`.

Đây là cổng kiểm chứng chính thức, chạy trực tiếp trong môi trường sạch (bản mã nguồn mới, venv mới).

Test phần cứng (`tests/hardware/`) mặc định **SKIPPED: HARDWARE_REQUIRED**; trên máy có webcam:

```bash
pip install -r requirements.txt -r requirements-ci.txt
FOCUSGUARD_HARDWARE_TESTS=1 python -m pytest tests/hardware -rs
```

## Firestore Emulator Verification

```bash
pip install -r requirements-firestore-ci.txt
npm ci
bash scripts/firestore_emulator_check.sh
```

Chạy hợp đồng lưu trữ trên Firestore **emulator** (project `demo-focusguard`, không bao giờ chạm production); giới hạn
thời gian, tự tắt emulator, trả đúng mã lỗi của test. Cần Java và Node.js (bảng trên).

## Real Classroom Dry Run

```bash
python tools/field_test.py --simulate
```

Diễn tập toàn bộ quy trình thực địa trên dữ liệu SYNTHETIC (CSDL tạm, không phải dữ liệu thật, không cần camera,
không bao giờ là bằng chứng thực tế). Cần `requirements.txt` hoặc `requirements-ci.txt`.

## Real Classroom Run

Kiểm tra camera đã lưu trong Cài đặt › Camera (ví dụ tên `classroom-front`), rồi chạy buổi thực địa với camera đó:

```bash
python tools/ip_camera_check.py --camera-name classroom-front
python tools/field_test.py --camera-name classroom-front --class-id <CLASS_ID> --teacher <TEACHER_USERNAME>
```

Không có camera đã lưu: `python tools/field_test.py --class-id <CLASS_ID> --teacher <TEACHER_USERNAME>` (camera của lớp /
mặc định của giáo viên, nếu không có thì webcam 0). Không với tới camera: `IP_CAMERA_HARDWARE_REQUIRED` /
`STATUS: HARDWARE_REQUIRED` — không có kết quả giả.

Cần `requirements.txt`, mô hình đã tải và camera. Công cụ dẫn từng bước: kiểm tra trước → đồng ý → camera → tạo buổi
đánh giá → 15 kịch bản BTC → mẫu bảng nhãn → kết thúc → tổng hợp. Không có camera: `STATUS: HARDWARE_REQUIRED`.
Xem [docs/REAL_CLASSROOM_DAY_CHECKLIST.md](docs/REAL_CLASSROOM_DAY_CHECKLIST.md) và mẫu đồng ý
[docs/CONSENT_FIELD_PACK.md](docs/CONSENT_FIELD_PACK.md).

## Finalize Evaluation

```bash
python scripts/finalize_real_classroom_evaluation.py <SESSION_ID> \
  --annotations-csv observer.csv \
  --teacher <TEACHER_USERNAME> \
  --identity-csv identity.csv \
  --calibrate
```

Báo cáo ở `evaluation/reports/real-classroom/<SESSION_ID>/` (đã quét quyền riêng tư); mẫu báo cáo
[evaluation/templates/real_classroom_report.md](evaluation/templates/real_classroom_report.md).

## Advanced / Troubleshooting (camera IP)

* **Thêm camera thủ công** (Cài đặt › Camera): địa chỉ IP, giao thức (Tự động / RTSP / ONVIF), cổng, đường dẫn luồng,
  profile ONVIF; hoặc dán đường dẫn `rtsp://…` — tên/mật khẩu trong đường dẫn được tách ra và mã hóa, đường dẫn gốc
  không được lưu.
* Camera chưa lưu: `FG_CAMERA_SECRET=… python tools/ip_camera_check.py --host 192.168.1.20 [--path /H.264]`
  (mật khẩu qua biến môi trường hoặc nhập khi được hỏi, không đưa vào dòng lệnh).
* “Địa chỉ không thuộc mạng camera được phép”: dùng **Tìm camera**, hoặc quản trị viên đặt
  `FG_CAMERA_ALLOWED_NETWORKS` (mạng / VLAN camera) hoặc thêm một địa chỉ vào `FG_CAMERA_ALLOWED_HOSTS`.
* Đọc thêm: [docs/IP_CAMERA_SETUP.md](docs/IP_CAMERA_SETUP.md) (độ trễ RTSP, kết nối lại, khuôn mặt quá nhỏ…).

## Cải tiến theo góp ý của Ban Tổ chức (Bảng A THCS)

| Góp ý | Đã triển khai | Trạng thái |
|---|---|---|
| A. Lớp học trực tiếp, nhiều học sinh | Chế độ **Đánh giá thực tế** (metadata camera, hoạt động lớp, 15 kịch bản BTC, nhãn thực tế), công cụ thực địa một lệnh `tools/field_test.py`, `scripts/finalize_real_classroom_evaluation.py`, quy trình [REAL_CLASSROOM_EVALUATION_PROTOCOL](docs/REAL_CLASSROOM_EVALUATION_PROTOCOL.md), [checklist ngày đánh giá](docs/REAL_CLASSROOM_DAY_CHECKLIST.md) | Code + cloud (kể cả chạy thử mô phỏng): xong. Lớp thật: **HARDWARE_REQUIRED** |
| B. Nhiều người: cắt nhau, người lạ, che mặt, đổi góc, lớp đông, FPS | 10 kịch bản SYNTHETIC (`focusguard/evaluation/stress.py`), `scripts/benchmark_classroom.py` (RUNTIME_BENCHMARK: không chạy YOLO/InsightFace, học sinh tổng hợp) | Hợp đồng danh tính + hiệu năng runtime: CLOUD_VERIFIED. Camera thật, FPS của mô hình: **HARDWARE_REQUIRED** |
| C. Cảnh báo sai, hiệu chỉnh | Hoạt động lớp do giáo viên chọn + `BehaviorInterpretationPolicy` (`focusguard/policies/`), engine chỉ số thực tế (P/R/F1, báo nhầm/giờ, `NOT_ENOUGH_DATA`), `scripts/calibrate_behavior.py` | Công cụ: xong. Ngưỡng đã hiệu chỉnh: **HUMAN_GROUND_TRUTH_REQUIRED** |
| D. Giáo viên xem lại | Trang **Sự kiện**, Xác nhận đúng / Báo nhầm / Không chắc / Ghi chú, lịch sử, "Cần giáo viên xem lại", chỉ số phản hồi, đoạn minh chứng tùy chọn — [EVENT_REVIEW](docs/EVENT_REVIEW.md) | Xong, có test |
| E. Đồng ý / quyền riêng tư | Đồng ý của phụ huynh theo phạm vi, thu hồi, xóa, thời hạn lưu, nhật ký, xuất dữ liệu, trang **Dữ liệu & quyền riêng tư** — [CONSENT_AND_RETENTION](docs/CONSENT_AND_RETENTION.md) | Xong, có test (SQLite + Firestore emulator) |

**Mặc định FocusGuard không lưu video. Evidence Mode là tùy chọn, chỉ lưu đoạn minh chứng ngắn quanh sự kiện khi
được phép, giới hạn quyền truy cập và tự động xóa theo retention policy.**

Đã triển khai framework đánh giá lớp học thật; kết quả accuracy chỉ được cập nhật sau khi hoàn thành evaluation có
consent và ground truth.

## Trạng thái bản thi

**Đã kiểm chứng**
* Kiến trúc lõi (IdentityManager, FocusEngine duy nhất theo thời gian, SessionRuntime, realtime do máy chủ quyết định,
  phân quyền theo quyền sở hữu): `bash scripts/ci_check.sh`, **675 test pass, 27 skip có lý do**
  (24 cần Firestore emulator, 3 cần phần cứng), `CI CHECK: PASS`. Số test **không phải** độ chính xác của AI.
* Giao diện: mọi trang của bốn vai trò, luồng bắt đầu/kết thúc buổi học, trạng thái camera và trang lỗi có test hồi quy
  (`tests/test_ui_contract.py`); đi thử bằng trình duyệt thật (Playwright, 1366 px và 390 px): 222/222 kiểm tra đạt.
  Xem [docs/UI_UX.md](docs/UI_UX.md).
* Bảo mật và repo công khai: `scripts/security_check.py` PASS trên tree hiện tại.
* Firestore backend **trên emulator** (project `demo-focusguard`, không chạm production):
  `bash scripts/firestore_emulator_check.sh` → **24/24 PASS** (cùng bộ hợp đồng `tests/repo_contract.py` chạy trên SQLite).
* Báo cáo công khai không chứa ảnh, vector khuôn mặt, tên học sinh hay đường dẫn video:
  `python scripts/artifact_privacy_check.py --self-test <thư mục>` → `ARTIFACT_PRIVACY_CHECK: PASS`.
* **Phần cứng và camera thật** (MacBook Air M3, FaceTime HD, khoảng 7.5 FPS): hardware tests 3/3 PASS; ứng dụng web chạy với webcam;
  các kịch bản 1–5, 8, 9 PASS, kịch bản 6–7 cần thêm tình nguyện viên. Xem `docs/REAL_CAMERA_RESULTS.md`
  (đây là kiểm tra tích hợp và hành vi, **không phải** đánh giá độ chính xác).

**Chưa đánh giá**
* Độ chính xác ngoài thực tế: **NOT_EVALUATED** (chưa có bộ dữ liệu có nhãn và có sự đồng ý). Khung đánh giá đã có;
  cần chạy quy trình ở `docs/REAL_CLASSROOM_EVALUATION_PROTOCOL.md`.
* Ngưỡng và hệ số phạt theo hoạt động: giá trị khởi đầu, `calibration_status = UNCALIBRATED_DEFAULT`.
* Luồng camera thật trên giao diện mới: chưa chạy lại (kết quả ở `docs/REAL_CAMERA_RESULTS.md` là trên giao diện cũ;
  pipeline camera và logic chấm điểm không đổi).
* Hai người cắt nhau và người lạ trên camera thật: cần thêm tình nguyện viên (đã có kịch bản 08, 09 trong công cụ thực địa).
* Firestore production (Cloud): chưa kiểm tra.

**Hạn chế đã biết**
* Mã nguồn hiện tại bắt đầu từ snapshot sạch; các bản lưu trữ cũ trước đó còn tài sản nhạy cảm (ảnh khuôn mặt,
  export emulator, `.env` cũ, khoá giả của emulator) — xem `docs/PRIVACY_AND_AI_SAFETY.md`.
* Heuristic EAR không phân biệt nhắm mắt với nhìn xuống rất thấp; YOLOv8n phát hiện điện thoại với độ tin cậy dao động.
* Trên macOS, lệnh cần camera phải chạy từ Terminal.app (quyền Camera).
* Camera chỉ phân tích khi trang Giám sát (giáo viên) hoặc Phiên học (học sinh) đang mở; giao diện có nhắc điều này.
* Giáo viên xác nhận "báo nhầm" thì thống kê sự kiện (Phân tích, tổng kết buổi) không tính sự kiện đó; điểm tập trung
  và số lần rời chỗ **đã chốt** của buổi học cũ không được tính lại.
* Tóm tắt buổi học (điểm, điểm danh — không có dữ liệu sinh trắc) không nằm trong lịch xóa tự động; xóa khi xóa tài
  khoản hoặc theo yêu cầu.
* Vector khuôn mặt cũ (đăng ký trước khi có tính năng đồng ý, không có bản ghi đồng ý) không dùng để nhận diện và được
  đánh dấu trên trang Quyền riêng tư để quản trị viên xóa; hệ thống không tự xóa loại này.
* Đoạn minh chứng (nếu bật) chỉ gồm khung hình đã cắt theo khung của đúng học sinh đó; sự kiện AWAY (học sinh không có
  trong khung hình) vì vậy không có đoạn minh chứng. Tệp minh chứng được bảo vệ bằng quyền tệp (0600), chưa mã hoá.
* Chống CSRF bằng cookie SameSite=Lax + kiểm tra Origin/Referer cùng trang; chưa có token CSRF riêng.

## Kiến trúc

```
Camera → YOLOv8 + ByteTrack (track_id tạm thời) → InsightFace → IdentityManager (student_id chuẩn)
      → StudentRuntimeState → BehaviorAnalyzer (theo thời gian, tín hiệu quan sát được)
      → BehaviorInterpretationPolicy (hoạt động lớp do giáo viên chọn) → FocusEngine (DUY NHẤT, theo giây)
      → SessionRuntime → focus_events (+ cách hiểu theo hoạt động) / focus_snapshots / session_students
      → Giáo viên xem lại (event_reviews, không sửa sự kiện gốc) · Evidence Mode (tắt mặc định)
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
| Lưu trữ (SQLite / Firestore) | `repository.py`, `focusguard/repo_extensions.py` |
| Hoạt động lớp + chính sách diễn giải | `focusguard/policies/` (`behavior_policy.json`) |
| Giáo viên xem lại sự kiện | `focusguard/review.py` |
| Đồng ý, nhật ký, minh chứng, lưu trữ, xuất dữ liệu | `focusguard/privacy/` |
| Đánh giá lớp học thật / chỉ số thực tế | `focusguard/evaluation/real_classroom/` |
| Hiệu chỉnh, kịch bản nhiều người, dữ liệu replay | `focusguard/evaluation/calibration.py`, `stress.py`, `replay.py` |
| Route cho các tính năng trên | `focusguard/web_routes.py` |
| Giao diện xem lại / đánh giá / quyền riêng tư | `static/js/feedback.js`, `privacy.js`, `parent.js` |

## Cấu hình (`.env`)

| Biến | Ý nghĩa |
|---|---|
| `FOCUSGUARD_ENV` | `development` hoặc `production`. Production từ chối cấu hình không an toàn. |
| `FLASK_SECRET_KEY` | Bắt buộc ở production (≥ 32 ký tự ngẫu nhiên). Dev: tự sinh ngẫu nhiên mỗi lần chạy. |
| `ALLOWED_ORIGINS` | Danh sách origin cho Socket.IO. Rỗng = cùng origin. `*` bị cấm ở production. |
| `SEED_DEMO_ACCOUNTS` / `DEMO_ACCOUNT_PASSWORD` | Tài khoản demo chỉ tạo ở môi trường dev. |
| `DATABASE_TYPE` | `sqlite` (mặc định) hoặc `firestore` (chỉ truy cập từ máy chủ). |
| `EVENT_EVIDENCE_ENABLED` | `false` mặc định. Xem `docs/CONSENT_AND_RETENTION.md` cho mọi biến minh chứng/thời hạn lưu. |
| `FACE_EMBEDDING_RETENTION_DAYS` / `EVENT_METADATA_RETENTION_DAYS` / `EVALUATION_DATA_RETENTION_DAYS` | Thời hạn lưu (365 / 365 / 90 ngày). |
| `FG_TRUST_PROXY` | Số proxy ngược tin cậy phía trước (mặc định 0). Chạy sau nginx/Caddy: đặt `1` để kiểm tra cùng trang (chống CSRF) dùng tên miền công khai từ `X-Forwarded-Host`/`X-Forwarded-Proto`. |
| `FG_CONTEXT_POLICY_PATH` | Tệp chính sách theo hoạt động khác (mặc định `focusguard/policies/behavior_policy.json`). |
| `FG_CAMERA_CREDENTIAL_KEY` | Khóa (Fernet) mã hóa mật khẩu / mã xác minh camera. Bắt buộc ở production khi có camera dùng mật khẩu (thiếu → camera đó không dùng được, an toàn); dev: tự tạo `.focusguard/camera_credential.key` (git-ignore). Tạo: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `FG_CAMERA_ALLOWED_NETWORKS` | Mạng camera được phép (CIDR, phân cách dấu phẩy, ví dụ `192.168.1.0/24`). Đặt → **chỉ** các mạng này. Không đặt → dải /24 của chính máy chủ: camera tìm thấy dùng được; địa chỉ nhập tay chỉ ở môi trường phát triển. |
| `FG_CAMERA_ALLOWED_HOSTS` | Thêm từng địa chỉ / dải / tên máy camera (luôn được phép; mặc định trống). |
| `FG_CAMERA_ALLOWED_PORTS` | Cổng camera được phép (mặc định các cổng RTSP / ONVIF: 80, 88, 554, 2020, 5000, 5554, 7447, 8000, 8080, 8554, 8899, 8999, 10554). |
| `FG_LATE_AFTER_SECONDS` / `FG_ATTENDANCE_FINALIZE_SECONDS` | Đi muộn sau bao nhiêu giây (mặc định 300); chốt vắng mặt sớm sau bao nhiêu giây (mặc định: khi kết thúc buổi; chờ nếu còn người chưa xác định). |

Tài khoản demo (chỉ dev, mật khẩu = `DEMO_ACCOUNT_PASSWORD`, mặc định `123`): `admin`, `teacher`, `hocsinh`,
`phuhuynh`. Trang đăng nhập ở môi trường dev chỉ gợi ý tên đăng nhập, không in mật khẩu. Mật khẩu luôn được lưu dạng hash Werkzeug; mật khẩu cũ dạng plaintext được hash lại khi khởi động.

## Đánh giá

```bash
python scripts/run_evaluation.py --output evaluation/reports/latest.json
```

Kết quả hiện có đều trên dữ liệu **SYNTHETIC** (tín hiệu sinh tự động để kiểm tra logic thời gian/danh tính).
Mục `real_world` là **NOT_EVALUATED** cho tới khi có bộ dữ liệu lớp học thật, có nhãn và có sự đồng ý.

```bash
python scripts/run_competition_evaluation.py --output-dir evaluation/reports/ci   # REPLAY + stress + benchmark + calibration
python scripts/benchmark_classroom.py [--heavy]
python scripts/calibrate_behavior.py --replay-fixture | --package P.json | --evaluation-session <id>
python tools/run_classroom_evaluation.py check                                     # STATUS: HARDWARE_REQUIRED nếu không có webcam
```

Phân loại kiểm chứng: **CLOUD_VERIFIED** (code, hợp đồng, bảo mật, chỉ số trên replay, kịch bản nhiều người tổng hợp,
hiệu năng runtime không gồm mô hình phát hiện, Firestore emulator, quy trình thực địa chạy mô phỏng) · **HARDWARE_REQUIRED** (góc camera, lớp đông, ánh sáng, cắt nhau thật, FPS của
mô hình trên máy lớp học) · **HUMAN_GROUND_TRUTH_REQUIRED** (độ chính xác thực tế, ngưỡng đã hiệu chỉnh, tỉ lệ báo
nhầm do giáo viên xác nhận).
