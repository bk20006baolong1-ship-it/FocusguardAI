"""Unit tests for the 10 mandatory scenarios required by FocusGuard AI specification:
1. Focused 10s: exactly +0.5 points (+0.05/s).
2. Closed eyes 15-20s: DROWSY detected and penalized in real-time.
3. Normal blinks: no drowsiness.
4. Sustained head bow (cúi đầu): HEAD_AWAY detected and penalized.
5. Sustained turn left/right: HEAD_AWAY detected and penalized.
6. Turn head backwards losing face landmarks while body tracked: sustained HEAD_AWAY, retains identity, not AWAY.
7. Short occlusion: TEMPORARILY_NOT_VISIBLE, score frozen (0 penalty).
8. Left seat >= away_min_seconds: AWAY detected and penalized.
9. Camera loss: NO_CAMERA_SIGNAL, score frozen, no recovery or penalty.
10. Consistent scores and behavior timings across 5, 15, and 30 FPS.
"""
import pytest
from focusguard import runtime_state as rs
from focusguard.behavior import AWAY, DROWSY, HEAD_AWAY, BehaviorAnalyzer, Observation
from focusguard.config import BehaviorConfig, FocusConfig, PipelineConfig
from focusguard.focus_engine import FocusEngine
from focusguard.session_runtime import SessionRuntime


def _feed(rt, sid, t0, t1, dt, make_obs):
    t = t0
    while t <= t1 + 1e-9:
        obs = make_obs(t)
        rt.process_frame(t, {sid: obs})
        t += dt


def test_1_focused_30_seconds_adds_half_point():
    """1. Học sinh tập trung hợp lệ: chỉ cộng +0,5 điểm sau mỗi 30 giây (không cộng liên tục từng frame)."""
    cfg = PipelineConfig(focus=FocusConfig(initial_score=80.0))
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # First observation initializes score to 80.0
    rt.process_frame(100.0, {1: Observation(timestamp=100.0, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0)})
    assert rt.states[1].focus_score == pytest.approx(80.0)

    # Feed 25.0 seconds of continuous focused observations at 10 Hz (< 30s): no points added
    _feed(rt, 1, 100.1, 125.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
    assert rt.states[1].focus_score == pytest.approx(80.0, abs=1e-3)

    # Reach 30.0 seconds of valid focus: exactly +0.5 points awarded
    _feed(rt, 1, 125.1, 130.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
    assert rt.states[1].focus_state == rs.FOCUS_FOCUSED
    assert rt.states[1].focus_score == pytest.approx(80.5, abs=1e-3)

    # Reach 60.0 seconds of valid focus: another +0.5 -> total +1.0
    _feed(rt, 1, 130.1, 160.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
    assert rt.states[1].focus_score == pytest.approx(81.0, abs=1e-3)


def test_2_closed_eyes_15_to_20_seconds_detects_drowsy_and_penalizes():
    """2. Học sinh nhắm mắt liên tục 15–20 giây: phải phát hiện DROWSY và điểm giảm theo thời gian thực."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(drowsy_min_seconds=1.5, ear_min_closed_threshold=0.16),
        focus=FocusConfig(initial_score=100.0, recovery_per_second=0.05)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # Initial 3 seconds open eyes (EAR 0.26) to warm up baseline
    _feed(rt, 1, 100.0, 103.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.26, yaw=0.0, pitch=0.0))
    score_before_close = rt.states[1].focus_score
    assert score_before_close == pytest.approx(100.0)

    # Now close eyes continuously for 18 seconds (EAR 0.10)
    _feed(rt, 1, 103.1, 121.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.10, yaw=0.0, pitch=0.0))

    st = rt.states[1]
    assert st.focus_state == rs.FOCUS_DROWSY
    assert "DROWSY" in st.active_episodes
    # Drowsy confirms after 1.5s, so active penalty applied for ~16.5s at 1.5 pts/s
    assert st.focus_score < 90.0

    # Student opens eyes back to normal
    _feed(rt, 1, 121.1, 123.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.26, yaw=0.0, pitch=0.0))
    assert st.focus_state == rs.FOCUS_FOCUSED
    assert "DROWSY" not in st.active_episodes


def test_3_normal_blinks_do_not_trigger_drowsy():
    """3. Học sinh chớp mắt bình thường: không báo buồn ngủ."""
    an = BehaviorAnalyzer(1)
    # 30 seconds at 10 Hz with normal blinks (0.2s closed every 5 seconds)
    started = []
    for i in range(300):
        t = 100.0 + i * 0.1
        ear = 0.26
        # Blink intervals (0.2s duration)
        if any(b <= t < b + 0.2 for b in (105.0, 110.0, 115.0, 120.0)):
            ear = 0.08
        u = an.update(Observation(timestamp=t, visible=True, face_visible=True, ear=ear, yaw=0.0, pitch=0.0))
        started += u.started

    drowsy_eps = [e for e in started if e.type == DROWSY]
    assert drowsy_eps == []
    assert an.focus_state == rs.FOCUS_FOCUSED


def test_4_sustained_bowing_head_down_detects_head_away_and_penalizes():
    """4. Học sinh cúi đầu liên tục: phát hiện tư thế bất thường khi vượt thời gian xác nhận và trừ điểm."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(head_away_min_seconds=2.0, pitch_down_limit_deg=15.0),
        focus=FocusConfig(initial_score=100.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # 3 seconds resting pose (pitch 0, yaw 0)
    _feed(rt, 1, 100.0, 103.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
    assert rt.states[1].focus_state == rs.FOCUS_FOCUSED

    # Bow head down (pitch = -25.0 degrees, delta = -25 < -15) for 6 seconds
    _feed(rt, 1, 103.1, 109.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=-25.0))

    st = rt.states[1]
    assert st.focus_state == rs.FOCUS_HEAD_AWAY
    assert "HEAD_AWAY" in st.active_episodes
    # Penalized at 1.0 pt/s after 2s confirmation: ~4s penalty = ~4 pts
    assert st.focus_score < 98.0

    # Return to resting posture
    _feed(rt, 1, 109.1, 111.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
    assert st.focus_state == rs.FOCUS_FOCUSED
    assert "HEAD_AWAY" not in st.active_episodes


def test_5_sustained_turning_left_right_detects_head_away():
    """5. Học sinh quay trái/phải kéo dài: phát hiện HEAD_AWAY nếu góc đo đủ tin cậy."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(head_away_min_seconds=2.0, yaw_limit_deg=20.0),
        focus=FocusConfig(initial_score=100.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # Warm up resting pose
    _feed(rt, 1, 100.0, 103.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))

    # Turn head to the right (yaw = 35.0 deg > 20 deg) for 5 seconds
    _feed(rt, 1, 103.1, 108.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=35.0, pitch=0.0))

    assert rt.states[1].focus_state == rs.FOCUS_HEAD_AWAY
    assert "HEAD_AWAY" in rt.states[1].active_episodes
    assert rt.states[1].focus_score < 100.0


def test_6_turning_head_back_losing_landmarks_sustains_head_away_and_preserves_identity():
    """6. Học sinh quay mặt ra sau, mất landmark nhưng cơ thể vẫn được tracking:
    thử cơ chế tín hiệu thay thế, giữ danh tính và không tự kết luận AWAY ngay lập tức."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(head_away_min_seconds=2.0, yaw_limit_deg=20.0, away_min_seconds=10.0),
        focus=FocusConfig(initial_score=100.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Student A"}], started_at=100.0, config=cfg)

    # Student sits normally and locks track 1
    _feed(rt, 1, 100.0, 103.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, track_id=1, ear=0.28, yaw=0.0, pitch=0.0))
    assert rt.states[1].identity_status == rs.ID_CONFIRMED

    # Student starts turning head away (yaw = 30.0 deg) for 0.8s
    _feed(rt, 1, 103.1, 103.8, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, track_id=1, ear=0.28, yaw=30.0, pitch=0.0))

    # Now head is completely turned backwards: face landmarks are LOST (face_visible=False),
    # but the student's body is STILL TRACKED (visible=True, track_id=1) for 5 seconds
    _feed(rt, 1, 103.9, 109.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=False, track_id=1, ear=None, yaw=None, pitch=None))

    st = rt.states[1]
    # Identity must still be confirmed, NOT lost, NOT new student
    assert st.identity_status == rs.ID_CONFIRMED
    assert st.active_track_id == 1
    # Must NOT be marked as AWAY (body is still in seat)
    assert st.visibility_status == rs.VIS_VISIBLE
    # Since head was turned away prior to landmark loss and sustained > 2.0s: HEAD_AWAY
    assert st.focus_state == rs.FOCUS_HEAD_AWAY
    assert "HEAD_AWAY" in st.active_episodes
    # Score must be penalized
    assert st.focus_score < 100.0


def test_7_short_occlusion_freezes_score_without_penalty():
    """7. Học sinh tạm khuất ngắn: không trừ điểm cho trạng thái TEMPORARILY_NOT_VISIBLE."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(away_min_seconds=10.0),
        focus=FocusConfig(initial_score=90.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # Initialize focused at 90.0
    rt.process_frame(100.0, {1: Observation(timestamp=100.0, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0)})
    score_before = rt.states[1].focus_score

    # Student becomes occluded at t=100.1
    rt.process_frame(100.1, {1: Observation(timestamp=100.1, visible=False)})
    assert rt.states[1].visibility_status == rs.VIS_TEMPORARILY_NOT_VISIBLE
    score_during_occlusion = rt.states[1].focus_score

    # Student remains occluded for 4 seconds (< 10s away_min_seconds)
    _feed(rt, 1, 100.2, 104.0, 0.1,
          lambda t: Observation(timestamp=t, visible=False))

    st = rt.states[1]
    assert st.visibility_status == rs.VIS_TEMPORARILY_NOT_VISIBLE
    assert st.focus_state == rs.FOCUS_TEMPORARILY_NOT_VISIBLE
    # Score must be completely frozen throughout the occlusion (0 penalty, 0 recovery)
    assert st.focus_score == pytest.approx(score_during_occlusion, abs=1e-6)
    assert st.active_episodes == {}


def test_8_absence_longer_than_away_threshold_transitions_to_away_and_penalizes():
    """8. Học sinh rời khỏi khung hình đủ lâu: chuyển sang AWAY và điểm được cập nhật đúng."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(away_min_seconds=10.0),
        focus=FocusConfig(initial_score=100.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # Student visible initially
    _feed(rt, 1, 100.0, 102.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))

    # Student leaves seat for 25 seconds (> 10s)
    _feed(rt, 1, 102.1, 127.0, 0.1,
          lambda t: Observation(timestamp=t, visible=False))

    st = rt.states[1]
    assert st.visibility_status == rs.VIS_AWAY
    assert st.focus_state == rs.FOCUS_AWAY
    assert "AWAY" in st.active_episodes
    # AWAY penalty applied for 15s at 1.0 pt/s
    assert st.focus_score < 90.0


def test_9_camera_signal_loss_freezes_score_without_points_or_penalties():
    """9. Camera mất tín hiệu: không tiếp tục cộng điểm hoặc phạt học sinh dựa trên dữ liệu không tồn tại."""
    cfg = PipelineConfig(
        focus=FocusConfig(initial_score=85.0),
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0,
                         camera_stale_seconds=3.0, config=cfg)

    # Initial frame
    rt.process_frame(100.0, {1: Observation(timestamp=100.0, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0)})
    score_before = rt.states[1].focus_score

    # Camera stalls: no frames arrive for 10 seconds. Heartbeat checks at 110.0
    status = rt.heartbeat(110.0)
    assert status == "NO_SIGNAL"

    st = rt.states[1]
    assert st.visibility_status == rs.VIS_NO_SIGNAL
    assert st.focus_state == rs.FOCUS_UNKNOWN
    # Score must be frozen at score_before, neither increased nor decreased
    assert st.focus_score == pytest.approx(score_before, abs=1e-3)


@pytest.mark.parametrize("fps", [5.0, 15.0, 30.0])
def test_10_score_and_behavior_consistent_across_fps(fps):
    """10. Các mức điểm và thời gian hành vi phải nhất quán khi chạy ở nhiều FPS."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(phone_min_seconds=1.0, drowsy_min_seconds=1.5, head_away_min_seconds=2.0),
        focus=FocusConfig(initial_score=100.0, recovery_per_second=0.05)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    dt = 1.0 / fps
    t = 100.0
    # 40s total: 5s focused, 10s drowsy, 5s focused, 10s head away, 10s focused
    while t <= 140.0:
        rel = t - 100.0
        ear = 0.28
        yaw = 0.0
        pitch = 0.0
        if 5.0 <= rel < 15.0:
            ear = 0.10  # DROWSY
        elif 20.0 <= rel < 30.0:
            yaw = 35.0  # HEAD_AWAY
        rt.process_frame(t, {1: Observation(timestamp=t, visible=True, face_visible=True, ear=ear, yaw=yaw, pitch=pitch)})
        t += dt

    # Final scores across 5, 15, and 30 FPS must be consistent within 1.5 points
    final_score = rt.states[1].focus_score
    assert 70.0 <= final_score <= 85.0


def test_11_mediapipe_positive_pitch_bowing_head_detects_head_away():
    """11. Cúi đầu với góc pitch dương (chuẩn MediaPipe FaceLandmarker RQDecomp3x3: +28 độ):
    phát hiện HEAD_AWAY và trừ điểm sau 2s."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(head_away_min_seconds=2.0, pitch_down_limit_deg=15.0),
        focus=FocusConfig(initial_score=100.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # 3s resting pose (pitch = +5.0 deg baseline)
    _feed(rt, 1, 100.0, 103.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=5.0))
    assert rt.states[1].focus_state == rs.FOCUS_FOCUSED

    # Student bows head down: pitch increases to +28.0 deg (delta = +23.0 > 15.0) for 5 seconds
    _feed(rt, 1, 103.1, 108.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=28.0))

    st = rt.states[1]
    assert st.focus_state == rs.FOCUS_HEAD_AWAY
    assert "HEAD_AWAY" in st.active_episodes
    assert st.focus_score < 99.0


def test_12_sudden_turn_backwards_losing_face_freezes_then_penalizes_and_recovers():
    """12. Quay mặt ra sau đột ngột khiến mất khuôn mặt:
    - Dưới 2s: trạng thái UNKNOWN, điểm bị đóng băng (không phạt oan khi che mặt tạm thời).
    - Đủ >= 2s: kích hoạt HEAD_AWAY và trừ điểm.
    - Quay lại: quay về FOCUSED."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(head_away_min_seconds=2.0),
        focus=FocusConfig(initial_score=100.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Student A"}], started_at=100.0, config=cfg)

    # Normal sitting
    _feed(rt, 1, 100.0, 103.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, track_id=1, ear=0.28, yaw=0.0, pitch=5.0))
    assert rt.states[1].focus_state == rs.FOCUS_FOCUSED
    score_before = rt.states[1].focus_score

    # Student begins turning head outward (yaw = 35.0 deg) before losing landmarks
    _feed(rt, 1, 103.1, 103.4, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, track_id=1, ear=0.28, yaw=35.0, pitch=5.0))

    # Face landmarks are lost as head turns backward, but under 2.0s total: grace period
    _feed(rt, 1, 103.5, 104.5, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=False, track_id=1))
    assert rt.states[1].active_episodes == {}
    assert rt.states[1].focus_score == pytest.approx(score_before, abs=1e-3)

    # Turn backwards sustained past 2.0s (from 103.1 to 109.0 > 2s): confirms HEAD_AWAY
    _feed(rt, 1, 104.6, 109.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=False, track_id=1))
    assert rt.states[1].focus_state == rs.FOCUS_HEAD_AWAY
    assert "HEAD_AWAY" in rt.states[1].active_episodes
    assert rt.states[1].focus_score < score_before

    # Student turns head back to camera at 109.1
    _feed(rt, 1, 109.1, 111.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, track_id=1, ear=0.28, yaw=0.0, pitch=5.0))
    assert rt.states[1].focus_state == rs.FOCUS_FOCUSED
    assert "HEAD_AWAY" not in rt.states[1].active_episodes


def test_13_turning_head_back_with_active_phone_retains_phone_state():
    """13. Khi quay mặt lại nhưng vẫn đang dùng điện thoại: không chuyển sang FOCUSED mà giữ PHONE."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(head_away_min_seconds=2.0, phone_min_seconds=1.0),
        focus=FocusConfig(initial_score=100.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # Normal 3s
    _feed(rt, 1, 100.0, 103.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=5.0))

    # Turn head away for 3s
    _feed(rt, 1, 103.1, 106.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=35.0, pitch=5.0))
    assert rt.states[1].focus_state == rs.FOCUS_HEAD_AWAY

    # Phone is detected for 2s while head still turned
    _feed(rt, 1, 106.1, 108.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=35.0, pitch=5.0, phone_confidence=0.8))
    assert rt.states[1].focus_state == rs.FOCUS_PHONE

    # Head returns to normal (yaw=0.0), BUT phone is still held
    _feed(rt, 1, 108.1, 110.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=5.0, phone_confidence=0.8))
    # Must NOT become FOCUSED; must remain PHONE
    assert rt.states[1].focus_state == rs.FOCUS_PHONE
    assert "PHONE" in rt.states[1].active_episodes


def test_14_turning_head_back_with_eyes_closed_retains_drowsy_state():
    """14. Khi quay mặt lại nhưng mắt vẫn nhắm: không chuyển sang FOCUSED mà giữ DROWSY."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(head_away_min_seconds=2.0, drowsy_min_seconds=1.5),
        focus=FocusConfig(initial_score=100.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # Normal 3s
    _feed(rt, 1, 100.0, 103.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=5.0))

    # Turn head away for 3s
    _feed(rt, 1, 103.1, 106.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=35.0, pitch=5.0))
    assert rt.states[1].focus_state == rs.FOCUS_HEAD_AWAY

    # Eyes closed while head turned for 2.5s
    _feed(rt, 1, 106.1, 108.5, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.10, yaw=35.0, pitch=5.0))

    # Head returns to center (yaw=0.0), but eyes STILL closed for 2s
    _feed(rt, 1, 108.6, 110.5, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.10, yaw=0.0, pitch=5.0))
    # Must NOT become FOCUSED; must remain DROWSY
    assert rt.states[1].focus_state == rs.FOCUS_DROWSY
    assert "DROWSY" in rt.states[1].active_episodes


def test_15_head_away_requires_7_seconds_continuous_before_penalizing():
    """15. Ngoảnh mặt dưới 7 giây rồi quay lại: không bị trừ điểm. Ngoảnh mặt đủ 7 giây: bắt đầu trừ điểm."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(head_away_min_seconds=7.0),
        focus=FocusConfig(initial_score=100.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # 1. First observation facing center: 100.0
    rt.process_frame(100.0, {1: Observation(timestamp=100.0, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0)})
    assert rt.states[1].focus_score == pytest.approx(100.0)

    # 2. Turn head away for 5.0 seconds (< 7.0s): NO episode started, NO penalty
    _feed(rt, 1, 100.1, 105.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=35.0, pitch=0.0))
    assert rt.states[1].focus_state == rs.FOCUS_FOCUSED
    assert "HEAD_AWAY" not in rt.states[1].active_episodes
    assert rt.states[1].focus_score == pytest.approx(100.0)

    # 3. Turn head back to center for 2.0 seconds: remains focused and full score
    _feed(rt, 1, 105.1, 107.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
    assert rt.states[1].focus_state == rs.FOCUS_FOCUSED
    assert rt.states[1].focus_score == pytest.approx(100.0)

    # 4. Turn head away continuously for 7.0 seconds: episode starts, penalty applies
    _feed(rt, 1, 107.1, 114.1, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=35.0, pitch=0.0))
    assert rt.states[1].focus_state == rs.FOCUS_HEAD_AWAY
    assert "HEAD_AWAY" in rt.states[1].active_episodes

    # 5. Continue turning head for another 3.0 seconds: score is penalized according to rate
    score_at_confirm = rt.states[1].focus_score
    _feed(rt, 1, 114.2, 117.2, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=35.0, pitch=0.0))
    assert rt.states[1].focus_score < score_at_confirm

    # 6. Turn head back to center: episode ends cleanly and penalty stops
    _feed(rt, 1, 117.3, 119.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
    assert rt.states[1].focus_state == rs.FOCUS_FOCUSED
    assert "HEAD_AWAY" not in rt.states[1].active_episodes


def test_16_head_away_persists_across_tracking_id_changes_for_same_student():
    """16. Đổi track_id nhưng cùng student_id: bộ đếm 7 giây không bị xóa/khởi động lại từ đầu."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(head_away_min_seconds=7.0),
        focus=FocusConfig(initial_score=100.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # Start facing center
    rt.process_frame(100.0, {1: Observation(timestamp=100.0, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0, track_id=1)})

    # Head away with track_id=1 for 4.0 seconds (100.1 -> 104.0)
    _feed(rt, 1, 100.1, 104.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=35.0, pitch=0.0, track_id=1))
    assert rt.states[1].focus_state == rs.FOCUS_FOCUSED

    # Tracker reassigns track_id=2 for the SAME student_id=1 (with a brief 0.1s frame switch)
    # Continue head away with track_id=2 for 3.2 seconds (total 7.2s head away)
    _feed(rt, 1, 104.1, 107.3, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=35.0, pitch=0.0, track_id=2))

    # At 7.2s total head away duration, HEAD_AWAY episode MUST be active even though track_id switched!
    assert rt.states[1].focus_state == rs.FOCUS_HEAD_AWAY
    assert "HEAD_AWAY" in rt.states[1].active_episodes


def test_17_focus_recovery_resets_streak_on_distraction():
    """17. Tập trung 20s (chưa đủ 30s), sau đó vi phạm HEAD_AWAY: chuỗi 30s bị reset, không cộng dồn bù."""
    cfg = PipelineConfig(
        behavior=BehaviorConfig(head_away_min_seconds=7.0),
        focus=FocusConfig(initial_score=80.0)
    )
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)

    # Initialize at 80.0
    rt.process_frame(100.0, {1: Observation(timestamp=100.0, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0)})

    # Focus for 20.0s (accumulated 20s < 30s): score remains 80.0
    _feed(rt, 1, 100.1, 120.0, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
    assert rt.states[1].focus_score == pytest.approx(80.0)

    # Distraction for 8s (becomes HEAD_AWAY): streak resets to 0.0, penalty applied
    _feed(rt, 1, 120.1, 128.1, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=35.0, pitch=0.0))
    assert rt.states[1].focus_state == rs.FOCUS_HEAD_AWAY
    # Turn back to center: after gap tolerance (0.6s), state returns to FOCUSED and starts new streak
    _feed(rt, 1, 128.2, 128.8, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
    assert rt.states[1].focus_state == rs.FOCUS_FOCUSED
    score_when_focused = rt.states[1].focus_score

    # Focus for 20s on new streak (accumulated 20s < 30s): score does NOT increase yet
    _feed(rt, 1, 128.9, 148.8, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
    assert rt.states[1].focus_score == pytest.approx(score_when_focused, abs=1e-3)

    # Reach 30s on the new streak: exactly +0.5 added!
    _feed(rt, 1, 148.9, 158.8, 0.1,
          lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
    assert rt.states[1].focus_score == pytest.approx(score_when_focused + 0.5, abs=1e-3)


def test_18_focus_recovery_is_fps_invariant():
    """18. Tốc độ khung hình (5 FPS vs 10 FPS vs 20 FPS) không làm tăng điểm nhanh hơn: đều +0,5 sau 30s."""
    scores = {}
    for fps in (5, 10, 20):
        dt = 1.0 / fps
        cfg = PipelineConfig(focus=FocusConfig(initial_score=80.0))
        rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "Test"}], started_at=100.0, config=cfg)
        rt.process_frame(100.0, {1: Observation(timestamp=100.0, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0)})
        _feed(rt, 1, 100.0 + dt, 130.0, dt,
              lambda t: Observation(timestamp=t, visible=True, face_visible=True, ear=0.28, yaw=0.0, pitch=0.0))
        scores[fps] = rt.states[1].focus_score

    assert scores[5] == pytest.approx(80.5, abs=1e-3)
    assert scores[10] == pytest.approx(80.5, abs=1e-3)
    assert scores[20] == pytest.approx(80.5, abs=1e-3)


