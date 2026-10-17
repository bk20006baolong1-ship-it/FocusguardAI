"""Cloud-runnable tools: benchmark, calibration CLI, competition bundle, real-camera tool without hardware."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
PY = sys.executable


def run(*args, cwd=ROOT):
    return subprocess.run([PY] + list(args), cwd=cwd, capture_output=True, text=True, timeout=600)


def test_benchmark_script_smoke(tmp_path):
    out = tmp_path / "benchmark.json"
    r = run("scripts/benchmark_classroom.py", "--students", "5,10", "--seconds", "4", "--output", str(out))
    assert r.returncode == 0, r.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["dataset_kind"] == "SYNTHETIC" and data["hardware"]["status"] == "HARDWARE_REQUIRED"
    for row in data["results"]:
        for key in ("student_count", "input_resolution", "processing_fps", "frame_latency_p50", "frame_latency_p95",
                    "identity_latency", "cpu_usage_if_available", "memory_usage_if_available"):
            assert key in row
        assert row["identity_contract"]["wrong_assignments"] == 0
    assert set(data["event_latency"]["by_type"]) >= {"PHONE", "DROWSY", "HEAD_AWAY", "AWAY"}


def test_calibration_markdown_summary(tmp_path):
    import calibrate_behavior
    from focusguard.evaluation.calibration import calibrate
    from focusguard.evaluation.replay import generate_package
    rep = calibrate(generate_package(), grid={"PHONE": {"phone_min_seconds": [1.0]}})
    md = calibrate_behavior.markdown(rep, "replay-fixture")
    assert "Applied: **False**" in md and "Generated data" in md and "| PHONE |" in md


def test_calibrate_cli_writes_json_and_markdown(tmp_path):
    pkg = tmp_path / "pkg.json"
    from focusguard.evaluation.replay import generate_package
    p = generate_package(n_students=2, duration=120)
    pkg.write_text(json.dumps(p), encoding="utf-8")
    out = tmp_path / "calibration.json"
    r = run("scripts/calibrate_behavior.py", "--package", str(pkg), "--grid", "quick", "--output", str(out))
    assert r.returncode == 0, r.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["applied"] is False and {"current_config", "recommended_config", "delta", "evidence"} <= set(data)
    assert (tmp_path / "calibration.md").exists()


def test_real_camera_tool_without_hardware_exits_cleanly(tmp_path):
    r = run("tools/run_classroom_evaluation.py", "check")
    if "HARDWARE_OK" in r.stdout:            # a developer machine with a webcam
        return
    assert r.returncode == 0 and "STATUS: HARDWARE_REQUIRED" in r.stdout
    strict = run("tools/run_classroom_evaluation.py", "record", "--class-id", "1", "--teacher", "teacher", "--strict")
    assert strict.returncode == 2 and "HARDWARE_REQUIRED" in strict.stdout


def test_real_camera_tool_file_commands(tmp_path):
    from focusguard.evaluation.replay import generate_package
    pkg = generate_package(n_students=2, duration=120)
    pkg["annotations"] = []
    path = tmp_path / "package.json"
    path.write_text(json.dumps(pkg), encoding="utf-8")
    csv = tmp_path / "observer.csv"
    csv.write_text("student_id,label,start_s,end_s,note\n1,PHONE_DISTRACTION,30,42,\n1,FOCUSED,0,30,\n"
                   "9,FOCUSED,0,10,\n1,BORED,0,5,\n", encoding="utf-8")
    r = run("tools/run_classroom_evaluation.py", "import-annotations", "--package", str(path), "--csv", str(csv))
    assert r.returncode == 0 and '"added": 2' in r.stdout and '"skipped": 2' in r.stdout
    ident = tmp_path / "identity.csv"
    ident.write_text("track_id,student_id,start_s,end_s\n1,1,0,600\n99,STRANGER,0,600\n", encoding="utf-8")
    assert run("tools/run_classroom_evaluation.py", "import-identity", "--package", str(path), "--csv",
               str(ident)).returncode == 0
    out = tmp_path / "report.json"
    r = run("tools/run_classroom_evaluation.py", "metrics", "--package", str(path), "--output", str(out))
    assert r.returncode == 0 and "REPLAY" in r.stdout
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["is_real_world_evidence"] is False


def test_retention_cli_dry_run():
    r = run("scripts/privacy_retention.py", "--dry-run")
    assert r.returncode == 0 and '"evidence_enabled": false' in r.stdout
