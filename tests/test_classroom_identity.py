"""Classroom identity: teacher tagging, multi-template profiles, enrollment burst, progressive
enrollment guards, correction, review instead of silent swaps, unidentified-present and the
attendance engine — driven through the real ClassroomIdentity / IdentityManager / SessionRuntime
with the synthetic classroom simulator (no camera, no CV stack)."""
import json
import sqlite3

import numpy as np
import pytest

from conftest import DEV_ENV, FakeClock, add_teacher, grant_consent, login
from focusguard import runtime_state as rs
from focusguard.authz import Principal
from focusguard.classroom_sim import SimulatedClassroomPipeline, default_people, synthetic_templates
from focusguard.config import DEFAULT_CONFIG
from focusguard.face_profiles import (FRONT, LEFT, NEEDS_MORE_SAMPLES, NOT_ENROLLED, PARTIAL, READY, RIGHT, Sample,
                                      evaluate, face_metrics, normalize, profile_status, select_diverse)
from focusguard.identity import IdentityManager, CONFIRMED, SRC_TEACHER_CORRECTED, SRC_TEACHER_TAG
from focusguard.session_runtime import PHASE_INITIALIZING, PHASE_MONITORING

TEACHER = Principal(2, "teacher", frozenset({1}))
ADMIN = Principal(1, "admin")
# Simulated people P1..P5 -> who they really are (class 10A1 students)
P_STUDENT = {"P1": 3, "P2": 4, "P3": 6, "P4": 7, "P5": 8}


def seed_profile(sm, student_id, person_key, n=5, seed=1):
    for i, v in enumerate(synthetic_templates(person_key, n=n, seed=seed)):
        sm.repo.add_face_template({"student_id": student_id, "embedding": v.tolist(), "source": "ADMIN_ENROLLMENT",
                                   "quality_score": 0.8, "pose_bucket": [FRONT, LEFT, RIGHT][i % 3]})
    sm.repo.mark_face_enrolled(student_id)


@pytest.fixture
def world(manager, clock):
    """Consent: 3, 4, 5, 6, 8 face recognition; 7 none. Profiles: 3 (P1), 4 (P2) right; 5 seeded from P3's face
    (a wrong enrollment -> wrong suggestion); 6 and 8 none."""
    for sid in (3, 4, 5, 6, 8):
        grant_consent(manager, sid, face_recognition=True)
    seed_profile(manager, 3, "P1")
    seed_profile(manager, 4, "P2", n=3)
    seed_profile(manager, 5, "P3", n=4, seed=9)
    return manager, clock


def start(manager, clock, initialize=True):
    rt = manager.start_class(1, mode="classroom", started_by=2, initialize=initialize)
    sim = SimulatedClassroomPipeline(manager, 1, rt.class_session_id, default_people(), clock=clock, noise=0.10)
    return rt, sim


def run(sim, clock, seconds, fps=8.0):
    for _ in range(int(seconds * fps)):
        clock.advance(1.0 / fps)
        sim.get_frame()


def track_of(sim, key):
    return [p.key for p in sim.people].index(key) + 1


# ------------------------------------------------------------ profiles
def test_multi_template_matching_and_agreement_rule():
    rng = np.random.default_rng(0)
    a, b = normalize(rng.normal(size=64)), normalize(rng.normal(size=64))
    near = lambda v, s: normalize(v + s * rng.normal(size=64) / 8)            # noqa: E731
    m = IdentityManager({3: [near(a, 0.2) for _ in range(4)], 4: [near(b, 0.2) for _ in range(3)]})
    assert m.gallery_size == 2
    r = m.match(near(a, 0.3))
    assert r.accepted_id == 3 and r.margin > DEFAULT_CONFIG.identity.min_margin
    # one odd template cannot carry a match alone: student 5 has one template = a, one unrelated
    m2 = IdentityManager({5: [a, normalize(rng.normal(size=64))], 6: [b]})
    scores = m2.student_scores(a)
    assert scores[5] < 1.0                                                # averaged with the disagreeing template


def test_quality_filter_rejects_poor_candidates():
    good = face_metrics(np.random.default_rng(1).integers(40, 220, (96, 96)).astype(np.uint8), (0, 0, 96, 96), 0.9, 5, 3)
    ok, q, reasons = evaluate(good)
    assert ok and q >= DEFAULT_CONFIG.enrollment.min_quality and reasons == []
    cases = {
        "TOO_SMALL": face_metrics(None, (0, 0, 30, 30), 0.9, 0, 0),
        "LOW_DETECTOR_SCORE": dict(good, det_score=0.3),
        "BLURRY": face_metrics(np.full((96, 96), 120, np.uint8), (0, 0, 96, 96), 0.9, 0, 0),
        "TOO_DARK": dict(good, brightness=20.0),
        "TOO_BRIGHT": dict(good, brightness=240.0),
        "EXTREME_POSE": dict(good, yaw=60.0),
    }
    for code, metrics in cases.items():
        ok, _q, reasons = evaluate(metrics)
        assert not ok and code in reasons, (code, reasons)
    assert "OCCLUDED" in evaluate(good, occluded=True)[2]
    assert "ASSOCIATION_UNCERTAIN" in evaluate(good, association_ok=False)[2]


def test_diversity_selection_prefers_new_poses_and_skips_duplicates():
    cfg = DEFAULT_CONFIG.enrollment
    rng = np.random.default_rng(3)
    base = normalize(rng.normal(size=64))
    front = [Sample(3, normalize(base + 0.01 * rng.normal(size=64)), 0.95, FRONT, 0) for _ in range(5)]
    left = Sample(3, normalize(base + 0.3 * rng.normal(size=64)), 0.7, LEFT, 0)
    right = Sample(3, normalize(base + 0.3 * rng.normal(size=64)), 0.7, RIGHT, 0)
    chosen = select_diverse(front + [left, right], [], 3, cfg)
    assert {s.pose for s in chosen} == {FRONT, LEFT, RIGHT}               # not three near-identical fronts
    dup = select_diverse([Sample(3, base, 0.9, FRONT, 0)], [{"embedding": base.tolist(), "pose_bucket": FRONT}], 3, cfg)
    assert dup == []


def test_profile_status_levels():
    cfg = DEFAULT_CONFIG.enrollment
    assert profile_status(0, 0, True, cfg) == NOT_ENROLLED
    assert profile_status(2, 1, True, cfg) == NEEDS_MORE_SAMPLES
    assert profile_status(3, 1, True, cfg) == PARTIAL
    assert profile_status(5, 1, True, cfg) == PARTIAL                       # 5 near-identical fronts: not READY
    assert profile_status(5, 2, True, cfg) == READY
    assert profile_status(5, 2, False, cfg) == "NEEDS_REVIEW"


def test_profile_size_is_capped(world):
    sm, clock = world
    rng = np.random.default_rng(5)
    base = normalize(rng.normal(size=64))
    for i in range(4):
        samples = [Sample(8, normalize(base + 0.4 * rng.normal(size=64)), 0.6 + 0.05 * k, [FRONT, LEFT, RIGHT][k % 3], 0)
                   for k in range(6)]
        sm.faces.commit(8, samples, "TEACHER_TAG_BURST")
    assert len(sm.repo.list_face_templates(8)) == DEFAULT_CONFIG.enrollment.max_active_templates
    assert sm.faces.status(8)["status"] == READY


def test_commit_requires_active_consent_and_never_stores_images(world):
    sm, clock = world
    s = [Sample(7, normalize(np.ones(8)), 0.9, FRONT, 0)]
    assert sm.faces.commit(7, s, "TEACHER_TAG_BURST") == 0 and sm.repo.list_face_templates(7) == []
    audit = json.dumps(sm.repo.get_audit(limit=1000))
    assert "IDENTITY_PROFILE_SAMPLE_REJECTED" in audit and "no_face_consent" in audit
    cols = {c for c, _ in __import__("focusguard.repo_extensions", fromlist=["TABLES"]).TABLES["face_profiles"][1]}
    assert not {"image", "photo", "frame", "jpeg", "crop"} & cols


# ------------------------------------------------------- migration / repo
def test_legacy_single_embedding_is_migrated_not_destroyed(tmp_path):
    from repository import SQLiteRepository
    path = str(tmp_path / "legacy.db")
    r = SQLiteRepository(path)
    r.init_db(seed_demo_accounts=True, demo_password="pw")
    con = sqlite3.connect(path)
    con.execute("UPDATE users SET face_embedding = ?, face_enrolled_at = 1000.0 WHERE id = 3",
                (json.dumps([0.25] * 512),))
    con.commit()
    con.close()
    r2 = SQLiteRepository(path)
    r2.init_db()
    tpl = r2.list_face_templates(3)
    assert len(tpl) == 1 and tpl[0]["source"] == "LEGACY" and tpl[0]["embedding"] == [0.25] * 512
    enr = r2.get_face_enrollment(3)
    assert enr == {"has_face": True, "enrolled_at": 1000.0, "template_count": 1}
    assert r2._execute("SELECT face_embedding FROM users WHERE id = 3", fetch="one")["face_embedding"] is None
    r2.init_db()                                                            # idempotent
    assert len(r2.list_face_templates(3)) == 1
    g = r2.get_student_embeddings_by_class(1)
    assert [x["student_id"] for x in g] == [3] and len(g[0]["embeddings"]) == 1
    r2.save_face_embedding(3, None)                                        # deletion covers templates too
    assert r2.list_face_templates(3) == [] and r2.get_face_enrollment(3)["has_face"] is False


# ----------------------------------------------- initialization + tagging
def test_initialization_phase_scores_nothing_and_tags_bind_tracks(world):
    sm, clock = world
    rt, sim = start(sm, clock)
    assert rt.phase == PHASE_INITIALIZING
    run(sim, clock, 2.0)
    snap = sim.coord.snapshot()
    sugg = {v["track_id"]: (v["suggestion"] or {}).get("student_id") for v in snap["tracks"]}
    assert sugg[1] == 3 and sugg[2] == 4 and sugg[3] == 5                   # AI suggestions (P3 is a WRONG one)
    assert sugg[4] is None and sugg[5] is None                              # no profile: no suggestion
    assert all(s["focus_score"] is None for s in rt.class_view(clock())["students"])   # nothing scored
    assert sm.repo.get_focus_events(class_session_id=rt.class_session_id) == []
    # confirm two, correct one, tag two manually
    assert sim.coord.tag(TEACHER, 1, 3, clock())["source"] == SRC_TEACHER_TAG
    assert sim.coord.tag(TEACHER, 2, 4, clock())["source"] == SRC_TEACHER_TAG
    corr = sim.coord.tag(TEACHER, 3, 6, clock(), reason="AI nhận nhầm")
    assert corr["source"] == SRC_TEACHER_CORRECTED and corr["previous_student_id"] == 5
    only = sim.coord.tag(TEACHER, 4, 7, clock())
    assert only["enrollment"] == "SESSION_ONLY"                             # 7 has no face consent
    burst = sim.coord.tag(TEACHER, 5, 8, clock())
    assert burst["enrollment"] == "BURST_STARTED"
    run(sim, clock, DEFAULT_CONFIG.enrollment.burst_seconds + 1.0)
    # enrollment burst -> diverse templates for 8 (and 6 after the correction), nothing for 7
    assert sm.repo.list_face_templates(7) == []
    st8 = sm.faces.status(8)
    assert st8["template_count"] >= 3 and st8["status"] in (PARTIAL, READY)
    assert len(set(st8["pose_buckets"])) >= 2
    assert sm.faces.status(6)["template_count"] >= 3
    view = {s["student_id"]: s for s in rt.class_view(clock())["students"]}
    for sid in (3, 4, 6, 7, 8):
        assert view[sid]["attendance_status"] == rs.ATT_PRESENT
        assert view[sid]["attendance_source"] == rs.ATT_SRC_TEACHER_CONFIRMED
    assert view[6]["identity_source"] == SRC_TEACHER_CORRECTED and view[7]["identity_source"] == SRC_TEACHER_TAG
    assert view[5]["attendance_status"] == rs.ATT_NOT_SEEN                  # the wrong suggestion never counted
    bindings = sm.repo.list_identity_bindings(class_session_id=rt.class_session_id)
    teacher_rows = [b for b in bindings if b["identity_source"] in (SRC_TEACHER_TAG, SRC_TEACHER_CORRECTED)]
    assert len(teacher_rows) == 5 and all(b["confirmed_by"] == 2 and b["confirmed_at"] for b in teacher_rows)
    assert any(b["student_id"] == 5 and b["ended_at"] for b in bindings)  # wrong auto binding ended, kept as history
    rt.begin_monitoring(clock())
    run(sim, clock, 3.0)
    assert rt.phase == PHASE_MONITORING
    assert all(rt.student_view(s)["focus_score"] is not None for s in (3, 4, 6, 7, 8))
    sim.release()
    sm.end_class(1)
    audit = json.dumps(sm.repo.get_audit(limit=2000), ensure_ascii=False)
    for action in ("IDENTITY_TEACHER_TAGGED", "IDENTITY_TEACHER_CORRECTED", "IDENTITY_PROFILE_SAMPLE_ADDED"):
        assert action in audit
    assert "embedding" not in audit and "[0." not in audit


def test_face_hidden_track_keeps_identity_and_no_away(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 1.0)
    sim.coord.tag(TEACHER, 4, 7, clock())                                   # session-only student
    sim.control("hide_face", "P4")
    run(sim, clock, 15.0)                                                   # longer than away_min_seconds
    v = rt.student_view(7)
    assert v["identity_status"] == rs.ID_CONFIRMED and v["visibility_status"] == rs.VIS_VISIBLE
    assert v["event_counts"].get("AWAY", 0) == 0
    sim.release()
    sm.end_class(1)


def test_contradiction_on_teacher_binding_requires_review_never_swaps(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 1.0)
    sim.coord.tag(TEACHER, 1, 3, clock())
    sim.coord.tag(TEACHER, 2, 4, clock())
    sim.control("contradict", "P2", "P1", weight=0.95)                       # P2's face now looks like student 3
    run(sim, clock, 5.0)
    t2 = sim.identity.tracks[2]
    assert t2.student_id == 4 and t2.status == CONFIRMED and t2.review_required   # NOT silently switched
    assert sim.identity.tracks[1].student_id == 3
    v4 = rt.student_view(4)
    assert v4["identity_review"] is True and v4["visibility_status"] != rs.VIS_AWAY
    assert sim.coord.snapshot()["conflicts"] == [2]
    sim.control("reset", "P2")
    res = sim.coord.tag(TEACHER, 2, 4, clock())                             # teacher resolves: still student 4
    assert not sim.identity.tracks[2].review_required and res["source"] in (SRC_TEACHER_TAG, SRC_TEACHER_CORRECTED)
    assert rt.student_view(4)["identity_review"] is False
    sim.release()
    sm.end_class(1)


def test_progressive_samples_only_from_confirmed_and_committed_at_close(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    before = len(sm.repo.list_face_templates(3))
    run(sim, clock, 25.0)                                                   # P1 auto-confirmed (READY profile)
    assert sim.identity.tracks[1].student_id == 3
    pend = sim.coord.pending_samples()
    # P1..P3 are auto-confirmed (P3 as 5 because of 5's wrong profile); P4 / P5 stay unknown
    assert {s.student_id for s in pend} <= {3, 4, 5} and len([s for s in pend if s.student_id == 3]) >= 1
    assert all(s.state == "PENDING" for s in pend)
    assert not any(s.student_id in (6, 7, 8) for s in pend)                 # UNKNOWN tracks never learn
    sim.release(final=True)                                                 # the class ended
    after = sm.repo.list_face_templates(3)
    assert before < len(after) <= before + DEFAULT_CONFIG.enrollment.progressive_max_per_session
    assert any(t["source"] == "PROGRESSIVE" for t in after)
    sm.end_class(1)


def test_teacher_correction_rejects_pending_and_wrong_binding_samples(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 25.0)
    assert sim.identity.tracks[3].student_id == 5                            # wrong recognition (bad profile of 5)
    pend5 = [s for s in sim.coord.pending_samples(5)]
    t5_before = len(sm.repo.list_face_templates(5))
    res = sim.coord.tag(TEACHER, 3, 6, clock(), reason="nhận nhầm")
    assert res["source"] == SRC_TEACHER_CORRECTED and res["previous_student_id"] == 5
    assert sim.coord.pending_samples(5) == [] and all(s.state == "REJECTED" for s in pend5)
    run(sim, clock, 2.0)
    assert sim.identity.tracks[3].student_id == 6                            # correction wins in the session
    assert 5 in sim.identity.tracks[3].rejected_students
    sim.release()
    assert len(sm.repo.list_face_templates(5)) == t5_before                 # nothing learned for the wrong student
    if pend5:
        audit = json.dumps(sm.repo.get_audit(action="IDENTITY_PROFILE_SAMPLE_REJECTED", limit=500))
        assert "teacher_correction" in audit
    sm.end_class(1)


def test_untag_and_never_rebind_same_student_to_that_track(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 3.0)
    assert sim.identity.tracks[1].student_id == 3
    assert sim.coord.untag(TEACHER, 1, clock())["student_id"] == 3
    run(sim, clock, 5.0)
    assert sim.identity.tracks[1].student_id is None                        # not auto-rebound to 3
    sim.release()
    sm.end_class(1)


def test_unknown_person_is_counted_not_assigned_and_blocks_finalization(world):
    sm, clock = world
    from dataclasses import replace
    sm.config = replace(sm.config, attendance=replace(sm.config.attendance, finalize_after_seconds=5.0))
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 8.0)
    stats = rt.class_view(clock())["statistics"]
    assert stats["confirmed"] >= 3 and stats["unidentified_present"] >= 2        # P4, P5 unknown
    assert not rt.attendance_finalized                                        # waits while unknown people are seen
    assert all(rt.student_view(s)["attendance_status"] == rs.ATT_NOT_SEEN for s in (7, 8, 9, 10, 11))
    sim.coord.tag(TEACHER, 4, 7, clock())
    sim.coord.tag(TEACHER, 5, 8, clock())
    run(sim, clock, 1.0)
    assert not rt.attendance_finalized          # one clear second (a detector miss?) is not enough
    run(sim, clock, 5.5)
    assert rt.attendance_finalized
    v = {s["student_id"]: s for s in rt.class_view(clock())["students"]}
    assert v[9]["attendance_status"] == rs.ATT_ABSENT and v[9]["attendance_source"] == rs.ATT_SRC_AUTO_FINALIZED
    assert v[7]["attendance_status"] == rs.ATT_PRESENT
    sim.release()
    sm.end_class(1)


def test_lost_identity_with_unknown_person_visible_is_not_away(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 3.0)
    assert sim.identity.tracks[1].student_id == 3
    sim.coord.untag(TEACHER, 1, clock())                                    # identity of P1 lost, P1 still visible
    run(sim, clock, 90.0)                                                   # no time limit while P1 is unresolved
    v3 = rt.student_view(3)
    assert v3["visibility_status"] == rs.VIS_IDENTITY_UNRESOLVED and v3["event_counts"].get("AWAY", 0) == 0
    sim.release()
    sm.end_class(1)


def test_revoke_consent_cancels_enrollment_and_deletes_templates(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    sm.face_listeners.append(lambda _sid: sim.reload_gallery())
    run(sim, clock, 1.0)
    sim.coord.tag(TEACHER, 5, 8, clock())
    run(sim, clock, 2.0)                                                    # burst running
    sm.consent.revoke(ADMIN, 8, reason="phụ huynh rút lại")
    run(sim, clock, DEFAULT_CONFIG.enrollment.burst_seconds + 1)
    assert sm.repo.list_face_templates(8) == [] and sm.faces.status(8)["status"] == NOT_ENROLLED
    assert sim.identity.tracks[5].student_id == 8                           # the session tag itself still stands
    sm.consent.revoke(ADMIN, 3)
    assert sm.repo.list_face_templates(3) == []
    run(sim, clock, 1.0)
    assert 3 not in set(sim.identity._gallery[0])
    sim.release()
    sm.end_class(1)


def test_pipeline_closed_mid_class_drops_pending_samples_class_end_commits(world):
    """A page reload must not persist samples a later correction could no longer reject."""
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    before = len(sm.repo.list_face_templates(3))
    run(sim, clock, 25.0)
    assert sim.coord.pending_samples(3)
    sim.release()                                                           # viewer left, class still running
    assert len(sm.repo.list_face_templates(3)) == before
    assert "pipeline_closed" in json.dumps(sm.repo.get_audit(action="IDENTITY_PROFILE_SAMPLE_REJECTED", limit=500))
    sm.end_class(1)


def test_teacher_tag_between_identify_and_after_frame_uses_the_new_identity(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 25.0)
    assert sim.identity.tracks[3].student_id == 5 and rt.student_view(5)["attendance_status"] == rs.ATT_PRESENT
    orig, fired = sim.coord.after_frame, []

    def racing_after_frame(now, decisions, *a, **k):
        if not fired:                                                       # the HTTP thread wins the race
            fired.append(sim.coord.tag(TEACHER, 3, 6, now))
        return orig(now, decisions, *a, **k)
    sim.coord.after_frame = racing_after_frame
    run(sim, clock, 0.125)
    sim.coord.after_frame = orig
    assert fired and fired[0]["previous_student_id"] == 5
    assert rt.student_view(5)["attendance_status"] == rs.ATT_NOT_SEEN       # not re-marked by the stale decision
    assert sim.coord.pending_samples(5) == []
    assert 3 in sim.coord._bursts and sim.coord._bursts[3].student_id == 6  # the new burst was not cancelled
    sim.release()
    sm.end_class(1)


def test_correction_removes_templates_of_every_binding_of_the_wrong_student_on_that_track(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 1.0)
    base = len(sm.repo.list_face_templates(4))
    sim.coord.tag(TEACHER, 2, 4, clock())
    run(sim, clock, DEFAULT_CONFIG.enrollment.burst_seconds + 1)
    sim.coord.tag(TEACHER, 2, 4, clock())                                   # saved again on the same track
    run(sim, clock, DEFAULT_CONFIG.enrollment.burst_seconds + 1)
    assert len(sm.repo.list_face_templates(4)) > base
    sim.coord.tag(TEACHER, 2, 6, clock(), reason="nhầm")
    left = sm.repo.list_face_templates(4)                                   # both bursts' templates are gone
    assert left and all(t["source"] == "ADMIN_ENROLLMENT" and not t.get("binding_id") for t in left)
    sim.release()
    sm.end_class(1)


def test_progressive_commit_never_survives_a_concurrent_consent_revoke(world):
    sm, clock = world
    answers = iter([True, False])                                           # active at the check, revoked meanwhile
    sm.faces.consent_active = lambda sid: next(answers, False)
    v = normalize(np.ones(512))
    n = sm.faces.commit(6, [Sample(6, v, 0.9, FRONT, clock())], "PROGRESSIVE")
    assert n == 0 and sm.repo.list_face_templates(6) == []


# ----------------------------------------------------------- attendance
def test_attendance_engine_present_late_absent_override(manager, clock):
    from focusguard.behavior import Observation
    rt = manager.start_class(1, mode="classroom", started_by=2)
    assert all(s["attendance_status"] == rs.ATT_NOT_SEEN for s in rt.class_view(clock())["students"])
    vis = lambda t, sid: {sid: Observation(timestamp=t, visible=True, face_visible=True, ear=0.3, yaw=0, pitch=0)}  # noqa
    rt.process_frame(clock.advance(1), vis(clock(), 3))
    assert rt.student_view(3)["attendance_status"] == rs.ATT_PRESENT
    rt.process_frame(clock.advance(DEFAULT_CONFIG.attendance.late_after_seconds + 5), vis(clock(), 4))
    assert rt.student_view(4)["attendance_status"] == rs.ATT_LATE
    rt.teacher_set_attendance(4, rs.ATT_PRESENT, clock(), by=2)                # e.g. had permission
    rt.teacher_set_attendance(5, rs.ATT_ABSENT, clock(), by=2)
    rt.process_frame(clock.advance(1), vis(clock(), 5))                       # seen later: override stands
    assert rt.student_view(5)["attendance_status"] == rs.ATT_ABSENT
    assert rt.student_view(4)["attendance_source"] == rs.ATT_SRC_TEACHER_CORRECTED
    clock.advance(60)
    rt.heartbeat(clock())                                                     # camera lost: nobody becomes ABSENT
    assert rt.student_view(6)["attendance_status"] == rs.ATT_NOT_SEEN
    manager.end_class(1)
    rows = {r["student_id"]: r for r in manager.repo.get_session_students(class_session_id=rt.class_session_id)}
    assert rows[6]["attendance_status"] == rs.ATT_ABSENT and rows[6]["attendance_source"] == rs.ATT_SRC_AUTO_FINALIZED
    assert rows[4]["attendance_status"] == rs.ATT_PRESENT and rows[4]["attendance_source"] == rs.ATT_SRC_TEACHER_CORRECTED
    with pytest.raises(ValueError):
        rt2 = manager.start_class(1, mode="classroom")
        rt2.teacher_set_attendance(3, "MAYBE", clock())
    manager.end_class(1)


def test_lateness_is_waived_only_for_the_first_initialization(manager, clock):
    from focusguard.behavior import Observation
    late = DEFAULT_CONFIG.attendance.late_after_seconds + 5
    vis = lambda t, sid: {sid: Observation(timestamp=t, visible=True, face_visible=True, track_id=sid)}  # noqa
    rt = manager.start_class(1, mode="classroom", started_by=2, initialize=True)
    rt.process_frame(clock.advance(late), vis(clock(), 3))
    assert rt.student_view(3)["attendance_status"] == rs.ATT_PRESENT        # in the room while being tagged
    rt.begin_monitoring(clock())
    rt.begin_initialization(clock.advance(5))                               # re-initialization mid-class
    rt.process_frame(clock.advance(1), vis(clock(), 4))
    assert rt.student_view(4)["attendance_status"] == rs.ATT_LATE
    manager.end_class(1)


def test_teacher_override_after_the_class_ended_is_stored(manager, clock):
    rt = manager.start_class(1, mode="classroom", started_by=2)
    manager.end_class(1)
    assert rt.teacher_set_attendance(3, rs.ATT_PRESENT, clock(), by=2) is None   # ended: caller corrects the record
    assert rt.student_view(3)["attendance_status"] == rs.ATT_ABSENT


# ------------------------------------------------------------------ HTTP
@pytest.fixture
def http(world):
    sm, clock = world
    from app import create_app
    app, _ = create_app(env=DEV_ENV, async_mode="threading", start_background=False, session_manager=sm)
    app.config["TESTING"] = True
    add_teacher(sm.repo, "teacher2", class_id=2)
    return app, sm, clock


def attach(app, sm, clock, rt):
    sim = SimulatedClassroomPipeline(sm, 1, rt.class_session_id, default_people(), clock=clock, noise=0.10)
    hub = app.extensions["focusguard"]["cameras"]
    hub.acquire(("classroom", 1, rt.class_session_id), lambda: sim)
    return sim


def test_identity_api_flow_and_authorization(http):
    app, sm, clock = http
    t = login(app, "teacher")
    r = t.post("/api/teacher/start_offline_class", json={"class_id": 1, "initialize": True})
    assert r.status_code == 200
    rt = sm.class_runtime(1)
    assert rt.phase == PHASE_INITIALIZING
    sim = attach(app, sm, clock, rt)
    run(sim, clock, 2.0)
    ident = t.get("/api/teacher/classroom/identity?class_id=1").get_json()
    assert ident["camera_open"] and len(ident["tracks"]) == 5 and ident["phase"] == PHASE_INITIALIZING
    body = json.dumps(ident)
    assert "embedding" not in body                                          # no vectors leave the server
    import re
    assert not re.search(r"\[(-?\d+\.\d+, ){8,}", body)
    # forged fields are ignored; outside-class student refused; duplicate student refused
    res = t.post("/api/teacher/classroom/tag", json={"class_id": 1, "track_id": 1, "student_id": 3,
                                                     "confirmed_by": 99, "identity_source": "AUTO_FACE"}).get_json()
    assert res["source"] == SRC_TEACHER_TAG
    b = [x for x in sm.repo.list_identity_bindings(class_session_id=rt.class_session_id) if x["track_id"] == 1][-1]
    assert b["confirmed_by"] == 2 and b["identity_source"] == SRC_TEACHER_TAG
    other_class = sm.repo.get_user_by_username("teacher2")["id"]
    sm.repo.create_user("hs_lop2", "123", "Học sinh lớp 2", "student", 2)
    outside = sm.repo.get_user_by_username("hs_lop2")["id"]
    assert t.post("/api/teacher/classroom/tag", json={"class_id": 1, "track_id": 2, "student_id": outside}).status_code == 400
    dup = t.post("/api/teacher/classroom/tag", json={"class_id": 1, "track_id": 2, "student_id": 3})
    assert dup.status_code == 409 and "Người #1" in dup.get_json()["message"]
    assert t.post("/api/teacher/classroom/tag", json={"class_id": 1, "track_id": 99, "student_id": 4}).status_code == 404
    ok = t.post("/api/teacher/classroom/confirm_suggestions", json={"class_id": 1}).get_json()
    assert ok["confirmed"] >= 1                                              # P2 -> 4 (strict, high confidence)
    # a confident WRONG match (5's profile was enrolled from P3's face) passes the strict filter too:
    # bulk confirmation cannot detect a bad enrollment -> the teacher corrects it below
    assert all(sim.identity.tracks[t].teacher_locked for t in (2, 3))
    assert not sim.identity.tracks[4].teacher_locked and sim.identity.tracks[4].student_id is None
    # other teacher / student / parent / admin cannot tag
    for user, code in (("teacher2", 403), ("hocsinh", 403), ("phuhuynh", 403), ("admin", 403)):
        c = login(app, user)
        for url in ("/api/teacher/classroom/tag", "/api/teacher/classroom/untag", "/api/teacher/classroom/begin",
                    "/api/teacher/attendance"):
            assert c.post(url, json={"class_id": 1, "track_id": 1, "student_id": 4, "status": "PRESENT"}).status_code == code
        assert c.get("/api/teacher/classroom/identity?class_id=1").status_code == code
    assert other_class
    # untag + correction through HTTP
    assert t.post("/api/teacher/classroom/untag", json={"class_id": 1, "track_id": 1}).status_code == 200
    corr = t.post("/api/teacher/classroom/tag", json={"class_id": 1, "track_id": 3, "student_id": 6}).get_json()
    assert corr["source"] == SRC_TEACHER_CORRECTED and corr["previous_student_id"] == 5
    assert corr["enrollment"] == "BURST_STARTED"
    assert rt.student_view(5)["attendance_status"] == rs.ATT_NOT_SEEN       # the wrong sighting is withdrawn
    assert t.post("/api/teacher/classroom/begin", json={"class_id": 1}).get_json()["phase"] == PHASE_MONITORING
    # attendance override: live, then for the ended session
    a = t.post("/api/teacher/attendance", json={"class_id": 1, "student_id": 9, "status": "LATE"}).get_json()
    assert a["attendance_status"] == "LATE" and rt.student_view(9)["attendance_source"] == rs.ATT_SRC_TEACHER_CORRECTED
    assert t.post("/api/teacher/attendance", json={"class_id": 1, "student_id": 9, "status": "MAYBE"}).status_code == 400
    assert t.post("/api/teacher/attendance", json={"class_id": 1, "student_id": outside, "status": "PRESENT"}).status_code == 400
    cs = rt.class_session_id
    assert t.post("/api/teacher/end_offline_class", json={"class_id": 1}).status_code == 200
    rows = {r["student_id"]: r for r in sm.repo.get_session_students(class_session_id=cs)}
    assert rows[9]["attendance_status"] == "LATE"
    t.post("/api/teacher/attendance", json={"class_id": 1, "student_id": 10, "status": "PRESENT", "class_session_id": cs})
    rows = {r["student_id"]: r for r in sm.repo.get_session_students(class_session_id=cs)}
    assert rows[10]["attendance_status"] == "PRESENT" and rows[10]["attendance_source"] == rs.ATT_SRC_TEACHER_CORRECTED
    detail = t.get(f"/api/teacher/class_session/{cs}").get_json()
    assert {s["student_id"]: s["attendance_source"] for s in detail["students"]}[10] == rs.ATT_SRC_TEACHER_CORRECTED
    recs = sm.repo.get_attendance_records(class_session_id=cs)
    assert {r["student_id"] for r in recs} == {9, 10} and all(r["updated_by"] == 2 for r in recs)
    assert "ATTENDANCE_TEACHER_CORRECTED" in json.dumps(sm.repo.get_audit(limit=2000))
    t2 = login(app, "teacher2")
    assert t2.post("/api/teacher/attendance", json={"class_id": 1, "student_id": 10, "status": "ABSENT",
                                                    "class_session_id": cs}).status_code == 403


def test_student_export_and_user_lists_never_contain_embeddings(http):
    app, sm, clock = http
    adm = login(app, "admin")
    users = adm.get("/api/admin/users").get_data(as_text=True)
    export = adm.get("/api/export/student/3?format=json").get_data(as_text=True)
    for text in (users, export):
        assert "embedding\": [" not in text and "[0." not in text
    assert '"has_face": true' in users.replace(" ", "").replace('"has_face":true', '"has_face": true')


# ------------------------------------------------ IdentityManager continuity
def _faces(seed=0, n=3, dim=64):
    rng = np.random.default_rng(seed)
    return [normalize(rng.normal(size=dim)) for _ in range(n)]


def test_teacher_binding_survives_no_face_stranger_and_crossing():
    a, b, stranger = _faces(1)
    m = IdentityManager({3: [a], 4: [b]})
    m.process_frame([(1, None), (2, None)], 0.0)
    m.teacher_bind(1, 3, 0.0, by=2)
    m.teacher_bind(2, 4, 0.0, by=2)
    t = 0.0
    for _ in range(40):                       # 8 s: no face, then a stranger's face on track 1
        t += 0.2
        m.process_frame([(1, None if t < 4 else stranger), (2, b)], t)
    assert m.student_for_track(1) == 3 and not m.tracks[1].review_required     # teacher authority, no release
    for _ in range(3):                        # crossing: faces swap for 0.6 s (below the review threshold)
        t += 0.2
        m.process_frame([(1, b), (2, a)], t)
    assert m.student_for_track(1) == 3 and m.student_for_track(2) == 4
    assert not m.tracks[1].review_required and not m.tracks[2].review_required
    assert m.bindings() == {3: 1, 4: 2}                                        # one student, one track


def test_teacher_bind_moves_a_student_never_two_tracks():
    a, b, _ = _faces(2)
    m = IdentityManager({3: [a], 4: [b]})
    for i in range(6):
        m.process_frame([(1, a), (2, None)], i * 0.3)
    assert m.student_for_track(1) == 3
    res = m.teacher_bind(2, 3, 2.0)
    assert res["displaced_track"] == 1 and m.student_for_track(1) is None and m.student_for_track(2) == 3
    assert 3 in m.tracks[1].rejected_students
    for i in range(10):                                                         # track 1 keeps showing a's face
        m.process_frame([(1, a), (2, None)], 2.2 + i * 0.3)
    assert m.student_for_track(1) is None and m.student_for_track(2) == 3


def test_lost_track_recovers_automatically_with_provenance():
    a, b, _ = _faces(3)
    m = IdentityManager({3: [a], 4: [b]})
    m.process_frame([(1, None)], 0.0)
    m.teacher_bind(1, 3, 0.0)
    m.process_frame([(1, a)], 0.2)
    m.process_frame([], 4.0)                                                    # track lost
    assert m.track_for_student(3) is None
    for i in range(4):
        d = m.process_frame([(7, a)], 4.2 + i * 0.2)[7]
    assert d.student_id == 3 and d.source == "AUTO_RECOVERED" and not d.teacher_locked


def test_unknown_person_never_becomes_an_enrolled_student():
    a, b, stranger = _faces(4)
    m = IdentityManager({3: [a], 4: [b]})
    for i in range(30):
        d = m.process_frame([(9, stranger)], i * 0.2)[9]
    assert d.status in ("UNKNOWN", "UNCERTAIN") and d.student_id is None and d.suggestion_id is None
