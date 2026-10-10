"""The real-camera harness verdict logic (pure; the hardware run itself is manual)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import real_camera_check as rc  # noqa: E402


def frame(t, step, tracks=(), **student):
    base = {"identity_status": "CONFIRMED", "active_track_id": 1, "visibility_status": "VISIBLE",
            "connection_status": "NOT_APPLICABLE", "focus_state": "FOCUSED", "focus_score": 100}
    base.update(student)
    return {"t": t, "step": step, "fps": 10, "camera_status": "ACTIVE",
            "tracks": list(tracks) or [{"track_id": 1, "status": "CONFIRMED", "student_id": 3}],
            "students": {"3": base, "4": {}}}


def test_identity_pass_and_duplicate_fail():
    log = {"frames": [frame(i * 0.1, "S1") for i in range(50)], "episodes": []}
    assert rc.verdict_identity(log)["status"] == "PASS"
    dup = [{"track_id": 1, "status": "CONFIRMED", "student_id": 3}, {"track_id": 2, "status": "CONFIRMED", "student_id": 3}]
    log["frames"][10] = frame(1.0, "S1", tracks=dup)
    assert rc.verdict_identity(log)["status"] == "FAIL"


def test_identity_score_reset_is_detected():
    frames = [frame(i * 0.1, "S1", focus_score=80) for i in range(20)] + [frame(2.0 + i * 0.1, "S1", focus_score=100) for i in range(5)]
    assert rc.verdict_identity({"frames": frames, "episodes": []})["score_resets"] == 1


def test_blink_fails_when_drowsy_episode_started():
    frames = [frame(i * 0.1, "S2") for i in range(100)]
    assert rc.verdict_no_episode({"frames": frames, "episodes": []}, "S2", "DROWSY")["status"] == "PASS"
    eps = [{"student_id": 3, "type": "DROWSY", "start_time": 3.0}]
    assert rc.verdict_no_episode({"frames": frames, "episodes": eps}, "S2", "DROWSY")["status"] == "FAIL"


def test_phone_requires_episode_duration_confidence_and_alert():
    frames = [frame(i * 0.1, "S4") for i in range(150)]
    ep = {"student_id": 3, "type": "PHONE", "start_time": 1.0, "duration": 12.0, "confidence": 0.7}
    logs = [{"type": "behavior_started", "episode": {"type": "PHONE"}}]
    assert rc.verdict_phone({"frames": frames, "episodes": [ep], "logs": logs})["status"] == "PASS"
    assert rc.verdict_phone({"frames": frames, "episodes": [ep], "logs": []})["status"] == "FAIL"
    assert rc.verdict_phone({"frames": frames, "episodes": [], "logs": logs})["status"] == "FAIL"


def test_leave_return_requires_single_away_and_recovery():
    before = [frame(i * 0.1, "S5a", focus_score=90) for i in range(20)]
    away = [frame(2 + i * 0.1, "S5b", tracks=[{"track_id": 9, "status": "UNKNOWN", "student_id": None}],
                  identity_status="LOST", visibility_status="TEMPORARILY_NOT_VISIBLE" if i < 100 else "AWAY",
                  focus_state="AWAY", focus_score=85) for i in range(180)]
    back = [frame(20 + i * 0.1, "S5c", active_track_id=7, focus_score=85) for i in range(50)]
    log = {"frames": before + away + back, "episodes": [{"student_id": 3, "type": "AWAY", "start_time": 2.0}]}
    v = rc.verdict_leave(log)
    assert v["status"] == "PASS" and v["track_ids_after"] == [7]
    log["episodes"].append({"student_id": 3, "type": "AWAY", "start_time": 10.0})
    assert rc.verdict_leave(log)["status"] == "FAIL"


def test_second_person_scenarios_are_user_action_required_when_not_run():
    v = rc.compute_verdicts({"frames": [], "episodes": []})
    assert v["6_crossing"]["status"] == "USER_ACTION_REQUIRED"
    assert v["7_unknown_person"]["status"] == "USER_ACTION_REQUIRED"
    assert v["1_single_identity"]["status"] == "NOT_RUN"


def test_open_camera_waits_for_permission_then_succeeds(monkeypatch):
    """macOS: first opens fail while the permission dialog is pending."""
    import types
    import numpy as np
    attempts = {"n": 0}

    class FakeCap:
        def __init__(self, idx):
            attempts["n"] += 1
            self.ok = attempts["n"] >= 3          # authorised on the third open

        def read(self):
            return (True, np.zeros((4, 4, 3), np.uint8)) if self.ok else (False, None)

        def release(self):
            pass

    monkeypatch.setitem(sys.modules, "cv2", types.SimpleNamespace(VideoCapture=FakeCap))
    monkeypatch.setattr(rc.time, "sleep", lambda s: None)
    cap = rc.open_camera(0, timeout=5.0)
    assert cap.ok and attempts["n"] == 3


def test_open_camera_gives_instructions_when_never_authorised(monkeypatch):
    import types
    import pytest

    class DeniedCap:
        def __init__(self, idx):
            pass

        def read(self):
            return False, None

        def release(self):
            pass

    clock = {"t": 0.0}

    def fake_time():
        clock["t"] += 0.5
        return clock["t"]

    monkeypatch.setitem(sys.modules, "cv2", types.SimpleNamespace(VideoCapture=DeniedCap))
    monkeypatch.setattr(rc.time, "sleep", lambda s: None)
    monkeypatch.setattr(rc.time, "time", fake_time)
    with pytest.raises(SystemExit) as exc:
        rc.open_camera(0, timeout=3.0)
    assert "Privacy & Security" in str(exc.value)
