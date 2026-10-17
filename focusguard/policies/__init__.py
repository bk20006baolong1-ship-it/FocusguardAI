"""Pedagogical interpretation policies.

The vision pipeline only reports *observed* behaviour signals (PHONE, DROWSY,
HEAD_AWAY, AWAY). Whether such a signal means "distracted" depends on what the
class is doing, which only the teacher knows. This package keeps that decision
out of the detectors:

    observed_behavior  (BehaviorAnalyzer, unchanged)
  + activity_context   (chosen by the teacher/tester, never inferred by AI)
  = interpreted_state  (BehaviorInterpretationPolicy)
  + penalty_policy     (multiplier applied by the single FocusEngine)

An AI signal is not a pedagogical conclusion; the teacher reviews events.
"""
from .contexts import ACTIVITY_CONTEXTS, CONTEXT_LABELS_VI, is_valid_context  # noqa: F401
from .interpretation import (BehaviorInterpretationPolicy, Interpretation, load_policy,  # noqa: F401
                             DEFAULT_POLICY_PATH)
