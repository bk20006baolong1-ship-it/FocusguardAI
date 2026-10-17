"""BehaviorInterpretationPolicy: observed behaviour + teacher-chosen context.

The policy never changes what was observed. It only answers, for a sustained
behaviour signal in a given activity context:

* ``interpreted_state``  SIGNAL | REDUCED_IN_CONTEXT | AMBIGUOUS_IN_CONTEXT |
                          EXPECTED_IN_CONTEXT | NOT_A_SIGNAL
* ``penalty_multiplier`` applied by the single FocusEngine to the configured
                          penalty rate (0 = no penalty)
* ``treat_as_focused``   EXPECTED_IN_CONTEXT behaviour scores like FOCUSED
                          (the student is doing what the activity asks)
* ``alert``              whether the live "Cần chú ý" list should show it
* ``severity``           ``normal`` | ``high`` (e.g. PHONE during EXAM)

With no context selected the result is exactly the legacy behaviour:
multiplier 1.0, alert on, interpretation SIGNAL.
"""
import json
import os
from dataclasses import asdict, dataclass
from typing import Optional

from .contexts import ACTIVITY_CONTEXTS

DEFAULT_POLICY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "behavior_policy.json")

SIGNAL = "SIGNAL"
REDUCED_IN_CONTEXT = "REDUCED_IN_CONTEXT"
AMBIGUOUS_IN_CONTEXT = "AMBIGUOUS_IN_CONTEXT"
EXPECTED_IN_CONTEXT = "EXPECTED_IN_CONTEXT"
NOT_A_SIGNAL = "NOT_A_SIGNAL"
INTERPRETATIONS = (SIGNAL, REDUCED_IN_CONTEXT, AMBIGUOUS_IN_CONTEXT, EXPECTED_IN_CONTEXT)

INTERPRETATION_LABELS_VI = {
    SIGNAL: "AI phát hiện tín hiệu",
    REDUCED_IN_CONTEXT: "Tín hiệu giảm mức theo hoạt động",
    AMBIGUOUS_IN_CONTEXT: "Tín hiệu chưa rõ trong hoạt động này",
    EXPECTED_IN_CONTEXT: "Phù hợp với hoạt động",
    NOT_A_SIGNAL: "Không có tín hiệu",
}

POLICY_BEHAVIORS = ("PHONE", "DROWSY", "HEAD_AWAY", "AWAY")
DIRECTIONS = ("UP", "DOWN", "LEFT", "RIGHT")
SEVERITIES = ("normal", "high")
_RULE_KEYS = {"penalty_multiplier", "alert", "interpretation", "severity", "escalate_after_seconds", "directions"}
MAX_MULTIPLIER = 3.0


@dataclass(frozen=True)
class Interpretation:
    observed_behavior: Optional[str]
    context: Optional[str]
    interpreted_state: str
    penalty_multiplier: float
    alert: bool
    severity: str = "normal"
    treat_as_focused: bool = False
    escalated: bool = False
    direction: Optional[str] = None
    policy_version: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["label"] = INTERPRETATION_LABELS_VI.get(self.interpreted_state, self.interpreted_state)
        return d


def _validate_rule(rule, where):
    if not isinstance(rule, dict):
        raise ValueError(f"{where}: rule must be an object")
    unknown = set(rule) - _RULE_KEYS
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    if "penalty_multiplier" in rule:
        m = rule["penalty_multiplier"]
        if not isinstance(m, (int, float)) or isinstance(m, bool) or not 0.0 <= float(m) <= MAX_MULTIPLIER:
            raise ValueError(f"{where}: penalty_multiplier must be a number in [0, {MAX_MULTIPLIER}]")
    if "alert" in rule and not isinstance(rule["alert"], bool):
        raise ValueError(f"{where}: alert must be true/false")
    if "interpretation" in rule and rule["interpretation"] not in INTERPRETATIONS:
        raise ValueError(f"{where}: interpretation must be one of {INTERPRETATIONS}")
    if "severity" in rule and rule["severity"] not in SEVERITIES:
        raise ValueError(f"{where}: severity must be one of {SEVERITIES}")
    esc = rule.get("escalate_after_seconds")
    if esc is not None and (not isinstance(esc, (int, float)) or isinstance(esc, bool) or esc <= 0):
        raise ValueError(f"{where}: escalate_after_seconds must be a positive number or null")
    for d, sub in (rule.get("directions") or {}).items():
        if d not in DIRECTIONS:
            raise ValueError(f"{where}: unknown direction {d!r}")
        if isinstance(sub, dict) and "directions" in sub:
            raise ValueError(f"{where}.{d}: nested directions are not allowed")
        _validate_rule(sub, f"{where}.{d}")


def validate_policy(data: dict) -> dict:
    if not isinstance(data, dict) or not data.get("policy_version"):
        raise ValueError("policy must be an object with a policy_version")
    _validate_rule(data.get("default") or {}, "default")
    contexts = data.get("contexts") or {}
    for ctx, rules in contexts.items():
        if ctx not in ACTIVITY_CONTEXTS:
            raise ValueError(f"unknown context {ctx!r}")
        if not isinstance(rules, dict):
            raise ValueError(f"{ctx}: must be an object")
        for beh, rule in rules.items():
            if beh not in POLICY_BEHAVIORS:
                raise ValueError(f"{ctx}: unknown behaviour {beh!r}")
            _validate_rule(rule, f"{ctx}.{beh}")
    return data


def load_policy(path: Optional[str] = None) -> "BehaviorInterpretationPolicy":
    path = path or os.environ.get("FG_CONTEXT_POLICY_PATH") or DEFAULT_POLICY_PATH
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return BehaviorInterpretationPolicy(data)


class BehaviorInterpretationPolicy:
    def __init__(self, data: Optional[dict] = None):
        if data is None:
            with open(DEFAULT_POLICY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        self.data = validate_policy(data)
        self.version = data["policy_version"]
        self.calibration_status = data.get("calibration_status", "UNCALIBRATED_DEFAULT")
        base = {"penalty_multiplier": 1.0, "alert": True, "interpretation": SIGNAL, "severity": "normal",
                "escalate_after_seconds": None}
        base.update({k: v for k, v in (data.get("default") or {}).items() if k != "directions"})
        self._default = base

    def rule(self, behavior: str, context: Optional[str], direction: Optional[str] = None) -> dict:
        """Effective rule (default <- context rule <- direction rule)."""
        out = dict(self._default)
        if context is None:
            return out
        ctx_rule = (self.data.get("contexts") or {}).get(context, {}).get(behavior)
        if ctx_rule:
            out.update({k: v for k, v in ctx_rule.items() if k != "directions"})
            if direction and direction in (ctx_rule.get("directions") or {}):
                out.update(ctx_rule["directions"][direction])
        return out

    def interpret(self, behavior: Optional[str], context: Optional[str], direction: Optional[str] = None,
                  episode_seconds: Optional[float] = None) -> Interpretation:
        if behavior not in POLICY_BEHAVIORS:
            return Interpretation(behavior, context, NOT_A_SIGNAL, 1.0, False, policy_version=self.version,
                                  reason="no sustained behaviour signal")
        r = self.rule(behavior, context, direction if behavior == "HEAD_AWAY" else None)
        state = r["interpretation"]
        mult = float(r["penalty_multiplier"])
        alert = bool(r["alert"])
        escalated = False
        esc = r.get("escalate_after_seconds")
        if esc is not None and episode_seconds is not None and episode_seconds >= esc and state != SIGNAL:
            # Expected-in-context for a short while, but it has lasted long enough
            # that the teacher should look: back to the default treatment.
            escalated = True
            state, mult, alert = SIGNAL, float(self._default["penalty_multiplier"]), True
        if context is None:
            reason = "no activity context selected: legacy behaviour"
        elif escalated:
            reason = f"lasted >= {esc:g}s in {context}"
        else:
            reason = f"{behavior} in {context}" + (f" ({direction})" if direction and behavior == "HEAD_AWAY" else "")
        return Interpretation(
            observed_behavior=behavior, context=context, interpreted_state=state, penalty_multiplier=mult,
            alert=alert, severity=r.get("severity", "normal"),
            treat_as_focused=(state == EXPECTED_IN_CONTEXT), escalated=escalated,
            direction=direction if behavior == "HEAD_AWAY" else None, policy_version=self.version, reason=reason)

    def table(self) -> dict:
        """Effective policy for every context x behaviour (UI / reports)."""
        out = {}
        for ctx in ACTIVITY_CONTEXTS:
            out[ctx] = {}
            for beh in POLICY_BEHAVIORS:
                out[ctx][beh] = self.rule(beh, ctx)
                dirs = ((self.data.get("contexts") or {}).get(ctx, {}).get(beh) or {}).get("directions") or {}
                if dirs:
                    out[ctx][beh] = dict(out[ctx][beh], directions={d: self.rule(beh, ctx, d) for d in dirs})
        return out

    def to_dict(self) -> dict:
        return {"policy_version": self.version, "calibration_status": self.calibration_status,
                "note": self.data.get("note"), "effective": self.table()}
