import ast
import os

import pytest

from focusguard.config import FocusConfig
from focusguard.evaluation.synthetic import STANDARD_BEHAVIOR_SCRIPT, STANDARD_DURATION, behavior_observations
from focusguard.focus_engine import FocusEngine
from focusguard.session_runtime import SessionRuntime
from focusguard import runtime_state as rs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def final_score(fps, segments, duration, jitter=0.0):
    rt = SessionRuntime(enrolled=[{"student_id": 1, "display_name": "s"}], started_at=1_000_000.0)
    for obs in behavior_observations(fps, duration, segments, jitter=jitter):
        rt.process_frame(obs.timestamp, {1: obs})
    return rt.states[1].focus_score


@pytest.mark.parametrize("segments,duration", [
    ([(5.0, 25.0, "PHONE")], 40.0),
    ([(5.0, 15.0, "DROWSY"), (20.0, 30.0, "HEAD_AWAY")], 40.0),
    (STANDARD_BEHAVIOR_SCRIPT, STANDARD_DURATION),
])
def test_same_behavior_duration_gives_same_score_at_5_15_30_fps(segments, duration):
    scores = {fps: final_score(fps, segments, duration) for fps in (5.0, 15.0, 30.0)}
    assert max(scores.values()) - min(scores.values()) <= 1.5, scores


def test_score_is_invariant_to_timestamp_jitter():
    base = final_score(15.0, [(5.0, 25.0, "PHONE")], 40.0)
    jittered = final_score(15.0, [(5.0, 25.0, "PHONE")], 40.0, jitter=0.4)
    assert abs(base - jittered) <= 1.0


def test_penalty_and_recovery_are_per_second():
    cfg = FocusConfig()
    eng = FocusEngine(cfg)
    st = rs.StudentRuntimeState(student_id=1)
    eng.begin(st, rs.FOCUS_FOCUSED, 0.0)
    t = 0.0
    for _ in range(100):           # 10 s phone at 10 Hz
        t += 0.1
        eng.advance(st, rs.FOCUS_PHONE, t)
    assert st.focus_score == pytest.approx(100 - 10 * cfg.penalty_per_second["PHONE"], abs=1e-6)
    before = st.focus_score
    for _ in range(40):            # 4 s focused recovers 4 * rate
        t += 0.1
        eng.advance(st, rs.FOCUS_FOCUSED, t)
    assert st.focus_score == pytest.approx(before + 4 * cfg.recovery_per_second, abs=1e-6)


def test_score_is_clamped_and_long_gaps_are_bounded():
    eng = FocusEngine()
    st = rs.StudentRuntimeState(student_id=1)
    eng.begin(st, rs.FOCUS_FOCUSED, 0.0)
    eng.advance(st, rs.FOCUS_PHONE, 3600.0)   # one hour stall
    assert st.focus_score == pytest.approx(100 - 2.0 * FocusConfig().max_step_seconds)


def test_unmeasured_states_do_not_change_score():
    eng = FocusEngine()
    st = rs.StudentRuntimeState(student_id=1)
    assert eng.advance(st, rs.FOCUS_UNKNOWN, 10.0) is None
    eng.begin(st, rs.FOCUS_FOCUSED, 10.0)
    eng.advance(st, rs.FOCUS_UNKNOWN, 20.0)
    eng.advance(st, rs.FOCUS_TEMPORARILY_NOT_VISIBLE, 21.0)
    assert st.focus_score == 100.0


def test_camera_pipelines_do_not_compute_scores():
    """ONE focus engine: camera modules must not mutate focus scores themselves."""
    for name in ("camera_ai.py", "classroom_ai.py"):
        tree = ast.parse(open(os.path.join(ROOT, name), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for tgt in targets:
                    text = ast.unparse(tgt)
                    assert "focus_score" not in text, f"{name} assigns {text}"
            if isinstance(node, ast.Attribute):
                assert node.attr not in ("update_score", "mock_students"), f"{name} uses {node.attr}"
