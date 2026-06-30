"""
detector/marker.py — retroreflective-marker detection in image zones.

All public functions accept BGR numpy arrays (as returned by OpenCV).
The markers are retroreflective red/white strips that appear as the
brightest objects in the frame regardless of lighting or IR mode.

Public API
----------
find_blobs_in_zone(frame, zone, margin)  →  ZoneResult
is_ir_mode(frame)                         →  bool
find_anchor_offset(frame)                 →  (dx, dy) | None
annotate_frame(frame, ...)                →  annotated BGR ndarray
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Blob:
    """A single detected bright blob."""
    cx: int    # centroid x in full-frame coordinates
    cy: int    # centroid y in full-frame coordinates
    area: int  # blob pixel area
    bbox: tuple[int, int, int, int]  # (x, y, w, h) in full-frame coordinates


@dataclass
class ZoneResult:
    """Detection result for one zone."""
    has_marker: bool          # True if bright marker found
    confidence: float         # 0–1 (fraction of ZONE_BRIGHT_MIN_PX filled)
    blobs: list[Blob]         # individual blobs found
    bright_px: int            # total bright pixels in searched area
    zone_rect: tuple[int, int, int, int]  # actual rectangle searched
    max_pixel: int = 0        # brightest raw pixel value in zone (pre-threshold)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_ir_mode(frame: np.ndarray) -> bool:
    """
    Heuristic: if the frame is essentially grayscale (B ≈ G ≈ R across the
    image) it is almost certainly in IR night mode.
    """
    if frame.ndim == 2:
        return True
    b, g, r = cv2.split(frame.astype(np.int16))
    mean_diff = float(np.mean(np.abs(r - g)) + np.mean(np.abs(r - b)))
    return mean_diff < 8.0


def _expand_zone(
    zone: tuple[int, int, int, int],
    margin: int,
    frame_h: int,
    frame_w: int,
) -> tuple[int, int, int, int]:
    """Return zone expanded by margin, clamped to frame bounds."""
    x, y, w, h = zone
    x1 = max(0, x - margin)
    y1 = max(0, y - margin)
    x2 = min(frame_w, x + w + margin)
    y2 = min(frame_h, y + h + margin)
    return x1, y1, x2 - x1, y2 - y1


def _to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def find_blobs_in_zone(
    frame: np.ndarray,
    zone: tuple[int, int, int, int],
    margin: int = 0,
    bright_threshold: int | None = None,
) -> ZoneResult:
    """
    Locate bright (retroreflective) blobs inside a zone of the frame.

    Parameters
    ----------
    frame            Full BGR (or grayscale) camera frame.
    zone             (x, y, w, h) nominal target zone.
    margin           Extra pixels around the zone for camera-shake tolerance.
    bright_threshold Override for config.BRIGHT_THRESHOLD.

    Returns
    -------
    ZoneResult with individual blobs and aggregate confidence.
    """
    thresh = bright_threshold if bright_threshold is not None else config.BRIGHT_THRESHOLD
    fh, fw = frame.shape[:2]
    sx, sy, sw, sh = _expand_zone(zone, margin, fh, fw)

    gray = _to_gray(frame)
    roi = gray[sy : sy + sh, sx : sx + sw]

    # Hard threshold → binary mask of bright pixels.
    _, binary = cv2.threshold(roi, thresh, 255, cv2.THRESH_BINARY)

    # Small morphological open to kill single-pixel hot-spots.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    n_labels, _labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8
    )

    blobs: list[Blob] = []
    total_bright = int(np.count_nonzero(binary))

    for i in range(1, n_labels):  # label 0 = background
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < config.MIN_BLOB_AREA or area > config.MAX_BLOB_AREA:
            continue

        bx = int(stats[i, cv2.CC_STAT_LEFT])
        by = int(stats[i, cv2.CC_STAT_TOP])
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])

        bbox_area = bw * bh
        if bbox_area > 0 and (area / bbox_area) < config.MIN_BLOB_SOLIDITY:
            continue  # too sparse — likely noise

        cx = int(round(centroids[i][0])) + sx
        cy = int(round(centroids[i][1])) + sy
        blobs.append(Blob(cx=cx, cy=cy, area=area, bbox=(bx + sx, by + sy, bw, bh)))

    has_marker = total_bright >= config.ZONE_BRIGHT_MIN_PX and len(blobs) > 0
    confidence = min(1.0, total_bright / max(1, config.ZONE_BRIGHT_MIN_PX))

    logger.debug(
        "zone(%d,%d,%d,%d)+%dpx → bright=%d blobs=%d has=%s conf=%.2f",
        *zone, margin, total_bright, len(blobs), has_marker, confidence,
    )
    return ZoneResult(
        has_marker=has_marker,
        confidence=confidence,
        blobs=blobs,
        bright_px=total_bright,
        zone_rect=(sx, sy, sw, sh),
        max_pixel=int(roi.max()),
    )


def find_anchor_offset(frame: np.ndarray) -> tuple[int, int] | None:
    """
    Locate the fixed reference marker and return (dx, dy) camera drift.
    Returns (0, 0) if ANCHOR_ZONE is None (disabled).
    Returns None if the anchor was not found (too large a shift).
    """
    if config.ANCHOR_ZONE is None:
        return (0, 0)

    result = find_blobs_in_zone(
        frame, config.ANCHOR_ZONE, margin=config.SEARCH_MARGIN
    )
    if not result.has_marker or not result.blobs:
        logger.warning("Anchor marker not found in frame")
        return None

    ex, ey = config.ANCHOR_EXPECTED
    # Use the blob closest to the expected position.
    best = min(result.blobs, key=lambda b: (b.cx - ex) ** 2 + (b.cy - ey) ** 2)
    dx, dy = best.cx - ex, best.cy - ey
    logger.debug("Anchor at (%d,%d) drift=(%+d,%+d)", best.cx, best.cy, dx, dy)
    return (dx, dy)


# ---------------------------------------------------------------------------
# Debug annotation
# ---------------------------------------------------------------------------

def annotate_frame(
    frame: np.ndarray,
    *,
    closed_result: ZoneResult,
    open_result: ZoneResult,
    anchor_offset: tuple[int, int] | None,
    state: str,
    confidence: float,
    is_ir: bool,
) -> np.ndarray:
    """
    Return an annotated copy of the frame.

    Green  = CLOSED zone + blobs
    Orange = OPEN zone + blobs
    Yellow = ANCHOR zone
    Text overlay shows state, scores, and drift.
    """
    out = frame.copy()
    if out.ndim == 2:
        out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)

    COLOR_CLOSED = (0, 220, 0)      # green
    COLOR_OPEN   = (0, 140, 255)    # orange
    COLOR_ANCHOR = (0, 220, 220)    # yellow
    COLOR_TEXT_BG = (0, 0, 0)
    COLOR_TEXT_FG = (255, 255, 255)

    def _rect(rect, color, label):
        x, y, w, h = rect
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        cv2.putText(out, label, (x + 2, max(12, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    def _blobs(blobs, color):
        for b in blobs:
            bx, by, bw, bh = b.bbox
            cv2.rectangle(out, (bx, by), (bx + bw, by + bh), color, 1)
            cv2.drawMarker(out, (b.cx, b.cy), color,
                           cv2.MARKER_CROSS, 14, 1, cv2.LINE_AA)
            cv2.putText(out, f"{b.area}px", (bx, max(10, by - 2)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)

    _rect(closed_result.zone_rect, COLOR_CLOSED,
          f"CLOSED bright={closed_result.bright_px}")
    _rect(open_result.zone_rect,   COLOR_OPEN,
          f"OPEN bright={open_result.bright_px}")
    if config.ANCHOR_ZONE:
        _rect(config.ANCHOR_ZONE, COLOR_ANCHOR, "ANCHOR")

    _blobs(closed_result.blobs, COLOR_CLOSED)
    _blobs(open_result.blobs,   COLOR_OPEN)

    if anchor_offset:
        drift_s = f"drift=({anchor_offset[0]:+d},{anchor_offset[1]:+d})"
    else:
        drift_s = "drift=LOST"

    state_color = (0, 220, 0) if state == "closed" \
        else (0, 140, 255) if state == "open" \
        else (0, 180, 220)

    overlay_lines = [
        (f"STATE: {state.upper()}  conf={confidence:.2f}",  state_color),
        (f"mode={'IR' if is_ir else 'DAY'}  {drift_s}",     COLOR_TEXT_FG),
        (f"closed_bright={closed_result.bright_px}  "
         f"open_bright={open_result.bright_px}",            COLOR_TEXT_FG),
    ]
    for i, (line, color) in enumerate(overlay_lines):
        y_pos = 22 + i * 22
        cv2.putText(out, line, (8, y_pos),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_TEXT_BG, 3, cv2.LINE_AA)
        cv2.putText(out, line, (8, y_pos),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)

    return out
