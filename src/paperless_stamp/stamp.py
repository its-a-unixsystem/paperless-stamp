"""Stamp overlay generator using ReportLab."""

from __future__ import annotations

import hashlib
import io
import math
import random
from dataclasses import dataclass

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from paperless_stamp.exceptions import StampGenerationError

# Courier-Bold is a ReportLab built-in — no font registration needed
_FONT_NAME = "Courier-Bold"

# Layout constants
_STAMP_WIDTH_RATIO = 0.20  # 20% of page width
_STAMP_RIGHT_EDGE = 0.90  # right edge at 90% page width
_STAMP_TOP_EDGE = 0.90  # top edge at 90% page height
_STACK_SPACING = 1.3  # vertical offset multiplier between stacked stamps
_DATE_FONT_RATIO = 0.35  # date font size relative to main text
_PADDING_RATIO = 0.25  # horizontal padding relative to font size
_BORDER_GAP = 2.0  # gap in points between inner and outer border
_BORDER_STROKES = 7  # number of overlapping strokes for fuzzy border
_TEXT_STROKES = 4  # number of overlapping strokes for fuzzy text
_JITTER_SCALE = 0.4  # Gaussian jitter magnitude in points


@dataclass(frozen=True)
class StampConfig:
    """Configuration for a single stamp mark."""

    text: str
    doc_id: int
    date: str | None = None
    color: str = "#003399"

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", self.text.upper())


@dataclass(frozen=True)
class StampPlacement:
    """Computed placement information for a stamp."""

    stamp: StampConfig
    layout: dict[str, float]
    tilt_degrees: float
    center_x: float
    center_y: float


def generate_stamp_overlay(
    page_width: float,
    page_height: float,
    stamps: list[StampConfig],
) -> bytes:
    """Generate a transparent PDF overlay with stamp marks.

    Args:
        page_width: Page width in points.
        page_height: Page height in points.
        stamps: List of stamp configurations to render.

    Returns:
        PDF bytes of the overlay.

    Raises:
        StampGenerationError: If inputs are invalid.
    """
    if not stamps:
        raise StampGenerationError("At least one stamp is required")
    if page_width <= 0 or page_height <= 0:
        raise StampGenerationError(
            f"Invalid page dimensions: {page_width}x{page_height}"
        )

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))

    stamp_width = page_width * _STAMP_WIDTH_RATIO
    placements = _calculate_stamp_placements(
        page_width=page_width,
        page_height=page_height,
        stamps=stamps,
        stamp_width=stamp_width,
    )

    for placement in placements:
        _draw_stamp(
            c,
            placement.center_x,
            placement.center_y,
            placement.tilt_degrees,
            placement.stamp,
            placement.layout,
        )

    c.save()
    return buf.getvalue()


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert hex color string to (r, g, b) floats in [0, 1]."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise StampGenerationError(f"Invalid hex color: {hex_color}")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError as e:
        raise StampGenerationError(f"Invalid hex color: {hex_color}") from e
    return r / 255.0, g / 255.0, b / 255.0


def _compute_tilt(doc_id: int) -> float:
    """Compute a deterministic tilt angle in [-3.0, -1.0] ∪ [1.0, 3.0] degrees."""
    digest = hashlib.sha256(str(doc_id).encode()).hexdigest()

    sign_source = int(digest[:8], 16)
    magnitude_source = int(digest[8:16], 16)

    tilt_sign = -1.0 if sign_source % 2 else 1.0
    tilt_magnitude = 1.0 + (magnitude_source / 0xFFFFFFFF) * 2.0
    tilt_degrees = tilt_sign * tilt_magnitude

    assert 1.0 <= abs(tilt_degrees) <= 3.0
    return tilt_degrees


def _fit_font_size(
    text: str,
    max_width: float,
    padding: float,
) -> float:
    """Find the largest Courier-Bold size that fits text within max_width."""
    # Courier is monospaced: each char is ~0.6 * font_size
    available = max_width - 2 * padding
    if available <= 0 or not text:
        return 8.0  # minimum fallback
    char_width_ratio = 0.6
    size = available / (len(text) * char_width_ratio)
    return max(size, 8.0)


def _calculate_stamp_layout(
    stamp: StampConfig,
    stamp_width: float,
) -> dict:
    """Compute all dimensions and font sizes for a stamp."""
    font_size = _fit_font_size(stamp.text, stamp_width, stamp_width * _PADDING_RATIO)
    padding = font_size * _PADDING_RATIO

    # Height depends on whether date is present
    date_font_size = font_size * _DATE_FONT_RATIO
    if stamp.date:
        text_block_height = font_size + date_font_size + 2 * mm
        height = text_block_height + 2 * padding
    else:
        height = font_size + 2 * padding

    return {
        "width": stamp_width,
        "height": height,
        "font_size": font_size,
        "date_font_size": date_font_size,
        "padding": padding,
    }


def _projected_half_height(
    stamp_width: float,
    stamp_height: float,
    tilt_degrees: float,
) -> float:
    """Compute stamp half-height projected on the page y-axis after rotation."""
    tilt_radians = math.radians(tilt_degrees)
    projected_height = (
        abs(stamp_height * math.cos(tilt_radians))
        + abs(stamp_width * math.sin(tilt_radians))
    )
    return projected_height / 2


def _calculate_stamp_placements(
    page_width: float,
    page_height: float,
    stamps: list[StampConfig],
    stamp_width: float,
) -> list[StampPlacement]:
    """Calculate stacked placements while preventing overlap for mixed stamp sizes."""
    assert _STACK_SPACING >= 1.0

    stamp_center_x = page_width * _STAMP_RIGHT_EDGE - stamp_width / 2
    next_stamp_top_edge = page_height * _STAMP_TOP_EDGE
    placements: list[StampPlacement] = []

    for stamp in stamps:
        layout = _calculate_stamp_layout(stamp, stamp_width)
        tilt_degrees = _compute_tilt(stamp.doc_id)
        projected_half_height = _projected_half_height(
            stamp_width=layout["width"],
            stamp_height=layout["height"],
            tilt_degrees=tilt_degrees,
        )
        stamp_center_y = next_stamp_top_edge - projected_half_height

        placements.append(
            StampPlacement(
                stamp=stamp,
                layout=layout,
                tilt_degrees=tilt_degrees,
                center_x=stamp_center_x,
                center_y=stamp_center_y,
            )
        )

        stack_gap = layout["height"] * (_STACK_SPACING - 1.0)
        next_stamp_top_edge = stamp_center_y - projected_half_height - stack_gap

    return placements


def _draw_fuzzy_border(
    c: canvas.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    color: tuple[float, float, float],
    rng: random.Random,
) -> None:
    """Draw a double-line rectangle with fuzzy ink effect."""
    for rect_offset in (0, _BORDER_GAP + 1.5):
        rx = x + rect_offset
        ry = y + rect_offset
        rw = w - 2 * rect_offset
        rh = h - 2 * rect_offset
        if rw <= 0 or rh <= 0:
            continue

        for stroke_i in range(_BORDER_STROKES):
            alpha = 0.08 + 0.04 * stroke_i
            c.saveState()
            c.setStrokeColorRGB(*color)
            c.setStrokeAlpha(min(alpha, 0.5))
            c.setLineWidth(0.8)
            c.setFillAlpha(0)
            jx = rng.gauss(0, _JITTER_SCALE)
            jy = rng.gauss(0, _JITTER_SCALE)
            c.rect(rx + jx, ry + jy, rw, rh)
            c.restoreState()


def _draw_fuzzy_text(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    font_size: float,
    color: tuple[float, float, float],
    rng: random.Random,
) -> None:
    """Draw text with overlapping strokes for ink realism."""
    for stroke_i in range(_TEXT_STROKES):
        alpha = 0.12 + 0.10 * stroke_i
        c.saveState()
        c.setFillColorRGB(*color)
        c.setFillAlpha(min(alpha, 0.5))
        c.setFont(_FONT_NAME, font_size)
        jx = rng.gauss(0, _JITTER_SCALE * 0.5)
        jy = rng.gauss(0, _JITTER_SCALE * 0.5)
        c.drawCentredString(x + jx, y + jy, text)
        c.restoreState()


def _draw_stamp(
    c: canvas.Canvas,
    cx: float,
    cy: float,
    tilt: float,
    stamp: StampConfig,
    layout: dict,
) -> None:
    """Compose a full stamp (border + text + optional date) at center point."""
    color = _hex_to_rgb(stamp.color)
    rng = random.Random(f"{stamp.doc_id}:{stamp.text}")

    w = layout["width"]
    h = layout["height"]
    font_size = layout["font_size"]
    date_font_size = layout["date_font_size"]

    c.saveState()
    c.translate(cx, cy)
    c.rotate(tilt)

    # Border centered on origin
    _draw_fuzzy_border(c, -w / 2, -h / 2, w, h, color, rng)

    # Main text
    if stamp.date:
        # Text slightly above center, date below
        text_y = -font_size * 0.2 + 1 * mm
        date_y = text_y - date_font_size - 2 * mm
        _draw_fuzzy_text(c, stamp.text, 0, text_y, font_size, color, rng)
        _draw_fuzzy_text(c, stamp.date, 0, date_y, date_font_size, color, rng)
    else:
        # Vertically centered
        text_y = -font_size * 0.35
        _draw_fuzzy_text(c, stamp.text, 0, text_y, font_size, color, rng)

    c.restoreState()
