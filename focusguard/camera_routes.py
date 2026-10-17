"""HTTP routes for account camera settings (registered by app.create_app).

    GET    /settings/cameras                                 page (teacher, admin)
    GET    /api/settings/cameras                             my cameras + class (school) cameras
    POST   /api/settings/cameras/discover                    start a LAN discovery job
    GET    /api/settings/cameras/discovery/<job>             job progress / results
    POST   /api/settings/cameras/discovery/<job>/stop        stop it
    POST   /api/settings/cameras/test                        easy connect / manual test (+ test token)
    POST   /api/settings/cameras                             save a tested camera
    PATCH  /api/settings/cameras/<id>                        rename / update credentials
    DELETE /api/settings/cameras/<id>
    POST   /api/settings/cameras/<id>/test                   re-test a saved camera
    POST   /api/settings/cameras/<id>/default                make it the default camera
    POST   /api/settings/cameras/<id>/assign-class           (admin) assign / unassign a class
    POST   /api/settings/cameras/<id>/preview/start          preview a saved camera
    POST   /api/settings/cameras/<id>/preview/stop
    POST   /api/settings/cameras/preview/start               preview a tested, unsaved camera
    GET    /api/settings/cameras/preview/<pid>/stream        MJPEG (bounded, no analysis)
    GET    /api/settings/cameras/preview/<pid>/health
    POST   /api/settings/cameras/preview/<pid>/stop
    GET    /api/teacher/class_camera                         camera that a class session would use

Every route decides access server-side (focusguard.authz via CameraService).
Students and parents get 403 everywhere. Responses never contain a camera
password / verification code or a credential-bearing URL; errors are redacted.
"""
import functools

from flask import Response, jsonify, render_template, request

from . import authz
from .cameras.netguard import HostNotAllowed
from .cameras.redact import safe_error
from .cameras.service import CameraError

NO_STORE = {"Cache-Control": "no-store, private, max-age=0", "Pragma": "no-cache"}


def register_camera_routes(app, sm, h):
    """``h``: api_auth, api_error, login_required_page, teacher_class_id, cameras (CameraHub), sleep."""
    svc = sm.cameras
    api_auth, api_error = h.api_auth, h.api_error
    svc.startup_check(app.config["FOCUSGUARD"]["IS_PRODUCTION"])

    def body():
        return request.get_json(silent=True) or {}

    def handled(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                resp = fn(*args, **kwargs)
            except CameraError as exc:
                return api_error(exc.message, exc.status)
            except HostNotAllowed as exc:
                return api_error(str(exc), 400)
            except Exception as exc:                       # never leak a credential in an error
                app.logger.error("camera route error: %s", safe_error(exc))
                return api_error("Không thực hiện được thao tác với camera.", 500)
            if isinstance(resp, Response):
                for k, v in NO_STORE.items():
                    resp.headers.setdefault(k, v)
            return resp
        return wrapper

    def ok(**payload):
        return jsonify(dict(payload, status="success"))

    def busy(cam):
        """A webcam already used by a running class pipeline cannot be previewed at the same time."""
        if cam is not None and cam.get("source_type") != "WEBCAM":
            return False
        idx = (cam or {}).get("device_index") or 0
        return h.cameras.uses_device(lambda src: getattr(src, "source_type", None) == "WEBCAM"
                                     and getattr(src, "index", None) == idx)

    # ------------------------------------------------------------------ page
    @app.route("/settings/cameras")
    @h.login_required_page()
    def settings_cameras(p):
        if not authz.can_manage_cameras(p):
            return render_template("error.html", code=403, title="Không có quyền truy cập",
                                   message="Chỉ giáo viên và quản trị viên quản lý camera.", logged_in=True), 403
        classes = sm.repo.get_classes_list() if p.is_admin else \
            [{"id": c, "class_name": sm.repo.get_class_name(c)} for c in sorted(p.class_ids)]
        return render_template("cameras.html", active_tab="cameras", classes=classes, is_admin=p.is_admin)

    # ------------------------------------------------------------------- API
    @app.route("/api/settings/cameras", methods=["GET"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_list(p):
        return ok(**svc.list_for(p))

    @app.route("/api/settings/cameras/discover", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_discover(p):
        return ok(job=svc.discover(p))

    @app.route("/api/settings/cameras/discovery/<job_id>", methods=["GET"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_discovery(p, job_id):
        return ok(job=svc.discovery_status(p, job_id[:64]))

    @app.route("/api/settings/cameras/discovery/<job_id>/stop", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_discovery_stop(p, job_id):
        return ok(job=svc.stop_discovery(p, job_id[:64]))

    @app.route("/api/settings/cameras/test", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_test(p):
        data = body()
        data.pop("camera_id", None)              # saved cameras: /<id>/test
        return ok(result=svc.test_connection(p, data))

    @app.route("/api/settings/cameras", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_save(p):
        return ok(camera=svc.save(p, body()), message="Đã lưu camera.")

    @app.route("/api/settings/cameras/<camera_id>", methods=["PATCH"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_update(p, camera_id):
        return ok(camera=svc.update(p, camera_id[:64], body()), message="Đã cập nhật camera.")

    @app.route("/api/settings/cameras/<camera_id>", methods=["DELETE"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_delete(p, camera_id):
        return ok(**svc.delete(p, camera_id[:64]), message="Đã xóa camera.")

    @app.route("/api/settings/cameras/<camera_id>/test", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_retest(p, camera_id):
        data = body()
        data["camera_id"] = camera_id[:64]
        for k in ("discovery_id", "url", "host", "source_type"):     # a saved camera keeps its address
            data.pop(k, None)
        return ok(result=svc.test_connection(p, data))

    @app.route("/api/settings/cameras/<camera_id>/default", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_default(p, camera_id):
        return ok(camera=svc.set_default(p, camera_id[:64]), message="Đã đặt làm camera mặc định.")

    @app.route("/api/settings/cameras/<camera_id>/assign-class", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_assign(p, camera_id):
        data = body()
        return ok(camera=svc.assign_class(p, camera_id[:64], data.get("class_id"), unassign=bool(data.get("unassign"))),
                  message="Đã cập nhật lớp dùng camera.")

    # --------------------------------------------------------------- preview
    @app.route("/api/settings/cameras/<camera_id>/preview/start", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_preview_saved(p, camera_id):
        return ok(preview=svc.start_preview(p, camera_id=camera_id[:64], busy=busy))

    @app.route("/api/settings/cameras/<camera_id>/preview/stop", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_preview_saved_stop(p, camera_id):
        svc.stop_previews(camera_id=camera_id[:64], owner=p.user_id)
        return ok(stopped=True)

    @app.route("/api/settings/cameras/preview/start", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_preview_test(p):
        return ok(preview=svc.start_preview(p, test_token=body().get("test_token"), busy=busy))

    @app.route("/api/settings/cameras/preview/<preview_id>/health")
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_preview_health(p, preview_id):
        return ok(health=svc.preview_health(p, preview_id[:64]))

    @app.route("/api/settings/cameras/preview/<preview_id>/stop", methods=["POST"])
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_preview_stop(p, preview_id):
        return ok(**svc.stop_preview(p, preview_id[:64]))

    @app.route("/api/settings/cameras/preview/<preview_id>/stream")
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_cameras_preview_stream(p, preview_id):
        frames = svc.preview_frames(p, preview_id[:64], sleep=h.sleep)
        svc.preview(p, preview_id[:64])          # 404 now (not mid-stream) if it does not exist

        def gen():
            try:
                for jpeg in frames:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            except GeneratorExit:
                pass
            finally:
                frames.close()                   # releases the preview camera at once
        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    # ------------------------------------------------------ class camera
    @app.route("/api/teacher/class_camera")
    @api_auth(authz.ROLE_TEACHER, authz.ROLE_ADMIN)
    @handled
    def api_teacher_class_camera(p):
        cid = h.teacher_class_id(p)
        if cid is None:
            return api_error("Bạn chưa được phân công lớp này.")
        cam, reason = svc.resolve_for_class(p, cid, request.args.get("camera_id") or None)
        options = [svc.public(c, p) for c in svc.usable_cameras(p, cid)]
        rt = sm.class_runtime(cid)
        live = getattr(rt, "camera", None) if rt is not None else None
        hub = h.cameras.describe(lambda key: key[:2] == ("classroom", cid))
        return ok(class_id=cid, reason=reason, selected=svc.public(cam, p) if cam else None, options=options,
                  legacy_webcam=reason == "LEGACY_WEBCAM", live_camera=live,
                  live_health=_public_health(hub.get("health")))


def _public_health(health):
    """Live stream health for the teacher UI (no errors text, no addresses)."""
    if not health:
        return None
    return {k: health.get(k) for k in ("camera_name", "source_type", "state", "resolution", "measured_fps",
                                       "frame_age_ms", "reconnect_count", "stale_frames", "dropped_frames")}
