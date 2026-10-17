"""Easy connect: from (camera IP, username, password / verification code) to a
working RTSP stream, without the user ever typing an RTSP URL.

Order (stops at the first definitive answer):

1. an explicit stream path (advanced mode) — probed directly;
2. ONVIF (if the device speaks it): manufacturer/model, media profiles,
   GetStreamUri; the RTSP path it returns is then probed;
3. RTSP path candidates: vendor hints first (EZVIZ: ``/H.264``; Hikvision
   family: ``/Streaming/Channels/101``…), then a short generic list.

A rejected password stops the search immediately (cameras such as EZVIZ /
Hikvision lock the account after a few failed logins), so wrong credentials
cost exactly one failed attempt. Then :func:`measure` opens the stream with
OpenCV through the same LatestFrameReader the classroom uses and reports
resolution, measured FPS and connection latency.

EZVIZ: not every model/firmware exposes a local RTSP/ONVIF stream; some are
cloud-only. FocusGuard never logs in to the EZVIZ cloud and never reverse-
engineers it: if no local stream answers, the result is
DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM.
"""
import time

from . import health as H
from . import onvif, rtsp
from .redact import redact

PROTOCOL_AUTO, PROTOCOL_RTSP, PROTOCOL_ONVIF = "AUTO", "RTSP", "ONVIF"
PROTOCOLS = (PROTOCOL_AUTO, PROTOCOL_RTSP, PROTOCOL_ONVIF)

VENDOR_PATTERNS = [
    ("EZVIZ", ("ezviz",)), ("Hikvision", ("hikvision", "hik-connect", "dnvrs-webs")), ("Dahua", ("dahua",)),
    ("Imou", ("imou",)), ("TP-Link", ("tp-link", "tplink", "tapo")), ("Reolink", ("reolink",)),
    ("Xiaomi", ("xiaomi", "mijia")), ("Axis", ("axis",)), ("Uniview", ("uniview", "unv")),
]
VENDOR_PATHS = {
    "EZVIZ": ["/H.264", "/Streaming/Channels/101", "/Streaming/Channels/102", "/h264/ch1/main/av_stream"],
    "Hikvision": ["/Streaming/Channels/101", "/Streaming/Channels/102", "/h264/ch1/main/av_stream", "/H.264"],
    "Dahua": ["/cam/realmonitor?channel=1&subtype=0", "/cam/realmonitor?channel=1&subtype=1"],
    "Imou": ["/cam/realmonitor?channel=1&subtype=0", "/cam/realmonitor?channel=1&subtype=1"],
    "TP-Link": ["/stream1", "/stream2"],
    "Reolink": ["/h264Preview_01_main", "/h264Preview_01_sub"],
}
GENERIC_PATHS = ["/H.264", "/Streaming/Channels/101", "/stream1", "/live", "/cam/realmonitor?channel=1&subtype=0",
                 "/h264Preview_01_main", "/live/ch00_0", "/onvif1", "/11", "/"]
DEFAULT_USERNAMES = {"EZVIZ": "admin", "Hikvision": "admin"}


def detect_vendor(*texts):
    blob = " ".join(t for t in texts if t).lower()
    for vendor, keys in VENDOR_PATTERNS:
        if any(k in blob for k in keys):
            return vendor
    return None


def candidate_paths(vendor=None, explicit=None):
    out = []
    for p in ([explicit] if explicit else []) + VENDOR_PATHS.get(vendor or "", []) + GENERIC_PATHS:
        if p and p not in out:
            out.append(p)
    return out


class Deps:
    """Network operations, injectable for tests (mock RTSP / ONVIF)."""
    rtsp_probe = staticmethod(rtsp.probe)
    rtsp_options = staticmethod(rtsp.options)
    onvif_client = onvif.OnvifClient


def _port_ok(port_guard, port):
    if port_guard is None:
        return True
    try:
        port_guard(port)
        return True
    except ValueError:            # HostNotAllowed
        return False


def resolve_stream(ip, username=None, secret=None, protocol=PROTOCOL_AUTO, port=None, path=None, profile=None,
                   vendor_hint=None, onvif_port=None, onvif_path=None, deps=Deps, rtsp_only_paths=None,
                   port_guard=None):
    """Find the stream. Returns a dict (never contains the secret):

    ``status`` (STREAM_FOUND | AUTH_REQUIRED | AUTH_FAILED | DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM |
    STREAM_UNAVAILABLE | UNREACHABLE), ``port``, ``path``, ``profile``, ``vendor``, ``model``,
    ``auth_required``, ``device_found``, ``codec``, ``steps`` (sanitised log)."""
    res = {"status": H.UNREACHABLE, "port": int(port or 554), "path": None, "profile": None, "vendor": vendor_hint,
           "model": None, "auth_required": None, "device_found": False, "codec": None, "steps": []}

    def step(kind, outcome):
        res["steps"].append(f"{kind}: {redact(outcome, secrets=(secret,))}"[:160])

    rport = int(port or 554)
    onvif_path_found = None
    try_onvif = protocol in (PROTOCOL_AUTO, PROTOCOL_ONVIF) and not path
    if try_onvif and not _port_ok(port_guard, int(onvif_port or 80)):
        if protocol == PROTOCOL_ONVIF:
            port_guard(int(onvif_port or 80))          # raises HostNotAllowed(port_not_allowed)
        step("onvif", "port not allowed by the camera-network policy")
        try_onvif = False
    if try_onvif:
        client = deps.onvif_client(ip, int(onvif_port or 80), onvif_path or "/onvif/device_service",
                                   username=username, password=secret)
        try:
            info = client.device_information()
            res["device_found"] = True
            res["vendor"] = detect_vendor(info.get("vendor")) or (info.get("vendor") or "")[:40] or res["vendor"]
            res["model"] = (info.get("model") or "")[:60] or None
            if port_guard is not None and hasattr(client, "media_service"):
                # the media service address is advertised by the device: host pinned to the device IP,
                # and its port must be a camera port too (never a pivot to another local service)
                media = client.media_service()
                if not _port_ok(port_guard, media[1]):
                    raise onvif.OnvifError("media service port not allowed")
            profs = client.profiles()
            chosen = next((p for p in profs if p["token"] == profile), None) or (
                max(profs, key=lambda p: (p["width"] or 0) * (p["height"] or 0)) if profs else None)
            if chosen:
                uri = client.stream_uri(chosen["token"])
                if _port_ok(port_guard, uri["port"]):
                    rport, onvif_path_found = uri["port"], uri["path"]
                    res["profile"] = chosen["name"]
                    step("onvif", f"profile {chosen['name']} -> {onvif_path_found}")
                else:
                    step("onvif", "advertised stream port not allowed by the camera-network policy")
        except onvif.OnvifAuthError:
            res["device_found"] = True
            res["auth_required"] = True
            step("onvif", "not authorized")
            if protocol == PROTOCOL_ONVIF:
                res["status"] = H.AUTH_FAILED if username else H.AUTH_REQUIRED
                return res
        except onvif.OnvifError as exc:
            step("onvif", f"unavailable ({str(exc)[:60]})")
            if protocol == PROTOCOL_ONVIF and not res["device_found"]:
                res["status"] = H.UNREACHABLE
                return res

    if port_guard is not None:
        port_guard(rport)                              # raises HostNotAllowed(port_not_allowed)
    reachable, server = deps.rtsp_options(ip, rport)
    if reachable == rtsp.UNREACHABLE:
        step("rtsp", f"port {rport} closed")
        res["status"] = H.DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM if res["device_found"] else H.UNREACHABLE
        return res
    res["device_found"] = True
    res["vendor"] = res["vendor"] or detect_vendor(server)
    explicit = path or onvif_path_found
    paths = [explicit] if (explicit and protocol == PROTOCOL_ONVIF) else (
        rtsp_only_paths or candidate_paths(res["vendor"], explicit))
    for p in paths:
        r = deps.rtsp_probe(ip, rport, p, username=username, secret=secret)
        step("rtsp", f"{p} -> {r.status}")
        if r.server and not res["vendor"]:
            res["vendor"] = detect_vendor(r.server)
        if r.status == rtsp.OK:
            res.update(status=H.STREAM_FOUND, port=rport, path=p, codec=r.codec,
                       auth_required=bool(username) or res["auth_required"])
            return res
        if r.status == rtsp.AUTH_REQUIRED:
            res.update(status=H.AUTH_REQUIRED, auth_required=True, port=rport)
            return res
        if r.status == rtsp.AUTH_FAILED:
            res.update(status=H.AUTH_FAILED, auth_required=True, port=rport)
            return res
        if r.status == rtsp.UNREACHABLE:
            res.update(status=H.STREAM_UNAVAILABLE, port=rport)
            return res
        if r.status == rtsp.REDIRECT:
            # never follow a camera to another address (SSRF): such a device is not usable
            res.update(status=H.DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM, port=rport)
            return res
        if explicit and p == explicit and path:
            break            # advanced mode: only the path the user gave
    res.update(status=H.DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM, port=rport)
    return res


def measure(source, seconds=3.0, min_frames=3, reader_factory=None, clock=time.monotonic, sleep=time.sleep):
    """Open ``source`` through a LatestFrameReader and measure it.

    Returns {"status", "resolution", "measured_fps", "latency_ms", "frames", "changing_frames"}."""
    from .reader import LatestFrameReader, frame_signature
    reader = (reader_factory or (lambda s: LatestFrameReader(s, auto_reconnect=False, detect_repeats=False)))(source)
    reader.start()
    t0 = clock()
    seq, frames, sigs, first = 0, 0, set(), None
    try:
        while clock() - t0 < seconds:
            f = reader.get_newer(seq)
            if f is None:
                if reader.state in (H.OFFLINE, H.AUTH_FAILED) and not reader.running:
                    break
                sleep(0.02)
                continue
            seq = f.seq
            frames += 1
            if first is None:
                first = clock()
            sigs.add(frame_signature(f.image))
            if frames >= max(min_frames, 1) and clock() - first >= min(1.0, seconds / 2):
                break
        h = reader.health()
    finally:
        reader.stop()
    ok = frames >= 1
    return {"status": H.CONNECTED if ok else (H.AUTH_FAILED if h["state"] == H.AUTH_FAILED else H.STREAM_UNAVAILABLE),
            "resolution": h["resolution"], "measured_fps": h["measured_fps"],
            "latency_ms": h["first_frame_latency_ms"] if ok else None, "frames": frames,
            "changing_frames": len(sigs), "error": None if ok else h["last_error_sanitized"]}
