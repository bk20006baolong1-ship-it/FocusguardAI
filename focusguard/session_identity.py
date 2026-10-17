"""Session-owned identity bindings (who is who for the lifetime of ONE class session).

A camera pipeline (ClassroomAI / the simulator) and its tracker are disposable: the
stream reconnects, the teacher reloads the page, the pipeline is rebuilt and the
tracker hands out new track ids. The identity the TEACHER established must not be
lost with it. This store belongs to the class session (it hangs off the
SessionRuntime and is closed when the class ends), not to the camera:

* every binding (track ↔ student) is a ``BindingRec`` persisted in
  ``identity_bindings``; history is append-only (a binding is ended, never rewritten);
* a teacher-anchored binding (teacher tag / correction, and what was restored from
  one) whose track disappears — track lost, camera reconnect, page reload, pipeline
  rebuilt — is DETACHED, not ended: the row stays open for the session;
* a detached binding is re-attached to a new track only on SAFE continuity evidence
  (``ClassroomIdentity._restore``: the new person appears where the student was, is
  the only candidate there, has no face match to another student, stays there for
  a while); otherwise the student stays NOT_SEEN / unresolved and the teacher re-tags.
  Old track ids are never reused for an arbitrary new person. No face image or
  embedding is involved (no biometric consent needed for this session metadata);
* ``derived_from`` links a binding to the binding it continues *as the same physical
  person* (same track re-confirmed, tracker re-id at the same place, seat-continuity
  restore), kept for the audit trail. When a teacher says a binding was wrong, only
  what is PROVEN to depend on it is revoked (``revocation_lineage``): the binding and
  its predecessors on the same track. A link to another track is an inference; the
  earlier binding had its own confirmation and is never revoked by that correction.
"""
import threading
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

TEACHER_SOURCES = ("TEACHER_TAG", "TEACHER_CORRECTED")
METHOD_SEAT_CONTINUITY = "SEAT_CONTINUITY"          # detached teacher binding re-attached by position
METHOD_TRACK_CONTINUITY = "TRACK_CONTINUITY"        # tracker re-id at the same place (face-recovered)
METHOD_SAME_TRACK = "SAME_TRACK"                    # same track, same student (e.g. teacher confirms the AI)


@dataclass
class BindingRec:
    binding_id: str
    student_id: int
    track_id: Optional[int]
    gen: int
    source: str
    teacher_anchored: bool
    started_at: float
    derived_from: Optional[str] = None
    recovery_method: Optional[str] = None
    confirmed_by: Optional[int] = None
    ended_at: Optional[float] = None
    end_reason: Optional[str] = None
    detached_at: Optional[float] = None
    last_box: Optional[tuple] = None             # normalised (x, y, w, h) of the person box
    last_seen_at: Optional[float] = None
    persisted: bool = True

    @property
    def open(self) -> bool:
        return self.ended_at is None

    @property
    def attached(self) -> bool:
        return self.open and self.detached_at is None and self.track_id is not None


class SessionIdentityStore:
    """Bindings of one class session. Thread-safe; persistence failures never raise."""

    def __init__(self, repo, class_id, class_session_id):
        self.repo = repo
        self.class_id = int(class_id)
        self.class_session_id = class_session_id
        self.lock = threading.RLock()
        self.gen = 0
        self.recs: Dict[str, BindingRec] = {}
        self.closed = False
        self.pipeline_started_at = None
        self.pipeline_closed_at = None                    # last time a camera pipeline went away
        self.last_frame_at = None                         # last frame any pipeline of this session processed

    # ------------------------------------------------------------ lifecycle
    def new_pipeline(self, now) -> int:
        """A new camera pipeline (new tracker, new track ids): every binding still attached to a
        track of an older pipeline is detached (teacher-anchored) or ended (automatic: face
        recognition finds those students again)."""
        with self.lock:
            prev_start = self.pipeline_started_at
            self.gen += 1
            self.pipeline_started_at = now
            if prev_start is not None and (self.pipeline_closed_at is None or self.pipeline_closed_at <= prev_start):
                # the previous pipeline died without closing: it ended with its last processed frame
                self.pipeline_closed_at = self.last_frame_at if self.last_frame_at is not None else now
            for rec in list(self.recs.values()):
                if rec.attached and rec.gen < self.gen:
                    if rec.teacher_anchored:
                        self.detach(rec.binding_id, rec.last_seen_at if rec.last_seen_at is not None else now)
                    else:
                        self.end(rec.binding_id, now, "pipeline_closed")
            return self.gen

    def on_end(self, runtime, now):
        """SessionRuntime hook: the class ended -> every open binding ends."""
        self.close(now)

    def close(self, now):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for rec in list(self.recs.values()):
                if rec.open:
                    self.end(rec.binding_id, now, "session_closed")

    # ------------------------------------------------------------- bindings
    def start(self, track_id, student_id, source, now, confidence=None, by=None, reason=None, replaced=None,
              derived_from=None, recovery_method=None, teacher_anchored=None) -> BindingRec:
        with self.lock:
            anchored = (source in TEACHER_SOURCES) if teacher_anchored is None else bool(teacher_anchored)
            if self.closed:                               # the class ended: nothing new is bound
                rec = BindingRec("closed-" + uuid.uuid4().hex, int(student_id), track_id, self.gen, source,
                                 anchored, now, ended_at=now, end_reason="session_closed", persisted=False)
                self.recs[rec.binding_id] = rec
                return rec
            row = {"class_session_id": self.class_session_id, "class_id": self.class_id,
                   "track_id": None if track_id is None else int(track_id), "student_id": int(student_id),
                   "identity_source": source, "confidence": None if confidence is None else round(float(confidence), 4),
                   "confirmed_by": by, "confirmed_at": now, "correction_reason": reason,
                   "replaced_binding_id": replaced, "derived_from_binding_id": derived_from,
                   "recovery_method": recovery_method, "teacher_anchored": anchored}
            persisted = True
            try:
                bid = self.repo.create_identity_binding(row)
            except Exception as exc:                      # persistence must never stop the camera loop
                print("[IDENTITY] binding not persisted:", type(exc).__name__)
                bid, persisted = "local-" + uuid.uuid4().hex, False
            rec = BindingRec(str(bid), int(student_id), None if track_id is None else int(track_id), self.gen,
                             source, anchored, now, derived_from=derived_from, recovery_method=recovery_method,
                             confirmed_by=by, last_seen_at=now, persisted=persisted)
            self.recs[rec.binding_id] = rec
            return rec

    def end(self, binding_id, now, reason) -> Optional[BindingRec]:
        with self.lock:
            rec = self.recs.get(str(binding_id)) if binding_id else None
            if rec is None or not rec.open:
                return rec
            rec.ended_at, rec.end_reason = now, reason
            if rec.persisted:
                try:
                    self.repo.end_identity_binding(rec.binding_id, now, reason)
                except Exception as exc:
                    print("[IDENTITY] binding end not persisted:", type(exc).__name__)
            return rec

    def detach(self, binding_id, at) -> Optional[BindingRec]:
        """The binding's person track is gone (camera / tracker), the session identity is not."""
        with self.lock:
            rec = self.recs.get(str(binding_id)) if binding_id else None
            if rec is None or not rec.open or rec.detached_at is not None:
                return rec
            rec.detached_at = at
            if rec.persisted:
                try:
                    self.repo.update_identity_binding(rec.binding_id, {"detached_at": at,
                                                                        "last_seen_at": rec.last_seen_at})
                except Exception as exc:
                    print("[IDENTITY] binding detach not persisted:", type(exc).__name__)
            return rec

    def touch(self, binding_id, box, now):
        with self.lock:
            rec = self.recs.get(str(binding_id)) if binding_id else None
            if rec is not None and rec.attached:
                if box is not None:
                    rec.last_box = tuple(box)
                rec.last_seen_at = now

    # ------------------------------------------------------------- queries
    def get(self, binding_id) -> Optional[BindingRec]:
        return self.recs.get(str(binding_id)) if binding_id else None

    def open_for(self, student_id) -> Optional[BindingRec]:
        """The student's current session binding (attached or detached), if any."""
        with self.lock:
            open_recs = [r for r in self.recs.values() if r.open and r.student_id == int(student_id)]
            return max(open_recs, key=lambda r: r.started_at) if open_recs else None

    def latest_for(self, student_id) -> Optional[BindingRec]:
        with self.lock:
            recs = [r for r in self.recs.values() if r.student_id == int(student_id)]
            return max(recs, key=lambda r: r.started_at) if recs else None

    def detached(self) -> List[BindingRec]:
        with self.lock:
            return [r for r in self.recs.values() if r.open and r.detached_at is not None]

    def lineage(self, binding_id) -> List[str]:
        """The binding and every ancestor it continues as the same physical person."""
        out, seen = [], set()
        rec = self.get(binding_id)
        while rec is not None and rec.binding_id not in seen:
            seen.add(rec.binding_id)
            out.append(rec.binding_id)
            rec = self.get(rec.derived_from)
        return out

    def revocation_lineage(self, binding_id) -> List[str]:
        """What a teacher correction of ``binding_id`` invalidates: ONLY what is proven to depend on
        it — the binding and its predecessors on the SAME track (same tracker id = the same physical
        person, so an earlier AI match or tag of that very track is contradicted too). Links across
        tracks (tracker re-id at the same place, seat restore) are inferences: the earlier binding
        had its own confirmation (face match or teacher tag), so it is never revoked by a correction
        of a later track."""
        out, seen = [], set()
        rec = self.get(binding_id)
        while rec is not None and rec.binding_id not in seen:
            seen.add(rec.binding_id)
            out.append(rec.binding_id)
            if rec.recovery_method != METHOD_SAME_TRACK:
                break
            rec = self.get(rec.derived_from)
        return out


def iou(a, b) -> float:
    """Intersection over union of two (x, y, w, h) boxes (any consistent unit)."""
    if a is None or b is None:
        return 0.0
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0
