"""
Layout-aware PDF extraction with PyMuPDF.

The single most valuable thing here is column detection. A two-column resume
with a sidebar, read naively top-to-bottom, produces interleaved nonsense:
"Sarah Chen React Senior Engineer TypeScript Stripe 2019". Clustering text
blocks by x-position and reading each column fully before moving on fixes a
large share of real-world parsing failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.extraction.quality import detect_hidden_text

logger = get_logger(__name__)

#: Minimum horizontal gap (as a fraction of page width) to call it a column break.
COLUMN_GAP_RATIO = 0.06
#: A sidebar narrower than this fraction of the page is read after the main body.
SIDEBAR_MAX_RATIO = 0.38
#: Left edges within this many points are treated as the same column.
#: Bullet indents and nested lists drift a few points from their parent.
X_CLUSTER_TOLERANCE = 12.0


@dataclass(slots=True)
class PdfExtraction:
    text: str
    page_count: int
    page_breaks: list[int] = field(default_factory=list)
    has_text_layer: bool = True
    hidden_text: list[dict] = field(default_factory=list)
    is_multi_column: bool = False


def _blocks_for_page(page) -> list[dict]:
    """Flatten PyMuPDF's dict output into positioned text blocks."""
    data = page.get_text("dict")
    blocks = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:  # 0 = text
            continue
        lines = []
        spans_meta = []
        for line in block.get("lines", []):
            parts = []
            for span in line.get("spans", []):
                parts.append(span.get("text", ""))
                spans_meta.append({
                    "text": span.get("text", ""),
                    "color": span.get("color", 0),
                    "size": span.get("size", 12),
                })
            if parts:
                lines.append("".join(parts))
        text = "\n".join(lines).strip()
        if not text:
            continue
        x0, y0, x1, y1 = block["bbox"]
        blocks.append({
            "text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "spans": spans_meta,
        })
    return blocks


def _detect_columns(blocks: list[dict], page_width: float) -> list[list[dict]]:
    """
    Group blocks into columns by x-position.

    Returns a list of block groups in reading order. A single-column page returns
    one group, which is the common case and costs almost nothing to check.

    The subtlety worth knowing: most two-column resumes put a full-width name
    banner across the top. Those spanning blocks have to be excluded from the
    gutter search, because a single block reaching the right margin makes every
    subsequent gap look like part of one wide column and defeats detection
    entirely. They are re-inserted afterwards, above or below the columns
    according to their vertical position.
    """
    if len(blocks) < 6:
        return [blocks]

    # Cluster the left edges. Column text shares an x-start; a two-column page
    # produces two dense clusters. This is used instead of scanning for a gap in
    # x-coverage because a header that is merely *wide* — a contact line running
    # past the gutter, say — silently closes the gap and defeats that approach.
    starts = sorted(b["x0"] for b in blocks)
    clusters: list[list[float]] = [[starts[0]]]
    for value in starts[1:]:
        if value - clusters[-1][-1] <= X_CLUSTER_TOLERANCE:
            clusters[-1].append(value)
        else:
            clusters.append([value])

    # Keep clusters with enough members to be a real column.
    dense = [c for c in clusters if len(c) >= 2]
    if len(dense) < 2:
        return [blocks]

    left_x = sum(dense[0]) / len(dense[0])
    right_x = sum(dense[-1]) / len(dense[-1])
    if right_x - left_x < page_width * COLUMN_GAP_RATIO:
        return [blocks]

    boundary = right_x - X_CLUSTER_TOLERANCE
    right = [b for b in blocks if b["x0"] >= boundary]
    if len(right) < 2:
        return [blocks]

    # Everything sitting entirely above the first right-column block is a
    # full-width header band: name, title, contact details.
    column_top = min(b["y0"] for b in right)
    header = sorted(
        [b for b in blocks if b["y1"] <= column_top], key=lambda b: b["y0"]
    )
    header_ids = {id(b) for b in header}

    left = [
        b for b in blocks
        if b["x0"] < boundary and id(b) not in header_ids
    ]
    right = [b for b in right if id(b) not in header_ids]
    if len(left) < 2 or len(right) < 2:
        return [blocks]

    left_width = max(b["x1"] for b in left) - min(b["x0"] for b in left)
    right_width = max(b["x1"] for b in right) - min(b["x0"] for b in right)

    # A narrow sidebar is read after the main column, whichever side it is on —
    # the main column is the document's spine and should lead.
    if left_width < page_width * SIDEBAR_MAX_RATIO and right_width > left_width:
        body = [right, left]
    else:
        body = [left, right]

    return ([header] if header else []) + body


def extract(path: str) -> PdfExtraction:
    """Extract text from a PDF, preserving reading order and page offsets."""
    import fitz  # PyMuPDF

    parts: list[str] = []
    page_breaks: list[int] = []
    hidden: list[dict] = []
    multi_column = False
    cursor = 0

    with fitz.open(path) as doc:
        page_count = doc.page_count
        for page_index, page in enumerate(doc):
            blocks = _blocks_for_page(page)
            if not blocks:
                if page_index < page_count - 1:
                    page_breaks.append(cursor)
                continue

            all_spans = [s for b in blocks for s in b["spans"]]
            hidden.extend(detect_hidden_text(all_spans))

            columns = _detect_columns(blocks, page.rect.width)
            if len(columns) > 1:
                multi_column = True

            page_text_parts = []
            for column in columns:
                for block in sorted(column, key=lambda b: (round(b["y0"], 1), b["x0"])):
                    page_text_parts.append(block["text"])

            page_text = "\n".join(page_text_parts)
            parts.append(page_text)
            cursor += len(page_text) + 1
            if page_index < page_count - 1:
                page_breaks.append(cursor)

    text = "\n".join(parts)
    if hidden:
        logger.warning("hidden_text_detected", count=len(hidden), path=path)

    return PdfExtraction(
        text=text,
        page_count=page_count,
        page_breaks=page_breaks,
        has_text_layer=bool(text.strip()),
        hidden_text=hidden,
        is_multi_column=multi_column,
    )


def rasterise(path: str, dpi: int = 300) -> list[bytes]:
    """Render pages to PNG bytes for the OCR path."""
    import fitz

    images: list[bytes] = []
    with fitz.open(path) as doc:
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        for page in doc:
            images.append(page.get_pixmap(matrix=matrix).tobytes("png"))
    return images
