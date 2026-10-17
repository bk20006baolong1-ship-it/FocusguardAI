"""User-triggered LAN camera discovery (never automatic, never the Internet).

A discovery job runs in a background thread (the HTTP request returns at once
with a job id; the page polls it) with an overall timeout (default 15 s) and
can be stopped. Strategies, in order:

1. ONVIF WS-Discovery multicast probe (TTL 1): name, model, ONVIF service port;
2. SSDP M-SEARCH (TTL 1): only used to add a vendor hint to a device that is
   found otherwise (SSDP also answers for TVs, routers…);
3. the operating system's ARP / neighbour table (devices already seen on the
   LAN are probed first);
4. bounded host discovery: TCP connect to the RTSP port (554) of every host of
   the machine's OWN approved camera subnet(s) (``NetGuard.scan_networks``: the
   local /24, narrowed to FG_CAMERA_ALLOWED_NETWORKS when configured) — at most
   2 subnets (508 hosts), 48 parallel connections, 0.4 s timeout each; hosts with
   RTSP get an OPTIONS/DESCRIBE probe without credentials ("needs authentication?");
5. diagnosis only: hosts whose 554 is closed get one more TCP connect to 8000 (the
   Hikvision/EZVIZ SDK port, also a camera port) once every RTSP probe is done. A host
   with 8000 open is reported as RTSP_DISABLED ("maybe an EZVIZ/Hikvision camera whose
   RTSP is off") so the teacher knows to enable RTSP in the vendor app; it is never
   connectable (FocusGuard does not use the vendor SDK or cloud).

mDNS is not used (it would add a dependency and cameras rarely announce video
over it). Only addresses that pass the camera-network policy (origin DISCOVERED)
are probed or reported; nothing outside the machine's own camera subnets is scanned.
Results never include MAC addresses or credentials; the camera IP is shown to
teachers/admins only (the routes require those roles).
"""
import ipaddress
import os
import re
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import rtsp
from .connect import detect_vendor
from .netguard import ORIGIN_DISCOVERED, HostNotAllowed, local_interface_ips

RUNNING, DONE, STOPPED = "RUNNING", "DONE", "STOPPED"
ST_AUTH_REQUIRED, ST_CONNECTABLE, ST_INCOMPATIBLE = "AUTH_REQUIRED", "CONNECTABLE", "NO_COMPATIBLE_STREAM"
ST_RTSP_DISABLED = "RTSP_DISABLED"
STATUS_LABELS_VI = {ST_AUTH_REQUIRED: "Cần xác thực", ST_CONNECTABLE: "Có thể kết nối",
                    ST_INCOMPATIBLE: "Camera không tương thích",
                    ST_RTSP_DISABLED: "Có thể là camera EZVIZ/Hikvision — chưa bật RTSP"}
SDK_PORT = 8000                    # Hikvision/EZVIZ SDK port: a vendor hint / diagnosis, never a video source
MAX_SUBNETS = 2
SCAN_WORKERS = 48
CONNECT_TIMEOUT = 0.4
JOB_TTL = 600.0
_ARP_RX = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


def local_private_ips(netguard):
    """This machine's private interface addresses (the guard's view when it has one)."""
    own = netguard.own_ips()
    return own if own else local_interface_ips()


def neighbour_ips(reader=None):
    """IPs from the OS neighbour table (no packets sent)."""
    text = ""
    try:
        if reader is not None:
            text = reader()
        elif os.path.exists("/proc/net/arp"):
            with open("/proc/net/arp", encoding="ascii", errors="replace") as f:
                text = f.read()
        else:
            text = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(set(_ARP_RX.findall(text)))


def rtsp_disabled(result):
    """Only the SDK port answered (no RTSP / ONVIF): a diagnosis result, never connectable."""
    protocols = set(result.get("protocols_detected") or [])
    return "SDK" in protocols and not ({"RTSP", "ONVIF"} & protocols)


class Result(dict):
    """A discovered device. Internal fields start with '_' and never leave the server."""


class DiscoveryJob:
    def __init__(self, owner_id, timeout):
        self.job_id = uuid.uuid4().hex
        self.owner_id = owner_id
        self.status = RUNNING
        self.started_at = time.time()
        self.finished_at = None
        self.timeout = timeout
        self.stage = "Đang tìm…"
        self.results = {}            # host -> Result
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

    def add(self, host, **fields):
        with self.lock:
            r = self.results.get(host)
            if r is None:
                r = self.results[host] = Result(discovery_id=uuid.uuid4().hex[:12], host=host, vendor=None, model=None,
                                                device_name=None, protocols_detected=[], auth_required=None,
                                                suggested_port=None, confidence="low", _onvif_port=None,
                                                _onvif_path=None, _hint=None)
            for k, v in fields.items():
                if k == "protocols":
                    for p in v:
                        if p not in r["protocols_detected"]:
                            r["protocols_detected"].append(p)
                elif k == "confidence":
                    order = {"low": 0, "medium": 1, "high": 2}
                    if order[v] > order[r["confidence"]]:
                        r["confidence"] = v
                elif v is not None and (r.get(k) is None or k == "auth_required"):
                    r[k] = v
            return r

    def public(self):
        with self.lock:
            items = []
            for r in self.results.values():
                if rtsp_disabled(r):
                    status = ST_RTSP_DISABLED
                elif not ({"RTSP", "ONVIF"} & set(r["protocols_detected"])):
                    status = ST_INCOMPATIBLE
                elif r["auth_required"] is False:
                    status = ST_CONNECTABLE
                else:
                    status = ST_AUTH_REQUIRED
                items.append({k: v for k, v in r.items() if not k.startswith("_")} |
                             {"status": status, "status_label": STATUS_LABELS_VI[status],
                              "display_name": r["device_name"] or (" ".join(x for x in (r["vendor"], r["model"]) if x))
                              or ("Thiết bị chưa rõ" if status == ST_RTSP_DISABLED else "Camera IP")})
        items.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2}[x["confidence"]], _ip_key(x["host"])))
        return {"job_id": self.job_id, "status": self.status, "stage": self.stage, "count": len(items),
                "started_at": self.started_at, "finished_at": self.finished_at, "timeout_seconds": self.timeout,
                "results": items}


def _ip_key(host):
    try:
        return (0, int(ipaddress.ip_address(host)))
    except ValueError:
        return (1, 0)


# ------------------------------------------------------------- strategies
def strategy_ws_discovery(ctx):
    from .onvif import pin_url, OnvifError, ws_discover
    ctx.job.stage = "Đang tìm camera ONVIF…"
    for sender, match in ws_discover(ctx.local_ips, timeout=min(3.0, ctx.remaining())):
        try:
            ip = ctx.netguard.check_ip(sender, ORIGIN_DISCOVERED)
        except HostNotAllowed:
            continue
        port, path = 80, "/onvif/device_service"
        for x in match.get("xaddrs") or []:
            try:
                _h, port, path = pin_url(x, ip)
                break
            except OnvifError:
                continue
        ctx.job.add(ip, protocols=["ONVIF"], vendor=detect_vendor(match.get("manufacturer"), match.get("name"),
                                                                  match.get("hardware")),
                    model=match.get("hardware"), device_name=match.get("name"), confidence="high",
                    _onvif_port=port, _onvif_path=path)


def strategy_ssdp(ctx):
    ctx.job.stage = "Đang dò SSDP…"
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
           "ST: ssdp:all\r\n\r\n").encode()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        s.settimeout(0.3)
        s.sendto(msg, ("239.255.255.250", 1900))
    except OSError:
        return
    end = time.monotonic() + min(2.5, ctx.remaining())
    try:
        while time.monotonic() < end and not ctx.stopped():
            try:
                data, (sender, _p) = s.recvfrom(8192)
            except (socket.timeout, OSError):
                continue
            server = next((ln.split(":", 1)[1].strip() for ln in data.decode("iso-8859-1").split("\r\n")
                           if ln.lower().startswith("server:")), None)
            vendor = detect_vendor(server)
            if vendor:
                try:
                    ctx.ssdp_hints[ctx.netguard.check_ip(sender, ORIGIN_DISCOVERED)] = vendor
                except HostNotAllowed:
                    continue
    finally:
        s.close()


def strategy_lan_scan(ctx):
    """ARP neighbours first, then the local /24(s); RTSP port, then (diagnosis) the SDK port."""
    ctx.job.stage = "Đang dò các thiết bị trong mạng LAN…"
    hosts, seen = [], set(ctx.local_ips)
    scan_nets = ctx.netguard.scan_networks(ctx.local_ips)[:MAX_SUBNETS]
    for ip in ctx.neighbours:
        try:
            ip = ctx.netguard.check_ip(ip, ORIGIN_DISCOVERED)
            a = ipaddress.ip_address(ip)
        except (HostNotAllowed, ValueError):
            continue
        if not any(a.version == n.version and a in n for n in scan_nets):
            continue                       # a neighbour outside the camera subnets is not probed
        if ip not in seen:
            seen.add(ip)
            hosts.append(ip)
    for net in scan_nets:
        for h in net.hosts():
            h = str(h)
            if h not in seen:
                seen.add(h)
                hosts.append(h)
    hosts = hosts[:MAX_SUBNETS * 254 + 64]

    def port_ok(port):
        try:
            ctx.netguard.check_port(port)
            return True
        except HostNotAllowed:
            return False
    if not port_ok(554):
        return                             # RTSP's port is not a camera port here: nothing to scan
    probe_sdk = port_ok(SDK_PORT)

    def scan(ip):
        """True when 554 is closed (the host is a candidate for the SDK-port diagnosis)."""
        if ctx.stopped():
            return False
        if not ctx.connect(ip, 554):
            return True
        status, server = ctx.rtsp_options(ip, 554)
        if status == rtsp.UNREACHABLE:
            return False
        probe = ctx.rtsp_probe(ip, 554, "/")
        sdk_port = ctx.connect(ip, SDK_PORT) if probe_sdk else False
        vendor = detect_vendor(server, probe.server) or ctx.ssdp_hints.get(ip)
        ctx.job.add(ip, protocols=["RTSP"], vendor=vendor, suggested_port=554, confidence="medium",
                    auth_required=probe.status in (rtsp.AUTH_REQUIRED,) or (None if probe.status != rtsp.OK
                                                                             else False),
                    _hint=vendor or ("EZVIZ" if sdk_port else None))
        return False

    def scan_sdk(ip):
        # 554 closed, 8000 open: typically an EZVIZ/Hikvision camera with RTSP ("Local Service
        # Settings") disabled. Reported for diagnosis only: no protocol talk, never connectable.
        if not ctx.stopped() and ctx.connect(ip, SDK_PORT):
            ctx.job.add(ip, protocols=["SDK"], confidence="low")
        return False

    def run(fn, items):
        futures = [(h, pool.submit(fn, h)) for h in items]
        flagged = []
        for h, f in futures:
            if ctx.stopped():
                break
            try:
                if f.result(timeout=max(0.05, ctx.remaining())):
                    flagged.append(h)
            except Exception:
                continue
        return flagged

    pool = ThreadPoolExecutor(max_workers=SCAN_WORKERS)
    try:
        rtsp_closed = run(scan, hosts)         # every RTSP probe first: real cameras win the time budget
        if probe_sdk and rtsp_closed and not ctx.stopped():
            run(scan_sdk, rtsp_closed)
    finally:
        # never wait for a straggler: the job finishes on time (stragglers end on their own
        # bounded socket deadlines; a late result just joins the finished job)
        pool.shutdown(wait=False, cancel_futures=True)


def tcp_connect(ip, port, timeout=CONNECT_TIMEOUT):
    try:
        with socket.create_connection((ip, port), timeout):
            return True
    except OSError:
        return False


DEFAULT_STRATEGIES = (strategy_ws_discovery, strategy_ssdp, strategy_lan_scan)


class _Ctx:
    def __init__(self, job, netguard, local_ips, neighbours, deadline, connect, rtsp_options, rtsp_probe):
        self.job, self.netguard, self.local_ips, self.neighbours = job, netguard, local_ips, neighbours
        self.deadline = deadline
        self.connect, self.rtsp_options, self.rtsp_probe = connect, rtsp_options, rtsp_probe
        self.ssdp_hints = {}

    def remaining(self):
        return max(0.0, self.deadline - time.monotonic())

    def stopped(self):
        return self.job.stop_event.is_set() or self.remaining() <= 0


class DiscoveryManager:
    def __init__(self, netguard, strategies=DEFAULT_STRATEGIES, timeout=15.0, max_running=2,
                 local_ips=None, neighbours=None, connect=tcp_connect, rtsp_options=rtsp.options,
                 rtsp_probe=rtsp.probe):
        self.netguard = netguard
        self.strategies = strategies
        self.timeout = max(5.0, min(20.0, float(timeout)))
        self.max_running = max_running
        self._local_ips = local_ips
        self._neighbours = neighbours
        self.connect, self.rtsp_options, self.rtsp_probe = connect, rtsp_options, rtsp_probe
        self.jobs = {}
        self.lock = threading.Lock()

    def _gc(self):
        now = time.time()
        for jid, j in list(self.jobs.items()):
            if j.status != RUNNING and now - (j.finished_at or now) > JOB_TTL:
                del self.jobs[jid]

    def start(self, owner_id):
        with self.lock:
            self._gc()
            for j in self.jobs.values():
                if j.owner_id == owner_id and j.status == RUNNING:
                    return j
            if sum(1 for j in self.jobs.values() if j.status == RUNNING) >= self.max_running:
                raise RuntimeError("busy")
            job = DiscoveryJob(owner_id, self.timeout)
            self.jobs[job.job_id] = job
        threading.Thread(target=self._run, args=(job,), name="camera-discovery", daemon=True).start()
        return job

    def _run(self, job):
        try:
            local = list(self._local_ips) if self._local_ips is not None else local_private_ips(self.netguard)
            neigh = list(self._neighbours) if self._neighbours is not None else neighbour_ips()
            ctx = _Ctx(job, self.netguard, local, neigh, time.monotonic() + job.timeout, self.connect,
                       self.rtsp_options, self.rtsp_probe)
            for strategy in self.strategies:
                if ctx.stopped():
                    break
                try:
                    strategy(ctx)
                except Exception as exc:          # one failing strategy never ends discovery
                    print("[CAMERA] discovery strategy error:", type(exc).__name__)
            for host, vendor in ctx.ssdp_hints.items():
                if host in job.results:
                    job.add(host, vendor=vendor)
        finally:
            job.finished_at = time.time()
            job.status = STOPPED if job.stop_event.is_set() else DONE
            n = len(job.results)
            job.stage = "Không tìm thấy camera" if n == 0 else f"Đã tìm thấy {n} thiết bị"

    def get(self, job_id, owner_id):
        j = self.jobs.get(job_id)
        return j if j is not None and j.owner_id == owner_id else None

    def stop(self, job_id, owner_id):
        j = self.get(job_id, owner_id)
        if j is not None:
            j.stop_event.set()
        return j

    def lookup(self, owner_id, discovery_id):
        """Internal result (host, ONVIF port/path, hint) of the owner's jobs."""
        for j in list(self.jobs.values()):
            if j.owner_id != owner_id:
                continue
            with j.lock:
                for r in j.results.values():
                    if r["discovery_id"] == discovery_id:
                        return dict(r)
        return None
