import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import security_check  # noqa: E402


def test_current_tree_passes_security_and_hygiene_scan():
    assert security_check.check() == []


def test_scanner_detects_forbidden_files():
    problems = security_check.check(files=[".env", "data/x.json", "focusguard.db", "static/uploads/avatars/s.jpg",
                                           "firebase-export-1/auth_export/accounts.json", "clip.mp4",
                                           ".env.example"])
    joined = "\n".join(problems)
    for p in (".env ", "data/x.json", "focusguard.db", "static/uploads/avatars/s.jpg", "firebase-export-1", "clip.mp4"):
        assert p.strip() in joined
    assert ".env.example" not in joined


def test_gitignore_covers_sensitive_material():
    text = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
    for pattern in (".env", "*.db", "data/", "firebase-export-*/", "static/uploads/", "*.mp4", "*.pem",
                    "evaluation/private/"):
        assert pattern in text, pattern


def test_env_example_contains_placeholders_only():
    text = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
    for line in text.splitlines():
        if line.startswith("FLASK_SECRET_KEY=") or line.startswith("DEMO_ACCOUNT_PASSWORD="):
            assert line.split("=", 1)[1].startswith("<"), line


def test_no_tracked_env_file():
    out = subprocess.run(["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert out == ""
