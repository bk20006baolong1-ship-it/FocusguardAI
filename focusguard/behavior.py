"""Temporal behaviour analysis.

Behaviours are decided from *durations*, never from frame streaks, so the
outcome is the same at 5, 15 or 30 FPS:

* a blink (eyes closed 0.2 s) never becomes DROWSY,
* a quick glance (head turned 0.5 s) never becomes HEAD_AWAY,
* a phone flash (0.2 s detection) never becomes PHONE,
* short occlusions are TEMPORARILY_NOT_VISIBLE; only a long absence is AWAY.

The analyzer never touches the focus score. It only reports the current
dominant behaviour and emits :class:`BehaviorEpisode` start/end transitions.
"""
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .config import BehaviorConfig, DEFAULT_CONFIG
from . import runtime_state as rs

PHONE = "PHONE"
DROWSY = "DROWSY"
HEAD_AWAY = "HEAD_AWAY"
TEMPORARILY_NOT_VISIBLE = "TEMPORARILY_NOT_VISIBLE"
AWAY = "AWAY"

SIGNAL_KINDS = (PHONE, DROWSY, HEAD_AWAY)
ALERT_KINDS = (PHONE, DROWSY, HEAD_AWAY, AWAY)
# Highest priority first: decides the single dominant focus_state.
PRIORITY = (PHONE, DROWSY, HEAD_AWAY)


@dataclass
class Observation:
    """What the vision pipeline measured for ONE student at ONE instant.

    Every field except ``timestamp`` and ``visible`` is optional: ``None``
    means "not measured", which is different from "measured and negative".
    """
    timestamp: float
    visible: bool
    face_visible: Optional[bool] = None
    ear: Optional[float] = None
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    phone_confidence: Optional[float] = None
    identity_confidence: Optional[float] = None
    track_id: Optional[int] = None
    seat: Optional[Tuple[int, int]] = None
    source: str = "camera"
    extra: dict = field(default_factory=dict)


@dataclass
class BehaviorEpisode:
    student_id: int
    type: str
    start_time: float
    end_time: Optional[float] = None
    confidence: Optional[float] = None
    source: str = "camera"
    metadata: dict = field(default_factory=dict)

    @property
    def duration(self) -> Optional[float]:
        if self.end_time is None:
            return None
        return max(0.0, self.end_time - self.start_time)

    def to_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "type": self.type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": None if self.duration is None else round(self.duration, 3),
            "confidence": None if self.confidence is None else round(self.confidence, 4),
            "source": self.source,
            "metadata": dict(self.metadata),
        }


@dataclass
class _SignalTracker:
    kind: str
    raw_since: Optional[float] = None       # start of the current raw run
    last_true: Optional[float] = None       # last instant the raw signal was on
    frames_true: int = 0
    frames_total: int = 0
    conf_sum: float = 0.0
    conf_n: int = 0
    episode: Optional[BehaviorEpisode] = None

    def reset_run(self):
        self.raw_since = None
        self.last_true = None
        self.frames_true = 0
        self.frames_total = 0
        self.conf_sum = 0.0
        self.conf_n = 0


@dataclass
class BehaviorUpdate:
    started: List[BehaviorEpisode] = field(default_factory=list)
    ended: List[BehaviorEpisode] = field(default_factory=list)
    focus_state: str = rs.FOCUS_UNKNOWN
    visibility_status: str = rs.VIS_NOT_SEEN


class BehaviorAnalyzer:
    """Per-student temporal state machine."""

    def __init__(self, student_id: int, config: BehaviorConfig = DEFAULT_CONFIG.behavior,
                 source: str = "camera"):
        self.student_id = student_id
        self.config = config
        self.source = source
        self._signals: Dict[str, _SignalTracker] = {k: _SignalTracker(k) for k in SIGNAL_KINDS}
        self._ever_visible = False
        self._not_visible_since: Optional[float] = None
        self._away_episode: Optional[BehaviorEpisode] = None
        self.focus_state = rs.FOCUS_UNKNOWN
        self.visibility_status = rs.VIS_NOT_SEEN
        self._ear_hist = deque()                 # (timestamp, ear) of open-eye samples
        self.ear_baseline: Optional[float] = None
        self.ear_closed_threshold: Optional[float] = None

    # ---------------------------------------------------------------- signals
    def _eyes_closed(self, obs: Observation) -> bool:
        """Eye closure relative to THIS student's own open-eye EAR.

        Until enough open-eye samples exist the eyes count as "not measured"
        (never as closed), so a student whose normal EAR is low is not flagged
        drowsy during warm-up. Samples taken while the eyes look closed are not
        added to the baseline, so a long closure cannot drag it down.
        """
        c = self.config
        if obs.ear is None:
            return False
        hist = self._ear_hist
        while hist and obs.timestamp - hist[0][0] > c.ear_baseline_window_seconds:
            hist.popleft()
        closed = False
        ready = (len(hist) >= c.ear_baseline_min_samples
                 and hist[-1][0] - hist[0][0] >= c.ear_baseline_min_seconds)
        if ready:
            vals = sorted(e for _, e in hist)
            self.ear_baseline = vals[int(c.ear_baseline_percentile * (len(vals) - 1))]
            self.ear_closed_threshold = min(c.ear_threshold, c.ear_closed_ratio * self.ear_baseline)
        if self.ear_closed_threshold is not None:
            # Keep the last baseline when recent open-eye samples aged out
            # (e.g. eyes closed longer than the window).
            closed = obs.ear < self.ear_closed_threshold
        if not closed:
            hist.append((obs.timestamp, obs.ear))
        return closed

    def raw_signals(self, obs: Observation) -> Dict[str, Tuple[bool, Optional[float]]]:
        """Threshold raw measurements. Returns ``{kind: (on, confidence)}``."""
        c = self.config
        phone_on = obs.phone_confidence is not None and obs.phone_confidence >= c.phone_min_confidence
        eyes_closed = self._eyes_closed(obs)
        head_away = False
        if obs.yaw is not None and abs(obs.yaw) > c.yaw_limit_deg:
            head_away = True
        if obs.pitch is not None and abs(obs.pitch) > c.pitch_limit_deg:
            head_away = True
        return {
            PHONE: (phone_on, obs.phone_confidence if phone_on else None),
            DROWSY: (eyes_closed, None),
            HEAD_AWAY: (head_away, None),
        }

    def _min_seconds(self, kind: str) -> float:
        c = self.config
        return {PHONE: c.phone_min_seconds, DROWSY: c.drowsy_min_seconds,
                HEAD_AWAY: c.head_away_min_seconds}[kind]

    # ---------------------------------------------------------------- update
    def update(self, obs: Observation) -> BehaviorUpdate:
        out = BehaviorUpdate()
        now = obs.timestamp
        if obs.visible:
            self._on_visible(obs, now, out)
        else:
            self._on_not_visible(now, out)
        out.focus_state = self.focus_state
        out.visibility_status = self.visibility_status
        return out

    def _on_visible(self, obs: Observation, now: float, out: BehaviorUpdate):
        self._ever_visible = True
        if self._away_episode is not None:
            self._away_episode.end_time = now
            out.ended.append(self._away_episode)
            self._away_episode = None
        self._not_visible_since = None
        self.visibility_status = rs.VIS_VISIBLE

        raw = self.raw_signals(obs)
        for kind, tracker in self._signals.items():   # PHONE is stepped first
            on, conf = raw[kind]
            if kind == DROWSY and self._phone_active(now):
                # Eyes looking down at a phone look "closed" to EAR; while a
                # phone is in use the eye signal is not evidence of drowsiness.
                on = False
            self._step_signal(tracker, on, conf, now, obs, out)

        self.focus_state = rs.FOCUS_FOCUSED
        for kind in PRIORITY:
            if self._signals[kind].episode is not None:
                self.focus_state = kind
                break
        else:
            if obs.face_visible is False:
                # Body visible but no face: eyes/head cannot be measured, so
                # the state is unknown (score frozen), never "focused".
                self.focus_state = rs.FOCUS_UNKNOWN

    def _gap(self, kind: str) -> float:
        return self.config.phone_gap_tolerance_seconds if kind == PHONE else self.config.gap_tolerance_seconds

    def _phone_active(self, now: float) -> bool:
        t = self._signals[PHONE]
        return t.last_true is not None and now - t.last_true <= self._gap(PHONE)

    def _step_signal(self, t: _SignalTracker, on: bool, conf: Optional[float], now: float,
                     obs: Observation, out: BehaviorUpdate):
        gap = self._gap(t.kind)
        if on:
            if t.raw_since is None or (t.last_true is not None and now - t.last_true > gap):
                # New run (or the previous one lapsed beyond tolerance).
                if t.episode is not None:
                    self._end_signal_episode(t, out)
                t.reset_run()
                t.raw_since = now
            t.last_true = now
            t.frames_true += 1
            t.frames_total += 1
            if conf is not None:
                t.conf_sum += conf
                t.conf_n += 1
            if t.episode is None and (now - t.raw_since) >= self._min_seconds(t.kind):
                t.episode = BehaviorEpisode(
                    student_id=self.student_id, type=t.kind, start_time=t.raw_since,
                    source=obs.source or self.source,
                    metadata={"min_duration_seconds": self._min_seconds(t.kind),
                              "track_id": obs.track_id, "detected_at": now},
                )
                out.started.append(t.episode)
        else:
            if t.raw_since is None:
                return
            t.frames_total += 1
            if now - (t.last_true or now) > gap:
                if t.episode is not None:
                    self._end_signal_episode(t, out)
                t.reset_run()

    def _end_signal_episode(self, t: _SignalTracker, out: BehaviorUpdate, end_time: Optional[float] = None):
        ep = t.episode
        if ep is None:
            return
        ep.end_time = end_time if end_time is not None else (t.last_true or ep.start_time)
        consistency = t.frames_true / t.frames_total if t.frames_total else None
        if t.conf_n:
            det_conf = t.conf_sum / t.conf_n
            ep.confidence = det_conf * (consistency if consistency is not None else 1.0)
            ep.metadata["mean_detector_confidence"] = round(det_conf, 4)
        else:
            ep.confidence = consistency
        ep.metadata["frames_on"] = t.frames_true
        ep.metadata["frames_total"] = t.frames_total
        out.ended.append(ep)
        t.episode = None

    def _on_not_visible(self, now: float, out: BehaviorUpdate):
        # Behaviour signals cannot continue while the student is not visible.
        for t in self._signals.values():
            if t.episode is not None:
                self._end_signal_episode(t, out)
            t.reset_run()
        if not self._ever_visible:
            self.visibility_status = rs.VIS_NOT_SEEN
            self.focus_state = rs.FOCUS_UNKNOWN
            return
        if self._not_visible_since is None:
            self._not_visible_since = now
        gone_for = now - self._not_visible_since
        if gone_for >= self.config.away_min_seconds:
            if self._away_episode is None:
                self._away_episode = BehaviorEpisode(
                    student_id=self.student_id, type=AWAY, start_time=self._not_visible_since,
                    confidence=None, source=self.source,
                    metadata={"min_duration_seconds": self.config.away_min_seconds, "detected_at": now},
                )
                out.started.append(self._away_episode)
            self.visibility_status = rs.VIS_AWAY
            self.focus_state = rs.FOCUS_AWAY
        else:
            self.visibility_status = rs.VIS_TEMPORARILY_NOT_VISIBLE
            self.focus_state = rs.FOCUS_TEMPORARILY_NOT_VISIBLE

    @property
    def not_visible_since(self) -> Optional[float]:
        return self._not_visible_since

    def active_episodes(self) -> List[BehaviorEpisode]:
        eps = [t.episode for t in self._signals.values() if t.episode is not None]
        if self._away_episode is not None:
            eps.append(self._away_episode)
        return eps

    def close(self, now: float) -> List[BehaviorEpisode]:
        """End every open episode at ``now`` (class ended / runtime shutdown)."""
        out = BehaviorUpdate()
        for t in self._signals.values():
            if t.episode is not None:
                self._end_signal_episode(t, out, end_time=max(t.last_true or now, t.episode.start_time))
            t.reset_run()
        if self._away_episode is not None:
            self._away_episode.end_time = now
            out.ended.append(self._away_episode)
            self._away_episode = None
        return out.ended
