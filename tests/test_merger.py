"""Unit tests for PDF merging."""

from __future__ import annotations

import io

import pikepdf
import pytest

from paperless_stamp.exceptions import StampMergeError
from paperless_stamp.merger import get_page1_dimensions, merge_stamp_overlay
from paperless_stamp.stamp import StampConfig, generate_stamp_overlay

from .conftest import A4_HEIGHT, A4_WIDTH, LETTER_HEIGHT, LETTER_WIDTH, _make_pdf


class TestGetPage1Dimensions:
    def test_a4(self, a4_pdf):
        w, h = get_page1_dimensions(a4_pdf)
        assert w == pytest.approx(A4_WIDTH, abs=1)
        assert h == pytest.approx(A4_HEIGHT, abs=1)

    def test_letter(self, letter_pdf):
        w, h = get_page1_dimensions(letter_pdf)
        assert w == pytest.approx(LETTER_WIDTH, abs=1)
        assert h == pytest.approx(LETTER_HEIGHT, abs=1)

    def test_encrypted_raises(self):
        # Create an encrypted PDF
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        buf = io.BytesIO()
        pdf.save(buf, encryption=pikepdf.Encryption(owner="secret", user="secret"))
        encrypted_bytes = buf.getvalue()

        with pytest.raises(StampMergeError, match="encrypted"):
            get_page1_dimensions(encrypted_bytes)


class TestMergeStampOverlay:
    def test_returns_valid_pdf(self, a4_pdf):
        stamp = StampConfig(text="PAID", doc_id=1)
        overlay = generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, [stamp])
        result = merge_stamp_overlay(a4_pdf, overlay)
        with pikepdf.open(io.BytesIO(result)) as pdf:
            assert len(pdf.pages) >= 1

    def test_preserves_page_count(self, multipage_pdf):
        stamp = StampConfig(text="PAID", doc_id=1)
        overlay = generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, [stamp])
        result = merge_stamp_overlay(multipage_pdf, overlay)
        with pikepdf.open(io.BytesIO(result)) as pdf:
            assert len(pdf.pages) == 3

    def test_preserves_dimensions(self, a4_pdf):
        stamp = StampConfig(text="PAID", doc_id=1)
        overlay = generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, [stamp])
        result = merge_stamp_overlay(a4_pdf, overlay)
        w, h = get_page1_dimensions(result)
        assert w == pytest.approx(A4_WIDTH, abs=1)
        assert h == pytest.approx(A4_HEIGHT, abs=1)

    def test_encrypted_pdf_raises(self):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        buf = io.BytesIO()
        pdf.save(buf, encryption=pikepdf.Encryption(owner="secret", user="secret"))
        encrypted_bytes = buf.getvalue()

        stamp = StampConfig(text="PAID", doc_id=1)
        overlay = generate_stamp_overlay(612, 792, [stamp])
        with pytest.raises(StampMergeError, match="encrypted"):
            merge_stamp_overlay(encrypted_bytes, overlay)

    def test_roundtrip_a4(self, a4_pdf):
        stamp = StampConfig(text="PAID", doc_id=42, date="2024-01-15")
        overlay = generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, [stamp])
        result = merge_stamp_overlay(a4_pdf, overlay)
        # Result should be a valid PDF we can re-read
        w, h = get_page1_dimensions(result)
        assert w == pytest.approx(A4_WIDTH, abs=1)

    def test_roundtrip_letter(self, letter_pdf):
        stamp = StampConfig(text="RECEIVED", doc_id=99, color="#990000")
        overlay = generate_stamp_overlay(LETTER_WIDTH, LETTER_HEIGHT, [stamp])
        result = merge_stamp_overlay(letter_pdf, overlay)
        w, h = get_page1_dimensions(result)
        assert w == pytest.approx(LETTER_WIDTH, abs=1)

    def test_multi_stamp_merge(self, a4_pdf):
        stamps = [
            StampConfig(text="PAID", doc_id=1, date="2024-03-15"),
            StampConfig(text="RECEIVED", doc_id=1, color="#990000"),
        ]
        overlay = generate_stamp_overlay(A4_WIDTH, A4_HEIGHT, stamps)
        result = merge_stamp_overlay(a4_pdf, overlay)
        with pikepdf.open(io.BytesIO(result)) as pdf:
            assert len(pdf.pages) == 1
