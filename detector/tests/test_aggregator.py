"""
tests/test_aggregator.py — L1: pure temporal-aggregator logic.

No images needed.  All frames are synthetic FrameResult objects.
Exercises:
  - Initial state is "unknown"
  - Uncertain / low-quality frames are skipped (don't fill the window)
  - FLIP_THRESHOLD frames needed to commit a new state
  - Fewer than FLIP_THRESHOLD never flips
  - Hysteresis: once committed, opposing frames below threshold don't flip
"""
from __future__ import annotations

import time

import pytest

import config
from detector import FrameResult, TemporalAggregator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    state: str,
    confidence: float = 0.9,
    quality: float = 0.9,
) -> FrameResult:
    return FrameResult(
        state=state,
        confidence=confidence,
        closed_score=confidence if state == "closed" else 0.1,
        open_score=confidence if state == "open" else 0.1,
        anchor_offset=(0, 0),
        quality=quality,
        is_ir=False,
        timestamp=time.time(),
    )


def _feed(agg: TemporalAggregator, state: str, n: int) -> str:
    reported = "unknown"
    for _ in range(n):
        reported, _ = agg.update(_make_result(state))
    return reported


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_starts_unknown(self):
        agg = TemporalAggregator()
        assert agg.reported_state == "unknown"

    def test_zero_frames_still_unknown(self):
        agg = TemporalAggregator()
        assert agg.reported_state == "unknown"


class TestFlipThreshold:
    def test_exact_threshold_flips(self):
        agg = TemporalAggregator()
        state = _feed(agg, "closed", config.FLIP_THRESHOLD)
        assert state == "closed"

    def test_below_threshold_no_flip(self):
        agg = TemporalAggregator()
        state = _feed(agg, "open", config.FLIP_THRESHOLD - 1)
        assert state == "unknown"

    def test_one_frame_does_not_flip(self):
        agg = TemporalAggregator()
        state, _ = agg.update(_make_result("closed"))
        assert state == "unknown"


class TestUncertainFramesSkipped:
    def test_uncertain_frames_not_counted(self):
        agg = TemporalAggregator()
        # Inject many uncertain frames — window should stay empty.
        for _ in range(20):
            agg.update(_make_result("uncertain", confidence=0.0, quality=0.0))
        assert agg.reported_state == "unknown"
        assert len(agg.window_snapshot()) == 0

    def test_low_quality_frames_skipped(self):
        agg = TemporalAggregator()
        low_q = config.MIN_FRAME_QUALITY - 0.01
        for _ in range(20):
            agg.update(_make_result("open", quality=low_q))
        assert agg.reported_state == "unknown"

    def test_uncertain_between_good_frames_not_counted(self):
        """Uncertain frames interspersed in a good run don't dilute the window."""
        agg = TemporalAggregator()
        for _ in range(config.FLIP_THRESHOLD):
            agg.update(_make_result("closed"))        # good
            agg.update(_make_result("uncertain", quality=0.0))  # skipped
        assert agg.reported_state == "closed"


class TestHysteresis:
    def test_stable_state_not_flipped_by_minority(self):
        agg = TemporalAggregator()
        # Establish CLOSED.
        _feed(agg, "closed", config.FLIP_THRESHOLD)
        assert agg.reported_state == "closed"

        # Inject FLIP_THRESHOLD - 1 "open" frames — not enough to flip.
        state = _feed(agg, "open", config.FLIP_THRESHOLD - 1)
        assert state == "closed", (
            f"State should remain 'closed' but flipped to '{state}'"
        )

    def test_sufficient_opposing_frames_do_flip(self):
        agg = TemporalAggregator()
        _feed(agg, "closed", config.FLIP_THRESHOLD)
        state = _feed(agg, "open", config.FLIP_THRESHOLD)
        assert state == "open"

    def test_noise_does_not_flip_stable_state(self):
        """
        Core false-detection regression:
        One stray 'open' frame in a window full of 'closed' must NOT flip state.
        """
        agg = TemporalAggregator()
        _feed(agg, "closed", config.FLIP_THRESHOLD)
        # Single noise frame.
        agg.update(_make_result("open"))
        assert agg.reported_state == "closed"


class TestWindowSize:
    def test_old_frames_evicted(self):
        """After WINDOW_SIZE + N frames, only WINDOW_SIZE frames are counted."""
        agg = TemporalAggregator()
        # Fill window with "closed".
        _feed(agg, "closed", config.FLIP_THRESHOLD)
        # Now overflow with "open" — old closed frames evicted → should flip.
        _feed(agg, "open", config.WINDOW_SIZE)
        assert agg.reported_state == "open"
