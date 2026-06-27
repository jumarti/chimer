"""
tests/test_classify_fixtures.py — L3: golden regression on real labeled images.

Requires images resolvable from either:
  - tests/fixtures/images/  (committed)
  - .data/samples/          (local dev — immediately usable without extra steps)

Tests are SKIPPED (not failed) if no images are found.
"""
from __future__ import annotations

import pytest

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

