"""
tests/test_marker.py — L2: marker detection on synthetic images.

All images are generated programmatically — no files needed.
Exercises bright-blob detection, zone expansion, size filtering,
IR-mode heuristic, and annotation.
"""
from __future__ import annotations

import numpy as np
import pytest

import config
from tests.helpers import (
    make_color_frame,
    make_dark_frame,
    make_frame_with_blob,
    make_ir_frame,
)
from marker import (
    ZoneResult,
    annotate_frame,
    find_blobs_in_zone,
    is_ir_mode,
)

# Use a simple test zone well inside a 720×1280 frame.
ZONE = (400, 450, 200, 150)   # (x, y, w, h)
BLOB_X, BLOB_Y = 430, 480    # inside the zone
BLOB_W, BLOB_H = 60, 40      # area = 2400 px (above MIN_BLOB_AREA)
SEARCH_MARGIN = config.SEARCH_MARGIN


# ---------------------------------------------------------------------------
# Blob detection
# ---------------------------------------------------------------------------

class TestFindBlobsInZone:
    def test_bright_blob_found(self):
        frame = make_frame_with_blob(
            blob_x=BLOB_X, blob_y=BLOB_Y, blob_w=BLOB_W, blob_h=BLOB_H,
            brightness=250,
        )
        result = find_blobs_in_zone(frame, ZONE)
        assert result.has_marker, "Expected marker to be found"
        assert result.confidence > 0
        assert len(result.blobs) >= 1

    def test_dark_zone_no_marker(self):
        frame = make_dark_frame()
        result = find_blobs_in_zone(frame, ZONE)
        assert not result.has_marker
        assert result.bright_px == 0
        assert result.blobs == []

    def test_blob_outside_zone_not_found(self):
        """Blob placed far outside the zone (no margin) should not be detected."""
        frame = make_frame_with_blob(
            blob_x=0, blob_y=0, blob_w=60, blob_h=40, brightness=250
        )
        result = find_blobs_in_zone(frame, ZONE, margin=0)
        assert not result.has_marker

    def test_small_blob_filtered_out(self):
        """Blobs below MIN_BLOB_AREA should not register as a marker."""
        tiny_area = max(1, config.MIN_BLOB_AREA - 10)
        # Approximate a square root for size:
        side = max(1, int(tiny_area ** 0.5))
        frame = make_frame_with_blob(
            blob_x=BLOB_X, blob_y=BLOB_Y,
            blob_w=side, blob_h=side,
            brightness=250,
        )
        result = find_blobs_in_zone(frame, ZONE)
        assert not result.has_marker or result.bright_px < config.ZONE_BRIGHT_MIN_PX

    def test_confidence_scales_with_brightness(self):
        """More bright pixels → higher confidence, up to 1.0."""
        small = make_frame_with_blob(
            blob_x=BLOB_X, blob_y=BLOB_Y, blob_w=10, blob_h=10, brightness=240
        )
        large = make_frame_with_blob(
            blob_x=BLOB_X, blob_y=BLOB_Y, blob_w=80, blob_h=60, brightness=240
        )
        r_small = find_blobs_in_zone(small, ZONE)
        r_large = find_blobs_in_zone(large, ZONE)
        assert r_large.confidence >= r_small.confidence


class TestSearchMargin:
    def test_margin_finds_blob_just_outside_zone(self):
        """Blob placed just outside the zone should be found when margin is enough."""
        outside_x = ZONE[0] - 20   # 20 px to the left of zone edge
        frame = make_frame_with_blob(
            blob_x=outside_x, blob_y=BLOB_Y, blob_w=BLOB_W, blob_h=BLOB_H,
            brightness=250,
        )
        result = find_blobs_in_zone(frame, ZONE, margin=30)  # 30 > 20
        assert result.has_marker

    def test_blob_too_far_outside_not_found(self):
        """Blob placed well beyond the search margin should not be detected."""
        # Expanded zone x-range: [ZONE[0]-margin, ZONE[0]+ZONE[2]+margin] = [370, 660]
        # Place blob completely before 370: far_x + BLOB_W < 370 → far_x = 240
        far_x = ZONE[0] - SEARCH_MARGIN - BLOB_W - 20  # completely outside expanded zone
        frame = make_frame_with_blob(
            blob_x=far_x, blob_y=BLOB_Y, blob_w=BLOB_W, blob_h=BLOB_H,
            brightness=250,
        )
        result = find_blobs_in_zone(frame, ZONE, margin=SEARCH_MARGIN)
        assert not result.has_marker

    def test_zero_margin_strict(self):
        """With zero margin, a blob exactly on the zone boundary should be found."""
        edge_x = ZONE[0]
        frame = make_frame_with_blob(
            blob_x=edge_x, blob_y=BLOB_Y, blob_w=BLOB_W, blob_h=BLOB_H,
            brightness=250,
        )
        result = find_blobs_in_zone(frame, ZONE, margin=0)
        assert result.has_marker


# ---------------------------------------------------------------------------
# IR / Day mode detection
# ---------------------------------------------------------------------------

class TestIrModeDetection:
    def test_ir_frame_detected(self):
        assert is_ir_mode(make_ir_frame()) is True

    def test_grayscale_2d_array_is_ir(self):
        gray = np.zeros((480, 640), dtype=np.uint8)
        assert is_ir_mode(gray) is True

    def test_color_frame_not_ir(self):
        assert is_ir_mode(make_color_frame()) is False

    def test_neutral_gray_bgr_is_ir(self):
        """A perfectly neutral grey image (all channels equal) is IR."""
        frame = np.full((480, 640, 3), 128, dtype=np.uint8)
        assert is_ir_mode(frame) is True


# ---------------------------------------------------------------------------
# Annotation sanity check
# ---------------------------------------------------------------------------

class TestAnnotation:
    def test_annotate_returns_bgr_same_size(self):
        frame = make_frame_with_blob(
            blob_x=BLOB_X, blob_y=BLOB_Y, blob_w=BLOB_W, blob_h=BLOB_H,
            brightness=250,
        )
        closed_res = find_blobs_in_zone(frame, ZONE)
        open_res   = find_blobs_in_zone(frame, (100, 80, 300, 100))
        out = annotate_frame(
            frame,
            closed_result=closed_res,
            open_result=open_res,
            anchor_offset=(0, 0),
            state="closed",
            confidence=0.8,
            is_ir=False,
        )
        assert out.shape == frame.shape
        assert out.dtype == np.uint8

    def test_annotate_on_ir_frame(self):
        frame = make_ir_frame()
        closed_res = find_blobs_in_zone(frame, ZONE)
        open_res   = find_blobs_in_zone(frame, (100, 80, 300, 100))
        out = annotate_frame(
            frame,
            closed_result=closed_res,
            open_result=open_res,
            anchor_offset=None,
            state="uncertain",
            confidence=0.0,
            is_ir=True,
        )
        assert out.shape[2] == 3  # always BGR output
