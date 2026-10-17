"""DEV / TEST ONLY: a simulated classroom pipeline with synthetic person tracks and face embeddings.

Drop-in for classroom_ai.ClassroomAI in the browser walkthrough demo server and tests
(SessionManager.classroom_pipeline_factory). It runs the REAL identity layer
(ClassroomIdentity: IdentityManager, teacher tags, enrollment burst, progressive
enrollment, attendance plan) and the REAL SessionRuntime; only the detectors are
replaced by synthetic tracks. Never used by the production app factory; its
results are SYNTHETIC and are never evidence of real-world accuracy.

Scenario controls (``control``): hide / show a face (the track continues), make a
face resemble another person (contradiction), bias a face toward another person's
templates (wrong recognition), remove / add a person, move a person to another's
seat, tracker id switch (``reid``).
"""
import hashlib
import time

import numpy as np

from .behavior import Observation
from .classroom_identity import ClassroomIdentity, FaceObs

DIM = 512
FRAME_W, FRAME_H = 960, 540


def base_vector(key):
    """Deterministic synthetic identity vector (seeded, unit length)."""
    seed = int.from_bytes(hashlib.sha256(f"focusguard-sim:{key}".encode()).digest()[:4], "big")
    rng = np.random.default_rng(seed)
    v = rng.normal(size=DIM)
    return v / np.linalg.norm(v)


class SimPerson:
    def __init__(self, key, box, face=True, tid=None):
        self.key = key
        self.tid = tid
        self.base = base_vector(key)
        self.box = box
        self.face = face
        self.like = None          # (other SimPerson, weight): contradiction / bias


class SimulatedClassroomPipeline:
    mode = "classroom"

    def __init__(self, session_manager, class_id, class_session_id, people, jpeg_encoder=None, fps=8.0,
                 clock=time.time, noise=0.12, seed=7):
        self.session_manager = session_manager
        self.class_id = int(class_id)
        self.class_session_id = class_session_id
        self.people = list(people)
        for i, p in enumerate(self.people, start=1):
            if p.tid is None:
                p.tid = i                   # stable tracker id per person
        self._removed = []
        self.jpeg_encoder = jpeg_encoder
        self.fps = fps
        self.clock = clock
        self.noise = noise
        self.rng = np.random.default_rng(seed)
        self.coord = ClassroomIdentity(session_manager, class_id, class_session_id)
        self.identity = self.coord.identity
        self.device_ok = True
        self.source = None
        self._last = 0.0
        self._t0 = clock()
        self.frames = 0
        self.last_frame_meta = {}

    # ---------------------------------------------------------- controls
    def control(self, action, person=None, other=None, weight=0.6):
        p = next((x for x in self.people if x.key == person), None)
        o = next((x for x in self.people + self._removed if x.key == other), None)
        if action == "hide_face" and p:
            p.face = False
        elif action == "show_face" and p:
            p.face, p.like = True, None
        elif action in ("contradict", "bias") and p and o:
            p.like = (o, float(weight))
        elif action == "reset" and p:
            p.like, p.face = None, True
        elif action == "remove" and p:
            self.people.remove(p)           # leaves the frame: the tracker loses the track
            self._removed.append(p)
        elif action == "move" and p and o:
            p.box = tuple(o.box)              # sits down where ``other`` was
        elif action == "reid" and p:
            # the tracker switches this person's id without the person leaving
            p.tid = max([x.tid for x in self.people] + [x.tid for x in self._removed]) + 1
        elif action == "restore":
            back = next((x for x in self._removed if x.key == person), None)
            if back is None:
                return False
            self._removed.remove(back)
            back.tid = max([x.tid for x in self.people] + [x.tid for x in self._removed] + [back.tid]) + 1
            back.face, back.like = True, None
            self.people.append(back)        # comes back with a NEW tracker id, like a real tracker
        else:
            return False
        return True

    def reload_gallery(self):
        self.coord.reload_gallery()

    # ------------------------------------------------------------- frames
    def embedding_of(self, p, t):
        base = p.base
        if p.like is not None:
            other, w = p.like
            base = (1 - w) * base + w * other.base
        v = base + self.noise * self.rng.normal(size=DIM) / np.sqrt(DIM) * 4
        return v / np.linalg.norm(v)

    def get_frame(self, block=0.0):
        now = self.clock()
        if now - self._last < 1.0 / self.fps:
            if block:
                time.sleep(min(block, 1.0 / self.fps))
            return None, False, "OK"
        self._last = now
        self.frames += 1
        t = now - self._t0
        persons, faces, embs = {}, {}, {}
        for p in self.people:
            i = p.tid
            persons[i] = p.box
            if p.face:
                x1, y1, x2, y2 = p.box
                fw = (x2 - x1) * 0.5
                fx1, fy1 = x1 + (x2 - x1) * 0.25, y1 + 10
                yaw = 22.0 * np.sin(t * 0.9 + i)                  # natural head movement -> pose diversity
                pitch = 6.0 * np.sin(t * 0.5 + i)
                gray = self.rng.integers(40, 220, size=(96, 96)).astype(np.uint8)
                emb = self.embedding_of(p, t)
                embs[i] = emb
                faces[i] = FaceObs(emb, (fx1, fy1, fx1 + fw, fy1 + fw), 0.92, gray, float(yaw), float(pitch))
        decisions = self.coord.identify(now, persons, embs, frame_hw=(FRAME_H, FRAME_W))
        plan = self.coord.after_frame(now, decisions, faces, frame_hw=(FRAME_H, FRAME_W))
        obs = {}
        for tid, sid in plan["confirmed"].items():
            obs[sid] = Observation(timestamp=now, visible=True, face_visible=tid in faces, ear=0.3, yaw=0.0,
                                   pitch=0.0, identity_confidence=decisions[tid].confidence, track_id=tid,
                                   source="classroom_camera", extra=dict(plan["bindings"].get(tid) or {}))
        for sid in plan["unresolved_students"]:
            obs.setdefault(sid, Observation(timestamp=now, visible=False, source="classroom_camera",
                                            extra={"identity_unresolved": True}))
        rt = self.session_manager.classroom_runtime(self.class_id, self.class_session_id)
        if rt is not None and not plan.get("closed"):
            rt.process_frame(now, obs, unidentified=plan["unidentified"])
        self.last_frame_meta = {"t": now, "tracks": {tid: {"status": d.status, "student_id": d.student_id}
                                                     for tid, d in decisions.items()}}
        return self.render(persons, decisions), False, "OK"

    def render(self, persons, decisions):
        img = np.full((FRAME_H, FRAME_W, 3), 70, dtype=np.uint8)
        for tid, (x1, y1, x2, y2) in persons.items():
            d = decisions.get(tid)
            color = (60, 180, 60) if d is not None and d.status == "CONFIRMED" else (160, 160, 160)
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            img[y1:y1 + 4, x1:x2] = color
            img[y2 - 4:y2, x1:x2] = color
            img[y1:y2, x1:x1 + 4] = color
            img[y1:y2, x2 - 4:x2] = color
            img[y1 + 20:y1 + 80, x1 + 40:x2 - 40] = (120, 140, 170)
        return self.jpeg_encoder(img) if self.jpeg_encoder else None

    def camera_health(self):
        return {"camera_name": "Mô phỏng lớp học", "source_type": "SIMULATED", "state": "CONNECTED",
                "resolution": f"{FRAME_W}x{FRAME_H}", "measured_fps": self.fps, "frame_age_ms": 0.0,
                "reconnect_count": 0, "stale_frames": 0, "dropped_frames": 0}

    def release(self, final=False):
        self.coord.close(final=final)


def default_people():
    """Five synthetic people sitting in a row (track ids 1..5)."""
    boxes = [(40 + i * 180, 140, 200 + i * 180, 500) for i in range(5)]
    return [SimPerson(f"P{i + 1}", boxes[i]) for i in range(5)]


def synthetic_templates(person_key, n=5, noise=0.12, seed=1):
    """Template embeddings that look like ``person_key`` (to seed a demo profile)."""
    rng = np.random.default_rng(seed)
    base = base_vector(person_key)
    out = []
    for _ in range(n):
        v = base + noise * rng.normal(size=DIM) / np.sqrt(DIM) * 4
        out.append(v / np.linalg.norm(v))
    return out
