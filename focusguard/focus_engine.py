"""The ONE authoritative focus-score engine.

Camera pipelines only produce observations. The score is integrated over real
elapsed time from the dominant behaviour state, so it does not depend on FPS:

    score(t + dt) = clamp(score(t) + rate(state) * dt)

where ``rate`` is ``+recovery_per_second`` while FOCUSED and
``-penalty_per_second[state]`` for sustained behaviours. States without a
measurement (UNKNOWN, NO_CAMERA_SIGNAL) freeze the score instead of inventing
one. ``TEMPORARILY_NOT_VISIBLE`` also freezes it (penalty 0) so an occlusion is
not treated as distraction.

Usage per observation (see SessionRuntime):

    engine.advance(state, state.focus_state, now)   # integrate the interval that just elapsed
    ... behaviour analyzer decides the new focus_state ...
    engine.begin(state, new_focus_state, now)        # start scoring on first measurement
"""
from typing import Optional

from .config import DEFAULT_CONFIG, FocusConfig
from . import runtime_state as rs

MEASURED_STATES = frozenset({
    rs.FOCUS_FOCUSED, rs.FOCUS_PHONE, rs.FOCUS_DROWSY, rs.FOCUS_HEAD_AWAY,
    rs.FOCUS_TEMPORARILY_NOT_VISIBLE, rs.FOCUS_AWAY,
})


class FocusEngine:
    def __init__(self, config: FocusConfig = DEFAULT_CONFIG.focus):
        self.config = config

    def rate(self, focus_state: str) -> float:
        if focus_state == rs.FOCUS_FOCUSED:
            return self.config.recovery_per_second
        return -float(self.config.penalty_per_second.get(focus_state, 0.0))

    def advance(self, state: rs.StudentRuntimeState, focus_state: str, now: float) -> Optional[float]:
        """Integrate ``state.focus_score`` over ``[state.last_update_at, now]``.

        ``focus_state`` must be the state that was in force during that
        interval (i.e. the state *before* the new observation is applied).
        """
        c = self.config
        last = state.last_update_at
        state.last_update_at = now
        if state.focus_score is None or last is None or focus_state not in MEASURED_STATES:
            return state.focus_score

        dt = min(max(0.0, now - last), c.max_step_seconds)
        if dt <= 0:
            return state.focus_score

        before = state.focus_score
        after = max(c.min_score, min(c.max_score, before + self.rate(focus_state) * dt))
        state.focus_score = after

        if focus_state != rs.FOCUS_TEMPORARILY_NOT_VISIBLE:
            state.measured_seconds += dt
            state.score_integral += 0.5 * (before + after) * dt
            if focus_state == rs.FOCUS_FOCUSED:
                state.focused_seconds += dt
        return after

    def begin(self, state: rs.StudentRuntimeState, focus_state: str, now: float):
        """Start scoring the first time a student is actually measured."""
        if state.focus_score is None and focus_state in MEASURED_STATES:
            state.focus_score = self.config.initial_score
            state.last_update_at = now
