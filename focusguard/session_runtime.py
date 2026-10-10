"""SessionRuntime: authoritative in-memory state of ONE running session.

A runtime is scoped by ``class_session_id`` (classroom camera / online class)
or by a personal ``session_id`` (self-study), and inside it by ``student_id``.
It owns, per student, exactly one StudentRuntimeState, one BehaviorAnalyzer
and is driven by the single shared FocusEngine.

Lifecycle
---------
* ``SessionRuntime(...)`` starts clean: every enrolled student is attached with
  NOT_YET attendance and no score. Nothing leaks from earlier sessions.
* ``process_frame(now, observations)`` advances time for every enrolled
  student. Students absent from ``observations`` are "not visible" at ``now``.
* ``heartbeat(now)`` marks the camera as stalled when no frame arrived for a
  while (e.g. the browser that was pulling /video_feed disconnected). The
  runtime keeps running; it just reports NO_CAMERA_SIGNAL and freezes scores.
* ``end(now)`` closes every open episode, writes final snapshots and per
  student summaries through the sink, and returns the summary.

Persistence goes through a ``sink`` object so the runtime stays testable:
``save_focus_event(dict)``, ``save_focus_snapshot(dict)`` and
``save_session_student(dict)``. Realtime notifications go through an optional
``notifier(event_name, payload)`` callback.
"""
import threading
from collections import deque
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional

from .behavior import ALERT_KINDS, AWAY, BehaviorAnalyzer, BehaviorEpisode, Observation
from .config import DEFAULT_CONFIG, PipelineConfig
from .focus_engine import FocusEngine
from . import runtime_state as rs

MODE_CLASSROOM = "classroom"   # one classroom camera, many students
MODE_ONLINE = "online"         # online class, each student has own camera
MODE_PERSONAL = "personal"     # self-study session of one student


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
                 source: str = "camera"):
        self.class_session_id = class_session_id
        self.class_id = class_id
        self.session_id = session_id
        self.mode = mode
        self.config = config
        self.sink = sink or NullSink()
        self.notifier = notifier
        self.snapshot_interval = snapshot_interval_seconds
        self.camera_stale_seconds = camera_stale_seconds
        self.source = source
        self.started_at = started_at
        self.ended_at: Optional[float] = None
        self.last_frame_at: Optional[float] = None
        self.camera_status = "WAITING"  # WAITING | ACTIVE | NO_SIGNAL
        self.engine = FocusEngine(config.focus)
        self.states: Dict[int, rs.StudentRuntimeState] = {}
        self.analyzers: Dict[int, BehaviorAnalyzer] = {}
        self._last_snapshot_at: Dict[int, float] = {}
        self._history: Dict[int, deque] = {}           # live chart, 1 sample/s, in memory only
        self._last_obs_at: Dict[int, float] = {}       # per-student camera freshness (online/personal)
        self.episodes: List[BehaviorEpisode] = []   # closed episodes, for summaries/evaluation
        self.logs: List[dict] = []
        self.lock = threading.RLock()
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
    def process_frame(self, now: float, observations: Dict[int, Observation]) -> List[dict]:
        """Advance every enrolled student to ``now``.

        ``observations`` maps canonical ``student_id`` to its observation for
        this frame; ids that are not enrolled in this session are ignored
        (class isolation). Returns realtime notifications produced.
        """
        with self.lock:
            if not self.active:
                return []
            self.last_frame_at = now
            self.camera_status = "ACTIVE"
            notes: List[dict] = []
            for sid, st in self.states.items():
                obs = observations.get(sid)
                if obs is None:
                    obs = Observation(timestamp=now, visible=False, source=self.source)
                notes.extend(self._apply(st, self.analyzers[sid], obs, now))
            return notes

    def ingest(self, student_id: int, obs: Observation) -> List[dict]:
        """Single-student variant used by personal / online-class cameras."""
        with self.lock:
            sid = int(student_id)
            if not self.active or sid not in self.states:
                return []
            self.last_frame_at = obs.timestamp
            self._last_obs_at[sid] = obs.timestamp
            self.camera_status = "ACTIVE"
            return self._apply(self.states[sid], self.analyzers[sid], obs, obs.timestamp)

    def _mark_no_signal(self, st: rs.StudentRuntimeState, last_real: float):
        # Close the scoring interval at the last real observation, then freeze.
        self.engine.advance(st, st.focus_state, last_real)
        if st.visibility_status not in (rs.VIS_NOT_SEEN, rs.VIS_NO_SIGNAL):
            st.visibility_status = rs.VIS_NO_SIGNAL
            st.focus_state = rs.FOCUS_UNKNOWN

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
        # 1) integrate the elapsed interval with the state that was in force
        prev_state = st.focus_state
        prev_vis = st.visibility_status
        self.engine.advance(st, prev_state, now)
        if prev_vis == rs.VIS_VISIBLE and st.last_seen_at is not None:
            st.visible_seconds += max(0.0, min(now - st.last_seen_at, self.config.focus.max_step_seconds))

        # 2) behaviour
        upd = analyzer.update(obs)
        st.focus_state = upd.focus_state
        st.visibility_status = upd.visibility_status
        st.not_visible_since = analyzer.not_visible_since

        # 3) identity / attendance
        if obs.visible:
            if st.first_seen_at is None:
                st.first_seen_at = now
                st.checkin_at = now
                late = (now - self.started_at) > self.config.attendance.late_after_seconds
                st.attendance_status = rs.ATT_LATE if late else rs.ATT_PRESENT
                notes.append(self._log("attendance", st, now,
                                       f"{st.display_name} điểm danh: {rs.VI_LABELS[st.attendance_status]}"))
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
            st.active_episodes[ep.type] = ep.to_dict()
            if ep.type == AWAY:
                st.away_count += 1
            if ep.type in ALERT_KINDS:
                notes.append(self._log("behavior_started", st, now,
                                       f"{st.display_name}: {rs.VI_LABELS.get(ep.type, ep.type)}",
                                       episode=ep.to_dict()))
        for ep in upd.ended:
            self._close_episode(st, ep)

        # 6) live history (1 Hz) and periodic persisted snapshot
        hist = self._history.setdefault(st.student_id, deque(maxlen=600))
        if st.focus_score is not None and (not hist or now - hist[-1][0] >= 1.0):
            hist.append((now, st.focus_score))
        last = self._last_snapshot_at.get(st.student_id)
        if st.focus_score is not None and (last is None or now - last >= self.snapshot_interval):
            self._last_snapshot_at[st.student_id] = now
            self._snapshot(st, now)
        return notes

    def _close_episode(self, st: rs.StudentRuntimeState, ep: BehaviorEpisode):
        st.active_episodes.pop(ep.type, None)
        if ep.type == AWAY and ep.duration is not None:
            st.away_seconds += ep.duration
        st.count_event(ep.type)
        self.episodes.append(ep)
        row = ep.to_dict()
        row.update({
            "class_session_id": self.class_session_id,
            "session_id": self.session_id,
            "class_id": self.class_id,
            "duration_seconds": row.pop("duration"),
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
                "drowsy": sum(1 for s in students if s["focus_state"] == rs.FOCUS_DROWSY),
                "head_away": sum(1 for s in students if s["focus_state"] == rs.FOCUS_HEAD_AWAY),
                "average_focus_score": (int(round(sum(measured) / len(measured))) if measured else None),
                "measured_students": len(measured),
            }
            return {
                "class_session_id": self.class_session_id,
                "class_id": self.class_id,
                "mode": self.mode,
                "active": self.active,
                "camera_status": self.camera_status,
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
                self.engine.advance(st, st.focus_state, end_at if st.visibility_status != rs.VIS_NO_SIGNAL
                                    else (st.last_update_at or end_at))
                for ep in self.analyzers[sid].close(end_at):
                    self._close_episode(st, ep)
                if st.focus_score is not None:
                    self._snapshot(st, end_at)
                if st.attendance_status == rs.ATT_NOT_YET:
                    st.attendance_status = rs.ATT_ABSENT
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
                }
                try:
                    self.sink.save_session_student(row)
                except Exception as exc:
                    self._persist_failed("save_session_student", exc, now)
                summaries.append(row)
            self.ended_at = now
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
