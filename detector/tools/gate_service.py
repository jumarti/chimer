#!/usr/bin/env python3
"""
Gate-state mock REST service for GateMonitor.ino.

Usage:
  python3 tools/gate_service.py            # default port 8080
  python3 tools/gate_service.py --port 9090

Endpoints:
  GET /gate    -> {"gate":"open"} or {"gate":"closed"}
  GET /health  -> {"status":"ok"}

Control the state by writing to tools/state.txt (relative to CWD):
  echo open   > tools/state.txt
  echo closed > tools/state.txt

Any value other than "open" is treated as "closed".
"""

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.txt")


def read_gate_state() -> str:
    """Read gate state from state.txt. Defaults to 'closed' on any error."""
    try:
        with open(STATE_FILE, "r") as f:
            raw = f.read().strip().lower()
            return "open" if raw == "open" else "closed"
    except OSError:
        return "closed"


class GateHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: N802 – override stdlib name
        print(f"[{self.address_string()}] {fmt % args}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 – matches BaseHTTPRequestHandler convention
        if self.path == "/gate":
            state = read_gate_state()
            print(f"  state.txt -> {state!r}")
            self._send_json(200, {"gate": state})

        elif self.path == "/health":
            self._send_json(200, {"status": "ok"})

        else:
            self._send_json(404, {"error": "not_found", "path": self.path})


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate-state mock service")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w") as f:
            f.write("closed\n")
        print(f"Created {STATE_FILE} with default state 'closed'")

    print(f"Gate service listening on http://0.0.0.0:{args.port}")
    print(f"State file: {os.path.abspath(STATE_FILE)}")
    print()
    print("  Set gate OPEN:   echo open   > tools/state.txt")
    print("  Set gate CLOSED: echo closed > tools/state.txt")
    print()

    httpd = HTTPServer(("", args.port), GateHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
