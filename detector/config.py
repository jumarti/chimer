"""
detector/config.py — all tuneable parameters in one place.

Edit this file before running app.py.
"""

# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
# RTSP stream URL. Examples:
#   rtsp://admin:password@192.168.1.100:554/stream1
#   rtsp://192.168.1.100/live
RTSP_URL: str = "rtsp://CHANGE_ME/stream"

# How often (seconds) to grab a new frame from the stream and analyse it.
CAMERA_POLL_INTERVAL: float = 2.0

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
# Region of Interest inside the frame: (x, y, width, height) in pixels.
# Set to None to use the whole frame.
# TODO: tune after pointing camera at the gate.
ROI: tuple | None = None  # e.g. (100, 80, 320, 200)

# Fraction of ROI pixels that must differ from the baseline frame to call
# the gate OPEN (0.0 – 1.0).
# TODO: calibrate with gate fully open and fully closed.
CHANGE_THRESHOLD: float = 0.05  # 5 %

# Absolute pixel-intensity difference to count a pixel as "changed".
PIXEL_DIFF_THRESHOLD: int = 30  # out of 255

# How many consecutive OPEN detections before reporting "open"
# (debounce, avoids single-frame false positives).
OPEN_DEBOUNCE: int = 2

# How many consecutive CLOSED detections before reporting "closed".
CLOSE_DEBOUNCE: int = 3

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
HOST: str = "0.0.0.0"
PORT: int = 8080
