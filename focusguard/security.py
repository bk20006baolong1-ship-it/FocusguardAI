"""Security configuration and password handling."""
import logging
import os
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger(__name__)

# Secrets that were committed in earlier versions of this repository. They are
# public knowledge and must never be accepted in production.
KNOWN_INSECURE_SECRETS = {"focusguard_ai_secret_key", "changeme", "secret", "dev", ""}

_HASH_PREFIXES = ("scrypt:", "pbkdf2:", "argon2")


class InsecureConfigurationError(RuntimeError):
    pass


def environment() -> str:
    return (os.environ.get("FOCUSGUARD_ENV") or os.environ.get("FLASK_ENV") or "development").lower()


def is_production(env: str = None) -> bool:
    return (env or environment()) in ("production", "prod")


def env_value(env_map, name, default=""):
    """Read a setting, treating ``<placeholder>`` values from .env.example as unset."""
    raw = env_map.get(name, default)
    if raw is None:
        return default
    raw = str(raw).strip()
    if raw.startswith("<") and raw.endswith(">"):
        return default
    return raw


def parse_allowed_origins(raw):
    if raw is None:
        return []
    return [o.strip() for o in str(raw).split(",") if o.strip()]


def build_flask_config(env_map=None):
    """Return a dict of Flask settings derived from the environment.

    Production refuses to start with a missing/known secret key or a wildcard
    CORS origin. Development generates a random per-process secret instead of
    shipping a hard-coded one.
    """
    env_map = os.environ if env_map is None else env_map
    env = (env_map.get("FOCUSGUARD_ENV") or env_map.get("FLASK_ENV") or "development").lower()
    prod = env in ("production", "prod")

    secret = env_value(env_map, "FLASK_SECRET_KEY", "")
    if prod:
        if secret in KNOWN_INSECURE_SECRETS or len(secret) < 32:
            raise InsecureConfigurationError(
                "FLASK_SECRET_KEY must be set to a random value of at least 32 characters in production.")
    elif not secret:
        secret = secrets.token_hex(32)
        log.warning("FLASK_SECRET_KEY not set; using a random per-process key (sessions reset on restart).")

    origins = parse_allowed_origins(env_value(env_map, "ALLOWED_ORIGINS", ""))
    if prod and ("*" in origins):
        raise InsecureConfigurationError("ALLOWED_ORIGINS must not contain '*' in production.")

    return {
        "ENV_NAME": env,
        "IS_PRODUCTION": prod,
        "SECRET_KEY": secret,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": prod or env_map.get("SESSION_COOKIE_SECURE", "").lower() == "true",
        "PERMANENT_SESSION_LIFETIME": 8 * 3600,
        "MAX_CONTENT_LENGTH": 16 * 1024 * 1024,
        # None -> Flask-SocketIO same-origin policy; list -> explicit allow-list.
        "SOCKETIO_CORS_ORIGINS": origins or None,
        # Number of trusted reverse proxies in front of the app (0 = none). With a proxy, set it to 1 so the
        # CSRF same-origin check (and url_for) see the public host from X-Forwarded-Host / X-Forwarded-Proto.
        "TRUST_PROXY": max(0, int(env_value(env_map, "FG_TRUST_PROXY", "0") or 0)),
        "SEED_DEMO_ACCOUNTS": (not prod) and env_map.get("SEED_DEMO_ACCOUNTS", "true").lower() == "true",
    }


def hash_password(plain: str) -> str:
    if not plain:
        raise ValueError("password must not be empty")
    return generate_password_hash(plain)


def is_hashed(stored) -> bool:
    return isinstance(stored, str) and stored.startswith(_HASH_PREFIXES)


def verify_password(stored, provided):
    """Return ``(ok, needs_rehash)``.

    Legacy rows stored the plaintext password. Those still verify (constant
    time compare) but ``needs_rehash`` is True so the caller can migrate the
    row to a Werkzeug hash immediately after a successful login.
    """
    if stored is None or provided is None or provided == "":
        return False, False
    if is_hashed(stored):
        try:
            return check_password_hash(stored, provided), False
        except (ValueError, TypeError):
            return False, False
    ok = secrets.compare_digest(str(stored).encode("utf-8"), str(provided).encode("utf-8"))
    return ok, ok


SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def cross_site_write(method: str, host: str, origin: str = None, referer: str = None, trusted=()) -> bool:
    """CSRF guard (OWASP "verify origin"): True when a state-changing request was
    sent by a page of ANOTHER site. Browsers attach ``Origin`` (or at least
    ``Referer``) to cross-site POSTs; a request with neither comes from a
    non-browser client (CLI, tests) and cannot ride a user's cookie."""
    if (method or "").upper() in SAFE_METHODS:
        return False
    from urllib.parse import urlsplit
    source = origin if origin else referer
    if not source:
        return False
    if source == "null":
        return True        # sandboxed iframe / file: page
    netloc = urlsplit(source).netloc.lower()
    allowed = {(host or "").lower()} | {urlsplit(t).netloc.lower() or t.lower() for t in trusted or ()}
    return netloc not in allowed
