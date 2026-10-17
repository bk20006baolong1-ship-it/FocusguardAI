"""Consent domain model (student / guardian consent for biometric processing).

States (computed, never stored by the client):

    NOT_PROVIDED  no consent record for the student
    ACTIVE        latest record accepted, not revoked, not expired
    EXPIRED       latest record past ``expires_at``
    REVOKED       latest record revoked

Face enrolment, face recognition (gallery loading) and evidence clips are
only allowed while the state is ACTIVE *and* the matching scope is granted.
The server enforces this; a frontend flag can never bypass it.
"""
import time
from typing import Callable, Optional

from . import audit as A
from .policy import POLICY_VERSION
from .settings import PrivacySettings

NOT_PROVIDED = "NOT_PROVIDED"
ACTIVE = "ACTIVE"
EXPIRED = "EXPIRED"
REVOKED = "REVOKED"
STATES = (NOT_PROVIDED, ACTIVE, EXPIRED, REVOKED)
STATE_LABELS_VI = {NOT_PROVIDED: "Chưa có đồng ý", ACTIVE: "Đang có hiệu lực", EXPIRED: "Đã hết hạn",
                   REVOKED: "Đã thu hồi"}

SCOPE_FACE = "face_recognition"
SCOPE_EVIDENCE = "event_evidence"
SCOPE_EVALUATION = "evaluation"
SCOPES = (SCOPE_FACE, SCOPE_EVIDENCE, SCOPE_EVALUATION)

MAX_TEXT = 80
MAX_CONSENT_DAYS = 3650             # an explicit expiry date may not exceed 10 years


class ConsentError(ValueError):
    pass


def state_of(record: Optional[dict], now: float) -> str:
    if not record:
        return NOT_PROVIDED
    if record.get("revoked_at"):
        return REVOKED
    exp = record.get("expires_at")
    if exp is not None and now >= float(exp):
        return EXPIRED
    return ACTIVE


class ConsentService:
    def __init__(self, repo, audit: A.AuditLog, settings: PrivacySettings, clock: Callable[[], float] = time.time,
                 evidence=None, on_face_changed: Optional[Callable[[int], None]] = None):
        self.repo = repo
        self.audit = audit
        self.settings = settings
        self.clock = clock
        self.evidence = evidence                # EvidenceService, wired by SessionManager
        self.on_face_changed = on_face_changed

    # --------------------------------------------------------------- reads
    def latest(self, student_id) -> Optional[dict]:
        rows = self.repo.get_consents(student_id=student_id)
        return rows[-1] if rows else None

    def status(self, student_id, now=None) -> dict:
        now = self.clock() if now is None else now
        rec = self.latest(student_id)
        st = state_of(rec, now)
        scopes = {s: bool(rec and rec.get("scope_" + s)) for s in SCOPES}
        return {"student_id": int(student_id), "state": st, "state_label": STATE_LABELS_VI[st],
                "scopes": scopes if st == ACTIVE else {s: False for s in SCOPES},
                "record": rec, "policy_version": POLICY_VERSION,
                "policy_outdated": bool(rec and rec.get("policy_version") != POLICY_VERSION)}

    def has_scope(self, student_id, scope, now=None) -> bool:
        if scope not in SCOPES:
            raise ValueError(scope)
        return bool(self.status(student_id, now)["scopes"].get(scope))

    def history(self, student_id):
        return self.repo.get_consents(student_id=student_id)

    def record_policy_change(self) -> bool:
        """Audit POLICY_CHANGED once when the consent policy version differs from the
        version existing consents were given under (call at start-up)."""
        older = {c.get("policy_version") for c in self.repo.get_consents()} - {POLICY_VERSION, None}
        if not older:
            return False
        for a in self.repo.get_audit(action=A.POLICY_CHANGED, limit=10 ** 9):
            if (a.get("metadata") or {}).get("consent_policy_version") == POLICY_VERSION:
                return False
        self.audit.record(A.POLICY_CHANGED, None, None, {"consent_policy_version": POLICY_VERSION,
                                                         "previous_versions": sorted(older)})
        return True

    # -------------------------------------------------------------- writes
    def grant(self, actor, student_id, *, guardian_name=None, guardian_relationship=None, scopes=None,
              student_is_adult=False, expires_at=None, note=None) -> dict:
        """Record a consent given by the guardian (or an adult student).

        ``actor`` is the authenticated Principal recording it (admin)."""
        user = self.repo.get_user_by_id(student_id)
        if not user or user.get("role") != "student":
            raise ConsentError("Không tìm thấy học sinh.")
        scopes = scopes or {}
        granted = {s: bool(scopes.get(s)) for s in SCOPES}
        if not any(granted.values()):
            raise ConsentError("Cần chọn ít nhất một phạm vi đồng ý.")
        guardian_name = (guardian_name or "").strip()[:MAX_TEXT]
        guardian_relationship = (guardian_relationship or "").strip()[:MAX_TEXT]
        if not student_is_adult and (not guardian_name or not guardian_relationship):
            # Lower-secondary students are minors: the guardian gives consent.
            raise ConsentError("Học sinh chưa đủ tuổi: cần họ tên và quan hệ của phụ huynh/người giám hộ.")
        now = self.clock()
        if expires_at is None:
            expires_at = now + self.settings.consent_validity_days * 86400
        else:
            try:
                expires_at = float(expires_at)
            except (TypeError, ValueError):
                raise ConsentError("Ngày hết hạn không hợp lệ.")
            if expires_at <= now:
                raise ConsentError("Ngày hết hạn phải ở tương lai.")
            if expires_at > now + MAX_CONSENT_DAYS * 86400:
                raise ConsentError(f"Thời hạn đồng ý tối đa {MAX_CONSENT_DAYS // 365} năm; cần gia hạn sau đó.")
        row = {"student_id": int(student_id), "guardian_name": guardian_name or None,
               "guardian_relationship": guardian_relationship or None, "student_is_adult": bool(student_is_adult),
               "scope_face_recognition": granted[SCOPE_FACE], "scope_event_evidence": granted[SCOPE_EVIDENCE],
               "scope_evaluation": granted[SCOPE_EVALUATION], "policy_version": POLICY_VERSION,
               "accepted_at": now, "expires_at": expires_at, "revoked_at": None,
               "created_by": getattr(actor, "user_id", None), "created_at": now,
               "evidence_note": (note or "").strip()[:200] or None}
        cid = self.repo.create_consent(row)
        self.audit.record(A.CONSENT_GRANTED, actor, student_id,
                          {"consent_id": cid, "scopes": granted, "policy_version": POLICY_VERSION,
                           "guardian_relationship": guardian_relationship or None, "expires_at": expires_at})
        # A new record supersedes the old one: data of a scope no longer granted is removed now.
        if not granted[SCOPE_FACE] and self.repo.get_face_enrollment(student_id)["has_face"]:
            self.repo.save_face_embedding(int(student_id), None)
            self.audit.record(A.FACE_DELETED, actor, student_id, {"reason": "consent_scope_removed"})
        if not granted[SCOPE_EVIDENCE] and self.evidence is not None:
            self.evidence.cancel_pending(student_id)
            if self.settings.revoke_deletes_evidence:
                self.evidence.delete_for_student(student_id, actor, reason="consent_scope_removed")
        if self.on_face_changed:
            self.on_face_changed(int(student_id))
        return self.status(student_id)

    def revoke(self, actor, student_id, reason=None) -> dict:
        """Revoke: stop recognition, delete the face embedding, delete clips (policy)."""
        now = self.clock()
        rec = self.latest(student_id)
        if state_of(rec, now) == NOT_PROVIDED:
            raise ConsentError("Học sinh chưa có bản ghi đồng ý.")
        if not rec.get("revoked_at"):
            self.repo.update_consent(rec["consent_id"], {"revoked_at": now, "revoked_by": getattr(actor, "user_id", None),
                                                        "revoke_reason": (reason or "").strip()[:200] or None})
        result = {"consent_id": rec["consent_id"], "face_deleted": False, "evidence_deleted": 0}
        if self.evidence is not None:
            self.evidence.cancel_pending(student_id)        # no clip is written after a revoke
        if self.repo.get_face_enrollment(student_id)["has_face"]:
            self.repo.save_face_embedding(int(student_id), None)
            result["face_deleted"] = True
            self.audit.record(A.FACE_DELETED, actor, student_id, {"reason": "consent_revoked"})
        if self.settings.revoke_deletes_evidence and self.evidence is not None:
            result["evidence_deleted"] = self.evidence.delete_for_student(student_id, actor, reason="consent_revoked")
        self.audit.record(A.CONSENT_REVOKED, actor, student_id,
                          {"consent_id": rec["consent_id"], "face_deleted": result["face_deleted"],
                           "evidence_deleted": result["evidence_deleted"]})
        if self.on_face_changed:
            self.on_face_changed(int(student_id))
        result["status"] = self.status(student_id)
        return result
