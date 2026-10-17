"""Camera subsystem: sources, latest-frame reader, health, reconnect, redaction, SSRF
guard, credential encryption, RTSP probe / easy connect (over a real local socket),
discovery, ONVIF parsing, the no-frame != AWAY rule and the IP camera check tool.
No camera hardware: MockCameraSource / MockRTSPServer (focusguard/cameras/testing.py)."""
import json
import os
import sys
import time

import pytest

from focusguard.behavior import Observation
from focusguard.cameras import connect, health as H, onvif, rtsp
from focusguard.cameras.check import HARDWARE_REQUIRED, PASS, run_check
from focusguard.cameras.crypto import CredentialKeyMismatch, CredentialKeyMissing, CredentialVault, generate_key
from focusguard.cameras.discovery import DiscoveryManager
from focusguard.cameras.netguard import HostNotAllowed, NetGuard, split_camera_url
from focusguard.cameras.reader import LatestFrameReader
from focusguard.cameras.redact import redact
from focusguard.cameras.sources import RTSPSource, WebcamSource
from focusguard.cameras.testing import MockCameraSource, MockRTSPServer, mock_discovery_strategy
from focusguard import runtime_state as rs

SECRET = "Zq7-SECRET-ABC123"


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------- sources
class FakeCap:
    def __init__(self, frames=3, opened=True):
        import numpy as np
        self.frames = [np.full((48, 64, 3), i, dtype=np.uint8) for i in range(frames)]
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened and not self.released

    def read(self):
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)

    def release(self):
        self.released = True

    def get(self, _prop):
        return 25.0

    def set(self, *_a):
        return True


def test_webcam_source_is_backward_compatible_with_index_0():
    caps = []
    src = WebcamSource(0, capture_factory=lambda target, *a: caps.append(target) or FakeCap(), sleep=lambda s: None)
    assert src.open() and caps == [0] and src.state == H.CONNECTED
    ok, frame = src.read()                       # the warm-up frame is not lost
    assert ok and frame.shape == (48, 64, 3)
    assert src.metadata()["device_index"] == 0 and src.metadata()["resolution"] == "64x48"
    src.close()
    assert src.state == H.DISCONNECTED and not src.is_open()


def test_webcam_source_reports_offline_without_frames():
    src = WebcamSource(1, capture_factory=lambda *a: FakeCap(frames=0), warmup_reads=3, sleep=lambda s: None)
    assert not src.open() and src.state == H.OFFLINE


def test_rtsp_source_never_keeps_or_reports_the_url_or_secret():
    seen = []

    def factory(target, *a):
        seen.append(target)
        return FakeCap()
    src = RTSPSource("192.168.1.20", 554, "/H.264", "admin", secret_provider=lambda: SECRET, name="EZVIZ H1C",
                     capture_factory=factory, netguard=NetGuard())
    assert src.open()
    assert seen and SECRET.replace("-", "") in seen[0].replace("-", "")      # only handed to the capture
    blob = json.dumps([src.metadata(), src.health(), repr(src), vars(src).get("path")], default=str)
    assert SECRET not in blob and "rtsp://" not in blob
    assert not any(SECRET in str(v) for v in vars(src).values())


def test_rtsp_source_rechecks_the_host_at_connect_time():
    src = RTSPSource("169.254.169.254", 554, "/", "admin", secret_provider=lambda: SECRET,
                     capture_factory=lambda *a: FakeCap(), netguard=NetGuard())
    assert not src.open() and src.state == H.OFFLINE
    assert SECRET not in (src.last_error or "")


def test_rtsp_source_error_messages_are_redacted():
    def boom(target, *a):
        raise RuntimeError(f"cannot open {target}")
    src = RTSPSource("192.168.1.20", 554, "/x", "admin", secret_provider=lambda: SECRET, capture_factory=boom)
    assert not src.open()
    assert SECRET not in src.last_error and "admin:" not in src.last_error and "***@" in src.last_error


# ------------------------------------------------------------ reader
def test_reader_delivers_only_the_latest_frame_and_never_the_same_twice():
    src = MockCameraSource(fps=200)
    r = LatestFrameReader(src).start()
    try:
        assert wait_until(lambda: r.seq >= 5)
        time.sleep(0.1)                          # producer runs ahead: older frames are dropped, not queued
        f = r.get_newer(0)
        assert f is not None and f.seq == r.seq or f.seq >= r.seq - 2
        assert r.get_newer(f.seq) is None or r.get_newer(f.seq).seq > f.seq   # never the same frame again
        assert r.dropped_frame_count > 0
        h = r.health()
        assert h["state"] == H.CONNECTED and h["resolution"] == "320x240" and h["measured_fps"] > 10
        assert h["frame_age_ms"] is not None and h["frame_age_ms"] < 1000
    finally:
        r.stop()
    assert not r.running and not src.is_open()


def test_reader_latency_does_not_grow_with_a_slow_consumer():
    src = MockCameraSource(fps=100)
    r = LatestFrameReader(src).start()
    try:
        seq, ages = 0, []
        for _ in range(8):
            time.sleep(0.12)                      # consumer 8x slower than the camera
            f = r.get_newer(seq)
            assert f is not None
            seq = f.seq
            ages.append(time.time() - f.timestamp)
        assert max(ages) < 0.2                    # always a fresh frame, no backlog
    finally:
        r.stop()


def test_reader_counts_frozen_frames_as_stale_and_reports_no_signal():
    src = MockCameraSource(fps=100, frozen_after=3)
    r = LatestFrameReader(src, stale_after=0.3, initial_backoff=5.0, detect_repeats=True).start()
    try:
        assert wait_until(lambda: r.stale_frame_count > 5, 3)
        frozen_seq = r.seq
        assert frozen_seq <= 3                       # identical frames are not "new"
        assert wait_until(lambda: r.live_state() in (H.NO_SIGNAL, H.RECONNECTING), 3)
        assert not r.has_signal()
    finally:
        r.stop()


def test_reader_reconnects_with_backoff_after_the_stream_dies():
    src = MockCameraSource(fps=200, frames_before_failure=20)
    r = LatestFrameReader(src, max_read_failures=3, initial_backoff=0.05, max_backoff=0.2).start()
    try:
        assert wait_until(lambda: r.reconnect_count >= 2, 5)
        assert src.opens >= 3
        assert r.health()["reconnect_count"] >= 2
    finally:
        r.stop()


def test_reader_backoff_is_bounded_and_open_failures_are_reported():
    src = MockCameraSource(fail_open=True)
    r = LatestFrameReader(src, initial_backoff=0.01, max_backoff=0.05).start()
    try:
        assert wait_until(lambda: src.opens >= 6, 3)       # keeps trying, 50 ms cap
        assert r.state in (H.OFFLINE, H.RECONNECTING) and not r.has_signal()
    finally:
        r.stop()


def test_reader_auth_failure_is_reported_and_redacted():
    src = MockCameraSource(auth_failed=True)
    r = LatestFrameReader(src, auto_reconnect=False).start()
    try:
        assert wait_until(lambda: not r.running, 3)
        h = r.health()
        assert h["state"] == H.AUTH_FAILED
        assert "topsecret" not in json.dumps(h) and "***@" in h["last_error_sanitized"]
    finally:
        r.stop()


def test_reader_request_reconnect_reopens_from_the_reader_thread():
    src = MockCameraSource(fps=200)
    r = LatestFrameReader(src, initial_backoff=0.01).start()
    try:
        assert wait_until(lambda: r.seq > 2)
        r.request_reconnect()
        assert wait_until(lambda: r.reconnect_count == 1 and r.has_signal(), 3)
    finally:
        r.stop()


# ------------------------------------------------------------ redaction
@pytest.mark.parametrize("text", [
    f"rtsp://admin:{SECRET}@192.168.1.20:554/H.264",
    f"open failed: RTSP://user:{SECRET}@cam.local/stream",
    f"password={SECRET}&x=1", f"verification_code: {SECRET}", f'{{"pwd": "{SECRET}"}}',
    f"Authorization: Basic {SECRET}", f"authorization: Digest username=\"admin\", response=\"{SECRET}\"",
    f"<wsse:Password Type=\"x#PasswordDigest\">{SECRET}</wsse:Password>",
    f"<Password>{SECRET}</Password>", f"secret={SECRET}", f"token: {SECRET}",
])
def test_redact_removes_every_credential_form(text):
    out = redact(text)
    assert SECRET not in out and "***" in out


def test_redact_known_secret_and_url_encoded_form():
    s = "p@ss w/rd:1"
    from urllib.parse import quote
    assert s not in redact(f"value {s}", secrets=[s])
    assert quote(s, safe="") not in redact("x " + quote(s, safe=""), secrets=[s])
    assert redact("Camera Phòng 8/1 1920x1080 18 FPS") == "Camera Phòng 8/1 1920x1080 18 FPS"


# ------------------------------------------------------------ SSRF guard
@pytest.mark.parametrize("host", ["192.168.1.20", "10.0.0.5", "172.16.3.4", "172.31.255.1", "fd12::1", "[fd12::5]"])
def test_netguard_allows_private_lan(host):
    assert NetGuard().resolve_host(host)


@pytest.mark.parametrize("host,code", [
    ("8.8.8.8", "public"), ("1.1.1.1", "public"), ("169.254.169.254", "metadata"), ("169.254.10.1", "blocked"),
    ("127.0.0.1", "loopback"), ("localhost", "loopback"), ("::1", "loopback"), ("0.0.0.0", "blocked"),
    ("100.64.1.1", "blocked"), ("224.0.0.1", "blocked"), ("fe80::1", "blocked"), ("::ffff:169.254.169.254", "metadata"),
    ("172.32.0.1", "public"), ("", "empty"), ("file:///etc/passwd", "malformed"), ("192.168.1.1/x", "malformed"),
    ("a b", "malformed"), ("user@192.168.1.1", "malformed"), ("x" * 300, "malformed"),
])
def test_netguard_blocks_everything_else(host, code):
    with pytest.raises(HostNotAllowed) as e:
        NetGuard().resolve_host(host)
    assert e.value.code == code


def test_netguard_hostnames_every_resolved_address_must_pass():
    g = NetGuard(resolver=lambda h: ["192.168.1.9"])
    assert g.resolve_host("cam.local") == "192.168.1.9"
    mixed = NetGuard(resolver=lambda h: ["192.168.1.9", "93.184.216.34"])        # DNS rebinding style answer
    with pytest.raises(HostNotAllowed):
        mixed.resolve_host("cam.local")
    meta = NetGuard(resolver=lambda h: ["169.254.169.254"])
    with pytest.raises(HostNotAllowed):
        meta.resolve_host("evil.example")


def test_netguard_resolves_on_every_call_dns_rebinding():
    answers = iter([["192.168.1.9"], ["169.254.169.254"]])
    g = NetGuard(resolver=lambda h: next(answers))
    assert g.resolve_host("cam.local") == "192.168.1.9"
    with pytest.raises(HostNotAllowed):
        g.resolve_host("cam.local")                 # re-resolved and re-checked, the pinned IP is used to connect


def test_netguard_admin_approved_hosts_and_loopback_switch():
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_HOSTS": "203.0.113.0/24, cam.school.vn", "FG_CAMERA_ALLOW_LOOPBACK": "1"},
                          resolver=lambda h: ["203.0.113.7"])
    assert g.resolve_host("203.0.113.5") and g.resolve_host("cam.school.vn") and g.resolve_host("127.0.0.1")
    with pytest.raises(HostNotAllowed):
        g.resolve_host("169.254.169.254")           # metadata is never approvable


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://192.168.1.2/x", "gopher://192.168.1.2/", "http://192.168.1.2/",
                                 "unix:///var/run/docker.sock", "rtsp://", "javascript:alert(1)"])
def test_pasted_urls_only_rtsp(url):
    with pytest.raises(HostNotAllowed):
        split_camera_url(url)


def test_pasted_rtsp_url_is_split_and_credentials_separated():
    parts = split_camera_url(f"rtsp://admin:{SECRET}@192.168.1.20:8554/Streaming/Channels/101?transport=tcp")
    assert parts == {"scheme": "rtsp", "host": "192.168.1.20", "port": 8554,
                     "path": "/Streaming/Channels/101?transport=tcp", "username": "admin", "secret": SECRET}


@pytest.mark.parametrize("path", ["/live?user=admin&password=x", "/user=admin_password=x_channel=1", "/cam?pwd=1",
                                  "/stream?token=abc", "/a;auth=b", "/live?verification_code=ABCDEF"])
def test_stream_paths_carrying_credentials_are_refused(path):
    """The stream path is stored and listed in plaintext: credentials belong in the encrypted fields."""
    from focusguard.cameras.netguard import check_path
    with pytest.raises(HostNotAllowed) as e:
        check_path(path)
    assert e.value.code == "credentials_in_path" and "x" not in str(e.value)
    with pytest.raises(HostNotAllowed):
        split_camera_url("rtsp://192.168.1.20:554" + path)
    assert check_path("/cam/realmonitor?channel=1&subtype=0") == "/cam/realmonitor?channel=1&subtype=0"


def test_pasted_url_credentials_with_control_characters_are_refused():
    with pytest.raises(HostNotAllowed):
        split_camera_url("rtsp://a%0d%0aX-Injected:1@192.168.1.20/live")
    with pytest.raises(HostNotAllowed):
        split_camera_url("rtsp://admin:pw%0a@192.168.1.20/live")


def test_netguard_approved_name_never_reaches_loopback_and_its_address_passes_at_connect():
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_HOSTS": "cam.school.vn"}, resolver=lambda h: ["127.0.0.1"])
    with pytest.raises(HostNotAllowed):
        g.resolve_host("cam.school.vn")
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_HOSTS": "cam.school.vn"}, resolver=lambda h: ["203.0.113.7"])
    with pytest.raises(HostNotAllowed):
        g.check_ip("203.0.113.7")                   # a public address is not allowed by itself ...
    ip = g.resolve_host("cam.school.vn")
    assert g.check_ip(ip) == "203.0.113.7"          # ... but the approved name's address is, at connect time


def test_netguard_approved_name_never_reaches_always_blocked_ranges_and_vetting_expires():
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_HOSTS": "cam.school.vn"}, resolver=lambda h: ["100.64.1.2"])
    with pytest.raises(HostNotAllowed):
        g.resolve_host("cam.school.vn")                     # CGNAT stays blocked even for an approved name
    now = [1000.0]
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_HOSTS": "cam.school.vn"}, resolver=lambda h: ["203.0.113.7"])
    g._clock = lambda: now[0]
    g.resolve_host("cam.school.vn")
    assert g.check_ip("203.0.113.7")
    now[0] += g.NAME_VETTED_TTL + 1                         # the name moved / was not resolved again
    with pytest.raises(HostNotAllowed):
        g.check_ip("203.0.113.7")


def test_rtsp_probe_has_an_overall_deadline_against_a_slow_drip_device():
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    done = threading.Event()

    def drip():
        conn, _ = srv.accept()
        try:
            conn.recv(4096)
            while not done.is_set():
                conn.sendall(b"R")                  # one byte at a time, never a full response
                time.sleep(0.05)
        except OSError:
            pass
        finally:
            conn.close()
    threading.Thread(target=drip, daemon=True).start()
    t0 = time.monotonic()
    status, _server = rtsp.options("127.0.0.1", port, timeout=0.3)
    done.set()
    srv.close()
    assert status == rtsp.ERROR and time.monotonic() - t0 < 2.0


def test_onvif_stream_uri_carrying_credentials_is_not_used():
    caps = ("<s:Envelope xmlns:s='x'><s:Body><Capabilities><Media><XAddr>http://192.168.1.64/onvif/media"
            "</XAddr></Media></Capabilities></s:Body></s:Envelope>")
    uri = ("<s:Envelope xmlns:s='x'><s:Body><GetStreamUriResponse><MediaUri><Uri>rtsp://192.168.1.64/live?"
           "user=admin&amp;password=pw</Uri></MediaUri></GetStreamUriResponse></s:Body></s:Envelope>")
    FakeHTTP.replies = [(200, caps.encode()), (200, uri.encode())]
    with pytest.raises(onvif.OnvifError):
        onvif.OnvifClient("192.168.1.64", 80, http_factory=FakeHTTP).stream_uri("p1")


# --------------------------------------------------------------- crypto
def test_vault_encrypts_and_decrypts_and_detects_key_change():
    v = CredentialVault(generate_key())
    blob = v.encrypt(SECRET)
    assert SECRET not in blob and blob.startswith("fgc1:")
    assert v.decrypt(blob) == SECRET
    with pytest.raises(CredentialKeyMismatch):
        CredentialVault(generate_key()).decrypt(blob)


def test_vault_production_without_key_fails_safe(tmp_path):
    v = CredentialVault.from_env({"FOCUSGUARD_ENV": "production"})
    assert not v.available
    with pytest.raises(CredentialKeyMissing):
        v.encrypt(SECRET)
    with pytest.raises(CredentialKeyMissing):
        v.decrypt("fgc1:x:y")
    placeholder = CredentialVault.from_env({"FOCUSGUARD_ENV": "production",
                                            "FG_CAMERA_CREDENTIAL_KEY": "<generate-a-fernet-key>"})
    assert not placeholder.available


def test_vault_development_key_file_is_private_and_stable(tmp_path):
    path = str(tmp_path / "k" / "camera.key")
    env = {"FOCUSGUARD_ENV": "development", "FG_CAMERA_KEY_FILE": path}
    a = CredentialVault.from_env(env)
    b = CredentialVault.from_env(env)
    assert a.available and a.key_id == b.key_id
    assert b.decrypt(a.encrypt(SECRET)) == SECRET
    if os.name == "posix":
        assert (os.stat(path).st_mode & 0o777) == 0o600


def test_vault_rejects_invalid_key():
    with pytest.raises(CredentialKeyMissing):
        CredentialVault("not-a-fernet-key")


# ------------------------------------------- RTSP probe / easy connect
def test_rtsp_probe_digest_auth_against_a_real_socket():
    with MockRTSPServer(password=SECRET, paths=("/H.264",)) as srv:
        ok = rtsp.probe("127.0.0.1", srv.port, "/H.264", "admin", SECRET)
        assert ok.status == rtsp.OK and ok.codec == "H264" and ok.server.startswith("EZVIZ")
        assert rtsp.probe("127.0.0.1", srv.port, "/H.264").status == rtsp.AUTH_REQUIRED
        assert rtsp.probe("127.0.0.1", srv.port, "/H.264", "admin", "wrong").status == rtsp.AUTH_FAILED
        assert rtsp.probe("127.0.0.1", srv.port, "/nope", "admin", SECRET).status == rtsp.NOT_FOUND
        assert all(SECRET not in (uri or "") for _m, uri, _a in srv.requests)     # never in the request URI
    assert rtsp.probe("127.0.0.1", srv.port, "/H.264", timeout=0.5).status == rtsp.UNREACHABLE


def test_digest_header_with_qop():
    h = rtsp.auth_header('Digest realm="r", nonce="n", qop="auth"', "DESCRIBE", "rtsp://x/", "u", SECRET)
    assert h.startswith("Digest ") and "qop=auth" in h and 'response="' in h and SECRET not in h
    assert rtsp.auth_header("Basic realm=x", "DESCRIBE", "rtsp://x/", "u", "p").startswith("Basic ")
    assert SECRET not in redact("Authorization: " + h)


class NoOnvif:
    def __init__(self, *a, **k):
        pass

    def device_information(self):
        raise onvif.OnvifError("closed")


class LocalDeps(connect.Deps):
    onvif_client = NoOnvif


def test_easy_connect_finds_the_ezviz_stream_without_a_url():
    with MockRTSPServer(password=SECRET, paths=("/H.264",)) as srv:
        r = connect.resolve_stream("127.0.0.1", "admin", SECRET, port=srv.port, deps=LocalDeps)
    assert r["status"] == H.STREAM_FOUND and r["path"] == "/H.264" and r["vendor"] == "EZVIZ"
    assert SECRET not in json.dumps(r)


def test_easy_connect_finds_a_generic_path():
    with MockRTSPServer(password=SECRET, paths=("/stream1",), server="GenericCam") as srv:
        r = connect.resolve_stream("127.0.0.1", "admin", SECRET, port=srv.port, deps=LocalDeps)
    assert r["status"] == H.STREAM_FOUND and r["path"] == "/stream1"


def test_wrong_password_costs_exactly_one_failed_login():
    with MockRTSPServer(password=SECRET, paths=("/H.264",)) as srv:
        r = connect.resolve_stream("127.0.0.1", "admin", "WRONG", port=srv.port, deps=LocalDeps)
        assert r["status"] == H.AUTH_FAILED and r["auth_required"] is True
        assert srv.failed_logins == 1                  # camera lock-out protection


def test_no_credentials_reports_auth_required():
    with MockRTSPServer(password=SECRET) as srv:
        r = connect.resolve_stream("127.0.0.1", None, None, port=srv.port, deps=LocalDeps)
    assert r["status"] == H.AUTH_REQUIRED


def test_device_without_compatible_stream():
    with MockRTSPServer(password=SECRET, paths=()) as srv:
        r = connect.resolve_stream("127.0.0.1", "admin", SECRET, port=srv.port, deps=LocalDeps)
    assert r["status"] == H.DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM


def test_unreachable_device():
    r = connect.resolve_stream("127.0.0.1", "admin", SECRET, port=1, deps=LocalDeps)
    assert r["status"] == H.UNREACHABLE


def test_manual_advanced_path_is_probed_only():
    with MockRTSPServer(password=SECRET, paths=("/H.264",)) as srv:
        r = connect.resolve_stream("127.0.0.1", "admin", SECRET, protocol=connect.PROTOCOL_RTSP, port=srv.port,
                                   path="/custom", deps=LocalDeps)
        assert r["status"] == H.DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM
        assert [u for m, u, a in srv.requests if m == "DESCRIBE" and "/H.264" in u] == []


class FakeOnvif:
    def __init__(self, ip, port, path, username=None, password=None):
        self.username, self.password = username, password

    def device_information(self):
        if self.password != SECRET:
            raise onvif.OnvifAuthError("no")
        return {"vendor": "EZVIZ", "model": "CS-H1C"}

    def profiles(self):
        return [{"token": "sub", "name": "Sub", "width": 640, "height": 360},
                {"token": "main", "name": "Main", "width": 1920, "height": 1080}]

    def stream_uri(self, token):
        return {"port": self.port, "path": "/onvif-" + token}


def test_onvif_resolution_picks_the_main_profile():
    with MockRTSPServer(password=SECRET, paths=("/onvif-main",)) as srv:
        FakeOnvif.port = srv.port

        class D(connect.Deps):
            onvif_client = FakeOnvif
        r = connect.resolve_stream("127.0.0.1", "admin", SECRET, port=srv.port, deps=D)
    assert r["status"] == H.STREAM_FOUND and r["path"] == "/onvif-main" and r["profile"] == "Main"
    assert r["vendor"] == "EZVIZ" and r["model"] == "CS-H1C"


def test_measure_reports_resolution_fps_and_latency():
    m = connect.measure(MockCameraSource(fps=60), seconds=1.0)
    assert m["status"] == H.CONNECTED and m["resolution"] == "320x240" and m["frames"] >= 3
    assert m["latency_ms"] is not None and m["changing_frames"] >= 2
    bad = connect.measure(MockCameraSource(fail_open=True), seconds=0.5)
    assert bad["status"] == H.STREAM_UNAVAILABLE


# ----------------------------------------------------------- ONVIF / XML
PROBE_MATCH = """<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><s:Body><d:ProbeMatches><d:ProbeMatch>
 <d:Scopes>onvif://www.onvif.org/name/EZVIZ_H1C onvif://www.onvif.org/hardware/CS-H1C-R100
 onvif://www.onvif.org/location/office</d:Scopes><d:XAddrs>http://192.168.1.64:80/onvif/device_service</d:XAddrs>
 </d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"""


def test_ws_discovery_probe_match_parsing():
    m = onvif.parse_probe_match(PROBE_MATCH.encode())
    assert m["name"] == "EZVIZ H1C" and m["hardware"] == "CS-H1C-R100"
    assert m["xaddrs"] == ["http://192.168.1.64:80/onvif/device_service"]
    assert connect.detect_vendor(m["name"], m["hardware"]) == "EZVIZ"


def test_onvif_xml_with_entities_is_refused():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    with pytest.raises(onvif.OnvifError):
        onvif.parse_xml(bomb)
    with pytest.raises(onvif.OnvifError):
        onvif.parse_xml(b"<x>" + b"a" * (onvif.MAX_XML + 10) + b"</x>")


def test_device_advertised_addresses_are_pinned_to_the_device_ip():
    assert onvif.pin_url("http://169.254.169.254:8080/onvif/device_service", "192.168.1.64") == \
        ("192.168.1.64", 8080, "/onvif/device_service")
    with pytest.raises(onvif.OnvifError):
        onvif.pin_url("file:///etc/passwd", "192.168.1.64")


def test_onvif_security_header_has_digest_not_password():
    hdr = onvif.security_header("admin", SECRET, created="2026-01-01T00:00:00.000Z", nonce=b"0123456789abcdef")
    assert SECRET not in hdr and "PasswordDigest" in hdr
    assert "***" in redact(f"<s:Envelope>{hdr}</s:Envelope>")


class FakeHTTP:
    replies = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def request(self, method, path, body=None, headers=None):
        FakeHTTP.sent = (self.host, self.port, path, body)

    def getresponse(self):
        status, data = FakeHTTP.replies.pop(0)

        class R:
            def read(self, n):
                return data
        r = R()
        r.status = status
        return r

    def close(self):
        pass


def test_onvif_client_profiles_and_stream_uri_never_trust_the_advertised_host():
    caps = ("<s:Envelope xmlns:s='x'><s:Body><Capabilities><Media><XAddr>http://8.8.8.8:8899/onvif/media"
            "</XAddr></Media></Capabilities></s:Body></s:Envelope>")
    profs = ("<s:Envelope xmlns:s='x'><s:Body><GetProfilesResponse><Profiles token='p1'><Name>Main</Name>"
             "<Resolution><Width>1920</Width><Height>1080</Height></Resolution></Profiles></GetProfilesResponse>"
             "</s:Body></s:Envelope>")
    uri = ("<s:Envelope xmlns:s='x'><s:Body><GetStreamUriResponse><MediaUri><Uri>rtsp://evil:pw@8.8.8.8:5554/live"
           "</Uri></MediaUri></GetStreamUriResponse></s:Body></s:Envelope>")
    FakeHTTP.replies = [(200, caps.encode()), (200, profs.encode()), (200, uri.encode())]
    c = onvif.OnvifClient("192.168.1.64", 80, username="admin", password=SECRET, http_factory=FakeHTTP)
    assert c.profiles() == [{"token": "p1", "name": "Main", "width": 1920, "height": 1080}]
    s = c.stream_uri("p1")
    assert s == {"port": 5554, "path": "/live"}
    assert FakeHTTP.sent[0] == "192.168.1.64" and FakeHTTP.sent[1] == 8899      # pinned to the device
    assert SECRET.encode() not in FakeHTTP.sent[3]
    FakeHTTP.replies = [(400, b"<x>NotAuthorized</x>")]
    with pytest.raises(onvif.OnvifAuthError):
        onvif.OnvifClient("192.168.1.64", http_factory=FakeHTTP, username="a", password="b").device_information()


# ------------------------------------------------------------- discovery
def test_discovery_job_is_bounded_owned_and_stoppable():
    g = NetGuard()
    dm = DiscoveryManager(g, strategies=[mock_discovery_strategy("192.168.1.64")], local_ips=[], neighbours=[])
    job = dm.start(owner_id=2)
    assert wait_until(lambda: job.status != "RUNNING")
    pub = job.public()
    assert pub["count"] == 1 and pub["results"][0]["status"] == "AUTH_REQUIRED"
    assert pub["results"][0]["vendor"] == "EZVIZ" and pub["results"][0]["host"] == "192.168.1.64"
    assert not any(k.startswith("_") for k in pub["results"][0])          # internal fields never leave the server
    assert dm.get(job.job_id, owner_id=3) is None                          # another user's job is invisible
    rid = pub["results"][0]["discovery_id"]
    assert dm.lookup(2, rid)["host"] == "192.168.1.64" and dm.lookup(3, rid) is None
    assert dm.timeout <= 20


def test_discovery_lan_scan_only_probes_private_hosts_with_bounded_concurrency():
    from focusguard.cameras import discovery
    probed = []

    def connect_(ip, port, timeout=0.4):
        probed.append((ip, port))
        return ip == "192.168.7.20" and port in (554, 8000)
    dm = DiscoveryManager(NetGuard(), strategies=[discovery.strategy_lan_scan], local_ips=["192.168.7.5"],
                          neighbours=["192.168.7.20", "8.8.8.8", "169.254.169.254"], connect=connect_,
                          rtsp_options=lambda ip, port: (rtsp.OK, "Hikvision-Webs"),
                          rtsp_probe=lambda ip, port, path: rtsp.ProbeResult(rtsp.AUTH_REQUIRED, None, False, None, 401))
    job = dm.start(1)
    assert wait_until(lambda: job.status != "RUNNING", 10)
    hosts = {ip for ip, _ in probed}
    assert "8.8.8.8" not in hosts and "169.254.169.254" not in hosts and "192.168.7.5" not in hosts
    assert all(h.startswith("192.168.7.") for h in hosts) and len(hosts) <= 254
    res = job.public()["results"]
    assert len(res) == 1 and res[0]["vendor"] == "Hikvision" and res[0]["status"] == "AUTH_REQUIRED"
    assert discovery.SCAN_WORKERS <= 64 and discovery.MAX_SUBNETS <= 2


def test_discovery_limits_parallel_jobs():
    import threading
    gate = threading.Event()

    def slow(ctx):
        gate.wait(2)
    dm = DiscoveryManager(NetGuard(), strategies=[slow], local_ips=[], neighbours=[], max_running=1)
    j1 = dm.start(1)
    assert dm.start(1) is j1                             # same user: the running job
    with pytest.raises(RuntimeError):
        dm.start(2)
    gate.set()
    assert wait_until(lambda: j1.status != "RUNNING")
    dm.stop(j1.job_id, 1)


# --------------------------------------------- no frame != AWAY (runtime)
def _visible(t):
    return Observation(timestamp=t, visible=True, face_visible=True, ear=0.3, yaw=0.0, pitch=0.0)


def test_camera_loss_never_becomes_away(manager, clock):
    rt = manager.start_class(1, mode="classroom", started_by=2)
    sid = 3
    # 1) student visible, then briefly not visible (below the AWAY threshold) ...
    for _ in range(10):
        t = clock.advance(0.2)
        rt.process_frame(t, {sid: _visible(t)})
    for _ in range(5):
        t = clock.advance(0.2)
        rt.process_frame(t, {})                       # 1 s not visible: TEMPORARILY_NOT_VISIBLE
    assert rt.student_view(sid)["visibility_status"] == rs.VIS_TEMPORARILY_NOT_VISIBLE
    # 2) ... then the camera dies for 60 s (no frames at all)
    clock.advance(60.0)
    assert rt.heartbeat(clock()) == "NO_SIGNAL"
    assert rt.student_view(sid)["visibility_status"] == rs.VIS_NO_SIGNAL
    # 3) frames resume and the student is still out of frame for 2 s: not AWAY (needs a NEW run)
    for _ in range(10):
        t = clock.advance(0.2)
        rt.process_frame(t, {})
    view = rt.student_view(sid)
    assert view["visibility_status"] != rs.VIS_AWAY and view["focus_state"] != rs.FOCUS_AWAY
    manager.end_class(1)
    events = manager.repo.get_focus_events(class_session_id=rt.class_session_id)
    assert [e for e in events if e["type"] == "AWAY"] == []


def test_episode_open_when_the_camera_dies_ends_at_the_last_frame(manager, clock):
    rt = manager.start_class(1, mode="classroom", started_by=2)
    sid = 3
    for _ in range(10):
        t = clock.advance(0.2)
        rt.process_frame(t, {sid: _visible(t)})
    for _ in range(int(manager.config.behavior.away_min_seconds / 0.2) + 10):
        t = clock.advance(0.2)
        rt.process_frame(t, {})                       # a REAL away episode starts
    last_frame = clock()
    assert rt.student_view(sid)["visibility_status"] == rs.VIS_AWAY
    clock.advance(120.0)                              # camera lost for 2 minutes
    rt.heartbeat(clock())
    manager.end_class(1)
    away = [e for e in manager.repo.get_focus_events(class_session_id=rt.class_session_id) if e["type"] == "AWAY"]
    assert len(away) == 1
    assert away[0]["end_time"] == pytest.approx(last_frame)             # outage not counted
    assert away[0]["metadata"].get("ended_by") == "NO_CAMERA_SIGNAL"


def test_score_frozen_during_camera_loss_and_resumes(manager, clock):
    rt = manager.start_class(1, mode="classroom", started_by=2)
    for _ in range(25):
        t = clock.advance(0.2)
        rt.process_frame(t, {3: _visible(t)})
    score = rt.states[3].focus_score
    clock.advance(30)
    rt.heartbeat(clock())
    assert rt.states[3].focus_score == score and rt.camera_status == "NO_SIGNAL"
    t = clock.advance(0.2)
    rt.process_frame(t, {3: _visible(t)})
    assert rt.camera_status == "ACTIVE" and rt.states[3].focus_score == score
    manager.end_class(1)


# ----------------------------------------------------- pipeline semantics
class FakePipelineReader:
    """Reader stub for the ClassroomAI.get_frame contract (no CV stack needed)."""


def test_classroom_pipeline_source_contract():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "classroom_ai.py"),
               encoding="utf-8").read()
    assert "cv2.VideoCapture" not in src and "LatestFrameReader" in src and "WebcamSource(camera_index" in src
    assert "self.reader.get_newer(self._seq)" in src            # never the same frame twice
    assert "now = latest.timestamp" in src                      # frame time, not processing time
    personal = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "camera_ai.py"),
                    encoding="utf-8").read()
    assert "cv2.VideoCapture" not in personal and "LatestFrameReader" in personal


# ------------------------------------------------------- check tool
def test_run_check_pass_with_a_healthy_stream():
    r = run_check(MockCameraSource(name="classroom-front", fps=50), seconds=1.0, test_reconnect=True)
    assert r["STATUS"] == PASS and r["CHANGING_FRAMES"] >= 2 and r["RESOLUTION"] == "320x240"
    assert r["RECONNECT"] == PASS and r["RECONNECTS"] >= 1 and r["CAMERA_INGEST_LATENCY_MS"] is not None


def test_run_check_without_a_camera_is_hardware_required_not_fake():
    r = run_check(MockCameraSource(fail_open=True), seconds=0.5, connect_timeout=0.5)
    assert r["STATUS"] == HARDWARE_REQUIRED and r["CONNECT"] == "FAIL"


def test_ip_camera_check_tool_saved_camera_and_output_has_no_secret(manager):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import ip_camera_check
    manager.repo.create_camera({"owner_user_id": 2, "name": "classroom-front", "source_type": "RTSP",
                                "host": "192.168.1.20", "port": 554, "stream_path": "/H.264", "username": "admin",
                                "encrypted_secret": "fgc1:abc:" + SECRET, "is_shared": False})
    lines = []
    code = ip_camera_check.main(["--camera-name", "classroom-front", "--teacher", "teacher", "--seconds", "0.5"],
                                sm=manager, source_factory=lambda cam: MockCameraSource(name=cam["name"], fps=50),
                                out=lines.append)
    text = "\n".join(lines)
    assert code == 0 and "STATUS: PASS" in text and "CAMERA_NAME: classroom-front" in text
    for key in ("SOURCE_TYPE", "RESOLUTION", "MEASURED_FPS", "FRAME_AGE_MS", "STALE_FRAMES", "RECONNECTS"):
        assert key + ":" in text
    assert SECRET not in text
    lines.clear()
    assert ip_camera_check.main(["--camera-name", "nope"], sm=manager, source_factory=lambda c: None,
                                out=lines.append) == 1 and "CONFIG_ERROR" in "\n".join(lines)


def test_ip_camera_check_tool_blocks_public_hosts():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import ip_camera_check
    lines = []
    assert ip_camera_check.main(["--host", "169.254.169.254"], source_factory=lambda r: None, secret="x",
                                out=lines.append) == 1
    assert "HOST_NOT_ALLOWED" in "\n".join(lines)


# ------------------------------------------------- field test integration
def _field_tool():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import field_test
    return field_test


def test_field_test_camera_name_resolution(manager):
    ft = _field_tool()
    from focusguard.evaluation.real_classroom import field
    mine = manager.repo.create_camera({"owner_user_id": 2, "name": "classroom-front", "source_type": "RTSP",
                                       "host": "192.168.1.20", "is_default": True, "is_shared": False})
    args = ft.build_parser().parse_args(["--camera-name", "Classroom-Front", "--class-id", "1", "--teacher", "teacher"])
    src, err = ft.resolve_camera(manager, args)
    assert err is None and isinstance(src, field.CameraSource) and src.camera["camera_id"] == mine
    meta = src.camera_metadata()
    assert meta == {"camera_id": mine, "camera_name": "classroom-front", "camera_source_type": "RTSP",
                    "camera_vendor": None, "camera_model": None}                     # no host, no secret
    src, err = ft.resolve_camera(manager, ft.build_parser().parse_args(["--camera-name", "nope"]))
    assert src is None and "not found" in err and "classroom-front" in err
    src, err = ft.resolve_camera(manager, ft.build_parser().parse_args(["--class-id", "1"]))
    assert err is None and src.camera["camera_id"] == mine                         # default camera
    src, err = ft.resolve_camera(manager, ft.build_parser().parse_args(["--camera", "2"]))
    assert err is None and src.camera is None and src.camera_index == 2            # explicit webcam


def test_field_test_without_saved_cameras_uses_webcam_0(manager):
    ft = _field_tool()
    src, err = ft.resolve_camera(manager, ft.build_parser().parse_args([]))
    assert err is None and src.camera is None and src.camera_index == 0


def test_field_run_preflight_checks_the_saved_ip_camera(manager):
    from focusguard.evaluation.real_classroom import field
    cid = manager.repo.create_camera({"owner_user_id": 2, "name": "classroom-front", "source_type": "RTSP",
                                      "host": "192.168.1.20", "is_shared": False})
    manager.cameras.source_factory = lambda spec: MockCameraSource(name=spec.get("name"), fps=40)
    src = field.CameraSource(camera=manager.repo.get_camera(cid))
    ok_, detail = src.check(manager, timeout=3)
    assert ok_ and "classroom-front" in detail
    manager.cameras.source_factory = lambda spec: MockCameraSource(fail_open=True)
    ok_, detail = src.check(manager, timeout=0.5)
    assert not ok_ and "192.168" not in detail
    io = field.ScriptedIO(echo=False)
    run = field.FieldRun(manager, io, src, class_id=1, teacher="teacher", strict=True)
    code = run.run()
    cam_check = next(c for c in run.result["preflight"] if c["check"] == "camera")
    assert cam_check["status"] == field.HARDWARE_REQUIRED and code == field.EXIT_HARDWARE
    assert "evaluation_session_id" not in run.result                               # nothing faked
