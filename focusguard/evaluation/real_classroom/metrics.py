"""Real-world metrics engine (ground truth vs AI output).

Inputs are an evaluation package (see models.PACKAGE_FORMAT): AI
``predictions`` (raw events with their context interpretation), human
``annotations`` and optionally ``identity_frames`` and ``performance``.

Rules
-----
* Only time covered by a ground-truth label (other than UNKNOWN) of that
  student is scored. Unlabelled time is never counted as a false positive.
* A rate is reported only when the data supports it; otherwise the value is
  the string ``NOT_ENOUGH_DATA`` (never 0%). Counts are always reported.
* Two views: ``raw`` (every AI signal) and ``context_aware`` (only signals the
  context policy still treats as alerts). The difference is the measured
  effect of the activity-context policy.
"""
from collections import defaultdict
from statistics import mean
from typing import Dict, List, Optional

from .models import (GT_LABELS, LABEL_TO_BEHAVIOR, ON_TASK_LABELS, REAL, UNKNOWN)

NOT_ENOUGH_DATA = "NOT_ENOUGH_DATA"
BEHAVIORS = ("PHONE", "DROWSY", "HEAD_AWAY", "AWAY")
PRIORITY = ("PHONE", "DROWSY", "HEAD_AWAY", "AWAY")
NO_EVENT = "NO_EVENT"
ALERTING_STATES = (None, "SIGNAL")

DEFAULT_THRESHOLDS = {
    "min_events_for_precision": 3,       # predictions scored (TP+FP)
    "min_events_for_recall": 3,          # ground-truth positives (TP+FN)
    "min_annotated_hours_for_rate": 0.25,  # student-hours for false positives / hour
    "min_matched_for_latency": 3,
    "match_tolerance_seconds": 1.0,
    "min_identity_frames": 50,
    "min_perf_frames": 30,
    "recovery_gap_seconds": 2.0,
    "recovery_window_seconds": 10.0,
}


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _r(v, n=4):
    return None if v is None else round(v, n)


def _merge(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def coverage(annotations) -> Dict[int, list]:
    per = defaultdict(list)
    for a in annotations:
        if a.get("label") != UNKNOWN:
            per[int(a["student_id"])].append((float(a["started_at"]), float(a["ended_at"])))
    return {sid: _merge(iv) for sid, iv in per.items()}


def _in_coverage(cov, sid, start, end) -> bool:
    return any(_overlap(start, end, a, b) > 0 for a, b in cov.get(sid, []))


def _gt_label_at(annotations_by_student, sid, t) -> Optional[str]:
    for a in annotations_by_student.get(sid, ()):
        if a["started_at"] <= t < a["ended_at"]:
            return a["label"]
    return None


def _event_metrics(preds, gts, annotated_seconds, th, ann_by_student):
    tol = th["match_tolerance_seconds"]
    pairs = []
    for i, p in enumerate(preds):
        for j, g in enumerate(gts):
            if p["student_id"] != g["student_id"]:
                continue
            ov = _overlap(p["start_time"], p["end_time"] or p["start_time"], g["start"] - tol, g["end"] + tol)
            if ov > 0 or (p["start_time"] >= g["start"] - tol and p["start_time"] <= g["end"] + tol):
                pairs.append((ov, i, j))
    pairs.sort(reverse=True)
    used_p, used_g, matches = set(), set(), []
    for _, i, j in pairs:
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        matches.append((preds[i], gts[j]))
    tp, fp, fn = len(matches), len(preds) - len(used_p), len(gts) - len(used_g)
    hours = annotated_seconds / 3600.0
    fp_by_label = defaultdict(int)
    fp_by_context = defaultdict(int)
    for i, p in enumerate(preds):
        if i in used_p:
            continue
        mid = 0.5 * (p["start_time"] + (p["end_time"] or p["start_time"]))
        fp_by_label[_gt_label_at(ann_by_student, p["student_id"], mid) or "UNLABELLED"] += 1
        fp_by_context[p.get("activity_context") or "NONE"] += 1
    lat = [(p.get("metadata") or {}).get("detected_at", p["start_time"]) - g["start"] for p, g in matches]
    dur = [abs(((p["end_time"] or p["start_time"]) - p["start_time"]) - (g["end"] - g["start"])) for p, g in matches]
    precision = tp / (tp + fp) if (tp + fp) >= th["min_events_for_precision"] else NOT_ENOUGH_DATA
    recall = tp / (tp + fn) if (tp + fn) >= th["min_events_for_recall"] else NOT_ENOUGH_DATA
    if NOT_ENOUGH_DATA in (precision, recall):
        f1 = NOT_ENOUGH_DATA
    else:
        f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    enough_lat = len(matches) >= th["min_matched_for_latency"]
    return {
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "ground_truth_events": len(gts), "predicted_events_in_coverage": len(preds),
        "precision": _r(precision) if precision != NOT_ENOUGH_DATA else NOT_ENOUGH_DATA,
        "recall": _r(recall) if recall != NOT_ENOUGH_DATA else NOT_ENOUGH_DATA,
        "f1": _r(f1) if f1 != NOT_ENOUGH_DATA else NOT_ENOUGH_DATA,
        "false_positives_per_hour": (_r(fp / hours, 3) if hours >= th["min_annotated_hours_for_rate"]
                                     else NOT_ENOUGH_DATA),
        "false_positives_by_ground_truth_label": dict(fp_by_label),
        "false_positives_by_activity_context": dict(fp_by_context),
        "event_onset_latency_seconds": ({"mean": _r(mean(lat), 3), "p50": _r(_pct(lat, 0.5), 3),
                                         "p95": _r(_pct(lat, 0.95), 3)} if enough_lat else NOT_ENOUGH_DATA),
        "event_duration_error_seconds": ({"mean": _r(mean(dur), 3), "p50": _r(_pct(dur, 0.5), 3)}
                                         if enough_lat else NOT_ENOUGH_DATA),
    }


def behavior_report(package: dict, thresholds: Optional[dict] = None) -> dict:
    th = dict(DEFAULT_THRESHOLDS, **(thresholds or {}))
    anns = [a for a in package.get("annotations") or [] if a.get("label") in GT_LABELS]
    cov = coverage(anns)
    annotated_seconds = sum(b - a for iv in cov.values() for a, b in iv)
    ann_by_student = defaultdict(list)
    for a in anns:
        ann_by_student[int(a["student_id"])].append(a)
    preds_all = [dict(p) for p in package.get("predictions") or [] if p.get("type") in BEHAVIORS
                 and p.get("start_time") is not None]
    for p in preds_all:
        p.setdefault("end_time", p["start_time"])
        p["student_id"] = int(p["student_id"])
    out = {"raw": {}, "context_aware": {}}
    for beh in BEHAVIORS:
        gts = [{"student_id": int(a["student_id"]), "start": float(a["started_at"]), "end": float(a["ended_at"])}
               for a in anns if LABEL_TO_BEHAVIOR.get(a["label"]) == beh]
        scored = [p for p in preds_all if p["type"] == beh
                  and _in_coverage(cov, p["student_id"], p["start_time"], p["end_time"] or p["start_time"])]
        out["raw"][beh] = _event_metrics(scored, gts, annotated_seconds, th, ann_by_student)
        alerting = [p for p in scored if p.get("interpreted_state") in ALERTING_STATES]
        out["context_aware"][beh] = _event_metrics(alerting, gts, annotated_seconds, th, ann_by_student)
    return {"annotated_student_seconds": round(annotated_seconds, 1), "per_behavior": out["raw"],
            "per_behavior_context_aware": out["context_aware"]}


def confusion_matrix(package: dict, step: float = 1.0, context_aware: bool = False) -> dict:
    """Time-sampled matrix: rows = ground-truth label, columns = AI state."""
    anns = [a for a in package.get("annotations") or [] if a.get("label") not in (None, UNKNOWN)]
    preds = defaultdict(list)
    for p in package.get("predictions") or []:
        if p.get("type") not in BEHAVIORS:
            continue
        if context_aware and p.get("interpreted_state") not in ALERTING_STATES:
            continue
        preds[int(p["student_id"])].append(p)
    cols = list(PRIORITY) + [NO_EVENT]
    rows = {lab: {c: 0 for c in cols} for lab in GT_LABELS if lab != UNKNOWN}
    for a in anns:
        sid = int(a["student_id"])
        t = float(a["started_at"]) + step / 2
        while t < float(a["ended_at"]):
            active = {p["type"] for p in preds[sid] if p["start_time"] <= t <= (p.get("end_time") or p["start_time"])}
            state = next((b for b in PRIORITY if b in active), NO_EVENT)
            rows[a["label"]][state] += 1
            t += step
    return {"step_seconds": step, "columns": cols, "rows": rows}


def identity_report(frames: List[dict], thresholds: Optional[dict] = None) -> dict:
    th = dict(DEFAULT_THRESHOLDS, **(thresholds or {}))
    frames = [f for f in (frames or []) if f.get("gt_student_id", "UNLABELLED") != "UNLABELLED"]
    if len(frames) < th["min_identity_frames"]:
        return {"status": NOT_ENOUGH_DATA, "frames": len(frames),
                "required_frames": th["min_identity_frames"]}
    frames.sort(key=lambda f: (f["t"], f["track_id"]))
    known = [f for f in frames if f.get("gt_student_id") is not None]
    strangers = [f for f in frames if f.get("gt_student_id") is None]
    assigned = [f for f in known if f.get("pred_student_id") is not None]
    correct = sum(1 for f in assigned if f["pred_student_id"] == f["gt_student_id"])
    last = {}
    switches = 0
    for f in known:
        g, p = f["gt_student_id"], f.get("pred_student_id")
        if p is None:
            continue
        if g in last and last[g] != p:
            switches += 1
        last[g] = p
    # recovery: a known student disappears for >= gap and reappears
    seen = defaultdict(list)
    for f in known:
        seen[f["gt_student_id"]].append(f)
    reappear = recovered = 0
    for g, fs in seen.items():
        for prev, cur in zip(fs, fs[1:]):
            if cur["t"] - prev["t"] >= th["recovery_gap_seconds"]:
                reappear += 1
                window = [x for x in fs if cur["t"] <= x["t"] <= cur["t"] + th["recovery_window_seconds"]]
                if any(x.get("pred_student_id") == g for x in window):
                    recovered += 1
    by_t = defaultdict(list)
    for f in frames:
        if f.get("pred_student_id") is not None:
            by_t[f["t"]].append(f["pred_student_id"])
    duplicates = sum(len(v) - len(set(v)) for v in by_t.values())
    return {
        "status": "OK", "frames": len(frames), "frames_with_known_student": len(known),
        "identity_match_accuracy": _r(correct / len(assigned)) if assigned else NOT_ENOUGH_DATA,
        "identity_switch_count": switches,
        "unknown_rate": _r(1 - len(assigned) / len(known)) if known else NOT_ENOUGH_DATA,
        "wrong_assignments": len(assigned) - correct,
        "duplicate_identity_frames": duplicates,
        "stranger_frames": len(strangers),
        "stranger_false_accept_rate": (_r(sum(1 for f in strangers if f.get("pred_student_id") is not None)
                                          / len(strangers)) if strangers else NOT_ENOUGH_DATA),
        "reappearances": reappear,
        "recovery_rate": _r(recovered / reappear) if reappear else NOT_ENOUGH_DATA,
    }


def performance_report(perf: Optional[dict], thresholds: Optional[dict] = None) -> dict:
    th = dict(DEFAULT_THRESHOLDS, **(thresholds or {}))
    perf = perf or {}
    ts = sorted(perf.get("frame_timestamps") or [])
    lat = list(perf.get("frame_latency_ms") or [])
    if len(ts) < th["min_perf_frames"]:
        return {"status": NOT_ENOUGH_DATA, "frames": len(ts), "required_frames": th["min_perf_frames"]}
    span = ts[-1] - ts[0]
    return {"status": "OK", "frames": len(ts),
            "fps": _r((len(ts) - 1) / span, 2) if span > 0 else NOT_ENOUGH_DATA,
            "latency_p50_ms": _r(_pct(lat, 0.5), 2) if lat else NOT_ENOUGH_DATA,
            "latency_p95_ms": _r(_pct(lat, 0.95), 2) if lat else NOT_ENOUGH_DATA,
            "identity_latency_p95_ms": (_r(_pct(perf.get("identity_latency_ms"), 0.95), 2)
                                        if perf.get("identity_latency_ms") else NOT_ENOUGH_DATA),
            "device": perf.get("device")}


def scenario_report(package: dict) -> dict:
    """Per-scenario breakdown: which scripted scenarios ran, how much of their
    time is labelled, and the AI signals inside each window. Counts only; the
    precision/recall for a scenario is not computed (too few events per window)."""
    from .scenarios import SCENARIO_IDS
    runs = [r for r in package.get("scenarios") or [] if r.get("started_at") is not None]
    end_of = (package.get("metadata") or {}).get("ended_at") or package.get("evaluated_until")
    anns = [a for a in package.get("annotations") or [] if a.get("label") in GT_LABELS]
    preds = [p for p in package.get("predictions") or [] if p.get("type") in BEHAVIORS
             and p.get("start_time") is not None]
    per = {}
    for r in runs:
        a = float(r["started_at"])
        b = float(r.get("ended_at") or end_of or a)
        e = per.setdefault(r["scenario_id"], {"runs": 0, "seconds": 0.0, "annotated_student_seconds": 0.0,
                                              "ground_truth_labels": defaultdict(float), "ai_signals": defaultdict(int),
                                              "ai_alerts_context_aware": defaultdict(int)})
        e["runs"] += 1
        e["seconds"] += max(0.0, b - a)
        for x in anns:
            if x["label"] == UNKNOWN:
                continue
            ov = _overlap(a, b, float(x["started_at"]), float(x["ended_at"]))
            if ov > 0:
                e["annotated_student_seconds"] += ov
                e["ground_truth_labels"][x["label"]] += ov
        for p in preds:
            if a <= p["start_time"] < b:
                e["ai_signals"][p["type"]] += 1
                if p.get("interpreted_state") in ALERTING_STATES:
                    e["ai_alerts_context_aware"][p["type"]] += 1
    out = {}
    for sid, e in per.items():
        out[sid] = {"runs": e["runs"], "seconds": round(e["seconds"], 1),
                    "annotated_student_seconds": round(e["annotated_student_seconds"], 1),
                    "ground_truth_seconds_by_label": {k: round(v, 1) for k, v in e["ground_truth_labels"].items()},
                    "ai_signals": dict(e["ai_signals"]), "ai_alerts_context_aware": dict(e["ai_alerts_context_aware"]),
                    "labelled": e["annotated_student_seconds"] > 0}
    return {"pack_scenarios": len(SCENARIO_IDS), "run": sorted(out),
            "not_run": [s for s in SCENARIO_IDS if s not in out],
            "run_and_labelled": sorted(k for k, v in out.items() if v["labelled"]),
            "per_scenario": out}


def evaluate_package(package: dict, thresholds: Optional[dict] = None) -> dict:
    kind = package.get("dataset_kind") or REAL
    beh = behavior_report(package, thresholds)
    ident = identity_report(package.get("identity_frames"), thresholds)
    perf = performance_report(package.get("performance"), thresholds)
    consent_ok = bool((package.get("metadata") or {}).get("consent_verified"))
    any_number = any(v.get("f1") != NOT_ENOUGH_DATA for v in beh["per_behavior"].values())
    status = "EVALUATED" if any_number else NOT_ENOUGH_DATA
    return {
        "dataset_kind": kind,
        "status": status,
        # Only a consented REAL package can ever be quoted as real-world accuracy.
        "is_real_world_evidence": kind == REAL and consent_ok and status == "EVALUATED",
        "consent_verified": consent_ok,
        "thresholds": dict(DEFAULT_THRESHOLDS, **(thresholds or {})),
        "behavior": beh,
        "confusion_matrix": confusion_matrix(package),
        "confusion_matrix_context_aware": confusion_matrix(package, context_aware=True),
        "identity": ident,
        "performance": perf,
        "scenarios": scenario_report(package),
        "label_note": ("SYNTHETIC/REPLAY data: validates code paths, not real-world accuracy."
                       if kind != REAL else "REAL data: numbers apply only to this recorded set-up."),
    }
