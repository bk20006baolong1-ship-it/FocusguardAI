"""CameraService: account camera settings on top of the repository.

* storage — only sanitised connection parts + the encrypted secret; the API
  view (:meth:`CameraService.public`) has ``has_credentials`` instead of the
  secret and never a URL;
* authorization — focusguard.authz (teachers: personal cameras; admins: school
  cameras + class assignment; students/parents: nothing);
* one default camera per owner (server-enforced), one camera per class;
* connection tests (easy connect / manual), bounded previews that never touch
  the AI pipeline, and :meth:`open_source` for the classroom pipeline;
* audit: CAMERA_ADDED / UPDATED / DELETED / DEFAULT_CHANGED /
  ASSIGNED_TO_CLASS / CREDENTIAL_UPDATED / CONNECTION_TESTED (no secrets, no IPs).
"""
import re
import threading
import time
import uuid

from .. import authz
from ..privacy import audit as A
from . import health as H
from .connect import (DEFAULT_USERNAMES, PROTOCOL_AUTO, PROTOCOL_ONVIF, PROTOCOL_RTSP, PROTOCOLS, Deps, measure,
                      resolve_stream)
from .crypto import CredentialError, CredentialKeyMissing, CredentialVault
from .discovery import DiscoveryManager, rtsp_disabled
from .netguard import (ORIGIN_DISCOVERED, ORIGIN_MANUAL, HostNotAllowed, NetGuard, check_path, check_port,
                       split_camera_url)
from .reader import LatestFrameReader
from .redact import redact, safe_error
from .sources import RTSP, WEBCAM, RTSPSource, WebcamSource

TEST_TOKEN_TTL = 300.0
TESTS_PER_MINUTE = 10          # per user: "Kiểm tra" must not become a port scanner
PREVIEW_SECONDS = 60.0
MAX_PREVIEWS = 3
NAME_MAX = 60
_CTRL_RX = re.compile(r"[\x00-\x1f\x7f]")


class CameraError(Exception):
    def __init__(self, message, status=400, code="INVALID"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def clean_name(name):
    name = _CTRL_RX.sub("", str(name or "")).strip()
    if not name:
        raise CameraError("Hãy đặt tên cho camera.", 400, "NAME_REQUIRED")
    return name[:NAME_MAX]


def clean_text(value, limit=64):
    if value in (None, ""):
        return None
    return _CTRL_RX.sub("", str(value)).strip()[:limit] or None


def encode_jpeg(image, quality=70, max_width=960):
    """JPEG bytes of a frame for previews (OpenCV). Dev/test servers without OpenCV inject
    another encoder (CameraService.jpeg_encoder)."""
    import cv2
    h, w = image.shape[:2]
    if w > max_width:
        image = cv2.resize(image, (max_width, int(h * max_width / w)))
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


class CameraService:
    def __init__(self, repo, audit, clock=time.time, vault=None, netguard=None, discovery=None,
                 connect_deps=Deps, source_factory=None, env=None, measure_seconds=3.0):
        self.repo = repo
        self.audit = audit
        self.clock = clock
        self._vault = vault
        self._env = env
        self.netguard = netguard or NetGuard.from_env(env)
        self.discovery = discovery or DiscoveryManager(self.netguard)
        self.connect_deps = connect_deps
        self.source_factory = source_factory      # tests / dev demo only: spec -> CameraSource
        self.measure_seconds = measure_seconds
        self.jpeg_encoder = encode_jpeg
        self._tests = {}
        self._test_locks = {}
        self._test_times = {}
        self._previews = {}
        self._lock = threading.RLock()

    def startup_check(self, is_production):
        """Production with saved camera passwords but no FG_CAMERA_CREDENTIAL_KEY: say so loudly.
        The app still starts; using those cameras fails safe (CREDENTIAL_KEY_MISSING)."""
        try:
            cams = self.repo.list_cameras()
        except Exception:
            cams = []
        if is_production and self.netguard.policy == "LOCAL_SUBNETS":
            legacy = [c for c in cams if c.get("source_type") == RTSP and not c.get("network_origin")]
            if legacy:
                print(f"[CAMERA] {len(legacy)} IP camera(s) saved before the camera-network policy count as "
                      "manually added: set FG_CAMERA_ALLOWED_NETWORKS (camera network) or add them again with "
                      "'Tìm camera', otherwise they are refused in production.")
        if not is_production or self.vault.available:
            return True
        stored = any(c.get("encrypted_secret") for c in cams)
        if stored:
            print("[CAMERA] FG_CAMERA_CREDENTIAL_KEY is not set: saved camera passwords cannot be used "
                  "until it is configured (fail safe).")
        return not stored

    # ------------------------------------------------------------ helpers
    @property
    def vault(self):
        if self._vault is None:
            self._vault = CredentialVault.from_env(self._env)
        return self._vault

    def _cam(self, camera_id):
        cam = self.repo.get_camera(str(camera_id)) if camera_id else None
        if cam is None:
            raise CameraError("Không tìm thấy camera.", 404, "NOT_FOUND")
        return cam

    def assigned_classes(self, camera_id):
        return [r["class_id"] for r in self.repo.list_class_cameras(camera_id=camera_id)]

    def _require(self, ok):
        if not ok:
            raise CameraError("Không có quyền truy cập camera này.", 403, "FORBIDDEN")

    def _audit(self, action, p, cam=None, **meta):
        md = {"camera_id": cam.get("camera_id") if cam else None, "camera_name": cam.get("name") if cam else None}
        md.update(meta)
        self.audit.record(action, p, None, md)

    def public(self, cam, p):
        """API view of a camera. Never the secret, never a URL; the IP only for teachers/admins."""
        classes = self.assigned_classes(cam["camera_id"])
        status = cam.get("status") or H.UNTESTED
        return {
            "camera_id": cam["camera_id"], "name": cam.get("name"), "vendor": cam.get("vendor"),
            "model": cam.get("model"), "source_type": cam.get("source_type"), "protocol": cam.get("protocol"),
            "host": cam.get("host") if authz.can_manage_cameras(p) else None,
            "port": cam.get("port"), "stream_path": cam.get("stream_path"), "stream_profile": cam.get("stream_profile"),
            "device_index": cam.get("device_index"), "username": cam.get("username"),
            "has_credentials": bool(cam.get("encrypted_secret")),
            "is_default": bool(cam.get("is_default")), "is_shared": bool(cam.get("is_shared")),
            "status": status, "status_label": H.LABELS_VI.get(status, status),
            "resolution": cam.get("resolution"), "measured_fps": cam.get("measured_fps"),
            "last_connected_at": cam.get("last_connected_at"), "last_tested_at": cam.get("last_tested_at"),
            "classes": [{"class_id": c, "class_name": self.repo.get_class_name(c)} for c in classes],
            "can_edit": authz.can_edit_camera(p, cam), "can_assign": authz.can_assign_camera(p, cam),
        }

    # --------------------------------------------------------------- lists
    def list_for(self, p):
        self._require(authz.can_manage_cameras(p))
        if p.is_admin:
            mine = []
            shared = self.repo.list_cameras(is_shared=True)
        else:
            mine = self.repo.list_cameras(owner_user_id=p.user_id, is_shared=False)
            shared = []
            for cid in sorted(p.class_ids):
                row = self.repo.get_class_camera(cid)
                cam = self.repo.get_camera(row["camera_id"]) if row else None
                if cam and cam["camera_id"] not in {c["camera_id"] for c in shared}:
                    shared.append(cam)
        return {"my_cameras": [self.public(c, p) for c in mine],
                "class_cameras": [self.public(c, p) for c in shared],
                "is_admin": p.is_admin}

    def usable_cameras(self, p, class_id):
        """Cameras ``p`` may use for ``class_id`` (personal + the class's school camera)."""
        out = []
        row = self.repo.get_class_camera(class_id) if class_id is not None else None
        if row:
            cam = self.repo.get_camera(row["camera_id"])
            if cam and authz.can_use_camera(p, cam, class_id, [class_id]):
                out.append(cam)
        if p is not None and p.role == authz.ROLE_TEACHER:
            out += self.repo.list_cameras(owner_user_id=p.user_id, is_shared=False)
        return out

    def find_by_name(self, p, class_id, name):
        """A camera ``p`` may use for ``class_id`` whose name matches (case-insensitive), else None."""
        want = clean_text(name, NAME_MAX)
        if not want:
            return None
        for cam in self.usable_cameras(p, class_id):
            if (cam.get("name") or "").casefold() == want.casefold():
                return cam
        return None

    def resolve_for_class(self, p, class_id, requested_camera_id=None):
        """Camera for a class session: requested -> class camera -> teacher default.

        Returns ``(camera or None, reason)``. ``reason``: REQUESTED | CLASS_CAMERA | DEFAULT |
        LEGACY_WEBCAM (no camera configured at all: the local webcam 0, as before) | CHOOSE."""
        if requested_camera_id:
            cam = self._cam(requested_camera_id)
            self._require(authz.can_use_camera(p, cam, class_id, self.assigned_classes(cam["camera_id"])))
            return cam, "REQUESTED"
        usable = self.usable_cameras(p, class_id)
        row = self.repo.get_class_camera(class_id) if class_id is not None else None
        if row and usable and usable[0]["camera_id"] == row["camera_id"]:
            return usable[0], "CLASS_CAMERA"
        default = next((c for c in usable if c.get("is_default") and not c.get("is_shared")), None)
        if default:
            return default, "DEFAULT"
        if not usable:
            return None, "LEGACY_WEBCAM"
        if len(usable) == 1:
            return usable[0], "DEFAULT"
        return None, "CHOOSE"

    # ----------------------------------------------------------- discovery
    def discover(self, p):
        self._require(authz.can_manage_cameras(p))
        try:
            return self.discovery.start(p.user_id).public()
        except RuntimeError:
            raise CameraError("Hệ thống đang tìm camera cho người khác, thử lại sau ít giây.", 429, "BUSY")

    def discovery_status(self, p, job_id):
        self._require(authz.can_manage_cameras(p))
        job = self.discovery.get(job_id, p.user_id)
        if job is None:
            raise CameraError("Không tìm thấy lượt tìm camera.", 404, "NOT_FOUND")
        return job.public()

    def stop_discovery(self, p, job_id):
        self._require(authz.can_manage_cameras(p))
        job = self.discovery.stop(job_id, p.user_id)
        if job is None:
            raise CameraError("Không tìm thấy lượt tìm camera.", 404, "NOT_FOUND")
        return job.public()

    # ------------------------------------------------------------ the spec
    def _spec_from_payload(self, p, data, cam=None):
        """Normalise a test request into an internal spec (host vetted, credentials split)."""
        data = data or {}
        spec = {"source_type": RTSP, "protocol": PROTOCOL_AUTO, "host": None, "port": None, "path": None,
                "profile": None, "username": None, "secret": None, "vendor": None, "model": None,
                "onvif_port": None, "onvif_path": None, "device_index": None, "hint": None,
                "camera_id": cam["camera_id"] if cam else None, "origin": ORIGIN_MANUAL}
        if cam is not None:
            spec.update(source_type=cam.get("source_type") or RTSP, protocol=cam.get("protocol") or PROTOCOL_AUTO,
                        host=cam.get("host"), port=cam.get("port"), path=cam.get("stream_path"),
                        profile=cam.get("stream_profile"), username=cam.get("username"), vendor=cam.get("vendor"),
                        model=cam.get("model"), onvif_port=cam.get("onvif_port"), onvif_path=cam.get("onvif_path"),
                        device_index=cam.get("device_index"), origin=cam.get("network_origin") or ORIGIN_MANUAL)
            if data.get("secret") in (None, "") and cam.get("encrypted_secret"):
                spec["secret"] = self.decrypt(cam)
        if (data.get("source_type") or spec["source_type"]) == WEBCAM:
            try:
                idx = int(data.get("device_index", spec["device_index"] or 0))
            except (TypeError, ValueError):
                raise CameraError("Số thứ tự webcam không hợp lệ.", 400)
            if not 0 <= idx <= 9:
                raise CameraError("Số thứ tự webcam không hợp lệ.", 400)
            spec.update(source_type=WEBCAM, device_index=idx, host=None, protocol=None)
            return spec
        if data.get("discovery_id"):
            found = self.discovery.lookup(p.user_id, str(data["discovery_id"]))
            if found is None:
                raise CameraError("Kết quả tìm camera đã hết hạn. Hãy tìm lại.", 404, "DISCOVERY_EXPIRED")
            if rtsp_disabled(found):                       # only the SDK port answered: RTSP is off
                raise CameraError("Thiết bị chưa bật RTSP. Hãy bật RTSP trong ứng dụng của hãng rồi tìm lại.", 409,
                                  "RTSP_DISABLED")
            # found by the server's own LAN discovery on an approved camera subnet
            spec.update(host=found["host"], port=found.get("suggested_port"), vendor=found.get("vendor"),
                        model=found.get("model"), onvif_port=found.get("_onvif_port"),
                        onvif_path=found.get("_onvif_path"), hint=found.get("_hint"), origin=ORIGIN_DISCOVERED)
        if data.get("url"):
            parts = split_camera_url(data["url"])          # pasted URL: credentials split off, URL discarded
            spec.update(host=parts["host"], port=parts["port"], path=parts["path"], protocol=PROTOCOL_RTSP,
                        origin=ORIGIN_MANUAL)
            if parts["username"]:
                spec["username"] = parts["username"]
            if parts["secret"] is not None:
                spec["secret"] = parts["secret"]
        if data.get("host"):
            spec["host"] = str(data["host"]).strip()
            spec["origin"] = ORIGIN_MANUAL                 # a typed address is never "discovered"
        protocol = str(data.get("protocol") or spec["protocol"] or PROTOCOL_AUTO).upper()
        if protocol not in PROTOCOLS:
            raise CameraError("Giao thức không hợp lệ.", 400)
        spec["protocol"] = protocol
        if "port" in data:
            spec["port"] = check_port(data.get("port"), None)
        if "stream_path" in data:
            spec["path"] = check_path(data.get("stream_path"))
        if "profile" in data:
            spec["profile"] = clean_text(data.get("profile"))
        if data.get("username") not in (None, ""):
            spec["username"] = clean_text(data.get("username"), 64)
        if data.get("secret") not in (None, ""):
            spec["secret"] = str(data["secret"])[:128]
        if not spec["host"]:
            raise HostNotAllowed("empty")
        spec["ip"] = self.netguard.resolve_host(spec["host"], spec["origin"])
        for k in ("port", "onvif_port"):               # camera ports only (RTSP / ONVIF)
            if spec.get(k) is not None:
                spec[k] = self.netguard.check_port(spec[k])
        return spec

    def decrypt(self, cam):
        try:
            return self.vault.decrypt(cam.get("encrypted_secret"))
        except CredentialError as exc:
            raise CameraError("Không giải mã được mật khẩu camera. Hãy cập nhật xác thực." if
                              not isinstance(exc, CredentialKeyMissing) else H.HELP_VI[H.CREDENTIAL_KEY_MISSING],
                              503 if isinstance(exc, CredentialKeyMissing) else 409, exc.code)

    def _build_source(self, spec, name=None, netguard_check=True):
        if self.source_factory is not None:
            return self.source_factory(dict(spec, name=name))
        if spec["source_type"] == WEBCAM:
            return WebcamSource(spec.get("device_index") or 0, name=name, camera_id=spec.get("camera_id"))
        secret = spec.get("secret")
        return RTSPSource(spec["ip"], spec.get("port") or 554, spec.get("path"), spec.get("username"),
                          secret_provider=(lambda: secret), name=name, camera_id=spec.get("camera_id"),
                          vendor=spec.get("vendor"), model=spec.get("model"),
                          netguard=self.netguard if netguard_check else None,
                          origin=spec.get("origin") or ORIGIN_MANUAL, host=spec.get("host"))

    # ---------------------------------------------------------------- test
    def test_connection(self, p, data):
        """Easy connect / manual test. Returns a sanitised result (+ a short-lived test token)."""
        self._require(authz.can_manage_cameras(p))
        cam = None
        if (data or {}).get("camera_id"):
            cam = self._cam(data["camera_id"])
            self._require(authz.can_edit_camera(p, cam) or authz.can_use_camera(
                p, cam, None, self.assigned_classes(cam["camera_id"])))
        now = self.clock()
        with self._lock:
            recent = [t for t in self._test_times.get(p.user_id, []) if now - t < 60.0]
            if len(recent) >= TESTS_PER_MINUTE:
                raise CameraError("Bạn đã kiểm tra quá nhiều lần trong một phút. Hãy đợi rồi thử lại.", 429,
                                  "RATE_LIMITED")
            self._test_times[p.user_id] = recent + [now]
        lock = self._test_locks.setdefault(p.user_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise CameraError("Đang kiểm tra một camera khác, hãy đợi.", 429, "BUSY")
        try:
            return self._test(p, data or {}, cam)
        finally:
            lock.release()

    def _test(self, p, data, cam):
        result = {"status": None, "resolution": None, "measured_fps": None, "latency_ms": None, "vendor": None,
                  "model": None, "profile": None, "auth_required": None, "test_token": None, "source_type": None}
        secret = None
        try:
            spec = self._spec_from_payload(p, data, cam)
            secret = spec.get("secret")
            result["source_type"] = spec["source_type"]
            if spec["source_type"] == RTSP:
                if not spec.get("username") and spec.get("secret"):
                    spec["username"] = DEFAULT_USERNAMES.get(spec.get("vendor") or spec.get("hint") or "", "admin")
                r = resolve_stream(spec["ip"], spec.get("username"), spec.get("secret"), spec["protocol"],
                                   spec.get("port"), spec.get("path"), spec.get("profile"),
                                   vendor_hint=spec.get("vendor") or spec.get("hint"), onvif_port=spec.get("onvif_port"),
                                   onvif_path=spec.get("onvif_path"), deps=self.connect_deps,
                                   port_guard=self.netguard.check_port)
                result.update(vendor=r["vendor"], model=r["model"] or spec.get("model"), profile=r["profile"],
                              auth_required=r["auth_required"])
                spec.update(port=r["port"], path=r["path"] or spec.get("path"), profile=r["profile"],
                            vendor=r["vendor"] or spec.get("vendor"), model=r["model"] or spec.get("model"))
                if r["status"] != H.STREAM_FOUND:
                    result["status"] = r["status"]
                    return self._finish_test(p, cam, result, spec)
            m = measure(self._build_source(spec, name=data.get("name") or (cam or {}).get("name")),
                        seconds=self.measure_seconds)
            result.update(status=m["status"], resolution=m["resolution"], measured_fps=m["measured_fps"],
                          latency_ms=m["latency_ms"])
            if m["status"] == H.CONNECTED:
                spec.update(resolution=m["resolution"], measured_fps=m["measured_fps"])
            return self._finish_test(p, cam, result, spec)
        except HostNotAllowed as exc:
            result["status"] = H.HOST_NOT_ALLOWED
            result["reason"] = str(exc)
            return self._finish_test(p, cam, result, None)
        except CameraError:
            raise
        except Exception as exc:
            print("[CAMERA] test error:", safe_error(exc, secrets=(secret,)))
            result["status"] = H.STREAM_UNAVAILABLE
            return self._finish_test(p, cam, result, None)

    def _finish_test(self, p, cam, result, spec):
        now = self.clock()
        st = result["status"]
        result["status_label"] = H.LABELS_VI.get(st, st)
        result["help"] = H.HELP_VI.get(st)
        if st == H.CONNECTED and spec is not None:
            token = uuid.uuid4().hex
            with self._lock:
                self._gc_tests(now)
                self._tests[token] = {"owner": p.user_id, "expires": now + TEST_TOKEN_TTL, "spec": dict(spec),
                                      "camera_id": cam["camera_id"] if cam else None}
            result["test_token"] = token
        if cam is not None:
            fields = {"status": st, "last_tested_at": now}
            if st == H.CONNECTED:
                fields.update(last_connected_at=now, resolution=result["resolution"],
                              measured_fps=result["measured_fps"])
            self.repo.update_camera(cam["camera_id"], fields)
        self._audit(A.CAMERA_CONNECTION_TESTED, p, cam, status=st, vendor=result.get("vendor"),
                    source_type=result.get("source_type"))
        return result

    def _gc_tests(self, now):
        for k, v in list(self._tests.items()):
            if v["expires"] < now:
                del self._tests[k]

    def _take_test(self, p, token, camera_id=None, consume=True):
        with self._lock:
            self._gc_tests(self.clock())
            t = self._tests.get(str(token or ""))
            if t is None or t["owner"] != p.user_id or (camera_id is not None and t["camera_id"] != camera_id):
                raise CameraError("Hãy kiểm tra kết nối thành công trước khi lưu.", 409, "TEST_REQUIRED")
            if consume:
                del self._tests[str(token)]
            return t

    # ---------------------------------------------------------------- save
    def save(self, p, data):
        self._require(authz.can_manage_cameras(p))
        data = data or {}
        t = self._take_test(p, data.get("test_token"), consume=False)
        if t["camera_id"] is not None:
            raise CameraError("Dùng 'Cập nhật xác thực' cho camera đã lưu.", 409, "TEST_REQUIRED")
        spec = t["spec"]
        name = clean_name(data.get("name"))
        encrypted = None
        if spec.get("secret"):
            try:
                encrypted = self.vault.encrypt(spec["secret"])
            except CredentialKeyMissing:
                raise CameraError(H.HELP_VI[H.CREDENTIAL_KEY_MISSING], 503, H.CREDENTIAL_KEY_MISSING)
        self._take_test(p, data.get("test_token"))
        shared = p.is_admin                      # admin cameras are school cameras
        owner_cams = self.repo.list_cameras(owner_user_id=p.user_id)
        make_default = bool(data.get("is_default")) or not owner_cams
        now = self.clock()
        row = {"owner_user_id": p.user_id, "name": name, "vendor": clean_text(spec.get("vendor"), 40),
               "model": clean_text(spec.get("model"), 60), "host": spec.get("host"), "port": spec.get("port"),
               "protocol": spec.get("protocol"), "source_type": spec["source_type"],
               "stream_path": spec.get("path"), "stream_profile": spec.get("profile"),
               "onvif_port": spec.get("onvif_port"), "onvif_path": spec.get("onvif_path"),
               "device_index": spec.get("device_index"), "username": spec.get("username"),
               "encrypted_secret": encrypted, "is_default": False, "is_shared": shared, "status": H.CONNECTED,
               "resolution": spec.get("resolution"), "measured_fps": spec.get("measured_fps"),
               "last_connected_at": now, "last_tested_at": now, "created_by": p.user_id, "created_at": now,
               "updated_at": now,
               "network_origin": spec.get("origin") if spec["source_type"] == RTSP else None}
        camera_id = self.repo.create_camera(row)
        cam = self.repo.get_camera(camera_id)
        self._audit(A.CAMERA_ADDED, p, cam, source_type=row["source_type"], vendor=row["vendor"], shared=shared)
        if make_default:
            self._make_default(p, cam, audit=False)
            cam = self.repo.get_camera(camera_id)
        return self.public(cam, p)

    # -------------------------------------------------------------- update
    def update(self, p, camera_id, data):
        cam = self._cam(camera_id)
        self._require(authz.can_edit_camera(p, cam))
        data = data or {}
        fields, changed = {}, []
        if "name" in data:
            fields["name"] = clean_name(data.get("name"))
            changed.append("name")
        if data.get("test_token"):                         # credentials verified by a re-test of this camera
            t = self._take_test(p, data["test_token"], camera_id=cam["camera_id"])
            spec = t["spec"]
            try:
                fields["encrypted_secret"] = self.vault.encrypt(spec["secret"]) if spec.get("secret") else None
            except CredentialKeyMissing:
                raise CameraError(H.HELP_VI[H.CREDENTIAL_KEY_MISSING], 503, H.CREDENTIAL_KEY_MISSING)
            fields.update(username=spec.get("username"), port=spec.get("port"), stream_path=spec.get("path"),
                          stream_profile=spec.get("profile"), status=H.CONNECTED,
                          vendor=clean_text(spec.get("vendor"), 40) or cam.get("vendor"),
                          model=clean_text(spec.get("model"), 60) or cam.get("model"))
            changed.append("credentials")
        elif data.get("secret") not in (None, "") or data.get("username") not in (None, ""):
            if cam.get("source_type") == WEBCAM:
                raise CameraError("Webcam không cần xác thực.", 400)
            if data.get("username") not in (None, ""):
                fields["username"] = clean_text(data["username"], 64)
            if data.get("secret") not in (None, ""):
                try:
                    fields["encrypted_secret"] = self.vault.encrypt(str(data["secret"])[:128])
                except CredentialKeyMissing:
                    raise CameraError(H.HELP_VI[H.CREDENTIAL_KEY_MISSING], 503, H.CREDENTIAL_KEY_MISSING)
            fields["status"] = H.UNTESTED
            changed.append("credentials")
        if not fields:
            raise CameraError("Không có thay đổi.", 400, "NO_CHANGE")
        self.repo.update_camera(cam["camera_id"], fields)
        cam = self.repo.get_camera(cam["camera_id"])
        if "credentials" in changed:
            self._audit(A.CAMERA_CREDENTIAL_UPDATED, p, cam)
        if "name" in changed:
            self._audit(A.CAMERA_UPDATED, p, cam, fields=["name"])
        return self.public(cam, p)

    def delete(self, p, camera_id):
        cam = self._cam(camera_id)
        self._require(authz.can_edit_camera(p, cam))
        self.stop_previews(camera_id=cam["camera_id"])
        self.repo.delete_camera(cam["camera_id"])
        self._audit(A.CAMERA_DELETED, p, cam)
        return {"camera_id": cam["camera_id"], "deleted": True}

    def _make_default(self, p, cam, audit=True):
        owner = cam.get("owner_user_id")
        for other in self.repo.list_cameras(owner_user_id=owner):
            if other["camera_id"] != cam["camera_id"] and other.get("is_default"):
                self.repo.update_camera(other["camera_id"], {"is_default": False})
        self.repo.update_camera(cam["camera_id"], {"is_default": True})
        if audit:
            self._audit(A.CAMERA_DEFAULT_CHANGED, p, cam)

    def set_default(self, p, camera_id):
        cam = self._cam(camera_id)
        self._require(authz.can_edit_camera(p, cam))
        self._make_default(p, cam)
        return self.public(self.repo.get_camera(cam["camera_id"]), p)

    def assign_class(self, p, camera_id, class_id, unassign=False):
        cam = self._cam(camera_id)
        self._require(authz.can_assign_camera(p, cam))
        try:
            cid = int(class_id)
        except (TypeError, ValueError):
            raise CameraError("Lớp không hợp lệ.", 400)
        if not any(int(c["id"]) == cid for c in self.repo.get_classes_list()):
            raise CameraError("Không tìm thấy lớp.", 404, "NOT_FOUND")
        if unassign:
            row = self.repo.get_class_camera(cid)
            if row and row["camera_id"] == cam["camera_id"]:
                self.repo.set_class_camera(cid, None)
        else:
            self.repo.set_class_camera(cid, cam["camera_id"], assigned_by=p.user_id, assigned_at=self.clock())
        self._audit(A.CAMERA_ASSIGNED_TO_CLASS, p, cam, class_id=cid, unassigned=bool(unassign))
        return self.public(self.repo.get_camera(cam["camera_id"]), p)

    # ------------------------------------------------------ runtime source
    def open_source(self, cam):
        """CameraSource for a saved camera (secret decrypted lazily, at connect time)."""
        if cam.get("source_type") == WEBCAM:
            spec = {"source_type": WEBCAM, "device_index": cam.get("device_index") or 0,
                    "camera_id": cam["camera_id"]}
            return self._build_source(spec, name=cam.get("name"))
        origin = cam.get("network_origin") or ORIGIN_MANUAL
        ip = self.netguard.resolve_host(cam.get("host"), origin)          # the policy applies at every use
        spec = {"source_type": RTSP, "ip": ip, "host": cam.get("host"),
                "port": self.netguard.check_port(cam.get("port") or 554),
                "path": cam.get("stream_path"), "username": cam.get("username"), "vendor": cam.get("vendor"),
                "model": cam.get("model"), "camera_id": cam["camera_id"], "origin": origin}
        if self.source_factory is not None:
            return self.source_factory(dict(spec, name=cam.get("name"), secret=None))
        blob = cam.get("encrypted_secret")
        vault = self.vault

        def secret_provider():
            return vault.decrypt(blob) if blob else None
        if blob and not vault.available:
            raise CameraError(H.HELP_VI[H.CREDENTIAL_KEY_MISSING], 503, H.CREDENTIAL_KEY_MISSING)
        return RTSPSource(ip, spec["port"], spec["path"], spec["username"], secret_provider=secret_provider,
                          name=cam.get("name"), camera_id=cam["camera_id"], vendor=cam.get("vendor"),
                          model=cam.get("model"), netguard=self.netguard, origin=origin, host=cam.get("host"))

    def mark_connected(self, camera_id, health):
        if not camera_id:
            return
        try:
            fields = {"status": health.get("state"), "last_connected_at": self.clock()}
            if health.get("resolution"):
                fields["resolution"] = health["resolution"]
            if health.get("measured_fps"):
                fields["measured_fps"] = health["measured_fps"]
            self.repo.update_camera(camera_id, fields)
        except Exception as exc:
            print("[CAMERA] status update failed:", safe_error(exc))

    @staticmethod
    def evaluation_metadata(cam, health=None):
        """Camera fields recorded with an evaluation: never credentials, URLs or IP addresses."""
        if cam is None:
            return {"camera_id": None, "camera_name": "Webcam 0 (mặc định)", "camera_source_type": WEBCAM,
                    "camera_vendor": None, "camera_model": None}
        return {"camera_id": cam["camera_id"], "camera_name": cam.get("name"),
                "camera_source_type": cam.get("source_type"), "camera_vendor": cam.get("vendor"),
                "camera_model": cam.get("model")}

    # ------------------------------------------------------------- preview
    def start_preview(self, p, camera_id=None, test_token=None, busy=None):
        """Bounded preview (<= PREVIEW_SECONDS). Never creates events, scores or identity changes."""
        self._require(authz.can_manage_cameras(p))
        if camera_id:
            cam = self._cam(camera_id)
            self._require(authz.can_edit_camera(p, cam) or authz.can_use_camera(
                p, cam, None, self.assigned_classes(cam["camera_id"])))
            if busy is not None and busy(cam):
                raise CameraError("Camera đang được dùng cho buổi học.", 409, "IN_USE")
            source = self.open_source(cam)
        else:
            t = self._take_test(p, test_token, consume=False)
            if busy is not None and t["spec"]["source_type"] == WEBCAM and busy(
                    {"source_type": WEBCAM, "device_index": t["spec"].get("device_index") or 0}):
                raise CameraError("Camera đang được dùng cho buổi học.", 409, "IN_USE")
            source = self._build_source(t["spec"], name="Xem trước")
            cam = None
        now = self.clock()
        with self._lock:
            self._sweep_previews(now)
            for pid, pv in list(self._previews.items()):
                if pv["owner"] == p.user_id:
                    self._stop_preview(pid)
            if len(self._previews) >= MAX_PREVIEWS:
                raise CameraError("Đang có quá nhiều người xem thử camera. Thử lại sau.", 429, "BUSY")
            preview_id = uuid.uuid4().hex
            reader = LatestFrameReader(source, max_backoff=4.0).start()
            self._previews[preview_id] = {"owner": p.user_id, "reader": reader, "started": now,
                                          "expires": now + PREVIEW_SECONDS,
                                          "camera_id": cam["camera_id"] if cam else None}
        return {"preview_id": preview_id, "expires_in": PREVIEW_SECONDS,
                "stream_url": f"/api/settings/cameras/preview/{preview_id}/stream"}

    def sweep_previews(self):
        """Release expired previews (called before a class pipeline opens a camera)."""
        with self._lock:
            self._sweep_previews(self.clock())

    def _sweep_previews(self, now):
        for pid, pv in list(self._previews.items()):
            if pv["expires"] < now:
                self._stop_preview(pid)

    def _stop_preview(self, preview_id):
        pv = self._previews.pop(preview_id, None)
        if pv is not None:
            pv["reader"].stop(timeout=0.5)
        return pv is not None

    def preview(self, p, preview_id):
        with self._lock:
            self._sweep_previews(self.clock())
            pv = self._previews.get(str(preview_id))
            if pv is None or pv["owner"] != p.user_id:
                raise CameraError("Phiên xem thử đã kết thúc.", 404, "NOT_FOUND")
            return pv

    def preview_health(self, p, preview_id):
        pv = self.preview(p, preview_id)
        h = pv["reader"].health()
        state = h["state"]
        return {"state": state, "label": H.LABELS_VI.get(state, state), "resolution": h["resolution"],
                "measured_fps": h["measured_fps"], "frame_age_ms": h["frame_age_ms"],
                "latency_ms": h["first_frame_latency_ms"], "reconnect_count": h["reconnect_count"],
                "seconds_left": max(0, int(pv["expires"] - self.clock()))}

    def stop_preview(self, p, preview_id):
        with self._lock:
            pv = self._previews.get(str(preview_id))
            if pv is None or pv["owner"] != p.user_id:
                raise CameraError("Phiên xem thử đã kết thúc.", 404, "NOT_FOUND")
            self._stop_preview(str(preview_id))
        return {"stopped": True}

    def stop_previews(self, camera_id=None, owner=None):
        with self._lock:
            for pid, pv in list(self._previews.items()):
                if (camera_id is None or pv["camera_id"] == camera_id) and (owner is None or pv["owner"] == owner):
                    self._stop_preview(pid)

    def preview_frames(self, p, preview_id, sleep=time.sleep):
        """JPEG frames of a preview until it ends (MJPEG generator body)."""
        pv = self.preview(p, preview_id)
        reader, seq = pv["reader"], 0
        try:
            while str(preview_id) in self._previews and self.clock() < pv["expires"]:
                f = reader.get_newer(seq)
                if f is None:
                    sleep(0.05)
                    continue
                seq = f.seq
                jpeg = self.jpeg_encoder(f.image)
                if jpeg:
                    yield jpeg
        finally:
            # expired, stopped or the viewer went away: release the camera now, not at the next sweep
            with self._lock:
                if self._previews.get(str(preview_id)) is pv:
                    self._stop_preview(str(preview_id))
