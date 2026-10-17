"""Event Evidence Mode: short clips around AI events, OFF by default.

* OFF (default): nothing but event metadata is stored; ``push_frame`` is a
  no-op and no frame is ever buffered.
* ON: the camera pipeline pushes already-encoded JPEG frames into a RAM ring
  buffer (sampled at ``evidence_fps``, bounded in seconds and frames). When a
  behaviour episode of a student with ACTIVE ``event_evidence`` consent
  starts, the frames from ``pre`` seconds before the onset to ``post`` seconds
  after confirmation are written as ONE short clip. Video is never recorded
  continuously.

Storage rules (enforced here, tested in tests/test_evidence.py):
* random UUID file names, server-side folder outside ``static/`` (no public URL);
* names are validated and resolved inside the folder (no path traversal);
* every clip has ``expires_at``; ``cleanup_expired`` deletes files + rows;
* viewing and deleting go through EvidenceService, which writes the audit log.
Authorisation (teacher of the class only) is decided in focusguard.authz.
"""
import base64
import json
import os
import re
import struct
import threading
import time
import uuid
from collections import deque
from typing import Callable, Dict, List, Optional

from . import audit as A
from .consent import SCOPE_EVIDENCE
from .settings import PrivacySettings

MAGIC = b"FGEV1\n"
_ID_RX = re.compile(r"^[0-9a-f]{32}$")
_FILE_RX = re.compile(r"^[0-9a-f]{32}\.fgev$")


class EvidenceError(ValueError):
    pass


class EvidenceUnavailable(EvidenceError):
    """The clip row exists but its file is missing or unreadable (it is removed)."""


def valid_evidence_id(value) -> bool:
    return isinstance(value, str) and bool(_ID_RX.match(value))


class EvidenceStore:
    """Files on the server only. Names are generated here, never by a client."""

    def __init__(self, base_dir: str):
        self.base_dir = os.path.realpath(base_dir)

    def _path(self, file_name: str) -> str:
        if not isinstance(file_name, str) or not _FILE_RX.match(file_name):
            raise EvidenceError("invalid evidence file name")
        path = os.path.realpath(os.path.join(self.base_dir, file_name))
        if os.path.commonpath([path, self.base_dir]) != self.base_dir:
            raise EvidenceError("path outside evidence store")
        return path

    def write(self, evidence_id: str, header: dict, frames: List[tuple]) -> tuple:
        if not valid_evidence_id(evidence_id):
            raise EvidenceError("invalid evidence id")
        os.makedirs(self.base_dir, mode=0o700, exist_ok=True)
        file_name = f"{evidence_id}.fgev"
        path = self._path(file_name)
        head = json.dumps(header, separators=(",", ":")).encode("utf-8")
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(MAGIC)
            f.write(struct.pack(">I", len(head)))
            f.write(head)
            for ts, jpeg in frames:
                f.write(struct.pack(">dI", float(ts), len(jpeg)))
                f.write(jpeg)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return file_name, os.path.getsize(path)

    def read(self, file_name: str) -> dict:
        """Strict reader: a missing, foreign, truncated or corrupted file raises
        EvidenceError instead of returning partial frames. Other OS errors (too many
        open files, permission, I/O) propagate unchanged: they are not proof the clip
        is gone, so the caller must not delete it."""
        if not file_name:
            raise EvidenceError("clip file missing")
        path = self._path(file_name)
        try:
            with open(path, "rb") as f:
                if f.read(len(MAGIC)) != MAGIC:
                    raise EvidenceError("not an evidence clip")
                (n,) = struct.unpack(">I", f.read(4))
                if not 0 < n <= 64 * 1024:
                    raise EvidenceError("bad clip header")
                header = json.loads(f.read(n).decode("utf-8"))
                frames = []
                while True:
                    chunk = f.read(12)
                    if not chunk:
                        break
                    if len(chunk) < 12:
                        raise EvidenceError("truncated clip")
                    ts, size = struct.unpack(">dI", chunk)
                    data = f.read(size)
                    if len(data) != size:
                        raise EvidenceError("truncated clip")
                    frames.append((ts, data))
        except FileNotFoundError:
            raise EvidenceError("clip file missing")
        except (struct.error, UnicodeDecodeError, ValueError) as exc:
            if isinstance(exc, EvidenceError):
                raise
            raise EvidenceError("unreadable clip")
        if not frames:
            raise EvidenceError("empty clip")
        return {"header": header, "frames": frames}

    def delete(self, file_name: Optional[str]) -> bool:
        if not file_name:
            return False
        path = self._path(file_name)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def list_files(self) -> List[str]:
        if not os.path.isdir(self.base_dir):
            return []
        return [n for n in os.listdir(self.base_dir) if _FILE_RX.match(n)]


class FrameRingBuffer:
    """Bounded RAM buffer of (timestamp, jpeg, boxes). Nothing touches disk."""

    def __init__(self, seconds: float, fps: float, max_frames: int):
        self.seconds = seconds
        self.min_interval = 1.0 / fps if fps > 0 else 0.0
        self.frames = deque(maxlen=max(1, int(max_frames)))

    def push(self, ts: float, jpeg: bytes, boxes: Optional[dict] = None) -> bool:
        if self.frames and ts - self.frames[-1][0] < self.min_interval:
            return False
        self.frames.append((ts, jpeg, dict(boxes or {})))
        while self.frames and ts - self.frames[0][0] > self.seconds:
            self.frames.popleft()
        return True

    def window(self, start: float, end: float) -> list:
        return [f for f in self.frames if start <= f[0] <= end]


class EvidenceService:
    """Clip lifecycle (record, view, delete, expire). Also a SessionRuntime hook."""

    def __init__(self, repo, settings: PrivacySettings, audit: A.AuditLog, consent=None,
                 clock: Callable[[], float] = time.time, store: Optional[EvidenceStore] = None):
        self.repo = repo
        self.settings = settings
        self.audit = audit
        self.consent = consent
        self.clock = clock
        self.store = store or EvidenceStore(settings.evidence_dir)
        self._lock = threading.RLock()
        self._buffers: Dict[int, FrameRingBuffer] = {}       # class_session_id -> buffer
        self._pending: Dict[int, List[dict]] = {}             # class_session_id -> pending captures
        self.cropper: Optional[Callable[[bytes, tuple], Optional[bytes]]] = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.evidence_enabled)

    # ------------------------------------------------------- frame intake
    def buffer_seconds(self, longest_confirmation: float = 10.0) -> float:
        return self.settings.evidence_pre_seconds + longest_confirmation + self.settings.evidence_post_seconds + 2.0

    def push_frame(self, class_session_id, ts: float, jpeg: bytes, boxes: Optional[dict] = None):
        """Called by the camera pipeline for every analysed frame. OFF -> no-op."""
        if not self.enabled or class_session_id is None or not jpeg:
            return
        with self._lock:
            buf = self._buffers.get(class_session_id)
            if buf is None:
                buf = self._buffers[class_session_id] = FrameRingBuffer(
                    self.buffer_seconds(), self.settings.evidence_fps, self.settings.evidence_max_frames)
            buf.push(ts, jpeg, boxes)
            self._flush(class_session_id, ts)

    # ----------------------------------------------------- runtime hooks
    def on_episode_started(self, rt, st, ep):
        if not self.enabled or rt.class_session_id is None:
            return
        if self.consent is None or not self.consent.has_scope(st.student_id, SCOPE_EVIDENCE):
            return      # no consent for clips -> metadata only
        detected = (ep.metadata or {}).get("detected_at") or ep.start_time
        with self._lock:
            self._pending.setdefault(rt.class_session_id, []).append({
                "episode_uid": ep.metadata.get("episode_uid"), "student_id": st.student_id, "type": ep.type,
                "class_id": rt.class_id, "class_session_id": rt.class_session_id,
                "from": ep.start_time - self.settings.evidence_pre_seconds,
                "to": detected + self.settings.evidence_post_seconds})

    def on_end(self, rt, now):
        if rt.class_session_id is None:
            return
        with self._lock:
            self._flush(rt.class_session_id, float("inf"))
            self._buffers.pop(rt.class_session_id, None)
            self._pending.pop(rt.class_session_id, None)

    def _flush(self, cs_id, ts):
        pending = self._pending.get(cs_id) or []
        due = [p for p in pending if p["to"] <= ts]
        if not due:
            return
        self._pending[cs_id] = [p for p in pending if p["to"] > ts]
        buf = self._buffers.get(cs_id)
        for p in due:
            frames = buf.window(p["from"], p["to"]) if buf else []
            if frames:
                self._save_clip(p, frames)

    def _save_clip(self, p, frames):
        """Write a clip of ONE student. Consent is checked again here (it may have
        been revoked while the capture was pending) and only frames cropped to
        that student's box are kept: a full classroom frame, which would show
        other students, is never stored. No usable frame -> no clip."""
        if self.consent is None or not self.consent.has_scope(p["student_id"], SCOPE_EVIDENCE):
            return None
        out = []
        for ts, jpeg, boxes in frames:
            box = (boxes or {}).get(p["student_id"])
            if self.cropper is None or box is None:
                continue
            try:
                c = self.cropper(jpeg, box)
            except Exception:
                c = None
            if c:
                out.append((ts, c))
        if not out:
            return None
        cropped = True
        evidence_id = uuid.uuid4().hex
        now = self.clock()
        header = {"evidence_id": evidence_id, "event_type": p["type"], "student_id": p["student_id"],
                  "fps": self.settings.evidence_fps, "clip_start": out[0][0], "clip_end": out[-1][0]}
        file_name, size = self.store.write(evidence_id, header, out)
        self.repo.create_evidence({
            "evidence_id": evidence_id, "episode_uid": p["episode_uid"], "event_type": p["type"],
            "student_id": p["student_id"], "class_session_id": p["class_session_id"], "class_id": p["class_id"],
            "clip_start": out[0][0], "clip_end": out[-1][0], "frame_count": len(out),
            "fps": self.settings.evidence_fps, "file_name": file_name, "size_bytes": size, "cropped": cropped,
            "created_at": now, "expires_at": now + self.settings.evidence_retention_hours * 3600})
        self.audit.record(A.EVIDENCE_CREATED, None, p["student_id"],
                          {"evidence_id": evidence_id, "event_type": p["type"], "frame_count": len(out)})
        return evidence_id

    def cancel_pending(self, student_id) -> int:
        """Drop captures still waiting for their post-event window (consent revoked)."""
        n = 0
        with self._lock:
            for cs_id, pending in self._pending.items():
                keep = [p for p in pending if p["student_id"] != int(student_id)]
                n += len(pending) - len(keep)
                self._pending[cs_id] = keep
        return n

    # ------------------------------------------------------------ queries
    def describe(self, row: dict, now=None) -> dict:
        now = self.clock() if now is None else now
        return {"evidence_id": row["evidence_id"], "event_type": row.get("event_type"),
                "student_id": row.get("student_id"), "class_id": row.get("class_id"),
                "class_session_id": row.get("class_session_id"), "episode_uid": row.get("episode_uid"),
                "clip_start": row.get("clip_start"), "clip_end": row.get("clip_end"),
                "duration_seconds": (None if row.get("clip_start") is None else
                                     round(float(row["clip_end"]) - float(row["clip_start"]), 1)),
                "frame_count": row.get("frame_count"), "cropped": bool(row.get("cropped")),
                "created_at": row.get("created_at"), "expires_at": row.get("expires_at"),
                "expires_in_seconds": None if row.get("expires_at") is None else max(0, int(row["expires_at"] - now))}

    def for_episode(self, episode_uid) -> Optional[dict]:
        if not episode_uid:
            return None
        now = self.clock()
        rows = [r for r in self.repo.list_evidence(episode_uid=episode_uid)
                if r.get("expires_at") is None or r["expires_at"] > now]
        return rows[-1] if rows else None

    def get(self, evidence_id, include_expired=False) -> Optional[dict]:
        """Live clip row. Expired clips are never served; ``include_expired`` lets
        an authorised user delete one before the retention sweep does."""
        if not valid_evidence_id(evidence_id):
            return None
        row = self.repo.get_evidence(evidence_id)
        if not row or row.get("deleted_at"):
            return None
        if not include_expired and row.get("expires_at") is not None and row["expires_at"] <= self.clock():
            return None
        return row

    def frames_payload(self, row: dict, actor) -> dict:
        """Clip content for an authorised viewer (caller checked authz). Audited.
        A missing/corrupted file removes the row (audited) and raises EvidenceUnavailable."""
        try:
            data = self.store.read(row.get("file_name"))
        except EvidenceError as exc:
            with self._lock:      # re-read: the retention sweep may have removed it meanwhile
                current = self.repo.get_evidence(row["evidence_id"])
                if current and not current.get("deleted_at"):
                    self.delete(current, actor, reason="file_unreadable")
            raise EvidenceUnavailable(str(exc))
        self.audit.record(A.EVIDENCE_VIEWED, actor, row.get("student_id"),
                          {"evidence_id": row["evidence_id"], "event_type": row.get("event_type")})
        return {"evidence_id": row["evidence_id"], "fps": row.get("fps") or self.settings.evidence_fps,
                "frames": [{"t": ts, "src": "data:image/jpeg;base64," + base64.b64encode(j).decode("ascii")}
                           for ts, j in data["frames"]],
                **self.describe(row)}

    # ------------------------------------------------------------ deletes
    def delete(self, row: dict, actor, reason="manual") -> bool:
        if row.get("deleted_at"):
            return False
        try:
            self.store.delete(row.get("file_name"))
        except EvidenceError:
            pass
        self.repo.mark_evidence_deleted(row["evidence_id"], self.clock(), getattr(actor, "user_id", None), reason)
        self.audit.record(A.EVIDENCE_DELETED, actor, row.get("student_id"),
                          {"evidence_id": row["evidence_id"], "reason": reason})
        return True

    def delete_for_student(self, student_id, actor, reason="manual") -> int:
        return sum(1 for r in self.repo.list_evidence(student_id=student_id) if self.delete(r, actor, reason))

    def cleanup_expired(self, now=None) -> int:
        now = self.clock() if now is None else now
        n = 0
        with self._lock:      # a clip being written (file, then row) is never taken for an orphan
            for r in self.repo.list_evidence():
                if r.get("expires_at") is not None and r["expires_at"] <= now:
                    if self.delete(r, None, reason="retention_expired"):
                        n += 1
            # Files without a live row (crash between write and insert) are removed too.
            live = {r["file_name"] for r in self.repo.list_evidence() if r.get("file_name")}
            for name in self.store.list_files():
                if name not in live:
                    self.store.delete(name)
        return n

    def count_for_student(self, student_id) -> int:
        now = self.clock()
        return sum(1 for r in self.repo.list_evidence(student_id=student_id)
                   if r.get("expires_at") is None or r["expires_at"] > now)
