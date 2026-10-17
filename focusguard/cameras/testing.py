"""TEST / DEV ONLY camera doubles. Never used by the app factory or any production path.

* :class:`MockCameraSource` — synthetic frames (numpy), configurable failures,
  frozen frames and open failures: drives LatestFrameReader / pipelines in tests
  and the browser walkthrough demo server.
* :class:`MockRTSPServer` — a tiny RTSP server on 127.0.0.1 speaking OPTIONS and
  DESCRIBE with Digest auth, so the real RTSP probe / easy-connect code is
  exercised over a real socket.
* :func:`mock_discovery_strategy` — adds a fixed device to a discovery job.
* :func:`mock_rtsp_disabled_strategy` — adds an SDK-port-only device (RTSP off).
"""
import hashlib
import re
import socket
import threading
import time

from . import health as H
from .sources import CameraSource


class MockCameraSource(CameraSource):
    source_type = "RTSP"

    def __init__(self, name="Mock camera", camera_id=None, width=320, height=240, fps=20.0, fail_open=False,
                 auth_failed=False, frames_before_failure=None, frozen_after=None, sleep=time.sleep, source_type=None):
        super().__init__(name, camera_id)
        if source_type:
            self.source_type = source_type
        self.w, self.h, self.fps = width, height, fps
        self.fail_open, self.auth_failed = fail_open, auth_failed
        self.frames_before_failure = frames_before_failure
        self.frozen_after = frozen_after
        self._sleep = sleep
        self._open = False
        self.opens = 0
        self.reads = 0
        self.vendor = self.model = None

    def open(self):
        self.opens += 1
        if self.auth_failed:
            self.state = H.AUTH_FAILED
            self.last_error = "401 Unauthorized rtsp://admin:topsecret@192.168.1.20/H.264"
            return False
        if self.fail_open:
            self.state = H.OFFLINE
            self.last_error = "connection refused"
            return False
        self._open = True
        self.width, self.height, self.reported_fps = self.w, self.h, self.fps
        self.state = H.CONNECTED
        return True

    def read(self):
        import numpy as np
        if not self._open:
            return False, None
        self._sleep(1.0 / self.fps if self.fps else 0)
        self.reads += 1
        if self.frames_before_failure is not None and self.reads > self.frames_before_failure:
            return False, None
        n = self.reads if self.frozen_after is None else min(self.reads, self.frozen_after)
        frame = np.full((self.h, self.w, 3), 90, dtype=np.uint8)
        x = (n * 7) % max(1, self.w - 40)
        frame[self.h // 3: self.h // 3 + 40, x:x + 40] = (40, 160, 230)
        return True, frame

    def is_open(self):
        return self._open

    def close(self):
        self._open = False
        self.state = H.DISCONNECTED


class MockRTSPServer:
    """Minimal RTSP server: ``paths`` that exist, Digest credentials, ``Server`` header."""

    def __init__(self, username="admin", password="ABCDEF", paths=("/H.264",), server="EZVIZ-mock/1.0",
                 realm="mockcam", require_auth=True):
        self.username, self.password = username, password
        self.paths, self.server, self.realm, self.require_auth = set(paths), server, realm, require_auth
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.requests = []
        self.failed_logins = 0
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)     # wakes the accept() thread
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        nonce = hashlib.md5(str(time.time()).encode()).hexdigest()
        buf = b""
        conn.settimeout(3)
        try:
            while True:
                while b"\r\n\r\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buf += chunk
                head, _, buf = buf.partition(b"\r\n\r\n")
                lines = head.decode().split("\r\n")
                method, uri, _ = lines[0].split(" ", 2)
                hdrs = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
                self.requests.append((method, uri, hdrs.get("authorization")))
                cseq = hdrs.get("cseq", "1")
                if method == "OPTIONS":
                    conn.sendall(self._resp(200, "OK", cseq, {"Public": "OPTIONS, DESCRIBE"}))
                    continue
                path = re.sub(r"^rtsp://[^/]+", "", uri) or "/"
                auth = hdrs.get("authorization")
                if self.require_auth:
                    if not auth:
                        conn.sendall(self._resp(401, "Unauthorized", cseq,
                                                {"WWW-Authenticate": f'Digest realm="{self.realm}", nonce="{nonce}"'}))
                        continue
                    if not self._valid(auth, method, nonce):
                        self.failed_logins += 1
                        conn.sendall(self._resp(401, "Unauthorized", cseq,
                                                {"WWW-Authenticate": f'Digest realm="{self.realm}", nonce="{nonce}"'}))
                        continue
                if path in self.paths:
                    sdp = "v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
                    conn.sendall(self._resp(200, "OK", cseq, {"Content-Type": "application/sdp"}, sdp))
                else:
                    conn.sendall(self._resp(404, "Not Found", cseq))
        except (OSError, ValueError):
            return
        finally:
            conn.close()

    def _valid(self, auth, method, nonce):
        p = dict(re.findall(r'(\w+)="?([^",]*)"?', auth))
        ha1 = hashlib.md5(f"{self.username}:{self.realm}:{self.password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{p.get('uri')}".encode()).hexdigest()
        expected = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        return p.get("username") == self.username and p.get("response") == expected

    def _resp(self, code, reason, cseq, headers=None, body=""):
        lines = [f"RTSP/1.0 {code} {reason}", f"CSeq: {cseq}", f"Server: {self.server}"]
        for k, v in (headers or {}).items():
            lines.append(f"{k}: {v}")
        if body:
            lines.append(f"Content-Length: {len(body.encode())}")
        return ("\r\n".join(lines) + "\r\n\r\n" + body).encode()


def mock_discovery_strategy(host, vendor="EZVIZ", model="CS-H1C", name="EZVIZ H1C", auth_required=True):
    def strategy(ctx):
        from .netguard import ORIGIN_DISCOVERED, HostNotAllowed
        ctx.job.stage = "Đang tìm camera (mô phỏng)…"
        try:                                   # like the real strategies: only approved camera subnets
            ctx.netguard.check_ip(host, ORIGIN_DISCOVERED)
        except HostNotAllowed:
            return
        ctx.job.add(host, protocols=["ONVIF", "RTSP"], vendor=vendor, model=model, device_name=name,
                    suggested_port=554, confidence="high", auth_required=auth_required, _hint=vendor)
    return strategy


def mock_rtsp_disabled_strategy(host):
    """Adds a device that answers only on the SDK port 8000 (an EZVIZ camera with RTSP off)."""
    def strategy(ctx):
        from .netguard import ORIGIN_DISCOVERED, HostNotAllowed
        try:
            ctx.netguard.check_ip(host, ORIGIN_DISCOVERED)
        except HostNotAllowed:
            return
        ctx.job.add(host, protocols=["SDK"], confidence="low")
    return strategy
