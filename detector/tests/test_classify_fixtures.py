"""
tests/test_classify_fixtures.py — L3: golden regression on real labeled images.

Requires images resolvable from either:
  - tests/fixtures/images/  (committed)
  - .data/samples/          (local dev — immediately usable without extra steps)

Tests are SKIPPED (not failed) if no images are found.
"""
from __future__ import annotations

import pytest

import config
from detector import GateClassifier


class TestPerImageClassification:
    def test_all_labeled_images_present(self, labeled_images):
        if not labeled_images:
            pytest.skip("No labeled images found on disk — run populate_fixtures.py for CI")
        assert len(labeled_images) > 0

    def test_each_image_classified_correctly(self, labeled_images):
        """
        Golden regression: every labeled image must be classified to its
        expected state.  Failures are accumulated and reported together.
        """
        if not labeled_images:
            pytest.skip("No labeled images found")

        clf = GateClassifier()
        failures = []

        for filename, expected, frame in labeled_images:
            result = clf.classify(frame)
            if result.state != expected:
                c_bright = result.closed_result.bright_px if result.closed_result else "?"
                o_bright = result.open_result.bright_px   if result.open_result   else "?"
                failures.append(
                    f"  {filename}: expected={expected!r} got={result.state!r} "
                    f"conf={result.confidence:.2f} "
                    f"closed_bright={c_bright} open_bright={o_bright} "
                    f"{'IR' if result.is_ir else 'DAY'}"
                )

        assert not failures, (
            f"{len(failures)} image(s) misclassified:\n" + "\n".join(failures)
        )


class TestZoneScores:
    def test_open_frames_have_higher_open_score(self, open_frames, closed_frames):
        if not open_frames or not closed_frames:
            pytest.skip("Need both open and closed labeled images")

        clf = GateClassifier()
        avg_open_in_open   = sum(clf.classify(f).open_score for f in open_frames)   / len(open_frames)
        avg_open_in_closed = sum(clf.classify(f).open_score for f in closed_frames) / len(closed_frames)

        assert avg_open_in_open > avg_open_in_closed, (
            f"Open zone score should be higher in open frames "
            f"({avg_open_in_open:.2f} vs {avg_open_in_closed:.2f})"
        )


# ---------------------------------------------------------------------------
# Small-blade-open detection tests (feature-flagged)
# ---------------------------------------------------------------------------

def _skip_if_not_calibrated():
    if config.ZONE_SMALL_REST is None or config.ZONE_SMALL_OPEN is None:
        pytest.skip(
            "ZONE_SMALL_REST / ZONE_SMALL_OPEN not yet calibrated in config.py"
        )


class TestSmallBladeDetection:
    """
    Tests for the small-blade-open detection branch.

    All tests run with ``config.DETECT_SMALL_BLADE_OPEN = True`` (monkeypatched).
    They are skipped automatically when zone calibration has not been done yet
    (ZONE_SMALL_REST or ZONE_SMALL_OPEN is None) or when no images are available.

    To enable: calibrate ZONE_SMALL_REST / ZONE_SMALL_OPEN in config.py, then
    set DETECT_SMALL_BLADE_OPEN = True.
    """

    def test_small_blade_frames_classified_open(self, small_blade_frames, monkeypatch):
        """
        Frames 116-132 must mostly be classified as 'open' with the flag on.

        A subset (≈frames 125-129) have the person's body occluding the marker;
        those frames are expected to be missed.  Require at least 60 % detection.
        """
        monkeypatch.setattr(config, "DETECT_SMALL_BLADE_OPEN", True)
        _skip_if_not_calibrated()
        if not small_blade_frames:
            pytest.skip("No small-blade images found on disk")

        clf = GateClassifier()
        results = [(fn, clf.classify(f)) for fn, f in small_blade_frames]
        detected = [fn for fn, r in results if r.state == "open"]
        missed = [
            f"  {fn}: got={r.state!r} conf={r.confidence:.2f} reason={r.open_reason!r} "
            f"{'IR' if r.is_ir else 'DAY'}"
            for fn, r in results if r.state != "open"
        ]
        total = len(small_blade_frames)
        rate = len(detected) / total
        assert rate >= 0.60, (
            f"Only {len(detected)}/{total} ({rate:.0%}) small-blade frames detected as open "
            f"(need ≥60 %  — frames with occluded marker are expected to be missed):\n"
            + "\n".join(missed)
        )

    def test_small_blade_open_reason(self, small_blade_frames, monkeypatch):
        """Frames detected as open via small blade must report open_reason='small_blade'."""
        monkeypatch.setattr(config, "DETECT_SMALL_BLADE_OPEN", True)
        _skip_if_not_calibrated()
        if not small_blade_frames:
            pytest.skip("No small-blade images found on disk")

        clf = GateClassifier()
        open_results = [
            (fn, clf.classify(f))
            for fn, f in small_blade_frames
        ]
        detected_open = [(fn, r) for fn, r in open_results if r.state == "open"]
        if not detected_open:
            pytest.skip("No small-blade frames detected as open — check zone calibration")

        wrong_reason = [
            f"  {fn}: open_reason={r.open_reason!r}"
            for fn, r in detected_open
            if r.open_reason != "small_blade"
        ]
        assert not wrong_reason, (
            "Expected open_reason='small_blade' for small-blade frames:\n"
            + "\n".join(wrong_reason)
        )

    def test_no_regression_on_existing_frames_with_flag_on(
        self, labeled_images, monkeypatch
    ):
        """
        Enabling DETECT_SMALL_BLADE_OPEN must not change the result for any
        existing big-blade frame (guards against false positives from new zones).
        """
        monkeypatch.setattr(config, "DETECT_SMALL_BLADE_OPEN", True)
        _skip_if_not_calibrated()
        if not labeled_images:
            pytest.skip("No labeled images found")

        clf = GateClassifier()
        failures = []
        for filename, expected, frame in labeled_images:
            result = clf.classify(frame)
            if result.state != expected:
                failures.append(
                    f"  {filename}: expected={expected!r} got={result.state!r} "
                    f"conf={result.confidence:.2f} reason={result.open_reason!r}"
                )
        assert not failures, (
            f"{len(failures)} existing image(s) regressed with DETECT_SMALL_BLADE_OPEN=True:\n"
            + "\n".join(failures)
        )

    def test_flag_off_classifies_small_blade_as_closed(
        self, small_blade_frames, monkeypatch
    ):
        """
        With the flag off, small-blade-open frames must classify as 'closed'
        (big blade is still latched).  Proves the branch is fully isolated.
        """
        monkeypatch.setattr(config, "DETECT_SMALL_BLADE_OPEN", False)
        _skip_if_not_calibrated()
        if not small_blade_frames:
            pytest.skip("No small-blade images found on disk")

        clf = GateClassifier()
        not_closed = [
            f"  {fn}: got={clf.classify(f).state!r}"
            for fn, f in small_blade_frames
            if clf.classify(f).state != "closed"
        ]
        assert not not_closed, (
            "Small-blade frames should be 'closed' when flag is off:\n"
            + "\n".join(not_closed)
        )

