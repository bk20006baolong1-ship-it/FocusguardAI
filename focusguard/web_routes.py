"""HTTP routes for the BTC-feedback features (registered by app.create_app).

Teacher event review, needs-review queue, human feedback metrics, evidence
clips, activity context, real-classroom evaluation, consent, privacy
dashboard, audit log, retention and data export.

Every route decides access with focusguard.authz from the server-side
Principal. Identity fields in request bodies (reviewer, annotator, consent
author, "consent": true flags...) are ignored.
"""
import json
import time

from flask import Response, jsonify, request

from . import authz
from .analytics import BEHAVIOR_TYPES
from .cameras.service import CameraError
from .evaluation.real_classroom.models import (CAMERA_POSITIONS, GT_LABELS, GT_LABELS_VI, LIGHTING)
from .evaluation.real_classroom.scenarios import public_pack as scenario_pack
from .evaluation.real_classroom.service import EvaluationEnded, EvaluationError
from .policies.contexts import ACTIVITY_CONTEXTS, CONTEXT_HELP_VI, CONTEXT_LABELS_VI
from .privacy import policy_texts
from .privacy import audit as A
from .privacy.consent import ConsentError, STATE_LABELS_VI
from .privacy.evidence import EvidenceUnavailable, valid_evidence_id
from .privacy.export import build_student_export, to_csv
from .review import REVIEW_LABELS_VI, REVIEW_STATES, ReviewError

NO_STORE = {"Cache-Control": "no-store, private, max-age=0", "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff"}


def register_feedback_routes(app, sm, h):
    """``h`` carries app helpers: current_principal, api_auth, api_error, int_arg,
    teacher_class_id, refresh_gallery."""
    repo = sm.repo
    api_auth, api_error, int_arg = h.api_auth, h.api_error, h.int_arg

    def names_for(class_id):
        return {s["student_id"]: s["display_name"] for s in repo.get_students_in_class(class_id)}

    def body():
        return request.get_json(silent=True) or {}

    def no_store(resp):
        for k, v in NO_STORE.items():
            resp.headers[k] = v
        return resp

    # ------------------------------------------------------------ vocabulary
    @app.route("/api/feedback/vocabulary")
    @api_auth()
    def api_feedback_vocabulary(p):
        return jsonify({
            "contexts": [{"key": c, "label": CONTEXT_LABELS_VI[c], "help": CONTEXT_HELP_VI[c]} for c in ACTIVITY_CONTEXTS],
            "review_states": [{"key": k, "label": REVIEW_LABELS_VI[k]} for k in REVIEW_STATES],
            "gt_labels": [{"key": k, "label": GT_LABELS_VI[k]} for k in GT_LABELS],
            "camera_positions": list(CAMERA_POSITIONS), "lighting": list(LIGHTING),
            "behaviors": list(BEHAVIOR_TYPES), "policy": sm.policy.to_dict(),
        })

    @app.route("/api/privacy/policy")
    @api_auth()
    def api_privacy_policy(p):
        return jsonify(dict(policy_texts(sm.privacy), settings=sm.privacy.public_dict()))

    # ------------------------------------------------------ activity context
    @app.route("/api/teacher/activity_context", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_set_activity_context(p):
        data = body()
        cid = h.teacher_class_id(p, data) if data.get("class_id") is not None else h.teacher_class_id(p)
        if cid is None:
            return api_error("Bạn chưa được phân công lớp này.")
        try:
            seg = sm.set_activity_context(cid, data.get("context"), actor=p)
        except ValueError:
            return api_error("Hoạt động không hợp lệ.", 400)
        except LookupError:
            return api_error("Chưa có buổi học đang diễn ra.", 409)
        h.broadcast_class(cid)
        return jsonify({"status": "success", "class_id": cid, "context": seg.get("context"),
                        "label": CONTEXT_LABELS_VI.get(seg.get("context"), "Không chọn hoạt động")})

    # ------------------------------------------------------------- events
    def _event_or_error(p, event_id):
        ev = repo.get_focus_event(event_id)
        if not ev:
            return None, api_error("Không tìm thấy sự kiện.", 404)
        if not authz.can_review_class_events(p, ev.get("class_id")):
            return None, api_error()
        return ev, None

    @app.route("/api/teacher/events")
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_events(p):
        cid = h.teacher_class_id(p)
        if cid is None or not authz.can_review_class_events(p, cid):
            return api_error("Bạn chưa được phân công lớp này.")
        etype = (request.args.get("type") or "").upper() or None
        status = (request.args.get("review_status") or "").upper() or None
        ctx = (request.args.get("context") or "").upper() or None
        date = (request.args.get("date") or "").strip() or None
        if etype and etype not in BEHAVIOR_TYPES:
            return api_error("Loại sự kiện không hợp lệ.", 400)
        if status and status not in REVIEW_STATES:
            return api_error("Trạng thái xem lại không hợp lệ.", 400)
        if ctx and ctx not in ACTIVITY_CONTEXTS + ("NONE",):
            return api_error("Hoạt động không hợp lệ.", 400)
        if date and (len(date) != 10 or date[4] != "-" or date[7] != "-"):
            return api_error("Ngày phải có dạng YYYY-MM-DD.", 400)
        events = sm.reviews.class_events(cid, names_for(cid), student_id=int_arg("student_id"), date=date,
                                         event_type=etype, review_status=status, context=ctx,
                                         class_session_id=int_arg("class_session_id"))
        return jsonify({"status": "success", "class_id": cid, "events": events,
                        "students": [{"student_id": k, "name": v} for k, v in sorted(names_for(cid).items(),
                                                                                       key=lambda kv: kv[1])],
                        "evidence_enabled": sm.evidence.enabled})

    @app.route("/api/teacher/events/<int:event_id>")
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_event_detail(p, event_id):
        ev, err = _event_or_error(p, event_id)
        if err:
            return err
        rows = [r for r in sm.reviews.class_events(ev["class_id"], names_for(ev["class_id"]),
                                                   class_session_id=ev.get("class_session_id"))
                if r["event_id"] == event_id]
        if not rows:
            return api_error("Không tìm thấy sự kiện.", 404)
        detail = rows[0]
        detail["interpretation"] = (ev.get("metadata") or {}).get("interpretation")
        detail["history"] = sm.reviews.history(event_id)
        return no_store(jsonify({"status": "success", "event": detail}))

    @app.route("/api/teacher/events/<int:event_id>/review", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_event_review(p, event_id):
        ev, err = _event_or_error(p, event_id)
        if err:
            return err
        data = body()
        try:
            row = sm.reviews.review(p, ev, (data.get("verdict") or "").upper(), data.get("note"))
        except ReviewError as exc:
            return api_error(str(exc), 400)
        return jsonify({"status": "success", "review": row, "review_label": REVIEW_LABELS_VI[row["verdict"]]})

    @app.route("/api/teacher/review_queue")
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_review_queue(p):
        cid = h.teacher_class_id(p)
        if cid is None or not authz.can_review_class_events(p, cid):
            return api_error("Bạn chưa được phân công lớp này.")
        an = sm.config.analytics
        return jsonify({"status": "success", "class_id": cid, "recent_days": an.review_recent_days,
                        "queue": sm.reviews.review_queue(cid, names_for(cid), recent_seconds=an.review_recent_days * 86400,
                                                         limit=an.review_queue_limit)})

    @app.route("/api/teacher/review_metrics")
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_review_metrics(p):
        cid = h.teacher_class_id(p)
        if cid is None or not authz.can_review_class_events(p, cid):
            return api_error("Bạn chưa được phân công lớp này.")
        return jsonify(dict(sm.reviews.class_feedback_metrics(cid, min_reviews=sm.config.analytics.min_reviews_for_rates),
                            status="success", class_id=cid))

    # ------------------------------------------------------------ evidence
    def _clip_or_error(evidence_id, include_expired=False):
        if not valid_evidence_id(evidence_id):
            return None, api_error("Không tìm thấy đoạn minh chứng.", 404)
        clip = sm.evidence.get(evidence_id, include_expired=include_expired)
        if clip is None:
            return None, api_error("Không tìm thấy đoạn minh chứng.", 404)
        return clip, None

    @app.route("/api/teacher/evidence/<evidence_id>")
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_evidence(p, evidence_id):
        clip, err = _clip_or_error(evidence_id)
        if err:
            return err
        if not authz.can_view_evidence(p, clip):
            return api_error()
        try:
            payload = sm.evidence.frames_payload(clip, p)
        except EvidenceUnavailable:
            return api_error("Tệp minh chứng bị thiếu hoặc hỏng nên đã được gỡ khỏi hệ thống (đã ghi nhật ký).", 410)
        except Exception:
            app.logger.exception("evidence read failed")
            return api_error("Không đọc được đoạn minh chứng.", 500)
        return no_store(jsonify(dict(payload, status="success")))

    @app.route("/api/evidence/<evidence_id>/delete", methods=["POST", "DELETE"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    def api_evidence_delete(p, evidence_id):
        clip, err = _clip_or_error(evidence_id, include_expired=True)
        if err:
            return err
        if not authz.can_delete_evidence(p, clip):
            return api_error()
        sm.evidence.delete(clip, p, reason="manual")
        return jsonify({"status": "success", "message": "Đã xóa đoạn minh chứng."})

    # ---------------------------------------------------------- evaluation
    def _eval_or_error(p, eid):
        es = sm.evaluation.get(eid)
        if es is None:
            return None, api_error("Không tìm thấy buổi đánh giá.", 404)
        if not authz.can_run_evaluation(p, es.get("class_id")):
            return None, api_error()
        return es, None

    def _eval_view(es):
        eid = es["evaluation_session_id"]
        return {"session": es, "annotations": repo.get_annotations(evaluation_session_id=eid),
                "participants": [{"student_id": s["student_id"], "name": s["display_name"]}
                                 for s in sm.evaluation.consented_students(es["class_id"])],
                "scenario_runs": sm.evaluation.scenario_runs(eid), "scenario_pack": scenario_pack()}

    @app.route("/api/teacher/evaluations", methods=["GET", "POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_evaluations(p):
        data = body() if request.method == "POST" else {}
        cid = h.teacher_class_id(p, data) if data.get("class_id") is not None else h.teacher_class_id(p)
        if cid is None or not authz.can_run_evaluation(p, cid):
            return api_error("Bạn chưa được phân công lớp này.")
        if request.method == "GET":
            return jsonify({"status": "success", "class_id": cid, "sessions": sm.evaluation.list(cid),
                            "participants": [{"student_id": s["student_id"], "name": s["display_name"]}
                                             for s in sm.evaluation.consented_students(cid)],
                            "class_session_active": sm.class_runtime(cid) is not None,
                            "scenario_pack": scenario_pack()})
        # Camera actually used: the running classroom session's camera, else the selected/default one.
        rt = sm.class_runtime(cid)
        camera_meta = getattr(rt, "camera", None) if rt is not None else None
        if camera_meta is None and (rt is None or rt.mode == "classroom"):
            try:
                cam, reason = sm.cameras.resolve_for_class(p, cid, data.get("camera_id") or None)
            except CameraError as exc:
                return api_error(exc.message, exc.status)
            camera_meta = None if reason == "CHOOSE" else sm.cameras.evaluation_metadata(cam)
        try:
            es = sm.evaluation.start(p, cid, data, camera_meta=camera_meta)
        except EvaluationError as exc:
            return api_error(str(exc), 400)
        return jsonify(dict(_eval_view(es), status="success"))

    @app.route("/api/teacher/evaluations/<eid>")
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_evaluation(p, eid):
        es, err = _eval_or_error(p, eid)
        if err:
            return err
        return jsonify(dict(_eval_view(es), status="success"))

    @app.route("/api/teacher/evaluations/<eid>/context", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_evaluation_context(p, eid):
        es, err = _eval_or_error(p, eid)
        if err:
            return err
        ctx = (body().get("context") or "").upper() or None
        if ctx is not None and ctx not in ACTIVITY_CONTEXTS:
            return api_error("Hoạt động không hợp lệ.", 400)
        try:
            sm.evaluation.set_context(p, eid, ctx)
        except EvaluationError as exc:
            return api_error(str(exc), 409)
        return jsonify({"status": "success", "context": ctx})

    @app.route("/api/teacher/evaluations/<eid>/scenario", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_evaluation_scenario(p, eid):
        """{"action": "start", "scenario_id": "05_phone", "apply_context": true} or {"action": "stop"}."""
        es, err = _eval_or_error(p, eid)
        if err:
            return err
        data = body()
        try:
            if data.get("action") == "stop":
                run = sm.evaluation.stop_scenario(p, eid)
            elif data.get("action") == "start":
                run = sm.evaluation.start_scenario(p, eid, data.get("scenario_id"),
                                                   apply_context=data.get("apply_context") is True,
                                                   note=data.get("note"))
            else:
                return api_error("Thao tác không hợp lệ.", 400)
        except EvaluationEnded as exc:
            return api_error(str(exc), 409)
        except EvaluationError as exc:
            return api_error(str(exc), 400)
        return jsonify(dict(_eval_view(sm.evaluation.get(eid)), status="success", run=run))

    @app.route("/api/teacher/evaluations/<eid>/annotations", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_evaluation_annotate(p, eid):
        es, err = _eval_or_error(p, eid)
        if err:
            return err
        try:
            ann = sm.evaluation.annotate(p, eid, body())
        except EvaluationError as exc:
            return api_error(str(exc), 400)
        return jsonify({"status": "success", "annotation": ann})

    @app.route("/api/teacher/evaluations/<eid>/end", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_evaluation_end(p, eid):
        es, err = _eval_or_error(p, eid)
        if err:
            return err
        try:
            es = sm.evaluation.end(p, eid)
        except EvaluationError as exc:
            return api_error(str(exc), 409)
        return jsonify(dict(_eval_view(es), status="success"))

    @app.route("/api/teacher/evaluations/<eid>/metrics")
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_evaluation_metrics(p, eid):
        es, err = _eval_or_error(p, eid)
        if err:
            return err
        m = sm.evaluation.metrics(eid)
        return jsonify(dict(m, status="success", evaluation_status=m["status"]))

    @app.route("/api/teacher/evaluations/<eid>/export")
    @api_auth(authz.ROLE_TEACHER)
    def api_teacher_evaluation_export(p, eid):
        es, err = _eval_or_error(p, eid)
        if err:
            return err
        pkg = sm.evaluation.package(eid)
        sm.audit.record(A.DATA_EXPORTED, p, None, {"evaluation_session_id": eid, "kind": "evaluation_package"})
        resp = Response(json.dumps(pkg, ensure_ascii=False), mimetype="application/json")
        resp.headers["Content-Disposition"] = f'attachment; filename="evaluation-{eid}.json"'
        return no_store(resp)

    # ------------------------------------------------------------- consent
    def _student(student_id):
        u = repo.get_user_by_id(student_id)
        return u if u and u.get("role") == "student" else None

    def _privacy_row(s, now):
        st = sm.consent.status(s["id"], now)
        enr = repo.get_face_enrollment(s["id"])
        rec = st["record"] or {}
        return {"student_id": s["id"], "name": s["display_name"], "class_name": s.get("class_name"),
                "class_id": s.get("class_id"), "consent_state": st["state"], "consent_label": st["state_label"],
                "scopes": st["scopes"], "policy_outdated": st["policy_outdated"],
                "consent_policy_version": rec.get("policy_version"), "current_policy_version": st["policy_version"],
                "guardian_name": rec.get("guardian_name"), "guardian_relationship": rec.get("guardian_relationship"),
                "accepted_at": rec.get("accepted_at"), "expires_at": rec.get("expires_at"),
                "revoked_at": rec.get("revoked_at"),
                "face_embedding_exists": enr["has_face"], "last_enrollment": enr["enrolled_at"],
                "face_expires_at": (enr["enrolled_at"] + sm.privacy.face_embedding_retention_days * 86400
                                    if enr["enrolled_at"] else None),
                "face_without_consent": enr["has_face"] and not st["scopes"].get("face_recognition"),
                "evidence_count": sm.evidence.count_for_student(s["id"])}

    @app.route("/api/admin/privacy/students")
    @api_auth(authz.ROLE_ADMIN)
    def api_admin_privacy_students(p):
        now = time.time()
        rows = [_privacy_row(u, now) for u in repo.get_users_list() if u.get("role") == "student"]
        return jsonify({"status": "success", "students": rows, "policy": policy_texts(sm.privacy),
                        "settings": sm.privacy.public_dict(),
                        "state_labels": STATE_LABELS_VI})

    @app.route("/api/admin/consent/<int:student_id>", methods=["GET", "POST"])
    @api_auth(authz.ROLE_ADMIN)
    def api_admin_consent(p, student_id):
        if not authz.can_manage_privacy(p):
            return api_error()
        if _student(student_id) is None:
            return api_error("Không tìm thấy học sinh.", 404)
        if request.method == "GET":
            return jsonify({"status": "success", "consent": sm.consent.status(student_id),
                            "history": sm.consent.history(student_id)})
        data = body()
        scopes = data.get("scopes") if isinstance(data.get("scopes"), dict) else {}
        expires = data.get("expires_at")
        try:
            st = sm.consent.grant(p, student_id, guardian_name=data.get("guardian_name"),
                                  guardian_relationship=data.get("guardian_relationship"), scopes=scopes,
                                  student_is_adult=data.get("student_is_adult") is True,
                                  expires_at=expires if expires not in (None, "") else None, note=data.get("note"))
        except ConsentError as exc:
            return api_error(str(exc), 400)
        return jsonify({"status": "success", "consent": st, "message": "Đã ghi nhận đồng ý."})

    @app.route("/api/consent/<int:student_id>/revoke", methods=["POST"])
    @api_auth(authz.ROLE_ADMIN, authz.ROLE_PARENT)
    def api_consent_revoke(p, student_id):
        if not authz.can_revoke_consent(p, student_id) or _student(student_id) is None:
            return api_error()
        try:
            res = sm.consent.revoke(p, student_id, reason=body().get("reason"))
        except ConsentError as exc:
            return api_error(str(exc), 409)
        return jsonify({"status": "success", "result": res,
                        "message": "Đã thu hồi đồng ý. Nhận diện khuôn mặt đã dừng và dữ liệu khuôn mặt đã được xóa."})

    @app.route("/api/admin/privacy/<int:student_id>/delete_face", methods=["POST"])
    @api_auth(authz.ROLE_ADMIN)
    def api_admin_delete_face(p, student_id):
        if _student(student_id) is None:
            return api_error("Không tìm thấy học sinh.", 404)
        existed = repo.get_face_enrollment(student_id)["has_face"]
        repo.save_face_embedding(student_id, None)
        if existed:
            sm.audit.record(A.FACE_DELETED, p, student_id, {"reason": "admin_request"})
        sm._face_changed(student_id)
        return jsonify({"status": "success", "deleted": existed, "message": "Đã xóa dữ liệu khuôn mặt."})

    @app.route("/api/admin/privacy/<int:student_id>/delete_evidence", methods=["POST"])
    @api_auth(authz.ROLE_ADMIN)
    def api_admin_delete_evidence(p, student_id):
        if _student(student_id) is None:
            return api_error("Không tìm thấy học sinh.", 404)
        n = sm.evidence.delete_for_student(student_id, p, reason="admin_request")
        return jsonify({"status": "success", "deleted": n, "message": f"Đã xóa {n} đoạn minh chứng."})

    @app.route("/api/admin/audit")
    @api_auth(authz.ROLE_ADMIN)
    def api_admin_audit(p):
        action = (request.args.get("action") or "").upper() or None
        if action and action not in A.ACTIONS:
            return api_error("Loại thao tác không hợp lệ.", 400)
        rows = sm.audit.list(student_id=int_arg("student_id"), action=action, limit=300)
        return no_store(jsonify({"status": "success", "entries": rows}))

    @app.route("/api/admin/retention/run", methods=["POST"])
    @api_auth(authz.ROLE_ADMIN)
    def api_admin_retention_run(p):
        return jsonify({"status": "success", "report": sm.retention.run(actor=p)})

    @app.route("/api/parent/privacy")
    @api_auth(authz.ROLE_PARENT)
    def api_parent_privacy(p):
        sid = p.linked_student_id
        if sid is None or _student(sid) is None:
            return api_error("Tài khoản chưa được liên kết với học sinh.", 404)
        st = sm.consent.status(sid)
        return jsonify({"status": "success", "student_id": sid, "consent_state": st["state"],
                        "consent_label": st["state_label"], "scopes": st["scopes"],
                        "face_embedding_exists": repo.get_face_enrollment(sid)["has_face"],
                        "policy": policy_texts(sm.privacy)})

    # -------------------------------------------------------------- export
    @app.route("/api/export/student/<int:student_id>")
    @api_auth(authz.ROLE_ADMIN, authz.ROLE_TEACHER, authz.ROLE_PARENT)
    def api_export_student(p, student_id):
        user = _student(student_id)
        if user is None:
            return api_error("Không tìm thấy học sinh.", 404)
        if not authz.can_export_student(p, student_id, user.get("class_id")):
            return api_error()
        fmt = (request.args.get("format") or "json").lower()
        section = (request.args.get("section") or "events").lower()
        data = build_student_export(repo, student_id, include_teacher_notes=p.role != authz.ROLE_PARENT)
        sm.audit.record(A.DATA_EXPORTED, p, student_id, {"format": fmt, "section": section if fmt == "csv" else "all"})
        if fmt == "csv":
            try:
                text = to_csv(data, section)
            except ValueError:
                return api_error("Mục xuất không hợp lệ.", 400)
            resp = Response(text, mimetype="text/csv; charset=utf-8")
            resp.headers["Content-Disposition"] = f'attachment; filename="hoc-sinh-{student_id}-{section}.csv"'
            return no_store(resp)
        if fmt != "json":
            return api_error("Định dạng không hợp lệ.", 400)
        resp = Response(json.dumps(data, ensure_ascii=False, default=str), mimetype="application/json")
        resp.headers["Content-Disposition"] = f'attachment; filename="hoc-sinh-{student_id}.json"'
        return no_store(resp)
