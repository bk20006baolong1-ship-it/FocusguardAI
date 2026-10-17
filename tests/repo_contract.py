"""Repository contract for the BTC-feedback tables, shared by both backends.

tests/test_repo_contract.py runs it on SQLite; tests/firestore/ runs the very
same functions on the Firestore emulator, so the two backends cannot drift.
"""
import json
import time


def contract_focus_event_extra_fields(repo):
    eid = repo.create_focus_event({"student_id": 3, "class_session_id": 7, "class_id": 1, "type": "HEAD_AWAY",
                                   "start_time": 1000.0, "end_time": 1010.0, "duration_seconds": 10.0,
                                   "confidence": 0.8, "source": "classroom_camera", "metadata": {"direction": "DOWN"},
                                   "activity_context": "WRITING", "interpreted_state": "EXPECTED_IN_CONTEXT",
                                   "penalty_multiplier": 0.0, "policy_version": "p1", "episode_uid": "u" * 32})
    assert eid
    ev = repo.get_focus_event(eid)
    assert ev["type"] == "HEAD_AWAY" and ev["activity_context"] == "WRITING"
    assert ev["interpreted_state"] == "EXPECTED_IN_CONTEXT" and ev["penalty_multiplier"] == 0.0
    assert ev["episode_uid"] == "u" * 32 and ev["metadata"]["direction"] == "DOWN"
    assert repo.get_focus_event(987654) is None
    return eid


def contract_event_reviews(repo):
    eid = contract_focus_event_extra_fields(repo)
    before = repo.get_focus_event(eid)
    r1 = repo.create_event_review({"event_id": eid, "student_id": 3, "class_session_id": 7, "class_id": 1,
                                   "verdict": "UNCERTAIN", "reviewed_by": 2, "reviewed_at": 10.0})
    repo.create_event_review({"event_id": eid, "student_id": 3, "class_session_id": 7, "class_id": 1,
                              "verdict": "CONFIRMED", "teacher_note": "ok", "reviewed_by": 2, "reviewed_at": 20.0})
    rows = repo.get_event_reviews(event_id=eid)
    assert [r["verdict"] for r in rows] == ["UNCERTAIN", "CONFIRMED"] and rows[0]["event_review_id"] == r1
    assert len(repo.get_event_reviews(class_id=1)) == 2 and repo.get_event_reviews(class_id=2) == []
    assert repo.get_focus_event(eid) == before          # raw event untouched


def contract_consents(repo):
    cid = repo.create_consent({"student_id": 3, "guardian_name": "Mẹ An", "guardian_relationship": "Mẹ",
                               "student_is_adult": False, "scope_face_recognition": True,
                               "scope_event_evidence": False, "scope_evaluation": True, "policy_version": "v1",
                               "accepted_at": 100.0, "expires_at": 9e9, "created_by": 1, "created_at": 100.0,
                               "unknown_field": "dropped"})
    rows = repo.get_consents(student_id=3)
    assert len(rows) == 1 and rows[0]["consent_id"] == cid
    assert rows[0]["scope_face_recognition"] is True and rows[0]["scope_event_evidence"] is False
    assert "unknown_field" not in rows[0] and rows[0]["revoked_at"] is None
    repo.update_consent(cid, {"revoked_at": 200.0, "revoked_by": 1, "student_id": 99, "scope_face_recognition": False})
    rec = repo.get_consent(cid)
    assert rec["revoked_at"] == 200.0 and rec["student_id"] == 3 and rec["scope_face_recognition"] is True


def contract_audit(repo):
    repo.create_audit({"actor_id": 1, "actor_role": "admin", "student_id": 3, "action": "FACE_DELETED",
                       "timestamp": 10.0, "metadata": {"reason": "x"}})
    repo.create_audit({"actor_id": None, "actor_role": "system", "student_id": None, "action": "RETENTION_RUN",
                       "timestamp": 20.0, "metadata": {}})
    rows = repo.get_audit()
    assert [r["action"] for r in rows] == ["RETENTION_RUN", "FACE_DELETED"]          # newest first
    assert repo.get_audit(student_id=3)[0]["metadata"] == {"reason": "x"}
    assert [r["action"] for r in repo.get_audit(action="FACE_DELETED")] == ["FACE_DELETED"]


def contract_evidence(repo):
    eid = "c" * 32
    repo.create_evidence({"evidence_id": eid, "episode_uid": "u" * 32, "event_type": "PHONE", "student_id": 3,
                          "class_session_id": 7, "class_id": 1, "clip_start": 1.0, "clip_end": 9.0, "frame_count": 16,
                          "fps": 2.0, "file_name": eid + ".fgev", "size_bytes": 10, "cropped": True,
                          "created_at": 10.0, "expires_at": 100.0})
    assert repo.get_evidence(eid)["cropped"] is True
    assert [r["evidence_id"] for r in repo.list_evidence(episode_uid="u" * 32)] == [eid]
    repo.mark_evidence_deleted(eid, 50.0, 2, "manual")
    assert repo.list_evidence(student_id=3) == []
    row = repo.list_evidence(student_id=3, include_deleted=True)[0]
    assert row["deleted_at"] == 50.0 and row["file_name"] is None


def contract_evaluation(repo):
    es = repo.create_evaluation_session({"class_id": 1, "class_session_id": 7, "camera_position": "FRONT_CORNER",
                                         "camera_angle": "2m", "resolution": "1280x720", "fps_target": 7.5,
                                         "device": "x", "student_count": 5, "lighting_condition": "DAYLIGHT",
                                         "activity_context": "LECTURE", "started_at": 1.0, "ended_at": None,
                                         "notes": "n", "consent_verified": True, "dataset_kind": "REAL",
                                         "status": "ACTIVE", "created_by": 2, "created_at": 1.0})
    got = repo.get_evaluation_session(es)
    assert got["consent_verified"] is True and got["fps_target"] == 7.5 and got["status"] == "ACTIVE"
    repo.update_evaluation_session(es, {"status": "ENDED", "ended_at": 5.0, "evaluation_session_id": "hijack"})
    assert repo.get_evaluation_session(es)["status"] == "ENDED"
    assert [s["evaluation_session_id"] for s in repo.list_evaluation_sessions(class_id=1)] == [es]
    a = repo.create_annotation({"evaluation_session_id": es, "student_id": 3, "label": "WRITING",
                                "started_at": 2.0, "ended_at": 3.0, "annotated_by": 2, "note": "x"})
    assert [x["annotation_id"] for x in repo.get_annotations(evaluation_session_id=es)] == [a]
    repo.save_context_segment({"evaluation_session_id": es, "context": "WRITING", "started_at": 2.0, "ended_at": 3.0})
    repo.save_context_segment({"class_session_id": 7, "class_id": 1, "context": "LECTURE", "started_at": 1.0,
                               "ended_at": 2.0, "set_by": 2})
    assert [s["context"] for s in repo.get_context_segments(evaluation_session_id=es)] == ["WRITING"]
    assert [s["context"] for s in repo.get_context_segments(class_session_id=7)] == ["LECTURE"]
    assert repo.delete_evaluation_session(es) >= 3
    assert repo.get_evaluation_session(es) is None and repo.get_annotations(evaluation_session_id=es) == []


def contract_face_enrollment(repo):
    assert repo.get_face_enrollment(3) == {"has_face": False, "enrolled_at": None, "template_count": 0}
    t = time.time()
    repo.save_face_embedding(3, json.dumps([0.1] * 4))
    enr = repo.get_face_enrollment(3)
    assert enr["has_face"] and enr["enrolled_at"] >= t - 1 and enr["template_count"] == 1
    assert [r["student_id"] for r in repo.get_students_with_face()] == [3]
    assert repo.get_students_with_face(before=t - 100) == []
    users = repo.get_users_list()
    assert all("face_embedding" not in u for u in users)
    repo.save_face_embedding(3, None)
    assert repo.get_face_enrollment(3)["has_face"] is False


def contract_retention_delete_before(repo):
    repo.create_focus_event({"student_id": 3, "class_id": 1, "class_session_id": 7, "type": "PHONE",
                             "start_time": 10.0, "end_time": 11.0})
    repo.create_focus_event({"student_id": 3, "class_id": 1, "class_session_id": 7, "type": "PHONE",
                             "start_time": 1_000.0, "end_time": 1_001.0})
    assert repo.delete_rows_before("focus_events", "start_time", 500.0) == 1
    assert [e["start_time"] for e in repo.get_focus_events(student_id=3)] == [1_000.0]


def contract_services_end_to_end(repo):
    """Consent -> class with events -> teacher review -> evaluation package, on this backend."""
    from focusguard.authz import Principal
    from focusguard.behavior import Observation
    from session_manager import SessionManager
    t = [1_800_000_000.0]
    sm = SessionManager(repo=repo, seed_demo_accounts=True, clock=lambda: t[0])
    admin, teacher = Principal(1, "admin"), Principal(2, "teacher", frozenset({1}))
    sm.consent.grant(admin, 3, guardian_name="Mẹ", guardian_relationship="Mẹ",
                     scopes={"face_recognition": True, "evaluation": True})
    assert sm.consent.status(3)["state"] == "ACTIVE"
    rt = sm.start_class(1, mode="classroom", started_by=2)
    es = sm.evaluation.start(teacher, 1, {"consent_verified": True, "activity_context": "LECTURE"})
    t0 = t[0]
    for i in range(80):
        t[0] += 0.25
        rt.process_frame(t[0], {3: Observation(timestamp=t[0], visible=True, ear=0.3, yaw=0, pitch=0,
                                               phone_confidence=0.9 if 20 <= i < 60 else None)})
    sm.set_activity_context(1, "WRITING", teacher)
    sm.evaluation.annotate(teacher, es["evaluation_session_id"], {"student_id": 3, "label": "PHONE_DISTRACTION",
                                                                  "started_at": t0 + 5, "ended_at": t0 + 15})
    sm.evaluation.end(teacher, es["evaluation_session_id"])
    sm.end_class(1)
    events = repo.get_focus_events(class_session_id=rt.class_session_id)
    assert [e["type"] for e in events] == ["PHONE"] and events[0]["activity_context"] == "LECTURE"
    sm.reviews.review(teacher, events[0], "CONFIRMED", "đúng")
    names = {3: "An"}
    rows = sm.reviews.class_events(1, names)
    assert rows[0]["review_state"] == "CONFIRMED"
    segs = repo.get_context_segments(class_session_id=rt.class_session_id)
    assert [s["context"] for s in segs] == ["LECTURE", "WRITING"]
    pkg = sm.evaluation.package(es["evaluation_session_id"])
    assert pkg["predictions"] and pkg["annotations"][0]["label"] == "PHONE_DISTRACTION"
    res = sm.consent.revoke(admin, 3)
    assert sm.consent.status(3)["state"] == "REVOKED" and res["face_deleted"] is False
    assert {a["action"] for a in repo.get_audit()} >= {"CONSENT_GRANTED", "EVENT_REVIEWED", "CONSENT_REVOKED",
                                                       "ACTIVITY_CONTEXT_CHANGED"}


def contract_scenario_runs(repo):
    eid = repo.create_evaluation_session({"class_id": 1, "started_at": 100.0, "status": "ACTIVE",
                                          "dataset_kind": "REAL", "scenario_pack_version": "btc-2026.1"})
    r1 = repo.create_scenario_run({"evaluation_session_id": eid, "scenario_id": "01_lecture", "started_at": 110.0,
                                   "started_by": 2})
    r2 = repo.create_scenario_run({"evaluation_session_id": eid, "scenario_id": "05_phone", "started_at": 200.0,
                                   "started_by": 2})
    repo.update_scenario_run(r1, {"ended_at": 190.0, "scenario_id": "99_forged", "started_at": 0.0})
    runs = repo.get_scenario_runs(eid)
    assert [r["scenario_run_id"] for r in runs] == [r1, r2]
    assert runs[0]["scenario_id"] == "01_lecture" and runs[0]["started_at"] == 110.0 and runs[0]["ended_at"] == 190.0
    assert runs[1]["ended_at"] is None
    repo.update_evaluation_session(eid, {"actual_fps": 7.25, "status": "ENDED"})
    es = repo.get_evaluation_session(eid)
    assert es["actual_fps"] == 7.25 and es["scenario_pack_version"] == "btc-2026.1"
    assert repo.delete_evaluation_session(eid) >= 3
    assert repo.get_scenario_runs(eid) == [] and repo.get_evaluation_session(eid) is None


def contract_retention_all_tables(repo):
    import pytest
    repo.create_focus_snapshot({"student_id": 3, "class_session_id": 7, "class_id": 1, "timestamp": 10.0,
                              "focus_score": 90})
    repo.create_focus_snapshot({"student_id": 3, "class_session_id": 7, "class_id": 1, "timestamp": 1_000.0,
                              "focus_score": 80})
    assert repo.delete_rows_before("focus_snapshots", "timestamp", 500.0) == 1
    for at in (10.0, 1_000.0):
        repo.create_event_review({"event_id": 1, "student_id": 3, "class_id": 1, "verdict": "CONFIRMED",
                                  "reviewed_by": 2, "reviewed_at": at})
        repo.save_context_segment({"class_session_id": 7, "class_id": 1, "context": "LECTURE", "started_at": at,
                                   "ended_at": at + 5})
    repo.create_event_review({"event_id": 2, "student_id": 3, "class_id": 1, "verdict": "UNCERTAIN",
                              "reviewed_by": 2, "reviewed_at": 2_000.0})
    assert repo.delete_rows_before("event_reviews", "reviewed_at", 500.0) == 1
    assert repo.delete_rows_before("context_segments", "started_at", 500.0) == 1
    assert [s["started_at"] for s in repo.get_context_segments(class_id=1)] == [1_000.0]
    assert repo.delete_event_reviews(event_id=2) == 1 and len(repo.get_event_reviews(class_id=1)) == 1
    repo.save_context_segment({"class_id": 1, "context": "EXAM", "started_at": 20.0, "evaluation_session_id": "e" * 32})
    repo.save_context_segment({"class_id": 1, "class_session_id": 8, "context": "EXAM", "started_at": 20.0})
    assert repo.delete_class_context_segments_before(500.0) == 1             # evaluation segment kept
    assert [s["evaluation_session_id"] for s in repo.get_context_segments(class_id=1) if s["started_at"] == 20.0] == ["e" * 32]
    with pytest.raises(ValueError):
        repo.delete_rows_before("event_reviews", "no_such_column", 1.0)   # unknown column: refused on both backends
    for i, cid in enumerate((1, 1, 2)):
        repo.create_evidence({"student_id": 3, "class_id": cid, "class_session_id": 7, "file_name": f"{i}.fgev",
                              "created_at": 1.0 + i})
    assert len(repo.list_evidence(class_id=1)) == 2 and len(repo.list_evidence(class_id=2)) == 1
    for i in range(5):
        repo.create_audit({"action": "DATA_EXPORTED", "timestamp": 100.0 + i})
    latest = repo.get_audit(action="DATA_EXPORTED", limit=2)
    assert [a["timestamp"] for a in latest] == [104.0, 103.0]


def contract_face_data_only_for_students(repo):
    import json
    assert repo.save_face_embedding(2, json.dumps([0.1] * 4)) is False          # teacher
    assert repo.save_face_embedding(987654, json.dumps([0.1] * 4)) is False     # no such user
    assert repo.get_face_enrollment(2)["has_face"] is False
    assert repo.save_face_embedding(2, None) is True                         # clearing is allowed for any role
    assert repo.save_face_embedding(3, json.dumps([0.1] * 4)) is True
    repo.save_face_embedding(3, None)


def contract_field_evaluation_end_to_end(repo):
    """Scenario windows + recorder FPS + per-scenario metrics on this backend."""
    from focusguard.authz import Principal
    from focusguard.behavior import Observation
    from session_manager import SessionManager
    t = [1_850_000_000.0]
    sm = SessionManager(repo=repo, seed_demo_accounts=True, clock=lambda: t[0])
    admin, teacher = Principal(1, "admin"), Principal(2, "teacher", frozenset({1}))
    sm.consent.grant(admin, 3, guardian_name="Mẹ", guardian_relationship="Mẹ", scopes={"evaluation": True})
    rt = sm.start_class(1, mode="classroom", started_by=2)
    es = sm.evaluation.start(teacher, 1, {"consent_verified": True})
    eid = es["evaluation_session_id"]
    sm.evaluation.start_scenario(teacher, eid, "05_phone", apply_context=True)
    t0 = t[0]
    for i in range(40):
        t[0] += 0.2
        rt.process_frame(t[0], {3: Observation(timestamp=t[0], visible=True, ear=0.3, yaw=0, pitch=0,
                                               phone_confidence=0.9 if 5 <= i < 30 else None)})
    sm.evaluation.stop_scenario(teacher, eid)
    sm.evaluation.annotate(teacher, eid, {"student_id": 3, "label": "PHONE_DISTRACTION", "started_at": t0 + 1,
                                          "ended_at": t0 + 6})
    ended = sm.evaluation.end(teacher, eid)
    sm.end_class(1)
    assert ended["actual_fps"] == 5.0 and ended["status"] == "ENDED"
    assert ended["activity_context"] == "LECTURE"            # 05_phone's suggested context, applied on request
    pkg = sm.evaluation.package(eid)
    assert [s["scenario_id"] for s in pkg["scenarios"]] == ["05_phone"]
    rep = sm.evaluation.metrics(eid)["scenarios"]
    assert rep["run"] == ["05_phone"] and rep["per_scenario"]["05_phone"]["ai_signals"] == {"PHONE": 1}
    assert len(rep["not_run"]) == 14 and rep["run_and_labelled"] == ["05_phone"]


def contract_cameras(repo):
    """Camera settings storage: CRUD, booleans, default/shared filters, class assignment (one per class)."""
    a = repo.create_camera({"owner_user_id": 2, "name": "Phòng 8/1", "vendor": "EZVIZ", "model": "CS-H1C",
                            "host": "192.168.1.64", "port": 554, "protocol": "AUTO", "source_type": "RTSP",
                            "stream_path": "/H.264", "username": "admin", "encrypted_secret": "fgc1:k:token",
                            "is_default": True, "is_shared": False, "status": "CONNECTED", "resolution": "1920x1080",
                            "measured_fps": 18.0, "unknown_field": "dropped", "url": "rtsp://never-stored"})
    b = repo.create_camera({"owner_user_id": 1, "name": "Cổng trường", "source_type": "RTSP", "host": "10.0.0.7",
                            "is_default": False, "is_shared": True})
    w = repo.create_camera({"owner_user_id": 2, "name": "Webcam", "source_type": "WEBCAM", "device_index": 0,
                            "is_default": False, "is_shared": False})
    cam = repo.get_camera(a)
    assert cam["is_default"] is True and cam["is_shared"] is False and cam["port"] == 554
    assert cam["measured_fps"] == 18.0 and "unknown_field" not in cam and "url" not in cam
    assert repo.get_camera("nope") is None
    assert [c["camera_id"] for c in repo.list_cameras(owner_user_id=2)] == [a, w]
    assert [c["camera_id"] for c in repo.list_cameras(is_shared=True)] == [b]
    assert {c["camera_id"] for c in repo.list_cameras()} == {a, b, w}
    repo.update_camera(a, {"name": "Phòng 8/2", "is_default": False, "camera_id": "hijack", "owner_user_id": 2})
    cam = repo.get_camera(a)
    assert cam["name"] == "Phòng 8/2" and cam["is_default"] is False and cam["camera_id"] == a
    repo.set_class_camera(1, b, assigned_by=1, assigned_at=5.0)
    repo.set_class_camera(2, b, assigned_by=1, assigned_at=6.0)
    assert repo.get_class_camera(1)["camera_id"] == b and repo.get_class_camera(3) is None
    repo.set_class_camera(1, a, assigned_by=1)                       # one camera per class: replaced
    assert repo.get_class_camera(1)["camera_id"] == a
    assert [r["class_id"] for r in repo.list_class_cameras(camera_id=b)] == [2]
    repo.set_class_camera(2, None)
    assert repo.get_class_camera(2) is None
    assert repo.delete_camera(a) == 1 and repo.get_camera(a) is None
    assert repo.get_class_camera(1) is None                          # assignment removed with the camera
    assert repo.delete_camera(w) == 1 and repo.delete_camera(b) == 1


def contract_camera_service_end_to_end(repo):
    """CameraService on this backend: encrypted secret only, default uniqueness, class resolution."""
    from focusguard.authz import Principal
    from focusguard.cameras.crypto import CredentialVault, generate_key
    from focusguard.cameras.service import CameraService
    from focusguard.cameras.testing import MockCameraSource
    from focusguard.privacy.audit import AuditLog

    class Deps:
        @staticmethod
        def rtsp_options(ip, port):
            return "OK", "EZVIZ"

        @staticmethod
        def rtsp_probe(ip, port, path, username=None, secret=None, **_k):
            from focusguard.cameras.rtsp import ProbeResult
            ok = secret == "ABCDEF" and path == "/H.264"
            return ProbeResult("OK" if ok else ("AUTH_FAILED" if secret != "ABCDEF" else "NOT_FOUND"),
                               "EZVIZ", ok, "H264", 200)

        class onvif_client:
            def __init__(self, *a, **k):
                pass

            def device_information(self):
                from focusguard.cameras.onvif import OnvifError
                raise OnvifError("no onvif")
    svc = CameraService(repo, AuditLog(repo), vault=CredentialVault(generate_key()), connect_deps=Deps,
                        source_factory=lambda spec: MockCameraSource(name=spec.get("name"), fps=60),
                        measure_seconds=0.5)
    teacher = Principal(2, "teacher", frozenset({1}))
    r = svc.test_connection(teacher, {"host": "192.168.1.64", "username": "admin", "secret": "ABCDEF"})
    assert r["status"] == "CONNECTED" and r["test_token"] and r["vendor"] == "EZVIZ"
    cam = svc.save(teacher, {"test_token": r["test_token"], "name": "Phòng 8/1"})
    row = repo.get_camera(cam["camera_id"])
    assert row["encrypted_secret"].startswith("fgc1:") and "ABCDEF" not in json.dumps(row)
    assert row["stream_path"] == "/H.264" and row["is_default"] is True and cam["has_credentials"] is True
    r2 = svc.test_connection(teacher, {"host": "192.168.1.65", "username": "admin", "secret": "ABCDEF"})
    cam2 = svc.save(teacher, {"test_token": r2["test_token"], "name": "Phòng 8/2", "is_default": True})
    defaults = [c["camera_id"] for c in repo.list_cameras(owner_user_id=2) if c["is_default"]]
    assert defaults == [cam2["camera_id"]]                           # exactly one default per owner
    assert svc.resolve_for_class(teacher, 1)[0]["camera_id"] == cam2["camera_id"]
    assert svc.decrypt(repo.get_camera(cam2["camera_id"])) == "ABCDEF"
    actions = [a["action"] for a in repo.get_audit()]
    assert "CAMERA_ADDED" in actions and "CAMERA_CONNECTION_TESTED" in actions
    assert "ABCDEF" not in json.dumps(repo.get_audit(), ensure_ascii=False)
    svc.delete(teacher, cam["camera_id"])
    svc.delete(teacher, cam2["camera_id"])


def contract_face_profiles(repo):
    """Multi-template face profiles + legacy migration + deletion, same on both backends."""
    v1, v2 = [0.1] * 8, [0.2] * 8
    a = repo.add_face_template({"student_id": 3, "embedding": v1, "quality_score": 0.9, "pose_bucket": "FRONT",
                                "source": "TEACHER_TAG_BURST", "class_session_id": 7, "binding_id": "b1",
                                "created_at": 100.0, "image": "dropped"})
    repo.add_face_template({"student_id": 3, "embedding": v2, "quality_score": 0.7, "pose_bucket": "LEFT",
                            "source": "PROGRESSIVE", "created_at": 200.0})
    repo.add_face_template({"student_id": 4, "embedding": v1, "source": "ADMIN_ENROLLMENT", "created_at": 150.0})
    rows = repo.list_face_templates(3)
    assert [r["face_profile_id"] for r in rows][0] == a and len(rows) == 2
    assert rows[0]["embedding"] == v1 and rows[0]["active"] is True and "image" not in rows[0]
    assert repo.face_template_counts() == {3: 2, 4: 1}
    repo.update_face_template(a, {"last_used_at": 300.0, "embedding": [9.0] * 8, "student_id": 4})
    r = [x for x in repo.list_face_templates(3) if x["face_profile_id"] == a][0]
    assert r["last_used_at"] == 300.0 and r["embedding"] == v1 and r["student_id"] == 3
    repo.mark_face_enrolled(3, 50.0)
    repo.mark_face_enrolled(3, 999.0)                               # never moved forward
    enr = repo.get_face_enrollment(3)
    assert enr["has_face"] and enr["template_count"] == 2 and enr["enrolled_at"] == 50.0
    gallery = {g["student_id"]: g for g in repo.get_student_embeddings_by_class(1)}
    assert len(gallery[3]["embeddings"]) == 2 and gallery[3]["embedding"] == v1
    assert repo.delete_face_templates_for_binding("b1") == 1
    assert len(repo.list_face_templates(3)) == 1
    assert repo.delete_rows_before("face_profiles", "created_at", 175.0) == 1          # retention cut (student 4's)
    assert repo.list_face_templates(4) == []
    assert {x["student_id"] for x in repo.get_students_with_face()} == {3}
    assert any(u["id"] == 3 and u["has_face"] for u in repo.get_users_list())
    repo.save_face_embedding(3, None)                               # revoke / delete: everything goes
    assert repo.list_face_templates(3) == [] and repo.get_face_enrollment(3)["has_face"] is False
    # legacy single embedding (older installations) -> migrated into one LEGACY template
    if hasattr(repo, "_execute"):
        repo._execute("UPDATE users SET face_embedding = ?, face_enrolled_at = 42.0 WHERE id = 5", (json.dumps(v2),))
    else:
        repo._col("users").document("5").update({"face_embedding": json.dumps(v2), "face_enrolled_at": 42.0})
    assert repo.migrate_legacy_face_embeddings() == 1 and repo.migrate_legacy_face_embeddings() == 0
    legacy = repo.list_face_templates(5)
    assert len(legacy) == 1 and legacy[0]["source"] == "LEGACY" and legacy[0]["embedding"] == v2
    assert repo.get_face_enrollment(5) == {"has_face": True, "enrolled_at": 42.0, "template_count": 1}
    repo.save_face_embedding(5, None)


def contract_identity_bindings_and_attendance(repo):
    b1 = repo.create_identity_binding({"class_session_id": 7, "class_id": 1, "track_id": 3, "student_id": 5,
                                       "identity_source": "AUTO_FACE", "confidence": 0.81, "confirmed_at": 10.0})
    b2 = repo.create_identity_binding({"class_session_id": 7, "class_id": 1, "track_id": 3, "student_id": 6,
                                       "identity_source": "TEACHER_CORRECTED", "confirmed_by": 2, "confirmed_at": 20.0,
                                       "correction_reason": "nhận nhầm", "replaced_binding_id": b1})
    repo.end_identity_binding(b1, 20.0, "teacher_corrected")
    rows = repo.list_identity_bindings(class_session_id=7)
    assert [r["binding_id"] for r in rows] == [b1, b2]
    assert rows[0]["ended_at"] == 20.0 and rows[0]["end_reason"] == "teacher_corrected"
    assert rows[1]["confirmed_by"] == 2 and rows[1]["replaced_binding_id"] == b1 and rows[1]["ended_at"] is None
    assert [r["binding_id"] for r in repo.list_identity_bindings(student_id=6)] == [b2]
    assert repo.delete_rows_before("identity_bindings", "confirmed_at", 15.0) == 1
    repo.save_attendance_record({"class_session_id": 7, "class_id": 1, "student_id": 5, "status": "LATE",
                                 "source": "TEACHER_CORRECTED", "updated_by": 2, "updated_at": 30.0})
    repo.save_attendance_record({"class_session_id": 7, "class_id": 1, "student_id": 5, "status": "PRESENT",
                                 "source": "TEACHER_CORRECTED", "updated_by": 2, "updated_at": 40.0})
    recs = repo.get_attendance_records(class_session_id=7)
    assert len(recs) == 1 and recs[0]["status"] == "PRESENT" and recs[0]["attendance_record_id"] == "7:5"
    cs = repo.create_class_session(1, "2026-01-01 08:00:00", mode="classroom", started_by=2)
    repo.save_session_student({"class_session_id": cs, "class_id": 1, "student_id": 5, "attendance_status": "ABSENT",
                               "attendance_source": "AUTO_FINALIZED", "event_counts": {}})
    repo.update_session_student_attendance(cs, 5, "PRESENT", "TEACHER_CORRECTED")
    row = repo.get_session_students(class_session_id=cs)[0]
    assert row["attendance_status"] == "PRESENT" and row["attendance_source"] == "TEACHER_CORRECTED"
    # a student with no stored row (added later / abnormal end): the correction creates it
    repo.update_session_student_attendance(cs, 6, "LATE", "TEACHER_CORRECTED")
    rows = {r["student_id"]: r for r in repo.get_session_students(class_session_id=cs)}
    assert len(rows) == 2 and rows[6]["attendance_status"] == "LATE" and rows[6]["class_id"] == 1
    repo.update_session_student_attendance(cs, 6, "PRESENT", "TEACHER_CORRECTED")
    assert len(repo.get_session_students(class_session_id=cs)) == 2               # updated, not duplicated


def contract_session_identity_and_attendance_evidence(repo):
    """Session-owned binding lifecycle (detach / continue) and the append-only evidence ledger."""
    b1 = repo.create_identity_binding({"class_session_id": 9, "class_id": 1, "track_id": 4, "student_id": 7,
                                       "identity_source": "TEACHER_TAG", "confirmed_by": 2, "confirmed_at": 10.0,
                                       "teacher_anchored": True})
    repo.update_identity_binding(b1, {"detached_at": 30.0, "last_seen_at": 29.5, "student_id": 99,
                                      "identity_source": "AUTO_FACE"})            # history fields are not rewritable
    b2 = repo.create_identity_binding({"class_session_id": 9, "class_id": 1, "track_id": 104, "student_id": 7,
                                       "identity_source": "AUTO_RECOVERED", "confirmed_at": 40.0,
                                       "derived_from_binding_id": b1, "recovery_method": "SEAT_CONTINUITY",
                                       "replaced_binding_id": b1, "teacher_anchored": True})
    repo.end_identity_binding(b1, 40.0, "restored")
    rows = {r["binding_id"]: r for r in repo.list_identity_bindings(class_session_id=9)}
    assert rows[b1]["student_id"] == 7 and rows[b1]["identity_source"] == "TEACHER_TAG"
    assert rows[b1]["detached_at"] == 30.0 and rows[b1]["last_seen_at"] == 29.5 and rows[b1]["end_reason"] == "restored"
    assert rows[b1]["teacher_anchored"] is True and rows[b2]["teacher_anchored"] is True
    assert rows[b2]["derived_from_binding_id"] == b1 and rows[b2]["recovery_method"] == "SEAT_CONTINUITY"
    assert rows[b2]["ended_at"] is None
    e1 = repo.save_attendance_evidence({"class_session_id": 9, "class_id": 1, "student_id": 7, "track_id": 4,
                                        "binding_id": b1, "identity_source": "TEACHER_TAG",
                                        "attendance_source": "TEACHER_CONFIRMED", "first_seen_at": 5.0,
                                        "confirmed_at": 10.0, "last_seen_at": 10.0, "late": False})
    e2 = repo.save_attendance_evidence({"class_session_id": 9, "class_id": 1, "student_id": 7, "track_id": 104,
                                        "binding_id": b2, "identity_source": "AUTO_RECOVERED",
                                        "attendance_source": "AUTO_FACE", "first_seen_at": 40.0,
                                        "confirmed_at": 40.0, "last_seen_at": 40.0, "late": True})
    repo.update_attendance_evidence(e2, {"revoked_at": 50.0, "revoked_reason": "teacher_correction",
                                         "revoked_by": 2, "binding_id": "tampered", "late": False})
    repo.update_attendance_evidence(e1, {"last_seen_at": 60.0})
    ev = {e["evidence_id"]: e for e in repo.list_attendance_evidence(class_session_id=9)}
    assert len(ev) == 2 and [e["evidence_id"] for e in repo.list_attendance_evidence(class_session_id=9)] == [e1, e2]
    assert ev[e2]["revoked_at"] == 50.0 and ev[e2]["revoked_reason"] == "teacher_correction"
    assert ev[e2]["binding_id"] == b2 and ev[e2]["late"] is True                  # provenance is append-only
    assert ev[e1]["last_seen_at"] == 60.0 and ev[e1]["revoked_at"] is None and ev[e1]["late"] is False
    assert [e["evidence_id"] for e in repo.list_attendance_evidence(student_id=7)] == [e1, e2]
    assert repo.delete_rows_before("attendance_evidence", "confirmed_at", 20.0) == 1


ALL = [contract_focus_event_extra_fields, contract_event_reviews, contract_consents, contract_audit, contract_evidence,
       contract_evaluation, contract_face_enrollment, contract_retention_delete_before, contract_services_end_to_end,
       contract_scenario_runs, contract_retention_all_tables, contract_face_data_only_for_students,
       contract_field_evaluation_end_to_end, contract_cameras, contract_camera_service_end_to_end,
       contract_face_profiles, contract_identity_bindings_and_attendance,
       contract_session_identity_and_attendance_evidence]
