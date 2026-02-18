"""PDF merger — overlays stamp PDFs onto original documents using pikepdf."""

from __future__ import annotations

import io

import pikepdf

from paperless_stamp.exceptions import StampMergeError


def get_page1_dimensions(pdf_bytes: bytes) -> tuple[float, float]:
    """Read the MediaBox of page 1 and return (width, height) in points.

    Raises:
        StampMergeError: If the PDF cannot be read.
    """
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            page = pdf.pages[0]
            box = page.mediabox
            width = float(box[2]) - float(box[0])
            height = float(box[3]) - float(box[1])
            return width, height
    except pikepdf.PasswordError as e:
        raise StampMergeError(f"PDF is encrypted: {e}") from e
    except pikepdf.PdfError as e:
        raise StampMergeError(f"Invalid PDF: {e}") from e


def merge_stamp_overlay(original_pdf: bytes, overlay_pdf: bytes) -> bytes:
    """Merge a stamp overlay onto page 1 of the original PDF.

    All other pages are left untouched.

    Args:
        original_pdf: Bytes of the original document PDF.
        overlay_pdf: Bytes of the stamp overlay PDF.

    Returns:
        Bytes of the merged PDF.

    Raises:
        StampMergeError: If merging fails (encrypted PDF, invalid PDF, etc.).
    """
    try:
        with (
            pikepdf.open(io.BytesIO(original_pdf)) as orig,
            pikepdf.open(io.BytesIO(overlay_pdf)) as overlay,
        ):
            orig.pages[0].add_overlay(overlay.pages[0])

            buf = io.BytesIO()
            orig.save(buf)
            return buf.getvalue()
    except pikepdf.PasswordError as e:
        raise StampMergeError(f"PDF is encrypted: {e}") from e
    except pikepdf.PdfError as e:
        raise StampMergeError(f"Failed to merge PDFs: {e}") from e
