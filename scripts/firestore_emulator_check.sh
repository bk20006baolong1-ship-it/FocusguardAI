#!/usr/bin/env bash
# Verify the Firestore backend against the LOCAL emulator only (never production).
# Needs: Java 11+, Node 20+ (npm ci -> firebase-tools from package-lock.json),
# requirements-firestore-ci.txt installed.
# Usage: bash scripts/firestore_emulator_check.sh
# `firebase emulators:exec` starts the emulator, waits until it is ready, runs
# the tests, stops the emulator and exits with the tests' exit code. The whole
# run is bounded by FIRESTORE_CHECK_TIMEOUT seconds (default 600) where
# coreutils `timeout` exists, so a stuck emulator cannot hang the check.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -n "${PYTHON:-}" ]]; then PY="$PYTHON"
elif [[ -x .venv/bin/python ]]; then PY=.venv/bin/python
else PY=python3; fi
PROJECT=demo-focusguard            # "demo-" projects can only talk to the emulator

command -v java >/dev/null 2>&1 || { echo "java not found: Firestore emulator needs Java 11+" >&2; exit 1; }
# Pinned firebase-tools from package-lock.json; never a global or floating install.
[[ -x node_modules/.bin/firebase ]] || npm ci --no-audit --no-fund

BOUND=()
if command -v timeout >/dev/null 2>&1; then
  BOUND=(timeout --kill-after=30 "${FIRESTORE_CHECK_TIMEOUT:-600}")
fi
# firebase is run directly (not via npx) so the timeout signal reaches the CLI,
# which then shuts the emulator down.
${BOUND[@]+"${BOUND[@]}"} node_modules/.bin/firebase emulators:exec --only firestore --project "$PROJECT" \
  "FOCUSGUARD_FIRESTORE_TESTS=1 FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 GOOGLE_CLOUD_PROJECT=$PROJECT USE_EMULATOR=true $PY -m pytest tests/firestore -rs -v"
