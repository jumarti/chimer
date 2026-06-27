"""
detector/calibrate.py — calibration and threshold-tuning CLI.

Loads labeled images, runs the GateClassifier on each, and reports
per-image results plus an accuracy summary.  Use this to:
  1. Verify zones are correct on your labeled samples.
  2. Sweep BRIGHT_THRESHOLD / ZONE_BRIGHT_MIN_PX to find optimal values.
  3. Visually inspect annotated output (--show).
  4. Copy a curated subset to tests/fixtures/images/ (--copy-fixtures).

Usage
-----
    uv run python calibrate.py                         # run against all labeled images
    uv run python calibrate.py --sweep                 # sweep thresholds
    uv run python calibrate.py --show                  # display annotated frames
    uv run python calibrate.py --copy-fixtures         # populate tests/fixtures/images/
    uv run python calibrate.py --images-dir /path/to/  # custom image directory
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2

# Ensure local imports work when run directly.
sys.path.insert(0, str(Path(__file__).parent))

import config  # noqa: E402  (after sys.path insert)
from detector import GateClassifier  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"
LABELS_FILE  = FIXTURES_DIR / "labels.json"
SAMPLES_DIR  = Path(__file__).parent / ".data" / "samples"


def _find_images_dir(override: str | None) -> Path:
    if override:
        return Path(override)
    if FIXTURES_DIR.joinpath("images").exists():
        return FIXTURES_DIR / "images"
    if SAMPLES_DIR.exists():
        return SAMPLES_DIR
    sys.exit(
        "No images found.  Supply --images-dir or populate "
        ".data/samples/ or tests/fixtures/images/"
    )


def _load_labels(images_dir: Path) -> list[tuple[Path, str]]:
    """Return [(image_path, expected_state), ...] based on labels.json."""
    if not LABELS_FILE.exists():
        sys.exit(f"labels.json not found at {LABELS_FILE}")

    with open(LABELS_FILE) as f:
        labels: dict[str, dict] = json.load(f)

    pairs = []
    for filename, meta in labels.items():
        p = images_dir / filename
        if p.exists():
            pairs.append((p, meta["state"]))
        else:
            print(f"  [SKIP] {filename} — not found in {images_dir}")
    return pairs


def run_calibration(
    images_dir: Path,
    *,
    show: bool = False,
    threshold_override: int | None = None,
    min_px_override: int | None = None,
) -> dict:
    """
    Classify every labeled image and return a results dict.
    """
    if threshold_override is not None:
        config.BRIGHT_THRESHOLD = threshold_override
    if min_px_override is not None:
        config.ZONE_BRIGHT_MIN_PX = min_px_override

    clf = GateClassifier()
    pairs = _load_labels(images_dir)

    if not pairs:
        print("No matching images found — nothing to calibrate.")
        return {}

    results = {"images": [], "total": 0, "correct": 0, "false_open": 0, "false_closed": 0}

    for img_path, expected in pairs:
        frame = cv2.imread(str(img_path))
        if frame is None:
            print(f"  [ERROR] Could not read {img_path.name}")
            continue

        fr = clf.classify(frame)
        correct = fr.state == expected
        results["total"] += 1
        if correct:
            results["correct"] += 1
        elif fr.state == "open" and expected == "closed":
            results["false_open"] += 1
        elif fr.state == "closed" and expected == "open":
            results["false_closed"] += 1

        marker = "✓" if correct else "✗"
        print(
            f"  {marker} {img_path.name:<30s}  expected={expected:<9s}"
            f"  got={fr.state:<9s}  conf={fr.confidence:.2f}"
            f"  closed_bright={fr.closed_result.bright_px if fr.closed_result else '?':>5}"
            f"  open_bright={fr.open_result.bright_px if fr.open_result else '?':>5}"
            f"  {'IR' if fr.is_ir else 'DAY'}"
        )

        if show:
            ann = clf.annotate(frame, fr)
            cv2.imshow(f"{img_path.name} — expected:{expected} got:{fr.state}", ann)
            key = cv2.waitKey(0) & 0xFF
            cv2.destroyAllWindows()
            if key == 27:  # ESC
                break

        results["images"].append({
            "file": img_path.name,
            "expected": expected,
            "got": fr.state,
            "correct": correct,
            "confidence": fr.confidence,
            "closed_bright": fr.closed_result.bright_px if fr.closed_result else 0,
            "open_bright": fr.open_result.bright_px if fr.open_result else 0,
            "is_ir": fr.is_ir,
        })

    acc = results["correct"] / results["total"] if results["total"] else 0.0
    results["accuracy"] = acc
    return results


def sweep_thresholds(images_dir: Path) -> None:
    """Sweep BRIGHT_THRESHOLD and ZONE_BRIGHT_MIN_PX; print accuracy table."""
    print("\n=== Threshold sweep ===")
    print(f"{'thresh':>7}  {'min_px':>7}  {'acc':>6}  {'FP':>4}  {'FN':>4}")
    for thresh in range(160, 250, 10):
        for min_px in [80, 120, 160, 200]:
            r = run_calibration(
                images_dir,
                threshold_override=thresh,
                min_px_override=min_px,
            )
            if r:
                print(
                    f"  {thresh:5d}    {min_px:5d}"
                    f"  {r['accuracy']:5.2f}"
                    f"  {r['false_open']:3d}"
                    f"  {r['false_closed']:3d}"
                )


def copy_fixtures(images_dir: Path) -> None:
    """Copy labeled images from images_dir into tests/fixtures/images/."""
    dest = FIXTURES_DIR / "images"
    dest.mkdir(parents=True, exist_ok=True)

    with open(LABELS_FILE) as f:
        labels = json.load(f)

    copied = 0
    for filename in labels:
        src = images_dir / filename
        if src.exists():
            shutil.copy2(src, dest / filename)
            copied += 1
            print(f"  Copied {filename}")
        else:
            print(f"  [SKIP] {filename} not found in {images_dir}")

    print(f"\nCopied {copied} images → {dest}")
    print("Remember to: git add tests/fixtures/images/ && git commit")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate detector calibration tool")
    parser.add_argument("--images-dir", help="Directory containing sample images")
    parser.add_argument("--sweep",  action="store_true", help="Sweep threshold values")
    parser.add_argument("--show",   action="store_true", help="Display annotated frames (requires GUI)")
    parser.add_argument("--copy-fixtures", action="store_true",
                        help="Copy labeled images to tests/fixtures/images/")
    args = parser.parse_args()

    images_dir = _find_images_dir(args.images_dir)
    print(f"Images dir : {images_dir}")
    print(f"Labels     : {LABELS_FILE}")
    print(f"ZONE_CLOSED: {config.ZONE_CLOSED}")
    print(f"ZONE_OPEN  : {config.ZONE_OPEN}")
    print(f"Threshold  : {config.BRIGHT_THRESHOLD}  min_px={config.ZONE_BRIGHT_MIN_PX}")
    print()

    if args.copy_fixtures:
        copy_fixtures(images_dir)
        return

    if args.sweep:
        sweep_thresholds(images_dir)
        return

    print("=== Per-image results ===")
    r = run_calibration(images_dir, show=args.show)
    if r:
        print(
            f"\nAccuracy: {r['correct']}/{r['total']} = {r['accuracy']:.1%}"
            f"  false_open={r['false_open']}  false_closed={r['false_closed']}"
        )


if __name__ == "__main__":
    main()
