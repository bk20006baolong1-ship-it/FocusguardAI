"""Context-aware interpretation (BTC feedback C): observed signal != pedagogical conclusion."""
import json

import pytest

from focusguard.behavior import Observation
from focusguard.policies import BehaviorInterpretationPolicy
from focusguard.policies.contexts import ACTIVITY_CONTEXTS, normalize_context
from focusguard.policies.interpretation import (EXPECTED_IN_CONTEXT, SIGNAL, DEFAULT_POLICY_PATH,
                                                validate_policy)
from focusguard.session_runtime import SessionRuntime

T0 = 1_000_000.0


class Sink:
    def __init__(self):
        self.events, self.segments = [], []

    def save_focus_event(self, row):
        self.events.append(row)

    def save_focus_snapshot(self, row):
        pass

    def save_session_student(self, row):
        pass

    def save_context_segment(self, row):
        self.segments.append(row)


def runtime(context=None, sink=None):
    return SessionRuntime(enrolled=[{"student_id": 1, "display_name": "A"}], started_at=T0,
                          class_id=1, class_session_id=7, sink=sink or Sink(), activity_context=context)


def drive(rt, seconds, fps=10.0, start=0.0, **obs):
    n = int(seconds * fps)
    for i in range(n):
        t = T0 + start + i / fps
        base = dict(ear=0.30, yaw=0.0, pitch=0.0, phone_confidence=None)
        base.update(obs)
        rt.process_frame(t, {1: Observation(timestamp=t, visible=True, face_visible=True, **base)})
    return T0 + start + seconds


def score(rt):
    return rt.states[1].focus_score


# ---------------------------------------------------------------- policy file
def test_policy_file_is_valid_and_marked_uncalibrated():
    data = json.load(open(DEFAULT_POLICY_PATH, encoding="utf-8"))
    validate_policy(data)
    assert data["calibration_status"] == "UNCALIBRATED_DEFAULT"
    assert set(data["contexts"]) <= set(ACTIVITY_CONTEXTS)


@pytest.mark.parametrize("bad", [
    {"policy_version": "x", "contexts": {"PARTY": {}}},
    {"policy_version": "x", "contexts": {"WRITING": {"HEAD_AWAY": {"penalty_multiplier": -1}}}},
    {"policy_version": "x", "contexts": {"WRITING": {"EMOTION": {}}}},
    {"policy_version": "x", "contexts": {"WRITING": {"HEAD_AWAY": {"alert": "yes"}}}},
    {"policy_version": "x", "contexts": {"WRITING": {"HEAD_AWAY": {"directions": {"SIDEWAYS": {}}}}}},
    {"contexts": {}},
])
def test_invalid_policies_are_rejected(bad):
    with pytest.raises(ValueError):
        validate_policy(bad)


def test_unknown_context_is_rejected():
    assert normalize_context(None) is None and normalize_context("writing") == "WRITING"
    with pytest.raises(ValueError):
        normalize_context("NAP_TIME")


def test_no_context_is_the_legacy_behaviour():
    p = BehaviorInterpretationPolicy()
    for beh in ("PHONE", "DROWSY", "HEAD_AWAY", "AWAY"):
        i = p.interpret(beh, None, "DOWN", 100)
        assert (i.interpreted_state, i.penalty_multiplier, i.alert) == (SIGNAL, 1.0, True)


def test_interpretation_keeps_observed_behavior_and_context_separate():
    i = BehaviorInterpretationPolicy().interpret("HEAD_AWAY", "WRITING", "DOWN", 3.0)
    d = i.to_dict()
    assert d["observed_behavior"] == "HEAD_AWAY" and d["context"] == "WRITING"
    assert d["interpreted_state"] == EXPECTED_IN_CONTEXT and d["penalty_multiplier"] == 0.0
    assert d["policy_version"] and d["label"]


# --------------------------------------------------------- runtime behaviour
def test_writing_head_down_is_not_penalised():
    legacy, writing = runtime(), runtime("WRITING")
    for rt in (legacy, writing):
        drive(rt, 5)
        drive(rt, 20, start=5, pitch=35.0)           # head down for 20 s
    assert score(legacy) < 85                          # old behaviour: HEAD_AWAY penalty
    assert score(writing) == pytest.approx(100.0)      # writing: expected, scored as focused
    assert writing.states[1].focus_state == "HEAD_AWAY"            # the signal itself is kept
    assert writing.states[1].interpretation["interpreted_state"] == EXPECTED_IN_CONTEXT


def test_writing_head_down_alert_is_suppressed_but_episode_recorded():
    sink = Sink()
    rt = runtime("WRITING", sink)
    end = drive(rt, 3)
    end = drive(rt, 6, start=3, pitch=35.0)
    view = rt.student_view(1, end)
    assert [b["alert"] for b in view["behaviors"]] == [False]
    starts = [log for log in rt.logs if log["type"] == "behavior_started"]
    assert starts and starts[0]["alert"] is False
    drive(rt, 3, start=9)
    ev = sink.events[-1]
    assert ev["type"] == "HEAD_AWAY" and ev["activity_context"] == "WRITING"
    assert ev["interpreted_state"] == EXPECTED_IN_CONTEXT and ev["penalty_multiplier"] == 0.0
    assert ev["metadata"]["direction"] == "DOWN" and ev["episode_uid"]


def test_group_discussion_head_turn_is_not_penalised():
    legacy, discussion = runtime(), runtime("GROUP_DISCUSSION")
    for rt in (legacy, discussion):
        drive(rt, 5)
        drive(rt, 20, start=5, yaw=45.0)
    assert score(legacy) < 85
    assert score(discussion) == pytest.approx(100.0)


def test_lecture_head_away_keeps_the_current_penalty():
    legacy, lecture = runtime(), runtime("LECTURE")
    for rt in (legacy, lecture):
        drive(rt, 5)
        drive(rt, 20, start=5, yaw=45.0)
    assert score(lecture) == pytest.approx(score(legacy))


@pytest.mark.parametrize("ctx", ["LECTURE", "WRITING", "GROUP_DISCUSSION", "EXAM", "SELF_STUDY"])
def test_phone_stays_active_and_penalised(ctx):
    legacy, rt = runtime(), runtime(ctx)
    for r in (legacy, rt):
        drive(r, 5)
        end = drive(r, 15, start=5, phone_confidence=0.8)
    assert rt.states[1].focus_state == "PHONE"
    assert score(rt) == pytest.approx(score(legacy))
    assert [b["alert"] for b in rt.student_view(1, end)["behaviors"]] == [True]


def test_exam_phone_is_high_severity():
    rt = runtime("EXAM")
    drive(rt, 3)
    end = drive(rt, 5, start=3, phone_confidence=0.9)
    assert rt.student_view(1, end)["behaviors"][0]["severity"] == "high"


def test_long_eye_closure_while_writing_escalates_to_review():
    rt = runtime("WRITING")
    drive(rt, 3)
    drive(rt, 25, start=3, ear=0.05)           # eyes look closed for 25 s while "writing"
    interp = rt.states[1].interpretation
    assert interp["escalated"] is True and interp["interpreted_state"] == SIGNAL
    assert any(log["type"] == "behavior_escalated" for log in rt.logs)


def test_context_switch_does_not_rescore_the_past_and_is_recorded():
    sink = Sink()
    rt = runtime("LECTURE", sink)
    drive(rt, 5)
    drive(rt, 10, start=5, yaw=45.0)            # penalised under LECTURE
    after_lecture = score(rt)
    rt.set_activity_context("GROUP_DISCUSSION", T0 + 15, set_by=2)
    drive(rt, 10, start=15, yaw=45.0)           # same signal, no penalty now
    assert score(rt) >= after_lecture - 0.2
    assert after_lecture < 100
    assert [s["context"] for s in rt.context_history] == ["LECTURE", "GROUP_DISCUSSION"]
    assert sink.segments[0]["context"] == "LECTURE" and sink.segments[0]["ended_at"] == T0 + 15
    rt.end(T0 + 26)
    assert [s["context"] for s in sink.segments] == ["LECTURE", "GROUP_DISCUSSION"]
    assert rt.class_view(T0 + 26)["activity_context"] == "GROUP_DISCUSSION"


def test_board_direction_calibration_offset():
    """Camera in a corner: looking at the board is a yaw offset, not HEAD_AWAY."""
    from focusguard.config import DEFAULT_CONFIG
    cfg = DEFAULT_CONFIG.with_behavior(yaw_center_deg=30.0)
    rt = SessionRuntime(enrolled=[{"student_id": 1}], started_at=T0, config=cfg)
    drive(rt, 15, yaw=32.0)
    assert rt.states[1].focus_state == "FOCUSED" and score(rt) == pytest.approx(100.0)
    drive(rt, 10, start=15, yaw=-20.0)
    assert rt.states[1].focus_state == "HEAD_AWAY"
