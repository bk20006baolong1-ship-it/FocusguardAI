"""One truthful camera state for the UI.

The UI must never show a healthy camera while no frames are being analysed,
and never spin forever. The state is derived on the server from what is
actually known:

    OFF            no camera session is running
    NOT_STREAMING  the session is running but nobody has opened the camera view,
                   so no frames are being analysed
    STARTING       the camera and models are being loaded, or the first frame
                   has not arrived yet
    ACTIVE         frames are being analysed
    NO_SIGNAL      frames stopped arriving (stalled / disconnected stream)
    UNAVAILABLE    the camera could not be opened or read (busy, unplugged,
                   permission denied) or the pipeline failed to start
"""

OFF = "OFF"
NOT_STREAMING = "NOT_STREAMING"
STARTING = "STARTING"
ACTIVE = "ACTIVE"
NO_SIGNAL = "NO_SIGNAL"
UNAVAILABLE = "UNAVAILABLE"

LABELS_VI = {
    OFF: "Camera chưa bật",
    NOT_STREAMING: "Chưa mở khung hình camera",
    STARTING: "Đang khởi động camera",
    ACTIVE: "Camera đang hoạt động",
    NO_SIGNAL: "Mất tín hiệu camera",
    UNAVAILABLE: "Không dùng được camera",
}

HELP_VI = {
    OFF: "Bắt đầu buổi học để bật camera.",
    NOT_STREAMING: "Mở trang Giám sát để camera bắt đầu phân tích.",
    STARTING: "Đang tải mô hình nhận diện, thường mất vài giây.",
    ACTIVE: "Hình ảnh được phân tích trực tiếp, không ghi hình.",
    NO_SIGNAL: "Không nhận được hình ảnh mới. Điểm tập trung tạm dừng, không bị trừ. Hãy kiểm tra camera hoặc tải lại trang.",
    UNAVAILABLE: "Kiểm tra camera đã cắm, không bị ứng dụng khác chiếm và đã được cấp quyền, rồi thử lại.",
}


def derive(session_active: bool, hub: dict, runtime_camera_status: str) -> str:
    """``hub`` is ``CameraHub.describe(key_predicate)``:
    ``{"starting": bool, "streaming": bool, "device_ok": bool|None, "failed": bool}``.
    ``runtime_camera_status`` is SessionRuntime.camera_status
    (WAITING | ACTIVE | NO_SIGNAL)."""
    if not session_active:
        return OFF
    if hub.get("starting"):
        return STARTING
    if hub.get("failed"):
        return UNAVAILABLE
    if not hub.get("streaming"):
        # Frames only flow while a camera view is open.
        return NOT_STREAMING
    if hub.get("device_ok") is False:
        return UNAVAILABLE
    if runtime_camera_status == "NO_SIGNAL":
        return NO_SIGNAL
    if runtime_camera_status == "ACTIVE":
        return ACTIVE
    return STARTING


def describe(state: str) -> dict:
    return {"state": state, "label": LABELS_VI[state], "help": HELP_VI[state]}
