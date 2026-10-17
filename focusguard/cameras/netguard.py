"""SSRF guard and camera-network policy for camera connections.

Camera settings make the server open network connections to an address a user
typed, so the camera code must never become a generic URL fetcher or a port
scanner for the school network. Rules:

* only the camera protocols this feature needs (RTSP for video; HTTP only for
  ONVIF SOAP calls built by the server itself) — ``file://``, ``ftp://``,
  ``gopher://``, ``unix:`` and every other scheme are rejected;
* always blocked: loopback (unless ``FG_CAMERA_ALLOW_LOOPBACK=1`` for local
  test servers), the server's own interface addresses, link-local incl. cloud
  metadata 169.254.169.254 / fe80::/10, multicast, unspecified, CGNAT 100.64/10;
* **camera networks** (where cameras may be):

  - ``FG_CAMERA_ALLOWED_NETWORKS`` (comma separated CIDRs) configured: ONLY
    those networks — never "any private address";
  - not configured: the server's own local /24 subnet(s). A device found by
    LAN discovery there may be used; a manually typed address there is
    accepted in development, and in production only when it is on the
    allowlist (``FG_CAMERA_ALLOWED_NETWORKS`` / ``FG_CAMERA_ALLOWED_HOSTS``);
  - ``FG_CAMERA_ALLOWED_HOSTS`` (IPs / CIDRs / host names) is always allowed;
* only camera ports (``FG_CAMERA_ALLOWED_PORTS``, default RTSP / ONVIF ports);
* host names are resolved by the server and EVERY resolved address must pass;
  the caller then connects to the returned IP (never re-resolving the name), so
  DNS rebinding cannot swap in another address between check and connect.
"""
import ipaddress
import os
import re
import socket
import time
from urllib.parse import unquote, urlsplit

PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]
ALWAYS_BLOCKED = [ipaddress.ip_network(n) for n in (
    "169.254.0.0/16", "fe80::/10", "100.64.0.0/10", "0.0.0.0/8", "224.0.0.0/4", "ff00::/8", "fd00:ec2::254/128")]
METADATA_IPS = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254"),
                ipaddress.ip_address("100.100.100.200")}
VIDEO_SCHEMES = ("rtsp",)
# RTSP (554, 8554, 10554, 5554, 7447), ONVIF / camera web services (80, 88, 2020, 5000, 8000, 8080, 8899, 8999)
DEFAULT_CAMERA_PORTS = frozenset({80, 88, 554, 2020, 5000, 5554, 7447, 8000, 8080, 8554, 8899, 8999, 10554})
_HOSTNAME_RX = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))*\.?$", re.I)

ORIGIN_MANUAL, ORIGIN_DISCOVERED = "MANUAL", "DISCOVERED"

# Messages are user facing (Vietnamese); codes are stable for tests and API clients.
REASONS_VI = {
    "empty": "Chưa nhập địa chỉ camera.",
    "malformed": "Địa chỉ camera không hợp lệ.",
    "scheme": "Chỉ hỗ trợ camera RTSP/ONVIF trong mạng LAN.",
    "public": "Chỉ kết nối tới camera trong mạng camera được phép.",
    "metadata": "Địa chỉ này bị chặn.",
    "loopback": "Không dùng địa chỉ máy chủ (localhost) làm camera.",
    "self": "Không dùng địa chỉ của chính máy chủ làm camera.",
    "blocked": "Địa chỉ này bị chặn.",
    "network_not_allowed": "Địa chỉ này không thuộc mạng camera được phép. Hãy dùng “Tìm camera trong mạng LAN”, "
                           "hoặc nhờ quản trị viên thêm mạng camera (FG_CAMERA_ALLOWED_NETWORKS).",
    "unresolved": "Không tìm thấy thiết bị với tên này trong mạng.",
    "port": "Cổng không hợp lệ (1–65535).",
    "port_not_allowed": "Cổng này không phải cổng camera được phép (RTSP / ONVIF). Quản trị viên có thể thêm vào "
                        "FG_CAMERA_ALLOWED_PORTS.",
    "credentials_in_path": "Đường dẫn luồng không được chứa tên đăng nhập / mật khẩu — hãy nhập vào ô Tên đăng nhập "
                           "và Mật khẩu (được mã hóa).",
}


class HostNotAllowed(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(REASONS_VI.get(code, REASONS_VI["blocked"]))


def _env(env):
    return env if env is not None else os.environ


def _env_flag(name, env=None):
    return _env(env).get(name, "").strip().lower() in ("1", "true", "yes")


def approved_entries(env=None):
    raw = _env(env).get("FG_CAMERA_ALLOWED_HOSTS", "")
    nets, names = [], set()
    for item in (x.strip() for x in raw.split(",")):
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            if _HOSTNAME_RX.match(item):
                names.add(item.lower().rstrip("."))
    return nets, names


def camera_networks_from_env(env=None):
    """FG_CAMERA_ALLOWED_NETWORKS -> list of networks, or None when not set (or set to an empty
    string: the own-subnet default applies). Invalid entries are ignored (and reported); a value
    with only invalid entries allows NO network (fail closed)."""
    raw = _env(env).get("FG_CAMERA_ALLOWED_NETWORKS")
    if raw is None or not str(raw).strip() or (str(raw).strip().startswith("<") and str(raw).strip().endswith(">")):
        return None
    nets = []
    for item in (x.strip() for x in str(raw).split(",")):
        if not item:
            continue
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            print(f"[CAMERA] FG_CAMERA_ALLOWED_NETWORKS: ignored invalid entry {item[:40]!r}")
            continue
        if net.prefixlen == 0:
            print("[CAMERA] FG_CAMERA_ALLOWED_NETWORKS: a /0 network is not a camera network (ignored)")
            continue
        nets.append(net)
    return nets


def camera_ports_from_env(env=None):
    raw = _env(env).get("FG_CAMERA_ALLOWED_PORTS", "")
    ports = set()
    for item in (x.strip() for x in str(raw or "").split(",")):
        if item.isdigit() and 1 <= int(item) <= 65535:
            ports.add(int(item))
    return frozenset(ports) if ports else DEFAULT_CAMERA_PORTS


def local_interface_ips():
    """Private IPv4 addresses of this machine's interfaces (no packet is sent)."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))        # UDP "connect" sends nothing; picks the LAN interface
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    out = []
    for ip in sorted(ips):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not a.is_loopback and any(a in n for n in PRIVATE_NETS):
            out.append(ip)
    return out


class NetGuard:
    """Camera network policy (tests inject a resolver, interface addresses and networks).

    The constructor default (``camera_networks=None``, ``local_ips=None``) is the permissive
    library default used by unit tests: any private address. Production code builds the guard
    with :meth:`from_env`, which always applies the camera-network policy."""

    LOCAL_CACHE_SECONDS = 60.0
    NAME_VETTED_TTL = 600.0

    def __init__(self, approved_nets=(), approved_names=(), allow_loopback=False, resolver=None,
                 camera_networks=None, local_ips=None, manual_local=True, allowed_ports=None):
        self.approved_nets = list(approved_nets)
        self.approved_names = {n.lower().rstrip(".") for n in approved_names}
        self.allow_loopback = allow_loopback
        self.camera_networks = None if camera_networks is None else list(camera_networks)
        # local_ips: None (any private: library default), a list, or a callable returning a list
        self._local_ips = local_ips
        self._local_cache = (0.0, [])
        self.manual_local = manual_local
        self.allowed_ports = frozenset(allowed_ports) if allowed_ports else None
        # address -> expiry: what an FG_CAMERA_ALLOWED_HOSTS *name* resolved to recently
        # (re-checked by sources at connect; refreshed on every resolution, expires otherwise)
        self._name_vetted = {}
        self._clock = time.monotonic
        self.resolver = resolver or _resolve

    @classmethod
    def from_env(cls, env=None, resolver=None, local_ips=None, manual_local=None):
        from ..security import is_production
        e = _env(env)
        nets, names = approved_entries(env)
        prod = is_production((e.get("FOCUSGUARD_ENV") or e.get("FLASK_ENV") or "development").strip().lower())
        # loopback test cameras are a development aid only: never in production
        loopback = _env_flag("FG_CAMERA_ALLOW_LOOPBACK", env) and not prod
        return cls(nets, names, allow_loopback=loopback, resolver=resolver,
                   camera_networks=camera_networks_from_env(env),
                   local_ips=local_ips if local_ips is not None else local_interface_ips,
                   manual_local=(not prod) if manual_local is None else manual_local,
                   allowed_ports=camera_ports_from_env(env))

    # ------------------------------------------------------------ policy view
    @property
    def policy(self):
        if self.camera_networks is not None:
            return "ALLOWED_NETWORKS"
        return "ANY_PRIVATE" if self._local_ips is None else "LOCAL_SUBNETS"

    def own_ips(self):
        if self._local_ips is None:
            return []
        if not callable(self._local_ips):
            return list(self._local_ips)
        at, ips = self._local_cache
        if self._clock() - at > self.LOCAL_CACHE_SECONDS or not at:
            try:
                ips = list(self._local_ips())
            except Exception:
                ips = []
            self._local_cache = (self._clock(), ips)
        return ips

    def local_networks(self, local_ips=None):
        nets = []
        for ip in (self.own_ips() if local_ips is None else local_ips):
            try:
                net = ipaddress.ip_network(f"{ip}/24", strict=False)
            except ValueError:
                continue
            if net not in nets:
                nets.append(net)
        return nets

    def scan_networks(self, local_ips=None):
        """Networks LAN discovery may scan: the machine's OWN local /24 subnets, narrowed to the
        configured camera networks (never a remote network, never the whole private space)."""
        out = []
        for local in self.local_networks(local_ips):
            if self.camera_networks is None:
                out.append(local)
                continue
            for allowed in self.camera_networks:
                if allowed.version != local.version or not allowed.overlaps(local):
                    continue
                net = local if local.subnet_of(allowed) else allowed
                if net not in out:
                    out.append(net)
        return out

    # ---------------------------------------------------------------- checks
    def check_port(self, port):
        p = check_port(port, None)
        if p is not None and self.allowed_ports is not None and p not in self.allowed_ports:
            raise HostNotAllowed("port_not_allowed")
        return p

    def _hard_checks(self, ip):
        """Rules nothing can override (an admin-approved name included). Returns (addr, loopback_ok)."""
        try:
            addr = ipaddress.ip_address(str(ip).split("%", 1)[0])
        except ValueError:
            raise HostNotAllowed("malformed")
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped                       # normalise BEFORE any test (::ffff:127.0.0.1)
        if addr in METADATA_IPS:
            raise HostNotAllowed("metadata")
        if addr.is_loopback:
            if self.allow_loopback:
                return addr, True
            raise HostNotAllowed("loopback")
        if any(addr in n for n in ALWAYS_BLOCKED) or addr.is_unspecified or addr.is_multicast or addr.is_link_local:
            raise HostNotAllowed("blocked")
        if not self.allow_loopback and str(addr) in self.own_ips():
            raise HostNotAllowed("self")                  # the server's own services are not cameras
        return addr, False

    def check_ip(self, ip, origin=ORIGIN_MANUAL):
        """Raise HostNotAllowed unless ``ip`` may be contacted. Returns the normalised address.

        ``origin``: ORIGIN_DISCOVERED for a device LAN discovery itself found (server side), else
        ORIGIN_MANUAL (typed / pasted by a user)."""
        addr, loopback_ok = self._hard_checks(ip)
        if loopback_ok:
            return str(addr)
        if any(addr.version == n.version and addr in n for n in self.approved_nets):
            return str(addr)
        if self._name_vetted.get(str(addr), 0) > self._clock():
            return str(addr)
        in_private = any(addr.version == n.version and addr in n for n in PRIVATE_NETS)
        if self.camera_networks is not None:              # explicit policy: only these networks
            if any(addr.version == n.version and addr in n for n in self.camera_networks):
                return str(addr)
            raise HostNotAllowed("network_not_allowed" if in_private else "public")
        if self._local_ips is None:                       # library default (tests): any private address
            if in_private:
                return str(addr)
            raise HostNotAllowed("public")
        if any(addr.version == n.version and addr in n for n in self.local_networks()):
            if origin == ORIGIN_DISCOVERED or self.manual_local:
                return str(addr)
        raise HostNotAllowed("network_not_allowed" if in_private else "public")

    def resolve_host(self, host, origin=ORIGIN_MANUAL):
        """Validate a user supplied host (IP literal or LAN host name) and return ONE vetted IP.

        Every address the name resolves to must pass :meth:`check_ip`."""
        host = normalize_host(host)
        try:
            return self.check_ip(ipaddress.ip_address(host), origin)
        except HostNotAllowed:
            raise
        except ValueError:
            pass
        if not _HOSTNAME_RX.match(host):
            raise HostNotAllowed("malformed")
        if host.lower().rstrip(".") in ("localhost", "localhost.localdomain") and not self.allow_loopback:
            raise HostNotAllowed("loopback")
        try:
            addrs = self.resolver(host)
        except OSError:
            raise HostNotAllowed("unresolved")
        if not addrs:
            raise HostNotAllowed("unresolved")
        approved_name = host.lower().rstrip(".") in self.approved_names
        vetted = []
        for a in addrs:
            if approved_name:
                ip, _ = self._hard_checks(a)              # same hard rules: no approval overrides them
                self._remember_name_address(str(ip))
                vetted.append(str(ip))
            else:
                vetted.append(self.check_ip(a, origin))
        return vetted[0]

    def _remember_name_address(self, ip):
        now = self._clock()
        self._name_vetted = {k: v for k, v in self._name_vetted.items() if v > now}
        if len(self._name_vetted) >= 256 and ip not in self._name_vetted:
            self._name_vetted.pop(min(self._name_vetted, key=self._name_vetted.get))
        self._name_vetted[ip] = now + self.NAME_VETTED_TTL


def _resolve(host):
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({i[4][0] for i in infos})


def normalize_host(host):
    if host is None:
        raise HostNotAllowed("empty")
    host = str(host).strip()
    if not host:
        raise HostNotAllowed("empty")
    if len(host) > 253 or any(c in host for c in "/\\@?#&;\"'<> \t\r\n") or "://" in host:
        raise HostNotAllowed("malformed")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def check_port(port, default=None):
    if port in (None, ""):
        return default
    try:
        p = int(port)
    except (TypeError, ValueError):
        raise HostNotAllowed("port")
    if not 1 <= p <= 65535:
        raise HostNotAllowed("port")
    return p


# user=/password=/pwd=/... inside a path or query: the stream path is stored and shown in
# plaintext, so credentials must go in the (encrypted) username / secret fields instead.
_CRED_PARAM_RX = re.compile(r"(?i)(?:^|[/?&;_.])(?:user(?:name)?|login|pass(?:word|wd)?|pwd|psw|auth|"
                            r"token|secret|key|code|verification_?code)=")


def check_path(path):
    """A stream path: relative URL path + optional query, no scheme, no host, no credentials."""
    if path in (None, ""):
        return None
    path = str(path).strip()
    if len(path) > 200 or any(c in path for c in "\\\"'<> \t\r\n@") or "://" in path or path.startswith("//"):
        raise HostNotAllowed("malformed")
    if _CRED_PARAM_RX.search(path):
        raise HostNotAllowed("credentials_in_path")
    return path if path.startswith("/") else "/" + path


def split_camera_url(text):
    """Parse a pasted camera URL into parts, separating the credentials.

    Returns ``{"scheme", "host", "port", "path", "username", "secret"}``. Only
    ``rtsp://`` is accepted. The raw URL is never stored by callers: only the
    parts (with the secret encrypted)."""
    raw = (text or "").strip()
    if not raw:
        raise HostNotAllowed("empty")
    if len(raw) > 500 or any(c in raw for c in "\r\n\t "):
        raise HostNotAllowed("malformed")
    try:
        u = urlsplit(raw)
        port = u.port
    except ValueError:
        raise HostNotAllowed("malformed")
    if (u.scheme or "").lower() not in VIDEO_SCHEMES:
        raise HostNotAllowed("scheme")
    if not u.hostname:
        raise HostNotAllowed("malformed")
    path = u.path or ""
    if u.query:
        path += "?" + u.query
    return {"scheme": u.scheme.lower(), "host": u.hostname, "port": check_port(port, 554),
            "path": check_path(path) if path not in ("", "/") else None,
            "username": _clean_cred(unquote(u.username), 64) if u.username else None,
            "secret": _clean_cred(unquote(u.password), 128) if u.password is not None else None}


def _clean_cred(value, limit):
    """No control characters (CR/LF would reach the RTSP Authorization header)."""
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HostNotAllowed("malformed")
    return value[:limit]
