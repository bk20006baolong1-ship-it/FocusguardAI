# Competition hardening: identity, runtime integrity, security and real analytics

Base `main` ← head `fix/competition-core-hardening`. **Do not merge until real-camera verification is done**
(see `docs/REAL_CAMERA_VERIFICATION.md`).

## Why
The previous build keyed runtime state by display name and tracker id, scored focus inside the camera loop
(frame-based), trusted browser-pushed names/scores, broadcast to global rooms, only checked "logged in", stored
plaintext passwords, and filled dashboards with random/mock numbers (drift, emotions, predictions, fallback
root causes). This PR replaces that with a verifiable pipeline:

```
Camera → YOLOv8+ByteTrack (temporary track_id) → InsightFace → IdentityManager (canonical student_id)
→ StudentRuntimeState → BehaviorAnalyzer (temporal episodes) → ONE time-based FocusEngine
→ SessionRuntime → focus_events / focus_snapshots / session_students
→ server-authoritative, class-isolated Socket.IO → analytics from persisted data only
```

## What changed
- **Canonical `student_id`**: display names and tracker ids are never runtime keys.
- **IdentityManager** (`focusguard/identity.py`): similarity threshold + margin to second best; consecutive
  temporal confirmation; identity lock with time-based unlock; cooldown; one student ↔ one live track
  (`CONFLICT` otherwise); track recovery after tracker re-assignment; crossing protection; uncertain stays
  UNKNOWN/UNCERTAIN.
- **StudentRuntimeState**: identity / connection / attendance / visibility / focus kept as separate fields;
  score is `None` until measured.
- **One time-based FocusEngine**: per-second penalty/recovery, FPS independent (5/15/30 FPS within 1.5 pts;
  0.8 on synthetic), freezes when nothing is measured, bounded step on stalls. Camera pipelines no longer
  touch scores (AST test).
- **Temporal behaviour episodes**: PHONE ≥1 s, DROWSY ≥1.5 s, HEAD_AWAY ≥2 s, TEMPORARILY_NOT_VISIBLE,
  AWAY ≥10 s; blinks, glances and phone flashes ignored; episodes carry student_id, type, start/end, duration,
  confidence, source, metadata.
- **SessionRuntime / RuntimeRegistry**: scoped by class_session_id (or personal session) × student_id; clean
  start with enrolled roster; end closes episodes and persists; camera disconnect → `NO_CAMERA_SIGNAL`, runtime
  survives.
- **Server-authoritative Socket.IO**: unauthenticated sockets refused; identity from the Flask session;
  `student_data_push` / `student_frame_push` rejected; rooms `class:{id}:teachers`, `class:{id}:students`,
  `student:{id}`.
- **Ownership authorization** (`focusguard/authz.py`): student → own data; teacher → assigned classes;
  parent → linked student; admin → explicit scope. `/video_feed`, session breakdown/latest/list, analytics,
  attendance and reports are protected. The role stored in the cookie is re-checked against the DB.
- **Security**: `FLASK_SECRET_KEY` from env (production refuses missing/known keys), secure cookies,
  `ALLOWED_ORIGINS` (no wildcard in production), Werkzeug password hashes with plaintext migration, Firestore
  rules deny all direct client access, embedded emulator key and plaintext Firebase Auth sync removed.
- **Fake data removed**: no mock students, random drift, fabricated emotions/confidence, predictions,
  learning-risk scores, fallback 5/3/12 root causes, seeded synthetic sessions, or hard-coded dashboard numbers.
- **Real analytics**: computed from `focus_events`, `focus_snapshots`, `class_sessions`, `session_students`;
  missing data → `insufficient_data` / N/A; zero stays zero; missed class sessions appear in history.
- **Normalized data**: `class_members`, `class_sessions(mode)`, `session_students`, enriched `focus_events`,
  `focus_snapshots`.
- **Evaluation framework** (`focusguard/evaluation`, `scripts/run_evaluation.py`): behaviour P/R/F1, false
  alerts/hour, latency, identity accuracy/switches/duplicates/recovery/time-to-confirmation, FPS consistency.
  Results labelled **SYNTHETIC**; real-world **NOT_EVALUATED**.
- **Public repo hygiene**: `.env`, `data/`, Firebase emulator export and a face photo are no longer tracked;
  `scripts/security_check.py` guards the current tree in CI.
- **Verification tooling**: `tools/real_camera_check.py` (guided webcam scenarios; no images stored,
  embeddings erased afterwards), `scripts/firestore_emulator_check.sh`.
- **Docs**: README, SUMMARY, `docs/COMPETITION_ARCHITECTURE.md`, `docs/DEMO_SCRIPT.md`,
  `docs/PRIVACY_AND_AI_SAFETY.md`, `docs/REAL_CAMERA_VERIFICATION.md`.

## Fixed during final review (each with a regression test in `tests/test_review_regressions.py`)
- **Stored XSS**: a teacher-controlled display name was injected into the admin accounts table via `innerHTML`
  (also present on `main`). All user-controlled values in the admin tables are now escaped.
- **Phone detection corrupted the person tracker**: phones were detected through the same YOLO object that
  ByteTrack's `persist=True` callbacks are attached to (also present on `main`). Phones now use a separate
  detector instance.
- **Classroom camera could feed the wrong session**: the stream and `ClassroomAI` are now bound to the exact
  classroom `class_session_id` and never score an online class.
- **CameraHub**: viewers are reference-counted per pipeline; a generation token stops generators holding a
  replaced pipeline; frame reads are serialised.
- **Identity**: an unenrolled face (clear non-match) on a locked track now releases the identity after the
  unlock window, instead of inheriting the student after a tracker id switch.
- **Body visible without a face** is now "unmeasured" (score frozen), not FOCUSED.
- **Online class**: students already in self-study are linked when the class starts; camera staleness is
  tracked per student.
- **Class analytics** exclude self-study data.
- **Teacher dashboard**: snapshots are requested for and filtered to the selected class; the remaining
  hard-coded placeholders and the static reports table were removed.
- `reset_face` validates `user_id` before touching the database.

## Verification
- `bash scripts/ci_check.sh`: compile, imports, pytest, JS syntax, security scan, synthetic evaluation.
- **142 passed / 0 failed / 9 skipped** (3 HARDWARE_REQUIRED in CI, 6 EMULATOR_REQUIRED).
- **GitHub Actions: PASS** on the PR head.
- **Firestore on the emulator**: `bash scripts/firestore_emulator_check.sh`, 6/6 passed; production Firestore has not been tested.
- **Hardware (MacBook Air M3, FaceTime HD)**: hardware tests **3/3 PASS**. The real web app runs with the webcam
  (1920x1080 `/video_feed`, runtime ACTIVE, authenticated websocket, no console errors).
- **Real-camera scenarios** (`tools/real_camera_check.py`, one consenting volunteer, about 7.5 FPS): identity, blink, quick glance,
  phone, leave/return, camera interrupt and end-class **PASS**; crossing and unknown person are **USER_ACTION_REQUIRED**
  (they need more volunteers). This is an integration/behaviour check, **not** an accuracy measurement. See
  `docs/REAL_CAMERA_RESULTS.md`.
- Real camera runs found 5 real bugs (close-up face association, identity loss under phone occlusion, fixed-EAR
  false drowsiness, duplicate-tracker-id flicker, fragmented phone signal). All are fixed, each with a regression
  test built from the logged values.

## Known limitations (not blocking, documented)
- During the leave/return scenario one extra low-confidence (0.12) 3.5 s PHONE episode came from sparse 0.25-0.46
  detections; ground truth is unknown.
- EAR cannot distinguish closed eyes from looking far down (except while a phone is in use).
- A personal session linked to an online class stores its events under the class session, so
  `/api/session/<id>/breakdown` for that personal session shows no events.
- One of several open student tabs disconnecting marks the student OFFLINE until the next reconnect.
- With `FOCUSGUARD_ENV` unset the app runs in development mode and seeds demo accounts (password from
  `DEMO_ACCOUNT_PASSWORD`, default `123`). Set `FOCUSGUARD_ENV=production` for deployments.

## Remaining blockers (not solved by this PR)
1. **Real webcam**: verified with one volunteer. Crossing and unknown-person scenarios need additional consenting volunteers.
2. **Labelled real-world dataset**: accuracy is `NOT_EVALUATED`.
3. **Firestore**: verified on the emulator only; production Firestore has not been tested.
4. **Old git history** still contains sensitive artifacts (face photo, emulator export with demo accounts,
   `.env`, session files, a dummy emulator private key). History was intentionally not rewritten here.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
