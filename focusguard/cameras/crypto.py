"""Camera credential encryption at rest.

Camera passwords / EZVIZ verification codes are encrypted with Fernet
(AES-128-CBC + HMAC-SHA256, ``cryptography`` package) before they reach the
repository, and decrypted only server-side, at connect time. The API never
returns them; it returns ``has_credentials: true/false``.

Key: ``FG_CAMERA_CREDENTIAL_KEY`` (a Fernet key, see README › Camera). It is a
dedicated key, separate from ``FLASK_SECRET_KEY``.

* production without the key: saving or using a credential fails safe
  (:class:`CredentialKeyMissing`), the rest of the app keeps working;
* development without the key: a random key is generated once into a local,
  git-ignored file (``FG_CAMERA_KEY_FILE``, default ``.focusguard/camera_credential.key``,
  mode 0600) so a fresh checkout works without setup;
* the stored value is ``fgc1:<key id>:<token>``; a different key yields
  :class:`CredentialKeyMismatch` ("re-enter the camera password"), never garbage.
"""
import hashlib
import os

from ..security import env_value

PREFIX = "fgc1"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_KEY_FILE = os.path.join(ROOT, ".focusguard", "camera_credential.key")


class CredentialError(RuntimeError):
    code = "CREDENTIAL_ERROR"


class CredentialKeyMissing(CredentialError):
    code = "CREDENTIAL_KEY_MISSING"


class CredentialKeyMismatch(CredentialError):
    code = "CREDENTIAL_KEY_MISMATCH"


def generate_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode("ascii")


def _key_id(key: bytes) -> str:
    return hashlib.sha256(b"focusguard-camera-key:" + key).hexdigest()[:12]


class CredentialVault:
    def __init__(self, key=None, source="none"):
        self._fernet = None
        self.key_id = None
        self.source = source
        if key:
            from cryptography.fernet import Fernet
            key = key.encode("ascii") if isinstance(key, str) else key
            try:
                self._fernet = Fernet(key)
            except (ValueError, TypeError):
                raise CredentialKeyMissing("FG_CAMERA_CREDENTIAL_KEY is not a valid Fernet key")
            self.key_id = _key_id(key)

    def __repr__(self):
        return f"<CredentialVault source={self.source} available={self.available}>"

    @property
    def available(self):
        return self._fernet is not None

    @classmethod
    def from_env(cls, env=None, is_production=None):
        env = os.environ if env is None else env
        if is_production is None:
            is_production = (env.get("FOCUSGUARD_ENV") or env.get("FLASK_ENV") or "").lower() in ("production", "prod")
        key = env_value(env, "FG_CAMERA_CREDENTIAL_KEY", "").strip()
        if key and not key.startswith("<"):
            return cls(key, source="env")
        if is_production:
            return cls(None, source="missing")
        path = env.get("FG_CAMERA_KEY_FILE") or DEFAULT_KEY_FILE
        return cls(_dev_key_from_file(path), source="dev-file")

    def encrypt(self, secret: str) -> str:
        if not self.available:
            raise CredentialKeyMissing("camera credential key not configured")
        token = self._fernet.encrypt(str(secret).encode("utf-8")).decode("ascii")
        return f"{PREFIX}:{self.key_id}:{token}"

    def decrypt(self, blob: str) -> str:
        if not blob:
            return None
        if not self.available:
            raise CredentialKeyMissing("camera credential key not configured")
        try:
            prefix, key_id, token = str(blob).split(":", 2)
        except ValueError:
            raise CredentialKeyMismatch("unrecognised credential format")
        if prefix != PREFIX or key_id != self.key_id:
            raise CredentialKeyMismatch("credential was encrypted with another key")
        from cryptography.fernet import InvalidToken
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            raise CredentialKeyMismatch("credential cannot be decrypted with the current key")


def _dev_key_from_file(path):
    """Read (or create once, 0600) the development-only key file."""
    try:
        with open(path, "r", encoding="ascii") as f:
            key = f.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    key = generate_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:            # another process created it first
        with open(path, "r", encoding="ascii") as f:
            return f.read().strip()
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(key + "\n")
    return key
