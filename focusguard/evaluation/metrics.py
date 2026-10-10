"""Evaluation metrics (dataset-agnostic)."""
from collections import defaultdict
from statistics import mean, pstdev
from typing import Dict, Iterable, List, Optional


def _interval_iou(a_start, a_end, b_start, b_end) -> float:
    inter = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    union = max(a_end, b_end) - min(a_start, b_start)
    return inter / union if union > 0 else 0.0


def match_episodes(pred: List[dict], gt: List[dict], min_iou: float = 0.3):
    """Greedy one-to-one matching per behaviour type by temporal IoU.

    Episodes are dicts with ``type``, ``start_time``, ``end_time``.
    Returns ``(matches, unmatched_pred, unmatched_gt)``.
    """
    pairs = []
    for i, p in enumerate(pred):
        for j, g in enumerate(gt):
            if p["type"] != g["type"]:
                continue
            iou = _interval_iou(p["start_time"], p["end_time"], g["start_time"], g["end_time"])
            if iou >= min_iou:
                pairs.append((iou, i, j))
    pairs.sort(reverse=True)
    used_p, used_g, matches = set(), set(), []
    for iou, i, j in pairs:
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        matches.append((pred[i], gt[j], iou))
    return (matches, [p for i, p in enumerate(pred) if i not in used_p],
            [g for j, g in enumerate(gt) if j not in used_g])


def _prf(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else (0.0 if tp + fp + fn else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def behavior_metrics(pred: List[dict], gt: List[dict], duration_seconds: float, min_iou: float = 0.3) -> dict:
    types = sorted({e["type"] for e in pred} | {e["type"] for e in gt})
    per_type = {}
    tot_tp = tot_fp = tot_fn = 0
    latencies = defaultdict(list)
    for t in types:
        p_t = [e for e in pred if e["type"] == t]
        g_t = [e for e in gt if e["type"] == t]
        matches, fps_, fns_ = match_episodes(p_t, g_t, min_iou)
        for p, g, _ in matches:
            detected = (p.get("metadata") or {}).get("detected_at")
            if detected is not None:
                latencies[t].append(detected - g["start_time"])
        per_type[t] = _prf(len(matches), len(fps_), len(fns_))
        per_type[t]["mean_latency_seconds"] = round(mean(latencies[t]), 3) if latencies[t] else None
        tot_tp += len(matches)
        tot_fp += len(fps_)
        tot_fn += len(fns_)
    hours = duration_seconds / 3600.0 if duration_seconds > 0 else None
    all_lat = [x for v in latencies.values() for x in v]
    return {
        "per_type": per_type,
        "overall": _prf(tot_tp, tot_fp, tot_fn),
        "false_alerts_per_hour": (tot_fp / hours) if hours else None,
        "mean_latency_seconds": round(mean(all_lat), 3) if all_lat else None,
        "duration_seconds": duration_seconds,
    }


def identity_metrics(frames: Iterable[dict]) -> dict:
    """``frames``: dicts with ``t``, ``track_id``, ``gt_student_id`` (None for
    strangers) and ``pred_student_id`` (None = unknown)."""
    frames = sorted(frames, key=lambda f: (f["t"], f["track_id"]))
    assigned = correct = wrong = 0
    gt_frames = 0
    stranger_frames = stranger_false = 0
    last_pred_for_gt: Dict[int, Optional[int]] = {}
    switches = 0
    duplicates = 0
    first_seen: Dict[int, float] = {}
    first_confirmed: Dict[int, float] = {}
    by_t = defaultdict(list)
    for f in frames:
        by_t[f["t"]].append(f)
        g, p = f.get("gt_student_id"), f.get("pred_student_id")
        if g is None:
            stranger_frames += 1
            if p is not None:
                stranger_false += 1
            continue
        gt_frames += 1
        first_seen.setdefault(g, f["t"])
        if p is not None:
            assigned += 1
            if p == g:
                correct += 1
                first_confirmed.setdefault(g, f["t"])
            else:
                wrong += 1
            prev = last_pred_for_gt.get(g)
            if prev is not None and prev != p:
                switches += 1
            last_pred_for_gt[g] = p
    for t, fs in by_t.items():
        preds = [f["pred_student_id"] for f in fs if f.get("pred_student_id") is not None]
        duplicates += len(preds) - len(set(preds))
    ttc = [first_confirmed[g] - first_seen[g] for g in first_confirmed]
    return {
        "frames_with_known_student": gt_frames,
        "coverage": assigned / gt_frames if gt_frames else None,
        "accuracy_when_assigned": correct / assigned if assigned else None,
        "wrong_assignments": wrong,
        "identity_switches": switches,
        "duplicate_identity_frames": duplicates,
        "stranger_false_accept_rate": stranger_false / stranger_frames if stranger_frames else None,
        "mean_time_to_confirmation_seconds": round(mean(ttc), 3) if ttc else None,
        "never_confirmed": sorted(set(first_seen) - set(first_confirmed)),
    }


def recovery_times(frames: Iterable[dict], change_times: Dict[int, float]) -> Dict[int, Optional[float]]:
    """For each student whose track id changed at ``change_times[sid]``, time
    until the new track is correctly confirmed again."""
    out = {}
    for sid, t0 in change_times.items():
        after = sorted(f["t"] for f in frames
                       if f.get("gt_student_id") == sid and f["t"] >= t0 and f.get("pred_student_id") == sid)
        out[sid] = round(after[0] - t0, 3) if after else None
    return out


def fps_consistency(scores_by_fps: Dict[float, float]) -> dict:
    vals = list(scores_by_fps.values())
    if not vals:
        return {"max_abs_diff": None, "stdev": None}
    return {"scores": {str(k): round(v, 3) for k, v in scores_by_fps.items()},
            "max_abs_diff": round(max(vals) - min(vals), 3),
            "stdev": round(pstdev(vals), 3)}
