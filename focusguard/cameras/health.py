"""Camera health and connection-test states (one vocabulary for API, UI and tools)."""

# Live stream health (LatestFrameReader)
DISCONNECTED = "DISCONNECTED"   # not started / stopped
CONNECTING = "CONNECTING"       # first connection attempt
CONNECTED = "CONNECTED"         # fresh frames arriving
DEGRADED = "DEGRADED"           # frames arrive, but slowly or late
AUTH_FAILED = "AUTH_FAILED"     # the camera refused the credentials
NO_SIGNAL = "NO_SIGNAL"         # connected, but no new frame for too long
OFFLINE = "OFFLINE"             # cannot reach / open the camera
RECONNECTING = "RECONNECTING"   # lost, retrying with backoff
STREAM_STATES = (DISCONNECTED, CONNECTING, CONNECTED, DEGRADED, AUTH_FAILED, NO_SIGNAL, OFFLINE, RECONNECTING)
LIVE_STATES = (CONNECTED, DEGRADED)

# Connection test / easy connect (EZVIZ and generic IP cameras)
DEVICE_FOUND = "DEVICE_FOUND"
AUTH_REQUIRED = "AUTH_REQUIRED"
STREAM_FOUND = "STREAM_FOUND"
DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM = "DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM"
STREAM_UNAVAILABLE = "STREAM_UNAVAILABLE"
UNREACHABLE = "UNREACHABLE"
HOST_NOT_ALLOWED = "HOST_NOT_ALLOWED"
CREDENTIAL_KEY_MISSING = "CREDENTIAL_KEY_MISSING"
UNTESTED = "UNTESTED"

LABELS_VI = {
    DISCONNECTED: "Chưa kết nối", CONNECTING: "Đang kết nối…", CONNECTED: "Đã kết nối",
    DEGRADED: "Kết nối chậm", AUTH_FAILED: "Sai tên đăng nhập hoặc mật khẩu",
    NO_SIGNAL: "Mất tín hiệu", OFFLINE: "Không kết nối được", RECONNECTING: "Mất kết nối, đang kết nối lại…",
    DEVICE_FOUND: "Đã thấy thiết bị", AUTH_REQUIRED: "Cần xác thực", STREAM_FOUND: "Đã tìm thấy luồng video",
    DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM: "Camera không tương thích (không có luồng video trong mạng LAN)",
    STREAM_UNAVAILABLE: "Không mở được luồng video", UNREACHABLE: "Không kết nối được tới thiết bị",
    HOST_NOT_ALLOWED: "Địa chỉ không được phép (chỉ camera trong mạng LAN)",
    CREDENTIAL_KEY_MISSING: "Máy chủ chưa cấu hình khóa mã hóa mật khẩu camera",
    UNTESTED: "Chưa kiểm tra",
}

HELP_VI = {
    AUTH_FAILED: "Kiểm tra tên đăng nhập và mật khẩu. Camera EZVIZ: tên đăng nhập thường là admin, mật khẩu là "
                 "mã xác minh (verification code) in trên nhãn dưới đáy camera.",
    DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM: "Thiết bị trả lời trong mạng LAN nhưng không cung cấp luồng RTSP/ONVIF. "
                 "Một số mẫu/firmware EZVIZ chỉ xem được qua ứng dụng đám mây; hãy bật RTSP/ONVIF trong ứng dụng "
                 "EZVIZ nếu mẫu đó có tùy chọn này, hoặc dùng camera khác.",
    STREAM_UNAVAILABLE: "Thiết bị trả lời nhưng không mở được luồng video. Thử lại, hoặc nhập đường dẫn luồng ở chế độ "
                 "nâng cao.",
    UNREACHABLE: "Máy tính và camera phải ở cùng mạng LAN (cùng Wi-Fi/router). Kiểm tra camera đang bật.",
    HOST_NOT_ALLOWED: "FocusGuard chỉ kết nối tới camera có địa chỉ trong mạng nội bộ (ví dụ 192.168.x.x).",
    CREDENTIAL_KEY_MISSING: "Quản trị viên cần đặt FG_CAMERA_CREDENTIAL_KEY trong .env (xem README › Camera).",
    NO_SIGNAL: "Không nhận được hình ảnh mới. Điểm tập trung tạm dừng, không bị trừ.",
    RECONNECTING: "Đang tự động kết nối lại. Điểm tập trung tạm dừng, không ai bị tính là rời chỗ.",
}


def describe(state):
    return {"state": state, "label": LABELS_VI.get(state, state), "help": HELP_VI.get(state)}
