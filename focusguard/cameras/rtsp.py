"""Minimal RTSP client used ONLY to probe a camera (OPTIONS / DESCRIBE).

It tells "wrong password" (401) from "no stream at this path" (404) before
OpenCV/FFmpeg opens the video, which cannot make that distinction. Supports
Basic and Digest (MD5, with or without qop=auth) authentication.

Safety: the caller passes an IP already vetted by netguard; the request URI
never contains credentials; responses are size-capped; one TCP connection per
probe with a short timeout. Credentials are sent only in the Authorization
header of the DESCRIBE retry after a 401 challenge.
"""
import base64
import hashlib
import os
import re
import socket
import time
from collections import namedtuple

OK = "OK"
AUTH_REQUIRED = "AUTH_REQUIRED"      # 401 and no credentials supplied
AUTH_FAILED = "AUTH_FAILED"          # 401 with the supplied credentials
NOT_FOUND = "NOT_FOUND"              # no stream at this path
UNREACHABLE = "UNREACHABLE"          # connection refused / timeout
REDIRECT = "REDIRECT"                # 3xx: a camera stream is never followed elsewhere
ERROR = "ERROR"                      # not an RTSP server / malformed reply
# The probe presents itself like the FFmpeg client that opens the stream afterwards, so a device
# cannot trivially answer the probe one way and the real connection another (e.g. with a redirect).
PROBE_USER_AGENT = "Lavf/60.16.100"

Response = namedtuple("Response", "status reason headers body")
ProbeResult = namedtuple("ProbeResult", "status server has_video codec code")
MAX_RESPONSE = 64 * 1024
_CHALLENGE_RX = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|([^\s,]+))')


def _uri(ip, port, path):
    host = f"[{ip}]" if ":" in str(ip) else str(ip)
    return f"rtsp://{host}:{int(port)}{path or '/'}"


def _md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def auth_header(challenge, method, uri, username, secret, nc=1):
    """Authorization header value for a WWW-Authenticate challenge (Basic or Digest)."""
    scheme, _, rest = (challenge or "").strip().partition(" ")
    if scheme.lower() == "basic":
        return "Basic " + base64.b64encode(f"{username}:{secret}".encode("utf-8")).decode("ascii")
    if scheme.lower() != "digest":
        return None
    p = {m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3)) for m in _CHALLENGE_RX.finditer(rest)}
    realm, nonce = p.get("realm", ""), p.get("nonce", "")
    ha1 = _md5(f"{username}:{realm}:{secret}")
    ha2 = _md5(f"{method}:{uri}")
    qop = p.get("qop")
    parts = [f'username="{username}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"']
    if qop and "auth" in [q.strip() for q in qop.split(",")]:
        cnonce = os.urandom(8).hex()
        ncs = f"{nc:08x}"
        resp = _md5(f"{ha1}:{nonce}:{ncs}:{cnonce}:auth:{ha2}")
        parts += [f'response="{resp}"', "qop=auth", f"nc={ncs}", f'cnonce="{cnonce}"']
    else:
        parts.append(f'response="{_md5(f"{ha1}:{nonce}:{ha2}")}"')
    if p.get("opaque"):
        parts.append(f'opaque="{p["opaque"]}"')
    return "Digest " + ", ".join(parts)


class _Conn:
    def __init__(self, ip, port, timeout, connector=None):
        self.sock = (connector or socket.create_connection)((ip, int(port)), timeout)
        self.sock.settimeout(timeout)
        self.timeout = timeout
        self.cseq = 0
        self.buf = b""
        self.deadline = None

    def _recv(self):
        """recv with an OVERALL deadline per response: a device dripping one byte per second
        cannot keep a probe (and the discovery job waiting on it) alive."""
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise socket.timeout("response deadline")
        self.sock.settimeout(min(self.timeout, left))
        return self.sock.recv(4096)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def request(self, method, uri, headers=None):
        self.cseq += 1
        lines = [f"{method} {uri} RTSP/1.0", f"CSeq: {self.cseq}", f"User-Agent: {PROBE_USER_AGENT}"]
        for k, v in (headers or {}).items():
            lines.append(f"{k}: {v}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        self.deadline = time.monotonic() + 2 * self.timeout
        return self._read_response()

    def _read_response(self):
        while b"\r\n\r\n" not in self.buf:
            chunk = self._recv()
            if not chunk:
                break
            self.buf += chunk
            if len(self.buf) > MAX_RESPONSE:
                raise ValueError("response too large")
        head, sep, rest = self.buf.partition(b"\r\n\r\n")
        if not sep:
            raise ValueError("incomplete response")
        lines = head.decode("iso-8859-1").split("\r\n")
        m = re.match(r"RTSP/\d\.\d\s+(\d{3})\s*(.*)", lines[0])
        if not m:
            raise ValueError("not an RTSP response")
        headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            if k:
                headers.setdefault(k.strip().lower(), v.strip())
        length = min(int(headers.get("content-length", "0") or 0), MAX_RESPONSE)
        while len(rest) < length:
            chunk = self._recv()
            if not chunk:
                break
            rest += chunk
        body, self.buf = rest[:length], rest[length:]
        return Response(int(m.group(1)), m.group(2), headers, body.decode("utf-8", "replace"))


def _sdp_video(body):
    has_video = "m=video" in (body or "")
    codec = None
    m = re.search(r"a=rtpmap:\d+\s+([A-Za-z0-9.\-]+)/", body or "")
    if m:
        codec = m.group(1).upper()
    return has_video, codec


def options(ip, port=554, timeout=1.5, connector=None):
    """Is there an RTSP server? Returns (status, server header) without credentials."""
    try:
        c = _Conn(ip, port, timeout, connector)
    except OSError:
        return UNREACHABLE, None
    try:
        r = c.request("OPTIONS", _uri(ip, port, "/"))
        return (OK if r.status < 500 else ERROR), r.headers.get("server")
    except (OSError, ValueError):
        return ERROR, None
    finally:
        c.close()


def probe(ip, port=554, path="/", username=None, secret=None, timeout=3.0, connector=None):
    """DESCRIBE ``path``; authenticate once if challenged. Never retries a rejected password."""
    uri = _uri(ip, port, path)
    try:
        c = _Conn(ip, port, timeout, connector)
    except OSError:
        return ProbeResult(UNREACHABLE, None, False, None, None)
    try:
        r = c.request("DESCRIBE", uri, {"Accept": "application/sdp"})
        server = r.headers.get("server")
        if r.status == 401:
            if not username:
                return ProbeResult(AUTH_REQUIRED, server, False, None, 401)
            hdr = auth_header(r.headers.get("www-authenticate"), "DESCRIBE", uri, username, secret or "")
            if hdr is None:
                return ProbeResult(ERROR, server, False, None, 401)
            r = c.request("DESCRIBE", uri, {"Accept": "application/sdp", "Authorization": hdr})
            server = r.headers.get("server") or server
            if r.status == 401:
                return ProbeResult(AUTH_FAILED, server, False, None, 401)
        if 300 <= r.status < 400:
            return ProbeResult(REDIRECT, server, False, None, r.status)
        if r.status == 200:
            has_video, codec = _sdp_video(r.body)
            return ProbeResult(OK if has_video else NOT_FOUND, server, has_video, codec, 200)
        if r.status == 403:
            return ProbeResult(AUTH_FAILED if username else AUTH_REQUIRED, server, False, None, 403)
        if r.status in (404, 454, 400, 451, 457):
            return ProbeResult(NOT_FOUND, server, False, None, r.status)
        return ProbeResult(ERROR, server, False, None, r.status)
    except (OSError, ValueError):
        return ProbeResult(ERROR, None, False, None, None)
    finally:
        c.close()
