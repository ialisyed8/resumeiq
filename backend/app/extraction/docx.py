"""
DOCX extraction.

python-docx walks the main document body and silently misses headers, footers,
and text boxes. Resume templates use text boxes constantly — an entire skills
sidebar can live in one. We walk the underlying XML to catch everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass(slots=True)
class DocxExtraction:
    text: str
    page_count: int = 1
    page_breaks: list[int] = field(default_factory=list)
    has_text_layer: bool = True


def _paragraph_text(element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{W_NS}t"))


def extract(path: str) -> DocxExtraction:
    import docx

    document = docx.Document(path)
    parts: list[str] = []

    # Main body, in document order, including tables.
    body = document.element.body
    seen: set[int] = set()
    for element in body.iter():
        tag = element.tag
        if tag == f"{W_NS}p":
            key = id(element)
            if key in seen:
                continue
            seen.add(key)
            text = _paragraph_text(element).strip()
            if text:
                parts.append(text)
        elif tag == f"{W_NS}tbl":
            for row in element.iter(f"{W_NS}tr"):
                cells = [
                    _paragraph_text(cell).strip()
                    for cell in row.iter(f"{W_NS}tc")
                ]
                line = "  ".join(c for c in cells if c)
                if line:
                    parts.append(line)

    # Headers and footers — often hold contact details and sometimes skills.
    for section in document.sections:
        for container in (section.header, section.footer):
            if container is None:
                continue
            for paragraph in container.paragraphs:
                if paragraph.text.strip():
                    parts.append(paragraph.text.strip())

    # Text boxes live in drawing elements that the paragraph walk above misses.
    for txbx in body.iter(f"{W_NS}txbxContent"):
        for paragraph in txbx.iter(f"{W_NS}p"):
            text = _paragraph_text(paragraph).strip()
            if text and text not in parts:
                parts.append(text)

    # De-duplicate while preserving order — headers repeat per section.
    deduped: list[str] = []
    seen_text: set[str] = set()
    for part in parts:
        if part not in seen_text:
            seen_text.add(part)
            deduped.append(part)

    text = "\n".join(deduped)
    # DOCX has no reliable page model; estimate for the UI only.
    estimated_pages = max(1, len(text) // 3000)
    return DocxExtraction(
        text=text, page_count=estimated_pages, has_text_layer=bool(text.strip())
    )
