#!/usr/bin/env python3
"""Artifact privacy check for reports that may be shared outside the school.

    python scripts/artifact_privacy_check.py PATH [PATH ...]      # files or folders
        [--names-from-db]        # also flag every student/parent name + username in the configured database
        [--self-test]            # prove the scanner catches planted bad samples, then scan

Flags: data:image URIs, base64 image blobs, embedding keys, long float vectors,
references to image / video / evidence files (.jpg .jpeg .png .mp4 .mov .avi
.mkv .fgev), student and parent names and usernames (demo accounts always, the
real database with --names-from-db), e-mail addresses and phone numbers.

Prints ``ARTIFACT_PRIVACY_CHECK: PASS`` and exits 0 only when every file is
clean. A finding is never auto-fixed: the report must be regenerated without it.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from focusguard.privacy.artifact_scan import known_names, scan, scan_paths, self_test  # noqa: E402,F401


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--names-from-db", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    names = known_names(a.names_from_db)
    if a.self_test and not self_test(names):
        print("ARTIFACT_PRIVACY_CHECK: FAIL (self-test)")
        return 1
    if not a.paths:
        if a.self_test:
            return 0
        ap.error("no paths to scan")
    results = scan(a.paths, names)
    bad = [(f, h) for f, h in results if h]
    for f, h in results:
        print(("FAIL " if h else "PASS ") + f + (f"  {h}" if h else ""))
    print(f"scanned {len(results)} file(s), {len(bad)} with findings")
    print("ARTIFACT_PRIVACY_CHECK: " + ("FAIL" if bad or not results else "PASS"))
    return 1 if bad or not results else 0


if __name__ == "__main__":
    sys.exit(main())
