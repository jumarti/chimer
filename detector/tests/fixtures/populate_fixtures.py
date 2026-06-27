"""
tests/fixtures/populate_fixtures.py

Copy the labeled sample images from .data/samples/ into
tests/fixtures/images/ so they can be committed and used in CI.

Run once, then: git add tests/fixtures/images/ && git commit
"""
from __future__ import annotations
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent  # detector/
LABELS_FILE = Path(__file__).parent / "labels.json"
SAMPLES_DIR = ROOT / ".data" / "samples"
DEST_DIR    = Path(__file__).parent / "images"


def main() -> None:
    if not LABELS_FILE.exists():
        sys.exit(f"labels.json not found at {LABELS_FILE}")
    if not SAMPLES_DIR.exists():
        sys.exit(f"Samples directory not found: {SAMPLES_DIR}")

    with open(LABELS_FILE) as f:
        labels = json.load(f)

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    for filename in labels:
        src = SAMPLES_DIR / filename
        if src.exists():
            shutil.copy2(src, DEST_DIR / filename)
            print(f"  Copied  {filename}")
            copied += 1
        else:
            print(f"  Missing {filename}")
            skipped += 1

    print(f"\nDone: {copied} copied, {skipped} missing.")
    if copied:
        print("Next: git add tests/fixtures/images/ && git commit")


if __name__ == "__main__":
    main()
