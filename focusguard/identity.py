"""IdentityManager: turns temporary tracker ids into canonical student ids.

A tracker ``track_id`` is only a temporary handle for "the same box across
frames". It is never used as an identity. The canonical identity is the
``student_id`` from the enrolment database, and it is only attached to a track
after temporal evidence:

* best cosine similarity above ``similarity_threshold``;
* ``best - second_best`` above ``min_margin`` (otherwise ambiguous);
* the same student wins ``confirm_consecutive`` recognitions in a row, spread
  over at least ``confirm_min_seconds``;
* the student is not already bound to another live track (one student can
  never be two tracks at once);
* a locked identity is only dropped after ``unlock_consecutive`` contradicting
  recognitions, which protects against one bad frame and people crossing;
* identities released by contradiction enter a cooldown during which they can
  only be re-bound with very strong evidence;
* identities of tracks that disappear are remembered for
  ``recovery_window_seconds`` so a re-assigned tracker id can recover quickly.

Anything short of that evidence keeps the track ``UNKNOWN`` / ``UNCERTAIN``.

Multi-template gallery: a student may have several face templates
(focusguard/face_profiles.py); the per-student score is the best template, and
with >= 2 templates one odd template cannot carry a match alone (the second
best must agree within ``template_agreement``). Threshold, margin between
STUDENTS, temporal confirmation and one-student-one-track still apply.

Provenance (``source``) is kept apart from the status: AUTO_FACE,
AUTO_RECOVERED, TEACHER_TAG, TEACHER_CORRECTED. A teacher binding
(:meth:`IdentityManager.teacher_bind`) is the authority for the session: face
evidence never switches it; persistent contradiction only raises
``review_required`` for the teacher to resolve. A student the teacher removed
from a track is never auto-bound to that track again.
"""
import copy
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .config import DEFAULT_CONFIG, IdentityConfig

# Track identity statuses
UNKNOWN = "UNKNOWN"          # never matched / no usable face yet
CANDIDATE = "CANDIDATE"      # accumulating consecutive evidence
CONFIRMED = "CONFIRMED"      # locked to a student_id
UNCERTAIN = "UNCERTAIN"      # evidence ambiguous or contradicted, lock dropped
CONFLICT = "CONFLICT"        # matched a student already bound to another live track

# Identity provenance
SRC_AUTO_FACE = "AUTO_FACE"
SRC_AUTO_RECOVERED = "AUTO_RECOVERED"
SRC_TEACHER_TAG = "TEACHER_TAG"
SRC_TEACHER_CORRECTED = "TEACHER_CORRECTED"
TEACHER_SOURCES = (SRC_TEACHER_TAG, SRC_TEACHER_CORRECTED)

# Per-recognition outcomes
MATCH = "MATCH"
NO_MATCH = "NO_MATCH"        # best similarity below threshold
AMBIGUOUS = "AMBIGUOUS"      # margin to the second best too small
NO_FACE = "NO_FACE"          # no embedding this frame


def _normalize(vec) -> Optional[np.ndarray]:
    if vec is None:
        return None
    arr = np.asarray(vec, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        return None
    norm = np.linalg.norm(arr)
    if norm <= 0 or not np.isfinite(norm):
        return None
    return arr / norm


@dataclass
class MatchResult:
    outcome: str
    best_id: Optional[int] = None
    best_similarity: float = 0.0
    second_id: Optional[int] = None
    second_similarity: float = 0.0

    @property
    def margin(self) -> float:
        return self.best_similarity - self.second_similarity

    @property
    def accepted_id(self) -> Optional[int]:
        return self.best_id if self.outcome == MATCH else None


@dataclass
class TrackIdentity:
    track_id: int
    status: str = UNKNOWN
    student_id: Optional[int] = None
    confidence: float = 0.0
    margin: float = 0.0
    candidate_id: Optional[int] = None
    candidate_count: int = 0
    candidate_since: Optional[float] = None
    contradict_count: int = 0
    contradict_since: Optional[float] = None
    stranger_since: Optional[float] = None      # first clear non-match while locked
    previous_student: Optional[int] = None      # student this track owned before a handover
    previous_at: Optional[float] = None
    conflict_with_track: Optional[int] = None
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None
    confirmed_at: Optional[float] = None
    last_outcome: str = NO_FACE
    source: Optional[str] = None                # provenance of the current binding
    teacher_locked: bool = False                # teacher tag / correction: face evidence never switches it
    confirmed_by: Optional[int] = None
    review_required: bool = False               # contradicting evidence on a teacher binding
    rejected_students: set = field(default_factory=set)   # teacher removed them from this track
    suggestion_id: Optional[int] = None         # best accepted match while not locked (AI suggestion)
    suggestion_similarity: float = 0.0
    suggestion_margin: float = 0.0
    restored: bool = False                      # teacher binding continued on a new track by seat continuity

    @property
    def needs_review(self) -> bool:
        return self.review_required

    def snapshot(self) -> dict:
        return {
            "track_id": self.track_id,
            "status": self.status,
            "student_id": self.student_id,
            "source": self.source,
            "teacher_locked": self.teacher_locked,
            "review_required": self.review_required,
            "confidence": round(float(self.confidence), 4),
            "margin": round(float(self.margin), 4),
            "candidate_id": self.candidate_id,
            "candidate_count": self.candidate_count,
            "conflict_with_track": self.conflict_with_track,
            "last_outcome": self.last_outcome,
        }


@dataclass
class IdentityEvent:
    """Audit trail of identity decisions (used by evaluation + logs)."""
    kind: str              # CONFIRMED | RELEASED | CONFLICT | RECOVERED | LOST
    track_id: int
    student_id: Optional[int]
    timestamp: float
    detail: dict = field(default_factory=dict)


class IdentityManager:
    def __init__(self, gallery: Optional[Dict[int, Sequence[float]]] = None,
                 config: IdentityConfig = DEFAULT_CONFIG.identity):
        self.config = config
        self._gallery: Tuple[List[int], Optional[np.ndarray]] = ([], None)
        self.tracks: Dict[int, TrackIdentity] = {}
        self._owner: Dict[int, int] = {}                    # student_id -> track_id
        self._cooldown_until: Dict[int, float] = {}         # student_id -> ts
        self._recently_lost: Dict[int, float] = {}          # student_id -> lost ts
        self.events: List[IdentityEvent] = []
        self._occluded: set = set()
        self._present: set = set()
        self.set_gallery(gallery or {})

    # ------------------------------------------------------------------ gallery
    def set_gallery(self, gallery: Dict[int, Sequence[float]]):
        """``{student_id: embedding}`` or ``{student_id: [embedding, ...]}`` (multi-template)."""
        ids, rows = [], []
        for sid, emb in gallery.items():
            arr = np.asarray(emb, dtype=np.float64) if emb is not None else None
            vectors = [emb] if arr is None or arr.ndim <= 1 else list(emb)
            for e in vectors:
                vec = _normalize(e)
                if vec is None:
                    continue
                if rows and vec.shape != rows[0].shape:
                    continue
                ids.append(int(sid))
                rows.append(vec)
        # Swap ids+matrix together so a concurrent match() never sees a mix.
        self._gallery = (ids, np.vstack(rows) if rows else None)
        # Students removed from the gallery lose an AUTOMATIC binding; a teacher binding is
        # session identity (no biometric data needed) and stays.
        present = set(self._gallery[0])
        for sid in list(self._owner):
            t = self.tracks.get(self._owner[sid])
            if sid not in present and not (t is not None and t.teacher_locked):
                self._release(self._owner[sid], reason="gallery_removed", now=None)

    @property
    def gallery_size(self) -> int:
        return len(set(self._gallery[0]))

    def student_scores(self, vec) -> Dict[int, float]:
        """Per-student similarity over that student's templates (see module docstring)."""
        gallery_ids, gallery_mat = self._gallery
        sims = gallery_mat @ vec
        per: Dict[int, List[float]] = {}
        for sid, s in zip(gallery_ids, sims):
            per.setdefault(sid, []).append(float(s))
        out = {}
        for sid, values in per.items():
            values.sort(reverse=True)
            best = values[0]
            if len(values) >= 2 and values[1] < best - self.config.template_agreement:
                best = (values[0] + values[1]) / 2.0
            out[sid] = best
        return out

    def match(self, embedding) -> MatchResult:
        vec = _normalize(embedding)
        if vec is None:
            return MatchResult(NO_FACE)
        gallery_ids, gallery_mat = self._gallery
        if gallery_mat is None or vec.shape[0] != gallery_mat.shape[1]:
            return MatchResult(NO_MATCH)
        scores = sorted(self.student_scores(vec).items(), key=lambda kv: -kv[1])
        best_id, best_sim = scores[0]
        second_id, second_sim = (scores[1] if len(scores) > 1 else (None, -1.0))
        res = MatchResult(NO_MATCH, best_id, best_sim, second_id, max(second_sim, 0.0))
        if best_sim < self.config.similarity_threshold:
            res.outcome = NO_MATCH
        elif best_sim - max(second_sim, 0.0) < self.config.min_margin:
            res.outcome = AMBIGUOUS
        else:
            res.outcome = MATCH
        return res

    # ---------------------------------------------------------------- queries
    def student_for_track(self, track_id) -> Optional[int]:
        t = self.tracks.get(track_id)
        return t.student_id if t and t.status == CONFIRMED else None

    def track_for_student(self, student_id) -> Optional[int]:
        return self._owner.get(student_id)

    def bindings(self) -> Dict[int, int]:
        """Current student_id -> track_id bindings (always one-to-one)."""
        return dict(self._owner)

    # --------------------------------------------------------------- per frame
    def process_frame(self, observations: Iterable[Tuple[int, Optional[Sequence[float]]]],
                      now: float) -> Dict[int, TrackIdentity]:
        """Update identities for one frame.

        ``observations`` is an iterable of ``(track_id, embedding_or_None)`` or
        ``(track_id, embedding_or_None, occluded)`` for every track visible in
        this frame. ``occluded`` marks a known occluder over the face (e.g. an
        associated phone): the embedding is then unreliable and a non-match does
        not count against a locked identity. Returns ``{track_id: TrackIdentity}``
        for those tracks. Tracks missing from the frame age out and eventually
        release their identity for recovery.
        """
        obs, occluded = [], set()
        for item in observations:
            tid = int(item[0])
            obs.append((tid, item[1]))
            if len(item) > 2 and item[2]:
                occluded.add(tid)
        self._occluded = occluded
        self._present = {tid for tid, _ in obs}
        matches = {tid: self.match(emb) for tid, emb in obs}

        # Locked tracks first so existing bindings are respected, then unlocked
        # tracks by descending similarity so the strongest claim wins a tie.
        def order(item):
            tid, _ = item
            t = self.tracks.get(tid)
            locked = 0 if (t and t.status == CONFIRMED) else 1
            return (locked, -matches[tid].best_similarity)

        # Age out tracks that disappeared first, so their identities become
        # available for recovery by a re-assigned track in this same frame.
        self._age_out({tid for tid, _ in obs}, now)
        result = {}
        for tid, _ in sorted(obs, key=order):
            # Return immutable-by-convention copies: callers may keep them.
            result[tid] = copy.copy(self._observe(tid, matches[tid], now))
        return result

    def observe(self, track_id: int, embedding, now: float) -> TrackIdentity:
        return self.process_frame([(track_id, embedding)], now)[int(track_id)]

    # ---------------------------------------------------------------- internals
    def _observe(self, tid: int, m: MatchResult, now: float) -> TrackIdentity:
        t = self.tracks.get(tid)
        if t is None:
            t = TrackIdentity(track_id=tid, first_seen=now)
            self.tracks[tid] = t
        t.last_seen = now
        t.last_outcome = m.outcome

        if t.status == CONFIRMED:
            self._observe_locked(t, m, now)
        else:
            self._observe_unlocked(t, m, now)
        return t

    def _observe_locked(self, t: TrackIdentity, m: MatchResult, now: float):
        accepted = m.accepted_id
        if accepted == t.student_id:
            t.contradict_count = 0
            t.contradict_since = None
            t.stranger_since = None
            t.confidence = m.best_similarity
            t.margin = m.margin
            return
        if t.teacher_locked:
            # Teacher authority: no face evidence switches or releases this binding. Another
            # enrolled student matching clearly and persistently only asks the teacher to review.
            # A student the teacher already rejected for this track (correction) is not new evidence.
            if accepted is None or accepted in t.rejected_students:
                return
            if t.contradict_count == 0:
                t.contradict_since = now
            t.contradict_count += 1
            if (not t.review_required and t.contradict_count >= self.config.teacher_review_consecutive
                    and now - (t.contradict_since if t.contradict_since is not None else now) >= self.config.teacher_review_min_seconds):
                t.review_required = True
                self.events.append(IdentityEvent("REVIEW_REQUIRED", t.track_id, t.student_id, now,
                                                  {"contradicting_student": accepted,
                                                   "similarity": round(m.best_similarity, 4)}))
            return
        clear_non_match = (m.outcome == NO_MATCH and
                           m.best_similarity < self.config.similarity_threshold - self.config.min_margin)
        if accepted is None:
            # No face / ambiguous / borderline / occluded face: keep the lock.
            # A clear non-match only releases after stranger_unlock_seconds of
            # no match at all (stranger on this track after an id switch).
            if clear_non_match and t.track_id not in getattr(self, "_occluded", ()):
                if t.stranger_since is None:
                    t.stranger_since = now
                if now - t.stranger_since >= self.config.stranger_unlock_seconds:
                    self._release(t.track_id, reason="no_match", now=now)
                    t.status = UNCERTAIN
                    t.stranger_since = None
                    t.candidate_id, t.candidate_count, t.candidate_since = None, 0, None
            return
        # Another ENROLLED student matches clearly on this track.
        if t.contradict_count == 0:
            t.contradict_since = now
        t.contradict_count += 1
        if (t.contradict_count >= self.config.unlock_consecutive
                and now - (t.contradict_since if t.contradict_since is not None else now) >= self.config.unlock_min_seconds):
            released = t.student_id
            self._release(t.track_id, reason="contradicted", now=now)
            self._cooldown_until[released] = now + self.config.conflict_cooldown_seconds
            t.status = UNCERTAIN
            t.candidate_id = accepted
            t.candidate_count = 1
            t.candidate_since = now

    def _observe_unlocked(self, t: TrackIdentity, m: MatchResult, now: float):
        accepted = m.accepted_id
        if accepted is not None and accepted in t.rejected_students:
            accepted = None                       # the teacher said this track is NOT that student
            m = MatchResult(AMBIGUOUS, m.best_id, m.best_similarity, m.second_id, m.second_similarity)
        t.confidence = m.best_similarity if m.outcome != NO_FACE else t.confidence
        t.margin = m.margin if m.outcome != NO_FACE else t.margin
        if m.outcome != NO_FACE:
            t.suggestion_id = accepted
            t.suggestion_similarity = m.best_similarity if accepted is not None else 0.0
            t.suggestion_margin = m.margin if accepted is not None else 0.0

        if m.outcome == NO_FACE:
            return  # no evidence either way; streak neither grows nor breaks
        if accepted is None:
            # Below threshold or ambiguous: break the consecutive streak.
            t.candidate_id = None
            t.candidate_count = 0
            t.candidate_since = None
            if t.status in (CANDIDATE, CONFLICT):
                t.status = UNCERTAIN
            elif t.status == UNKNOWN and m.outcome == AMBIGUOUS:
                t.status = UNCERTAIN
            return

        if accepted == t.candidate_id:
            t.candidate_count += 1
        else:
            t.candidate_id = accepted
            t.candidate_count = 1
            t.candidate_since = now
        was_conflict_owner = t.conflict_with_track
        t.status = CANDIDATE
        t.conflict_with_track = None

        recovering = self._is_recovering(accepted, now)
        needed = self.config.recovery_consecutive if recovering else self.config.confirm_consecutive
        min_secs = 0.0 if recovering else self.config.confirm_min_seconds
        if (t.previous_student == accepted and t.previous_at is not None
                and now - t.previous_at <= self.config.recovery_window_seconds):
            # This very track was confirmed for this student moments ago and
            # only lost it to a duplicate id of the same person.
            needed, min_secs, recovering = 1, 0.0, True
        if t.candidate_count < needed or (now - (t.candidate_since if t.candidate_since is not None else now)) < min_secs:
            return

        owner = self._owner.get(accepted)
        owner_t = self.tracks.get(owner) if owner is not None else None
        if owner_t is not None and owner_t.teacher_locked and owner != t.track_id:
            # The teacher bound this student to another track: face evidence cannot take it.
            if owner in getattr(self, "_present", ()) and was_conflict_owner != owner:
                self.events.append(IdentityEvent("CONFLICT", t.track_id, accepted, now, {"owner_track": owner}))
            t.status = CONFLICT
            t.conflict_with_track = owner
            return
        if owner is not None and owner != t.track_id:
            # The owner blocks this claim only while it is visible in THIS
            # frame. Real run: the tracker kept two ids for one person and
            # reported one or the other per frame; the id carrying the (fully
            # confirmed) face must be able to take over, otherwise the student
            # flickers to "not visible". Two tracks visible at once with the
            # same face remain a CONFLICT (one student, one track).
            owner_alive = owner in getattr(self, "_present", ())
            if owner_alive:
                if was_conflict_owner != owner:
                    self.events.append(IdentityEvent("CONFLICT", t.track_id, accepted, now,
                                                      {"owner_track": owner}))
                t.status = CONFLICT
                t.conflict_with_track = owner
                return
            # Owner track is not visible now: hand the identity over (track recovery).
            owner_t = self.tracks.get(owner)
            if owner_t is not None:
                owner_t.previous_student, owner_t.previous_at = accepted, now
            self._release(owner, reason="owner_not_visible", now=now)
            recovering = True

        cooldown = self._cooldown_until.get(accepted)
        if cooldown is not None and now < cooldown and m.best_similarity < self.config.steal_similarity:
            t.status = UNCERTAIN
            return

        self._lock(t, accepted, m, now, recovered=recovering)

    def _is_recovering(self, student_id: int, now: float) -> bool:
        lost_at = self._recently_lost.get(student_id)
        return lost_at is not None and (now - lost_at) <= self.config.recovery_window_seconds

    def _lock(self, t: TrackIdentity, student_id: int, m: MatchResult, now: float, recovered: bool):
        t.status = CONFIRMED
        t.student_id = student_id
        t.confidence = m.best_similarity
        t.margin = m.margin
        t.contradict_count = 0
        t.contradict_since = None
        t.stranger_since = None
        t.confirmed_at = now
        t.candidate_id = None
        t.candidate_count = 0
        t.candidate_since = None
        t.source = SRC_AUTO_RECOVERED if recovered else SRC_AUTO_FACE
        t.teacher_locked = False
        t.review_required = False
        t.confirmed_by = None
        self._owner[student_id] = t.track_id
        self._recently_lost.pop(student_id, None)
        self._cooldown_until.pop(student_id, None)
        self.events.append(IdentityEvent("RECOVERED" if recovered else "CONFIRMED",
                                          t.track_id, student_id, now,
                                          {"similarity": round(m.best_similarity, 4),
                                           "margin": round(m.margin, 4)}))

    # ------------------------------------------------------- teacher authority
    def teacher_bind(self, track_id: int, student_id: int, now: float, by=None) -> dict:
        """Teacher tags ``track_id`` as ``student_id`` (session identity, immediate).

        Releases whatever the student or the track was bound to before. Returns
        ``{"source", "previous_student", "displaced_track"}``; ``source`` is
        TEACHER_CORRECTED when the track carried another student, else TEACHER_TAG."""
        tid, sid = int(track_id), int(student_id)
        t = self.tracks.get(tid)
        if t is None:
            raise KeyError("unknown track")
        previous = t.student_id if t.status == CONFIRMED else None
        displaced = None
        owner = self._owner.get(sid)
        if owner is not None and owner != tid:
            displaced = owner
            self._release(owner, reason="teacher_reassigned", now=now)
            other = self.tracks.get(owner)
            if other is not None:
                other.teacher_locked = False
                other.rejected_students.add(sid)
        if previous is not None and previous != sid:
            self._release(tid, reason="teacher_corrected", now=now)
            t.rejected_students.add(previous)
        source = SRC_TEACHER_CORRECTED if (previous is not None and previous != sid) else SRC_TEACHER_TAG
        t.rejected_students.discard(sid)
        t.status = CONFIRMED
        t.student_id = sid
        t.source = source if not (previous == sid and t.source in TEACHER_SOURCES) else t.source
        t.teacher_locked = True
        t.review_required = False
        t.confirmed_by = by
        t.confirmed_at = now
        t.contradict_count, t.contradict_since, t.stranger_since = 0, None, None
        t.candidate_id, t.candidate_count, t.candidate_since = None, 0, None
        t.conflict_with_track = None
        t.restored = False
        self._owner[sid] = tid
        self._recently_lost.pop(sid, None)
        self._cooldown_until.pop(sid, None)
        self.events.append(IdentityEvent("TEACHER_" + ("CORRECTED" if source == SRC_TEACHER_CORRECTED else "TAG"),
                                          tid, sid, now, {"previous_student": previous, "displaced_track": displaced}))
        return {"source": t.source, "previous_student": previous, "displaced_track": displaced}

    def restore_bind(self, track_id: int, student_id: int, now: float) -> None:
        """Continue a teacher binding (the camera pipeline / tracker lost its track) on ``track_id``,
        decided by the session coordinator on position continuity — never on face evidence of a
        student without consent. Like a teacher binding it is locked (face evidence can only ask
        for review), its provenance is AUTO_RECOVERED + ``restored``. Raises ValueError if the
        track already carries an identity or the student is bound elsewhere (one student, one track)."""
        tid, sid = int(track_id), int(student_id)
        t = self.tracks.get(tid)
        if t is None:
            raise KeyError("unknown track")
        if (t.status == CONFIRMED and t.student_id is not None) or sid in t.rejected_students:
            raise ValueError("track not available")
        owner = self._owner.get(sid)
        if owner is not None and owner != tid:
            raise ValueError("student bound elsewhere")
        t.status = CONFIRMED
        t.student_id = sid
        t.source = SRC_AUTO_RECOVERED
        t.restored = True
        t.teacher_locked = True
        t.review_required = False
        t.confirmed_by = None
        t.confirmed_at = now
        t.confidence, t.margin = 0.0, 0.0
        t.contradict_count, t.contradict_since, t.stranger_since = 0, None, None
        t.candidate_id, t.candidate_count, t.candidate_since = None, 0, None
        t.conflict_with_track = None
        self._owner[sid] = tid
        self._recently_lost.pop(sid, None)
        self._cooldown_until.pop(sid, None)
        self.events.append(IdentityEvent("RESTORED", tid, sid, now, {"method": "SEAT_CONTINUITY"}))

    def teacher_unbind(self, track_id: int, now: float) -> Optional[int]:
        """Teacher removes the identity of a track; that student is never auto-bound to it again."""
        t = self.tracks.get(int(track_id))
        if t is None or t.student_id is None:
            return None
        sid = t.student_id
        self._release(t.track_id, reason="teacher_removed", now=now)
        t.rejected_students.add(sid)
        t.teacher_locked = False
        t.review_required = False
        t.source = None
        t.status = UNKNOWN
        return sid

    def _release(self, track_id: int, reason: str, now: Optional[float]):
        t = self.tracks.get(track_id)
        if t is None or t.student_id is None:
            return
        sid = t.student_id
        if self._owner.get(sid) == track_id:
            del self._owner[sid]
        t.student_id = None
        if t.status == CONFIRMED:
            t.status = UNCERTAIN
        t.teacher_locked = False
        t.review_required = False
        t.restored = False
        t.contradict_count = 0
        self.events.append(IdentityEvent("RELEASED", track_id, sid, now if now is not None else 0.0,
                                          {"reason": reason}))

    def _age_out(self, seen: set, now: float):
        for tid in list(self.tracks):
            if tid in seen:
                continue
            t = self.tracks[tid]
            if t.last_seen is None or (now - t.last_seen) < self.config.track_lost_seconds:
                continue
            if t.student_id is not None:
                sid = t.student_id
                self._release(tid, reason="track_lost", now=now)
                self._recently_lost[sid] = now
                self.events.append(IdentityEvent("LOST", tid, sid, now))
            del self.tracks[tid]
        # Forget stale recovery / cooldown entries.
        for sid, ts in list(self._recently_lost.items()):
            if now - ts > self.config.recovery_window_seconds:
                del self._recently_lost[sid]
        for sid, ts in list(self._cooldown_until.items()):
            if now > ts:
                del self._cooldown_until[sid]
