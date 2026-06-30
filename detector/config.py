"""
detector/config.py — all tuneable parameters in one place.

Sensitive values (RTSP_URL) come from .env (gitignored).
Copy .env.example → .env and fill in credentials before running.

Runtime-operational settings can be overridden via environment variables so
that Docker deployments do not need to modify code or rebuild the image.
Detection-geometry constants (ZONE_*, thresholds, calibration) remain in code.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw is not None else default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    return float(raw) if raw is not None else default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


# ---------------------------------------------------------------------------
# Camera / capture
# ---------------------------------------------------------------------------
RTSP_URL: str = _env_str("RTSP_URL", "rtsp://CHANGE_ME/stream")

# Seconds between image polls.  4 s × 5-frame window ≈ 20 s of context.
POLL_INTERVAL_S: float = _env_float("POLL_INTERVAL_S", 4.0)

# Capture backend.  Both are tried; primary goes first, fallback on failure.
# "opencv" uses cv2.VideoCapture; "ffmpeg" spawns a subprocess.
CAPTURE_METHOD: str = _env_str("CAPTURE_METHOD", "opencv")  # "opencv" | "ffmpeg"
FFMPEG_TIMEOUT_S: int = _env_int("FFMPEG_TIMEOUT_S", 12)

# cv2.VideoCapture connection and read timeouts (milliseconds).
# Prevents grab()/retrieve() from blocking indefinitely on a stale TCP connection.
CAMERA_OPEN_TIMEOUT_MS: int = _env_int("CAMERA_OPEN_TIMEOUT_MS", 5_000)
CAMERA_READ_TIMEOUT_MS: int = _env_int("CAMERA_READ_TIMEOUT_MS", 5_000)

# ---------------------------------------------------------------------------
# Detection zones  (x, y, width, height)  — native camera-frame pixels.
#
# ZONE_CLOSED  where both markers overlap when the gate is latched shut.
#   Derived from user-supplied corners (387,455)·(594,458)·(582,708)·(387,716).
# ZONE_OPEN    where the gate-bar markers travel when the gate swings open.
#   Determined from open-state sample analysis (0097-0100).
#
# ZONE_LATCH   *tight* zone around the exact pixel footprint of the retroreflective
#   marker overlap when the gate is fully latched.  Must be small enough that
#   bright concrete/ground is NOT included — typically 80-150 px wide/tall.
#   When set (non-None), the classifier becomes ZONE_LATCH-primary:
#     marker present in ZONE_LATCH  →  CLOSED  (markers aligned)
#     marker absent  from ZONE_LATCH →  OPEN   (any movement, even a few cm)
#   When None, falls back to ZONE_OPEN-primary logic (current safe default).
#
#   Calibrate by running:  uv run python calibrate.py --show
#   In the annotated closed-gate frame, find the bounding box of ONLY the
#   retroreflective blobs (green rectangles) inside ZONE_CLOSED and use
#   those coordinates here (add ~20 px margin on each side).
#
# SEARCH_MARGIN  adds shake tolerance: each zone is expanded by this many
#   pixels in every direction before the blob search runs.
# ---------------------------------------------------------------------------
ZONE_CLOSED: tuple[int, int, int, int] = (387, 455, 207, 261)  # (x, y, w, h)
ZONE_OPEN:   tuple[int, int, int, int] = (160,  85, 590, 140)  # (x, y, w, h)
ZONE_LATCH:  tuple[int, int, int, int] | None = (448, 499, 70, 73)  # upper marker overlap; x:448-518 y:499-572
SEARCH_MARGIN: int = 30  # px — camera-shake tolerance

# Latch-zone detection overrides (tighter than the general blob detector).
# LATCH_MIN_BLOB_AREA  rejects small concrete scatter (~50-110 px) while
#   accepting real marker blobs (~450-1200 px).  Derived from sample analysis.
# BRIGHT_THRESHOLD_IR_LATCH  retroreflective marker appears dimmer in the small
#   latch zone; lower threshold picks up the blob that BRIGHT_THRESHOLD_IR misses.
LATCH_MIN_BLOB_AREA:       int = 200  # px; concrete scatter is 50-110px, markers 450-1200px
BRIGHT_THRESHOLD_IR_LATCH:     int = 150  # IR latch threshold; nighttime markers can be 140-179 bright
BRIGHT_THRESHOLD_IR_LATCH_DIM: int = 120  # dim fallback; catches very faint IR markers still at latch position

# ---------------------------------------------------------------------------
# Small-blade open detection (feature flag)
# ---------------------------------------------------------------------------
# When DETECT_SMALL_BLADE_OPEN is True AND both ZONE_SMALL_REST / ZONE_SMALL_OPEN
# are non-None, GateClassifier adds a second open-detection branch:
#
#   big blade latched (latch_hit)
#   AND small marker ABSENT from ZONE_SMALL_REST
#   AND small marker PRESENT in ZONE_SMALL_OPEN
#   → state = "open", open_reason = "small_blade"
#
# With the flag False (default) behaviour is byte-for-byte identical to before.
#
# Calibrate both zones using:  uv run python calibrate.py --show
#   ZONE_SMALL_REST — bounding box of the small marker at rest on a closed frame
#                     (e.g. samples 0070 / 0085).
#   ZONE_SMALL_OPEN — zone the small marker travels into when the small blade
#                     opens (derive from samples 0116-0132).
DETECT_SMALL_BLADE_OPEN: bool = True

# ZONE_SMALL_REST  tight zone around the small marker at rest (both blades closed).
#   Derived from blob analysis of closed frames (0070, 0085):
#   marker centroid ≈ (499, 609), bbox ≈ (480, 576, 40, 62).
#   x ends at 510 so the marker exits cleanly once the blade starts moving right;
#   y starts at 575 to exclude a persistent structural blob at cy≈564.
ZONE_SMALL_REST: tuple[int, int, int, int] | None = (460, 575, 50, 75)  # x:460-510  y:575-650

# ZONE_SMALL_OPEN  zone the marker travels into when the small blade opens.
#   Marker swings right; confirmed positions across IR + DAY frames (0116-0124, 0130-0132):
#   cx range 530-666, cy range 600-620.  Upper-left chosen to avoid the cx≈563, cy≈522
#   static reflection that sits just above this zone in some closed frames.
ZONE_SMALL_OPEN: tuple[int, int, int, int] | None = (515, 565, 215, 100)  # x:515-730  y:565-665

# Minimum blob area to accept as the small marker (mirrors latch logic).
# Reuses LATCH_MIN_BLOB_AREA by default; override if the small marker is
# significantly different in size.
SMALL_BLADE_MIN_BLOB_AREA: int = LATCH_MIN_BLOB_AREA

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
MAX_BLOB_AREA: int = 3_000  # real markers 50-1600 px; headlights/sun 7000+ px

# Fraction of bounding-box pixels that must be bright (solidity filter).
MIN_BLOB_SOLIDITY: float = 0.30

# Total bright pixels inside a zone that must be met to call it "marker present".
# Keep this low — IR frames produce fewer bright pixels per marker.
ZONE_BRIGHT_MIN_PX: int = 50

# ---------------------------------------------------------------------------
# Temporal aggregator
# ---------------------------------------------------------------------------
# Sliding window length (number of qualifying frames kept).
WINDOW_SIZE: int = 5

# Qualifying frames that must agree before the reported state flips.
# 4-of-5 = 80 % majority — conservative but ~2.5× faster than the old 6-of-7.
# Worst-case detection latency: FLIP_THRESHOLD × POLL_INTERVAL_S = 4 × 4 = 16 s.
FLIP_THRESHOLD: int = 4

# Lower flip threshold used when ALL qualifying open frames in the window
# carry open_reason == "small_blade".  The small gate opens and closes fast
# (a few seconds) so the standard 4-frame requirement is too slow to catch it.
# 2 consecutive small-blade frames = 8 s at 4 s poll interval.
FLIP_THRESHOLD_SMALL_BLADE: int = 2

# Frames whose quality is below this threshold are silently discarded.
MIN_FRAME_QUALITY: float = 0.25

# ---------------------------------------------------------------------------
# Debug / logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str        = _env_str("LOG_LEVEL", "INFO")  # DEBUG | INFO | WARNING | ERROR | CRITICAL
DEBUG_DIR: str        = _env_str("DEBUG_DIR", ".data/debug")
DEBUG_ON_CHANGE: bool = _env_bool("DEBUG_ON_CHANGE", True)
DEBUG_RING_SIZE: int  = _env_int("DEBUG_RING_SIZE", 3)   # extra frames before a state change also saved

CAPTURES_DIR: str  = _env_str("CAPTURES_DIR", ".data/captures")
SAVE_CAPTURES: bool = _env_bool("SAVE_CAPTURES", False)  # set True to save every polled frame

# Uncertain-frame sampling: save 1 annotated frame every UNCERTAIN_SAMPLE_RATE
# consecutive uncertain frames, for post-hoc investigation of dusk/blind cases.
# Set SAVE_UNCERTAIN_SAMPLES=false to disable entirely.
UNCERTAIN_SAMPLE_DIR: str  = _env_str("UNCERTAIN_SAMPLE_DIR", ".data/uncertain")
SAVE_UNCERTAIN_SAMPLES: bool = _env_bool("SAVE_UNCERTAIN_SAMPLES", True)
UNCERTAIN_SAMPLE_RATE: int  = _env_int("UNCERTAIN_SAMPLE_RATE", 30)  # 1-in-N frames saved

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
HOST: str = _env_str("HOST", "0.0.0.0")
PORT: int = _env_int("PORT", 8080)

# How often to log the /gate request count (seconds).  0 = disable.
GATE_LOG_INTERVAL_S: float = _env_float("GATE_LOG_INTERVAL_S", 300.0)  # 5 min
