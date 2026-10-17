"""Consent, revoke, deletion, retention, audit and export (BTC feedback E)."""
import json
import sys
import types

import numpy as np
import pytest

from conftest import grant_consent, run_class_with_events
from focusguard.authz import Principal
from focusguard.privacy.audit import sanitize
from focusguard.privacy.consent import ACTIVE, EXPIRED, NOT_PROVIDED, REVOKED, ConsentError

ADMIN = Principal(1, "admin")
IMG = "data:image/jpeg;base64,AAAA"


@pytest.fixture
def fake_vision(monkeypatch):
    """Face model + OpenCV stand-ins so enrolment runs without hardware."""
    cv2 = types.SimpleNamespace(IMREAD_COLOR=1, imdecode=lambda arr, flag: np.zeros((8, 8, 3), np.uint8))
    fr = types.ModuleType("face_recognition")

    class FakeRecognizer:
        def register_student(self, imgs):
            return [0.1] * 512

        def extract_embedding(self, img):          # one embedding per photo (multi-template enrollment)
            return [0.1] * 512
    fr.FaceRecognizer = FakeRecognizer
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    monkeypatch.setitem(sys.modules, "face_recognition", fr)


def register(client, user_id=3, **extra):
    return client.post("/api/admin/register_face", json=dict({"user_id": user_id, "images": [IMG]}, **extra))


# ------------------------------------------------------------------ states
def test_consent_states(manager, clock):
    assert manager.consent.status(3)["state"] == NOT_PROVIDED
    st = grant_consent(manager, 3, face_recognition=True)
    assert st["state"] == ACTIVE and st["scopes"]["face_recognition"] and not st["scopes"]["event_evidence"]
    clock.advance(manager.privacy.consent_validity_days * 86400 + 1)
    assert manager.consent.status(3)["state"] == EXPIRED
    assert manager.consent.has_scope(3, "face_recognition") is False
    grant_consent(manager, 3, face_recognition=True)
    manager.consent.revoke(ADMIN, 3)
    assert manager.consent.status(3)["state"] == REVOKED


def test_guardian_is_required_for_minors_and_scopes_required(manager):
    with pytest.raises(ConsentError):
        manager.consent.grant(ADMIN, 3, scopes={"face_recognition": True})
    with pytest.raises(ConsentError):
        manager.consent.grant(ADMIN, 3, guardian_name="Mẹ An", guardian_relationship="Mẹ", scopes={})
    with pytest.raises(ConsentError):
        manager.consent.grant(ADMIN, 2, guardian_name="x", guardian_relationship="y", scopes={"evaluation": True})
    st = manager.consent.grant(ADMIN, 3, student_is_adult=True, scopes={"evaluation": True})
    assert st["state"] == ACTIVE and st["record"]["student_is_adult"] is True


# -------------------------------------------------------------- enrolment
def test_enrollment_blocked_without_consent(login_as, repo, fake_vision):
    admin = login_as("admin")
    resp = register(admin)
    assert resp.status_code == 403 and "đồng ý" in resp.get_json()["message"]
    assert repo.get_face_enrollment(3)["has_face"] is False


def test_client_consent_flag_cannot_bypass_server(login_as, repo, fake_vision):
    resp = register(login_as("admin"), consent=True, consent_verified=True, scope_face_recognition=True)
    assert resp.status_code == 403 and repo.get_face_enrollment(3)["has_face"] is False


def test_enrollment_allowed_with_active_consent(login_as, repo, manager, fake_vision):
    grant_consent(manager, 3, face_recognition=True)
    resp = register(login_as("admin"))
    assert resp.status_code == 200, resp.get_json()
    enr = repo.get_face_enrollment(3)
    assert enr["has_face"] and enr["enrolled_at"]
    assert [r["action"] for r in manager.audit.list(student_id=3, action="FACE_ENROLLED")] == ["FACE_ENROLLED"]


def test_consent_without_face_scope_still_blocks(login_as, manager, fake_vision):
    grant_consent(manager, 3, evaluation=True)
    assert register(login_as("admin")).status_code == 403


def test_revoked_consent_blocks_enrollment_and_recognition(login_as, repo, manager, fake_vision):
    grant_consent(manager, 3, face_recognition=True)
    repo.save_face_embedding(4, json.dumps([0.2] * 8))
    repo.save_face_embedding(3, json.dumps([0.1] * 8))
    grant_consent(manager, 4, face_recognition=True)
    assert {r["student_id"] for r in manager.recognition_gallery(1)} == {3, 4}
    res = manager.consent.revoke(ADMIN, 3, reason="phụ huynh yêu cầu")
    assert res["face_deleted"] is True
    assert {r["student_id"] for r in manager.recognition_gallery(1)} == {4}
    assert register(login_as("admin")).status_code == 403


def test_gallery_excludes_legacy_embeddings_without_consent(repo, manager):
    repo.save_face_embedding(5, json.dumps([0.3] * 8))         # enrolled before consent existed
    assert manager.recognition_gallery(1) == []
    assert repo.get_face_enrollment(5)["has_face"]            # not silently deleted; flagged on the privacy page


def test_revoke_deletes_embedding_and_evidence_and_is_audited(manager, repo, tmp_path):
    grant_consent(manager, 3, face_recognition=True, event_evidence=True)
    repo.save_face_embedding(3, json.dumps([0.1] * 8))
    repo.create_evidence({"evidence_id": "a" * 32, "student_id": 3, "class_id": 1, "file_name": None,
                          "created_at": 1.0, "expires_at": 9e18})
    res = manager.consent.revoke(ADMIN, 3)
    assert res["face_deleted"] and res["evidence_deleted"] == 1
    assert repo.get_face_enrollment(3)["has_face"] is False
    actions = [r["action"] for r in manager.audit.list(student_id=3)]
    assert {"CONSENT_REVOKED", "FACE_DELETED", "EVIDENCE_DELETED"} <= set(actions)


def test_revoke_keeps_academic_data(manager, clock):
    grant_consent(manager, 3, face_recognition=True)
    _, events = run_class_with_events(manager, clock)
    manager.consent.revoke(ADMIN, 3)
    assert len(manager.repo.get_focus_events(student_id=3)) == len(events) > 0
    assert manager.repo.get_session_students(student_id=3)


# ------------------------------------------------------------- endpoints
def test_admin_records_consent_via_api_and_creator_is_the_session_user(login_as, manager):
    resp = login_as("admin").post("/api/admin/consent/3", json={
        "guardian_name": "Nguyễn Thị Hoa", "guardian_relationship": "Mẹ", "created_by": 999,
        "scopes": {"face_recognition": True, "event_evidence": False}})
    assert resp.status_code == 200
    rec = manager.consent.latest(3)
    assert rec["created_by"] == 1 and rec["policy_version"]


def test_only_admin_can_grant_consent(login_as):
    body = {"guardian_name": "x", "guardian_relationship": "Mẹ", "scopes": {"face_recognition": True}}
    for user in ("teacher", "hocsinh", "phuhuynh"):
        assert login_as(user).post("/api/admin/consent/3", json=body).status_code == 403, user


def test_parent_can_revoke_only_own_child(login_as, manager, repo):
    grant_consent(manager, 3, face_recognition=True)
    grant_consent(manager, 4, face_recognition=True)
    parent = login_as("phuhuynh")
    assert parent.post("/api/consent/4/revoke", json={}).status_code == 403
    assert parent.post("/api/consent/3/revoke", json={}).status_code == 200
    assert manager.consent.status(3)["state"] == REVOKED and manager.consent.status(4)["state"] == ACTIVE
    for user in ("teacher", "hocsinh"):
        assert login_as(user).post("/api/consent/4/revoke", json={}).status_code == 403


def test_privacy_dashboard_is_admin_only_and_never_returns_vectors(login_as, repo, manager):
    repo.save_face_embedding(3, json.dumps([0.123456] * 512))
    resp = login_as("admin").get("/api/admin/privacy/students")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "0.123456" not in text and "face_embedding\"" not in text.replace("face_embedding_exists", "")
    row = next(r for r in resp.get_json()["students"] if r["student_id"] == 3)
    assert row["face_embedding_exists"] and row["face_without_consent"] and row["last_enrollment"]
    for user in ("teacher", "hocsinh", "phuhuynh"):
        assert login_as(user).get("/api/admin/privacy/students").status_code == 403


def test_admin_delete_face_and_evidence_endpoints(login_as, repo, manager):
    repo.save_face_embedding(3, json.dumps([0.1] * 8))
    admin = login_as("admin")
    assert admin.post("/api/admin/privacy/3/delete_face").get_json()["deleted"] is True
    assert repo.get_face_enrollment(3)["has_face"] is False
    assert admin.post("/api/admin/privacy/3/delete_evidence").get_json()["deleted"] == 0
    assert login_as("teacher").post("/api/admin/privacy/3/delete_face").status_code == 403


def test_policy_text_has_one_source_and_states_the_new_video_claim(login_as, manager):
    pol = login_as("hocsinh").get("/api/privacy/policy").get_json()
    assert "Mặc định FocusGuard không lưu video" in pol["evidence"]
    assert [r["key"] for r in pol["retention"]] == ["FACE_EMBEDDING_RETENTION_DAYS", "EVENT_METADATA_RETENTION_DAYS",
                                                     "EVENT_EVIDENCE_RETENTION_HOURS", "EVALUATION_DATA_RETENTION_DAYS"]


# -------------------------------------------------------------- retention
def test_retention_deletes_old_embeddings_events_and_evaluations(manager, clock, repo):
    grant_consent(manager, 3, face_recognition=True)
    repo.save_face_embedding(3, json.dumps([0.1] * 8))
    _, events = run_class_with_events(manager, clock)
    assert events
    es = repo.create_evaluation_session({"class_id": 1, "status": "ENDED", "created_at": clock.t})
    repo.create_annotation({"evaluation_session_id": es, "student_id": 3, "label": "FOCUSED",
                            "started_at": 1.0, "ended_at": 2.0})
    # face embedding was enrolled with the real clock: make it look old
    repo._execute("UPDATE users SET face_enrolled_at = ? WHERE id = 3", (clock.t - 400 * 86400,))
    report = manager.retention.run(now=clock.t + 400 * 86400)
    assert report["faces_deleted"] == 1 and report["events_deleted"] == len(events)
    assert report["evaluation_sessions_deleted"] == 1
    assert repo.get_focus_events(student_id=3) == [] and repo.get_annotations(evaluation_session_id=es) == []
    assert manager.audit.list(action="RETENTION_RUN")


def test_retention_keeps_recent_data(manager, clock, repo):
    _, events = run_class_with_events(manager, clock)
    report = manager.retention.run(now=clock.t + 3600)
    assert report["events_deleted"] == 0 and len(repo.get_focus_events(class_id=1)) == len(events)


def test_retention_deletes_embedding_when_consent_expired(manager, clock, repo):
    grant_consent(manager, 3, face_recognition=True)
    repo.save_face_embedding(3, json.dumps([0.1] * 8))
    manager.retention.run(now=clock.t + 10)
    assert repo.get_face_enrollment(3)["has_face"]
    report = manager.retention.run(now=clock.t + manager.privacy.consent_validity_days * 86400 + 5)
    assert report["faces_deleted"] == 1 and not repo.get_face_enrollment(3)["has_face"]


def test_admin_can_run_retention_from_the_dashboard(login_as):
    assert login_as("admin").post("/api/admin/retention/run", json={}).get_json()["status"] == "success"
    assert login_as("teacher").post("/api/admin/retention/run", json={}).status_code == 403


# ----------------------------------------------------------------- audit
def test_audit_never_stores_biometric_vectors_or_secrets(manager, repo):
    manager.audit.record("FACE_ENROLLED", ADMIN, 3, {
        "embedding": [0.1] * 512, "face_vector": [1, 2], "password": "pw", "api_token": "t", "frames": [b"x"],
        "nested": {"image": "data:...", "ok": 1}, "numbers": list(range(100)), "note": "x" * 2000})
    row = repo.get_audit(student_id=3)[0]
    blob = json.dumps(row["metadata"])
    for banned in ("embedding", "face_vector", "password", "api_token", "frames", "image", "0.1, 0.1"):
        assert banned not in blob
    assert row["metadata"]["nested"] == {"ok": 1} and row["metadata"]["numbers"].startswith("<100 numbers")
    assert len(row["metadata"]["note"]) == 500
    assert sanitize({"x": b"bytes"}) == {"x": "<binary removed>"}


def test_audit_log_is_admin_only(login_as):
    assert login_as("admin").get("/api/admin/audit").status_code == 200
    for user in ("teacher", "hocsinh", "phuhuynh"):
        assert login_as(user).get("/api/admin/audit").status_code == 403


# ----------------------------------------------------------------- export
def test_export_contains_events_reviews_consent_annotations_but_no_embedding(login_as, manager, clock, repo):
    grant_consent(manager, 3, face_recognition=True, evaluation=True)
    repo.save_face_embedding(3, json.dumps([0.987654] * 512))
    _, events = run_class_with_events(manager, clock)
    t = login_as("teacher")
    t.post(f"/api/teacher/events/{events[0]['id']}/review", json={"verdict": "CONFIRMED", "note": "ghi chú GV"})
    resp = login_as("admin").get("/api/export/student/3?format=json")
    assert resp.status_code == 200 and resp.headers["Cache-Control"].startswith("no-store")
    data = json.loads(resp.get_data(as_text=True))
    assert len(data["events"]) == len(events) and data["events"][0]["review_verdict"] == "CONFIRMED"
    assert data["reviews"][0]["teacher_note"] == "ghi chú GV" and data["consents"]
    assert data["face_embedding_exists"] is True and "0.987654" not in resp.get_data(as_text=True)
    assert "face_embedding" in data["excluded"]
    csv_text = login_as("teacher").get("/api/export/student/3?format=csv&section=events").get_data(as_text=True)
    assert csv_text.lstrip("﻿").startswith("event_id,type")
    assert manager.audit.list(action="DATA_EXPORTED")


def test_export_authorisation(login_as, repo):
    from conftest import add_teacher
    add_teacher(repo)
    assert login_as("hocsinh").get("/api/export/student/3").status_code == 403
    assert login_as("teacher2").get("/api/export/student/3").status_code == 403
    assert login_as("phuhuynh").get("/api/export/student/4").status_code == 403
    parent = login_as("phuhuynh").get("/api/export/student/3")
    assert parent.status_code == 200 and all("teacher_note" not in r for r in json.loads(parent.get_data())["reviews"])


def test_generic_user_api_never_returns_embeddings(login_as, repo):
    repo.save_face_embedding(3, json.dumps([0.55555] * 512))
    text = login_as("admin").get("/api/admin/users").get_data(as_text=True)
    assert "0.55555" not in text and '"face_embedding"' not in text
