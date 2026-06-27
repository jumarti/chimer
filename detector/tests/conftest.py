"""
tests/conftest.py — shared pytest fixtures and helpers.

Image resolution strategy
--------------------------
Tests look for images in two places, in priority order:
  1. tests/fixtures/images/  — committed subset (for CI; populated by
                                tests/fixtures/populate_fixtures.py)
  2. .data/samples/          — local-only full sample set (immediately
                                available in development without extra steps)

If neither location has an image, the test is skipped.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DETECTOR_DIR  = Path(__file__).parent.parent          # detector/
FIXTURES_IMG  = DETECTOR_DIR / "tests" / "fixtures" / "images"
SAMPLES_DIR   = DETECTOR_DIR / ".data" / "samples"
LABELS_FILE   = DETECTOR_DIR / "tests" / "fixtures" / "labels.json"


def _find_image(filename: str) -> Path | None:
    """Return the path to an image file from either fixture location."""
    for base in (FIXTURES_IMG, SAMPLES_DIR):
        p = base / filename
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def labels() -> dict[str, dict]:
    """All entries from labels.json keyed by filename."""
    with open(LABELS_FILE) as f:
        return json.load(f)


@pytest.fixture(scope="session")
def labeled_images(labels) -> list[tuple[str, str, np.ndarray]]:
    """
    List of (filename, expected_state, bgr_frame) for every labeled image
    that can be found on disk.  Images not found are silently skipped.
    """
    result = []
    for filename, meta in labels.items():
        path = _find_image(filename)
        if path is None:
            continue
        frame = cv2.imread(str(path))
        if frame is not None:
            result.append((filename, meta["state"], frame))
    return result


@pytest.fixture(scope="session")
def closed_frames(labeled_images) -> list[np.ndarray]:
    return [f for _, s, f in labeled_images if s == "closed"]


@pytest.fixture(scope="session")
def open_frames(labeled_images) -> list[np.ndarray]:
    return [f for _, s, f in labeled_images if s == "open"]


# ---------------------------------------------------------------------------
# Synthetic frame helpers (used by test_marker.py and test_sequence.py)
# ---------------------------------------------------------------------------

from tests.helpers import (
    make_dark_frame,
    make_frame_with_blob,
    make_ir_frame,
    make_color_frame,
)

__all__ = ["make_dark_frame", "make_frame_with_blob", "make_ir_frame", "make_color_frame"]

