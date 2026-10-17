"""Account camera settings over HTTP: flows, authorization / IDOR, SSRF, CSRF, XSS,
and that a camera password never appears in the database, an API response, a log,
an exception message, the audit log, or an evaluation export."""
import json
import time

import pytest

from conftest import DEV_ENV, add_teacher, grant_consent, login
from focusguard.cameras import connect, health as H, rtsp
from focusguard.cameras.crypto import CredentialVault, generate_key
from focusguard.cameras.discovery import DiscoveryManager
from focusguard.cameras.netguard import NetGuard
from focusguard.cameras.service import CameraService
from focusguard.cameras.testing import MockCameraSource, mock_discovery_strategy

SECRET = "QWERTY-VERIF-778"
EVIL_NAME = '<img src=x onerror="window.__xss=1">Phòng 8/1'


class NoOnvif:
    def __init__(self, *a, **k):
        pass

    def device_information(self):
        from focusguard.cameras.onvif import OnvifError
        raise OnvifError("closed")


class ApiDeps(connect.Deps):
    calls = []
    onvif_client = NoOnvif

    @staticmethod
    def rtsp_options(ip, port):
        ApiDeps.calls.append(("OPTIONS", ip))
        return rtsp.OK, "EZVIZ-mock"

    @staticmethod
    def rtsp_probe(ip, port, path, username=None, secret=None, **_k):
        ApiDeps.calls.append(("DESCRIBE", ip, path))
        if not username:
            return rtsp.ProbeResult(rtsp.AUTH_REQUIRED, "EZVIZ", False, None, 401)
        if secret != SECRET:
            return rtsp.ProbeResult(rtsp.AUTH_FAILED, "EZVIZ", False, None, 401)
        if path != "/H.264":
            return rtsp.ProbeResult(rtsp.NOT_FOUND, "EZVIZ", False, None, 404)
        return rtsp.ProbeResult(rtsp.OK, "EZVIZ", True, "H264", 200)


def factory(spec):
    if spec.get("explode"):
        raise RuntimeError(f"rtsp://admin:{SECRET}@192.168.1.64/H.264 refused")
    return MockCameraSource(name=spec.get("name"), fps=40)


@pytest.fixture
def cam_app(manager, clock):
    ApiDeps.calls = []
    guard = NetGuard()
    manager.cameras = CameraService(
        manager.repo, manager.audit, clock=time.time, vault=CredentialVault(generate_key()), netguard=guard,
        discovery=DiscoveryManager(guard, strategies=[mock_discovery_strategy("192.168.1.64")], local_ips=[],
                                   neighbours=[]),
        connect_deps=ApiDeps, source_factory=factory, measure_seconds=0.6)
    manager.cameras.jpeg_encoder = lambda image: b"\xff\xd8test-jpeg\xff\xd9"     # CI has no OpenCV
    from app import create_app
    app, _sio = create_app(env=DEV_ENV, async_mode="threading", start_background=False, session_manager=manager)
    app.config["TESTING"] = True
    add_teacher(manager.repo, "teacher2", class_id=2)
    return app, manager


def ok(resp, code=200):
    assert resp.status_code == code, resp.get_data(as_text=True)
    return resp.get_json()


def try_connect(client, **body):
    body.setdefault("host", "192.168.1.64")
    body.setdefault("username", "admin")
    body.setdefault("secret", SECRET)
    return ok(client.post("/api/settings/cameras/test", json=body))["result"]


def add_camera(client, name="Phòng 8/1", **body):
    r = try_connect(client, **body)
    assert r["status"] == H.CONNECTED, r
    return ok(client.post("/api/settings/cameras", json={"test_token": r["test_token"], "name": name}))["camera"]


def dump_db(manager):
    """Everything persisted: camera rows, audit, assignments and a full SQL dump of the database."""
    rows = [manager.repo.list_cameras(), manager.repo.get_audit(limit=1000), manager.repo.list_class_cameras()]
    con = getattr(manager.repo, "_memory_conn", None)
    if con is not None:
        rows.append("\n".join(con.iterdump()))
    return json.dumps(rows, ensure_ascii=False, default=str)


# ------------------------------------------------------------- flows
def test_teacher_discover_test_preview_save_default_rename_update_delete(cam_app, capsys):
    app, sm = cam_app
    c = login(app, "teacher")
    job = ok(c.post("/api/settings/cameras/discover", json={}))["job"]
    for _ in range(100):
        job = ok(c.get(f"/api/settings/cameras/discovery/{job['job_id']}"))["job"]
        if job["status"] != "RUNNING":
            break
        time.sleep(0.02)
    found = job["results"][0]
    assert found["status_label"] == "Cần xác thực" and found["vendor"] == "EZVIZ"
    assert not any(k.startswith("_") for k in found)
    # easy connect: discovered camera + verification code, no URL
    r = ok(c.post("/api/settings/cameras/test", json={"discovery_id": found["discovery_id"], "secret": SECRET}))["result"]
    assert r["status"] == H.CONNECTED and r["resolution"] == "320x240" and r["measured_fps"]
    assert r["latency_ms"] is not None and r["vendor"] == "EZVIZ" and r["test_token"]
    # preview of the tested (unsaved) camera: bounded, no analysis
    pv = ok(c.post("/api/settings/cameras/preview/start", json={"test_token": r["test_token"]}))["preview"]
    assert pv["expires_in"] <= 60
    stream = c.get(pv["stream_url"])
    assert stream.status_code == 200 and stream.mimetype == "multipart/x-mixed-replace"
    chunk = next(iter(stream.response))
    assert b"Content-Type: image/jpeg" in chunk
    h = ok(c.get(f"/api/settings/cameras/preview/{pv['preview_id']}/health"))["health"]
    assert h["state"] in (H.CONNECTED, H.DEGRADED, H.CONNECTING)
    ok(c.post(f"/api/settings/cameras/preview/{pv['preview_id']}/stop", json={}))
    stream.close()
    # a viewer that goes away releases the camera at once (no reader left running)
    pv2 = ok(c.post("/api/settings/cameras/preview/start", json={"test_token": r["test_token"]}))["preview"]
    stream = c.get(pv2["stream_url"])
    next(iter(stream.response))
    stream.close()
    assert c.get(f"/api/settings/cameras/preview/{pv2['preview_id']}/health").status_code == 404
    assert sm.cameras._previews == {}
    assert sm.class_runtime(1) is None and sm.repo.get_focus_events() == []      # preview never analyses
    # save -> default (first camera)
    cam = ok(c.post("/api/settings/cameras", json={"test_token": r["test_token"], "name": "EZVIZ H1C – Phòng 8/1"}))["camera"]
    assert cam["is_default"] and cam["has_credentials"] and cam["status"] == H.CONNECTED and cam["vendor"] == "EZVIZ"
    assert c.post("/api/settings/cameras", json={"test_token": r["test_token"], "name": "again"}).status_code == 409
    second = add_camera(c, "Webcam", source_type="WEBCAM", device_index=0)
    assert second["source_type"] == "WEBCAM" and not second["is_default"]
    ok(c.post(f"/api/settings/cameras/{second['camera_id']}/default", json={}))
    listing = ok(c.get("/api/settings/cameras"))
    defaults = [x["name"] for x in listing["my_cameras"] if x["is_default"]]
    assert defaults == ["Webcam"]
    ok(c.patch(f"/api/settings/cameras/{cam['camera_id']}", json={"name": "Phòng 8/2"}))
    # update credentials: re-test the saved camera with the new code, then save with the token
    bad = ok(c.post(f"/api/settings/cameras/{cam['camera_id']}/test", json={"secret": "WRONG"}))["result"]
    assert bad["status"] == H.AUTH_FAILED and bad["test_token"] is None and bad["help"]
    good = ok(c.post(f"/api/settings/cameras/{cam['camera_id']}/test", json={"secret": SECRET}))["result"]
    ok(c.patch(f"/api/settings/cameras/{cam['camera_id']}", json={"test_token": good["test_token"]}))
    ok(c.post(f"/api/settings/cameras/{cam['camera_id']}/preview/start", json={}))
    ok(c.post(f"/api/settings/cameras/{cam['camera_id']}/preview/stop", json={}))
    ok(c.delete(f"/api/settings/cameras/{cam['camera_id']}"))
    actions = {a["action"] for a in sm.repo.get_audit(limit=1000)}
    assert {"CAMERA_ADDED", "CAMERA_UPDATED", "CAMERA_DELETED", "CAMERA_DEFAULT_CHANGED", "CAMERA_CREDENTIAL_UPDATED",
            "CAMERA_CONNECTION_TESTED"} <= actions
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err and SECRET not in dump_db(sm)
    assert "192.168.1.64" not in json.dumps(sm.repo.get_audit(limit=1000))        # audit has no IPs


def test_save_requires_a_successful_test(cam_app):
    app, sm = cam_app
    c = login(app, "teacher")
    r = try_connect(c, secret="WRONG")
    assert r["status"] == H.AUTH_FAILED and r["test_token"] is None
    assert c.post("/api/settings/cameras", json={"name": "x"}).status_code == 409
    assert c.post("/api/settings/cameras", json={"name": "x", "test_token": "forged"}).status_code == 409
    assert sm.repo.list_cameras() == []
    other = login(app, "teacher2")
    good = try_connect(c)
    assert other.post("/api/settings/cameras", json={"name": "x", "test_token": good["test_token"]}).status_code == 409
    nodisc = ok(c.post("/api/settings/cameras/test", json={"discovery_id": "nope", "secret": SECRET}), 404)
    assert nodisc["status"] == "error"


def test_secret_never_in_api_db_log_exception_audit(cam_app, capsys, caplog):
    app, sm = cam_app
    c = login(app, "teacher")
    cam = add_camera(c)
    bodies = [c.get("/api/settings/cameras").get_data(as_text=True),
              c.post(f"/api/settings/cameras/{cam['camera_id']}/test", json={}).get_data(as_text=True),
              c.get("/api/teacher/class_camera?class_id=1").get_data(as_text=True),
              c.get("/settings/cameras").get_data(as_text=True)]
    # an exception carrying a credential URL deep in the stack
    sm.cameras.source_factory = lambda spec: factory(dict(spec, explode=True))
    bodies.append(c.post("/api/settings/cameras/test", json={"host": "192.168.1.64", "username": "admin",
                                                             "secret": SECRET}).get_data(as_text=True))
    out = capsys.readouterr()
    logs = caplog.text
    for text in bodies + [out.out, out.err, logs, dump_db(sm)]:
        assert SECRET not in text
    assert all('"encrypted_secret"' not in b for b in bodies)
    row = sm.repo.get_camera(cam["camera_id"])
    assert row["encrypted_secret"] and SECRET not in row["encrypted_secret"]
    assert "rtsp://" not in json.dumps(row)                       # no URL stored, only host/port/path


def test_pasted_url_with_credentials_is_split_and_never_stored(cam_app):
    app, sm = cam_app
    c = login(app, "teacher")
    r = ok(c.post("/api/settings/cameras/test", json={"url": f"rtsp://admin:{SECRET}@192.168.1.64:554/H.264"}))["result"]
    assert r["status"] == H.CONNECTED
    cam = ok(c.post("/api/settings/cameras", json={"test_token": r["test_token"], "name": "Dán URL"}))["camera"]
    row = sm.repo.get_camera(cam["camera_id"])
    assert row["host"] == "192.168.1.64" and row["stream_path"] == "/H.264" and row["username"] == "admin"
    assert "rtsp://" not in dump_db(sm) and SECRET not in dump_db(sm)


# --------------------------------------------------------- authorization
def test_idor_other_teacher_cannot_touch_my_camera(cam_app):
    app, sm = cam_app
    a = login(app, "teacher")
    cam = add_camera(a)
    b = login(app, "teacher2")
    cid = cam["camera_id"]
    assert ok(b.get("/api/settings/cameras"))["my_cameras"] == []
    for method, url, body in [("patch", f"/api/settings/cameras/{cid}", {"name": "pwned"}),
                              ("patch", f"/api/settings/cameras/{cid}", {"secret": "x"}),
                              ("delete", f"/api/settings/cameras/{cid}", None),
                              ("post", f"/api/settings/cameras/{cid}/default", {}),
                              ("post", f"/api/settings/cameras/{cid}/test", {}),
                              ("post", f"/api/settings/cameras/{cid}/preview/start", {}),
                              ("post", f"/api/settings/cameras/{cid}/assign-class", {"class_id": 2})]:
        resp = getattr(b, method)(url, json=body) if body is not None else getattr(b, method)(url)
        assert resp.status_code == 403, (method, url, resp.status_code)
    assert b.get(f"/api/teacher/class_camera?class_id=1").status_code == 403       # not their class
    started = b.post("/api/teacher/start_offline_class", json={"class_id": 2, "camera_id": cid})
    assert started.status_code in (403, 409)
    assert sm.repo.get_camera(cid)["name"] == "Phòng 8/1"


@pytest.mark.parametrize("user", ["hocsinh", "phuhuynh"])
def test_students_and_parents_have_no_camera_access(cam_app, user):
    app, sm = cam_app
    cam = add_camera(login(app, "teacher"))
    c = login(app, user)
    cid = cam["camera_id"]
    for method, url in [("get", "/api/settings/cameras"), ("post", "/api/settings/cameras/discover"),
                        ("post", "/api/settings/cameras/test"), ("post", "/api/settings/cameras"),
                        ("patch", f"/api/settings/cameras/{cid}"), ("delete", f"/api/settings/cameras/{cid}"),
                        ("post", f"/api/settings/cameras/{cid}/default"), ("post", f"/api/settings/cameras/{cid}/test"),
                        ("post", f"/api/settings/cameras/{cid}/preview/start"),
                        ("post", "/api/settings/cameras/preview/start"),
                        ("get", "/api/teacher/class_camera")]:
        resp = getattr(c, method)(url, json={}) if method != "get" else c.get(url)
        assert resp.status_code == 403, (url, resp.status_code)
    page = c.get("/settings/cameras")
    assert page.status_code in (302, 403)
    home = c.get("/" if user == "hocsinh" else "/parent/dashboard", follow_redirects=True).get_data(as_text=True)
    assert "/settings/cameras" not in home and "192.168.1.64" not in home


def test_unauthenticated_requests_are_rejected(cam_app):
    app, _ = cam_app
    c = app.test_client()
    assert c.get("/api/settings/cameras").status_code == 401
    assert c.post("/api/settings/cameras/test", json={}).status_code == 401
    assert c.get("/settings/cameras").status_code == 302


def test_admin_school_camera_assignment_and_teacher_scope(cam_app):
    app, sm = cam_app
    adm = login(app, "admin")
    cam = add_camera(adm, "Cổng – Phòng 8/1")
    assert cam["is_shared"] and cam["can_assign"]
    ok(adm.post(f"/api/settings/cameras/{cam['camera_id']}/assign-class", json={"class_id": 1}))
    assert adm.post(f"/api/settings/cameras/{cam['camera_id']}/assign-class", json={"class_id": 999}).status_code == 404
    t1, t2 = login(app, "teacher"), login(app, "teacher2")
    shared = ok(t1.get("/api/settings/cameras"))["class_cameras"]
    assert [x["camera_id"] for x in shared] == [cam["camera_id"]] and shared[0]["can_edit"] is False
    assert ok(t2.get("/api/settings/cameras"))["class_cameras"] == []            # not their class
    for method, url, body in [("patch", f"/api/settings/cameras/{cam['camera_id']}", {"secret": "x"}),
                              ("delete", f"/api/settings/cameras/{cam['camera_id']}", None),
                              ("post", f"/api/settings/cameras/{cam['camera_id']}/assign-class", {"class_id": 1})]:
        r = getattr(t1, method)(url, json=body) if body is not None else getattr(t1, method)(url)
        assert r.status_code == 403
    ok(t1.post(f"/api/settings/cameras/{cam['camera_id']}/preview/start", json={}))      # may USE it
    ok(t1.post(f"/api/settings/cameras/{cam['camera_id']}/preview/stop", json={}))
    assert t2.post(f"/api/settings/cameras/{cam['camera_id']}/preview/start", json={}).status_code == 403
    own = add_camera(t1, "Của tôi")
    assert t1.post(f"/api/settings/cameras/{own['camera_id']}/assign-class", json={"class_id": 1}).status_code == 403
    # the class camera wins over the teacher's default when the class starts
    sel = ok(t1.get("/api/teacher/class_camera?class_id=1"))
    assert sel["reason"] == "CLASS_CAMERA" and sel["selected"]["camera_id"] == cam["camera_id"]
    ok(t1.post("/api/teacher/start_offline_class", json={"class_id": 1}))
    assert sm.class_runtime(1).camera["camera_id"] == cam["camera_id"]
    status = ok(t1.get("/api/teacher/class_status?class_id=1"))
    assert status["camera"]["source"]["camera_name"] == "Cổng – Phòng 8/1"
    assert "192.168.1.64" not in json.dumps(status) and SECRET not in json.dumps(status)
    ok(t1.post("/api/teacher/end_offline_class", json={"class_id": 1}))
    assert "CAMERA_ASSIGNED_TO_CLASS" in {a["action"] for a in sm.repo.get_audit(limit=500)}


def test_camera_choice_and_legacy_webcam(cam_app):
    app, sm = cam_app
    t = login(app, "teacher")
    legacy = ok(t.get("/api/teacher/class_camera?class_id=1"))
    assert legacy["reason"] == "LEGACY_WEBCAM" and legacy["legacy_webcam"] is True
    ok(t.post("/api/teacher/start_offline_class", json={"class_id": 1}))
    assert sm.class_runtime(1).camera is None                     # webcam 0, exactly as before
    ok(t.post("/api/teacher/end_offline_class", json={"class_id": 1}))
    for name in ("A", "B"):
        sm.repo.create_camera({"owner_user_id": 2, "name": name, "source_type": "WEBCAM", "device_index": 0,
                               "is_default": False, "is_shared": False})
    assert ok(t.get("/api/teacher/class_camera?class_id=1"))["reason"] == "CHOOSE"
    refused = t.post("/api/teacher/start_offline_class", json={"class_id": 1})
    assert refused.status_code == 409 and "chọn camera" in refused.get_json()["message"]
    chosen = sm.repo.list_cameras(owner_user_id=2)[1]["camera_id"]
    ok(t.post("/api/teacher/start_offline_class", json={"class_id": 1, "camera_id": chosen}))
    assert sm.class_runtime(1).camera["camera_name"] == "B"
    ok(t.post("/api/teacher/end_offline_class", json={"class_id": 1}))


# ------------------------------------------------------------- SSRF
@pytest.mark.parametrize("body", [
    {"host": "169.254.169.254"}, {"host": "8.8.8.8"}, {"host": "127.0.0.1"}, {"host": "localhost"},
    {"host": "0.0.0.0"}, {"host": "fe80::1"}, {"url": "file:///etc/passwd"}, {"url": "http://192.168.1.64/"},
    {"url": "gopher://192.168.1.64/"}, {"url": "rtsp://8.8.8.8/live"}, {"host": "192.168.1.64/../x"},
    {"host": "evil.example.com"},
])
def test_ssrf_targets_are_refused_before_any_network_call(cam_app, body):
    app, _ = cam_app
    c = login(app, "teacher")
    ApiDeps.calls = []
    body = dict(body, username="admin", secret=SECRET)
    resp = c.post("/api/settings/cameras/test", json=body)
    data = resp.get_json()
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        assert data["result"]["status"] == H.HOST_NOT_ALLOWED and data["result"]["test_token"] is None
    assert ApiDeps.calls == []                                     # nothing was contacted


def test_invalid_port_and_path_are_rejected(cam_app):
    app, _ = cam_app
    c = login(app, "teacher")
    for extra in ({"port": 70000}, {"port": "abc"}, {"stream_path": "rtsp://x@y/"}, {"stream_path": "/a b"}):
        r = c.post("/api/settings/cameras/test", json=dict({"host": "192.168.1.64", "secret": SECRET}, **extra))
        assert r.status_code in (200, 400)
        if r.status_code == 200:
            assert r.get_json()["result"]["status"] == H.HOST_NOT_ALLOWED


# ------------------------------------------------------------ CSRF / XSS
def test_cross_site_writes_are_blocked(cam_app):
    app, sm = cam_app
    c = login(app, "teacher")
    cam = add_camera(c)
    for method, url in [("post", "/api/settings/cameras/test"), ("post", "/api/settings/cameras"),
                        ("delete", f"/api/settings/cameras/{cam['camera_id']}"),
                        ("patch", f"/api/settings/cameras/{cam['camera_id']}"),
                        ("post", "/api/settings/cameras/discover")]:
        r = getattr(c, method)(url, json={"name": "x"}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
    assert sm.repo.get_camera(cam["camera_id"]) is not None


def test_camera_name_is_data_not_markup(cam_app):
    app, sm = cam_app
    c = login(app, "teacher")
    cam = add_camera(c, EVIL_NAME)
    assert ok(c.get("/api/settings/cameras"))["my_cameras"][0]["name"] == EVIL_NAME   # JSON, escaped by FG.esc
    page = c.get("/settings/cameras").get_data(as_text=True)
    assert "onerror=" not in page                                  # names are never rendered server-side raw
    js = open("static/js/cameras.js", encoding="utf-8").read()
    assert "esc(c.name)" in js and "innerHTML = c.name" not in js
    long = ok(c.patch(f"/api/settings/cameras/{cam['camera_id']}", json={"name": "x" * 500 + "\x00\x07"}))["camera"]
    assert len(long["name"]) == 60 and "\x00" not in long["name"]
    assert c.patch(f"/api/settings/cameras/{cam['camera_id']}", json={"name": "   "}).status_code == 400


# ------------------------------------------- evaluation / field metadata
def test_evaluation_records_camera_metadata_without_credentials_or_ip(cam_app):
    app, sm = cam_app
    grant_consent(sm, 3, face_recognition=True, evaluation=True)
    t = login(app, "teacher")
    add_camera(t, "EZVIZ H1C – Phòng 8/1")
    ok(t.post("/api/teacher/start_offline_class", json={"class_id": 1}))
    es = ok(t.post("/api/teacher/evaluations", json={"class_id": 1, "camera_position": "FRONT_CORNER",
                                                     "consent_verified": True, "dataset_kind": "REAL"}))["session"]
    assert es["camera_name"] == "EZVIZ H1C – Phòng 8/1" and es["camera_source_type"] == "RTSP"
    assert es["camera_vendor"] == "EZVIZ" and es["camera_id"]
    export = t.get(f"/api/teacher/evaluations/{es['evaluation_session_id']}/export").get_data(as_text=True)
    for text in (json.dumps(es), export):
        assert SECRET not in text and "192.168.1.64" not in text and "rtsp://" not in text
    ok(t.post("/api/teacher/end_offline_class", json={"class_id": 1}))


def test_production_without_key_fails_safe_but_testing_still_works(cam_app):
    app, sm = cam_app
    sm.cameras._vault = CredentialVault(None, source="missing")
    c = login(app, "teacher")
    r = try_connect(c)
    assert r["status"] == H.CONNECTED
    resp = c.post("/api/settings/cameras", json={"test_token": r["test_token"], "name": "x"})
    assert resp.status_code == 503 and "FG_CAMERA_CREDENTIAL_KEY" in resp.get_json()["message"]
    assert sm.repo.list_cameras() == []
    assert sm.cameras.startup_check(is_production=True) is True


def test_camera_page_has_accessible_labels_and_live_regions(cam_app):
    app, _ = cam_app
    page = login(app, "teacher").get("/settings/cameras").get_data(as_text=True)
    for fid in ("camName", "camUser", "camSecret", "camHost", "camPort", "camPath", "renameInput", "camIndex"):
        assert f'for="{fid}"' in page
    assert 'id="discoveryStatus" role="status" aria-live="polite"' in page
    assert 'id="camResult" role="status" aria-live="polite"' in page
    assert 'autocomplete="new-password"' in page and "Tìm camera trong mạng LAN" in page
    assert "Thêm camera thủ công" in page and "Camera của tôi" in page and "Camera lớp học" in page
