"""Quality gate and chunker: the layer that decides what gets screened at all."""

from app.extraction.chunker import (
    chunk_resume,
    find_sections,
    page_for_offset,
)
from app.extraction.quality import (
    QualityVerdict,
    alpha_ratio,
    assess,
    detect_hidden_text,
    word_ratio,
)

GOOD_RESUME = """
Senior Frontend Engineer

EXPERIENCE

Senior Engineer, Stripe                                    Jan 2019 - Present
Rebuilt the Stripe Checkout surface in React 18 with concurrent rendering.
Drove the product to WCAG 2.1 AA conformance with quarterly screen reader audits.
Cut largest contentful paint from 3.1 seconds to 1.2 seconds.

Frontend Engineer, Figma                                   Jun 2016 - Dec 2018
Co-maintained the internal component library, 140 components in Storybook.
Shipped the plugin developer console used by several thousand developers.

SKILLS

React, TypeScript, GraphQL, Playwright, Jest, Kubernetes, PostgreSQL

EDUCATION

BSc Computer Science
""" * 2


class TestQualityGate:
    def test_good_document_passes(self):
        report = assess(GOOD_RESUME, page_count=2)
        assert report.verdict is QualityVerdict.GOOD
        assert report.passable
        assert report.confidence > 0.55

    def test_no_text_layer_routes_to_ocr(self):
        report = assess("", page_count=3, has_text_layer=False)
        assert report.verdict is QualityVerdict.OCR_REQUIRED
        assert "scanned" in report.reason.lower()

    def test_near_empty_text_routes_to_ocr(self):
        report = assess("Name\nPhone", page_count=2)
        assert report.verdict is QualityVerdict.OCR_REQUIRED

    def test_garbage_extraction_needs_review(self):
        garbage = ("]|{}~^ #@$%^&*() []|{}~^ " * 200)
        report = assess(garbage, page_count=1)
        assert report.verdict in (QualityVerdict.NEEDS_REVIEW, QualityVerdict.OCR_REQUIRED)
        assert not report.passable

    def test_signals_are_recorded_for_the_ui(self):
        report = assess(GOOD_RESUME, page_count=2)
        for key in ("total_chars", "alpha_ratio", "word_ratio", "chars_per_page"):
            assert key in report.signals

    def test_alpha_ratio(self):
        assert alpha_ratio("abcdef") == 1.0
        assert alpha_ratio("!!!!!!") == 0.0
        assert alpha_ratio("") == 0.0

    def test_word_ratio_distinguishes_prose_from_noise(self):
        assert word_ratio("the quick brown fox jumps") > 0.9
        assert word_ratio("$$ ## @@ %% ^^") < 0.2


class TestHiddenTextDetection:
    def test_white_on_white_flagged(self):
        spans = [{"text": "React Kubernetes AWS GraphQL", "color": 0xFFFFFF, "size": 11}]
        flagged = detect_hidden_text(spans)
        assert len(flagged) == 1
        assert "background" in flagged[0]["reason"]

    def test_microscopic_font_flagged(self):
        spans = [{"text": "python java c++ react", "color": 0x000000, "size": 1.5}]
        flagged = detect_hidden_text(spans)
        assert len(flagged) == 1
        assert "4pt" in flagged[0]["reason"]

    def test_normal_text_not_flagged(self):
        spans = [{"text": "Senior Engineer at Stripe", "color": 0x1A1A1A, "size": 11}]
        assert detect_hidden_text(spans) == []


class TestChunking:
    def test_sections_detected(self):
        labels = {label for _, label in find_sections(GOOD_RESUME)}
        assert "experience" in labels
        assert "skills" in labels
        assert "education" in labels

    def test_chunks_produced_with_offsets(self):
        chunks = chunk_resume(GOOD_RESUME)
        assert chunks
        for chunk in chunks:
            assert chunk.char_end > chunk.char_start
            # Offsets must actually point at the chunk text in the source.
            window = GOOD_RESUME[chunk.char_start:chunk.char_end]
            assert chunk.text.strip()[:30] in window

    def test_experience_split_per_role(self):
        chunks = chunk_resume(GOOD_RESUME)
        exp = [c for c in chunks if c.section == "experience"]
        assert len(exp) >= 2, "each role should be its own chunk"

    def test_empty_input(self):
        assert chunk_resume("") == []
        assert chunk_resume("   \n  ") == []

    def test_unstructured_resume_still_chunks(self):
        plain = ("I have worked as a software engineer for eight years building "
                 "web applications in React and TypeScript. " * 8)
        chunks = chunk_resume(plain)
        assert len(chunks) >= 1

    def test_page_mapping(self):
        breaks = [1000, 2000]
        assert page_for_offset(500, breaks) == 1
        assert page_for_offset(1500, breaks) == 2
        assert page_for_offset(2500, breaks) == 3
        assert page_for_offset(500, []) is None

    def test_chunks_are_bounded(self):
        from app.extraction.chunker import MAX_CHUNK_CHARS
        huge = "Built scalable systems with React and TypeScript. " * 400
        for chunk in chunk_resume(huge):
            assert len(chunk.text) <= MAX_CHUNK_CHARS + 200
