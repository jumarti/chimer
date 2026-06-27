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
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify

import config
from capture import grab_frame
from detector import FrameResult, GateClassifier, TemporalAggregator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
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
        logger.error("Could not save debug bundle: %s", e)


# ---------------------------------------------------------------------------
# Poll loop (background thread)
# ---------------------------------------------------------------------------

def _poll_loop() -> None:
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
    with _lock:
        state  = _reported_state
        result = _latest_result

    # "unknown" is an internal warmup state; report "closed" to the Arduino
    # so it stays in its safe default (matches gate_service.py mock contract).
    gate_value = state if state in ("open", "closed") else "closed"
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

def main() -> None:
    parser = argparse.ArgumentParser(description="Gate detector REST service")
    parser.add_argument("--port",  type=int, default=config.PORT)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logger.info("RTSP URL   : %s", config.RTSP_URL)
    logger.info("Poll every : %.0f s", config.POLL_INTERVAL_S)
    logger.info("Window     : %d frames, flip at %d", config.WINDOW_SIZE, config.FLIP_THRESHOLD)
    logger.info("ZONE_CLOSED: %s", config.ZONE_CLOSED)
    logger.info("ZONE_OPEN  : %s", config.ZONE_OPEN)
    logger.info("Listening  : http://%s:%d", config.HOST, args.port)

    poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="poll")
    poll_thread.start()

    app.run(host=config.HOST, port=args.port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
