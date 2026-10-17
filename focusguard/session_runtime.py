"""SessionRuntime: authoritative in-memory state of ONE running session.

A runtime is scoped by ``class_session_id`` (classroom camera / online class)
or by a personal ``session_id`` (self-study), and inside it by ``student_id``.
It owns, per student, exactly one StudentRuntimeState, one BehaviorAnalyzer
and is driven by the single shared FocusEngine.

Lifecycle
---------
* ``SessionRuntime(...)`` starts clean: every enrolled student is attached with
  NOT_SEEN attendance and no score. Nothing leaks from earlier sessions.
* ``process_frame(now, observations)`` advances time for every enrolled
  student. Students absent from ``observations`` are "not visible" at ``now``.
* ``heartbeat(now)`` marks the camera as stalled when no frame arrived for a
  while (e.g. the browser that was pulling /video_feed disconnected). The
  runtime keeps running; it just reports NO_CAMERA_SIGNAL and freezes scores.
* ``end(now)`` closes every open episode, writes final snapshots and per
  student summaries through the sink, and returns the summary.

Persistence goes through a ``sink`` object so the runtime stays testable:
``save_focus_event(dict)``, ``save_focus_snapshot(dict)`` and
``save_session_student(dict)`` (optionally ``save_context_segment(dict)``).
Realtime notifications go through an optional ``notifier(event_name, payload)``
callback.

Activity context (BTC feedback, bounded change)
-----------------------------------------------
``set_activity_context(context, now)`` records what the class is doing
(LECTURE, WRITING, GROUP_DISCUSSION, ...), chosen by the teacher. Observed
behaviour is unchanged; ``focusguard.policies.BehaviorInterpretationPolicy``
maps (behaviour, context) to an interpretation whose penalty multiplier the
single FocusEngine applies. No context = legacy behaviour (multiplier 1.0).

Hooks
-----
``runtime.hooks`` holds optional observers (evidence recorder, evaluation
recorder). Each may implement ``on_frame(rt, now, observations)``,
``on_observation(rt, student_id, obs)``, ``on_episode_started(rt, state, ep)``,
``on_episode_closed(rt, state, ep, row)``, ``on_context_changed(rt, segment)``
and ``on_end(rt, now)``. Hook failures never break the camera loop.
"""
import functools
import os
import threading
import uuid
from collections import deque
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional

from .behavior import ALERT_KINDS, AWAY, HEAD_AWAY, BehaviorAnalyzer, BehaviorEpisode, Observation
from .config import DEFAULT_CONFIG, PipelineConfig
from .focus_engine import FocusEngine
from .policies.contexts import CONTEXT_LABELS_VI, normalize_context
from .policies.interpretation import POLICY_BEHAVIORS, BehaviorInterpretationPolicy, Interpretation
from . import runtime_state as rs


@functools.lru_cache(maxsize=1)
def default_policy() -> BehaviorInterpretationPolicy:
    from .policies.interpretation import load_policy
    return load_policy()

MODE_CLASSROOM = "classroom"   # one classroom camera, many students
MODE_ONLINE = "online"         # online class, each student has own camera
MODE_PERSONAL = "personal"     # self-study session of one student

# Classroom phases. INITIALIZING (Khởi tạo danh sách): the camera detects / tracks
# people and the teacher tags them; NO focus scoring, NO behaviour events, NO
# absence finalisation — only real sightings mark attendance. MONITORING: normal.
PHASE_INITIALIZING = "INITIALIZING"
PHASE_MONITORING = "MONITORING"


class NullSink:
    def save_focus_event(self, row):
        pass

    def save_focus_snapshot(self, row):
        pass

    def save_session_student(self, row):
        pass


class SessionRuntime:
    def __init__(self, *, enrolled: Iterable[dict], started_at: float,
                 class_session_id: Optional[int] = None, class_id: Optional[int] = None,
                 session_id: Optional[int] = None, mode: str = MODE_CLASSROOM,
                 config: PipelineConfig = DEFAULT_CONFIG, sink=None,
                 notifier: Optional[Callable[[str, dict], None]] = None,
                 snapshot_interval_seconds: float = 10.0,
                 camera_stale_seconds: float = 3.0,
                 source: str = "camera",
                 policy: Optional[BehaviorInterpretationPolicy] = None,
                 activity_context: Optional[str] = None, initializing: bool = False):
        self.class_session_id = class_session_id
        self.class_id = class_id
        self.session_id = session_id
        self.mode = mode
        self.config = config
        self.sink = sink or NullSink()
        self.notifier = notifier
        self.snapshot_interval = snapshot_interval_seconds
        if camera_stale_seconds == 3.0:
            env_stale = os.environ.get("FG_CAMERA_STALE_SECONDS")
            camera_stale_seconds = float(env_stale) if env_stale else 6.0
        self.camera_stale_seconds = camera_stale_seconds
        self.source = source
        self.started_at = started_at
        self.ended_at: Optional[float] = None
        self.last_frame_at: Optional[float] = None
        self.camera_status = "WAITING"  # WAITING | ACTIVE | NO_SIGNAL
        self.camera = None              # classroom camera metadata (id/name/type; never credentials)
        self.identity_store = None      # SessionIdentityStore (classroom): bindings owned by this session
        self.phase = PHASE_INITIALIZING if (initializing and mode == MODE_CLASSROOM) else PHASE_MONITORING
        # Lateness is waived only for the FIRST roster initialization (students are in the room
        # while the teacher tags them); a mid-class re-initialization does not waive it.
        self.monitoring_started = self.phase == PHASE_MONITORING
        self._clear_since: Optional[float] = None    # no unidentified person visible since
        self._clear_frames = 0
        self.unidentified_present = 0   # persons in frame whose identity is not resolved (last frame)
        self.attendance_finalized = False
        self.evidence: Dict[int, List[rs.AttendanceEvidence]] = {}     # attendance evidence ledger
        self.engine = FocusEngine(config.focus)
        self.states: Dict[int, rs.StudentRuntimeState] = {}
        self.analyzers: Dict[int, BehaviorAnalyzer] = {}
        self._last_snapshot_at: Dict[int, float] = {}
        self._history: Dict[int, deque] = {}           # live chart, 1 sample/s, in memory only
        self._last_obs_at: Dict[int, float] = {}       # per-student camera freshness (online/personal)
        self.episodes: List[BehaviorEpisode] = []   # closed episodes, for summaries/evaluation
        self.logs: List[dict] = []
        self.lock = threading.RLock()
        self.policy = policy or default_policy()
        self.activity_context: Optional[str] = normalize_context(activity_context)
        self.context_history: List[dict] = []
        if self.activity_context is not None:
            self.context_history.append({"context": self.activity_context, "started_at": started_at,
                                         "ended_at": None, "set_by": None})
        self._interp: Dict[int, Interpretation] = {}     # interpretation in force per student
        self._escalated: set = set()                      # episode_uids already escalated
        self.hooks: List[object] = []
        default_conn = rs.CONN_OFFLINE if mode in (MODE_ONLINE, MODE_PERSONAL) else rs.CONN_NOT_APPLICABLE
        for s in enrolled:
            sid = int(s["student_id"])
            self.states[sid] = rs.StudentRuntimeState(
                student_id=sid,
                display_name=s.get("display_name") or s.get("full_name") or f"#{sid}",
                class_id=s.get("class_id", class_id),
                roll=s.get("roll"),
                connection_status=default_conn,
            )
            self.analyzers[sid] = BehaviorAnalyzer(sid, config.behavior, source=source)

    # ----------------------------------------------------------------- status
    @property
    def active(self) -> bool:
        return self.ended_at is None

    def enrolled_ids(self):
        return list(self.states.keys())

    def set_connection(self, student_id: int, online: bool):
        with self.lock:
            st = self.states.get(int(student_id))
            if st is None or self.mode == MODE_CLASSROOM:
                return
            st.connection_status = rs.CONN_ONLINE if online else rs.CONN_OFFLINE

    # --------------------------------------------------------------- per frame
    def process_frame(self, now: float, observations: Dict[int, Observation], unidentified: int = 0) -> List[dict]:
        """Advance every enrolled student to ``now``.

        ``observations`` maps canonical ``student_id`` to its observation for
        this frame; ids that are not enrolled in this session are ignored
        (class isolation). ``unidentified``: persons visible in this frame whose
        identity is not resolved (counted, never attributed to a student).
        Returns realtime notifications produced.
        """
        with self.lock:
            if not self.active:
                return []
            self.last_frame_at = now
            self.camera_status = "ACTIVE"
            self.unidentified_present = max(0, int(unidentified or 0))
            notes: List[dict] = []
            if self.phase == PHASE_INITIALIZING:
                # Detect / track / tag only: real sightings mark attendance, nothing is scored.
                for sid, obs in observations.items():
                    st = self.states.get(sid)
                    if st is not None and obs.visible:
                        self._mark_seen(st, now, rs.ATT_SRC_AUTO_FACE, notes, **self._evidence_args(obs))
                        st.last_seen_at = now
                        st.identity_status = rs.ID_CONFIRMED
                        st.active_track_id = obs.track_id
                return notes
            for sid, st in self.states.items():
                obs = observations.get(sid)
                if obs is None:
                    obs = Observation(timestamp=now, visible=False, source=self.source)
                notes.extend(self._apply(st, self.analyzers[sid], obs, now))
            self._maybe_finalize_attendance(now, notes)
            self._emit("on_frame", self, now, observations)
            return notes

    # ------------------------------------------------------------ attendance
    # Attendance is DERIVED from an evidence ledger (``self.evidence``): every confirmed sighting
    # (face recognition, teacher tag, restored session identity) is one AttendanceEvidence entry per
    # identity binding, with its provenance. Status = the EARLIEST valid evidence: PRESENT, or LATE
    # when that sighting came after ``late_after_seconds`` (lateness is fixed when the evidence is
    # recorded, against the class start). A teacher correction revokes only the evidence proven to
    # depend on the wrong binding (``revoke_attendance_evidence``); an earlier independent check-in
    # stands exactly as it was. Nothing is deleted: revoked evidence keeps revoked_at / reason.
    # A teacher attendance override is locked and always wins.
    def _mark_seen(self, st: rs.StudentRuntimeState, now: float, source: str, notes=None, track_id=None,
                   binding_id=None, identity_source=None, first_seen_at=None):
        """A CONFIRMED sighting (face recognition, teacher tag, restored session identity)."""
        key = binding_id if binding_id is not None else f"track:{track_id}"
        same = [e for e in self.evidence.get(st.student_id, ()) if e.key == key]
        if binding_id is not None and any(e.revoked_at is not None for e in same):
            return              # a teacher said this binding was wrong (a frame in flight must not undo it)
        ev = next((e for e in same if e.revoked_at is None), None)
        if ev is not None:                       # the same binding seen again: one evidence entry per binding
            ev.last_seen_at = now
            return
        seen_at = now
        if first_seen_at is not None:            # the person (this track) was in the room from then on
            seen_at = min(now, max(float(first_seen_at), self.started_at))
        late = bool(st.finalized_absent) or (self.monitoring_started and
                                             (seen_at - self.started_at) > self.config.attendance.late_after_seconds)
        ev = rs.AttendanceEvidence(
            evidence_id=uuid.uuid4().hex, key=key, student_id=st.student_id,
            class_session_id=self.class_session_id, class_id=self.class_id, track_id=track_id,
            binding_id=binding_id, identity_source=identity_source, attendance_source=source,
            first_seen_at=seen_at, confirmed_at=now, last_seen_at=now, late=late)
        self.evidence.setdefault(st.student_id, []).append(ev)
        self._persist_evidence(ev)
        self._recompute_attendance(st, now, notes)

    def _persist_evidence(self, ev, fields=None):
        fn = getattr(self.sink, "update_attendance_evidence" if fields else "save_attendance_evidence", None)
        if fn is None or self.class_session_id is None:
            return
        try:
            fn(ev.evidence_id, fields) if fields else fn(ev.row())
        except Exception as exc:
            self._persist_failed("attendance_evidence", exc, ev.confirmed_at)

    def _recompute_attendance(self, st: rs.StudentRuntimeState, now: float, notes=None, reason=None):
        if st.attendance_locked:
            return
        before = st.attendance_status
        valid = [e for e in self.evidence.get(st.student_id, ()) if e.revoked_at is None]
        if valid:
            first = min(valid, key=lambda e: (e.first_seen_at, e.confirmed_at))
            st.attendance_status = rs.ATT_LATE if first.late else rs.ATT_PRESENT
            st.attendance_source = (rs.ATT_SRC_TEACHER_CONFIRMED
                                    if any(e.attendance_source == rs.ATT_SRC_TEACHER_CONFIRMED for e in valid)
                                    else rs.ATT_SRC_AUTO_FACE)
            st.checkin_at, st.first_seen_at, st.checkin_track_id = first.first_seen_at, first.first_seen_at, first.track_id
        else:
            st.checkin_at = st.first_seen_at = st.checkin_track_id = None
            if st.finalized_absent:
                st.attendance_status, st.attendance_source = rs.ATT_ABSENT, rs.ATT_SRC_AUTO_FINALIZED
            else:
                st.attendance_status, st.attendance_source = rs.ATT_NOT_SEEN, None
        if st.attendance_status != before:
            if reason is not None:
                msg = f"{st.display_name}: bỏ điểm danh dựa trên nhận diện sai ({rs.VI_LABELS[st.attendance_status]})"
                entry = self._log("attendance_reverted", st, now, msg)
            else:
                entry = self._log("attendance", st, now,
                                  f"{st.display_name} điểm danh: {rs.VI_LABELS[st.attendance_status]}")
            if notes is not None:
                notes.append(entry)

    def revoke_attendance_evidence(self, student_id: int, binding_ids, now: float, reason: str,
                                   by: Optional[int] = None) -> int:
        """A teacher said these bindings were WRONG for ``student_id``: the evidence that rested on
        them is revoked (kept, marked revoked) and attendance is re-derived from what remains. An
        earlier independent check-in is untouched; a teacher override stays locked."""
        ids = {str(b) for b in (binding_ids or ()) if b is not None}
        with self.lock:
            st = self.states.get(int(student_id))
            if st is None or not ids:
                return 0
            n = 0
            for e in self.evidence.get(st.student_id, ()):
                if e.revoked_at is None and e.binding_id is not None and str(e.binding_id) in ids:
                    e.revoked_at, e.revoked_reason, e.revoked_by = now, str(reason)[:60], by
                    self._persist_evidence(e, {"revoked_at": now, "revoked_reason": e.revoked_reason,
                                               "revoked_by": by})
                    n += 1
            if n:
                self._recompute_attendance(st, now, reason=reason)
            return n

    def attendance_evidence(self, student_id=None) -> List[dict]:
        with self.lock:
            out = []
            for sid, evs in self.evidence.items():
                if student_id is None or int(student_id) == sid:
                    out.extend(e.row() for e in evs)
            return out

    @staticmethod
    def _evidence_args(obs) -> dict:
        x = obs.extra or {}
        return {"track_id": obs.track_id, "binding_id": x.get("binding_id"),
                "identity_source": x.get("identity_source"), "first_seen_at": x.get("first_seen_at")}

    def _maybe_finalize_attendance(self, now: float, notes=None):
        after = self.config.attendance.finalize_after_seconds
        if (self.attendance_finalized or after is None or self.phase != PHASE_MONITORING or self.mode != MODE_CLASSROOM
                or now - self.started_at < after):
            return
        if self.unidentified_present:
            self._clear_since, self._clear_frames = None, 0
            return            # unidentified people in the room: the teacher resolves them first
        if self._clear_since is None:
            self._clear_since = now
        self._clear_frames += 1
        if now - self._clear_since < 5.0 or self._clear_frames < 10:
            return            # a few frames without unidentified people (detector misses) are not enough
        self.finalize_attendance(now, notes)

    def finalize_attendance(self, now: float, notes=None) -> int:
        """NOT_SEEN -> ABSENT (AUTO_FINALIZED) for every student without a teacher override."""
        with self.lock:
            n = 0
            for st in self.states.values():
                if st.attendance_status == rs.ATT_NOT_SEEN and not st.attendance_locked:
                    st.finalized_absent = True
                    st.attendance_status = rs.ATT_ABSENT
                    st.attendance_source = rs.ATT_SRC_AUTO_FINALIZED
                    n += 1
            self.attendance_finalized = True
            if n:
                entry = {"type": "attendance_finalized", "student_id": None, "time": now,
                         "message": f"Đã chốt điểm danh: {n} học sinh vắng mặt"}
                self.logs.append(entry)
                if notes is not None:
                    notes.append(entry)
            return n

    def teacher_set_attendance(self, student_id: int, status: str, now: float, by: Optional[int] = None) -> dict:
        """Teacher override (PRESENT / LATE / ABSENT): wins over automation for the rest of the session.
        Returns None when the session already ended or the student is not part of it (the caller
        then corrects the stored session record instead)."""
        if status not in (rs.ATT_PRESENT, rs.ATT_LATE, rs.ATT_ABSENT):
            raise ValueError("invalid attendance status")
        with self.lock:
            st = self.states.get(int(student_id))
            if st is None or not self.active:
                return None
            st.attendance_status = status
            st.attendance_source = rs.ATT_SRC_TEACHER_CORRECTED
            st.attendance_locked = True
            if status != rs.ATT_ABSENT and st.checkin_at is None:
                st.checkin_at = now
            self._log("attendance_corrected", st, now,
                      f"{st.display_name}: giáo viên sửa điểm danh thành {rs.VI_LABELS[status]}")
            return st.to_public_dict(now=now, class_started_at=self.started_at)

    def teacher_identified(self, student_id: int, now: float, source: str, track_id=None, confidence=None,
                           binding_id=None, first_seen_at=None):
        """The teacher tagged / corrected this student on a person track (session identity)."""
        with self.lock:
            st = self.states.get(int(student_id))
            if st is None:
                return
            st.identity_source = source
            st.identity_review = False
            st.identity_status = rs.ID_CONFIRMED
            st.active_track_id = track_id
            if confidence is not None:
                st.identity_confidence = confidence
            st.last_seen_at = now
            self._mark_seen(st, now, rs.ATT_SRC_TEACHER_CONFIRMED, track_id=track_id, binding_id=binding_id,
                            identity_source=source, first_seen_at=first_seen_at)

    def identity_released(self, student_id: int):
        with self.lock:
            st = self.states.get(int(student_id))
            if st is not None and st.identity_status == rs.ID_CONFIRMED:
                st.identity_status = rs.ID_LOST
                st.active_track_id = None

    def set_identity_meta(self, student_id: int, source=None, review=None):
        with self.lock:
            st = self.states.get(int(student_id))
            if st is None:
                return
            if source is not None:
                st.identity_source = source
            if review is not None:
                st.identity_review = bool(review)

    def begin_monitoring(self, now: float):
        """INITIALIZING -> MONITORING ("Hoàn tất danh sách / Bắt đầu buổi học")."""
        with self.lock:
            if self.phase == PHASE_MONITORING:
                return False
            self.phase = PHASE_MONITORING
            self.monitoring_started = True
            self.logs.append({"type": "phase", "student_id": None, "time": now,
                              "message": "Hoàn tất danh sách: bắt đầu theo dõi buổi học"})
            return True

    def begin_initialization(self, now: float):
        """MONITORING -> INITIALIZING (re-tag students mid-class): scoring pauses, nothing is penalised."""
        with self.lock:
            if self.phase == PHASE_INITIALIZING or self.mode != MODE_CLASSROOM:
                return False
            for st in self.states.values():
                if st.visibility_status != rs.VIS_NO_SIGNAL:
                    self._advance(st, now)
                for ep in self.analyzers[st.student_id].suspend(now):
                    self._close_episode(st, ep)
                st.focus_state = rs.FOCUS_UNKNOWN
            self.phase = PHASE_INITIALIZING
            self._clear_since, self._clear_frames = None, 0
            self.logs.append({"type": "phase", "student_id": None, "time": now,
                              "message": "Khởi tạo danh sách: tạm dừng chấm điểm"})
            return True

    def ingest(self, student_id: int, obs: Observation) -> List[dict]:
        """Single-student variant used by personal / online-class cameras."""
        with self.lock:
            sid = int(student_id)
            if not self.active or sid not in self.states:
                return []
            self.last_frame_at = obs.timestamp
            self._last_obs_at[sid] = obs.timestamp
            self.camera_status = "ACTIVE"
            notes = self._apply(self.states[sid], self.analyzers[sid], obs, obs.timestamp)
            self._emit("on_observation", self, sid, obs)
            return notes

    def _mark_no_signal(self, st: rs.StudentRuntimeState, last_real: float):
        # Close the scoring interval at the last real observation, then freeze.
        # No frame is not "not visible": open episodes end at the last real frame and
        # the AWAY timer restarts only when frames resume (BehaviorAnalyzer.suspend).
        self._clear_since, self._clear_frames = None, 0     # no frames: nothing was "clear"
        if st.visibility_status == rs.VIS_NO_SIGNAL:
            return
        self.engine.advance(st, st.focus_state, last_real)
        for ep in self.analyzers[st.student_id].suspend(last_real):
            self._close_episode(st, ep)
        if st.visibility_status != rs.VIS_NOT_SEEN:
            st.visibility_status = rs.VIS_NO_SIGNAL
            st.focus_state = rs.FOCUS_UNKNOWN
        st.not_visible_since = None

    def heartbeat(self, now: float) -> str:
        with self.lock:
            if self.mode == MODE_CLASSROOM:
                # One shared camera: staleness is global.
                if self.last_frame_at is not None and now - self.last_frame_at > self.camera_stale_seconds:
                    if self.camera_status != "NO_SIGNAL":
                        self.camera_status = "NO_SIGNAL"
                        for st in self.states.values():
                            self._mark_no_signal(st, self.last_frame_at)
                return self.camera_status
            # Online / personal: every student has their own camera, so a
            # stalled camera only affects that student.
            fresh = False
            for sid, last in self._last_obs_at.items():
                if now - last > self.camera_stale_seconds:
                    self._mark_no_signal(self.states[sid], last)
                else:
                    fresh = True
            if self._last_obs_at:
                self.camera_status = "ACTIVE" if fresh else "NO_SIGNAL"
            return self.camera_status

    def _apply(self, st: rs.StudentRuntimeState, analyzer: BehaviorAnalyzer,
               obs: Observation, now: float) -> List[dict]:
        notes: List[dict] = []
        # 1) integrate the elapsed interval with the state (and its context
        #    interpretation) that was in force
        prev_state = st.focus_state
        prev_vis = st.visibility_status
        self._advance(st, now)
        if prev_vis == rs.VIS_VISIBLE and st.last_seen_at is not None:
            st.visible_seconds += max(0.0, min(now - st.last_seen_at, self.config.focus.max_step_seconds))

        # 2) behaviour
        upd = analyzer.update(obs)
        st.focus_state = upd.focus_state
        st.visibility_status = upd.visibility_status
        st.not_visible_since = analyzer.not_visible_since

        # 3) identity / attendance
        if obs.visible:
            self._mark_seen(st, now, rs.ATT_SRC_AUTO_FACE, notes, **self._evidence_args(obs))
            st.last_seen_at = now
            st.identity_status = rs.ID_CONFIRMED
            st.active_track_id = obs.track_id
            if obs.identity_confidence is not None:
                st.identity_confidence = obs.identity_confidence
            if obs.seat is not None:
                st.seat_row, st.seat_col = obs.seat
        else:
            st.active_track_id = None
            if st.identity_status == rs.ID_CONFIRMED:
                st.identity_status = rs.ID_LOST

        # 4) scoring starts at the first real measurement
        self.engine.begin(st, st.focus_state, now)

        # 5) episodes
        for ep in upd.started:
            ep.metadata.setdefault("identity_confidence", st.identity_confidence)
            ep.metadata.setdefault("episode_uid", uuid.uuid4().hex)
            ep.metadata["activity_context"] = self.activity_context
            start_interp = self._interpret_episode(ep, analyzer, now)
            ep.metadata["interpretation_at_start"] = start_interp.interpreted_state
            st.active_episodes[ep.type] = ep.to_dict()
            st.active_episodes[ep.type]["interpretation"] = start_interp.to_dict()
            if ep.type == AWAY:
                st.away_count += 1
            if ep.type in ALERT_KINDS:
                label = rs.VI_LABELS.get(ep.type, ep.type)
                if not start_interp.alert and self.activity_context:
                    how = {"REDUCED_IN_CONTEXT": "giảm mức theo hoạt động",
                           "AMBIGUOUS_IN_CONTEXT": "chưa rõ trong hoạt động"}.get(start_interp.interpreted_state,
                                                                                "phù hợp hoạt động")
                    label += f" ({how}: {CONTEXT_LABELS_VI.get(self.activity_context)})"
                notes.append(self._log("behavior_started", st, now, f"{st.display_name}: {label}",
                                       episode=ep.to_dict(), interpretation=start_interp.to_dict(),
                                       alert=start_interp.alert))
            self._emit("on_episode_started", self, st, ep)
        for ep in upd.ended:
            self._close_episode(st, ep)

        # 5b) context interpretation of what is in force from now on
        self._reinterpret(st, analyzer, now, notes)

        # 6) live history (1 Hz) and periodic persisted snapshot
        hist = self._history.setdefault(st.student_id, deque(maxlen=600))
        if st.focus_score is not None and (not hist or now - hist[-1][0] >= 1.0):
            hist.append((now, st.focus_score))
        last = self._last_snapshot_at.get(st.student_id)
        if st.focus_score is not None and (last is None or now - last >= self.snapshot_interval):
            self._last_snapshot_at[st.student_id] = now
            self._snapshot(st, now)
        return notes

    # ------------------------------------------------------ interpretation
    def _advance(self, st: rs.StudentRuntimeState, now: float):
        interp = self._interp.get(st.student_id)
        if interp is not None and interp.observed_behavior == st.focus_state:
            state = rs.FOCUS_FOCUSED if interp.treat_as_focused else st.focus_state
            return self.engine.advance(st, state, now, penalty_multiplier=interp.penalty_multiplier)
        return self.engine.advance(st, st.focus_state, now)

    def _episode_direction(self, ep_type, analyzer: BehaviorAnalyzer):
        return analyzer.head_direction() if ep_type == HEAD_AWAY else None

    def _interpret_episode(self, ep: BehaviorEpisode, analyzer: BehaviorAnalyzer, now: float,
                           context="__current__") -> Interpretation:
        ctx = self.activity_context if context == "__current__" else context
        direction = ep.metadata.get("direction") or self._episode_direction(ep.type, analyzer)
        return self.policy.interpret(ep.type, ctx, direction, max(0.0, now - ep.start_time))

    def _reinterpret(self, st: rs.StudentRuntimeState, analyzer: BehaviorAnalyzer, now: float, notes=None):
        sid = st.student_id
        active = {ep.type: ep for ep in analyzer.active_episodes()}
        for kind, ep in active.items():
            interp = self._interpret_episode(ep, analyzer, now)
            row = st.active_episodes.get(kind)
            if row is not None:
                row["interpretation"] = interp.to_dict()
            uid = ep.metadata.get("episode_uid")
            if interp.escalated and uid and uid not in self._escalated:
                self._escalated.add(uid)
                ep.metadata["escalated_at"] = now
                if notes is not None and kind in ALERT_KINDS:
                    notes.append(self._log("behavior_escalated", st, now,
                                           f"{st.display_name}: {rs.VI_LABELS.get(kind, kind)} kéo dài, "
                                           "cần giáo viên xem lại", interpretation=interp.to_dict(), alert=True))
        behavior = st.focus_state if st.focus_state in POLICY_BEHAVIORS else None
        if behavior is None:
            self._interp.pop(sid, None)
            st.interpretation = None
            return
        ep = active.get(behavior)
        interp = (self._interpret_episode(ep, analyzer, now) if ep is not None
                  else self.policy.interpret(behavior, self.activity_context))
        self._interp[sid] = interp
        st.interpretation = interp.to_dict()

    def set_activity_context(self, context, now: float, set_by: Optional[int] = None) -> dict:
        """Switch the teacher-chosen activity context at ``now``.

        Scores are first integrated up to ``now`` under the old context so a
        switch never re-scores the past. Returns the new segment."""
        new = normalize_context(context)
        with self.lock:
            if not self.active:
                raise RuntimeError("session already ended")
            if new == self.activity_context:
                return self.context_history[-1] if self.context_history else {"context": None}
            for st in self.states.values():
                if st.visibility_status != rs.VIS_NO_SIGNAL:
                    self._advance(st, now)
            self._close_context_segment(now)
            self.activity_context = new
            seg = {"context": new, "started_at": now, "ended_at": None, "set_by": set_by}
            if new is not None:
                self.context_history.append(seg)
            for sid, st in self.states.items():
                self._reinterpret(st, self.analyzers[sid], now)
            label = CONTEXT_LABELS_VI.get(new, "Không chọn hoạt động")
            self.logs.append({"type": "context_changed", "student_id": None, "time": now, "context": new,
                              "message": f"Hoạt động lớp: {label}"})
            if self.notifier:
                try:
                    self.notifier("context_changed", self.logs[-1])
                except Exception:
                    pass
            self._emit("on_context_changed", self, seg)
            return seg

    def _close_context_segment(self, now: float):
        if not self.context_history or self.context_history[-1]["ended_at"] is not None:
            return
        seg = self.context_history[-1]
        seg["ended_at"] = now
        save = getattr(self.sink, "save_context_segment", None)
        if save is not None:
            try:
                save(dict(seg, class_session_id=self.class_session_id, class_id=self.class_id,
                          session_id=self.session_id))
            except Exception as exc:
                self._persist_failed("save_context_segment", exc, now)

    def contexts_between(self, start: float, end: Optional[float]) -> List[str]:
        end = end if end is not None else start
        out = []
        for seg in self.context_history:
            s_end = seg["ended_at"] if seg["ended_at"] is not None else float("inf")
            if seg["started_at"] <= end and s_end >= start and seg["context"] not in out:
                out.append(seg["context"])
        return out

    def _emit(self, name: str, *args):
        for hook in list(self.hooks):
            fn = getattr(hook, name, None)
            if fn is None:
                continue
            try:
                fn(*args)
            except Exception as exc:   # observers must never stop scoring
                print(f"[RUNTIME] hook {type(hook).__name__}.{name} failed: {exc!r}")

    def _close_episode(self, st: rs.StudentRuntimeState, ep: BehaviorEpisode):
        st.active_episodes.pop(ep.type, None)
        if ep.type == AWAY and ep.duration is not None:
            st.away_seconds += ep.duration
        st.count_event(ep.type)
        self.episodes.append(ep)
        start_ctx = ep.metadata.get("activity_context", self.activity_context)
        final = self.policy.interpret(ep.type, start_ctx, ep.metadata.get("direction"),
                                      ep.duration if ep.duration is not None else 0.0)
        ep.metadata["contexts_during"] = self.contexts_between(ep.start_time, ep.end_time)
        ep.metadata["interpretation"] = final.to_dict()
        row = ep.to_dict()
        row.update({
            "class_session_id": self.class_session_id,
            "session_id": self.session_id,
            "class_id": self.class_id,
            "duration_seconds": row.pop("duration"),
            # Observed type stays in "type"; the context reading is stored next to it.
            "activity_context": start_ctx,
            "interpreted_state": final.interpreted_state,
            "penalty_multiplier": final.penalty_multiplier,
            "policy_version": final.policy_version,
            "episode_uid": ep.metadata.get("episode_uid"),
        })
        if ep.type in ALERT_KINDS and ep.end_time is not None:
            # Finished episodes carry the measured duration and confidence.
            self._log("behavior_ended", st, ep.end_time,
                      f"{st.display_name}: {rs.VI_LABELS.get(ep.type, ep.type)} đã kết thúc",
                      episode=ep.to_dict())
        try:
            self.sink.save_focus_event(row)
        except Exception as exc:  # persistence must never crash the camera loop
            self._persist_failed("save_focus_event", exc, ep.end_time)
        self._emit("on_episode_closed", self, st, ep, row)

    def _persist_failed(self, what: str, exc: Exception, when):
        # Technical detail stays in the server log; the teacher only sees a plain message.
        print(f"[RUNTIME] {what} failed: {exc!r}")
        self.logs.append({"type": "error", "time": when,
                          "message": "Không lưu được một phần dữ liệu buổi học. Vui lòng báo quản trị viên."})

    def _snapshot(self, st: rs.StudentRuntimeState, now: float):
        row = {
            "student_id": st.student_id,
            "class_session_id": self.class_session_id,
            "session_id": self.session_id,
            "class_id": self.class_id,
            "timestamp": now,
            "focus_score": None if st.focus_score is None else round(st.focus_score, 2),
            "focus_state": st.focus_state,
            "visibility_status": st.visibility_status,
            "attendance_status": st.attendance_status,
            "identity_confidence": st.identity_confidence,
        }
        try:
            self.sink.save_focus_snapshot(row)
        except Exception as exc:
            self._persist_failed("save_focus_snapshot", exc, now)

    def _log(self, kind: str, st: rs.StudentRuntimeState, now: float, message: str, **extra) -> dict:
        entry = {"type": kind, "student_id": st.student_id, "time": now, "message": message}
        entry.update(extra)
        self.logs.append(entry)
        if len(self.logs) > 200:
            del self.logs[: len(self.logs) - 200]
        if self.notifier:
            try:
                self.notifier(kind, entry)
            except Exception:
                pass
        return entry

    # ------------------------------------------------------------------ views
    def history(self, student_id: int, limit: int = 60) -> List[dict]:
        with self.lock:
            hist = list(self._history.get(int(student_id), ()))[-limit:]
            return [{"time": datetime.fromtimestamp(ts).strftime("%H:%M:%S"), "score": int(round(sc))}
                    for ts, sc in hist]

    def student_view(self, student_id: int, now: Optional[float] = None) -> Optional[dict]:
        with self.lock:
            st = self.states.get(int(student_id))
            if st is None:
                return None
            return st.to_public_dict(now=now, class_started_at=self.started_at)

    def class_view(self, now: Optional[float] = None) -> dict:
        with self.lock:
            students = [st.to_public_dict(now=now, class_started_at=self.started_at)
                        for st in self.states.values()]
            measured = [s["focus_score"] for s in students if s["focus_score"] is not None
                        and s["visibility_status"] == rs.VIS_VISIBLE]
            present = sum(1 for s in students if s["attendance_status"] in (rs.ATT_PRESENT, rs.ATT_LATE))
            stats = {
                "enrolled": len(students),
                "present": present,
                "visible": sum(1 for s in students if s["visibility_status"] == rs.VIS_VISIBLE),
                "away": sum(1 for s in students if s["visibility_status"] == rs.VIS_AWAY),
                "phone": sum(1 for s in students if s["focus_state"] == rs.FOCUS_PHONE),
                # Roster-based attendance view: confirmed vs unidentified vs not yet seen.
                "confirmed": sum(1 for s in students if s["identity_status"] == rs.ID_CONFIRMED),
                "unidentified_present": self.unidentified_present,
                "not_seen": sum(1 for s in students if s["attendance_status"] == rs.ATT_NOT_SEEN),
                "identity_review": sum(1 for s in students if s.get("identity_review")),
                "drowsy": sum(1 for s in students if s["focus_state"] == rs.FOCUS_DROWSY),
                "head_away": sum(1 for s in students if s["focus_state"] == rs.FOCUS_HEAD_AWAY),
                "average_focus_score": (int(round(sum(measured) / len(measured))) if measured else None),
                "measured_students": len(measured),
            }
            return {
                "class_session_id": self.class_session_id,
                "class_id": self.class_id,
                "activity_context": self.activity_context,
                "activity_context_label": CONTEXT_LABELS_VI.get(self.activity_context),
                "context_history": [dict(s) for s in self.context_history],
                "policy_version": self.policy.version,
                "mode": self.mode,
                "active": self.active,
                "camera_status": self.camera_status,
                "phase": self.phase,
                "attendance_finalized": self.attendance_finalized,
                "started_at": self.started_at,
                "elapsed_seconds": int((now if now is not None else self.started_at) - self.started_at),
                "server_time": now,
                "students": students,
                "statistics": stats,
                "logs": list(self.logs[-50:]),
            }

    # --------------------------------------------------------------------- end
    def end(self, now: float) -> List[dict]:
        with self.lock:
            if not self.active:
                return []
            end_at = self.last_frame_at if (self.camera_status == "NO_SIGNAL" and self.last_frame_at) else now
            summaries = []
            for sid, st in self.states.items():
                if st.visibility_status != rs.VIS_NO_SIGNAL:
                    self._advance(st, end_at)
                else:
                    self.engine.advance(st, st.focus_state, st.last_update_at or end_at)
                for ep in self.analyzers[sid].close(end_at):
                    self._close_episode(st, ep)
                if st.focus_score is not None:
                    self._snapshot(st, end_at)
                if st.attendance_status == rs.ATT_NOT_SEEN:
                    st.finalized_absent = True
                    st.attendance_status = rs.ATT_ABSENT
                    st.attendance_source = rs.ATT_SRC_AUTO_FINALIZED
                avg = st.average_focus_score
                row = {
                    "class_session_id": self.class_session_id,
                    "session_id": self.session_id,
                    "class_id": self.class_id,
                    "student_id": sid,
                    "attendance_status": st.attendance_status,
                    "first_seen_at": st.first_seen_at,
                    "last_seen_at": st.last_seen_at,
                    "visible_seconds": round(st.visible_seconds, 2),
                    "away_count": st.away_count,
                    "away_seconds": round(st.away_seconds, 2),
                    "final_focus_score": None if st.focus_score is None else round(st.focus_score, 2),
                    "avg_focus_score": None if avg is None else round(avg, 2),
                    "measured_seconds": round(st.measured_seconds, 2),
                    "identity_confidence": st.identity_confidence,
                    "event_counts": dict(st.event_counts),
                    "attendance_source": st.attendance_source,
                }
                try:
                    self.sink.save_session_student(row)
                except Exception as exc:
                    self._persist_failed("save_session_student", exc, now)
                summaries.append(row)
            self._close_context_segment(end_at)
            for evs in self.evidence.values():           # final last-seen times of the evidence ledger
                for e in evs:
                    self._persist_evidence(e, {"last_seen_at": e.last_seen_at})
            self.ended_at = now
            self._emit("on_end", self, now)
            return summaries


class RuntimeRegistry:
    """Holds at most one active runtime per class and per personal session.

    Starting a class always builds a *new* runtime; a previous runtime for the
    same class is ended first so no state can leak between sessions.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._classes: Dict[int, SessionRuntime] = {}
        self._personal: Dict[int, SessionRuntime] = {}   # student_id -> runtime

    def start_class(self, class_id: int, runtime: SessionRuntime, now: float) -> SessionRuntime:
        with self._lock:
            old = self._classes.get(int(class_id))
            if old is not None and old.active:
                old.end(now)
            self._classes[int(class_id)] = runtime
            return runtime

    def end_class(self, class_id: int, now: float) -> Optional[List[dict]]:
        with self._lock:
            rt = self._classes.pop(int(class_id), None)
            if rt is None:
                return None
            return rt.end(now)

    def class_runtime(self, class_id) -> Optional[SessionRuntime]:
        if class_id is None:
            return None
        with self._lock:
            rt = self._classes.get(int(class_id))
            return rt if rt is not None and rt.active else None

    def active_classes(self) -> Dict[int, SessionRuntime]:
        with self._lock:
            return {cid: rt for cid, rt in self._classes.items() if rt.active}

    def start_personal(self, student_id: int, runtime: SessionRuntime, now: float) -> SessionRuntime:
        with self._lock:
            old = self._personal.get(int(student_id))
            if old is not None and old.active:
                old.end(now)
            self._personal[int(student_id)] = runtime
            return runtime

    def end_personal(self, student_id: int, now: float) -> Optional[List[dict]]:
        with self._lock:
            rt = self._personal.pop(int(student_id), None)
            if rt is None:
                return None
            return rt.end(now)

    def personal_runtime(self, student_id) -> Optional[SessionRuntime]:
        if student_id is None:
            return None
        with self._lock:
            rt = self._personal.get(int(student_id))
            return rt if rt is not None and rt.active else None
