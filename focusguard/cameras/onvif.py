"""Minimal ONVIF support (standard library only): WS-Discovery and stream resolution.

Why not a third-party ONVIF library: the maintained options (onvif-zeep and
forks) pull in zeep + lxml + WSDL files for four SOAP calls. FocusGuard only
needs: discover devices (WS-Discovery Probe), read the manufacturer/model, and
ask for the RTSP stream URI of a media profile. That is done here with
``socket`` + ``http.client`` + ``xml.etree`` and WS-Security UsernameToken
(PasswordDigest). Video itself is always ingested over RTSP.

Safety: every HTTP call goes to the device IP that netguard vetted. Addresses a
device advertises (XAddrs, stream URIs) are never trusted for the host part:
the vetted device IP is substituted, so a device cannot point FocusGuard at
another machine. Responses are size-capped and XML with a DOCTYPE/ENTITY is
refused (no entity expansion). No redirects are followed.
"""
import base64
import datetime as _dt
import hashlib
import http.client
import os
import socket
import threading
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from .netguard import HostNotAllowed, check_path, check_port

WSD_ADDR = ("239.255.255.250", 3702)
MAX_XML = 256 * 1024
NS_SOAP = "http://www.w3.org/2003/05/soap-envelope"


class OnvifError(Exception):
    pass


class OnvifAuthError(OnvifError):
    pass


def parse_xml(data):
    raw = data if isinstance(data, bytes) else str(data).encode("utf-8")
    if len(raw) > MAX_XML:
        raise OnvifError("response too large")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise OnvifError("refusing XML with DOCTYPE/ENTITY")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise OnvifError(f"malformed XML: {exc}")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def find_all(root, name):
    return [e for e in root.iter() if _local(e.tag) == name]


def find_text(root, name):
    for e in root.iter():
        if _local(e.tag) == name:
            return (e.text or "").strip()
    return None


# ------------------------------------------------------------- WS-Discovery
def probe_message(message_id=None):
    mid = message_id or f"uuid:{uuid.uuid4()}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        f'<e:Header><w:MessageID>{mid}</w:MessageID>'
        '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
        '<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
        '</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>'
    ).encode("utf-8")


def parse_probe_match(data):
    """ProbeMatch -> {"xaddrs": [...], "name", "hardware", "manufacturer", "scopes": [...]}."""
    root = parse_xml(data)
    out = {"xaddrs": [], "name": None, "hardware": None, "manufacturer": None, "scopes": []}
    for x in find_all(root, "XAddrs"):
        out["xaddrs"] += (x.text or "").split()
    for s in find_all(root, "Scopes"):
        out["scopes"] += (s.text or "").split()
    from urllib.parse import unquote
    for scope in out["scopes"]:
        low = scope.lower()
        for key, field in (("/name/", "name"), ("/hardware/", "hardware"), ("/mfr/", "manufacturer"),
                           ("/manufacturer/", "manufacturer")):
            if key in low and out[field] is None:
                out[field] = unquote(scope[low.index(key) + len(key):]).replace("_", " ").strip()[:60] or None
    return out


def ws_discover(local_ips, timeout=3.0, sock_factory=None, clock=None):
    """Send one WS-Discovery Probe per local interface; return [(sender_ip, ProbeMatch dict)].

    Multicast TTL 1 (never leaves the LAN)."""
    import time as _time
    clock = clock or _time.monotonic
    results = []
    socks = []
    msg = probe_message()
    for ip in local_ips or ["0.0.0.0"]:
        try:
            s = (sock_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)))()
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            if ip != "0.0.0.0":
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            s.bind((ip, 0))
            s.settimeout(0.3)
            s.sendto(msg, WSD_ADDR)
            socks.append(s)
        except OSError:
            continue
    end = clock() + timeout
    try:
        while clock() < end and socks:
            for s in socks:
                try:
                    data, (sender, _port) = s.recvfrom(65535)
                except (socket.timeout, BlockingIOError):
                    continue
                except OSError:
                    continue
                try:
                    results.append((sender, parse_probe_match(data)))
                except OnvifError:
                    continue
    finally:
        for s in socks:
            s.close()
    return results


# ---------------------------------------------------------------- SOAP calls
def security_header(username, password, created=None, nonce=None):
    nonce = nonce if nonce is not None else os.urandom(16)
    created = created or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + (password or "").encode("utf-8")).digest())
    return (
        '<s:Header><Security s:mustUnderstand="1" '
        'xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        f'<UsernameToken><Username>{escape(username or "")}</Username>'
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0'
        f'#PasswordDigest">{digest.decode()}</Password>'
        '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-soap-message-security-1.0'
        f'#Base64Binary">{base64.b64encode(nonce).decode()}</Nonce>'
        '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f'{created}</Created></UsernameToken></Security></s:Header>')


def envelope(body, username=None, password=None):
    head = security_header(username, password) if username else ""
    return (f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="{NS_SOAP}" '
            'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
            f'xmlns:tt="http://www.onvif.org/ver10/schema">{head}<s:Body>{body}</s:Body></s:Envelope>').encode()


def pin_url(url, device_ip, default_port=80):
    """Keep only port + path of a device-advertised URL; the host is always the vetted device IP."""
    try:
        u = urlsplit(url)
        port = u.port or default_port
    except ValueError:
        raise OnvifError("bad service address")
    if (u.scheme or "").lower() != "http":
        raise OnvifError("only plain-HTTP ONVIF service addresses are supported")
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    return device_ip, int(port), path


def _shutdown(conn):
    sock = getattr(conn, "sock", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class OnvifClient:
    def __init__(self, device_ip, port=80, path="/onvif/device_service", username=None, password=None,
                 timeout=4.0, http_factory=None):
        self.ip, self.port, self.path = device_ip, int(port), path
        self.username, self._password = username, password
        self.timeout = timeout
        self.http_factory = http_factory or http.client.HTTPConnection
        self.media = None

    def _post(self, port, path, body):
        conn = self.http_factory(self.ip, port, timeout=self.timeout)
        # Overall deadline: a device that drips bytes resets the per-recv timeout forever;
        # shutting the socket down unblocks the read.
        watchdog = threading.Timer(3 * self.timeout, _shutdown, args=(conn,))
        watchdog.daemon = True
        watchdog.start()
        try:
            conn.request("POST", path, body=envelope(body, self.username, self._password),
                         headers={"Content-Type": "application/soap+xml; charset=utf-8",
                                  "Host": f"{self.ip}:{port}"})
            resp = conn.getresponse()
            data = resp.read(MAX_XML + 1)
            status = resp.status
        except (OSError, http.client.HTTPException) as exc:
            raise OnvifError(f"{type(exc).__name__}")
        finally:
            watchdog.cancel()
            conn.close()
        if len(data) > MAX_XML:
            raise OnvifError("response too large")
        text = data.decode("utf-8", "replace")
        if status in (401, 403) or "NotAuthorized" in text or "FailedAuthentication" in text:
            raise OnvifAuthError("not authorized")
        if status >= 400:
            raise OnvifError(f"HTTP {status}")
        return parse_xml(data)

    def device_information(self):
        r = self._post(self.port, self.path, "<tds:GetDeviceInformation/>")
        return {"vendor": find_text(r, "Manufacturer"), "model": find_text(r, "Model")}

    def media_service(self):
        r = self._post(self.port, self.path, "<tds:GetCapabilities><tds:Category>Media</tds:Category>"
                                             "</tds:GetCapabilities>")
        media = None
        for m in find_all(r, "Media"):
            x = find_text(m, "XAddr")
            if x:
                media = x
                break
        self.media = pin_url(media, self.ip, self.port) if media else (self.ip, self.port, self.path)
        return self.media

    def profiles(self):
        _ip, port, path = self.media or self.media_service()
        r = self._post(port, path, "<trt:GetProfiles/>")
        out = []
        for p in find_all(r, "Profiles"):
            token = p.attrib.get("token")
            if not token:
                continue
            w, h = find_text(p, "Width"), find_text(p, "Height")
            out.append({"token": token[:64], "name": (find_text(p, "Name") or token)[:64],
                        "width": int(w) if w and w.isdigit() else None,
                        "height": int(h) if h and h.isdigit() else None})
        return out

    def stream_uri(self, profile_token):
        _ip, port, path = self.media or self.media_service()
        body = ("<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport>"
                "<tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>"
                f"<trt:ProfileToken>{escape(profile_token)}</trt:ProfileToken></trt:GetStreamUri>")
        r = self._post(port, path, body)
        uri = find_text(r, "Uri")
        if not uri:
            raise OnvifError("no stream URI")
        try:
            u = urlsplit(uri)
            rport = u.port or 554
        except ValueError:
            raise OnvifError("bad stream URI")
        if (u.scheme or "").lower() != "rtsp":
            raise OnvifError("stream is not RTSP")
        # host pinned to the device; userinfo is dropped, and a path carrying credentials
        # (stored and shown in plaintext) is refused -> vendor / generic paths are tried instead
        try:
            path = check_path((u.path or "/") + (("?" + u.query) if u.query else ""))
            rport = check_port(rport, 554)
        except HostNotAllowed:
            raise OnvifError("stream URI not usable")
        return {"port": rport, "path": path}
