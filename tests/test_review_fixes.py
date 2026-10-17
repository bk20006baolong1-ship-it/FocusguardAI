"""Regression tests for the defects found in the pre-merge code review."""
import json
import os
import subprocess
import sys
import threading

import pytest

from conftest import DEV_ENV, grant_consent, login
from focusguard.authz import Principal
from focusguard.behavior import Observation
from focusguard.evaluation.real_classroom import field
from focusguard.privacy import audit as A
from test_evidence import build, phone_episode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN = Principal(1, "admin")
TEACHER = Principal(2, "teacher", frozenset({1}))


# 1 ------------------------------------------------------- reverse proxy + CSRF
def _proxied_app(manager, trust):
    from app import create_app
    env = dict(DEV_ENV, FG_TRUST_PROXY="1") if trust else DEV_ENV
    app, _ = create_app(env=env, async_mode="threading", start_background=False, session_manager=manager)
    app.config["TESTING"] = True
    return app


@pytest.mark.parametrize("trust,expected", [(True, 302), (False, 403)])
def test_login_behind_a_reverse_proxy(manager, trust, expected):
    app = _proxied_app(manager, trust)
    resp = app.test_client().post("/login", data={"username": "admin", "password": "123"},
                                  base_url="http://127.0.0.1:5001",
                                  headers={"X-Forwarded-Host": "fg.school.vn", "X-Forwarded-Proto": "https",
                                           "Origin": "https://fg.school.vn"})
    assert resp.status_code == expected


def test_proxy_trust_does_not_open_cross_site_writes(manager):
    app = _proxied_app(manager, True)
    resp = app.test_client().post("/login", data={"username": "admin", "password": "123"},
                                  headers={"X-Forwarded-Host": "fg.school.vn", "Origin": "https://evil.example"})
    assert resp.status_code == 403


# 2 ------------------------------------------------- transient read errors
def test_transient_io_error_does_not_delete_the_clip(tmp_path, clock, monkeypatch):
    sm, app = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    phone_episode(sm, clock)
    clip = sm.repo.list_evidence(student_id=3)[0]

    def boom(name):
        raise OSError(24, "Too many open files")
    monkeypatch.setattr(sm.evidence.store, "read", boom)
    resp = login(app, "teacher").get(f"/api/teacher/evidence/{clip['evidence_id']}")
    assert resp.status_code == 500
    row = sm.repo.get_evidence(clip["evidence_id"])
    assert not row["deleted_at"] and os.path.exists(tmp_path / "evidence" / clip["file_name"])


# 3 / 13 ------------------------------------------------- account edits
def test_failed_role_change_keeps_face_data(manager, repo, app):
    grant_consent(manager, 5, face_recognition=True)
    repo.save_face_embedding(5, json.dumps([0.1] * 8))
    resp = login(app, "admin").post("/api/admin/edit_user/5", json={
        "username": "teacher", "display_name": "x", "role": "teacher", "class_id": 1})   # username taken
    assert resp.status_code == 409
    assert repo.get_face_enrollment(5)["has_face"] is True
    assert repo.get_audit(student_id=5, action=A.FACE_DELETED) == []


def test_deleting_a_student_removes_clips_too(tmp_path, clock):
    sm, app = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    phone_episode(sm, clock)
    assert login(app, "admin").post("/api/admin/delete_user/3").status_code == 200
    assert sm.repo.list_evidence(student_id=3) == [] and os.listdir(tmp_path / "evidence") == []


def test_stale_face_data_on_a_non_student_can_be_cleared(manager, repo):
    repo._execute("UPDATE users SET face_embedding = ? WHERE id = 2", (json.dumps([0.1] * 8),))
    assert repo.save_face_embedding(2, json.dumps([0.2] * 8)) is False      # never set on a teacher
    assert repo.save_face_embedding(2, None) is True                         # but always clearable
    assert repo.get_face_enrollment(2)["has_face"] is False


# 4 ------------------------------------------- identity of non-consenting students
def test_package_never_reveals_non_consenting_students(manager, clock):
    grant_consent(manager, 3, evaluation=True)
    grant_consent(manager, 4, face_recognition=True)            # recognised, but no evaluation consent
    manager.start_class(1, mode="classroom", started_by=2)
    eid = manager.evaluation.start(TEACHER, 1, {"consent_verified": True})["evaluation_session_id"]
    manager.evaluation.save_field_recording(eid, [
        {"t": clock.t, "track_id": 1, "gt_student_id": "UNLABELLED", "pred_student_id": 3},
        {"t": clock.t, "track_id": 2, "gt_student_id": "UNLABELLED", "pred_student_id": 4},
        {"t": clock.t, "track_id": 3, "gt_student_id": 4, "pred_student_id": 4}], {})
    frames = manager.evaluation.package(eid)["identity_frames"]
    assert [f["pred_student_id"] for f in frames] == [3, None]
    assert all(4 not in (f.get("pred_student_id"), f.get("gt_student_id")) for f in frames)


# 5 / 6 / 8 ------------------------------------------------- field tool
class FakeCam:
    def __init__(self):
        self.calls = 0
        self.last_frame_meta = {"t": 1.0, "latency_ms": 30.0, "identity_latency_ms": 2.0,
                                "tracks": {7: {"student_id": 3, "status": "CONFIRMED"}}}

    def get_frame(self, block=0.0):
        self.calls += 1          # camera offline: meta not refreshed


def test_camera_source_does_not_record_stale_frames():
    src = field.CameraSource(0)
    src.cam = FakeCam()
    stop, seen = threading.Event(), []

    def sink(t, ident, lat, id_lat):
        seen.append(t)
    timer = threading.Timer(0.2, stop.set)
    timer.start()
    src.run("01_lecture", stop, sink)
    assert src.cam.calls > 1 and seen == [1.0]


class FakeRealSource:
    kind = "REAL"
    camera_index = 0

    def __init__(self, sm, fail_in=None):
        self.sm, self.fail_in, self.rt = sm, fail_in, None

    def open(self, sm, class_id, cs_id):
        if self.fail_in == "open":
            raise RuntimeError("camera busy")
        self.rt = sm.class_runtime(class_id)

    def measured(self):
        return {"resolution": "1280x720", "camera_fps": 30.0}

    def run(self, sid, stop, sink):
        if self.fail_in == "run":
            raise RuntimeError("cv2 error")
        while not stop.wait(0.02):
            t = self.sm.clock()
            self.rt.process_frame(t, {3: Observation(timestamp=t, visible=True, ear=0.3, yaw=0, pitch=0)})
            sink(t, [], 25.0, 1.0)

    def close(self):
        pass


def _real_run(manager, answers, fail_in=None, monkeypatch=None):
    monkeypatch.setattr(field, "preflight", lambda *a, **k: [{"check": "all", "status": "PASS", "detail": "stub"}])
    grant_consent(manager, 3, evaluation=True)
    run = field.FieldRun(manager, field.ScriptedIO(answers, echo=False), FakeRealSource(manager, fail_in),
                         class_id=1, teacher="teacher", scenarios=["01_lecture"], scenario_seconds=0.1,
                         report_dir=None)
    return run, run.run()


def test_unattended_real_run_needs_explicit_paper_consent(manager, monkeypatch):
    run, code = _real_run(manager, {}, monkeypatch=monkeypatch)
    assert code == field.EXIT_CONSENT and "evaluation_session_id" not in run.result


@pytest.mark.parametrize("fail_in", ["open", "run"])
def test_a_camera_failure_closes_session_and_evaluation(manager, monkeypatch, fail_in):
    run, code = _real_run(manager, {"Phiếu đồng ý": "y"}, fail_in=fail_in, monkeypatch=monkeypatch)
    assert code == field.EXIT_ERROR and run.result["status"] == "FIELD_ERROR"
    assert manager.class_runtime(1) is None
    eid = run.result.get("evaluation_session_id")
    if eid:
        assert manager.evaluation.get(eid)["status"] == "ENDED"


def test_timed_unattended_scenario(manager, monkeypatch, tmp_path):
    monkeypatch.setattr(field, "_root", lambda: str(tmp_path))
    run, code = _real_run(manager, {"Phiếu đồng ý": "y"}, monkeypatch=monkeypatch)
    assert code == field.EXIT_OK
    eid = run.result["evaluation_session_id"]
    assert manager.evaluation.get(eid)["status"] == "ENDED" and manager.class_runtime(1) is None
    assert len(manager.evaluation.scenario_runs(eid)) == 1


# 7 ------------------------------------------------------------- retention
def test_retention_keeps_segments_of_a_kept_evaluation(manager, repo, clock):
    repo.save_context_segment({"class_id": 1, "class_session_id": 9, "context": "LECTURE", "started_at": 10.0})
    repo.save_context_segment({"class_id": 1, "evaluation_session_id": "e" * 32, "context": "WRITING",
                               "started_at": 10.0})
    report = manager.retention.run(now=10.0 + 366 * 86400)
    assert report["context_segments_deleted"] == 1
    assert [s["context"] for s in repo.get_context_segments(evaluation_session_id="e" * 32)] == ["WRITING"]


# 9 / 10 ------------------------------------------------------------ metrics
def test_running_scenario_counts_up_to_now(manager, clock):
    grant_consent(manager, 3, evaluation=True)
    manager.start_class(1, mode="classroom", started_by=2)
    eid = manager.evaluation.start(TEACHER, 1, {"consent_verified": True})["evaluation_session_id"]
    manager.evaluation.start_scenario(TEACHER, eid, "01_lecture")
    clock.advance(30)
    assert manager.evaluation.metrics(eid)["scenarios"]["per_scenario"]["01_lecture"]["seconds"] == 30.0


def test_nan_times_are_rejected(manager, clock):
    grant_consent(manager, 3, evaluation=True)
    eid = manager.evaluation.start(TEACHER, 1, {"consent_verified": True})["evaluation_session_id"]
    res = manager.evaluation.import_annotations(TEACHER, eid, [
        {"student_id": "3", "label": "FOCUSED", "start_s": "nan", "end_s": "5"},
        {"student_id": "3", "label": "FOCUSED", "start_s": "0", "end_s": "inf"}])
    assert res["added"] == 0 and manager.repo.get_annotations(evaluation_session_id=eid) == []


# 11 ------------------------------------------------------------- finalize
def test_finalize_package_rejects_import_flags(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"metadata": {"evaluation_session_id": "a" * 32}}), encoding="utf-8")
    r = subprocess.run([sys.executable, "scripts/finalize_real_classroom_evaluation.py", "a" * 32, "--package", str(p),
                        "--annotations-csv", "x.csv"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "offline package" in (r.stdout + r.stderr)


# 12 ------------------------------------------------------------ audit
def test_policy_change_is_not_reaudited_after_many_threshold_changes(manager, repo, clock):
    repo.create_consent({"student_id": 3, "scope_face_recognition": True, "policy_version": "old",
                         "accepted_at": clock.t, "expires_at": clock.t + 86400})
    assert manager.consent.record_policy_change() is True
    for i in range(600):
        repo.create_audit({"action": A.POLICY_CHANGED, "timestamp": clock.t + 10 + i,
                           "metadata": {"kind": "behavior_thresholds"}})
    assert manager.consent.record_policy_change() is False
