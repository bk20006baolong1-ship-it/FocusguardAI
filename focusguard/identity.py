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

    def snapshot(self) -> dict:
        return {
            "track_id": self.track_id,
            "status": self.status,
            "student_id": self.student_id,
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
        ids, rows = [], []
        for sid, emb in gallery.items():
            vec = _normalize(emb)
            if vec is None:
                continue
            if rows and vec.shape != rows[0].shape:
                continue
            ids.append(int(sid))
            rows.append(vec)
        # Swap ids+matrix together so a concurrent match() never sees a mix.
        self._gallery = (ids, np.vstack(rows) if rows else None)
        # Students removed from the gallery lose any binding.
        for sid in list(self._owner):
            if sid not in self._gallery[0]:
                self._release(self._owner[sid], reason="gallery_removed", now=None)

    @property
    def gallery_size(self) -> int:
        return len(self._gallery[0])

    def match(self, embedding) -> MatchResult:
        vec = _normalize(embedding)
        if vec is None:
            return MatchResult(NO_FACE)
        gallery_ids, gallery_mat = self._gallery
        if gallery_mat is None or vec.shape[0] != gallery_mat.shape[1]:
            return MatchResult(NO_MATCH)
        sims = gallery_mat @ vec
        order = np.argsort(-sims)
        best_i = int(order[0])
        best_sim = float(sims[best_i])
        second_sim = float(sims[int(order[1])]) if len(order) > 1 else -1.0
        second_id = gallery_ids[int(order[1])] if len(order) > 1 else None
        res = MatchResult(NO_MATCH, gallery_ids[best_i], best_sim, second_id, max(second_sim, 0.0))
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
                and now - (t.contradict_since or now) >= self.config.unlock_min_seconds):
            released = t.student_id
            self._release(t.track_id, reason="contradicted", now=now)
            self._cooldown_until[released] = now + self.config.conflict_cooldown_seconds
            t.status = UNCERTAIN
            t.candidate_id = accepted
            t.candidate_count = 1
            t.candidate_since = now

    def _observe_unlocked(self, t: TrackIdentity, m: MatchResult, now: float):
        accepted = m.accepted_id
        t.confidence = m.best_similarity if m.outcome != NO_FACE else t.confidence
        t.margin = m.margin if m.outcome != NO_FACE else t.margin

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
        if t.candidate_count < needed or (now - (t.candidate_since or now)) < min_secs:
            return

        owner = self._owner.get(accepted)
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
        self._owner[student_id] = t.track_id
        self._recently_lost.pop(student_id, None)
        self._cooldown_until.pop(student_id, None)
        self.events.append(IdentityEvent("RECOVERED" if recovered else "CONFIRMED",
                                          t.track_id, student_id, now,
                                          {"similarity": round(m.best_similarity, 4),
                                           "margin": round(m.margin, 4)}))

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
