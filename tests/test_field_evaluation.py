"""Real-classroom field package (BTC feedback A/B/C): scenario pack, guided field
test (simulated in the cloud), observer imports, finalize command, calibration
coverage, scenario API, artifact privacy scan."""
import csv
import json
import os
import subprocess
import sys

import pytest

from conftest import add_teacher, grant_consent, login
from focusguard.authz import Principal
from focusguard.behavior import Observation
from focusguard.evaluation.real_classroom import field
from focusguard.evaluation.real_classroom.metrics import NOT_ENOUGH_DATA
from focusguard.evaluation.real_classroom.models import GT_LABELS
from focusguard.evaluation.real_classroom.report import OUTPUTS
from focusguard.evaluation.real_classroom.scenarios import SCENARIO_IDS, SCENARIOS, public_pack
from focusguard.policies.contexts import ACTIVITY_CONTEXTS
from focusguard.privacy.artifact_scan import scan_paths, self_test

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEACHER = Principal(2, "teacher", frozenset({1}))
BTC_SCENARIOS = ("01_lecture", "02_writing", "03_looking_at_board", "04_group_discussion", "05_phone", "06_drowsy",
                 "07_away", "08_crossing", "09_stranger", "10_face_occlusion", "11_phone_occlusion",
                 "12_camera_angle_change", "13_track_replacement", "14_leave_and_return", "15_crowded_classroom")


# ------------------------------------------------------------- scenario pack
def test_scenario_pack_covers_every_btc_scenario_with_instructions():
    assert SCENARIO_IDS == BTC_SCENARIOS
    for s in SCENARIOS:
        assert s["steps"] and all(isinstance(x, str) and x for x in s["steps"]), s["scenario_id"]
        assert s["observer"] and s["measures"] and s["btc_group"] in ("A", "B")
        assert s["context"] in ACTIVITY_CONTEXTS                       # suggested; the teacher applies it
        assert set(s["expected_labels"]) <= set(GT_LABELS)
    assert json.loads(json.dumps(public_pack()))["version"]


# ------------------------------------------------------------ field (simulated)
def _sim(tmp_path, answers=None, scenarios=None, revoke=False):
    sm, clock = field.simulation_manager(str(tmp_path / "sim"))
    if revoke:
        for s in sm.evaluation.consented_students(1):
            sm.consent.revoke(Principal(1, "admin"), s["student_id"])
    participants = [s["student_id"] for s in sm.evaluation.consented_students(1)]
    source = field.SimulatedSource(participants or [3], clock)
    io = field.ScriptedIO(answers, echo=False)
    run = field.FieldRun(sm, io, source, class_id=1, teacher="teacher", scenarios=scenarios,
                         report_dir=str(tmp_path / "report"), simulate=True)
    return run, run.run(), io, sm


@pytest.fixture(scope="module")
def full_simulation(tmp_path_factory):
    return _sim(tmp_path_factory.mktemp("field"))


def test_simulated_field_test_runs_every_step(full_simulation):
    run, code, io, sm = full_simulation
    assert code == field.EXIT_OK and run.result["status"] == "FIELD_TEST_COMPLETE"
    for step in range(1, 10):
        assert any(line.startswith(f"== {step}/9") for line in io.lines), step
    for name in OUTPUTS:
        assert os.path.exists(os.path.join(run.result["report_dir"], name)), name
    assert run.result["privacy_clean"] is True


def test_simulation_is_never_real_world_evidence(full_simulation):
    run, *_ = full_simulation
    report = json.load(open(os.path.join(run.result["report_dir"], "evaluation-report.json"), encoding="utf-8"))
    assert report["metadata"]["dataset_kind"] == "SYNTHETIC" and report["real_world_evidence"] is False
    assert report["verdict"] == "NOT_REAL_WORLD_DATA"
    perf = report["performance"]
    assert perf["benchmark_kind"] == "SIMULATION_NO_DETECTOR"
    assert perf.get("latency_p95_ms", NOT_ENOUGH_DATA) == NOT_ENOUGH_DATA      # no fake detector timings


def test_simulation_exercises_every_scenario_and_the_metrics(full_simulation):
    run, *_ = full_simulation
    m = json.load(open(os.path.join(run.result["report_dir"], "evaluation-report.json"), encoding="utf-8"))["metrics"]
    assert m["scenarios"]["run"] == list(BTC_SCENARIOS) and not m["scenarios"]["not_run"]
    raw, ctx = m["behavior"]["per_behavior"], m["behavior"]["per_behavior_context_aware"]
    assert raw["PHONE"]["true_positives"] >= 3 and ctx["PHONE"]["false_positives"] == 0
    assert raw["AWAY"]["true_positives"] >= 3
    # head down while writing / turning in group work: raw signals, not alerts after the context policy
    assert raw["HEAD_AWAY"]["false_positives"] > 0 and ctx["HEAD_AWAY"]["false_positives"] == 0
    ident = m["identity"]
    assert ident["status"] == "OK" and ident["stranger_frames"] > 0 and ident["stranger_false_accept_rate"] == 0
    assert ident["reappearances"] >= 1 and ident["wrong_assignments"] == 0


def test_observer_templates_never_prefill_labels(full_simulation):
    run, *_ = full_simulation
    eid = run.result["evaluation_session_id"]
    ann = os.path.join(run.sm.evaluation.run_dir(eid), "observer-annotations-template.csv")
    rows = list(csv.DictReader(open(ann, encoding="utf-8")))
    assert rows and all(r["label"] == "" and r["expected_label_hint"] for r in rows)
    assert {r["scenario_id"] for r in rows} == set(BTC_SCENARIOS)


def test_session_records_actual_fps_and_scenario_runs(full_simulation):
    run, *_ = full_simulation
    es = run.sm.evaluation.get(run.result["evaluation_session_id"])
    assert es["actual_fps"] == 5.0 and es["scenario_pack_version"] == "btc-2026.1" and es["status"] == "ENDED"
    assert len(run.sm.evaluation.scenario_runs(es["evaluation_session_id"])) == 15


def test_field_test_stops_without_consent(tmp_path):
    run, code, io, _ = _sim(tmp_path, revoke=True, scenarios=["01_lecture"])
    assert code == field.EXIT_CONSENT and run.result["status"] == "CONSENT_REQUIRED"


def test_operator_must_confirm_paper_consent(tmp_path):
    run, code, *_ = _sim(tmp_path, answers={"Phiếu đồng ý": "n"}, scenarios=["01_lecture"])
    assert code == field.EXIT_CONSENT


def test_scenarios_can_be_skipped_and_selected(tmp_path):
    run, code, io, sm = _sim(tmp_path, scenarios=["05_phone", "06_drowsy"], answers={"Enter = bắt đầu": "s"})
    eid = run.result["evaluation_session_id"]
    assert code == 0 and sm.evaluation.scenario_runs(eid) == []


def test_field_test_without_camera_is_hardware_required(manager):
    for strict, expected in ((False, field.EXIT_OK), (True, field.EXIT_HARDWARE)):
        io = field.ScriptedIO(echo=False)
        run = field.FieldRun(manager, io, field.CameraSource(0), class_id=1, teacher="teacher", strict=strict,
                             check_camera=lambda i: (False, "no frame from camera index 0"))
        assert run.run() == expected and run.result["status"] == field.HARDWARE_REQUIRED
        assert "evaluation_session_id" not in run.result                   # nothing recorded, nothing faked


def test_cli_start_simulate_and_hardware_required(tmp_path):
    env = dict(os.environ, SQLITE_PATH=str(tmp_path / "cli.db"), EVALUATION_PRIVATE_DIR=str(tmp_path / "priv"))
    py = sys.executable
    r = subprocess.run([py, "tools/run_classroom_evaluation.py", "start", "--simulate", "--scenarios", "05_phone",
                        "--report-dir", str(tmp_path / "rep")], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "FIELD_TEST_COMPLETE" in r.stdout and "ARTIFACT_PRIVACY_CHECK: PASS" in r.stdout
    r = subprocess.run([py, "tools/field_test.py", "--strict"], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 2 and "STATUS: HARDWARE_REQUIRED" in r.stdout


# ------------------------------------------------------- imports / finalize
def _evaluated_session(manager, clock, notes=None):
    grant_consent(manager, 3, face_recognition=True, evaluation=True)
    rt = manager.start_class(1, mode="classroom", started_by=2)
    es = manager.evaluation.start(TEACHER, 1, {"consent_verified": True, "notes": notes})
    eid = es["evaluation_session_id"]
    manager.evaluation.start_scenario(TEACHER, eid, "05_phone")
    for i in range(60):
        t = clock.advance(0.2)
        rt.process_frame(t, {3: Observation(timestamp=t, visible=True, ear=0.3, yaw=0, pitch=0,
                                            phone_confidence=0.9 if 10 <= i < 40 else None)})
    manager.evaluation.end(TEACHER, eid)
    manager.end_class(1)
    return eid


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def test_observer_csv_import_enforces_consent_and_class(manager, clock, tmp_path):
    eid = _evaluated_session(manager, clock)
    rows = list(csv.DictReader(open(_write_csv(tmp_path / "o.csv", ["student_id", "label", "start_s", "end_s", "note"], [
        [3, "PHONE_DISTRACTION", 2, 8, ""], [3, "", 0, 1, ""],              # empty label: not scored
        [4, "FOCUSED", 0, 5, ""],                                          # no evaluation consent
        [3, "NOT_A_LABEL", 0, 5, ""], [3, "FOCUSED", 5, 2, ""]]), encoding="utf-8")))
    res = manager.evaluation.import_annotations(TEACHER, eid, rows)
    assert res["added"] == 1 and len(res["skipped"]) == 4
    assert any("chưa đồng ý" in s for s in res["skipped"])
    ann = manager.repo.get_annotations(evaluation_session_id=eid)
    assert ann[0]["annotated_by"] == 2 and ann[0]["label"] == "PHONE_DISTRACTION"


def test_identity_sheet_labels_recorded_frames(manager, clock):
    eid = _evaluated_session(manager, clock)
    manager.evaluation.save_field_recording(eid, [
        {"t": clock.t - 5, "track_id": 7, "gt_student_id": "UNLABELLED", "pred_student_id": 3},
        {"t": clock.t - 4, "track_id": 9, "gt_student_id": "UNLABELLED", "pred_student_id": None}], {})
    es = manager.evaluation.get(eid)
    res = manager.evaluation.import_identity(eid, [
        {"track_id": "7", "student_id": "3", "start_s": "0", "end_s": str(clock.t - es["started_at"])},
        {"track_id": "9", "student_id": "STRANGER", "start_s": "0", "end_s": str(clock.t - es["started_at"])},
        {"track_id": "x", "student_id": "3", "start_s": "0", "end_s": "1"}])
    assert res == {"frames": 2, "labelled_frames": 2, "unreadable_rows": 1}
    frames = manager.evaluation.package(eid)["identity_frames"]
    assert {f["track_id"]: f["gt_student_id"] for f in frames} == {7: 3, 9: None}


def test_finalize_writes_all_reports_without_names_or_notes(manager, clock, tmp_path):
    eid = _evaluated_session(manager, clock, notes="Nguyễn Văn An ngồi bàn đầu")
    manager.evaluation.annotate(TEACHER, eid, {"student_id": 3, "label": "PHONE_DISTRACTION",
                                               "started_at": manager.evaluation.get(eid)["started_at"] + 2,
                                               "ended_at": manager.evaluation.get(eid)["started_at"] + 8})
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(manager.evaluation.package(eid)), encoding="utf-8")
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "scripts/finalize_real_classroom_evaluation.py", eid, "--package",
                        str(pkg_path), "--output-dir", str(out), "--calibrate"], cwd=ROOT, capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ARTIFACT_PRIVACY_CHECK: PASS" in r.stdout and "REAL_WORLD_EVIDENCE: NO" in r.stdout
    assert sorted(os.listdir(out)) == sorted(OUTPUTS)
    report = json.loads((out / "evaluation-report.json").read_text(encoding="utf-8"))
    assert report["metadata"]["notes_present"] is True and "notes" not in report["metadata"]
    assert report["metadata"]["scenario_pack_version"] == "btc-2026.1"
    assert report["metrics"]["scenarios"]["run"] == ["05_phone"]
    assert report["calibration"]["applied"] is False and "coverage" in report["calibration"]
    assert "Nguyễn Văn An" not in "".join((out / n).read_text(encoding="utf-8") for n in OUTPUTS)


def test_finalize_refuses_a_package_of_another_session(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"metadata": {"evaluation_session_id": "b" * 32}}), encoding="utf-8")
    r = subprocess.run([sys.executable, "scripts/finalize_real_classroom_evaluation.py", "a" * 32, "--package",
                        str(p)], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "another evaluation session" in (r.stdout + r.stderr)


def test_calibration_reports_coverage_confidence_and_sample_count():
    from focusguard.evaluation.calibration import calibrate
    from focusguard.evaluation.replay import generate_package
    from scripts.calibrate_behavior import QUICK_GRID
    rep = calibrate(generate_package(), grid=QUICK_GRID)
    assert rep["applied"] is False and rep["requires_human_approval"] is True
    assert rep["sample_count"] > 0 and rep["coverage"]["sample_count"] == rep["sample_count"]
    assert set(rep["coverage"]["confidence"]) == {"PHONE", "DROWSY", "HEAD_AWAY", "AWAY"}
    for beh, e in rep["evidence"].items():
        assert e["confidence"] in ("LOW", "MEDIUM", "HIGH", NOT_ENOUGH_DATA)


# ------------------------------------------------------------- scenario API
@pytest.fixture
def eval_app(manager, app):
    grant_consent(manager, 3, evaluation=True)
    t = login(app, "teacher")
    eid = t.post("/api/teacher/evaluations", json={"class_id": 1, "consent_verified": True,
                                                   "dataset_kind": "REAL"}).get_json()["session"]["evaluation_session_id"]
    return manager, app, t, eid


def test_teacher_runs_scenarios_through_the_api(eval_app):
    manager, app, t, eid = eval_app
    r = t.post(f"/api/teacher/evaluations/{eid}/scenario", json={"action": "start", "scenario_id": "02_writing",
                                                                  "apply_context": True})
    body = r.get_json()
    assert r.status_code == 200 and body["run"]["scenario_id"] == "02_writing"
    assert body["session"]["activity_context"] == "WRITING" and len(body["scenario_pack"]["scenarios"]) == 15
    r = t.post(f"/api/teacher/evaluations/{eid}/scenario", json={"action": "start", "scenario_id": "05_phone"})
    runs = r.get_json()["scenario_runs"]
    assert [x["scenario_id"] for x in runs] == ["02_writing", "05_phone"] and runs[0]["ended_at"] is not None
    assert r.get_json()["session"]["activity_context"] == "WRITING"     # context only changes when asked
    assert t.post(f"/api/teacher/evaluations/{eid}/scenario", json={"action": "stop"}).status_code == 200
    assert t.post(f"/api/teacher/evaluations/{eid}/scenario",
                  json={"action": "start", "scenario_id": "99_bogus"}).status_code == 400
    assert t.post(f"/api/teacher/evaluations/{eid}/scenario", json={"action": "jump"}).status_code == 400
    t.post(f"/api/teacher/evaluations/{eid}/end", json={})
    assert t.post(f"/api/teacher/evaluations/{eid}/scenario",
                  json={"action": "start", "scenario_id": "01_lecture"}).status_code == 409


def test_other_roles_cannot_run_scenarios(eval_app):
    manager, app, t, eid = eval_app
    add_teacher(manager.repo, "teacher2", class_id=2)
    for user in ("teacher2", "hocsinh", "phuhuynh", "admin"):
        r = login(app, user).post(f"/api/teacher/evaluations/{eid}/scenario",
                                  json={"action": "start", "scenario_id": "01_lecture"})
        assert r.status_code in (401, 403), user
    assert manager.evaluation.scenario_runs(eid) == []


def test_evaluation_page_has_the_scenario_guide(login_as):
    html = login_as("teacher").get("/teacher/evaluation").get_data(as_text=True)
    for el in ('id="eScenario"', 'id="eScenarioStart"', 'id="eScenarioStop"', 'id="eScenarioContext"'):
        assert el in html
    js = open(os.path.join(ROOT, "static", "js", "feedback.js"), encoding="utf-8").read()
    assert "/scenario" in js and "apply_context" in js


# ------------------------------------------------------------ privacy scan
def test_artifact_privacy_scanner_self_test_and_cli(tmp_path):
    assert self_test() is True
    good = tmp_path / "good"
    good.mkdir()
    (good / "r.json").write_text(json.dumps({"precision": 0.5, "student_id": 3}), encoding="utf-8")
    assert scan_paths([str(good)], quiet=True) is True
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "r.md").write_text("Học sinh Trần Thị Bình, clip 3f.mp4", encoding="utf-8")
    r = subprocess.run([sys.executable, "scripts/artifact_privacy_check.py", "--self-test", str(good)], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "ARTIFACT_PRIVACY_CHECK: PASS" in r.stdout
    r = subprocess.run([sys.executable, "scripts/artifact_privacy_check.py", str(bad)], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "ARTIFACT_PRIVACY_CHECK: FAIL" in r.stdout
