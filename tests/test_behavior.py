import pytest

from focusguard.behavior import AWAY, DROWSY, HEAD_AWAY, PHONE, BehaviorAnalyzer, Observation
from focusguard.evaluation.synthetic import behavior_observations
from focusguard import runtime_state as rs


def run(segments, duration=30.0, fps=15.0):
    an = BehaviorAnalyzer(1)
    started, ended, states = [], [], []
    for obs in behavior_observations(fps, duration, segments):
        u = an.update(obs)
        started += u.started
        ended += u.ended
        states.append(u.focus_state)
    ended += an.close(1_000_000.0 + duration)
    return started, ended, states


@pytest.mark.parametrize("fps", [5.0, 15.0, 30.0])
def test_blink_is_ignored(fps):
    started, ended, states = run([(5.0, 5.2, "BLINK"), (9.0, 9.3, "BLINK")], fps=fps)
    assert started == [] and ended == []
    assert rs.FOCUS_DROWSY not in states


@pytest.mark.parametrize("fps", [5.0, 15.0, 30.0])
def test_quick_glance_is_ignored(fps):
    started, _, _ = run([(5.0, 5.8, "GLANCE")], fps=fps)
    assert started == []


@pytest.mark.parametrize("fps", [5.0, 15.0, 30.0])
def test_short_phone_flash_is_ignored(fps):
    started, _, _ = run([(5.0, 5.2, "PHONE_FLASH")], fps=fps)
    assert started == []


@pytest.mark.parametrize("fps", [5.0, 15.0, 30.0])
def test_sustained_phone_creates_one_episode_with_real_duration(fps):
    _, ended, states = run([(5.0, 15.0, "PHONE")], fps=fps)
    phone = [e for e in ended if e.type == PHONE]
    assert len(phone) == 1
    ep = phone[0]
    assert abs(ep.start_time - 1_000_005.0) <= 1.0 / fps + 1e-6
    assert abs(ep.duration - 10.0) <= 2.0 / fps + 1e-6
    assert ep.confidence is not None and 0 < ep.confidence <= 1
    assert ep.student_id == 1 and ep.source == "synthetic"
    assert "detected_at" in ep.metadata
    assert rs.FOCUS_PHONE in states


def test_sustained_drowsy_and_head_away():
    _, ended, _ = run([(3.0, 8.0, "DROWSY"), (12.0, 18.0, "HEAD_AWAY")])
    kinds = sorted(e.type for e in ended)
    assert kinds == [DROWSY, HEAD_AWAY]


def test_short_occlusion_is_temporarily_not_visible_not_away():
    an = BehaviorAnalyzer(1)
    started, statuses = [], []
    for obs in behavior_observations(10.0, 20.0, [(5.0, 8.0, "OCCLUSION")]):
        u = an.update(obs)
        started += u.started
        statuses.append(u.visibility_status)
    assert rs.VIS_TEMPORARILY_NOT_VISIBLE in statuses
    assert rs.VIS_AWAY not in statuses
    assert started == []


def test_long_absence_is_away_once():
    an = BehaviorAnalyzer(1)
    started, ended = [], []
    for obs in behavior_observations(10.0, 60.0, [(5.0, 30.0, "AWAY")]):
        u = an.update(obs)
        started += u.started
        ended += u.ended
    away = [e for e in started if e.type == AWAY]
    assert len(away) == 1
    assert away[0].start_time == pytest.approx(1_000_005.0, abs=0.11)
    ended_away = [e for e in ended if e.type == AWAY]
    assert len(ended_away) == 1 and ended_away[0].duration == pytest.approx(25.0, abs=0.2)


def test_never_seen_student_is_unknown_not_away():
    an = BehaviorAnalyzer(1)
    for i in range(300):
        u = an.update(Observation(timestamp=1_000.0 + i * 0.1, visible=False))
    assert u.visibility_status == rs.VIS_NOT_SEEN
    assert u.focus_state == rs.FOCUS_UNKNOWN
    assert u.started == []


def test_unmeasured_signals_are_not_negative_evidence():
    an = BehaviorAnalyzer(1)
    for i in range(100):
        u = an.update(Observation(timestamp=1_000.0 + i * 0.1, visible=True, ear=None, yaw=None, pitch=None))
    assert u.focus_state == rs.FOCUS_FOCUSED and u.started == []


def _eyes(fps, duration, open_ear, closed=()):
    """Observations with a person-specific open-eye EAR and optional closures."""
    out = []
    for i in range(int(duration * fps)):
        t = i / fps
        ear = open_ear
        for a, b, v in closed:
            if a <= t < b:
                ear = v
        out.append(Observation(timestamp=1_000.0 + t, visible=True, face_visible=True, ear=ear, yaw=0.0, pitch=0.0))
    return out


def test_low_open_eye_ear_is_not_drowsy():
    """Regression (real webcam): a student's open eyes measured below the old
    fixed 0.22 threshold and produced drowsy episodes while blinking normally."""
    an = BehaviorAnalyzer(1)
    started = []
    blinks = [(t, t + 0.2, 0.08) for t in (5, 9, 13, 17, 21, 25)]
    for obs in _eyes(7.5, 30, 0.19, blinks):      # 7.5 FPS as measured on the real run
        started += an.update(obs).started
    assert [e.type for e in started] == []
    assert an.ear_baseline == pytest.approx(0.19) and an.ear_closed_threshold < 0.19


def test_low_open_eye_ear_still_detects_real_eye_closure():
    an = BehaviorAnalyzer(1)
    started = []
    for obs in _eyes(7.5, 30, 0.19, [(10, 14, 0.07)]):
        started += an.update(obs).started
    assert [e.type for e in started] == [DROWSY]


def test_long_closure_does_not_drag_baseline_down():
    an = BehaviorAnalyzer(1)
    kinds = []
    for obs in _eyes(10, 120, 0.30, [(10, 110, 0.10)]):   # eyes closed 100 s
        u = an.update(obs)
        kinds.append(u.focus_state)
    assert kinds[-120] == rs.FOCUS_DROWSY                    # still drowsy near the end of the closure
    assert an.ear_baseline == pytest.approx(0.30)


def test_no_drowsy_before_baseline_warm_up():
    an = BehaviorAnalyzer(1)
    started = []
    for obs in _eyes(10, 1.5, 0.05):                        # eyes "closed" from the first frame
        started += an.update(obs).started
    assert started == []
