"""ClassroomIdentity: one classroom session's identity layer (CV-free, testable without a camera).

Sits between the detectors (YOLO + ByteTrack person tracks, InsightFace faces —
classroom_ai.ClassroomAI, or the dev simulator) and the SessionRuntime:

* IdentityManager decides which PERSON TRACK is which student (face recognition
  with temporal confirmation, multi-template gallery);
* the teacher tags a person track (session identity, immediate, provenance
  TEACHER_TAG / TEACHER_CORRECTED, persisted in identity_bindings with
  confirmed_by / confirmed_at; nothing the client sends decides those fields);
* enrollment burst: after a tag, ONLY with ACTIVE face-recognition consent, the
  tagged track is followed for ``burst_seconds``; good, diverse face samples
  become face_profiles templates (embeddings only; the frame is never stored);
  without consent the tag is session-only and no embedding is kept;
* progressive enrollment (later sessions): guarded samples of confirmed,
  conflict-free identities, kept PENDING in memory and committed at the end —
  a teacher correction rejects them first;
* "unidentified present": a visible person whose identity is not resolved is
  counted, never turned into a specific student; the students who could be that
  person (recently lost, or under review) are reported as ``identity_unresolved``
  so the runtime neither starts an AWAY timer nor finalises them ABSENT;
* session-owned bindings (focusguard/session_identity.py): the bindings live in the
  class session's SessionIdentityStore, not in this (disposable) pipeline. A teacher
  binding whose track disappears (tracker re-id, camera reconnect, page reload,
  rebuilt pipeline) is detached and continued on a new track only on safe seat
  continuity (``_restore``) — never by reusing an old track id;
* every confirmed track carries its binding into the runtime plan, so attendance
  evidence knows which binding it rests on (provenance-safe corrections).
"""
import copy
import threading
import time
from typing import Dict, List, Optional

from . import runtime_state as rs
from .config import DEFAULT_CONFIG
from .face_profiles import (SRC_BURST, SRC_PROGRESSIVE, PENDING, REJECTED, Sample, consistent, evaluate,
                            face_metrics, max_similarity, normalize, pose_bucket)
from .identity import (AMBIGUOUS, CONFIRMED, CONFLICT, NO_MATCH, SRC_AUTO_FACE, SRC_AUTO_RECOVERED,
                       SRC_TEACHER_CORRECTED, SRC_TEACHER_TAG, TEACHER_SOURCES, UNCERTAIN, UNKNOWN, IdentityManager)
from .privacy import audit as A
from .session_identity import (METHOD_SAME_TRACK, METHOD_SEAT_CONTINUITY, METHOD_TRACK_CONTINUITY, BindingRec,
                               SessionIdentityStore, iou)

# A previous binding of the student ended for one of these reasons may be continued (same person)
# by a face recovery at the same place; a teacher removal / correction never is.
_CONTINUABLE_END = ("track_lost", "owner_not_visible", "pipeline_closed", "superseded")

ENROLL_BURST_STARTED = "BURST_STARTED"
ENROLL_SESSION_ONLY = "SESSION_ONLY"        # no face-recognition consent: no template is created
FAR_CAMERA_WARNING_VI = ("Camera đang ở quá xa để tạo mẫu nhận diện ổn định cho một số học sinh "
                         "(khuôn mặt quá nhỏ trong khung hình).")


class TagError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


class FaceObs:
    """One face associated with a person track in this frame (in memory only)."""
    __slots__ = ("embedding", "box", "det_score", "gray", "yaw", "pitch", "association_ok")

    def __init__(self, embedding=None, box=None, det_score=None, gray=None, yaw=None, pitch=None,
                 association_ok=True):
        self.embedding, self.box, self.det_score, self.gray = embedding, box, det_score, gray
        self.yaw, self.pitch, self.association_ok = yaw, pitch, association_ok


class _Burst:
    def __init__(self, student_id, binding_id, deadline, actor_id, had_profile):
        self.student_id, self.binding_id, self.deadline = student_id, binding_id, deadline
        self.actor_id, self.had_profile = actor_id, had_profile
        self.samples: List[Sample] = []
        self.rejected = 0


class ClassroomIdentity:
    def __init__(self, sm, class_id, class_session_id, config=None, clock=None):
        self.sm = sm
        self.repo = sm.repo
        self.class_id = int(class_id)
        self.class_session_id = class_session_id
        cfg = config or getattr(sm, "config", None) or DEFAULT_CONFIG
        self.cfg = cfg
        self.ecfg = cfg.enrollment
        self.clock = clock or getattr(sm, "clock", None) or time.time     # the session's clock
        self.identity = IdentityManager(config=cfg.identity)
        self.lock = threading.RLock()
        self.roster = {s["student_id"]: s["display_name"] for s in self.repo.get_students_in_class(self.class_id)}
        self.consented = set()
        rt = self.runtime()
        store = getattr(rt, "identity_store", None) if rt is not None else None
        # The class session owns the bindings; a pipeline without a session (tests) gets its own store.
        self.store: SessionIdentityStore = store if store is not None else \
            SessionIdentityStore(self.repo, self.class_id, class_session_id)
        self._binding: Dict[int, BindingRec] = {}      # track_id -> binding of THIS pipeline
        self._restore_votes: Dict[str, list] = {}      # detached binding_id -> [track_id, frames, since]
        self._boxes: Dict[int, tuple] = {}             # track_id -> normalised (x, y, w, h), this frame
        self._bursts: Dict[int, _Burst] = {}
        self._pending: List[Sample] = []
        self._progressive_n: Dict[int, int] = {}
        self._last_progressive: Dict[int, float] = {}
        self._lost_at: Dict[int, float] = {}
        self._lost_track: Dict[int, int] = {}          # student -> track the identity was lost on
        self._audited_auto = set()
        self._event_index = 0
        self._persons: Dict[int, tuple] = {}
        self._frame_hw = None
        self._face_px: Dict[int, float] = {}
        self._small_ratio = 0.0
        self.views: List[dict] = []
        self.closed = False
        self.reload_gallery()
        now = self.clock()
        self.gen = self.store.new_pipeline(now)        # old track ids are never reused for new people
        self._resumed_at = now                         # restore window: from when frames are available
        self._last_frame_at = None
        # the outage the current frames resumed from (the previous pipeline's end, at first)
        self._outage_start = self.store.pipeline_closed_at
        self._carry_until: Dict[int, float] = {}
        # The AWAY shield carries over, bounded: only students who were still being tracked when the
        # previous pipeline went away (not someone who walked out long before), and only for
        # restore_max_gap_seconds (then they are simply not seen).
        closed_at = self.store.pipeline_closed_at if self.store.pipeline_closed_at is not None else now
        margin = self.cfg.identity.track_lost_seconds + 2.0
        with self.store.lock:
            for sid in self.roster:
                rec = self.store.latest_for(sid)
                if rec is None:
                    continue
                if rec.open and rec.detached_at is not None:
                    at = rec.detached_at
                elif rec.end_reason == "pipeline_closed":
                    at = rec.last_seen_at if rec.last_seen_at is not None else rec.ended_at
                else:
                    continue
                if at is not None and at >= closed_at - margin:
                    self._lost(sid, None, at)
                    self._carry_until[sid] = now + self.cfg.identity.restore_max_gap_seconds

    # ------------------------------------------------------------- gallery
    def runtime(self):
        return self.sm.classroom_runtime(self.class_id, self.class_session_id)

    def reload_gallery(self):
        """Only students with ACTIVE face-recognition consent; pending samples of anyone else are dropped."""
        rows = self.sm.recognition_gallery(self.class_id)
        with self.lock:
            self.identity.set_gallery({r["student_id"]: r.get("embeddings") or [r["embedding"]] for r in rows})
            self.consented = {sid for sid in self.roster if self.sm.faces.consent_active(sid)}
            for tid, b in list(self._bursts.items()):
                if b.student_id not in self.consented:
                    self._cancel_burst(tid, "consent_not_active")
            dropped = [s for s in self._pending if s.student_id not in self.consented]
            for s in dropped:
                s.state = REJECTED
            self._pending = [s for s in self._pending if s.state == PENDING]
            for sid in {s.student_id for s in dropped}:
                self.sm.faces.reject(sid, sum(1 for s in dropped if s.student_id == sid), "consent_not_active")

    @property
    def gallery_size(self):
        return self.identity.gallery_size

    # ------------------------------------------------------------ per frame
    def identify(self, now, persons: Dict[int, tuple], embeddings: Dict[int, object], occluded=(), frame_hw=None):
        """Step 1: identities for this frame's person tracks. Returns {track_id: TrackIdentity copy}."""
        with self.lock:
            if self.closed:                            # a frame in flight after the class / pipeline closed
                return {}
            if self._last_frame_at is not None and now - self._last_frame_at > self.cfg.identity.outage_seconds:
                self._outage_start, self._resumed_at = self._last_frame_at, now   # frames are back after an outage
            self._last_frame_at = now
            self.store.last_frame_at = now
            self._persons = dict(persons)
            self._frame_hw = frame_hw or self._frame_hw
            self._boxes = {tid: self._norm(box) for tid, box in persons.items()}
            occ = set(occluded or ())
            decisions = self.identity.process_frame(
                [(tid, embeddings.get(tid), tid in occ) for tid in persons], now)
            self._handle_events(now)
            return decisions

    def after_frame(self, now, decisions, faces: Dict[int, FaceObs], frame_hw=None, occluded=()) -> dict:
        """Step 2 (after landmarks): enrollment sampling, views, and the runtime plan:
        ``{"confirmed": {track_id: student_id}, "unidentified": int, "unresolved_students": set}``."""
        with self.lock:
            if self.closed:     # callers must not feed the runtime from a closed pipeline (no false "not visible")
                return {"confirmed": {}, "unidentified": 0, "unresolved_students": set(), "bindings": {},
                        "closed": True}
            # A teacher tag / untag may have landed between identify() and now (landmarks run
            # outside the lock): act on the CURRENT identities, never on the stale copies.
            self._frame_hw = frame_hw or self._frame_hw
            self._restore(now)                         # may continue a detached teacher binding
            decisions = {tid: copy.copy(self.identity.tracks[tid]) if tid in self.identity.tracks else d
                         for tid, d in decisions.items()}
            occ = set(occluded or ())
            self._track_face_sizes(faces)
            for tid, face in faces.items():
                d = decisions.get(tid)
                if d is None or face is None or face.embedding is None:
                    continue
                if tid in self._bursts:
                    self._burst_sample(tid, d, face, now, tid in occ)
                else:
                    self._progressive_sample(tid, d, face, now, tid in occ)
            for tid in list(self._bursts):
                b = self._bursts[tid]
                if now >= b.deadline or len(b.samples) >= self.ecfg.burst_max_candidates or tid not in self._persons:
                    self._finish_burst(tid, now)
            confirmed, unresolved_tracks, review_students, bindings = {}, [], set(), {}
            for tid, d in decisions.items():
                rec = self._binding.get(tid)
                if rec is not None and d.status == CONFIRMED and d.student_id == rec.student_id:
                    self.store.touch(rec.binding_id, self._boxes.get(tid), now)
                if d.status == CONFIRMED and d.student_id is not None and not d.review_required:
                    confirmed[tid] = d.student_id
                    bindings[tid] = {"binding_id": rec.binding_id if rec is not None else None,
                                     "identity_source": d.source, "first_seen_at": d.first_seen}
                else:
                    unresolved_tracks.append(tid)
                    if d.status == CONFIRMED and d.student_id is not None:
                        review_students.add(d.student_id)
            # Which students could the unresolved people be? Under review first, then the most recently lost.
            seen = set(confirmed.values())
            room = max(0, len(unresolved_tracks) - len(review_students))
            # A lost student may be an unidentified person only if that person is on the very track
            # the identity was lost on, or appeared after the loss (someone present all along, e.g.
            # the teacher, never shields a student who walked out). No time limit otherwise;
            # bounded by the number of unidentified people.
            recent = [sid for sid, _t in sorted(self._lost_at.items(), key=lambda kv: -kv[1])
                      if sid not in seen and sid not in review_students
                      and self._could_be_unresolved(sid, unresolved_tracks, now)]
            unresolved_students = review_students | set(recent[:room])
            self._build_views(decisions, now)
            return {"confirmed": confirmed, "unidentified": len(unresolved_tracks),
                    "unresolved_students": unresolved_students, "bindings": bindings}

    # ---------------------------------------------------------- continuity
    def _norm(self, box):
        """Person box -> (x, y, w, h) as fractions of the frame (comparable across pipelines)."""
        if box is None:
            return None
        x1, y1, x2, y2 = box
        h, w = self._frame_hw or (0, 0)
        if not w or not h:
            return None
        return (x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h)

    def _restore(self, now) -> List[int]:
        """Continue DETACHED teacher bindings on new tracks, on safe seat continuity only:

        * the new person's box overlaps where the student last was (IoU >= restore_min_iou) and is
          the ONLY person there (no other person overlaps that place >= restore_ambiguity_iou);
        * the person fits no other detached student's place, carries no identity, no face match /
          candidate for any enrolled student, and the teacher never removed the student from it;
        * the person appeared after the binding was detached (not someone present all along);
        * it holds for restore_confirm_frames frames and restore_min_seconds, within
          restore_max_gap_seconds of the loss.
        Otherwise nothing happens: the student stays NOT_SEEN / unresolved until re-tagged."""
        c = self.cfg.identity
        cands = [r for r in self.store.detached()
                 if r.teacher_anchored and r.last_box is not None and r.student_id in self.roster
                 and self._restore_open(r, now)
                 and self.identity.track_for_student(r.student_id) is None]
        identifiable = self.identity.gallery_size > 0
        live = {r.binding_id for r in cands}
        for bid in list(self._restore_votes):
            if bid not in live:
                del self._restore_votes[bid]
        restored = []
        boxes = {tid: b for tid, b in self._boxes.items() if b is not None}
        for rec in cands:
            here = [tid for tid, b in boxes.items() if iou(b, rec.last_box) >= c.restore_min_iou]
            near = [tid for tid, b in boxes.items() if iou(b, rec.last_box) >= c.restore_ambiguity_iou]
            tid = here[0] if len(here) == 1 and len(near) == 1 else None
            t = self.identity.tracks.get(tid) if tid is not None else None
            ok = (t is not None and t.status in (UNKNOWN, UNCERTAIN) and t.student_id is None
                  and t.suggestion_id is None and t.candidate_id is None and t.last_outcome != AMBIGUOUS
                  and rec.student_id not in t.rejected_students
                  and t.first_seen is not None and t.first_seen >= rec.detached_at - c.track_lost_seconds - 1.0
                  and not any(iou(boxes[tid], o.last_box) >= c.restore_ambiguity_iou
                              for o in cands if o is not rec))
            if not ok:
                self._restore_votes.pop(rec.binding_id, None)
                continue
            vote = self._restore_votes.get(rec.binding_id)
            if vote is None or vote[0] != tid:
                vote = self._restore_votes[rec.binding_id] = [tid, 0, now, 0]
            vote[1] += 1
            vote[3] += t.last_outcome == NO_MATCH          # a face was seen and matched NO enrolled student
            if vote[1] < c.restore_confirm_frames or now - vote[2] < c.restore_min_seconds:
                continue
            if identifiable and vote[3] < max(2, c.restore_confirm_frames // 2):
                continue        # someone recognisable could be hiding their face: wait (or the teacher re-tags)
            del self._restore_votes[rec.binding_id]
            try:
                self.identity.restore_bind(tid, rec.student_id, now)
            except (KeyError, ValueError):
                continue
            self._start_binding(tid, rec.student_id, SRC_AUTO_RECOVERED, now, replaced=rec.binding_id,
                                derived_from=rec.binding_id, method=METHOD_SEAT_CONTINUITY, teacher_anchored=True)
            self._found(rec.student_id)
            rt = self.runtime()
            if rt is not None:
                rt.set_identity_meta(rec.student_id, source=SRC_AUTO_RECOVERED, review=False)
                rt.logs.append({"type": "identity_restored", "student_id": rec.student_id, "time": now,
                                "message": f"{self.roster.get(rec.student_id, rec.student_id)}: khôi phục danh tính "
                                           "giáo viên đã gắn (cùng vị trí sau khi camera kết nối lại)"})
            self.sm.audit.record(A.IDENTITY_SESSION_RESTORED, None, rec.student_id,
                                 {"class_id": self.class_id, "class_session_id": self.class_session_id,
                                  "track_id": tid, "method": METHOD_SEAT_CONTINUITY})
            boxes.pop(tid, None)
            restored.append(tid)
        return restored

    def _restore_open(self, rec, now) -> bool:
        """The restore window runs from when frames are available again (pipeline start / camera back),
        not from the last sighting: time with no picture is not evidence either way."""
        c = self.cfg.identity
        start = rec.detached_at
        # Only a binding lost AROUND the outage gets the window extended to when frames came back;
        # one lost long before (the student walked out) keeps its own window.
        if self._outage_start is not None and rec.detached_at >= self._outage_start - (c.track_lost_seconds + 2.0):
            start = max(rec.detached_at, self._resumed_at)
        return now - start <= c.restore_max_gap_seconds

    def _continuity_parent(self, sid, track_id, now) -> Optional[BindingRec]:
        """The student's previous binding that this new automatic binding continues as the SAME
        physical person: lost at the same place moments ago (tracker re-id / handover)."""
        c = self.cfg.identity
        prev = self.store.latest_for(sid)
        if prev is None or (not prev.open and prev.end_reason not in _CONTINUABLE_END):
            return None
        box = self._boxes.get(track_id)
        last = prev.last_seen_at if prev.last_seen_at is not None else prev.started_at
        if box is None or prev.last_box is None or now - last > c.continuity_max_gap_seconds:
            return None
        return prev if iou(box, prev.last_box) >= c.continuity_min_iou else None

    # ---------------------------------------------------------- bookkeeping
    def _lost(self, sid, track_id, now):
        self._lost_at[sid] = now
        self._lost_track[sid] = track_id

    def _found(self, sid):
        self._lost_at.pop(sid, None)
        self._lost_track.pop(sid, None)

    def _could_be_unresolved(self, sid, unresolved_tracks, now=None) -> bool:
        if now is not None and now > self._carry_until.get(sid, float("inf")):
            return False                               # carried over from a previous pipeline, expired
        lost_at = self._lost_at[sid]
        for tid in unresolved_tracks:
            if tid == self._lost_track.get(sid):
                return True
            t = self.identity.tracks.get(tid)
            first = getattr(t, "first_seen", None)
            # the tracker reports a loss only after track_lost_seconds: a re-id appears before that
            if first is not None and first >= lost_at - self.cfg.identity.track_lost_seconds - 1.0:
                return True
        return False

    def _handle_events(self, now):
        events = self.identity.events
        new, self._event_index = events[self._event_index:], len(events)
        rt = self.runtime()
        for ev in new:
            sid = ev.student_id
            if ev.kind in ("CONFIRMED", "RECOVERED"):
                source = SRC_AUTO_RECOVERED if ev.kind == "RECOVERED" else SRC_AUTO_FACE
                self._start_binding(ev.track_id, sid, source, now, confidence=ev.detail.get("similarity"))
                self._found(sid)
                if rt is not None:
                    rt.set_identity_meta(sid, source=source, review=False)
                if sid not in self._audited_auto:
                    self._audited_auto.add(sid)
                    self.sm.audit.record(A.IDENTITY_AUTO_CONFIRMED, None, sid,
                                         {"class_id": self.class_id, "class_session_id": self.class_session_id,
                                          "source": source})
            elif ev.kind == "RELEASED":
                b = self._binding.get(ev.track_id)
                if b is not None and b.student_id == sid:
                    if ev.detail.get("reason") == "track_lost" and b.teacher_anchored:
                        # the session identity outlives the track: detached, may be restored
                        self._binding.pop(ev.track_id, None)
                        self.store.detach(b.binding_id, b.last_seen_at if b.last_seen_at is not None else now)
                    else:
                        self._end_binding(ev.track_id, now, ev.detail.get("reason"))
                if sid is not None and self.identity.track_for_student(sid) is not None:
                    continue                    # re-bound elsewhere in the same step (teacher re-assignment)
                if sid is not None:
                    self._lost(sid, ev.track_id, now)
                    self._reject_pending(sid, track_id=ev.track_id, reason="identity_released")
                    if ev.track_id in self._bursts and self._bursts[ev.track_id].student_id == sid:
                        self._cancel_burst(ev.track_id, "identity_released")
                if rt is not None and sid is not None:
                    rt.identity_released(sid)
            elif ev.kind == "REVIEW_REQUIRED":
                self._reject_pending(sid, track_id=ev.track_id, reason="identity_conflict")
                if ev.track_id in self._bursts:
                    self._cancel_burst(ev.track_id, "identity_conflict")
                if rt is not None:
                    rt.set_identity_meta(sid, review=True)
                    rt.logs.append({"type": "identity_review", "student_id": sid, "time": now,
                                    "message": f"{self.roster.get(sid, sid)}: AI thấy khuôn mặt giống học sinh khác — "
                                               "cần giáo viên kiểm tra lại"})
            elif ev.kind == "CONFLICT" and sid is not None:
                self._reject_pending(sid, reason="identity_conflict")

    def _start_binding(self, track_id, student_id, source, now, confidence=None, by=None, reason=None,
                       replaced=None, derived_from=None, method=None, teacher_anchored=None) -> str:
        """A new session binding of ``track_id`` (append-only history). ``derived_from``: the binding
        it continues as the same physical person (same track re-confirmed, tracker re-id at the same
        place, seat-continuity restore) — attendance evidence and templates follow these links when a
        teacher says the binding was wrong."""
        sid = int(student_id)
        old = self._binding.get(track_id)
        if old is not None:
            if derived_from is None and old.student_id == sid:
                derived_from, method = old.binding_id, METHOD_SAME_TRACK
            self._end_binding(track_id, now, "replaced")
        prev = self.store.open_for(sid)                # e.g. a detached binding of the student
        if derived_from is None and source not in TEACHER_SOURCES:
            parent = self._continuity_parent(sid, track_id, now)
            if parent is not None:
                derived_from, method = parent.binding_id, METHOD_TRACK_CONTINUITY
                if teacher_anchored is None:
                    teacher_anchored = parent.teacher_anchored      # same person, the teacher's tag
        self._carry_until.pop(sid, None)
        if prev is not None and prev.open:
            self.store.end(prev.binding_id, now, "restored" if method == METHOD_SEAT_CONTINUITY else "superseded")
        rec = self.store.start(track_id, sid, source, now, confidence=confidence, by=by, reason=reason,
                               replaced=replaced, derived_from=derived_from, recovery_method=method,
                               teacher_anchored=teacher_anchored)
        rec.gen = self.gen
        rec.last_box = self._boxes.get(track_id)
        self._binding[track_id] = rec
        return rec.binding_id

    def _end_binding(self, track_id, now, reason):
        b = self._binding.pop(track_id, None)
        if b is not None:
            self.store.end(b.binding_id, now, reason)
        return b

    # --------------------------------------------------------------- views
    def _track_face_sizes(self, faces):
        sizes = {}
        small = 0
        for tid, f in faces.items():
            if f is not None and f.box is not None:
                x1, y1, x2, y2 = f.box
                px = max(0.0, min(x2 - x1, y2 - y1))
                sizes[tid] = px
                small += px < self.ecfg.recognition_min_face_px
        self._face_px = sizes
        if sizes:
            self._small_ratio = 0.9 * self._small_ratio + 0.1 * (small / len(sizes))

    def _build_views(self, decisions, now):
        h, w = (self._frame_hw or (0, 0))
        out = []
        for tid, box in sorted(self._persons.items()):
            d = decisions.get(tid) or self.identity.tracks.get(tid)
            x1, y1, x2, y2 = box
            px = self._face_px.get(tid)
            v = {"track_id": tid,
                 "box": [round(x1 / w, 4), round(y1 / h, 4), round((x2 - x1) / w, 4), round((y2 - y1) / h, 4)]
                 if w and h else None,
                 "status": getattr(d, "status", "UNKNOWN"), "student_id": None, "student_name": None,
                 "source": None, "teacher_locked": False, "review_required": False, "conflict_with_track": None,
                 "suggestion": None, "face_px": None if px is None else int(px),
                 "face_too_small": px is not None and px < self.ecfg.min_face_px,
                 "enrolling": tid in self._bursts}
            if d is not None:
                v["review_required"] = bool(getattr(d, "review_required", False))
                v["conflict_with_track"] = getattr(d, "conflict_with_track", None)
                if d.status == CONFIRMED and d.student_id is not None:
                    v.update(student_id=d.student_id, student_name=self.roster.get(d.student_id),
                             source=d.source, teacher_locked=bool(d.teacher_locked),
                             restored=bool(getattr(d, "restored", False)),
                             confidence=round(float(d.confidence), 3))
                    if not d.teacher_locked:
                        v["suggestion"] = {"student_id": d.student_id, "student_name": self.roster.get(d.student_id),
                                           "confidence": round(float(d.confidence), 3),
                                           "high_confidence": self._high_confidence(d)}
                elif getattr(d, "suggestion_id", None) is not None:
                    v["suggestion"] = {"student_id": d.suggestion_id, "student_name": self.roster.get(d.suggestion_id),
                                       "confidence": round(float(d.suggestion_similarity), 3), "high_confidence": False}
            out.append(v)
        self.views = out

    def _high_confidence(self, d) -> bool:
        c = self.cfg.identity
        return (d.status == CONFIRMED and not d.teacher_locked and not d.review_required
                and d.source in (SRC_AUTO_FACE, SRC_AUTO_RECOVERED)
                and float(d.confidence) >= c.bulk_confirm_similarity and float(d.margin) >= 2 * c.min_margin)

    def snapshot(self, now=None) -> dict:
        """Everything the teacher's initialization / roster UI needs (no vectors, no images)."""
        with self.lock:
            bound = {}
            for tid, t in self.identity.tracks.items():
                if t.status == CONFIRMED and t.student_id is not None:
                    bound[t.student_id] = {"track_id": tid, "source": t.source, "review_required": t.review_required,
                                           "restored": bool(getattr(t, "restored", False))}
            now = self.clock() if now is None else now
            detached = {r.student_id for r in self.store.detached() if self._restore_open(r, now)}
            roster = []
            for sid, name in sorted(self.roster.items(), key=lambda kv: kv[1] or ""):
                b = bound.get(sid)
                roster.append({"student_id": sid, "name": name, "track_id": b["track_id"] if b else None,
                               "identity_source": b["source"] if b else None,
                               "review_required": bool(b and b["review_required"]),
                               "restored": bool(b and b["restored"]),
                               # the teacher's tag is kept for the session; waiting for a safe match
                               "awaiting_restore": b is None and sid in detached,
                               "face_consent": sid in self.consented,
                               "enrolling": any(bu.student_id == sid for bu in self._bursts.values())})
            warnings = []
            if self._face_px and self._small_ratio >= 0.3:
                warnings.append(FAR_CAMERA_WARNING_VI)
            return {"tracks": list(self.views), "roster": roster,
                    "unassigned": [r["student_id"] for r in roster if r["track_id"] is None],
                    "unknown_tracks": [v["track_id"] for v in self.views if v["status"] != CONFIRMED],
                    "conflicts": [v["track_id"] for v in self.views if v["review_required"] or v["status"] == CONFLICT],
                    "warnings": warnings, "gallery_size": self.identity.gallery_size}

    # --------------------------------------------------------- teacher tags
    def tag(self, actor, track_id, student_id, now=None, reason=None) -> dict:
        now = self.clock() if now is None else now
        sid, tid = int(student_id), int(track_id)
        with self.lock:
            if sid not in self.roster:
                raise TagError("Học sinh không thuộc lớp này.", 400)
            t = self.identity.tracks.get(tid)
            if t is None or tid not in self._persons:
                raise TagError("Người này không còn trong khung hình. Hãy chọn lại.", 404)
            old = self._binding.get(tid)
            res = self.identity.teacher_bind(tid, sid, now, by=getattr(actor, "user_id", None))
            prev, displaced = res["previous_student"], res["displaced_track"]
            rt = self.runtime()
            if displaced is not None and displaced in self._binding:
                self._end_binding(displaced, now, "teacher_reassigned")
                self._cancel_burst(displaced, "teacher_reassigned")
            if prev is not None and prev != sid:
                # Wrong identity: anything that rests on it (this binding and the bindings it continues
                # as the same person) is rejected — templates, pending samples, attendance evidence.
                wrong = self.store.revocation_lineage(old.binding_id) if old is not None else []
                self._cancel_burst(tid, "teacher_correction")
                self._reject_pending(prev, track_id=tid, reason="teacher_correction")
                for b in wrong:
                    self.sm.faces.remove_binding_templates(b, prev, actor)
                if rt is not None:
                    rt.identity_released(prev)
                    rt.revoke_attendance_evidence(prev, wrong, now, "teacher_correction",
                                                  by=getattr(actor, "user_id", None))
                self._lost(prev, tid, now)
            self._reject_pending(sid, reason="teacher_retag")          # restart learning from the teacher's tag
            bid = self._start_binding(tid, sid, res["source"], now, by=getattr(actor, "user_id", None),
                                      reason=(str(reason)[:200] if reason else None),
                                      replaced=old.binding_id if old is not None else None)
            self._found(sid)
            if rt is not None:
                rt.teacher_identified(sid, now, res["source"], track_id=tid, binding_id=bid, first_seen_at=t.first_seen)
            action = A.IDENTITY_TEACHER_CORRECTED if res["source"] == SRC_TEACHER_CORRECTED else A.IDENTITY_TEACHER_TAGGED
            self.sm.audit.record(action, actor, sid, {"class_id": self.class_id,
                                                       "class_session_id": self.class_session_id,
                                                       "track_id": tid, "previous_student_id": prev})
            enrollment = ENROLL_SESSION_ONLY
            if sid in self.consented:
                self._cancel_burst(tid, "restarted")
                self._bursts[tid] = _Burst(sid, bid, now + self.ecfg.burst_seconds, getattr(actor, "user_id", None),
                                           had_profile=sid in set(self.identity._gallery[0]))
                enrollment = ENROLL_BURST_STARTED
            return {"track_id": tid, "student_id": sid, "source": res["source"], "previous_student_id": prev,
                    "displaced_track": displaced, "enrollment": enrollment}

    def untag(self, actor, track_id, now=None) -> dict:
        now = self.clock() if now is None else now
        tid = int(track_id)
        with self.lock:
            old = self._binding.get(tid)
            sid = self.identity.teacher_unbind(tid, now)
            if sid is None:
                raise TagError("Người này chưa được gắn tên.", 409)
            wrong = self.store.revocation_lineage(old.binding_id) if old is not None else []
            self._end_binding(tid, now, "teacher_removed")
            self._cancel_burst(tid, "teacher_removed")
            self._reject_pending(sid, track_id=tid, reason="teacher_removed")
            for b in wrong:
                self.sm.faces.remove_binding_templates(b, sid, actor, reason="teacher_removed")
            rt = self.runtime()
            if rt is not None:
                rt.identity_released(sid)
                rt.revoke_attendance_evidence(sid, wrong, now, "teacher_removed", by=getattr(actor, "user_id", None))
            self._lost(sid, tid, now)
            self.sm.audit.record(A.IDENTITY_TAG_REMOVED, actor, sid, {"class_id": self.class_id,
                                                                       "class_session_id": self.class_session_id,
                                                                       "track_id": tid})
            return {"track_id": tid, "student_id": sid}

    def has_track(self, track_id) -> bool:
        with self.lock:
            return int(track_id) in self._persons and int(track_id) in self.identity.tracks

    def confirm_suggestions(self, actor, now=None) -> List[dict]:
        """One click for the strict, high-confidence AUTO matches only (unique, no conflict)."""
        now = self.clock() if now is None else now
        with self.lock:
            todo = [(tid, t.student_id) for tid, t in self.identity.tracks.items()
                    if tid in self._persons and self._high_confidence(t)]
            return [self.tag(actor, tid, sid, now) for tid, sid in todo]

    # ----------------------------------------------------------- enrollment
    def _sample(self, sid, face, now, occluded, tid, binding_id, min_quality=None):
        vec = normalize(face.embedding)
        if vec is None:
            return None, ["NO_EMBEDDING"]
        metrics = face_metrics(face.gray, face.box, face.det_score, face.yaw, face.pitch)
        ok, q, reasons = evaluate(metrics, self.ecfg, occluded=occluded, association_ok=face.association_ok)
        if ok and min_quality is not None and q < min_quality:
            ok, reasons = False, ["LOW_QUALITY"]
        if not ok:
            return None, reasons
        return Sample(int(sid), vec, q, pose_bucket(face.yaw, face.pitch), now, tid, binding_id), []

    def _burst_sample(self, tid, d, face, now, occluded):
        b = self._bursts[tid]
        if d.status != CONFIRMED or d.student_id != b.student_id or d.review_required:
            self._cancel_burst(tid, "identity_changed")
            return
        s, reasons = self._sample(b.student_id, face, now, occluded, tid, b.binding_id)
        if s is None:
            b.rejected += 1
            return
        if b.samples and max_similarity(s.embedding, [x.embedding for x in b.samples]) >= self.ecfg.duplicate_similarity:
            b.rejected += 1
            return
        b.samples.append(s)

    def _finish_burst(self, tid, now):
        b = self._bursts.pop(tid, None)
        if b is None:
            return 0
        samples = consistent(b.samples, self.ecfg.burst_self_consistency)
        if b.had_profile:
            c = self.cfg.identity
            kept = []
            for s in samples:
                scores = self.identity.student_scores(s.embedding) if self.identity._gallery[1] is not None else {}
                own = scores.get(b.student_id)
                other = max((v for k, v in scores.items() if k != b.student_id), default=-1.0)
                if own is not None and own < c.similarity_threshold - c.min_margin and other >= c.similarity_threshold:
                    s.state = REJECTED
                    s.reasons.append("CONTRADICTS_PROFILE")
                    continue
                kept.append(s)
            samples = kept
        dropped = len(b.samples) - len(samples) + b.rejected
        actor = b.actor_id
        try:
            n = self.sm.faces.commit(b.student_id, samples, SRC_BURST, actor, self.class_session_id, b.binding_id)
            if dropped:
                self.sm.faces.reject(b.student_id, dropped, "quality_or_consistency", actor)
            if n:
                self.reload_gallery()
        except Exception as exc:                       # storage errors must never stop the camera loop
            print("[IDENTITY] enrollment burst not stored:", type(exc).__name__)
            return 0
        return n

    def _cancel_burst(self, tid, reason):
        b = self._bursts.pop(tid, None)
        if b is not None and (b.samples or b.rejected):
            for s in b.samples:
                s.state = REJECTED
            self.sm.faces.reject(b.student_id, len(b.samples), reason)

    def _progressive_sample(self, tid, d, face, now, occluded):
        e, c = self.ecfg, self.cfg.identity
        sid = d.student_id
        if (d.status != CONFIRMED or sid is None or d.review_required or sid not in self.consented
                or self._progressive_n.get(sid, 0) >= e.progressive_max_per_session
                or d.confirmed_at is None or now - d.confirmed_at < e.progressive_min_confirmed_seconds
                or now - self._last_progressive.get(sid, -1e9) < 10.0):
            return
        if d.source not in TEACHER_SOURCES and (d.source not in (SRC_AUTO_FACE, SRC_AUTO_RECOVERED)
                                                or float(d.confidence) < e.progressive_min_similarity
                                                or float(d.margin) < 2 * c.min_margin):
            return
        b = self._binding.get(tid)
        s, _reasons = self._sample(sid, face, now, occluded, tid, b.binding_id if b is not None else None,
                                   e.progressive_min_quality)
        if s is None:
            return
        gallery_ids, mat = self.identity._gallery
        own = [mat[i] for i, g in enumerate(gallery_ids) if g == sid] if mat is not None else []
        if own:
            sim = max_similarity(s.embedding, own)
            if sim < e.progressive_min_similarity or sim > e.progressive_max_similarity_to_existing:
                return                      # not clearly this student, or nothing new to learn
        if max_similarity(s.embedding, [p.embedding for p in self._pending if p.student_id == sid]) >= \
                e.duplicate_similarity:
            return
        self._pending.append(s)
        self._progressive_n[sid] = self._progressive_n.get(sid, 0) + 1
        self._last_progressive[sid] = now

    def _reject_pending(self, student_id, track_id=None, reason="rejected"):
        if student_id is None:
            return 0
        hit = [s for s in self._pending if s.student_id == student_id and s.state == PENDING
               and (track_id is None or s.track_id == track_id)]
        for s in hit:
            s.state = REJECTED
            s.reasons.append(reason)
        if hit:
            self._pending = [s for s in self._pending if s.state == PENDING]
            self._progressive_n[student_id] = max(0, self._progressive_n.get(student_id, 0) - len(hit))
            self.sm.faces.reject(student_id, len(hit), reason)
        return len(hit)

    def pending_samples(self, student_id=None) -> List[Sample]:
        return [s for s in self._pending if student_id is None or s.student_id == student_id]

    # ----------------------------------------------------------------- end
    def close(self, now=None, final=False):
        """Pipeline closed: finish bursts and end open bindings. PENDING progressive samples are
        committed only when the CLASS ends (``final``): a pipeline closed mid-class (page reload,
        viewer left) drops them, because the in-memory bindings a teacher correction would need
        to reject them are gone. Nothing else is kept in memory."""
        now = self.clock() if now is None else now
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for tid in list(self._bursts):
                self._finish_burst(tid, now)
            by_student: Dict[int, List[Sample]] = {}
            for s in self._pending:
                if s.state == PENDING and s.student_id in self.consented:
                    by_student.setdefault(s.student_id, []).append(s)
            for sid, samples in by_student.items():
                try:
                    if final and self.sm.faces.consent_active(sid):
                        self.sm.faces.commit(sid, samples, SRC_PROGRESSIVE, None, self.class_session_id,
                                             limit=self.ecfg.progressive_max_per_session)
                    else:
                        for s in samples:
                            s.state = REJECTED
                        self.sm.faces.reject(sid, len(samples), "pipeline_closed")
                except Exception as exc:
                    print("[IDENTITY] progressive samples not stored:", type(exc).__name__)
            self._pending = []
            if final:
                self._binding.clear()
                self.store.close(now)                  # the class ended: every session binding ends
                return
            # Pipeline closed mid-class (reconnect, page reload, rebuilt pipeline): teacher bindings
            # stay with the session (detached, restorable); automatic ones end (face finds them again).
            self.store.pipeline_closed_at = now
            for tid, b in list(self._binding.items()):
                if b.teacher_anchored:
                    self.store.detach(b.binding_id, b.last_seen_at if b.last_seen_at is not None else now)
                else:
                    self.store.end(b.binding_id, now, "pipeline_closed")
            self._binding.clear()
