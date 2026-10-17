"""Camera-network policy: camera Test / discovery / saved cameras may only reach approved
camera networks and camera ports — never arbitrary private host:port targets (no internal
service or port probing through the camera settings)."""
import time

import pytest

from focusguard.authz import Principal
from focusguard.cameras import connect, health as H, onvif, rtsp
from focusguard.cameras.crypto import CredentialVault, generate_key
from focusguard.cameras.discovery import DiscoveryManager, strategy_lan_scan
from focusguard.cameras.netguard import (ORIGIN_DISCOVERED, ORIGIN_MANUAL, HostNotAllowed, NetGuard,
                                         camera_networks_from_env)
from focusguard.cameras.service import TESTS_PER_MINUTE, CameraError, CameraService
from focusguard.cameras.testing import MockCameraSource

TEACHER = Principal(2, "teacher", frozenset({1}))
SECRET = "VERIF-CODE-1"
LOCAL = ["192.168.1.10"]             # the classroom PC


def code(fn, *a, **k):
    with pytest.raises(HostNotAllowed) as e:
        fn(*a, **k)
    return e.value.code


# ------------------------------------------------------------------ policy
def test_explicit_allowlist_replaces_the_any_private_default():
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.0/24, 10.20.30.0/24"}, local_ips=LOCAL)
    assert g.policy == "ALLOWED_NETWORKS"
    assert g.check_ip("192.168.1.64") and g.check_ip("10.20.30.7")
    assert code(g.check_ip, "192.168.2.5") == "network_not_allowed"          # other private subnet
    assert code(g.check_ip, "172.17.0.1") == "network_not_allowed"           # docker host / internal services
    assert code(g.check_ip, "10.0.0.1", ORIGIN_DISCOVERED) == "network_not_allowed"
    assert code(g.check_ip, "8.8.8.8") == "public"
    assert code(g.check_ip, "192.168.1.10") == "self"                        # the server itself
    assert code(g.check_ip, "127.0.0.1") == "loopback"
    assert code(g.check_ip, "169.254.169.254") == "metadata"
    assert code(g.check_ip, "::ffff:172.17.0.1") == "network_not_allowed"    # IPv4-mapped form


def test_default_policy_is_the_machines_own_subnet_and_production_needs_an_allowlist_for_typed_hosts():
    dev = NetGuard.from_env({"FOCUSGUARD_ENV": "development"}, local_ips=LOCAL)
    prod = NetGuard.from_env({"FOCUSGUARD_ENV": "production"}, local_ips=LOCAL)
    assert dev.policy == prod.policy == "LOCAL_SUBNETS"
    assert dev.check_ip("192.168.1.64") == "192.168.1.64"                    # dev: typed address on own subnet
    assert code(dev.check_ip, "192.168.5.64") == "network_not_allowed"       # never another private subnet
    assert code(dev.check_ip, "10.1.1.1") == "network_not_allowed"
    assert prod.check_ip("192.168.1.64", ORIGIN_DISCOVERED)                  # prod: discovered on own subnet
    assert code(prod.check_ip, "192.168.1.64") == "network_not_allowed"      # prod: typed needs an allowlist
    allowed = NetGuard.from_env({"FOCUSGUARD_ENV": "production", "FG_CAMERA_ALLOWED_HOSTS": "192.168.1.64"},
                                local_ips=LOCAL)
    assert allowed.check_ip("192.168.1.64") and code(allowed.check_ip, "192.168.1.65") == "network_not_allowed"


def test_allowlist_parsing_ignores_garbage_and_never_a_slash_zero():
    assert camera_networks_from_env({}) is None
    assert camera_networks_from_env({"FG_CAMERA_ALLOWED_NETWORKS": "<placeholder>"}) is None
    nets = camera_networks_from_env({"FG_CAMERA_ALLOWED_NETWORKS": "0.0.0.0/0, nonsense, 192.168.1.0/24"})
    assert [str(n) for n in nets] == ["192.168.1.0/24"]
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_NETWORKS": "0.0.0.0/0"}, local_ips=LOCAL)
    assert code(g.check_ip, "192.168.1.64") == "network_not_allowed"         # configured but empty: nothing


def test_dns_answers_must_all_be_inside_the_camera_networks_rebinding_included():
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.0/24"}, local_ips=LOCAL,
                          resolver=lambda h: ["192.168.1.64", "192.168.9.9"])
    assert code(g.resolve_host, "cam.local") == "network_not_allowed"
    answers = iter([["192.168.1.64"], ["10.0.0.5"]])
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.0/24"}, local_ips=LOCAL,
                          resolver=lambda h: next(answers))
    assert g.resolve_host("cam.local") == "192.168.1.64"
    assert code(g.resolve_host, "cam.local") == "network_not_allowed"         # re-resolved at every use


def test_only_camera_ports():
    g = NetGuard.from_env({}, local_ips=LOCAL)
    assert g.check_port(554) == 554 and g.check_port(80) == 80 and g.check_port(8000) == 8000
    for p in (22, 3306, 5432, 6379, 9200, 11211, 2375):
        assert code(g.check_port, p) == "port_not_allowed"
    custom = NetGuard.from_env({"FG_CAMERA_ALLOWED_PORTS": "554,9554"}, local_ips=LOCAL)
    assert custom.check_port(9554) == 9554 and code(custom.check_port, 80) == "port_not_allowed"


def test_discovery_scans_only_the_machines_own_approved_camera_subnet():
    probed = []

    def connect_(ip, port, timeout=0.4):
        probed.append(ip)
        return ip == "192.168.1.70" and port == 554
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.64/26, 10.20.30.0/24"}, local_ips=LOCAL)
    assert [str(n) for n in g.scan_networks()] == ["192.168.1.64/26"]       # 10.20.30.0/24 is not local: not scanned
    dm = DiscoveryManager(g, strategies=[strategy_lan_scan], local_ips=LOCAL,
                          neighbours=["192.168.1.70", "192.168.1.5", "172.17.0.1", "10.20.30.4"], connect=connect_,
                          rtsp_options=lambda ip, port: (rtsp.OK, "EZVIZ"),
                          rtsp_probe=lambda ip, port, path: rtsp.ProbeResult(rtsp.AUTH_REQUIRED, None, False, None, 401))
    job = dm.start(1)
    end = time.time() + 10
    while job.status == "RUNNING" and time.time() < end:
        time.sleep(0.02)
    assert probed and all(64 <= int(ip.rsplit(".", 1)[1]) <= 127 and ip.startswith("192.168.1.") for ip in probed)
    assert "172.17.0.1" not in probed and "10.20.30.4" not in probed and "192.168.1.5" not in probed
    assert [r["host"] for r in job.public()["results"]] == ["192.168.1.70"]


# ----------------------------------------------------------------- service
class Deps(connect.Deps):
    calls = []

    class onvif_client:
        def __init__(self, ip, port, path, username=None, password=None):
            Deps.calls.append(("ONVIF", ip, port))
            self.ip, self.port = ip, port

        def device_information(self):
            return {"vendor": "EZVIZ", "model": "CS-H1C"}

        def media_service(self):
            return (self.ip, Deps.media_port, "/onvif/media")

        def profiles(self):
            Deps.calls.append(("PROFILES", self.ip, Deps.media_port))
            return [{"token": "p1", "name": "Main", "width": 1920, "height": 1080}]

        def stream_uri(self, token):
            return {"port": Deps.stream_port, "path": "/H.264"}

    media_port, stream_port = 80, 554

    @staticmethod
    def rtsp_options(ip, port):
        Deps.calls.append(("OPTIONS", ip, port))
        return rtsp.OK, "EZVIZ"

    @staticmethod
    def rtsp_probe(ip, port, path, username=None, secret=None, **_k):
        Deps.calls.append(("DESCRIBE", ip, port, path))
        return rtsp.ProbeResult(rtsp.OK if secret == SECRET else rtsp.AUTH_FAILED, "EZVIZ", True, "H264", 200)


def service(manager, env, local_ips=LOCAL, strategies=()):
    Deps.calls, Deps.media_port, Deps.stream_port = [], 80, 554
    guard = NetGuard.from_env(env, local_ips=local_ips)
    svc = CameraService(manager.repo, manager.audit, clock=time.time, vault=CredentialVault(generate_key()),
                        netguard=guard, discovery=DiscoveryManager(guard, strategies=list(strategies),
                                                                   local_ips=local_ips, neighbours=[]),
                        connect_deps=Deps, source_factory=lambda spec: MockCameraSource(fps=40), measure_seconds=0.4)
    return svc


def test_manual_test_cannot_probe_internal_services_or_other_subnets(manager):
    svc = service(manager, {"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.0/24"})
    for target in ({"host": "172.17.0.1", "port": 554}, {"host": "10.0.0.2"}, {"host": "192.168.1.10"},
                   {"url": "rtsp://10.1.2.3:554/live"}, {"host": "192.168.1.64", "port": 22},
                   {"host": "192.168.1.64", "port": 6379}):
        r = svc.test_connection(TEACHER, dict(target, secret=SECRET))
        assert r["status"] == H.HOST_NOT_ALLOWED and r["test_token"] is None, target
    assert Deps.calls == []                                                 # nothing was ever contacted
    ok = svc.test_connection(TEACHER, {"host": "192.168.1.64", "secret": SECRET})
    assert ok["status"] == H.CONNECTED


def test_onvif_advertised_ports_outside_the_policy_are_never_used(manager):
    svc = service(manager, {"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.0/24"})
    Deps.media_port = 6379                       # device advertises its "media service" on another local service
    r = svc.test_connection(TEACHER, {"host": "192.168.1.64", "secret": SECRET})
    assert not any(c[0] == "PROFILES" for c in Deps.calls)                  # never contacted
    assert r["status"] == H.CONNECTED and all(c[2] == 554 for c in Deps.calls if c[0] in ("OPTIONS", "DESCRIBE"))
    Deps.calls, Deps.media_port, Deps.stream_port = [], 80, 22
    svc._test_times.clear()
    r = svc.test_connection(TEACHER, {"host": "192.168.1.64", "secret": SECRET})
    assert all(c[2] != 22 for c in Deps.calls if c[0] in ("OPTIONS", "DESCRIBE"))


def test_production_discovered_camera_works_typed_one_needs_the_allowlist(manager):
    from focusguard.cameras.testing import mock_discovery_strategy
    env = {"FOCUSGUARD_ENV": "production"}
    svc = service(manager, env, strategies=[mock_discovery_strategy("192.168.1.64"),
                                            mock_discovery_strategy("192.168.7.7")])
    typed = svc.test_connection(TEACHER, {"host": "192.168.1.64", "secret": SECRET})
    assert typed["status"] == H.HOST_NOT_ALLOWED and "FG_CAMERA_ALLOWED_NETWORKS" in typed["reason"]
    job = svc.discover(TEACHER)
    end = time.time() + 5
    while svc.discovery_status(TEACHER, job["job_id"])["status"] == "RUNNING" and time.time() < end:
        time.sleep(0.02)
    found = svc.discovery_status(TEACHER, job["job_id"])["results"]
    assert [f["host"] for f in found] == ["192.168.1.64"]                   # 192.168.7.7: not a camera subnet
    r = svc.test_connection(TEACHER, {"discovery_id": found[0]["discovery_id"], "secret": SECRET})
    assert r["status"] == H.CONNECTED
    # overriding the discovered host with a typed one does not inherit "discovered"
    r2 = svc.test_connection(TEACHER, {"discovery_id": found[0]["discovery_id"], "host": "192.168.1.65",
                                       "secret": SECRET})
    assert r2["status"] == H.HOST_NOT_ALLOWED
    cam = svc.save(TEACHER, {"test_token": r["test_token"], "name": "Lớp 8/1"})
    stored = manager.repo.get_camera(cam["camera_id"])
    assert stored["network_origin"] == ORIGIN_DISCOVERED
    assert svc.open_source(stored) is not None                             # re-checked with its origin


def test_saved_camera_is_rechecked_when_the_policy_changes(manager):
    svc = service(manager, {"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.0/24"})
    r = svc.test_connection(TEACHER, {"host": "192.168.1.64", "secret": SECRET})
    cam = manager.repo.get_camera(svc.save(TEACHER, {"test_token": r["test_token"], "name": "c"})["camera_id"])
    assert cam["network_origin"] == ORIGIN_MANUAL
    stricter = service(manager, {"FG_CAMERA_ALLOWED_NETWORKS": "10.20.30.0/24"})
    with pytest.raises(HostNotAllowed):
        stricter.open_source(cam)
    from focusguard.cameras.sources import RTSPSource
    src = RTSPSource("192.168.1.64", 554, "/H.264", netguard=stricter.netguard, origin=ORIGIN_MANUAL,
                     capture_factory=lambda *a: pytest.fail("must not connect"))
    assert src.open() is False and src.state != H.CONNECTED


def test_camera_test_is_rate_limited_per_user(manager):
    svc = service(manager, {"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.0/24"})
    for _ in range(TESTS_PER_MINUTE):
        svc.test_connection(TEACHER, {"host": "172.17.0.1"})
    with pytest.raises(CameraError) as e:
        svc.test_connection(TEACHER, {"host": "192.168.1.64"})
    assert e.value.status == 429
    other = Principal(3, "teacher", frozenset({1}))
    assert svc.test_connection(other, {"host": "192.168.1.64", "secret": SECRET})["status"] == H.CONNECTED


def test_the_app_builds_the_policy_from_the_environment(monkeypatch):
    monkeypatch.setenv("FG_CAMERA_ALLOWED_NETWORKS", "10.20.30.0/24")
    from focusguard.cameras.service import CameraService as CS
    svc = CS(repo=None, audit=None)
    assert svc.netguard.policy == "ALLOWED_NETWORKS"
    assert code(svc.netguard.check_ip, "192.168.1.64") == "network_not_allowed"


# ------------------------------------------------------- review round fixes
def test_approved_name_never_resolves_to_mapped_loopback_or_blocked_ranges():
    for answer in ("::ffff:127.0.0.1", "::ffff:169.254.169.254", "::ffff:100.64.0.9", "192.168.1.10"):
        g = NetGuard.from_env({"FG_CAMERA_ALLOWED_HOSTS": "cam.example"}, local_ips=LOCAL,
                              resolver=lambda h, a=answer: [a])
        with pytest.raises(HostNotAllowed):
            g.resolve_host("cam.example")


def test_loopback_test_cameras_are_never_allowed_in_production():
    dev = NetGuard.from_env({"FG_CAMERA_ALLOW_LOOPBACK": "1"}, local_ips=LOCAL)
    prod = NetGuard.from_env({"FG_CAMERA_ALLOW_LOOPBACK": "1", "FOCUSGUARD_ENV": " production "}, local_ips=LOCAL)
    assert dev.check_ip("127.0.0.1") == "127.0.0.1"
    assert code(prod.check_ip, "127.0.0.1") == "loopback" and code(prod.check_ip, "192.168.1.10") == "self"
    assert code(prod.check_ip, "192.168.1.64") == "network_not_allowed"           # " production " is production


def test_a_camera_host_name_is_re_resolved_and_re_vetted_at_every_connect():
    from focusguard.cameras.sources import RTSPSource
    answers = iter([["192.168.1.64"], ["10.9.9.9"]])
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.0/24"}, local_ips=LOCAL,
                          resolver=lambda h: next(answers))
    opened = []

    class Cap:
        def __init__(self, url, *a):
            opened.append(url)

        def isOpened(self):
            return True

        def set(self, *a):
            return True

        def read(self):
            return False, None

        def release(self):
            pass
    src = RTSPSource("192.168.1.64", 554, "/H.264", netguard=g, host="cam.lan", capture_factory=Cap)
    src.open()
    assert opened and "192.168.1.64" in opened[0]
    assert src.open() is False and len(opened) == 1                      # the name moved out of the policy


def test_discovery_respects_the_port_policy():
    probed = []
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.0/24", "FG_CAMERA_ALLOWED_PORTS": "8554,80"},
                          local_ips=LOCAL)
    dm = DiscoveryManager(g, strategies=[strategy_lan_scan], local_ips=LOCAL, neighbours=[],
                          connect=lambda ip, port, timeout=0.4: probed.append(port) or False)
    job = dm.start(1)
    end = time.time() + 10
    while job.status == "RUNNING" and time.time() < end:
        time.sleep(0.02)
    assert probed == []                                                     # 554 / 8000 are not camera ports here


def test_discovery_sdk_port_diagnosis_respects_the_port_policy():
    probed = []
    g = NetGuard.from_env({"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.0/24", "FG_CAMERA_ALLOWED_PORTS": "554"},
                          local_ips=LOCAL)
    dm = DiscoveryManager(g, strategies=[strategy_lan_scan], local_ips=LOCAL, neighbours=["192.168.1.4"],
                          connect=lambda ip, port, timeout=0.4: probed.append(port) or port == 8000)
    job = dm.start(1)
    end = time.time() + 10
    while job.status == "RUNNING" and time.time() < end:
        time.sleep(0.02)
    assert probed and set(probed) == {554}                                  # 8000 is not a camera port here
    assert job.public()["results"] == []


def test_an_rtsp_disabled_discovery_result_is_never_connectable(manager):
    from focusguard.cameras.testing import mock_rtsp_disabled_strategy
    svc = service(manager, {"FG_CAMERA_ALLOWED_NETWORKS": "192.168.1.0/24"},
                  strategies=[mock_rtsp_disabled_strategy("192.168.1.4"), mock_rtsp_disabled_strategy("10.9.9.9")])
    job = svc.discover(TEACHER)
    end = time.time() + 5
    while svc.discovery_status(TEACHER, job["job_id"])["status"] == "RUNNING" and time.time() < end:
        time.sleep(0.02)
    found = svc.discovery_status(TEACHER, job["job_id"])["results"]
    assert [(f["host"], f["status"]) for f in found] == [("192.168.1.4", "RTSP_DISABLED")]
    with pytest.raises(CameraError) as e:
        svc.test_connection(TEACHER, {"discovery_id": found[0]["discovery_id"], "secret": SECRET})
    assert e.value.code == "RTSP_DISABLED" and e.value.status == 409
    assert Deps.calls == []                                                 # nothing was contacted


def test_an_rtsp_redirect_is_never_followed():
    class RedirectDeps(connect.Deps):
        class onvif_client:
            def __init__(self, *a, **k):
                pass

            def device_information(self):
                raise onvif.OnvifError("closed")

        @staticmethod
        def rtsp_options(ip, port):
            return rtsp.OK, "evil"

        @staticmethod
        def rtsp_probe(ip, port, path, username=None, secret=None, **_k):
            return rtsp.ProbeResult(rtsp.REDIRECT, "evil", False, None, 302)
    r = connect.resolve_stream("192.168.1.64", "admin", SECRET, deps=RedirectDeps)
    assert r["status"] == H.DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM
    assert rtsp.PROBE_USER_AGENT.startswith("Lavf/")             # the probe looks like the FFmpeg client


def test_production_warns_about_cameras_saved_before_the_policy(manager, capsys):
    manager.repo.create_camera({"owner_user_id": 2, "name": "old", "source_type": "RTSP", "host": "192.168.1.20"})
    svc = service(manager, {"FOCUSGUARD_ENV": "production"})
    svc.startup_check(True)
    assert "camera-network policy" in capsys.readouterr().out
