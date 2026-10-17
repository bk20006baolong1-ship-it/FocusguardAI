"""Centralised, typed configuration for the vision + behaviour pipeline.

Every threshold that used to be hard-coded inside camera_ai.py / classroom_ai.py
lives here so it can be tuned in one place, overridden from the environment and
reported alongside evaluation results.
"""
from dataclasses import dataclass, field, asdict, replace
import os


def _env_float(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class IdentityConfig:
    # Cosine similarity a face embedding must reach to be a candidate match.
    similarity_threshold: float = 0.45
    # best - second_best must be at least this, otherwise the match is ambiguous.
    min_margin: float = 0.08
    # A candidate must win this many consecutive recognitions ...
    confirm_consecutive: int = 3
    # ... spread over at least this many seconds before the track is locked.
    confirm_min_seconds: float = 0.6
    # Once locked, this many consecutive contradicting recognitions are needed
    # before the lock is dropped (protects against one bad frame / crossing).
    unlock_consecutive: int = 4
    # ... and they must span at least this long (FPS-independent).
    unlock_min_seconds: float = 1.0
    # After a track loses its identity, the same student cannot be re-bound to a
    # *different* track for this many seconds unless the evidence is strong.
    conflict_cooldown_seconds: float = 2.0
    # Similarity required to steal an identity from another live track.
    steal_similarity: float = 0.60
    # Track that has not been observed for this long is considered lost.
    track_lost_seconds: float = 3.0
    # Seconds a lost track's identity is remembered for recovery on a new track.
    recovery_window_seconds: float = 10.0
    # Consecutive matches needed to re-bind a recently lost student to a new
    # track (tracker re-assigned an id after occlusion).
    recovery_consecutive: int = 2
    # A locked track whose face clearly matches NOBODY enrolled (e.g. a stranger
    # after a tracker id switch) is released only after this long without any
    # match of the locked student. Longer than unlock_min_seconds because partial
    # occlusion (hand, phone, looking down) also lowers similarity: on the real
    # webcam a phone in front of the face dropped it from ~0.87 to 0.13-0.44 for
    # several seconds.
    stranger_unlock_seconds: float = 5.0
    # Teacher-confirmed bindings (teacher tag / correction) are never switched by
    # face evidence. Contradicting evidence this persistent only flags the track
    # REVIEW_REQUIRED (teacher resolves); the binding is kept, nothing is swapped.
    teacher_review_consecutive: int = 6
    teacher_review_min_seconds: float = 2.0
    # "Confirm high-confidence suggestions" (one click for several tracks) only
    # accepts AUTO matches at least this similar AND with twice the normal margin.
    bulk_confirm_similarity: float = 0.60
    # Multi-template matching: per-student score = best template similarity; with
    # >= 2 templates the second best of that student may not be lower than
    # best - template_agreement (one odd template cannot carry a match alone).
    template_agreement: float = 0.25
    # Session identity continuity (focusguard/session_identity.py, UNCALIBRATED_DEFAULT): a teacher binding
    # whose track disappeared (reconnect / reload / tracker re-id) is continued on a new track ONLY when the
    # new person appears where the student was (IoU of the person boxes), is the only candidate there, has
    # no face match to another student and stays there for restore_confirm_frames / restore_min_seconds.
    restore_max_gap_seconds: float = 120.0
    restore_min_iou: float = 0.5
    restore_ambiguity_iou: float = 0.2
    restore_confirm_frames: int = 6
    restore_min_seconds: float = 1.5
    # A face recovery counts as the SAME physical person (attendance evidence / templates follow the
    # link on a correction) only at the place the student was lost, within this gap.
    continuity_min_iou: float = 0.3
    continuity_max_gap_seconds: float = 6.0
    # No frame for this long = a camera outage (CPU-only machines can take > 2 s per frame).
    outage_seconds: float = 5.0


@dataclass(frozen=True)
class BehaviorConfig:
    # Eye aspect ratio below which eyes are considered closed.
    ear_threshold: float = 0.22
    # Head-pose limits (degrees) outside of which the head is "away".
    yaw_limit_deg: float = 20.0
    pitch_limit_deg: float = 20.0
    # Head-pose reference ("looking at the lesson"). 0/0 = facing the camera,
    # the old behaviour. A classroom camera mounted in a corner sees students
    # looking at the board with a yaw offset; calibrate_behavior.py recommends
    # these from LOOKING_AT_BOARD ground truth. HEAD_AWAY then means
    # |yaw - yaw_center| > yaw_limit or |pitch - pitch_center| > pitch_limit.
    yaw_center_deg: float = 0.0
    pitch_center_deg: float = 0.0
    # Sign of pitch when the head tilts DOWN (used only to label the direction
    # of a HEAD_AWAY episode for the context policy, e.g. writing = DOWN).
    # HARDWARE_REQUIRED: confirm on the real camera with the head calibration
    # step of docs/REAL_CLASSROOM_EVALUATION_PROTOCOL.md.
    pitch_down_positive: bool = True
    # Minimum continuous duration before a raw signal becomes a behaviour.
    drowsy_min_seconds: float = 1.5
    head_away_min_seconds: float = 2.0
    phone_min_seconds: float = 1.0
    # Gaps shorter than this inside an active episode do not end it
    # (e.g. one missed phone detection mid-episode).
    gap_tolerance_seconds: float = 0.5
    # Not visible for less than this is TEMPORARILY_NOT_VISIBLE,
    # longer is AWAY (left the seat).
    away_min_seconds: float = 10.0
    # Eye-closure threshold adapts to each student's own open-eye EAR: closed
    # means EAR < min(ear_threshold, ear_closed_ratio * baseline), where the
    # baseline is a high percentile of that student's recent EAR. A fixed 0.22
    # produced drowsy episodes for a real student with open eyes (EAR of open
    # eyes depends on the person and the camera angle).
    # 0.6 from the real run: open-eye EAR p20 ~0.196 / median ~0.248 for the
    # volunteer, closed eyes <= ~0.12; 0.75*baseline (~0.216) flagged open eyes.
    ear_closed_ratio: float = 0.6
    ear_baseline_percentile: float = 0.8
    ear_baseline_window_seconds: float = 60.0
    ear_baseline_min_seconds: float = 2.0
    ear_baseline_min_samples: int = 10
    # Minimum detector confidence for a phone box to count. Real run: during
    # continuous phone use YOLOv8n scored the phone 0.25-0.62, so 0.35 split the
    # signal into fragments shorter than the 1 s confirmation.
    phone_min_confidence: float = 0.25
    # Phone detections drop out for a few frames (3 missed frames = 0.4 s at
    # 7.5 FPS); a longer gap tolerance keeps one episode. Temporal confirmation
    # (phone_min_seconds) still rejects a single short flash.
    phone_gap_tolerance_seconds: float = 1.0
    # Fraction of the phone box that must lie inside the person's box.
    phone_min_containment: float = 0.6


@dataclass(frozen=True)
class FocusConfig:
    # Score change per second. Positive rates recover, negative penalise.
    recovery_per_second: float = 0.5
    penalty_per_second: dict = field(default_factory=lambda: {
        "PHONE": 2.0,
        "DROWSY": 1.5,
        "HEAD_AWAY": 1.0,
        "AWAY": 1.0,
        "TEMPORARILY_NOT_VISIBLE": 0.0,
    })
    initial_score: float = 100.0
    min_score: float = 0.0
    max_score: float = 100.0
    # Largest time step the engine will integrate at once. Longer gaps (e.g. a
    # stalled camera) are clamped so a single stall cannot wipe the score.
    max_step_seconds: float = 2.0


@dataclass(frozen=True)
class AttendanceConfig:
    # Seconds after class start after which a first sighting is "late".
    late_after_seconds: float = 300.0
    # Optional: seconds after class start at which NOT_SEEN students become ABSENT
    # (only while no unidentified person is visible; otherwise it waits for the
    # teacher). None = attendance is finalised when the class ends.
    finalize_after_seconds: float = None


@dataclass(frozen=True)
class EnrollmentConfig:
    """Multi-template face profiles (focusguard/face_profiles.py). Defaults, not calibrated
    on real classroom data (UNCALIBRATED_DEFAULT)."""
    min_useful_templates: int = 3           # PARTIAL from here
    target_templates: int = 5               # READY at this many, with >= ready_min_pose_buckets
    max_active_templates: int = 10
    ready_min_pose_buckets: int = 2
    burst_seconds: float = 6.0              # enrollment burst after a teacher tag
    burst_max_candidates: int = 24
    burst_select: int = 6                   # templates kept from one burst (diversity selection)
    min_face_px: float = 64.0               # shorter side of the face box, enrollment
    recognition_min_face_px: float = 36.0   # below this a face is too small to recognise reliably
    min_det_score: float = 0.60
    min_sharpness: float = 25.0             # variance of the Laplacian of the grey face crop
    min_brightness: float = 45.0
    max_brightness: float = 215.0
    min_contrast: float = 18.0
    max_yaw_deg: float = 35.0
    max_pitch_deg: float = 25.0
    min_quality: float = 0.55               # combined quality score in [0, 1]
    duplicate_similarity: float = 0.97      # nearly identical to a kept sample -> duplicate
    burst_self_consistency: float = 0.45    # a burst sample must resemble the burst median
    # Progressive enrollment (later sessions): only confirmed, conflict-free, high-confidence
    # identities, high-quality and diverse samples, committed at class end.
    progressive_min_quality: float = 0.70
    progressive_min_similarity: float = 0.55
    progressive_min_confirmed_seconds: float = 5.0
    progressive_max_per_session: int = 2
    progressive_max_similarity_to_existing: float = 0.90


@dataclass(frozen=True)
class AnalyticsConfig:
    """Teacher-facing summaries (heuristic, never a ranking of students)."""
    # "Có thể cần giáo viên trao đổi": average heuristic score below this ...
    watchlist_score_below: float = 70.0
    # ... over at least this many measured sessions.
    watchlist_min_sessions: int = 2
    # "Cần giáo viên xem lại": window of recent AI events and list length.
    review_recent_days: float = 7.0
    review_queue_limit: int = 10
    # AI-vs-teacher rates are NOT_ENOUGH_DATA below this many teacher reviews.
    min_reviews_for_rates: int = 5


@dataclass(frozen=True)
class PipelineConfig:
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    focus: FocusConfig = field(default_factory=FocusConfig)
    attendance: AttendanceConfig = field(default_factory=AttendanceConfig)
    analytics: AnalyticsConfig = field(default_factory=AnalyticsConfig)
    enrollment: EnrollmentConfig = field(default_factory=EnrollmentConfig)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_env(cls):
        ident = IdentityConfig()
        ident = replace(
            ident,
            similarity_threshold=_env_float("FG_FACE_SIM_THRESHOLD", ident.similarity_threshold),
            min_margin=_env_float("FG_FACE_MIN_MARGIN", ident.min_margin),
        )
        beh = BehaviorConfig()
        beh = replace(
            beh,
            ear_threshold=_env_float("FG_EAR_THRESHOLD", beh.ear_threshold),
            drowsy_min_seconds=_env_float("FG_DROWSY_MIN_SECONDS", beh.drowsy_min_seconds),
            head_away_min_seconds=_env_float("FG_HEAD_AWAY_MIN_SECONDS", beh.head_away_min_seconds),
            phone_min_seconds=_env_float("FG_PHONE_MIN_SECONDS", beh.phone_min_seconds),
            phone_min_confidence=_env_float("FG_PHONE_MIN_CONFIDENCE", beh.phone_min_confidence),
            ear_closed_ratio=_env_float("FG_EAR_CLOSED_RATIO", beh.ear_closed_ratio),
            away_min_seconds=_env_float("FG_AWAY_MIN_SECONDS", beh.away_min_seconds),
            yaw_limit_deg=_env_float("FG_YAW_LIMIT_DEG", beh.yaw_limit_deg),
            pitch_limit_deg=_env_float("FG_PITCH_LIMIT_DEG", beh.pitch_limit_deg),
            yaw_center_deg=_env_float("FG_YAW_CENTER_DEG", beh.yaw_center_deg),
            pitch_center_deg=_env_float("FG_PITCH_CENTER_DEG", beh.pitch_center_deg),
        )
        att = AttendanceConfig(late_after_seconds=_env_float("FG_LATE_AFTER_SECONDS",
                                                             AttendanceConfig().late_after_seconds),
                               finalize_after_seconds=_env_float("FG_ATTENDANCE_FINALIZE_SECONDS", None))
        an = AnalyticsConfig()
        an = replace(an, watchlist_score_below=_env_float("FG_WATCHLIST_SCORE_BELOW", an.watchlist_score_below))
        return cls(identity=ident, behavior=beh, attendance=att, analytics=an)

    def with_behavior(self, **kwargs):
        return replace(self, behavior=replace(self.behavior, **kwargs))


DEFAULT_CONFIG = PipelineConfig()
