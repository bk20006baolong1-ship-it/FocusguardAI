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

echo "== 1/8 compile =="
"$PY" -m compileall -q app.py repository.py session_manager.py camera_ai.py classroom_ai.py \
    face_recognition.py tracker.py seating_manager.py focusguard scripts tools tests

echo "== 2/8 imports (hardware-free modules) =="
"$PY" - <<'EOF'
import importlib
mods = ["focusguard.config", "focusguard.identity", "focusguard.behavior", "focusguard.focus_engine",
        "focusguard.runtime_state", "focusguard.session_runtime", "focusguard.analytics", "focusguard.authz",
        "focusguard.realtime", "focusguard.security", "focusguard.association", "focusguard.vision_metrics",
        "focusguard.evaluation.metrics", "focusguard.evaluation.synthetic", "focusguard.evaluation.runner",
        "focusguard.policies", "focusguard.policies.interpretation", "focusguard.privacy.consent",
        "focusguard.privacy.audit", "focusguard.privacy.evidence", "focusguard.privacy.retention",
        "focusguard.privacy.export", "focusguard.review", "focusguard.repo_extensions", "focusguard.web_routes",
        "focusguard.evaluation.real_classroom.metrics", "focusguard.evaluation.real_classroom.service",
        "focusguard.evaluation.replay", "focusguard.evaluation.calibration", "focusguard.evaluation.stress",
        "focusguard.evaluation.real_classroom.scenarios", "focusguard.evaluation.real_classroom.simulation",
        "focusguard.evaluation.real_classroom.report", "focusguard.evaluation.real_classroom.field",
        "focusguard.privacy.artifact_scan",
        "focusguard.cameras.health", "focusguard.cameras.redact", "focusguard.cameras.netguard",
        "focusguard.cameras.crypto", "focusguard.cameras.sources", "focusguard.cameras.reader",
        "focusguard.cameras.rtsp", "focusguard.cameras.onvif", "focusguard.cameras.connect",
        "focusguard.cameras.discovery", "focusguard.cameras.service", "focusguard.cameras.check",
        "focusguard.camera_routes", "focusguard.face_profiles", "focusguard.classroom_identity",
        "focusguard.identity_routes",
        "repository", "session_manager", "app"]
for m in mods:
    importlib.import_module(m)
print(f"imported {len(mods)} modules")
EOF

echo "== 3/8 tests (unit, contract, context policy, review, consent, evidence, retention, metrics, multi-person, UI) =="
"$PY" -m pytest -q -rs

echo "== 4/8 JS syntax =="
if command -v node >/dev/null 2>&1; then
    for f in core teacher student admin feedback privacy parent; do
        node --check "static/js/$f.js" && echo "static/js/$f.js OK"
    done
else
    echo "node not found: JS syntax check NOT RUN" && exit 1
fi

echo "== 5/8 security / public repo hygiene / dependency files =="
"$PY" scripts/security_check.py
"$PY" scripts/dependency_check.py

echo "== 6/8 evaluation (SYNTHETIC) =="
"$PY" scripts/run_evaluation.py --output "${TMPDIR:-/tmp}/focusguard_eval_report.json"

echo "== 7/8 cloud evaluation bundle (REPLAY metrics, multi-person stress, benchmark smoke, calibration) =="
EVAL_DIR="${EVAL_OUTPUT_DIR:-${TMPDIR:-/tmp}/focusguard_competition_eval}"
"$PY" scripts/run_competition_evaluation.py --output-dir "$EVAL_DIR"

echo "== 8/8 artifact privacy (scanner self-test + public reports) =="
"$PY" scripts/artifact_privacy_check.py --self-test "$EVAL_DIR" "${TMPDIR:-/tmp}/focusguard_eval_report.json"

echo "CI CHECK: PASS"
