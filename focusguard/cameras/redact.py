"""Central credential redaction.

Every string that may contain a camera credential passes through :func:`redact`
before it reaches a log line, an exception message shown to a user, a health
payload, an audit record or an API error. It removes:

* URL user-info: ``rtsp://user:pass@host`` -> ``rtsp://***@host``
* ``password=…``, ``pwd=…``, ``verification_code=…``, ``secret=…``, ``token=…``
* HTTP ``Authorization:`` header values (Basic / Digest / Bearer)
* ONVIF / WS-Security ``<Password>…</Password>`` and ``<Nonce>`` elements
* any explicitly known secret value (and its URL-encoded form)
"""
import re
from urllib.parse import quote

MASK = "***"
_USERINFO_RX = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]{1,15}://)[^/\s@]*@")
_KV_RX = re.compile(r"(?i)\b(password|passwd|pwd|pass|secret|verification[_\- ]?code|verify[_\- ]?code|"
                    r"validate[_\- ]?code|token|api[_\- ]?key)([\"']?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s&;,\"'<>]+)")
_AUTH_HDR_RX = re.compile(r"(?i)\b((?:proxy-)?authorization\s*:\s*)[^\r\n]+")
_WWW_AUTH_RX = re.compile(r"(?i)(\b(?:username|response|cnonce)\s*=\s*)(\"[^\"]*\"|[^\s,]+)")
_XML_SECRET_RX = re.compile(r"(?is)(<(?:[\w\-]+:)?(Password|Nonce)\b[^>]*>).*?(</(?:[\w\-]+:)?\2\s*>)")


def redact(value, secrets=()):
    """Return ``value`` as a string with every credential masked."""
    if value is None:
        return None
    text = str(value)
    for s in secrets or ():
        if s and len(str(s)) >= 3:
            for form in {str(s), quote(str(s), safe=""), quote(str(s))}:
                text = text.replace(form, MASK)
    text = _USERINFO_RX.sub(lambda m: m.group(1) + MASK + "@", text)
    text = _AUTH_HDR_RX.sub(lambda m: m.group(1) + MASK, text)
    text = _XML_SECRET_RX.sub(lambda m: m.group(1) + MASK + m.group(3), text)
    text = _KV_RX.sub(lambda m: m.group(1) + m.group(2) + MASK, text)
    text = _WWW_AUTH_RX.sub(lambda m: m.group(1) + MASK, text)
    return text


def safe_error(exc, secrets=(), limit=300):
    """A short, redacted description of an exception for logs / health payloads."""
    return redact(f"{type(exc).__name__}: {exc}", secrets)[:limit]
