"""tests/helpers.py — synthetic frame factories for tests."""
from __future__ import annotations
import numpy as np
import cv2


def make_dark_frame(h: int = 720, w: int = 1280) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def make_frame_with_blob(
    h: int = 720,
    w: int = 1280,
    blob_x: int = 400,
    blob_y: int = 500,
    blob_w: int = 60,
    blob_h: int = 40,
    brightness: int = 240,
) -> np.ndarray:
    frame = make_dark_frame(h, w)
    frame[blob_y : blob_y + blob_h, blob_x : blob_x + blob_w] = brightness
    return frame


def make_ir_frame(h: int = 720, w: int = 1280) -> np.ndarray:
    gray = np.random.randint(30, 80, (h, w), dtype=np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def make_color_frame(h: int = 720, w: int = 1280) -> np.ndarray:
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :, 2] = 80    # red
    frame[:, :, 1] = 140   # green
    frame[:, :, 0] = 30    # blue
    return frame
