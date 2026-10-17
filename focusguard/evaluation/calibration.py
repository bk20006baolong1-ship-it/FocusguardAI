"""Calibration: sweep behaviour thresholds against ground truth.

Replays the raw observation log of an evaluation package through the real
SessionRuntime (same BehaviorAnalyzer, same context policy) with candidate
thresholds and compares the resulting events with the human annotations.

Output: ``current_config``, ``recommended_config``, ``delta`` and ``evidence``.
Nothing is applied: ``applied`` is always False and ``requires_human_approval``
always True. A behaviour without enough ground truth keeps its current values
and is reported as NOT_ENOUGH_DATA.
"""
import itertools
import statistics
from dataclasses import asdict, replace
from typing import Dict, List, Optional

from ..behavior import Observation
from ..config import DEFAULT_CONFIG, BehaviorConfig, PipelineConfig
from ..policies.interpretation import BehaviorInterpretationPolicy
from ..session_runtime import SessionRuntime, default_policy
from .real_classroom.metrics import NOT_ENOUGH_DATA, behavior_report
from .real_classroom.models import REAL
from .replay import frames_from_package

# behaviour -> {parameter: candidate values}
DEFAULT_GRID = {
    "PHONE": {"phone_min_seconds": [0.5, 1.0, 1.5, 2.0], "phone_min_confidence": [0.2, 0.25, 0.35, 0.45]},
    "DROWSY": {"drowsy_min_seconds": [1.0, 1.5, 2.0, 3.0], "ear_closed_ratio": [0.5, 0.6, 0.7]},
    "HEAD_AWAY": {"head_away_min_seconds": [1.0, 2.0, 3.0, 5.0], "yaw_limit_deg": [15.0, 20.0, 25.0, 30.0],
                  "pitch_limit_deg": [15.0, 20.0, 25.0, 30.0]},
    "AWAY": {"away_min_seconds": [5.0, 10.0, 15.0, 20.0]},
}
MIN_HEAD_SAMPLES = 50


def replay(package: dict, behavior: BehaviorConfig, policy: Optional[BehaviorInterpretationPolicy] = None,
           base: PipelineConfig = DEFAULT_CONFIG) -> List[dict]:
    """Events produced by the pipeline on the package's observations."""
    frames = frames_from_package(package)
    if not frames:
        return []
    enrolled = package.get("enrolled") or sorted({sid for _, f in frames for sid in f})
    segs = sorted(package.get("context_segments") or [], key=lambda s: s["started_at"])
    events = []

    class Sink:
        def save_focus_event(self, row):
            events.append(row)

        def save_focus_snapshot(self, row):
            pass

        def save_session_student(self, row):
            pass

    t0 = frames[0][0]
    cfg = replace(base, behavior=behavior)
    rt = SessionRuntime(enrolled=[{"student_id": s} for s in enrolled], started_at=t0, config=cfg, sink=Sink(),
                        class_id=1, class_session_id=1, policy=policy or default_policy(), source="replay")
    si = 0
    for t, rows in frames:
        while si < len(segs) and segs[si]["started_at"] <= t:
            rt.set_activity_context(segs[si]["context"], segs[si]["started_at"])
            si += 1
        obs = {sid: Observation(timestamp=t, visible=bool(r.get("visible", True)), face_visible=r.get("face_visible"),
                                ear=r.get("ear"), yaw=r.get("yaw"), pitch=r.get("pitch"),
                                phone_confidence=r.get("phone_confidence"),
                                identity_confidence=r.get("identity_confidence"), track_id=r.get("track_id"))
               for sid, r in rows.items()}
        rt.process_frame(t, obs)
    rt.end(frames[-1][0])
    return events


def _score(m: dict):
    """Higher is better: F1, then fewer false positives, then recall."""
    f1 = m["f1"] if m["f1"] != NOT_ENOUGH_DATA else -1.0
    rec = m["recall"] if m["recall"] != NOT_ENOUGH_DATA else -1.0
    return (round(f1, 4), -m["false_positives"], round(rec, 4))


def _metrics_for(package, events, behavior, view):
    pkg = dict(package, predictions=events)
    rep = behavior_report(pkg)
    return rep["per_behavior_context_aware" if view == "context_aware" else "per_behavior"][behavior]


def head_pose_recommendation(package: dict, current: BehaviorConfig) -> dict:
    anns = package.get("annotations") or []
    by_label: Dict[str, Dict[str, list]] = {}
    for o in package.get("observations") or []:
        for a in anns:
            if a["student_id"] == o["student_id"] and a["started_at"] <= o["t"] < a["ended_at"]:
                d = by_label.setdefault(a["label"], {"yaw": [], "pitch": []})
                if o.get("yaw") is not None:
                    d["yaw"].append(o["yaw"])
                if o.get("pitch") is not None:
                    d["pitch"].append(o["pitch"])
                break
    out = {"status": NOT_ENOUGH_DATA, "current": {"yaw_center_deg": current.yaw_center_deg,
                                                   "pitch_center_deg": current.pitch_center_deg,
                                                   "pitch_down_positive": current.pitch_down_positive}}
    ref = by_label.get("FOCUSED") or by_label.get("LOOKING_AT_BOARD")
    if ref and len(ref["yaw"]) >= MIN_HEAD_SAMPLES:
        out["status"] = "OK"
        out["recommended"] = {"yaw_center_deg": round(statistics.median(ref["yaw"]), 1),
                              "pitch_center_deg": round(statistics.median(ref["pitch"]), 1)}
        wr = by_label.get("WRITING")
        if wr and len(wr["pitch"]) >= MIN_HEAD_SAMPLES:
            out["recommended"]["pitch_down_positive"] = statistics.median(wr["pitch"]) > statistics.median(ref["pitch"])
        out["samples"] = {k: len(v["yaw"]) for k, v in by_label.items()}
    return out


def penalty_recommendation(package: dict, policy: BehaviorInterpretationPolicy, min_events: int = 5) -> dict:
    """Penalty multiplier per (context, behaviour) ~ precision of the signal there.

    A signal that is right half of the time in GROUP_DISCUSSION should cost at
    most half the penalty. Only reported with enough scored events."""
    out = {}
    report = behavior_report(package)["per_behavior"]
    preds = package.get("predictions") or []
    contexts = sorted({p.get("activity_context") for p in preds if p.get("activity_context")})
    for ctx in contexts:
        sub = dict(package, predictions=[p for p in preds if p.get("activity_context") == ctx])
        rep = behavior_report(sub)["per_behavior"]
        for beh, m in rep.items():
            scored = m["true_positives"] + m["false_positives"]
            cur = policy.rule(beh, ctx)["penalty_multiplier"]
            if scored < min_events:
                rec = NOT_ENOUGH_DATA
            else:
                rec = round(min(cur, m["true_positives"] / scored) if cur > 0 else 0.0, 2)
            out.setdefault(ctx, {})[beh] = {"current_multiplier": cur, "recommended_multiplier": rec,
                                            "scored_events": scored, "true_positives": m["true_positives"]}
    return {"by_context": out, "overall_raw": {b: {"precision": v["precision"]} for b, v in report.items()},
            "min_events": min_events}


def _confidence(gt_events: int) -> str:
    """How much a recommendation can be trusted, from the ground-truth events behind it."""
    if gt_events < 3:
        return NOT_ENOUGH_DATA
    return "LOW" if gt_events < 10 else ("MEDIUM" if gt_events < 30 else "HIGH")


def coverage(package: dict) -> dict:
    rep = behavior_report(package)
    gt = {b: m["ground_truth_events"] for b, m in rep["per_behavior"].items()}
    obs = package.get("observations") or []
    return {"annotated_student_seconds": rep["annotated_student_seconds"],
            "ground_truth_events": gt,
            "sample_count": len(obs),
            "frames": len((package.get("performance") or {}).get("frame_timestamps") or []),
            "students_observed": len({o.get("student_id") for o in obs}),
            "confidence": {b: _confidence(n) for b, n in gt.items()}}


def calibrate(package: dict, current: Optional[PipelineConfig] = None,
              policy: Optional[BehaviorInterpretationPolicy] = None, grid: Optional[dict] = None,
              view: str = "context_aware") -> dict:
    current = current or DEFAULT_CONFIG
    policy = policy or default_policy()
    grid = grid or DEFAULT_GRID
    kind = package.get("dataset_kind") or REAL
    cur_b = current.behavior
    if not package.get("observations"):
        return {"status": NOT_ENOUGH_DATA, "dataset_kind": kind, "applied": False, "requires_human_approval": True,
                "reason": "No raw observation log in the package: thresholds cannot be replayed.",
                "current_config": asdict(cur_b), "recommended_config": asdict(cur_b), "delta": {}, "evidence": {},
                "penalty": penalty_recommendation(package, policy), "coverage": coverage(package),
                "sample_count": 0}
    baseline_events = replay(package, cur_b, policy, current)
    recommended = dict(asdict(cur_b))
    evidence = {}
    for beh, params in grid.items():
        base_m = _metrics_for(package, baseline_events, beh, view)
        enough = base_m["f1"] != NOT_ENOUGH_DATA
        entry = {"status": "OK" if enough else NOT_ENOUGH_DATA, "current_metrics": base_m,
                 "ground_truth_events": base_m["ground_truth_events"], "candidates_evaluated": 0,
                 "confidence": _confidence(base_m["ground_truth_events"])}
        if not enough:
            evidence[beh] = entry
            continue
        names = list(params)
        best = (_score(base_m), {n: getattr(cur_b, n) for n in names}, base_m)
        for combo in itertools.product(*(params[n] for n in names)):
            values = dict(zip(names, combo))
            if all(getattr(cur_b, n) == v for n, v in values.items()):
                continue
            ev = replay(package, replace(cur_b, **values), policy, current)
            m = _metrics_for(package, ev, beh, view)
            entry["candidates_evaluated"] += 1
            sc = _score(m)
            if sc > best[0]:
                best = (sc, values, m)
        entry["recommended_values"] = best[1]
        entry["recommended_metrics"] = best[2]
        recommended.update(best[1])
        evidence[beh] = entry
    delta = {k: {"current": getattr(cur_b, k), "recommended": v} for k, v in recommended.items()
             if getattr(cur_b, k) != v}
    statuses = {e["status"] for e in evidence.values()}
    return {
        "status": "OK" if "OK" in statuses else NOT_ENOUGH_DATA,
        "dataset_kind": kind,
        "is_real_world_evidence": kind == REAL and bool((package.get("metadata") or {}).get("consent_verified")),
        "objective": f"max F1 ({view}), then fewer false positives, then recall",
        "applied": False,
        "requires_human_approval": True,
        "current_config": asdict(cur_b),
        "recommended_config": recommended,
        "delta": delta,
        "evidence": evidence,
        "head_pose": head_pose_recommendation(package, cur_b),
        "penalty": penalty_recommendation(package, policy),
        "coverage": coverage(package),
        "sample_count": len(package.get("observations") or []),
        "policy_version": policy.version,
        "note": ("Recommendations only. Apply through FG_* environment variables or the policy file after a "
                 "teacher/organiser reviews the evidence." + ("" if kind == REAL else
                                                             " Computed on generated data: NOT real-world calibration.")),
    }
