"""Real-classroom evaluation report (finalize command and field tool).

Builds the shareable report of one evaluation session: student ids, labels,
timings and AI event metadata only. Free-text notes stay private; names,
images, video, embeddings and clips are never included.
"""
import csv
import json
import os
import subprocess
import time

from .metrics import NOT_ENOUGH_DATA, evaluate_package
from .models import GT_LABELS_VI
from .scenarios import SCENARIOS, SCENARIO_PACK_VERSION

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

PUBLIC_META = ("evaluation_session_id", "class_id", "camera_position", "camera_angle", "resolution", "fps_target",
               "actual_fps", "device", "student_count", "lighting_condition", "activity_context", "started_at",
               "ended_at", "consent_verified", "dataset_kind", "status", "scenario_pack_version")
OUTPUTS = ("evaluation-report.json", "evaluation-summary.md", "confusion-matrix.json", "identity-report.json",
           "performance-report.json", "metrics.csv", "confusion-matrix.csv", "scenarios.csv")


def git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return None


def fmt(v):
    if v == NOT_ENOUGH_DATA:
        return "NOT_ENOUGH_DATA"
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".")
    return str(v)


def build_report(pkg, sm):
    report = evaluate_package(pkg)
    meta = {k: (pkg.get("metadata") or {}).get(k) for k in PUBLIC_META}
    meta["notes_present"] = bool((pkg.get("metadata") or {}).get("notes"))     # free text stays private
    perf = dict(report["performance"])
    perf["actual_fps_recorded"] = meta.get("actual_fps")
    kind = meta.get("dataset_kind") or pkg.get("dataset_kind")
    if kind != "REAL":
        perf["benchmark_kind"] = "SIMULATION_NO_DETECTOR"      # numbers here never describe real hardware
    elif (pkg.get("performance") or {}).get("frame_latency_ms"):
        perf["benchmark_kind"] = "FULL_DETECTOR_CAMERA"
    else:
        perf["benchmark_kind"] = "RUNTIME_FRAME_CLOCK"
    teacher_review = None
    if sm is not None and meta.get("class_id") is not None:
        teacher_review = sm.reviews.class_feedback_metrics(meta["class_id"],
                                                           min_reviews=sm.config.analytics.min_reviews_for_rates)
    real = bool(report["is_real_world_evidence"])
    out = {
        "format": "focusguard-real-classroom-report/1",
        "generated_at": time.time(), "git_sha": git_sha(),
        "policy_version": pkg.get("policy_version"), "scenario_pack_version": SCENARIO_PACK_VERSION,
        "metadata": meta,
        "participants": {"students_in_package": len(pkg.get("enrolled") or []),
                         "annotated_students": len({a["student_id"] for a in pkg.get("annotations") or []})},
        "duration_seconds": (round(meta["ended_at"] - meta["started_at"], 1)
                             if meta.get("ended_at") and meta.get("started_at") else None),
        "real_world_evidence": real,
        "verdict": "REAL_WORLD_EVIDENCE" if real else ("NOT_ENOUGH_DATA" if report["status"] == NOT_ENOUGH_DATA
                                                       else "NOT_REAL_WORLD_DATA"),
        "metrics": report, "performance": perf, "teacher_review": teacher_review,
        "privacy": {"contains": "student ids, labels, timings and AI event metadata only",
                    "excluded": ["names", "free-text notes", "images", "video", "face embeddings", "evidence clips"]},
    }
    return out


def summary_md(r):
    m, meta = r["metrics"], r["metadata"]
    L = [f"# Báo cáo đánh giá lớp học thật — {meta.get('evaluation_session_id')}", "",
         f"* Loại dữ liệu: **{meta.get('dataset_kind')}** · Bằng chứng thực tế: **{'CÓ' if r['real_world_evidence'] else 'CHƯA'}**"
         f" (`{r['verdict']}`)",
         f"* Commit: `{r.get('git_sha')}` · chính sách `{r.get('policy_version')}` · bộ kịch bản `{r['scenario_pack_version']}`",
         "* Số liệu chỉ áp dụng cho đúng buổi, camera và lớp này. Tín hiệu AI không phải kết luận sư phạm.", "",
         "## 1. Môi trường", "",
         "| Hạng mục | Giá trị |", "|---|---|"]
    for k in ("camera_position", "camera_angle", "resolution", "fps_target", "actual_fps", "device",
              "lighting_condition", "student_count"):
        L.append(f"| {k} | {fmt(meta.get(k))} |")
    L += [f"| duration_seconds | {fmt(r.get('duration_seconds'))} |",
          f"| consent_verified | {meta.get('consent_verified')} |", "",
          "## 2. Người tham gia", "",
          f"* Học sinh trong gói dữ liệu (dữ liệu REAL: chỉ học sinh đã đồng ý phạm vi đánh giá): "
          f"{r['participants']['students_in_package']}",
          f"* Học sinh có nhãn thực tế: {r['participants']['annotated_students']}",
          f"* Thời gian có nhãn (cộng theo học sinh): {m['behavior']['annotated_student_seconds']} giây", "",
          "## 3. Kịch bản", ""]
    sc = m["scenarios"]
    L.append(f"Đã chạy {len(sc['run'])}/{sc['pack_scenarios']} kịch bản; có nhãn: {len(sc['run_and_labelled'])}.")
    L += ["", "| Kịch bản | Lần chạy | Giây | Giây-HS có nhãn | Tín hiệu AI | Cảnh báo sau hoạt động |", "|---|---|---|---|---|---|"]
    titles = {s["scenario_id"]: s["title"] for s in SCENARIOS}
    for sid in [s["scenario_id"] for s in SCENARIOS]:
        e = sc["per_scenario"].get(sid)
        if e is None:
            L.append(f"| {sid} {titles[sid]} | 0 | — | — | — | — |")
        else:
            L.append(f"| {sid} {titles[sid]} | {e['runs']} | {e['seconds']} | {e['annotated_student_seconds']} | "
                     f"{json.dumps(e['ai_signals'])} | {json.dumps(e['ai_alerts_context_aware'])} |")
    for view, title in (("per_behavior", "4. Chỉ số theo tín hiệu (raw: mọi tín hiệu AI)"),
                        ("per_behavior_context_aware", "5. Chỉ số theo tín hiệu (sau khi áp dụng hoạt động lớp)")):
        L += ["", f"## {title}", "",
              "| Tín hiệu | Precision | Recall | F1 | TP | FP | FN | FP/giờ | Trễ phát hiện p50 (s) | Sai số thời lượng p50 (s) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for beh, x in m["behavior"][view].items():
            lat = x["event_onset_latency_seconds"]
            dur = x["event_duration_error_seconds"]
            L.append(f"| {beh} | {fmt(x['precision'])} | {fmt(x['recall'])} | {fmt(x['f1'])} | {x['true_positives']} | "
                     f"{x['false_positives']} | {x['false_negatives']} | {fmt(x['false_positives_per_hour'])} | "
                     f"{fmt(lat if lat == NOT_ENOUGH_DATA else lat['p50'])} | {fmt(dur if dur == NOT_ENOUGH_DATA else dur['p50'])} |")
    L += ["", "## 6. Báo nhầm và bỏ sót", ""]
    for beh, x in m["behavior"]["per_behavior_context_aware"].items():
        if x["false_positives"] or x["false_negatives"]:
            L.append(f"* {beh}: {x['false_positives']} báo nhầm (theo nhãn thực tế: "
                     f"{json.dumps(x['false_positives_by_ground_truth_label'], ensure_ascii=False)}; theo hoạt động: "
                     f"{json.dumps(x['false_positives_by_activity_context'], ensure_ascii=False)}), {x['false_negatives']} bỏ sót")
    if L[-1] == "":
        L.append("* Không có (hoặc chưa có nhãn).")
    cm = m["confusion_matrix_context_aware"]
    L += ["", "## 7. Ma trận nhầm lẫn (giây, sau khi áp dụng hoạt động)", "",
          "| Nhãn thực tế \\ AI | " + " | ".join(cm["columns"]) + " |", "|---" * (len(cm["columns"]) + 1) + "|"]
    for lab, row in cm["rows"].items():
        if any(row.values()):
            L.append(f"| {GT_LABELS_VI.get(lab, lab)} | " + " | ".join(str(row[c]) for c in cm["columns"]) + " |")
    ident = m["identity"]
    L += ["", "## 8. Danh tính", ""]
    if ident.get("status") == "OK":
        for k in ("frames", "identity_match_accuracy", "identity_switch_count", "unknown_rate", "wrong_assignments",
                  "duplicate_identity_frames", "stranger_frames", "stranger_false_accept_rate", "reappearances",
                  "recovery_rate"):
            L.append(f"* {k}: {fmt(ident.get(k))}")
    else:
        L.append(f"* NOT_ENOUGH_DATA ({ident.get('frames', 0)}/{ident.get('required_frames')} khung hình có bảng danh tính)")
    p = r["performance"]
    L += ["", "## 9. Hiệu năng", "", f"* Loại đo: `{p['benchmark_kind']}`"]
    if p.get("status") == "OK":
        L += [f"* FPS thực: {fmt(p.get('fps'))} (recorder: {fmt(p.get('actual_fps_recorded'))})",
              f"* Độ trễ mỗi khung hình p50/p95: {fmt(p.get('latency_p50_ms'))} / {fmt(p.get('latency_p95_ms'))} ms",
              f"* Độ trễ danh tính p95: {fmt(p.get('identity_latency_p95_ms'))} ms"]
    else:
        L.append(f"* NOT_ENOUGH_DATA ({p.get('frames', 0)} khung hình)")
    L += ["", "## 10. Giáo viên xem lại", ""]
    tr = r.get("teacher_review")
    if tr:
        for beh, v in tr["per_behavior"].items():
            L.append(f"* {beh}: đã xem {v['reviewed']}/{v['ai_events']}, xác nhận đúng {fmt(v['confirmed_rate'])}, "
                     f"báo nhầm {fmt(v['false_positive_rate'])}")
    else:
        L.append("* Không có (gói ngoại tuyến).")
    L += ["", "## 11. Hiệu chỉnh", "",
          "* Chạy `python scripts/calibrate_behavior.py --evaluation-session "
          f"{meta.get('evaluation_session_id')} --grid full` (chỉ đề xuất, không tự áp dụng)."]
    if r.get("calibration"):
        c = r["calibration"]
        L.append(f"* Kết quả: {c['status']}, delta = {json.dumps(c['delta'])} (applied={c['applied']})")
    L += ["", "## 12. Quyền riêng tư", "",
          "* Báo cáo chỉ có mã học sinh, nhãn, thời gian và siêu dữ liệu sự kiện AI.",
          "* Không có tên, ghi chú tự do, ảnh, video, vector khuôn mặt hay đoạn minh chứng (đã quét tự động).",
          "", "## 13. Giới hạn", "",
          "* Một buổi, một camera, một lớp: không khái quát cho mọi lớp học.",
          "* Nhãn thực tế do người quan sát gán; độ lệch giữa hai người quan sát cần được ghi riêng.",
          "* `NOT_ENOUGH_DATA` nghĩa là chưa đủ dữ liệu, không phải 0%.",
          "", "## 14. Kết luận", "",
          ("* Đủ điều kiện làm bằng chứng thực tế cho cấu hình này." if r["real_world_evidence"] else
           "* CHƯA đủ điều kiện làm bằng chứng độ chính xác thực tế (cần dữ liệu REAL có đồng ý và đủ nhãn)."), ""]
    return "\n".join(L)


def write_csvs(r, out_dir):
    m = r["metrics"]["behavior"]
    with open(os.path.join(out_dir, "metrics.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["view", "behavior", "precision", "recall", "f1", "true_positives", "false_positives",
                    "false_negatives", "false_positives_per_hour"])
        for view in ("per_behavior", "per_behavior_context_aware"):
            for beh, x in m[view].items():
                w.writerow([view, beh, x["precision"], x["recall"], x["f1"], x["true_positives"],
                            x["false_positives"], x["false_negatives"], x["false_positives_per_hour"]])
    cm = r["metrics"]["confusion_matrix_context_aware"]
    with open(os.path.join(out_dir, "confusion-matrix.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ground_truth"] + cm["columns"])
        for lab, row in cm["rows"].items():
            w.writerow([lab] + [row[c] for c in cm["columns"]])
    sc = r["metrics"]["scenarios"]
    with open(os.path.join(out_dir, "scenarios.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario_id", "runs", "seconds", "annotated_student_seconds", "ai_signals", "ai_alerts_context_aware"])
        for s in SCENARIOS:
            e = sc["per_scenario"].get(s["scenario_id"]) or {}
            w.writerow([s["scenario_id"], e.get("runs", 0), e.get("seconds", 0), e.get("annotated_student_seconds", 0),
                        json.dumps(e.get("ai_signals", {})), json.dumps(e.get("ai_alerts_context_aware", {}))])


def write_outputs(r, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    def dump(name, obj):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
    dump("evaluation-report.json", r)
    dump("confusion-matrix.json", {"raw": r["metrics"]["confusion_matrix"],
                                   "context_aware": r["metrics"]["confusion_matrix_context_aware"]})
    dump("identity-report.json", r["metrics"]["identity"])
    dump("performance-report.json", r["performance"])
    with open(os.path.join(out_dir, "evaluation-summary.md"), "w", encoding="utf-8") as f:
        f.write(summary_md(r))
    write_csvs(r, out_dir)
    return [os.path.join(out_dir, n) for n in OUTPUTS]
