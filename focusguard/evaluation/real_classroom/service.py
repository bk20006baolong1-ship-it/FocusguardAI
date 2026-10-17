"""EvaluationService: structured real-classroom evaluation sessions.

Kept apart from production data: its own tables (evaluation_sessions,
evaluation_annotations), its own raw log folder, its own retention. It reads
the AI events of the linked class session but never writes to them.
"""
import json
import os
import re
from typing import List, Optional

from ...privacy.consent import SCOPE_EVALUATION
from .metrics import evaluate_package
from .models import (PACKAGE_FORMAT, REAL, clean_annotation, clean_metadata)
from .recorder import ObservationRecorder, read_jsonl, read_observations, write_jsonl
from .scenarios import SCENARIO_PACK_VERSION, get_scenario

ACTIVE = "ACTIVE"
ENDED = "ENDED"
_ID_RX = re.compile(r"^[0-9a-f]{32}$")


class EvaluationError(ValueError):
    pass


class EvaluationEnded(EvaluationError):
    """The evaluation session is no longer ACTIVE."""


class EvaluationService:
    def __init__(self, sm):
        self.sm = sm
        self.repo = sm.repo
        self._recorders = {}
        self._segments = {}     # standalone sessions: evaluation_session_id -> open segment

    @property
    def private_dir(self):
        return self.sm.privacy.evaluation_private_dir

    def run_dir(self, eid):
        """Private folder of one evaluation (numbers only; git-ignored; retention-deleted)."""
        if not _ID_RX.match(str(eid)):
            raise EvaluationError("invalid evaluation id")
        return os.path.join(self.private_dir, str(eid))

    def _obs_path(self, eid):
        return os.path.join(self.run_dir(eid), "observations.jsonl")

    def identity_frames_path(self, eid):
        return os.path.join(self.run_dir(eid), "identity_frames.jsonl")

    def performance_path(self, eid):
        return os.path.join(self.run_dir(eid), "performance.json")

    # --------------------------------------------------------------- reads
    def get(self, eid) -> Optional[dict]:
        if not eid or not _ID_RX.match(str(eid)):
            return None
        return self.repo.get_evaluation_session(eid)

    def list(self, class_id):
        return self.repo.list_evaluation_sessions(class_id=class_id)

    def consented_students(self, class_id):
        return [s for s in self.repo.get_students_in_class(class_id)
                if self.sm.consent.has_scope(s["student_id"], SCOPE_EVALUATION)]

    # -------------------------------------------------------------- writes
    def start(self, actor, class_id, data, camera_meta=None) -> dict:
        """``camera_meta``: camera_id / camera_name / camera_source_type / camera_vendor / camera_model
        of the camera used (CameraService.evaluation_metadata); never credentials or IP addresses."""
        try:
            meta = clean_metadata(data)
        except ValueError as exc:
            raise EvaluationError(str(exc))
        participants = self.consented_students(class_id)
        if meta["dataset_kind"] == REAL:
            if not meta["consent_verified"]:
                raise EvaluationError("Cần xác nhận đã có sự đồng ý của học sinh/phụ huynh trước khi đánh giá.")
            if not participants:
                raise EvaluationError("Chưa có học sinh nào trong lớp đồng ý tham gia đánh giá (phạm vi 'Tham gia "
                                      "đánh giá thử nghiệm').")
        now = self.sm.clock()
        rt = self.sm.class_runtime(class_id)
        row = dict(meta, class_id=int(class_id), class_session_id=rt.class_session_id if rt else None,
                   student_count=meta.get("student_count") or len(participants), started_at=now, ended_at=None,
                   status=ACTIVE, created_by=actor.user_id, scenario_pack_version=SCENARIO_PACK_VERSION,
                   consent_verified_by=actor.user_id if meta["consent_verified"] else None)
        for k in ("camera_id", "camera_name", "camera_source_type", "camera_vendor", "camera_model"):
            if camera_meta and camera_meta.get(k) is not None:
                row[k] = str(camera_meta[k])[:80]
        eid = self.repo.create_evaluation_session(row)
        if rt is not None:
            rec = ObservationRecorder(self._obs_path(eid))
            rt.hooks.append(rec)
            self._recorders[eid] = (rt, rec)
            if meta.get("activity_context"):
                self.sm.set_activity_context(class_id, meta["activity_context"], actor)
        elif meta.get("activity_context"):
            self._segments[eid] = {"context": meta["activity_context"], "started_at": now, "set_by": actor.user_id}
        return self.get(eid)

    def _require_active(self, eid):
        es = self.get(eid)
        if es is None:
            raise LookupError("evaluation not found")
        if es.get("status") != ACTIVE:
            raise EvaluationEnded("Buổi đánh giá đã kết thúc.")
        return es

    def set_context(self, actor, eid, context):
        es = self._require_active(eid)
        now = self.sm.clock()
        linked = self._recorders.get(eid)
        if linked and linked[0].active:
            seg = self.sm.set_activity_context(es["class_id"], context, actor)
        else:
            self._close_segment(eid, now)
            seg = {"context": context, "started_at": now, "set_by": actor.user_id}
            if context:
                self._segments[eid] = seg
        self.repo.update_evaluation_session(eid, {"activity_context": context})
        return seg

    def _close_segment(self, eid, now):
        seg = self._segments.pop(eid, None)
        if seg:
            self.repo.save_context_segment(dict(seg, ended_at=now, evaluation_session_id=eid))

    # ----------------------------------------------------------- scenarios
    def scenario_runs(self, eid) -> List[dict]:
        return self.repo.get_scenario_runs(eid)

    def open_scenario(self, eid) -> Optional[dict]:
        runs = [r for r in self.scenario_runs(eid) if r.get("ended_at") is None]
        return runs[-1] if runs else None

    def start_scenario(self, actor, eid, scenario_id, apply_context=False, note=None) -> dict:
        """Mark the start of a scripted scenario. The suggested activity context
        is applied only when the operator asks for it (a human choice)."""
        es = self._require_active(eid)
        sc = get_scenario(scenario_id)
        if sc is None:
            raise EvaluationError("Kịch bản không hợp lệ.")
        now = self.sm.clock()
        self._stop_open_scenario(eid, now)
        row = {"evaluation_session_id": eid, "scenario_id": scenario_id, "started_at": now, "ended_at": None,
               "started_by": actor.user_id, "note": (str(note).strip()[:300] or None) if note else None}
        row["scenario_run_id"] = self.repo.create_scenario_run(row)
        if apply_context and sc.get("context") and sc["context"] != es.get("activity_context"):
            self.set_context(actor, eid, sc["context"])
        return row

    def stop_scenario(self, actor, eid) -> Optional[dict]:
        self._require_active(eid)
        return self._stop_open_scenario(eid, self.sm.clock())

    def _stop_open_scenario(self, eid, now) -> Optional[dict]:
        run = self.open_scenario(eid)
        if run is None:
            return None
        self.repo.update_scenario_run(run["scenario_run_id"], {"ended_at": now})
        return dict(run, ended_at=now)

    # --------------------------------------------------------- annotations
    def annotate(self, actor, eid, data) -> dict:
        es = self.get(eid)
        if es is None:
            raise LookupError("evaluation not found")
        try:
            ann = clean_annotation(data, es, self.sm.clock())
        except ValueError as exc:
            raise EvaluationError(str(exc))
        enrolled = {s["student_id"] for s in self.repo.get_students_in_class(es["class_id"])}
        if ann["student_id"] not in enrolled:
            raise EvaluationError("Học sinh không thuộc lớp của buổi đánh giá.")
        if es.get("dataset_kind", REAL) == REAL and not self.sm.consent.has_scope(ann["student_id"], SCOPE_EVALUATION):
            raise EvaluationError("Học sinh này chưa đồng ý tham gia đánh giá; không ghi nhãn.")
        ann.update(evaluation_session_id=eid, annotated_by=actor.user_id)   # annotator from the session only
        ann["annotation_id"] = self.repo.create_annotation(ann)
        return ann

    def import_annotations(self, actor, eid, rows) -> dict:
        """Observer CSV rows ``student_id,label,start_s,end_s,note`` (seconds from the
        evaluation start). Every row goes through :meth:`annotate` (same consent,
        class and time checks as the web form). Returns counts + reasons."""
        es = self.get(eid)
        if es is None:
            raise LookupError("evaluation not found")
        t0 = float(es["started_at"])
        added, skipped = 0, []
        for i, r in enumerate(rows, start=2):
            label = str(r.get("label") or "").strip().upper()
            if not label:
                skipped.append(f"line {i}: empty label (not scored)")
                continue
            try:
                data = {"student_id": int(str(r.get("student_id")).strip()), "label": label,
                        "started_at": t0 + float(r.get("start_s")), "ended_at": t0 + float(r.get("end_s")),
                        "note": r.get("note")}
                self.annotate(actor, eid, data)
                added += 1
            except (TypeError, ValueError) as exc:
                skipped.append(f"line {i}: {exc}")
        return {"added": added, "skipped": skipped}

    def import_identity(self, eid, rows) -> dict:
        """Observer identity sheet ``track_id,student_id,start_s,end_s`` (STRANGER =
        not enrolled) applied to the recorded identity frames."""
        es = self.get(eid)
        if es is None:
            raise LookupError("evaluation not found")
        t0 = float(es["started_at"])
        sheet = []
        bad = 0
        for r in rows:
            try:
                raw = str(r.get("student_id")).strip().upper()
                gt = None if raw == "STRANGER" else int(raw)
                sheet.append((int(r.get("track_id")), gt, t0 + float(r.get("start_s")), t0 + float(r.get("end_s"))))
            except (TypeError, ValueError):
                bad += 1
        path = self.identity_frames_path(eid)
        frames = read_jsonl(path)
        labelled = 0
        for fr in frames:
            for tid, gt, a, b in sheet:
                if int(fr.get("track_id", -1)) == tid and a <= fr["t"] < b:
                    fr["gt_student_id"] = gt
                    labelled += 1
                    break
        if frames:
            write_jsonl(path, frames)
        return {"frames": len(frames), "labelled_frames": labelled, "unreadable_rows": bad}

    def save_field_recording(self, eid, identity_frames, performance) -> None:
        """Identity decisions per track and frame timing from the field tool."""
        write_jsonl(self.identity_frames_path(eid), identity_frames or [])
        os.makedirs(self.run_dir(eid), mode=0o700, exist_ok=True)
        with open(self.performance_path(eid), "w", encoding="utf-8") as f:
            json.dump(performance or {}, f)

    def end(self, actor, eid) -> dict:
        es = self._require_active(eid)
        now = self.sm.clock()
        linked = self._recorders.pop(eid, None)
        fields = {"status": ENDED, "ended_at": now}
        if linked:
            rt, rec = linked
            rec.close()
            if rec in rt.hooks:
                rt.hooks.remove(rec)
            if rec.actual_fps() is not None:
                fields["actual_fps"] = rec.actual_fps()
        self._stop_open_scenario(eid, now)
        self._close_segment(eid, now)
        self.repo.update_evaluation_session(eid, fields)
        return self.get(eid)

    # ------------------------------------------------------------ package
    def package(self, eid) -> dict:
        es = self.get(eid)
        if es is None:
            raise LookupError("evaluation not found")
        now = self.sm.clock()
        t0, t1 = es["started_at"], es.get("ended_at") or now
        allowed = None
        if es.get("dataset_kind", REAL) == REAL:
            allowed = {s["student_id"] for s in self.consented_students(es["class_id"])}
        keep = (lambda sid: True) if allowed is None else (lambda sid: sid in allowed)
        preds, segments = [], []
        cs = es.get("class_session_id")
        if cs is not None:
            for e in self.repo.get_focus_events(class_session_id=cs):
                st = e.get("start_time")
                if st is None or not keep(e.get("student_id")) or st > t1 or (e.get("end_time") or st) < t0:
                    continue
                preds.append({k: e.get(k) for k in ("id", "student_id", "type", "start_time", "end_time",
                                                     "duration_seconds", "confidence", "activity_context",
                                                     "interpreted_state", "penalty_multiplier", "metadata")})
            segments = [s for s in self.repo.get_context_segments(class_session_id=cs)]
            rt = self.sm.registry.class_runtime(es["class_id"])
            if rt is not None and rt.class_session_id == cs and rt.context_history:
                cur = rt.context_history[-1]
                if cur.get("ended_at") is None:
                    segments.append(dict(cur))
        segments += self.repo.get_context_segments(evaluation_session_id=eid)
        if eid in self._segments:
            segments.append(dict(self._segments[eid], ended_at=None))
        segs = []
        for s in segments:
            a, b = s.get("started_at"), s.get("ended_at") or t1
            if a is None or b < t0 or a > t1:
                continue
            segs.append({"context": s.get("context"), "started_at": max(a, t0), "ended_at": min(b, t1)})
        obs, frames = ([], [])
        try:
            obs, frames = read_observations(self._obs_path(eid))
        except EvaluationError:
            pass
        obs = [o for o in obs if keep(o.get("student_id"))]
        anns = [a for a in self.repo.get_annotations(evaluation_session_id=eid) if keep(a.get("student_id"))]
        meta = {k: es.get(k) for k in es if k not in ("created_by",)}
        performance = {"frame_timestamps": frames}
        identity_frames = []
        try:
            identity_frames = []
            for f in read_jsonl(self.identity_frames_path(eid)):
                gt = f.get("gt_student_id")
                if gt is not None and gt != "UNLABELLED" and not keep(gt):
                    continue                     # a non-consenting student's own frames are left out
                if f.get("pred_student_id") is not None and not keep(f["pred_student_id"]):
                    f = dict(f, pred_student_id=None, status="NOT_IN_EVALUATION")   # never reveal who it was
                identity_frames.append(f)
            if os.path.exists(self.performance_path(eid)):
                with open(self.performance_path(eid), "r", encoding="utf-8") as f:
                    recorded = json.load(f)
                # field-tool timing (per-frame latency) wins over the runtime frame clock
                performance = dict(performance, **{k: v for k, v in recorded.items() if v})
        except (EvaluationError, ValueError, OSError):
            pass
        scenarios = [{k: r.get(k) for k in ("scenario_id", "started_at", "ended_at")}
                     for r in self.repo.get_scenario_runs(eid)]
        return {
            "format": PACKAGE_FORMAT, "dataset_kind": es.get("dataset_kind") or REAL, "metadata": meta,
            "enrolled": sorted(allowed) if allowed is not None else
            [s["student_id"] for s in self.repo.get_students_in_class(es["class_id"])],
            "context_segments": sorted(segs, key=lambda s: s["started_at"]),
            "scenarios": scenarios,
            "annotations": anns, "predictions": preds, "observations": obs,
            "identity_frames": identity_frames, "performance": performance,
            "policy_version": self.sm.policy.version, "evaluated_until": t1,
        }

    def metrics(self, eid) -> dict:
        return evaluate_package(self.package(eid))
