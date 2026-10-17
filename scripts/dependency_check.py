#!/usr/bin/env python3
"""Dependency regression guard: every third-party import is declared in the
right dependency file, and no document tells people to install packages by hand.

    python scripts/dependency_check.py        # prints DEPENDENCY_CHECK: PASS / FAIL

Conventions (README "Quick Start" and the verification sections):

* ``requirements.txt``             runtime: web app, camera pipeline, real field test, AI models
* ``requirements-ci.txt``          hardware-free tests, synthetic/replay evaluation, security checks
* ``requirements-firestore-ci.txt`` the CI set + Firestore client libraries (emulator contract)
* ``requirements-dev.txt``         the CI set + developer tools (browser walkthrough)
* ``package.json`` + ``package-lock.json``  Node tools (Firebase CLI) — installed with ``npm ci``

Checks
1. the dependency files exist; package-lock.json matches package.json;
2. imports of each file group are covered by its dependency file (runtime files:
   every import; hardware-free files: module-level imports only, because camera
   libraries are imported lazily inside functions);
3. no Markdown / shell / batch / Python / YAML text contains an ad-hoc install
   (``pip install <package>``, ``npm install …``) — only ``pip install -r <file>``,
   ``npm ci`` and the browser download ``python -m playwright install chromium``;
4. README contains the canonical commands of every section;
5. requirements.txt pins ``opencv-contrib-python`` (pulled in by mediapipe) to the same
   version as ``opencv-python``: both wheels install the same ``cv2`` package, so an
   unpinned or different contrib version silently replaces the pinned OpenCV.

It is a practical guard, not a resolver: transitive packages (e.g. Werkzeug via
Flask) are mapped explicitly below.
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ_FILES = ("requirements.txt", "requirements-ci.txt", "requirements-firestore-ci.txt", "requirements-dev.txt")
NODE_FILES = ("package.json", "package-lock.json")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "artifacts"}

# import name -> distributions that provide it (any one of them satisfies the import)
PROVIDERS = {
    "cv2": {"opencv-python", "opencv-contrib-python", "opencv-python-headless"},
    "PIL": {"pillow"},
    "flask": {"flask"},
    "flask_socketio": {"flask-socketio"},
    "werkzeug": {"flask", "werkzeug"},                 # installed with Flask
    "google.cloud.firestore": {"google-cloud-firestore"},
    "google.auth": {"google-auth", "google-cloud-firestore"},
    "firebase_admin": {"firebase-admin"},
    "insightface": {"insightface"},
    "mediapipe": {"mediapipe"},
    "numpy": {"numpy"},
    "ultralytics": {"ultralytics"},
    "eventlet": {"eventlet"},
    "onnxruntime": {"onnxruntime"},
    "playwright": {"playwright"},
    "pytest": {"pytest"},
    "cryptography": {"cryptography"},
}
LOCAL = {"app", "repository", "session_manager", "camera_ai", "classroom_ai", "face_recognition", "tracker",
         "seating_manager", "focusguard", "conftest", "repo_contract", "scripts", "tools", "tests",
         "real_camera_check", "security_check", "benchmark_classroom", "calibrate_behavior", "field_test",
         "artifact_privacy_check", "run_classroom_evaluation"}

RUNTIME_FILES = ("app.py", "camera_ai.py", "classroom_ai.py", "face_recognition.py", "tracker.py",
                 "seating_manager.py", "repository.py", "session_manager.py", "focusguard/",
                 "tools/field_test.py", "tools/run_classroom_evaluation.py", "tools/download_models.py",
                 "tools/real_camera_check.py", "tools/camera_diagnostic.py", "scripts/")
CI_FILES = ("app.py", "repository.py", "session_manager.py", "focusguard/", "scripts/", "tests/",
            "tools/field_test.py", "tools/run_classroom_evaluation.py")
CI_EXCLUDE = ("tests/hardware/", "tests/firestore/")
FIRESTORE_FILES = ("tests/firestore/",)
DEV_FILES = ("tools/ui_walkthrough.py", "tools/ui_demo_server.py")

TEXT_EXT = (".md", ".sh", ".bat", ".py", ".yml", ".yaml", ".txt", ".cfg", ".toml")
PIP_RX = re.compile(r"\bpip3?\s+install\b([^\n&;|`]*)")
NPM_RX = re.compile(r"\bnpm\s+(install|i|add)\b([^\n&;|`]*)")
PLAYWRIGHT_RX = re.compile(r"playwright\s+install\b([^\n&;|`]*)")

README_COMMANDS = (
    "pip install -r requirements.txt",
    "cp .env.example .env",
    "python tools/download_models.py",
    "python app.py",
    "pip install -r requirements-dev.txt",
    "python -m playwright install chromium",
    "python tools/ui_walkthrough.py",
    "pip install -r requirements-ci.txt",
    "bash scripts/ci_check.sh",
    "pip install -r requirements-firestore-ci.txt",
    "npm ci",
    "bash scripts/firestore_emulator_check.sh",
    "python tools/field_test.py --simulate",
    "python tools/field_test.py --class-id",
    "python scripts/finalize_real_classroom_evaluation.py",
    "python tools/ip_camera_check.py --camera-name",
    "python tools/field_test.py --camera-name",
)


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_requirements(path, seen=None):
    """Distribution names declared in a requirements file (following -r includes)."""
    seen = seen or set()
    path = os.path.join(ROOT, path)
    if path in seen or not os.path.exists(path):
        return set()
    seen.add(path)
    out = set()
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith(("-r ", "--requirement ")):
            out |= read_requirements(os.path.join(os.path.dirname(path), line.split(None, 1)[1]), seen)
            continue
        m = re.match(r"([A-Za-z0-9_.\-]+)", line)
        if m:
            out.add(norm(m.group(1)))
    return out


def python_files():
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.relpath(os.path.join(root, f), ROOT).replace(os.sep, "/")


def imports(rel, module_level_only=False):
    """Third-party import names of a file: dotted for google.*, top-level otherwise."""
    tree = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())
    nodes = tree.body if module_level_only else list(ast.walk(tree))
    if module_level_only:     # module level incl. module-level try/if blocks, not function bodies
        stack, nodes = list(tree.body), []
        while stack:
            n = stack.pop()
            nodes.append(n)
            if isinstance(n, (ast.Try, ast.If)):
                stack += n.body + n.orelse + getattr(n, "finalbody", []) + \
                    [x for h in getattr(n, "handlers", []) for x in h.body]
    found = set()
    for n in nodes:
        names = []
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            names = [n.module + "." + a.name for a in n.names] if n.module == "google.cloud" else [n.module]
        for name in names:
            top = name.split(".")[0]
            if top in sys.stdlib_module_names or top in LOCAL or top.startswith("test_"):
                continue
            found.add(".".join(name.split(".")[:3]) if top == "google" else top)
    return found


def _in(rel, prefixes):
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in prefixes)


def check_imports(problems):
    declared = {f: read_requirements(f) for f in REQ_FILES}
    groups = (("requirements.txt", RUNTIME_FILES, (), False),
              ("requirements-ci.txt", CI_FILES, CI_EXCLUDE, True),
              ("requirements-firestore-ci.txt", FIRESTORE_FILES, (), False),
              ("requirements-dev.txt", DEV_FILES, (), False))
    for req, include, exclude, module_level in groups:
        for rel in python_files():
            if not _in(rel, include) or _in(rel, exclude):
                continue
            for name in imports(rel, module_level):
                key = next((k for k in PROVIDERS if name == k or name.startswith(k + ".")), name)
                providers = PROVIDERS.get(key)
                if providers is None:
                    problems.append(f"{rel}: import '{name}' has no known distribution (add it to PROVIDERS and "
                                    f"to the right requirements file)")
                elif not providers & declared[req]:
                    problems.append(f"{rel}: import '{name}' not declared in {req} (needs one of {sorted(providers)})")


def check_files(problems):
    for f in REQ_FILES + NODE_FILES:
        if not os.path.exists(os.path.join(ROOT, f)):
            problems.append(f"missing dependency file {f}")
    try:
        pkg = json.load(open(os.path.join(ROOT, "package.json"), encoding="utf-8"))
        lock = json.load(open(os.path.join(ROOT, "package-lock.json"), encoding="utf-8"))
        want = dict(pkg.get("dependencies", {}), **pkg.get("devDependencies", {}))
        root = lock.get("packages", {}).get("", {})
        have = dict(root.get("dependencies", {}), **root.get("devDependencies", {}))
        if want != have:
            problems.append(f"package-lock.json out of sync with package.json ({want} vs {have}): run npm install "
                            f"once to regenerate the lockfile, commit it, then use npm ci")
        for name in want:
            if f"node_modules/{name}" not in lock.get("packages", {}):
                problems.append(f"package-lock.json has no entry for {name}")
    except (OSError, ValueError) as exc:
        problems.append(f"cannot read package.json / package-lock.json: {exc}")


def adhoc_installs(text):
    """Ad-hoc install commands in a text (list of offending snippets)."""
    bad = []
    for m in PIP_RX.finditer(text):
        tokens = m.group(1).split()
        i, ok = 0, True
        while i < len(tokens):
            t = tokens[i]
            if t in ("-r", "--requirement"):
                i += 2
                continue
            if t.startswith("-r") and len(t) > 2:
                i += 1
                continue
            if t in ("--upgrade", "-U") and i + 1 < len(tokens) and tokens[i + 1] == "pip":
                i += 2
                continue
            if t.startswith("-"):
                i += 1
                continue
            if t.startswith(("\"", "'", ")", "<", "(")) or t in ("#",):
                break
            ok = False
            break
        if not ok:
            bad.append(m.group(0).strip())
    for m in NPM_RX.finditer(text):
        bad.append(m.group(0).strip())
    for m in PLAYWRIGHT_RX.finditer(text):
        if m.group(1).split()[:1] != ["chromium"]:
            bad.append(m.group(0).strip())
    return bad


def check_docs(problems):
    me = os.path.relpath(os.path.abspath(__file__), ROOT).replace(os.sep, "/")
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), ROOT).replace(os.sep, "/")
            if not f.endswith(TEXT_EXT) or rel in (me, "tests/test_dependency_check.py"):
                continue
            text = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
            for snippet in adhoc_installs(text):
                problems.append(f"{rel}: ad-hoc install '{snippet}' (use pip install -r <requirements file> / npm ci)")


def pinned_versions(path):
    """{normalized name: version} for ``name==version`` lines of one requirements file."""
    out = {}
    for line in open(os.path.join(ROOT, path), encoding="utf-8"):
        m = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*==\s*([^\s#;]+)", line)
        if m:
            out[norm(m.group(1))] = m.group(2)
    return out


def check_opencv_pins(problems, path="requirements.txt"):
    pins = pinned_versions(path)
    base, contrib = pins.get("opencv-python"), pins.get("opencv-contrib-python")
    if base and contrib != base:
        problems.append(f"{path}: opencv-contrib-python (required by mediapipe) must be pinned to the same version as "
                        f"opencv-python=={base}, found {contrib or 'unpinned'} — otherwise it overwrites cv2")


def check_readme(problems):
    text = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    for cmd in README_COMMANDS:
        if cmd not in text:
            problems.append(f"README.md is missing the command: {cmd}")


def main():
    problems = []
    check_files(problems)
    check_imports(problems)
    check_docs(problems)
    check_readme(problems)
    check_opencv_pins(problems)
    for p in problems:
        print("  -", p)
    print("DEPENDENCY_CHECK: " + ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
