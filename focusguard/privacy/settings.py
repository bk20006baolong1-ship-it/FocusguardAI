"""Privacy and retention configuration (environment variables, safe defaults).

| Variable                          | Default | Meaning                                         |
|-----------------------------------|---------|-------------------------------------------------|
| EVENT_EVIDENCE_ENABLED            | false   | keep short clips around AI events (OFF = metadata only) |
| EVENT_EVIDENCE_PRE_SECONDS        | 3       | seconds kept before the event onset             |
| EVENT_EVIDENCE_POST_SECONDS       | 5       | seconds kept after the event is confirmed       |
| EVENT_EVIDENCE_RETENTION_HOURS    | 24      | clip auto-deleted after this many hours         |
| EVENT_EVIDENCE_FPS                | 2       | frames per second kept in a clip                |
| EVENT_EVIDENCE_DIR                | instance/evidence | server-side folder (never under static/) |
| EVENT_EVIDENCE_PARENT_ACCESS      | false   | parents never see clips (no route exists)       |
| FACE_EMBEDDING_RETENTION_DAYS     | 365     | embedding deleted this long after enrolment     |
| EVENT_METADATA_RETENTION_DAYS     | 365     | AI events, snapshots and reviews older are deleted |
| EVALUATION_DATA_RETENTION_DAYS    | 90      | evaluation sessions / labels / raw logs deleted |
| CONSENT_VALIDITY_DAYS             | 365     | default consent expiry                          |
| REVOKE_DELETES_EVIDENCE           | true    | revoking consent also deletes that student's clips |
| RETENTION_SWEEP_MINUTES           | 10      | how often the server runs the retention sweep  |
"""
import os
from dataclasses import asdict, dataclass


def _bool(env, name, default):
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _num(env, name, default, minimum=None, maximum=None, cast=float):
    raw = env.get(name)
    try:
        v = cast(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        v = default
    if minimum is not None and v < minimum:
        v = default
    if maximum is not None and v > maximum:
        v = maximum
    return v


@dataclass(frozen=True)
class PrivacySettings:
    evidence_enabled: bool = False
    evidence_pre_seconds: float = 3.0
    evidence_post_seconds: float = 5.0
    evidence_retention_hours: float = 24.0
    evidence_fps: float = 2.0
    evidence_dir: str = os.path.join("instance", "evidence")
    evidence_parent_access: bool = False
    evidence_max_frames: int = 64
    face_embedding_retention_days: float = 365.0
    event_metadata_retention_days: float = 365.0
    evaluation_data_retention_days: float = 90.0
    consent_validity_days: float = 365.0
    revoke_deletes_evidence: bool = True
    retention_sweep_minutes: float = 10.0
    evaluation_private_dir: str = os.path.join("evaluation", "private")

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        d = cls()
        return cls(
            evidence_enabled=_bool(env, "EVENT_EVIDENCE_ENABLED", d.evidence_enabled),
            evidence_pre_seconds=_num(env, "EVENT_EVIDENCE_PRE_SECONDS", d.evidence_pre_seconds, 0, 30),
            evidence_post_seconds=_num(env, "EVENT_EVIDENCE_POST_SECONDS", d.evidence_post_seconds, 0, 30),
            evidence_retention_hours=_num(env, "EVENT_EVIDENCE_RETENTION_HOURS", d.evidence_retention_hours, 0.01, 24 * 30),
            evidence_fps=_num(env, "EVENT_EVIDENCE_FPS", d.evidence_fps, 0.5, 10),
            evidence_dir=env.get("EVENT_EVIDENCE_DIR") or d.evidence_dir,
            evidence_parent_access=False,     # not offered: no parent route serves clips
            face_embedding_retention_days=_num(env, "FACE_EMBEDDING_RETENTION_DAYS", d.face_embedding_retention_days, 1),
            event_metadata_retention_days=_num(env, "EVENT_METADATA_RETENTION_DAYS", d.event_metadata_retention_days, 1),
            evaluation_data_retention_days=_num(env, "EVALUATION_DATA_RETENTION_DAYS", d.evaluation_data_retention_days, 1),
            consent_validity_days=_num(env, "CONSENT_VALIDITY_DAYS", d.consent_validity_days, 1, 3650),
            revoke_deletes_evidence=_bool(env, "REVOKE_DELETES_EVIDENCE", d.revoke_deletes_evidence),
            retention_sweep_minutes=_num(env, "RETENTION_SWEEP_MINUTES", d.retention_sweep_minutes, 1, 24 * 60),
            evaluation_private_dir=env.get("EVALUATION_PRIVATE_DIR") or d.evaluation_private_dir,
        )

    def public_dict(self):
        d = asdict(self)
        d.pop("evidence_dir")
        d.pop("evaluation_private_dir")
        return d
