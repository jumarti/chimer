"""
tests/test_sequence.py — L4: end-to-end pipeline + temporal aggregator integration.

Core regression: the system MUST NOT flip state on transient noise.
Uses synthetic FrameResults to drive the aggregator through realistic sequences.
"""
from __future__ import annotations

import time

import pytest

import config
from detector import FrameResult, GateClassifier, TemporalAggregator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(state: str, quality: float = 0.9) -> FrameResult:
    return FrameResult(
        state=state,
        confidence=0.9 if state != "uncertain" else 0.0,
        closed_score=0.85 if state == "closed" else 0.1,
        open_score=0.85 if state == "open" else 0.1,
        anchor_offset=(0, 0),
        quality=quality if state != "uncertain" else 0.0,
        is_ir=False,
        timestamp=time.time(),
    )


def _run_sequence(frames: list[FrameResult]) -> list[str]:
    """Feed a list of FrameResults through a fresh aggregator; return all reported states."""
    agg = TemporalAggregator()
    states = []
    for fr in frames:
        reported, _ = agg.update(fr)
        states.append(reported)
    return states


# ---------------------------------------------------------------------------
# FALSE-DETECTION REGRESSION TESTS
# These are the most important tests in the suite.
# ---------------------------------------------------------------------------

class TestNoFalseDetections:
    def test_single_noise_frame_does_not_flip(self):
        """
        REGRESSION: one stray 'open' frame in a stable-closed window
        MUST NOT flip the reported state.
        """
        frames = (
            [_result("closed")] * config.FLIP_THRESHOLD   # commit to closed
            + [_result("open")]                            # single noise
            + [_result("closed")] * 2                     # back to closed
        )
        states = _run_sequence(frames)
        # After the initial commit, state must never flip to 'open'.
        committed = states[config.FLIP_THRESHOLD - 1]
        assert committed == "closed"
        post_noise = states[config.FLIP_THRESHOLD :]
        assert all(s == "closed" for s in post_noise), (
            f"False detection: state flipped after single noise frame — {post_noise}"
        )

    def test_burst_of_noise_below_threshold_does_not_flip(self):
        """
        A burst of FLIP_THRESHOLD-1 opposing frames must not flip state.
        """
        frames = (
            [_result("closed")] * config.FLIP_THRESHOLD
            + [_result("open")] * (config.FLIP_THRESHOLD - 1)
        )
        states = _run_sequence(frames)
        assert states[-1] == "closed", (
            f"State flipped after {config.FLIP_THRESHOLD - 1} opposing frames — should not"
        )

    def test_uncertain_burst_does_not_change_state(self):
        """
        A long run of uncertain frames between two stable states must
        leave the reported state unchanged.
        """
        frames = (
            [_result("closed")] * config.FLIP_THRESHOLD
            + [_result("uncertain")] * 20
            + [_result("closed")] * 2
        )
        states = _run_sequence(frames)
        assert all(s == "closed" for s in states[config.FLIP_THRESHOLD :]), (
            "Uncertain burst changed the state"
        )

    def test_alternating_noise_does_not_flip(self):
        """
        Rapidly alternating open/closed noise must not accumulate enough
        votes to flip a stable state.
        """
        # Establish closed.
        base = [_result("closed")] * config.FLIP_THRESHOLD
        # Alternating noise (equal votes for open and closed).
        noise = [_result("open"), _result("closed")] * (config.WINDOW_SIZE // 2)
        frames = base + noise
        states = _run_sequence(frames)
        # State must remain closed (alternating adds equal votes → no dominant).
        final = states[-1]
        assert final == "closed", f"Alternating noise caused unexpected flip to '{final}'"


# ---------------------------------------------------------------------------
# CORRECT DETECTION TESTS
# ---------------------------------------------------------------------------

class TestCorrectDetections:
    def test_full_sequence_commits_and_flips(self):
        """
        Full realistic sequence: CLOSED → OPEN flip after FLIP_THRESHOLD open frames.
        """
        frames = (
            [_result("closed")] * config.FLIP_THRESHOLD
            + [_result("open")]  * config.FLIP_THRESHOLD
        )
        states = _run_sequence(frames)
        assert states[config.FLIP_THRESHOLD - 1]  == "closed", "Should commit closed first"
        assert states[-1] == "open", "Should flip to open after enough frames"

    def test_state_stays_unknown_during_uncertain_period(self):
        """
        If ALL frames are uncertain, state never leaves 'unknown'.
        """
        frames = [_result("uncertain")] * 30
        states = _run_sequence(frames)
        assert all(s == "unknown" for s in states)

    def test_recovery_after_obstruction(self):
        """
        After a long uncertain gap (obstruction), valid frames eventually
        commit the correct state again.
        """
        frames = (
            [_result("closed")] * config.FLIP_THRESHOLD
            + [_result("uncertain")] * 15          # extended obstruction
            + [_result("open")] * config.FLIP_THRESHOLD   # gate opened
        )
        states = _run_sequence(frames)
        assert states[-1] == "open", (
            "After obstruction clears, should detect open state"
        )


# ---------------------------------------------------------------------------
# INTEGRATION: GateClassifier + TemporalAggregator with real images
# ---------------------------------------------------------------------------

class TestFullPipelineWithImages:
    def test_closed_sequence_stays_closed(self, closed_frames):
        if len(closed_frames) < 2:
            pytest.skip("Need at least 2 closed frames")

        clf = GateClassifier()
        agg = TemporalAggregator()

        # Feed all closed frames repeatedly until window is full.
        last_state = "unknown"
        for _ in range(config.FLIP_THRESHOLD):
            for frame in closed_frames[:3]:  # use first 3 closed frames
                result = clf.classify(frame)
                last_state, _ = agg.update(result)

        # State should have committed to closed (or at least not open).
        assert last_state != "open", (
            f"Closed frame sequence reported '{last_state}'"
        )

    def test_open_sequence_stays_open(self, open_frames):
        if len(open_frames) < 2:
            pytest.skip("Need at least 2 open frames")

        clf = GateClassifier()
        agg = TemporalAggregator()

        last_state = "unknown"
        for _ in range(config.FLIP_THRESHOLD):
            for frame in open_frames[:3]:
                result = clf.classify(frame)
                last_state, _ = agg.update(result)

        assert last_state != "closed", (
            f"Open frame sequence reported '{last_state}'"
        )
