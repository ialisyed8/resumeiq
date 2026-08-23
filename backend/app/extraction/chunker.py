"""
Semantic chunking.

Fixed-token chunking splits a role description in half and produces evidence
quotes that start mid-sentence. We chunk on the structure resumes actually have:
section headings, role entries, project entries, bullet groups.

Every chunk keeps its character offsets into the full document so evidence can
be highlighted in the source viewer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "experience": re.compile(
        r"^\s*(work\s+)?(experience|employment|professional\s+experience|career\s+history|"
        r"work\s+history)\s*:?\s*$", re.I | re.M),
    "education": re.compile(r"^\s*(education|academic\s+background|qualifications)\s*:?\s*$", re.I | re.M),
    "skills": re.compile(
        r"^\s*(technical\s+)?(skills|technologies|technical\s+proficiencies|"
        r"core\s+competencies|tech\s+stack)\s*:?\s*$", re.I | re.M),
    "projects": re.compile(r"^\s*(projects|selected\s+projects|personal\s+projects|portfolio)\s*:?\s*$", re.I | re.M),
    "certifications": re.compile(r"^\s*(certifications?|licen[cs]es?|accreditations?)\s*:?\s*$", re.I | re.M),
    "summary": re.compile(r"^\s*(summary|profile|objective|about\s+me|professional\s+summary)\s*:?\s*$", re.I | re.M),
    "publications": re.compile(r"^\s*(publications|papers|talks)\s*:?\s*$", re.I | re.M),
}

#: A line that looks like a role heading: "Senior Engineer, Stripe  2019-2024"
ROLE_HEADING = re.compile(
    r"^\s*(?P<line>[A-Z][^\n]{4,120}?)\s{2,}(?P<dates>[A-Z][a-z]{2,8}\.?\s*\d{4}|\d{1,2}/\d{4}|\d{4})",
    re.M,
)
DATE_HINT = re.compile(
    r"(19|20)\d{2}\s*(?:-|–|—|to)\s*((19|20)\d{2}|present|current)", re.I
)

MIN_CHUNK_CHARS = 60
MAX_CHUNK_CHARS = 1800


@dataclass(slots=True)
class Chunk:
    index: int
    text: str
    section: str | None
    char_start: int
    char_end: int
    page: int | None = None

    def as_dict(self) -> dict:
        return {
            "chunk_index": self.index, "text": self.text, "section": self.section,
            "char_start": self.char_start, "char_end": self.char_end, "page": self.page,
        }


def find_sections(text: str) -> list[tuple[int, str]]:
    """Locate section headings as (offset, label), in document order."""
    found: list[tuple[int, str]] = []
    for label, pattern in SECTION_PATTERNS.items():
        for match in pattern.finditer(text):
            found.append((match.start(), label))
    found.sort()
    return found


def page_for_offset(offset: int, page_breaks: list[int]) -> int | None:
    """Map a character offset to a 1-based page number."""
    if not page_breaks:
        return None
    page = 1
    for boundary in page_breaks:
        if offset >= boundary:
            page += 1
        else:
            break
    return page


def _split_long(block: str, base_offset: int) -> list[tuple[str, int, int]]:
    """Split an oversized block on paragraph then sentence boundaries."""
    if len(block) <= MAX_CHUNK_CHARS:
        return [(block, base_offset, base_offset + len(block))]

    pieces: list[tuple[str, int, int]] = []
    cursor = 0
    for para in re.split(r"\n\s*\n", block):
        start = block.index(para, cursor)
        cursor = start + len(para)
        if len(para) <= MAX_CHUNK_CHARS:
            if para.strip():
                pieces.append((para, base_offset + start, base_offset + start + len(para)))
            continue
        # Still too long: fall back to sentence boundaries.
        sent_cursor = 0
        buffer, buf_start = "", start
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            s_at = para.index(sentence, sent_cursor)
            sent_cursor = s_at + len(sentence)
            if len(buffer) + len(sentence) > MAX_CHUNK_CHARS and buffer:
                pieces.append((buffer, base_offset + start + buf_start,
                               base_offset + start + buf_start + len(buffer)))
                buffer, buf_start = sentence, s_at
            else:
                if not buffer:
                    buf_start = s_at
                buffer = f"{buffer} {sentence}".strip()
        if buffer.strip():
            pieces.append((buffer, base_offset + start + buf_start,
                           base_offset + start + buf_start + len(buffer)))
    return pieces


def chunk_resume(text: str, page_breaks: list[int] | None = None) -> list[Chunk]:
    """
    Split a resume into retrievable, quotable units.

    Falls back to paragraph blocks when no recognisable sections are found —
    plenty of resumes have no headings at all.
    """
    if not text or not text.strip():
        return []

    page_breaks = page_breaks or []
    sections = find_sections(text)
    chunks: list[Chunk] = []
    index = 0

    if not sections:
        boundaries = [(0, len(text), None)]
    else:
        boundaries = []
        if sections[0][0] > 0:
            boundaries.append((0, sections[0][0], "header"))
        for i, (offset, label) in enumerate(sections):
            end = sections[i + 1][0] if i + 1 < len(sections) else len(text)
            boundaries.append((offset, end, label))

    for start, end, label in boundaries:
        block = text[start:end]
        if len(block.strip()) < MIN_CHUNK_CHARS:
            continue

        # Inside experience and projects, split per entry so a chunk is one role.
        if label in {"experience", "projects"}:
            entries = _split_entries(block, start)
        else:
            entries = _split_long(block, start)

        for body, e_start, e_end in entries:
            if len(body.strip()) < MIN_CHUNK_CHARS:
                continue
            chunks.append(
                Chunk(
                    index=index,
                    text=body.strip(),
                    section=label,
                    char_start=e_start,
                    char_end=e_end,
                    page=page_for_offset(e_start, page_breaks),
                )
            )
            index += 1

    return chunks


def _split_entries(block: str, base_offset: int) -> list[tuple[str, int, int]]:
    """Split an experience section into one chunk per role."""
    starts = [m.start() for m in ROLE_HEADING.finditer(block)]
    if not starts:
        starts = [m.start() for m in DATE_HINT.finditer(block)]
        # Snap each match back to the start of its line.
        starts = [block.rfind("\n", 0, s) + 1 for s in starts]
    starts = sorted(set(s for s in starts if s >= 0))

    if not starts:
        return _split_long(block, base_offset)

    if starts[0] > MIN_CHUNK_CHARS:
        starts.insert(0, 0)

    out: list[tuple[str, int, int]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(block)
        out.extend(_split_long(block[start:end], base_offset + start))
    return out
