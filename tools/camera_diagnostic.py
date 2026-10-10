#!/usr/bin/env python3
"""Numeric diagnostic for the classroom identity path on the real webcam.

Prints/records numbers only (no images): faces detected, person tracks,
face-to-track association and cosine similarity of live face embeddings to a
reference taken during the first seconds. Output: evaluation/private/camera_diag.json
Usage (Terminal.app, sit in front of the camera for ~15 s):
    .venv/bin/python tools/camera_diagnostic.py --camera 0
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=20.0)
    args = ap.parse_args()
    os.chdir(ROOT)
    import numpy as np
    from face_recognition import FaceRecognizer
    from focusguard.association import associate_faces
    from real_camera_check import Prompter, open_camera
    from tracker import PersonTracker

    cap = open_camera(args.camera)
    rec = FaceRecognizer()
    trk = PersonTracker()
    prompter = Prompter()
    prompter.say("Ngồi trước camera như lúc học, nhìn thẳng, giữ yên.")
    ref = None
    rows = []
    t_start = time.time()
    t_end = t_start + args.seconds
    phone_prompted = False
    while time.time() < t_end:
        if not phone_prompted and time.time() - t_start > args.seconds / 2:
            prompter.say("Bây giờ cầm điện thoại lên trước người và dùng như bình thường.")
            phone_prompted = True
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        faces = rec.app.get(frame)
        tracked = trk.track(frame)
        persons = {p["track_id"]: tuple(p["bbox"]) for p in tracked}
        boxes = [tuple(map(float, f.bbox)) for f in faces]
        assoc = associate_faces(persons, boxes)
        phones = trk.detect_phones(frame)
        embs = [np.asarray(f.embedding, float) for f in faces]
        embs = [e / np.linalg.norm(e) for e in embs]
        if ref is None and len(embs) == 1:
            ref = embs[0]
        rows.append({
            "frame_hw": [h, w],
            "faces": len(faces),
            "face_boxes": [[round(v) for v in b] for b in boxes],
            "person_boxes": {str(k): list(v) for k, v in persons.items()},
            "associated": {str(k): v for k, v in assoc.items()},
            "sim_to_ref": [round(float(e @ ref), 3) for e in embs] if ref is not None else [],
            "emb_dim": [int(e.shape[0]) for e in embs],
            "phase": "phone" if phone_prompted else "sit",
            "phones": [[[round(v) for v in b], round(c, 3)] for b, c in phones],
            # where the face centre sits inside each person box (0 = top, 1 = bottom)
            "face_rel_y": [round(((b[1] + b[3]) / 2 - p[1]) / max(1, p[3] - p[1]), 3)
                           for b in boxes for p in persons.values()],
        })
    cap.release()
    n = len(rows)
    with_face = sum(1 for r in rows if r["faces"])
    with_person = sum(1 for r in rows if r["person_boxes"])
    with_assoc = sum(1 for r in rows if r["associated"])
    sims = [s for r in rows[5:] for s in r["sim_to_ref"]]
    rel = sorted(v for r in rows for v in r["face_rel_y"])
    phone_rows = [r for r in rows if r["phase"] == "phone"]
    summary = {"face_centre_rel_y_min_median_max": ([rel[0], rel[len(rel) // 2], rel[-1]] if rel else None),
               "phone_phase_frames": len(phone_rows),
               "phone_phase_frames_with_phone_detection": sum(1 for r in phone_rows if r["phones"]),
               "max_phone_conf": max((c for r in rows for _, c in r["phones"]), default=None),
               "frames": n, "frames_with_face": with_face, "frames_with_person": with_person,
               "frames_with_association": with_assoc,
               "sim_to_ref_min_median_max": ([round(min(sims), 3), round(sorted(sims)[len(sims) // 2], 3),
                                              round(max(sims), 3)] if sims else None)}
    os.makedirs(os.path.join(ROOT, "evaluation", "private"), exist_ok=True)
    out = os.path.join(ROOT, "evaluation", "private", "camera_diag.json")
    json.dump({"summary": summary, "rows": rows}, open(out, "w"), indent=1)
    print(json.dumps(summary, indent=1))
    print("saved ->", out)


if __name__ == "__main__":
    main()
