#!/usr/bin/env bash
# CI-equivalent local check. Usage: bash scripts/ci_check.sh
# Uses $PYTHON if set, else .venv/bin/python if present, else python3.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -n "${PYTHON:-}" ]]; then PY="$PYTHON"
elif [[ -x .venv/bin/python ]]; then PY=.venv/bin/python
else PY=python3; fi
export DATABASE_TYPE=sqlite
export FOCUSGUARD_ENV=development
export SOCKETIO_ASYNC_MODE=threading

echo "== 1/6 compile =="
"$PY" -m compileall -q app.py repository.py session_manager.py camera_ai.py classroom_ai.py \
    face_recognition.py tracker.py seating_manager.py focusguard scripts tools tests

echo "== 2/6 imports (hardware-free modules) =="
"$PY" - <<'EOF'
import importlib
mods = ["focusguard.config", "focusguard.identity", "focusguard.behavior", "focusguard.focus_engine",
        "focusguard.runtime_state", "focusguard.session_runtime", "focusguard.analytics", "focusguard.authz",
        "focusguard.realtime", "focusguard.security", "focusguard.association", "focusguard.vision_metrics",
        "focusguard.evaluation.metrics", "focusguard.evaluation.synthetic", "focusguard.evaluation.runner",
        "repository", "session_manager", "app"]
for m in mods:
    importlib.import_module(m)
print(f"imported {len(mods)} modules")
EOF

echo "== 3/6 tests =="
"$PY" -m pytest -q -rs

echo "== 4/6 JS syntax =="
if command -v node >/dev/null 2>&1; then
    for f in core teacher student admin; do
        node --check "static/js/$f.js" && echo "static/js/$f.js OK"
    done
else
    echo "node not found: JS syntax check NOT RUN" && exit 1
fi

echo "== 5/6 security / public repo hygiene =="
"$PY" scripts/security_check.py

echo "== 6/6 evaluation (SYNTHETIC) =="
"$PY" scripts/run_evaluation.py --output "${TMPDIR:-/tmp}/focusguard_eval_report.json"

echo "CI CHECK: PASS"
