#!/usr/bin/env python3
"""CLI script for visual verification of stamp generation.

Usage:
    # Generate a sample A4 PDF
    python scripts/test_stamp.py --generate-sample sample.pdf

    # Letter-size sample
    python scripts/test_stamp.py --generate-sample sample_letter.pdf \
        --page-size letter

    # Single stamp
    python scripts/test_stamp.py sample.pdf stamped.pdf \
        --text PAID --date 2024-03-15 --doc-id 42

    # Custom color
    python scripts/test_stamp.py sample.pdf stamped.pdf \
        --text PAID --color "#990000" --doc-id 42

    # Multi-stamp demo (PAID + RECEIVED stacked)
    python scripts/test_stamp.py sample.pdf stamped.pdf \
        --demo --doc-id 42
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from project root without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from paperless_stamp.merger import (
    get_page1_dimensions,
    merge_stamp_overlay,
)
from paperless_stamp.stamp import (
    StampConfig,
    generate_stamp_overlay,
)

# Page sizes in points
PAGE_SIZES = {
    "a4": (595.28, 841.89),
    "letter": (612.0, 792.0),
}


def generate_sample_pdf(path: Path, page_size: str) -> None:
    """Generate a simple sample PDF with text content."""
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.pdfgen import canvas

    sizes = {"a4": A4, "letter": letter}
    w, h = sizes[page_size]
    c = canvas.Canvas(str(path), pagesize=(w, h))
    c.setFont("Helvetica", 24)
    c.drawString(72, h - 72, "Sample Document")
    c.setFont("Helvetica", 12)
    c.drawString(
        72, h - 108,
        f"Page size: {page_size.upper()} ({w:.0f} x {h:.0f} pt)",
    )
    c.drawString(
        72, h - 130,
        "This is a test document for stamp overlay verification.",
    )

    # Add some filler content
    y = h - 180
    for i in range(1, 20):
        c.drawString(
            72, y,
            f"Line {i}: Lorem ipsum dolor sit amet.",
        )
        y -= 18
        if y < 72:
            break

    c.save()
    print(f"Generated sample PDF: {path} ({page_size.upper()})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Visual stamp verification tool",
    )
    parser.add_argument("input", nargs="?", help="Input PDF")
    parser.add_argument("output", nargs="?", help="Output PDF")
    parser.add_argument(
        "--generate-sample", metavar="PATH",
        help="Generate a sample PDF",
    )
    parser.add_argument(
        "--page-size", choices=["a4", "letter"], default="a4",
    )
    parser.add_argument(
        "--text", default="PAID", help="Stamp text",
    )
    parser.add_argument("--date", help="Date (ISO 8601)")
    parser.add_argument(
        "--color", default="#003399", help="Hex color",
    )
    parser.add_argument(
        "--doc-id", type=int, default=1, help="Document ID",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Multi-stamp demo (PAID + RECEIVED)",
    )

    args = parser.parse_args()

    if args.generate_sample:
        generate_sample_pdf(
            Path(args.generate_sample), args.page_size,
        )
        return

    if not args.input or not args.output:
        parser.error(
            "input and output paths required "
            "(or use --generate-sample)",
        )

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    original_pdf = input_path.read_bytes()
    page_w, page_h = get_page1_dimensions(original_pdf)
    print(f"Input: {input_path} ({page_w:.0f} x {page_h:.0f} pt)")

    if args.demo:
        stamps = [
            StampConfig(
                text="PAID", doc_id=args.doc_id,
                date="2024-03-15", color="#003399",
            ),
            StampConfig(
                text="RECEIVED", doc_id=args.doc_id,
                color="#990000",
            ),
        ]
        print(f"Demo mode: {len(stamps)} stamps")
    else:
        stamps = [
            StampConfig(
                text=args.text,
                doc_id=args.doc_id,
                date=args.date,
                color=args.color,
            )
        ]
        date_str = f", date={args.date}" if args.date else ""
        print(
            f"Stamp: text={stamps[0].text}, "
            f"color={args.color}{date_str}, "
            f"doc_id={args.doc_id}",
        )

    overlay = generate_stamp_overlay(page_w, page_h, stamps)
    result = merge_stamp_overlay(original_pdf, overlay)
    output_path.write_bytes(result)
    print(f"Output: {output_path} ({len(result)} bytes)")


if __name__ == "__main__":
    main()
