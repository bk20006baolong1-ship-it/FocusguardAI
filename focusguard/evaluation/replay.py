"""REPLAY classroom fixture: a generated evaluation package for cloud CI.

It mimics the *structure* of a real classroom evaluation (several students,
teacher-chosen activity contexts, writing / board / group discussion, phone,
drowsiness, leaving the seat, observer ground-truth timeline) so that the
metrics engine, the context policy and the calibration tool can be exercised
end-to-end without a camera. The signals are generated: results computed from
this package are labelled REPLAY and are NOT real-world accuracy.
"""
import math
import random
import time
from typing import Dict, List, Tuple

from ..behavior import Observation
from ..config import DEFAULT_CONFIG, PipelineConfig
from ..session_runtime import SessionRuntime
from .real_classroom.models import PACKAGE_FORMAT, REPLAY
from .synthetic import identity_scenario

T0 = 1_800_000_000.0

# (start, end, context) relative seconds
CONTEXT_TIMELINE = [
    (0, 120, "LECTURE"),
    (120, 240, "WRITING"),
    (240, 360, "GROUP_DISCUSSION"),
    (360, 480, "LOOKING_AT_BOARD"),
    (480, 600, "LECTURE"),
]
DEFAULT_LABEL = {"LECTURE": "FOCUSED", "WRITING": "WRITING", "GROUP_DISCUSSION": "GROUP_DISCUSSION",
                 "LOOKING_AT_BOARD": "LOOKING_AT_BOARD"}

# (student, start, end, kind, ground-truth label)
#  kind: PHONE | DROWSY | LOOK_AWAY | AWAY | MOVE (walks to another group: not visible, on task)
SCRIPT = [
    (1, 30, 42, "PHONE", "PHONE_DISTRACTION"),
    (4, 60, 75, "DROWSY", "DROWSY"),
    (5, 80, 95, "LOOK_AWAY", "OFF_TASK_LOOKING_AWAY"),
    (1, 150, 170, "PHONE", "PHONE_DISTRACTION"),
    (2, 200, 214, "LOOK_AWAY", "OFF_TASK_LOOKING_AWAY"),
    (3, 300, 330, "MOVE", "GROUP_DISCUSSION"),
    (5, 400, 415, "LOOK_AWAY", "OFF_TASK_LOOKING_AWAY"),
    (6, 430, 445, "DROWSY", "DROWSY"),
    (2, 500, 530, "AWAY", "AWAY"),
    (6, 520, 535, "PHONE", "PHONE_DISTRACTION"),
    (3, 550, 562, "DROWSY", "DROWSY"),
    (4, 570, 584, "LOOK_AWAY", "OFF_TASK_LOOKING_AWAY"),
]


def _context_at(rel):
    for a, b, c in CONTEXT_TIMELINE:
        if a <= rel < b:
            return c
    return CONTEXT_TIMELINE[-1][2]


def _observation(rng, sid, rel, t, ctx, active) -> Observation:
    """Signals for one student at one instant."""
    ear = 0.30 + rng.gauss(0, 0.012)
    yaw = rng.gauss(0, 4)
    pitch = rng.gauss(0, 4)
    phone = None
    if ctx == "WRITING":
        # copying: mostly head down, sometimes up to the board
        pitch = 34 + rng.gauss(0, 4) if (rel + sid * 7) % 20 < 15 else -24 + rng.gauss(0, 3)
        ear = 0.25 + rng.gauss(0, 0.015)      # eyes look lower when writing
    elif ctx == "GROUP_DISCUSSION":
        yaw = (42 if sid % 2 else -42) + rng.gauss(0, 5) if (rel + sid * 3) % 16 < 11 else rng.gauss(0, 4)
    elif ctx == "LOOKING_AT_BOARD":
        pitch = -26 + rng.gauss(0, 3)
    if active == "PHONE":
        phone = rng.uniform(0.3, 0.7) if rng.random() > 0.12 else None
        pitch = 30 + rng.gauss(0, 4)
    elif active == "DROWSY":
        ear = 0.07 + abs(rng.gauss(0, 0.02))
    elif active == "LOOK_AWAY":
        yaw = (48 if sid % 2 else -48) + rng.gauss(0, 4)
        pitch = rng.gauss(0, 4)
    # blinks and short glances that must never become events
    if rng.random() < 0.02:
        ear = 0.06
    if rng.random() < 0.01:
        yaw = 40.0
    return Observation(timestamp=t, visible=True, face_visible=True, ear=ear, yaw=yaw, pitch=pitch,
                       phone_confidence=phone, identity_confidence=0.8, track_id=sid, source="replay")


def generate_package(n_students: int = 6, duration: float = 600.0, fps: float = 5.0, seed: int = 7,
                     config: PipelineConfig = DEFAULT_CONFIG) -> dict:
    rng = random.Random(seed)
    students = list(range(1, n_students + 1))
    events: List[dict] = []

    class Sink:
        def save_focus_event(self, row):
            row = dict(row)
            row["id"] = len(events) + 1
            events.append(row)

        def save_focus_snapshot(self, row):
            pass

        def save_session_student(self, row):
            pass

    rt = SessionRuntime(enrolled=[{"student_id": s, "display_name": f"HS{s}"} for s in students], started_at=T0,
                        class_id=1, class_session_id=1, config=config, sink=Sink(), source="replay",
                        activity_context=CONTEXT_TIMELINE[0][2])
    observations, frame_ts, latency_ms = [], [], []
    n = int(math.floor(duration * fps))
    for i in range(n):
        rel = i / fps
        t = T0 + rel
        ctx = _context_at(rel)
        if ctx != rt.activity_context:
            rt.set_activity_context(ctx, t)
        frame = {}
        for sid in students:
            active = next((k for (s, a, b, k, _) in SCRIPT if s == sid and a <= rel < b), None)
            if active in ("AWAY", "MOVE"):
                continue
            obs = _observation(rng, sid, rel, t, ctx, active)
            frame[sid] = obs
            observations.append({"t": t, "student_id": sid, "visible": True, "face_visible": True,
                                 "ear": round(obs.ear, 4), "yaw": round(obs.yaw, 3), "pitch": round(obs.pitch, 3),
                                 "phone_confidence": None if obs.phone_confidence is None else round(obs.phone_confidence, 3),
                                 "identity_confidence": 0.8, "track_id": sid})
        t_start = time.perf_counter()
        rt.process_frame(t, frame)
        latency_ms.append((time.perf_counter() - t_start) * 1000.0)
        frame_ts.append(t)
    rt.end(T0 + duration)

    annotations = []
    for sid in students:
        # observer timeline: context default label, overridden by scripted behaviour
        cuts = sorted({0.0, duration} | {float(a) for a, _, _ in CONTEXT_TIMELINE}
                      | {float(x) for (s, a, b, _, _) in SCRIPT if s == sid for x in (a, b)})
        for a, b in zip(cuts, cuts[1:]):
            if b <= a:
                continue
            mid = 0.5 * (a + b)
            label = next((lab for (s, x, y, _, lab) in SCRIPT if s == sid and x <= mid < y),
                         DEFAULT_LABEL[_context_at(mid)])
            annotations.append({"annotation_id": f"r{sid}-{int(a)}", "evaluation_session_id": "replay",
                                "student_id": sid, "label": label, "started_at": T0 + a, "ended_at": T0 + b,
                                "annotated_by": 0, "created_at": T0 + duration, "note": "replay fixture"})

    gallery, id_frames = identity_scenario()
    from ..identity import IdentityManager
    mgr = IdentityManager(gallery, config.identity)
    identity_rows = []
    for t, items in id_frames:
        decisions = mgr.process_frame([(tid, emb) for tid, emb, _ in items], t)
        for tid, _, gt in items:
            d = decisions[tid]
            identity_rows.append({"t": t, "track_id": tid, "gt_student_id": gt,
                                  "pred_student_id": d.student_id if d.status == "CONFIRMED" else None,
                                  "status": d.status})

    return {
        "format": PACKAGE_FORMAT, "dataset_kind": REPLAY,
        "metadata": {"evaluation_session_id": "replay-fixture", "class_id": 1, "camera_position": "FRONT_CORNER",
                     "camera_angle": "generated", "resolution": "1280x720", "fps_target": fps,
                     "device": "generated replay (no camera)", "student_count": n_students,
                     "lighting_condition": "OTHER", "activity_context": CONTEXT_TIMELINE[0][2],
                     "started_at": T0, "ended_at": T0 + duration,
                     "notes": "REPLAY fixture generated in code; not a recording of people.",
                     "consent_verified": False},
        "enrolled": students,
        "context_segments": [{"context": c, "started_at": T0 + a, "ended_at": T0 + b} for a, b, c in CONTEXT_TIMELINE],
        "annotations": annotations,
        "predictions": [{k: e.get(k) for k in ("id", "student_id", "type", "start_time", "end_time",
                                                "duration_seconds", "confidence", "activity_context",
                                                "interpreted_state", "penalty_multiplier", "metadata")}
                        for e in events],
        "observations": observations,
        "identity_frames": identity_rows,
        "performance": {"frame_timestamps": frame_ts, "frame_latency_ms": latency_ms,
                        "device": "cloud CPU, runtime only (detectors not executed)"},
        "policy_version": rt.policy.version,
    }


def frames_from_package(package: dict) -> List[Tuple[float, Dict[int, dict]]]:
    """Group observation rows into frames ``[(t, {student_id: row})]``."""
    by_t: Dict[float, Dict[int, dict]] = {}
    for o in package.get("observations") or []:
        by_t.setdefault(float(o["t"]), {})[int(o["student_id"])] = o
    for t in (package.get("performance") or {}).get("frame_timestamps") or []:
        by_t.setdefault(float(t), {})
    return sorted(by_t.items())
