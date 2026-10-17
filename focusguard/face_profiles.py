"""Multi-template face profiles: quality filter, diversity selection, enrollment status.

A student's face profile is a few good, DIFFERENT embeddings (front, slight left /
right, slight up / down, other light), not one averaged vector from one frame:

* minimum useful ~3 templates (PARTIAL), target 5 over >= 2 pose buckets (READY),
  at most ~10 active (``EnrollmentConfig``); never hundreds;
* only embeddings — no face photo is written anywhere; the face crop used for
  the quality check lives in memory for that frame only;
* every write requires ACTIVE face-recognition consent (a teacher tag is NOT
  consent); revoke / retention delete all templates (repository + ConsentService);
* candidates are rejected when the face is too small, blurry, too dark / bright,
  low-contrast, in an extreme pose, occluded, uncertainly associated with the
  person track, or a near-duplicate of a kept template.

Thresholds are defaults (UNCALIBRATED_DEFAULT), not tuned on real classroom data.
"""
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import numpy as np

from .config import DEFAULT_CONFIG, EnrollmentConfig
from .privacy import audit as A

# pose buckets
FRONT, LEFT, RIGHT, UP, DOWN = "FRONT", "LEFT", "RIGHT", "UP", "DOWN"
# template sources
SRC_LEGACY = "LEGACY"                     # migrated users.face_embedding
SRC_ADMIN = "ADMIN_ENROLLMENT"            # admin photo registration
SRC_BURST = "TEACHER_TAG_BURST"           # enrollment burst after a teacher tag
SRC_PROGRESSIVE = "PROGRESSIVE"           # guarded learning in later sessions
# sample states (in memory only)
PENDING, CONFIRMED, REJECTED = "PENDING", "CONFIRMED", "REJECTED"
# enrollment status
NOT_ENROLLED = "NOT_ENROLLED"
NEEDS_MORE_SAMPLES = "NEEDS_MORE_SAMPLES"
PARTIAL = "PARTIAL"
READY = "READY"
NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_LABELS_VI = {NOT_ENROLLED: "Chưa có mẫu", NEEDS_MORE_SAMPLES: "Cần thêm mẫu", PARTIAL: "Đủ dùng, nên thêm mẫu",
                    READY: "Sẵn sàng", NEEDS_REVIEW: "Cần xem lại"}
REJECT_LABELS_VI = {
    "TOO_SMALL": "khuôn mặt quá nhỏ", "LOW_DETECTOR_SCORE": "phát hiện khuôn mặt không chắc", "BLURRY": "ảnh mờ",
    "TOO_DARK": "quá tối", "TOO_BRIGHT": "quá sáng", "LOW_CONTRAST": "thiếu tương phản", "EXTREME_POSE": "quay mặt quá nhiều",
    "OCCLUDED": "bị che", "ASSOCIATION_UNCERTAIN": "không chắc khuôn mặt thuộc người này", "DUPLICATE": "trùng mẫu đã có",
    "NO_EMBEDDING": "không có đặc trưng",
}


def normalize(vec) -> Optional[np.ndarray]:
    if vec is None:
        return None
    arr = np.asarray(vec, dtype=np.float64).reshape(-1)
    n = np.linalg.norm(arr) if arr.size else 0.0
    if not arr.size or n <= 0 or not np.isfinite(n):
        return None
    return arr / n


def pose_bucket(yaw, pitch) -> str:
    if yaw is None or pitch is None:
        return FRONT
    if abs(yaw) >= 12:
        return LEFT if yaw < 0 else RIGHT
    if abs(pitch) >= 10:
        return DOWN if pitch > 0 else UP
    return FRONT


def face_metrics(gray=None, box=None, det_score=None, yaw=None, pitch=None) -> dict:
    """Practical quality signals of one face candidate. ``gray``: grey face crop (2-D uint8),
    used in memory only. Sharpness = variance of a 4-neighbour Laplacian on a <= 128 px grid."""
    m = {"size_px": None, "det_score": det_score, "yaw": yaw, "pitch": pitch,
         "sharpness": None, "brightness": None, "contrast": None}
    if box is not None:
        x1, y1, x2, y2 = box
        m["size_px"] = float(max(0.0, min(x2 - x1, y2 - y1)))
    if gray is not None:
        g = np.asarray(gray, dtype=np.float64)
        if g.ndim == 3:
            g = g.mean(axis=2)
        if g.size >= 9 and min(g.shape) >= 3:
            step = max(1, int(max(g.shape) // 128))
            g = g[::step, ::step]
            lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
            m["sharpness"] = float(lap.var())
            m["brightness"] = float(g.mean())
            m["contrast"] = float(g.std())
    return m


def evaluate(metrics: dict, cfg: EnrollmentConfig = DEFAULT_CONFIG.enrollment, occluded=False,
             association_ok=True):
    """(accepted, quality in [0, 1], [reason codes]) for an enrollment candidate."""
    reasons = []
    size = metrics.get("size_px")
    if size is None or size < cfg.min_face_px:
        reasons.append("TOO_SMALL")
    det = metrics.get("det_score")
    if det is not None and det < cfg.min_det_score:
        reasons.append("LOW_DETECTOR_SCORE")
    sharp = metrics.get("sharpness")
    if sharp is not None and sharp < cfg.min_sharpness:
        reasons.append("BLURRY")
    b = metrics.get("brightness")
    if b is not None and b < cfg.min_brightness:
        reasons.append("TOO_DARK")
    if b is not None and b > cfg.max_brightness:
        reasons.append("TOO_BRIGHT")
    c = metrics.get("contrast")
    if c is not None and c < cfg.min_contrast:
        reasons.append("LOW_CONTRAST")
    yaw, pitch = metrics.get("yaw"), metrics.get("pitch")
    if (yaw is not None and abs(yaw) > cfg.max_yaw_deg) or (pitch is not None and abs(pitch) > cfg.max_pitch_deg):
        reasons.append("EXTREME_POSE")
    if occluded:
        reasons.append("OCCLUDED")
    if not association_ok:
        reasons.append("ASSOCIATION_UNCERTAIN")
    q_size = min(1.0, (size or 0.0) / (2.0 * cfg.min_face_px))
    q_det = 0.7 if det is None else max(0.0, min(1.0, det))
    q_sharp = 0.6 if sharp is None else min(1.0, sharp / (4.0 * cfg.min_sharpness))
    q_exp = 0.6 if b is None else max(0.0, 1.0 - abs(b - 128.0) / 128.0)
    pose_err = max(abs(yaw or 0.0) / cfg.max_yaw_deg, abs(pitch or 0.0) / cfg.max_pitch_deg)
    q_pose = max(0.0, 1.0 - 0.6 * pose_err)
    quality = round(0.25 * q_size + 0.2 * q_det + 0.2 * q_sharp + 0.15 * q_exp + 0.2 * q_pose, 4)
    if not reasons and quality < cfg.min_quality:
        reasons.append("LOW_QUALITY")
    return (not reasons), quality, reasons


@dataclass(eq=False)
class Sample:
    """An enrollment candidate: embedding + quality, never an image."""
    student_id: int
    embedding: np.ndarray
    quality: float
    pose: str
    t: float
    track_id: Optional[int] = None
    binding_id: Optional[str] = None
    state: str = PENDING
    reasons: List[str] = field(default_factory=list)


def max_similarity(vec, others: Iterable) -> float:
    best = -1.0
    for o in others:
        o = normalize(o)
        if o is not None and o.shape == vec.shape:
            best = max(best, float(o @ vec))
    return best


def consistent(samples: List[Sample], threshold: float) -> List[Sample]:
    """Drop burst samples that do not resemble the burst consensus (another person's face
    associated with the track for a moment)."""
    if len(samples) < 3:
        return list(samples)
    mat = np.vstack([s.embedding for s in samples])
    center = normalize(np.median(mat, axis=0))
    return [s for s in samples if float(s.embedding @ center) >= threshold]


def select_diverse(candidates: List[Sample], existing: List[dict], k: int, cfg: EnrollmentConfig) -> List[Sample]:
    """Greedy: prefer a pose bucket not covered yet, then the sample least similar to what is
    kept, then quality. Near-duplicates (>= duplicate_similarity) are never kept."""
    kept_vecs = [normalize(e["embedding"]) for e in existing if e.get("embedding") is not None]
    kept_vecs = [v for v in kept_vecs if v is not None]
    buckets = {e.get("pose_bucket") or FRONT for e in existing}
    chosen: List[Sample] = []
    pool = sorted(candidates, key=lambda s: -s.quality)
    while pool and len(chosen) < k:
        best, best_score = None, None
        for s in pool:
            sim = max_similarity(s.embedding, kept_vecs) if kept_vecs else -1.0
            if sim >= cfg.duplicate_similarity:
                continue
            score = s.quality + (0.5 if s.pose not in buckets else 0.0) + 0.3 * (1.0 - max(sim, 0.0))
            if best_score is None or score > best_score:
                best, best_score = s, score
        if best is None:
            break
        chosen.append(best)
        pool.remove(best)
        kept_vecs.append(best.embedding)
        buckets.add(best.pose)
    for s in pool:
        if s not in chosen and "DUPLICATE" not in s.reasons:
            s.reasons.append("DUPLICATE" if kept_vecs and max_similarity(s.embedding, kept_vecs) >= cfg.duplicate_similarity
                             else "NOT_SELECTED")
    return chosen


def profile_status(template_count: int, pose_buckets: int, consent_active: bool,
                   cfg: EnrollmentConfig = DEFAULT_CONFIG.enrollment) -> str:
    if template_count and not consent_active:
        return NEEDS_REVIEW
    if template_count <= 0:
        return NOT_ENROLLED
    if template_count < cfg.min_useful_templates:
        return NEEDS_MORE_SAMPLES
    if template_count >= cfg.target_templates and pose_buckets >= cfg.ready_min_pose_buckets:
        return READY
    return PARTIAL


class FaceProfileService:
    """Reads and writes face_profiles with the consent check and audit (no vectors in audit)."""

    def __init__(self, repo, consent, audit, clock, config: EnrollmentConfig = DEFAULT_CONFIG.enrollment):
        self.repo, self.consent, self.audit, self.clock, self.cfg = repo, consent, audit, clock, config

    def consent_active(self, student_id) -> bool:
        from .privacy.consent import SCOPE_FACE
        return self.consent.has_scope(int(student_id), SCOPE_FACE)

    def status(self, student_id, templates=None) -> dict:
        templates = self.repo.list_face_templates(student_id) if templates is None else templates
        n = len(templates)
        buckets = {t.get("pose_bucket") or FRONT for t in templates}
        consent = self.consent_active(student_id)
        st = profile_status(n, len(buckets), consent, self.cfg)
        return {"status": st, "label": STATUS_LABELS_VI[st], "template_count": n, "target": self.cfg.target_templates,
                "pose_buckets": sorted(buckets) if n else [], "consent_active": consent,
                "legacy": any(t.get("source") == SRC_LEGACY for t in templates),
                "summary": f"Mẫu nhận diện: {min(n, self.cfg.target_templates)}/{self.cfg.target_templates}"}

    def commit(self, student_id, samples: List[Sample], source: str, actor=None, class_session_id=None,
               binding_id=None, limit=None) -> int:
        """Store the best, most diverse samples as templates (ACTIVE consent required)."""
        sid = int(student_id)
        if not samples:
            return 0
        if not self.consent_active(sid):
            for s in samples:
                s.state = REJECTED
                s.reasons.append("NO_CONSENT")
            self.reject(sid, len(samples), "no_face_consent", actor)
            return 0
        before = self.status(sid)
        existing = self.repo.list_face_templates(sid)
        k = limit if limit is not None else self.cfg.burst_select
        chosen = select_diverse(samples, existing, k, self.cfg)
        now = self.clock()
        for s in chosen:
            self.repo.add_face_template({"student_id": sid, "embedding": s.embedding.tolist(),
                                         "quality_score": s.quality, "pose_bucket": s.pose, "source": source,
                                         "class_session_id": class_session_id,
                                         "binding_id": binding_id or s.binding_id, "created_at": now})
            s.state = CONFIRMED
        for s in samples:
            if s.state != CONFIRMED:
                s.state = REJECTED
        if chosen and not self.consent_active(sid):
            # consent revoked while these were being written: the revoke deleted the profile
            # before our inserts landed -> delete again, nothing may survive a revoke
            self.repo.delete_face_templates(sid)
            self.reject(sid, len(chosen), "no_face_consent", actor)
            return 0
        if chosen:
            self.repo.mark_face_enrolled(sid, now)
            self._prune(sid)
            self.audit.record(A.IDENTITY_PROFILE_SAMPLE_ADDED, actor, sid,
                              {"count": len(chosen), "source": source, "class_session_id": class_session_id,
                               "pose_buckets": sorted({s.pose for s in chosen})})
        rejected = len(samples) - len(chosen)
        if rejected:
            self.reject(sid, rejected, "not_selected_or_duplicate", actor, audit_only_if_meaningful=True)
        after = self.status(sid)
        if after["status"] == READY and before["status"] != READY:
            self.audit.record(A.FACE_PROFILE_READY, actor, sid, {"template_count": after["template_count"]})
        return len(chosen)

    def reject(self, student_id, count, reason, actor=None, audit_only_if_meaningful=False):
        if count <= 0 or (audit_only_if_meaningful and reason == "not_selected_or_duplicate"):
            return
        self.audit.record(A.IDENTITY_PROFILE_SAMPLE_REJECTED, actor, int(student_id),
                          {"count": int(count), "reason": reason})

    def remove_binding_templates(self, binding_id, student_id, actor=None, reason="teacher_correction") -> int:
        n = self.repo.delete_face_templates_for_binding(binding_id)
        if n:
            self.reject(student_id, n, reason, actor)
        return n

    def _prune(self, sid):
        """Keep at most max_active_templates: drop the lowest-quality template of the most crowded pose bucket."""
        rows = self.repo.list_face_templates(sid)
        while len(rows) > self.cfg.max_active_templates:
            counts: Dict[str, int] = {}
            for r in rows:
                b = r.get("pose_bucket") or FRONT
                counts[b] = counts.get(b, 0) + 1
            crowded = max(counts, key=lambda b: counts[b])
            victim = min((r for r in rows if (r.get("pose_bucket") or FRONT) == crowded),
                         key=lambda r: (r.get("quality_score") if r.get("quality_score") is not None else 0.5,
                                        r.get("created_at") or 0))
            self.repo.delete_face_template(victim["face_profile_id"])
            rows = [r for r in rows if r["face_profile_id"] != victim["face_profile_id"]]

    def replace_from_photos(self, student_id, embeddings: List, actor=None) -> int:
        """Admin photo registration: every usable photo becomes a candidate template (multi-template
        instead of one averaged vector); the previous profile is replaced."""
        sid = int(student_id)
        vecs = [normalize(e) for e in embeddings]
        samples = [Sample(sid, v, 0.8, FRONT, self.clock()) for v in vecs if v is not None]
        if not samples:
            return 0
        self.repo.save_face_embedding(sid, None)     # replace the whole profile; restarts the retention clock
        return self.commit(sid, samples, SRC_ADMIN, actor, limit=self.cfg.max_active_templates)
