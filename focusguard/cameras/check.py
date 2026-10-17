"""Stream quality check used by tools/ip_camera_check.py (and its tests).

Measures, through the same LatestFrameReader the classroom pipeline uses:
connect, first-frame latency (CAMERA_INGEST_LATENCY), changing frames,
resolution, measured FPS, frame age, stale / dropped frames, stability (share
of the window with a fresh frame) and, optionally, a forced reconnect.
It never prints or returns credentials.
"""
import time

from . import health as H
from .reader import LatestFrameReader, frame_signature

PASS, FAIL = "PASS", "FAIL"
HARDWARE_REQUIRED = "IP_CAMERA_HARDWARE_REQUIRED"


def run_check(source, seconds=10.0, test_reconnect=False, connect_timeout=15.0, clock=time.monotonic,
              sleep=time.sleep, reader=None):
    reader = reader or LatestFrameReader(source, max_backoff=4.0)
    reader.start()
    out = {"STATUS": None, "CONNECT": FAIL, "STREAM": FAIL, "CHANGING_FRAMES": 0, "FRAMES": 0,
           "CAMERA_INGEST_LATENCY_MS": None, "STABILITY": None, "RECONNECT": "NOT_TESTED",
           "RECONNECT_SECONDS": None}
    try:
        t0 = clock()
        first = None
        while clock() - t0 < connect_timeout:
            first = reader.get_newer(0)
            if first is not None:
                break
            if reader.state == H.AUTH_FAILED:
                break
            sleep(0.05)
        if first is None:
            st = reader.health()["state"]
            out["STATUS"] = H.AUTH_FAILED if st == H.AUTH_FAILED else HARDWARE_REQUIRED
            out["DETAIL"] = H.LABELS_VI.get(st, st)
            return _finish(out, reader)
        out["CONNECT"] = out["STREAM"] = PASS
        out["CAMERA_INGEST_LATENCY_MS"] = reader.first_frame_latency_ms
        seq, sigs, fresh, ticks = first.seq, {frame_signature(first.image)}, 0, 0
        frames = 1
        t1 = clock()
        while clock() - t1 < seconds:
            f = reader.get_newer(seq)
            ticks += 1
            if f is not None:
                seq = f.seq
                frames += 1
                sigs.add(frame_signature(f.image))
            if reader.has_signal():
                fresh += 1
            sleep(0.05)
        out["FRAMES"] = frames
        out["CHANGING_FRAMES"] = len(sigs)
        out["STABILITY"] = round(fresh / ticks, 3) if ticks else None
        if test_reconnect:
            before = reader.reconnect_count
            reader.request_reconnect()
            t2 = clock()
            while clock() - t2 < connect_timeout:
                if reader.reconnect_count > before and reader.has_signal():
                    break
                sleep(0.05)
            ok = reader.reconnect_count > before and reader.has_signal()
            out["RECONNECT"] = PASS if ok else FAIL
            out["RECONNECT_SECONDS"] = round(clock() - t2, 2) if ok else None
        good = out["CHANGING_FRAMES"] >= 2 and (out["STABILITY"] or 0) >= 0.8 and out["RECONNECT"] != FAIL
        out["STATUS"] = PASS if good else "DEGRADED"
        return _finish(out, reader)
    finally:
        reader.stop()


def _finish(out, reader):
    h = reader.health()
    src = reader.source
    out.update({"CAMERA_NAME": getattr(src, "name", None), "SOURCE_TYPE": getattr(src, "source_type", None),
                "VENDOR": getattr(src, "vendor", None), "MODEL": getattr(src, "model", None),
                "RESOLUTION": h["resolution"], "MEASURED_FPS": h["measured_fps"], "FRAME_AGE_MS": h["frame_age_ms"],
                "STALE_FRAMES": h["stale_frames"], "DROPPED_FRAMES": h["dropped_frames"],
                "RECONNECTS": h["reconnect_count"], "LAST_ERROR": h["last_error_sanitized"]})
    return out


ORDER = ("CAMERA_NAME", "SOURCE_TYPE", "VENDOR", "MODEL", "RESOLUTION", "MEASURED_FPS", "FRAME_AGE_MS",
         "STALE_FRAMES", "DROPPED_FRAMES", "RECONNECTS", "CAMERA_INGEST_LATENCY_MS", "CONNECT", "STREAM",
         "FRAMES", "CHANGING_FRAMES", "STABILITY", "RECONNECT", "RECONNECT_SECONDS", "LAST_ERROR", "DETAIL", "STATUS")
