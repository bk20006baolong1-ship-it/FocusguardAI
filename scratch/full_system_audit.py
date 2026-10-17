import os
import sys
import py_compile
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "artifacts"}

print(f"Auditing root: {ROOT}")
errors = []

# 1. Compile all .py files
py_files = []
for root, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP]
    for f in files:
        if f.endswith(".py"):
            py_files.append(os.path.join(root, f))

print(f"Found {len(py_files)} Python files to compile...")
for p in py_files:
    try:
        py_compile.compile(p, doraise=True)
    except Exception as exc:
        errors.append(f"Syntax/compile error in {os.path.relpath(p, ROOT)}: {exc}")

# 2. Check for merge conflict markers in all text files
conflict_markers = ["<<<" + "<<<", "===" + "===", ">>>" + ">>>"]
for root, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP]
    for f in files:
        p = os.path.join(root, f)
        if any(f.endswith(ext) for ext in [".py", ".html", ".js", ".css", ".md", ".json", ".sh", ".bat"]):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fp:
                    for idx, line in enumerate(fp, 1):
                        for cm in conflict_markers:
                            if line.startswith(cm):
                                errors.append(f"Conflict marker {cm} at {os.path.relpath(p, ROOT)}:{idx}")
            except Exception as e:
                pass

print(f"Compilation and conflict check done. Errors found: {len(errors)}")
for err in errors:
    print("  ERROR:", err)

if not errors:
    print("ALL PYTHON FILES COMPILE CLEANLY. NO CONFLICT MARKERS.")
