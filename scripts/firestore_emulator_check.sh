#!/usr/bin/env bash
# Verify the Firestore backend against the LOCAL emulator only (never production).
# Needs: Java 11+, Node (npm install -> firebase-tools), requirements.txt installed.
# Usage: bash scripts/firestore_emulator_check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
PROJECT=demo-focusguard            # "demo-" projects can only talk to the emulator
[[ -d node_modules/firebase-tools ]] || npm install --no-audit --no-fund
npx firebase emulators:exec --only firestore --project "$PROJECT" \
  "FOCUSGUARD_FIRESTORE_TESTS=1 FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 GOOGLE_CLOUD_PROJECT=$PROJECT USE_EMULATOR=true $PY -m pytest tests/firestore -rs -v"
