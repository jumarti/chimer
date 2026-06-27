"""
detector/config.py — all tuneable parameters in one place.

Sensitive values (RTSP_URL) come from .env (gitignored).
Copy .env.example → .env and fill in credentials before running.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Camera / capture
# ---------------------------------------------------------------------------
RTSP_URL: str = os.environ.get("RTSP_URL", "rtsp://CHANGE_ME/stream")

# Seconds between image polls.  7 s × 7-frame window ≈ 49 s of context.
POLL_INTERVAL_S: float = 7.0

# Capture backend.  Both are tried; primary goes first, fallback on failure.
# "opencv" uses cv2.VideoCapture; "ffmpeg" spawns a subprocess.
CAPTURE_METHOD: str = "opencv"  # "opencv" | "ffmpeg"
FFMPEG_TIMEOUT_S: int = 12

# ---------------------------------------------------------------------------
# Detection zones  (x, y, width, height)  — native camera-frame pixels.
#
# ZONE_CLOSED  where both markers overlap when the gate is latched shut.
#   Derived from user-supplied corners (387,455)·(594,458)·(582,708)·(387,716).
# ZONE_OPEN    where the gate-bar markers travel when the gate swings open.
#   Determined from open-state sample analysis (0097-0100).
#
# SEARCH_MARGIN  adds shake tolerance: each zone is expanded by this many
#   pixels in every direction before the blob search runs.
# ---------------------------------------------------------------------------
ZONE_CLOSED: tuple[int, int, int, int] = (387, 455, 207, 261)  # (x, y, w, h)
ZONE_OPEN:   tuple[int, int, int, int] = (160,  85, 590, 140)  # (x, y, w, h)
SEARCH_MARGIN: int = 30  # px — camera-shake tolerance

# Anchor: a fixed reference marker used to measure camera drift and shift
# both zones to compensate.  Set to None to disable (simpler, less robust).
# ANCHOR_ZONE  bounding box of the always-visible fixed post marker.
# ANCHOR_EXPECTED  its nominal centroid in a steady frame.
# ANCHOR_MAX_DRIFT  max tolerated drift before declaring UNCERTAIN.
ANCHOR_ZONE: tuple[int, int, int, int] | None = None  # disabled in v1
ANCHOR_EXPECTED: tuple[int, int] = (470, 690)
ANCHOR_MAX_DRIFT: int = 40  # px

# ---------------------------------------------------------------------------
# Marker detection thresholds
# ---------------------------------------------------------------------------
# Grayscale brightness above which a pixel is "bright" (retroreflective).
# Use BRIGHT_THRESHOLD_IR for IR/night frames (markers ~255, background 30–80).
# Use BRIGHT_THRESHOLD_DAY for colour/daylight (markers ~235–255, concrete ~200–230).
BRIGHT_THRESHOLD:     int = 215  # daylight default
BRIGHT_THRESHOLD_IR:  int = 200  # IR / night mode

# Connected-component blob size limits (pixels).
MIN_BLOB_AREA: int = 40
MAX_BLOB_AREA: int = 12_000

# Fraction of bounding-box pixels that must be bright (solidity filter).
MIN_BLOB_SOLIDITY: float = 0.30

# Total bright pixels inside a zone that must be met to call it "marker present".
# Keep this low — IR frames produce fewer bright pixels per marker.
ZONE_BRIGHT_MIN_PX: int = 50

# ---------------------------------------------------------------------------
# Temporal aggregator
# ---------------------------------------------------------------------------
# Sliding window length (number of qualifying frames kept).
WINDOW_SIZE: int = 7

# Qualifying frames that must agree before the reported state flips.
# 6-of-7 = 85 % majority — heavily biased against false detections.
FLIP_THRESHOLD: int = 6

# Frames whose quality is below this threshold are silently discarded.
MIN_FRAME_QUALITY: float = 0.25

# ---------------------------------------------------------------------------
# Debug / logging
# ---------------------------------------------------------------------------
DEBUG_DIR: str = ".data/debug"
DEBUG_ON_CHANGE: bool = True
DEBUG_RING_SIZE: int = 3   # extra frames before a state change also saved

CAPTURES_DIR: str = ".data/captures"
SAVE_CAPTURES: bool = False  # set True to save every polled frame

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
HOST: str = "0.0.0.0"
PORT: int = 8080
