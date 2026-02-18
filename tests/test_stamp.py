"""Unit tests for stamp overlay generation."""

from __future__ import annotations

import io

import pikepdf
import pytest

from paperless_stamp.exceptions import StampGenerationError
from paperless_stamp.stamp import (
    StampConfig,
    _compute_tilt,
    _hex_to_rgb,
    generate_stamp_overlay,
)

from .conftest import A4_HEIGHT, A4_WIDTH, LETTER_HEIGHT, LETTER_WIDTH


# --- _hex_to_rgb ---


class TestHexToRgb:
    def test_valid_color(self):
        assert _hex_to_rgb("#003399") == pytest.approx((0.0, 0.2, 0.6))

    def test_valid_no_hash(self):
        assert _hex_to_rgb("FF0000") == pytest.approx((1.0, 0.0, 0.0))

    def test_white(self):
        assert _hex_to_rgb("#FFFFFF") == pytest.approx((1.0, 1.0, 1.0))

    def test_black(self):
        assert _hex_to_rgb("#000000") == pytest.approx((0.0, 0.0, 0.0))

    def test_invalid_short(self):
        with pytest.raises(StampGenerationError, match="Invalid hex color"):
            _hex_to_rgb("#FFF")

    def test_invalid_chars(self):
        with pytest.raises(StampGenerationError, match="Invalid hex color"):
            _hex_to_rgb("#GGGGGG")


# --- _compute_tilt ---


class TestComputeTilt:
    def test_deterministic(self):
        assert _compute_tilt(42) == _compute_tilt(42)

    def test_within_range(self):
        for doc_id in range(1000):
            tilt = _compute_tilt(doc_id)
            assert -3.0 <= tilt <= 3.0

    def test_different_ids_differ(self):
        assert _compute_tilt(1) != _compute_tilt(2)


# --- StampConfig ---


class TestStampConfig:
    def test_uppercases_text(self):
        s = StampConfig(text="paid", doc_id=1)
        assert s.text == "PAID"

    def test_default_color(self):
        s = StampConfig(text="test", doc_id=1)
        assert s.color == "#003399"


# --- generate_stamp_overlay ---


class TestGenerateStampOverlay:
    def test_returns_valid_pdf(self, stamp_paid):
        result = generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, [stamp_paid])
        # Should be parseable as a PDF
        with pikepdf.open(io.BytesIO(result)) as pdf:
            assert len(pdf.pages) == 1

    def test_correct_dimensions_a4(self, stamp_paid):
        result = generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, [stamp_paid])
        with pikepdf.open(io.BytesIO(result)) as pdf:
            box = pdf.pages[0].mediabox
            assert float(box[2]) == pytest.approx(A4_WIDTH, abs=1)
            assert float(box[3]) == pytest.approx(A4_HEIGHT, abs=1)

    def test_correct_dimensions_letter(self, stamp_paid):
        # Use a new stamp since page size differs
        stamp = StampConfig(text="paid", doc_id=42)
        result = generate_stamp_overlay(LETTER_WIDTH, LETTER_HEIGHT, [stamp])
        with pikepdf.open(io.BytesIO(result)) as pdf:
            box = pdf.pages[0].mediabox
            assert float(box[2]) == pytest.approx(LETTER_WIDTH, abs=1)
            assert float(box[3]) == pytest.approx(LETTER_HEIGHT, abs=1)

    def test_with_date(self, stamp_paid_with_date):
        result = generate_stamp_overlay(
            A4_WIDTH, A4_HEIGHT, [stamp_paid_with_date]
        )
        with pikepdf.open(io.BytesIO(result)) as pdf:
            assert len(pdf.pages) == 1

    def test_without_date(self, stamp_paid):
        result = generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, [stamp_paid])
        with pikepdf.open(io.BytesIO(result)) as pdf:
            assert len(pdf.pages) == 1

    def test_multiple_stamps(self, stamp_paid, stamp_received):
        result = generate_stamp_overlay(
            A4_WIDTH, A4_HEIGHT, [stamp_paid, stamp_received]
        )
        with pikepdf.open(io.BytesIO(result)) as pdf:
            assert len(pdf.pages) == 1

    def test_deterministic_layout(self, stamp_paid):
        """Same inputs produce same page structure (ReportLab embeds timestamps)."""
        r1 = generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, [stamp_paid])
        r2 = generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, [stamp_paid])
        # Both should be valid single-page PDFs with same dimensions
        with pikepdf.open(io.BytesIO(r1)) as p1, pikepdf.open(io.BytesIO(r2)) as p2:
            assert len(p1.pages) == len(p2.pages)
            b1, b2 = p1.pages[0].mediabox, p2.pages[0].mediabox
            assert float(b1[2]) == float(b2[2])
            assert float(b1[3]) == float(b2[3])

    def test_empty_stamps_raises(self):
        with pytest.raises(StampGenerationError, match="At least one stamp"):
            generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, [])

    def test_invalid_dimensions_zero(self, stamp_paid):
        with pytest.raises(StampGenerationError, match="Invalid page dimensions"):
            generate_stamp_overlay(0, A4_HEIGHT, [stamp_paid])

    def test_invalid_dimensions_negative(self, stamp_paid):
        with pytest.raises(StampGenerationError, match="Invalid page dimensions"):
            generate_stamp_overlay(-100, A4_HEIGHT, [stamp_paid])

    def test_custom_color(self):
        stamp = StampConfig(text="APPROVED", doc_id=7, color="#009900")
        result = generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, [stamp])
        with pikepdf.open(io.BytesIO(result)) as pdf:
            assert len(pdf.pages) == 1
