"""SYNTHETIC scenarios for exercising the pipeline without cameras.

These are generated signals, not recordings of people. They test that the
logic behaves as designed (timing rules, identity bookkeeping, FPS invariance).
They say nothing about real-world accuracy.
"""
import math
import random
from typing import Dict, List, Sequence, Tuple

import numpy as np

from ..behavior import Observation

# Behaviour segment kinds understood by ``behavior_observations``.
#  sustained (expected to produce an episode): PHONE, DROWSY, HEAD_AWAY, AWAY
#  short (expected to be ignored):             BLINK, GLANCE, PHONE_FLASH, OCCLUSION
EXPECTED = {"PHONE", "DROWSY", "HEAD_AWAY", "AWAY"}


def behavior_observations(fps: float, duration: float, segments: Sequence[Tuple[float, float, str]],
                          start: float = 1_000_000.0, phone_conf: float = 0.8,
                          jitter: float = 0.0, seed: int = 0) -> List[Observation]:
    """Observations for one student at ``fps`` over ``duration`` seconds.

    ``segments`` are ``(t0, t1, kind)`` relative to ``start``.
    ``jitter`` adds uniform timestamp noise (fraction of a frame interval).
    """
    rng = random.Random(seed)
    dt = 1.0 / fps
    n = int(math.floor(duration * fps)) + 1
    out = []
    for i in range(n):
        rel = i * dt
        if jitter and 0 < i < n - 1:
            rel += rng.uniform(-jitter, jitter) * dt
        kinds = {k for (a, b, k) in segments if a <= rel < b}
        visible = not ({"AWAY", "OCCLUSION"} & kinds)
        obs = Observation(timestamp=start + rel, visible=visible, face_visible=visible,
                          ear=0.30, yaw=0.0, pitch=0.0, phone_confidence=None, source="synthetic")
        if visible:
            if kinds & {"DROWSY", "BLINK"}:
                obs.ear = 0.12
            if kinds & {"HEAD_AWAY", "GLANCE"}:
                obs.yaw = 40.0
            if kinds & {"PHONE", "PHONE_FLASH"}:
                obs.phone_confidence = phone_conf
        out.append(obs)
    return out


def ground_truth(segments: Sequence[Tuple[float, float, str]], start: float = 1_000_000.0) -> List[dict]:
    return [{"type": k, "start_time": start + a, "end_time": start + b}
            for (a, b, k) in segments if k in EXPECTED]


STANDARD_BEHAVIOR_SCRIPT = [
    (5.0, 5.2, "BLINK"),
    (8.0, 8.15, "BLINK"),
    (12.0, 12.6, "GLANCE"),
    (15.0, 15.2, "PHONE_FLASH"),
    (20.0, 32.0, "PHONE"),
    (40.0, 46.0, "DROWSY"),
    (55.0, 63.0, "HEAD_AWAY"),
    (70.0, 73.0, "OCCLUSION"),
    (80.0, 100.0, "AWAY"),
    (110.0, 110.4, "GLANCE"),
]
STANDARD_DURATION = 120.0


# --------------------------------------------------------------- identity
def random_gallery(n: int, dim: int = 128, seed: int = 0) -> Dict[int, np.ndarray]:
    rng = np.random.default_rng(seed)
    gal = {}
    for sid in range(1, n + 1):
        v = rng.normal(size=dim)
        gal[sid] = v / np.linalg.norm(v)
    return gal


def noisy(vec: np.ndarray, noise: float, rng: np.random.Generator) -> np.ndarray:
    v = vec + rng.normal(scale=noise, size=vec.shape)
    return v / np.linalg.norm(v)


def identity_scenario(n_students: int = 6, fps: float = 10.0, duration: float = 30.0, noise: float = 0.05,
                      seed: int = 1):
    """Classroom-like identity stream.

    * every student has a track from t=0; tracks 1..n map to students 1..n
    * student 1 is occluded between 8-9 s and comes back on a NEW track id
      (tracker re-assignment) -> recovery;
    * tracks of students 2 and 3 cross at 12-12.5 s (faces swapped for a few
      frames) -> crossing protection;
    * a stranger (not enrolled) appears on track 99 from 15 s;
    * student 4's face is heavily blurred (high noise) 20-22 s -> uncertainty.

    Returns ``(gallery, frames)`` where frames is a list of
    ``(t, [(track_id, embedding_or_None, gt_student_id)])``.
    """
    rng = np.random.default_rng(seed)
    gallery = random_gallery(n_students, seed=seed)
    stranger = random_gallery(1, seed=seed + 100)[1]
    frames = []
    n = int(duration * fps)
    for i in range(n):
        t = 1_000_000.0 + i / fps
        rel = i / fps
        items = []
        for sid in range(1, n_students + 1):
            tid = sid
            if sid == 1:
                if 8.0 <= rel < 9.0:
                    continue          # occluded, no detection at all
                if rel >= 9.0:
                    tid = 101         # tracker assigned a new id
            face_sid = sid
            if 12.0 <= rel < 12.5 and sid in (2, 3):
                face_sid = 3 if sid == 2 else 2   # crossing: faces swap boxes
            sigma = 0.35 if (sid == 4 and 20.0 <= rel < 22.0) else noise
            items.append((tid, noisy(gallery[face_sid], sigma, rng), sid))
        if rel >= 15.0:
            items.append((99, noisy(stranger, noise, rng), None))
        frames.append((t, items))
    return gallery, frames
