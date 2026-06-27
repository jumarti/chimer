"""
detector/detector.py — CV gate-open/closed detection.

Strategy: frame differencing against a rolling baseline.
- Extract the configured ROI from each new frame.
- Compare pixel-by-pixel with the baseline.
- If the fraction of changed pixels exceeds CHANGE_THRESHOLD the gate is
  considered OPEN; otherwise CLOSED.
- A simple debounce counter prevents single-frame noise from flipping state.

TODO: tune ROI, CHANGE_THRESHOLD, and PIXEL_DIFF_THRESHOLD in config.py
      once the camera is aimed at the gate.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

import config

logger = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    state: str          # "open" | "closed" | "unknown"
    change_ratio: float # fraction of ROI pixels that changed (0.0–1.0)
    timestamp: float = field(default_factory=time.time)


class GateDetector:
    """Stateful gate detector with debounce."""

    def __init__(self) -> None:
        self._baseline: Optional[np.ndarray] = None
        self._last_state: str = "unknown"
        # Debounce counters
        self._open_streak: int = 0
        self._close_streak: int = 0

    def update(self, frame: np.ndarray) -> DetectionResult:
        """
        Feed a new frame and return the current gate state.

        The first call establishes the baseline; subsequent calls compare
        against it.  Call `reset_baseline()` if lighting conditions change
        significantly.
        """
        roi = self._extract_roi(frame)

        if self._baseline is None:
            self._baseline = roi.copy()
            logger.info("Baseline frame captured (%dx%d)", roi.shape[1], roi.shape[0])
            return DetectionResult(state="unknown", change_ratio=0.0)

        change_ratio = self._compute_change(roi)
        raw_open = change_ratio >= config.CHANGE_THRESHOLD

        # Debounce ---------------------------------------------------
        if raw_open:
            self._open_streak += 1
            self._close_streak = 0
        else:
            self._close_streak += 1
            self._open_streak = 0

        if self._open_streak >= config.OPEN_DEBOUNCE:
            self._last_state = "open"
        elif self._close_streak >= config.CLOSE_DEBOUNCE:
            self._last_state = "closed"
        # else: keep previous state (still building streak)

        logger.debug(
            "change=%.3f threshold=%.3f raw=%s state=%s (open_streak=%d close_streak=%d)",
            change_ratio, config.CHANGE_THRESHOLD, raw_open,
            self._last_state, self._open_streak, self._close_streak,
        )

        return DetectionResult(state=self._last_state, change_ratio=change_ratio)

    def reset_baseline(self) -> None:
        """Force re-capture of the baseline on the next frame."""
        self._baseline = None
        self._open_streak = 0
        self._close_streak = 0
        logger.info("Baseline reset")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_roi(self, frame: np.ndarray) -> np.ndarray:
        if config.ROI is None:
            return frame
        x, y, w, h = config.ROI
        return frame[y : y + h, x : x + w]

    def _compute_change(self, roi: np.ndarray) -> float:
        """Return fraction of pixels whose intensity changed beyond threshold."""
        # Convert to grayscale for a single-channel diff.
        gray_current  = cv2.cvtColor(roi,            cv2.COLOR_BGR2GRAY)
        gray_baseline = cv2.cvtColor(self._baseline, cv2.COLOR_BGR2GRAY)

        diff = cv2.absdiff(gray_current, gray_baseline)
        changed_pixels = int(np.sum(diff > config.PIXEL_DIFF_THRESHOLD))
        total_pixels   = diff.size
        return changed_pixels / total_pixels if total_pixels > 0 else 0.0
