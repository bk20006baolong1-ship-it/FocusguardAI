#!/usr/bin/env python3
"""Check a real IP camera (EZVIZ / generic RTSP / ONVIF) or webcam before a class.

    python tools/ip_camera_check.py --camera-name classroom-front [--teacher teacher] [--class-id 1]
    python tools/ip_camera_check.py --host 192.168.1.20 [--username admin] [--protocol AUTO|RTSP|ONVIF]
        [--port 554] [--path /H.264]
    common: [--seconds 10] [--test-reconnect] [--strict]

``--camera-name`` uses a camera saved in Cài đặt › Camera (its password is
decrypted server-side). ``--host`` tests a camera that is not saved yet: the
password / verification code is read from the environment variable
``FG_CAMERA_SECRET`` or asked interactively (never a command-line argument, so it
does not land in the shell history). Only private-LAN addresses are allowed.

Checks: connect, authentication, stream, changing frames, resolution, measured
FPS, frame age, stale frames, stability, reconnect (``--test-reconnect``).
Prints ``KEY: value`` lines ending with ``STATUS:``. The secret is never printed.

STATUS: PASS (exit 0) | DEGRADED (exit 1) | AUTH_FAILED (exit 1) | HOST_NOT_ALLOWED / CONFIG_ERROR (exit 1) |
IP_CAMERA_HARDWARE_REQUIRED — no reachable camera / no OpenCV (exit 0, ``--strict``: exit 2). Nothing is faked.
CAMERA_INGEST_LATENCY_MS is the camera/network part only (open -> first frame), never mixed into the AI
model benchmark.
"""
import argparse
import getpass
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--camera-name", help="a camera saved in Cài đặt › Camera")
    g.add_argument("--host", help="private-LAN IP / host name of a camera that is not saved yet")
    ap.add_argument("--teacher", default="teacher", help="account that owns / may use the saved camera")
    ap.add_argument("--class-id", type=int, default=None)
    ap.add_argument("--username", default=None)
    ap.add_argument("--protocol", default="AUTO", choices=("AUTO", "RTSP", "ONVIF"))
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--path", default=None)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--test-reconnect", action="store_true")
    ap.add_argument("--strict", action="store_true")
    return ap


def _emit(out, result):
    from focusguard.cameras.check import ORDER
    from focusguard.cameras.redact import redact
    for k in ORDER:
        if k in result and result[k] is not None:
            out(f"{k}: {redact(result[k])}")


def _exit(status, strict):
    from focusguard.cameras.check import HARDWARE_REQUIRED, PASS
    if status == PASS:
        return 0
    if status == HARDWARE_REQUIRED:
        return 2 if strict else 0
    return 1


def main(argv=None, sm=None, source_factory=None, out=print, secret=None):
    a = build_parser().parse_args(argv)
    from focusguard.cameras import health as H
    from focusguard.cameras.check import HARDWARE_REQUIRED, run_check
    if source_factory is None:
        try:
            import cv2  # noqa: F401
        except ImportError:
            _emit(out, {"DETAIL": "OpenCV is not installed (pip install -r requirements.txt)",
                        "STATUS": HARDWARE_REQUIRED})
            return _exit(HARDWARE_REQUIRED, a.strict)

    if a.camera_name:
        from focusguard.authz import build_principal
        if sm is None:
            from repository import get_repository
            from session_manager import SessionManager
            sm = SessionManager(repo=get_repository())
        user = sm.repo.get_user_by_username(a.teacher)
        p = build_principal(user["id"], user.get("role"), sm.repo) if user else None
        if p is None:
            _emit(out, {"DETAIL": f"account {a.teacher!r} not found", "STATUS": "CONFIG_ERROR"})
            return 1
        class_ids = [a.class_id] if a.class_id is not None else (sorted(p.class_ids) or [None])
        cam = next((c for cid in class_ids for c in [sm.cameras.find_by_name(p, cid, a.camera_name)] if c), None)
        if cam is None and p.is_admin:
            cam = next((c for c in sm.repo.list_cameras(is_shared=True)
                        if (c.get("name") or "").casefold() == a.camera_name.casefold()), None)
        if cam is None:
            _emit(out, {"DETAIL": f"camera {a.camera_name!r} not found for {a.teacher!r}", "STATUS": "CONFIG_ERROR"})
            return 1
        try:
            source = source_factory(cam) if source_factory else sm.cameras.open_source(cam)
        except Exception as exc:
            _emit(out, {"CAMERA_NAME": cam.get("name"), "DETAIL": getattr(exc, "message", type(exc).__name__),
                        "STATUS": "CONFIG_ERROR"})
            return 1
    else:
        from focusguard.cameras.connect import resolve_stream
        from focusguard.cameras.netguard import HostNotAllowed, NetGuard
        from focusguard.cameras.sources import RTSPSource
        # The operator runs this on the classroom machine itself: a local-subnet address is accepted
        # like a discovered one; FG_CAMERA_ALLOWED_NETWORKS (when set) still applies.
        guard = NetGuard.from_env(manual_local=True)
        try:
            ip = guard.resolve_host(a.host)
            if a.port is not None:
                guard.check_port(a.port)
        except HostNotAllowed as exc:
            _emit(out, {"DETAIL": str(exc), "STATUS": H.HOST_NOT_ALLOWED})
            return 1
        if secret is None:
            secret = os.environ.get("FG_CAMERA_SECRET")
        if secret is None and sys.stdin.isatty():
            secret = getpass.getpass("Mật khẩu / mã xác minh camera (để trống nếu không có): ") or None
        username = a.username or ("admin" if secret else None)
        try:
            r = resolve_stream(ip, username, secret, a.protocol, a.port, a.path, port_guard=guard.check_port)
        except HostNotAllowed as exc:
            _emit(out, {"DETAIL": str(exc), "STATUS": H.HOST_NOT_ALLOWED})
            return 1
        if r["status"] != H.STREAM_FOUND:
            status = H.AUTH_FAILED if r["status"] in (H.AUTH_FAILED, H.AUTH_REQUIRED) else HARDWARE_REQUIRED
            _emit(out, {"VENDOR": r["vendor"], "MODEL": r["model"],
                        "DETAIL": H.LABELS_VI.get(r["status"], r["status"]), "STATUS": status})
            return _exit(status, a.strict)
        source = source_factory(r) if source_factory else RTSPSource(
            ip, r["port"], r["path"], username, secret_provider=lambda: secret, name=r["vendor"] or "IP camera",
            vendor=r["vendor"], model=r["model"], netguard=guard)
    result = run_check(source, seconds=a.seconds, test_reconnect=a.test_reconnect)
    _emit(out, result)
    return _exit(result["STATUS"], a.strict)


if __name__ == "__main__":
    sys.exit(main())
