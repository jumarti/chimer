"""
detector/app.py — Gate-state REST service.

Polls the RTSP camera every POLL_INTERVAL_S seconds, runs the two-zone
CV detector, and exposes the result via a small Flask API.

Endpoints
---------
GET  /gate    {"gate":"open|closed|unknown","confidence":float,"last_updated":ISO}
GET  /health  {"status":"ok","camera":"connected|error","mode":"IR|DAY","drift":[dx,dy]|null}
GET  /debug   Latest annotated JPEG frame (image/jpeg)
POST /reset   Flush the temporal window and re-anchor on the next poll

Run
---
    uv run python app.py
    uv run python app.py --port 9090 --debug
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify

import config
from capture import grab_frame
from detector import FrameResult, GateClassifier, TemporalAggregator

try:
    __version__ = _pkg_version("detector")
except PackageNotFoundError:
    __version__ = "unknown"

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Werkzeug logs every HTTP request at INFO; keep those quiet unless DEBUG.
logging.getLogger("werkzeug").setLevel(
    logging.DEBUG if config.LOG_LEVEL.upper() == "DEBUG" else logging.WARNING
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_reported_state: str = "unknown"
_latest_result: FrameResult | None = None
_latest_annotated: np.ndarray | None = None
_camera_ok: bool = False

# Debug ring buffer: deque of (raw_frame, annotated_frame, result)
_ring: collections.deque = collections.deque(maxlen=config.DEBUG_RING_SIZE + 1)

# Counter for uncertain-frame sampling
_uncertain_streak: int = 0

# Periodic /gate request counter
_gate_req_count: int = 0
_gate_req_last_log: float = time.time()

# ---------------------------------------------------------------------------
# CV objects
# ---------------------------------------------------------------------------
_classifier  = GateClassifier()
_aggregator  = TemporalAggregator()


# ---------------------------------------------------------------------------
# Debug bundle helper
# ---------------------------------------------------------------------------

def _save_debug_bundle(tag: str) -> None:
    """Flush ring buffer + current frame to disk."""
    if not _ring:
        return
    ts = time.strftime("%Y%m%dT%H%M%S")
    bundle = Path(config.DEBUG_DIR) / f"{ts}_{tag}"
    try:
        bundle.mkdir(parents=True, exist_ok=True)
        for i, (raw, ann, res) in enumerate(_ring):
            cv2.imwrite(
                str(bundle / f"frame_{i:02d}_raw.jpg"), raw,
                [cv2.IMWRITE_JPEG_QUALITY, 90],
            )
            cv2.imwrite(
                str(bundle / f"frame_{i:02d}_annotated.jpg"), ann,
                [cv2.IMWRITE_JPEG_QUALITY, 90],
            )
            with open(bundle / f"frame_{i:02d}_result.json", "w") as f:
                json.dump(res.to_dict(), f, indent=2)
        logger.info("Debug bundle saved → %s", bundle)
    except OSError as e:
        logger.exception("Could not save debug bundle: %s", e)
        raise


# ---------------------------------------------------------------------------
# Poll loop (background thread)
# ---------------------------------------------------------------------------

def _poll_loop() -> None:
    global _reported_state, _latest_result, _latest_annotated, _camera_ok
    try:
        _poll_loop_inner()
    except Exception:
        logger.exception("Fatal error in poll thread — aborting process")
        os._exit(1)


def _poll_loop_inner() -> None:
    global _reported_state, _latest_result, _latest_annotated, _camera_ok

    while True:
        start = time.monotonic()

        frame = grab_frame()
        with _lock:
            _camera_ok = frame is not None

        if frame is None:
            logger.warning("Frame capture failed — skipping poll")
            time.sleep(config.POLL_INTERVAL_S)
            continue

        result = _classifier.classify(frame)
        annotated = _classifier.annotate(frame, result)
        new_state, debug = _aggregator.update(result)

        if config.SAVE_CAPTURES:
            _save_capture(frame)

        if config.SAVE_UNCERTAIN_SAMPLES:
            _maybe_save_uncertain(result.state, annotated)

        with _lock:
            prev_state = _reported_state
            _reported_state   = new_state
            _latest_result    = result
            _latest_annotated = annotated

        _ring.append((frame, annotated, result))

        if config.DEBUG_ON_CHANGE and new_state != prev_state and prev_state != "unknown":
            _save_debug_bundle(f"{prev_state}_to_{new_state}")

        elapsed = time.monotonic() - start
        sleep_s = max(0.0, config.POLL_INTERVAL_S - elapsed)
        time.sleep(sleep_s)


def _maybe_save_uncertain(state: str, annotated: np.ndarray) -> None:
    global _uncertain_streak
    if state != "uncertain":
        _uncertain_streak = 0
        return
    _uncertain_streak += 1
    if _uncertain_streak % config.UNCERTAIN_SAMPLE_RATE != 1:
        return  # only save on streak counts 1, 6, 11, …
    ts = time.strftime("%Y%m%dT%H%M%S")
    path = Path(config.UNCERTAIN_SAMPLE_DIR)
    try:
        path.mkdir(parents=True, exist_ok=True)
        out = path / f"{ts}_uncertain_{_uncertain_streak:04d}.jpg"
        cv2.imwrite(str(out), annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
        logger.debug("Uncertain sample saved → %s", out)
    except OSError as e:
        logger.warning("Could not save uncertain sample: %s", e)


def _save_capture(frame: np.ndarray) -> None:
    ts = time.strftime("%Y%m%dT%H%M%S")
    path = Path(config.CAPTURES_DIR)
    try:
        path.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path / f"{ts}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    except OSError as e:
        logger.warning("Could not save capture: %s", e)


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.get("/gate")
def gate():
    global _gate_req_count, _gate_req_last_log
    with _lock:
        state  = _reported_state
        result = _latest_result

    _gate_req_count += 1
    now = time.time()
    elapsed = now - _gate_req_last_log
    if config.GATE_LOG_INTERVAL_S > 0 and elapsed >= config.GATE_LOG_INTERVAL_S:
        logger.info("/gate polled %d times in the last %.0f min",
                    _gate_req_count, elapsed / 60)
        _gate_req_count = 0
        _gate_req_last_log = now

    # Map internal states to the three values the client understands.
    # "unknown" is the warmup state (no frames yet); treat it the same as
    # "uncertain" so the client can show an indeterminate UI.
    if state in ("open", "closed"):
        gate_value = state
    else:
        gate_value = "uncertain"
    confidence  = round(result.confidence, 4) if result else 0.0
    ts = result.timestamp if result else time.time()
    return jsonify(
        gate=gate_value,
        confidence=confidence,
        last_updated=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
    )


@app.get("/health")
def health():
    with _lock:
        ok     = _camera_ok
        result = _latest_result

    mode  = ("IR" if result.is_ir else "DAY") if result else "unknown"
    drift = list(result.anchor_offset) if (result and result.anchor_offset) else None
    return jsonify(
        status="ok",
        camera="connected" if ok else "error",
        mode=mode,
        drift=drift,
        reported_state=_aggregator.reported_state,
    )


@app.get("/debug")
def debug():
    """Return the latest annotated frame as JPEG."""
    with _lock:
        img = _latest_annotated

    if img is None:
        return Response("No frame yet", status=503, mimetype="text/plain")

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return Response("Encode failed", status=500, mimetype="text/plain")
    return Response(buf.tobytes(), mimetype="image/jpeg")


@app.post("/reset")
def reset():
    """Flush the temporal window; re-anchor on the next poll."""
    global _aggregator
    with _lock:
        _aggregator = TemporalAggregator()
    logger.info("Temporal window reset via /reset")
    return jsonify(status="reset")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _check_writable_dirs() -> None:
    """Verify that enabled output directories exist and are writable at startup.

    Raises OSError immediately (before the poll thread starts) so the process
    crashes with a clear message rather than failing silently later.
    """
    checks = []
    if config.DEBUG_ON_CHANGE:
        checks.append(("DEBUG_DIR", config.DEBUG_DIR))
    if config.SAVE_CAPTURES:
        checks.append(("CAPTURES_DIR", config.CAPTURES_DIR))
    if config.SAVE_UNCERTAIN_SAMPLES:
        checks.append(("UNCERTAIN_SAMPLE_DIR", config.UNCERTAIN_SAMPLE_DIR))

    for label, dir_str in checks:
        path = Path(dir_str)
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        try:
            probe.touch()
            probe.unlink()
        except OSError as e:
            raise OSError(
                f"{label}={dir_str!r} is not writable: {e}"
            ) from e
        logger.info("Write check OK: %s=%s", label, dir_str)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate detector REST service")
    parser.add_argument("--port",  type=int, default=config.PORT)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logger.info("detector v%s", __version__)
    try:
        from urllib.parse import urlparse
        _u = urlparse(config.RTSP_URL)
        _rtsp_display = f"{_u.scheme}://{_u.hostname}:{_u.port or 554}"
    except Exception:
        _rtsp_display = "<unparseable>"
    logger.info("RTSP URL   : %s", _rtsp_display)
    logger.info("Capture    : %s  ffmpeg_timeout=%ds  cv_open=%dms  cv_read=%dms",
                config.CAPTURE_METHOD, config.FFMPEG_TIMEOUT_S,
                config.CAMERA_OPEN_TIMEOUT_MS, config.CAMERA_READ_TIMEOUT_MS)
    logger.info("Poll every : %.0f s", config.POLL_INTERVAL_S)
    logger.info("Window     : %d frames, flip at %d", config.WINDOW_SIZE, config.FLIP_THRESHOLD)
    logger.info("ZONE_CLOSED: %s", config.ZONE_CLOSED)
    logger.info("ZONE_OPEN  : %s", config.ZONE_OPEN)
    logger.info("Debug      : dir=%s  on_change=%s  ring=%d",
                config.DEBUG_DIR, config.DEBUG_ON_CHANGE, config.DEBUG_RING_SIZE)
    logger.info("Uncertain  : dir=%s  enabled=%s  rate=1/%d",
                config.UNCERTAIN_SAMPLE_DIR, config.SAVE_UNCERTAIN_SAMPLES, config.UNCERTAIN_SAMPLE_RATE)
    logger.info("Log level  : %s", config.LOG_LEVEL.upper())

    _check_writable_dirs()

    logger.info("Listening  : http://%s:%d", config.HOST, args.port)

    poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="poll")
    poll_thread.start()

    # Exit immediately on SIGTERM (docker stop) instead of waiting for Flask's
    # internal shutdown timeout, which can be up to 10+ seconds.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    app.run(host=config.HOST, port=args.port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
