"""LatestFrameReader: a dedicated reader thread with a single latest-frame slot.

IP cameras (and FFmpeg) buffer frames. Reading them from the analysis loop
would analyse ever older frames and latency would grow with time. Instead one
thread reads the source as fast as it delivers and keeps ONLY the newest frame:

* the consumer asks for "a frame newer than sequence N" (non-blocking
  :meth:`get_newer`, or :meth:`wait_newer` for plain-thread tools) and gets the
  latest one, never a backlog; overwritten, never-consumed frames are counted
  as ``dropped_frame_count``;
* the same frame is never handed out twice, so the AI pipeline never feeds the
  BehaviorAnalyzer the same frame/timestamp again;
* a source that keeps returning a byte-identical frame (frozen stream) is not
  "new": counted as ``stale_frame_count`` and, after ``stale_after`` seconds,
  treated as lost (NO_SIGNAL);
* read failures / no new frame for ``stale_after`` seconds -> NO_SIGNAL, the
  source is closed and reopened with bounded exponential backoff
  (``initial_backoff`` .. ``max_backoff``), ``reconnect_count`` increments.

Health (:meth:`health`) never contains credentials: errors are redacted.
"""
import hashlib
import threading
import time
from collections import deque, namedtuple

from . import health as H
from .redact import redact, safe_error

Frame = namedtuple("Frame", "seq timestamp image")


def frame_signature(image):
    """Cheap content signature of a frame (a sparse sample), used only to detect frozen streams."""
    try:
        sample = image[::8, ::8]
        data = sample.tobytes()
    except Exception:
        data = bytes(memoryview(image))[::64] if image is not None else b""
    return hashlib.blake2b(data, digest_size=8).digest()


class LatestFrameReader:
    def __init__(self, source, clock=time.time, stale_after=3.0, degraded_age=1.0, initial_backoff=0.5,
                 max_backoff=20.0, max_read_failures=10, detect_repeats=None, auto_reconnect=True,
                 fps_window=30):
        self.source = source
        self.clock = clock
        self.stale_after = float(stale_after)
        self.degraded_age = float(degraded_age)
        self.initial_backoff = float(initial_backoff)
        self.max_backoff = float(max_backoff)
        self.max_read_failures = int(max_read_failures)
        self.detect_repeats = (getattr(source, "source_type", None) == "RTSP") if detect_repeats is None \
            else bool(detect_repeats)
        self.auto_reconnect = auto_reconnect
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._reconnect = threading.Event()
        self._thread = None
        self._seq = 0
        self._consumed = 0
        self._frame = None
        self._ts = None
        self._times = deque(maxlen=fps_window)
        self._last_sig = None
        self._repeat_since = None
        self.state = H.DISCONNECTED
        self.last_error = None
        self.dropped_frame_count = 0
        self.stale_frame_count = 0
        self.reconnect_count = 0
        self.frames_received = 0
        self.connected_at = None
        self.first_frame_latency_ms = None
        self._open_started = None

    # ------------------------------------------------------------- lifecycle
    def start(self):
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name=f"camera-reader-{id(self):x}", daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout=2.0):
        """Ask the thread to stop. The thread itself closes the source (never
        concurrently with a read in progress)."""
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)
        if t is None:
            self._close_source()
        self.state = H.DISCONNECTED

    def request_reconnect(self):
        """Close and reopen the source from the reader thread (never concurrently with a read)."""
        self._reconnect.set()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _close_source(self):
        try:
            self.source.close()
        except Exception as exc:
            self.last_error = safe_error(exc)

    def _wait(self, seconds):
        self._stop.wait(seconds)

    def _run(self):
        backoff = self.initial_backoff
        first = True
        try:
            while not self._stop.is_set():
                self.state = H.CONNECTING if first else H.RECONNECTING
                self._open_started = self.clock()
                try:
                    ok = self.source.open()
                except Exception as exc:
                    ok = False
                    self.source.last_error = safe_error(exc)
                if not ok:
                    self.last_error = redact(getattr(self.source, "last_error", None))
                    failed = H.AUTH_FAILED if getattr(self.source, "state", None) == H.AUTH_FAILED else H.OFFLINE
                    if not self.auto_reconnect or self._stop.is_set():
                        self.state = failed
                        break
                    self.state = failed if first else H.RECONNECTING
                    self._wait(backoff)
                    backoff = min(backoff * 2, self.max_backoff)
                    continue
                if not first:
                    self.reconnect_count += 1
                first = False
                backoff = self.initial_backoff
                self.connected_at = self.clock()
                self.state = H.CONNECTED
                self._read_loop()
                self._close_source()
                if self._stop.is_set() or not self.auto_reconnect:
                    break
                self._wait(backoff)
                backoff = min(backoff * 2, self.max_backoff)
        finally:
            self._close_source()
            if self._stop.is_set():
                self.state = H.DISCONNECTED

    def _read_loop(self):
        failures = 0
        last_good = self.clock()
        while not self._stop.is_set():
            if self._reconnect.is_set():
                self._reconnect.clear()
                self.state = H.NO_SIGNAL
                self.last_error = "reconnect requested"
                return
            try:
                ok, image = self.source.read()
            except Exception as exc:
                ok, image = False, None
                self.last_error = safe_error(exc)
            now = self.clock()
            if not ok:
                failures += 1
                if failures >= self.max_read_failures or now - last_good > self.stale_after:
                    self.state = H.NO_SIGNAL
                    self.last_error = redact(getattr(self.source, "last_error", None)) or "no frame"
                    return
                self._wait(0.02)
                continue
            failures = 0
            if self.detect_repeats:
                sig = frame_signature(image)
                if sig == self._last_sig:
                    self.stale_frame_count += 1
                    if self._repeat_since is None:
                        self._repeat_since = now
                    if now - self._repeat_since > self.stale_after:
                        self.state = H.NO_SIGNAL
                        self.last_error = "frozen stream (identical frames)"
                        self._repeat_since = None
                        return
                    continue
                self._last_sig = sig
                self._repeat_since = None
            last_good = now
            self._publish(image, now)

    def _publish(self, image, now):
        with self._cond:
            if self._seq > self._consumed:
                self.dropped_frame_count += 1          # the previous frame was never used
            self._seq += 1
            self._frame = image
            self._ts = now
            self._cond.notify_all()
        self.frames_received += 1
        if self.first_frame_latency_ms is None and self._open_started is not None:
            self.first_frame_latency_ms = round((now - self._open_started) * 1000.0, 1)
        self._times.append(now)
        if self.state in (H.CONNECTED, H.DEGRADED, H.NO_SIGNAL, H.RECONNECTING, H.CONNECTING):
            self.state = H.CONNECTED

    # -------------------------------------------------------------- consumer
    @property
    def seq(self):
        return self._seq

    def latest(self):
        with self._cond:
            return None if self._frame is None else Frame(self._seq, self._ts, self._frame)

    def get_newer(self, seq):
        """The newest frame with sequence > ``seq``, or None (non-blocking)."""
        with self._cond:
            if self._frame is None or self._seq <= seq:
                return None
            self._consumed = self._seq
            return Frame(self._seq, self._ts, self._frame)

    def wait_newer(self, seq, timeout=1.0):
        """Blocking variant for plain-thread tools (not for eventlet request handlers)."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq > seq or self._stop.is_set(), timeout)
        return self.get_newer(seq)

    # ---------------------------------------------------------------- health
    def frame_age(self, now=None):
        ts = self._ts
        return None if ts is None else max(0.0, (now if now is not None else self.clock()) - ts)

    def measured_fps(self):
        t = list(self._times)
        if len(t) < 2 or t[-1] <= t[0]:
            return None
        return round((len(t) - 1) / (t[-1] - t[0]), 2)

    def live_state(self, now=None):
        """State with frame age taken into account (a blocked read shows as NO_SIGNAL)."""
        st = self.state
        if st == H.CONNECTED:
            age = self.frame_age(now)
            if age is None:
                return H.CONNECTING
            if age > self.stale_after:
                return H.NO_SIGNAL
            if age > self.degraded_age:
                return H.DEGRADED
        return st

    def has_signal(self, now=None):
        return self.live_state(now) in H.LIVE_STATES

    def health(self, now=None):
        now = self.clock() if now is None else now
        src = self.source
        w, h = getattr(src, "width", None), getattr(src, "height", None)
        age = self.frame_age(now)
        return {
            "camera_id": getattr(src, "camera_id", None),
            "camera_name": getattr(src, "name", None),
            "source_type": getattr(src, "source_type", None),
            "state": self.live_state(now),
            "resolution": f"{w}x{h}" if w and h else None,
            "reported_fps": getattr(src, "reported_fps", None),
            "measured_fps": self.measured_fps(),
            "frame_age_ms": None if age is None else round(age * 1000.0, 1),
            "frame_timestamp": self._ts,
            "last_frame_received_at": self._ts,
            "stale_frames": self.stale_frame_count,
            "dropped_frames": self.dropped_frame_count,
            "reconnect_count": self.reconnect_count,
            "first_frame_latency_ms": self.first_frame_latency_ms,
            "last_error_sanitized": redact(self.last_error),
        }
