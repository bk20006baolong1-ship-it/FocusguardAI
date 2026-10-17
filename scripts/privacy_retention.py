#!/usr/bin/env python3
"""Run the data-retention sweep once (for cron / scheduled jobs).

    python scripts/privacy_retention.py [--dry-run]

Deletes expired evidence clips, face embeddings past FACE_EMBEDDING_RETENTION_DAYS
or whose consent was revoked/expired, AI event metadata past
EVENT_METADATA_RETENTION_DAYS and evaluation data past
EVALUATION_DATA_RETENTION_DAYS. Every deletion is audited.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the settings only")
    a = ap.parse_args(argv)
    from focusguard.privacy.settings import PrivacySettings
    settings = PrivacySettings.from_env()
    if a.dry_run:
        print(json.dumps(settings.public_dict(), indent=2))
        return 0
    from repository import get_repository
    from session_manager import SessionManager
    sm = SessionManager(repo=get_repository(), privacy_settings=settings)
    print(json.dumps(sm.retention.run(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
