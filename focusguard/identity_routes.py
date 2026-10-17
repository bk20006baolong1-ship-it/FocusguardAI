"""HTTP routes: classroom initialization, teacher tagging / correction, roster, attendance override.

    GET  /api/teacher/classroom/identity?class_id=        tracks, roster, enrollment, counts, conflicts
    POST /api/teacher/classroom/initialize                 start (or re-enter) "Khởi tạo danh sách"
    POST /api/teacher/classroom/begin                      "Hoàn tất danh sách / Bắt đầu buổi học"
    POST /api/teacher/classroom/tag                        tag / correct a person track (one student, one track)
    POST /api/teacher/classroom/untag                      remove a tag
    POST /api/teacher/classroom/confirm_suggestions        accept the strict high-confidence AI suggestions
    POST /api/teacher/attendance                           PRESENT / LATE / ABSENT override (live or ended class)

Only teachers ASSIGNED to the class may tag or correct (focusguard.authz).
``confirmed_by`` and ``identity_source`` are decided by the server: values in the
request body are ignored. Responses never contain embeddings or images.
"""
from flask import jsonify, request

from . import authz
from . import runtime_state as rs
from .classroom_identity import TagError
from .privacy import audit as A
from .session_runtime import MODE_CLASSROOM, PHASE_INITIALIZING

ATTENDANCE_CHOICES = (rs.ATT_PRESENT, rs.ATT_LATE, rs.ATT_ABSENT)


def can_tag(p, class_id) -> bool:
    """Tagging decides who is who in a class: the class's own teachers only."""
    return authz.can_review_class_events(p, class_id)


def register_identity_routes(app, sm, h):
    api_auth, api_error = h.api_auth, h.api_error
    repo = sm.repo

    def body():
        return request.get_json(silent=True) or {}

    def class_for(p, data=None):
        data = data if data is not None else {}
        cid = h.teacher_class_id(p, data) if data.get("class_id") is not None else h.teacher_class_id(p)
        return cid if cid is not None and can_tag(p, cid) else None

    def coordinator(cid):
        rt = sm.class_runtime(cid)
        if rt is None or rt.mode != MODE_CLASSROOM:
            return rt, None
        hub = h.cameras
        pipeline = hub.pipeline
        key = hub.key
        if pipeline is None or key is None or tuple(key[:3]) != ("classroom", cid, rt.class_session_id):
            return rt, None
        return rt, getattr(pipeline, "coord", None)

    def roster_rows(cid, rt, snap):
        live = {s["student_id"]: s for s in (rt.class_view(sm.clock())["students"] if rt is not None else [])}
        by_id = {r["student_id"]: r for r in (snap or {}).get("roster", [])}
        store = getattr(rt, "identity_store", None) if rt is not None else None
        kept = {r.student_id for r in store.detached()} if store is not None else set()     # session tags
        out = []
        for s in repo.get_students_in_class(cid):
            sid = s["student_id"]
            r = by_id.get(sid, {})
            lv = live.get(sid, {})
            out.append({"student_id": sid, "name": s["display_name"], "track_id": r.get("track_id"),
                        "identity_source": r.get("identity_source") or lv.get("identity_source"),
                        "review_required": bool(r.get("review_required") or lv.get("identity_review")),
                        "face_consent": sm.faces.consent_active(sid),
                        "enrolling": bool(r.get("enrolling")),
                        "restored": bool(r.get("restored")),
                        # the teacher's tag is kept by the session while the camera finds the student again
                        "awaiting_restore": bool(r.get("awaiting_restore")) or (not r and sid in kept),
                        "enrollment": sm.faces.status(sid),
                        "attendance_status": lv.get("attendance_status"),
                        "attendance_source": lv.get("attendance_source"),
                        "visibility_status": lv.get("visibility_status")})
        return out

    @app.route("/api/teacher/classroom/identity")
    @api_auth(authz.ROLE_TEACHER)
    def api_classroom_identity(p):
        cid = class_for(p)
        if cid is None:
            return api_error("Bạn chưa được phân công lớp này.")
        rt, coord = coordinator(cid)
        snap = coord.snapshot() if coord is not None else None
        stats = rt.class_view(sm.clock())["statistics"] if rt is not None else {}
        return jsonify({"status": "success", "class_id": cid,
                        "class_session_id": rt.class_session_id if rt is not None else None,
                        "active": rt is not None, "mode": rt.mode if rt is not None else None,
                        "phase": rt.phase if rt is not None else None,
                        "camera_open": coord is not None,
                        "tracks": (snap or {}).get("tracks", []), "roster": roster_rows(cid, rt, snap),
                        "unknown_tracks": (snap or {}).get("unknown_tracks", []),
                        "conflicts": (snap or {}).get("conflicts", []),
                        "warnings": (snap or {}).get("warnings", []),
                        "counts": {"enrolled": stats.get("enrolled"), "confirmed": stats.get("confirmed"),
                                   "unidentified": stats.get("unidentified_present"),
                                   "not_seen": stats.get("not_seen"), "present": stats.get("present")}})

    @app.route("/api/teacher/classroom/initialize", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_classroom_initialize(p):
        cid = class_for(p, body())
        if cid is None:
            return api_error("Bạn chưa được phân công lớp này.")
        rt = sm.class_runtime(cid)
        if rt is None or rt.mode != MODE_CLASSROOM:
            return api_error("Hãy bắt đầu buổi học bằng camera lớp học trước.", 409)
        changed = rt.begin_initialization(sm.clock())
        h.broadcast_class(cid)
        return jsonify({"status": "success", "phase": rt.phase, "changed": changed})

    @app.route("/api/teacher/classroom/begin", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_classroom_begin(p):
        cid = class_for(p, body())
        if cid is None:
            return api_error("Bạn chưa được phân công lớp này.")
        rt = sm.class_runtime(cid)
        if rt is None:
            return api_error("Chưa có buổi học đang diễn ra.", 409)
        changed = rt.begin_monitoring(sm.clock())
        h.broadcast_class(cid)
        return jsonify({"status": "success", "phase": rt.phase, "changed": changed,
                        "message": "Đã hoàn tất danh sách. Bắt đầu theo dõi buổi học."})

    def _tag_target(p, data):
        cid = class_for(p, data)
        if cid is None:
            return None, None, api_error("Bạn chưa được phân công lớp này.")
        rt, coord = coordinator(cid)
        if coord is None:
            return None, None, api_error("Hãy mở camera lớp học trên trang Giám sát để gắn tên.", 409)
        try:
            track_id = int(data.get("track_id"))
        except (TypeError, ValueError):
            return None, None, api_error("Thiếu người cần gắn tên.", 400)
        return cid, (rt, coord, track_id), None

    @app.route("/api/teacher/classroom/tag", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_classroom_tag(p):
        data = body()
        cid, target, err = _tag_target(p, data)
        if err:
            return err
        rt, coord, track_id = target
        try:
            student_id = int(data.get("student_id"))
        except (TypeError, ValueError):
            return api_error("Hãy chọn học sinh.", 400)
        if student_id not in coord.roster:
            return api_error("Học sinh không thuộc lớp này.", 400)
        if not coord.has_track(track_id):
            return api_error("Người này không còn trong khung hình. Hãy chọn lại.", 404)
        owner = coord.identity.track_for_student(student_id)
        if owner is not None and owner != track_id:
            return api_error(f"Học sinh này đã được gắn cho Người #{owner}. Gỡ gắn ở người đó trước.", 409)
        try:
            res = coord.tag(p, track_id, student_id, sm.clock(), reason=data.get("reason"))
        except TagError as exc:
            return api_error(exc.message, exc.status)
        h.broadcast_class(cid)
        res["student_name"] = coord.roster.get(student_id)
        res["enrollment_status"] = sm.faces.status(student_id)
        return jsonify(dict(res, status="success"))

    @app.route("/api/teacher/classroom/untag", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_classroom_untag(p):
        data = body()
        cid, target, err = _tag_target(p, data)
        if err:
            return err
        _rt, coord, track_id = target
        try:
            res = coord.untag(p, track_id, sm.clock())
        except TagError as exc:
            return api_error(exc.message, exc.status)
        h.broadcast_class(cid)
        return jsonify(dict(res, status="success"))

    @app.route("/api/teacher/classroom/confirm_suggestions", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_classroom_confirm(p):
        cid = class_for(p, body())
        if cid is None:
            return api_error("Bạn chưa được phân công lớp này.")
        _rt, coord = coordinator(cid)
        if coord is None:
            return api_error("Hãy mở camera lớp học trên trang Giám sát.", 409)
        done = coord.confirm_suggestions(p, sm.clock())
        h.broadcast_class(cid)
        return jsonify({"status": "success", "confirmed": len(done),
                        "message": f"Đã xác nhận {len(done)} đề xuất độ tin cậy cao."})

    @app.route("/api/teacher/attendance", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_attendance(p):
        data = body()
        cid = class_for(p, data)
        if cid is None:
            return api_error("Bạn chưa được phân công lớp này.")
        status = str(data.get("status") or "").upper()
        if status not in ATTENDANCE_CHOICES:
            return api_error("Trạng thái điểm danh không hợp lệ.", 400)
        try:
            sid = int(data.get("student_id"))
        except (TypeError, ValueError):
            return api_error("Thiếu học sinh.", 400)
        if sid not in {s["student_id"] for s in repo.get_students_in_class(cid)}:
            return api_error("Học sinh không thuộc lớp này.", 400)
        now = sm.clock()
        rt = sm.class_runtime(cid)
        cs_id = data.get("class_session_id")
        if cs_id in (None, "", "live"):
            if rt is None:
                return api_error("Chưa có buổi học đang diễn ra.", 409)
            cs_id = rt.class_session_id
            if rt.teacher_set_attendance(sid, status, now, by=p.user_id) is None:
                # ended meanwhile, or a student added after the start: correct the stored record
                repo.update_session_student_attendance(cs_id, sid, status, rs.ATT_SRC_TEACHER_CORRECTED)
        else:
            try:
                cs_id = int(cs_id)
            except (TypeError, ValueError):
                return api_error("Buổi học không hợp lệ.", 400)
            row = repo.get_class_session(cs_id)
            if not row or int(row["class_id"]) != cid or not authz.can_view_class_session(p, row):
                return api_error("Không tìm thấy buổi học.", 404)
            if rt is None or rt.class_session_id != cs_id or \
                    rt.teacher_set_attendance(sid, status, now, by=p.user_id) is None:
                repo.update_session_student_attendance(cs_id, sid, status, rs.ATT_SRC_TEACHER_CORRECTED)
        repo.save_attendance_record({"class_session_id": cs_id, "class_id": cid, "student_id": sid, "status": status,
                                     "source": rs.ATT_SRC_TEACHER_CORRECTED, "updated_by": p.user_id,
                                     "updated_at": now, "note": (str(data.get("note") or "")[:200] or None)})
        sm.audit.record(A.ATTENDANCE_TEACHER_CORRECTED, p, sid, {"class_id": cid, "class_session_id": cs_id,
                                                                  "status": status})
        h.broadcast_class(cid)
        return jsonify({"status": "success", "student_id": sid, "attendance_status": status,
                        "attendance_source": rs.ATT_SRC_TEACHER_CORRECTED, "class_session_id": cs_id})

    # keep the INITIALIZING constant importable for templates / tests
    app.extensions.setdefault("focusguard", {})["phase_initializing"] = PHASE_INITIALIZING
