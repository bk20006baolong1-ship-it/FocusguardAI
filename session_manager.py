"""Application service: session lifecycle + analytics on top of the repository.

This replaces the previous mock-driven SessionManager. There are no mock
students, no random drift, no fabricated emotions or predictions. Live state
comes only from SessionRuntime (fed by the camera pipelines) and historical
numbers only from persisted rows via ``focusguard.analytics``.
"""
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from focusguard import analytics
from focusguard.behavior import Observation
from focusguard.config import PipelineConfig
from focusguard.session_runtime import (MODE_CLASSROOM, MODE_ONLINE, MODE_PERSONAL,
                                        RuntimeRegistry, SessionRuntime)
from focusguard import runtime_state as rs


def _fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class RepositorySink:
    """Adapter so SessionRuntime can persist through the repository."""

    def __init__(self, repo):
        self.repo = repo

    def save_focus_event(self, row):
        self.repo.create_focus_event(row)

    def save_focus_snapshot(self, row):
        self.repo.create_focus_snapshot(row)

    def save_session_student(self, row):
        self.repo.save_session_student(row)


class SessionManager:
    def __init__(self, repo=None, config: Optional[PipelineConfig] = None,
                 notifier: Optional[Callable[[str, dict], None]] = None,
                 seed_demo_accounts: bool = False, clock: Callable[[], float] = time.time):
        if repo is None:
            from repository import get_repository
            repo = get_repository()
        self.repo = repo
        self.repo.init_db(seed_demo_accounts=seed_demo_accounts)
        self.config = config or PipelineConfig.from_env()
        self.notifier = notifier
        self.clock = clock
        self.registry = RuntimeRegistry()
        self.sink = RepositorySink(repo)
        self._lock = threading.RLock()
        # student_id -> {"session_id", "started_at", "class_id", "linked_class"}
        self._personal_sessions = {}

    # ================================================================ classes
    def start_class(self, class_id: int, mode: str = MODE_CLASSROOM, started_by: Optional[int] = None) -> SessionRuntime:
        if mode not in (MODE_CLASSROOM, MODE_ONLINE):
            raise ValueError("mode must be 'classroom' or 'online'")
        with self._lock:
            now = self.clock()
            existing = self.registry.class_runtime(class_id)
            if existing is not None:
                self.end_class(class_id)
            enrolled = self.repo.get_students_in_class(class_id)
            cs_id = self.repo.create_class_session(int(class_id), _fmt(now), mode=mode, started_by=started_by)
            runtime = SessionRuntime(
                enrolled=enrolled, started_at=now, class_session_id=cs_id, class_id=int(class_id),
                mode=mode, config=self.config, sink=self.sink, notifier=self._notify_for(class_id),
                source="classroom_camera" if mode == MODE_CLASSROOM else "student_camera",
            )
            self.registry.start_class(class_id, runtime, now)
            if mode == MODE_ONLINE:
                self._link_running_personal_sessions(runtime, now)
            return runtime

    def _link_running_personal_sessions(self, runtime: SessionRuntime, now: float):
        """Students already studying alone join the online class: their personal
        runtime is closed (its episodes persist under the personal session) and
        from now on the class runtime is the ONE runtime scoring them."""
        for sid, info in self._personal_sessions.items():
            if info.get("linked_class") is not None or sid not in runtime.states:
                continue
            self.registry.end_personal(sid, now)
            info["linked_class"] = runtime.class_id
            runtime.set_connection(sid, True)

    def end_class(self, class_id: int):
        with self._lock:
            now = self.clock()
            rt = self.registry.class_runtime(class_id)
            if rt is None:
                return None
            # Online class: students' personal sessions that are linked to it end too.
            for sid, info in list(self._personal_sessions.items()):
                if info.get("linked_class") == int(class_id):
                    self._finish_personal(sid, now, rt)
            summaries = self.registry.end_class(class_id, now)
            self.repo.end_class_session(rt.class_session_id, _fmt(now))
            return {"class_session_id": rt.class_session_id, "students": summaries}

    def class_runtime(self, class_id) -> Optional[SessionRuntime]:
        return self.registry.class_runtime(class_id)

    def classroom_runtime(self, class_id, class_session_id=None) -> Optional[SessionRuntime]:
        """Runtime a classroom camera may feed: classroom mode only and, when
        given, the exact class session the camera was started for."""
        rt = self.registry.class_runtime(class_id)
        if rt is None or rt.mode != MODE_CLASSROOM:
            return None
        if class_session_id is not None and rt.class_session_id != class_session_id:
            return None
        return rt

    def class_live_view(self, class_id) -> dict:
        rt = self.registry.class_runtime(class_id)
        now = self.clock()
        if rt is not None:
            rt.heartbeat(now)
            return rt.class_view(now)
        roster = [rs.StudentRuntimeState(student_id=s["student_id"], display_name=s["display_name"],
                                         class_id=s["class_id"], roll=s.get("roll")).to_public_dict()
                  for s in self.repo.get_students_in_class(class_id)]
        return {"class_session_id": None, "class_id": int(class_id), "mode": None, "active": False,
                "camera_status": "INACTIVE", "started_at": None, "elapsed_seconds": 0, "students": roster,
                "statistics": {"enrolled": len(roster), "present": 0, "visible": 0, "away": 0, "phone": 0,
                               "drowsy": 0, "head_away": 0, "average_focus_score": None,
                               "measured_students": 0},
                "logs": []}

    def _notify_for(self, class_id):
        def notify(kind, payload):
            if self.notifier:
                self.notifier(kind, dict(payload, class_id=int(class_id)))
        return notify

    # ======================================================= personal sessions
    def start_personal_session(self, student_id: int, subject: Optional[str] = None) -> int:
        with self._lock:
            now = self.clock()
            if student_id in self._personal_sessions:
                # Idempotent: a double click / second tab must not create a second session.
                return self._personal_sessions[student_id]["session_id"]
            user = self.repo.get_user_by_id(student_id) or {}
            class_id = user.get("class_id")
            class_rt = self.registry.class_runtime(class_id)
            linked = class_rt is not None and class_rt.mode == MODE_ONLINE and student_id in class_rt.states
            session_id = self.repo.create_session(
                student_id, _fmt(now), subject=subject,
                class_session_id=class_rt.class_session_id if linked else None,
                source="online_class" if linked else "personal_camera")
            if linked:
                class_rt.set_connection(student_id, True)
            else:
                rt = SessionRuntime(
                    enrolled=[{"student_id": student_id, "display_name": user.get("display_name"),
                               "class_id": class_id}],
                    # class_id=None: self-study rows must never count as class data
                    started_at=now, session_id=session_id, class_id=None, mode=MODE_PERSONAL,
                    config=self.config, sink=self.sink, source="personal_camera")
                rt.set_connection(student_id, True)
                self.registry.start_personal(student_id, rt, now)
            self._personal_sessions[student_id] = {"session_id": session_id, "started_at": now,
                                                   "class_id": class_id,
                                                   "linked_class": int(class_id) if linked else None}
            return session_id

    def stop_personal_session(self, student_id: int) -> Optional[dict]:
        with self._lock:
            if student_id not in self._personal_sessions:
                return None
            now = self.clock()
            info = self._personal_sessions[student_id]
            class_rt = self.registry.class_runtime(info["linked_class"]) if info["linked_class"] else None
            return self._finish_personal(student_id, now, class_rt)

    def _finish_personal(self, student_id, now, class_rt):
        info = self._personal_sessions.pop(student_id, None)
        if info is None:
            return None
        if info["linked_class"] and class_rt is not None:
            st = class_rt.states.get(student_id)
            class_rt.set_connection(student_id, False)
        else:
            rt = self.registry.personal_runtime(student_id)
            self.registry.end_personal(student_id, now)  # closes episodes, final snapshot
            st = rt.states.get(student_id) if rt else None
        final = None if st is None or st.focus_score is None else int(round(st.focus_score))
        avg = None if st is None or st.average_focus_score is None else round(st.average_focus_score, 2)
        events = 0 if st is None else st.total_events
        self.repo.update_session(info["session_id"], _fmt(now), int(now - info["started_at"]),
                                 final, events, avg_focus_score=avg)
        return {"session_id": info["session_id"], "final_score": final, "avg_focus_score": avg,
                "total_distractions": events}

    def personal_session_info(self, student_id):
        return self._personal_sessions.get(student_id)

    def runtime_for_student(self, student_id) -> Optional[SessionRuntime]:
        info = self._personal_sessions.get(student_id)
        if info is None:
            return None
        if info["linked_class"]:
            return self.registry.class_runtime(info["linked_class"])
        return self.registry.personal_runtime(student_id)

    def route_observation(self, student_id: int, obs: Observation):
        """Personal / online camera -> the ONE runtime currently scoring this student."""
        rt = self.runtime_for_student(student_id)
        if rt is None:
            return []
        return rt.ingest(student_id, obs)

    def student_live_view(self, student_id) -> dict:
        info = self._personal_sessions.get(student_id)
        rt = self.runtime_for_student(student_id)
        now = self.clock()
        if rt is None or info is None:
            return {"is_active": False, "seconds_elapsed": 0, "student": None}
        rt.heartbeat(now)
        return {"is_active": True, "seconds_elapsed": int(now - info["started_at"]),
                "session_id": info["session_id"], "linked_class_session_id": rt.class_session_id if info["linked_class"] else None,
                "camera_status": rt.camera_status, "student": rt.student_view(student_id, now)}

    # ============================================================== analytics
    def student_sessions(self, student_id):
        return self.repo.get_sessions_for_user(student_id)

    def student_profile(self, student_id):
        return analytics.student_profile(self.student_sessions(student_id))

    def student_heatmap(self, student_id):
        return analytics.hourly_heatmap(self.student_sessions(student_id))

    def student_subject_analytics(self, student_id):
        return analytics.subject_analytics(self.student_sessions(student_id))

    def student_session_comparison(self, student_id, limit=5):
        return analytics.session_comparison(self.student_sessions(student_id), limit)

    def student_recommendation(self, student_id):
        return analytics.study_time_recommendation(self.student_sessions(student_id))

    def student_attendance_history(self, student_id):
        user = self.repo.get_user_by_id(student_id) or {}
        if user.get("class_id") is None:
            return []
        class_sessions = self.repo.get_class_sessions(user["class_id"])
        rows = self.repo.get_session_students(student_id=student_id)
        return analytics.attendance_history(class_sessions, rows)

    def session_breakdown(self, session_id):
        events = self.repo.get_focus_events(session_id=session_id)
        sess = self.repo.get_session(session_id) or {}
        measured = 1 if sess.get("end_time") and analytics.session_score(sess) is not None else 0
        return analytics.event_breakdown(events, measured)

    def _class_names(self, class_id):
        return {s["student_id"]: s["display_name"] for s in self.repo.get_students_in_class(class_id)}

    def _class_rows(self, fetch, class_id):
        # Only rows produced by a class session (legacy personal rows may carry a class_id).
        return [r for r in fetch(class_id=class_id) if r.get("class_session_id") is not None]

    def class_analytics(self, class_id):
        rows = self._class_rows(self.repo.get_session_students, class_id)
        events = self._class_rows(self.repo.get_focus_events, class_id)
        measured = sum(1 for r in rows if analytics.session_score(r) is not None)
        names = self._class_names(class_id)
        return {
            "breakdown": analytics.event_breakdown(events, measured),
            "danger_hour": analytics.danger_hour(self._class_rows(self.repo.get_focus_snapshots, class_id)),
            "watchlist": analytics.watchlist(rows, names),
            "class_sessions": len([c for c in self.repo.get_class_sessions(class_id) if c.get("ended_at")]),
        }

    def class_leaderboard_today(self, class_id):
        today = datetime.now().strftime("%Y-%m-%d")
        cs_today = {c["id"] for c in self.repo.get_class_sessions(class_id)
                    if str(c.get("started_at") or "").startswith(today)}
        rows = [r for r in self._class_rows(self.repo.get_session_students, class_id)
                if r.get("class_session_id") in cs_today]
        return analytics.leaderboard(rows, self._class_names(class_id))

    def class_session_summary(self, class_session_id, class_id):
        rows = self.repo.get_session_students(class_session_id=class_session_id)
        return analytics.class_session_summary(rows, self._class_names(class_id))

    @staticmethod
    def _duration_seconds(started_at, ended_at):
        a, b = analytics._to_dt(started_at), analytics._to_dt(ended_at)
        return int((b - a).total_seconds()) if a and b else None

    def class_session_history(self, class_id):
        """Finished class sessions of a class, newest first, with real summaries."""
        names = self._class_names(class_id)
        out = []
        for cs in self.repo.get_class_sessions(class_id):
            if not cs.get("ended_at"):
                continue
            rows = self.repo.get_session_students(class_session_id=cs["id"])
            events = self.repo.get_focus_events(class_session_id=cs["id"])
            summary = analytics.class_session_summary(rows, names)
            counts = analytics.event_breakdown(events, 1)["counts"]
            out.append({
                "class_session_id": cs["id"], "started_at": cs.get("started_at"), "ended_at": cs.get("ended_at"),
                "mode": cs.get("mode"), "duration_seconds": self._duration_seconds(cs.get("started_at"), cs.get("ended_at")),
                "enrolled": summary.get("enrolled", len(rows)), "present": summary.get("present", 0),
                "late": summary.get("late", 0), "average_score": summary.get("average_score"),
                "measured": summary.get("measured", 0), "event_counts": counts, "events_total": sum(counts.values()),
            })
        return out

    def class_session_detail(self, class_session_id, class_id):
        names = self._class_names(class_id)
        cs = self.repo.get_class_session(class_session_id) or {}
        rows = self.repo.get_session_students(class_session_id=class_session_id)
        events = self.repo.get_focus_events(class_session_id=class_session_id)
        students = [{
            "student_id": r["student_id"], "name": names.get(r["student_id"], f"#{r['student_id']}"),
            "attendance_status": r.get("attendance_status"),
            "average_score": None if analytics.session_score(r) is None else int(round(analytics.session_score(r))),
            "away_count": r.get("away_count") or 0, "away_seconds": int(r.get("away_seconds") or 0),
            "event_counts": r.get("event_counts") or {},
        } for r in rows]
        students.sort(key=lambda x: x["name"])
        event_rows = [{
            "student_id": e.get("student_id"), "name": names.get(e.get("student_id"), f"#{e.get('student_id')}"),
            "type": analytics.normalize_event_type(e.get("type")) or e.get("type"),
            "start_time": e.get("start_time"), "duration_seconds": e.get("duration_seconds"),
            "confidence": e.get("confidence"),
        } for e in events]
        event_rows.sort(key=lambda e: e.get("start_time") or 0)
        return {
            "class_session_id": class_session_id, "started_at": cs.get("started_at"), "ended_at": cs.get("ended_at"),
            "mode": cs.get("mode"), "duration_seconds": self._duration_seconds(cs.get("started_at"), cs.get("ended_at")),
            "summary": analytics.class_session_summary(rows, names),
            "breakdown": analytics.event_breakdown(events, 1 if rows else 0),
            "students": students, "events": event_rows,
        }

    def latest_class_session_id(self, class_id):
        done = [c for c in self.repo.get_class_sessions(class_id) if c.get("ended_at")]
        return done[0]["id"] if done else None
