"""Landmark geometry shared by the camera pipelines (pure Python)."""
import math

LEFT_EYE = (362, 385, 387, 263, 373, 380)
RIGHT_EYE = (33, 160, 158, 133, 153, 144)


def eye_aspect_ratio(landmarks, idx):
    def dist(a, b):
        return math.dist((a.x, a.y), (b.x, b.y))
    horizontal = dist(landmarks[idx[0]], landmarks[idx[3]])
    if horizontal == 0:
        return None
    v1 = dist(landmarks[idx[1]], landmarks[idx[5]])
    v2 = dist(landmarks[idx[2]], landmarks[idx[4]])
    return (v1 + v2) / (2.0 * horizontal)


def mean_ear(landmarks):
    vals = [eye_aspect_ratio(landmarks, LEFT_EYE), eye_aspect_ratio(landmarks, RIGHT_EYE)]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None
