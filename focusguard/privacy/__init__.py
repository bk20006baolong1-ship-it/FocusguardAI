"""Consent, audit, evidence clips, retention and export (BTC feedback E/D).

Everything here is enforced on the server. The browser can display consent
state and ask for actions, but it can never grant consent, bypass it, choose
who the reviewer is, or read a clip without an authorised server request.
"""
from .settings import PrivacySettings  # noqa: F401
from .policy import POLICY_VERSION, policy_texts, privacy_notice  # noqa: F401
