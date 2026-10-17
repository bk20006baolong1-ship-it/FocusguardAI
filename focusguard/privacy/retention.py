"""Scheduled data retention (FACE_EMBEDDING / EVENT_METADATA / EVENT_EVIDENCE /
EVALUATION_DATA retention settings).

``RetentionService.run(now)`` is idempotent. The web server runs it every
``RETENTION_SWEEP_MINUTES``; ``python scripts/privacy_retention.py`` runs it
from cron. Audit records themselves are kept (they contain no biometric data).
"""
import os
import shutil
import time

from . import audit as A
from .consent import ACTIVE, state_of


class RetentionService:
    def __init__(self, repo, settings, audit: A.AuditLog, evidence=None, consent=None, clock=time.time,
                 on_face_changed=None):
        self.repo = repo
        self.settings = settings
        self.audit = audit
        self.evidence = evidence
        self.consent = consent
        self.clock = clock
        self.on_face_changed = on_face_changed

    def run(self, now=None, actor=None) -> dict:
        now = self.clock() if now is None else now
        s = self.settings
        report = {"ran_at": now, "evidence_deleted": 0, "faces_deleted": 0, "events_deleted": 0,
                  "snapshots_deleted": 0, "reviews_deleted": 0, "evaluation_sessions_deleted": 0,
                  "context_segments_deleted": 0}
        if self.evidence is not None:
            report["evidence_deleted"] = self.evidence.cleanup_expired(now)

        face_cutoff = now - s.face_embedding_retention_days * 86400
        changed = set()
        for row in self.repo.get_students_with_face():
            sid = row["student_id"]
            reason = None
            if row.get("enrolled_at") is not None and row["enrolled_at"] < face_cutoff and not any(
                    (t.get("created_at") or 0) >= face_cutoff for t in self.repo.list_face_templates(sid)):
                # nothing newer than the retention period (newer templates keep the profile alive;
                # the old templates themselves are removed by the per-template rule below)
                reason = "retention_expired"
            else:
                rec = self.consent.latest(sid) if self.consent is not None else None
                st = state_of(rec, now)
                # Legacy embeddings without any consent record are excluded from
                # recognition (gallery filter) and flagged in the privacy page for
                # the admin to delete; REVOKED/EXPIRED consent, or an ACTIVE consent
                # without the face scope, deletes them automatically.
                if rec is not None and st != ACTIVE:
                    reason = "consent_" + st.lower()
                elif rec is not None and not rec.get("scope_face_recognition"):
                    reason = "consent_scope_removed"
            if reason:
                self.repo.save_face_embedding(sid, None)
                self.audit.record(A.FACE_DELETED, None, sid, {"reason": reason})
                report["faces_deleted"] += 1
                changed.add(sid)

        # Multi-template profiles follow the same face rule: a template older than the face
        # retention period is removed even when the student still has newer ones.
        old_templates = self.repo.delete_rows_before("face_profiles", "created_at", face_cutoff)
        report["face_templates_deleted"] = old_templates
        if old_templates and self.on_face_changed:
            self.on_face_changed(None)                 # galleries of running classes are reloaded

        ev_cutoff = now - s.event_metadata_retention_days * 86400
        report["events_deleted"] = self.repo.delete_rows_before("focus_events", "start_time", ev_cutoff)
        report["snapshots_deleted"] = self.repo.delete_rows_before("focus_snapshots", "timestamp", ev_cutoff)
        report["reviews_deleted"] = self.repo.delete_rows_before("event_reviews", "reviewed_at", ev_cutoff)
        # a review is written after its event, so it can outlive it: remove orphans too
        for r in self.repo.get_event_reviews():
            if self.repo.get_focus_event(r["event_id"]) is None:
                report["reviews_deleted"] += self.repo.delete_event_reviews(event_id=r["event_id"])
        report["context_segments_deleted"] = self.repo.delete_class_context_segments_before(ev_cutoff)
        # Session identity history (track -> student, who confirmed) follows the event metadata rule.
        report["identity_bindings_deleted"] = self.repo.delete_rows_before("identity_bindings", "confirmed_at",
                                                                           ev_cutoff)
        # The attendance evidence ledger (which sighting supported a check-in) follows the same rule.
        report["attendance_evidence_deleted"] = self.repo.delete_rows_before("attendance_evidence", "confirmed_at",
                                                                             ev_cutoff)

        eval_cutoff = now - s.evaluation_data_retention_days * 86400
        for es in self.repo.list_evaluation_sessions():
            if (es.get("created_at") or now) < eval_cutoff:
                self.repo.delete_evaluation_session(es["evaluation_session_id"])
                private = os.path.join(s.evaluation_private_dir, es["evaluation_session_id"])
                if os.path.isdir(private):
                    shutil.rmtree(private, ignore_errors=True)
                report["evaluation_sessions_deleted"] += 1

        if any(v for k, v in report.items() if k != "ran_at"):
            self.audit.record(A.RETENTION_RUN, actor, None, {k: v for k, v in report.items() if k != "ran_at"})
        if self.on_face_changed:
            for sid in changed:
                self.on_face_changed(sid)
        return report
