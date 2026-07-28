"""Generates Backend/eval_data/sample_corpus.pdf: a small, zero-dependency,
multi-page synthetic PDF used as the fixture for the offline RAG evaluation
harness (see run_eval.py). Hand-assembles raw PDF object/xref syntax with the
standard (non-embedded) Helvetica font, so no PDF-authoring library is needed
at runtime or in CI.

Run this only when the fixture content needs to change:
    python scripts/generate_eval_fixture.py
"""

from pathlib import Path

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "eval_data" / "sample_corpus.pdf"

PAGES: list[list[str]] = [
    [
        "Meridian Robotics - Employee Handbook",
        "",
        "Company Overview",
        "Meridian Robotics was founded in 2016 and is headquartered in",
        "Austin, Texas. The company designs and builds autonomous",
        "warehouse robots used by logistics operators worldwide.",
    ],
    [
        "Paid Time Off Policy",
        "",
        "Employees accrue 18 days of paid time off per year. Sick leave",
        "is capped at 10 days annually and does not roll over. Parental",
        "leave is provided at 16 weeks paid for the primary caregiver.",
    ],
    [
        "Information Security Policy",
        "",
        "All company laptops must have full-disk encryption enabled",
        "before first use. Passwords must be rotated every 90 days.",
        "Security incidents must be reported to the security team",
        "within 24 hours of discovery.",
    ],
    [
        "Travel and Expense Policy",
        "",
        "Travel expenses under 75 dollars do not require a receipt.",
        "Reimbursement requests must be submitted within 30 days of",
        "the expense date. The approved travel booking tool is",
        "called TripLedger.",
    ],
    [
        "Remote Work Policy",
        "",
        "Employees may work remotely up to 3 days per week. Remote",
        "workers must be reachable during core hours of 10 AM to 4 PM",
        "Central Time. The annual home office stipend is 500 dollars.",
    ],
]


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(lines: list[str]) -> bytes:
    parts = ["BT", "/F1 11 Tf", "72 740 Td", "16 TL"]
    for index, line in enumerate(lines):
        if index > 0:
            parts.append("T*")
        parts.append(f"({_escape(line)}) Tj")
    parts.append("ET")
    stream_body = "\n".join(parts).encode("latin-1")
    return (
        f"<< /Length {len(stream_body)} >>\nstream\n".encode("latin-1")
        + stream_body
        + b"\nendstream"
    )


def build_pdf(pages: list[list[str]]) -> bytes:
    page_count = len(pages)
    font_id = 3 + 2 * page_count
    page_ids = [3 + 2 * index for index in range(page_count)]
    content_ids = [4 + 2 * index for index in range(page_count)]

    buffer = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}

    def write_object(object_id: int, body: bytes) -> None:
        offsets[object_id] = len(buffer)
        buffer.extend(f"{object_id} 0 obj\n".encode("latin-1"))
        buffer.extend(body)
        buffer.extend(b"\nendobj\n")

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    write_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    write_object(
        2,
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("latin-1"),
    )
    for page_id, content_id, lines in zip(page_ids, content_ids, pages):
        write_object(
            page_id,
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode("latin-1"),
        )
        write_object(content_id, _content_stream(lines))
    write_object(font_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    object_count = font_id + 1
    xref_offset = len(buffer)
    buffer.extend(f"xref\n0 {object_count}\n".encode("latin-1"))
    buffer.extend(b"0000000000 65535 f \n")
    for object_id in range(1, object_count):
        buffer.extend(f"{offsets[object_id]:010d} 00000 n \n".encode("latin-1"))
    buffer.extend(
        (
            f"trailer\n<< /Size {object_count} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("latin-1")
    )
    return bytes(buffer)


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(build_pdf(PAGES))
    print(f"Wrote {OUTPUT_PATH} ({len(PAGES)} pages)")


if __name__ == "__main__":
    main()
