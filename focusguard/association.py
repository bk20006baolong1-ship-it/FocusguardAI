"""Pure geometry used by the camera pipelines (no OpenCV dependency).

* faces are associated to person tracks one-to-one, using the face centre
  inside the upper part of the person box and distance to the expected head
  position;
* phones are associated to at most one person, only when the detector is
  confident enough and most of the phone box lies inside that person's box
  (containment = intersection / phone area), with ties broken by centre
  distance. A mere corner overlap no longer counts as "using a phone".
"""
from typing import Dict, List, Optional, Sequence, Tuple

Box = Sequence[float]  # x1, y1, x2, y2


def area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def intersection(a: Box, b: Box) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def iou(a: Box, b: Box) -> float:
    inter = intersection(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def containment(inner: Box, outer: Box) -> float:
    """Fraction of ``inner`` that lies inside ``outer``."""
    a = area(inner)
    return intersection(inner, outer) / a if a > 0 else 0.0


def center(b: Box) -> Tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def associate_faces(persons: Dict[int, Box], faces: List[Box],
                    head_fraction: float = 0.85) -> Dict[int, int]:
    """Return ``{track_id: face_index}`` with each face used at most once.

    A face is a candidate for a person when its centre lies inside the top
    ``head_fraction`` of the person box. 0.85 (not 0.5) because a laptop webcam
    frames a student from very close: the person box is little more than head
    and shoulders and the face centre sits around the middle of it. Global
    greedy matching by distance to each person's head point keeps the
    assignment one-to-one in crowded classroom frames.
    """
    candidates = []
    for tid, p in persons.items():
        head_x = (p[0] + p[2]) / 2.0
        head_y = p[1] + (p[3] - p[1]) * 0.15
        limit_y = p[1] + (p[3] - p[1]) * head_fraction
        for fi, f in enumerate(faces):
            fx, fy = center(f)
            if p[0] <= fx <= p[2] and p[1] <= fy <= limit_y:
                d = ((fx - head_x) ** 2 + (fy - head_y) ** 2) ** 0.5
                candidates.append((d, tid, fi))
    candidates.sort()
    used_t, used_f, out = set(), set(), {}
    for _, tid, fi in candidates:
        if tid in used_t or fi in used_f:
            continue
        out[tid] = fi
        used_t.add(tid)
        used_f.add(fi)
    return out


def expand(b: Box, margin: float) -> Box:
    w, h = b[2] - b[0], b[3] - b[1]
    return (b[0] - margin * w, b[1] - margin * h, b[2] + margin * w, b[3] + margin * h)


def associate_phones(persons: Dict[int, Box], phones: List[Tuple[Box, float]],
                     min_confidence: float, min_containment: float,
                     reach_margin: float = 0.25) -> Dict[int, float]:
    """Return ``{track_id: phone_confidence}`` (max over phones assigned to it).

    Containment is measured against the person box grown by ``reach_margin``
    on every side: a phone is held in the hands, which person detectors often
    leave outside the body box (real webcam run: a detected phone, conf up to
    0.82, was not associated in 71 of 84 frames). Each phone still goes to at
    most one person (highest containment, then nearest centre).
    """
    out: Dict[int, float] = {}
    for pbox, conf in phones:
        if conf is None or conf < min_confidence:
            continue
        best: Optional[Tuple[float, float, int]] = None
        pc = center(pbox)
        for tid, person in persons.items():
            c = containment(pbox, expand(person, reach_margin))
            if c < min_containment:
                continue
            bc = center(person)
            dist = ((pc[0] - bc[0]) ** 2 + (pc[1] - bc[1]) ** 2) ** 0.5
            key = (c, -dist, tid)
            if best is None or key[:2] > best[:2]:
                best = key
        if best is not None:
            tid = best[2]
            out[tid] = max(out.get(tid, 0.0), float(conf))
    return out
