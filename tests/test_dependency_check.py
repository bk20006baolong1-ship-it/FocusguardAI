"""scripts/dependency_check.py: dependency files, import coverage, no ad-hoc installs in docs."""
import os

import pytest

from scripts import dependency_check as dc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_repository_passes_the_dependency_check(capsys):
    assert dc.main() == 0
    assert "DEPENDENCY_CHECK: PASS" in capsys.readouterr().out


@pytest.mark.parametrize("text", [
    "pip install playwright", "pip install pillow", "python -m pip install -q playwright==1.56.0",
    "pip3 install opencv-python", "pip install -r requirements.txt pillow", "npm install firebase-tools",
    "npm install -g firebase-tools", "npm install", "npx playwright install", "python -m playwright install",
])
def test_ad_hoc_install_commands_are_flagged(text):
    assert dc.adhoc_installs(text), text


@pytest.mark.parametrize("text", [
    "pip install -r requirements.txt", "python -m pip install -r requirements-ci.txt",
    "pip install -r requirements.txt -r requirements-ci.txt", "python -m pip install --upgrade pip",
    "npm ci", "python -m playwright install chromium", "missing: cv2 (pip install -r requirements.txt)",
])
def test_dependency_file_workflow_is_allowed(text):
    assert dc.adhoc_installs(text) == []


def test_every_runtime_import_is_declared():
    runtime = dc.read_requirements("requirements.txt")
    for dist in ("flask", "flask-socketio", "opencv-python", "mediapipe", "ultralytics", "insightface",
                 "onnxruntime", "numpy", "google-cloud-firestore"):
        assert dist in runtime, dist
    dev = dc.read_requirements("requirements-dev.txt")
    assert {"playwright", "pillow", "pytest", "flask"} <= dev            # includes the CI set
    assert {"google-cloud-firestore", "pytest"} <= dc.read_requirements("requirements-firestore-ci.txt")
    assert "playwright" not in runtime and "pillow" not in runtime        # developer tools stay out of runtime


def test_an_undeclared_import_is_reported(tmp_path, monkeypatch):
    planted = tmp_path / "planted.py"
    planted.write_text("import requests\nimport cv2\n", encoding="utf-8")
    assert dc.imports(str(planted)) == {"requests", "cv2"}
    monkeypatch.setattr(dc, "python_files", lambda: [str(planted)])
    monkeypatch.setattr(dc, "RUNTIME_FILES", (str(planted),))
    problems = []
    dc.check_imports(problems)
    assert any("'requests' has no known distribution" in p for p in problems)


def test_lazy_camera_imports_do_not_count_for_the_ci_set(tmp_path):
    f = tmp_path / "lazy.py"
    f.write_text("import numpy\n\ndef cam():\n    import cv2\n", encoding="utf-8")
    assert dc.imports(str(f), module_level_only=True) == {"numpy"}
    assert dc.imports(str(f)) == {"numpy", "cv2"}


@pytest.mark.parametrize("contrib,ok", [("opencv-contrib-python==4.13.0.92\n", True),
                                        ("opencv-contrib-python==5.0.0.93\n", False),
                                        ("", False)])
def test_opencv_contrib_must_match_the_opencv_pin(tmp_path, contrib, ok):
    req = tmp_path / "req.txt"
    req.write_text("opencv-python==4.13.0.92\nmediapipe==0.10.35\n" + contrib, encoding="utf-8")
    problems = []
    dc.check_opencv_pins(problems, str(req))
    assert (problems == []) is ok
    pins = dc.pinned_versions("requirements.txt")
    assert pins["opencv-contrib-python"] == pins["opencv-python"]
