"""SYNTHETIC classroom for the field tool's ``--simulate`` mode.

Plays each scenario of the pack as scripted per-frame observations (no camera,
no images) through the REAL SessionRuntime, plus identity decisions per track
and an observer timeline derived from the same script. It exists so the whole
field flow (preflight -> consent -> session -> scenarios -> annotation ->
finalize) can be exercised in the cloud. Everything it produces is labelled
SYNTHETIC and can never count as real-world evidence.
"""
import random
from typing import Dict, List, Tuple

from ...behavior import Observation
from .models import (AWAY, DROWSY, FOCUSED, GROUP_DISCUSSION, LOOKING_AT_BOARD, PHONE_DISTRACTION, WRITING)

FPS = 5.0
SECONDS = {"07_away": 40.0, "14_leave_and_return": 40.0, "15_crowded_classroom": 60.0}
DEFAULT_SECONDS = 24.0
# On-task label the observer would give in each scenario when nothing else happens.
BASE_LABEL = {"02_writing": WRITING, "03_looking_at_board": LOOKING_AT_BOARD, "04_group_discussion": GROUP_DISCUSSION}


def scenario_seconds(scenario_id: str) -> float:
    return SECONDS.get(scenario_id, DEFAULT_SECONDS)


class SimulatedClassroom:
    def __init__(self, participants: List[int], seed: int = 7):
        if not participants:
            raise ValueError("simulation needs at least one participant")
        self.participants = list(participants)
        self.rng = random.Random(seed)
        self.tracks = {sid: 100 + i for i, sid in enumerate(self.participants)}
        self._next_track = 900

    # ------------------------------------------------------------ script
    def _events(self, scenario_id: str, seconds: float) -> List[Tuple[int, str, float, float]]:
        """(student_id, kind, start_s, end_s) scripted deviations from the base behaviour."""
        p = self.participants
        a = p[0]
        b = p[1] if len(p) > 1 else p[0]
        s = seconds
        table = {
            "05_phone": [(a, "phone", 0.25 * s, 0.65 * s)] + ([(b, "phone", 0.70 * s, 0.90 * s)] if b != a else []),
            "06_drowsy": [(a, "drowsy", 0.35 * s, 0.70 * s)],
            "07_away": [(a, "away", 0.20 * s, 0.75 * s)],
            "10_face_occlusion": [(a, "occluded", 0.30 * s, 0.60 * s)],
            "11_phone_occlusion": [(a, "phone", 0.30 * s, 0.75 * s), (a, "occluded", 0.30 * s, 0.75 * s)],
            "13_track_replacement": [(a, "new_track", 0.50 * s, 0.55 * s)],
            "14_leave_and_return": [(a, "away", 0.15 * s, 0.70 * s), (a, "new_track", 0.70 * s, 0.75 * s)],
            "15_crowded_classroom": [(a, "phone", 0.20 * s, 0.40 * s), (b, "away", 0.45 * s, 0.75 * s)],
        }
        return table.get(scenario_id, [])

    @staticmethod
    def _active(events, sid, kind, t):
        return any(e[0] == sid and e[1] == kind and e[2] <= t < e[3] for e in events)

    # ------------------------------------------------------------- frames
    def frames(self, scenario_id: str, t0: float):
        """Yields (t, observations, identity_rows, latency_ms, identity_latency_ms).
        Latencies are None: a simulation measures no detector, so it reports no
        performance numbers (performance stays NOT_ENOUGH_DATA)."""
        seconds = scenario_seconds(scenario_id)
        events = self._events(scenario_id, seconds)
        n = int(seconds * FPS)
        writing = scenario_id == "02_writing"
        board = scenario_id == "03_looking_at_board"
        group = scenario_id == "04_group_discussion"
        angle_shift = scenario_id == "12_camera_angle_change"
        for i in range(n):
            rel = i / FPS
            t = t0 + rel + 1.0 / FPS
            obs: Dict[int, Observation] = {}
            ident = []
            for sid in self.participants:
                if self._active(events, sid, "new_track", rel) and self.tracks[sid] < 900:
                    self.tracks[sid] = self._next_track          # tracker gave this person a new id
                    self._next_track += 1
                track = self.tracks[sid]
                if self._active(events, sid, "away", rel):
                    continue                                    # out of frame: no track at all
                occluded = self._active(events, sid, "occluded", rel)
                recovering = self._active(events, sid, "new_track", rel)
                yaw = self.rng.uniform(-6, 6)
                pitch = self.rng.uniform(-5, 5)
                if writing:
                    pitch = 28.0 + self.rng.uniform(-3, 3)        # head down (pitch_down_positive)
                elif board:
                    pitch = -12.0 + self.rng.uniform(-3, 3)
                elif group and 0.15 * seconds <= rel < 0.85 * seconds:
                    yaw = (40.0 if sid % 2 else -40.0) + self.rng.uniform(-4, 4)
                if angle_shift and rel >= 0.5 * seconds:
                    yaw += 8.0                                   # camera moved: the whole class shifts
                ear = 0.10 if self._active(events, sid, "drowsy", rel) else 0.30 + self.rng.uniform(-0.02, 0.02)
                phone = 0.9 if self._active(events, sid, "phone", rel) else None
                identified = not occluded and not recovering
                ident.append({"track_id": track, "gt_student_id": sid, "pred_student_id": sid if identified else None,
                              "status": "CONFIRMED" if identified else "UNKNOWN"})
                if not identified and not phone:
                    continue                                     # pipeline only scores identified tracks
                obs[sid] = Observation(timestamp=t, visible=True, face_visible=not occluded,
                                       ear=None if occluded else ear, yaw=None if occluded else yaw,
                                       pitch=None if occluded else pitch, phone_confidence=phone,
                                       identity_confidence=0.9 if identified else None, track_id=track)
            if scenario_id == "09_stranger" and 0.3 * seconds <= rel < 0.7 * seconds:
                ident.append({"track_id": 990, "gt_student_id": None, "pred_student_id": None, "status": "UNKNOWN"})
            if scenario_id == "08_crossing" and len(self.participants) > 1 and 0.45 * seconds <= rel < 0.55 * seconds:
                for row in ident[:2]:
                    row["pred_student_id"], row["status"] = None, "UNKNOWN"      # brief loss while crossing
            yield (t, obs, ident, None, None)

    # ------------------------------------------------------- ground truth
    def ground_truth(self, scenario_id: str, t0: float) -> List[dict]:
        """What a careful observer would label for this scenario (SYNTHETIC)."""
        seconds = scenario_seconds(scenario_id)
        events = self._events(scenario_id, seconds)
        base = BASE_LABEL.get(scenario_id, FOCUSED)
        out = []
        for sid in self.participants:
            cuts = sorted({0.0, seconds} | {x for e in events if e[0] == sid and e[1] in ("phone", "drowsy", "away")
                                            for x in (e[2], e[3])})
            for a, b in zip(cuts, cuts[1:]):
                mid = 0.5 * (a + b)
                label = base
                if self._active(events, sid, "away", mid):
                    label = AWAY
                elif self._active(events, sid, "phone", mid):
                    label = PHONE_DISTRACTION
                elif self._active(events, sid, "drowsy", mid):
                    label = DROWSY
                out.append({"student_id": sid, "label": label, "start_s": a, "end_s": b,
                            "scenario_id": scenario_id, "t0": t0})
        return out
