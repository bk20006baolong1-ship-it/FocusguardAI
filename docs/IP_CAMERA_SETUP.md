# Camera IP (EZVIZ / RTSP / ONVIF) — cài đặt và xử lý sự cố

Người dùng bình thường chỉ cần **Cài đặt › Camera** (README › Camera setup). Trang này dành cho người cài đặt và hỗ trợ
kỹ thuật.

## 1. Điều kiện

* Máy chạy FocusGuard và camera **cùng mạng LAN** (cùng Wi-Fi / router / VLAN cho phép multicast). FocusGuard không đi
  qua Internet tới camera và không dùng dịch vụ đám mây của hãng.
* Camera phải cung cấp luồng video **trong mạng LAN**: RTSP (trực tiếp hoặc qua ONVIF `GetStreamUri`).
* Máy chủ cần `requirements.txt` (OpenCV có FFmpeg). Khóa mã hóa mật khẩu camera: `FG_CAMERA_CREDENTIAL_KEY`
  (bắt buộc ở production; dev tự tạo trong `.focusguard/`, đã git-ignore).

## 2. Tìm camera trong mạng LAN (chỉ khi bấm nút)

Thứ tự, tổng thời gian tối đa ~15 giây (5–20 s), chạy nền, có thể **Dừng tìm**:

1. **ONVIF WS-Discovery** (multicast, TTL 1): tên, model, cổng dịch vụ ONVIF.
2. **SSDP** (TTL 1): chỉ bổ sung tên hãng cho thiết bị đã tìm thấy bằng cách khác.
3. **Bảng ARP / neighbour** của hệ điều hành: thiết bị đã thấy trong LAN được dò trước.
4. **Dò có giới hạn**: cổng RTSP 554 của các máy trong **mạng camera của chính máy chủ** — dải /24 của máy, thu hẹp
   theo `FG_CAMERA_ALLOWED_NETWORKS` nếu có (tối đa 2 dải, 48 kết nối song song, 0,4 s mỗi kết nối). Không bao giờ dò
   mạng khác hay toàn bộ dải nội bộ. Máy có RTSP được hỏi `OPTIONS` / `DESCRIBE` **không kèm mật khẩu** để biết có cần
   xác thực không. Thiết bị trả lời WS-Discovery / SSDP / ARP nằm ngoài mạng camera được phép bị bỏ qua.

mDNS không được dùng (camera hiếm khi quảng bá video qua mDNS, và sẽ phải thêm thư viện). Kết quả không chứa địa chỉ
MAC; địa chỉ IP chỉ hiện cho giáo viên / quản trị viên.

## 3. Kết nối dễ (easy connect)

Sau khi nhập tên đăng nhập + mật khẩu / mã xác minh, máy chủ thử theo thứ tự:

1. đường dẫn luồng người dùng nhập (chế độ nâng cao);
2. ONVIF: hãng / model, các profile, `GetStreamUri` của profile phân giải cao nhất (địa chỉ thiết bị quảng bá không
   được tin: luôn dùng IP đã kiểm tra);
3. đường dẫn RTSP quen thuộc theo hãng (EZVIZ: `/H.264`; họ Hikvision: `/Streaming/Channels/101`…), rồi một danh sách
   chung ngắn.

**Mật khẩu sai chỉ tốn đúng 1 lần đăng nhập thất bại** (dừng ngay ở lỗi 401): camera EZVIZ / Hikvision khóa tài khoản
sau vài lần sai. Sau đó luồng được mở bằng OpenCV qua cùng bộ đọc của lớp học để đo độ phân giải, FPS, độ trễ.

Trạng thái: `DEVICE_FOUND`, `AUTH_REQUIRED`, `STREAM_FOUND`, `CONNECTED`; lỗi: `AUTH_FAILED`,
`DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM`, `STREAM_UNAVAILABLE`, `UNREACHABLE`, `HOST_NOT_ALLOWED`.

## 4. EZVIZ (C1C, H1C và mẫu khác)

* Tên đăng nhập: `admin`; mật khẩu: **mã xác minh** (verification code) 6 chữ in hoa trên nhãn dưới đáy camera.
* Một số mẫu / firmware cần bật **RTSP / ONVIF / LAN live view** trong ứng dụng EZVIZ trước; một số **không có** luồng
  LAN → “Camera không tương thích”. FocusGuard không đăng nhập đám mây EZVIZ, không lấy mật khẩu đám mây, không
  dịch ngược giao thức đám mây.
* **Chưa kiểm chứng trên camera EZVIZ thật** (`EZVIZ_HARDWARE_VERIFICATION_PENDING`): mọi đường đi đã được kiểm thử
  bằng máy chủ RTSP mô phỏng (Digest auth, đường dẫn đúng/sai) và camera mô phỏng. Kiểm chứng C1C/H1C:
  1. lưu camera trong Cài đặt › Camera (tên ví dụ `classroom-front`);
  2. `python tools/ip_camera_check.py --camera-name classroom-front --seconds 30 --test-reconnect`;
  3. ghi lại `RESOLUTION`, `MEASURED_FPS`, `FRAME_AGE_MS`, `STALE_FRAMES`, `RECONNECTS`, `STATUS` (không có mật khẩu
     trong kết quả) và firmware của camera.

## 5. Thêm camera thủ công (nâng cao)

Địa chỉ IP (hoặc tên máy trong LAN), giao thức Tự động / RTSP / ONVIF, cổng, đường dẫn luồng, profile ONVIF. Có thể
dán `rtsp://<tên>:<mật khẩu>@192.168.1.20:554/H.264`: tên và mật khẩu được tách ra, mật khẩu mã hóa, **đường dẫn gốc không
được lưu** (chỉ lưu host / cổng / đường dẫn). Đường dẫn luồng có tham số mật khẩu (`?user=…&password=…`,
`token=`, `pwd=`…) bị từ chối — đường dẫn được lưu và hiển thị dạng thường, nên mật khẩu phải nhập ở ô Mật khẩu (được
mã hóa). Camera chỉ chấp nhận mật khẩu trong đường dẫn (một số đầu ghi XMEye) chưa được hỗ trợ.

## 6. Mạng camera được phép (camera-network policy)

Nút “Kiểm tra” và việc tìm camera làm máy chủ mở kết nối mạng, nên chỉ được tới **mạng camera** — không phải mọi
địa chỉ nội bộ (không dò dịch vụ nội bộ, Docker, máy chủ trường hay mạng khác qua trang Camera).

| Cấu hình | Camera được dùng |
|---|---|
| `FG_CAMERA_ALLOWED_NETWORKS=192.168.1.0/24,10.20.30.0/24` | **chỉ** các mạng này (cả camera tìm thấy lẫn nhập tay); không còn “mọi địa chỉ nội bộ” |
| không đặt (mặc định) | dải /24 của chính máy chủ: camera **tìm thấy** bằng “Tìm camera” dùng được; địa chỉ **nhập tay** dùng được ở môi trường phát triển, còn ở production phải nằm trong `FG_CAMERA_ALLOWED_NETWORKS` / `FG_CAMERA_ALLOWED_HOSTS` |
| `FG_CAMERA_ALLOWED_HOSTS=192.168.5.20,cam.truong.vn` | thêm từng địa chỉ / tên máy cụ thể (luôn áp dụng) |
| `FG_CAMERA_ALLOWED_PORTS` | cổng được phép; mặc định chỉ cổng RTSP / ONVIF (80, 88, 554, 2020, 5000, 5554, 7447, 8000, 8080, 8554, 8899, 8999, 10554) |

* Địa chỉ của **chính máy chủ** không bao giờ là camera. Cổng không phải cổng camera (22, 3306, 6379…) bị từ chối, kể
  cả cổng do thiết bị ONVIF tự quảng bá (dịch vụ media / luồng).
* Camera đã lưu nhớ **nguồn gốc** (`DISCOVERED` tìm thấy / `MANUAL` nhập tay) và được kiểm tra lại theo chính sách
  hiện hành **mỗi lần kết nối** — đổi `FG_CAMERA_ALLOWED_NETWORKS` có hiệu lực ngay cả với camera đã lưu. Camera lưu từ
  bản trước được coi là nhập tay: ở production hãy “Tìm camera” lại hoặc thêm mạng camera vào cấu hình.
* “Kiểm tra” bị giới hạn 10 lần / phút / người dùng.

## 7. Bảo mật

* **Mật khẩu**: mã hóa Fernet (AES + HMAC) bằng `FG_CAMERA_CREDENTIAL_KEY` trước khi ghi CSDL; chỉ giải mã trên máy
  chủ lúc kết nối; API chỉ trả `has_credentials`. Đổi khóa → camera báo “cập nhật xác thực”, không trả dữ liệu sai.
* **Không ghi nhật ký** mật khẩu, mã xác minh, header Authorization, URL có mật khẩu: một hàm che chung
  (`focusguard/cameras/redact.py`) cho lỗi, nhật ký, trạng thái, audit; quét bảo mật chặn URL có mật khẩu trong mã nguồn.
* **SSRF**: chỉ mạng camera được phép (mục 6); luôn chặn loopback, địa chỉ của chính máy chủ, 169.254.0.0/16
  (169.254.169.254), fe80::/10, multicast, 100.64/10, địa chỉ công cộng, `file://`, `ftp://`, `gopher://`, `http://`,
  `unix:`… Tên máy được phân giải lại mỗi lần kết nối, **mọi** địa chỉ phải nằm trong mạng camera, và kết nối dùng IP
  đã kiểm tra (chống DNS rebinding). Địa chỉ thiết bị ONVIF quảng bá luôn được ghim về IP thiết bị. Không có endpoint
  “tải URL bất kỳ”.
* Chuyển hướng RTSP (3xx) không bao giờ được theo khi thăm dò; thiết bị trả 3xx bị coi là “không tương thích”. Giới
  hạn còn lại: khi mở luồng, OpenCV/FFmpeg có thể tự theo chuyển hướng RTSP mà ứng dụng không chặn được — một thiết bị
  độc hại **trong** mạng camera được phép có thể khiến máy chủ kết nối (không đọc được phản hồi) tới địa chỉ khác.
  Khi triển khai, hãy đặt tường lửa ra (egress) cho máy chủ: chỉ cho phép mạng / cổng camera.
* Tên máy trong `FG_CAMERA_ALLOWED_HOSTS` được phân giải lại và kiểm tra lại ở **mỗi** lần kết nối; `FG_CAMERA_ALLOW_LOOPBACK`
  (camera thử trên máy) bị bỏ qua ở production.
* Khi triển khai, vẫn nên đặt camera ở VLAN riêng và đặt `FG_CAMERA_ALLOWED_NETWORKS` đúng VLAN đó.
* Quyền: giáo viên quản lý camera của mình; quản trị viên quản lý camera của trường và gán lớp; giáo viên của lớp chỉ
  **dùng** camera của trường; học sinh / phụ huynh không thấy gì.

## 8. Độ trễ, kết nối lại, mất tín hiệu

* Thăm dò RTSP / ONVIF có hạn chót tổng cho mỗi phản hồi (thiết bị gửi nhỏ giọt từng byte không giữ được việc tìm
  camera), việc tìm kết thúc đúng hạn, và xem thử dừng ngay khi người xem đóng trang.
* Một luồng đọc riêng giữ **khung hình mới nhất** (không xếp hàng): khung cũ bị bỏ (`dropped_frames`), cùng một khung
  không bao giờ được phân tích hai lần, luồng “đóng băng” (khung giống hệt) bị coi là mất tín hiệu (`stale_frames`).
* Mất tín hiệu → `NO_SIGNAL` → tự kết nối lại với thời gian chờ tăng dần (0,5 s → tối đa 20 s), `reconnect_count` tăng;
  giao diện: “Camera mất kết nối — đang kết nối lại…”.
* **Không có khung hình ≠ rời chỗ**: điểm đóng băng, sự kiện đang mở kết thúc ở khung hình thật cuối cùng, đồng hồ
  “rời chỗ” bắt đầu lại sau khi có hình; mất tín hiệu không bao giờ thành AWAY hay vắng mặt.
* Độ trễ camera/mạng (`CAMERA_INGEST_LATENCY_MS`, `frame_age_ms`) được đo **riêng**, không trộn vào benchmark mô hình AI.

## 9. Khoảng cách và kích thước khuôn mặt

Phần mềm không nhận diện được khuôn mặt quá nhỏ. Khuôn mặt < ~36 px: khó nhận diện; < 64 px: không tạo mẫu nhận
diện. Khi nhiều khuôn mặt quá nhỏ, trang Giám sát hiện: “Camera đang ở quá xa để tạo mẫu nhận diện ổn định cho một số
học sinh”. Hãy đặt camera gần hơn / độ phân giải cao hơn; không có “nhận diện giả”.

## 10. Sự cố thường gặp

| Hiện tượng | Kiểm tra |
|---|---|
| Không tìm thấy camera | cùng mạng? router chặn multicast? thử Thêm thủ công bằng IP trong ứng dụng EZVIZ / router |
| Sai tên đăng nhập hoặc mật khẩu | mã xác minh in hoa; đợi nếu camera tạm khóa sau nhiều lần sai |
| Camera không tương thích | bật RTSP/ONVIF trong ứng dụng hãng; mẫu chỉ hỗ trợ đám mây thì không dùng được |
| Địa chỉ không thuộc mạng camera được phép | dùng “Tìm camera”; hoặc quản trị viên thêm mạng vào `FG_CAMERA_ALLOWED_NETWORKS` (hoặc địa chỉ vào `FG_CAMERA_ALLOWED_HOSTS`) |
| Cổng không phải cổng camera | dùng cổng RTSP / ONVIF; cổng khác: quản trị viên thêm vào `FG_CAMERA_ALLOWED_PORTS` |
| Cần FG_CAMERA_CREDENTIAL_KEY | đặt khóa trong `.env` (README › Cấu hình) rồi khởi động lại |
| Hình trễ / giật | Wi-Fi yếu; dùng luồng phụ (profile thấp hơn) hoặc dây mạng |

## 11. Quyền riêng tư

Xem thử camera không phân tích, không tạo sự kiện, không lưu hình. Thông tin camera ghi vào buổi đánh giá chỉ gồm mã,
tên, loại nguồn, hãng, model — **không** địa chỉ IP, mật khẩu hay URL; công cụ quét báo cáo chặn URL RTSP, IP nội bộ
và trường mật khẩu trong báo cáo công khai.
