"""Storage for the BTC-feedback features, shared by SQLite and Firestore.

Domain methods (consents, audit log, teacher event reviews, evidence clips,
evaluation sessions and ground-truth annotations, activity-context segments)
are written ONCE in :class:`DomainTablesMixin` on top of five primitives that
each backend implements:

    _g_insert(table, row)              -> key
    _g_select(table, **equals)         -> [row]      (None values = no filter)
    _g_update(table, key, fields)
    _g_delete(table, **equals)         -> count
    _g_delete_before(table, column, value) -> count  (column < value)

So both backends support the same schema and the same contract tests
(tests/contracts/). The browser never talks to either backend directly.

Raw AI output is never mutated by these tables: reviews, annotations and
interpretations live next to ``focus_events``, keyed by ``event_id`` /
``episode_uid``.
"""
import json
import time
import uuid
from typing import Dict, List, Optional

# table -> (key column, [(column, sqlite declaration)])
TABLES: Dict[str, tuple] = {
    "consents": ("consent_id", [
        ("consent_id", "TEXT PRIMARY KEY"), ("student_id", "INTEGER NOT NULL"), ("guardian_name", "TEXT"),
        ("guardian_relationship", "TEXT"), ("student_is_adult", "INTEGER"),
        ("scope_face_recognition", "INTEGER"), ("scope_event_evidence", "INTEGER"), ("scope_evaluation", "INTEGER"),
        ("policy_version", "TEXT"), ("accepted_at", "REAL"), ("expires_at", "REAL"), ("revoked_at", "REAL"),
        ("revoked_by", "INTEGER"), ("revoke_reason", "TEXT"), ("created_by", "INTEGER"), ("created_at", "REAL"),
        ("evidence_note", "TEXT")]),
    "audit_log": ("audit_id", [
        ("audit_id", "TEXT PRIMARY KEY"), ("actor_id", "INTEGER"), ("actor_role", "TEXT"), ("student_id", "INTEGER"),
        ("action", "TEXT NOT NULL"), ("timestamp", "REAL"), ("metadata", "TEXT")]),
    "event_reviews": ("event_review_id", [
        ("event_review_id", "TEXT PRIMARY KEY"), ("event_id", "INTEGER NOT NULL"), ("student_id", "INTEGER"),
        ("class_session_id", "INTEGER"), ("class_id", "INTEGER"), ("verdict", "TEXT NOT NULL"),
        ("teacher_note", "TEXT"), ("reviewed_by", "INTEGER"), ("reviewed_at", "REAL")]),
    "evidence_clips": ("evidence_id", [
        ("evidence_id", "TEXT PRIMARY KEY"), ("episode_uid", "TEXT"), ("event_type", "TEXT"),
        ("student_id", "INTEGER"), ("class_session_id", "INTEGER"), ("class_id", "INTEGER"),
        ("clip_start", "REAL"), ("clip_end", "REAL"), ("frame_count", "INTEGER"), ("fps", "REAL"),
        ("file_name", "TEXT"), ("size_bytes", "INTEGER"), ("cropped", "INTEGER"), ("created_at", "REAL"),
        ("expires_at", "REAL"), ("deleted_at", "REAL"), ("deleted_by", "INTEGER"), ("delete_reason", "TEXT")]),
    "evaluation_sessions": ("evaluation_session_id", [
        ("evaluation_session_id", "TEXT PRIMARY KEY"), ("class_id", "INTEGER"), ("class_session_id", "INTEGER"),
        ("camera_position", "TEXT"), ("camera_angle", "TEXT"), ("resolution", "TEXT"), ("fps_target", "REAL"),
        ("device", "TEXT"), ("student_count", "INTEGER"), ("lighting_condition", "TEXT"),
        ("activity_context", "TEXT"), ("started_at", "REAL"), ("ended_at", "REAL"), ("notes", "TEXT"),
        ("consent_verified", "INTEGER"), ("consent_verified_by", "INTEGER"), ("dataset_kind", "TEXT"),
        ("status", "TEXT"), ("created_by", "INTEGER"), ("created_at", "REAL"), ("actual_fps", "REAL"),
        ("scenario_pack_version", "TEXT"), ("camera_id", "TEXT"), ("camera_name", "TEXT"),
        ("camera_source_type", "TEXT"), ("camera_vendor", "TEXT"), ("camera_model", "TEXT")]),
    "evaluation_scenarios": ("scenario_run_id", [
        ("scenario_run_id", "TEXT PRIMARY KEY"), ("evaluation_session_id", "TEXT NOT NULL"),
        ("scenario_id", "TEXT NOT NULL"), ("started_at", "REAL"), ("ended_at", "REAL"), ("started_by", "INTEGER"),
        ("note", "TEXT")]),
    "evaluation_annotations": ("annotation_id", [
        ("annotation_id", "TEXT PRIMARY KEY"), ("evaluation_session_id", "TEXT NOT NULL"), ("student_id", "INTEGER"),
        ("label", "TEXT NOT NULL"), ("started_at", "REAL"), ("ended_at", "REAL"), ("annotated_by", "INTEGER"),
        ("created_at", "REAL"), ("note", "TEXT")]),
    "context_segments": ("segment_id", [
        ("segment_id", "TEXT PRIMARY KEY"), ("class_session_id", "INTEGER"), ("session_id", "INTEGER"),
        ("class_id", "INTEGER"), ("evaluation_session_id", "TEXT"), ("context", "TEXT"), ("started_at", "REAL"),
        ("ended_at", "REAL"), ("set_by", "INTEGER")]),
    # Account camera settings. No credential-bearing URL is ever stored: host / port / path are
    # separate, the password / verification code only as ``encrypted_secret`` (focusguard/cameras/crypto.py).
    "cameras": ("camera_id", [
        ("camera_id", "TEXT PRIMARY KEY"), ("owner_user_id", "INTEGER"), ("name", "TEXT NOT NULL"),
        ("vendor", "TEXT"), ("model", "TEXT"), ("host", "TEXT"), ("port", "INTEGER"), ("protocol", "TEXT"),
        ("source_type", "TEXT"), ("stream_path", "TEXT"), ("stream_profile", "TEXT"), ("onvif_port", "INTEGER"),
        ("onvif_path", "TEXT"), ("device_index", "INTEGER"), ("username", "TEXT"), ("encrypted_secret", "TEXT"),
        ("is_default", "INTEGER"), ("is_shared", "INTEGER"), ("status", "TEXT"), ("resolution", "TEXT"),
        ("measured_fps", "REAL"), ("last_connected_at", "REAL"), ("last_tested_at", "REAL"),
        ("created_by", "INTEGER"), ("created_at", "REAL"), ("updated_at", "REAL"),
        # DISCOVERED (found by the server's own LAN discovery) | MANUAL (typed): the camera-network
        # policy (focusguard/cameras/netguard.py) is applied with this origin at every connect
        ("network_origin", "TEXT")]),
    # Multi-template face profiles (focusguard/face_profiles.py): a few good, diverse embeddings per
    # student. Never a photo. Same consent / retention lifecycle as the legacy users.face_embedding,
    # which is migrated into this table (source LEGACY).
    "face_profiles": ("face_profile_id", [
        ("face_profile_id", "TEXT PRIMARY KEY"), ("student_id", "INTEGER NOT NULL"), ("embedding", "TEXT"),
        ("quality_score", "REAL"), ("pose_bucket", "TEXT"), ("source", "TEXT"), ("active", "INTEGER"),
        ("class_session_id", "INTEGER"), ("binding_id", "TEXT"), ("created_at", "REAL"), ("updated_at", "REAL"),
        ("last_used_at", "REAL")]),
    # Session identity: which person track was which student, who confirmed it, and when it ended.
    # History is kept (a correction ends the old binding and starts a new one); raw AI events are not rewritten.
    "identity_bindings": ("binding_id", [
        ("binding_id", "TEXT PRIMARY KEY"), ("class_session_id", "INTEGER"), ("class_id", "INTEGER"),
        ("track_id", "INTEGER"), ("student_id", "INTEGER"), ("identity_source", "TEXT"), ("confidence", "REAL"),
        ("confirmed_by", "INTEGER"), ("confirmed_at", "REAL"), ("ended_at", "REAL"), ("end_reason", "TEXT"),
        ("correction_reason", "TEXT"), ("replaced_binding_id", "TEXT"),
        # session-owned lifecycle (focusguard/session_identity.py): a teacher-anchored binding whose
        # track disappeared is DETACHED (row stays open) and may be continued by a later binding
        ("teacher_anchored", "INTEGER"), ("derived_from_binding_id", "TEXT"), ("recovery_method", "TEXT"),
        ("detached_at", "REAL"), ("last_seen_at", "REAL")]),
    # Attendance evidence ledger (focusguard/session_runtime.py): every automatic / teacher-confirmed
    # sighting that supports a student's check-in, with its provenance. Never deleted by a correction:
    # a correction marks the evidence it invalidates as revoked (revoked_at / revoked_reason).
    "attendance_evidence": ("evidence_id", [
        ("evidence_id", "TEXT PRIMARY KEY"), ("class_session_id", "INTEGER"), ("class_id", "INTEGER"),
        ("student_id", "INTEGER"), ("track_id", "INTEGER"), ("binding_id", "TEXT"), ("identity_source", "TEXT"),
        ("attendance_source", "TEXT"), ("first_seen_at", "REAL"), ("confirmed_at", "REAL"),
        ("last_seen_at", "REAL"), ("late", "INTEGER"), ("revoked_at", "REAL"), ("revoked_reason", "TEXT"),
        ("revoked_by", "INTEGER"), ("created_at", "REAL")]),
    # Attendance decisions made by a teacher (key = "<class_session_id>:<student_id>").
    "attendance_records": ("attendance_record_id", [
        ("attendance_record_id", "TEXT PRIMARY KEY"), ("class_session_id", "INTEGER"), ("class_id", "INTEGER"),
        ("student_id", "INTEGER"), ("status", "TEXT"), ("source", "TEXT"), ("updated_by", "INTEGER"),
        ("updated_at", "REAL"), ("note", "TEXT")]),
    # One camera per class (key = class id); a shared camera may serve several classes.
    "class_cameras": ("class_camera_id", [
        ("class_camera_id", "TEXT PRIMARY KEY"), ("class_id", "INTEGER NOT NULL"), ("camera_id", "TEXT NOT NULL"),
        ("assigned_by", "INTEGER"), ("assigned_at", "REAL")]),
}
BOOL_COLS = {"student_is_adult", "scope_face_recognition", "scope_event_evidence", "scope_evaluation",
             "cropped", "consent_verified", "is_default", "is_shared", "active", "teacher_anchored", "late"}
JSON_COLS = {"metadata", "embedding"}
INDEXES = [
    ("consents", "student_id"), ("audit_log", "student_id"), ("audit_log", "action"),
    ("event_reviews", "event_id"), ("event_reviews", "class_id"), ("evidence_clips", "student_id"),
    ("evidence_clips", "episode_uid"), ("evaluation_annotations", "evaluation_session_id"),
    ("evaluation_scenarios", "evaluation_session_id"),
    ("context_segments", "class_session_id"),
    ("cameras", "owner_user_id"), ("class_cameras", "camera_id"), ("face_profiles", "student_id"),
    ("identity_bindings", "class_session_id"), ("attendance_records", "class_session_id"),
    ("attendance_evidence", "class_session_id"),
]
# Extra focus_events fields written next to the raw observed type.
FOCUS_EVENT_EXTRA = [("activity_context", "TEXT"), ("interpreted_state", "TEXT"), ("penalty_multiplier", "REAL"),
                     ("policy_version", "TEXT"), ("episode_uid", "TEXT")]


def check_retention_column(table, column):
    """Same rule on both backends: retention may only cut on a known column."""
    allowed = {c for c, _ in TABLES.get(table, (None, []))[1]}
    if table in ("focus_events", "focus_snapshots"):
        allowed = {"start_time", "timestamp"}
    if column not in allowed:
        raise ValueError("unknown retention column")


def new_id() -> str:
    return uuid.uuid4().hex


def _clean(table, row: dict) -> dict:
    cols = {c for c, _ in TABLES[table][1]}
    out = {}
    for k, v in row.items():
        if k not in cols:
            continue
        if k in BOOL_COLS and v is not None:
            v = bool(v)
        out[k] = v
    return out


class DomainTablesMixin:
    """Backend-independent domain methods (both repositories inherit this)."""

    # ----------------------------------------------------------- consents
    def create_consent(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("consent_id", new_id())
        row.setdefault("created_at", time.time())
        return self._g_insert("consents", _clean("consents", row))

    def get_consents(self, student_id=None) -> List[dict]:
        rows = self._g_select("consents", student_id=None if student_id is None else int(student_id))
        return sorted(rows, key=lambda r: (r.get("created_at") or 0, r.get("accepted_at") or 0))

    def get_consent(self, consent_id) -> Optional[dict]:
        rows = self._g_select("consents", consent_id=str(consent_id))
        return rows[0] if rows else None

    def update_consent(self, consent_id, fields: dict):
        allowed = {"revoked_at", "revoked_by", "revoke_reason"}     # a consent record is otherwise immutable
        self._g_update("consents", str(consent_id), {k: v for k, v in fields.items() if k in allowed})

    # ---------------------------------------------------------- audit log
    def create_audit(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("audit_id", new_id())
        row.setdefault("timestamp", time.time())
        return self._g_insert("audit_log", _clean("audit_log", row))

    def get_audit(self, student_id=None, action=None, limit: int = 500) -> List[dict]:
        rows = self._g_select("audit_log", student_id=None if student_id is None else int(student_id), action=action)
        rows.sort(key=lambda r: -(r.get("timestamp") or 0))
        return rows[:limit]

    # ------------------------------------------------------ event reviews
    def create_event_review(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("event_review_id", new_id())
        row.setdefault("reviewed_at", time.time())
        return self._g_insert("event_reviews", _clean("event_reviews", row))

    def get_event_reviews(self, event_id=None, class_id=None, student_id=None, class_session_id=None) -> List[dict]:
        def i(v):
            return None if v is None else int(v)
        rows = self._g_select("event_reviews", event_id=i(event_id), class_id=i(class_id), student_id=i(student_id),
                              class_session_id=i(class_session_id))
        return sorted(rows, key=lambda r: r.get("reviewed_at") or 0)

    def delete_event_reviews(self, event_id) -> int:
        return self._g_delete("event_reviews", event_id=int(event_id))

    # ----------------------------------------------------- evidence clips
    def create_evidence(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("evidence_id", new_id())
        row.setdefault("created_at", time.time())
        return self._g_insert("evidence_clips", _clean("evidence_clips", row))

    def get_evidence(self, evidence_id) -> Optional[dict]:
        rows = self._g_select("evidence_clips", evidence_id=str(evidence_id))
        return rows[0] if rows else None

    def list_evidence(self, student_id=None, class_id=None, episode_uid=None, include_deleted=False) -> List[dict]:
        rows = self._g_select("evidence_clips", student_id=None if student_id is None else int(student_id),
                              class_id=None if class_id is None else int(class_id), episode_uid=episode_uid)
        if not include_deleted:
            rows = [r for r in rows if not r.get("deleted_at")]
        return sorted(rows, key=lambda r: r.get("created_at") or 0)

    def mark_evidence_deleted(self, evidence_id, deleted_at, deleted_by=None, reason=None):
        self._g_update("evidence_clips", str(evidence_id),
                       {"deleted_at": deleted_at, "deleted_by": deleted_by, "delete_reason": reason, "file_name": None})

    # ------------------------------------------------ evaluation sessions
    def create_evaluation_session(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("evaluation_session_id", new_id())
        row.setdefault("created_at", time.time())
        return self._g_insert("evaluation_sessions", _clean("evaluation_sessions", row))

    def get_evaluation_session(self, evaluation_session_id) -> Optional[dict]:
        rows = self._g_select("evaluation_sessions", evaluation_session_id=str(evaluation_session_id))
        return rows[0] if rows else None

    def list_evaluation_sessions(self, class_id=None) -> List[dict]:
        rows = self._g_select("evaluation_sessions", class_id=None if class_id is None else int(class_id))
        return sorted(rows, key=lambda r: -(r.get("created_at") or 0))

    def update_evaluation_session(self, evaluation_session_id, fields: dict):
        fields = _clean("evaluation_sessions", fields)
        fields.pop("evaluation_session_id", None)
        self._g_update("evaluation_sessions", str(evaluation_session_id), fields)

    def delete_evaluation_session(self, evaluation_session_id) -> int:
        eid = str(evaluation_session_id)
        n = self._g_delete("evaluation_annotations", evaluation_session_id=eid)
        n += self._g_delete("context_segments", evaluation_session_id=eid)
        n += self._g_delete("evaluation_scenarios", evaluation_session_id=eid)
        n += self._g_delete("evaluation_sessions", evaluation_session_id=eid)
        return n

    # ---------------------------------------------- ground-truth labels
    def create_annotation(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("annotation_id", new_id())
        row.setdefault("created_at", time.time())
        return self._g_insert("evaluation_annotations", _clean("evaluation_annotations", row))

    def get_annotations(self, evaluation_session_id=None, student_id=None) -> List[dict]:
        rows = self._g_select("evaluation_annotations",
                              evaluation_session_id=None if evaluation_session_id is None else str(evaluation_session_id),
                              student_id=None if student_id is None else int(student_id))
        return sorted(rows, key=lambda r: (r.get("started_at") or 0, r.get("created_at") or 0))

    # -------------------------------------------- scenario runs (windows)
    def create_scenario_run(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("scenario_run_id", new_id())
        return self._g_insert("evaluation_scenarios", _clean("evaluation_scenarios", row))

    def get_scenario_runs(self, evaluation_session_id) -> List[dict]:
        rows = self._g_select("evaluation_scenarios", evaluation_session_id=str(evaluation_session_id))
        return sorted(rows, key=lambda r: r.get("started_at") or 0)

    def update_scenario_run(self, scenario_run_id, fields: dict):
        allowed = {"ended_at", "note"}       # which scenario and when it started never change
        self._g_update("evaluation_scenarios", str(scenario_run_id), {k: v for k, v in fields.items() if k in allowed})

    # --------------------------------------------------- context segments
    def save_context_segment(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("segment_id", new_id())
        return self._g_insert("context_segments", _clean("context_segments", row))

    def get_context_segments(self, class_session_id=None, evaluation_session_id=None, class_id=None) -> List[dict]:
        rows = self._g_select("context_segments",
                              class_session_id=None if class_session_id is None else int(class_session_id),
                              evaluation_session_id=evaluation_session_id,
                              class_id=None if class_id is None else int(class_id))
        return sorted(rows, key=lambda r: r.get("started_at") or 0)

    # ---------------------------------------------------------- retention
    def delete_class_context_segments_before(self, cutoff: float) -> int:
        """Class-session activity segments older than ``cutoff``. Segments of an
        evaluation session are kept: they go with the evaluation (its own retention)."""
        n = 0
        for r in self._g_select("context_segments"):
            if r.get("evaluation_session_id") is None and r.get("started_at") is not None and r["started_at"] < cutoff:
                n += self._g_delete("context_segments", segment_id=r["segment_id"])
        return n

    def delete_rows_before(self, table: str, column: str, cutoff: float) -> int:
        return self._g_delete_before(table, column, cutoff)

    # ------------------------------------------------------- face profiles
    def add_face_template(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("face_profile_id", new_id())
        now = time.time()
        row.setdefault("created_at", now)
        row.setdefault("updated_at", row["created_at"])
        row.setdefault("active", True)
        row["student_id"] = int(row["student_id"])
        row["embedding"] = [float(x) for x in row["embedding"]]
        return self._g_insert("face_profiles", _clean("face_profiles", row))

    def list_face_templates(self, student_id=None, active=True) -> List[dict]:
        rows = self._g_select("face_profiles", student_id=None if student_id is None else int(student_id),
                              active=None if active is None else bool(active))
        return sorted(rows, key=lambda r: (r.get("created_at") or 0, r["face_profile_id"]))

    def update_face_template(self, face_profile_id, fields: dict):
        fields = {k: v for k, v in _clean("face_profiles", fields).items()
                  if k not in ("face_profile_id", "student_id", "embedding", "created_at")}
        fields.setdefault("updated_at", time.time())
        self._g_update("face_profiles", str(face_profile_id), fields)

    def delete_face_template(self, face_profile_id) -> int:
        return self._g_delete("face_profiles", face_profile_id=str(face_profile_id))

    def delete_face_templates(self, student_id) -> int:
        return self._g_delete("face_profiles", student_id=int(student_id))

    def delete_face_templates_for_binding(self, binding_id) -> int:
        return self._g_delete("face_profiles", binding_id=str(binding_id)) if binding_id else 0

    def face_template_counts(self) -> Dict[int, int]:
        out: Dict[int, int] = {}
        for r in self._g_select("face_profiles", active=True):
            out[int(r["student_id"])] = out.get(int(r["student_id"]), 0) + 1
        return out

    # --------------------------------------------------- identity bindings
    def create_identity_binding(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("binding_id", new_id())
        return self._g_insert("identity_bindings", _clean("identity_bindings", row))

    def end_identity_binding(self, binding_id, ended_at, reason=None):
        self._g_update("identity_bindings", str(binding_id), {"ended_at": ended_at, "end_reason": reason})

    def update_identity_binding(self, binding_id, fields: dict):
        allowed = {"detached_at", "last_seen_at"}               # lifecycle fields only; history is not rewritten
        data = {k: v for k, v in (fields or {}).items() if k in allowed}
        if data:
            self._g_update("identity_bindings", str(binding_id), data)

    def list_identity_bindings(self, class_session_id=None, student_id=None) -> List[dict]:
        rows = self._g_select("identity_bindings",
                              class_session_id=None if class_session_id is None else int(class_session_id),
                              student_id=None if student_id is None else int(student_id))
        return sorted(rows, key=lambda r: (r.get("confirmed_at") or 0, r["binding_id"]))

    # ------------------------------------------------- attendance evidence
    def save_attendance_evidence(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("evidence_id", new_id())
        row.setdefault("created_at", time.time())
        return self._g_insert("attendance_evidence", _clean("attendance_evidence", row))

    def update_attendance_evidence(self, evidence_id, fields: dict):
        allowed = {"last_seen_at", "revoked_at", "revoked_reason", "revoked_by"}     # append-only otherwise
        data = {k: v for k, v in (fields or {}).items() if k in allowed}
        if data:
            self._g_update("attendance_evidence", str(evidence_id), data)

    def list_attendance_evidence(self, class_session_id=None, student_id=None) -> List[dict]:
        rows = self._g_select("attendance_evidence",
                              class_session_id=None if class_session_id is None else int(class_session_id),
                              student_id=None if student_id is None else int(student_id))
        return sorted(rows, key=lambda r: (r.get("confirmed_at") or 0, r["evidence_id"]))

    # -------------------------------------------------- attendance records
    def save_attendance_record(self, row: dict) -> str:
        key = f"{int(row['class_session_id'])}:{int(row['student_id'])}"
        self._g_delete("attendance_records", attendance_record_id=key)
        return self._g_insert("attendance_records", _clean("attendance_records", dict(row, attendance_record_id=key)))

    def get_attendance_records(self, class_session_id=None, student_id=None) -> List[dict]:
        return self._g_select("attendance_records",
                              class_session_id=None if class_session_id is None else int(class_session_id),
                              student_id=None if student_id is None else int(student_id))

    # ------------------------------------------------------------ cameras
    def create_camera(self, row: dict) -> str:
        row = dict(row)
        row.setdefault("camera_id", new_id())
        now = time.time()
        row.setdefault("created_at", now)
        row.setdefault("updated_at", now)
        return self._g_insert("cameras", _clean("cameras", row))

    def get_camera(self, camera_id) -> Optional[dict]:
        rows = self._g_select("cameras", camera_id=str(camera_id))
        return rows[0] if rows else None

    def list_cameras(self, owner_user_id=None, is_shared=None) -> List[dict]:
        rows = self._g_select("cameras", owner_user_id=None if owner_user_id is None else int(owner_user_id),
                              is_shared=None if is_shared is None else bool(is_shared))
        return sorted(rows, key=lambda r: (r.get("created_at") or 0, r["camera_id"]))

    def update_camera(self, camera_id, fields: dict):
        fields = {k: v for k, v in _clean("cameras", fields).items() if k not in ("camera_id", "created_at")}
        fields.setdefault("updated_at", time.time())
        self._g_update("cameras", str(camera_id), fields)

    def delete_camera(self, camera_id) -> int:
        self._g_delete("class_cameras", camera_id=str(camera_id))
        return self._g_delete("cameras", camera_id=str(camera_id))

    def set_class_camera(self, class_id, camera_id, assigned_by=None, assigned_at=None):
        key = str(int(class_id))
        self._g_delete("class_cameras", class_camera_id=key)
        if camera_id is None:
            return None
        return self._g_insert("class_cameras", _clean("class_cameras", {
            "class_camera_id": key, "class_id": int(class_id), "camera_id": str(camera_id),
            "assigned_by": assigned_by, "assigned_at": assigned_at if assigned_at is not None else time.time()}))

    def get_class_camera(self, class_id) -> Optional[dict]:
        rows = self._g_select("class_cameras", class_camera_id=str(int(class_id)))
        return rows[0] if rows else None

    def list_class_cameras(self, camera_id=None) -> List[dict]:
        rows = self._g_select("class_cameras", camera_id=None if camera_id is None else str(camera_id))
        return sorted(rows, key=lambda r: r["class_id"])


class SQLiteTablesMixin:
    def _init_extension_tables(self):
        for table, (_key, cols) in TABLES.items():
            self._execute(f"CREATE TABLE IF NOT EXISTS {table} ("
                          + ", ".join(f"{c} {d}" for c, d in cols) + ")")
            for c, d in cols:     # forward migration for tables created by an older version
                self._ensure_column(table, c, d.replace(" PRIMARY KEY", "").replace(" NOT NULL", ""))
        for table, col in INDEXES:
            self._execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_{col} ON {table}({col})")
        for col, decl in FOCUS_EVENT_EXTRA:
            self._ensure_column("focus_events", col, decl)
        self._ensure_column("users", "face_enrolled_at", "REAL")
        self._execute("CREATE INDEX IF NOT EXISTS ix_events_uid ON focus_events(episode_uid)")

    @staticmethod
    def _decode(row: dict) -> dict:
        out = dict(row)
        for k in list(out):
            if k in BOOL_COLS and out[k] is not None:
                out[k] = bool(out[k])
            elif k in JSON_COLS:
                try:
                    out[k] = json.loads(out[k]) if out[k] else {}
                except (TypeError, ValueError):
                    out[k] = {}
        return out

    def _g_insert(self, table, row):
        data = dict(row)
        for k in JSON_COLS & set(data):
            data[k] = json.dumps(data[k] or {}, ensure_ascii=False, default=str)
        for k in BOOL_COLS & set(data):
            data[k] = None if data[k] is None else int(bool(data[k]))
        cols = list(data)
        self._execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                      tuple(data[c] for c in cols))
        return row[TABLES[table][0]]

    def _g_select(self, table, **equals):
        where = [(k, v) for k, v in equals.items() if v is not None]
        sql = f"SELECT * FROM {table}" + (" WHERE " + " AND ".join(f"{k} = ?" for k, _ in where) if where else "")
        return [self._decode(r) for r in self._execute(sql, tuple(v for _, v in where), fetch="all")]

    def _g_update(self, table, key, fields):
        if not fields:
            return
        data = dict(fields)
        for k in BOOL_COLS & set(data):
            data[k] = None if data[k] is None else int(bool(data[k]))
        sets = ", ".join(f"{k} = ?" for k in data)
        self._execute(f"UPDATE {table} SET {sets} WHERE {TABLES[table][0]} = ?", tuple(data.values()) + (key,))

    def _g_delete(self, table, **equals):
        where = [(k, v) for k, v in equals.items() if v is not None]
        if not where:
            raise ValueError("refusing to delete a whole table")
        clause = " AND ".join(f"{k} = ?" for k, _ in where)
        n = self._execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {clause}", tuple(v for _, v in where),
                          fetch="one")["n"]
        self._execute(f"DELETE FROM {table} WHERE {clause}", tuple(v for _, v in where))
        return n

    def _g_delete_before(self, table, column, cutoff):
        check_retention_column(table, column)
        n = self._execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} IS NOT NULL AND {column} < ?",
                          (cutoff,), fetch="one")["n"]
        self._execute(f"DELETE FROM {table} WHERE {column} IS NOT NULL AND {column} < ?", (cutoff,))
        return n


class FirestoreTablesMixin:
    def _g_insert(self, table, row):
        key = str(row[TABLES[table][0]])
        self._col(table).document(key).set(dict(row))
        return row[TABLES[table][0]]

    def _g_select(self, table, **equals):
        out = []
        for d in self._docs(table, **equals):
            for c, _ in TABLES[table][1]:
                d.setdefault(c, None)
            if "metadata" in d and d["metadata"] is None and table == "audit_log":
                d["metadata"] = {}
            out.append(d)
        return out

    def _g_update(self, table, key, fields):
        if fields:
            self._col(table).document(str(key)).update(dict(fields))

    def _g_delete(self, table, **equals):
        if not any(v is not None for v in equals.values()):
            raise ValueError("refusing to delete a whole collection")
        q = self._col(table)
        for k, v in equals.items():
            if v is not None:
                q = q.where(k, "==", v)
        n = 0
        for d in q.stream():
            d.reference.delete()
            n += 1
        return n

    def _g_delete_before(self, table, column, cutoff):
        check_retention_column(table, column)
        n = 0
        for d in self._col(table).where(column, "<", cutoff).stream():
            d.reference.delete()
            n += 1
        return n
