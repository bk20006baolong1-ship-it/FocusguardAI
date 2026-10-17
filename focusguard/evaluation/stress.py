"""Multi-person stress harness (SYNTHETIC, cloud-runnable).

Ten scenarios drive the real IdentityManager + SessionRuntime with generated
face embeddings and track ids. They check *contracts* (no student on two
tracks, strangers never accepted, a locked identity survives occlusion,
recovery after a tracker id change, no score reset) and a performance
budget. They are regression and load tests, NOT real-world accuracy: real
crossing, occlusion, camera angle and crowding need the protocol in
docs/REAL_CLASSROOM_EVALUATION_PROTOCOL.md (HARDWARE_REQUIRED).
"""
import time
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from ..behavior import Observation
from ..config import DEFAULT_CONFIG, PipelineConfig
from ..identity import CONFIRMED, IdentityManager
from ..session_runtime import SessionRuntime
from . import SYNTHETIC
from .metrics import identity_metrics
from .synthetic import noisy, random_gallery

T0 = 1_000_000.0

# spec(rel, sid) -> None (not visible) or (track_id, face_sid | None, sigma, occluded)
Spec = Callable[[float, int], Optional[Tuple[int, Optional[int], float, bool]]]


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def run_scenario(n_students: int, duration: float, fps: float, spec: Spec, *, seed: int = 0,
                 strangers: Optional[Callable[[float], List[Tuple[int, float]]]] = None,
                 angle_shift: Optional[Callable[[float], float]] = None,
                 config: PipelineConfig = DEFAULT_CONFIG, with_runtime: bool = True) -> dict:
    rng = np.random.default_rng(seed)
    gallery = random_gallery(n_students, seed=seed)
    stranger_vecs = random_gallery(5, seed=seed + 1000)
    shift_dirs = {sid: noisy(np.zeros(gallery[1].shape) + rng.normal(size=gallery[1].shape), 0.0, rng)
                  for sid in gallery}
    mgr = IdentityManager(gallery, config.identity)
    rt = None
    if with_runtime:
        rt = SessionRuntime(enrolled=[{"student_id": s} for s in gallery], started_at=T0, config=config,
                            class_id=1, class_session_id=1, source="synthetic")
    rows, id_lat, rt_lat = [], [], []
    score_trace: Dict[int, List[float]] = {}
    n = int(duration * fps)
    for i in range(n):
        rel = i / fps
        t = T0 + rel
        items, gts = [], {}
        shift = angle_shift(rel) if angle_shift else 0.0
        for sid in gallery:
            s = spec(rel, sid)
            if s is None:
                continue
            tid, face_sid, sigma, occ = s
            emb = None
            if face_sid is not None:
                base = gallery[face_sid]
                if shift:
                    base = base + shift * shift_dirs[face_sid]
                    base = base / np.linalg.norm(base)
                emb = noisy(base, sigma, rng)
            items.append((tid, emb, occ))
            gts[tid] = sid
        for tid, sigma in (strangers(rel) if strangers else []):
            items.append((tid, noisy(stranger_vecs[1 + tid % 5], sigma, rng), False))
            gts[tid] = None
        a = time.perf_counter()
        decisions = mgr.process_frame(items, t)
        id_lat.append((time.perf_counter() - a) * 1000.0)
        obs = {}
        for tid, _, _ in items:
            d = decisions[tid]
            pred = d.student_id if d.status == CONFIRMED else None
            rows.append({"t": t, "track_id": tid, "gt_student_id": gts[tid], "pred_student_id": pred})
            if pred is not None:
                obs[pred] = Observation(timestamp=t, visible=True, face_visible=True, ear=0.3, yaw=0.0, pitch=0.0,
                                        identity_confidence=d.confidence, track_id=tid, source="synthetic")
        if rt is not None:
            a = time.perf_counter()
            rt.process_frame(t, obs)
            rt_lat.append((time.perf_counter() - a) * 1000.0)
            for sid, st in rt.states.items():
                if st.focus_score is not None:
                    score_trace.setdefault(sid, []).append(st.focus_score)
    if rt is not None:
        rt.end(T0 + duration)
    events = {k: sum(1 for e in mgr.events if e.kind == k)
              for k in ("CONFIRMED", "RECOVERED", "RELEASED", "CONFLICT", "LOST")}
    released_by_student: Dict[int, int] = {}
    for e in mgr.events:
        if e.kind == "RELEASED" and e.student_id is not None:
            released_by_student[e.student_id] = released_by_student.get(e.student_id, 0) + 1
    frame_lat = [a + b for a, b in zip(id_lat, rt_lat)] if rt_lat else id_lat
    return {
        "rows": rows, "manager": mgr, "runtime": rt, "events": events, "released_by_student": released_by_student,
        "score_trace": score_trace,
        "identity": identity_metrics(rows),
        "performance": {"frames": n, "fps_simulated": fps, "students": n_students,
                        "identity_latency_ms_p50": round(_pct(id_lat, 0.5), 4),
                        "identity_latency_ms_p95": round(_pct(id_lat, 0.95), 4),
                        "frame_latency_ms_p50": round(_pct(frame_lat, 0.5), 4),
                        "frame_latency_ms_p95": round(_pct(frame_lat, 0.95), 4),
                        "frame_latency_ms_mean": round(sum(frame_lat) / len(frame_lat), 4) if frame_lat else None,
                        "identity_latency_ms_mean": round(sum(id_lat) / len(id_lat), 4) if id_lat else None},
    }


def _base(rel, sid, sigma=0.05):
    return (sid, sid, sigma, False)


def _wrong_frames(rows, after=0.0):
    return sum(1 for r in rows if r["t"] >= T0 + after and r["gt_student_id"] is not None
               and r["pred_student_id"] is not None and r["pred_student_id"] != r["gt_student_id"])


def _covered(rows, sid, a, b):
    sel = [r for r in rows if r["gt_student_id"] == sid and T0 + a <= r["t"] < T0 + b]
    return sum(1 for r in sel if r["pred_student_id"] == sid) / len(sel) if sel else 0.0


def _result(sid, name, desc, run, checks):
    ident = run["identity"]
    return {"scenario": sid, "name": name, "description": desc, "dataset_kind": SYNTHETIC,
            "checks": checks, "passed": all(checks.values()),
            "identity": {k: v for k, v in ident.items() if k != "never_confirmed"},
            "never_confirmed": ident["never_confirmed"], "identity_events": run["events"],
            "performance": run["performance"]}


# ------------------------------------------------------------------ scenarios
def s1_crossing():
    def spec(rel, sid):
        if sid in (1, 2) and 10.0 <= rel < 11.0:
            return (sid, 2 if sid == 1 else 1, 0.05, False)   # boxes cross: faces swap
        return _base(rel, sid)
    r = run_scenario(4, 20, 10, spec, seed=11)
    return _result(1, "two_students_cross", "Hai học sinh đi cắt nhau (khuôn mặt đổi chỗ 1 giây).", r, {
        "no_wrong_identity": _wrong_frames(r["rows"]) == 0,
        "no_identity_switch": r["identity"]["identity_switches"] == 0,
        "no_duplicate_identity": r["identity"]["duplicate_identity_frames"] == 0})


def s2_track_id_change():
    def spec(rel, sid):
        if sid in (1, 2) and rel >= 10.0:
            return (2 if sid == 1 else 1, sid, 0.05, False)   # tracker swaps the two ids
        if sid == 3 and rel >= 12.0:
            return (300, 3, 0.05, False)                     # tracker re-numbers a track
        return _base(rel, sid)
    r = run_scenario(4, 25, 10, spec, seed=12)
    wrong = _wrong_frames(r["rows"], after=10.0)
    budget = int(2 * 2.5 * 10)          # two students x 2.5 s x 10 fps while the lock is contradicted
    return _result(2, "track_id_change", "Tracker đổi/hoán đổi track_id giữa chừng.", r, {
        "no_duplicate_identity": r["identity"]["duplicate_identity_frames"] == 0,
        "wrong_frames_within_unlock_window": wrong <= budget,
        "student3_recovered_on_new_track": _covered(r["rows"], 3, 14.0, 25.0) >= 0.95,
        "swapped_students_reconfirmed": _covered(r["rows"], 1, 18.0, 25.0) >= 0.95
        and _covered(r["rows"], 2, 18.0, 25.0) >= 0.95})


def s3_disappear_return():
    def spec(rel, sid):
        if sid == 3 and 10.0 <= rel < 16.0:
            return None
        if sid == 3 and rel >= 16.0:
            return (103, 3, 0.05, False)
        return _base(rel, sid)
    r = run_scenario(4, 25, 10, spec, seed=13)
    trace = r["score_trace"].get(3, [])
    st = r["runtime"].states[3]
    return _result(3, "disappear_and_return", "Một học sinh biến mất 6 giây rồi quay lại với track mới.", r, {
        "recovered_event": r["events"]["RECOVERED"] >= 1,
        "recovered_within_1s": _covered(r["rows"], 3, 17.0, 25.0) >= 0.95,
        "attendance_not_reset": st.attendance_status == "PRESENT",
        "score_not_reset": bool(trace) and min(trace) >= 99.0 and st.away_count == 0})


def s4_stranger():
    def strangers(rel):
        out = []
        if 5.0 <= rel < 25.0:
            out.append((99, 0.05))
        if 12.0 <= rel < 13.0:
            out.append((98, 0.05))           # someone walks past the camera
        return out
    r = run_scenario(4, 30, 10, _base, seed=14, strangers=strangers)
    return _result(4, "stranger", "Người lạ (không đăng ký) xuất hiện trong khung hình.", r, {
        "stranger_never_accepted": r["identity"]["stranger_false_accept_rate"] == 0.0,
        "students_unaffected": _wrong_frames(r["rows"]) == 0})


def s5_face_occlusion():
    def spec(rel, sid):
        if sid == 4 and 10.0 <= rel < 15.0:
            return (4, None, 0.05, False)     # body tracked, face hidden (hand / mask)
        return _base(rel, sid)
    r = run_scenario(4, 20, 10, spec, seed=15)
    return _result(5, "face_occlusion", "Khuôn mặt bị che 5 giây, người vẫn trong khung hình.", r, {
        "lock_kept_during_occlusion": _covered(r["rows"], 4, 10.0, 15.0) == 1.0,
        "no_release": r["released_by_student"].get(4, 0) == 0})


def s6_phone_covers_face():
    def spec(rel, sid):
        if sid == 5 and 10.0 <= rel < 17.0:
            return (5, 5, 0.9, True)          # phone in front: unreliable embedding, occluder known
        return _base(rel, sid)
    r = run_scenario(5, 22, 10, spec, seed=16)
    return _result(6, "phone_covers_face", "Điện thoại che mặt 7 giây (embedding nhiễu mạnh).", r, {
        "lock_kept": _covered(r["rows"], 5, 10.0, 17.0) == 1.0,
        "no_release": r["released_by_student"].get(5, 0) == 0,
        "no_wrong_identity": _wrong_frames(r["rows"]) == 0})


def s7_camera_angle_change():
    r = run_scenario(6, 30, 10, _base, seed=17, angle_shift=lambda rel: 0.55 if rel >= 15.0 else 0.0)
    after = [_covered(r["rows"], s, 16.0, 30.0) for s in range(1, 7)]
    return _result(7, "camera_angle_change", "Đổi góc camera giữa buổi (đặc trưng khuôn mặt lệch có hệ thống).", r, {
        "no_wrong_identity": _wrong_frames(r["rows"]) == 0,
        "no_duplicate_identity": r["identity"]["duplicate_identity_frames"] == 0}) | {"coverage_after_change": [round(x, 3) for x in after]}


def _crowd(n, seed, budget_ms):
    def spec(rel, sid):
        k = int(rel * 10)
        if (k + sid * 13) % 53 == 0:
            return None                       # missed detection
        if sid % 5 == 0 and 8.0 <= rel < 8.5:
            return (sid, sid - 1, 0.05, False)  # neighbours cross
        return (sid, sid, 0.06, False)
    r = run_scenario(n, 20, 10, spec, seed=seed)
    res = _result(0, f"{n}_students", f"{n} học sinh (dữ liệu tải tổng hợp).", r, {
        "everyone_confirmed": r["identity"]["never_confirmed"] == [],
        "no_wrong_identity": _wrong_frames(r["rows"]) == 0,
        "no_duplicate_identity": r["identity"]["duplicate_identity_frames"] == 0,
        "frame_latency_within_budget": r["performance"]["frame_latency_ms_p95"] <= budget_ms})
    res["performance"]["budget_ms_p95"] = budget_ms
    return res


def s8_five():
    return dict(_crowd(5, 18, 25.0), scenario=8)


def s9_ten():
    return dict(_crowd(10, 19, 40.0), scenario=9)


def s10_twenty():
    return dict(_crowd(20, 20, 80.0), scenario=10, name="20_students_synthetic_workload")


SCENARIOS = [s1_crossing, s2_track_id_change, s3_disappear_return, s4_stranger, s5_face_occlusion,
             s6_phone_covers_face, s7_camera_angle_change, s8_five, s9_ten, s10_twenty]


def run_all() -> dict:
    results = [fn() for fn in SCENARIOS]
    return {"dataset_kind": SYNTHETIC, "passed": all(r["passed"] for r in results),
            "scenarios": results,
            "note": ("Synthetic embeddings and track ids: regression, load and identity-contract checks only. "
                     "Not real-world accuracy (HARDWARE_REQUIRED for real crossing/occlusion/angle/crowding).")}
