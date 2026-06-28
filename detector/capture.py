"""
detector/capture.py — single-frame RTSP image capture.

Two backends (both try on failure):
  opencv  cv2.VideoCapture  — low overhead, pure Python
  ffmpeg  subprocess call   — more reliable for some RTSP dialects / HEVC

Usage
-----
    from capture import grab_frame
    frame = grab_frame()   # returns BGR ndarray or None
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

import config

logger = logging.getLogger(__name__)

CaptureMethod = Literal["opencv", "ffmpeg"]


def grab_frame_opencv(url: str | None = None) -> np.ndarray | None:
    """Grab the latest frame via cv2.VideoCapture."""
    url = url or config.RTSP_URL
    cap = cv2.VideoCapture()
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, config.CAMERA_OPEN_TIMEOUT_MS)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, config.CAMERA_READ_TIMEOUT_MS)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.open(url, cv2.CAP_FFMPEG)
    try:
        if not cap.isOpened():
            logger.warning("OpenCV: cannot open stream %s", url)
            return None
        # Drain buffered frames to get the freshest one.
        for _ in range(5):
            cap.grab()
        ret, frame = cap.retrieve()
        if ret and frame is not None:
            logger.debug("OpenCV: captured %dx%d", frame.shape[1], frame.shape[0])
            return frame
        logger.warning("OpenCV: retrieve() returned no frame")
        return None
    finally:
        cap.release()


def grab_frame_ffmpeg(url: str | None = None) -> np.ndarray | None:
    """Grab one frame via ffmpeg, written to a temp JPEG and read back."""
    url = url or config.RTSP_URL
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    cmd = [
        "ffmpeg", "-y",
        "-rtsp_transport", "tcp",
        "-i", url,
        "-frames:v", "1",
        "-q:v", "2",
        str(tmp_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=config.FFMPEG_TIMEOUT_S,
        )
        if result.returncode != 0:
            tail = result.stderr[-400:].decode(errors="replace")
            logger.warning("ffmpeg exited %d: …%s", result.returncode, tail)
            return None
        frame = cv2.imread(str(tmp_path))
        if frame is None:
            logger.warning("ffmpeg: output file could not be read as image")
        return frame
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg: timed out after %ds", config.FFMPEG_TIMEOUT_S)
        return None
    except FileNotFoundError:
        logger.error("ffmpeg not found in PATH")
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def grab_frame(
    url: str | None = None,
    method: CaptureMethod | None = None,
) -> np.ndarray | None:
    """
    Grab one frame using the configured backend; fall back to the other on
    failure.  Returns a BGR ndarray or None if both backends fail.
    """
    method = method or config.CAPTURE_METHOD
    primary  = grab_frame_opencv if method == "opencv" else grab_frame_ffmpeg
    fallback = grab_frame_ffmpeg if method == "opencv" else grab_frame_opencv

    frame = primary(url)
    if frame is not None:
        return frame
    logger.warning("Primary capture (%s) failed — trying fallback", method)
    return fallback(url)
