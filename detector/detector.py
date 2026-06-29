"""
detector/detector.py — two-zone gate classifier + temporal aggregator.

Approach
--------
Retroreflective markers are the brightest objects in every lighting
condition (day colour, dusk, IR night).  Rather than frame-differencing,
we look at WHERE markers currently are:

  CLOSED  marker(s) present in ZONE_CLOSED  AND  NOT in ZONE_OPEN
  OPEN    marker(s) present in ZONE_OPEN    AND  NOT in ZONE_CLOSED
  UNCERTAIN  any other combination (both lit, neither lit, anchor lost)

Temporal aggregation
--------------------
A sliding window of WINDOW_SIZE qualifying frames is maintained.
Uncertain or low-quality frames are discarded, not counted.
The reported state only flips when FLIP_THRESHOLD frames in the window
agree — heavily biased against false detections.
"""
from __future__ import annotations

import collections
import logging
import time
from dataclasses import dataclass, field

import numpy as np

import config
from marker import (
    ZoneResult,
    annotate_frame,
    find_anchor_offset,
    find_blobs_in_zone,
    is_ir_mode,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class FrameResult:
    """Full per-frame detection output."""
    state: str            # "open" | "closed" | "uncertain"
    confidence: float     # 0–1
    closed_score: float   # zone confidence for CLOSED zone
    open_score:   float   # zone confidence for OPEN zone
    anchor_offset: tuple[int, int] | None  # (dx, dy); None = anchor lost
    quality: float        # max(closed_score, open_score) — frame usefulness
    is_ir: bool
    timestamp: float = field(default_factory=time.time)
    # "big_blade" | "small_blade" | None.  Set when state=="open";
    # None for closed/uncertain or when flag is off.
    open_reason: str | None = field(default=None)
    # Kept for annotation; excluded from JSON serialisation.
    closed_result: ZoneResult | None = field(default=None, repr=False)
    open_result:   ZoneResult | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        """Serialisable summary (no numpy objects)."""
        return {
            "state": self.state,
            "confidence": round(self.confidence, 4),
            "closed_score": round(self.closed_score, 4),
            "open_score": round(self.open_score, 4),
            "anchor_offset": list(self.anchor_offset) if self.anchor_offset else None,
            "quality": round(self.quality, 4),
            "is_ir": self.is_ir,
            "open_reason": self.open_reason,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Per-frame classifier
# ---------------------------------------------------------------------------

class GateClassifier:
    """Classifies a single frame into open / closed / uncertain."""

    def classify(self, frame: np.ndarray) -> FrameResult:
        ir = is_ir_mode(frame)

        # --- Camera drift correction via anchor marker ---
        offset = find_anchor_offset(frame)
        if offset is None:
            logger.warning("Anchor lost — returning UNCERTAIN")
            return FrameResult(
                state="uncertain", confidence=0.0,
                closed_score=0.0, open_score=0.0,
                anchor_offset=None, quality=0.0, is_ir=ir,
            )

        dx, dy = offset
        drift = (dx ** 2 + dy ** 2) ** 0.5
        if drift > config.ANCHOR_MAX_DRIFT:
            logger.warning("Camera drift %.1f px exceeds limit — UNCERTAIN", drift)
            return FrameResult(
                state="uncertain", confidence=0.0,
                closed_score=0.0, open_score=0.0,
                anchor_offset=offset, quality=0.0, is_ir=ir,
            )

        # IR-aware detection threshold.
        threshold = config.BRIGHT_THRESHOLD_IR if ir else config.BRIGHT_THRESHOLD

        # Shift both zones by the measured drift.
        def _shift(zone: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
            x, y, w, h = zone
            return (max(0, x + dx), max(0, y + dy), w, h)

        closed_res = find_blobs_in_zone(
            frame, _shift(config.ZONE_CLOSED), margin=config.SEARCH_MARGIN,
            bright_threshold=threshold,
        )
        open_res = find_blobs_in_zone(
            frame, _shift(config.ZONE_OPEN), margin=config.SEARCH_MARGIN,
            bright_threshold=threshold,
        )

        closed_score = closed_res.confidence
        open_score   = open_res.confidence

        closed_hit = closed_res.has_marker
        open_hit   = open_res.has_marker

        # Frame-level sanity: if both zones are completely dark the camera is
        # temporarily blinded (AGC recovering from a bright headlight).  Skip
        # the frame entirely so the aggregator ignores it.
        if closed_res.bright_px == 0 and open_res.bright_px == 0:
            logger.warning("Both zones dark — camera may be blinded, returning UNCERTAIN")
            return FrameResult(
                state="uncertain", confidence=0.0,
                closed_score=0.0, open_score=0.0,
                anchor_offset=offset, quality=0.0, is_ir=ir,
                closed_result=closed_res, open_result=open_res,
            )

        # ZONE_OPEN saturation guard: raw bright_px far exceeding any real marker
        # signals a vehicle headlight or direct sun in frame — treat as UNCERTAIN.
        # Real gate-bar markers produce 475-710 px; headlights produce 7000+ px.
        _OPEN_SAT_LIMIT = config.ZONE_BRIGHT_MIN_PX * 80  # 50 * 80 = 4000 px
        if open_res.bright_px > _OPEN_SAT_LIMIT:
            logger.warning(
                "ZONE_OPEN saturated (%d px > %d) — headlight/sun in frame, UNCERTAIN",
                open_res.bright_px, _OPEN_SAT_LIMIT,
            )
            return FrameResult(
                state="uncertain", confidence=0.0,
                closed_score=0.0, open_score=0.0,
                anchor_offset=offset, quality=0.0, is_ir=ir,
                closed_result=closed_res, open_result=open_res,
            )
        # ---------------------------------------------------------------
        open_reason: str | None = None
        if config.ZONE_LATCH is not None:
            # --- LATCH-PRIMARY mode (fine-grained, requires calibration) ---
            # ZONE_LATCH is a tight zone that covers ONLY the retroreflective
            # marker overlap footprint.  Any movement (even a few cm) takes
            # the markers outside it, so absence == open.
            #
            #   latch_hit              → CLOSED  (markers perfectly aligned)
            #   ¬latch_hit + open_hit  → OPEN    (fully open, high confidence)
            #   ¬latch_hit + ¬open_hit → OPEN    (slightly open / between zones)
            latch_threshold = config.BRIGHT_THRESHOLD_IR_LATCH if ir else threshold
            latch_res = find_blobs_in_zone(
                frame, _shift(config.ZONE_LATCH), margin=0,
                bright_threshold=latch_threshold,
            )
            # Require a substantial marker signal in ZONE_LATCH.
            # IR mode: use sum-of-areas (external lights can fragment the blob into
            #   several small pieces; concrete is dark at night so false positives
            #   are not a concern).
            # DAY mode: use max single-blob (concrete scatter sums can exceed the
            #   threshold even though no individual blob qualifies).
            if ir:
                latch_hit = sum(b.area for b in latch_res.blobs) >= config.LATCH_MIN_BLOB_AREA
            else:
                latch_hit = any(b.area >= config.LATCH_MIN_BLOB_AREA for b in latch_res.blobs)
            if latch_hit:
                state      = "closed"
                confidence = latch_res.confidence
                # --- Small-blade-open check (feature-flagged) ---------------
                # Even though the big-blade marker is at its latch position,
                # the small blade may be open.  Detected by: small marker
                # absent from its rest zone AND present in its open zone.
                if (
                    config.DETECT_SMALL_BLADE_OPEN
                    and config.ZONE_SMALL_REST is not None
                    and config.ZONE_SMALL_OPEN is not None
                ):
                    # REST zone: use the dim IR threshold so we reliably detect
                    # the marker even in low-IR-illumination closed frames and
                    # avoid false "marker absent" readings.
                    small_rest_threshold = (
                        config.BRIGHT_THRESHOLD_IR_LATCH_DIM if ir else threshold
                    )
                    small_rest_res = find_blobs_in_zone(
                        frame, _shift(config.ZONE_SMALL_REST), margin=0,
                        bright_threshold=small_rest_threshold,
                    )
                    # OPEN zone: use the standard IR/day threshold so only a
                    # clearly-bright marker (not scattered noise) qualifies.
                    small_open_res = find_blobs_in_zone(
                        frame, _shift(config.ZONE_SMALL_OPEN), margin=config.SEARCH_MARGIN,
                        bright_threshold=threshold,
                    )
                    # Area-based checks mirror the latch logic: sum in IR mode
                    # (fragmentation), max single-blob in day mode (scatter).
                    if ir:
                        small_rest_area = sum(b.area for b in small_rest_res.blobs)
                        small_open_area = sum(b.area for b in small_open_res.blobs)
                    else:
                        small_rest_area = max(
                            (b.area for b in small_rest_res.blobs), default=0
                        )
                        small_open_area = max(
                            (b.area for b in small_open_res.blobs), default=0
                        )
                    small_rest_hit = small_rest_area >= config.SMALL_BLADE_MIN_BLOB_AREA
                    if not small_rest_hit and small_open_area >= config.SMALL_BLADE_MIN_BLOB_AREA:
                        state       = "open"
                        open_reason = "small_blade"
                        confidence  = small_open_res.confidence
                        logger.info(
                            "Small-blade open detected: rest_area=%d open_area=%d conf=%.2f",
                            small_rest_area, small_open_area, confidence,
                        )
                # ------------------------------------------------------------
            else:
                # IR dim fallback: re-check ZONE_LATCH at a lower threshold.
                # Handles cases where the marker is at the latch position but
                # dim (AGC, weak IR illumination).  Uses ZONE_LATCH (not
                # ZONE_CLOSED) to preserve spatial discrimination: an open-gate
                # marker in the lower part of ZONE_CLOSED will NOT appear in
                # the tight ZONE_LATCH regardless of threshold.
                if ir:
                    latch_dim_res = find_blobs_in_zone(
                        frame, _shift(config.ZONE_LATCH), margin=0,
                        bright_threshold=config.BRIGHT_THRESHOLD_IR_LATCH_DIM,
                    )
                    latch_dim_hit = (
                        sum(b.area for b in latch_dim_res.blobs)
                        >= config.LATCH_MIN_BLOB_AREA
                    )
                else:
                    latch_dim_hit = False

                if latch_dim_hit:
                    state      = "closed"
                    confidence = min(latch_dim_res.confidence, 0.7)  # lower than direct hit
                elif open_hit:
                    state      = "open"
                    open_reason = "big_blade"
                    confidence = open_score
                else:
                    # Markers absent from both latch zones and open zone → gate is ajar.
                    state      = "open"
                    open_reason = "big_blade"
                    confidence = 0.45  # inferred from absence; counts in window (> MIN_FRAME_QUALITY)
            quality = max(latch_res.confidence, open_score) if latch_hit or open_hit else 0.5
        else:
            # --- ZONE_OPEN-PRIMARY mode (default, works without calibration) ---
            # ZONE_OPEN (sky/vegetation) is almost always dark when closed, so
            # a marker there is a clean OPEN signal.  ZONE_CLOSED is used only
            # as a secondary confirmation because sunlit concrete can mimic
            # marker brightness in the larger ZONE_CLOSED area.
            #
            #   open_hit              → OPEN
            #   ¬open_hit + closed_hit → CLOSED
            #   neither               → UNCERTAIN
            if open_hit:
                state      = "open"
                open_reason = "big_blade"
                confidence = open_score * (1.0 - closed_score * 0.15)
            elif closed_hit:
                state      = "closed"
                confidence = closed_score
            else:
                state      = "uncertain"
                confidence = 0.0
            quality = max(closed_score, open_score)

        logger.debug(
            "classify: %-9s conf=%.2f  closed=%s(%.2f)  open=%s(%.2f)"
            "  drift=(%+d,%+d)  ir=%s  reason=%s",
            state, confidence,
            "HIT " if closed_hit else "miss", closed_score,
            "HIT " if open_hit   else "miss", open_score,
            dx, dy, ir, open_reason,
        )
        return FrameResult(
            state=state, confidence=confidence,
            closed_score=closed_score, open_score=open_score,
            anchor_offset=offset, quality=quality, is_ir=ir,
            open_reason=open_reason,
            closed_result=closed_res, open_result=open_res,
        )

    def annotate(self, frame: np.ndarray, result: FrameResult) -> np.ndarray:
        """Return an annotated debug copy of the frame."""
        if result.closed_result is None or result.open_result is None:
            return frame.copy()
        return annotate_frame(
            frame,
            closed_result=result.closed_result,
            open_result=result.open_result,
            anchor_offset=result.anchor_offset,
            state=result.state,
            confidence=result.confidence,
            is_ir=result.is_ir,
        )


# ---------------------------------------------------------------------------
# Temporal aggregator
# ---------------------------------------------------------------------------

class TemporalAggregator:
    """
    Sliding-window smoother biased toward *fewer* false state transitions.

    - Uncertain and low-quality frames are discarded before entering the window.
    - A new state is committed only when FLIP_THRESHOLD qualifying frames agree.
    - Window length = WINDOW_SIZE; threshold = FLIP_THRESHOLD.
      Default: 7 frames, 6-of-7 majority ≈ 85 % — very conservative.
    """

    def __init__(self) -> None:
        self._window: collections.deque[FrameResult] = collections.deque(
            maxlen=config.WINDOW_SIZE
        )
        self._reported_state: str = "unknown"

    @property
    def reported_state(self) -> str:
        return self._reported_state

    def update(self, result: FrameResult) -> tuple[str, dict]:
        """
        Feed a FrameResult.  Returns (reported_state, debug_dict).
        """
        skipped = (
            result.state == "uncertain"
            or result.quality < config.MIN_FRAME_QUALITY
        )
        if skipped:
            logger.debug(
                "Frame discarded (state=%s quality=%.2f)", result.state, result.quality
            )
            return self._reported_state, self._debug_dict(result, skipped=True)

        self._window.append(result)

        votes: dict[str, int] = {"open": 0, "closed": 0}
        for r in self._window:
            if r.state in votes:
                votes[r.state] += 1

        dominant = max(votes, key=lambda k: votes[k])
        if votes[dominant] >= config.FLIP_THRESHOLD and dominant != self._reported_state:
            prev = self._reported_state
            self._reported_state = dominant
            logger.info(
                "STATE FLIP: %s → %s  (open=%d closed=%d window=%d/%d)",
                prev, dominant,
                votes["open"], votes["closed"],
                len(self._window), config.WINDOW_SIZE,
            )

        return self._reported_state, self._debug_dict(result, skipped=False, votes=votes)

    def window_snapshot(self) -> list[FrameResult]:
        return list(self._window)

    def _debug_dict(
        self,
        result: FrameResult,
        *,
        skipped: bool,
        votes: dict | None = None,
    ) -> dict:
        return {
            "frame_state": result.state,
            "frame_confidence": result.confidence,
            "frame_quality": result.quality,
            "frame_ir": result.is_ir,
            "frame_drift": list(result.anchor_offset) if result.anchor_offset else None,
            "skipped": skipped,
            "window_size": len(self._window),
            "votes": votes or {},
            "reported_state": self._reported_state,
        }

