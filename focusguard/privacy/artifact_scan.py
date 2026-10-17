"""Artifact privacy scan (shared by scripts/artifact_privacy_check.py, the
finalize command, the field tool and ci_check).

Flags data:image URIs, base64 image blobs, embedding keys, long float vectors,
image / video / evidence file references, student and parent names/usernames
(demo accounts always; the configured database on request), e-mail addresses,
phone numbers, and camera infrastructure: URLs with credentials, RTSP stream
URLs, private-LAN IP addresses and password / verification-code fields. Non-text files in a report folder are findings too.
"""
import json
import os
import re
import tempfile

TEXT_EXT = (".json", ".jsonl", ".md", ".csv", ".txt", ".html")
CHECKS = {
    "data:image URI": re.compile(r"data:image/", re.I),
    "base64 image blob": re.compile(r"(/9j/|iVBORw0KGgo)[A-Za-z0-9+/]{40,}|[A-Za-z0-9+/]{400,}={0,2}"),
    "embedding key": re.compile(r"[\"']?(face_)?embeddings?[\"']?\s*[:=]\s*[\[\"']", re.I),
    "image/video/evidence file reference": re.compile(r"[\w\-./]+\.(jpe?g|png|mp4|mov|avi|mkv|fgev)\b", re.I),
    "email address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone number": re.compile(r"(?<![\d.])(\+?84|0)(\d[ .-]?){8,10}\d(?![\d.])"),
    # Camera infrastructure (Cài đặt › Camera): never in a shareable report.
    "URL with credentials": re.compile(r"\b[a-z][a-z0-9+.\-]{1,15}://[^/\s:@'\"]+:[^/\s@'\"]*@", re.I),
    "camera stream URL": re.compile(r"\brtsps?://", re.I),
    "private network IP address": re.compile(r"(?<![\d.])(10\.\d{1,3}|192\.168|172\.(1[6-9]|2\d|3[01]))"
                                             r"\.\d{1,3}\.\d{1,3}(?![\d.])"),
    "camera credential field": re.compile(r"[\"']?(password|passwd|verification_code|encrypted_secret|camera_secret)"
                                          r"[\"']?\s*[:=]", re.I),
}
MIN_VECTOR = 64


def known_names(from_db=False, repo=None):
    """Demo account names always; with ``repo`` (or ``from_db``: the configured
    database) every student and parent name and username as well."""
    from repository import DEMO_USERS
    names = set()
    for _id, username, display, role, *_ in DEMO_USERS:
        if role in ("student", "parent"):
            names.update({username, display})
    if repo is None and from_db:
        from repository import get_repository
        repo = get_repository()
        repo.init_db()                # idempotent schema; no demo seeding
    if repo is not None:
        for u in repo.get_users_list():
            if u.get("role") in ("student", "parent"):
                names.update(x for x in (u.get("username"), u.get("display_name")) if x)
    return {n for n in names if len(n) >= 4}


def _vectors(obj, path="$"):
    if isinstance(obj, list):
        if len(obj) >= MIN_VECTOR and all(isinstance(x, float) for x in obj):
            yield path
            return
        for i, x in enumerate(obj):
            yield from _vectors(x, f"{path}[{i}]")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _vectors(v, f"{path}.{k}")


def scan_text(text, names, is_json=False):
    hits = [name for name, rx in CHECKS.items() if rx.search(text)]
    hits += [f"student/parent name or username '{n}'" for n in sorted(names) if n in text]
    if is_json:
        try:
            docs = [json.loads(text)]
        except ValueError:
            docs = []
            for line in text.splitlines():
                try:
                    docs.append(json.loads(line))
                except ValueError:
                    pass
        for d in docs:
            hits += [f"float vector at {p}" for p in _vectors(d)]
    return hits


def iter_files(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in sorted(files):
                    yield os.path.join(root, f)
        else:
            yield p


def scan(paths, names):
    results = []
    for f in iter_files(paths):
        if not f.lower().endswith(TEXT_EXT):
            results.append((f, [f"non-text file in a public report ({os.path.splitext(f)[1] or 'no extension'})"]))
            continue
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        results.append((f, scan_text(text, names, f.lower().endswith((".json", ".jsonl")))))
    return results


def scan_paths(paths, names_from_db=False, quiet=False, repo=None) -> bool:
    """Scan and print; True when every file is clean (used by finalize / ci_check)."""
    results = scan(paths, known_names(names_from_db, repo))
    for f, h in results:
        if h or not quiet:
            print(("  FAIL " if h else "  PASS ") + f + (f"  {h}" if h else ""))
    return bool(results) and not any(h for _, h in results)


PLANTED = {
    "embedding.json": json.dumps({"student_id": 3, "embedding": [0.01] * 512}),
    "vector.json": json.dumps({"x": [0.5] * 128}),
    "image.json": json.dumps({"frame": "data:image/jpeg;base64,/9j/" + "A" * 200}),
    "clip.md": "Evidence saved to instance/evidence/3f2a.fgev and recording.mp4",
    "photo.csv": "student_id,photo\n3,faces/student3.jpg\n",
    "name.md": "Học sinh Nguyễn Văn An bị báo PHONE",
    "username.json": json.dumps({"user": "hocsinh"}),
    "email.md": "Liên hệ phu.huynh@example.com",
    "phone.md": "SĐT phụ huynh 0912 345 678",
    "binary.png": "\x89PNG",
    "camera_url.md": "Camera: rtsp://admin:ABCDEF@cam.local:554/H.264",
    "camera_ip.json": json.dumps({"camera_host": "192.168.1.64"}),
    "camera_secret.json": json.dumps({"camera": "EZVIZ", "verification_code": "x"}),
    "stream.txt": "stream rtsp://cam.local/Streaming/Channels/101",
}
CLEAN = {"clean.json": json.dumps({"student_id": 3, "precision": 0.75, "fps": 7.5, "label": "PHONE_DISTRACTION",
                                   "values": [1.0, 2.0, 3.0], "commit": "db451f0", "p95_ms": 12.3,
                                   "camera_name": "Phòng 8/1", "camera_source_type": "RTSP", "camera_vendor": "EZVIZ",
                                   "version": "10.0.1", "resolution": "1920x1080"})}


def self_test(names=None) -> bool:
    """Plant known-bad samples (and one clean file) and prove each is classified right.
    The demo student names are always part of the name list."""
    names = set(names or ()) | known_names()
    ok = True
    with tempfile.TemporaryDirectory() as d:
        for fname, content in dict(PLANTED, **CLEAN).items():
            with open(os.path.join(d, fname), "w", encoding="utf-8") as fh:
                fh.write(content)
        res = dict((os.path.basename(f), h) for f, h in scan([d], names))
    for fname in PLANTED:
        caught = bool(res.get(fname))
        ok &= caught
        print(f"  self-test {'caught' if caught else 'MISSED'}: {fname}")
    for fname in CLEAN:
        clean = not res.get(fname)
        ok &= clean
        print(f"  self-test {'clean' if clean else 'FALSE POSITIVE'}: {fname} {res.get(fname) or ''}")
    print("ARTIFACT_PRIVACY_SELF_TEST: " + ("PASS" if ok else "FAIL"))
    return ok
