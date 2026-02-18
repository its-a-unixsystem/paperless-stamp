"""Shared pytest fixtures for paperless-stamp tests."""

from __future__ import annotations

import io

import pikepdf
import pytest

from paperless_stamp.stamp import StampConfig

# Standard page sizes in points
A4_WIDTH, A4_HEIGHT = 595.28, 841.89
LETTER_WIDTH, LETTER_HEIGHT = 612.0, 792.0


def _make_pdf(width: float, height: float, pages: int = 1) -> bytes:
    """Create a minimal blank PDF with the given dimensions."""
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(width, height))

    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


@pytest.fixture
def a4_pdf() -> bytes:
    """Single-page A4 PDF."""
    return _make_pdf(A4_WIDTH, A4_HEIGHT)


@pytest.fixture
def letter_pdf() -> bytes:
    """Single-page US Letter PDF."""
    return _make_pdf(LETTER_WIDTH, LETTER_HEIGHT)


@pytest.fixture
def multipage_pdf() -> bytes:
    """Three-page A4 PDF."""
    return _make_pdf(A4_WIDTH, A4_HEIGHT, pages=3)


@pytest.fixture
def stamp_paid() -> StampConfig:
    """Basic PAID stamp config."""
    return StampConfig(text="paid", doc_id=42)


@pytest.fixture
def stamp_paid_with_date() -> StampConfig:
    """PAID stamp with a date."""
    return StampConfig(text="paid", doc_id=42, date="2024-03-15")


@pytest.fixture
def stamp_received() -> StampConfig:
    """RECEIVED stamp in red."""
    return StampConfig(text="received", doc_id=42, color="#990000")
