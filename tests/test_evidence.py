"""Event Evidence Mode (BTC feedback D/E): OFF by default, consent-gated, teacher-only, expiring."""
import os

import pytest

from conftest import DEV_ENV, FakeClock, add_teacher, grant_consent, login
from focusguard.behavior import Observation
from focusguard.privacy.evidence import EvidenceError, EvidenceStore, FrameRingBuffer
from focusguard.privacy.settings import PrivacySettings
from repository import SQLiteRepository
from session_manager import SessionManager

JPEG = b"\xff\xd8fake-jpeg\xff\xd9"


def test_evidence_mode_is_off_by_default():
    assert PrivacySettings().evidence_enabled is False
    assert PrivacySettings.from_env({}).evidence_enabled is False
    assert PrivacySettings.from_env({"EVENT_EVIDENCE_ENABLED": "true"}).evidence_enabled is True
    s = PrivacySettings.from_env({})
    assert (s.evidence_pre_seconds, s.evidence_post_seconds, s.evidence_retention_hours) == (3.0, 5.0, 24.0)
    assert s.evidence_parent_access is False
    assert PrivacySettings.from_env({"EVENT_EVIDENCE_PARENT_ACCESS": "true"}).evidence_parent_access is False


def test_evidence_folder_is_not_public():
    s = PrivacySettings()
    assert not os.path.abspath(s.evidence_dir).startswith(os.path.abspath("static"))
    gi = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".gitignore"), encoding="utf-8").read()
    assert "instance/" in gi


@pytest.fixture
def clock():
    return FakeClock()


def build(tmp_path, clock, enabled=True):
    settings = PrivacySettings(evidence_enabled=enabled, evidence_dir=str(tmp_path / "evidence"),
                               evidence_fps=5.0, evidence_retention_hours=24.0)
    sm = SessionManager(repo=SQLiteRepository(":memory:"), seed_demo_accounts=True, clock=clock,
                        privacy_settings=settings)
    # Clips only ever keep frames cropped to one student (the camera pipeline installs
    # classroom_ai.crop_jpeg); here a deterministic stand-in marks the cropped frame.
    sm.evidence.cropper = fake_crop
    from app import create_app
    app, _ = create_app(env=DEV_ENV, async_mode="threading", start_background=False, session_manager=sm)
    app.config["TESTING"] = True
    return sm, app


def fake_crop(jpeg, box):
    return b"crop" + repr(tuple(box)).encode() + jpeg


def phone_episode(sm, clock, student_id=3, push=True):
    rt = sm.start_class(1, mode="classroom")
    for i in range(60):                       # 12 s at 5 fps: phone from 2 s to 8 s
        t = clock.advance(0.2)
        phone = 0.9 if 10 <= i < 40 else None
        rt.process_frame(t, {student_id: Observation(timestamp=t, visible=True, face_visible=True, ear=0.3,
                                                     yaw=0.0, pitch=0.0, phone_confidence=phone)})
        if push:
            sm.evidence.push_frame(rt.class_session_id, t, JPEG + bytes([i]), {student_id: (0, 0, 10, 10)})
    sm.end_class(1)
    return rt


def test_no_clip_and_no_buffer_when_off(tmp_path, clock):
    sm, _ = build(tmp_path, clock, enabled=False)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    phone_episode(sm, clock)
    assert sm.repo.list_evidence() == [] and sm.evidence._buffers == {}
    assert not (tmp_path / "evidence").exists()
    assert sm.repo.get_focus_events(student_id=3)          # metadata still stored


def test_no_clip_without_evidence_consent(tmp_path, clock):
    sm, _ = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True)            # no event_evidence scope
    phone_episode(sm, clock)
    assert sm.repo.list_evidence() == []


def test_clip_saved_around_the_event_only(tmp_path, clock):
    sm, _ = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    phone_episode(sm, clock)
    clips = sm.repo.list_evidence(student_id=3)
    assert len(clips) == 1
    c = clips[0]
    ev = sm.repo.get_focus_events(student_id=3)[0]
    assert c["episode_uid"] == ev["episode_uid"] and c["event_type"] == "PHONE"
    assert c["clip_start"] >= ev["start_time"] - 3.0 - 1e-6           # pre seconds
    assert c["clip_end"] <= ev["metadata"]["detected_at"] + 5.0 + 1e-6  # post seconds
    assert c["clip_end"] - c["clip_start"] < 12.0                     # not the whole session
    assert len(c["file_name"]) == 32 + 5 and c["expires_at"] == pytest.approx(c["created_at"] + 24 * 3600)
    files = os.listdir(tmp_path / "evidence")
    assert files == [c["file_name"]]


@pytest.fixture
def clip_app(tmp_path, clock):
    sm, app = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    phone_episode(sm, clock)
    clip = sm.repo.list_evidence(student_id=3)[0]
    ev = sm.repo.get_focus_events(student_id=3)[0]
    return sm, app, clip, ev


def test_teacher_of_class_can_view_clip_and_view_is_audited(clip_app):
    sm, app, clip, ev = clip_app
    t = login(app, "teacher")
    detail = t.get(f"/api/teacher/events/{ev['id']}").get_json()["event"]
    assert detail["evidence"]["evidence_id"] == clip["evidence_id"] and detail["evidence"]["expires_in_seconds"] > 0
    resp = t.get(f"/api/teacher/evidence/{clip['evidence_id']}")
    assert resp.status_code == 200 and resp.headers["Cache-Control"].startswith("no-store")
    body = resp.get_json()
    assert body["frames"] and body["frames"][0]["src"].startswith("data:image/jpeg;base64,")
    assert [a["action"] for a in sm.audit.list(action="EVIDENCE_VIEWED")] == ["EVIDENCE_VIEWED"]


def test_unauthorised_users_cannot_view_clip(clip_app):
    sm, app, clip, _ = clip_app
    add_teacher(sm.repo)
    url = f"/api/teacher/evidence/{clip['evidence_id']}"
    assert app.test_client().get(url).status_code == 401
    for user in ("teacher2", "hocsinh", "phuhuynh", "admin"):
        assert login(app, user).get(url).status_code == 403, user
    assert sm.audit.list(action="EVIDENCE_VIEWED") == []


def test_no_public_static_url_and_traversal_rejected(clip_app, tmp_path):
    sm, app, clip, _ = clip_app
    anon = app.test_client()
    assert anon.get("/static/" + clip["file_name"]).status_code == 404
    assert anon.get("/instance/evidence/" + clip["file_name"]).status_code == 404
    t = login(app, "teacher")
    for bad in ("../../etc/passwd", "..%2F..%2Fapp.py", clip["evidence_id"][:-1] + "Z", "x" * 32):
        assert t.get("/api/teacher/evidence/" + bad).status_code == 404, bad
    store = EvidenceStore(str(tmp_path / "evidence"))
    for name in ("../x.fgev", "/etc/passwd", "a" * 32 + ".jpg", "..", ""):
        with pytest.raises(EvidenceError):
            store.read(name)


def test_teacher_can_delete_clip_and_deletion_is_audited(clip_app, tmp_path):
    sm, app, clip, ev = clip_app
    t = login(app, "teacher")
    assert t.post(f"/api/evidence/{clip['evidence_id']}/delete").status_code == 200
    assert os.listdir(tmp_path / "evidence") == []
    assert t.get(f"/api/teacher/evidence/{clip['evidence_id']}").status_code == 404
    assert t.get(f"/api/teacher/events/{ev['id']}").get_json()["event"]["evidence"] is None
    row = sm.repo.get_evidence(clip["evidence_id"])
    assert row["deleted_at"] and row["deleted_by"] == 2 and row["file_name"] is None
    assert sm.audit.list(action="EVIDENCE_DELETED")[0]["actor_id"] == 2
    assert sm.repo.get_focus_event(ev["id"])                # the event metadata is kept


def test_other_users_cannot_delete_but_admin_can(clip_app):
    sm, app, clip, _ = clip_app
    add_teacher(sm.repo)
    for user in ("teacher2", "hocsinh", "phuhuynh"):
        assert login(app, user).post(f"/api/evidence/{clip['evidence_id']}/delete").status_code == 403, user
    assert login(app, "admin").post(f"/api/evidence/{clip['evidence_id']}/delete").status_code == 200


def test_retention_cleanup_deletes_expired_clips(clip_app, clock, tmp_path):
    sm, app, clip, _ = clip_app
    assert sm.retention.run(now=clock.t + 3600)["evidence_deleted"] == 0
    assert sm.retention.run(now=clock.t + 25 * 3600)["evidence_deleted"] == 1
    assert os.listdir(tmp_path / "evidence") == []
    assert login(app, "teacher").get(f"/api/teacher/evidence/{clip['evidence_id']}").status_code == 404


def test_expired_clip_is_not_served_even_before_cleanup(clip_app, clock):
    sm, app, clip, _ = clip_app
    clock.advance(25 * 3600)
    assert login(app, "teacher").get(f"/api/teacher/evidence/{clip['evidence_id']}").status_code == 404


def test_orphan_files_are_removed_by_cleanup(tmp_path, clock):
    sm, _ = build(tmp_path, clock)
    store = sm.evidence.store
    name, _ = store.write("b" * 32, {"x": 1}, [(1.0, JPEG)])
    sm.evidence.cleanup_expired()
    assert name not in store.list_files()


def test_ring_buffer_is_bounded_and_sampled():
    buf = FrameRingBuffer(seconds=5.0, fps=2.0, max_frames=8)
    for i in range(100):
        buf.push(i * 0.1, JPEG)
    assert len(buf.frames) <= 8
    ts = [f[0] for f in buf.frames]
    assert ts[-1] - ts[0] <= 5.0 and all(b - a >= 0.5 - 1e-9 for a, b in zip(ts, ts[1:]))


def test_revoke_with_policy_deletes_clips(clip_app, tmp_path):
    sm, app, clip, _ = clip_app
    assert login(app, "phuhuynh").post("/api/consent/3/revoke", json={}).status_code == 200
    assert sm.repo.list_evidence(student_id=3) == [] and os.listdir(tmp_path / "evidence") == []


def test_clip_crop_is_used_when_available(tmp_path, clock):
    sm, _ = build(tmp_path, clock)
    grant_consent(sm, 3, face_recognition=True, event_evidence=True)
    seen = []
    sm.evidence.cropper = lambda jpeg, box: seen.append(box) or b"cropped"
    phone_episode(sm, clock)
    clip = sm.repo.list_evidence(student_id=3)[0]
    assert clip["cropped"] is True and seen
    assert {f for _, f in sm.evidence.store.read(clip["file_name"])["frames"]} == {b"cropped"}
