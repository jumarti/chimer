"""
detector/camera.py — background RTSP frame grabber.

Runs a daemon thread that continuously reads the latest frame from the
RTSP stream. Consumers call `get_latest_frame()` to obtain a copy of
the most recent successfully decoded frame (or None if none yet).
"""

import logging
import threading
import time
from typing import Optional

import cv2
import numpy as np

import config

logger = logging.getLogger(__name__)


class CameraPoller:
    """Thread-safe RTSP frame grabber."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._connected = False
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="camera-poller")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling thread."""
        self._thread.start()
        logger.info("Camera poller started (url=%s)", config.RTSP_URL)

    def stop(self) -> None:
        """Signal the polling thread to stop."""
        self._stop_event.set()

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """Return a copy of the latest decoded frame, or None."""
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            self._stop_event.wait(config.CAMERA_POLL_INTERVAL)

    def _poll_once(self) -> None:
        cap = cv2.VideoCapture(config.RTSP_URL)
        try:
            if not cap.isOpened():
                logger.warning("Cannot open RTSP stream: %s", config.RTSP_URL)
                with self._lock:
                    self._connected = False
                return

            # Drain buffered frames to get the *latest* one.
            for _ in range(5):
                cap.grab()

            ret, frame = cap.retrieve()
            if ret and frame is not None:
                with self._lock:
                    self._frame = frame
                    self._connected = True
            else:
                logger.warning("Failed to retrieve frame from stream")
                with self._lock:
                    self._connected = False
        finally:
            cap.release()
