"""Session-owned identity bindings across camera restarts, and the provenance-safe attendance
evidence ledger. Driven through the real ClassroomIdentity / IdentityManager / SessionRuntime with
the synthetic classroom simulator (no camera, no CV stack; SYNTHETIC, not accuracy evidence).

People of the simulated class (see tests/test_classroom_identity.py::world): P1 = student 3 and
P2 = student 4 have correct profiles; P3 = student 6 (consent, no profile; student 5's profile was
wrongly seeded from P3's face); P4 = student 7 (NO face consent); P5 = student 8 (consent, no profile).
"""
import pytest

from conftest import add_teacher
from focusguard import runtime_state as rs
from focusguard.behavior import Observation
from focusguard.classroom_sim import SimPerson, SimulatedClassroomPipeline, default_people
from focusguard.config import DEFAULT_CONFIG
from focusguard.identity import SRC_AUTO_RECOVERED
from focusguard.session_identity import METHOD_SEAT_CONTINUITY, METHOD_TRACK_CONTINUITY
from test_classroom_identity import TEACHER, run, start, world  # noqa: F401  (fixture)

IC = DEFAULT_CONFIG.identity
LATE = DEFAULT_CONFIG.attendance.late_after_seconds


def pipeline(sm, clock, rt, people=None, tid_base=100):
    """A rebuilt camera pipeline (page reload / reconnect): a NEW tracker with NEW ids."""
    people = people if people is not None else default_people()
    for i, p in enumerate(people, start=1):
        p.tid = tid_base + i
    return SimulatedClassroomPipeline(sm, 1, rt.class_session_id, people, clock=clock, noise=0.10)


def bindings(sm, rt, sid=None):
    return sm.repo.list_identity_bindings(class_session_id=rt.class_session_id, student_id=sid)


def view(rt, sid):
    return rt.student_view(sid)


# ================================================================ fix 2: session-owned teacher tags
def test_teacher_tag_survives_a_rebuilt_pipeline_and_is_restored_by_seat_continuity(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 2.0)
    sim.coord.tag(TEACHER, 4, 7, clock())                                  # P4 = student 7, NO face consent
    run(sim, clock, 2.0)
    assert view(rt, 7)["attendance_status"] == rs.ATT_PRESENT
    checkin = view(rt, 7)["checkin_at"]
    sim.release()                                                           # page reload: last viewer left
    (b1,) = bindings(sm, rt, 7)
    assert b1["ended_at"] is None and b1["detached_at"] is not None         # persisted, open, detached
    assert b1["teacher_anchored"] is True
    clock.advance(4.0)
    sim2 = pipeline(sm, clock, rt)                                          # tracker ids 101..105
    run(sim2, clock, 0.5)
    assert sim2.identity.track_for_student(7) is None                       # not restored on sight: needs stability
    run(sim2, clock, 2.0)
    assert sim2.identity.track_for_student(7) == 104                        # P4's NEW track, by seat continuity
    t = sim2.identity.tracks[104]
    assert t.restored and t.source == SRC_AUTO_RECOVERED and t.teacher_locked
    rows = {r["binding_id"]: r for r in bindings(sm, rt, 7)}
    new = next(r for r in rows.values() if r["binding_id"] != b1["binding_id"])
    assert new["derived_from_binding_id"] == b1["binding_id"] and new["recovery_method"] == METHOD_SEAT_CONTINUITY
    assert rows[b1["binding_id"]]["end_reason"] == "restored" and new["ended_at"] is None
    assert view(rt, 7)["attendance_status"] == rs.ATT_PRESENT and view(rt, 7)["checkin_at"] == checkin
    assert sm.repo.list_face_templates(7) == []                             # no consent: no biometric data, ever
    assert sm.repo.get_audit(action="IDENTITY_SESSION_RESTORED", limit=5)
    roster = {r["student_id"]: r for r in sim2.coord.snapshot()["roster"]}
    assert roster[7]["restored"] and roster[7]["track_id"] == 104
    sim2.release(final=True)
    sm.end_class(1)
    assert all(r["ended_at"] is not None for r in bindings(sm, rt))         # ending the class closes them all


def test_tracker_id_switch_inside_the_same_pipeline_keeps_the_teacher_tag(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 1.0)
    sim.coord.tag(TEACHER, 4, 7, clock())
    run(sim, clock, 1.0)
    sim.control("reid", "P4")                                               # ByteTrack hands out a new id
    new_tid = next(p.tid for p in sim.people if p.key == "P4")
    run(sim, clock, IC.track_lost_seconds + IC.restore_min_seconds + 2.0)
    assert sim.identity.track_for_student(7) == new_tid
    assert view(rt, 7)["visibility_status"] != rs.VIS_AWAY and view(rt, 7)["event_counts"].get("AWAY", 0) == 0
    sim.release(final=True)
    sm.end_class(1)


def test_an_empty_seat_or_someone_else_there_is_never_restored(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 2.0)
    sim.coord.tag(TEACHER, 4, 7, clock())
    run(sim, clock, 1.0)
    sim.release()
    clock.advance(3.0)
    # 1) the student left: P4's seat is empty, a stranger sits elsewhere
    people = [p for p in default_people() if p.key != "P4"] + [SimPerson("STRANGER", (40, 20, 180, 120))]
    sim2 = pipeline(sm, clock, rt, people)
    run(sim2, clock, 6.0)
    assert sim2.identity.track_for_student(7) is None
    assert view(rt, 7)["attendance_status"] == rs.ATT_PRESENT               # the earlier valid check-in stands
    sim2.release()
    # 2) P1 (student 3, recognised by face) sat down in P4's seat: never handed student 7
    people = default_people()
    p1, p4 = people[0], people[3]
    p1.box = p4.box
    people = [p for p in people if p.key != "P4"]
    sim3 = pipeline(sm, clock, rt, people, tid_base=200)
    run(sim3, clock, 6.0)
    assert sim3.identity.track_for_student(7) is None and sim3.identity.track_for_student(3) == 201
    sim3.release()
    # 3) two people where the student was: ambiguous, nobody is restored
    people = default_people()
    x1, y1, x2, y2 = people[3].box
    people.append(SimPerson("NEIGHBOUR", (x1 + 60, y1, x2 + 60, y2)))
    sim4 = pipeline(sm, clock, rt, people, tid_base=300)
    run(sim4, clock, 6.0)
    assert sim4.identity.track_for_student(7) is None
    sim4.release()
    (still,) = [r for r in bindings(sm, rt, 7) if r["ended_at"] is None]    # the teacher's tag is still kept
    assert still["detached_at"] is not None
    sm.end_class(1)


def test_restore_window_runs_from_when_frames_are_back_and_is_bounded(world):
    """Time with no picture (camera off, page closed) is not evidence: the window starts when the new
    pipeline runs. If the student does not reappear within it, nothing is restored any more and the
    teacher re-tags."""
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 1.0)
    sim.coord.tag(TEACHER, 4, 7, clock())
    run(sim, clock, 1.0)
    sim.release()
    clock.advance(IC.restore_max_gap_seconds + 60)                          # monitoring page closed for long
    sim2 = pipeline(sm, clock, rt)
    run(sim2, clock, 3.0)
    assert sim2.identity.track_for_student(7) == 104                        # still restorable: no picture meanwhile
    sim2.release()
    people = [p for p in default_people() if p.key != "P4"]                 # this time the seat stays empty ...
    sim3 = pipeline(sm, clock, rt, people, tid_base=200)
    run(sim3, clock, IC.restore_max_gap_seconds + 5)
    roster = {r["student_id"]: r for r in sim3.coord.snapshot()["roster"]}
    assert roster[7]["track_id"] is None and not roster[7]["awaiting_restore"]   # window over
    sim3.people.append(SimPerson("P4", default_people()[3].box, tid=299))   # ... and the student comes back late
    run(sim3, clock, 5.0)
    assert sim3.identity.track_for_student(7) is None                       # not restored: the teacher re-tags
    sim3.coord.tag(TEACHER, 299, 7, clock())
    open_rows = [r for r in bindings(sm, rt, 7) if r["ended_at"] is None]
    assert len(open_rows) == 1 and open_rows[0]["track_id"] == 299          # one student, one binding
    sim3.release(final=True)
    sm.end_class(1)


def test_no_duplicate_identity_when_the_teacher_tags_the_student_elsewhere_meanwhile(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 1.0)
    sim.coord.tag(TEACHER, 4, 7, clock())
    run(sim, clock, 1.0)
    sim.release()
    sim2 = pipeline(sm, clock, rt)
    run(sim2, clock, 0.25)
    sim2.coord.tag(TEACHER, 105, 7, clock())                                # the teacher says: 7 is P5's track now
    run(sim2, clock, 5.0)
    assert sim2.identity.track_for_student(7) == 105                        # not also restored onto 104
    assert sim2.identity.tracks[104].student_id is None
    open_rows = [r for r in bindings(sm, rt, 7) if r["ended_at"] is None]
    assert [r["track_id"] for r in open_rows] == [105]
    sim2.release(final=True)
    sm.end_class(1)


def test_page_reload_through_the_camera_hub_keeps_the_tag_and_class_end_closes_it(world):
    sm, clock = world
    from app import create_app
    from conftest import DEV_ENV, login
    app, _ = create_app(env=DEV_ENV, async_mode="threading", start_background=False, session_manager=sm)
    app.config["TESTING"] = True
    add_teacher(sm.repo, "teacher2", class_id=2)
    hub = app.extensions["focusguard"]["cameras"]
    c = login(app, "teacher")
    rt = sm.start_class(1, mode="classroom", started_by=2)
    sims = []

    def factory(tid_base):
        def make():
            sims.append(pipeline(sm, clock, rt, tid_base=tid_base))
            return sims[-1]
        return make
    gen = hub.acquire(("classroom", 1, rt.class_session_id), factory(0))
    gen = gen[1] if isinstance(gen, tuple) else gen
    run(sims[0], clock, 1.0)
    r = c.post("/api/teacher/classroom/tag", json={"class_id": 1, "track_id": 4, "student_id": 7})
    assert r.status_code == 200, r.get_data(as_text=True)
    run(sims[0], clock, 1.0)
    hub.release(gen)                                                        # the teacher reloaded the page
    assert sims[0].coord.closed and sm.class_runtime(1) is rt
    hub.acquire(("classroom", 1, rt.class_session_id), factory(500))
    run(sims[1], clock, 3.0)
    assert sims[1].identity.track_for_student(7) == 504
    data = c.get("/api/teacher/classroom/identity?class_id=1").get_json()
    row = next(x for x in data["roster"] if x["student_id"] == 7)
    assert row["track_id"] == 504 and row["identity_source"] == SRC_AUTO_RECOVERED
    assert c.post("/api/teacher/end_class", json={"class_id": 1}).status_code == 200
    assert all(r["ended_at"] is not None for r in bindings(sm, rt))


# ================================================================ fix 3: attendance evidence ledger
def obs(t, tid, binding):
    return Observation(timestamp=t, visible=True, face_visible=True, ear=0.3, yaw=0, pitch=0, track_id=tid,
                       extra={"binding_id": binding, "identity_source": "AUTO_FACE"})


def test_a_valid_check_in_survives_a_later_wrong_re_id_correction(manager, clock):
    rt = manager.start_class(1, mode="classroom", started_by=2)
    rt.process_frame(clock.advance(10), {3: obs(clock(), 2, "b2")})          # A seen validly, on time
    t0 = view(rt, 3)["checkin_at"]
    rt.process_frame(clock.advance(LATE + 100), {3: obs(clock(), 9, "b9")})  # later: someone else matched as A
    assert rt.revoke_attendance_evidence(3, ["b9"], clock(), "teacher_correction", by=2) == 1
    v = view(rt, 3)
    assert v["attendance_status"] == rs.ATT_PRESENT and v["checkin_at"] == t0
    rows = manager.repo.list_attendance_evidence(class_session_id=rt.class_session_id, student_id=3)
    assert len(rows) == 2 and [r["revoked_reason"] for r in rows] == [None, "teacher_correction"]   # kept, not deleted
    manager.end_class(1)


def test_when_the_only_check_in_was_wrong_the_student_returns_to_not_seen(manager, clock):
    rt = manager.start_class(1, mode="classroom", started_by=2)
    rt.process_frame(clock.advance(10), {3: obs(clock(), 9, "b9")})
    assert view(rt, 3)["attendance_status"] == rs.ATT_PRESENT
    rt.revoke_attendance_evidence(3, ["b9"], clock.advance(5), "teacher_correction")
    assert view(rt, 3)["attendance_status"] == rs.ATT_NOT_SEEN and view(rt, 3)["checkin_at"] is None
    manager.end_class(1)


def test_a_correction_never_turns_an_on_time_check_in_into_late(manager, clock):
    rt = manager.start_class(1, mode="classroom", started_by=2)
    rt.process_frame(clock.advance(30), {3: obs(clock(), 2, "b2")})          # on time
    rt.process_frame(clock.advance(LATE), {3: obs(clock(), 9, "b9")})
    rt.revoke_attendance_evidence(3, ["b9"], clock.advance(10), "teacher_correction")
    rt.process_frame(clock.advance(60), {3: obs(clock(), 11, "b11")})        # seen again, long after the threshold
    v = view(rt, 3)
    assert v["attendance_status"] == rs.ATT_PRESENT and v["checkin_offset_seconds"] <= 31
    manager.end_class(1)
    row = next(r for r in manager.repo.get_session_students(class_session_id=rt.class_session_id) if r["student_id"] == 3)
    assert row["attendance_status"] == rs.ATT_PRESENT


def test_a_genuine_first_appearance_after_the_threshold_is_late(manager, clock):
    rt = manager.start_class(1, mode="classroom", started_by=2)
    rt.process_frame(clock.advance(20), {3: obs(clock(), 9, "b9")})          # wrong, early
    rt.revoke_attendance_evidence(3, ["b9"], clock.advance(5), "teacher_correction")
    rt.process_frame(clock.advance(LATE + 30), {3: obs(clock(), 12, "b12")})  # the real A arrives late
    assert view(rt, 3)["attendance_status"] == rs.ATT_LATE
    manager.end_class(1)


def test_a_teacher_attendance_override_survives_every_identity_correction(manager, clock):
    rt = manager.start_class(1, mode="classroom", started_by=2)
    rt.process_frame(clock.advance(10), {3: obs(clock(), 9, "b9"), 4: obs(clock(), 4, "b4")})
    rt.teacher_set_attendance(3, rs.ATT_LATE, clock(), by=2)
    rt.teacher_set_attendance(5, rs.ATT_PRESENT, clock(), by=2)
    rt.revoke_attendance_evidence(3, ["b9"], clock.advance(5), "teacher_correction")
    rt.process_frame(clock.advance(5), {5: obs(clock(), 7, "b7")})
    rt.revoke_attendance_evidence(5, ["b7"], clock.advance(5), "teacher_removed")
    assert view(rt, 3)["attendance_status"] == rs.ATT_LATE and view(rt, 3)["attendance_source"] == rs.ATT_SRC_TEACHER_CORRECTED
    assert view(rt, 5)["attendance_status"] == rs.ATT_PRESENT
    manager.end_class(1)


def test_tracker_re_id_correction_revokes_only_what_depends_on_the_corrected_binding(world):
    """ByteTrack recovery: P3 is recognised as student 5 (bad profile) on track 3; the tracker re-ids
    P3 and 5 is recovered onto the new id (linked TRACK_CONTINUITY). The teacher corrects the NEW
    track: the new binding's evidence is revoked; the earlier binding had its own face confirmation
    and is NOT proven wrong by this correction (a later re-id is an inference), so it is kept — the
    teacher fixes 5's attendance with an explicit override if needed. 6 is checked in."""
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 25.0)
    assert sim.identity.track_for_student(5) == 3 and view(rt, 5)["attendance_status"] == rs.ATT_PRESENT
    sim.control("reid", "P3")
    new_tid = next(p.tid for p in sim.people if p.key == "P3")
    run(sim, clock, IC.track_lost_seconds + 3.0)
    assert sim.identity.track_for_student(5) == new_tid
    rows = {r["binding_id"]: r for r in bindings(sm, rt, 5)}
    cont = next(r for r in rows.values() if r["track_id"] == new_tid)
    assert cont["recovery_method"] == METHOD_TRACK_CONTINUITY and cont["derived_from_binding_id"] in rows
    sim.coord.tag(TEACHER, new_tid, 6, clock(), reason="nhận nhầm")
    ev5 = sm.repo.list_attendance_evidence(class_session_id=rt.class_session_id, student_id=5)
    assert {e["binding_id"] for e in ev5 if e["revoked_at"] is not None} == {cont["binding_id"]}
    assert view(rt, 6)["attendance_status"] == rs.ATT_PRESENT
    # the corrected track's own history (same track) IS revoked: the teacher re-tags it again
    sim.coord.tag(TEACHER, new_tid, 8, clock(), reason="nhầm lần nữa")
    ev6 = sm.repo.list_attendance_evidence(class_session_id=rt.class_session_id, student_id=6)
    assert all(e["revoked_at"] is not None for e in ev6) and view(rt, 6)["attendance_status"] == rs.ATT_NOT_SEEN
    rt.teacher_set_attendance(5, rs.ATT_ABSENT, clock(), by=2)               # the teacher's override wins
    assert view(rt, 5)["attendance_status"] == rs.ATT_ABSENT
    sim.release(final=True)
    sm.end_class(1)


def test_an_independent_recovery_elsewhere_does_not_take_the_real_check_in_with_it(world):
    """Student 3 (P1) checks in on time. P1 leaves; later P5, at another seat, is biased to look like
    P1 and is recovered as 3. The teacher corrects P5's track: 3's own earlier check-in stands."""
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 3.0)
    assert view(rt, 3)["attendance_status"] == rs.ATT_PRESENT
    t0 = view(rt, 3)["checkin_at"]
    sim.control("remove", "P1")
    sim.control("bias", "P5", "P1", weight=0.92)
    run(sim, clock, LATE + 10)
    p5 = next(p.tid for p in sim.people if p.key == "P5")
    assert sim.identity.track_for_student(3) == p5
    sim.coord.tag(TEACHER, p5, 8, clock())
    v = view(rt, 3)
    assert v["attendance_status"] == rs.ATT_PRESENT and v["checkin_at"] == t0      # never false LATE / NOT_SEEN
    sim.release(final=True)
    sm.end_class(1)


def test_camera_reconnect_never_changes_a_valid_check_in(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 3.0)
    before = {s: (view(rt, s)["attendance_status"], view(rt, s)["checkin_at"]) for s in (3, 4)}
    assert before[3][0] == rs.ATT_PRESENT
    sim.release()                                                           # camera lost / pipeline rebuilt
    rt.heartbeat(clock.advance(LATE + 60))                                  # long gap, past the late threshold
    sim2 = pipeline(sm, clock, rt)
    run(sim2, clock, 3.0)                                                   # recognised again by face
    after = {s: (view(rt, s)["attendance_status"], view(rt, s)["checkin_at"]) for s in (3, 4)}
    assert after == before
    sim2.release(final=True)
    sm.end_class(1)


def test_teacher_tag_of_the_same_person_after_a_correction_is_not_lost_with_the_wrong_lineage(world):
    """Correcting a RESTORED binding revokes the restore's own evidence, never the teacher's original tag."""
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 1.0)
    sim.coord.tag(TEACHER, 4, 7, clock())
    run(sim, clock, 1.0)
    sim.release()
    sim2 = pipeline(sm, clock, rt)
    run(sim2, clock, 3.0)
    assert sim2.identity.track_for_student(7) == 104
    sim2.coord.tag(TEACHER, 104, 8, clock())                                # "that is not 7" (e.g. seats swapped)
    assert view(rt, 7)["attendance_status"] == rs.ATT_PRESENT               # 7's own teacher tag earlier stands
    ev = sm.repo.list_attendance_evidence(class_session_id=rt.class_session_id, student_id=7)
    assert [e["identity_source"] for e in ev if e["revoked_at"] is None] == ["TEACHER_TAG"]
    sim2.release(final=True)
    sm.end_class(1)


def test_finalised_absent_then_seen_is_late_and_a_correction_restores_absent(manager, clock):
    from dataclasses import replace
    manager.config = replace(manager.config, attendance=replace(manager.config.attendance, finalize_after_seconds=60.0))
    rt = manager.start_class(1, mode="classroom", started_by=2)
    for _ in range(30):                                                     # >= 60 s and >= 10 clear frames
        rt.process_frame(clock.advance(5), {})
    assert rt.attendance_finalized and view(rt, 3)["attendance_status"] == rs.ATT_ABSENT
    rt.process_frame(clock.advance(5), {3: obs(clock(), 9, "b9")})
    assert view(rt, 3)["attendance_status"] == rs.ATT_LATE
    rt.revoke_attendance_evidence(3, ["b9"], clock.advance(5), "teacher_correction")
    assert view(rt, 3)["attendance_status"] == rs.ATT_ABSENT and view(rt, 3)["attendance_source"] == rs.ATT_SRC_AUTO_FINALIZED
    manager.end_class(1)


@pytest.mark.parametrize("source", ["TEACHER_TAG"])
def test_teacher_tag_evidence_uses_the_person_first_seen_time(world, source):
    """A person in the room since the start and tagged by the teacher later is PRESENT, not LATE."""
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, LATE + 20)                                              # P4 (7) never recognised: no consent
    assert view(rt, 7)["attendance_status"] == rs.ATT_NOT_SEEN
    sim.coord.tag(TEACHER, 4, 7, clock())
    assert view(rt, 7)["attendance_status"] == rs.ATT_PRESENT
    sim.release(final=True)
    sm.end_class(1)


# ================================================================ review round fixes
def test_a_frame_in_flight_cannot_undo_a_correction(manager, clock):
    rt = manager.start_class(1, mode="classroom", started_by=2)
    rt.process_frame(clock.advance(10), {3: obs(clock(), 9, "b9")})
    rt.revoke_attendance_evidence(3, ["b9"], clock.advance(1), "teacher_correction")
    rt.process_frame(clock.advance(0.1), {3: obs(clock(), 9, "b9")})     # planned before the correction
    assert view(rt, 3)["attendance_status"] == rs.ATT_NOT_SEEN
    manager.end_class(1)


def test_a_hidden_face_at_the_seat_is_not_restored_until_it_is_seen_to_match_nobody(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 1.0)
    sim.coord.tag(TEACHER, 4, 7, clock())
    run(sim, clock, 1.0)
    sim.release()
    people = default_people()
    people[3].face = False                                                  # face hidden / turned away
    sim2 = pipeline(sm, clock, rt, people)
    run(sim2, clock, 6.0)
    assert sim2.identity.track_for_student(7) is None                       # could be someone recognisable
    sim2.control("show_face", "P4")
    run(sim2, clock, 3.0)
    assert sim2.identity.track_for_student(7) == 104                        # seen, matches no enrolled student
    sim2.release()
    # a recognisable student hiding the face at that seat is never locked as 7
    people = default_people()
    p1 = people[0]
    p1.box, p1.face = people[3].box, False
    people = [p for p in people if p.key != "P4"]
    sim3 = pipeline(sm, clock, rt, people, tid_base=200)
    run(sim3, clock, 6.0)
    assert sim3.identity.track_for_student(7) is None
    sim3.control("show_face", "P1")
    run(sim3, clock, 4.0)
    assert sim3.identity.track_for_student(3) == 201 and sim3.identity.track_for_student(7) is None
    sim3.release(final=True)
    sm.end_class(1)


def test_a_student_who_walked_out_long_before_a_reload_is_not_shielded_from_away(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 1.0)
    sim.coord.tag(TEACHER, 4, 7, clock())
    run(sim, clock, 1.0)
    sim.control("remove", "P4")                                             # walks out
    run(sim, clock, 60.0)
    sim.release()                                                           # page reload much later
    rt.heartbeat(clock.advance(10))                                         # no picture meanwhile: episodes suspend
    # P5 and four strangers (all unidentified) are in the room: enough "unidentified people" for every
    # carried-over student, so only the carry-over rule decides whether 7 is shielded
    people = [p for p in default_people() if p.key != "P4"] + [
        SimPerson(f"STRANGER{i}", (40 + i * 220, 0, 160 + i * 220, 120)) for i in range(4)]
    sim2 = pipeline(sm, clock, rt, people)
    run(sim2, clock, DEFAULT_CONFIG.behavior.away_min_seconds + 20)
    assert view(rt, 7)["visibility_status"] == rs.VIS_AWAY
    sim2.release(final=True)
    sm.end_class(1)


def test_nothing_is_bound_after_the_class_closed_its_bindings(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 3.0)
    sim.release(final=True)
    persons = {p.tid: p.box for p in sim.people}
    assert sim.coord.identify(clock(), persons, {}) == {}
    assert sim.coord.after_frame(clock(), {}, {})["confirmed"] == {}
    rec = sim.coord.store.start(1, 3, "AUTO_FACE", clock())
    assert not rec.open
    sm.end_class(1)
    assert all(r["ended_at"] is not None for r in bindings(sm, rt))


def test_a_stall_does_not_reopen_the_restore_window_of_a_student_who_left_before(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 1.0)
    sim.coord.tag(TEACHER, 4, 7, clock())
    run(sim, clock, 1.0)
    sim.control("remove", "P4")                                             # 7 leaves; the window runs out
    run(sim, clock, IC.restore_max_gap_seconds + 10)
    clock.advance(IC.outage_seconds + 5)                                    # a camera stall much later
    sim.control("move", "P5", "P4")                                         # someone else takes 7's seat
    run(sim, clock, 6.0)
    assert sim.identity.track_for_student(7) is None                        # never restored onto that person
    sim.release(final=True)
    sm.end_class(1)


def test_a_frame_in_flight_after_close_never_marks_anyone_not_visible(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 3.0)
    last = rt.last_frame_at
    sim.release()                                                           # pipeline closed (reload) ...
    clock.advance(1.0)
    sim._last = 0.0
    sim.get_frame()                                                         # ... while a frame was in flight
    assert rt.last_frame_at == last and view(rt, 3)["visibility_status"] != rs.VIS_AWAY
    sm.end_class(1)


def test_an_unclean_pipeline_death_does_not_widen_the_away_carry_over(world):
    sm, clock = world
    rt, sim = start(sm, clock, initialize=False)
    run(sim, clock, 1.0)
    sim.coord.tag(TEACHER, 4, 7, clock())
    run(sim, clock, 1.0)
    sim.release()                                                           # a clean reload
    only_p4 = [p for p in default_people() if p.key == "P4"]                # nobody else bound in pipeline 2
    sim2 = pipeline(sm, clock, rt, only_p4)
    run(sim2, clock, 3.0)
    assert sim2.identity.track_for_student(7) == 101
    sim2.control("remove", "P4")                                            # 7 walks out ...
    run(sim2, clock, 60.0)                                                  # ... and the pipeline later dies uncleanly
    rt.heartbeat(clock.advance(10))
    people = [p for p in default_people() if p.key != "P4"] + [
        SimPerson(f"STRANGER{i}", (40 + i * 220, 0, 160 + i * 220, 120)) for i in range(4)]
    sim3 = pipeline(sm, clock, rt, people, tid_base=300)
    run(sim3, clock, DEFAULT_CONFIG.behavior.away_min_seconds + 20)
    assert view(rt, 7)["visibility_status"] == rs.VIS_AWAY
    sim3.release(final=True)
    sm.end_class(1)
