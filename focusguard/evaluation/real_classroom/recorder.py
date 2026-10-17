"""Raw observation log for an evaluation session (numbers only, no images).

Written to ``<EVALUATION_PRIVATE_DIR>/<evaluation_session_id>/observations.jsonl``
(git-ignored, deleted by the evaluation retention policy). Calibration replays
these observations with candidate thresholds.
"""
import json
import os
import threading

FIELDS = ("visible", "face_visible", "ear", "yaw", "pitch", "phone_confidence", "identity_confidence", "track_id")


def _round(v):
    return round(v, 4) if isinstance(v, float) else v


class ObservationRecorder:
    """SessionRuntime hook. Bounded: stops writing after ``max_rows``."""

    def __init__(self, path: str, max_rows: int = 2_000_000):
        self.path = path
        self.max_rows = max_rows
        self.rows = 0
        self.frames = 0               # processed frames (actual FPS = frames over wall time)
        self.first_frame_at = None
        self.last_frame_at = None
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")

    def _write(self, obj):
        with self._lock:
            if self._fh is None or self.rows >= self.max_rows:
                return
            self._fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
            self.rows += 1

    def _count_frame(self, t):
        self.frames += 1
        if self.first_frame_at is None:
            self.first_frame_at = t
        self.last_frame_at = t

    def actual_fps(self):
        """Measured processing rate, or None below 2 frames / zero span."""
        if self.frames < 2 or not self.last_frame_at or self.last_frame_at <= self.first_frame_at:
            return None
        return round((self.frames - 1) / (self.last_frame_at - self.first_frame_at), 2)

    def on_frame(self, rt, now, observations):
        self._count_frame(now)
        self._write({"t": now, "frame": True})
        for sid, obs in observations.items():
            if sid in rt.states:
                self._write(dict({"t": obs.timestamp, "student_id": int(sid)},
                                 **{k: _round(getattr(obs, k)) for k in FIELDS}))

    def on_observation(self, rt, sid, obs):
        self._count_frame(obs.timestamp)
        self._write(dict({"t": obs.timestamp, "student_id": int(sid), "frame": True},
                         **{k: _round(getattr(obs, k)) for k in FIELDS}))

    def on_end(self, rt, now):
        self.close()

    def close(self):
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def read_observations(path: str, limit: int = 2_000_000):
    """Returns (observations, frame_timestamps)."""
    obs, frames = [], []
    if not os.path.exists(path):
        return obs, frames
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("frame"):
                frames.append(row["t"])
            if "student_id" in row:
                obs.append(row)
    return obs, frames


def write_jsonl(path: str, rows) -> int:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
            n += 1
    return n


def read_jsonl(path: str, limit: int = 5_000_000) -> list:
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
