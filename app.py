import os
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")

import base64
import functools
import json
import threading
import time
from types import SimpleNamespace

from flask import (Flask, Response, abort, jsonify, redirect, render_template, request, session, url_for)
from flask_socketio import SocketIO, disconnect, emit, join_room

from repository import load_dotenv
from focusguard import authz, camera_state, realtime
from focusguard import runtime_state as rs
from focusguard.policies.contexts import ACTIVITY_CONTEXTS, CONTEXT_LABELS_VI
from focusguard.privacy import audit as audit_actions
from focusguard.privacy import policy_texts, privacy_notice
from focusguard.privacy.consent import SCOPE_FACE
from focusguard.security import build_flask_config, cross_site_write
from focusguard.web_routes import register_feedback_routes
from focusguard.camera_routes import register_camera_routes, _public_health
from focusguard.identity_routes import register_identity_routes
from focusguard.cameras.service import CameraError

load_dotenv()

MSG_FORBIDDEN = "Không có quyền truy cập"


class CameraHub:
    """At most one physical camera pipeline per process.

    * viewers of the same pipeline key share it (reference counted); closing
      one browser tab does not stop the stream for the others;
    * acquiring a different key closes the old pipeline and bumps the
      generation, so generators still holding the old pipeline stop instead
      of re-opening the device;
    * ``read`` serialises ``get_frame`` (tracker and IdentityManager are not
      thread-safe).

    A browser disconnecting only releases the camera device; the
    authoritative SessionRuntime keeps running (it reports NO_CAMERA_SIGNAL
    until frames resume).
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.frame_lock = threading.Lock()
        self.pipeline = None
        self.key = None
        self.users = 0
        self.generation = 0
        self.starting_key = None      # set while a pipeline (camera + models) is loading
        self.failed_key = None        # last key whose pipeline could not be started

    def acquire(self, key, factory):
        with self.lock:
            if self.pipeline is not None and self.key != key:
                self._close()
            if self.pipeline is None:
                self.starting_key = key
                self.failed_key = None
                try:
                    self.pipeline = factory()
                except Exception as exc:
                    self.failed_key = key
                    print("[CAMERA] pipeline failed to start:", exc)   # details stay server-side
                    raise
                finally:
                    self.starting_key = None
                self.key = key
                self.users = 0
                self.generation += 1
            self.users += 1
            return self.pipeline, self.generation

    def describe(self, matches):
        """Status of the pipeline whose key satisfies ``matches`` (no locking:
        called from status endpoints while a pipeline may be loading)."""
        pipeline, key = self.pipeline, self.key
        streaming = pipeline is not None and key is not None and matches(key)
        health = None
        if streaming and hasattr(pipeline, "camera_health"):
            try:
                health = pipeline.camera_health()
            except Exception:
                health = None
        return {
            "starting": self.starting_key is not None and matches(self.starting_key),
            "failed": self.failed_key is not None and matches(self.failed_key),
            "streaming": streaming,
            "viewers": self.users if streaming else 0,
            "device_ok": getattr(pipeline, "device_ok", None) if streaming else None,
            "reconnecting": bool(health) and health.get("state") == "RECONNECTING",
            "health": health,
        }

    def uses_device(self, predicate):
        """Is a running pipeline reading a source that matches ``predicate(source)``?"""
        pipeline = self.pipeline
        src = getattr(pipeline, "source", None)
        return pipeline is not None and src is not None and predicate(src)

    def is_current(self, generation):
        return self.pipeline is not None and self.generation == generation

    def read(self, generation):
        with self.frame_lock:
            pipeline = self.pipeline
            if pipeline is None or self.generation != generation:
                return None
            return pipeline.get_frame()

    def release(self, generation):
        """A viewer of ``generation`` left."""
        with self.lock:
            if self.pipeline is None or generation != self.generation:
                return
            self.users -= 1
            if self.users <= 0:
                self._close()

    def force_release(self, predicate=None, final=False):
        """Close the pipeline now (e.g. class ended) if its key matches. ``final``: the session
        itself ended (not just the last viewer leaving)."""
        with self.lock:
            if self.pipeline is not None and (predicate is None or predicate(self.key)):
                self._close(final=final)
            if self.failed_key is not None and (predicate is None or predicate(self.failed_key)):
                self.failed_key = None

    def _close(self, final=False):
        try:
            try:
                self.pipeline.release(final=True) if final else self.pipeline.release()
            except TypeError:                     # a pipeline without the ``final`` flag
                self.pipeline.release()
        except Exception as exc:
            print("[CAMERA] release error:", exc)
        self.pipeline = None
        self.key = None
        self.users = 0
        self.generation += 1


def create_app(repo=None, env=None, async_mode=None, start_background=True, session_manager=None):
    cfg = build_flask_config(env)
    app = Flask(__name__)
    if cfg["TRUST_PROXY"]:
        from werkzeug.middleware.proxy_fix import ProxyFix
        n = cfg["TRUST_PROXY"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n, x_port=n)
    app.config.update(
        SECRET_KEY=cfg["SECRET_KEY"],
        SESSION_COOKIE_HTTPONLY=cfg["SESSION_COOKIE_HTTPONLY"],
        SESSION_COOKIE_SAMESITE=cfg["SESSION_COOKIE_SAMESITE"],
        SESSION_COOKIE_SECURE=cfg["SESSION_COOKIE_SECURE"],
        PERMANENT_SESSION_LIFETIME=cfg["PERMANENT_SESSION_LIFETIME"],
        MAX_CONTENT_LENGTH=cfg["MAX_CONTENT_LENGTH"],
        FOCUSGUARD=cfg,
    )

    if async_mode is None:
        async_mode = os.environ.get("SOCKETIO_ASYNC_MODE")
    if async_mode is None:
        try:
            import eventlet  # noqa: F401
            async_mode = "eventlet"
        except ImportError:
            async_mode = "threading"
    socketio = SocketIO(app, cors_allowed_origins=cfg["SOCKETIO_CORS_ORIGINS"], async_mode=async_mode,
                        logger=False, engineio_logger=False)

    def notifier(kind, payload):
        class_id = payload.get("class_id")
        if class_id is not None:
            socketio.emit("log_update", payload, room=realtime.teacher_room(class_id))

    if session_manager is None:
        from session_manager import SessionManager
        session_manager = SessionManager(repo=repo, notifier=notifier,
                                         seed_demo_accounts=cfg["SEED_DEMO_ACCOUNTS"])
    else:
        session_manager.notifier = notifier
    sm = session_manager
    repo = sm.repo
    cameras = CameraHub()
    teacher_settings = {}
    app.extensions["focusguard"] = {"session_manager": sm, "socketio": socketio, "cameras": cameras}
    sm.consent.record_policy_change()     # consent policy version bump -> one POLICY_CHANGED audit

    # ------------------------------------------------------------ auth helpers
    def current_principal():
        if "user_id" not in session:
            return None
        return authz.build_principal(session.get("user_id"), session.get("role"), repo)

    def api_error(message=MSG_FORBIDDEN, code=403):
        return jsonify({"status": "error", "message": message}), code

    @app.before_request
    def _csrf_origin_guard():
        # SameSite=Lax cookies + this same-origin check on every state-changing request.
        if cross_site_write(request.method, request.host, request.headers.get("Origin"),
                            request.headers.get("Referer"), cfg["SOCKETIO_CORS_ORIGINS"] or ()):
            if request.path.startswith("/api/"):
                return api_error("Yêu cầu bị chặn: không đến từ trang FocusGuard.", 403)
            return "Forbidden", 403

    ROLE_LABELS = {"student": "Học sinh", "teacher": "Giáo viên", "parent": "Phụ huynh", "admin": "Quản trị viên"}

    @app.context_processor
    def inject_shell():
        role = session.get("role")
        return {"shell_role": role, "shell_role_label": ROLE_LABELS.get(role, ""),
                "shell_name": session.get("display_name") or session.get("user") or "",
                # One privacy sentence for every page (focusguard/privacy/policy.py).
                "privacy_notice": privacy_notice(sm.privacy), "evidence_enabled": sm.privacy.evidence_enabled}

    def wants_json():
        return request.path.startswith("/api/") or request.path.startswith("/socket.io")

    def error_page(code, title, message):
        if wants_json():
            return jsonify({"status": "error", "message": message}), code
        return render_template("error.html", code=code, title=title, message=message,
                               logged_in=current_principal() is not None), code

    @app.errorhandler(404)
    def not_found(_e):
        return error_page(404, "Không tìm thấy trang", "Đường dẫn này không tồn tại hoặc đã được chuyển đi.")

    @app.errorhandler(403)
    def forbidden(_e):
        return error_page(403, "Không có quyền truy cập", "Tài khoản của bạn không được xem trang này.")

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return error_page(405, "Thao tác không hợp lệ", "Yêu cầu này không được hỗ trợ.")

    @app.errorhandler(413)
    def too_large(_e):
        return error_page(413, "Dữ liệu quá lớn", "Tệp hoặc ảnh gửi lên vượt quá giới hạn cho phép.")

    @app.errorhandler(Exception)
    def server_error(exc):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return error_page(exc.code or 500, "Có lỗi xảy ra", "Không thể xử lý yêu cầu này.")
        app.logger.exception("Unhandled error on %s", request.path)   # traceback stays server-side
        return error_page(500, "Có lỗi xảy ra", "Không thể tải dữ liệu lúc này. Vui lòng thử lại.")

    def login_required_page(role=None):
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                p = current_principal()
                if p is None:
                    had_session = "user_id" in session
                    session.clear()
                    return redirect(url_for("login", expired=1) if had_session else url_for("login"))
                if role and p.role != role:
                    return redirect(url_for("index"))
                return fn(p, *args, **kwargs)
            return wrapper
        return deco

    def api_auth(*roles):
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                p = current_principal()
                if p is None:
                    return api_error(MSG_FORBIDDEN, 401)
                if roles and p.role not in roles:
                    return api_error()
                return fn(p, *args, **kwargs)
            return wrapper
        return deco

    def int_arg(name, default=None, source=None):
        src = source if source is not None else request.args
        try:
            return int(src.get(name))
        except (TypeError, ValueError):
            return default

    def teacher_class_id(p, source=None):
        """Class requested by a teacher, defaulting to their first class; None if not allowed."""
        cid = int_arg("class_id", None, source)
        if cid is None:
            cid = session.get("class_id") if session.get("class_id") in p.class_ids else \
                (sorted(p.class_ids)[0] if p.class_ids else None)
        return cid if authz.can_manage_class(p, cid) else None

    def subject_student_id(p):
        """Whose personal analytics are requested: own (student), linked (parent) or
        ?student_id= for teachers/admin with an ownership check."""
        if p.role == authz.ROLE_STUDENT:
            return p.user_id
        if p.role == authz.ROLE_PARENT:
            return p.linked_student_id
        sid = int_arg("student_id")
        if sid is None:
            return None
        user = repo.get_user_by_id(sid)
        if not user or user.get("role") != "student":
            return None
        return sid if authz.can_view_student(p, sid, user.get("class_id")) else None

    # ------------------------------------------------------------------ pages
    @app.route("/")
    def index():
        p = current_principal()
        if p is None:
            session.clear()
            return redirect(url_for("login"))
        if p.role == authz.ROLE_TEACHER:
            return redirect(url_for("teacher_dashboard"))
        if p.role == authz.ROLE_ADMIN:
            return redirect(url_for("admin_root"))
        if p.role == authz.ROLE_PARENT:
            return redirect(url_for("parent_dashboard"))
        return render_template("student.html", active_tab="dashboard")

    def student_page(tab):
        @login_required_page(authz.ROLE_STUDENT)
        def view(p):
            return render_template("student.html", active_tab=tab)
        view.__name__ = f"student_{tab}"
        return view

    app.add_url_rule("/stats", "stats", student_page("stats"))
    app.add_url_rule("/reports", "reports", student_page("reports"))
    app.add_url_rule("/settings", "settings", student_page("settings"))

    @app.route("/alerts")
    def alerts():
        return redirect(url_for("reports"))

    def teacher_page(tab):
        @login_required_page(authz.ROLE_TEACHER)
        def view(p):
            classes = [{"id": cid, "class_name": repo.get_class_name(cid)} for cid in sorted(p.class_ids)]
            current = teacher_class_id(p)
            if current is not None and int_arg("class_id") == current:
                session["class_id"] = current      # remember the (authorised) class across pages
            return render_template("teacher.html", active_tab=tab,
                                   activity_contexts=[(c, CONTEXT_LABELS_VI[c]) for c in ACTIVITY_CONTEXTS],
                                   teacher_name=session.get("display_name", "Giáo viên"),
                                   class_name=repo.get_class_name(current) if current else None,
                                   teacher_classes=classes, current_class_id=current,
                                   min_focus_threshold=teacher_settings.get(p.user_id, {}).get("min_focus_threshold", 65))
        view.__name__ = f"teacher_{tab}"
        return view

    for tab in ("dashboard", "students", "analytics", "reports", "alerts", "events", "evaluation", "settings",
                "attendance"):
        endpoint = "teacher_dashboard" if tab == "dashboard" else f"teacher_{tab}"
        app.add_url_rule(f"/teacher/{tab}", endpoint, teacher_page(tab))

    @app.route("/parent/dashboard")
    @login_required_page(authz.ROLE_PARENT)
    def parent_dashboard(p):
        sid = p.linked_student_id
        student = repo.get_user_by_id(sid) if sid else None
        if not student:
            return render_template("parent.html", student_name=None, linked=False, consent=None,
                                   student_id=None, policy=policy_texts(sm.privacy),
                                   profile_stats=sm.student_profile(-1), recommendations=None,
                                   heatmap_data={}, comparison=[], attendance=[], attendance_summary=None)
        attendance = sm.student_attendance_history(sid)
        attended = sum(1 for a in attendance if not a["missed"])
        attendance_summary = None if not attendance else {
            "attended": attended, "total": len(attendance),
            "rate": int(round(100.0 * attended / len(attendance))),
            "away_count": sum(a.get("away_count") or 0 for a in attendance),
        }
        consent = sm.consent.status(sid)
        return render_template(
            "parent.html", student_name=student.get("display_name"), linked=True, consent=consent,
            student_id=sid, policy=policy_texts(sm.privacy),
            profile_stats=sm.student_profile(sid), recommendations=sm.student_recommendation(sid),
            heatmap_data=sm.student_heatmap(sid), comparison=sm.student_session_comparison(sid, 7),
            attendance=attendance[:10], attendance_summary=attendance_summary)

    @app.route("/mobile")
    def mobile_simulator():
        # The phone mock-up was replaced by responsive pages; keep the URL working.
        return redirect(url_for("index"))

    # ----------------------------------------------------------- authentication
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_principal() is not None:
            return redirect(url_for("index"))
        error, username = None, ""
        if request.method == "POST":
            username = request.form.get("username", "").strip()[:80]
            try:
                user = repo.authenticate_user(username, request.form.get("password", ""))
            except Exception:
                app.logger.exception("Login backend error")
                return render_template("login.html", username=username, show_demo=cfg["SEED_DEMO_ACCOUNTS"],
                                       error="Không thể đăng nhập lúc này. Vui lòng thử lại sau."), 503
            if user:
                session.clear()
                session.permanent = True
                session["user"], session["user_id"] = user[1], user[0]
                session["display_name"], session["role"], session["class_id"] = user[2], user[3], user[4]
                return redirect(url_for("index"))
            # Same message whether the account exists or not.
            error = "Tên đăng nhập hoặc mật khẩu không đúng."
        notice = "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại." if request.args.get("expired") else None
        return render_template("login.html", error=error, notice=notice, username=username,
                               show_demo=cfg["SEED_DEMO_ACCOUNTS"]), (401 if error else 200)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ----------------------------------------------------------- video stream
    def stream(pipeline_key, factory, keep_running):
        def gen():
            generation = None
            try:
                _, generation = cameras.acquire(pipeline_key, factory)
                while keep_running() and cameras.is_current(generation):
                    socketio.sleep(0.05)
                    out = cameras.read(generation)
                    if out is None or out[0] is None:
                        continue
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + out[0] + b"\r\n"
            except GeneratorExit:
                pass
            finally:
                if generation is not None:
                    cameras.release(generation)
        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/video_feed")
    def video_feed():
        p = current_principal()
        if p is None:
            return api_error(MSG_FORBIDDEN, 401)
        if request.args.get("mode") == "classroom" or p.role in (authz.ROLE_TEACHER, authz.ROLE_ADMIN):
            cid = int_arg("class_id") or teacher_class_id(p)
            if not authz.can_view_video_feed(p, "classroom", cid):
                return api_error()
            rt = sm.classroom_runtime(cid)
            if rt is None:
                return api_error("Lớp học chưa bắt đầu giám sát bằng camera.", 409)
            cs_id = rt.class_session_id
            camera_id = (rt.camera or {}).get("camera_id")

            def factory():
                if sm.classroom_pipeline_factory is not None:      # dev/test simulator only
                    return sm.classroom_pipeline_factory(sm, cid, cs_id)
                from classroom_ai import ClassroomAI
                sm.cameras.sweep_previews()       # an expired preview must not hold the device
                source = None
                if camera_id:                     # a saved IP camera / webcam; None = local webcam 0
                    cam = repo.get_camera(camera_id)
                    if cam is None:
                        raise RuntimeError("camera was deleted")
                    source = sm.cameras.open_source(cam)
                return ClassroomAI(sm, cid, class_session_id=cs_id, source=source)
            # Bound to this exact classroom session: stops if the class ends or
            # another (e.g. online) session replaces it.
            return stream(("classroom", cid, cs_id), factory,
                          lambda: sm.classroom_runtime(cid, cs_id) is not None)

        if not authz.can_view_video_feed(p, "personal"):
            return api_error()
        if sm.runtime_for_student(p.user_id) is None:
            return api_error("Chưa bắt đầu phiên học.", 409)
        sid = p.user_id

        def factory():
            from camera_ai import FocusAI
            return FocusAI(sm, sid)
        return stream(("personal", sid), factory, lambda: sm.runtime_for_student(sid) is not None)

    # ------------------------------------------------------- student live API
    def history_for(student_id):
        rt = sm.runtime_for_student(student_id)
        return rt.history(student_id) if rt is not None else []

    @app.route("/api/stats")
    @api_auth(authz.ROLE_STUDENT)
    def get_stats(p):
        live = sm.student_live_view(p.user_id)
        s = live.get("student") or {}
        focus_state = s.get("focus_state", rs.FOCUS_UNKNOWN)
        rt = sm.runtime_for_student(p.user_id)
        hub = cameras.describe(lambda key: key == ("personal", p.user_id))
        cam = camera_state.describe(camera_state.derive(live["is_active"], hub,
                                                        rt.camera_status if rt is not None else None))
        # Only whether the student's own class is in session, never class numbers.
        class_rt = next((r for r in (sm.class_runtime(c) for c in p.class_ids) if r is not None), None)
        return jsonify({
            "class_live": {"active": class_rt is not None, "mode": class_rt.mode if class_rt is not None else None},
            "is_active": live["is_active"],
            "seconds_elapsed": live["seconds_elapsed"],
            "session_id": live.get("session_id"),
            "linked_class_session_id": live.get("linked_class_session_id"),
            "server_time": time.time(),
            "camera": cam,
            "behaviors": s.get("behaviors", []),
            "connection_status": s.get("connection_status"),
            "camera_status": live.get("camera_status"),
            "focus_score": s.get("focus_score"),
            "average_focus_score": s.get("average_focus_score"),
            "focus_state": focus_state,
            "visibility_status": s.get("visibility_status"),
            "attendance_status": s.get("attendance_status"),
            "labels": s.get("labels"),
            "distractions": s.get("distractions", 0),
            "event_counts": s.get("event_counts", {}),
            "seat_leaving_count": s.get("away_count", 0),
            "seat_leaving_duration": s.get("away_seconds", 0),
            "visible_seconds": s.get("visible_seconds", 0),
            "history": history_for(p.user_id),
            "evaluation_status": "NOT_EVALUATED",
        })

    @app.route("/api/start_session", methods=["POST"])
    @api_auth(authz.ROLE_STUDENT)
    def start_session(p):
        data = request.get_json(silent=True) or {}
        subject = (data.get("subject") or "").strip()[:40] or None
        already = sm.personal_session_info(p.user_id) is not None
        session_id = sm.start_personal_session(p.user_id, subject=subject)
        return jsonify({"status": "success", "session_id": session_id, "already_active": already})

    @app.route("/api/stop_session", methods=["POST"])
    @api_auth(authz.ROLE_STUDENT)
    def stop_session(p):
        result = sm.stop_personal_session(p.user_id)
        cameras.force_release(lambda key: key == ("personal", p.user_id))
        return jsonify({"status": "success", "result": result, "was_active": result is not None})

    # ------------------------------------------------ personal analytics (own)
    def personal_endpoint(fn):
        @functools.wraps(fn)
        @api_auth(authz.ROLE_STUDENT, authz.ROLE_PARENT, authz.ROLE_TEACHER, authz.ROLE_ADMIN)
        def wrapper(p, *args, **kwargs):
            sid = subject_student_id(p)
            if sid is None:
                return api_error()
            return fn(p, sid, *args, **kwargs)
        return wrapper

    @app.route("/api/sessions")
    @personal_endpoint
    def get_all_sessions(p, sid):
        return jsonify(sm.student_sessions(sid))

    @app.route("/api/latest_session")
    @personal_endpoint
    def get_latest_session(p, sid):
        done = [s for s in sm.student_sessions(sid) if s.get("end_time")]
        if not done:
            return jsonify({"status": "empty", "message": "Chưa có phiên học nào"})
        return jsonify(done[0])

    @app.route("/api/session/<int:session_id>/breakdown")
    @api_auth()
    def api_session_breakdown(p, session_id):
        row = repo.get_session(session_id)
        owner = repo.get_user_by_id(row.get("user_id")) if row else None
        if not row or not authz.can_view_personal_session(p, row, (owner or {}).get("class_id")):
            return api_error(code=404 if not row else 403)
        return jsonify(sm.session_breakdown(session_id))

    @app.route("/api/student/heatmap")
    @personal_endpoint
    def api_student_heatmap(p, sid):
        return jsonify(sm.student_heatmap(sid))

    @app.route("/api/student/subject_analytics")
    @personal_endpoint
    def api_student_subject_analytics(p, sid):
        return jsonify(sm.student_subject_analytics(sid))

    @app.route("/api/student/profile")
    @personal_endpoint
    def api_student_profile(p, sid):
        return jsonify(sm.student_profile(sid))

    @app.route("/api/student/session_comparison")
    @personal_endpoint
    def api_student_session_comparison(p, sid):
        return jsonify(sm.student_session_comparison(sid))

    @app.route("/api/student/recommendations")
    @personal_endpoint
    def api_student_recommendations(p, sid):
        return jsonify(sm.student_recommendation(sid))

    @app.route("/api/student/attendance_history")
    @personal_endpoint
    def api_student_attendance_history(p, sid):
        return jsonify(sm.student_attendance_history(sid))

    @app.route("/api/leaderboard")
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    def api_leaderboard(p):
        cid = teacher_class_id(p)
        if cid is None:
            return api_error()
        return jsonify(sm.class_leaderboard_today(cid))

    # Admin-editable behaviour thresholds and their sane ranges.
    SETTINGS_RANGES = {"ear_threshold": (0.10, 0.40), "drowsy_min_seconds": (0.5, 10.0),
                       "head_away_min_seconds": (0.5, 30.0)}

    @app.route("/api/settings", methods=["GET", "POST"])
    @api_auth()
    def manage_settings(p):
        beh = sm.config.behavior
        current = {"ear_threshold": beh.ear_threshold, "drowsy_threshold": beh.drowsy_min_seconds,
                   "distraction_threshold": beh.head_away_min_seconds,
                   "phone_threshold": beh.phone_min_seconds, "away_threshold": beh.away_min_seconds,
                   "editable": p.is_admin}
        if request.method == "GET":
            return jsonify(current)
        if not p.is_admin:
            # Personal preferences (sound, volume) live in the browser; AI thresholds
            # are centralised and cannot be changed by students/teachers/parents.
            return jsonify({"status": "success", "thresholds_changed": False,
                            "message": "Đã lưu tuỳ chọn cá nhân. Ngưỡng nhận diện do quản trị viên cấu hình."})
        data = request.get_json(silent=True) or {}
        try:
            values = {"ear_threshold": float(data.get("ear_threshold", beh.ear_threshold)),
                      "drowsy_min_seconds": float(data.get("drowsy_threshold", beh.drowsy_min_seconds)),
                      "head_away_min_seconds": float(data.get("distraction_threshold", beh.head_away_min_seconds))}
        except (TypeError, ValueError):
            return api_error("Giá trị không hợp lệ", 400)
        for key, (lo, hi) in SETTINGS_RANGES.items():
            if not lo <= values[key] <= hi:     # also rejects NaN
                return api_error(f"Giá trị {key} phải trong khoảng {lo}–{hi}.", 400)
        sm.config = sm.config.with_behavior(**values)
        new = sm.config.behavior
        sm.audit.record(audit_actions.POLICY_CHANGED, p, None, {
            "kind": "behavior_thresholds",
            "before": {"ear_threshold": beh.ear_threshold, "drowsy_min_seconds": beh.drowsy_min_seconds,
                       "head_away_min_seconds": beh.head_away_min_seconds},
            "after": {"ear_threshold": new.ear_threshold, "drowsy_min_seconds": new.drowsy_min_seconds,
                      "head_away_min_seconds": new.head_away_min_seconds}})
        return jsonify({"status": "success", "message": "Cấu hình áp dụng cho phiên học mới."})

    # ----------------------------------------------------------- teacher API
    MODE_LABELS = {"classroom": "Camera lớp học", "online": "Lớp trực tuyến"}

    def class_payload(cid, view=None):
        """Canonical class state for the teacher UI (REST and Socket.IO share it)."""
        view = view or sm.class_live_view(cid)
        camera = None
        if view["active"] and view["mode"] == "classroom":
            hub = cameras.describe(lambda key: key[:2] == ("classroom", cid))
            camera = camera_state.describe(camera_state.derive(True, hub, view["camera_status"]))
            rt = sm.class_runtime(cid)
            camera["source"] = (getattr(rt, "camera", None) if rt is not None else None) or \
                {"camera_id": None, "camera_name": "Webcam 0 (mặc định)", "camera_source_type": "WEBCAM"}
            camera["health"] = _public_health(hub.get("health"))
        return {
            "status": "success", "class_id": cid, "class_name": repo.get_class_name(cid),
            "students": view["students"], "statistics": view["statistics"],
            "class_session_active": view["active"], "mode": view["mode"],
            "mode_label": MODE_LABELS.get(view["mode"]),
            "class_session_id": view["class_session_id"],
            "phase": view.get("phase"), "attendance_finalized": view.get("attendance_finalized", False),
            "started_at": view["started_at"], "elapsed_seconds": view["elapsed_seconds"],
            "server_time": time.time(), "camera": camera, "camera_status": view["camera_status"],
            "logs": view["logs"],
            # Teacher-chosen activity context (never inferred by the AI).
            "activity_context": view.get("activity_context"),
            "activity_context_label": CONTEXT_LABELS_VI.get(view.get("activity_context")),
            "context_history": view.get("context_history", []),
            "policy_version": view.get("policy_version") or sm.policy.version,
        }

    @app.route("/api/teacher/class_status")
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    def api_class_status(p):
        cid = teacher_class_id(p)
        if cid is None:
            return api_error("Bạn chưa được phân công lớp này.")
        return jsonify(class_payload(cid))

    start_lock = threading.Lock()

    def _start_class(p, mode):
        data = request.get_json(silent=True) or {}
        cid = teacher_class_id(p, data) if data.get("class_id") is not None else teacher_class_id(p)
        if cid is None:
            return api_error("Bạn chưa được phân công lớp này.")
        with start_lock:      # two clicks / two tabs must not create two sessions
            existing = sm.class_runtime(cid)
            if existing is not None:
                if existing.mode == mode:
                    return jsonify({"status": "success", "already_active": True, "class_id": cid,
                                    "class_session_id": existing.class_session_id,
                                    "message": "Buổi học đang diễn ra."})
                return api_error(f"Lớp đang có buổi học ở chế độ {MODE_LABELS.get(existing.mode, existing.mode)}. "
                                 "Hãy kết thúc buổi đó trước.", 409)
            if not repo.get_students_in_class(cid):
                return api_error("Lớp này chưa có học sinh. Hãy thêm học sinh trước khi bắt đầu.", 409)
            camera = None
            if mode == "classroom":
                # Assigned class camera -> teacher default -> ask; no camera configured = webcam 0.
                try:
                    cam, reason = sm.cameras.resolve_for_class(p, cid, data.get("camera_id") or None)
                except CameraError as exc:
                    return api_error(exc.message, exc.status)
                if reason == "CHOOSE":
                    return api_error("Hãy chọn camera cho buổi học (Cài đặt › Camera để đặt camera mặc định).", 409)
                camera = sm.cameras.evaluation_metadata(cam) if cam is not None else None
            rt = sm.start_class(cid, mode=mode, started_by=p.user_id, camera=camera,
                                initialize=bool(data.get("initialize")) and mode == "classroom")
        socketio.emit("class_started", {"message": "Giáo viên đã bắt đầu buổi học", "class_id": cid},
                      room=realtime.class_students_room(cid))
        socketio.emit("class_snapshot", class_payload(cid), room=realtime.teacher_room(cid))
        return jsonify({"status": "success", "already_active": False, "class_session_id": rt.class_session_id,
                        "class_id": cid, "message": "Buổi học đã bắt đầu."})

    def _end_class(p, mode):
        data = request.get_json(silent=True) or {}
        cid = teacher_class_id(p, data) if data.get("class_id") is not None else teacher_class_id(p)
        if cid is None:
            return api_error("Bạn chưa được phân công lớp này.")
        with start_lock:
            rt = sm.class_runtime(cid)
            if rt is None:
                return jsonify({"status": "success", "was_active": False, "class_id": cid,
                                "class_session_id": None, "message": "Không có buổi học đang diễn ra."})
            cameras.force_release(lambda key: key[:2] == ("classroom", cid), final=True)
            result = sm.end_class(cid)
        socketio.emit("class_ended", {"message": "Giáo viên đã kết thúc buổi học", "class_id": cid},
                      room=realtime.class_students_room(cid))
        socketio.emit("class_snapshot", class_payload(cid), room=realtime.teacher_room(cid))
        return jsonify({"status": "success", "was_active": True, "class_id": cid,
                        "class_session_id": result["class_session_id"], "message": "Buổi học đã kết thúc."})

    @app.route("/api/teacher/start_class", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def start_class(p):
        return _start_class(p, "online")

    @app.route("/api/teacher/end_class", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def end_class(p):
        return _end_class(p, "online")

    @app.route("/api/teacher/start_offline_class", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def start_offline_class(p):
        return _start_class(p, "classroom")

    @app.route("/api/teacher/end_offline_class", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def end_offline_class(p):
        return _end_class(p, "classroom")

    @app.route("/api/teacher/logs")
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    def api_teacher_logs(p):
        cid = teacher_class_id(p)
        if cid is None:
            return api_error()
        return jsonify(sm.class_live_view(cid)["logs"])

    @app.route("/api/teacher/analytics_summary")
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    def api_teacher_analytics_summary(p):
        cid = teacher_class_id(p)
        if cid is None:
            return api_error()
        data = sm.class_analytics(cid)
        live = sm.class_live_view(cid)["statistics"]
        bd = data["breakdown"]
        pct = bd.get("percentages")
        return jsonify({
            "status": "success", "class_id": cid,
            "average_score": live["average_focus_score"],
            "danger_hour": data["danger_hour"]["label"],
            "danger_hour_detail": data["danger_hour"],
            "watchlist": data["watchlist"],
            "breakdown": bd,
            "root_cause": None if pct is None else {
                "phone_pct": pct["PHONE"], "drowsy_pct": pct["DROWSY"],
                "distracted_pct": pct["HEAD_AWAY"], "away_pct": pct["AWAY"]},
            "class_sessions": data["class_sessions"],
        })

    @app.route("/api/teacher/class_summary")
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    def api_teacher_class_summary(p):
        cid = teacher_class_id(p)
        if cid is None:
            return api_error()
        cs_id = int_arg("class_session_id") or sm.latest_class_session_id(cid)
        if cs_id is None:
            return jsonify({"status": "insufficient_data", "summary_text": "Không đủ dữ liệu"})
        row = repo.get_class_session(cs_id)
        if not authz.can_view_class_session(p, row):
            return api_error()
        summary = sm.class_session_summary(cs_id, cid)
        summary["danger_hour"] = sm.class_analytics(cid)["danger_hour"]["label"]
        summary["status"] = "success" if summary.get("status") == "ok" else summary.get("status")
        return jsonify(summary)

    @app.route("/api/teacher/class_sessions")
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    def api_teacher_class_sessions(p):
        cid = teacher_class_id(p)
        if cid is None:
            return api_error("Bạn chưa được phân công lớp này.")
        return jsonify({"status": "success", "class_id": cid, "sessions": sm.class_session_history(cid)})

    @app.route("/api/teacher/class_session/<int:class_session_id>")
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    def api_teacher_class_session(p, class_session_id):
        row = repo.get_class_session(class_session_id)
        if not row:
            return api_error("Không tìm thấy buổi học.", 404)
        if not authz.can_view_class_session(p, row):
            return api_error()
        detail = sm.class_session_detail(class_session_id, row["class_id"])
        detail["status"] = "success"
        return jsonify(detail)

    @app.route("/api/teacher/settings", methods=["GET"])
    @api_auth(authz.ROLE_TEACHER)
    def get_teacher_settings(p):
        return jsonify({"status": "success", "display_name": session.get("display_name"),
                        "min_focus_threshold": teacher_settings.get(p.user_id, {}).get("min_focus_threshold", 65)})

    @app.route("/api/teacher/settings", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def save_teacher_settings(p):
        data = request.get_json(silent=True) or {}
        display_name = (data.get("display_name") or "").strip()
        if not display_name:
            return api_error("Tên giáo viên không được để trống.", 400)
        repo.update_display_name(p.user_id, display_name[:80])
        session["display_name"] = display_name[:80]
        try:
            threshold = max(0, min(100, int(data.get("min_focus_threshold", 65))))
        except (TypeError, ValueError):
            threshold = 65
        teacher_settings[p.user_id] = {"min_focus_threshold": threshold}
        return jsonify({"status": "success", "message": "Đã lưu cài đặt.", "display_name": display_name[:80],
                        "min_focus_threshold": threshold})

    # ------------------------------------------------------------- admin API
    @app.route("/admin")
    @app.route("/admin/")
    @app.route("/admin/dashboard")
    @login_required_page(authz.ROLE_ADMIN)
    def admin_root(p):
        return redirect(url_for("admin_accounts"))

    @app.route("/admin/accounts")
    @login_required_page(authz.ROLE_ADMIN)
    def admin_accounts(p):
        return render_template("admin.html", active_tab="accounts")

    @app.route("/admin/classes")
    @login_required_page(authz.ROLE_ADMIN)
    def admin_classes_page(p):
        return render_template("admin.html", active_tab="classes")

    @app.route("/admin/privacy")
    @login_required_page(authz.ROLE_ADMIN)
    def admin_privacy_page(p):
        return render_template("admin.html", active_tab="privacy", policy=policy_texts(sm.privacy),
                               audit_actions=[(a, audit_actions.ACTION_LABELS_VI.get(a, a))
                                              for a in audit_actions.ACTIONS])

    def op_result(ok, msg):
        # A failed operation is an HTTP error too, so clients cannot mistake it for success.
        return jsonify({"status": "success" if ok else "error", "message": msg}), (200 if ok else 409)

    @app.route("/api/admin/classes")
    @api_auth(authz.ROLE_ADMIN)
    def admin_classes(p):
        return jsonify(repo.get_classes_list())

    @app.route("/api/admin/users")
    @api_auth(authz.ROLE_ADMIN)
    def admin_users(p):
        users = repo.get_users_list()
        names = {u["id"]: u["display_name"] for u in users}
        for u in users:
            u["linked_student_name"] = names.get(u.get("student_id")) if u.get("role") == "parent" else None
            u["is_self"] = u["id"] == p.user_id
        return jsonify(users)

    @app.route("/api/admin/create_class", methods=["POST"])
    @api_auth(authz.ROLE_ADMIN)
    def admin_create_class(p):
        name = ((request.get_json(silent=True) or {}).get("class_name") or "").strip()
        if not name:
            return api_error("Tên lớp không được trống", 400)
        ok, msg = repo.create_class(name)
        return op_result(ok, msg)

    @app.route("/api/admin/edit_class/<int:class_id>", methods=["POST"])
    @api_auth(authz.ROLE_ADMIN)
    def admin_edit_class(p, class_id):
        name = ((request.get_json(silent=True) or {}).get("class_name") or "").strip()
        if not name:
            return api_error("Tên lớp không được trống", 400)
        ok, msg = repo.update_class(class_id, name)
        return op_result(ok, msg)

    @app.route("/api/admin/delete_class/<int:class_id>", methods=["POST", "DELETE"])
    @api_auth(authz.ROLE_ADMIN)
    def admin_delete_class(p, class_id):
        if sm.class_runtime(class_id) is not None:
            return api_error("Lớp đang có buổi học diễn ra. Hãy kết thúc buổi học trước khi xóa lớp.", 409)
        ok, msg = repo.delete_class(class_id)
        return op_result(ok, msg)

    def _user_payload():
        d = request.get_json(silent=True) or {}
        return ((d.get("username") or "").strip(), (d.get("password") or "").strip(),
                (d.get("display_name") or "").strip(), (d.get("role") or "").strip(),
                d.get("class_id"), d.get("student_id"))

    @app.route("/api/admin/create_user", methods=["POST"])
    @api_auth(authz.ROLE_ADMIN)
    def admin_create_user(p):
        username, password, display_name, role, class_id, student_id = _user_payload()
        if not username or not password or not display_name or not role:
            return api_error("Vui lòng nhập đầy đủ thông tin", 400)
        ok, msg = repo.create_user(username, password, display_name, role, class_id, student_id)
        return op_result(ok, msg)

    @app.route("/api/admin/edit_user/<int:user_id>", methods=["POST"])
    @api_auth(authz.ROLE_ADMIN)
    def admin_edit_user(p, user_id):
        username, password, display_name, role, class_id, student_id = _user_payload()
        if not username or not display_name or not role:
            return api_error("Vui lòng nhập đầy đủ thông tin", 400)
        old = repo.get_user_by_id(user_id)
        ok, msg = repo.update_user(user_id, username, password, display_name, role, class_id, student_id)
        if ok and old and old.get("role") == "student" and role != "student":
            _purge_student_biometrics(p, user_id, "role_changed")   # face data must not follow a non-student
        return op_result(ok, msg)

    @app.route("/api/admin/delete_user/<int:user_id>", methods=["POST", "DELETE"])
    @api_auth(authz.ROLE_ADMIN)
    def admin_delete_user(p, user_id):
        if user_id == p.user_id:
            return api_error("Không thể tự xóa tài khoản đang đăng nhập.", 400)
        old = repo.get_user_by_id(user_id)
        had_face = bool(old and old.get("role") == "student" and repo.get_face_enrollment(user_id)["has_face"])
        ok, msg = repo.delete_user(user_id)        # the embedding lives in the user row and goes with it
        if ok and old and old.get("role") == "student":
            _purge_student_biometrics(p, user_id, "account_deleted", had_face=had_face)
        return op_result(ok, msg)

    def _purge_student_biometrics(actor, student_id, reason, had_face=None):
        """Called after a successful delete / role change: face embedding + evidence
        clips go too (audited); consent records and the audit log are kept as the
        record of what happened."""
        sm.evidence.cancel_pending(student_id)
        if had_face is None:
            had_face = repo.get_face_enrollment(student_id)["has_face"]
            if had_face:
                repo.save_face_embedding(int(student_id), None)
        if had_face:
            sm.audit.record(audit_actions.FACE_DELETED, actor, student_id, {"reason": reason})
        sm.evidence.delete_for_student(student_id, actor, reason=reason)
        _refresh_classroom_gallery()

    recognizer_box = {}

    def face_recognizer():
        # Loading the face model takes seconds; do it once, not on every request.
        if "instance" not in recognizer_box:
            from face_recognition import FaceRecognizer
            recognizer_box["instance"] = FaceRecognizer()
        return recognizer_box["instance"]

    def _decode_image(b64):
        import cv2
        import numpy as np
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        arr = np.frombuffer(base64.b64decode(b64), np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)

    def _refresh_classroom_gallery():
        pipeline = cameras.pipeline
        if pipeline is not None and getattr(pipeline, "mode", None) == "classroom":
            pipeline.reload_gallery()

    @app.route("/api/admin/register_face", methods=["POST"])
    @api_auth(authz.ROLE_ADMIN)
    def admin_register_face(p):
        data = request.get_json(silent=True) or {}
        user_id = data.get("user_id")
        images = data.get("images") or []
        target = repo.get_user_by_id(user_id) if user_id is not None else None
        if not target or target.get("role") != "student" or not images:
            return api_error("Thiếu thông tin học sinh hoặc hình ảnh", 400)
        # Consent is decided on the server from the consent record; a client
        # flag such as {"consent": true} is ignored.
        if not sm.consent.has_scope(target["id"], SCOPE_FACE):
            return api_error("Chưa có đồng ý nhận diện khuôn mặt còn hiệu lực cho học sinh này. "
                             "Hãy ghi nhận đồng ý của phụ huynh trước khi đăng ký.", 403)
        imgs = []
        for b64 in images[:10]:
            try:
                img = _decode_image(b64)
                if img is not None:
                    imgs.append(img)
            except Exception:
                continue
        if not imgs:
            return api_error("Không có hình ảnh hợp lệ để trích xuất", 400)
        try:
            recognizer = face_recognizer()
            embeddings = [e for e in (recognizer.extract_embedding(img) for img in imgs) if e is not None]
        except Exception:
            app.logger.exception("register_face failed")
            return api_error("Không thể xử lý ảnh lúc này. Vui lòng thử lại.", 500)
        if not embeddings:
            return api_error("Không phát hiện thấy khuôn mặt rõ ràng trong các bức ảnh", 400)
        # Multi-template profile: each usable photo's embedding (diverse ones kept); no photo is written.
        n = sm.faces.replace_from_photos(target["id"], embeddings, actor=p)
        if not n:
            return api_error("Không thể lưu đặc trưng vào cơ sở dữ liệu", 500)
        sm.audit.record(audit_actions.FACE_ENROLLED, p, target["id"], {"images": len(imgs), "templates": n})
        _refresh_classroom_gallery()
        st = sm.faces.status(target["id"])
        return jsonify({"status": "success", "enrollment": st,
                        "message": f"Đăng ký khuôn mặt thành công ({st['summary']}). Chỉ lưu vector đặc trưng."})

    @app.route("/api/admin/reset_face", methods=["POST"])
    @api_auth(authz.ROLE_ADMIN)
    def admin_reset_face(p):
        try:
            user_id = int((request.get_json(silent=True) or {}).get("user_id"))
        except (TypeError, ValueError):
            return api_error("Thiếu thông tin user_id", 400)
        existed = repo.get_face_enrollment(user_id)["has_face"]
        repo.save_face_embedding(user_id, None)
        if existed:
            sm.audit.record(audit_actions.FACE_DELETED, p, user_id, {"reason": "admin_request"})
        legacy = os.path.join(app.static_folder, "uploads", "avatars", f"student_{user_id}.jpg")
        if os.path.exists(legacy):
            os.remove(legacy)
        _refresh_classroom_gallery()
        return jsonify({"status": "success", "message": "Đã xóa dữ liệu khuôn mặt thành công."})

    @app.route("/api/admin/detect_face", methods=["POST"])
    @api_auth(authz.ROLE_ADMIN)
    def admin_detect_face(p):
        b64 = (request.get_json(silent=True) or {}).get("image")
        if not b64:
            return api_error("Thiếu dữ liệu hình ảnh", 400)
        try:
            import cv2
            img = _decode_image(b64)
            if img is None:
                return api_error("Ảnh không hợp lệ", 400)
            faces = face_recognizer().app.get(img)
            if not faces:
                return jsonify({"status": "no_face", "message": "Không tìm thấy khuôn mặt"})
            f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
            x1, y1, x2, y2 = map(int, f.bbox)
            fw, fh = x2 - x1, y2 - y1
            if fw < 80 or fh < 80:
                return jsonify({"status": "low_quality", "message": "Khuôn mặt quá nhỏ hoặc quá xa camera"})
            crop = img[max(0, y1 - fh // 4):min(img.shape[0], y2 + fh // 4), max(0, x1 - fw // 4):min(img.shape[1], x2 + fw // 4)]
            _, buf = cv2.imencode(".jpg", crop)
            return jsonify({"status": "success",
                            "cropped_image": "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")})
        except Exception:
            app.logger.exception("detect_face failed")
            return api_error("Không thể xử lý ảnh lúc này. Vui lòng thử lại.", 500)

    sm.face_listeners.append(lambda _sid: _refresh_classroom_gallery())

    def broadcast_class(cid):
        socketio.emit("class_snapshot", class_payload(cid), room=realtime.teacher_room(cid))

    register_feedback_routes(app, sm, SimpleNamespace(
        current_principal=current_principal, api_auth=api_auth, api_error=api_error, int_arg=int_arg,
        teacher_class_id=teacher_class_id, broadcast_class=broadcast_class))
    register_identity_routes(app, sm, SimpleNamespace(
        api_auth=api_auth, api_error=api_error, teacher_class_id=teacher_class_id, cameras=cameras,
        broadcast_class=broadcast_class))
    register_camera_routes(app, sm, SimpleNamespace(
        api_auth=api_auth, api_error=api_error, login_required_page=login_required_page,
        teacher_class_id=teacher_class_id, cameras=cameras, sleep=socketio.sleep))

    # ------------------------------------------------------------- Socket.IO
    def socket_principal():
        return current_principal()

    @socketio.on("connect")
    def on_connect(auth=None):
        p = socket_principal()
        if p is None:
            return False  # reject unauthenticated sockets
        if p.role == authz.ROLE_STUDENT:
            join_room(realtime.student_room(p.user_id))
            for cid in p.class_ids:
                join_room(realtime.class_students_room(cid))
            rt = sm.runtime_for_student(p.user_id)
            if rt is not None:
                rt.set_connection(p.user_id, True)
        elif p.role == authz.ROLE_PARENT and p.linked_student_id:
            join_room(realtime.student_room(p.linked_student_id))

    @socketio.on("disconnect")
    def on_disconnect(*args):
        p = socket_principal()
        if p is not None and p.role == authz.ROLE_STUDENT:
            rt = sm.runtime_for_student(p.user_id)
            if rt is not None:
                rt.set_connection(p.user_id, False)

    @socketio.on("join_teacher_room")
    def on_join_teacher_room(data=None):
        p = socket_principal()
        if p is None or p.role not in (authz.ROLE_TEACHER, authz.ROLE_ADMIN):
            emit("error", {"code": "forbidden", "message": MSG_FORBIDDEN})
            return
        requested = (data or {}).get("class_id")
        targets = [requested] if requested is not None else sorted(p.class_ids)
        joined = []
        for cid in targets:
            if authz.can_manage_class(p, cid):
                join_room(realtime.teacher_room(cid))
                joined.append(int(cid))
        if not joined:
            emit("error", {"code": "forbidden", "message": MSG_FORBIDDEN})
            return
        emit("joined", {"rooms": [realtime.teacher_room(c) for c in joined],
                        "message": "Đã kết nối realtime với lớp học"})

    @socketio.on("join_student_room")
    def on_join_student_room(data=None):
        # Identity comes from the authenticated Flask session; any client
        # supplied "name" is ignored.
        p = socket_principal()
        if p is None or p.role != authz.ROLE_STUDENT:
            emit("error", {"code": "forbidden", "message": MSG_FORBIDDEN})
            return
        join_room(realtime.student_room(p.user_id))
        emit("joined", {"rooms": [realtime.student_room(p.user_id)], "message": "Đã kết nối"})

    @socketio.on("request_class_snapshot")
    def on_request_class_snapshot(data=None):
        p = socket_principal()
        cid = (data or {}).get("class_id")
        if p is not None and cid is None and p.class_ids:
            cid = session.get("class_id") if session.get("class_id") in p.class_ids else sorted(p.class_ids)[0]
        if p is None or not authz.can_manage_class(p, cid):
            emit("error", {"code": "forbidden", "message": MSG_FORBIDDEN})
            return
        emit("class_snapshot", class_payload(int(cid)))

    def _reject_client_state(event):
        def handler(data=None):
            p = socket_principal()
            emit("error", {"code": "client_state_rejected", "event": event,
                           "rejected_fields": realtime.rejected_fields(data),
                           "message": "Trạng thái AI, danh tính và điểm chỉ do máy chủ tạo ra."})
            if p is None:
                disconnect()
        return handler

    socketio.on_event("student_data_push", _reject_client_state("student_data_push"))
    socketio.on_event("student_frame_push", _reject_client_state("student_frame_push"))

    @socketio.on("student_tab_switch")
    def on_student_tab_switch(data=None):
        # Client-reported browser signal: logged for the authenticated student
        # with source=client_report. It never changes AI state or score.
        p = socket_principal()
        if p is None or p.role != authz.ROLE_STUDENT:
            emit("error", {"code": "forbidden", "message": MSG_FORBIDDEN})
            return
        rt = sm.runtime_for_student(p.user_id)
        if rt is not None and rt.class_id is not None:
            socketio.emit("log_update", {"type": "client_report", "student_id": p.user_id,
                                         "time": time.time(), "class_id": rt.class_id,
                                         "message": f"{session.get('display_name')} chuyển tab trình duyệt (client báo cáo)"},
                          room=realtime.teacher_room(rt.class_id))

    def broadcaster():
        while True:
            socketio.sleep(1.0)
            try:
                for cid, rt in sm.registry.active_classes().items():
                    socketio.emit("class_snapshot", class_payload(cid), room=realtime.teacher_room(cid))
                for sid in list(sm._personal_sessions.keys()):
                    live = sm.student_live_view(sid)
                    socketio.emit("my_stats_update", realtime.student_self_view(live.get("student")),
                                  room=realtime.student_room(sid))
            except Exception as exc:
                print("[BG] broadcaster error:", exc)

    def retention_sweeper():
        # Scheduled cleanup: expired evidence clips, embeddings, old metadata.
        # First pass right after start-up (a server restarted often still cleans up),
        # then every RETENTION_SWEEP_MINUTES.
        socketio.sleep(5.0)
        while True:
            try:
                sm.retention.run()
            except Exception as exc:
                print("[BG] retention error:", exc)
            socketio.sleep(sm.privacy.retention_sweep_minutes * 60.0)

    if start_background:
        socketio.start_background_task(broadcaster)
        socketio.start_background_task(retention_sweeper)

    app.extensions["focusguard"]["broadcaster"] = broadcaster
    return app, socketio


if __name__ == "__main__":
    application, sio = create_app()
    cfg = application.config["FOCUSGUARD"]
    run_kwargs = {}
    if sio.async_mode == "threading":
        # Werkzeug dev server: only acceptable outside production.
        run_kwargs["allow_unsafe_werkzeug"] = not cfg["IS_PRODUCTION"]
    sio.run(application, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5001")),
            debug=False, use_reloader=False, **run_kwargs)
