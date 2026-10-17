"""Privacy / security hardening found by the final BTC audit (evidence content,
consent changes, retention, CSRF, CSV injection, exports, account deletion)."""
import json
import os

import pytest

from conftest import grant_consent, login, run_class_with_events
from focusguard.authz import Principal
from focusguard.behavior import Observation
from focusguard.privacy import audit as A
from focusguard.privacy.consent import ConsentError
from focusguard.privacy.evidence import EvidenceStore
from focusguard.security import cross_site_write
from test_evidence import JPEG, build, phone_episode

ADMIN = Principal(1, "admin")


# ------------------------------------------------------------ evidence content
def test_full_classroom_frame_is_never_stored(tmp_path, clock):
    """Frames without this student's box (or with no cropper) are dropped: no clip
    shows the whole class."""
    sm, _ = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    rt = sm.start_class(1, mode="classroom")
    for i in range(60):
        t = clock.advance(0.2)
        rt.process_frame(t, {3: Observation(timestamp=t, visible=True, face_visible=True, ear=0.3, yaw=0.0,
                                            pitch=0.0, phone_confidence=0.9 if 10 <= i < 40 else None)})
        sm.evidence.push_frame(rt.class_session_id, t, JPEG, {4: (0, 0, 10, 10)})   # only another student's box
    sm.end_class(1)
    assert sm.repo.list_evidence(student_id=3) == []
    assert not (tmp_path / "evidence").exists() or os.listdir(tmp_path / "evidence") == []


def test_no_clip_without_a_cropper(tmp_path, clock):
    sm, _ = build(tmp_path, clock)
    sm.evidence.cropper = None
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    phone_episode(sm, clock)
    assert sm.repo.list_evidence(student_id=3) == []


def test_every_stored_frame_is_cropped_to_the_student(tmp_path, clock):
    sm, _ = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    phone_episode(sm, clock)
    clip = sm.repo.list_evidence(student_id=3)[0]
    frames = sm.evidence.store.read(clip["file_name"])["frames"]
    assert clip["cropped"] is True and frames and all(f.startswith(b"crop(0, 0, 10, 10)") for _, f in frames)


def test_classroom_pipeline_feeds_evidence_with_the_unannotated_frame():
    src = open(os.path.join(os.path.dirname(__file__), "..", "classroom_ai.py"), encoding="utf-8").read()
    assert "evidence.push_frame(runtime.class_session_id, now, clean_jpeg, boxes)" in src
    # encoded before names/scores are drawn in the visualisation block
    assert src.index("clean_jpeg = ") < src.index("# ---- visualisation only")


def test_revoke_during_a_pending_capture_writes_no_clip(tmp_path, clock):
    sm, _ = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    rt = sm.start_class(1, mode="classroom")
    for i in range(60):
        t = clock.advance(0.2)
        if i == 30:
            sm.consent.revoke(ADMIN, 3)        # phone episode already started, post window still open
        rt.process_frame(t, {3: Observation(timestamp=t, visible=True, face_visible=True, ear=0.3, yaw=0.0,
                                            pitch=0.0, phone_confidence=0.9 if 10 <= i < 40 else None)})
        sm.evidence.push_frame(rt.class_session_id, t, JPEG, {3: (0, 0, 10, 10)})
    sm.end_class(1)
    assert sm.repo.list_evidence(student_id=3, include_deleted=True) == []


def test_consent_rechecked_when_clip_is_saved(tmp_path, clock):
    sm, _ = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    pending = {"student_id": 3, "type": "PHONE", "episode_uid": "x", "class_id": 1, "class_session_id": 1}
    frames = [(1.0, JPEG, {3: (0, 0, 5, 5)})]
    sm.consent.revoke(ADMIN, 3)
    assert sm.evidence._save_clip(pending, frames) is None


# ------------------------------------------------------ unreadable clip files
@pytest.fixture
def clip_setup(tmp_path, clock):
    sm, app = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    phone_episode(sm, clock)
    return sm, app, sm.repo.list_evidence(student_id=3)[0], tmp_path / "evidence"


def _assert_gone(sm, app, clip, reason="file_unreadable"):
    t = login(app, "teacher")
    resp = t.get(f"/api/teacher/evidence/{clip['evidence_id']}")
    assert resp.status_code == 410 and "đã được gỡ" in resp.get_json()["message"]
    row = sm.repo.get_evidence(clip["evidence_id"])
    assert row["deleted_at"] and row["delete_reason"] == reason
    assert any(a["metadata"].get("evidence_id") == clip["evidence_id"]
               for a in sm.repo.get_audit(action=A.EVIDENCE_DELETED))
    assert t.get(f"/api/teacher/evidence/{clip['evidence_id']}").status_code == 404


def test_missing_clip_file_is_410_and_row_removed(clip_setup):
    sm, app, clip, folder = clip_setup
    os.remove(folder / clip["file_name"])
    _assert_gone(sm, app, clip)


def test_truncated_clip_file_is_410(clip_setup):
    sm, app, clip, folder = clip_setup
    path = folder / clip["file_name"]
    data = path.read_bytes()
    path.write_bytes(data[:-3])
    _assert_gone(sm, app, clip)


def test_corrupted_clip_header_is_410(clip_setup):
    sm, app, clip, folder = clip_setup
    path = folder / clip["file_name"]
    data = bytearray(path.read_bytes())
    data[10:14] = b"\xff\xff\xff\xff"
    path.write_bytes(bytes(data))
    _assert_gone(sm, app, clip)


def test_strict_reader_rejects_foreign_files(tmp_path):
    store = EvidenceStore(str(tmp_path))
    name = "a" * 32 + ".fgev"
    (tmp_path / name).write_bytes(b"not a clip at all")
    with pytest.raises(Exception) as exc:
        store.read(name)
    assert "not an evidence clip" in str(exc.value)
    with pytest.raises(Exception):
        store.read(None)


def test_expired_clip_can_still_be_deleted_by_its_teacher(clip_setup, clock):
    sm, app, clip, folder = clip_setup
    clock.advance(25 * 3600)
    t = login(app, "teacher")
    assert t.get(f"/api/teacher/evidence/{clip['evidence_id']}").status_code == 404     # never served
    assert t.post(f"/api/evidence/{clip['evidence_id']}/delete").status_code == 200
    assert os.listdir(folder) == []


# ------------------------------------------------------------ consent changes
def test_new_consent_without_face_scope_deletes_embedding(manager, repo):
    grant_consent(manager, 3, face_recognition=True)
    repo.save_face_embedding(3, json.dumps([0.1] * 8))
    manager.consent.grant(ADMIN, 3, guardian_name="Mẹ", guardian_relationship="Mẹ", scopes={"evaluation": True})
    assert repo.get_face_enrollment(3)["has_face"] is False
    entry = repo.get_audit(student_id=3, action=A.FACE_DELETED)[0]
    assert entry["metadata"] == {"reason": "consent_scope_removed"}


def test_new_consent_without_evidence_scope_deletes_clips(tmp_path, clock):
    sm, _ = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    phone_episode(sm, clock)
    assert sm.repo.list_evidence(student_id=3)
    sm.consent.grant(ADMIN, 3, guardian_name="Mẹ", guardian_relationship="Mẹ", scopes={"face_recognition": True})
    assert sm.repo.list_evidence(student_id=3) == [] and os.listdir(tmp_path / "evidence") == []


def test_explicit_consent_expiry_is_capped(manager, clock):
    with pytest.raises(ConsentError):
        manager.consent.grant(ADMIN, 3, guardian_name="Mẹ", guardian_relationship="Mẹ",
                              scopes={"face_recognition": True}, expires_at=clock.t + 20 * 365 * 86400)


def test_retention_deletes_embedding_when_active_consent_lacks_face_scope(manager, repo, clock):
    repo.create_consent({"student_id": 4, "guardian_name": "Bố", "guardian_relationship": "Bố",
                         "scope_face_recognition": False, "scope_evaluation": True, "policy_version": "x",
                         "accepted_at": clock.t, "expires_at": clock.t + 86400})
    repo.save_face_embedding(4, json.dumps([0.2] * 8))
    report = manager.retention.run(now=clock.t + 10)
    assert report["faces_deleted"] == 1 and repo.get_face_enrollment(4)["has_face"] is False


def test_policy_version_change_is_audited_once(manager, repo, clock):
    repo.create_consent({"student_id": 3, "guardian_name": "Mẹ", "guardian_relationship": "Mẹ",
                         "scope_face_recognition": True, "policy_version": "privacy-2025-01",
                         "accepted_at": clock.t, "expires_at": clock.t + 86400})
    assert manager.consent.record_policy_change() is True
    assert manager.consent.record_policy_change() is False
    entries = repo.get_audit(action=A.POLICY_CHANGED)
    assert len(entries) == 1 and entries[0]["metadata"]["previous_versions"] == ["privacy-2025-01"]


def test_privacy_rows_show_each_students_consent_policy_version(manager, app):
    grant_consent(manager, 3, face_recognition=True)
    rows = {r["student_id"]: r for r in login(app, "admin").get("/api/admin/privacy/students").get_json()["students"]}
    assert rows[3]["consent_policy_version"] == rows[3]["current_policy_version"]
    assert rows[3]["policy_outdated"] is False and rows[4]["consent_policy_version"] is None


def test_audit_filter_lists_every_action(app):
    html = login(app, "admin").get("/admin/privacy").get_data(as_text=True)
    for action in A.ACTIONS:
        assert f'value="{action}"' in html, action


# ------------------------------------------------------------- audit sanitize
def test_audit_keeps_counts_but_not_encoded_data(manager, repo):
    manager.audit.record(A.EVIDENCE_CREATED, None, 3, {
        "frame_count": 12, "image_count": 3, "frames": [1, 2], "note": "data:image/png;base64,iVBORw0KGgo=",
        "blob": "Ab1" * 100})
    meta = repo.get_audit(student_id=3)[0]["metadata"]
    assert meta["frame_count"] == 12 and meta["image_count"] == 3 and "frames" not in meta
    assert meta["note"] == "<encoded data removed>" and meta["blob"] == "<encoded data removed>"


# ---------------------------------------------------------------- retention
def test_retention_removes_orphan_reviews_and_old_context_segments(manager, clock):
    _, events = run_class_with_events(manager, clock, context="LECTURE")
    teacher = Principal(2, "teacher", class_ids=frozenset({1}))
    manager.reviews.review(teacher, events[0], "CONFIRMED")
    old = clock.t
    clock.advance(400 * 86400)
    manager.reviews.review(teacher, events[0], "UNCERTAIN")      # a review newer than the cut-off
    report = manager.retention.run(now=clock.t)
    assert report["events_deleted"] >= 1 and manager.repo.get_event_reviews(event_id=events[0]["id"]) == []
    assert report["context_segments_deleted"] >= 1
    assert manager.repo.get_context_segments(class_id=1) == [] and old < clock.t


def test_admin_retention_run_records_the_admin(manager, app, clock):
    run_class_with_events(manager, clock)
    clock.advance(400 * 86400)
    manager.clock = clock
    assert login(app, "admin").post("/api/admin/retention/run").status_code == 200
    runs = manager.repo.get_audit(action=A.RETENTION_RUN)
    assert runs and runs[0]["actor_role"] == "admin" and runs[0]["actor_id"] == 1


# ------------------------------------------------------------------ CSRF
@pytest.mark.parametrize("origin,referer,blocked", [
    ("https://evil.example", None, True),
    (None, "https://evil.example/page", True),
    ("null", None, True),
    ("http://localhost", None, False),
    (None, "http://localhost/teacher/events", False),
    (None, None, False),            # non-browser client (CLI, tests): no cookie riding possible
])
def test_cross_site_writes_are_rejected(app, origin, referer, blocked):
    client = login(app, "admin")
    headers = {k: v for k, v in (("Origin", origin), ("Referer", referer)) if v}
    resp = client.post("/api/admin/retention/run", headers=headers)
    assert (resp.status_code == 403) is blocked, resp.status_code
    if blocked:
        assert "không đến từ trang FocusGuard" in resp.get_json()["message"]


def test_cross_site_reads_and_login_form():
    assert cross_site_write("GET", "a.example", "https://evil.example") is False
    assert cross_site_write("POST", "a.example", "https://evil.example") is True
    assert cross_site_write("POST", "a.example", "https://partner.example", trusted=["https://partner.example"]) is False


def test_cross_site_login_post_is_rejected(app):
    resp = app.test_client().post("/login", data={"username": "admin", "password": "123"},
                                  headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


# ------------------------------------------------------- exports and CSV
def test_csv_export_neutralises_formulas(manager, app, clock):
    _, events = run_class_with_events(manager, clock)
    teacher = Principal(2, "teacher", class_ids=frozenset({1}))
    manager.reviews.review(teacher, events[0], "CONFIRMED", note='=HYPERLINK("http://x","bấm")')
    csv_text = login(app, "teacher").get("/api/export/student/3?format=csv&section=reviews").get_data(as_text=True)
    assert "'=HYPERLINK" in csv_text and ',=HYPERLINK' not in csv_text


def test_parent_export_strips_staff_notes(manager, app, clock):
    manager.consent.grant(ADMIN, 3, guardian_name="Mẹ", guardian_relationship="Mẹ",
                          scopes={"face_recognition": True}, note="ghi chú nội bộ")
    data = login(app, "phuhuynh").get("/api/export/student/3?format=json").get_json()
    for c in data["consents"]:
        assert "evidence_note" not in c and "created_by" not in c
    staff = login(app, "admin").get("/api/export/student/3?format=json").get_json()
    assert staff["consents"][0]["evidence_note"] == "ghi chú nội bộ"


# ------------------------------------------------- account deletion / role
def test_deleting_a_student_account_removes_face_data_with_audit(manager, repo, app):
    grant_consent(manager, 5, face_recognition=True)
    repo.save_face_embedding(5, json.dumps([0.1] * 8))
    assert login(app, "admin").post("/api/admin/delete_user/5").status_code == 200
    assert repo.get_user_by_id(5) is None
    assert repo.get_audit(student_id=5, action=A.FACE_DELETED)[0]["metadata"] == {"reason": "account_deleted"}


def test_changing_a_students_role_removes_face_data(manager, repo, app):
    grant_consent(manager, 5, face_recognition=True)
    repo.save_face_embedding(5, json.dumps([0.1] * 8))
    resp = login(app, "admin").post("/api/admin/edit_user/5", json={
        "username": "rohanverma", "display_name": "Lê Hoàng Giang", "role": "teacher", "class_id": 1})
    assert resp.status_code == 200
    assert repo.get_audit(student_id=5, action=A.FACE_DELETED)[0]["metadata"] == {"reason": "role_changed"}
    assert repo.get_face_enrollment(5)["has_face"] is False


def test_face_embedding_only_for_students_on_sqlite(manager, repo):
    assert repo.save_face_embedding(2, json.dumps([0.1] * 8)) is False      # teacher
    assert repo.save_face_embedding(999, json.dumps([0.1] * 8)) is False    # unknown
