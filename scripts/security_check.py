#!/usr/bin/env python3
"""Static security / public-repo hygiene checks for the CURRENT tree.

Fails (exit 1) when tracked files include secrets, private keys, databases,
face photos, emulator exports, recordings or private evaluation data, or when
known insecure configuration or fake production data re-appears.

History is not rewritten by this project; see docs/PRIVACY_AND_AI_SAFETY.md.
"""
import fnmatch
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORBIDDEN_PATHS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*service-account*.json", "serviceAccount*.json",
    "*-adminsdk-*.json", "*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3",
    "firebase-export-*", "firebase-export-*/*", "data/*", "static/uploads/*", "faces/*",
    "*.mp4", "*.avi", "*.mov", "*.mkv", "recordings/*", "evaluation/private/*", "evaluation/datasets/*",
]
ALLOWED_PATHS = {".env.example"}

# (pattern, label, paths allowed to contain it)
CONTENT_RULES = [
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key material", ()),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key", ()),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "Google API key", ()),
    # security.py keeps the leaked legacy secret on a DENY list.
    (re.compile(r"focusguard_ai_secret_key"), "hard-coded Flask secret", ("focusguard/security.py",)),
    (re.compile(r"allow\s+read\s*,\s*write\s*:\s*if\s+true"), "Firestore allow-all rule", ()),
    (re.compile(r"cors_allowed_origins\s*=\s*[\"']\*[\"']"), "wildcard CORS", ()),
]
TEXT_EXT = {".py", ".js", ".html", ".json", ".rules", ".md", ".txt", ".yml", ".yaml", ".sh", ".bat", ".cfg",
            ".toml", ".ini", ".example", ""}
SKIP_CONTENT = {"scripts/security_check.py", "tests/test_security.py", "tests/test_repo_hygiene.py",
                "package-lock.json"}

# Production runtime modules must not generate random/fake data.
PRODUCTION_MODULES = ["app.py", "session_manager.py", "repository.py", "camera_ai.py", "classroom_ai.py",
                      "focusguard/analytics.py", "focusguard/behavior.py", "focusguard/focus_engine.py",
                      "focusguard/session_runtime.py", "focusguard/identity.py", "focusguard/runtime_state.py",
                      "focusguard/authz.py", "focusguard/realtime.py", "focusguard/camera_state.py",
                      "static/js/core.js", "static/js/teacher.js", "static/js/student.js", "static/js/admin.js",
                      "templates/base.html", "templates/login.html", "templates/error.html",
                      "templates/teacher.html", "templates/student.html", "templates/parent.html",
                      "templates/admin.html"]
FAKE_DATA_RULES = [
    (re.compile(r"^\s*import random\b|^\s*from random import", re.M), "random module in production runtime"),
    (re.compile(r"Math\.random\("), "Math.random in production UI"),
    (re.compile(r"mock_students"), "mock student store"),
    (re.compile(r"emotion_confidence"), "fabricated emotion confidence"),
    (re.compile(r"predicted_(score|drowsy_risk|phone_risk)"), "fabricated prediction numbers"),
]


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    return [p for p in out.stdout.decode("utf-8").split("\0") if p]


def check(files=None):
    files = tracked_files() if files is None else files
    problems = []
    for path in files:
        if path in ALLOWED_PATHS:
            continue
        for pat in FORBIDDEN_PATHS:
            if fnmatch.fnmatch(path, pat):
                problems.append(f"forbidden tracked file: {path} (matches {pat})")
                break
    for path in files:
        if path in SKIP_CONTENT:
            continue
        ext = os.path.splitext(path)[1]
        full = os.path.join(ROOT, path)
        if ext not in TEXT_EXT or not os.path.isfile(full) or os.path.getsize(full) > 2_000_000:
            continue
        try:
            text = open(full, "r", encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for rx, label, allowed in CONTENT_RULES:
            if path not in allowed and rx.search(text):
                problems.append(f"{label}: {path}")
    for path in PRODUCTION_MODULES:
        full = os.path.join(ROOT, path)
        if not os.path.isfile(full):
            continue
        text = open(full, "r", encoding="utf-8").read()
        for rx, label in FAKE_DATA_RULES:
            if rx.search(text):
                problems.append(f"{label}: {path}")
    return problems


def main():
    problems = check()
    if problems:
        print("SECURITY CHECK: FAIL")
        for p in problems:
            print("  -", p)
        return 1
    print("SECURITY CHECK: PASS (current tree)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
