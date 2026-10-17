"""CameraSource: the one abstraction every pipeline reads frames from.

    open() -> bool      read() -> (ok, frame)      is_open() -> bool
    close()             health() -> dict           metadata() -> dict
    reconnect() -> bool

* :class:`WebcamSource` — a local camera by index (``cv2.VideoCapture(0)``),
  the behaviour FocusGuard always had; still the default.
* :class:`RTSPSource` — an IP camera (EZVIZ / generic) over RTSP. The URL with
  credentials is built only inside :meth:`RTSPSource.open`, from a vetted IP
  and a secret provider; it is never stored on the object, logged, or returned.

ONVIF is used only to discover/resolve the RTSP stream (connect.py); video is
always ingested through RTSP. OpenCV is imported lazily so hardware-free code
(tests, CI) never needs it.
"""
import os
import time
from urllib.parse import quote

from . import health as H
from .redact import redact, safe_error

WEBCAM = "WEBCAM"
RTSP = "RTSP"
SOURCE_TYPES = (WEBCAM, RTSP)


class CameraSource:
    source_type = None

    def __init__(self, name=None, camera_id=None):
        self.name = name
        self.camera_id = camera_id
        self.state = H.DISCONNECTED
        self.last_error = None
        self.width = self.height = None
        self.reported_fps = None

    # -- required interface ------------------------------------------------
    def open(self) -> bool:
        raise NotImplementedError

    def read(self):
        raise NotImplementedError

    def is_open(self) -> bool:
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def reconnect(self) -> bool:
        self.close()
        return self.open()

    def health(self) -> dict:
        return {"source_type": self.source_type, "camera_name": self.name, "state": self.state,
                "resolution": f"{self.width}x{self.height}" if self.width and self.height else None,
                "reported_fps": self.reported_fps, "last_error_sanitized": self.last_error}

    def metadata(self) -> dict:
        """What may be recorded with an evaluation or shown to a teacher: never credentials or URLs."""
        return {"camera_id": self.camera_id, "camera_name": self.name, "camera_source_type": self.source_type,
                "resolution": f"{self.width}x{self.height}" if self.width and self.height else None,
                "reported_fps": self.reported_fps}

    def __repr__(self):
        return f"<{type(self).__name__} name={self.name!r} state={self.state}>"


class _CvSource(CameraSource):
    """Shared OpenCV plumbing."""

    def __init__(self, name=None, camera_id=None, capture_factory=None):
        super().__init__(name, camera_id)
        self.cap = None
        self._capture_factory = capture_factory

    def _new_capture(self, target, *args):
        if self._capture_factory is not None:
            return self._capture_factory(target, *args)
        import cv2
        return cv2.VideoCapture(target, *args)

    def is_open(self):
        try:
            return self.cap is not None and bool(self.cap.isOpened())
        except Exception:
            return False

    def read(self):
        if not self.is_open():
            return False, None
        try:
            ok, frame = self.cap.read()
        except Exception as exc:
            self.last_error = safe_error(exc)
            return False, None
        if not ok or frame is None or getattr(frame, "size", 1) == 0:
            return False, None
        if self.width is None:
            self.height, self.width = frame.shape[:2]
        return True, frame

    def close(self):
        cap, self.cap = self.cap, None
        if cap is not None:
            try:
                cap.release()
            except Exception as exc:
                self.last_error = safe_error(exc)
        self.state = H.DISCONNECTED

    def _read_props(self):
        try:
            import cv2
            fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0)
            self.reported_fps = round(fps, 2) if 0 < fps < 241 else None
        except Exception:
            self.reported_fps = None


class WebcamSource(_CvSource):
    """Local camera by index (backward compatible with ``cv2.VideoCapture(index)``)."""
    source_type = WEBCAM

    def __init__(self, index=0, name=None, camera_id=None, warmup_reads=30, capture_factory=None, sleep=time.sleep):
        super().__init__(name or f"Webcam {int(index)}", camera_id, capture_factory)
        self.index = int(index)
        self.warmup_reads = warmup_reads
        self._sleep = sleep
        self._pending = None

    def open(self):
        self.close()
        self.state = H.CONNECTING
        try:
            self.cap = self._new_capture(self.index)
        except Exception as exc:
            self.last_error = safe_error(exc)
            self.state = H.OFFLINE
            return False
        # macOS AVFoundation: the first reads fail while the device warms up.
        for _ in range(self.warmup_reads):
            ok, frame = _CvSource.read(self)
            if ok:
                self._pending = frame
                self._read_props()
                self.state = H.CONNECTED
                return True
            self._sleep(0.1)
        self.last_error = "no frame from webcam"
        self.state = H.OFFLINE
        return False

    def read(self):
        if self._pending is not None:
            frame, self._pending = self._pending, None
            return True, frame
        return _CvSource.read(self)

    def metadata(self):
        return dict(super().metadata(), device_index=self.index)


class RTSPSource(_CvSource):
    """IP camera over RTSP (TCP transport, small buffer, bounded timeouts).

    ``secret_provider`` is called only inside :meth:`open` (decrypting the
    stored secret server-side at connect time)."""
    source_type = RTSP

    def __init__(self, ip, port=554, path=None, username=None, secret_provider=None, name=None, camera_id=None,
                 vendor=None, model=None, open_timeout_ms=8000, read_timeout_ms=5000, capture_factory=None,
                 netguard=None, origin="MANUAL", host=None):
        super().__init__(name, camera_id, capture_factory)
        self.ip = ip
        self.port = int(port or 554)
        self.path = path or "/"
        self.username = username
        self._secret_provider = secret_provider
        self.vendor, self.model = vendor, model
        self.open_timeout_ms, self.read_timeout_ms = open_timeout_ms, read_timeout_ms
        self.netguard = netguard
        self.origin = origin            # camera-network policy origin (DISCOVERED | MANUAL)
        self.host = host                # configured host name: re-resolved and re-vetted at every connect

    def _url(self, secret):
        host = f"[{self.ip}]" if ":" in str(self.ip) else str(self.ip)
        auth = ""
        if self.username:
            auth = quote(self.username, safe="") + (":" + quote(secret, safe="") if secret else "") + "@"
        return f"rtsp://{auth}{host}:{self.port}{self.path}"

    def open(self):
        self.close()
        self.state = H.CONNECTING
        secret = None
        try:
            if self.netguard is not None:
                if self.host and self.host != self.ip:
                    self.ip = self.netguard.resolve_host(self.host, self.origin)   # re-resolve + re-vet the name
                else:
                    self.netguard.check_ip(self.ip, self.origin)  # re-check at every connect
                self.netguard.check_port(self.port)
            secret = self._secret_provider() if self._secret_provider else None
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
            try:
                import cv2
                params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.open_timeout_ms,
                          cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.read_timeout_ms]
                # FFMPEG backend only: other OpenCV backends (CAP_IMAGES) would log the URL on failure.
                self.cap = self._new_capture(self._url(secret), cv2.CAP_FFMPEG, params)
            except ImportError:
                self.cap = self._new_capture(self._url(secret))
            try:
                import cv2
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # latest-frame semantics, best effort
            except Exception:
                pass
        except Exception as exc:
            self.last_error = safe_error(exc, secrets=(secret,))
            self.state = H.OFFLINE
            self.cap = None
            return False
        finally:
            secret = None
        if not self.is_open():
            self.last_error = "stream could not be opened"
            self.state = H.OFFLINE
            self.close()
            self.state = H.OFFLINE
            return False
        self._read_props()
        self.state = H.CONNECTED
        return True

    def metadata(self):
        return dict(super().metadata(), vendor=self.vendor, model=self.model)

    def health(self):
        h = super().health()
        h["last_error_sanitized"] = redact(h.get("last_error_sanitized"))
        return h
