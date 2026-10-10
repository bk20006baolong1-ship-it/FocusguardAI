import numpy as np
import pytest

from focusguard.config import IdentityConfig
from focusguard.evaluation.synthetic import noisy, random_gallery
from focusguard.identity import AMBIGUOUS, CANDIDATE, CONFIRMED, CONFLICT, MATCH, NO_FACE, NO_MATCH, \
    IdentityManager

FPS = 10.0
T0 = 1_000.0


@pytest.fixture
def gallery():
    return random_gallery(5, seed=7)


def run(mgr, seconds, frame_fn, start=T0):
    """frame_fn(t_rel) -> list[(track_id, embedding)] ; returns per-frame decisions."""
    out = []
    n = int(seconds * FPS)
    for i in range(n):
        t = start + i / FPS
        out.append((t, mgr.process_frame(frame_fn(i / FPS), t)))
    return out


def test_match_requires_threshold_and_margin(gallery):
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(0)
    assert mgr.match(noisy(gallery[1], 0.02, rng)).outcome == MATCH
    assert mgr.match(None).outcome == NO_FACE
    stranger = random_gallery(1, seed=999)[1]
    assert mgr.match(stranger).outcome == NO_MATCH
    blend = (gallery[1] + gallery[2]) / 2
    assert mgr.match(blend).outcome == AMBIGUOUS


def test_identity_is_stable_and_confirmed_only_after_temporal_evidence(gallery):
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(1)
    frames = run(mgr, 5.0, lambda t: [(7, noisy(gallery[3], 0.05, rng))])
    first = frames[0][1][7]
    assert first.status == CANDIDATE and first.student_id is None
    confirmed_at = next(t for t, d in frames if d[7].status == CONFIRMED)
    assert confirmed_at - T0 >= IdentityConfig().confirm_min_seconds
    after = [d[7].student_id for t, d in frames if t >= confirmed_at]
    assert set(after) == {3}
    assert mgr.track_for_student(3) == 7


def test_single_bad_frame_does_not_flip_locked_identity(gallery):
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(2)

    def fn(t):
        sid = 2 if 3.0 <= t < 3.3 else 1   # 3 wrong frames
        return [(1, noisy(gallery[sid], 0.03, rng))]
    frames = run(mgr, 6.0, fn)
    assert frames[-1][1][1].student_id == 1
    assert all(d[1].student_id in (None, 1) for _, d in frames)


def test_track_change_recovers_identity(gallery):
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(3)

    def fn(t):
        if t < 3.0:
            return [(10, noisy(gallery[4], 0.03, rng))]
        if t < 4.0:
            return []                                  # occlusion
        return [(11, noisy(gallery[4], 0.03, rng))]    # tracker assigned a new id
    frames = run(mgr, 12.0, fn)
    assert frames[25][1][10].student_id == 4
    recovered = [t for t, d in frames if 11 in d and d[11].student_id == 4]
    assert recovered, "identity was not recovered on the new track"
    assert any(e.kind == "RECOVERED" for e in mgr.events)
    assert mgr.track_for_student(4) == 11


def test_one_student_cannot_bind_two_live_tracks(gallery):
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(4)
    frames = run(mgr, 5.0, lambda t: [(1, noisy(gallery[5], 0.03, rng)), (2, noisy(gallery[5], 0.03, rng))])
    for _, d in frames:
        bound = [tid for tid, x in d.items() if x.student_id == 5]
        assert len(bound) <= 1
    last = frames[-1][1]
    assert {last[1].status, last[2].status} == {CONFIRMED, CONFLICT}


def test_crossing_does_not_swap_identities(gallery):
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(5)

    def fn(t):
        a, b = 1, 2
        if 4.0 <= t < 4.6:   # tracks cross: face boxes swap for 0.6 s
            a, b = 2, 1
        return [(100, noisy(gallery[a], 0.03, rng)), (200, noisy(gallery[b], 0.03, rng))]
    frames = run(mgr, 8.0, fn)
    for t, d in frames:
        assert d[100].student_id in (None, 1)
        assert d[200].student_id in (None, 2)
    assert frames[-1][1][100].student_id == 1 and frames[-1][1][200].student_id == 2


def test_uncertain_identity_stays_unknown(gallery):
    mgr = IdentityManager(gallery)
    blend = (gallery[1] + gallery[2]) / 2
    stranger = random_gallery(1, seed=321)[1]
    frames = run(mgr, 4.0, lambda t: [(1, blend), (2, stranger), (3, None)])
    for _, d in frames:
        assert all(x.student_id is None for x in d.values())
        assert d[1].status != CONFIRMED and d[2].status != CONFIRMED and d[3].status != CONFIRMED


def test_bindings_are_one_to_one(gallery):
    mgr = IdentityManager(gallery)
    rng = np.random.default_rng(6)
    run(mgr, 4.0, lambda t: [(i, noisy(gallery[i], 0.03, rng)) for i in range(1, 6)])
    b = mgr.bindings()
    assert len(set(b.values())) == len(b) == 5
